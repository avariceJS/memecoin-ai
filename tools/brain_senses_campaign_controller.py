#!/usr/bin/env python3
"""Finite lease controller for uniquely rooted Helius inventory campaigns."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memecoin_ai.domain import canonical  # noqa: E402
from tools.brain_senses_block_epoch_v2 import _validate_journal  # noqa: E402
from tools.brain_senses_block_epoch_v2_schedule import (  # noqa: E402
    _create,
    _read,
    _replace,
    _usage,
)
from tools.brain_senses_block_epoch_v3_schedule import (  # noqa: E402
    ATTEMPTS_MAX,
    CAMPAIGN_RAW_BYTES,
    CAMPAIGN_STORED_BYTES,
    DISK_FLOOR_BYTES,
    FRAME_RAW_BYTES,
    FRAME_STORED_BYTES,
    PEAK_WRITE_MARGIN_BYTES,
    REQUESTS_MAX,
    _create_stop_atomically,
    _read_manifest,
    _reconcile_epoch,
    _usage_v3,
)

CONTROLLER_LABEL = "ai.memecoin.brain.senses.helius-inventory-controller-v1"
LEASE_NS = 72 * 60 * 60 * 1_000_000_000
MAX_CAMPAIGNS = 3
LEASE_REQUESTS = MAX_CAMPAIGNS * REQUESTS_MAX
LEASE_RAW_BYTES = MAX_CAMPAIGNS * CAMPAIGN_RAW_BYTES
LEASE_STORED_BYTES = MAX_CAMPAIGNS * CAMPAIGN_STORED_BYTES


def _policy(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "controller_id": state["controller_id"],
        "lease_start_ns": state["lease_start_ns"],
        "lease_expires_ns": state["lease_expires_ns"],
        "max_campaigns": MAX_CAMPAIGNS,
        "aggregate_request_cap": LEASE_REQUESTS,
        "aggregate_raw_byte_cap": LEASE_RAW_BYTES,
        "aggregate_stored_byte_cap": LEASE_STORED_BYTES,
        "disk_floor_bytes": DISK_FLOOR_BYTES,
        "predecessor_root": state["predecessor_root"],
        "recovery_receipt_sha256": state.get("recovery_receipt_sha256"),
        "recovery_count": state.get("recovery_count", 0),
        "source_sha256": {
            name: hashlib.sha256((REPO_ROOT / name).read_bytes()).hexdigest()
            for name in (
                "tools/brain_senses_campaign_controller.py",
                "tools/brain_senses_block_epoch_v3_schedule.py",
                "tools/brain_senses_block_epoch_v3_launch.py",
                "ops/launchd/ai.memecoin.brain.senses.helius-inventory-controller.plist",
            )
        },
    }


def _atomic_bytes(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        tmp.unlink(missing_ok=True)


def _write_json(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    if exclusive:
        _create(path, value)
    else:
        _atomic_bytes(path, (canonical(value) + "\n").encode())


def _v2_lineage(predecessor_root: Path, *, campaign_id: str, now_ns: int) -> dict[str, Any]:
    stop_path = predecessor_root / "STOP"
    if not stop_path.is_file():
        raise ValueError("predecessor_stop_missing")
    stop = _read(stop_path)
    _verify_v2_predecessor(predecessor_root, stop)
    if (
        stop.get("campaign_id") != "helius-inventory-v2-16-hourly-20261010"
        or stop.get("status") != "closed_after_authorized_final_bucket"
        or stop.get("last_terminal_status") != "frame_complete"
        or stop.get("last_applied_bucket") != "20261010T020000Z"
    ):
        raise ValueError("v2_predecessor_not_normally_closed")
    usage = stop.get("usage") or {}
    if (
        usage.get("requests") != 104
        or usage.get("reserved_raw_bytes") != 0
        or usage.get("reserved_stored_bytes") != 0
        or usage.get("requests", LEASE_REQUESTS + 1) > REQUESTS_MAX
        or usage.get("raw_bytes", LEASE_RAW_BYTES + 1) > CAMPAIGN_RAW_BYTES
        or usage.get("stored_bytes", LEASE_STORED_BYTES + 1) > CAMPAIGN_STORED_BYTES
    ):
        raise ValueError("v2_predecessor_usage_unreconciled")
    return {
        "schema_version": 1,
        "provider": "helius-free",
        "campaign_id": campaign_id,
        "predecessor_campaign_id": stop["campaign_id"],
        "predecessor_stop_sha256": hashlib.sha256(stop_path.read_bytes()).hexdigest(),
        "predecessor_manifest_sha256": stop["manifest_sha256"],
        "lineage_receipt": "docs/evidence/helius-inventory-v2-final-2026-10-10.json",
        "last_attempt_bucket": stop["last_applied_bucket"],
        "last_observed_anchor_slot": stop["final_observed_anchor_slot"],
        "last_observed_anchor_receipt_ns": stop["final_anchor_receipt_ns"],
        "last_inventoried_interval": stop["last_inventoried_interval"],
        "gap_intervals": stop["inherited_gaps"],
        "predecessor_status": stop["status"],
        "lineage_created_ns": now_ns,
    }


def _verify_v2_predecessor(root: Path, stop: dict[str, Any]) -> None:
    manifest = root / "schedule-v2.json"
    frontier = root / "frontier-v2.json"
    if (
        not manifest.is_file()
        or not frontier.is_file()
        or hashlib.sha256(manifest.read_bytes()).hexdigest() != stop.get("manifest_sha256")
        or hashlib.sha256(frontier.read_bytes()).hexdigest() != stop.get("frontier_sha256")
    ):
        raise ValueError("v2_predecessor_manifest_or_frontier_mismatch")
    actual_usage = _usage(root / "epochs")
    if actual_usage != stop.get("usage"):
        raise ValueError("v2_predecessor_usage_mismatch")
    receipt_path = REPO_ROOT / "docs/evidence/helius-inventory-v2-final-2026-10-10.json"
    receipt = _read(receipt_path)
    if (
        receipt.get("stop_sha256") != hashlib.sha256((root / "STOP").read_bytes()).hexdigest()
        or receipt.get("manifest_sha256") != stop.get("manifest_sha256")
        or receipt.get("frontier_sha256") != stop.get("frontier_sha256")
    ):
        raise ValueError("v2_predecessor_final_receipt_mismatch")
    evidence = {row["bucket"]: row for row in stop.get("epoch_evidence", [])}
    epochs = sorted(
        path
        for path in (root / "epochs").iterdir()
        if path.is_dir() and (path / "epoch-plan.json").is_file()
    )
    if len(epochs) != stop.get("epoch_count") or len(evidence) != len(epochs):
        raise ValueError("v2_predecessor_epoch_count_mismatch")
    for epoch in epochs:
        expected = evidence.get(epoch.name)
        if expected is None:
            raise ValueError("v2_predecessor_epoch_evidence_missing")
        plan_path = epoch / "epoch-plan.json"
        summary_path = epoch / "epoch-summary.json"
        plan = _read(plan_path)
        summary = _read(summary_path)
        rows = _validate_journal(epoch, plan)
        if (
            len(rows) != summary.get("request_reservations")
            or hashlib.sha256(plan_path.read_bytes()).hexdigest() != expected.get("plan_sha256")
            or hashlib.sha256(summary_path.read_bytes()).hexdigest()
            != expected.get("summary_sha256")
            or (
                expected.get("journal_sha256") is not None
                and hashlib.sha256((epoch / "events" / "journal.jsonl").read_bytes()).hexdigest()
                != expected.get("journal_sha256")
            )
        ):
            raise ValueError("v2_predecessor_epoch_hash_or_journal_mismatch")
    expected_statuses = [
        "bootstrap_complete",
        "frame_complete",
        "oversized_frame",
        "frame_complete",
        "frame_complete",
    ]
    if [_read(epoch / "epoch-summary.json").get("status") for epoch in epochs] != expected_statuses:
        raise ValueError("v2_predecessor_status_lineage_mismatch")


def _campaign_usage(root: Path) -> dict[str, int]:
    usage = _usage_v3(root / "epochs")
    if usage["reserved_raw_bytes"] or usage["reserved_stored_bytes"]:
        raise ValueError("campaign_has_unresolved_reservations")
    return usage


def _validate_recovery_receipt(root: Path, state: dict[str, Any]) -> None:
    recovery_count = state.get("recovery_count", 0)
    receipt_sha256 = state.get("recovery_receipt_sha256")
    if recovery_count == 0 and receipt_sha256 is None:
        return
    if recovery_count != 1 or not isinstance(receipt_sha256, str):
        raise ValueError("recovery_authorization_count_invalid")
    receipt_path = Path(state.get("recovery_receipt_path", ""))
    if (
        not receipt_path.is_file()
        or hashlib.sha256(receipt_path.read_bytes()).hexdigest() != receipt_sha256
    ):
        raise ValueError("recovery_receipt_missing_or_changed")
    receipt = _read(receipt_path)
    proof = receipt.get("zero_io_proof") or {}
    if (
        receipt.get("schema_version") != 1
        or receipt.get("recovery_id") != "helius-v3-preanchor-setup-recovery-20261010-1"
        or receipt.get("predecessor_controller_id") != "helius-inventory-controller-20261010-72h"
        or receipt.get("predecessor_campaign_id") != "helius-inventory-v3-20261010-1"
        or receipt.get("predecessor_campaign_stop_sha256")
        != state.get("recovery_campaign_stop_sha256")
        or receipt.get("predecessor_controller_stop_sha256")
        != state.get("recovery_controller_stop_sha256")
        or proof.get("request_started") is not False
        or any(proof.get(key) != 0 for key in ("requests", "raw_bytes", "stored_bytes", "attempts"))
        or proof.get("empty_epoch_directories") != 1
        or proof.get("empty_epoch_directory") != "epochs/20261010T030000Z"
        or proof.get("empty_epoch_directory_entries") != []
        or proof.get("empty_epoch_directory_listing_sha256") != hashlib.sha256(b"[]\n").hexdigest()
        or proof.get("materialized_epoch_artifacts") != 0
        or proof.get("request_directories") != 0
        or any(proof.get(key) != 0 for key in ("request_files", "capture_files"))
        or receipt.get("root_cause_reproduction", {}).get("status") != "confirmed_before_any_rpc"
    ):
        raise ValueError("recovery_receipt_proof_invalid")
    code_hashes = receipt.get("source_sha256") or {}
    for name in (
        "src/memecoin_ai/sources.py",
        "tools/brain_senses_campaign_controller.py",
        "tools/brain_senses_block_epoch_v3_schedule.py",
        "tools/brain_senses_block_epoch_v3_launch.py",
        "ops/launchd/ai.memecoin.brain.senses.helius-inventory-controller.plist",
    ):
        if code_hashes.get(name) != hashlib.sha256((REPO_ROOT / name).read_bytes()).hexdigest():
            raise ValueError("recovery_code_binding_changed")
    zstd_path = receipt.get("zstd", {}).get("executable")
    if (
        not isinstance(zstd_path, str)
        or not Path(zstd_path).is_absolute()
        or os.environ.get("BRAIN_ZSTD_EXECUTABLE") != zstd_path
        or not Path(zstd_path).is_file()
        or hashlib.sha256(Path(zstd_path).read_bytes()).hexdigest()
        != receipt.get("zstd", {}).get("executable_sha256")
    ):
        raise ValueError("recovery_zstd_binding_changed")


def _verify_normal_campaign(root: Path, stop: dict[str, Any]) -> dict[str, int]:
    if (
        stop.get("reason") != "finite_attempt_cap_reached"
        or stop.get("attempt_count") != ATTEMPTS_MAX
        or stop.get("accounting_error_class") is not None
        or stop.get("active_reservation_status") != "reconciled"
    ):
        raise ValueError("predecessor_campaign_stop_abnormal")
    if not (root / "schedule-v3.json").is_file():
        raise ValueError("predecessor_manifest_missing")
    lineage_path = root / "epochs" / "predecessor-lineage" / "lineage.json"
    lineage = _read(lineage_path)
    manifest = _read_manifest(root, lineage)
    manifest_path = root / "schedule-v3.json"
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != stop.get("manifest_sha256"):
        raise ValueError("predecessor_manifest_stop_hash_mismatch")
    if manifest.get("schedule_id") != stop.get("campaign_id"):
        raise ValueError("predecessor_manifest_campaign_mismatch")
    epoch_dirs = sorted(
        p
        for p in (root / "epochs").iterdir()
        if p.is_dir() and re.fullmatch(r"\d{8}T\d{6}Z", p.name)
    )
    if len(epoch_dirs) != ATTEMPTS_MAX:
        raise ValueError("predecessor_attempt_count_mismatch")
    for epoch in epoch_dirs:
        plan = _read(epoch / "epoch-plan.json")
        summary = _read(epoch / "epoch-summary.json")
        reservation = _read(epoch / "frame-reservation.json")
        rows = _validate_journal(epoch, plan)
        if (
            summary.get("status") != "frame_complete"
            or plan.get("missed_schedule_buckets")
            or len(rows) != 34
        ):
            raise ValueError("predecessor_epoch_not_complete")
        _reconcile_epoch(epoch, plan, reservation)
    usage = _campaign_usage(root)
    if (
        usage["requests"] > REQUESTS_MAX
        or usage["raw_bytes"] > CAMPAIGN_RAW_BYTES
        or usage["stored_bytes"] > CAMPAIGN_STORED_BYTES
    ):
        raise ValueError("predecessor_campaign_over_budget")
    if (
        stop.get("requests") != usage["requests"]
        or stop.get("raw_bytes") != usage["raw_bytes"]
        or stop.get("stored_bytes") != usage["stored_bytes"]
    ):
        raise ValueError("predecessor_stop_usage_mismatch")
    frontier = _read(root / "frontier-v3.json")
    if (
        frontier.get("attempt_count") != ATTEMPTS_MAX
        or frontier.get("last_terminal_status") != "frame_complete"
        or frontier.get("last_attempt_bucket") != stop.get("last_attempt_bucket")
        or frontier.get("observed_anchor_slot") != stop.get("observed_anchor_slot")
        or frontier.get("last_inventoried_interval") != stop.get("last_inventoried_interval")
        or frontier.get("gap_intervals") != stop.get("gap_intervals")
    ):
        raise ValueError("predecessor_frontier_mismatch")
    return usage


def _campaign_plist(root: Path, lineage: Path, label: str) -> dict[str, Any]:
    supervisor = root / "supervisor"
    supervisor.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("stdout.log", "stderr.log"):
        path = supervisor / name
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        path.chmod(0o600)
    zstd_executable = os.environ.get("BRAIN_ZSTD_EXECUTABLE") or shutil.which("zstd")
    if not zstd_executable or not Path(zstd_executable).is_absolute():
        raise ValueError("configured_zstd_executable_unavailable")
    return {
        "Label": label,
        "ProgramArguments": [
            str(REPO_ROOT / ".venv/bin/python"),
            str(REPO_ROOT / "tools/brain_senses_block_epoch_v3_launch.py"),
            "--root-base",
            str(root),
            "--lineage",
            str(lineage),
            "--label",
            label,
        ],
        "WorkingDirectory": str(REPO_ROOT),
        "EnvironmentVariables": {"BRAIN_ZSTD_EXECUTABLE": zstd_executable},
        "StartCalendarInterval": {"Minute": 5},
        "RunAtLoad": False,
        "KeepAlive": False,
        "StandardOutPath": str(supervisor / "stdout.log"),
        "StandardErrorPath": str(supervisor / "stderr.log"),
    }


def _loaded(label: str, runner=subprocess.run) -> bool:
    target = f"gui/{os.getuid()}/{label}"
    return (
        runner(
            ["/bin/launchctl", "print", target], capture_output=True, text=True, check=False
        ).returncode
        == 0
    )


def _bootstrap(plist_path: Path, label: str, runner=subprocess.run) -> bool:
    if _loaded(label, runner=runner):
        return True
    target = f"gui/{os.getuid()}"
    return runner(
        ["/bin/launchctl", "bootstrap", target, str(plist_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    ).returncode == 0 or _loaded(label, runner=runner)


def _bootout(label: str, runner=subprocess.run) -> bool:
    target = f"gui/{os.getuid()}/{label}"
    result = runner(
        ["/bin/launchctl", "bootout", target],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    return result.returncode == 0 or not _loaded(label, runner=runner)


def _stop(root: Path, state: dict[str, Any], reason: str) -> dict[str, Any]:
    path = root / "STOP"
    if path.exists():
        return _read(path)
    value = {
        "schema_version": 1,
        "controller_id": state["controller_id"],
        "reason": reason,
        "time_ns": time.time_ns(),
        "lease_start_ns": state["lease_start_ns"],
        "lease_expires_ns": state["lease_expires_ns"],
        "campaign_count": state["campaign_count"],
        "aggregate_requests": state["aggregate_requests"],
        "aggregate_raw_bytes": state["aggregate_raw_bytes"],
        "aggregate_stored_bytes": state["aggregate_stored_bytes"],
        "current_campaign_id": state.get("current_campaign_id"),
        "state_sha256": hashlib.sha256((canonical(state) + "\n").encode()).hexdigest(),
    }
    _create_stop_atomically(path, value)
    state["status"] = "stopped"
    state["stop_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _replace(root / "controller-state.json", state)
    return value


def _create_successor(
    root: Path,
    state: dict[str, Any],
    lineage: dict[str, Any],
    *,
    now_ns: int,
    launch_agents: Path,
    runner=subprocess.run,
) -> dict[str, Any]:
    if now_ns >= state["lease_expires_ns"]:
        raise ValueError("controller_lease_expired")
    if state["campaign_count"] >= MAX_CAMPAIGNS:
        raise ValueError("controller_campaign_cap")
    if (
        state["aggregate_requests"] + REQUESTS_MAX > LEASE_REQUESTS
        or state["aggregate_raw_bytes"] + CAMPAIGN_RAW_BYTES > LEASE_RAW_BYTES
        or state["aggregate_stored_bytes"] + CAMPAIGN_STORED_BYTES > LEASE_STORED_BYTES
    ):
        raise ValueError("controller_aggregate_budget")
    if (
        shutil.disk_usage(root).free
        < DISK_FLOOR_BYTES + FRAME_RAW_BYTES + FRAME_STORED_BYTES + PEAK_WRITE_MARGIN_BYTES
    ):
        raise ValueError("controller_disk_floor")

    number = state["campaign_count"] + 1
    day = datetime.fromtimestamp(state["lease_start_ns"] / 1e9, UTC).strftime("%Y%m%d")
    campaign_id = f"helius-inventory-v3-{day}-{number}"
    label = f"ai.memecoin.brain.senses.{campaign_id}"
    campaign_root = root.parent / f"brain-senses-live-{campaign_id}"
    lineage = dict(lineage)
    lineage.update({"campaign_id": campaign_id, "controller_id": state["controller_id"]})
    lineage["controller_lease_expires_ns"] = state["lease_expires_ns"]
    lineage_path = campaign_root / "predecessor-lineage.json"
    campaign_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if lineage_path.exists():
        if _read(lineage_path) != lineage:
            raise ValueError("successor_lineage_conflict")
    else:
        _write_json(lineage_path, lineage, exclusive=True)
    plist_path = launch_agents / f"{label}.plist"
    plist = _campaign_plist(campaign_root, lineage_path, label)
    payload = plistlib.dumps(plist, fmt=plistlib.FMT_XML, sort_keys=True)
    if plist_path.exists():
        if plist_path.read_bytes() != payload:
            raise ValueError("successor_plist_conflict")
    else:
        _atomic_bytes(plist_path, payload)
    plist_path.chmod(0o600)
    plist_sha256 = hashlib.sha256(plist_path.read_bytes()).hexdigest()
    state.update(
        {
            "campaign_count": number,
            "current_campaign_id": campaign_id,
            "current_campaign_root": str(campaign_root),
            "current_campaign_label": label,
            "current_campaign_plist": str(plist_path),
            "current_campaign_plist_sha256": plist_sha256,
            "pending_bootstrap": True,
            "updated_ns": now_ns,
        }
    )
    _replace(root / "controller-state.json", state)
    if not _bootstrap(plist_path, label, runner=runner):
        return {"status": "successor_pending_bootstrap", "campaign_id": campaign_id}
    state["pending_bootstrap"] = False
    _replace(root / "controller-state.json", state)
    return {"status": "successor_loaded", "campaign_id": campaign_id}


def _tick_locked(
    root: Path,
    predecessor_root: Path,
    *,
    now_ns: int,
    launch_agents: Path,
    runner=subprocess.run,
) -> dict[str, Any]:
    state_path = root / "controller-state.json"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (root / "STOP").exists():
        stop = _read(root / "STOP")
        _bootout(CONTROLLER_LABEL, runner=runner)
        return {"status": "controller_stopped", **stop}
    if state_path.exists():
        state = _read(state_path)
        policy_path = root / "controller-policy.json"
        if not policy_path.is_file():
            _stop(root, state, "controller_policy_missing")
            _bootout(CONTROLLER_LABEL, runner=runner)
            return {"status": "controller_stopped", "reason": "controller_policy_missing"}
        policy = _read(policy_path)
        expected_policy = _policy(state)
        if (
            hashlib.sha256((canonical(policy) + "\n").encode()).hexdigest()
            != state.get("policy_sha256")
            or policy != expected_policy
            or state.get("campaign_count", MAX_CAMPAIGNS + 1) not in range(MAX_CAMPAIGNS + 1)
            or state.get("aggregate_requests", LEASE_REQUESTS + 1) > LEASE_REQUESTS
            or state.get("aggregate_raw_bytes", LEASE_RAW_BYTES + 1) > LEASE_RAW_BYTES
            or state.get("aggregate_stored_bytes", LEASE_STORED_BYTES + 1) > LEASE_STORED_BYTES
        ):
            _stop(root, state, "controller_state_or_policy_integrity_failure")
            _bootout(CONTROLLER_LABEL, runner=runner)
            return {
                "status": "controller_stopped",
                "reason": "controller_state_or_policy_integrity_failure",
            }
        try:
            _validate_recovery_receipt(root, state)
        except Exception as exc:
            _stop(root, state, f"recovery_receipt_invalid:{type(exc).__name__}")
            _bootout(CONTROLLER_LABEL, runner=runner)
            return {"status": "controller_stopped", "reason": "recovery_receipt_invalid"}
    else:
        lease_start = now_ns
        state = {
            "schema_version": 1,
            "controller_id": "helius-inventory-controller-20261010-72h",
            "status": "active",
            "lease_start_ns": lease_start,
            "lease_expires_ns": lease_start + LEASE_NS,
            "max_campaigns": MAX_CAMPAIGNS,
            "campaign_count": 0,
            "aggregate_requests": 0,
            "aggregate_raw_bytes": 0,
            "aggregate_stored_bytes": 0,
            "current_campaign_id": None,
            "created_ns": now_ns,
            "predecessor_root": str(predecessor_root),
        }
        policy_path = root / "controller-policy.json"
        policy = _policy(state)
        if policy_path.exists():
            existing_policy = _read(policy_path)
            if existing_policy.get("predecessor_root") != str(predecessor_root):
                raise ValueError("controller_policy_predecessor_mismatch")
            for key in (
                "max_campaigns",
                "aggregate_request_cap",
                "aggregate_raw_byte_cap",
                "aggregate_stored_byte_cap",
                "disk_floor_bytes",
                "source_sha256",
            ):
                if existing_policy.get(key) != policy.get(key):
                    raise ValueError("controller_policy_binding_changed")
            state["lease_start_ns"] = existing_policy["lease_start_ns"]
            state["lease_expires_ns"] = existing_policy["lease_expires_ns"]
            policy = existing_policy
        else:
            _write_json(policy_path, policy, exclusive=True)
        state["policy_sha256"] = hashlib.sha256((canonical(policy) + "\n").encode()).hexdigest()
        _write_json(state_path, state, exclusive=True)
    if state.get("status") != "active":
        return {"status": "controller_state_invalid"}
    if now_ns >= state["lease_expires_ns"]:
        _stop(root, state, "finite_72_hour_lease_expired")
        _bootout(CONTROLLER_LABEL, runner=runner)
        return {"status": "controller_stopped", "reason": "finite_72_hour_lease_expired"}

    current_id = state.get("current_campaign_id")
    if current_id is None:
        lineage = state.get("ready_lineage")
        if lineage is None:
            lineage = _v2_lineage(predecessor_root, campaign_id="pending", now_ns=now_ns)
            state["ready_lineage"] = lineage
            _replace(state_path, state)
        try:
            return _create_successor(
                root, state, lineage, now_ns=now_ns, launch_agents=launch_agents, runner=runner
            )
        except Exception as exc:
            _stop(root, state, f"successor_admission_failed:{type(exc).__name__}")
            _bootout(CONTROLLER_LABEL, runner=runner)
            return {"status": "controller_stopped", "reason": "successor_admission_failed"}

    campaign_root = Path(state["current_campaign_root"])
    campaign_plist = Path(state["current_campaign_plist"])
    if not campaign_plist.is_file() or hashlib.sha256(
        campaign_plist.read_bytes()
    ).hexdigest() != state.get("current_campaign_plist_sha256"):
        _stop(root, state, "campaign_launchagent_binding_changed")
        _bootout(CONTROLLER_LABEL, runner=runner)
        return {"status": "controller_stopped", "reason": "campaign_launchagent_binding_changed"}
    stop_path = campaign_root / "STOP"
    if not stop_path.is_file():
        if state.get("pending_bootstrap") or not _loaded(
            state["current_campaign_label"], runner=runner
        ):
            if _bootstrap(
                Path(state["current_campaign_plist"]),
                state["current_campaign_label"],
                runner=runner,
            ):
                state["pending_bootstrap"] = False
                _replace(state_path, state)
        return {"status": "campaign_active", "campaign_id": current_id}

    stop = _read(stop_path)
    if stop.get("campaign_id") != current_id:
        _stop(root, state, "predecessor_stop_campaign_id_mismatch")
        _bootout(CONTROLLER_LABEL, runner=runner)
        return {"status": "controller_stopped", "reason": "predecessor_stop_campaign_id_mismatch"}
    if state.get("accounted_campaign_id") != current_id:
        try:
            usage = _verify_normal_campaign(campaign_root, stop)
        except Exception as exc:
            _bootout(state["current_campaign_label"], runner=runner)
            _stop(root, state, f"abnormal_predecessor_stop:{type(exc).__name__}")
            _bootout(CONTROLLER_LABEL, runner=runner)
            return {"status": "controller_stopped", "reason": "abnormal_predecessor_stop"}
        if not _bootout(state["current_campaign_label"], runner=runner):
            return {"status": "predecessor_teardown_pending", "campaign_id": current_id}
        state["aggregate_requests"] += usage["requests"]
        state["aggregate_raw_bytes"] += usage["raw_bytes"]
        state["aggregate_stored_bytes"] += usage["stored_bytes"]
        state["accounted_campaign_id"] = current_id
        state["accounted_campaign_stop_sha256"] = hashlib.sha256(stop_path.read_bytes()).hexdigest()
        state["ready_lineage"] = {
            "schema_version": 1,
            "provider": "helius-free",
            "predecessor_campaign_id": current_id,
            "predecessor_stop_sha256": state["accounted_campaign_stop_sha256"],
            "predecessor_manifest_sha256": stop.get("manifest_sha256"),
            "last_attempt_bucket": stop.get("last_attempt_bucket"),
            "last_observed_anchor_slot": stop.get("observed_anchor_slot"),
            "last_observed_anchor_receipt_ns": stop.get("observed_anchor_receipt_ns"),
            "last_inventoried_interval": stop.get("last_inventoried_interval"),
            "gap_intervals": stop.get("gap_intervals", []),
            "predecessor_status": stop.get("reason"),
            "predecessor_campaign_usage": usage,
        }
        lineage = state["ready_lineage"]
        _replace(state_path, state)
    else:
        lineage = state.get("ready_lineage")

    if (
        state["campaign_count"] >= MAX_CAMPAIGNS
        or state["aggregate_requests"] + REQUESTS_MAX > LEASE_REQUESTS
        or state["aggregate_raw_bytes"] + CAMPAIGN_RAW_BYTES > LEASE_RAW_BYTES
        or state["aggregate_stored_bytes"] + CAMPAIGN_STORED_BYTES > LEASE_STORED_BYTES
    ):
        _stop(root, state, "finite_campaign_or_aggregate_request_cap_reached")
        _bootout(CONTROLLER_LABEL, runner=runner)
        return {
            "status": "controller_stopped",
            "reason": "finite_campaign_or_aggregate_request_cap_reached",
        }
    if now_ns >= state["lease_expires_ns"]:
        _stop(root, state, "finite_72_hour_lease_expired")
        _bootout(CONTROLLER_LABEL, runner=runner)
        return {"status": "controller_stopped", "reason": "finite_72_hour_lease_expired"}
    if lineage is None:
        raise ValueError("successor_lineage_unavailable")
    try:
        return _create_successor(
            root, state, lineage, now_ns=now_ns, launch_agents=launch_agents, runner=runner
        )
    except Exception as exc:
        _stop(root, state, f"successor_admission_failed:{type(exc).__name__}")
        _bootout(CONTROLLER_LABEL, runner=runner)
        return {"status": "controller_stopped", "reason": "successor_admission_failed"}


def tick(
    root: Path,
    predecessor_root: Path,
    *,
    now_ns: int | None = None,
    launch_agents: Path | None = None,
    runner=subprocess.run,
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    launch_agents = launch_agents or Path.home() / "Library/LaunchAgents"
    installed_controller_plist = launch_agents / f"{CONTROLLER_LABEL}.plist"
    template = REPO_ROOT / "ops/launchd/ai.memecoin.brain.senses.helius-inventory-controller.plist"
    if (
        installed_controller_plist.is_file()
        and installed_controller_plist.read_bytes() != template.read_bytes()
    ):
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        state_path = root / "controller-state.json"
        if state_path.is_file() and not (root / "STOP").exists():
            state = _read(state_path)
            _stop(root, state, "controller_launchagent_binding_changed")
        _bootout(CONTROLLER_LABEL, runner=runner)
        return {"status": "controller_stopped", "reason": "controller_launchagent_binding_changed"}
    with (root / ".controller.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("campaign_controller_already_active") from exc
        return _tick_locked(
            root,
            predecessor_root,
            now_ns=now_ns or time.time_ns(),
            launch_agents=launch_agents,
            runner=runner,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--predecessor-root", type=Path, required=True)
    args = parser.parse_args(argv)
    result = tick(args.root, args.predecessor_root)
    print(canonical(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
