"""Fail-closed resolution of compiled Solana instructions from a stored raw JSON transaction."""

from __future__ import annotations

from typing import Any

from .decoder import TOKEN, TOKEN2022, decode58

ADAPTER_VERSION = "solana_compiled_instruction_resolution_v1"
SUPPORTED_MESSAGE_VERSIONS = ("legacy", 0, 1)


class NormalizationError(ValueError):
    """A transaction shape that cannot be resolved without guessing."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


def _public_key(value: Any, category: str) -> str:
    if not isinstance(value, str):
        raise NormalizationError(category)
    try:
        decoded = decode58(value)
    except ValueError as exc:
        raise NormalizationError(category) from exc
    if len(decoded) != 32:
        raise NormalizationError(category)
    return value


def _indices(values: Any, *, category: str) -> list[int]:
    if not isinstance(values, list) or any(
        type(value) is not int or not 0 <= value <= 255 for value in values
    ):
        raise NormalizationError(category)
    return values


def _account_table(message: dict[str, Any], meta: dict[str, Any]) -> list[dict[str, Any]]:
    static_values = message.get("accountKeys")
    if not isinstance(static_values, list) or not static_values:
        raise NormalizationError("static_account_keys_missing")
    accounts = [
        {
            "index": index,
            "pubkey": _public_key(value, "static_account_key_invalid"),
            "source": "static",
        }
        for index, value in enumerate(static_values)
    ]

    lookups = message.get("addressTableLookups", [])
    if not isinstance(lookups, list):
        raise NormalizationError("address_table_lookup_shape")
    lookup_writable: list[dict[str, Any]] = []
    lookup_readonly: list[dict[str, Any]] = []
    for lookup in lookups:
        if not isinstance(lookup, dict):
            raise NormalizationError("address_table_lookup_shape")
        table = _public_key(lookup.get("accountKey"), "address_table_key_invalid")
        writable = _indices(lookup.get("writableIndexes"), category="lookup_writable_indexes")
        readonly = _indices(lookup.get("readonlyIndexes"), category="lookup_readonly_indexes")
        lookup_writable.extend(
            {"source": "loaded_writable", "lookup_table": table, "lookup_index": index}
            for index in writable
        )
        lookup_readonly.extend(
            {"source": "loaded_readonly", "lookup_table": table, "lookup_index": index}
            for index in readonly
        )

    loaded = meta.get("loadedAddresses")
    if loaded is None:
        if lookup_writable or lookup_readonly:
            raise NormalizationError("loaded_addresses_missing")
        loaded = {"writable": [], "readonly": []}
    if not isinstance(loaded, dict):
        raise NormalizationError("loaded_addresses_shape")
    writable_values = loaded.get("writable")
    readonly_values = loaded.get("readonly")
    if not isinstance(writable_values, list) or not isinstance(readonly_values, list):
        raise NormalizationError("loaded_addresses_shape")
    if len(writable_values) != len(lookup_writable) or len(readonly_values) != len(lookup_readonly):
        raise NormalizationError("loaded_address_count_mismatch")
    for values, provenance in (
        (writable_values, lookup_writable),
        (readonly_values, lookup_readonly),
    ):
        for value, source in zip(values, provenance, strict=True):
            account = {
                "index": len(accounts),
                "pubkey": _public_key(value, "loaded_account_key_invalid"),
                **source,
            }
            accounts.append(account)
    return accounts


def _resolve_instruction(
    instruction: Any,
    accounts: list[dict[str, Any]],
    *,
    path: list[int],
    outer_index: int,
    stack_height: int,
) -> dict[str, Any]:
    if not isinstance(instruction, dict):
        raise NormalizationError("compiled_instruction_shape")
    program_index = instruction.get("programIdIndex")
    if type(program_index) is not int or not 0 <= program_index < len(accounts):
        raise NormalizationError("program_id_index_out_of_range")
    indices = _indices(instruction.get("accounts"), category="instruction_account_indexes")
    if any(index >= len(accounts) for index in indices):
        raise NormalizationError("account_index_out_of_range")
    data = instruction.get("data")
    if not isinstance(data, str):
        raise NormalizationError("instruction_data_missing")
    try:
        decoded_data = decode58(data)
    except ValueError as exc:
        raise NormalizationError("instruction_data_invalid_base58") from exc
    return {
        "path": path,
        "outer_index": outer_index,
        "stack_height": stack_height,
        "program_id_index": program_index,
        "program_id": accounts[program_index]["pubkey"],
        "account_indexes": indices,
        "accounts": [accounts[index]["pubkey"] for index in indices],
        "account_provenance": [accounts[index] for index in indices],
        "data_base58": data,
        "data_hex": decoded_data.hex(),
        "data_length": len(decoded_data),
        "raw_instruction": instruction,
    }


def normalize_compiled_transaction(transaction: Any) -> dict[str, Any]:
    """Resolve raw `encoding=json` indices while retaining their source and original wire fields."""
    if not isinstance(transaction, dict):
        raise NormalizationError("transaction_shape")
    version = transaction.get("version")
    valid_version = version == "legacy" or (
        type(version) is int and version in SUPPORTED_MESSAGE_VERSIONS
    )
    if not valid_version:
        raise NormalizationError("transaction_version_unsupported_or_missing")
    raw_transaction = transaction.get("transaction")
    if not isinstance(raw_transaction, dict):
        raise NormalizationError("transaction_message_or_meta_missing")
    message = raw_transaction.get("message")
    meta = transaction.get("meta")
    if not isinstance(message, dict) or not isinstance(meta, dict):
        raise NormalizationError("transaction_message_or_meta_missing")
    lookups = message.get("addressTableLookups", [])
    if version == "legacy" and lookups:
        raise NormalizationError("legacy_transaction_has_address_table_lookups")
    accounts = _account_table(message, meta)
    raw_outer = message.get("instructions")
    if not isinstance(raw_outer, list):
        raise NormalizationError("outer_instructions_missing")
    outer = [
        _resolve_instruction(ix, accounts, path=[index], outer_index=index, stack_height=1)
        for index, ix in enumerate(raw_outer)
    ]
    if "innerInstructions" not in meta or not isinstance(meta["innerInstructions"], list):
        raise NormalizationError("inner_instruction_trace_unavailable")
    groups = meta["innerInstructions"]
    seen: set[int] = set()
    inner: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            raise NormalizationError("inner_instruction_group_shape")
        outer_index = group.get("index")
        if type(outer_index) is not int or not 0 <= outer_index < len(outer) or outer_index in seen:
            raise NormalizationError("inner_group_index_invalid")
        seen.add(outer_index)
        instructions = group.get("instructions")
        if not isinstance(instructions, list):
            raise NormalizationError("inner_instruction_list_missing")
        for index, instruction in enumerate(instructions):
            stack_height = instruction.get("stackHeight") if isinstance(instruction, dict) else None
            if type(stack_height) is not int or stack_height < 2:
                raise NormalizationError("inner_stack_height_unavailable")
            inner.append(
                _resolve_instruction(
                    instruction,
                    accounts,
                    path=[outer_index, index],
                    outer_index=outer_index,
                    stack_height=stack_height,
                )
            )
    return {
        "adapter_version": ADAPTER_VERSION,
        "transaction_version": version,
        "transaction_signatures": raw_transaction.get("signatures"),
        "meta_error": meta.get("err"),
        "accounts": accounts,
        "outer_instructions": outer,
        "inner_instructions": inner,
        "pre_token_balances": meta.get("preTokenBalances"),
        "post_token_balances": meta.get("postTokenBalances"),
        "raw_inner_groups": groups,
    }


def decode_token_transfer_checked(instruction: dict[str, Any]) -> dict[str, Any]:
    """Decode the exact legacy SPL TransferChecked wire form; quarantine Token-2022 effects."""
    program = instruction.get("program_id")
    if program not in (TOKEN, TOKEN2022):
        raise NormalizationError("not_a_supported_token_program")
    data_hex = instruction.get("data_hex")
    if not isinstance(data_hex, str):
        raise NormalizationError("normalized_instruction_data_missing")
    try:
        data = bytes.fromhex(data_hex)
    except ValueError as exc:
        raise NormalizationError("normalized_instruction_data_invalid_hex") from exc
    if not data or data[0] != 12:
        raise NormalizationError("token_instruction_unsupported")
    if len(data) != 10:
        raise NormalizationError("transfer_checked_wire_length")
    accounts = instruction.get("accounts")
    if (
        not isinstance(accounts, list)
        or len(accounts) < 4
        or any(not isinstance(account, str) for account in accounts[:4])
    ):
        raise NormalizationError("transfer_checked_account_shape")
    for account in accounts[:4]:
        _public_key(account, "transfer_checked_account_key_invalid")
    amount = int.from_bytes(data[1:9], "little")
    decimals = data[9]
    return {
        "instruction": "transfer_checked",
        "program_id": program,
        "source": accounts[0],
        "mint": accounts[1],
        "destination": accounts[2],
        "authority": accounts[3],
        "additional_signers": accounts[4:],
        "amount_base_units": amount,
        "decimals": decimals,
        "economic_status": (
            "exact_instruction_amount"
            if program == TOKEN
            else "token_2022_fee_and_hook_effects_unresolved"
        ),
    }
