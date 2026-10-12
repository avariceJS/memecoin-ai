#!/usr/bin/env python3
"""One-shot launchd timing rehearsal for read-only external-v2 status."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import plistlib
import re
import stat
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

REPO = Path(__file__).resolve().parents[1]
OLD_LABEL = "ai.memecoin.brain.senses.external-v2-20261012T0230Z"
TERMINAL_RECEIPT = (
    REPO / "docs/evidence/brain-senses-external-v2-missed-bootstrap-terminal-2026-10-12.json"
)
RETIRED_LABELS = (
    "ai.memecoin.brain.senses.block-epochs",
    "ai.memecoin.brain.senses.decode-watch",
    "ai.memecoin.brain.senses.helius-decode-watch",
    "ai.memecoin.brain.senses.helius-inventory-controller-v1",
    "ai.memecoin.brain.senses.helius-inventory-v3-20261010-1",
    "ai.memecoin.brain.senses.helius-inventory-v3-20261010-2",
    "ai.memecoin.brain.senses.helius-inventory-v3-20261010-3",
    "ai.memecoin.brain.senses.helius-inventory-v3-20261010-4",
    "ai.memecoin.brain.senses.helius-watch-v2",
    "ai.memecoin.brain.senses.helius-watch-v3",
    "ai.memecoin.brain.senses.helius-watch",
    "ai.memecoin.brain.senses.independent-verifier-512-pair64",
    "ai.memecoin.brain.senses.independent-verifier-512",
    "ai.memecoin.brain.senses.program-watch-v3",
    "ai.memecoin.brain.senses.program-watch",
    "ai.memecoin.brain.senses.external-v1-20261011T2133Z",
)
_FIELDS = {
    "schema",
    "schema_version",
    "decision",
    "label",
    "source_sha256",
    "plan_path",
    "plan_sha256",
    "old_activation_path",
    "old_activation_sha256",
    "storage_config_path",
    "storage_config_sha256",
    "terminal_receipt_sha256",
    "plist_path",
    "plist_sha256",
    "initialized_at_ns",
    "scheduled_minute_ns",
    "expires_ns",
}


class RehearsalError(ValueError):
    """The rehearsal binding or launchd execution is not qualified."""


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise RehearsalError(reason)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _open_parent(path: Path) -> int:
    _require(path.is_absolute() and ".." not in path.parts, "unsafe_path")
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read(path: Path, *, private: bool = True, limit: int = 4_000_000) -> bytes:
    _require(path.is_absolute() and ".." not in path.parts, "unsafe_path")
    parent = _open_parent(path)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        info = os.fstat(fd)
        _require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.getuid()
            and info.st_nlink == 1
            and info.st_size <= limit
            and (not private or stat.S_IMODE(info.st_mode) in {0o400, 0o600}),
            "unsafe_binding_file",
        )
        chunks = bytearray()
        while piece := os.read(fd, 65536):
            chunks.extend(piece)
            _require(len(chunks) <= limit, "binding_grew")
        after = os.fstat(fd)
        _require(
            (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
            "binding_changed",
        )
        return bytes(chunks)
    finally:
        os.close(fd)


def _command(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=15, check=False)


def _launchd_gate(binding: dict[str, Any], plist: dict[str, Any], activation_path: Path) -> None:
    domain = f"gui/{os.getuid()}"
    listing = _command(["/bin/launchctl", "list"])
    disabled = _command(["/bin/launchctl", "print-disabled", domain])
    _require(listing.returncode == disabled.returncode == 0, "launchd_inventory_unavailable")
    labels = {line.split()[-1] for line in listing.stdout.splitlines()[1:] if line.split()}
    _require(binding["label"] in labels and OLD_LABEL not in labels, "launchd_label_state")
    _require(
        re.search(
            r'"' + re.escape(binding["label"]) + r'"\s*=>\s*(?:true|disabled)\b', disabled.stdout
        )
        is None,
        "rehearsal_agent_disabled",
    )
    _require(
        re.search(r'"' + re.escape(OLD_LABEL) + r'"\s*=>\s*(?:true|disabled)\b', disabled.stdout)
        is not None,
        "old_lease_agent_not_disabled",
    )
    _require(
        not any(
            label.startswith("ai.memecoin.brain.senses.") and label != binding["label"]
            for label in labels
        ),
        "parallel_senses_agent",
    )
    for retired in RETIRED_LABELS:
        _require(retired not in labels, "retired_agent_loaded")
        _require(
            re.search(r'"' + re.escape(retired) + r'"\s*=>\s*(?:true|disabled)\b', disabled.stdout)
            is not None,
            "retired_agent_not_disabled",
        )
    info = _command(["/bin/launchctl", "print", f"{domain}/{binding['label']}"])
    _require(
        info.returncode == 0
        and re.search(r"\bpid = " + str(os.getpid()) + r"\b", info.stdout) is not None
        and re.search(r"(?m)^\s*path = " + re.escape(binding["plist_path"]) + r"\s*$", info.stdout)
        is not None
        and os.getppid() == 1,
        "not_launchd_calendar_process",
    )
    expected = [
        str(REPO / ".venv/bin/python"),
        str(Path(__file__).resolve()),
        "run",
        "--activation",
        str(activation_path),
    ]
    _require(sys.argv == expected[1:], "process_argv_mismatch")
    _require(
        plist.get("ProgramArguments") == expected
        and plist.get("WorkingDirectory") == str(REPO)
        and not plist.get("RunAtLoad")
        and not plist.get("KeepAlive")
        and not plist.get("EnvironmentVariables"),
        "plist_entrypoint",
    )


def _load_binding(activation_path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    raw = _read(activation_path)
    binding = json.loads(raw)
    _require(
        isinstance(binding, dict)
        and set(binding) == _FIELDS
        and binding.get("schema") == "brain-senses-external-timing-rehearsal-v1"
        and binding.get("schema_version") == 1
        and binding.get("decision") == "timing_rehearsal_no_provider"
        and binding.get("label", "").startswith("ai.memecoin.brain.senses.timing-rehearsal-"),
        "activation_schema_or_decision",
    )
    _require(
        _sha(_read(Path(__file__).resolve(), private=False)) == binding["source_sha256"],
        "source_hash",
    )
    plan_raw = _read(Path(binding["plan_path"]))
    old_activation_raw = _read(Path(binding["old_activation_path"]))
    config_raw = _read(Path(binding["storage_config_path"]))
    terminal_raw = _read(TERMINAL_RECEIPT, private=False)
    plist_raw = _read(Path(binding["plist_path"]))
    _require(
        _sha(plan_raw) == binding["plan_sha256"]
        and _sha(old_activation_raw) == binding["old_activation_sha256"]
        and _sha(config_raw) == binding["storage_config_sha256"]
        and _sha(terminal_raw) == binding["terminal_receipt_sha256"]
        and _sha(plist_raw) == binding["plist_sha256"],
        "activation_hash_binding",
    )
    plan = json.loads(plan_raw)
    old_activation = json.loads(old_activation_raw)
    terminal = json.loads(terminal_raw)
    plist = plistlib.loads(plist_raw)
    _require(
        old_activation.get("label") == OLD_LABEL
        and old_activation.get("plan_sha256") == binding["plan_sha256"]
        and old_activation.get("plan_path") == binding["plan_path"]
        and old_activation.get("storage_config_sha256") == binding["storage_config_sha256"]
        and terminal.get("schema") == "brain-senses-external-v2-missed-bootstrap-terminal"
        and terminal.get("plan_sha256") == binding["plan_sha256"]
        and terminal.get("label") == OLD_LABEL
        and terminal.get("provider_requests")
        == terminal.get("reservations")
        == terminal.get("intents")
        == 0
        and terminal.get("sealed_buckets") == 0
        and terminal.get("predecessor_lineage_verified") is True
        and terminal.get("service_id") == plan.get("service_id")
        and terminal.get("root_id") == plan.get("storage", {}).get("root_id")
        and terminal.get("volume_uuid", "").lower()
        == plan.get("storage", {}).get("volume_uuid", "").lower()
        and terminal.get("launchagent_disabled_after_retirement") is True
        and terminal.get("launchagent_loaded_after_retirement") is False,
        "failed_lease_sibling_binding",
    )
    scheduled = binding["scheduled_minute_ns"]
    initialized = binding["initialized_at_ns"]
    expires = binding["expires_ns"]
    _require(
        type(initialized) is int
        and type(scheduled) is int
        and scheduled % 60_000_000_000 == 0
        and scheduled - initialized >= 900_000_000_000
        and type(expires) is int
        and scheduled < expires <= scheduled + 60_000_000_000,
        "schedule_bounds",
    )
    local = datetime.fromtimestamp(scheduled / 1_000_000_000, UTC).astimezone()
    _require(
        plist.get("Label") == binding["label"]
        and plist.get("StartCalendarInterval")
        == [{"Month": local.month, "Day": local.day, "Hour": local.hour, "Minute": local.minute}],
        "calendar_schedule_mismatch",
    )
    output_path = Path(plist.get("StandardOutPath", ""))
    error_path = Path(plist.get("StandardErrorPath", ""))
    _require(
        output_path.is_absolute()
        and error_path.is_absolute()
        and output_path.parent == error_path.parent == activation_path.parent
        and output_path != error_path,
        "status_receipt_path",
    )
    for log_path in (output_path, error_path):
        log_raw = _read(log_path)
        log_fd = os.open(log_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parent_stat = os.fstat(log_fd)
        finally:
            os.close(log_fd)
        _require(
            len(log_raw) <= 4_000_000
            and parent_stat.st_uid == os.getuid()
            and stat.S_IMODE(parent_stat.st_mode) == 0o700,
            "status_receipt_permissions",
        )
    return binding, plan, plist


def _service_snapshot(plan: dict[str, Any], config_path: Path) -> dict[str, Any]:
    from memecoin_research_storage import writer_guard
    from tools import brain_senses_external_launch_v2 as old_launch
    from tools.brain_senses_external_verify_v2 import DescriptorReaderV2, canonical

    caps = plan["caps"]
    required = (
        caps["disk_floor_bytes"]
        + caps["stored_bytes_total"]
        + caps["peak_write_margin_bytes"]
        + caps["metadata_margin_bytes"]
    )
    manifest: list[dict[str, Any]] = []
    with old_launch.host_lock(), writer_guard(config_path, min_free_bytes=required) as guard:
        service_fd = old_launch.open_service(guard, plan["storage"]["relative_root"])
        try:
            with DescriptorReaderV2(service_fd) as reader:
                for relative in reader.list_files():
                    payload = reader.read_bytes(relative)
                    manifest.append(
                        {"path": relative, "size": len(payload), "sha256": _sha(payload)}
                    )
        finally:
            os.close(service_fd)
    encoded = canonical(manifest)
    events = next((item for item in manifest if item["path"] == "events-v2.jsonl"), None)
    stop = next((item for item in manifest if item["path"] == "STOP-v2"), None)
    _require(events is not None and stop is not None, "terminal_service_files_missing")
    assert events is not None and stop is not None
    return {
        "manifest_sha256": _sha(encoded),
        "file_count": len(manifest),
        "event_bytes": events["size"],
        "events_sha256": events["sha256"],
        "stop_sha256": stop["sha256"],
    }


def _validate_terminal_status(result: dict[str, Any], terminal: dict[str, Any]) -> None:
    events = result.get("events")
    final_event = events[-1] if isinstance(events, list) and events else {}
    stop_payload = final_event.get("payload", {}).get("stop", {})
    _require(
        result.get("status") == "stopped"
        and result.get("stop") is True
        and result.get("frontier") == terminal["frontier"] == 455802008
        and result.get("predecessor_lineage_verified") is True
        and result.get("requests") == 0
        and result.get("reservations") == {}
        and result.get("intents") == {}
        and result.get("v2_raw_bytes") == 0
        and result.get("v2_stored_bytes") == 0
        and final_event.get("kind") == "stop"
        and stop_payload.get("reason") == "missed_bootstrap",
        "terminal_status_semantics_mismatch",
    )


def run(activation_path: Path) -> dict[str, Any]:
    """Validate one exact launchd callback, then run only the pinned v2 status path."""
    binding, _plan, plist = _load_binding(activation_path)
    _launchd_gate(binding, plist, activation_path)
    callback_checked_ns = time.time_ns()
    start = binding["scheduled_minute_ns"]
    _require(
        start <= callback_checked_ns <= start + 15_000_000_000,
        "calendar_callback_gate_late",
    )
    _require(callback_checked_ns < binding["expires_ns"], "calendar_callback_outside_window")
    terminal = json.loads(_read(TERMINAL_RECEIPT, private=False))

    from memecoin_research_storage import StorageError

    old_launch = cast(Any, importlib.import_module("tools.brain_senses_external_launch_v2"))

    original_gate = old_launch.runtime_gate
    original_client = old_launch._client
    original_bootstrap = old_launch.engine.run_bootstrap
    original_frame = old_launch.engine.run_frame

    def status_only_gate(label: str, _old_plist: Any, *, running: bool) -> None:
        _require(label == OLD_LABEL and not running, "status_only_class_violation")

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise StorageError("timing_rehearsal_dispatch_forbidden")

    old_launch.runtime_gate = status_only_gate
    old_launch._client = forbidden
    old_launch.engine.run_bootstrap = forbidden
    old_launch.engine.run_frame = forbidden
    try:
        before = _service_snapshot(_plan, Path(binding["storage_config_path"]))
        _require(
            before["events_sha256"] == terminal["events_sha256"]
            and before["stop_sha256"] == terminal["stop_sha256"],
            "terminal_service_state_changed_before_rehearsal",
        )
        preflight_finished_ns = time.time_ns()
        _require(preflight_finished_ns <= start + 15_000_000_000, "calendar_preflight_late")
        status_started_ns = time.time_ns()
        _require(status_started_ns <= start + 15_000_000_000, "status_start_late")
        result = old_launch.execute(
            "status",
            activation_path=Path(binding["old_activation_path"]),
            config_path=Path(binding["storage_config_path"]),
        )
        status_finished_ns = time.time_ns()
        after = _service_snapshot(_plan, Path(binding["storage_config_path"]))
    finally:
        old_launch.runtime_gate = original_gate
        old_launch._client = original_client
        old_launch.engine.run_bootstrap = original_bootstrap
        old_launch.engine.run_frame = original_frame
    _require(status_finished_ns <= start + 30_000_000_000, "status_finish_late")
    _require(status_finished_ns < binding["expires_ns"], "status_path_overran_window")
    _require(before == after, "status_path_mutated_service_tree")
    _validate_terminal_status(result, terminal)
    return {
        "schema": "brain-senses-external-timing-rehearsal-result-v1",
        "status": "passed",
        "decision": binding["decision"],
        "label": binding["label"],
        "scheduled_minute_ns": start,
        "callback_checked_ns": callback_checked_ns,
        "preflight_finished_ns": preflight_finished_ns,
        "status_started_ns": status_started_ns,
        "status_finished_ns": status_finished_ns,
        "provider_io": False,
        "dispatch_calls": 0,
        "old_status": result.get("status"),
        "service_tree_unchanged": True,
        "service_manifest_sha256": before["manifest_sha256"],
        "service_file_count": before["file_count"],
        "event_bytes": before["event_bytes"],
        "events_sha256": before["events_sha256"],
        "stop_sha256": before["stop_sha256"],
        "provider_requests": terminal["provider_requests"],
        "reservations": terminal["reservations"],
        "intents": terminal["intents"],
    }


def main() -> int:
    if len(sys.argv) != 4 or sys.argv[1] != "run" or sys.argv[2] != "--activation":
        return 2
    try:
        result = run(Path(sys.argv[3]))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "schema": "brain-senses-external-timing-rehearsal-result-v1",
                    "status": "failed",
                    "error_class": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
