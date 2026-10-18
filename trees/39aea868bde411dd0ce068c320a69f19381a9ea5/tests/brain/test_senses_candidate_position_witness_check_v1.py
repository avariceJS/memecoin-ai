from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.brain_senses_candidate_position_witness_check_v1 import (  # noqa: E402
    CONTRACT,
    SELECTOR_ID,
    SELECTOR_SOURCE_SHA256,
    SYNTHETIC_EXECUTION_RUNTIME,
    TRANSITION_EFFECTS_SCHEMA,
    canonical_json_bytes,
    check_witness,
    receipt_sha256,
)


def _account(role: str, pubkey: str, writable: bool, data: bytes = b"raw") -> dict:
    return {
        "semantic_role": role,
        "pubkey": pubkey,
        "is_writable": writable,
        "owner": "owner-program",
        "lamports_atomic_units": 42,
        "data_length_bytes": len(data),
        "data_sha256": hashlib.sha256(data).hexdigest(),
        "data_bytes_hex": data.hex(),
        "executable": False,
        "rent_epoch": 7,
        "state_capture_status": "exact",
    }


def _frame(
    path: list[int],
    trace_index: int,
    child_ordinal: int,
    stack_height: int,
    program_id: str,
    roles: list[tuple[str, str, bool]],
    *,
    lifecycle: str = "returned_success",
    program_kind: str = "deployed_bytecode",
    data_byte: int = 0x41,
) -> tuple[dict, dict, dict]:
    instruction_hash = hashlib.sha256(bytes([data_byte])).hexdigest()
    identity = {
        "path": path,
        "trace_index": trace_index,
        "child_ordinal": child_ordinal,
        "stack_height": stack_height,
        "program_id": program_id,
        "instruction_data_sha256": instruction_hash,
    }
    required = [
        {"semantic_role": role, "pubkey": pubkey, "is_writable": writable}
        for role, pubkey, writable in roles
    ]
    pre = [_account(role, pubkey, writable, bytes([data_byte])) for role, pubkey, writable in roles]
    post = [
        _account(role, pubkey, writable, bytes([data_byte, 0x42]))
        for role, pubkey, writable in roles
    ]
    if program_kind == "deployed_bytecode":
        code = b"synthetic-program-code"
        binding = {
            "kind": program_kind,
            "binding_status": "exact",
            "program_id": program_id,
            "loader_id": "synthetic-loader",
            "deployment_slot": 100,
            "effective_slot": 100,
            "runtime_build_id": "agave-synthetic-build",
            "active_feature_set_hash": "e" * 64,
            "executable_bytes_hex": code.hex(),
            "executable_sha256": hashlib.sha256(code).hexdigest(),
        }
    else:
        binding = {
            "kind": program_kind,
            "binding_status": "exact",
            "program_id": program_id,
            "runtime_build_id": "agave-synthetic-build",
            "active_feature_set_hash": "e" * 64,
            "implementation_id": f"synthetic:{program_id}",
        }
    frame = {
        **identity,
        "lifecycle": lifecycle,
        "program_binding": binding,
        "pre_state": pre,
        "post_state": post,
    }
    return frame, {**identity}, {"path": path, "required_accounts": required, **identity}


def _seal(witness: dict, clocks: list[tuple[int, int, str, str]] | None = None) -> dict:
    existing_receipts = witness.get("receipts")
    if isinstance(existing_receipts, list):
        witness.pop("receipts", None)
    if clocks is None:
        clocks = [(1000 + i * 10, 2 + i, f"host-{i}", f"boot-{i}") for i in range(5)]
    if all(uncertainty is not None for _, uncertainty, _, _ in clocks):
        witness["admission_availability_upper_bound_utc_ms"] = max(
            observed + uncertainty for observed, uncertainty, _, _ in clocks
        )
    else:
        witness["admission_availability_upper_bound_utc_ms"] = None
    body_hash = hashlib.sha256(canonical_json_bytes(witness)).hexdigest()
    if isinstance(existing_receipts, list) and len(existing_receipts) == 5:
        payloads = [copy.deepcopy(item["payload"]) for item in existing_receipts]
    else:
        payloads = [
            {
                "witness_sha256": body_hash,
                "artifact_immutable": True,
                "fsync_confirmed": True,
            },
            {
                "bank_instance_id": "bank-instance-1",
                "slot": 900,
                "parent_slot": 899,
                "commit_batch_id": "batch-2",
                "commit_batch_index": 0,
                "signature": witness["signature"],
                "message_sha256": witness["message_sha256"],
                "commit_result": (
                    "failed_attempt_rollback_accounts_committed"
                    if witness["execution"]["outcome"] == "failed_execution"
                    else "committed_to_bank"
                ),
            },
            {
                "bank_instance_id": "bank-instance-1",
                "slot": 900,
                "parent_slot": 899,
                "bank_state_hash": "bank-hash-1",
                "parent_bank_state_hash": "parent-bank-hash-1",
                "freeze_result": "frozen",
            },
            {
                "bank_instance_id": "bank-instance-1",
                "slot": 900,
                "parent_slot": 899,
                "bank_state_hash": "bank-hash-1",
                "parent_bank_state_hash": "parent-bank-hash-1",
                "ledger_blockhash": "ledger-block-hash-1",
                "parent_ledger_blockhash": "parent-ledger-block-hash-1",
                "signature": witness["signature"],
                "message_sha256": witness["message_sha256"],
                "commit_batch_id": "batch-2",
                "commit_batch_index": 0,
                "block_transaction_index": 3,
                "unique_join_status": "unique_exact",
            },
            {
                "bank_instance_id": "bank-instance-1",
                "slot": 900,
                "parent_slot": 899,
                "bank_state_hash": "bank-hash-1",
                "parent_bank_state_hash": "parent-bank-hash-1",
                "ledger_blockhash": "ledger-block-hash-1",
                "parent_ledger_blockhash": "parent-ledger-block-hash-1",
                "status": "rooted",
                "bank_ledger_binding_status": "unique_exact",
            },
        ]
    payloads[0]["witness_sha256"] = body_hash
    receipts = []
    previous = None
    for index, (kind, payload, clock) in enumerate(
        zip(
            ("capture_seal", "bank_commit", "bank_freeze", "block_join", "root_status"),
            payloads,
            clocks,
            strict=True,
        )
    ):
        observed, uncertainty, host, boot = clock
        receipt = {
            "type": kind,
            "previous_receipt_sha256": previous,
            "payload": payload,
            "observed_utc_ms": observed,
            "clock_uncertainty_upper_bound_ms": uncertainty,
            "clock_uncertainty_status": "unknown" if uncertainty is None else "bounded",
            "clock_source": "synthetic-clock",
            "host_id": host,
            "boot_id": boot,
            "monotonic_ns": index * 100,
        }
        receipt["receipt_sha256"] = receipt_sha256(receipt)
        previous = receipt["receipt_sha256"]
        receipts.append(receipt)
    witness["receipts"] = receipts
    return witness


def _witness(
    *,
    failed: bool = False,
    program_kind: str = "deployed_bytecode",
    candidate_data: bytes = b"candidate-position-a",
) -> dict:
    candidate_roles = [
        ("candidate_token_account", "candidate-token-account", True),
        ("candidate_mint_readonly", "mint", False),
        ("pool_vault", "pool-vault", True),
    ]
    nested_roles = [
        ("source_token_account", "source", True),
        ("destination_token_account", "destination", True),
        ("mint_readonly", "mint", False),
        ("transfer_fee_config_readonly", "mint", False),
        ("source_withheld_fee_amount_readonly", "source", False),
        ("destination_withheld_fee_amount_readonly", "destination", False),
        ("hook_extra_account_readonly", "hook-extra", False),
    ]
    outer, outer_trace, outer_expected = _frame(
        [0], 0, 0, 1, "pump-swap", candidate_roles, data_byte=0x21
    )
    inner, inner_trace, inner_expected = _frame(
        [0, 0],
        1,
        0,
        2,
        "token-2022",
        nested_roles,
        program_kind=program_kind,
        data_byte=0x22,
    )
    hook, hook_trace, hook_expected = _frame(
        [0, 0, 0],
        2,
        0,
        3,
        "transfer-hook",
        [("hook_extra_account_readonly", "hook-extra", False)],
        data_byte=0x23,
    )
    outer["pre_state"][0]["data_bytes_hex"] = candidate_data.hex()
    outer["pre_state"][0]["data_length_bytes"] = len(candidate_data)
    outer["pre_state"][0]["data_sha256"] = hashlib.sha256(candidate_data).hexdigest()
    witness = {
        "contract": CONTRACT,
        "synthetic_only": True,
        "genesis_hash": "synthetic-genesis",
        "signature": "synthetic-signature",
        "message_sha256": "a" * 64,
        "transaction_boundary": {"before": 100, "after": 100},
        "unit_contract": {
            "lamports": "integer_atomic_units",
            "account_data_length": "bytes",
            "rent_epoch": "epoch_index",
            "token_amounts": "raw_account_bytes_without_decimal_conversion",
        },
        "execution_runtime": copy.deepcopy(SYNTHETIC_EXECUTION_RUNTIME),
        "expected_candidate_frames": [outer_expected, inner_expected, hook_expected],
        "candidate_path_selector": {
            "selector_id": SELECTOR_ID,
            "selector_source_sha256": SELECTOR_SOURCE_SHA256,
            "selection_status": "complete_for_synthetic_fixture",
            "selected_paths": [[0], [0, 0], [0, 0, 0]],
        },
        "post_execution_trace": [outer_trace, inner_trace, hook_trace],
        "frames": [outer, inner, hook],
        "execution": {
            "outcome": "failed_execution" if failed else "successful_execution",
            "disposition": "failed_attempt_exact" if failed else "successful_transaction",
            "candidate_effect_state": "attempted_only" if failed else "committed_candidate_state",
        },
        "transition_effects": {
            "schema": TRANSITION_EFFECTS_SCHEMA,
            "token_2022_transfer": {"status": "attempted_only"}
            if failed
            else {
                "status": "committed_core_before_hook",
                "path": [0, 0],
                "sequence": 0,
                "mint_pubkey": "mint",
                "gross_token_atomic_units": 5,
                "fee_token_atomic_units": 1,
                "source_is_native": False,
                "accounts": {
                    "source_token_account": {
                        "pubkey": "source",
                        "pre_base_amount_token_atomic_units": 11,
                        "core_post_base_amount_token_atomic_units": 6,
                        "pre_withheld_token_atomic_units": 2,
                        "core_post_withheld_token_atomic_units": 2,
                    },
                    "destination_token_account": {
                        "pubkey": "destination",
                        "pre_base_amount_token_atomic_units": 20,
                        "core_post_base_amount_token_atomic_units": 24,
                        "pre_withheld_token_atomic_units": 3,
                        "core_post_withheld_token_atomic_units": 4,
                    },
                },
                "hook_entry": {
                    "path": [0, 0, 0],
                    "sequence": 1,
                    "gross_token_atomic_units": 5,
                },
            },
            "wsol_operations": [],
            "wsol_instruction_trace": [],
        },
        "fork_finality_axis": "rooted",
        "candidate_position_label": "unknown_or_exact_frame_state_only",
    }
    return _seal(witness)


def _reseal(witness: dict, clocks: list[tuple[int, int, str, str]] | None = None) -> dict:
    return _seal(witness, clocks)


def _rechain_receipts(witness: dict) -> dict:
    previous = None
    for receipt in witness["receipts"]:
        receipt["previous_receipt_sha256"] = previous
        receipt["receipt_sha256"] = receipt_sha256(receipt)
        previous = receipt["receipt_sha256"]
    return witness


def _assert_synthetic_only(result: dict) -> None:
    assert result["economic_admission"] is False
    assert result["source_capability_qualified"] is False
    assert result["real_runtime_or_provider_completeness_certified"] is False


def _bind_wsol_instruction_trace(witness: dict) -> None:
    witness["transition_effects"]["wsol_instruction_trace"] = [
        {
            "sequence": operation["sequence"],
            "path": copy.deepcopy(operation["path"]),
            "kind": operation["kind"],
            "program_id": operation["program_id"],
            "lifecycle": "returned_success",
        }
        for operation in witness["transition_effects"]["wsol_operations"]
    ]


def test_non_object_input_fails_closed_and_never_sets_admission_flags():
    result = check_witness([])
    assert result["synthetic_contract_valid"] is False
    _assert_synthetic_only(result)


def test_positive_complete_synthetic_chain_is_consistency_only():
    witness = _witness()
    result = check_witness(witness)
    assert result["synthetic_contract_valid"] is True
    assert result["availability_status"] == "bounded"
    assert result["admission_availability_upper_bound_utc_ms"] == 1046
    _assert_synthetic_only(result)


def test_equal_transaction_endpoints_can_have_distinct_candidate_position_states():
    first_witness = _witness(candidate_data=b"candidate-position-a")
    second_witness = _witness(candidate_data=b"candidate-position-b")
    assert first_witness["transaction_boundary"] == second_witness["transaction_boundary"]
    first = check_witness(first_witness)
    second = check_witness(second_witness)
    assert first["synthetic_contract_valid"] is True
    assert second["synthetic_contract_valid"] is True
    _assert_synthetic_only(first)
    _assert_synthetic_only(second)


def test_failed_transaction_keeps_exact_attempt_and_rollback_separate_from_zero():
    witness = _witness(failed=True)
    result = check_witness(witness)
    assert result["synthetic_contract_valid"] is True
    assert witness["execution"]["disposition"] == "failed_attempt_exact"
    assert witness["execution"]["candidate_effect_state"] == "attempted_only"
    _assert_synthetic_only(result)


def test_token2022_fee_hook_fixture_requires_readonly_hook_state():
    witness = _witness()
    for endpoint in ("pre_state", "post_state"):
        witness["frames"][1][endpoint] = [
            item
            for item in witness["frames"][1][endpoint]
            if item["semantic_role"] != "hook_extra_account_readonly"
        ]
    result = check_witness(_reseal(witness))
    assert result["synthetic_contract_valid"] is False
    assert "post_state_account_role_set_incomplete" in result["diagnostics"]


def test_wsol_sync_close_fixture_keeps_lamports_and_raw_token_units_distinct():
    witness = _witness()
    witness["wsol_role_names"] = ["wsol_token_account", "wsol_lamport_account"]
    witness["ordered_effects"] = [
        {
            "sequence": 0,
            "effect": "SyncNative",
            "lamports_field": "lamports_atomic_units",
            "token_amount_encoding": "raw_account_bytes_without_decimal_conversion",
        },
        {
            "sequence": 1,
            "effect": "CloseAccount",
            "lamports_field": "lamports_atomic_units",
            "token_amount_encoding": "raw_account_bytes_without_decimal_conversion",
        },
    ]
    witness["transition_effects"]["wsol_operations"] = [
        {
            "kind": "SyncNative",
            "sequence": 0,
            "path": [0, 1],
            "program_id": "synthetic-token-program",
            "account": {"pubkey": "wsol-account"},
            "reported_rent_reserve_lamports": 2,
            "before": {
                "lamports_atomic_units": 10_000_000,
                "token_amount_atomic_units": 8,
                "is_native": True,
                "is_native_reserve_lamports": 2,
            },
            "after": {
                "lamports_atomic_units": 10_000_000,
                "token_amount_atomic_units": 9_999_998,
                "is_native": True,
                "is_native_reserve_lamports": 2,
            },
        },
        {
            "kind": "CloseAccount",
            "sequence": 1,
            "path": [0, 2],
            "program_id": "synthetic-token-program",
            "source": {
                "pubkey": "wsol-account",
                "pre_lamports_atomic_units": 10_000_000,
                "post_lamports_atomic_units": 0,
                "is_native": True,
                "post_state": "closed",
                "base_amount_token_atomic_units": 9_999_998,
            },
            "destination": {
                "pubkey": "wallet",
                "pre_lamports_atomic_units": 100,
                "post_lamports_atomic_units": 10_000_100,
            },
        },
    ]
    witness["transition_effects"]["wsol_instruction_trace"] = [
        {
            "sequence": 0,
            "path": [0, 1],
            "kind": "SyncNative",
            "program_id": "synthetic-token-program",
            "lifecycle": "returned_success",
        },
        {
            "sequence": 1,
            "path": [0, 2],
            "kind": "CloseAccount",
            "program_id": "synthetic-token-program",
            "lifecycle": "returned_success",
        },
    ]
    witness["unit_contract"]["token_amounts"] = "raw_account_bytes_without_decimal_conversion"
    result = check_witness(_reseal(witness))
    assert result["synthetic_contract_valid"] is True
    _assert_synthetic_only(result)

    failed_trace = copy.deepcopy(witness)
    failed_trace["transition_effects"]["wsol_instruction_trace"][0]["lifecycle"] = "returned_error"
    result = check_witness(_reseal(failed_trace))
    assert "wsol_instruction_trace_not_successful" in result["diagnostics"]

    discontinuous = copy.deepcopy(witness)
    discontinuous["transition_effects"]["wsol_operations"][1]["source"][
        "pre_lamports_atomic_units"
    ] += 1
    result = check_witness(_reseal(discontinuous))
    assert "wsol_sync_close_lamports_discontinuous" in result["diagnostics"]

    nonnative = copy.deepcopy(witness)
    nonnative["transition_effects"]["wsol_operations"][0]["before"]["is_native"] = False
    result = check_witness(_reseal(nonnative))
    assert "wsol_sync_native_account_not_native" in result["diagnostics"]

    bad_prior_reserve = copy.deepcopy(witness)
    bad_prior_reserve["transition_effects"]["wsol_operations"][0]["before"][
        "is_native_reserve_lamports"
    ] = "two"
    result = check_witness(_reseal(bad_prior_reserve))
    assert "wsol_sync_native_before_reserve_invalid" in result["diagnostics"]

    nonnative_close = copy.deepcopy(witness)
    nonnative_close["transition_effects"]["wsol_operations"][1]["source"]["is_native"] = False
    result = check_witness(_reseal(nonnative_close))
    assert "wsol_close_source_not_native" in result["diagnostics"]

    different_program = copy.deepcopy(witness)
    different_program["transition_effects"]["wsol_operations"][1]["program_id"] = (
        "different-synthetic-token-program"
    )
    different_program["transition_effects"]["wsol_instruction_trace"][1]["program_id"] = (
        "different-synthetic-token-program"
    )
    result = check_witness(_reseal(different_program))
    assert "wsol_sync_close_program_mismatch" in result["diagnostics"]

    nonzero_sequence = copy.deepcopy(witness)
    for item, legacy in zip(
        nonzero_sequence["transition_effects"]["wsol_operations"],
        nonzero_sequence["ordered_effects"],
        strict=True,
    ):
        item["sequence"] += 5
        legacy["sequence"] += 5
    for item in nonzero_sequence["transition_effects"]["wsol_instruction_trace"]:
        item["sequence"] += 5
    result = check_witness(_reseal(nonzero_sequence))
    assert "wsol_transition_order_invalid" in result["diagnostics"]

    mismatched_kind = copy.deepcopy(witness)
    mismatched_kind["ordered_effects"][0]["effect"] = "CloseAccount"
    result = check_witness(_reseal(mismatched_kind))
    assert "wsol_ordered_effect_kind_mismatch" in result["diagnostics"]

    unbound_labels = copy.deepcopy(witness)
    unbound_labels["wsol_role_names"] = []
    unbound_labels["ordered_effects"] = None
    result = check_witness(_reseal(unbound_labels))
    assert "wsol_transition_roles_missing_or_invalid" in result["diagnostics"]
    assert "wsol_ordered_effect_list_mismatch" in result["diagnostics"]

    missing_structured_list = copy.deepcopy(witness)
    missing_structured_list["transition_effects"]["wsol_operations"] = None
    result = check_witness(_reseal(missing_structured_list))
    assert "wsol_transition_operations_invalid" in result["diagnostics"]


def test_wsol_wrong_unit_or_effect_order_fails_closed():
    witness = _witness()
    witness["wsol_role_names"] = ["wsol_token_account"]
    witness["ordered_effects"] = [
        {
            "sequence": 0,
            "effect": "SyncNative",
            "lamports_field": "token_atomic_units",
            "token_amount_encoding": "ui_decimal_amount",
        },
        {
            "sequence": 1,
            "effect": "CloseAccount",
            "lamports_field": "lamports_atomic_units",
            "token_amount_encoding": "raw_account_bytes_without_decimal_conversion",
        },
    ]
    result = check_witness(_reseal(witness))
    assert "wsol_lamports_unit_mismatch" in result["diagnostics"]
    assert "wsol_token_unit_mismatch" in result["diagnostics"]


def test_token_2022_transition_requires_checked_core_deltas_and_hook_amount():
    witness = _witness()
    transfer = witness["transition_effects"]["token_2022_transfer"]
    transfer["accounts"]["destination_token_account"][
        "core_post_base_amount_token_atomic_units"
    ] += 1
    transfer["hook_entry"]["gross_token_atomic_units"] += 1
    result = check_witness(_reseal(witness))
    assert "token_2022_destination_delta_mismatch" in result["diagnostics"]
    assert "token_2022_hook_amount_mismatch" in result["diagnostics"]


def test_failed_hook_execution_never_claims_committed_token_effects():
    witness = _witness(failed=True)
    witness["transition_effects"]["token_2022_transfer"] = {
        "status": "attempted_only",
        "gross_token_atomic_units": 5,
    }
    result = check_witness(_reseal(witness))
    assert "failed_execution_committed_transition_assertion" in result["diagnostics"]


def test_successful_transaction_cannot_claim_a_failed_transfer_hook_checkpoint():
    witness = _witness()
    witness["frames"][2]["lifecycle"] = "returned_error"
    result = check_witness(_reseal(witness))
    assert "token_2022_transition_frame_not_successful" in result["diagnostics"]


def test_native_token_transfer_rejects_lamport_underfunding():
    witness = _witness()
    transfer = witness["transition_effects"]["token_2022_transfer"]
    transfer["source_is_native"] = True
    source = transfer["accounts"]["source_token_account"]
    source.update(
        {
            "pre_lamports_atomic_units": 10,
            "core_post_lamports_atomic_units": 5,
        }
    )
    destination = transfer["accounts"]["destination_token_account"]
    destination.update(
        {
            "pre_lamports_atomic_units": 100,
            "core_post_lamports_atomic_units": 105,
        }
    )
    valid = check_witness(_reseal(witness))
    assert valid["synthetic_contract_valid"] is True
    _assert_synthetic_only(valid)

    source["pre_lamports_atomic_units"] = 4
    source["core_post_lamports_atomic_units"] = 0
    result = check_witness(_reseal(witness))
    assert "token_2022_native_source_lamports_insufficient" in result["diagnostics"]


@pytest.mark.parametrize(
    ("field", "value", "diagnostic"),
    [
        ("sequence", True, "token_2022_transition_order_invalid"),
        ("path", [False, 0], "token_2022_transition_path_invalid"),
    ],
)
def test_token_2022_transition_rejects_bool_aliases(field: str, value, diagnostic: str):
    witness = _witness()
    witness["transition_effects"]["token_2022_transfer"][field] = value
    result = check_witness(_reseal(witness))
    assert diagnostic in result["diagnostics"]


def test_wsol_sync_and_close_reject_wrong_reserve_or_lamport_conservation():
    witness = _witness()
    witness["transition_effects"]["wsol_operations"] = [
        {
            "kind": "SyncNative",
            "sequence": 0,
            "path": [0, 1],
            "program_id": "synthetic-token-program",
            "account": {"pubkey": "wsol-account"},
            "reported_rent_reserve_lamports": 2,
            "before": {
                "lamports_atomic_units": 10,
                "token_amount_atomic_units": 0,
                "is_native": True,
                "is_native_reserve_lamports": 2,
            },
            "after": {
                "lamports_atomic_units": 10,
                "token_amount_atomic_units": 7,
                "is_native": True,
                "is_native_reserve_lamports": 3,
            },
        },
        {
            "kind": "CloseAccount",
            "sequence": 1,
            "path": [0, 2],
            "program_id": "synthetic-token-program",
            "source": {
                "pubkey": "wsol-account",
                "pre_lamports_atomic_units": 10,
                "post_lamports_atomic_units": 0,
                "is_native": True,
                "post_state": "closed",
                "base_amount_token_atomic_units": 8,
            },
            "destination": {
                "pubkey": "wallet",
                "pre_lamports_atomic_units": 4,
                "post_lamports_atomic_units": 13,
            },
        },
    ]
    _bind_wsol_instruction_trace(witness)
    result = check_witness(_reseal(witness))
    assert "wsol_sync_native_amount_mismatch" in result["diagnostics"]
    assert "wsol_sync_native_stored_reserve_mismatch" in result["diagnostics"]
    assert "wsol_close_lamports_conservation_mismatch" in result["diagnostics"]


@st.composite
def _valid_transfer_balances(draw):
    u64_max = 2**64 - 1
    gross = draw(st.integers(min_value=0, max_value=u64_max))
    fee = draw(st.integers(min_value=0, max_value=gross))
    source_after = draw(st.integers(min_value=0, max_value=u64_max - gross))
    destination_before = draw(st.integers(min_value=0, max_value=u64_max - (gross - fee)))
    withheld_before = draw(st.integers(min_value=0, max_value=u64_max - fee))
    return gross, fee, source_after, destination_before, withheld_before


@given(values=_valid_transfer_balances())
def test_token_2022_transition_equations_hold_over_u64_domain(values):
    gross, fee, source_after, destination_before, withheld_before = values
    witness = _witness()
    transfer = witness["transition_effects"]["token_2022_transfer"]
    transfer["gross_token_atomic_units"] = gross
    transfer["fee_token_atomic_units"] = fee
    transfer["accounts"]["source_token_account"].update(
        {
            "pre_base_amount_token_atomic_units": gross + source_after,
            "core_post_base_amount_token_atomic_units": source_after,
            "pre_withheld_token_atomic_units": 17,
            "core_post_withheld_token_atomic_units": 17,
        }
    )
    transfer["accounts"]["destination_token_account"].update(
        {
            "pre_base_amount_token_atomic_units": destination_before,
            "core_post_base_amount_token_atomic_units": destination_before + gross - fee,
            "pre_withheld_token_atomic_units": withheld_before,
            "core_post_withheld_token_atomic_units": withheld_before + fee,
        }
    )
    transfer["hook_entry"]["gross_token_atomic_units"] = gross
    result = check_witness(_reseal(witness))
    assert result["synthetic_contract_valid"] is True
    _assert_synthetic_only(result)


@st.composite
def _valid_wsol_reserve_cases(draw):
    u64_max = 2**64 - 1
    lamports = draw(st.integers(min_value=0, max_value=u64_max))
    reserve = draw(st.integers(min_value=0, max_value=lamports))
    destination_before = draw(st.integers(min_value=0, max_value=u64_max - lamports))
    return lamports, reserve, destination_before


@given(values=_valid_wsol_reserve_cases())
def test_wsol_sync_reserve_and_close_conservation_hold_at_boundaries(values):
    lamports, reserve, destination_before = values
    witness = _witness()
    amount = lamports - reserve
    witness["wsol_role_names"] = ["wsol_token_account"]
    witness["ordered_effects"] = [
        {
            "sequence": 0,
            "effect": "SyncNative",
            "lamports_field": "lamports_atomic_units",
            "token_amount_encoding": "raw_account_bytes_without_decimal_conversion",
        },
        {
            "sequence": 1,
            "effect": "CloseAccount",
            "lamports_field": "lamports_atomic_units",
            "token_amount_encoding": "raw_account_bytes_without_decimal_conversion",
        },
    ]
    witness["transition_effects"]["wsol_operations"] = [
        {
            "kind": "SyncNative",
            "sequence": 0,
            "path": [0, 1],
            "program_id": "synthetic-token-program",
            "account": {"pubkey": "wsol-account"},
            "reported_rent_reserve_lamports": reserve,
            "before": {
                "lamports_atomic_units": lamports,
                "token_amount_atomic_units": 0,
                "is_native": True,
                "is_native_reserve_lamports": reserve,
            },
            "after": {
                "lamports_atomic_units": lamports,
                "token_amount_atomic_units": amount,
                "is_native": True,
                "is_native_reserve_lamports": reserve,
            },
        },
        {
            "kind": "CloseAccount",
            "sequence": 1,
            "path": [0, 2],
            "program_id": "synthetic-token-program",
            "source": {
                "pubkey": "wsol-account",
                "pre_lamports_atomic_units": lamports,
                "post_lamports_atomic_units": 0,
                "is_native": True,
                "post_state": "closed",
                "base_amount_token_atomic_units": amount,
            },
            "destination": {
                "pubkey": "wallet",
                "pre_lamports_atomic_units": destination_before,
                "post_lamports_atomic_units": destination_before + lamports,
            },
        },
    ]
    _bind_wsol_instruction_trace(witness)
    result = check_witness(_reseal(witness))
    assert result["synthetic_contract_valid"] is True
    _assert_synthetic_only(result)


def test_signature_mismatch_across_block_join_fails_closed():
    witness = _witness()
    witness["receipts"][3]["payload"]["signature"] = "other-signature"
    result = check_witness(_reseal(witness))
    assert "receipt_signature_mismatch" in result["diagnostics"]


def test_path_order_or_readonly_account_omission_fails_closed():
    witness = _witness()
    witness["frames"][1]["pre_state"] = [
        a for a in witness["frames"][1]["pre_state"] if a["semantic_role"] != "mint_readonly"
    ]
    result = check_witness(_reseal(witness))
    assert "pre_state_account_role_set_incomplete" in result["diagnostics"]


def test_trace_index_and_depth_first_path_order_are_bound():
    witness = _witness()
    witness["post_execution_trace"][1]["trace_index"] = 0
    result = check_witness(_reseal(witness))
    assert "post_execution_trace_order_invalid" in result["diagnostics"]


def test_trace_child_requires_prior_parent_frame():
    witness = _witness()
    witness["post_execution_trace"].reverse()
    result = check_witness(_reseal(witness))
    assert "post_execution_trace_parent_missing_or_late" in result["diagnostics"]


def test_trace_rejects_non_depth_first_sibling_order():
    witness = _witness()
    trace = witness["post_execution_trace"]
    trace[1]["path"] = [0, 1]
    trace[1]["child_ordinal"] = 1
    trace[1]["stack_height"] = 2
    trace[2]["path"] = [0, 0]
    trace[2]["child_ordinal"] = 0
    trace[2]["stack_height"] = 2
    result = check_witness(_reseal(witness))
    assert "post_execution_trace_not_depth_first" in result["diagnostics"]
    assert "post_execution_trace_child_ordinals_not_contiguous" in result["diagnostics"]


@pytest.mark.parametrize("field", ["bank_instance_id", "slot", "parent_slot"])
def test_receipt_join_identities_cannot_be_deleted_consistently(field: str):
    witness = _witness()
    for receipt in witness["receipts"][1:]:
        receipt["payload"].pop(field, None)
    result = check_witness(_reseal(witness))
    assert result["synthetic_contract_valid"] is False
    assert any(
        code.endswith("_missing") or code.endswith("_invalid") for code in result["diagnostics"]
    )


def test_frame_and_candidate_scope_cannot_be_erased():
    witness = _witness()
    witness["frames"] = []
    witness["expected_candidate_frames"] = []
    witness["post_execution_trace"] = []
    witness["candidate_path_selector"]["selected_paths"] = []
    result = check_witness(_reseal(witness))
    assert result["synthetic_contract_valid"] is False
    assert "candidate_path_selector_scope_invalid" in result["diagnostics"]
    assert "frame_trace_scope_empty" in result["diagnostics"]


def test_account_role_manifest_cannot_be_deleted_or_reduced():
    witness = _witness()
    witness["expected_candidate_frames"][1]["required_accounts"] = []
    result = check_witness(_reseal(witness))
    assert result["synthetic_contract_valid"] is False
    assert "required_account_roles_invalid_or_duplicate" in result["diagnostics"]
    assert "required_account_roles_not_source_bound" in result["diagnostics"]


@pytest.mark.parametrize("field,value", [("pubkey", None), ("is_writable", None)])
def test_exact_account_identity_and_privilege_cannot_be_consistently_erased(field, value):
    witness = _witness()
    expected = witness["expected_candidate_frames"][0]["required_accounts"][0]
    role = expected["semantic_role"]
    expected[field] = value
    for endpoint in ("pre_state", "post_state"):
        account = next(
            item for item in witness["frames"][0][endpoint] if item["semantic_role"] == role
        )
        account[field] = value
    result = check_witness(_reseal(witness))
    assert result["synthetic_contract_valid"] is False
    assert any("missing" in item or "invalid" in item for item in result["diagnostics"])


def test_fork_and_success_effect_axes_must_match_receipts():
    witness = _witness()
    witness["fork_finality_axis"] = "dead"
    witness["execution"]["candidate_effect_state"] = "attempted_only"
    result = check_witness(_reseal(witness))
    assert "fork_finality_axis_mismatch" in result["diagnostics"]
    assert "successful_execution_effect_state_invalid" in result["diagnostics"]


def test_deployed_program_binding_requires_program_and_deployment_identity():
    witness = _witness()
    program = witness["frames"][1]["program_binding"]
    program["program_id"] = "rebound-program"
    program["loader_id"] = ""
    program["effective_slot"] = 99
    result = check_witness(_reseal(witness))
    assert "deployed_program_id_mismatch" in result["diagnostics"]
    assert "deployed_loader_missing" in result["diagnostics"]
    assert "effective_slot_invalid" in result["diagnostics"]


def test_program_deployment_must_be_effective_by_the_witnessed_bank_slot():
    witness = _witness()
    program = witness["frames"][1]["program_binding"]
    program["deployment_slot"] = 1000
    program["effective_slot"] = 1000
    result = check_witness(_reseal(witness))
    assert "program_effective_after_execution_slot" in result["diagnostics"]


@pytest.mark.parametrize(
    "field,value,diagnostic",
    [
        ("active_feature_set_hash", "d" * 64, "frame_feature_set_differs_from_execution"),
        ("runtime_build_id", "other-build", "frame_runtime_build_differs_from_execution"),
    ],
)
def test_all_frames_share_one_source_bound_execution_runtime(field, value, diagnostic):
    witness = _witness()
    witness["frames"][1]["program_binding"][field] = value
    result = check_witness(_reseal(witness))
    assert diagnostic in result["diagnostics"]


def test_loader_identity_must_be_typed_text():
    witness = _witness()
    witness["frames"][1]["program_binding"]["loader_id"] = True
    result = check_witness(_reseal(witness))
    assert "deployed_loader_missing" in result["diagnostics"]


@pytest.mark.parametrize("field,value", [("clock_source", True), ("observed_utc_ms", -1)])
def test_receipt_clock_identity_and_native_time_are_typed(field, value):
    witness = _witness()
    witness["receipts"][0][field] = value
    _rechain_receipts(witness)
    result = check_witness(witness)
    assert result["synthetic_contract_valid"] is False


def test_causal_receipt_monotonic_order_is_checked_within_same_host_boot():
    witness = _witness()
    for index, receipt in enumerate(witness["receipts"]):
        receipt["host_id"] = "same-host"
        receipt["boot_id"] = "same-boot"
        if index:
            receipt["monotonic_ns"] = (len(witness["receipts"]) - index) * 100
    _rechain_receipts(witness)
    result = check_witness(witness)
    assert "causal_receipt_monotonic_order_invalid" in result["diagnostics"]


@pytest.mark.parametrize("identity_field", ["program_id", "instruction_data_sha256"])
def test_selected_frame_identity_cannot_be_consistently_erased(identity_field: str):
    witness = _witness()
    witness["frames"][1][identity_field] = None
    witness["expected_candidate_frames"][1][identity_field] = None
    witness["post_execution_trace"][1][identity_field] = None
    if identity_field == "program_id":
        witness["frames"][1]["program_binding"]["program_id"] = None
    result = check_witness(_reseal(witness))
    assert result["synthetic_contract_valid"] is False
    assert any("missing" in item or "invalid" in item for item in result["diagnostics"])


def test_one_pubkey_cannot_have_conflicting_exact_bytes_in_same_boundary():
    witness = _witness()
    state = witness["frames"][1]["pre_state"]
    state_item = next(
        item for item in state if item["semantic_role"] == "transfer_fee_config_readonly"
    )
    state_item["data_bytes_hex"] = b"different".hex()
    state_item["data_length_bytes"] = len(b"different")
    state_item["data_sha256"] = hashlib.sha256(b"different").hexdigest()
    result = check_witness(_reseal(witness))
    assert "pre_state_same_pubkey_state_conflict" in result["diagnostics"]


@pytest.mark.parametrize(
    "field",
    ["bank_state_hash", "parent_bank_state_hash", "ledger_blockhash", "parent_ledger_blockhash"],
)
def test_receipt_hash_identity_cannot_be_a_json_boolean(field: str):
    witness = _witness()
    for index in (2, 3, 4):
        if field in witness["receipts"][index]["payload"]:
            witness["receipts"][index]["payload"][field] = True
    result = check_witness(_reseal(witness))
    assert result["synthetic_contract_valid"] is False
    assert any("missing_or_invalid" in item for item in result["diagnostics"])


@pytest.mark.parametrize("lifecycle", ["entered", "push_failed", "pop_failed", "capture_failed"])
def test_incomplete_frame_lifecycle_cannot_certify_pre_post_pair(lifecycle: str):
    witness = _witness()
    witness["frames"][1]["lifecycle"] = lifecycle
    result = check_witness(_reseal(witness))
    assert "frame_not_entered_or_not_returned" in result["diagnostics"]


def test_durable_receipt_missing_fails_closed():
    witness = _witness()
    witness["receipts"] = witness["receipts"][1:]
    result = check_witness(witness)
    assert "receipt_chain_incomplete" in result["diagnostics"]


def test_trace_push_failure_cannot_masquerade_as_exact_frame():
    witness = _witness()
    witness["frames"][1]["lifecycle"] = "push_failed"
    result = check_witness(_reseal(witness))
    assert "frame_not_entered_or_not_returned" in result["diagnostics"]


def test_success_without_bank_commit_is_not_consistent():
    witness = _witness()
    witness["receipts"][1]["payload"]["commit_result"] = "successful_execution_uncommitted"
    result = check_witness(_reseal(witness))
    assert "successful_execution_without_bank_commit" in result["diagnostics"]


def test_ambiguous_same_slot_root_join_fails_closed():
    witness = _witness()
    witness["receipts"][4]["payload"]["bank_ledger_binding_status"] = "ambiguous"
    result = check_witness(_reseal(witness))
    assert "root_bank_ledger_binding_not_unique" in result["diagnostics"]
    assert result["availability_status"] == "unknown"
    assert result["admission_availability_upper_bound_utc_ms"] is None


def test_builtin_binding_requires_runtime_and_feature_identity():
    witness = _witness(program_kind="builtin_or_runtime")
    del witness["frames"][1]["program_binding"]["active_feature_set_hash"]
    result = check_witness(_reseal(witness))
    assert "builtin_feature_hash_missing" in result["diagnostics"]


def test_precompile_binding_requires_runtime_and_feature_identity():
    result = check_witness(_witness(program_kind="precompile"))
    assert result["synthetic_contract_valid"] is True
    _assert_synthetic_only(result)


def test_commit_cannot_be_rebound_to_another_frozen_bank():
    witness = _witness()
    witness["receipts"][2]["payload"]["bank_instance_id"] = "other-bank-instance"
    result = check_witness(_reseal(witness))
    assert "commit_freeze_bank_instance_mismatch" in result["diagnostics"]


def test_commit_batch_index_is_not_assumed_to_be_block_transaction_index():
    witness = _witness()
    assert witness["receipts"][1]["payload"]["commit_batch_index"] == 0
    assert witness["receipts"][3]["payload"]["block_transaction_index"] == 3
    result = check_witness(witness)
    assert result["synthetic_contract_valid"] is True


def test_unknown_clock_bound_preserves_unknown_availability():
    witness = _witness()
    clocks = [(1000 + i * 10, None if i == 2 else 1, f"host-{i}", f"boot-{i}") for i in range(5)]
    result = check_witness(_reseal(witness, clocks))
    assert result["synthetic_contract_valid"] is True
    assert result["availability_status"] == "unknown"
    assert result["admission_availability_upper_bound_utc_ms"] is None
    _assert_synthetic_only(result)


def test_malformed_execution_runtime_fails_closed_without_raising():
    witness = _witness()
    witness["execution_runtime"] = None
    result = check_witness(_reseal(witness))
    assert result["synthetic_contract_valid"] is False
    assert "execution_runtime_identity_not_source_bound" in result["diagnostics"]


@given(
    left=st.dictionaries(st.text(min_size=1), st.integers()),
    right=st.dictionaries(st.text(min_size=1), st.integers()),
)
def test_canonical_receipt_hash_is_independent_of_mapping_insertion_order(left: dict, right: dict):
    value = {**left, **right}
    reversed_value = dict(reversed(list(value.items())))
    assert canonical_json_bytes(value) == canonical_json_bytes(reversed_value)
    assert receipt_sha256(value) == receipt_sha256(reversed_value)


@given(
    observed=st.lists(st.integers(min_value=0, max_value=10**15), min_size=5, max_size=5),
    uncertainty=st.lists(st.integers(min_value=0, max_value=10**6), min_size=5, max_size=5),
)
def test_availability_upper_bound_is_max_of_receipt_upper_bounds(
    observed: list[int], uncertainty: list[int]
):
    witness = _witness()
    clocks = [(observed[i], uncertainty[i], f"host-{i}", f"boot-{i}") for i in range(5)]
    result = check_witness(_reseal(witness, clocks))
    assert result["synthetic_contract_valid"] is True
    assert result["admission_availability_upper_bound_utc_ms"] == max(
        observed[i] + uncertainty[i] for i in range(5)
    )
    _assert_synthetic_only(result)


@given(byte=st.integers(min_value=0, max_value=255))
def test_mutating_sealed_witness_without_receipt_reseal_is_detected(byte: int):
    witness = _witness()
    changed = copy.deepcopy(witness)
    changed["frames"][0]["pre_state"][0]["data_bytes_hex"] = bytes([byte]).hex()
    result = check_witness(changed)
    assert result["synthetic_contract_valid"] is False
    assert "capture_seal_witness_hash_mismatch" in result["diagnostics"]


@pytest.mark.parametrize(
    "field,value,diagnostic",
    [
        ("bank_state_hash", "wrong-bank-hash", "bank_state_hash_join_mismatch"),
        ("parent_ledger_blockhash", "wrong-parent", "parent_ledger_blockhash_join_mismatch"),
    ],
)
def test_hash_domain_or_parent_mismatch_is_rejected(field: str, value: str, diagnostic: str):
    witness = _witness()
    witness["receipts"][3]["payload"][field] = value
    result = check_witness(_reseal(witness))
    assert diagnostic in result["diagnostics"]
