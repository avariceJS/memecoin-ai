import pytest

from memecoin_pumpswap.buy_exact_v1 import (
    FEE_RECIPIENTS_DOC_COMMIT,
    inspect_buy_exact_quote_in_v1,
)
from memecoin_pumpswap.compiled_economics import (
    FEE_RECIPIENTS_UPGRADE_2026_04_28,
    associated_token_address,
)
from memecoin_pumpswap.decoder import EVENT_TAG, PROGRAM, TOKEN, decode58, encode58
from memecoin_pumpswap.economic_buy_exact_v1 import diagnose_pumpswap_buy_exact_v1
from memecoin_pumpswap.schema import SCHEMA


def _key(value: int) -> str:
    return encode58(bytes([value]) * 32)


def _transaction(*, recipient=None, data_length=25, remaining_count=2, failed=False):
    names = SCHEMA["instructions"]["buy_exact_quote_in"]["accounts"]
    recipient = recipient or sorted(FEE_RECIPIENTS_UPGRADE_2026_04_28)[0]
    roles = {name: _key(index + 2) for index, name in enumerate(names)}
    roles["user"] = _key(100)
    roles["quote_mint"] = "So11111111111111111111111111111111111111112"
    roles["quote_token_program"] = TOKEN
    roles["program"] = PROGRAM
    fee_ata = associated_token_address(recipient, roles["quote_mint"], TOKEN)
    remaining = [_key(120 + index) for index in range(remaining_count)]
    if remaining_count >= 2:
        remaining[-2:] = [recipient, fee_ata]

    ordered_keys = [roles["user"]]
    ordered_keys.extend(value for name, value in roles.items() if name != "user")
    ordered_keys.extend(value for value in remaining if value != recipient)
    ordered_keys.append(recipient)
    role_indices = {value: index for index, value in enumerate(ordered_keys)}
    program_index = role_indices[PROGRAM]
    wire = bytes(SCHEMA["instructions"]["buy_exact_quote_in"]["discriminator"])
    wire += (123).to_bytes(8, "little") + (7).to_bytes(8, "little")
    if data_length >= 25:
        wire += b"\x01"
    if data_length == 26:
        wire += b"\x01"
    instructions = [
        {
            "programIdIndex": program_index,
            "accounts": [role_indices[value] for value in roles.values()]
            + [role_indices[value] for value in remaining],
            "data": encode58(wire),
        }
    ]
    return {
        "version": "legacy",
        "transaction": {
            "signatures": ["signature"],
            "message": {
                "header": {
                    "numRequiredSignatures": 1,
                    "numReadonlySignedAccounts": 0,
                    "numReadonlyUnsignedAccounts": 1,
                },
                "accountKeys": ordered_keys,
                "instructions": instructions,
            },
        },
        "meta": {
            "err": {"InstructionError": [0, "Custom"]} if failed else None,
            "innerInstructions": [],
            "preTokenBalances": [],
            "postTokenBalances": [],
        },
    }


def test_v1_fee_pair_is_only_a_structural_candidate():
    result = inspect_buy_exact_quote_in_v1(_transaction())
    reference = result["references"][0]

    assert FEE_RECIPIENTS_DOC_COMMIT == "8cda1fa30ea658b20909d8aedf002047119388d2"
    assert result["status"] == "structural_references_only"
    assert reference["argument_state"] == "pinned_idl_v1_shape"
    assert reference["arguments"] == {
        "spendable_quote_in": 123,
        "min_base_amount_out": 7,
        "track_volume": True,
        "raw_integer_units": True,
    }
    assert reference["suffix_state"] == "documented_fee_pair_suffix_candidate"
    assert reference["identified_roles"]["quote_mint"] == (
        "So11111111111111111111111111111111111111112"
    )
    assert reference["settlement_state"] == "not_assessed"
    assert result["admission"] == "none"
    assert not result["training_eligible"]
    assert not result["execution_supported"]


def test_fee_pair_requires_ata_derivation_and_conservative_privilege_checks():
    transaction = _transaction()
    instruction = transaction["transaction"]["message"]["instructions"][0]
    instruction["accounts"][-1] = instruction["accounts"][0]
    reference = inspect_buy_exact_quote_in_v1(transaction)["references"][0]
    assert reference["suffix_state"] == "fee_pair_unqualified"
    assert not reference["suffix_checks"][
        "fee_ata_derives_from_recipient_quote_mint_and_quote_program"
    ]

    transaction = _transaction()
    transaction["transaction"]["message"]["header"]["numReadonlyUnsignedAccounts"] = 0
    reference = inspect_buy_exact_quote_in_v1(transaction)["references"][0]
    assert reference["suffix_state"] == "fee_pair_unqualified"
    assert not reference["suffix_checks"]["fee_recipient_not_transaction_writable"]


@pytest.mark.parametrize("recipient", sorted(FEE_RECIPIENTS_UPGRADE_2026_04_28))
def test_every_pinned_fee_recipient_accepts_only_its_derived_ata(recipient):
    reference = inspect_buy_exact_quote_in_v1(_transaction(recipient=recipient))["references"][0]

    assert reference["suffix_state"] == "documented_fee_pair_suffix_candidate"
    assert reference["suffix_checks"]["fee_ata_derives_from_recipient_quote_mint_and_quote_program"]


def test_failed_transaction_keeps_attempt_but_has_no_settlement():
    result = inspect_buy_exact_quote_in_v1(_transaction(failed=True))

    assert result["status"] == "failed_tx_attempt_only"
    assert result["references"][0]["transaction_status"] == "failed_tx_attempt_only"
    assert result["references"][0]["economic_state"] == "failed_tx_no_settlement"
    assert result["settlement_state"] == "none_failed_transaction"


def test_extra_predecessor_stays_unresolved_even_with_valid_fee_pair():
    reference = inspect_buy_exact_quote_in_v1(_transaction(remaining_count=3))["references"][0]

    assert reference["suffix_state"] == "documented_fee_pair_with_unresolved_predecessor"
    assert reference["economic_state"] == "quarantined_unresolved_suffix_or_layout"


def test_account_role_alias_is_visible_without_claiming_economic_support():
    transaction = _transaction()
    accounts = transaction["transaction"]["message"]["instructions"][0]["accounts"]
    accounts[6] = accounts[8]

    reference = inspect_buy_exact_quote_in_v1(transaction)["references"][0]

    assert reference["base_account_prefix_state"] == "pinned_idl_prefix_candidate"
    assert "user_quote_token_account" in next(
        aliases
        for aliases in reference["base_account_role_aliases"].values()
        if "user_quote_token_account" in aliases
    )
    assert reference["economic_state"] == "not_assessed"


@pytest.mark.parametrize("data_length", [24, 26])
def test_unverified_instruction_data_lengths_stay_quarantined(data_length):
    reference = inspect_buy_exact_quote_in_v1(_transaction(data_length=data_length))["references"][
        0
    ]

    assert reference["argument_state"] == "unsupported_payload_shape"
    assert reference["economic_state"] == "quarantined_unverified_instruction_payload"


@pytest.mark.parametrize("remaining_count", [0, 1, 4, 7])
def test_unsupported_remaining_account_shapes_stay_quarantined(remaining_count):
    reference = inspect_buy_exact_quote_in_v1(_transaction(remaining_count=remaining_count))[
        "references"
    ][0]

    assert reference["suffix_state"] == "unsupported_suffix_layout"


def _buy_exact_compiled_transaction():
    recipient = sorted(FEE_RECIPIENTS_UPGRADE_2026_04_28)[0]
    names = SCHEMA["instructions"]["buy_exact_quote_in"]["accounts"]
    roles = {name: _key(index + 30) for index, name in enumerate(names)}
    roles.update(
        user=_key(2),
        base_mint=_key(3),
        quote_mint=_key(4),
        base_token_program=TOKEN,
        quote_token_program=TOKEN,
        system_program="11111111111111111111111111111111",
        associated_token_program="ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
        program=PROGRAM,
    )
    fee_ata = associated_token_address(recipient, roles["quote_mint"], TOKEN)
    account_keys = set(roles.values()) | {recipient, fee_ata}
    readonly = {
        recipient,
        PROGRAM,
        TOKEN,
        roles["system_program"],
        roles["associated_token_program"],
        roles["event_authority"],
    }
    writable_keys = [roles["user"]] + sorted(account_keys - readonly - {roles["user"]})
    readonly_keys = sorted(account_keys & readonly)
    account_keys = writable_keys + readonly_keys
    indexes = {key: index for index, key in enumerate(account_keys)}

    def compiled_transfer(source, mint, destination, authority, amount, decimals):
        data = b"\x0c" + amount.to_bytes(8, "little") + bytes([decimals])
        return {
            "programIdIndex": indexes[TOKEN],
            "accounts": [indexes[source], indexes[mint], indexes[destination], indexes[authority]],
            "data": encode58(data),
            "stackHeight": 2,
        }

    def token_balance(account, mint, owner, amount):
        return {
            "accountIndex": indexes[account],
            "mint": mint,
            "owner": owner,
            "programId": TOKEN,
            "uiTokenAmount": {"amount": str(amount), "decimals": 6},
        }

    spendable, minimum, base_out, gross, pool_credit = 100, 50, 60, 100, 98
    instruction_data = (
        bytes(SCHEMA["instructions"]["buy_exact_quote_in"]["discriminator"])
        + spendable.to_bytes(8, "little")
        + minimum.to_bytes(8, "little")
        + b"\x01"
    )
    instruction_accounts = [indexes[roles[name]] for name in names] + [
        indexes[recipient],
        indexes[fee_ata],
    ]
    values = {
        "timestamp": 1234,
        "base_amount_out": base_out,
        "max_quote_amount_in": spendable,
        "user_base_token_reserves": 0,
        "user_quote_token_reserves": 0,
        "pool_base_token_reserves": 1000,
        "pool_quote_token_reserves": 1000,
        "quote_amount_in": gross,
        "lp_fee_basis_points": 100,
        "lp_fee": 10,
        "protocol_fee_basis_points": 100,
        "protocol_fee": 2,
        "quote_amount_in_with_lp_fee": pool_credit,
        "user_quote_amount_in": 88,
        "pool": roles["pool"],
        "user": roles["user"],
        "user_base_token_account": roles["user_base_token_account"],
        "user_quote_token_account": roles["user_quote_token_account"],
        "protocol_fee_recipient": roles["protocol_fee_recipient"],
        "protocol_fee_recipient_token_account": roles["protocol_fee_recipient_token_account"],
        "coin_creator": roles["coin_creator_vault_authority"],
        "coin_creator_fee_basis_points": 0,
        "coin_creator_fee": 0,
        "track_volume": True,
        "total_unclaimed_tokens": 0,
        "total_claimed_tokens": 0,
        "current_sol_volume": 0,
        "last_update_timestamp": 0,
        "min_base_amount_out": minimum,
        "ix_name": "buy_exact_quote_in",
        "cashback_fee_basis_points": 0,
        "cashback": 0,
        "buyback_fee_basis_points": 5000,
        "buyback_fee": 1,
        "virtual_quote_reserves": 0,
        "can_boost": False,
        "base_supply": 0,
        "holder_rewards_bps": 0,
        "holder_rewards": 0,
        "creator_fee_unclaimed": 0,
    }
    event = EVENT_TAG + bytes(SCHEMA["events"]["BuyEvent"])
    for field, kind in SCHEMA["fields"]["BuyEvent"]:
        value = values[field]
        if kind == "pubkey":
            part = bytes(decode58(value))
        elif kind == "string":
            encoded = value.encode()
            part = len(encoded).to_bytes(4, "little") + encoded
        else:
            part = int(value).to_bytes(
                {"u64": 8, "i64": 8, "i128": 16, "bool": 1}[kind],
                "little",
                signed=kind.startswith("i"),
            )
        event += part
    inner = [
        compiled_transfer(
            roles["pool_base_token_account"],
            roles["base_mint"],
            roles["user_base_token_account"],
            roles["pool"],
            base_out,
            6,
        ),
        compiled_transfer(
            roles["user_quote_token_account"],
            roles["quote_mint"],
            roles["pool_quote_token_account"],
            roles["user"],
            pool_credit,
            6,
        ),
        compiled_transfer(
            roles["user_quote_token_account"],
            roles["quote_mint"],
            roles["protocol_fee_recipient_token_account"],
            roles["user"],
            1,
            6,
        ),
        compiled_transfer(
            roles["user_quote_token_account"],
            roles["quote_mint"],
            fee_ata,
            roles["user"],
            1,
            6,
        ),
        {
            "programIdIndex": indexes[PROGRAM],
            "accounts": [indexes[roles["event_authority"]]],
            "data": encode58(event),
            "stackHeight": 2,
        },
    ]
    base_accounts = [
        (roles["user_base_token_account"], roles["base_mint"], roles["user"], 0, 60),
        (roles["pool_base_token_account"], roles["base_mint"], roles["pool"], 1000, 940),
        (roles["user_quote_token_account"], roles["quote_mint"], roles["user"], 1000, 900),
        (roles["pool_quote_token_account"], roles["quote_mint"], roles["pool"], 1000, 1098),
        (
            roles["protocol_fee_recipient_token_account"],
            roles["quote_mint"],
            roles["protocol_fee_recipient"],
            0,
            1,
        ),
        (fee_ata, roles["quote_mint"], recipient, 0, 1),
    ]
    return {
        "version": "legacy",
        "transaction": {
            "signatures": [_key(1)],
            "message": {
                "header": {
                    "numRequiredSignatures": 1,
                    "numReadonlySignedAccounts": 0,
                    "numReadonlyUnsignedAccounts": len(readonly_keys),
                },
                "accountKeys": account_keys,
                "instructions": [
                    {
                        "programIdIndex": indexes[PROGRAM],
                        "accounts": instruction_accounts,
                        "data": encode58(instruction_data),
                    }
                ],
            },
        },
        "meta": {
            "err": None,
            "innerInstructions": [{"index": 0, "instructions": inner}],
            "preTokenBalances": [token_balance(*item[:4]) for item in base_accounts],
            "postTokenBalances": [
                token_balance(item[0], item[1], item[2], item[4]) for item in base_accounts
            ],
        },
    }


def test_buy_exact_v1_reports_atomic_trade_facts_and_full_ledger_without_admission():
    result = diagnose_pumpswap_buy_exact_v1(_buy_exact_compiled_transaction())

    assert result["status"] == "diagnostic_facts_only"
    fact = result["references"][0]
    assert fact["trade_fact_state"] == "exact_instruction_event_transfer_facts_diagnostic_only"
    assert fact["settlement_state"] == "exact"
    assert fact["amounts_atomic_units"]["gross_user_quote_debit"] == 100
    assert fact["amounts_atomic_units"]["pool_quote_credit"] == 98
    assert fact["amounts_atomic_units"]["base_user_credit"] == 60
    assert fact["unit_evidence"]["ui_conversion_performed"] is False
    assert fact["fee_flow_hypotheses"]["pool_credit_less_lp_fee_equals_event_net_user_quote_amount"]
    assert fact["account_ledger"]["target_account_count"] == 6
    assert result["admission"] == "none"
    assert not result["training_eligible"]
    assert not result["execution_supported"]


def test_buy_exact_v1_failed_transaction_is_attempt_only():
    transaction = _buy_exact_compiled_transaction()
    transaction["meta"]["err"] = {"InstructionError": [0, "Custom"]}

    result = diagnose_pumpswap_buy_exact_v1(transaction)

    assert result["status"] == "failed_tx_attempt_only"
    assert result["references"][0]["settlement_state"] == "none_failed_transaction"
    assert "amounts_atomic_units" not in result["references"][0]


@pytest.mark.parametrize("decimals", [0, 5, 7, 9, 18, 255])
def test_atomic_transfer_decimals_are_checked_against_observed_mint_balance(decimals):
    transaction = _buy_exact_compiled_transaction()
    transfer = transaction["meta"]["innerInstructions"][0]["instructions"][0]
    wire = bytearray(decode58(transfer["data"]))
    wire[-1] = decimals
    transfer["data"] = encode58(bytes(wire))

    result = diagnose_pumpswap_buy_exact_v1(transaction)

    assert result["status"] == "quarantined"
    assert result["references"][0]["reason"] == (
        "transfer_checked_mint_program_or_decimal_mismatch"
    )


def test_unrecognized_quote_recipient_remains_quarantined():
    transaction = _buy_exact_compiled_transaction()
    transfer = transaction["meta"]["innerInstructions"][0]["instructions"][1]
    transfer["accounts"][2] = (
        max(index for index, _ in enumerate(transaction["transaction"]["message"]["accountKeys"]))
        - 2
    )

    result = diagnose_pumpswap_buy_exact_v1(transaction)

    assert result["status"] == "quarantined"
    assert result["references"][0]["reason"] == "unrecognized_quote_transfer_destination"


def test_quote_destination_role_alias_remains_quarantined():
    transaction = _buy_exact_compiled_transaction()
    names = SCHEMA["instructions"]["buy_exact_quote_in"]["accounts"]
    instruction_accounts = transaction["transaction"]["message"]["instructions"][0]["accounts"]
    instruction_accounts[names.index("coin_creator_vault_ata")] = instruction_accounts[-1]

    result = diagnose_pumpswap_buy_exact_v1(transaction)

    assert result["status"] == "quarantined"
    assert result["references"][0]["reason"] == "quote_destination_role_alias"


def test_identical_base_and_quote_mints_remain_quarantined():
    transaction = _buy_exact_compiled_transaction()
    names = SCHEMA["instructions"]["buy_exact_quote_in"]["accounts"]
    instruction_accounts = transaction["transaction"]["message"]["instructions"][0]["accounts"]
    instruction_accounts[names.index("base_mint")] = instruction_accounts[names.index("quote_mint")]

    result = diagnose_pumpswap_buy_exact_v1(transaction)

    assert result["status"] == "quarantined"
    assert result["references"][0]["reason"] == "identical_base_quote_mints"


def test_successful_instruction_without_event_is_not_an_exact_fill():
    transaction = _transaction()
    names = SCHEMA["instructions"]["buy_exact_quote_in"]["accounts"]
    instructions = transaction["transaction"]["message"]["instructions"]
    instructions[0]["accounts"][names.index("base_token_program")] = instructions[0]["accounts"][
        names.index("quote_token_program")
    ]
    result = diagnose_pumpswap_buy_exact_v1(transaction)

    assert result["status"] == "quarantined"
    assert result["references"][0]["reason"] == "buy_event_count_or_shape"
    assert "amounts_atomic_units" not in result["references"][0]
