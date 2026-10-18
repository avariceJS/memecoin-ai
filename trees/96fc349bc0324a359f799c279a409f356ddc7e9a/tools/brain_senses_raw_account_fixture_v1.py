"""Synthetic-only raw account byte parsing and deterministic fixture replay.

The verifier checks byte framing, provenance bindings, account joins, and ordered
attempt records. It does not emulate a validator or establish economic effects.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import struct
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tools import brain_senses_raw_account_fixture_schema_v1 as schema

REPO = Path(__file__).resolve().parents[1]
MAX_FIXTURE_BYTES = 8_000_000
MAX_VECTORS = 256
MAX_ACCOUNTS_PER_VECTOR = 128
MAX_STEPS_PER_VECTOR = 512
U64_MAX = (1 << 64) - 1
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class FixtureRejected(ValueError):
    """A fixture violates the closed byte or provenance contract."""


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: Any) -> bytes:
    try:
        return (
            json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode()
    except (TypeError, ValueError) as exc:
        raise FixtureRejected("noncanonical_value") from exc


def source_bindings() -> dict[str, str]:
    """Hash the validator closure without importing any live decoder."""
    paths = {
        "tools/brain_senses_raw_account_fixture_schema_v1.py": REPO
        / "tools/brain_senses_raw_account_fixture_schema_v1.py",
        "tools/brain_senses_raw_account_fixture_v1.py": Path(__file__),
    }
    return {name: sha256(path.read_bytes()) for name, path in sorted(paths.items())}


def _duplicate_guard(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FixtureRejected("duplicate_json_key")
        result[key] = value
    return result


def _decode_json(raw: bytes) -> Any:
    if not isinstance(raw, bytes) or len(raw) > MAX_FIXTURE_BYTES:
        raise FixtureRejected("fixture_size")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_duplicate_guard)
    except FixtureRejected:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FixtureRejected("fixture_json") from exc
    if canonical(value) != raw:
        raise FixtureRejected("fixture_not_canonical")
    return value


def _exact_keys(value: Any, keys: set[str], reason: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise FixtureRejected(reason)
    return value


def _integer(value: Any, *, minimum: int = 0, maximum: int = U64_MAX, reason: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise FixtureRejected(reason)
    return value


def _digest(value: Any, reason: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise FixtureRejected(reason)
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise FixtureRejected(reason) from exc
    if value.lower() != value:
        raise FixtureRejected(reason)
    return value


def _b64(value: Any, reason: str) -> bytes:
    if not isinstance(value, str):
        raise FixtureRejected(reason)
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise FixtureRejected(reason) from exc
    if base64.b64encode(raw).decode("ascii") != value:
        raise FixtureRejected(reason)
    return raw


def _b58decode(value: Any) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise FixtureRejected("public_key_shape")
    number = 0
    for char in value:
        try:
            digit = BASE58_ALPHABET.index(char)
        except ValueError as exc:
            raise FixtureRejected("public_key_encoding") from exc
        number = number * 58 + digit
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    decoded = b"\0" * (len(value) - len(value.lstrip("1"))) + body
    if len(decoded) != 32:
        raise FixtureRejected("public_key_width")
    if _b58encode(decoded) != value:
        raise FixtureRejected("public_key_noncanonical")
    return decoded


def _b58encode(raw: bytes) -> str:
    number = int.from_bytes(raw, "big")
    encoded = ""
    while number:
        number, digit = divmod(number, 58)
        encoded = BASE58_ALPHABET[digit] + encoded
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + encoded


def _tlv_extensions(raw: bytes, *, role: str) -> dict[int, bytes]:
    base_length = 82 if role == "mint" else 165
    if len(raw) == base_length:
        return {}
    if len(raw) < 166:
        raise FixtureRejected("token_account_truncated_extension_header")
    expected_type = 1 if role == "mint" else 2
    if raw[165] != expected_type:
        raise FixtureRejected("token_account_extension_type")
    offset = 166
    result: dict[int, bytes] = {}
    while offset < len(raw):
        if len(raw) - offset < 4:
            raise FixtureRejected("tlv_header_truncated")
        extension_type, length = struct.unpack_from("<HH", raw, offset)
        offset += 4
        if extension_type == 0 or extension_type in result:
            raise FixtureRejected("tlv_type_invalid_or_duplicate")
        if length > len(raw) - offset:
            raise FixtureRejected("tlv_value_truncated")
        result[extension_type] = raw[offset : offset + length]
        offset += length
    return result


def _decode_transfer_fee(value: bytes) -> dict[str, Any]:
    if len(value) != 108:
        raise FixtureRejected("transfer_fee_config_length")
    older_epoch, older_cap, older_bps = struct.unpack_from("<QQH", value, 72)
    newer_epoch, newer_cap, newer_bps = struct.unpack_from("<QQH", value, 90)
    if older_bps > 10_000 or newer_bps > 10_000:
        raise FixtureRejected("transfer_fee_basis_points")
    return {
        "withheld_atomic_units": struct.unpack_from("<Q", value, 64)[0],
        "older": {
            "epoch": older_epoch,
            "maximum_fee_atomic_units": older_cap,
            "basis_points": older_bps,
        },
        "newer": {
            "epoch": newer_epoch,
            "maximum_fee_atomic_units": newer_cap,
            "basis_points": newer_bps,
        },
    }


def _decode_account(account: Mapping[str, Any]) -> dict[str, Any]:
    role = account["role"]
    owner = account["owner"]
    raw = _b64(account["data_b64"], "account_base64")
    _digest(account["data_sha256"], "account_digest")
    if sha256(raw) != account["data_sha256"]:
        raise FixtureRejected("account_digest_mismatch")
    if role in ("mint", "token_account"):
        if owner not in (schema.TOKEN_PROGRAM_ID, schema.TOKEN_2022_PROGRAM_ID):
            raise FixtureRejected("token_account_owner")
        base_length = 82 if role == "mint" else 165
        if len(raw) < base_length:
            raise FixtureRejected("token_base_truncated")
        if owner == schema.TOKEN_PROGRAM_ID and len(raw) != base_length:
            raise FixtureRejected("classic_token_extensions_unsupported")
        extensions = (
            _tlv_extensions(raw, role=role) if owner == schema.TOKEN_2022_PROGRAM_ID else {}
        )
        if role == "mint":
            (mint_auth_option,) = struct.unpack_from("<I", raw, 0)
            (freeze_auth_option,) = struct.unpack_from("<I", raw, 46)
            if mint_auth_option not in (0, 1) or freeze_auth_option not in (0, 1):
                raise FixtureRejected("mint_authority_option")
            decimals = raw[44]
            initialized = raw[45]
            if initialized not in (0, 1):
                raise FixtureRejected("mint_initialized_flag")
            decoded: dict[str, Any] = {
                "role": role,
                "owner_program": owner,
                "decimals": decimals,
                "decimals_dimension": "decimal_places",
                "supply_atomic_units": struct.unpack_from("<Q", raw, 36)[0],
                "initialized_byte": initialized,
                "extension_types": sorted(extensions),
            }
            if 1 in extensions:
                decoded["transfer_fee_config"] = _decode_transfer_fee(extensions[1])
            if 14 in extensions:
                if len(extensions[14]) != 64:
                    raise FixtureRejected("transfer_hook_config_length")
                decoded["transfer_hook_status"] = "unsupported_without_runtime_evidence"
            else:
                decoded["transfer_hook_status"] = "no_hook_extension_observed"
            unknown = sorted(set(extensions) - {1, 14})
            decoded["unsupported_extension_types"] = unknown
            return decoded

        state = raw[108]
        if state not in (0, 1, 2):
            raise FixtureRejected("token_account_state")
        mint = _b58encode(raw[:32])
        authority = _b58encode(raw[32:64])
        for offset in (72, 109, 129):
            (option,) = struct.unpack_from("<I", raw, offset)
            if option not in (0, 1):
                raise FixtureRejected("token_account_option")
        decoded = {
            "role": role,
            "owner_program": owner,
            "mint": mint,
            "authority": authority,
            "amount_atomic_units": struct.unpack_from("<Q", raw, 64)[0],
            "state_byte": state,
            "extension_types": sorted(extensions),
            "withheld_transfer_fee_atomic_units": (
                struct.unpack("<Q", extensions[2])[0]
                if 2 in extensions and len(extensions[2]) == 8
                else None
            ),
            "native_reserve_lamports": (
                struct.unpack_from("<Q", raw, 113)[0]
                if struct.unpack_from("<I", raw, 109)[0] == 1
                else None
            ),
            "native_reserve_dimension": "lamports_or_null",
        }
        if 2 in extensions and len(extensions[2]) != 8:
            raise FixtureRejected("withheld_fee_extension_length")
        if 14 in extensions:
            decoded["transfer_hook_status"] = "unsupported_without_runtime_evidence"
        if 15 in extensions:
            decoded["transfer_hook_account_status"] = "unsupported_without_runtime_evidence"
        return decoded

    if role == "clock":
        if (
            owner != schema.SYSVAR_OWNER_ID
            or account["address"] != schema.CLOCK_SYSVAR_ID
            or len(raw) != 40
        ):
            raise FixtureRejected("clock_account_shape")
        slot, epoch_start, epoch, leader_epoch, unix_timestamp = struct.unpack("<QqQQq", raw)
        return {
            "role": role,
            "owner_program": owner,
            "slot": slot,
            "epoch_start_timestamp_seconds": epoch_start,
            "epoch": epoch,
            "leader_schedule_epoch": leader_epoch,
            "unix_timestamp_seconds": unix_timestamp,
        }
    if role == "rent":
        if (
            owner != schema.SYSVAR_OWNER_ID
            or account["address"] != schema.RENT_SYSVAR_ID
            or len(raw) != 17
        ):
            raise FixtureRejected("rent_account_shape")
        lamports_per_byte_year, exemption_threshold, burn_percent = struct.unpack("<QdB", raw)
        if not math.isfinite(exemption_threshold) or exemption_threshold <= 0:
            raise FixtureRejected("rent_exemption_threshold")
        return {
            "role": role,
            "owner_program": owner,
            "lamports_per_byte_year": lamports_per_byte_year,
            "exemption_threshold_unverified": exemption_threshold,
            "burn_percent": burn_percent,
            "rent_unit_status": "raw_sysvar_fields_only_no_runtime_law",
        }
    raise FixtureRejected("account_role")


def _decode_step(step: Mapping[str, Any], account_ids: set[str]) -> dict[str, Any]:
    kind = step["kind"]
    program = step["program_id"]
    accounts = step["account_ids"]
    data = _b64(step["data_b64"], "step_base64")
    if sha256(data) != step["data_sha256"]:
        raise FixtureRejected("step_digest_mismatch")
    if (
        not isinstance(accounts, list)
        or not accounts
        or any(
            not isinstance(account_id, str) or account_id not in account_ids
            for account_id in accounts
        )
    ):
        raise FixtureRejected("step_account_reference")
    if len(set(accounts)) != len(accounts):
        raise FixtureRejected("step_duplicate_account")
    stack_height = _integer(step["stack_height"], minimum=1, maximum=255, reason="stack_height")
    if step["status"] != "attempted":
        raise FixtureRejected("step_status_must_be_attempted_only")
    if kind == "system_create_account":
        if (
            program != schema.SYSTEM_PROGRAM_ID
            or len(data) != 52
            or struct.unpack_from("<I", data)[0] != 0
        ):
            raise FixtureRejected("system_create_wire")
    elif kind == "system_transfer":
        if (
            program != schema.SYSTEM_PROGRAM_ID
            or len(data) != 12
            or struct.unpack_from("<I", data)[0] != 2
        ):
            raise FixtureRejected("system_transfer_wire")
    elif kind == "token_initialize_account3":
        if (
            program not in (schema.TOKEN_PROGRAM_ID, schema.TOKEN_2022_PROGRAM_ID)
            or len(data) != 33
            or data[0] != 18
        ):
            raise FixtureRejected("token_initialize_account3_wire")
    elif kind == "token_sync_native":
        if (
            program not in (schema.TOKEN_PROGRAM_ID, schema.TOKEN_2022_PROGRAM_ID)
            or data != b"\x11"
        ):
            raise FixtureRejected("token_sync_native_wire")
    elif kind == "token_close_account":
        if (
            program not in (schema.TOKEN_PROGRAM_ID, schema.TOKEN_2022_PROGRAM_ID)
            or data != b"\x09"
        ):
            raise FixtureRejected("token_close_account_wire")
    elif kind == "token_transfer_checked":
        if (
            program not in (schema.TOKEN_PROGRAM_ID, schema.TOKEN_2022_PROGRAM_ID)
            or len(data) != 10
            or data[0] != 12
        ):
            raise FixtureRejected("token_transfer_checked_wire")
        amount = struct.unpack_from("<Q", data, 1)[0]
        return {
            "ordinal": step["ordinal"],
            "kind": kind,
            "program_id": program,
            "stack_height": stack_height,
            "wire_amount_atomic_units": amount,
            "wire_decimals": data[9],
            "dimensions": {"amount": "token_atomic_units", "decimals": "decimal_places"},
            "status": "attempted_only",
        }
    elif kind in ("hook_invocation", "cpi_attempt"):
        if stack_height < 2:
            raise FixtureRejected("cpi_stack_height")
    else:
        raise FixtureRejected("step_kind")
    return {
        "ordinal": step["ordinal"],
        "kind": kind,
        "program_id": program,
        "stack_height": stack_height,
        "status": "attempted_only",
    }


def _verify_vector(vector: Any) -> dict[str, Any]:
    fields = {
        "case_id",
        "domain",
        "frame",
        "context_epoch",
        "clock_account_id",
        "rent_account_id",
        "focus_account_id",
        "transaction_status",
        "failure_step_ordinal",
        "decision_ns",
        "accounts",
        "steps",
    }
    vector = _exact_keys(vector, fields, "vector_fields")
    case_id = vector["case_id"]
    if not isinstance(case_id, str) or not case_id or len(case_id) > 100:
        raise FixtureRejected("case_id")
    if not isinstance(vector["domain"], str) or vector["domain"] not in schema.VECTOR_DOMAINS:
        raise FixtureRejected("vector_domain")
    frame = _exact_keys(
        vector["frame"],
        {
            "slot",
            "blockhash",
            "block_time_seconds",
            "capture_id",
            "receipt_ns",
            "durable_available_ns",
        },
        "frame_fields",
    )
    slot = _integer(frame["slot"], minimum=1, reason="frame_slot")
    blockhash = frame["blockhash"]
    _b58decode(blockhash)
    block_time = frame["block_time_seconds"]
    if block_time is not None:
        _integer(block_time, reason="frame_block_time")
    receipt_ns = _integer(frame["receipt_ns"], reason="frame_receipt_ns")
    available_ns = frame["durable_available_ns"]
    if available_ns is not None:
        _integer(available_ns, reason="frame_available_ns")
        if available_ns < receipt_ns:
            raise FixtureRejected("frame_availability_before_receipt")
    expected_capture_id = sha256(canonical({"slot": slot, "blockhash": blockhash}))
    if frame["capture_id"] != expected_capture_id:
        raise FixtureRejected("frame_capture_id")
    if vector["decision_ns"] is not None:
        raise FixtureRejected("decision_time_must_be_null")
    epoch = _integer(vector["context_epoch"], reason="context_epoch")
    status = vector["transaction_status"]
    if status not in ("succeeded", "failed"):
        raise FixtureRejected("transaction_status")
    failure_ordinal = vector["failure_step_ordinal"]
    if status == "failed":
        _integer(failure_ordinal, maximum=MAX_STEPS_PER_VECTOR - 1, reason="failure_step_ordinal")
    elif failure_ordinal is not None:
        raise FixtureRejected("unexpected_failure_step")

    accounts = vector["accounts"]
    steps = vector["steps"]
    if not isinstance(accounts, list) or not 1 <= len(accounts) <= MAX_ACCOUNTS_PER_VECTOR:
        raise FixtureRejected("account_count")
    if not isinstance(steps, list) or len(steps) > MAX_STEPS_PER_VECTOR:
        raise FixtureRejected("step_count")
    decoded_accounts: dict[str, dict[str, Any]] = {}
    parsed_accounts: dict[str, dict[str, Any]] = {}
    pubkeys: dict[tuple[str, str], str] = {}
    seen_addresses: set[str] = set()
    for account in accounts:
        fields = {
            "account_id",
            "role",
            "address",
            "owner",
            "lamports",
            "executable",
            "data_b64",
            "data_sha256",
        }
        account = _exact_keys(account, fields, "account_fields")
        account_id = account["account_id"]
        if not isinstance(account_id, str) or not account_id or account_id in decoded_accounts:
            raise FixtureRejected("account_id")
        role = account["role"]
        if not isinstance(role, str) or role not in schema.ACCOUNT_ROLES:
            raise FixtureRejected("account_role")
        address_raw = _b58decode(account["address"])
        if account["address"] in seen_addresses:
            raise FixtureRejected("duplicate_account_address")
        seen_addresses.add(account["address"])
        owner_raw = _b58decode(account["owner"])
        _integer(account["lamports"], reason="account_lamports")
        if account["executable"] is not False:
            raise FixtureRejected("executable_account_unsupported")
        parsed = _decode_account(account)
        if role in ("mint", "token_account"):
            parsed["address"] = account["address"]
            parsed["outer_owner_bytes_sha256"] = sha256(owner_raw)
            pubkeys[(role, account["address"])] = account_id
        decoded_accounts[account_id] = parsed
        decoded_accounts[account_id]["address_bytes_sha256"] = sha256(address_raw)
        parsed_accounts[account_id] = account

    if vector["clock_account_id"] is not None:
        clock_id = vector["clock_account_id"]
        if clock_id not in decoded_accounts or decoded_accounts[clock_id]["role"] != "clock":
            raise FixtureRejected("clock_account_reference")
        if decoded_accounts[clock_id]["epoch"] != epoch:
            raise FixtureRejected("clock_epoch_mismatch")
        if decoded_accounts[clock_id]["slot"] != slot:
            raise FixtureRejected("clock_frame_slot_mismatch")
    if vector["rent_account_id"] is not None:
        rent_id = vector["rent_account_id"]
        if rent_id not in decoded_accounts or decoded_accounts[rent_id]["role"] != "rent":
            raise FixtureRejected("rent_account_reference")
    for account_id, parsed in decoded_accounts.items():
        if parsed["role"] == "token_account":
            mint_id = pubkeys.get(("mint", parsed["mint"]))
            if mint_id is None:
                raise FixtureRejected("token_account_mint_missing")
            if parsed_accounts[mint_id]["owner"] != parsed_accounts[account_id]["owner"]:
                raise FixtureRejected("mint_token_program_mismatch")

    focus_id = vector["focus_account_id"]
    if focus_id is not None and focus_id not in decoded_accounts:
        raise FixtureRejected("focus_account_reference")
    parsed_steps = []
    seen_ordinals: set[int] = set()
    for step in steps:
        step_fields = {
            "ordinal",
            "kind",
            "program_id",
            "account_ids",
            "stack_height",
            "data_b64",
            "data_sha256",
            "status",
        }
        step = _exact_keys(step, step_fields, "step_fields")
        ordinal = _integer(step["ordinal"], maximum=MAX_STEPS_PER_VECTOR - 1, reason="step_ordinal")
        if ordinal in seen_ordinals:
            raise FixtureRejected("duplicate_step_ordinal")
        seen_ordinals.add(ordinal)
        _b58decode(step["program_id"])
        if not isinstance(step["account_ids"], list):
            raise FixtureRejected("step_account_ids")
        parsed_steps.append(_decode_step(step, set(decoded_accounts)))
    if [step["ordinal"] for step in parsed_steps] != sorted(seen_ordinals):
        raise FixtureRejected("step_order_not_monotone")
    if status == "failed" and failure_ordinal not in seen_ordinals:
        raise FixtureRejected("failure_step_missing")
    if vector["domain"] == "wsol_native_lifecycle":
        if focus_id is None or decoded_accounts[focus_id]["role"] != "token_account":
            raise FixtureRejected("wsol_focus_account")
        focus = decoded_accounts[focus_id]
        if focus["mint"] != schema.WSOL_MINT_ID:
            raise FixtureRejected("wsol_mint_mismatch")
        if vector["clock_account_id"] is None or vector["rent_account_id"] is None:
            raise FixtureRejected("wsol_clock_rent_required")
        required = [
            "system_create_account",
            "system_transfer",
            "token_initialize_account3",
            "token_sync_native",
            "token_close_account",
        ]
        observed = [step["kind"] for step in parsed_steps]
        if any(observed.count(kind) != 1 for kind in required):
            raise FixtureRejected("wsol_lifecycle_multiplicity")
        positions = [observed.index(kind) for kind in required]
        if positions != sorted(positions):
            raise FixtureRejected("wsol_lifecycle_order")
        for _step, original in zip(parsed_steps, steps, strict=True):
            if original["kind"] in required and focus_id not in original["account_ids"]:
                raise FixtureRejected("wsol_focus_not_in_step")
    elif vector["domain"] in ("transfer_hook_control", "transfer_hook_unsupported"):
        if focus_id is None or decoded_accounts[focus_id]["role"] != "mint":
            raise FixtureRejected("hook_focus_mint")
        has_hook = 14 in decoded_accounts[focus_id]["extension_types"]
        if has_hook != (vector["domain"] == "transfer_hook_unsupported"):
            raise FixtureRejected("hook_control_mismatch")
    elif vector["domain"] == "token2022_fee_config":
        if not any(item.get("transfer_fee_config") for item in decoded_accounts.values()):
            raise FixtureRejected("fee_config_missing")
    elif vector["domain"] == "cpi_attempt_only":
        if not parsed_steps or any(item["status"] != "attempted_only" for item in parsed_steps):
            raise FixtureRejected("cpi_attempt_only_status")
        if not any(step["kind"] == "cpi_attempt" for step in steps):
            raise FixtureRejected("cpi_attempt_missing")

    if status == "failed":
        replay_status = "attempted_only_transaction_failed"
    else:
        replay_status = "attempted_only_transaction_status_fixture"
    return {
        "case_id": case_id,
        "domain": vector["domain"],
        "frame": {
            "slot": slot,
            "blockhash": blockhash,
            "block_time_seconds": block_time,
            "event_time_ns": block_time * 1_000_000_000 if block_time is not None else None,
            "receipt_ns": receipt_ns,
            "durable_available_ns": available_ns,
            "decision_ns": None,
            "point_in_time_eligible": False,
        },
        "context_epoch": epoch,
        "transaction_status": status,
        "replay_status": replay_status,
        "account_count": len(decoded_accounts),
        "step_count": len(parsed_steps),
        "accounts": decoded_accounts,
        "steps": parsed_steps,
        "runtime_law_qualified": False,
        "economic_admission": False,
    }


def verify_bundle(
    value: Any, *, expected_sources: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Independently decode and validate a canonical synthetic fixture bundle."""
    fields = {
        "schema",
        "schema_version",
        "fixture_set_id",
        "source_pins",
        "validator_sources",
        "vectors",
        "runtime_law_qualified",
        "economic_admission",
    }
    value = _exact_keys(value, fields, "bundle_fields")
    if value["schema"] != schema.RAW_ACCOUNT_FIXTURE_SCHEMA or value["schema_version"] != 1:
        raise FixtureRejected("bundle_schema")
    if value["runtime_law_qualified"] is not False or value["economic_admission"] is not False:
        raise FixtureRejected("qualification_flags_must_be_false")
    fixture_set_id = value["fixture_set_id"]
    if not isinstance(fixture_set_id, str) or not fixture_set_id or len(fixture_set_id) > 100:
        raise FixtureRejected("fixture_set_id")
    if value["source_pins"] != schema.UPSTREAM_SOURCE_PINS:
        raise FixtureRejected("upstream_source_pin_mismatch")
    actual_sources = source_bindings()
    pinned_sources = expected_sources if expected_sources is not None else actual_sources
    if value["validator_sources"] != dict(pinned_sources) or dict(pinned_sources) != actual_sources:
        raise FixtureRejected("validator_source_pin_mismatch")
    vectors = value["vectors"]
    if not isinstance(vectors, list) or len(vectors) > MAX_VECTORS:
        raise FixtureRejected("vector_count")
    results = [_verify_vector(vector) for vector in vectors]
    case_ids = [item["case_id"] for item in results]
    if len(case_ids) != len(set(case_ids)):
        raise FixtureRejected("duplicate_case_id")
    return {
        "schema": "brain-senses-raw-account-fixture-v1-verification",
        "fixture_set_id": fixture_set_id,
        "vector_count": len(results),
        "vectors": results,
        "source_pins": value["source_pins"],
        "validator_sources": value["validator_sources"],
        "fixture_vectors": "synthetic_only",
        "runtime_law_qualified": False,
        "economic_admission": False,
    }


def verify_bundle_bytes(
    raw: bytes,
    *,
    expected_bundle_sha256: str | None = None,
    expected_sources: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if expected_bundle_sha256 is not None:
        _digest(expected_bundle_sha256, "expected_bundle_digest")
        if sha256(raw) != expected_bundle_sha256:
            raise FixtureRejected("fixture_hash_mismatch")
    value = _decode_json(raw)
    return verify_bundle(value, expected_sources=expected_sources)


def encode_bundle(value: Mapping[str, Any]) -> bytes:
    """Encode only if the exact schema and independent replay checks pass."""
    raw = canonical(value)
    verify_bundle_bytes(raw)
    return raw


def build_bundle(fixture_set_id: str, vectors: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "schema": schema.RAW_ACCOUNT_FIXTURE_SCHEMA,
        "schema_version": schema.RAW_ACCOUNT_FIXTURE_VERSION,
        "fixture_set_id": fixture_set_id,
        "source_pins": schema.UPSTREAM_SOURCE_PINS,
        "validator_sources": source_bindings(),
        "vectors": [dict(vector) for vector in vectors],
        **schema.FALSE_QUALIFICATION_FLAGS,
    }
