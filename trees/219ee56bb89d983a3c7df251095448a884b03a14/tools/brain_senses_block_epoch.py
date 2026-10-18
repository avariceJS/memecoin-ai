#!/usr/bin/env python3
"""Start a new finalized-block source epoch without resuming an older cursor."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import platform
import random
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

from memecoin_ai.domain import canonical, digest
from memecoin_ai.sources import Capture, ReadOnlyRPC, verify_capture_storage_codec

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.brain_senses_block_provider_pilot import (  # noqa: E402
    PROVIDERS,
    SUPPORTED_PROVIDERS,
    run_once,
)
from tools.brain_senses_program_watch import (  # noqa: E402
    _create,
    _provider_endpoint,
    _read,
    _sync_dir,
)

MAX_WINDOW_SLOTS = 512
MAX_CAPTURE_BYTES = 2_000_000_000
MAX_RPC_RESPONSE_BYTES = 32_000_000


def stratified_slot_windows(
    frame_start: int,
    frame_end: int,
    *,
    window_slots: int,
    stratum_count: int,
    seed: str,
) -> tuple[list[dict[str, Any]], list[int], dict[str, str]]:
    """Select one uniform window per equal slot stratum and record exact inclusion odds."""
    if frame_start < 1 or frame_end <= frame_start or not seed:
        raise ValueError("invalid_stratified_frame")
    if not 1 <= stratum_count <= window_slots or window_slots % stratum_count:
        raise ValueError("invalid_stratified_window_shape")
    frame_width = frame_end - frame_start
    if frame_width < window_slots:
        raise ValueError("stratified_frame_too_small")
    window_width = window_slots // stratum_count
    rng = random.Random(seed)
    windows: list[dict[str, Any]] = []
    inclusion_probabilities: dict[str, str] = {}
    for index in range(stratum_count):
        stratum_start = frame_start + frame_width * index // stratum_count
        stratum_end = frame_start + frame_width * (index + 1) // stratum_count
        latest_start = stratum_end - window_width
        if latest_start < stratum_start:
            raise ValueError("stratum_too_small_for_contiguous_window")
        selected_start = rng.randint(stratum_start, latest_start)
        candidate_count = latest_start - stratum_start + 1
        windows.append(
            {
                "stratum": index,
                "stratum_slot_range_exclusive": [stratum_start, stratum_end],
                "selected_slot_range_exclusive": [selected_start, selected_start + window_width],
                "candidate_start_count": candidate_count,
            }
        )
        for slot in range(stratum_start, stratum_end):
            first_start = max(stratum_start, slot - window_width + 1)
            last_start = min(latest_start, slot)
            included_start_count = max(0, last_start - first_start + 1)
            probability = Fraction(included_start_count, candidate_count)
            inclusion_probabilities[str(slot)] = (
                f"{probability.numerator}/{probability.denominator}"
            )
    selected_slots = sorted(
        slot for window in windows for slot in range(*window["selected_slot_range_exclusive"])
    )
    return windows, selected_slots, inclusion_probabilities


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _durable_capture(capture: Capture, path: Path) -> None:
    capture.save(path)
    for name in ("response.json", "receipt.json"):
        with (path / name).open("rb") as stream:
            os.fsync(stream.fileno())
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    _sync_dir(path.parent)


def _anchor_once(root: Path, provider: str, sequence: int, client: Any) -> dict[str, Any]:
    params = [{"commitment": "finalized"}]
    body = canonical({"jsonrpc": "2.0", "id": 1, "method": "getSlot", "params": params}).encode()
    reservation = {
        "schema_version": 1,
        "sequence": sequence,
        "provider": provider,
        "method": "getSlot",
        "params": params,
        "request_sha256": hashlib.sha256(body).hexdigest(),
        "reserved_ns": time.time_ns(),
        "expected_credit_reservation": 1 if provider == "helius-free" else 0,
        "policy": "single finalized anchor request; no retry",
    }
    reservation_path = root / "requests" / f"{sequence:012d}.json"
    _create(reservation_path, reservation)
    try:
        capture = client.fetch("getSlot", params)
        capture_path = root / "captures" / provider / f"{sequence:012d}"
        _durable_capture(capture, capture_path)
        value = capture.result()
        if type(value) is not int or value < 1:
            raise ValueError("finalized_anchor_shape")
        result = {
            "sequence": sequence,
            "provider": provider,
            "method": "getSlot",
            "status": "captured",
            "slot": value,
            "capture_path": str(capture_path.relative_to(root)),
            "response_sha256": digest(capture.response),
            "response_bytes": len(capture.response),
            "request_started_ns": capture.request_started_ns,
            "receipt_ns": capture.received_ns,
        }
    except Exception as exc:
        result = {
            "sequence": sequence,
            "provider": provider,
            "method": "getSlot",
            "status": "request_failed",
            "error_class": type(exc).__name__,
            "receipt_ns": time.time_ns(),
        }
    _create(root / "request-results" / f"{sequence:012d}.json", result)
    return result


def _recover_anchor(root: Path, provider: str, sequence: int, client: Any) -> dict[str, Any]:
    """Reuse a durable anchor outcome; an unresolved reservation is never replayed."""
    name = f"{sequence:012d}.json"
    reservation_path = root / "requests" / name
    result_path = root / "request-results" / name
    if not reservation_path.exists():
        if result_path.exists():
            raise ValueError("epoch_anchor_result_without_reservation")
        return _anchor_once(root, provider, sequence, client)
    reservation = _read(reservation_path)
    params = [{"commitment": "finalized"}]
    request_body = canonical(
        {"jsonrpc": "2.0", "id": 1, "method": "getSlot", "params": params}
    ).encode()
    if (
        reservation.get("sequence") != sequence
        or reservation.get("provider") != provider
        or reservation.get("method") != "getSlot"
        or reservation.get("params") != params
        or reservation.get("request_sha256") != hashlib.sha256(request_body).hexdigest()
    ):
        raise ValueError("epoch_anchor_request_identity_mismatch")
    if not result_path.exists():
        _create(
            root / "STOP",
            dict(
                reason="ambiguous_epoch_anchor_requires_manual_reconciliation",
                sequence=sequence,
                provider=provider,
                time_ns=time.time_ns(),
            ),
        )
        raise ValueError("ambiguous_epoch_anchor_requires_manual_reconciliation")
    result = _read(result_path)
    if (
        result.get("sequence") != sequence
        or result.get("provider") != provider
        or result.get("method") != "getSlot"
    ):
        raise ValueError("epoch_anchor_result_identity_mismatch")
    if result.get("status") == "captured":
        capture = Capture.load(root / result["capture_path"])
        if digest(capture.response) != result.get("response_sha256"):
            raise ValueError("epoch_anchor_capture_hash_mismatch")
        if capture.result() != result.get("slot"):
            raise ValueError("epoch_anchor_capture_result_mismatch")
    return result


def _validate_window_lineage(
    root: Path,
    window: dict[str, Any],
    *,
    plan_path: Path,
    anchors: dict[str, dict[str, Any]],
    window_slots: int,
    slots_per_hour_estimate: int | None,
    sampling_seed: str | None,
    signature_check_all_slots: bool,
    stratum_count: int,
    paired_provider: str | None,
    paired_slots_per_stratum: int,
    old_epoch_root: Path | None,
) -> None:
    """Recompute the pinned selection so edits cannot redirect a resumed request plan."""
    if (
        window.get("epoch_id") != root.name
        or window.get("anchor_plan_sha256") != _sha(plan_path)
        or window.get("anchors") != anchors
    ):
        raise ValueError("epoch_window_selection_lineage_mismatch")
    common_tip = min(row["slot"] for row in anchors.values())
    if sampling_seed is not None:
        if (
            not sampling_seed
            or not slots_per_hour_estimate
            or slots_per_hour_estimate < window_slots // max(1, stratum_count)
        ):
            raise ValueError("invalid_hourly_sampling_frame")
        lower = max(1, common_tip - slots_per_hour_estimate + 1)
        upper = common_tip + 1
        if stratum_count:
            selected_windows, selected_slots, probabilities = stratified_slot_windows(
                lower,
                upper,
                window_slots=window_slots,
                stratum_count=stratum_count,
                seed=sampling_seed,
            )
            slot_range = [lower, upper]
        else:
            start = random.Random(sampling_seed).randint(lower, upper - window_slots)
            slot_range = [start, start + window_slots]
            selected_windows, probabilities = [], {}
            selected_slots = list(range(*slot_range))
        selection_name = (
            "seeded_equal_slot_strata_with_contiguous_windows"
            if stratum_count
            else "uniform_seeded_contiguous_window_within_recent_hour"
        )
    else:
        lower = upper = None
        slot_start = max(1, common_tip - window_slots + 1)
        slot_range = [slot_start, slot_start + window_slots]
        selected_windows, probabilities = [], {}
        selected_slots = list(range(*slot_range))
        selection_name = "latest_finalized_slots"

    provider_full: dict[str, list[int]] | None = None
    provider_signatures: dict[str, list[int]] | None = None
    paired_probabilities: dict[str, str] = {}
    if paired_provider is not None:
        if not selected_windows or paired_slots_per_stratum < 1:
            raise ValueError("paired_provider_requires_stratified_subsampling")
        provider_full = {"helius-free": selected_slots}
        paired_slots: list[int] = []
        pair_rng = random.Random(f"{sampling_seed}:paired:{paired_provider}")
        for sampled_window in selected_windows:
            start, end = sampled_window["selected_slot_range_exclusive"]
            candidates = list(range(start, end))
            if paired_slots_per_stratum > len(candidates):
                raise ValueError("paired_slots_exceed_selected_stratum")
            selected_for_provider = sorted(pair_rng.sample(candidates, paired_slots_per_stratum))
            paired_slots.extend(selected_for_provider)
            conditional = Fraction(paired_slots_per_stratum, len(candidates))
            for slot in selected_for_provider:
                probability = Fraction(probabilities[str(slot)]) * conditional
                paired_probabilities[str(slot)] = (
                    f"{probability.numerator}/{probability.denominator}"
                )
        provider_full[paired_provider] = sorted(paired_slots)
        provider_signatures = {provider: list(slots) for provider, slots in provider_full.items()}

    frame = {
        "start_inclusive": lower,
        "end_exclusive": upper,
        "slots_per_hour_estimate": slots_per_hour_estimate,
        "sampling_seed": sampling_seed,
        "stratum_count": stratum_count,
        "selection": selection_name,
        "selected_windows": selected_windows,
        "slot_inclusion_probabilities": probabilities,
    }
    expected = {
        "common_tip_slot": common_tip,
        "selected_slot_range_exclusive": slot_range,
        "full_sample_slots": selected_slots,
        "signature_only_slots": (
            selected_slots
            if signature_check_all_slots
            else [selected_slots[len(selected_slots) // 2]]
        ),
        "provider_full_sample_slots": provider_full,
        "provider_signature_sample_slots": provider_signatures,
        "paired_selection_probabilities": paired_probabilities,
        "selection_frame": frame,
    }
    if any(window.get(key) != value for key, value in expected.items()):
        raise ValueError("epoch_window_selection_recomputation_mismatch")
    old_gap = window.get("old_epoch_gap")
    if old_epoch_root is None:
        expected_gap = None
    else:
        old_stop = old_epoch_root / "STOP"
        old_results = old_epoch_root / "request-results"
        old_last_receipt = max(
            (
                _read(path).get("receipt_ns") or _read(path).get("received_ns") or 0
                for path in old_results.glob("*.json")
            ),
            default=None,
        )
        expected_gap = {
            "old_epoch_stop": _read(old_stop) if old_stop.exists() else None,
            "last_receipt_ns_in_old_epoch": old_last_receipt,
            "first_new_anchor_request_ns": min(
                row["request_started_ns"] for row in anchors.values()
            ),
            "claim": (
                "unobserved interval between source epochs; "
                "no slot-range completeness inference"
            ),
        }
    if old_gap != expected_gap:
        raise ValueError("epoch_gap_lineage_mismatch")


def start_epoch(
    root: Path,
    *,
    window_slots: int = 20,
    old_epoch_root: Path | None = None,
    providers: tuple[str, ...] = PROVIDERS,
    slots_per_hour_estimate: int | None = None,
    sampling_seed: str | None = None,
    signature_check_all_slots: bool = False,
    stratum_count: int = 0,
    paired_provider: str | None = None,
    paired_slots_per_stratum: int = 0,
    max_capture_response_bytes: int = MAX_CAPTURE_BYTES,
    max_capture_stored_bytes: int = 1_000_000_000,
    capture_storage_codec: str = "raw",
    clients: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not 1 <= window_slots <= MAX_WINDOW_SLOTS:
        raise ValueError("window_size_outside_bound")
    if stratum_count < 0 or stratum_count > window_slots:
        raise ValueError("stratum_count_outside_bound")
    if stratum_count and window_slots % stratum_count:
        raise ValueError("window_slots_must_divide_evenly_across_strata")
    if paired_provider is not None and (
        paired_provider not in providers or paired_provider == "helius-free"
    ):
        raise ValueError("invalid_paired_provider")
    if paired_slots_per_stratum < 0 or (
        paired_provider is None and paired_slots_per_stratum
    ):
        raise ValueError("invalid_paired_slots_per_stratum")
    if not MAX_RPC_RESPONSE_BYTES <= max_capture_response_bytes <= 10_000_000_000:
        raise ValueError("epoch_capture_response_cap_outside_bound")
    if not MAX_RPC_RESPONSE_BYTES <= max_capture_stored_bytes <= 10_000_000_000:
        raise ValueError("epoch_capture_stored_cap_outside_bound")
    verify_capture_storage_codec(capture_storage_codec)
    if (
        not providers
        or len(set(providers)) != len(providers)
        or any(provider not in SUPPORTED_PROVIDERS for provider in providers)
    ):
        raise ValueError("invalid_provider_set")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    for name in ("requests", "request-results", "captures", "events"):
        (root / name).mkdir(exist_ok=True, mode=0o700)
        os.chmod(root / name, 0o700)
    plan_path = root / "epoch-plan.json"
    script_path = Path(__file__)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    plan = {
        "schema_version": 1,
        "epoch_id": root.name,
        "created_ns": time.time_ns(),
        "code_commit": commit,
        "code_sha256": _sha(script_path),
        "python_version": platform.python_version(),
        "providers": list(providers),
        "anchor": {"method": "getSlot", "commitment": "finalized"},
        "window_slots": window_slots,
        "slots_per_hour_estimate": slots_per_hour_estimate,
        "sampling_seed": sampling_seed,
        "signature_check_all_slots": signature_check_all_slots,
        "stratum_count": stratum_count,
        "max_capture_response_bytes": max_capture_response_bytes,
        "max_capture_stored_bytes": max_capture_stored_bytes,
        "capture_storage_codec": capture_storage_codec,
        "retry_policy": (
            "no retries; preserve anchor failures and do not dispatch blocks after anchor failure"
        ),
        "previous_epoch_root": str(old_epoch_root) if old_epoch_root else None,
        "gap_policy": "new epoch only; never bridge or infer slot coverage from the old epoch",
        "scope": "fresh finalized anchor and one bounded provider-specific block window",
    }
    if plan_path.exists():
        existing_plan = _read(plan_path)
        comparable_plan = dict(plan)
        comparable_plan["created_ns"] = existing_plan.get("created_ns")
        if existing_plan != comparable_plan:
            raise ValueError("epoch_plan_changed")
        plan = existing_plan
    else:
        _create(plan_path, plan)
    if (root / "STOP").exists():
        raise ValueError("epoch_stopped")
    summary_path = root / "epoch-summary.json"
    if summary_path.exists():
        return _read(summary_path)

    own_clients = clients is None
    if own_clients:
        clients = {}
        for provider in providers:
            endpoint, alias = _provider_endpoint(provider)
            clients[provider] = ReadOnlyRPC(
                endpoint,
                alias,
                timeout=30,
                # The same connections are used for finalized block bodies below.
                max_bytes=MAX_RPC_RESPONSE_BYTES,
                keep_alive=True,
            )
    try:
        anchors: dict[str, dict[str, Any]] = {}
        for sequence, provider in enumerate(providers, start=1):
            anchors[provider] = _recover_anchor(root, provider, sequence, clients[provider])
        if any(row["status"] != "captured" for row in anchors.values()):
            failed = [provider for provider, row in anchors.items() if row["status"] != "captured"]
            _create(
                root / "STOP",
                dict(reason="paired_finalized_anchor_incomplete", failed_providers=failed),
            )
            summary = {"status": "stopped_anchor_failure", "anchors": anchors}
            _create(root / "epoch-summary.json", summary)
            return summary

        window_path = root / "window-plan.json"
        if window_path.exists():
            window_metadata = _read(window_path)
            _validate_window_lineage(
                root,
                window_metadata,
                plan_path=plan_path,
                anchors=anchors,
                window_slots=window_slots,
                slots_per_hour_estimate=slots_per_hour_estimate,
                sampling_seed=sampling_seed,
                signature_check_all_slots=signature_check_all_slots,
                stratum_count=stratum_count,
                paired_provider=paired_provider,
                paired_slots_per_stratum=paired_slots_per_stratum,
                old_epoch_root=old_epoch_root,
            )
            slot_range = window_metadata["selected_slot_range_exclusive"]
            selected_slots = window_metadata["full_sample_slots"]
            selected_windows = window_metadata["selection_frame"].get("selected_windows", [])
            inclusion_probabilities = window_metadata["selection_frame"].get(
                "slot_inclusion_probabilities", {}
            )
            old_gap = window_metadata.get("old_epoch_gap")
            provider_full_sample_slots = window_metadata.get("provider_full_sample_slots")
            provider_signature_sample_slots = window_metadata.get(
                "provider_signature_sample_slots"
            )
        else:
            anchor_slots = {provider: anchors[provider]["slot"] for provider in providers}
            common_tip = min(anchor_slots.values())
            if sampling_seed is not None:
                invalid_sampling_frame = (
                    not sampling_seed
                    or not slots_per_hour_estimate
                    or slots_per_hour_estimate < window_slots // max(1, stratum_count)
                )
                if invalid_sampling_frame:
                    raise ValueError("invalid_hourly_sampling_frame")
                lower = max(1, common_tip - slots_per_hour_estimate + 1)
                upper = common_tip + 1
                selected_windows: list[dict[str, Any]] = []
                inclusion_probabilities: dict[str, str] = {}
                if stratum_count:
                    (
                        selected_windows,
                        selected_slots,
                        inclusion_probabilities,
                    ) = stratified_slot_windows(
                        lower,
                        upper,
                        window_slots=window_slots,
                        stratum_count=stratum_count,
                        seed=sampling_seed,
                    )
                    window_start = lower
                    slot_range = [lower, upper]
                else:
                    latest_start = upper - window_slots
                    window_start = random.Random(sampling_seed).randint(lower, latest_start)
                    slot_range = [window_start, window_start + window_slots]
                    selected_slots = list(range(*slot_range))
            else:
                lower = upper = None
                window_start = max(1, common_tip - window_slots + 1)
                slot_range = [window_start, window_start + window_slots]
                selected_windows = []
                selected_slots = list(range(*slot_range))
                inclusion_probabilities = {}
            provider_full_sample_slots: dict[str, list[int]] | None = None
            provider_signature_sample_slots: dict[str, list[int]] | None = None
            paired_selection_probabilities: dict[str, str] = {}
            if paired_provider is not None:
                if not selected_windows or paired_slots_per_stratum < 1:
                    raise ValueError("paired_provider_requires_stratified_subsampling")
                provider_full_sample_slots = {"helius-free": selected_slots}
                paired_slots: list[int] = []
                pair_rng = random.Random(f"{sampling_seed}:paired:{paired_provider}")
                for window in selected_windows:
                    start, end = window["selected_slot_range_exclusive"]
                    candidates = list(range(start, end))
                    if paired_slots_per_stratum > len(candidates):
                        raise ValueError("paired_slots_exceed_selected_stratum")
                    selected_for_provider = sorted(
                        pair_rng.sample(candidates, paired_slots_per_stratum)
                    )
                    paired_slots.extend(selected_for_provider)
                    conditional_probability = Fraction(
                        paired_slots_per_stratum, len(candidates)
                    )
                    for slot in selected_for_provider:
                        primary_probability = Fraction(inclusion_probabilities[str(slot)])
                        combined_probability = primary_probability * conditional_probability
                        paired_selection_probabilities[str(slot)] = (
                            f"{combined_probability.numerator}/{combined_probability.denominator}"
                        )
                provider_full_sample_slots[paired_provider] = sorted(paired_slots)
                provider_signature_sample_slots = {
                    provider: list(slots) for provider, slots in provider_full_sample_slots.items()
                }
            old_gap = None
            if old_epoch_root is not None:
                old_stop = old_epoch_root / "STOP"
                old_results = old_epoch_root / "request-results"
                old_last_receipt = max(
                    (
                        _read(path).get("receipt_ns") or _read(path).get("received_ns") or 0
                        for path in old_results.glob("*.json")
                    ),
                    default=None,
                )
                old_gap = {
                    "old_epoch_stop": _read(old_stop) if old_stop.exists() else None,
                    "last_receipt_ns_in_old_epoch": old_last_receipt,
                    "first_new_anchor_request_ns": min(
                        row["request_started_ns"] for row in anchors.values()
                    ),
                    "claim": (
                        "unobserved interval between source epochs; "
                        "no slot-range completeness inference"
                    ),
                }
            window_metadata = {
                "epoch_id": root.name,
                "anchor_plan_sha256": _sha(plan_path),
                "anchors": anchors,
                "common_tip_slot": common_tip,
                "anchor_slot_difference": (
                    abs(anchor_slots[providers[0]] - anchor_slots[providers[1]])
                    if len(providers) > 1
                    else None
                ),
                "selected_slot_range_exclusive": slot_range,
                "full_sample_slots": selected_slots,
                "signature_only_slots": (
                    selected_slots
                    if signature_check_all_slots
                    else [selected_slots[len(selected_slots) // 2]]
                ),
                "provider_full_sample_slots": provider_full_sample_slots,
                "provider_signature_sample_slots": provider_signature_sample_slots,
                "paired_selection_probabilities": paired_selection_probabilities,
                "selection_frame": {
                    "start_inclusive": lower,
                    "end_exclusive": upper,
                    "slots_per_hour_estimate": slots_per_hour_estimate,
                    "sampling_seed": sampling_seed,
                    "stratum_count": stratum_count,
                    "selection": (
                        "seeded_equal_slot_strata_with_contiguous_windows"
                        if stratum_count
                        else "uniform_seeded_contiguous_window_within_recent_hour"
                        if sampling_seed is not None
                        else "latest_finalized_slots"
                    ),
                    "selected_windows": selected_windows,
                    "slot_inclusion_probabilities": inclusion_probabilities,
                },
                "old_epoch_gap": old_gap,
            }
            _create(root / "window-plan.json", window_metadata)
        signature_slots = window_metadata["signature_only_slots"]
        paired = run_once(
            root / "paired-block-capture",
            slot_ranges=[slot_range],
            request_cap=window_slots * 2 + 2,
            helius_pace=0.35,
            sampling_metadata=window_metadata,
            signature_sample_slots=signature_slots,
            full_sample_slots=selected_slots,
            provider_full_sample_slots=provider_full_sample_slots,
            provider_signature_sample_slots=provider_signature_sample_slots,
            providers=providers,
            max_captured_bytes=max_capture_response_bytes,
            max_captured_stored_bytes=max_capture_stored_bytes,
            helius_total_credit_cap=window_slots * 2 + 10,
            helius_daily_credit_cap=window_slots * 2 + 10,
            clients=clients,
            capture_storage_codec=capture_storage_codec,
        )
        comparison_window = paired["comparison"]["windows"][0]
        rows_by_slot = {row["slot"]: row for row in comparison_window["slots_compared"]}
        target_provider = "helius-free" if "helius-free" in providers else providers[0]
        stratum_coverage = []
        for window in selected_windows:
            selected_range = window["selected_slot_range_exclusive"]
            stratum_rows = [rows_by_slot.get(slot) for slot in range(*selected_range)]
            qualified_count = sum(
                row is not None
                and row["providers"][target_provider]["observation_status"]
                == "qualified_structural_reference_count"
                for row in stratum_rows
            )
            stratum_coverage.append(
                {
                    "stratum": window["stratum"],
                    "selected_slot_range_exclusive": selected_range,
                    "expected_selected_slots": selected_range[1] - selected_range[0],
                    "qualified_slot_count": qualified_count,
                    "status": (
                        "all_selected_slots_qualified"
                        if qualified_count == selected_range[1] - selected_range[0]
                        else "partial_or_unknown_selected_support"
                    ),
                }
            )
        block_capture = {
            "completed": paired["completed"],
            "attempts": paired["attempts"],
            "helius_credit_reservations": paired["helius_credit_reservations"],
            "captured_response_bytes": paired["captured_response_bytes"],
            "stored_response_bytes": paired["stored_response_bytes"],
            "outcomes": paired["outcomes"],
            "qualified_sample_slot_count": sum(
                row["providers"][target_provider]["observation_status"]
                == "qualified_structural_reference_count"
                for row in comparison_window["slots_compared"]
            ),
            "stratum_coverage": stratum_coverage,
        }
        summary = {
            "status": "provider_window_attempted",
            "anchors": anchors,
            "window_plan_sha256": _sha(root / "window-plan.json"),
            "slot_range": slot_range,
            "providers": list(providers),
            "block_capture": block_capture,
            "gap": old_gap,
            "claim": "new bounded finalized block observation; does not close prior epoch gap",
        }
        _create(root / "epoch-summary.json", summary)
        return summary
    finally:
        if own_clients and clients:
            for client in clients.values():
                client.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--old-epoch-root", type=Path)
    parser.add_argument("--window-slots", type=int, default=20)
    parser.add_argument("--capture-storage-codec", choices=("raw", "zstd"), default="raw")
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.root / ".epoch.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("block epoch already active", file=sys.stderr)
            return 2
        os.chmod(args.root, 0o700)
        try:
            summary = start_epoch(
                args.root,
                window_slots=args.window_slots,
                old_epoch_root=args.old_epoch_root,
                capture_storage_codec=args.capture_storage_codec,
            )
            print(canonical(summary), flush=True)
            return 0 if summary["status"] == "provider_window_attempted" else 1
        except Exception as exc:
            if not (args.root / "STOP").exists():
                _create(
                    args.root / "STOP",
                    dict(reason="epoch_exception", error_class=type(exc).__name__),
                )
            print(f"block epoch stopped: {type(exc).__name__}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
