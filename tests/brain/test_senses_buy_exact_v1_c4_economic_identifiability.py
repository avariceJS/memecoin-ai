from __future__ import annotations

import copy

import pytest

from memecoin_pumpswap.decoder import TOKEN, encode58
from memecoin_pumpswap.schema import SCHEMA
from tools.brain_senses_buy_exact_v1_c4_economic_identifiability_v1 import (
    EXPECTED_CATEGORIES,
    IdentifiabilityError,
    _boundary_facts,
    _event_facts,
    _raw_transaction_facts,
    _wire_facts,
    classify_population,
)


def _population() -> list[dict]:
    taxonomy = {
        "successful_structural_pass": (
            "successful_transaction",
            "structural_pass_by_frozen_partition_and_exact_success_identity_complement",
        ),
        "successful_structural_mismatch": (
            "successful_transaction",
            "known_successful_structural_mismatch_from_replay_receipt",
        ),
        "failed_structural_pass": (
            "failed_attempt_only",
            "failed_structural_pass_pool_v2_readonly",
        ),
        "failed_structural_mismatch": (
            "failed_attempt_only",
            "failed_structural_mismatch_pool_v2_writable",
        ),
    }
    rows = []
    index = 0
    for category, count in EXPECTED_CATEGORIES.items():
        status, structure = taxonomy[category]
        for _ in range(count):
            censor = None
            if category == "successful_structural_pass" and index < 4:
                censor = (
                    "censored_missing_endpoint"
                    if index < 2
                    else "censored_transaction_boundary_residual"
                )
            rows.append(
                {
                    "identity": {
                        "slot": index,
                        "transaction_index": 0,
                        "path": [0],
                        "signature": str(index),
                    },
                    "transaction_state": status,
                    "structural_taxonomy": structure,
                    "economic_admission": False,
                    "candidate_roles": {"base_token_program": TOKEN, "quote_token_program": TOKEN},
                    "legacy_candidate_subtree_vs_boundary_feasibility": {"state": censor},
                }
            )
            index += 1
    return rows


def test_population_reconciles_overlapping_status_and_structure_denominators():
    result = classify_population(_population())
    assert result["category_counts"] == EXPECTED_CATEGORIES
    assert result["successful_references"] == 1_553
    assert result["failed_attempts"] == 780
    assert result["structural_mismatches"] == 16
    assert result["legacy_success_pass_censorings"] == {
        "censored_missing_endpoint": 2,
        "censored_transaction_boundary_residual": 2,
    }
    assert result["economic_admission"] is False


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("duplicate", "candidate_identity_duplicate"),
        ("admit", "economic_admission_claim_present"),
        ("censor", "legacy_censor_partition_mismatch"),
    ],
)
def test_population_rejects_rebound_rows(mutation, reason):
    rows = _population()
    if mutation == "duplicate":
        rows[-1]["identity"] = copy.deepcopy(rows[0]["identity"])
    elif mutation == "admit":
        rows[0]["economic_admission"] = True
    else:
        rows[0]["legacy_candidate_subtree_vs_boundary_feasibility"]["state"] = None
    with pytest.raises(IdentifiabilityError, match=reason):
        classify_population(rows)


def _synthetic_candidate_transaction():
    account_names = SCHEMA["instructions"]["buy_exact_quote_in"]["accounts"]
    role_names = [*account_names, "pool_v2_prefix", "fee_wallet_suffix", "fee_quote_ata_suffix"]
    accounts_by_role = {
        name: encode58(bytes([index + 1]) * 32) for index, name in enumerate(role_names)
    }
    accounts_by_role.update(
        {
            "base_token_program": TOKEN,
            "quote_token_program": TOKEN,
            "system_program": "11111111111111111111111111111111",
            "associated_token_program": "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
            "program": SCHEMA["address"],
        }
    )
    keys = list(dict.fromkeys(accounts_by_role.values()))
    index_by_key = {key: index for index, key in enumerate(keys)}
    pump_ix = {
        "programIdIndex": index_by_key[SCHEMA["address"]],
        "accounts": [index_by_key[accounts_by_role[name]] for name in role_names],
        "data": encode58(bytes(range(25))),
    }
    target_ix = {
        "programIdIndex": index_by_key[TOKEN],
        "accounts": [
            index_by_key[accounts_by_role["user_quote_token_account"]],
            index_by_key[accounts_by_role["pool_quote_token_account"]],
        ],
        "data": encode58(b"\x03" + (123).to_bytes(8, "little")),
        "stackHeight": 2,
    }
    transaction = {
        "version": "legacy",
        "transaction": {
            "signatures": ["candidate-signature"],
            "message": {"accountKeys": keys, "instructions": [pump_ix]},
        },
        "meta": {
            "err": None,
            "fee": 5000,
            "preBalances": [1000 + index for index in range(len(keys))],
            "postBalances": [900 + index for index in range(len(keys))],
            "innerInstructions": [{"index": 0, "instructions": [target_ix]}],
            "preTokenBalances": [],
            "postTokenBalances": [],
        },
    }
    return transaction, accounts_by_role


def test_raw_transaction_projection_keeps_fee_and_lamports_transaction_wide():
    transaction, roles = _synthetic_candidate_transaction()
    facts = _raw_transaction_facts(
        transaction,
        {"signature": "candidate-signature", "path": [0]},
        roles,
        "successful_transaction",
    )
    assert facts["transaction_meta_fee_lamports"] == 5000
    assert facts["lamport_balance_boundary_state"] == "complete_transaction_boundary"
    assert facts["boundary_scope"].startswith("whole transaction")
    assert facts["ordered_target_account_instruction_inventory"][0]["candidate_descendant"] is True
    assert facts["pre_post_mint_or_token_account_state_bytes"] is False


def test_missing_boundary_and_transfer_values_remain_unknown_not_zero():
    assert _boundary_facts({})["missing_endpoint_interpretation"] == "unknown, never zero"
    assert _wire_facts({})["transfer_wire_observations"] == []
    assert _event_facts({})["event_fee_field_raw_values"] == []
