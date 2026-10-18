from __future__ import annotations

import copy
import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from memecoin_pumpswap.birth_schema import PROFILES
from tools.brain_senses_pumpswap_create_pool_feasibility_v1 import (
    CREATE_POOL_EVENT,
    CREATE_POOL_IX,
    EVENT_AUTHORITY,
    EVENT_TAG,
    EXPECTED_DATASETS,
    GLOBAL_CONFIG,
    NETWORK,
    PROGRAM_ID,
    RESULT_SCHEMA,
    FeasibilityError,
    _canonical,
    _children_by_parent,
    _classify_candidate,
    _parse_fixed_fields,
    _reconcile_model,
    _sha,
    base58_encode,
    verify_projection_model,
)


def _fixture_model():
    stage = {
        "content_sha256": "stage-1-test-pin",
        "inputs": {"ledger": {"sha256": "ledger"}},
        "historical_source_pins": {"program": PROGRAM_ID},
        "implementation_source_hashes": {"verifier": "code"},
        "historical_idl_profile_provenance": [],
        "implementation_baseline_head": "a" * 40,
        "selected_rows": [
            {
                "dataset_id": "pair64",
                "slot": 101,
                "blockhash": "block-101",
                "event_time_unix_seconds": 1234,
                "full_receipt_ns": 2_000,
                "signature_receipt_ns": 2_100,
                "availability_ns": None,
                "expected_program_unique_transaction_count": 1,
                "structural_novelty_instruction_references": 2,
            }
        ],
        "dataset_expected_totals": {},
    }
    for dataset_id in EXPECTED_DATASETS:
        stage["dataset_expected_totals"][dataset_id] = {
            "full_block_transactions": 0,
            "success_transactions": 0,
            "failed_transactions": 0,
            "unknown_status_transactions": 0,
            "outer_instruction_references": 0,
            "inner_instruction_references": 0,
            "unsupported_normalization": 0,
            "program_instruction_references": 0,
            "program_unique_transactions": 0,
            "structural_novelty_instruction_references": 0,
        }
    stage["dataset_expected_totals"]["pair64"].update(
        {
            "full_block_transactions": 1,
            "failed_transactions": 1,
            "outer_instruction_references": 1,
            "program_instruction_references": 1,
            "program_unique_transactions": 1,
            "structural_novelty_instruction_references": 2,
        }
    )
    candidate = {
        "network": NETWORK,
        "dataset_id": "pair64",
        "slot": 101,
        "blockhash": "block-101",
        "signature": "signature-101",
        "path": [0],
        "path_kind": "outer",
        "transaction_status": "failed",
        "instruction_data_length": 8,
        "instruction_data_sha256": "data-hash",
        "account_count": 18,
        "accounts": [f"account-{i}" for i in range(18)],
        "assessment": {
            "historical_profile_status": "exact_historical_profile",
            "event_cpi_status": "uniquely_bound_event_cpi",
            "event_roles_consistent": True,
            "event_role_checks": {"base_mint": True},
            "pool_mint_vault_tuple_consistent": True,
            "transaction_boundary_status": "slot_blockhash_signature_and_status_bound",
            "feasibility_class": "failed_transaction_attempt_only",
        },
    }
    slot = {
        "slot": 101,
        "blockhash": "block-101",
        "block_time_unix_seconds": 1234,
        "receipt_ns": 2_000,
        "signature_receipt_ns": 2_100,
        "availability_ns": None,
        "decision_time_ns": None,
        "full_capture_receipt_path": "work/full/receipt.json",
        "full_capture_receipt_sha256": "full-receipt",
        "full_response_sha256": "full-response",
        "full_stored_sha256": "full-stored",
        "signature_capture_receipt_path": "work/sig/receipt.json",
        "signature_capture_receipt_sha256": "sig-receipt",
        "signature_response_sha256": "sig-response",
        "signature_stored_sha256": "sig-stored",
        "transaction_count": 1,
        "signature_count": 1,
        "selected_capture_status": "full_and_ordered_signatures_verified",
        "expected_program_unique_transaction_count": 1,
        "program_unique_transaction_count": 1,
        "program_unique_transaction_count_matches_ledger": True,
        "structural_novelty_instruction_references": 2,
        "counts": {
            "transactions": 1,
            "failed_transactions": 1,
            "outer_instruction_references": 1,
            "program_instruction_references": 1,
            "program_unique_transaction_count": 1,
            "create_pool_discriminator_references": 1,
            "create_pool_failed_attempts": 1,
            "candidate_outcome:failed_transaction_attempt_only": 1,
            "normalization_failures": 0,
            "unknown_transaction_status": 0,
        },
        "selected_discriminator_zero_permitted": False,
    }
    candidates_by_dataset = {name: [] for name in EXPECTED_DATASETS}
    candidates_by_dataset["pair64"] = [candidate]
    slot_rows = {("pair64", 101): slot}
    dataset_totals = {
        name: {"structural_novelty_instruction_references": 0} for name in EXPECTED_DATASETS
    }
    dataset_totals["pair64"] = {
        "transactions": 1,
        "failed_transactions": 1,
        "outer_instruction_references": 1,
        "program_instruction_references": 1,
        "program_unique_transaction_count": 1,
        "create_pool_discriminator_references": 1,
        "create_pool_failed_attempts": 1,
        "candidate_outcome:failed_transaction_attempt_only": 1,
        "normalization_failures": 0,
        "unknown_transaction_status": 0,
        "structural_novelty_instruction_references": 2,
    }
    model = {
        "candidates_by_dataset": candidates_by_dataset,
        "slot_rows": slot_rows,
        "dataset_totals": dataset_totals,
        "outcomes": {"failed_transaction_attempt_only": 1},
        "cross_dataset": [],
        "duplicate_identity_count_within_capture": 0,
    }
    result = {
        "schema": RESULT_SCHEMA,
        "stage1_sha256": stage["content_sha256"],
        "status": "verified_candidate_feasibility_only",
        "network": NETWORK,
        "target_program_id": PROGRAM_ID,
        "instruction_discriminator_hex": CREATE_POOL_IX.hex(),
        "event_discriminator_hex": CREATE_POOL_EVENT.hex(),
        "scope": "exactly_608_existing_selected_full_blocks_and_ordered_signature_lists",
        "dataset_order": list(EXPECTED_DATASETS),
        "zero_claim_scope": (
            "per-selected-block zero only for this fixed create_pool instruction discriminator "
            "when full capture/signature verification and every transaction normalization/status "
            "are complete"
        ),
        "decision_time_ns": None,
        "time_fields": {
            "receipt_time": "capture receipt received_ns",
            "availability_time": "sealed receipt availability_ns or null",
            "event_time": "blockTime, kept separate",
            "decision_time_ns": None,
        },
        "stage1_provenance": {
            "inputs": stage["inputs"],
            "historical_source_pins": stage["historical_source_pins"],
            "implementation_source_hashes": stage["implementation_source_hashes"],
            "historical_idl_profile_provenance": stage["historical_idl_profile_provenance"],
            "implementation_baseline_head": stage["implementation_baseline_head"],
        },
        "datasets": {
            name: {
                "totals": dataset_totals[name],
                "selected_slots": [slot] if name == "pair64" else [],
                "candidates": candidates_by_dataset[name],
            }
            for name in EXPECTED_DATASETS
        },
        "candidate_outcome_partition": {"failed_transaction_attempt_only": 1},
        "cross_dataset_candidate_overlaps": [],
        "duplicate_candidate_identity_count_within_capture": 0,
        "reconciliation": _reconcile_model(stage, model),
        "claims": {
            "births": False,
            "population_completeness": False,
            "economic_validity": False,
            "admission": False,
            "settlement": False,
            "global_chain_zero": False,
        },
    }
    result["reproducible_content_sha256"] = _sha(_canonical(result))
    return stage, model, result


def _reseal(result):
    result.pop("reproducible_content_sha256", None)
    result["reproducible_content_sha256"] = _sha(_canonical(result))
    return result


def test_fixed_discriminators_and_profile_arg_parser_are_exact():
    assert hashlib.sha256(b"global:create_pool").digest()[:8] == CREATE_POOL_IX
    assert hashlib.sha256(b"event:CreatePoolEvent").digest()[:8] == CREATE_POOL_EVENT
    profile = PROFILES[3]
    assert bytes(profile["discriminator"]) == CREATE_POOL_EVENT
    with pytest.raises(ValueError, match="truncated_or_unsupported_field"):
        _parse_fixed_fields(b"\x01", [{"name": "amount", "type": "u64"}])
    with pytest.raises(ValueError, match="unknown_suffix"):
        _parse_fixed_fields(b"\x01\0", [{"name": "enabled", "type": "bool"}])


@given(
    st.lists(st.sampled_from(["u8", "u16", "u64", "i64", "bool", "pubkey"]), max_size=8), st.data()
)
def test_fixed_field_parser_roundtrips_generated_layouts_and_rejects_suffix(layout_types, data):
    sizes = {"u8": 1, "u16": 2, "u64": 8, "i64": 8, "bool": 1, "pubkey": 32}
    layout = []
    encoded = bytearray()
    expected = {}
    for index, kind in enumerate(layout_types):
        name = f"field_{index}"
        layout.append({"name": name, "type": kind})
        if kind == "pubkey":
            raw = data.draw(st.binary(min_size=32, max_size=32), label=name)
            encoded.extend(raw)
            expected[name] = base58_encode(raw)
        else:
            width = sizes[kind]
            if kind == "bool":
                value = data.draw(st.integers(min_value=0, max_value=1), label=name)
            elif kind == "i64":
                value = data.draw(
                    st.integers(min_value=-(1 << 63), max_value=(1 << 63) - 1), label=name
                )
            else:
                value = data.draw(
                    st.integers(min_value=0, max_value=(1 << (width * 8)) - 1), label=name
                )
            encoded.extend(value.to_bytes(width, "little", signed=kind == "i64"))
            expected[name] = value

    assert _parse_fixed_fields(bytes(encoded), layout) == expected
    with pytest.raises(ValueError, match="unknown_suffix"):
        _parse_fixed_fields(bytes(encoded) + b"\x00", layout)


def _synthetic_exact_candidate(event_base_mint_override: str | None = None):
    profile = PROFILES[3]
    roles = {}
    for i, name in enumerate(profile["accounts"]):
        roles[name] = base58_encode(bytes([i + 1]) * 32)
    roles.update(
        {"program": PROGRAM_ID, "event_authority": EVENT_AUTHORITY, "global_config": GLOBAL_CONFIG}
    )
    args = {}
    for item in profile["args"]:
        args[item["name"]] = base58_encode(bytes([77]) * 32) if item["type"] == "pubkey" else 1
    coin_creator = args.get("coin_creator")
    event_values = {}
    for field in profile["fields"]:
        name = field["name"]
        if name == "base_mint" and event_base_mint_override is not None:
            value = event_base_mint_override
        elif name in roles:
            value = roles[name]
        elif name == "coin_creator" and coin_creator is not None:
            value = coin_creator
        elif name in args:
            value = args[name]
        elif name.endswith("_decimals"):
            value = 6
        elif name == "timestamp":
            value = 1234
        else:
            value = 1
        event_values[name] = value

    def encode(layout, values):
        out = bytearray()
        for field in layout:
            name, typ = field["name"], field["type"]
            value = values[name]
            if typ == "pubkey":
                # Fixture keys are deterministic base58 encodings of 32-byte arrays.
                n = 0
                for char in value:
                    n = n * 58 + "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz".index(
                        char
                    )
                raw = n.to_bytes(32, "big")
                out.extend(raw)
            else:
                out.extend(
                    int(value).to_bytes(
                        {"u8": 1, "u16": 2, "u64": 8, "i64": 8, "bool": 1}[typ],
                        "little",
                        signed=typ == "i64",
                    )
                )
        return bytes(out)

    account_list = [roles[name] for name in profile["accounts"]]
    ix_data = CREATE_POOL_IX + encode(profile["args"], args)
    event_data = EVENT_TAG + CREATE_POOL_EVENT + encode(profile["fields"], event_values)
    ix = {
        "path": [0],
        "stack_height": 1,
        "program_id": PROGRAM_ID,
        "accounts": account_list,
        "data_hex": ix_data.hex(),
    }
    event_ix = {
        "path": [0, 0],
        "stack_height": 2,
        "program_id": PROGRAM_ID,
        "accounts": [EVENT_AUTHORITY],
        "data_hex": event_data.hex(),
    }
    normalized = {
        "outer_instructions": [ix],
        "inner_instructions": [event_ix],
        "accounts": [{"index": i, "pubkey": key} for i, key in enumerate(account_list)],
    }

    def idx(key):
        return account_list.index(key)

    tx = {
        "meta": {
            "postTokenBalances": [
                {
                    "accountIndex": idx(roles["pool_base_token_account"]),
                    "mint": roles["base_mint"],
                    "uiTokenAmount": {"decimals": event_values["base_mint_decimals"]},
                },
                {
                    "accountIndex": idx(roles["pool_quote_token_account"]),
                    "mint": roles["quote_mint"],
                    "uiTokenAmount": {"decimals": event_values["quote_mint_decimals"]},
                },
            ]
        }
    }
    candidate = {"path": [0], "instruction": ix}
    assessment = _classify_candidate(
        tx, normalized, candidate, _children_by_parent(normalized), "success"
    )
    return assessment


def test_event_cpi_roles_and_vault_tuple_are_separately_checked():
    good = _synthetic_exact_candidate()
    assert good["historical_profile_status"] == "exact_historical_profile"
    assert good["event_cpi_status"] == "uniquely_bound_event_cpi"
    assert good["event_roles_consistent"] is True
    assert good["pool_mint_vault_tuple_consistent"] is True
    assert good["feasibility_class"] == "successful_structural_feasibility_candidate"
    wrong = base58_encode(bytes([99]) * 32)
    bad = _synthetic_exact_candidate(wrong)
    assert bad["event_role_checks"]["base_mint"] is False
    assert bad["event_roles_consistent"] is False
    assert bad["pool_mint_vault_tuple_status"] == "role_mint_mismatch"
    assert bad["feasibility_class"] == "role_or_tuple_inconsistent_or_unknown"


@pytest.mark.parametrize(
    "mutation",
    [
        "omit",
        "duplicate",
        "slot",
        "blockhash",
        "failed_to_success",
        "event_role",
        "time_conflation",
    ],
)
def test_independent_projection_verifier_rejects_candidate_and_time_mutations(mutation):
    stage, model, result = _fixture_model()
    altered = copy.deepcopy(result)
    candidate = altered["datasets"]["pair64"]["candidates"][0]
    if mutation == "omit":
        altered["datasets"]["pair64"]["candidates"] = []
    elif mutation == "duplicate":
        altered["datasets"]["pair64"]["candidates"].append(copy.deepcopy(candidate))
    elif mutation == "slot":
        candidate["slot"] = 102
    elif mutation == "blockhash":
        candidate["blockhash"] = "substituted-blockhash"
    elif mutation == "failed_to_success":
        candidate["transaction_status"] = "success"
        candidate["assessment"]["feasibility_class"] = "successful_structural_feasibility_candidate"
    elif mutation == "event_role":
        candidate["assessment"]["event_role_checks"]["base_mint"] = False
        candidate["assessment"]["event_roles_consistent"] = False
    else:
        altered["datasets"]["pair64"]["selected_slots"][0]["receipt_ns"] = 1234
    _reseal(altered)
    with pytest.raises(FeasibilityError):
        verify_projection_model(stage, model, altered)


def test_projection_verifier_rejects_unselected_or_extra_dataset_rows():
    stage, model, result = _fixture_model()
    altered = copy.deepcopy(result)
    altered["datasets"]["c3:20261010T050000Z"]["selected_slots"].append(
        copy.deepcopy(altered["datasets"]["pair64"]["selected_slots"][0])
    )
    _reseal(altered)
    with pytest.raises(FeasibilityError):
        verify_projection_model(stage, model, altered)
