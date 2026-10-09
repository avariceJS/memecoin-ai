import pytest

from memecoin_pumpswap.buy_exact_v1 import (
    FEE_RECIPIENTS_DOC_COMMIT,
    inspect_buy_exact_quote_in_v1,
)
from memecoin_pumpswap.compiled_economics import (
    FEE_RECIPIENTS_UPGRADE_2026_04_28,
    associated_token_address,
)
from memecoin_pumpswap.decoder import PROGRAM, TOKEN, encode58
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
