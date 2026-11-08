#!/usr/bin/env python3
"""Compile and publish the fixed 06:00Z lease's partial structural prefix.

This separately versioned path preserves the full scheduled denominator and
never routes incomplete evidence through the full-seal v1 queue compiler.
It performs no provider I/O and writes only to a descriptor-bound Kingston
sibling after independently verifying the terminal service state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Mapping
from fractions import Fraction
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from memecoin_research_storage import writer_guard  # noqa: E402
from tools import brain_senses_external_launch_v2 as launch  # noqa: E402
from tools import brain_senses_external_orchestrator_v2 as engine  # noqa: E402
from tools import brain_senses_external_schema_v2 as schema  # noqa: E402
from tools import brain_senses_external_structural_input_v1 as structural  # noqa: E402
from tools import brain_senses_external_verify_v1 as predecessor_verify  # noqa: E402
from tools import brain_senses_external_verify_v2 as verify  # noqa: E402
from tools.brain_senses_external_structural_input_schema_v1 import (  # noqa: E402
    canonical,
    digest,
)
from tools.brain_senses_external_structural_publish_v1 import (  # noqa: E402
    _existing_shared_lock,
    _open_relative,
    _private_directory,
    _same_directory,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402

SERVICE_ID = "external-v2-20261012T0600Z"
EXPECTED_PLAN_SHA256 = "2bb19afee5475173e7e5a73a59d1cd33a27c6af1ef8e309fc39bfc025fa5a688"
FAILURE_RECEIPT = (
    REPO / "docs/evidence/brain-senses-external-v2-20261012T0600Z-callback-failure-unload.json"
)
FINAL_STATUS_RECEIPT = (
    REPO / "docs/evidence/brain-senses-external-v2-20261012T0600Z-final-expiry-status.json"
)
OUTPUT_PREFIX = "derived/partial-structural-queue-v2"
STORAGE_CONFIG = Path.home() / "Developer/memecoin-runtime/brain/config/storage-v1.json"
ACTIVATION = (
    Path.home()
    / "Developer/memecoin-runtime/brain/config/external-v2-20261012T0600Z-final/activation.json"
)
MIN_FREE_BYTES = (
    schema.DISK_FLOOR_BYTES + schema.PEAK_WRITE_MARGIN_BYTES + schema.METADATA_MARGIN_BYTES
)


class PartialQueueError(RuntimeError):
    """Evidence did not satisfy the fixed partial-prefix contract."""


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise PartialQueueError(code)


def _read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = verify.decode(raw)
    _require(isinstance(value, dict), "receipt_object")
    return value, raw


def _check_receipts(
    *, failure_path: Path, status_path: Path, activation: Mapping[str, Any], plan: Mapping[str, Any]
) -> dict[str, Any]:
    failure, failure_raw = _read_json(failure_path)
    final, final_raw = _read_json(status_path)
    _require(
        failure.get("schema") == "brain-senses-external-v2-callback-failure-stop"
        and failure.get("schema_version") == 1,
        "failure_receipt_schema",
    )
    _require(
        failure.get("lease", {}).get("service_id") == SERVICE_ID
        and failure.get("lease", {}).get("plan_sha256") == EXPECTED_PLAN_SHA256
        and failure.get("lease", {}).get("plist_sha256") == activation.get("plist_sha256"),
        "failure_receipt_lease_binding",
    )
    _require(
        failure.get("unexpected_callback", {}).get("bucket") == "20261012T062000Z"
        and failure.get("unexpected_callback", {}).get("cause") == "unknown_unrecoverable"
        and failure.get("unexpected_callback", {}).get("durable_reservation_or_intent_created")
        is False,
        "callback_failure_evidence",
    )
    unload = failure.get("post_unload", {})
    _require(
        unload.get("label_disabled") is True
        and unload.get("label_loaded") is False
        and unload.get("active_runner_pids") == [],
        "disable_evidence",
    )
    _require(
        final.get("schema") == "brain-senses-external-v2-final-expiry-status"
        and final.get("schema_version") == 1
        and final.get("service_id") == SERVICE_ID
        and final.get("plan_sha256") == EXPECTED_PLAN_SHA256,
        "final_status_schema",
    )
    projection = final.get("status_projection")
    _require(isinstance(projection, dict), "final_status_projection")
    projection_raw = canonical(projection)
    _require(
        digest(projection_raw) == final.get("status_projection_sha256"),
        "final_status_projection_hash",
    )
    _require(
        final.get("launch_failure_disable_receipt") == failure_path.name,
        "final_status_failure_receipt_link",
    )
    _require(
        final.get("launch", {}).get("disabled") is True
        and final.get("launch", {}).get("loaded") is False
        and final.get("launch", {}).get("active_runner_pids") == []
        and final.get("launch", {}).get("plist_sha256") == activation.get("plist_sha256"),
        "final_status_launch_state",
    )
    _require(
        final.get("storage", {}).get("root_id") == plan["storage"]["root_id"]
        and final.get("storage", {}).get("volume_uuid", "").lower()
        == plan["storage"]["volume_uuid"].lower()
        and final.get("storage", {}).get("health") == "ready",
        "final_status_storage_identity",
    )
    return {
        "failure": failure,
        "failure_sha256": digest(failure_raw),
        "disable_sha256": digest(failure_raw),
        "final": final,
        "final_sha256": digest(final_raw),
    }


def _status_projection(state: Mapping[str, Any], completion: Mapping[str, Any]) -> dict[str, Any]:
    sealed = state.get("sealed", {})
    return {
        "status": state.get("status"),
        "event_tail_sha256": state.get("event_tail_sha256"),
        "frontier": state.get("frontier"),
        "requests": state.get("requests"),
        "raw_bytes": state.get("v2_raw_bytes"),
        "stored_bytes": state.get("v2_stored_bytes"),
        "sealed_buckets": sorted(bucket for bucket, yes in sealed.items() if yes),
        "scheduled_windows": completion.get("scheduled_windows"),
        "sealed_windows": completion.get("sealed_windows"),
        "expired_unobserved": completion.get("expired_unobserved"),
        "missed_windows": completion.get("missed_windows"),
        "unresolved_buckets": state.get("unresolved_buckets"),
        "stop": state.get("stop"),
        "predecessor_lineage_verified": state.get("predecessor_lineage_verified"),
        "replay_authorized": state.get("replay_authorized"),
        "successor_eligible": completion.get("successor_eligible"),
    }


def _class_rows(references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for ref in references:
        key = ref["class_fingerprint_sha256"]
        row = groups.setdefault(
            key,
            {
                "class_fingerprint_sha256": key,
                "program_id": ref["program_id"],
                "instruction_data_sha256": ref["instruction_data_sha256"],
                "account_count": ref["account_count"],
                "classification": "unresolved_unsupported_no_decoder_claim",
                "reference_occurrences": 0,
                "transaction_status_counts": {s: 0 for s in ("success", "failed", "unknown")},
            },
        )
        _require(
            row["program_id"] == ref["program_id"]
            and row["instruction_data_sha256"] == ref["instruction_data_sha256"]
            and row["account_count"] == ref["account_count"],
            "class_fingerprint_collision",
        )
        row["reference_occurrences"] += 1
        row["transaction_status_counts"][ref["transaction_status"]] += 1
    return [groups[key] for key in sorted(groups)]


def _full_schedule_rows(
    planned: list[dict[str, Any]],
    sealed_buckets: list[str],
    observed_frames: list[dict[str, Any]],
    expired_buckets: set[str],
) -> list[dict[str, Any]]:
    """Retain every planned row and keep unobserved metrics unidentified."""
    _require(len(planned) == 12, "schedule_projection_forbidden")
    _require(len(sealed_buckets) == 3 and len(observed_frames) == 3, "sealed_projection_shape")
    by_bucket = {frame["bucket"]: frame for frame in observed_frames}
    _require(set(by_bucket) == set(sealed_buckets), "observed_frame_set")
    rows: list[dict[str, Any]] = []
    for row in planned:
        bucket = row["bucket"]
        if bucket in sealed_buckets:
            rows.append(by_bucket[bucket])
            continue
        state_name = (
            "callback_failed_unobserved" if bucket == "20261012T062000Z" else "expired_unobserved"
        )
        _require(bucket in expired_buckets, "unobserved_schedule_state")
        rows.append(
            {
                "bucket": bucket,
                "start_ns": row["start_ns"],
                "end_ns": row["end_ns"],
                "state": state_name,
                "produced_slots": None,
                "selected_blocks": None,
                "scanned_transactions": None,
                "success_transactions": None,
                "failed_transactions": None,
                "unknown_transactions": None,
                "reference_occurrences": None,
                "unknown_scan_transactions": None,
                "inclusion_probability": None,
            }
        )
    return rows


def compile_partial_prefix(
    reader: Any,
    predecessor_reader: Any,
    *,
    activation: Mapping[str, Any],
    expected_sources: Mapping[str, str],
    failure_path: Path = FAILURE_RECEIPT,
    status_path: Path = FINAL_STATUS_RECEIPT,
    now_ns: int | None = None,
) -> dict[str, Any]:
    """Verify the full service first, then scan only its three sealed frames."""
    now = time.time_ns() if now_ns is None else now_ns
    plan_raw = reader.read_bytes(schema.PLAN_FILE)
    plan = verify.decode(plan_raw)
    _require(
        verify.sha(plan_raw) == EXPECTED_PLAN_SHA256 == activation.get("plan_sha256"), "plan_pin"
    )
    _require(plan.get("service_id") == SERVICE_ID, "service_id")
    _require(plan.get("code_sha256") == dict(expected_sources), "source_pin")
    _require(isinstance(plan.get("storage"), dict), "storage_shape")

    # This verifies the complete append-only prefix, predecessor lineage and
    # closed source file set before any selected full block is reopened.
    state = verify.verify(
        reader,
        expected_plan_sha256=EXPECTED_PLAN_SHA256,
        expected_sources=expected_sources,
        predecessor_reader=predecessor_reader,
    )
    completion = launch.completion_view(plan, state, now)
    receipts = _check_receipts(
        failure_path=failure_path, status_path=status_path, activation=activation, plan=plan
    )
    final = receipts["final"]
    _require(
        _status_projection(state, completion) == final["status_projection"],
        "final_status_state_mismatch",
    )
    expected_frames = plan["schedule"]["frames"]
    _require(
        len(expected_frames) == 12 and completion["scheduled_windows"] == 13, "schedule_denominator"
    )
    sealed = [
        row["bucket"] for row in expected_frames if state["sealed"].get(row["bucket"]) is True
    ]
    _require(sealed == [row["bucket"] for row in expected_frames[:3]], "sealed_prefix_shape")
    _require(
        state["status"] == "verified_prefix"
        and state["stop"] is False
        and state["unresolved_buckets"] == []
        and completion["expired_unobserved"] == [row["bucket"] for row in expected_frames[3:]],
        "terminal_prefix_state",
    )
    _require(
        completion["missed_windows"] == 0
        and completion["all_windows_clean_complete"] is False
        and completion["successor_eligible"] is False,
        "incomplete_claim_boundary",
    )

    from tools.brain_senses_external_verify_v2 import _verify_bucket

    bootstrap_bucket = plan["schedule"]["bootstrap"]["bucket"]
    bootstrap = state["buckets"].get(bootstrap_bucket)
    _require(
        bootstrap is not None and state["sealed"].get(bootstrap_bucket) is True,
        "bootstrap_seal",
    )
    bootstrap_bundle = bootstrap["bundle"]
    frontier = bootstrap_bundle["bootstrap_slot"]
    observed_frames: list[dict[str, Any]] = []
    all_captures: list[dict[str, Any]] = []
    all_references: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()
    for frame_plan in expected_frames[:3]:
        bucket = frame_plan["bucket"]
        verified_bucket = _verify_bucket(reader, plan, bucket, frontier)
        frontier = verified_bucket["frontier"]
        bundle = verified_bucket["bundle"]
        frame = bundle["frame"]
        selected = bundle["selected_blocks"]
        _require(
            len(selected) == 16
            and frame.get("selection_count") == 16
            and frame.get("selected_slots") == [row["slot"] for row in selected]
            and type(frame.get("denominator")) is int
            and frame["denominator"] >= len(selected),
            "selected_frame_membership",
        )
        expected_probability = str(Fraction(len(selected), frame["denominator"]))
        _require(
            frame.get("inclusion_probability") == expected_probability,
            "selection_probability_mismatch",
        )
        captures: list[dict[str, Any]] = []
        references: list[dict[str, Any]] = []
        tx_counts: Counter[str] = Counter()
        unknown_scans = 0
        block_rows: list[dict[str, Any]] = []
        for index, member in enumerate(selected):
            ordinal = 3 + 2 * index
            capture, refs, tx_summary = structural._read_selected_block(
                reader,
                plan=plan,
                plan_sha256=EXPECTED_PLAN_SHA256,
                bucket=bucket,
                frame_index=frame_plan["index"],
                selected=member,
                ordinal=ordinal,
            )
            captures.append(capture)
            references.extend(refs)
            for status_name, count in tx_summary["transaction_status_counts"].items():
                tx_counts[status_name] += count
            unknown_scans += tx_summary["transactions_with_unknown_structural_scan"]
            block_rows.append(
                {
                    "slot": capture["slot"],
                    "blockhash": capture["blockhash"],
                    "capture_id": capture["capture_id"],
                    "capture_receipt_sha256": capture["capture_receipt_sha256"],
                    "result_sha256": capture["result_sha256"],
                    "response_sha256": capture["response_sha256"],
                    "received_ns": capture["received_ns"],
                    "available_ns": capture["available_ns"],
                    "transaction_count": tx_summary["transaction_count"],
                    "transaction_status_counts": tx_summary["transaction_status_counts"],
                    "unknown_structural_scan_transactions": tx_summary[
                        "transactions_with_unknown_structural_scan"
                    ],
                }
            )
        frame_record = {
            "bucket": bucket,
            "start_ns": frame_plan["start_ns"],
            "end_ns": frame_plan["end_ns"],
            "state": "sealed",
            "bundle_sha256": verified_bucket["bundle_sha256"],
            "seal_sha256": verified_bucket["seal_sha256"],
            "selection": {
                "inventory_sha256": frame["inventory_sha256"],
                "produced_slots": frame["denominator"],
                "selected_slots": len(selected),
                "inclusion_probability": frame["inclusion_probability"],
                "selection_rule": frame["selection_rule"],
                "selected_slot_ids": frame["selected_slots"],
            },
            "counts": {
                "produced_slots": frame["denominator"],
                "selected_blocks": len(selected),
                "scanned_transactions": sum(tx_counts.values()),
                "success_transactions": tx_counts["success"],
                "failed_transactions": tx_counts["failed"],
                "unknown_transactions": tx_counts["unknown"],
                "reference_occurrences": len(references),
                "unknown_scan_transactions": unknown_scans,
            },
            "blocks": block_rows,
        }
        observed_frames.append(frame_record)
        all_captures.extend(captures)
        all_references.extend(references)
        totals["produced_slots"] += frame["denominator"]
        totals["selected_blocks"] += len(selected)
        totals["scanned_transactions"] += sum(tx_counts.values())
        totals["success_transactions"] += tx_counts["success"]
        totals["failed_transactions"] += tx_counts["failed"]
        totals["unknown_transactions"] += tx_counts["unknown"]
        totals["reference_occurrences"] += len(references)
        totals["unknown_scan_transactions"] += unknown_scans

    _require(frontier == state["frontier"], "observed_prefix_frontier")
    _require(totals["selected_blocks"] == 48, "selected_block_denominator")
    expired = set(completion["expired_unobserved"])
    frames = _full_schedule_rows(expected_frames, sealed, observed_frames, expired)

    bootstrap_record = {
        "bucket": bootstrap_bucket,
        "state": "sealed",
        "bootstrap_slot": bootstrap_bundle["bootstrap_slot"],
        "bundle_sha256": bootstrap["bundle_sha256"],
        "seal_sha256": bootstrap["seal_sha256"],
        "request_count": bootstrap_bundle["request_count"],
        "reservation_id": bootstrap_bundle["reservation_id"],
        "request_hashes": [
            {
                "request_sha256": item["request_sha256"],
                "result_sha256": item["result_sha256"],
                "receipt_sha256": item["receipt_sha256"],
                "response_sha256": item["response_sha256"],
            }
            for item in bootstrap_bundle["requests"]
        ],
        "gap_intervals": state["gap_intervals"],
    }
    artifact = {
        "schema": "brain-senses-external-partial-structural-queue-v2",
        "schema_version": 1,
        "status": "partial_observed_prefix",
        "scope": {
            "service_id": SERVICE_ID,
            "plan_sha256": EXPECTED_PLAN_SHA256,
            "source_sha256": dict(sorted(expected_sources.items())),
            "event_tail_sha256": state["event_tail_sha256"],
            "predecessor_certificate_sha256": plan["predecessor"]["certificate_sha256"],
            "verified_gap_intervals": state["gap_intervals"],
            "final_status_receipt_sha256": receipts["final_sha256"],
            "launch_failure_receipt_sha256": receipts["failure_sha256"],
            "disable_receipt_sha256": receipts["disable_sha256"],
        },
        "bootstrap": bootstrap_record,
        "schedule": {"bootstrap": bootstrap_record, "frames": frames},
        "observed": {
            "frames": observed_frames,
            "captures": all_captures,
            "references": all_references,
            "classes": _class_rows(all_references),
        },
        "denominators": {
            "scheduled_frames": 12,
            "sealed_frames": 3,
            "unobserved_frames": 9,
            "produced_slots_in_sealed_frames": totals["produced_slots"],
            "selected_blocks_in_sealed_frames": totals["selected_blocks"],
            "scanned_transactions": totals["scanned_transactions"],
            "success_transactions": totals["success_transactions"],
            "failed_transactions": totals["failed_transactions"],
            "unknown_transactions": totals["unknown_transactions"],
            "reference_occurrences": totals["reference_occurrences"],
            "class_count": len(_class_rows(all_references)),
            "unknown_scan_transactions": totals["unknown_scan_transactions"],
        },
        "nonclaims": {
            "full_schedule_complete": False,
            "tail_coverage": "unknown",
            "no_new_class_claim": False,
            "market_completeness": False,
            "point_in_time_eligible": False,
            "economic_admission": False,
            "successor_eligible": False,
        },
        "maximum_source_availability_ns": max(
            (capture["available_ns"] for capture in all_captures), default=None
        ),
    }
    return artifact


def _assert_launch_disabled(label: str) -> None:
    domain = f"gui/{os.getuid()}"
    disabled = subprocess.run(
        ["/bin/launchctl", "print-disabled", domain], capture_output=True, text=True, timeout=10
    )
    disabled_line = next(
        (line.strip() for line in disabled.stdout.splitlines() if f'"{label}" =>' in line),
        "",
    )
    _require(
        disabled.returncode == 0
        and (disabled_line.endswith("=> disabled") or disabled_line.endswith("=> true")),
        "launch_not_disabled",
    )
    loaded = subprocess.run(
        ["/bin/launchctl", "list", label], capture_output=True, text=True, timeout=10
    )
    _require(loaded.returncode != 0, "launch_still_loaded")
    ps = subprocess.run(["/bin/ps", "-axo", "command"], capture_output=True, text=True, timeout=10)
    _require(ps.returncode == 0, "process_census_failed")
    _require(not any(label in line for line in ps.stdout.splitlines()), "runner_process_present")


def publish_partial_prefix(
    *,
    config_path: Path = STORAGE_CONFIG,
    activation_path: Path = ACTIVATION,
    failure_path: Path = FAILURE_RECEIPT,
    status_path: Path = FINAL_STATUS_RECEIPT,
    guard_factory: Any = writer_guard,
    host_lock: Any = launch.host_lock,
) -> dict[str, Any]:
    """Reverify twice and create immutable artifact plus post-fsync receipt."""
    activation, plan = launch.activation_gate(activation_path, config_path, running=False)
    _require(activation.get("plan_sha256") == EXPECTED_PLAN_SHA256, "activation_plan_pin")
    _assert_launch_disabled(activation["label"])
    sources = engine.source_bindings()
    _require(plan.get("code_sha256") == sources, "source_binding")
    with host_lock(), guard_factory(config_path, min_free_bytes=MIN_FREE_BYTES) as guard:
        guard.revalidate(min_free_bytes=MIN_FREE_BYTES)
        _require(
            guard.root_id == plan["storage"]["root_id"]
            and str(guard.volume_uuid).lower() == plan["storage"]["volume_uuid"].lower(),
            "storage_identity",
        )
        predecessor_fd = _open_relative(
            guard, plan["predecessor"]["relative_root"], create=False, minimum=MIN_FREE_BYTES
        )
        source_fd = _open_relative(
            guard, f"services/{SERVICE_ID}", create=False, minimum=MIN_FREE_BYTES
        )
        try:
            with launch.predecessor_read_lock(predecessor_fd):
                with _existing_shared_lock(
                    source_fd, schema.LOCK_FILE, device=os.fstat(source_fd).st_dev
                ):
                    with verify.DescriptorReaderV2(source_fd) as source_reader:
                        with predecessor_verify.DescriptorReader(predecessor_fd) as prior_reader:
                            first = compile_partial_prefix(
                                source_reader,
                                prior_reader,
                                activation=activation,
                                expected_sources=sources,
                                failure_path=failure_path,
                                status_path=status_path,
                            )
                            second = compile_partial_prefix(
                                source_reader,
                                prior_reader,
                                activation=activation,
                                expected_sources=sources,
                                failure_path=failure_path,
                                status_path=status_path,
                            )
                            first_bytes = canonical(first)
                            _require(
                                first_bytes == canonical(second), "deterministic_replay_mismatch"
                            )
                            output_root = f"{OUTPUT_PREFIX}/{SERVICE_ID}"
                            output_fd = _open_relative(
                                guard, output_root, create=True, minimum=MIN_FREE_BYTES
                            )
                            try:
                                _private_directory(output_fd, os.fstat(guard.mount_fd).st_dev)
                                with DescriptorWriter(
                                    output_fd,
                                    before_write=lambda: guard.revalidate(
                                        min_free_bytes=MIN_FREE_BYTES
                                    ),
                                ) as writer:
                                    _same_directory(writer.root_fd, output_fd, "output_descriptor")
                                    with writer.lock(".writer.lock"):
                                        guard.revalidate(min_free_bytes=MIN_FREE_BYTES)
                                        artifact_sha = _create_only(
                                            writer, "partial-queue.json", first_bytes
                                        )
                                        receipt_path = "publication-receipt.json"
                                        if writer.exists(receipt_path):
                                            prior_receipt = verify.decode(
                                                writer.read_bytes(receipt_path, max_bytes=64_000)
                                            )
                                            _require(
                                                isinstance(prior_receipt, dict)
                                                and prior_receipt.get("artifact_sha256")
                                                == artifact_sha
                                                and prior_receipt.get("final_status_receipt_sha256")
                                                == first["scope"]["final_status_receipt_sha256"]
                                                and prior_receipt.get(
                                                    "launch_failure_receipt_sha256"
                                                )
                                                == first["scope"]["launch_failure_receipt_sha256"]
                                                and prior_receipt.get("disable_receipt_sha256")
                                                == first["scope"]["disable_receipt_sha256"],
                                                "publication_receipt_conflict",
                                            )
                                            _fsync_existing(writer, receipt_path)
                                            return {
                                                **prior_receipt,
                                                "publication_receipt_path": (
                                                    f"{output_root}/{receipt_path}"
                                                ),
                                                "publication_receipt_sha256": digest(
                                                    canonical(prior_receipt)
                                                ),
                                            }
                                        artifact_receipt = {
                                            "schema": (
                                                "brain-senses-external-partial-structural-queue-"
                                                "publication-v2"
                                            ),
                                            "schema_version": 1,
                                            "status": "created_or_verified",
                                            "artifact_path": f"{output_root}/partial-queue.json",
                                            "artifact_sha256": artifact_sha,
                                            "final_status_receipt_sha256": first["scope"][
                                                "final_status_receipt_sha256"
                                            ],
                                            "launch_failure_receipt_sha256": first["scope"][
                                                "launch_failure_receipt_sha256"
                                            ],
                                            "disable_receipt_sha256": first["scope"][
                                                "disable_receipt_sha256"
                                            ],
                                            "source_root_id": guard.root_id,
                                            "volume_uuid": str(guard.volume_uuid),
                                            "availability_ns": time.time_ns(),
                                            "point_in_time_eligible": False,
                                            "provider_io": False,
                                        }
                                        receipt_sha = _create_only(
                                            writer,
                                            receipt_path,
                                            canonical(artifact_receipt),
                                        )
                                        return {
                                            **artifact_receipt,
                                            "publication_receipt_path": (
                                                f"{output_root}/{receipt_path}"
                                            ),
                                            "publication_receipt_sha256": receipt_sha,
                                        }
                            finally:
                                os.close(output_fd)
        finally:
            os.close(source_fd)
            os.close(predecessor_fd)


def _create_only(writer: DescriptorWriter, relative: str, payload: bytes) -> str:
    checksum = hashlib.sha256(payload).hexdigest()
    if writer.exists(relative):
        _require(
            writer.read_bytes(relative, max_bytes=len(payload) + 1) == payload,
            "immutable_collision",
        )
        _fsync_existing(writer, relative)
        return checksum
    writer.create(relative, payload)
    _fsync_existing(writer, relative)
    return checksum


def _fsync_existing(writer: DescriptorWriter, relative: str) -> None:
    from tools.brain_senses_structural_unknown_queue_v1 import _fsync_existing as fsync

    fsync(writer, relative)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("publish", choices=("publish",))
    parser.add_argument("--storage-config", type=Path, default=STORAGE_CONFIG)
    parser.add_argument("--activation", type=Path, default=ACTIVATION)
    parser.add_argument("--failure-receipt", type=Path, default=FAILURE_RECEIPT)
    parser.add_argument("--status-receipt", type=Path, default=FINAL_STATUS_RECEIPT)
    args = parser.parse_args()
    try:
        result = publish_partial_prefix(
            config_path=args.storage_config,
            activation_path=args.activation,
            failure_path=args.failure_receipt,
            status_path=args.status_receipt,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"status": "stopped", "error_class": type(exc).__name__, "provider_io": False},
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
