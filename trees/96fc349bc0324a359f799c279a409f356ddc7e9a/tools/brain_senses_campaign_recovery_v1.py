#!/usr/bin/env python3
"""Prepare the single authorized zero-I/O recovery for the 2026-10-10 Helius lease."""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
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
from tools import brain_senses_campaign_controller as controller  # noqa: E402

CAMPAIGN_ID = "helius-inventory-v3-20261010-1"
CONTROLLER_ID = "helius-inventory-controller-20261010-72h"
RECOVERY_ID = "helius-v3-preanchor-setup-recovery-20261010-1"
FAILED_CAMPAIGN_ROOT = REPO_ROOT / "work/brain-senses-live-helius-inventory-v3-20261010-1"
FAILED_CONTROLLER_ROOT = REPO_ROOT / "work/brain-senses-helius-controller-20261010"
PREDECESSOR_ROOT = REPO_ROOT / "work/brain-senses-live-helius-inventory-v2-16-hourly-20261010"
RECOVERY_ROOT = REPO_ROOT / "work/brain-senses-helius-controller-recovery-20261010"
RECEIPT_PATH = REPO_ROOT / "docs/evidence/helius-inventory-v3-preanchor-recovery-2026-10-10.json"
CONTROLLER_TEMPLATE = (
    REPO_ROOT / "ops/launchd/ai.memecoin.brain.senses.helius-inventory-controller.plist"
)
CONTROLLER_LABEL = controller.CONTROLLER_LABEL
CONTROLLER_INSTALL_PATH = Path.home() / "Library/LaunchAgents" / f"{CONTROLLER_LABEL}.plist"
BOUND_SOURCES = (
    "src/memecoin_ai/sources.py",
    "tools/brain_senses_campaign_controller.py",
    "tools/brain_senses_block_epoch_v3_schedule.py",
    "tools/brain_senses_block_epoch_v3_launch.py",
    "tools/brain_senses_campaign_recovery_v1.py",
    "ops/launchd/ai.memecoin.brain.senses.helius-inventory-controller.plist",
)
LAUNCHD_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("expected_json_object")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_bytes(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temp.unlink(missing_ok=True)


def _launchctl_loaded(label: str) -> bool:
    result = subprocess.run(
        ["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    return result.returncode == 0


def _campaign_process_active() -> bool:
    result = subprocess.run(
        ["/bin/ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise ValueError("process_inventory_unavailable")
    markers = (
        "tools/brain_senses_block_epoch_v3_launch.py",
        "tools/brain_senses_block_epoch_v3_schedule.py",
    )
    return any(
        int(parts[0]) != os.getpid() and any(marker in line for marker in markers)
        for line in result.stdout.splitlines()
        if len(parts := line.strip().split(maxsplit=1)) == 2
    )


def _zstd_proof(plist: dict[str, Any]) -> dict[str, str]:
    environment = plist.get("EnvironmentVariables") or {}
    executable = environment.get("BRAIN_ZSTD_EXECUTABLE")
    if not isinstance(executable, str) or not Path(executable).is_absolute():
        raise ValueError("launchd_zstd_path_missing_or_not_absolute")
    binary = Path(executable)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("launchd_zstd_binary_unavailable")
    minimal_env = {"PATH": LAUNCHD_PATH, "BRAIN_ZSTD_EXECUTABLE": executable}
    version = subprocess.run(
        [executable, "--version"],
        capture_output=True,
        text=True,
        env=minimal_env,
        check=False,
        timeout=10,
    )
    if version.returncode != 0:
        raise ValueError("launchd_zstd_version_check_failed")
    sample = b"Brain launchd codec preflight; no RPC and no credentials.\n" * 5
    encoded = subprocess.run(
        [executable, "-q", "-c"],
        input=sample,
        capture_output=True,
        env=minimal_env,
        check=False,
        timeout=10,
    )
    if encoded.returncode != 0 or not encoded.stdout:
        raise ValueError("launchd_zstd_compress_roundtrip_failed")
    decoded = subprocess.run(
        [executable, "-q", "-d", "-c"],
        input=encoded.stdout,
        capture_output=True,
        env=minimal_env,
        check=False,
        timeout=10,
    )
    if decoded.returncode != 0 or decoded.stdout != sample:
        raise ValueError("launchd_zstd_decompress_roundtrip_failed")
    return {
        "executable": executable,
        "executable_sha256": _sha(binary),
        "version": version.stdout.strip(),
        "roundtrip": "pass_under_launchd_minimal_environment",
    }


def _reproduce_pre_anchor_failure() -> dict[str, str]:
    script = (
        "from memecoin_ai.sources import verify_capture_storage_codec; "
        "verify_capture_storage_codec('zstd')"
    )
    environment = {"PATH": LAUNCHD_PATH, "PYTHONPATH": str(REPO_ROOT / "src")}
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
        timeout=15,
    )
    expected = "zstd executable is required for compressed capture storage"
    if result.returncode == 0 or expected not in result.stderr:
        raise ValueError("pre_anchor_codec_failure_reproduction_mismatch")
    return {
        "status": "confirmed_before_any_rpc",
        "failure_class": "SourceError",
        "sanitized_error": expected,
        "environment_path": LAUNCHD_PATH,
        "configured_zstd_variable_present": "false",
    }


def _zero_io_proof(stop: dict[str, Any]) -> dict[str, Any]:
    epochs = FAILED_CAMPAIGN_ROOT / "epochs"
    epoch_directories = (
        [path for path in epochs.iterdir() if path.is_dir() and path.name != "predecessor-lineage"]
        if epochs.is_dir()
        else []
    )
    expected_empty_epoch = epochs / "20261010T030000Z"
    unexpected_epoch_entries: list[str] = []
    if epoch_directories != [expected_empty_epoch] or not expected_empty_epoch.is_dir():
        unexpected_epoch_entries.append("expected_empty_epoch_directory_mismatch")
    elif any(expected_empty_epoch.iterdir()):
        unexpected_epoch_entries.extend(path.name for path in expected_empty_epoch.iterdir())
    materialized_epoch_files = (
        [
            path
            for path in epochs.rglob("*")
            if path.is_file() and path.parent.name != "predecessor-lineage"
        ]
        if epochs.is_dir()
        else []
    )
    request_directories = [
        path
        for path in FAILED_CAMPAIGN_ROOT.rglob("*")
        if path.is_dir() and path.name in {"requests", "request-results", "captures"}
    ]
    request_files = [
        path
        for path in FAILED_CAMPAIGN_ROOT.rglob("*")
        if path.is_file() and ("requests" in path.parts or path.parent.name == "requests")
    ]
    capture_files = [
        path
        for path in FAILED_CAMPAIGN_ROOT.rglob("*")
        if path.is_file() and path.parent.name in {"captures", "request-results"}
    ]
    if (
        stop.get("campaign_id") != CAMPAIGN_ID
        or stop.get("reason") != "scheduler_process_failed"
        or stop.get("error_class") != "SourceError"
        or stop.get("request_started") is not False
        or stop.get("requests") != 0
        or stop.get("raw_bytes") != 0
        or stop.get("stored_bytes") != 0
        or stop.get("attempt_count") != 0
        or stop.get("epoch_created") is not False
        or stop.get("frame_reservation_sha256") is not None
        or unexpected_epoch_entries
        or materialized_epoch_files
        or request_directories
        or request_files
        or capture_files
    ):
        raise ValueError("failed_campaign_zero_io_not_proven")
    failed_bucket = datetime.fromtimestamp(stop["time_ns"] / 1e9, UTC).replace(
        minute=0, second=0, microsecond=0
    )
    if failed_bucket.strftime("%Y%m%dT%H0000Z") != "20261010T030000Z":
        raise ValueError("failed_campaign_bucket_unexpected")
    return {
        "request_started": False,
        "requests": 0,
        "raw_bytes": 0,
        "stored_bytes": 0,
        "attempts": 0,
        "empty_epoch_directories": len(epoch_directories),
        "materialized_epoch_artifacts": len(materialized_epoch_files),
        "request_directories": len(request_directories),
        "request_files": len(request_files),
        "capture_files": len(capture_files),
        "failed_schedule_bucket": "20261010T030000Z",
        "in_progress_marker_sha256": _sha(FAILED_CAMPAIGN_ROOT / "invocation-in-progress.json"),
        "empty_epoch_directory": str(expected_empty_epoch.relative_to(FAILED_CAMPAIGN_ROOT)),
        "empty_epoch_directory_entries": [],
        "empty_epoch_directory_listing_sha256": hashlib.sha256(b"[]\n").hexdigest(),
    }


def _build_receipt() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    failed_stop_path = FAILED_CAMPAIGN_ROOT / "STOP"
    controller_stop_path = FAILED_CONTROLLER_ROOT / "STOP"
    old_state_path = FAILED_CONTROLLER_ROOT / "controller-state.json"
    if not all(path.is_file() for path in (failed_stop_path, controller_stop_path, old_state_path)):
        raise ValueError("failed_campaign_or_controller_stop_missing")
    if (
        _launchctl_loaded(controller.CONTROLLER_LABEL)
        or _launchctl_loaded("ai.memecoin.brain.senses.helius-inventory-v3-20261010-1")
        or _campaign_process_active()
    ):
        raise ValueError("predecessor_launchagent_still_loaded")
    failed_stop = _read(failed_stop_path)
    controller_stop = _read(controller_stop_path)
    old_state = _read(old_state_path)
    if (
        controller_stop.get("reason") != "abnormal_predecessor_stop:ValueError"
        or old_state.get("status") != "stopped"
        or old_state.get("stop_sha256") != _sha(controller_stop_path)
        or old_state.get("controller_id") != CONTROLLER_ID
        or old_state.get("current_campaign_id") != CAMPAIGN_ID
        or old_state.get("aggregate_requests") != 0
        or old_state.get("aggregate_raw_bytes") != 0
        or old_state.get("aggregate_stored_bytes") != 0
        or old_state.get("campaign_count") != 1
        or time.time_ns() >= old_state.get("lease_expires_ns", 0)
    ):
        raise ValueError("predecessor_controller_state_not_recoverable")
    proof = _zero_io_proof(failed_stop)
    root_cause_reproduction = _reproduce_pre_anchor_failure()
    template = plistlib.loads(CONTROLLER_TEMPLATE.read_bytes())
    zstd = _zstd_proof(template)
    controller_stop_hash = _sha(controller_stop_path)
    campaign_stop_hash = _sha(failed_stop_path)
    source_hashes = {name: _sha(REPO_ROOT / name) for name in BOUND_SOURCES}
    receipt = {
        "schema_version": 1,
        "recovery_id": RECOVERY_ID,
        "authorization_basis": "owner resume instruction plus Sol High recovery decision",
        "prepared_ns": time.time_ns(),
        "predecessor_controller_id": CONTROLLER_ID,
        "predecessor_controller_stop_sha256": controller_stop_hash,
        "predecessor_campaign_id": CAMPAIGN_ID,
        "predecessor_campaign_stop_sha256": campaign_stop_hash,
        "predecessor_campaign_manifest_sha256": failed_stop.get("manifest_sha256"),
        "zero_io_proof": proof,
        "confirmed_root_cause": (
            "LaunchAgent default PATH omitted the installed Homebrew zstd executable; "
            "codec preflight failed before any anchor or RPC request."
        ),
        "root_cause_reproduction": root_cause_reproduction,
        "zstd": zstd,
        "uncovered_schedule_bucket": proof["failed_schedule_bucket"],
        "source_sha256": source_hashes,
        "lease": {
            "lease_start_ns": old_state["lease_start_ns"],
            "lease_expires_ns": old_state["lease_expires_ns"],
            "campaign_count_before_recovery": old_state["campaign_count"],
            "aggregate_requests_before_recovery": old_state["aggregate_requests"],
            "aggregate_raw_bytes_before_recovery": old_state["aggregate_raw_bytes"],
            "aggregate_stored_bytes_before_recovery": old_state["aggregate_stored_bytes"],
            "authorized_setup_replacements": 1,
            "request_and_byte_ceilings_increased": False,
            "deadline_extended": False,
        },
        "network_requests_made_by_recovery": 0,
    }
    return receipt, old_state, failed_stop


def _seed_recovery_controller(
    receipt: dict[str, Any], old_state: dict[str, Any], failed_stop: dict[str, Any]
) -> None:
    receipt_sha = hashlib.sha256((canonical(receipt) + "\n").encode()).hexdigest()
    lineage = dict(old_state["ready_lineage"])
    lineage.update(
        {
            "campaign_id": "pending",
            "predecessor_campaign_id": CAMPAIGN_ID,
            "predecessor_stop_sha256": receipt["predecessor_campaign_stop_sha256"],
            "predecessor_status": "failed_pre_anchor_setup_recovered_once",
            "recovery_id": RECOVERY_ID,
            "recovery_receipt_sha256": receipt_sha,
            "uncovered_setup_gap_buckets": [receipt["uncovered_schedule_bucket"]],
        }
    )
    state = {
        "schema_version": 1,
        "controller_id": "helius-inventory-controller-20261010-72h-recovery-v1",
        "status": "active",
        "lease_start_ns": old_state["lease_start_ns"],
        "lease_expires_ns": old_state["lease_expires_ns"],
        "max_campaigns": controller.MAX_CAMPAIGNS,
        "campaign_count": old_state["campaign_count"],
        "aggregate_requests": old_state["aggregate_requests"],
        "aggregate_raw_bytes": old_state["aggregate_raw_bytes"],
        "aggregate_stored_bytes": old_state["aggregate_stored_bytes"],
        "current_campaign_id": None,
        "created_ns": receipt["prepared_ns"],
        "updated_ns": receipt["prepared_ns"],
        "predecessor_root": old_state["predecessor_root"],
        "ready_lineage": lineage,
        "recovery_count": 1,
        "recovery_receipt_path": str(RECEIPT_PATH),
        "recovery_receipt_sha256": receipt_sha,
        "recovery_campaign_stop_sha256": receipt["predecessor_campaign_stop_sha256"],
        "recovery_controller_stop_sha256": receipt["predecessor_controller_stop_sha256"],
    }
    policy = controller._policy(state)
    state["policy_sha256"] = hashlib.sha256((canonical(policy) + "\n").encode()).hexdigest()
    policy_path = RECOVERY_ROOT / "controller-policy.json"
    state_path = RECOVERY_ROOT / "controller-state.json"
    if not RECOVERY_ROOT.exists():
        RECOVERY_ROOT.mkdir(mode=0o700, parents=True)
        controller._write_json(policy_path, policy, exclusive=True)
        controller._write_json(state_path, state, exclusive=True)
    else:
        existing_state = _read(state_path) if state_path.is_file() else {}
        existing_policy = _read(policy_path) if policy_path.is_file() else {}
        expected_state = {
            key: state[key]
            for key in (
                "controller_id",
                "status",
                "lease_start_ns",
                "lease_expires_ns",
                "campaign_count",
                "aggregate_requests",
                "aggregate_raw_bytes",
                "aggregate_stored_bytes",
                "current_campaign_id",
                "predecessor_root",
                "ready_lineage",
                "recovery_count",
                "recovery_receipt_path",
                "recovery_receipt_sha256",
                "recovery_campaign_stop_sha256",
                "recovery_controller_stop_sha256",
                "policy_sha256",
            )
        }
        if any(existing_state.get(key) != value for key, value in expected_state.items()) or (
            existing_policy != policy
        ):
            raise ValueError("recovery_controller_root_already_bound_differently")


def _prepare_controller_logs() -> None:
    supervisor = RECOVERY_ROOT / "supervisor"
    supervisor.mkdir(mode=0o700, parents=True, exist_ok=True)
    supervisor.chmod(0o700)
    for name in ("stdout.log", "stderr.log"):
        path = supervisor / name
        if not path.exists():
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
        path.chmod(0o600)


def prepare() -> dict[str, Any]:
    receipt, old_state, failed_stop = _build_receipt()
    expected_receipt = canonical(receipt) + "\n"
    if RECEIPT_PATH.exists():
        existing_receipt = RECEIPT_PATH.read_text()
        if json.loads(existing_receipt).get("recovery_id") != RECOVERY_ID:
            raise ValueError("recovery_receipt_id_conflict")
        receipt = json.loads(existing_receipt)
        if any(
            receipt.get(key) != expected
            for key, expected in (
                ("predecessor_campaign_stop_sha256", _sha(FAILED_CAMPAIGN_ROOT / "STOP")),
                ("predecessor_controller_stop_sha256", _sha(FAILED_CONTROLLER_ROOT / "STOP")),
                ("source_sha256", {name: _sha(REPO_ROOT / name) for name in BOUND_SOURCES}),
                ("zero_io_proof", _zero_io_proof(_read(FAILED_CAMPAIGN_ROOT / "STOP"))),
                ("root_cause_reproduction", _reproduce_pre_anchor_failure()),
                ("zstd", _zstd_proof(plistlib.loads(CONTROLLER_TEMPLATE.read_bytes()))),
            )
        ):
            raise ValueError("recovery_receipt_binding_changed")
    else:
        RECEIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(RECEIPT_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(expected_receipt)
                stream.flush()
                os.fsync(stream.fileno())
            directory = os.open(RECEIPT_PATH.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            RECEIPT_PATH.unlink(missing_ok=True)
            raise
    _seed_recovery_controller(receipt, old_state, failed_stop)
    _prepare_controller_logs()
    template_bytes = CONTROLLER_TEMPLATE.read_bytes()
    if CONTROLLER_INSTALL_PATH.exists() and CONTROLLER_INSTALL_PATH.read_bytes() != template_bytes:
        _atomic_bytes(CONTROLLER_INSTALL_PATH, template_bytes)
    elif not CONTROLLER_INSTALL_PATH.exists():
        _atomic_bytes(CONTROLLER_INSTALL_PATH, template_bytes)
    CONTROLLER_INSTALL_PATH.chmod(0o600)
    if not _launchctl_loaded(CONTROLLER_LABEL):
        result = subprocess.run(
            ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(CONTROLLER_INSTALL_PATH)],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
            env={**os.environ, "BRAIN_ZSTD_EXECUTABLE": receipt["zstd"]["executable"]},
        )
        if result.returncode != 0 and not _launchctl_loaded(CONTROLLER_LABEL):
            raise RuntimeError("recovery_controller_bootstrap_failed")
    return {
        "status": "recovery_controller_loaded_or_already_loaded",
        "recovery_id": RECOVERY_ID,
        "recovery_receipt_sha256": _sha(RECEIPT_PATH),
        "new_campaign_requests_made": 0,
        "next_campaign_boundary_policy": "next future local :05 only; no backfill",
    }


def main() -> int:
    result = prepare()
    print(canonical(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
