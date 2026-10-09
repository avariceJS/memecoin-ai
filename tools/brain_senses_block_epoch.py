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
from pathlib import Path
from typing import Any

from memecoin_ai.domain import canonical, digest
from memecoin_ai.sources import Capture, ReadOnlyRPC, verify_capture_storage_codec

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.brain_senses_block_provider_pilot import PROVIDERS, run_once  # noqa: E402
from tools.brain_senses_program_watch import (  # noqa: E402
    _create,
    _provider_endpoint,
    _read,
    _sync_dir,
)

MAX_WINDOW_SLOTS = 64
MAX_CAPTURE_BYTES = 1_000_000_000


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


def start_epoch(
    root: Path,
    *,
    window_slots: int = 20,
    old_epoch_root: Path | None = None,
    providers: tuple[str, ...] = PROVIDERS,
    slots_per_hour_estimate: int | None = None,
    sampling_seed: str | None = None,
    signature_check_all_slots: bool = False,
    capture_storage_codec: str = "raw",
    clients: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not 1 <= window_slots <= MAX_WINDOW_SLOTS:
        raise ValueError("window_size_outside_bound")
    verify_capture_storage_codec(capture_storage_codec)
    if (
        not providers
        or len(set(providers)) != len(providers)
        or any(provider not in PROVIDERS for provider in providers)
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
        "max_capture_response_bytes": MAX_CAPTURE_BYTES,
        "capture_storage_codec": capture_storage_codec,
        "retry_policy": (
            "no retries; preserve anchor failures and do not dispatch blocks after anchor failure"
        ),
        "previous_epoch_root": str(old_epoch_root) if old_epoch_root else None,
        "gap_policy": "new epoch only; never bridge or infer slot coverage from the old epoch",
        "scope": "fresh finalized anchor and one bounded provider-specific block window",
    }
    if plan_path.exists():
        if _read(plan_path) != plan:
            raise ValueError("epoch_plan_changed")
    else:
        _create(plan_path, plan)
    if (root / "STOP").exists():
        raise ValueError("epoch_stopped")
    if any(any((root / name).glob("*.json")) for name in ("requests", "request-results")):
        raise ValueError("epoch_anchor_attempts_already_exist")

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
                max_bytes=MAX_CAPTURE_BYTES,
                keep_alive=True,
            )
    try:
        anchors: dict[str, dict[str, Any]] = {}
        for sequence, provider in enumerate(providers, start=1):
            anchors[provider] = _anchor_once(root, provider, sequence, clients[provider])
        if any(row["status"] != "captured" for row in anchors.values()):
            failed = [provider for provider, row in anchors.items() if row["status"] != "captured"]
            _create(
                root / "STOP",
                dict(reason="paired_finalized_anchor_incomplete", failed_providers=failed),
            )
            summary = {"status": "stopped_anchor_failure", "anchors": anchors}
            _create(root / "epoch-summary.json", summary)
            return summary

        anchor_slots = {provider: anchors[provider]["slot"] for provider in providers}
        common_tip = min(anchor_slots.values())
        if sampling_seed is not None:
            invalid_sampling_frame = (
                not sampling_seed
                or not slots_per_hour_estimate
                or slots_per_hour_estimate < window_slots
            )
            if invalid_sampling_frame:
                raise ValueError("invalid_hourly_sampling_frame")
            lower = max(1, common_tip - slots_per_hour_estimate + 1)
            upper = common_tip - window_slots + 1
            window_start = random.Random(sampling_seed).randint(lower, upper)
        else:
            lower = upper = None
            window_start = max(1, common_tip - window_slots + 1)
        slot_range = [window_start, window_start + window_slots]
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
            "selection_frame": {
                "start_inclusive": lower,
                "end_exclusive": (upper + window_slots) if upper is not None else None,
                "slots_per_hour_estimate": slots_per_hour_estimate,
                "sampling_seed": sampling_seed,
                "selection": "uniform_seeded_contiguous_window_within_recent_hour"
                if sampling_seed is not None
                else "latest_finalized_slots",
            },
            "old_epoch_gap": old_gap,
        }
        _create(root / "window-plan.json", window_metadata)
        signature_slots = (
            list(range(*slot_range))
            if signature_check_all_slots
            else [slot_range[0] + (slot_range[1] - slot_range[0]) // 2]
        )
        paired = run_once(
            root / "paired-block-capture",
            slot_ranges=[slot_range],
            request_cap=MAX_WINDOW_SLOTS * 2 + 2,
            helius_pace=0.35,
            sampling_metadata=window_metadata,
            signature_sample_slots=signature_slots,
            providers=providers,
            max_captured_bytes=MAX_CAPTURE_BYTES,
            helius_total_credit_cap=MAX_WINDOW_SLOTS * 2 + 10,
            helius_daily_credit_cap=MAX_WINDOW_SLOTS * 2 + 10,
            clients=clients,
            capture_storage_codec=capture_storage_codec,
        )
        block_capture = {
            "completed": paired["completed"],
            "attempts": paired["attempts"],
            "helius_credit_reservations": paired["helius_credit_reservations"],
            "captured_response_bytes": paired["captured_response_bytes"],
            "outcomes": paired["outcomes"],
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
