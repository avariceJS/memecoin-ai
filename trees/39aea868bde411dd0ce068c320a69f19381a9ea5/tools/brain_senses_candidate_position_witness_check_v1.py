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
TRANSITION_EFFECTS_SCHEMA = "synthetic_transition_effects_v1"
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


def _u64(value: Any) -> bool:
    return _exact_int(value) and 0 <= value <= 2**64 - 1


def _check_keys(
    value: Mapping[str, Any], required: set[str], optional: set[str], code: str, errors: list[str]
) -> None:
    keys = set(value)
    _check(required <= keys and keys <= required | optional, code, errors)


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


def _expected_role_pubkeys(witness: Mapping[str, Any], path: list[int]) -> dict[str, str]:
    expected = witness.get("expected_candidate_frames")
    if not isinstance(expected, list):
        return {}
    for frame in expected:
        frame_path = frame.get("path") if isinstance(frame, Mapping) else None
        if (
            not isinstance(frame, Mapping)
            or not isinstance(frame_path, list)
            or not all(_exact_int(item) for item in frame_path)
            or frame_path != path
        ):
            continue
        accounts = frame.get("required_accounts")
        if not isinstance(accounts, list):
            return {}
        return {
            str(account["semantic_role"]): str(account["pubkey"])
            for account in accounts
            if isinstance(account, Mapping)
            and isinstance(account.get("semantic_role"), str)
            and isinstance(account.get("pubkey"), str)
        }
    return {}


def _frame_lifecycle(witness: Mapping[str, Any], path: list[int]) -> str | None:
    frames = witness.get("frames")
    if not isinstance(frames, list):
        return None
    for frame in frames:
        if isinstance(frame, Mapping) and frame.get("path") == path:
            lifecycle = frame.get("lifecycle")
            return lifecycle if isinstance(lifecycle, str) else None
    return None


def _check_token_2022_transition(
    witness: Mapping[str, Any], transition: Any, errors: list[str]
) -> None:
    execution = witness.get("execution")
    successful = (
        isinstance(execution, Mapping) and execution.get("outcome") == "successful_execution"
    )
    if not isinstance(transition, Mapping):
        errors.append("token_2022_transition_missing")
        return
    if not successful:
        _check(
            transition == {"status": "attempted_only"},
            "failed_execution_committed_transition_assertion",
            errors,
        )
        return
    _check_keys(
        transition,
        {
            "status",
            "path",
            "sequence",
            "mint_pubkey",
            "gross_token_atomic_units",
            "fee_token_atomic_units",
            "source_is_native",
            "accounts",
            "hook_entry",
        },
        set(),
        "token_2022_transition_fields_invalid",
        errors,
    )
    accounts = transition.get("accounts")
    if isinstance(accounts, Mapping):
        _check(
            set(accounts) == {"source_token_account", "destination_token_account"},
            "token_2022_transition_account_roles_invalid",
            errors,
        )
    _check(
        transition.get("status") == "committed_core_before_hook",
        "token_2022_transition_status_invalid",
        errors,
    )
    _check(
        _frame_lifecycle(witness, [0, 0]) == "returned_success"
        and _frame_lifecycle(witness, [0, 0, 0]) == "returned_success",
        "token_2022_transition_frame_not_successful",
        errors,
    )
    path = transition.get("path")
    _check(
        isinstance(path, list)
        and len(path) == 2
        and all(_exact_int(item) for item in path)
        and path == [0, 0],
        "token_2022_transition_path_invalid",
        errors,
    )
    _check(
        _exact_int(transition.get("sequence")) and transition.get("sequence") == 0,
        "token_2022_transition_order_invalid",
        errors,
    )
    gross = transition.get("gross_token_atomic_units")
    fee = transition.get("fee_token_atomic_units")
    _check(
        transition.get("mint_pubkey")
        == _expected_role_pubkeys(witness, [0, 0]).get("mint_readonly"),
        "token_2022_mint_pubkey_unbound",
        errors,
    )
    _check(
        type(transition.get("source_is_native")) is bool,
        "token_2022_source_native_flag_invalid",
        errors,
    )
    _check(_u64(gross), "token_2022_gross_unit_or_value_invalid", errors)
    _check(
        _u64(fee) and _u64(gross) and fee <= gross, "token_2022_fee_unit_or_value_invalid", errors
    )
    if not isinstance(accounts, Mapping):
        errors.append("token_2022_transition_accounts_missing")
        return
    source = accounts.get("source_token_account")
    destination = accounts.get("destination_token_account")
    for role, account in (
        ("source_token_account", source),
        ("destination_token_account", destination),
    ):
        _check(isinstance(account, Mapping), f"token_2022_{role}_missing", errors)
        if not isinstance(account, Mapping):
            continue
        required_fields = {
            "pubkey",
            "pre_base_amount_token_atomic_units",
            "core_post_base_amount_token_atomic_units",
        }
        optional_fields = {
            "pre_withheld_token_atomic_units",
            "core_post_withheld_token_atomic_units",
        }
        if transition.get("source_is_native") is True:
            optional_fields |= {
                "pre_lamports_atomic_units",
                "core_post_lamports_atomic_units",
            }
        if role == "destination_token_account":
            required_fields |= {
                "pre_withheld_token_atomic_units",
                "core_post_withheld_token_atomic_units",
            }
        _check_keys(
            account,
            required_fields,
            optional_fields,
            f"token_2022_{role}_fields_invalid",
            errors,
        )
        _check(
            account.get("pubkey") == _expected_role_pubkeys(witness, [0, 0]).get(role),
            f"token_2022_{role}_pubkey_unbound",
            errors,
        )
        for field in (
            "pre_base_amount_token_atomic_units",
            "core_post_base_amount_token_atomic_units",
        ):
            _check(_u64(account.get(field)), f"token_2022_{field}_invalid", errors)
    if not isinstance(source, Mapping) or not isinstance(destination, Mapping):
        return
    source_before = source.get("pre_base_amount_token_atomic_units")
    source_after = source.get("core_post_base_amount_token_atomic_units")
    destination_before = destination.get("pre_base_amount_token_atomic_units")
    destination_after = destination.get("core_post_base_amount_token_atomic_units")
    _check(
        source.get("pubkey") != destination.get("pubkey"),
        "token_2022_self_transfer_unsupported",
        errors,
    )
    if _u64(gross) and _u64(source_before):
        _check(source_before >= gross, "token_2022_source_balance_insufficient", errors)
        if source_before >= gross:
            _check(
                source_after == source_before - gross, "token_2022_source_delta_mismatch", errors
            )
    if _u64(gross) and _u64(fee) and fee <= gross and _u64(destination_before):
        expected = destination_before + gross - fee
        _check(expected <= 2**64 - 1, "token_2022_destination_overflow", errors)
        if expected <= 2**64 - 1:
            _check(destination_after == expected, "token_2022_destination_delta_mismatch", errors)
    source_withheld = source.get("pre_withheld_token_atomic_units")
    _check(
        ("pre_withheld_token_atomic_units" in source)
        == ("core_post_withheld_token_atomic_units" in source),
        "token_2022_source_withheld_pair_incomplete",
        errors,
    )
    if source_withheld is not None:
        _check(_u64(source_withheld), "token_2022_source_withheld_invalid", errors)
        _check(
            _u64(source.get("core_post_withheld_token_atomic_units")),
            "token_2022_source_withheld_post_invalid",
            errors,
        )
        _check(
            source.get("core_post_withheld_token_atomic_units") == source_withheld,
            "token_2022_source_withheld_changed",
            errors,
        )
    destination_withheld = destination.get("pre_withheld_token_atomic_units")
    destination_withheld_after = destination.get("core_post_withheld_token_atomic_units")
    _check(_u64(destination_withheld), "token_2022_destination_withheld_invalid", errors)
    _check(_u64(destination_withheld_after), "token_2022_destination_withheld_post_invalid", errors)
    if _u64(destination_withheld) and _u64(fee):
        expected = destination_withheld + fee
        _check(expected <= 2**64 - 1, "token_2022_withheld_overflow", errors)
        if expected <= 2**64 - 1:
            _check(
                destination_withheld_after == expected, "token_2022_withheld_delta_mismatch", errors
            )
    if transition.get("source_is_native") is True and _u64(gross):
        for account in (source, destination):
            _check(
                _u64(account.get("pre_lamports_atomic_units")),
                "token_2022_native_pre_lamports_invalid",
                errors,
            )
            _check(
                _u64(account.get("core_post_lamports_atomic_units")),
                "token_2022_native_post_lamports_invalid",
                errors,
            )
        source_lamports = source.get("pre_lamports_atomic_units")
        destination_lamports = destination.get("pre_lamports_atomic_units")
        if _u64(source_lamports):
            _check(
                source_lamports >= gross,
                "token_2022_native_source_lamports_insufficient",
                errors,
            )
            if source_lamports >= gross:
                _check(
                    source.get("core_post_lamports_atomic_units") == source_lamports - gross,
                    "token_2022_native_source_lamports_delta_mismatch",
                    errors,
                )
        if _u64(destination_lamports):
            expected = destination_lamports + gross
            _check(expected <= 2**64 - 1, "token_2022_native_destination_lamports_overflow", errors)
            if expected <= 2**64 - 1:
                _check(
                    destination.get("core_post_lamports_atomic_units") == expected,
                    "token_2022_native_destination_lamports_delta_mismatch",
                    errors,
                )
    hook = transition.get("hook_entry")
    _check(isinstance(hook, Mapping), "token_2022_hook_entry_missing", errors)
    if isinstance(hook, Mapping):
        _check_keys(
            hook,
            {"path", "sequence", "gross_token_atomic_units"},
            set(),
            "token_2022_hook_fields_invalid",
            errors,
        )
        hook_path = hook.get("path")
        _check(
            isinstance(hook_path, list)
            and len(hook_path) == 3
            and all(_exact_int(item) for item in hook_path)
            and hook_path == [0, 0, 0],
            "token_2022_hook_path_invalid",
            errors,
        )
        _check(
            _exact_int(hook.get("sequence")) and hook.get("sequence") == 1,
            "token_2022_hook_order_invalid",
            errors,
        )
        _check(
            _u64(hook.get("gross_token_atomic_units"))
            and hook.get("gross_token_atomic_units") == gross,
            "token_2022_hook_amount_mismatch",
            errors,
        )


def _check_wsol_transitions(
    witness: Mapping[str, Any], operations: Any, trace: Any, errors: list[str]
) -> None:
    if not isinstance(operations, list):
        errors.append("wsol_transition_operations_invalid")
        return
    if not isinstance(trace, list) or len(trace) != len(operations):
        errors.append("wsol_instruction_trace_incomplete")
        trace = []
    committed = (
        isinstance(witness.get("execution"), Mapping)
        and witness["execution"].get("outcome") == "successful_execution"
    )
    if not committed:
        _check(not operations, "failed_execution_committed_wsol_transition", errors)
        return
    sequences: list[int] = []
    names: list[str] = []
    paths: list[tuple[int, ...]] = []
    sync_pubkey: str | None = None
    sync_after: Mapping[str, Any] | None = None
    sync_program_id: str | None = None
    for operation in operations:
        if not isinstance(operation, Mapping):
            errors.append("wsol_transition_operation_shape_invalid")
            continue
        sequence = operation.get("sequence")
        _check(_exact_int(sequence) and sequence >= 0, "wsol_transition_sequence_invalid", errors)
        if _exact_int(sequence):
            sequences.append(sequence)
        path = operation.get("path")
        _check(
            isinstance(path, list) and bool(path) and all(_exact_int(i) and i >= 0 for i in path),
            "wsol_transition_path_invalid",
            errors,
        )
        if isinstance(path, list) and all(_exact_int(i) and i >= 0 for i in path):
            paths.append(tuple(path))
        kind = operation.get("kind")
        names.append(kind if isinstance(kind, str) else "")
        if kind == "SyncNative":
            _check_keys(
                operation,
                {
                    "kind",
                    "sequence",
                    "path",
                    "program_id",
                    "account",
                    "reported_rent_reserve_lamports",
                    "before",
                    "after",
                },
                set(),
                "wsol_sync_native_fields_invalid",
                errors,
            )
            account = operation.get("account")
            before, after = operation.get("before"), operation.get("after")
            reserve = operation.get("reported_rent_reserve_lamports")
            _check(
                isinstance(account, Mapping)
                and isinstance(account.get("pubkey"), str)
                and bool(account.get("pubkey")),
                "wsol_sync_native_account_invalid",
                errors,
            )
            if isinstance(account, Mapping):
                _check_keys(
                    account, {"pubkey"}, set(), "wsol_sync_native_account_fields_invalid", errors
                )
            if isinstance(account, Mapping) and isinstance(account.get("pubkey"), str):
                sync_pubkey = account["pubkey"]
            if isinstance(operation.get("program_id"), str):
                sync_program_id = operation["program_id"]
            _check(
                isinstance(before, Mapping) and isinstance(after, Mapping),
                "wsol_sync_native_snapshots_missing",
                errors,
            )
            if isinstance(before, Mapping) and isinstance(after, Mapping):
                _check_keys(
                    before,
                    {
                        "lamports_atomic_units",
                        "token_amount_atomic_units",
                        "is_native",
                        "is_native_reserve_lamports",
                    },
                    set(),
                    "wsol_sync_native_before_fields_invalid",
                    errors,
                )
                _check(
                    _u64(before.get("is_native_reserve_lamports")),
                    "wsol_sync_native_before_reserve_invalid",
                    errors,
                )
                if isinstance(account, Mapping):
                    _check_keys(
                        account,
                        {"pubkey"},
                        set(),
                        "wsol_sync_native_account_fields_invalid",
                        errors,
                    )
                _check_keys(
                    after,
                    {
                        "lamports_atomic_units",
                        "token_amount_atomic_units",
                        "is_native",
                        "is_native_reserve_lamports",
                    },
                    set(),
                    "wsol_sync_native_after_fields_invalid",
                    errors,
                )
                for state in (before, after):
                    for field in ("lamports_atomic_units", "token_amount_atomic_units"):
                        _check(_u64(state.get(field)), f"wsol_sync_native_{field}_invalid", errors)
                    _check(
                        type(state.get("is_native")) is bool and state.get("is_native") is True,
                        "wsol_sync_native_account_not_native",
                        errors,
                    )
                _check(
                    _u64(after.get("is_native_reserve_lamports")),
                    "wsol_sync_native_stored_reserve_invalid",
                    errors,
                )
                _check(_u64(reserve), "wsol_sync_native_reported_reserve_invalid", errors)
                if _u64(reserve) and _u64(after.get("lamports_atomic_units")):
                    lamports = after["lamports_atomic_units"]
                    _check(lamports >= reserve, "wsol_sync_native_reserve_exceeds_lamports", errors)
                    if lamports >= reserve:
                        _check(
                            after.get("token_amount_atomic_units") == lamports - reserve,
                            "wsol_sync_native_amount_mismatch",
                            errors,
                        )
                    _check(
                        after.get("is_native_reserve_lamports") == reserve,
                        "wsol_sync_native_stored_reserve_mismatch",
                        errors,
                    )
                _check(
                    before.get("lamports_atomic_units") == after.get("lamports_atomic_units"),
                    "wsol_sync_native_lamports_changed",
                    errors,
                )
                sync_after = after
        elif kind == "CloseAccount":
            _check_keys(
                operation,
                {"kind", "sequence", "path", "program_id", "source", "destination"},
                set(),
                "wsol_close_fields_invalid",
                errors,
            )
            source, destination = operation.get("source"), operation.get("destination")
            _check(
                isinstance(source, Mapping) and isinstance(destination, Mapping),
                "wsol_close_accounts_missing",
                errors,
            )
            if isinstance(source, Mapping) and isinstance(destination, Mapping):
                _check_keys(
                    source,
                    {
                        "pubkey",
                        "pre_lamports_atomic_units",
                        "post_lamports_atomic_units",
                        "is_native",
                        "post_state",
                    },
                    {"base_amount_token_atomic_units"},
                    "wsol_close_source_fields_invalid",
                    errors,
                )
                _check_keys(
                    destination,
                    {"pubkey", "pre_lamports_atomic_units", "post_lamports_atomic_units"},
                    set(),
                    "wsol_close_destination_fields_invalid",
                    errors,
                )
                _check(
                    isinstance(source.get("pubkey"), str) and bool(source.get("pubkey")),
                    "wsol_close_source_pubkey_missing",
                    errors,
                )
                _check(
                    isinstance(destination.get("pubkey"), str) and bool(destination.get("pubkey")),
                    "wsol_close_destination_pubkey_missing",
                    errors,
                )
                _check(
                    source.get("pubkey") != destination.get("pubkey"),
                    "wsol_close_accounts_not_distinct",
                    errors,
                )
                if sync_pubkey is not None:
                    _check(
                        source.get("pubkey") == sync_pubkey,
                        "wsol_sync_close_account_identity_mismatch",
                        errors,
                    )
                _check(
                    source.get("is_native") is True,
                    "wsol_close_source_not_native",
                    errors,
                )
                if sync_program_id is not None:
                    _check(
                        operation.get("program_id") == sync_program_id,
                        "wsol_sync_close_program_mismatch",
                        errors,
                    )
                if sync_after is not None:
                    _check(
                        source.get("pre_lamports_atomic_units")
                        == sync_after.get("lamports_atomic_units"),
                        "wsol_sync_close_lamports_discontinuous",
                        errors,
                    )
                    _check(
                        source.get("base_amount_token_atomic_units")
                        == sync_after.get("token_amount_atomic_units"),
                        "wsol_sync_close_amount_discontinuous",
                        errors,
                    )
                for account, prefix in ((source, "source"), (destination, "destination")):
                    for field in ("pre_lamports_atomic_units", "post_lamports_atomic_units"):
                        _check(
                            _u64(account.get(field)), f"wsol_close_{prefix}_{field}_invalid", errors
                        )
                _check(
                    source.get("post_lamports_atomic_units") == 0,
                    "wsol_close_source_not_closed",
                    errors,
                )
                _check(
                    source.get("post_state") == "closed",
                    "wsol_close_source_state_not_closed",
                    errors,
                )
                src, dst0, dst1 = (
                    source.get("pre_lamports_atomic_units"),
                    destination.get("pre_lamports_atomic_units"),
                    destination.get("post_lamports_atomic_units"),
                )
                if _u64(src) and _u64(dst0):
                    expected = dst0 + src
                    _check(expected <= 2**64 - 1, "wsol_close_destination_overflow", errors)
                    if expected <= 2**64 - 1:
                        _check(
                            dst1 == expected, "wsol_close_lamports_conservation_mismatch", errors
                        )
                _check(
                    type(source.get("is_native")) is bool, "wsol_close_native_flag_invalid", errors
                )
                if source.get("base_amount_token_atomic_units") is not None:
                    _check(
                        _u64(source.get("base_amount_token_atomic_units")),
                        "wsol_close_source_base_amount_invalid",
                        errors,
                    )
                if source.get("is_native") is False:
                    _check(
                        _u64(source.get("base_amount_token_atomic_units"))
                        and source.get("base_amount_token_atomic_units") == 0,
                        "wsol_close_nonnative_nonzero_balance",
                        errors,
                    )
        else:
            errors.append("wsol_transition_kind_invalid")
    _check(
        len(sequences) == len(operations) and sequences == ([0, 1] if operations else []),
        "wsol_transition_order_invalid",
        errors,
    )
    if operations:
        _check(paths == [(0, 1), (0, 2)], "wsol_transition_path_binding_invalid", errors)
        _check(
            names == ["SyncNative", "CloseAccount"],
            "wsol_transition_lifecycle_incomplete_or_reordered",
            errors,
        )
    trace_paths: list[tuple[int, ...]] = []
    for item in trace:
        if not isinstance(item, Mapping):
            errors.append("wsol_instruction_trace_entry_invalid")
            continue
        _check_keys(
            item,
            {"sequence", "path", "kind", "program_id", "lifecycle"},
            set(),
            "wsol_instruction_trace_fields_invalid",
            errors,
        )
        path = item.get("path")
        _check(
            isinstance(path, list)
            and bool(path)
            and all(_exact_int(part) and part >= 0 for part in path),
            "wsol_instruction_trace_path_invalid",
            errors,
        )
        if isinstance(path, list) and all(_exact_int(part) and part >= 0 for part in path):
            trace_paths.append(tuple(path))
        _check(
            isinstance(item.get("program_id"), str) and bool(item.get("program_id")),
            "wsol_instruction_trace_program_id_invalid",
            errors,
        )
        _check(_exact_int(item.get("sequence")), "wsol_instruction_trace_sequence_invalid", errors)
        _check(
            item.get("kind") in {"SyncNative", "CloseAccount"},
            "wsol_instruction_trace_kind_invalid",
            errors,
        )
        _check(
            item.get("lifecycle") == "returned_success",
            "wsol_instruction_trace_not_successful",
            errors,
        )
    _check(
        len(trace_paths) == len(set(trace_paths)),
        "wsol_instruction_trace_path_duplicate",
        errors,
    )
    for operation in operations:
        if not isinstance(operation, Mapping):
            continue
        matches = [
            item
            for item in trace
            if isinstance(item, Mapping) and item.get("path") == operation.get("path")
        ]
        _check(len(matches) == 1, "wsol_instruction_trace_path_not_unique", errors)
        if len(matches) == 1:
            item = matches[0]
            _check(
                _exact_int(item.get("sequence"))
                and item.get("sequence") == operation.get("sequence"),
                "wsol_instruction_trace_sequence_mismatch",
                errors,
            )
            _check(
                item.get("kind") == operation.get("kind"),
                "wsol_instruction_trace_kind_mismatch",
                errors,
            )
            _check(
                item.get("program_id") == operation.get("program_id"),
                "wsol_instruction_trace_program_mismatch",
                errors,
            )
            _check(
                item.get("lifecycle") == "returned_success",
                "wsol_instruction_trace_operation_not_successful",
                errors,
            )
    ordered = witness.get("ordered_effects")
    if operations:
        roles = witness.get("wsol_role_names")
        _check(
            isinstance(roles, list)
            and bool(roles)
            and all(isinstance(role, str) and role for role in roles),
            "wsol_transition_roles_missing_or_invalid",
            errors,
        )
        _check(
            isinstance(ordered, list) and len(ordered) == len(operations),
            "wsol_ordered_effect_list_mismatch",
            errors,
        )
    if isinstance(ordered, list) and len(ordered) == len(operations):
        for index, (operation, legacy_effect) in enumerate(zip(operations, ordered, strict=True)):
            if isinstance(operation, Mapping) and isinstance(legacy_effect, Mapping):
                _check(
                    _exact_int(operation.get("sequence"))
                    and operation.get("sequence") == index
                    and legacy_effect.get("sequence") == index,
                    "wsol_ordered_effect_sequence_mismatch",
                    errors,
                )
                _check(
                    operation.get("kind") == legacy_effect.get("effect"),
                    "wsol_ordered_effect_kind_mismatch",
                    errors,
                )


def _check_transition_effects(witness: Mapping[str, Any], errors: list[str]) -> None:
    effects = witness.get("transition_effects")
    if not isinstance(effects, Mapping):
        errors.append("transition_effects_missing_or_invalid")
        return
    _check(
        effects.get("schema") == TRANSITION_EFFECTS_SCHEMA,
        "transition_effects_schema_invalid",
        errors,
    )
    _check_keys(
        effects,
        {"schema", "token_2022_transfer", "wsol_operations", "wsol_instruction_trace"},
        set(),
        "transition_effects_fields_invalid",
        errors,
    )
    if witness.get("wsol_role_names") and not effects.get("wsol_operations"):
        errors.append("wsol_structured_transition_missing")
    _check_token_2022_transition(witness, effects.get("token_2022_transfer"), errors)
    _check_wsol_transitions(
        witness,
        effects.get("wsol_operations"),
        effects.get("wsol_instruction_trace"),
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
    _check_transition_effects(witness, errors)
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
