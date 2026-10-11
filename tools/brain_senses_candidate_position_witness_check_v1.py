#!/usr/bin/env python3
"""Fail-closed consistency checks for synthetic candidate-position witnesses.

This tool checks only the synthetic contract shape. It cannot validate a real
Agave hook, provider capture, market semantics, or economic settlement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

CONTRACT = "solana_candidate_position_witness_hook_feasibility_v4"
RECEIPT_ORDER = ("capture_seal", "bank_commit", "bank_freeze", "block_join", "root_status")
PROGRAM_KINDS = {"deployed_bytecode", "builtin_or_runtime", "precompile"}
FRAME_LIFECYCLES = {"returned_success", "returned_error"}
SELECTOR_ID = "synthetic_candidate_path_selector_v1"
SELECTOR_SOURCE_SHA256 = hashlib.sha256(
    b"synthetic-selector-v1: require candidate paths [0],[0,0],[0,0,0]; "
    b"bind all frame role manifests; no runtime discovery claim"
).hexdigest()
SYNTHETIC_EXECUTION_RUNTIME = {
    "runtime_build_id": "agave-synthetic-build",
    "active_feature_set_hash": "e" * 64,
}
PINNED_SYNTHETIC_ROLES = {
    (0,): {"candidate_token_account", "candidate_mint_readonly", "pool_vault"},
    (0, 0): {
        "source_token_account",
        "destination_token_account",
        "mint_readonly",
        "transfer_fee_config_readonly",
        "source_withheld_fee_amount_readonly",
        "destination_withheld_fee_amount_readonly",
        "hook_extra_account_readonly",
    },
    (0, 0, 0): {"hook_extra_account_readonly"},
}


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def receipt_sha256(receipt: Mapping[str, Any]) -> str:
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def _check(condition: bool, code: str, errors: list[str]) -> None:
    if not condition:
        errors.append(code)


def _exact_int(value: Any) -> bool:
    return type(value) is int


def _is_sha256_hex(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        return len(bytes.fromhex(value)) == 32 and value == value.lower()
    except ValueError:
        return False


def _check_account_state(state: Any, expected: Mapping[str, Any], errors: list[str]) -> None:
    if not isinstance(state, Mapping):
        errors.append("account_state_missing")
        return
    _check(state.get("state_capture_status") == "exact", "account_state_not_exact", errors)
    _check(
        state.get("semantic_role") == expected.get("semantic_role"), "account_role_mismatch", errors
    )
    expected_pubkey = expected.get("pubkey")
    _check(
        isinstance(expected_pubkey, str) and bool(expected_pubkey.strip()),
        "required_account_pubkey_missing",
        errors,
    )
    observed_pubkey = state.get("pubkey")
    _check(
        isinstance(observed_pubkey, str) and bool(observed_pubkey.strip()),
        "account_pubkey_missing",
        errors,
    )
    _check(observed_pubkey == expected_pubkey, "account_pubkey_mismatch", errors)
    _check(
        type(expected.get("is_writable")) is bool,
        "required_account_writability_invalid",
        errors,
    )
    _check(type(state.get("is_writable")) is bool, "account_writability_invalid", errors)
    _check(
        state.get("is_writable") is expected.get("is_writable"),
        "account_writability_mismatch",
        errors,
    )
    for field in ("owner", "executable"):
        _check(field in state, f"account_{field}_missing", errors)
    _check(isinstance(state.get("owner"), str), "account_owner_invalid", errors)
    _check(type(state.get("executable")) is bool, "account_executable_invalid", errors)
    lamports = state.get("lamports_atomic_units")
    _check(_exact_int(lamports) and lamports >= 0, "lamports_unit_or_value_invalid", errors)
    rent_epoch = state.get("rent_epoch")
    _check(_exact_int(rent_epoch) and rent_epoch >= 0, "rent_epoch_unit_or_value_invalid", errors)
    raw_hex = state.get("data_bytes_hex")
    if not isinstance(raw_hex, str) or len(raw_hex) % 2:
        errors.append("account_data_bytes_invalid")
        return
    try:
        raw = bytes.fromhex(raw_hex)
    except ValueError:
        errors.append("account_data_bytes_invalid")
        return
    _check(raw.hex() == raw_hex, "account_data_bytes_not_canonical_hex", errors)
    _check(
        _exact_int(state.get("data_length_bytes")) and state.get("data_length_bytes") == len(raw),
        "account_data_length_mismatch",
        errors,
    )
    _check(
        state.get("data_sha256") == hashlib.sha256(raw).hexdigest(),
        "account_data_hash_mismatch",
        errors,
    )


def _check_frame(frame: Any, expected: Any, trace: Any, errors: list[str]) -> None:
    if not isinstance(frame, Mapping) or not isinstance(expected, Mapping):
        errors.append("frame_record_missing")
        return
    path = frame.get("path")
    _check(path == expected.get("path"), "frame_path_mismatch", errors)
    _check(
        frame.get("trace_index") == expected.get("trace_index"),
        "frame_trace_index_mismatch",
        errors,
    )
    _check(
        frame.get("child_ordinal") == expected.get("child_ordinal"),
        "frame_child_ordinal_mismatch",
        errors,
    )
    _check(
        frame.get("stack_height") == expected.get("stack_height"),
        "frame_stack_height_mismatch",
        errors,
    )
    for record_name, record in (("frame", frame), ("expected", expected)):
        _check(
            isinstance(record.get("program_id"), str)
            and bool(record.get("program_id", "").strip()),
            f"{record_name}_program_id_missing",
            errors,
        )
        _check(
            _is_sha256_hex(record.get("instruction_data_sha256")),
            f"{record_name}_instruction_hash_invalid",
            errors,
        )
    _check(frame.get("program_id") == expected.get("program_id"), "frame_program_mismatch", errors)
    _check(
        frame.get("instruction_data_sha256") == expected.get("instruction_data_sha256"),
        "frame_instruction_hash_mismatch",
        errors,
    )
    lifecycle = frame.get("lifecycle")
    _check(lifecycle in FRAME_LIFECYCLES, "frame_not_entered_or_not_returned", errors)
    ti = frame.get("trace_index")
    if not _exact_int(ti) or not isinstance(trace, list) or ti < 0 or ti >= len(trace):
        errors.append("frame_trace_reference_invalid")
    elif trace[ti] != {
        key: frame.get(key)
        for key in (
            "path",
            "trace_index",
            "child_ordinal",
            "stack_height",
            "program_id",
            "instruction_data_sha256",
        )
    }:
        errors.append("frame_trace_binding_mismatch")

    program = frame.get("program_binding")
    if not isinstance(program, Mapping):
        errors.append("program_binding_missing")
    else:
        kind = program.get("kind")
        _check(kind in PROGRAM_KINDS, "program_identity_kind_unknown", errors)
        _check(program.get("binding_status") == "exact", "program_binding_not_exact", errors)
        _check(
            isinstance(program.get("program_id"), str)
            and bool(program.get("program_id", "").strip()),
            "program_binding_program_id_missing",
            errors,
        )
        if kind == "deployed_bytecode":
            _check(
                program.get("program_id") == frame.get("program_id"),
                "deployed_program_id_mismatch",
                errors,
            )
            _check(
                isinstance(program.get("loader_id"), str)
                and bool(program.get("loader_id", "").strip()),
                "deployed_loader_missing",
                errors,
            )
            _check(
                _exact_int(program.get("deployment_slot")) and program.get("deployment_slot") >= 0,
                "deployment_slot_invalid",
                errors,
            )
            _check(
                _exact_int(program.get("effective_slot"))
                and program.get("effective_slot") >= program.get("deployment_slot", 0),
                "effective_slot_invalid",
                errors,
            )
            _check(
                isinstance(program.get("runtime_build_id"), str)
                and bool(program.get("runtime_build_id", "").strip()),
                "deployed_runtime_build_missing",
                errors,
            )
            _check(
                _is_sha256_hex(program.get("active_feature_set_hash")),
                "deployed_feature_hash_missing",
                errors,
            )
            digest = program.get("executable_sha256")
            _check(
                _is_sha256_hex(digest),
                "deployed_program_hash_missing",
                errors,
            )
            raw_program = program.get("executable_bytes_hex")
            if not isinstance(raw_program, str) or len(raw_program) % 2:
                errors.append("deployed_program_bytes_missing")
            else:
                try:
                    _check(
                        hashlib.sha256(bytes.fromhex(raw_program)).hexdigest() == digest,
                        "deployed_program_hash_mismatch",
                        errors,
                    )
                except ValueError:
                    errors.append("deployed_program_bytes_invalid")
        elif kind in {"builtin_or_runtime", "precompile"}:
            _check(
                program.get("program_id") == frame.get("program_id"),
                "runtime_program_id_mismatch",
                errors,
            )
            _check(
                isinstance(program.get("runtime_build_id"), str)
                and bool(program.get("runtime_build_id", "").strip()),
                "builtin_runtime_build_missing",
                errors,
            )
            _check(
                _is_sha256_hex(program.get("active_feature_set_hash")),
                "builtin_feature_hash_missing",
                errors,
            )
            _check(bool(program.get("implementation_id")), "builtin_implementation_missing", errors)

    required = expected.get("required_accounts")
    if not isinstance(required, list):
        errors.append("required_account_roles_missing")
        return
    required_roles = [
        account.get("semantic_role") for account in required if isinstance(account, Mapping)
    ]
    _check(
        bool(required)
        and len(required_roles) == len(required)
        and all(isinstance(role, str) and role for role in required_roles)
        and len(set(required_roles)) == len(required_roles),
        "required_account_roles_invalid_or_duplicate",
        errors,
    )
    path_key = tuple(path) if isinstance(path, list) and all(_exact_int(p) for p in path) else ()
    _check(
        set(required_roles) == PINNED_SYNTHETIC_ROLES.get(path_key, set()),
        "required_account_roles_not_source_bound",
        errors,
    )
    for account in required:
        if isinstance(account, Mapping) and str(account.get("semantic_role", "")).endswith(
            "_readonly"
        ):
            _check(
                account.get("is_writable") is False,
                "readonly_role_declared_writable",
                errors,
            )
    for endpoint in ("pre_state", "post_state"):
        observed = frame.get(endpoint)
        if not isinstance(observed, list) or not observed:
            errors.append(f"{endpoint}_missing")
            continue
        by_role: dict[str, Mapping[str, Any]] = {}
        for account in observed:
            if not isinstance(account, Mapping) or not isinstance(
                account.get("semantic_role"), str
            ):
                errors.append(f"{endpoint}_account_shape_invalid")
                continue
            role = account["semantic_role"]
            if role in by_role:
                errors.append(f"{endpoint}_duplicate_account_role")
            by_role[role] = account
        expected_by_role = {a.get("semantic_role"): a for a in required if isinstance(a, Mapping)}
        _check(
            set(by_role) == set(expected_by_role), f"{endpoint}_account_role_set_incomplete", errors
        )
        for role, exp in expected_by_role.items():
            if role in by_role:
                _check_account_state(by_role[role], exp, errors)
        by_pubkey: dict[str, Mapping[str, Any]] = {}
        state_fields = (
            "owner",
            "lamports_atomic_units",
            "data_length_bytes",
            "data_sha256",
            "data_bytes_hex",
            "executable",
            "rent_epoch",
        )
        for account in by_role.values():
            pubkey = account.get("pubkey")
            if not isinstance(pubkey, str) or not pubkey:
                continue
            prior = by_pubkey.get(pubkey)
            if prior is not None:
                _check(
                    all(prior.get(field) == account.get(field) for field in state_fields),
                    f"{endpoint}_same_pubkey_state_conflict",
                    errors,
                )
            else:
                by_pubkey[pubkey] = account


def _check_receipts(witness: Mapping[str, Any], errors: list[str]) -> int | None:
    receipts = witness.get("receipts")
    if not isinstance(receipts, list) or len(receipts) != len(RECEIPT_ORDER):
        errors.append("receipt_chain_incomplete")
        return None
    previous: str | None = None
    upper_bounds: list[int] = []
    clocks_bounded = True
    payload_by_type: dict[str, Mapping[str, Any]] = {}
    monotonic_by_host_boot: dict[tuple[str, str], list[int]] = {}
    for receipt, expected_type in zip(receipts, RECEIPT_ORDER, strict=True):
        if not isinstance(receipt, Mapping):
            errors.append("receipt_shape_invalid")
            continue
        _check(receipt.get("type") == expected_type, "receipt_order_or_type_invalid", errors)
        _check(
            receipt.get("previous_receipt_sha256") == previous, "receipt_chain_link_invalid", errors
        )
        computed = receipt_sha256(receipt)
        _check(receipt.get("receipt_sha256") == computed, "receipt_hash_invalid", errors)
        previous = (
            receipt.get("receipt_sha256")
            if isinstance(receipt.get("receipt_sha256"), str)
            else computed
        )
        payload = receipt.get("payload")
        if isinstance(payload, Mapping):
            payload_by_type[expected_type] = payload
        else:
            errors.append("receipt_payload_missing")
        observed = receipt.get("observed_utc_ms")
        uncertainty = receipt.get("clock_uncertainty_upper_bound_ms")
        _check(
            isinstance(receipt.get("host_id"), str)
            and bool(receipt.get("host_id", "").strip())
            and isinstance(receipt.get("boot_id"), str)
            and bool(receipt.get("boot_id", "").strip()),
            "receipt_host_boot_missing",
            errors,
        )
        _check(
            isinstance(receipt.get("clock_source"), str)
            and bool(receipt.get("clock_source", "").strip()),
            "receipt_clock_source_missing",
            errors,
        )
        _check(
            _exact_int(receipt.get("monotonic_ns")) and receipt.get("monotonic_ns") >= 0,
            "receipt_monotonic_time_invalid",
            errors,
        )
        if (
            expected_type != "capture_seal"
            and isinstance(receipt.get("host_id"), str)
            and isinstance(receipt.get("boot_id"), str)
            and _exact_int(receipt.get("monotonic_ns"))
            and receipt.get("monotonic_ns") >= 0
        ):
            host_boot = (receipt["host_id"], receipt["boot_id"])
            monotonic_by_host_boot.setdefault(host_boot, []).append(receipt["monotonic_ns"])
        if not _exact_int(observed) or observed < 0:
            errors.append("receipt_observed_time_invalid")
            clocks_bounded = False
        elif uncertainty is None:
            _check(
                receipt.get("clock_uncertainty_status") == "unknown",
                "receipt_clock_uncertainty_state_missing",
                errors,
            )
            clocks_bounded = False
        elif not _exact_int(uncertainty) or uncertainty < 0:
            errors.append(
                "receipt_clock_uncertainty_invalid",
            )
            clocks_bounded = False
        else:
            _check(
                receipt.get("clock_uncertainty_status") == "bounded",
                "receipt_clock_uncertainty_state_mismatch",
                errors,
            )
            upper_bounds.append(observed + uncertainty)
    for monotonic_values in monotonic_by_host_boot.values():
        _check(
            monotonic_values == sorted(monotonic_values),
            "causal_receipt_monotonic_order_invalid",
            errors,
        )
    if len(payload_by_type) != len(RECEIPT_ORDER):
        return None
    capture = payload_by_type["capture_seal"]
    commit = payload_by_type["bank_commit"]
    freeze = payload_by_type["bank_freeze"]
    block = payload_by_type["block_join"]
    root = payload_by_type["root_status"]
    witness_hash = hashlib.sha256(
        canonical_json_bytes({k: v for k, v in witness.items() if k != "receipts"})
    ).hexdigest()
    _check(
        capture.get("witness_sha256") == witness_hash, "capture_seal_witness_hash_mismatch", errors
    )
    _check(capture.get("artifact_immutable") is True, "artifact_not_immutable", errors)
    _check(capture.get("fsync_confirmed") is True, "durable_seal_not_confirmed", errors)
    _check(
        commit.get("bank_instance_id") == freeze.get("bank_instance_id"),
        "commit_freeze_bank_instance_mismatch",
        errors,
    )
    _check(
        commit.get("slot") == freeze.get("slot") == block.get("slot") == root.get("slot"),
        "receipt_slot_mismatch",
        errors,
    )
    _check(
        commit.get("parent_slot") == freeze.get("parent_slot") == block.get("parent_slot"),
        "receipt_parent_slot_mismatch",
        errors,
    )
    _check(
        root.get("parent_slot") == commit.get("parent_slot"), "root_parent_slot_mismatch", errors
    )
    _check(
        commit.get("bank_instance_id")
        == block.get("bank_instance_id")
        == root.get("bank_instance_id"),
        "receipt_bank_instance_mismatch",
        errors,
    )
    _check(
        freeze.get("bank_state_hash")
        == block.get("bank_state_hash")
        == root.get("bank_state_hash"),
        "bank_state_hash_join_mismatch",
        errors,
    )
    _check(
        freeze.get("parent_bank_state_hash") == block.get("parent_bank_state_hash"),
        "parent_bank_hash_join_mismatch",
        errors,
    )
    _check(
        freeze.get("parent_bank_state_hash") == root.get("parent_bank_state_hash"),
        "root_parent_bank_hash_mismatch",
        errors,
    )
    _check(
        block.get("ledger_blockhash") == root.get("ledger_blockhash"),
        "ledger_blockhash_join_mismatch",
        errors,
    )
    _check(
        block.get("parent_ledger_blockhash") == root.get("parent_ledger_blockhash"),
        "parent_ledger_blockhash_join_mismatch",
        errors,
    )
    for payload in (commit, block):
        _check(
            isinstance(payload.get("signature"), str)
            and payload.get("signature") == witness.get("signature"),
            "receipt_signature_mismatch",
            errors,
        )
        _check(
            payload.get("message_sha256") == witness.get("message_sha256"),
            "receipt_message_hash_mismatch",
            errors,
        )
    for name, payload in (
        ("commit", commit),
        ("freeze", freeze),
        ("block", block),
        ("root", root),
    ):
        bank_id = payload.get("bank_instance_id")
        _check(
            isinstance(bank_id, str) and bool(bank_id.strip()),
            f"{name}_bank_instance_missing",
            errors,
        )
    for name, payload in (("commit", commit), ("freeze", freeze), ("block", block), ("root", root)):
        slot = payload.get("slot")
        _check(_exact_int(slot) and slot >= 0, f"{name}_slot_invalid", errors)
        parent_slot = payload.get("parent_slot")
        _check(
            _exact_int(parent_slot)
            and parent_slot >= 0
            and _exact_int(slot)
            and parent_slot < slot,
            f"{name}_parent_slot_invalid",
            errors,
        )
    for field, values in (
        ("bank_state_hash", (freeze, block, root)),
        ("parent_bank_state_hash", (freeze, block, root)),
        ("ledger_blockhash", (block, root)),
        ("parent_ledger_blockhash", (block, root)),
    ):
        for payload in values:
            value = payload.get(field)
            _check(
                isinstance(value, str) and bool(value.strip()),
                f"receipt_{field}_missing_or_invalid",
                errors,
            )
    _check(
        block.get("commit_batch_id") == commit.get("commit_batch_id"),
        "commit_batch_id_mismatch",
        errors,
    )
    _check(
        isinstance(commit.get("commit_batch_id"), str)
        and bool(commit.get("commit_batch_id", "").strip())
        and _exact_int(commit.get("commit_batch_index"))
        and commit.get("commit_batch_index") >= 0,
        "commit_batch_identity_invalid",
        errors,
    )
    _check(
        block.get("commit_batch_index") == commit.get("commit_batch_index"),
        "commit_batch_index_mismatch",
        errors,
    )
    _check(
        _exact_int(block.get("block_transaction_index"))
        and block.get("block_transaction_index") >= 0,
        "block_transaction_index_missing",
        errors,
    )
    _check(
        block.get("unique_join_status") == "unique_exact",
        "block_transaction_join_not_unique",
        errors,
    )
    _check(root.get("status") == "rooted", "root_status_not_rooted", errors)
    _check(
        witness.get("fork_finality_axis") == root.get("status"),
        "fork_finality_axis_mismatch",
        errors,
    )
    _check(freeze.get("freeze_result") == "frozen", "bank_freeze_not_confirmed", errors)
    _check(
        root.get("bank_ledger_binding_status") == "unique_exact",
        "root_bank_ledger_binding_not_unique",
        errors,
    )
    _check(
        commit.get("commit_result")
        in {"committed_to_bank", "failed_attempt_rollback_accounts_committed"},
        "commit_result_invalid",
        errors,
    )
    if not clocks_bounded or len(upper_bounds) != len(RECEIPT_ORDER):
        return None
    return max(upper_bounds)


def _check_ordered_effects(witness: Mapping[str, Any], errors: list[str]) -> None:
    effects = witness.get("ordered_effects")
    wsol_roles = witness.get("wsol_role_names", [])
    if effects is None and not wsol_roles:
        return
    if not isinstance(effects, list):
        errors.append("ordered_effects_missing")
        return
    names: list[str] = []
    for index, effect in enumerate(effects):
        if not isinstance(effect, Mapping):
            errors.append("ordered_effect_shape_invalid")
            continue
        _check(
            _exact_int(effect.get("sequence")) and effect.get("sequence") == index,
            "ordered_effect_sequence_invalid",
            errors,
        )
        name = effect.get("effect")
        if not isinstance(name, str):
            errors.append("ordered_effect_name_missing")
            continue
        names.append(name)
        if name in {"SyncNative", "CloseAccount"}:
            _check(
                effect.get("lamports_field") == "lamports_atomic_units",
                "wsol_lamports_unit_mismatch",
                errors,
            )
            _check(
                effect.get("token_amount_encoding")
                == "raw_account_bytes_without_decimal_conversion",
                "wsol_token_unit_mismatch",
                errors,
            )
    if wsol_roles:
        _check(
            isinstance(wsol_roles, list)
            and all(isinstance(role, str) and role for role in wsol_roles),
            "wsol_role_names_invalid",
            errors,
        )
        _check(
            "SyncNative" in names and "CloseAccount" in names,
            "wsol_lifecycle_effects_incomplete",
            errors,
        )
        if "SyncNative" in names and "CloseAccount" in names:
            _check(
                names.index("SyncNative") < names.index("CloseAccount"),
                "wsol_lifecycle_order_invalid",
                errors,
            )


def _check_witness(witness: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    _check(witness.get("contract") == CONTRACT, "contract_version_mismatch", errors)
    _check(witness.get("synthetic_only") is True, "synthetic_scope_missing", errors)
    _check(
        isinstance(witness.get("signature"), str)
        and bool(witness.get("signature", "").strip())
        and _is_sha256_hex(witness.get("message_sha256")),
        "transaction_identity_missing",
        errors,
    )
    _check(
        isinstance(witness.get("genesis_hash"), str)
        and bool(witness.get("genesis_hash", "").strip()),
        "genesis_hash_missing",
        errors,
    )
    _check_ordered_effects(witness, errors)
    _check(
        witness.get("unit_contract")
        == {
            "lamports": "integer_atomic_units",
            "account_data_length": "bytes",
            "rent_epoch": "epoch_index",
            "token_amounts": "raw_account_bytes_without_decimal_conversion",
        },
        "unit_contract_invalid",
        errors,
    )
    execution_runtime = witness.get("execution_runtime")
    _check(
        execution_runtime == SYNTHETIC_EXECUTION_RUNTIME,
        "execution_runtime_identity_not_source_bound",
        errors,
    )
    runtime_identity = execution_runtime if isinstance(execution_runtime, Mapping) else {}
    trace = witness.get("post_execution_trace")
    expected = witness.get("expected_candidate_frames")
    frames = witness.get("frames")
    selector = witness.get("candidate_path_selector")
    if not isinstance(selector, Mapping):
        errors.append("candidate_path_selector_missing")
        selector = {}
    _check(selector.get("selector_id") == SELECTOR_ID, "candidate_path_selector_id_invalid", errors)
    _check(
        selector.get("selector_source_sha256") == SELECTOR_SOURCE_SHA256,
        "candidate_path_selector_source_unbound",
        errors,
    )
    _check(
        selector.get("selection_status") == "complete_for_synthetic_fixture",
        "candidate_path_selection_incomplete",
        errors,
    )
    _check(
        selector.get("selected_paths") == [[0], [0, 0], [0, 0, 0]],
        "candidate_path_selector_scope_invalid",
        errors,
    )
    if (
        not isinstance(trace, list)
        or not isinstance(expected, list)
        or not isinstance(frames, list)
    ):
        errors.append("frame_trace_contract_missing")
    else:
        _check(bool(trace) and bool(expected) and bool(frames), "frame_trace_scope_empty", errors)
        _check(len(expected) == len(frames), "candidate_frame_count_mismatch", errors)
        trace_paths: list[tuple[int, ...]] = []
        for trace_index, trace_frame in enumerate(trace):
            if not isinstance(trace_frame, Mapping):
                errors.append("post_execution_trace_entry_invalid")
                continue
            _check(
                isinstance(trace_frame.get("program_id"), str)
                and bool(trace_frame.get("program_id", "").strip()),
                "trace_program_id_missing",
                errors,
            )
            _check(
                _is_sha256_hex(trace_frame.get("instruction_data_sha256")),
                "trace_instruction_hash_invalid",
                errors,
            )
            _check(
                trace_frame.get("trace_index") == trace_index,
                "post_execution_trace_order_invalid",
                errors,
            )
            path = trace_frame.get("path")
            _check(
                isinstance(path, list)
                and bool(path)
                and all(_exact_int(part) and part >= 0 for part in path),
                "post_execution_trace_path_invalid",
                errors,
            )
            if isinstance(path, list) and path:
                path_tuple = tuple(path) if all(_exact_int(p) and p >= 0 for p in path) else ()
                if path_tuple:
                    trace_paths.append(path_tuple)
                _check(
                    trace_frame.get("stack_height") == len(path),
                    "post_execution_trace_stack_height_mismatch",
                    errors,
                )
                if len(path) > 1:
                    _check(
                        trace_frame.get("child_ordinal") == path[-1],
                        "post_execution_trace_child_ordinal_mismatch",
                        errors,
                    )
                    _check(
                        tuple(path[:-1])
                        in {
                            tuple(previous.get("path", []))
                            for previous in trace[:trace_index]
                            if isinstance(previous, Mapping)
                            and isinstance(previous.get("path"), list)
                        },
                        "post_execution_trace_parent_missing_or_late",
                        errors,
                    )
                else:
                    _check(
                        trace_frame.get("child_ordinal") == path[0],
                        "post_execution_trace_top_level_ordinal_mismatch",
                        errors,
                    )
        _check(
            len({str(item.get("path")) for item in trace if isinstance(item, Mapping)})
            == len(trace),
            "duplicate_trace_path",
            errors,
        )
        _check(trace_paths == sorted(trace_paths), "post_execution_trace_not_depth_first", errors)
        direct_children: dict[tuple[int, ...], list[int]] = {}
        for path in trace_paths:
            parent = path[:-1]
            direct_children.setdefault(parent, []).append(path[-1])
        for ordinals in direct_children.values():
            _check(
                ordinals == list(range(len(ordinals))),
                "post_execution_trace_child_ordinals_not_contiguous",
                errors,
            )
        for frame, expected_frame in zip(frames, expected, strict=False):
            _check_frame(frame, expected_frame, trace, errors)
            if isinstance(frame, Mapping) and isinstance(frame.get("program_binding"), Mapping):
                program = frame["program_binding"]
                _check(
                    program.get("runtime_build_id") == runtime_identity.get("runtime_build_id"),
                    "frame_runtime_build_differs_from_execution",
                    errors,
                )
                _check(
                    program.get("active_feature_set_hash")
                    == runtime_identity.get("active_feature_set_hash"),
                    "frame_feature_set_differs_from_execution",
                    errors,
                )
        expected_paths = [item.get("path") for item in expected if isinstance(item, Mapping)]
        frame_paths = [item.get("path") for item in frames if isinstance(item, Mapping)]
        _check(frame_paths == expected_paths, "candidate_frame_path_set_or_order_mismatch", errors)
        _check(
            expected_paths == selector.get("selected_paths"),
            "candidate_selector_frame_scope_mismatch",
            errors,
        )

    commit_slot = None
    receipts_for_slot = witness.get("receipts")
    if isinstance(receipts_for_slot, list) and len(receipts_for_slot) == len(RECEIPT_ORDER):
        commit_receipt = receipts_for_slot[1]
        if isinstance(commit_receipt, Mapping) and isinstance(
            commit_receipt.get("payload"), Mapping
        ):
            commit_slot = commit_receipt["payload"].get("slot")
    if isinstance(frames, list):
        for frame in frames:
            if not isinstance(frame, Mapping):
                continue
            program = frame.get("program_binding")
            if isinstance(program, Mapping) and program.get("kind") == "deployed_bytecode":
                effective_slot = program.get("effective_slot")
                _check(
                    _exact_int(commit_slot)
                    and _exact_int(effective_slot)
                    and effective_slot <= commit_slot,
                    "program_effective_after_execution_slot",
                    errors,
                )

    execution = witness.get("execution")
    if not isinstance(execution, Mapping):
        errors.append("execution_outcome_missing")
    else:
        outcome = execution.get("outcome")
        _check(
            outcome in {"successful_execution", "failed_execution"},
            "execution_outcome_invalid",
            errors,
        )
        if outcome == "failed_execution":
            _check(
                execution.get("disposition") == "failed_attempt_exact",
                "failed_execution_disposition_invalid",
                errors,
            )
            _check(
                execution.get("candidate_effect_state") == "attempted_only",
                "failed_execution_not_attempted_only",
                errors,
            )
        elif outcome == "successful_execution":
            _check(
                execution.get("disposition") == "successful_transaction",
                "successful_execution_disposition_invalid",
                errors,
            )
            _check(
                execution.get("candidate_effect_state") == "committed_candidate_state",
                "successful_execution_effect_state_invalid",
                errors,
            )
    receipts = witness.get("receipts")
    if isinstance(receipts, list) and len(receipts) == len(RECEIPT_ORDER):
        commit_receipt = receipts[1]
        if isinstance(commit_receipt, Mapping) and isinstance(
            commit_receipt.get("payload"), Mapping
        ):
            commit_result = commit_receipt["payload"].get("commit_result")
            if (
                isinstance(execution, Mapping)
                and execution.get("outcome") == "successful_execution"
            ):
                _check(
                    commit_result == "committed_to_bank",
                    "successful_execution_without_bank_commit",
                    errors,
                )
            elif isinstance(execution, Mapping) and execution.get("outcome") == "failed_execution":
                _check(
                    commit_result == "failed_attempt_rollback_accounts_committed",
                    "failed_execution_commit_disposition_invalid",
                    errors,
                )
    availability_upper = _check_receipts(witness, errors) if isinstance(receipts, list) else None
    availability_status = "bounded" if availability_upper is not None else "unknown"
    if availability_status == "bounded":
        _check(
            witness.get("admission_availability_upper_bound_utc_ms") == availability_upper,
            "availability_upper_bound_mismatch",
            errors,
        )
    else:
        _check(
            witness.get("admission_availability_upper_bound_utc_ms") is None,
            "unknown_availability_must_not_have_bound",
            errors,
        )
    valid = not errors
    if not valid:
        if availability_upper is not None:
            errors.append("declared_admission_availability_not_supported")
        availability_status = "unknown"
        availability_upper = None
    return {
        "synthetic_contract_valid": valid,
        "diagnostics": errors,
        "availability_status": availability_status,
        "admission_availability_upper_bound_utc_ms": availability_upper,
        "economic_admission": False,
        "source_capability_qualified": False,
        "real_runtime_or_provider_completeness_certified": False,
    }


def check_witness(witness: Any) -> dict[str, Any]:
    if not isinstance(witness, Mapping):
        return {
            "synthetic_contract_valid": False,
            "diagnostics": ["witness_not_object"],
            "availability_status": "unknown",
            "admission_availability_upper_bound_utc_ms": None,
            "economic_admission": False,
            "source_capability_qualified": False,
            "real_runtime_or_provider_completeness_certified": False,
        }
    try:
        return _check_witness(witness)
    except (TypeError, ValueError, OverflowError, KeyError):
        return {
            "synthetic_contract_valid": False,
            "diagnostics": ["witness_serialization_or_shape_invalid"],
            "availability_status": "unknown",
            "admission_availability_upper_bound_utc_ms": None,
            "economic_admission": False,
            "source_capability_qualified": False,
            "real_runtime_or_provider_completeness_certified": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("witness", type=Path, help="synthetic JSON witness")
    args = parser.parse_args()
    try:
        witness = json.loads(args.witness.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {
                    "synthetic_contract_valid": False,
                    "diagnostics": [f"input_invalid:{type(exc).__name__}"],
                    "availability_status": "unknown",
                    "admission_availability_upper_bound_utc_ms": None,
                    "economic_admission": False,
                    "source_capability_qualified": False,
                    "real_runtime_or_provider_completeness_certified": False,
                }
            )
        )
        return 2
    result = check_witness(witness)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["synthetic_contract_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
