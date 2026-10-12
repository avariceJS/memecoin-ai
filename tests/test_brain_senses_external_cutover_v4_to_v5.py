from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tools import brain_senses_external_cutover_v4_to_v5 as cutover
from tools import brain_senses_external_orchestrator_v4 as runtime
from tools import brain_senses_external_schema_v4 as schema
from tools.brain_senses_external_writer_v1 import DescriptorWriter

ROOT_ID = "a" * 32
VOLUME_UUID = "8f8f1081-9be4-4914-9111-75f1b90255bf"


class FakeAgent:
    def __init__(self, writer: DescriptorWriter) -> None:
        self.writer = writer
        self.calls = 0

    def disable_bootout_and_verify(self) -> None:
        assert self.writer.exists(cutover.STOP_FILE)
        self.calls += 1


@contextlib.contextmanager
def seeded_epoch(
    *, tear_control: bool = False
) -> Iterator[tuple[DescriptorWriter, str, str, bytes]]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        writer = DescriptorWriter(fd)
        os.close(fd)
        writer.mkdir("services")
        writer.mkdir(schema.SERVICE_RELATIVE_ROOT)
        writer.mkdir(f"{schema.SERVICE_RELATIVE_ROOT}/epochs")
        now = time.time_ns()
        epoch_id = runtime._open_epoch(
            writer,
            root_id=ROOT_ID,
            volume_uuid=VOLUME_UUID,
            now_ns=now,
        )
        plan_path = f"{schema.SERVICE_RELATIVE_ROOT}/epochs/{epoch_id}/{schema.PLAN_FILE}"
        plan_bytes = writer.read_bytes(plan_path)
        if tear_control:
            control_path = f"{schema.SERVICE_RELATIVE_ROOT}/{schema.CONTROL_FILE}"
            writer.append(control_path, b'{"partial":')
        try:
            yield writer, epoch_id, schema.sha(plan_bytes), plan_bytes
        finally:
            writer.close()


def test_cutover_publishes_stop_before_disable_then_terminalizes_without_dispatch() -> None:
    with seeded_epoch() as (writer, epoch_id, plan_hash, _plan):
        agent = FakeAgent(writer)
        result = cutover.cutover_pending_epoch(
            writer,
            epoch_id=epoch_id,
            expected_plan_sha256=plan_hash,
            root_id=ROOT_ID,
            volume_uuid=VOLUME_UUID,
            agent=agent,
        )

        assert agent.calls == 1
        assert result["status"] == "abandoned_before_dispatch"
        assert result["request_intents"] == result["captures"] == result["outcomes"] == 0
        assert writer.exists(cutover.STOP_FILE)
        events = schema.verify_event_chain(
            writer.read_bytes(
                f"{schema.SERVICE_RELATIVE_ROOT}/epochs/{epoch_id}/{schema.EVENT_FILE}"
            ),
            schema=schema.EVENT_SCHEMA,
        )
        assert [event["kind"] for event in events] == ["epoch_started", "epoch_terminal"]
        control = schema.verify_event_chain(
            writer.read_bytes(f"{schema.SERVICE_RELATIVE_ROOT}/{schema.CONTROL_FILE}"),
            schema=schema.CONTROL_SCHEMA,
        )
        assert [
            event["kind"] for event in control if event["fields"].get("epoch_id") == epoch_id
        ] == [
            "epoch_open",
            "epoch_abandon",
        ]


@contextlib.contextmanager
def seeded_invalid_open_after_two_abandons() -> Iterator[tuple[DescriptorWriter, str, str]]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        writer = DescriptorWriter(fd)
        os.close(fd)
        writer.mkdir("services")
        writer.mkdir(schema.SERVICE_RELATIVE_ROOT)
        writer.mkdir(f"{schema.SERVICE_RELATIVE_ROOT}/epochs")
        now = time.time_ns()
        for offset in (0, 2 * 60 * 60 * 1_000_000_000):
            epoch_id = runtime._open_epoch(
                writer,
                root_id=ROOT_ID,
                volume_uuid=VOLUME_UUID,
                now_ns=now + offset,
            )
            plan_path = f"{schema.SERVICE_RELATIVE_ROOT}/epochs/{epoch_id}/{schema.PLAN_FILE}"
            plan_bytes = writer.read_bytes(plan_path)
            terminal_at = now + offset + 1_000_000_000
            runtime._finish_epoch(
                writer,
                epoch_id=epoch_id,
                plan_bytes=plan_bytes,
                status="abandoned",
                reason="provider_cooldown",
                pause_reason=None,
                now_ns=terminal_at,
            )
        target_id = runtime._open_epoch(
            writer,
            root_id=ROOT_ID,
            volume_uuid=VOLUME_UUID,
            now_ns=now + 4 * 60 * 60 * 1_000_000_000,
        )
        target_plan = writer.read_bytes(
            f"{schema.SERVICE_RELATIVE_ROOT}/epochs/{target_id}/{schema.PLAN_FILE}"
        )
        try:
            yield writer, target_id, schema.sha(target_plan)
        finally:
            writer.close()


def test_cutover_forensic_exception_closes_only_exact_invalid_open_prefix() -> None:
    with seeded_invalid_open_after_two_abandons() as (writer, epoch_id, plan_hash):
        result = cutover.cutover_pending_epoch(
            writer,
            epoch_id=epoch_id,
            expected_plan_sha256=plan_hash,
            root_id=ROOT_ID,
            volume_uuid=VOLUME_UUID,
            agent=FakeAgent(writer),
        )
        assert result["status"] == "abandoned_before_dispatch_v4_policy_violation"
        assert result["v4_policy_violation"] is True
        exception_raw = writer.read_bytes(cutover.EXCEPTION_PATH)
        exception = schema.decode(exception_raw)
        assert exception["classification"] == "invalid_open_after_two_abandons"
        assert exception["cutover_only"] is True
        assert exception["activation_authority"] == "none"
        assert exception["target_request_intents"] == 0
        assert exception["target_captures"] == 0
        assert exception["target_outcomes"] == 0
        state, no_second_exception = cutover._verify_all_epochs_terminal(
            writer,
            root_id=ROOT_ID,
            volume_uuid=VOLUME_UUID,
            expected_epoch_id=epoch_id,
            allow_expected_active=True,
        )
        assert no_second_exception is not None
        assert state.active_epoch_id is None
        assert writer.read_bytes(cutover.EXCEPTION_PATH) == exception_raw


def test_cutover_forensic_exception_rejects_target_dispatch_artifact() -> None:
    with seeded_invalid_open_after_two_abandons() as (writer, epoch_id, plan_hash):
        plan = schema.decode(
            writer.read_bytes(
                f"{schema.SERVICE_RELATIVE_ROOT}/epochs/{epoch_id}/{schema.PLAN_FILE}"
            )
        )
        window = plan["windows"][0]
        event_at = window["start_ns"] + 1
        params = [{"commitment": "finalized"}]
        runtime._window_event(
            writer,
            epoch_id,
            kind="window_reserved",
            at_ns=event_at,
            fields={
                "index": window["index"],
                "bucket": window["bucket"],
                "kind": window["kind"],
                "mode": "anchor" if window["kind"] == "anchor" else "inventory",
                "request_cap": window["request_cap"],
            },
        )
        runtime._window_event(
            writer,
            epoch_id,
            kind="request_intent",
            at_ns=event_at + 1,
            fields={
                "request_id": f"{window['bucket']}-00",
                "bucket": window["bucket"],
                "ordinal": 0,
                "method": "getSlot",
                "params": params,
                "wire_request_sha256": runtime.wire_request_sha256("getSlot", params),
                "decision_ns": event_at + 1,
            },
        )
        with pytest.raises(cutover.CutoverError, match="epoch_not_unstarted"):
            cutover.cutover_pending_epoch(
                writer,
                epoch_id=epoch_id,
                expected_plan_sha256=plan_hash,
                root_id=ROOT_ID,
                volume_uuid=VOLUME_UUID,
                agent=FakeAgent(writer),
            )


def test_cutover_is_idempotent_after_terminal_and_control_publication() -> None:
    with seeded_epoch() as (writer, epoch_id, plan_hash, _plan):
        agent = FakeAgent(writer)
        first = cutover.cutover_pending_epoch(
            writer,
            epoch_id=epoch_id,
            expected_plan_sha256=plan_hash,
            root_id=ROOT_ID,
            volume_uuid=VOLUME_UUID,
            agent=agent,
        )
        before_events = writer.read_bytes(
            f"{schema.SERVICE_RELATIVE_ROOT}/epochs/{epoch_id}/{schema.EVENT_FILE}"
        )
        before_control = writer.read_bytes(f"{schema.SERVICE_RELATIVE_ROOT}/{schema.CONTROL_FILE}")
        second = cutover.cutover_pending_epoch(
            writer,
            epoch_id=epoch_id,
            expected_plan_sha256=plan_hash,
            root_id=ROOT_ID,
            volume_uuid=VOLUME_UUID,
            agent=agent,
        )
        assert first == second
        assert (
            writer.read_bytes(
                f"{schema.SERVICE_RELATIVE_ROOT}/epochs/{epoch_id}/{schema.EVENT_FILE}"
            )
            == before_events
        )
        assert (
            writer.read_bytes(f"{schema.SERVICE_RELATIVE_ROOT}/{schema.CONTROL_FILE}")
            == before_control
        )
        assert agent.calls == 2


def test_cutover_completes_crash_after_terminal_event_before_terminal_file() -> None:
    with seeded_epoch() as (writer, epoch_id, plan_hash, plan_bytes):
        runtime._window_event(
            writer,
            epoch_id,
            kind="epoch_terminal",
            at_ns=time.time_ns(),
            fields={
                "epoch_id": epoch_id,
                "status": "abandoned",
                "plan_sha256": plan_hash,
            },
        )
        agent = FakeAgent(writer)
        result = cutover.cutover_pending_epoch(
            writer,
            epoch_id=epoch_id,
            expected_plan_sha256=plan_hash,
            root_id=ROOT_ID,
            volume_uuid=VOLUME_UUID,
            agent=agent,
        )
        terminal = schema.decode(
            writer.read_bytes(
                f"{schema.SERVICE_RELATIVE_ROOT}/epochs/{epoch_id}/{schema.TERMINAL_FILE}"
            )
        )
        assert terminal["status"] == "abandoned"
        assert terminal["plan_sha256"] == schema.sha(plan_bytes)
        assert result["request_intents"] == 0


def test_cutover_completes_crash_after_terminal_file_before_control_event() -> None:
    with seeded_epoch() as (writer, epoch_id, plan_hash, plan_bytes):
        runtime._window_event(
            writer,
            epoch_id,
            kind="epoch_terminal",
            at_ns=time.time_ns(),
            fields={"epoch_id": epoch_id, "status": "abandoned", "plan_sha256": plan_hash},
        )
        runtime._finish_epoch(
            writer,
            epoch_id=epoch_id,
            plan_bytes=plan_bytes,
            status="abandoned",
            reason="operator_cutover_before_dispatch",
            pause_reason=None,
            now_ns=time.time_ns(),
        )
        control_path = f"{schema.SERVICE_RELATIVE_ROOT}/{schema.CONTROL_FILE}"
        control = schema.verify_event_chain(
            writer.read_bytes(control_path), schema=schema.CONTROL_SCHEMA
        )
        writer.atomic_write(control_path, schema.canonical(control[0]))
        result = cutover.cutover_pending_epoch(
            writer,
            epoch_id=epoch_id,
            expected_plan_sha256=plan_hash,
            root_id=ROOT_ID,
            volume_uuid=VOLUME_UUID,
            agent=FakeAgent(writer),
        )
        assert result["status"] == "abandoned_before_dispatch"
        control_after = schema.verify_event_chain(
            writer.read_bytes(control_path), schema=schema.CONTROL_SCHEMA
        )
        assert [row["kind"] for row in control_after] == ["epoch_open", "epoch_abandon"]


def test_cutover_repairs_torn_control_suffix_but_rejects_torn_epoch_journal() -> None:
    with seeded_epoch(tear_control=True) as (writer, epoch_id, plan_hash, _plan):
        result = cutover.cutover_pending_epoch(
            writer,
            epoch_id=epoch_id,
            expected_plan_sha256=plan_hash,
            root_id=ROOT_ID,
            volume_uuid=VOLUME_UUID,
            agent=FakeAgent(writer),
        )
        assert result["status"] == "abandoned_before_dispatch"

    with seeded_epoch() as (writer, epoch_id, plan_hash, _plan):
        event_path = f"{schema.SERVICE_RELATIVE_ROOT}/epochs/{epoch_id}/{schema.EVENT_FILE}"
        writer.append(event_path, b'{"partial":')
        with pytest.raises(cutover.CutoverError, match="epoch_journal_torn_possibly_spent"):
            cutover.cutover_pending_epoch(
                writer,
                epoch_id=epoch_id,
                expected_plan_sha256=plan_hash,
                root_id=ROOT_ID,
                volume_uuid=VOLUME_UUID,
                agent=FakeAgent(writer),
            )
        assert writer.exists(cutover.STOP_FILE)


@pytest.mark.parametrize("tamper", ["intent", "capture", "wrong_hash", "wrong_volume"])
def test_cutover_fails_closed_on_spent_or_mismatched_pending_epoch(tamper: str) -> None:
    with seeded_epoch() as (writer, epoch_id, plan_hash, _plan):
        if tamper == "intent":
            runtime._window_event(
                writer,
                epoch_id,
                kind="request_intent",
                at_ns=time.time_ns(),
                fields={"request_id": "00"},
            )
            expected_error = "epoch_not_unstarted"
        elif tamper == "capture":
            writer.create(
                f"{schema.SERVICE_RELATIVE_ROOT}/epochs/{epoch_id}/captures/orphan.json", b"{}\n"
            )
            expected_error = "epoch_capture_artifact_present"
        elif tamper == "wrong_hash":
            plan_hash = "0" * 64
            expected_error = "plan_hash_mismatch"
        else:
            expected_error = "plan_binding_invalid"
        agent = FakeAgent(writer)
        with pytest.raises(cutover.CutoverError, match=expected_error):
            cutover.cutover_pending_epoch(
                writer,
                epoch_id=epoch_id,
                expected_plan_sha256=plan_hash if tamper != "wrong_volume" else schema.sha(_plan),
                root_id=ROOT_ID,
                volume_uuid="00000000-0000-0000-0000-000000000000"
                if tamper == "wrong_volume"
                else VOLUME_UUID,
                agent=agent,
            )
        if tamper in {"intent", "capture"}:
            assert writer.exists(cutover.STOP_FILE)
            assert agent.calls == 1
        else:
            assert not writer.exists(cutover.STOP_FILE)
            assert agent.calls == 0


def test_stop_marker_short_circuits_v4_poll_before_provider_client_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with seeded_epoch() as (writer, epoch_id, plan_hash, _plan):
        cutover.cutover_pending_epoch(
            writer,
            epoch_id=epoch_id,
            expected_plan_sha256=plan_hash,
            root_id=ROOT_ID,
            volume_uuid=VOLUME_UUID,
            agent=FakeAgent(writer),
        )

        class Guard:
            root_id = ROOT_ID
            volume_uuid = VOLUME_UUID

        @contextlib.contextmanager
        def fake_open(_path: Path) -> Iterator[tuple[Any, DescriptorWriter]]:
            yield Guard(), writer

        class NeverFetch:
            fetch_count = 0

            def fetch(self, *_args: Any, **_kwargs: Any) -> Any:
                self.fetch_count += 1
                raise AssertionError("provider dispatch after STOP")

        client = NeverFetch()
        monkeypatch.setattr(runtime, "_open_storage_writer", fake_open)
        result = runtime._run_poll_once(client=client, clock_record={})
        assert result == {"status": "stopped", "reason": "operator_stop_marker"}
        assert client.fetch_count == 0


def test_poll_racing_between_stop_and_bootout_exits_before_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with seeded_epoch() as (writer, epoch_id, plan_hash, _plan):

        class Guard:
            root_id = ROOT_ID
            volume_uuid = VOLUME_UUID

        @contextlib.contextmanager
        def fake_open(_path: Path) -> Iterator[tuple[Any, DescriptorWriter]]:
            yield Guard(), writer

        monkeypatch.setattr(runtime, "_open_storage_writer", fake_open)

        class NeverFetch:
            fetch_count = 0

            def fetch(self, *_args: Any, **_kwargs: Any) -> Any:
                self.fetch_count += 1
                raise AssertionError("racing poll dispatched after STOP")

        client = NeverFetch()

        class PollingAgent:
            def disable_bootout_and_verify(self) -> None:
                result = runtime._run_poll_once(client=client, clock_record={})
                assert result == {"status": "stopped", "reason": "operator_stop_marker"}

        result = cutover.cutover_pending_epoch(
            writer,
            epoch_id=epoch_id,
            expected_plan_sha256=plan_hash,
            root_id=ROOT_ID,
            volume_uuid=VOLUME_UUID,
            agent=PollingAgent(),
        )
        assert result["status"] == "abandoned_before_dispatch"
        assert client.fetch_count == 0


def test_failed_launchagent_disable_leaves_stop_but_does_not_claim_cutover() -> None:
    with seeded_epoch() as (writer, epoch_id, plan_hash, _plan):

        class AgentFailure:
            def disable_bootout_and_verify(self) -> None:
                assert writer.exists(cutover.STOP_FILE)
                raise cutover.CutoverError("v4_agent_still_loaded")

        with pytest.raises(cutover.CutoverError, match="v4_agent_still_loaded"):
            cutover.cutover_pending_epoch(
                writer,
                epoch_id=epoch_id,
                expected_plan_sha256=plan_hash,
                root_id=ROOT_ID,
                volume_uuid=VOLUME_UUID,
                agent=AgentFailure(),
            )
        assert writer.exists(cutover.STOP_FILE)
        events = schema.verify_event_chain(
            writer.read_bytes(
                f"{schema.SERVICE_RELATIVE_ROOT}/epochs/{epoch_id}/{schema.EVENT_FILE}"
            ),
            schema=schema.EVENT_SCHEMA,
        )
        assert [event["kind"] for event in events] == ["epoch_started"]


def test_detach_at_stop_create_does_not_write_to_replaced_mountpoint(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    writer = DescriptorWriter(fd)
    os.close(fd)
    writer.mkdir("services")
    writer.mkdir(schema.SERVICE_RELATIVE_ROOT)
    writer.mkdir(f"{schema.SERVICE_RELATIVE_ROOT}/epochs")
    now = time.time_ns()
    epoch_id = runtime._open_epoch(writer, root_id=ROOT_ID, volume_uuid=VOLUME_UUID, now_ns=now)
    plan_hash = schema.sha(
        writer.read_bytes(f"{schema.SERVICE_RELATIVE_ROOT}/epochs/{epoch_id}/{schema.PLAN_FILE}")
    )
    detached = tmp_path / "held-root"
    underlay = tmp_path / "root-underlay"
    underlay.mkdir(mode=0o700)

    def detach_before_create(operation: str) -> None:
        if operation == "create":
            os.rename(root, detached)
            os.rename(underlay, root)
            raise OSError("simulated_detach_before_create")

    writer._fault_hook = detach_before_create
    agent = FakeAgent(writer)
    try:
        with pytest.raises(OSError, match="simulated_detach_before_create"):
            cutover.cutover_pending_epoch(
                writer,
                epoch_id=epoch_id,
                expected_plan_sha256=plan_hash,
                root_id=ROOT_ID,
                volume_uuid=VOLUME_UUID,
                agent=agent,
            )
        assert agent.calls == 0
        assert list(root.iterdir()) == []
        assert not (root / "services").exists()
        assert not writer.exists(cutover.STOP_FILE)
    finally:
        writer.close()
        os.replace(root, underlay)
        os.replace(detached, root)


def test_recovery_after_terminal_file_create_fault_is_no_replay() -> None:
    with seeded_epoch() as (writer, epoch_id, plan_hash, _plan):

        class FailTerminalCreate:
            def disable_bootout_and_verify(self) -> None:
                writer._fault_hook = lambda operation: (
                    (_ for _ in ()).throw(OSError("simulated_terminal_create_failure"))
                    if operation == "create"
                    else None
                )

        with pytest.raises(OSError, match="simulated_terminal_create_failure"):
            cutover.cutover_pending_epoch(
                writer,
                epoch_id=epoch_id,
                expected_plan_sha256=plan_hash,
                root_id=ROOT_ID,
                volume_uuid=VOLUME_UUID,
                agent=FailTerminalCreate(),
            )
        writer._fault_hook = None
        result = cutover.cutover_pending_epoch(
            writer,
            epoch_id=epoch_id,
            expected_plan_sha256=plan_hash,
            root_id=ROOT_ID,
            volume_uuid=VOLUME_UUID,
            agent=FakeAgent(writer),
        )
        assert result["request_intents"] == 0
        assert result["status"] == "abandoned_before_dispatch"


def test_recovery_after_control_append_fault_completes_once() -> None:
    with seeded_epoch() as (writer, epoch_id, plan_hash, _plan):

        class FailControlAppend:
            def disable_bootout_and_verify(self) -> None:
                append_count = 0

                def hook(operation: str) -> None:
                    nonlocal append_count
                    if operation == "append_write":
                        append_count += 1
                        if append_count == 2:
                            raise OSError("simulated_control_append_failure")

                writer._fault_hook = hook

        with pytest.raises(OSError, match="simulated_control_append_failure"):
            cutover.cutover_pending_epoch(
                writer,
                epoch_id=epoch_id,
                expected_plan_sha256=plan_hash,
                root_id=ROOT_ID,
                volume_uuid=VOLUME_UUID,
                agent=FailControlAppend(),
            )
        writer._fault_hook = None
        result = cutover.cutover_pending_epoch(
            writer,
            epoch_id=epoch_id,
            expected_plan_sha256=plan_hash,
            root_id=ROOT_ID,
            volume_uuid=VOLUME_UUID,
            agent=FakeAgent(writer),
        )
        control = schema.verify_event_chain(
            writer.read_bytes(f"{schema.SERVICE_RELATIVE_ROOT}/{schema.CONTROL_FILE}"),
            schema=schema.CONTROL_SCHEMA,
        )
        target_abandons = [
            row
            for row in control
            if row["kind"] == "epoch_abandon" and row["fields"].get("epoch_id") == epoch_id
        ]
        assert len(target_abandons) == 1
        assert result["request_intents"] == 0


def test_other_active_epoch_prevents_cutover_completion() -> None:
    with seeded_epoch() as (writer, epoch_id, plan_hash, _plan):
        runtime._open_epoch(
            writer,
            root_id=ROOT_ID,
            volume_uuid=VOLUME_UUID,
            now_ns=time.time_ns(),
        )
        with pytest.raises(cutover.CutoverError, match="control_active_state_conflict"):
            cutover.cutover_pending_epoch(
                writer,
                epoch_id=epoch_id,
                expected_plan_sha256=plan_hash,
                root_id=ROOT_ID,
                volume_uuid=VOLUME_UUID,
                agent=FakeAgent(writer),
            )
        assert writer.exists(cutover.STOP_FILE)


def test_launchctl_adapter_requires_unloaded_and_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    outputs = iter(
        [
            (0, ""),
            (0, ""),
            (0, "PID Status Label\n"),
            (0, "python unrelated.py\n"),
            (0, f'{{ "{cutover._LABEL}" => true }}'),
        ]
    )
    observed: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: Any) -> Any:
        observed.append(argv)
        code, stdout = next(outputs)
        return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cutover.LaunchctlAgentControl(uid=501).disable_bootout_and_verify()
    assert [row[1] for row in observed[:2]] == ["disable", "bootout"]
    assert observed[2] == ["/bin/launchctl", "list"]
    assert observed[3] == ["/bin/ps", "-axo", "pid=,command="]

    outputs_bad = iter([(0, ""), (0, ""), (0, f"123 0 {cutover._LABEL}\n")])

    def fake_loaded(argv: list[str], **_kwargs: Any) -> Any:
        code, stdout = next(outputs_bad)
        return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_loaded)
    with pytest.raises(cutover.CutoverError, match="v4_agent_still_loaded"):
        cutover.LaunchctlAgentControl(uid=501).disable_bootout_and_verify()

    outputs_running = iter(
        [
            (0, ""),
            (0, ""),
            (0, "PID Status Label\n"),
            (0, f"123 {cutover._V4_SCRIPT} --scheduled-tick --plist x\n"),
        ]
    )

    def fake_running(argv: list[str], **_kwargs: Any) -> Any:
        code, stdout = next(outputs_running)
        return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_running)
    with pytest.raises(cutover.CutoverError, match="v4_process_still_running"):
        cutover.LaunchctlAgentControl(uid=501).disable_bootout_and_verify()


def test_cli_uses_pinned_storage_config_and_exposes_no_config_override(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    observed: dict[str, Any] = {}

    def fake_run(config_path: Path, **kwargs: Any) -> dict[str, Any]:
        observed["config"] = config_path
        observed.update(kwargs)
        return {
            "epoch_id": kwargs["epoch_id"],
            "terminal_sha256": "f" * 64,
            "request_intents": 0,
        }

    monkeypatch.setattr(cutover, "run_bound_cutover", fake_run)
    rc = cutover.main(["--epoch-id", "20261012T160000Z-0123456789ab", "--plan-sha256", "a" * 64])
    assert rc == 0
    assert observed["config"] == cutover.DEFAULT_STORAGE_CONFIG
    assert "cutover complete" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cutover.main(["--storage-config", "/tmp/other.json"])
