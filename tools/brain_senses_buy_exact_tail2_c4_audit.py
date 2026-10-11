#!/usr/bin/env python3
"""One-pass, offline captured-class PumpSwap buyExact tail2 c4 audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memecoin_pumpswap.buy_exact_v1 import (  # noqa: E402
    inspect_buy_exact_quote_in_v1,
)
from memecoin_pumpswap.decoder import PROGRAM, TOKEN, TOKEN2022, decode58  # noqa: E402
from memecoin_pumpswap.economic_buy_exact_tail2_v1 import (  # noqa: E402
    diagnose_pumpswap_buy_exact_tail2_dual_legacy_v1,
)
from tools import brain_senses_block_epoch_v2 as epoch_v2  # noqa: E402
from tools import brain_senses_buy_exact_tail2_c4_manifest as manifest_tool  # noqa: E402
from tools import brain_senses_buy_exact_tail2_c4_plan as plan_tool  # noqa: E402
from tools import brain_senses_buy_exact_v1_census as census_tool  # noqa: E402
from tools import brain_senses_campaign4_recovery as campaign4  # noqa: E402

FEE_KEYS = (
    "pool_credit_less_lp_fee_equals_event_net_user_quote_amount",
    "protocol_recipient_transfer_plus_buyback_equals_event_protocol_fee",
    "protocol_recipient_transfer_equals_protocol_fee_less_buyback",
    "creator_vault_transfer_equals_event_creator_fee",
    "buyback_ata_transfer_equals_event_buyback_fee",
)


def _stat_identity(row: os.stat_result) -> tuple[int, int, int, int, int]:
    return row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    before = path.lstat()
    if path.is_symlink() or not path.is_file():
        raise ValueError("audit_artifact_not_regular_file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read()
            after = os.fstat(stream.fileno())
    finally:
        final = path.lstat()
    if (
        _stat_identity(before) != _stat_identity(after)
        or _stat_identity(after) != _stat_identity(final)
        or len(raw) != after.st_size
    ):
        raise ValueError("audit_artifact_changed_while_reading")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("audit_json_object_required")
    return value, raw


def _hash_file(path: Path) -> dict[str, Any]:
    before = path.lstat()
    if path.is_symlink() or not path.is_file():
        raise ValueError("audit_capture_not_regular_file")
    digest = hashlib.sha256()
    size = 0
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with os.fdopen(descriptor, "rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
            after = os.fstat(stream.fileno())
    finally:
        final = path.lstat()
    if (
        _stat_identity(before) != _stat_identity(after)
        or _stat_identity(after) != _stat_identity(final)
        or size != after.st_size
    ):
        raise ValueError("audit_capture_changed_while_hashing")
    return {"bytes": size, "sha256": digest.hexdigest()}


def _write_create_only(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return {"path": str(path), "sha256": _sha(encoded)}


def _preflight(plan_path: Path, freeze_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    plan, plan_bytes = _read_json(plan_path)
    freeze, freeze_bytes = _read_json(freeze_path)
    if (
        plan.get("contract") != "pumpswap_buy_exact_tail2_c4_one_pass_audit_plan_v1"
        or plan.get("status") != "frozen_metadata_plan_semantic_replay_not_started"
        or freeze.get("contract") != "pumpswap_buy_exact_tail2_c4_plan_freeze_receipt_v1"
        or freeze.get("status") != "plan_frozen_before_semantic_payload_access"
        or freeze.get("plan_sha256") != _sha(plan_bytes)
        or Path(freeze.get("plan_path", "")).resolve() != plan_path.resolve()
        or freeze.get("manifest_sha256") != plan.get("manifest_sha256")
        or plan.get("campaign_id") != campaign4.CAMPAIGN_ID
        or Path(plan.get("campaign_root", "")).resolve() != campaign4.ROOT.resolve()
        or plan.get("preregistration_sha256") != manifest_tool.EXPECTED_PREREG_SHA256
        or plan.get("protocol_anchor", {}).get("commit") != "f7ec8c0"
    ):
        raise ValueError("audit_plan_or_freeze_receipt_invalid")
    source_pins = plan.get("source_sha256")
    if not isinstance(source_pins, dict) or set(source_pins) != set(plan_tool.SOURCE_PATHS):
        raise ValueError("audit_source_pin_set_invalid")
    for name, expected in plan.get("source_sha256", {}).items():
        current = _hash_file(REPO_ROOT / name)
        if current["sha256"] != expected:
            raise ValueError("audit_source_pin_mismatch")
    deviation = plan.get("deviation_receipt", {})
    if Path(deviation.get("path", "")).resolve() != plan_tool.DEVIATION_PATH.resolve():
        raise ValueError("audit_deviation_receipt_path_invalid")
    deviation_value, deviation_bytes = _read_json(Path(deviation.get("path", "")))
    if (
        _sha(deviation_bytes) != deviation.get("sha256")
        or deviation_value.get("result", {}).get("eligible_frame_retained") is not True
        or deviation_value.get("observed_access", {}).get("decompressed") is not False
    ):
        raise ValueError("audit_deviation_receipt_pin_mismatch")
    synthetic = plan.get("synthetic_preflight", {})
    synthetic_path = Path(synthetic.get("path", ""))
    if synthetic_path.resolve().is_relative_to(Path(plan["campaign_root"]).resolve()):
        raise ValueError("audit_synthetic_receipt_inside_frozen_campaign")
    test_receipt, test_bytes = _read_json(synthetic_path)
    expected_test_hashes = {name: source_pins[name] for name in plan_tool.TEST_SOURCE_PATHS}
    if (
        _sha(test_bytes) != synthetic.get("sha256")
        or synthetic.get("status") != "passed_synthetic_only"
        or test_receipt.get("contract") != plan_tool.TEST_RECEIPT_CONTRACT
        or test_receipt.get("status") != "passed_synthetic_only"
        or test_receipt.get("rpc_requests") != 0
        or test_receipt.get("semantic_campaign_payloads_opened") != 0
        or test_receipt.get("test_source_hashes") != synthetic.get("test_source_hashes")
        or synthetic.get("test_source_hashes") != expected_test_hashes
        or test_receipt.get("tested_source_hashes") != synthetic.get("tested_source_hashes")
        or synthetic.get("tested_source_hashes") != source_pins
    ):
        raise ValueError("audit_synthetic_preflight_receipt_invalid")
    test_binding = plan_tool._test_binding(synthetic_path, REPO_ROOT, campaign4.ROOT.resolve())
    if test_binding["sha256"] != synthetic.get("sha256") or test_binding[
        "result_artifact"
    ] != synthetic.get("test_result_artifact"):
        raise ValueError("audit_synthetic_test_result_binding_invalid")
    python_runtime = plan.get("python_runtime", {})
    if (
        python_runtime.get("implementation") != platform.python_implementation()
        or python_runtime.get("version") != platform.python_version()
    ):
        raise ValueError("audit_python_runtime_binding_mismatch")
    manifest_path = Path(plan.get("manifest_path", ""))
    if manifest_path.resolve().is_relative_to(Path(plan["campaign_root"]).resolve()):
        raise ValueError("audit_manifest_inside_frozen_campaign")
    manifest_value, manifest_bytes = _read_json(manifest_path)
    if (
        _sha(manifest_bytes) != plan.get("manifest_sha256")
        or manifest_value.get("preregistration_sha256") != plan.get("preregistration_sha256")
        or manifest_value.get("selected_frames") != plan.get("selected_frames")
        or manifest_value.get("scheduled_bucket_denominator")
        != plan.get("scheduled_bucket_denominator")
    ):
        raise ValueError("audit_manifest_plan_binding_mismatch")
    zstd = plan.get("zstd", {})
    zstd_path = Path(zstd.get("path", ""))
    if _hash_file(zstd_path)["sha256"] != zstd.get("sha256"):
        raise ValueError("audit_zstd_binary_pin_mismatch")
    version = subprocess.run(
        [str(zstd_path), "--version"], capture_output=True, text=True, check=True, timeout=10
    ).stdout.strip()
    if version != zstd.get("version"):
        raise ValueError("audit_zstd_version_mismatch")
    return plan, {"plan_sha256": _sha(plan_bytes), "freeze_receipt_sha256": _sha(freeze_bytes)}


def _preflight_frames(plan: dict[str, Any]) -> list[dict[str, Any]]:
    root = Path(plan["campaign_root"])
    frames = plan.get("selected_frames")
    denominator = plan.get("scheduled_bucket_denominator")
    if not isinstance(frames, list) or len(frames) != 4 or not isinstance(denominator, list):
        raise ValueError("audit_four_frame_selection_required")
    active_reservation, _ = _read_json(root / "active-frame-reservation.json")
    if active_reservation.get("status") != "reconciled":
        raise ValueError("audit_open_or_ambiguous_campaign_reservation")
    if not plan_tool._selection_is_first_four_successes(frames, denominator):
        raise ValueError("audit_frozen_selection_rule_invalid")
    denominator_buckets = [row.get("bucket") for row in denominator]
    if denominator_buckets != sorted(denominator_buckets):
        raise ValueError("audit_scheduled_bucket_order_invalid")
    if any(
        int(manifest_tool._bucket_start(bucket).timestamp() * 1_000_000_000)
        <= manifest_tool.FREEZE_COMMIT_NS
        for bucket in denominator_buckets
    ):
        raise ValueError("audit_pre_freeze_bucket_included")
    denominator_successes = [
        row["bucket"]
        for row in denominator
        if isinstance(row, dict) and row.get("state") == "successful"
    ]
    frame_buckets = [row.get("bucket") for row in frames if isinstance(row, dict)]
    if len(frame_buckets) != 4 or denominator_successes[:4] != frame_buckets:
        raise ValueError("audit_first_four_frame_rule_mismatch")
    if not plan_tool._scheduled_denominator_is_complete(denominator, frame_buckets[-1]):
        raise ValueError("audit_scheduled_bucket_denominator_incomplete")
    all_slots: list[int] = []
    prepared: list[dict[str, Any]] = []
    for frame in frames:
        bucket = frame.get("bucket")
        if (
            frame.get("status") != "frame_complete"
            or frame.get("request_count") != campaign4.REQUESTS_PER_FRAME
            or len(frame.get("selected_slots", [])) != campaign4.SELECTION_COUNT
            or any(type(slot) is not int or slot < 0 for slot in frame.get("selected_slots", []))
            or len(frame.get("captures", [])) != campaign4.REQUESTS_PER_FRAME
        ):
            raise ValueError("audit_frame_manifest_shape_invalid")
        epoch = root / "epochs" / str(bucket)
        if epoch.is_symlink():
            raise ValueError("audit_epoch_symlink")
        expected_artifacts = {
            row["path"]: row for row in frame.get("artifact_index", []) if isinstance(row, dict)
        }
        if len(expected_artifacts) != len(frame.get("artifact_index", [])):
            raise ValueError("audit_artifact_index_duplicate")
        actual_files: set[str] = set()
        for current, dirs, files in os.walk(epoch, followlinks=False):
            for name in dirs:
                if (Path(current) / name).is_symlink():
                    raise ValueError("audit_epoch_directory_symlink")
            for name in files:
                path = Path(current) / name
                if path.is_symlink() or not path.is_file():
                    raise ValueError("audit_epoch_file_not_regular")
                actual_files.add(path.relative_to(epoch).as_posix())
        if actual_files != set(expected_artifacts):
            raise ValueError("audit_frame_artifact_inventory_mismatch")
        for rel, expected in expected_artifacts.items():
            actual = _hash_file(epoch / rel)
            if actual != {"bytes": expected.get("bytes"), "sha256": expected.get("sha256")}:
                raise ValueError("audit_frame_artifact_hash_mismatch")

        source_plan, _ = _read_json(epoch / "epoch-plan.json")
        selection, _ = _read_json(epoch / "selection-plan.json")
        operations = epoch_v2._operations_for(epoch, source_plan, selection)
        if len(operations) != campaign4.REQUESTS_PER_FRAME:
            raise ValueError("audit_frame_operation_count_mismatch")
        requests = sorted((epoch / "requests").glob("*.json"))
        results = sorted((epoch / "request-results").glob("*.json"))
        if len(requests) != len(operations) or len(results) != len(operations):
            raise ValueError("audit_frame_journal_count_mismatch")
        operations_by_slot: dict[int, dict[str, Any]] = {}
        for sequence, (request_path, result_path, operation) in enumerate(
            zip(requests, results, operations, strict=True), start=1
        ):
            request, _ = _read_json(request_path)
            result, _ = _read_json(result_path)
            expected_request = epoch_v2._request_body(operation["method"], operation["params"])
            if (
                request_path.name != f"{sequence:012d}.json"
                or result_path.name != f"{sequence:012d}.json"
                or request.get("sequence") != sequence
                or request.get("phase") != operation["phase"]
                or request.get("provider") != source_plan["provider"]
                or request.get("method") != operation["method"]
                or request.get("params") != operation["params"]
                or request.get("request_sha256") != _sha(expected_request)
                or result.get("sequence") != sequence
                or result.get("phase") != operation["phase"]
                or result.get("provider") != source_plan["provider"]
                or not epoch_v2._result_identity(result, operation, sequence)
                or result.get("status") != "captured"
                or result.get("result_status") != "parsed"
            ):
                raise ValueError("audit_request_result_identity_mismatch")
            if operation.get("slot") is not None:
                operations_by_slot.setdefault(operation["slot"], {})[operation["phase"]] = {
                    "operation": operation,
                    "result": result,
                }
            capture_dir = epoch / "captures" / f"{sequence:012d}"
            receipt, _ = _read_json(capture_dir / "receipt.json")
            compressed_path = capture_dir / "response.json.zst"
            digest = _hash_file(compressed_path)
            storage = receipt.get("storage")
            if (
                receipt.get("source_alias") != source_plan.get("provider")
                or receipt.get("method") != operation["method"]
                or receipt.get("request_json") != expected_request.decode()
                or receipt.get("request_started_ns") != result.get("request_started_ns")
                or receipt.get("received_ns") != result.get("receipt_ns")
                or receipt.get("response_sha256") != result.get("response_sha256")
                or receipt.get("response_bytes") != result.get("response_bytes")
                or not isinstance(storage, dict)
                or storage.get("path") != "response.json.zst"
                or storage.get("codec") != "zstd"
                or storage.get("sha256") != digest["sha256"]
                or storage.get("bytes") != digest["bytes"]
                or result.get("stored_response_bytes") != digest["bytes"]
            ):
                raise ValueError("audit_capture_receipt_mismatch")
        if len(operations_by_slot) != campaign4.SELECTION_COUNT or any(
            set(parts) != {"full_block", "signature_check"} for parts in operations_by_slot.values()
        ):
            raise ValueError("audit_full_signature_pair_missing")
        all_slots.extend(frame["selected_slots"])
        prepared.append(
            {
                "bucket": bucket,
                "epoch": epoch,
                "selected_slots": frame["selected_slots"],
                "operations_by_slot": operations_by_slot,
            }
        )
    if len(all_slots) != len(set(all_slots)):
        raise ValueError("audit_duplicate_selected_slot_across_frames")
    return prepared


def _decompress(path: Path, zstd_path: Path, expected_bytes: int, expected_sha: str) -> bytes:
    completed = subprocess.run(
        [str(zstd_path), "-dc", str(path)], capture_output=True, check=True, timeout=180
    )
    raw = completed.stdout
    if len(raw) != expected_bytes or _sha(raw) != expected_sha:
        raise ValueError("audit_decompressed_response_hash_or_size_mismatch")
    return raw


def _response_result(raw: bytes) -> dict[str, Any]:
    wrapper = json.loads(raw)
    if (
        not isinstance(wrapper, dict)
        or wrapper.get("jsonrpc") != "2.0"
        or type(wrapper.get("id")) is not int
        or wrapper.get("id") != 1
        or "error" in wrapper
        or not isinstance(wrapper.get("result"), dict)
    ):
        raise ValueError("audit_rpc_response_envelope_invalid")
    return wrapper["result"]


def _response(capture_dir: Path, result: dict[str, Any], zstd: Path) -> dict[str, Any]:
    receipt, _ = _read_json(capture_dir / "receipt.json")
    storage = receipt["storage"]
    compressed_path = capture_dir / storage["path"]
    if (
        receipt.get("response_sha256") != result.get("response_sha256")
        or receipt.get("response_bytes") != result.get("response_bytes")
        or receipt.get("received_ns") != result.get("receipt_ns")
        or _hash_file(compressed_path).get("sha256") != storage.get("sha256")
    ):
        raise ValueError("audit_capture_changed_after_preflight")
    raw = _decompress(
        compressed_path,
        zstd,
        int(receipt["response_bytes"]),
        str(receipt["response_sha256"]),
    )
    return _response_result(raw)


def _tail2_predicate(reference: dict[str, Any]) -> bool:
    if not isinstance(reference, dict):
        return False
    roles = reference.get("identified_roles", {})
    if not isinstance(roles, dict):
        return False
    return (
        reference.get("transaction_status") == "successful_tx_instruction_reference"
        and reference.get("argument_state") == "pinned_idl_v1_shape"
        and reference.get("instruction_data_length") == 25
        and reference.get("remaining_account_count") == 2
        and reference.get("suffix_state") == "documented_fee_pair_suffix_candidate"
        and roles.get("base_token_program") == TOKEN
        and roles.get("quote_token_program") == TOKEN
    )


def _transaction_outcome(transaction: Any) -> str:
    """Return an exact transaction outcome; malformed status remains unknown."""
    if not isinstance(transaction, dict):
        return "unknown"
    metadata = transaction.get("meta")
    if not isinstance(metadata, dict) or "err" not in metadata:
        return "unknown"
    error = metadata["err"]
    if error is None:
        return "successful"
    if (isinstance(error, dict) and error) or (isinstance(error, str) and error):
        return "failed"
    return "unknown"


def _candidate_detection_state(transaction: Any) -> str:
    """Keep malformed candidate scans unknown instead of treating them as noncandidate."""
    try:
        return "candidate" if census_tool._is_candidate_transaction(transaction) else "noncandidate"
    except (AttributeError, TypeError):
        return "unknown"


def _replication_claim(
    denominator: int | None,
    exact_flow_count: int,
    event_match_count: int,
    event_mismatch_count: int,
    unresolved_population_count: int = 0,
) -> str:
    if event_mismatch_count:
        return "blocked_by_semantic_disagreement"
    if unresolved_population_count:
        return "no_replication_claim_unknown_frozen_population_cases"
    if denominator is None:
        return "no_replication_claim_unknown_frozen_population_cases"
    if denominator == 0:
        return "no_tail2_replication_claim_zero_eligible_references"
    if exact_flow_count == denominator and event_match_count == denominator:
        return (
            "candidate_checked_transfer_and_BuyEvent_agreement_"
            "on_selected_captured_block_class_only"
        )
    return "no_replication_claim_incomplete_candidate_flow_or_event_checks"


def _population_scope(counters: Counter[str]) -> dict[str, Any]:
    total = counters["captured_transactions"]
    outcome_total = sum(
        counters[f"transaction_outcome:{state}"] for state in ("successful", "failed", "unknown")
    )
    if outcome_total != total:
        raise ValueError("audit_transaction_outcome_partition_mismatch")
    observed_lower_bound = counters["frozen_tail2_successful_dual_legacy_denominator"]
    unknown_signals = (
        counters["transaction_outcome:unknown"]
        + counters["successful_tx_unresolved_instruction_transactions"]
        + counters["successful_tx_normalization_unknown_transactions"]
        + counters["successful_tx_candidate_detection_unknown_transactions"]
        + counters["unknown_frozen_population_reference_rows"]
    )
    return {
        "observed_candidate_lower_bound": observed_lower_bound,
        "complete_successful_class": unknown_signals == 0,
        "exact_candidate_denominator": observed_lower_bound if unknown_signals == 0 else None,
        "unknown_signals": unknown_signals,
        "failed_attempt_reference_census_complete": (
            counters["failed_tx_unresolved_instruction_transactions"] == 0
            and counters["failed_tx_normalization_unknown_transactions"] == 0
            and counters["failed_tx_candidate_detection_unknown_transactions"] == 0
        ),
    }


def _raw_program_resolution_unknowns(transaction: Any) -> tuple[int, int]:
    """Count unresolved program references and known PumpSwap instructions with unknown data."""
    if not isinstance(transaction, dict):
        return 1, 0
    body = transaction.get("transaction")
    message = body.get("message") if isinstance(body, dict) else None
    metadata = transaction.get("meta")
    if not isinstance(message, dict) or not isinstance(metadata, dict):
        return 1, 0
    account_keys = message.get("accountKeys")
    if not isinstance(account_keys, list):
        return 1, 0
    keys = [row.get("pubkey") if isinstance(row, dict) else row for row in account_keys]
    loaded = metadata.get("loadedAddresses") or {}
    if not isinstance(loaded, dict):
        return 1, 0
    for label in ("writable", "readonly"):
        values = loaded.get(label, [])
        if not isinstance(values, list):
            return 1, 0
        keys.extend(row.get("pubkey") if isinstance(row, dict) else row for row in values)
    outer = message.get("instructions")
    inner = metadata.get("innerInstructions") or []
    if not isinstance(outer, list) or not isinstance(inner, list):
        return 1, 0
    instructions: list[Any] = list(outer)
    for group in inner:
        if not isinstance(group, dict) or not isinstance(group.get("instructions"), list):
            return 1, 0
        instructions.extend(group["instructions"])
    unresolved_program = 0
    pump_data_unknown = 0
    for instruction in instructions:
        if not isinstance(instruction, dict):
            unresolved_program += 1
            continue
        index = instruction.get("programIdIndex")
        if type(index) is not int or not 0 <= index < len(keys) or not isinstance(keys[index], str):
            unresolved_program += 1
            continue
        if keys[index] != PROGRAM:
            continue
        data = instruction.get("data")
        if not isinstance(data, str):
            pump_data_unknown += 1
            continue
        try:
            decode58(data)
        except ValueError:
            pump_data_unknown += 1
    return unresolved_program, pump_data_unknown


def _audit_frame(
    frame: dict[str, Any],
    zstd: Path,
    counters: Counter[str],
    reference_keys: set[tuple[Any, ...]],
    transaction_signatures: set[str],
    classified_references: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    eligible_facts: list[dict[str, Any]] = []
    for slot in frame["selected_slots"]:
        pair = frame["operations_by_slot"][slot]
        full_op = pair["full_block"]["operation"]
        sig_op = pair["signature_check"]["operation"]
        full_result = pair["full_block"]["result"]
        sig_result = pair["signature_check"]["result"]
        full_dir = frame["epoch"] / full_result["capture_path"]
        sig_dir = frame["epoch"] / sig_result["capture_path"]
        full = _response(full_dir, full_result, zstd)
        signatures_only = _response(sig_dir, sig_result, zstd)
        if (
            full_op.get("slot") != slot
            or sig_op.get("slot") != slot
            or full.get("transactions") is None
            or signatures_only.get("transactions") is None
            or full.get("blockhash") != signatures_only.get("blockhash")
            or full.get("parentSlot") != signatures_only.get("parentSlot")
            or full.get("previousBlockhash") != signatures_only.get("previousBlockhash")
        ):
            raise ValueError("audit_full_signature_block_identity_mismatch")
        txs = full["transactions"]
        sig_txs = signatures_only["transactions"]
        if not isinstance(txs, list) or not isinstance(sig_txs, list) or len(txs) != len(sig_txs):
            raise ValueError("audit_full_signature_transaction_count_mismatch")
        counters["selected_slots"] += 1
        counters["captured_transactions"] += len(txs)
        for tx_index, (transaction, sig_transaction) in enumerate(zip(txs, sig_txs, strict=True)):
            sig_row = (
                sig_transaction.get("transaction", {}) if isinstance(sig_transaction, dict) else {}
            )
            sigs = sig_row.get("signatures") if isinstance(sig_row, dict) else None
            tx_body = transaction.get("transaction", {}) if isinstance(transaction, dict) else {}
            full_sigs = tx_body.get("signatures") if isinstance(tx_body, dict) else None
            if not isinstance(sigs, list) or not isinstance(full_sigs, list) or sigs != full_sigs:
                raise ValueError("audit_full_signature_order_mismatch")
            if not full_sigs or not isinstance(full_sigs[0], str):
                raise ValueError("audit_transaction_signature_missing")
            signature = full_sigs[0]
            if signature in transaction_signatures:
                raise ValueError("audit_duplicate_transaction_signature")
            transaction_signatures.add(signature)
            transaction_outcome = _transaction_outcome(transaction)
            counters[f"transaction_outcome:{transaction_outcome}"] += 1
            unresolved_programs, pump_data_unknown = _raw_program_resolution_unknowns(transaction)
            counters["instruction_program_resolution_unknowns"] += unresolved_programs
            counters["pump_program_instruction_data_unknowns"] += pump_data_unknown
            if unresolved_programs:
                counters["transactions_with_program_resolution_unknowns"] += 1
            if pump_data_unknown:
                counters["transactions_with_pump_instruction_data_unknowns"] += 1
            unresolved_population = bool(unresolved_programs or pump_data_unknown)
            if unresolved_population:
                if transaction_outcome == "successful":
                    counters["successful_tx_unresolved_instruction_transactions"] += 1
                elif transaction_outcome == "failed":
                    counters["failed_tx_unresolved_instruction_transactions"] += 1
                else:
                    counters["unknown_status_unresolved_instruction_transactions"] += 1
            candidate_state = _candidate_detection_state(transaction)
            if candidate_state == "unknown":
                counters["candidate_detection_unknown_transactions"] += 1
                if transaction_outcome == "successful":
                    counters["successful_tx_candidate_detection_unknown_transactions"] += 1
                elif transaction_outcome == "failed":
                    counters["failed_tx_candidate_detection_unknown_transactions"] += 1
                continue
            if candidate_state == "noncandidate":
                continue
            inspected = inspect_buy_exact_quote_in_v1(transaction)
            if inspected.get("status") == "malformed_or_missing_trace":
                counters["normalization_unknown_transactions"] += 1
                counters["normalization_unknown_pump_candidate_transactions"] += 1
                if transaction_outcome == "successful":
                    counters["successful_tx_normalization_unknown_transactions"] += 1
                elif transaction_outcome == "failed":
                    counters["failed_tx_normalization_unknown_transactions"] += 1
                continue
            refs = inspected.get("references")
            if not isinstance(refs, list):
                raise ValueError("audit_reference_inspector_result_invalid")
            for reference in refs:
                if reference.get("reference_state") != "recognized_instruction_attempt":
                    continue
                path = reference.get("path")
                if not isinstance(path, list) or any(
                    type(part) is not int or part < 0 for part in path
                ):
                    raise ValueError("audit_instruction_path_invalid")
                key = (slot, tx_index, tuple(path), signature)
                if key in reference_keys:
                    raise ValueError("audit_duplicate_buy_exact_reference")
                reference_keys.add(key)
                counters["buy_exact_instruction_references"] += 1
                transaction_status = reference.get("transaction_status")
                failed = transaction_status == "failed_tx_attempt_only"
                successful = transaction_status == "successful_tx_instruction_reference"
                if failed:
                    counters["failed_transaction_references"] += 1
                elif successful:
                    counters["successful_transaction_references"] += 1
                else:
                    counters["unknown_transaction_status_references"] += 1
                    counters["unknown_frozen_population_reference_rows"] += 1
                if reference.get("argument_state") == "pinned_idl_v1_shape":
                    counters["pinned_25_byte_argument_references"] += 1
                else:
                    counters["unsupported_or_unknown_argument_layouts"] += 1
                if reference.get("suffix_state") != "documented_fee_pair_suffix_candidate":
                    counters["unsupported_or_unknown_suffix_layouts"] += 1
                roles = reference.get("identified_roles", {})
                token_programs = {roles.get("base_token_program"), roles.get("quote_token_program")}
                if TOKEN2022 in token_programs:
                    counters["token2022_reference_cases"] += 1
                eligible = _tail2_predicate(reference)
                if failed:
                    classification = "failed_transaction_attempt_only"
                elif not successful:
                    classification = "unknown_transaction_status_quarantined"
                elif TOKEN2022 in token_programs:
                    classification = "token2022_quarantined"
                elif reference.get("base_account_prefix_state") != "pinned_idl_prefix_candidate":
                    classification = "unsupported_or_unknown_base_account_layout"
                elif reference.get("argument_state") != "pinned_idl_v1_shape":
                    classification = "unsupported_or_unknown_argument_layout"
                elif reference.get("suffix_state") != "documented_fee_pair_suffix_candidate":
                    classification = "unsupported_or_unknown_suffix_layout"
                elif any(program not in {TOKEN, TOKEN2022} for program in token_programs):
                    classification = "unsupported_or_unknown_token_program"
                elif not eligible:
                    classification = "successful_reference_outside_frozen_tail2_predicate"
                else:
                    classification = "frozen_tail2_successful_dual_legacy"
                classified_references.append(
                    {
                        "slot": slot,
                        "transaction_index": tx_index,
                        "signature": signature,
                        "path": path,
                        "transaction_status": reference.get("transaction_status"),
                        "instruction_data_length": reference.get("instruction_data_length"),
                        "argument_state": reference.get("argument_state"),
                        "remaining_account_count": reference.get("remaining_account_count"),
                        "suffix_state": reference.get("suffix_state"),
                        "base_account_prefix_state": reference.get("base_account_prefix_state"),
                        "base_token_program": roles.get("base_token_program"),
                        "quote_token_program": roles.get("quote_token_program"),
                        "frozen_tail2_predicate": eligible,
                        "classification": classification,
                    }
                )
                if not eligible:
                    counters["not_in_frozen_tail2_denominator"] += 1
                    if (
                        successful
                        and reference.get("base_account_prefix_state")
                        != "pinned_idl_prefix_candidate"
                    ):
                        counters["unknown_frozen_population_reference_rows"] += 1
                    continue
                counters["frozen_tail2_successful_dual_legacy_denominator"] += 1
                fact = diagnose_pumpswap_buy_exact_tail2_dual_legacy_v1(transaction, path)
                event_state = fact.get("event_crosscheck", {}).get("state", "unevaluable")
                counters[
                    f"candidate_flow_state:{fact.get('candidate_token_flow_state', 'unknown')}"
                ] += 1
                counters[f"event_crosscheck_state:{event_state}"] += 1
                if event_state == "match":
                    counters["event_crosscheck_pass"] += 1
                elif event_state == "mismatch":
                    counters["event_crosscheck_fail"] += 1
                else:
                    counters["event_crosscheck_unevaluable"] += 1
                endpoint_state = fact.get(
                    "transaction_target_account_ledger_state", "unsupported_or_unknown"
                )
                counters[f"endpoint_state:{endpoint_state}"] += 1
                for fee_key in FEE_KEYS:
                    state = fact.get("fee_flow_hypotheses", {}).get(fee_key)
                    if state is True:
                        counters[f"fee:{fee_key}:pass"] += 1
                    elif state is False:
                        counters[f"fee:{fee_key}:fail"] += 1
                    else:
                        counters[f"fee:{fee_key}:unevaluable"] += 1
                eligible_facts.append(
                    {
                        "slot": slot,
                        "transaction_index": tx_index,
                        "signature": signature,
                        "path": path,
                        "availability_ns": full_result["receipt_ns"],
                        "native_event_time_unix_seconds": full.get("blockTime"),
                        "transaction_status": reference["transaction_status"],
                        "reference": reference,
                        "diagnostic": fact,
                    }
                )
    return eligible_facts


def run_audit(plan_path: Path, freeze_receipt_path: Path, output_dir: Path) -> dict[str, Any]:
    if plan_path.is_symlink() or freeze_receipt_path.is_symlink():
        raise ValueError("audit_input_symlink")
    plan_path = plan_path.resolve(strict=True)
    freeze_receipt_path = freeze_receipt_path.resolve(strict=True)
    if output_dir.exists() or output_dir.is_symlink():
        raise ValueError("audit_output_exists_refusing_replay")
    plan, freeze_hashes = _preflight(plan_path, freeze_receipt_path)
    campaign_root = Path(plan["campaign_root"]).resolve()
    if plan_path.is_relative_to(campaign_root) or freeze_receipt_path.is_relative_to(campaign_root):
        raise ValueError("audit_plan_artifact_inside_frozen_campaign")
    if output_dir.resolve().is_relative_to(Path(plan["campaign_root"]).resolve()):
        raise ValueError("audit_output_inside_frozen_campaign")
    prepared_frames = _preflight_frames(plan)
    output_dir.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    output_dir.mkdir(mode=0o700)
    attempt_path = output_dir / "attempt-started.json"
    attempt = {
        "schema_version": 1,
        "contract": "pumpswap_buy_exact_tail2_c4_audit_attempt_v1",
        "status": "semantic_replay_started_once",
        **freeze_hashes,
        "started_ns": time.time_ns(),
    }
    attempt_hash = _write_create_only(attempt_path, attempt)
    semantic_started = True
    try:
        counters: Counter[str] = Counter()
        reference_keys: set[tuple[Any, ...]] = set()
        transaction_signatures: set[str] = set()
        facts: list[dict[str, Any]] = []
        classified_references: list[dict[str, Any]] = []
        zstd = Path(plan["zstd"]["path"])
        for frame in prepared_frames:
            frame_facts = _audit_frame(
                frame,
                zstd,
                counters,
                reference_keys,
                transaction_signatures,
                classified_references,
            )
            facts.extend(frame_facts)
        population_scope = _population_scope(counters)
        observed_candidate_lower_bound = population_scope["observed_candidate_lower_bound"]
        if observed_candidate_lower_bound != len(facts):
            raise ValueError("audit_tail2_denominator_mismatch")
        successful_class_complete = population_scope["complete_successful_class"]
        failed_attempt_reference_census_complete = population_scope[
            "failed_attempt_reference_census_complete"
        ]
        exact_denominator = population_scope["exact_candidate_denominator"]
        unknown_frozen_signals = population_scope["unknown_signals"]
        fee_counts = {
            key: {
                "pass": counters[f"fee:{key}:pass"],
                "fail": counters[f"fee:{key}:fail"],
                "unevaluable": counters[f"fee:{key}:unevaluable"],
                "observed_candidate_lower_bound": observed_candidate_lower_bound,
            }
            for key in FEE_KEYS
        }
        semantic_mismatches = counters["event_crosscheck_fail"]
        replication_claim = _replication_claim(
            exact_denominator,
            counters["candidate_flow_state:exact"],
            counters["event_crosscheck_pass"],
            semantic_mismatches,
            unknown_frozen_signals,
        )
        report = {
            "schema_version": 1,
            "contract": "pumpswap_buy_exact_tail2_c4_one_pass_captured_class_audit_v1",
            "status": "complete_captured_class_audit",
            "source": {
                **freeze_hashes,
                "plan_path": str(plan_path),
                "freeze_receipt_path": str(freeze_receipt_path),
                "campaign_id": plan["campaign_id"],
                "selected_frames": [frame["bucket"] for frame in prepared_frames],
                "scheduled_bucket_denominator": plan["scheduled_bucket_denominator"],
                "frames_semantically_opened": len(prepared_frames),
                "full_block_responses_replayed": len(prepared_frames) * campaign4.SELECTION_COUNT,
                "signature_responses_replayed": len(prepared_frames) * campaign4.SELECTION_COUNT,
                "frame_requests": len(prepared_frames) * campaign4.REQUESTS_PER_FRAME,
                "rpc_requests": 0,
                "procedure_deviation_receipt": plan["deviation_receipt"],
            },
            "scope": {
                "captured_selected_slots": counters["selected_slots"],
                "captured_transactions": counters["captured_transactions"],
                "transaction_outcomes": {
                    "total": counters["captured_transactions"],
                    "successful": counters["transaction_outcome:successful"],
                    "failed": counters["transaction_outcome:failed"],
                    "unknown_status": counters["transaction_outcome:unknown"],
                },
                "buy_exact_instruction_references": counters["buy_exact_instruction_references"],
                "reference_classification_rows": len(classified_references),
                "failed_transaction_references": counters["failed_transaction_references"],
                "failed_attempt_reference_census_complete": (
                    failed_attempt_reference_census_complete
                ),
                "successful_transaction_references": counters["successful_transaction_references"],
                "unknown_transaction_status_references": counters[
                    "unknown_transaction_status_references"
                ],
                "observed_tail2_successful_dual_legacy_candidates_lower_bound": (
                    observed_candidate_lower_bound
                ),
                "complete_captured_successful_class": successful_class_complete,
                "exact_frozen_tail2_successful_dual_legacy_denominator": exact_denominator,
                "normalization_unknown_transactions": counters[
                    "normalization_unknown_transactions"
                ],
                "normalization_unknown_pump_candidate_transactions": counters[
                    "normalization_unknown_pump_candidate_transactions"
                ],
                "candidate_detection_unknown_transactions": counters[
                    "candidate_detection_unknown_transactions"
                ],
                "transactions_with_program_resolution_unknowns": counters[
                    "transactions_with_program_resolution_unknowns"
                ],
                "transactions_with_pump_instruction_data_unknowns": counters[
                    "transactions_with_pump_instruction_data_unknowns"
                ],
                "successful_tx_unresolved_instruction_transactions": counters[
                    "successful_tx_unresolved_instruction_transactions"
                ],
                "failed_tx_unresolved_instruction_transactions": counters[
                    "failed_tx_unresolved_instruction_transactions"
                ],
                "failed_tx_normalization_unknown_transactions": counters[
                    "failed_tx_normalization_unknown_transactions"
                ],
                "successful_tx_candidate_detection_unknown_transactions": counters[
                    "successful_tx_candidate_detection_unknown_transactions"
                ],
                "failed_tx_candidate_detection_unknown_transactions": counters[
                    "failed_tx_candidate_detection_unknown_transactions"
                ],
                "unknown_status_unresolved_instruction_transactions": counters[
                    "unknown_status_unresolved_instruction_transactions"
                ],
                "successful_tx_normalization_unknown_transactions": counters[
                    "successful_tx_normalization_unknown_transactions"
                ],
                "unknown_frozen_population_signals": unknown_frozen_signals,
                "unknown_frozen_population_reference_rows": counters[
                    "unknown_frozen_population_reference_rows"
                ],
                "instruction_program_resolution_unknowns": counters[
                    "instruction_program_resolution_unknowns"
                ],
                "pump_program_instruction_data_unknowns": counters[
                    "pump_program_instruction_data_unknowns"
                ],
                "unsupported_or_unknown_argument_layouts": counters[
                    "unsupported_or_unknown_argument_layouts"
                ],
                "unsupported_or_unknown_suffix_layouts": counters[
                    "unsupported_or_unknown_suffix_layouts"
                ],
                "token2022_reference_cases": counters["token2022_reference_cases"],
            },
            "diagnostics": {
                "candidate_flow_states": {
                    key.split(":", 1)[1]: value
                    for key, value in sorted(counters.items())
                    if key.startswith("candidate_flow_state:")
                },
                "event_crosscheck": {
                    "pass": counters["event_crosscheck_pass"],
                    "fail": counters["event_crosscheck_fail"],
                    "unevaluable": counters["event_crosscheck_unevaluable"],
                    "observed_candidate_lower_bound": observed_candidate_lower_bound,
                },
                "endpoint_states": {
                    key.split(":", 1)[1]: value
                    for key, value in sorted(counters.items())
                    if key.startswith("endpoint_state:")
                },
                "fee_hypotheses": fee_counts,
                "fee_hypothesis_mismatch_observed": any(
                    row["fail"] > 0 for row in fee_counts.values()
                ),
            },
            "reference_classifications": classified_references,
            "facts": facts,
            "claims": {
                "candidate_transfer_flow": "diagnostic_only",
                "event_crosscheck": "selected_captured_class_only",
                "fee_equalities": "empirical_hypotheses_only",
                "settlement": "not_established",
                "wallet_net_price": "not_established",
                "discovery_or_process_completeness": "not_established",
                "exact_economic_zero": "not_established",
                "training": "not_admitted",
                "execution": "not_admitted",
                "semantic_disagreement_present": bool(semantic_mismatches),
                "blanket_replication_claim": replication_claim,
            },
            "time_semantics": {
                "native_event_time": "captured finalized blockTime; may be absent",
                "availability": "local full-block capture receipt_ns",
                "receipt_time": "each individual provider response receipt_ns",
                "decision_time": None,
            },
            "limitations": [
                (
                    "Four selected hourly frames are a captured-class diagnostic, not global "
                    "discovery completeness."
                ),
                (
                    "Unknown, failed, unsupported, partial and censored states remain distinct; "
                    "absent facts are not zero."
                ),
                "The five fee equations remain hypotheses even when all observed equalities match.",
            ],
        }
        result_path = output_dir / "audit-receipt.json"
        result_binding = _write_create_only(result_path, report)
        return {"status": report["status"], **result_binding, "attempt": attempt_hash}
    except BaseException as exc:
        failure = {
            "schema_version": 1,
            "contract": "pumpswap_buy_exact_tail2_c4_one_pass_captured_class_audit_v1",
            "status": "incomplete_semantic_audit_no_replay_permitted",
            "failure_kind": type(exc).__name__,
            "failure_reason": str(exc) if isinstance(exc, ValueError) else "audit_runtime_failure",
            "semantic_payload_access_started": semantic_started,
            "rpc_requests": 0,
            **freeze_hashes,
            "attempt_sha256": attempt_hash["sha256"],
            "created_ns": time.time_ns(),
        }
        _write_create_only(output_dir / "audit-incomplete-receipt.json", failure)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--freeze-receipt", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = run_audit(args.plan, args.freeze_receipt, args.output_dir)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        AttributeError,
        subprocess.SubprocessError,
    ) as exc:
        print(json.dumps({"status": "failed_closed", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
