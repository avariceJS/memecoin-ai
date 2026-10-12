from __future__ import annotations

import hashlib
import json
import plistlib
from datetime import UTC, datetime
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import pytest

from tools import brain_senses_external_timing_rehearsal_v1 as rehearsal


def _write(path: Path, payload: bytes) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o600)
    return payload


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, Any]]:
    old_dir = Path.home() / ".config/brain-research/external-v2-20261012T0230Z"
    plan_path = old_dir / "lease-plan.json"
    old_activation_path = old_dir / "activation.json"
    config_path = Path.home() / ".config/brain-research/storage-v1.json"
    plan_raw = plan_path.read_bytes()
    old_activation_raw = old_activation_path.read_bytes()
    config_raw = config_path.read_bytes()
    terminal_raw = rehearsal.TERMINAL_RECEIPT.read_bytes()
    monkeypatch.setattr(rehearsal, "TERMINAL_RECEIPT", tmp_path / "terminal.json")
    _write(tmp_path / "terminal.json", terminal_raw)

    minute = 1_900_000_020_000_000_000
    minute -= minute % 60_000_000_000
    label = "ai.memecoin.brain.senses.timing-rehearsal-test"
    activation_path = tmp_path / "activation.json"
    plist_path = tmp_path / "launchagent.plist"
    output_path = tmp_path / "result.json"
    error_path = tmp_path / "error.log"
    _write(output_path, b"")
    _write(error_path, b"")
    local = datetime.fromtimestamp(minute / 1_000_000_000, UTC).astimezone()
    argv = [
        str(rehearsal.REPO / ".venv/bin/python"),
        str(Path(rehearsal.__file__).resolve()),
        "run",
        "--activation",
        str(activation_path),
    ]
    plist = {
        "Label": label,
        "ProgramArguments": argv,
        "WorkingDirectory": str(rehearsal.REPO),
        "StartCalendarInterval": [
            {"Month": local.month, "Day": local.day, "Hour": local.hour, "Minute": local.minute}
        ],
        "StandardOutPath": str(output_path),
        "StandardErrorPath": str(error_path),
    }
    plist_raw = plistlib.dumps(plist)
    _write(plist_path, plist_raw)
    binding = {
        "schema": "brain-senses-external-timing-rehearsal-v1",
        "schema_version": 1,
        "decision": "timing_rehearsal_no_provider",
        "label": label,
        "source_sha256": hashlib.sha256(Path(rehearsal.__file__).read_bytes()).hexdigest(),
        "plan_path": str(plan_path),
        "plan_sha256": hashlib.sha256(plan_raw).hexdigest(),
        "old_activation_path": str(old_activation_path),
        "old_activation_sha256": hashlib.sha256(old_activation_raw).hexdigest(),
        "storage_config_path": str(config_path),
        "storage_config_sha256": hashlib.sha256(config_raw).hexdigest(),
        "terminal_receipt_sha256": hashlib.sha256(terminal_raw).hexdigest(),
        "plist_path": str(plist_path),
        "plist_sha256": hashlib.sha256(plist_raw).hexdigest(),
        "initialized_at_ns": minute - 900_000_000_000,
        "scheduled_minute_ns": minute,
        "expires_ns": minute + 60_000_000_000,
    }
    raw = _canonical(binding)
    _write(activation_path, raw)
    return activation_path, binding


def test_binding_hashes_failed_lease_sibling_and_one_calendar_minute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    activation_path, binding = _fixture(tmp_path, monkeypatch)
    loaded, plan, plist = rehearsal._load_binding(activation_path)

    assert loaded == binding
    assert plan["service_id"] == "external-v2-20261012T0230Z"
    assert plist["Label"] == binding["label"]


@pytest.mark.parametrize(
    "field,value", [("decision", "approved_for_finite_activation"), ("scheduled_minute_ns", 1)]
)
def test_binding_rejects_dispatch_class_or_schedule_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    activation_path, binding = _fixture(tmp_path, monkeypatch)
    binding[field] = value
    _write(activation_path, _canonical(binding))

    with pytest.raises(rehearsal.RehearsalError):
        rehearsal._load_binding(activation_path)


def test_launchd_gate_accepts_only_its_own_exact_calendar_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    activation_path, binding = _fixture(tmp_path, monkeypatch)
    _, _, plist = rehearsal._load_binding(activation_path)
    monkeypatch.setattr(rehearsal.os, "getpid", lambda: 4242)
    monkeypatch.setattr(rehearsal.os, "getppid", lambda: 1)
    monkeypatch.setattr(
        rehearsal.sys,
        "argv",
        [
            str(Path(rehearsal.__file__).resolve()),
            "run",
            "--activation",
            str(activation_path),
        ],
    )
    disabled_rows = [f'  "{label}" => true' for label in rehearsal.RETIRED_LABELS]
    disabled_rows.append(f'  "{rehearsal.OLD_LABEL}" => true')

    def command(argv: list[str]) -> CompletedProcess[str]:
        if argv[1] == "list":
            stdout = "PID\tStatus\tLabel\n-\t0\t" + binding["label"] + "\n"
        elif argv[1] == "print-disabled":
            stdout = "\n".join(disabled_rows)
        else:
            stdout = f"pid = 4242\npath = {binding['plist_path']}\n"
        return CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(rehearsal, "_command", command)

    rehearsal._launchd_gate(binding, plist, activation_path)


@pytest.mark.parametrize("bad_process", ["manual", "wrong_parent"])
def test_launchd_gate_rejects_manual_or_non_launchd_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_process: str
) -> None:
    activation_path, binding = _fixture(tmp_path, monkeypatch)
    _, _, plist = rehearsal._load_binding(activation_path)
    monkeypatch.setattr(rehearsal.os, "getpid", lambda: 4242)
    monkeypatch.setattr(rehearsal.os, "getppid", lambda: 2 if bad_process == "wrong_parent" else 1)
    if bad_process == "manual":
        monkeypatch.setattr(
            rehearsal.sys, "argv", ["python", "run", "--activation", str(activation_path)]
        )
    disabled_rows = [f'  "{label}" => true' for label in rehearsal.RETIRED_LABELS]
    disabled_rows.append(f'  "{rehearsal.OLD_LABEL}" => true')

    def command(argv: list[str]) -> CompletedProcess[str]:
        if argv[1] == "list":
            stdout = "PID\tStatus\tLabel\n-\t0\t" + binding["label"] + "\n"
        elif argv[1] == "print-disabled":
            stdout = "\n".join(disabled_rows)
        else:
            stdout = f"pid = 4242\npath = {binding['plist_path']}\n"
        return CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(rehearsal, "_command", command)

    with pytest.raises(rehearsal.RehearsalError):
        rehearsal._launchd_gate(binding, plist, activation_path)


def test_terminal_status_must_match_stop_frontier_lineage_and_zero_io() -> None:
    terminal = json.loads(rehearsal.TERMINAL_RECEIPT.read_bytes())
    valid = {
        "status": "stopped",
        "stop": True,
        "frontier": terminal["frontier"],
        "predecessor_lineage_verified": True,
        "requests": 0,
        "reservations": {},
        "intents": {},
        "v2_raw_bytes": 0,
        "v2_stored_bytes": 0,
        "events": [{"kind": "stop", "payload": {"stop": {"reason": "missed_bootstrap"}}}],
    }
    rehearsal._validate_terminal_status(valid, terminal)

    for field, wrong in (
        ("status", "verified_prefix"),
        ("frontier", terminal["frontier"] + 1),
        ("requests", 1),
    ):
        invalid = {**valid, field: wrong}
        with pytest.raises(rehearsal.RehearsalError, match="terminal_status_semantics_mismatch"):
            rehearsal._validate_terminal_status(invalid, terminal)


def test_late_calendar_callback_cannot_enter_status_path(monkeypatch: pytest.MonkeyPatch) -> None:
    binding = {
        "decision": "timing_rehearsal_no_provider",
        "label": "ai.memecoin.brain.senses.timing-rehearsal-test",
        "scheduled_minute_ns": 1_900_000_020_000_000_000,
        "expires_ns": 1_900_000_080_000_000_000,
    }
    monkeypatch.setattr(rehearsal, "_load_binding", lambda _path: (binding, {}, {}))
    monkeypatch.setattr(rehearsal, "_launchd_gate", lambda *_args: None)
    monkeypatch.setattr(
        rehearsal.time,
        "time_ns",
        lambda: binding["scheduled_minute_ns"] + 15_000_000_001,
    )

    with pytest.raises(rehearsal.RehearsalError, match="calendar_callback_gate_late"):
        rehearsal.run(Path("/private/rehearsal.json"))


def test_status_execution_is_pinned_to_status_command_and_restores_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import brain_senses_external_launch_v2 as old_launch

    binding = {
        "decision": "timing_rehearsal_no_provider",
        "label": "ai.memecoin.brain.senses.timing-rehearsal-test",
        "scheduled_minute_ns": 1_900_000_020_000_000_000,
        "expires_ns": 1_900_000_140_000_000_000,
        "old_activation_path": Path("/private/old-activation.json"),
        "storage_config_path": Path("/private/storage.json"),
    }
    terminal = json.loads(rehearsal.TERMINAL_RECEIPT.read_bytes())
    monkeypatch.setattr(rehearsal, "_load_binding", lambda _path: (binding, {}, {}))
    monkeypatch.setattr(rehearsal, "_launchd_gate", lambda *_args: None)
    monkeypatch.setattr(
        rehearsal,
        "_service_snapshot",
        lambda *_args: {
            "manifest_sha256": "a",
            "file_count": 3,
            "event_bytes": 0,
            "events_sha256": terminal["events_sha256"],
            "stop_sha256": terminal["stop_sha256"],
        },
    )
    ticks = iter(
        [
            binding["scheduled_minute_ns"] + 1,
            binding["scheduled_minute_ns"] + 2,
            binding["scheduled_minute_ns"] + 3,
            binding["scheduled_minute_ns"] + 4,
        ]
    )
    monkeypatch.setattr(rehearsal.time, "time_ns", lambda: next(ticks))
    calls: list[str] = []

    def status_only(command: str, **_kwargs: Any) -> dict[str, str]:
        assert command == "status"
        calls.append(command)
        old_launch.runtime_gate(rehearsal.OLD_LABEL, {}, running=False)
        for function in (
            old_launch._client,
            old_launch.engine.run_bootstrap,
            old_launch.engine.run_frame,
        ):
            with pytest.raises(Exception, match="timing_rehearsal_dispatch_forbidden"):
                function(None)
        return {
            "status": "stopped",
            "stop": True,
            "frontier": 455802008,
            "predecessor_lineage_verified": True,
            "requests": 0,
            "reservations": {},
            "intents": {},
            "v2_raw_bytes": 0,
            "v2_stored_bytes": 0,
            "events": [{"kind": "stop", "payload": {"stop": {"reason": "missed_bootstrap"}}}],
        }

    monkeypatch.setattr(old_launch, "execute", status_only)
    original = (
        old_launch.runtime_gate,
        old_launch._client,
        old_launch.engine.run_bootstrap,
        old_launch.engine.run_frame,
    )
    result = rehearsal.run(Path("/private/rehearsal.json"))

    assert calls == ["status"]
    assert result["status"] == "passed"
    assert result["provider_io"] is False
    assert result["dispatch_calls"] == 0
    assert result["service_tree_unchanged"] is True
    assert result["callback_checked_ns"] <= binding["scheduled_minute_ns"] + 15_000_000_000
    assert result["status_finished_ns"] <= binding["scheduled_minute_ns"] + 30_000_000_000
    assert (
        old_launch.runtime_gate,
        old_launch._client,
        old_launch.engine.run_bootstrap,
        old_launch.engine.run_frame,
    ) == original
