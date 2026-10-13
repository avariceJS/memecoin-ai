from __future__ import annotations

import contextlib
import hashlib
import json
import os
import plistlib
import signal
import subprocess
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

from tools import brain_senses_gecko_capture_only_v6_host as host

TESTS_DIR = Path(__file__).resolve().parents[1] / "tests"
if str(TESTS_DIR) not in os.sys.path:
    os.sys.path.insert(0, str(TESTS_DIR))
from test_brain_senses_gecko_capture_only_v6 import Harness  # noqa: E402


def _fake_repo(tmp_path: Path) -> tuple[Path, dict[str, bytes], Any]:
    committed: dict[str, bytes] = {}
    for index, relative in enumerate(host.SOURCE_PATHS):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = f"pinned source {index}\n".encode()
        path.write_bytes(raw)
        committed[relative] = raw

    def fake_git(_root: Path, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return "a" * 40
        if args[:1] == ("status",):
            return ""
        if len(args) == 2 and args[0] == "show":
            commit, relative = args[1].split(":", 1)
            assert commit == "a" * 40
            return committed[relative].decode()
        raise AssertionError(args)

    return tmp_path, committed, fake_git


def _plist(repo_root: Path) -> bytes:
    return plistlib.dumps(
        {
            "Label": host.LABEL,
            "ProgramArguments": [
                str(repo_root / ".venv/bin/python"),
                "-B",
                str(repo_root / "tools/brain_senses_gecko_capture_only_v6_host.py"),
                "--scheduled-tick",
            ],
            "RunAtLoad": False,
            "KeepAlive": False,
            "StartInterval": 60,
            "ProcessType": "Background",
            "StandardOutPath": "/dev/null",
            "StandardErrorPath": "/dev/null",
        },
        fmt=plistlib.FMT_XML,
        sort_keys=True,
    )


def test_source_manifest_pins_clean_bytes_and_rejects_drift(tmp_path: Path) -> None:
    repo, committed, fake_git = _fake_repo(tmp_path)
    manifest = host.compile_source_manifest(repo, git=fake_git)
    host.verify_source_manifest(repo, manifest, git=fake_git)
    changed = repo / host.SOURCE_PATHS[0]
    changed.write_bytes(b"changed\n")
    with pytest.raises(host.HostV6Error, match="source_blob_mismatch"):
        host.verify_source_manifest(repo, manifest, git=fake_git)
    assert committed[host.SOURCE_PATHS[0]] != changed.read_bytes()


def test_source_manifest_rejects_dirty_or_wrong_commit(tmp_path: Path) -> None:
    repo, _committed, fake_git = _fake_repo(tmp_path)

    def dirty_git(_root: Path, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return "a" * 40
        if args[:1] == ("status",):
            return " M tools/brain_senses_gecko_lane_policy_v6.py"
        return fake_git(_root, *args)

    with pytest.raises(host.HostV6Error, match="source_paths_dirty"):
        host.compile_source_manifest(repo, git=dirty_git)
    with pytest.raises(host.HostV6Error, match="source_commit_mismatch"):
        host.compile_source_manifest(repo, source_commit="b" * 40, git=fake_git)


def test_fixed_context_uses_exact_paths_canonical_bytes_and_manifest_pin() -> None:
    core = {
        "schema": host.SOURCE_MANIFEST_SCHEMA,
        "schema_version": 6,
        "source_commit": "a" * 40,
        "source_paths": list(host.SOURCE_PATHS),
        "source_map": {path: "b" * 64 for path in host.SOURCE_PATHS},
    }
    manifest = {**core, "source_manifest_sha256": host.sha256(host.canonical(core))}
    activation = {"source_manifest_sha256": manifest["source_manifest_sha256"]}

    class Reader:
        def __init__(self, rows: dict[str, bytes]) -> None:
            self.rows = rows

        def read_bytes(self, path: str, *, max_bytes: int) -> bytes:
            assert len(self.rows[path]) <= max_bytes
            return self.rows[path]

    reader = Reader(
        {
            host.SOURCE_MANIFEST_PATH: host.canonical(manifest),
            host.ACTIVATION_PATH: host.canonical(activation),
        }
    )
    result = host.load_fixed_release_context(reader)
    assert result["source_manifest"] == manifest
    assert result["activation"] == activation
    reader.rows[host.SOURCE_MANIFEST_PATH] = b'{"schema":"bad","schema":"duplicate"}\n'
    with pytest.raises(host.HostV6Error, match="duplicate_json_key"):
        host.load_fixed_release_context(reader)


def test_plist_is_inert_fixed_60_second_callback() -> None:
    raw = _plist(host.REPO_ROOT)
    parsed = host.validate_plist(raw, repo_root=host.REPO_ROOT)
    assert parsed["StartInterval"] == 60
    assert parsed["RunAtLoad"] is False
    assert parsed["KeepAlive"] is False
    for key in ("EnvironmentVariables", "WorkingDirectory", "ThrottleInterval"):
        assert key not in parsed
    checked_in = (host.REPO_ROOT / host.PLIST_PATH).read_bytes()
    assert host.validate_plist(checked_in, repo_root=host.REPO_ROOT) == parsed


def test_plist_drift_and_secret_arguments_rejected() -> None:
    value = plistlib.loads(_plist(host.REPO_ROOT))
    value["ProgramArguments"][-1] = "https://secret.invalid/key"
    with pytest.raises(host.HostV6Error, match="plist_binding"):
        host.validate_plist(plistlib.dumps(value), repo_root=host.REPO_ROOT)
    value = plistlib.loads(_plist(host.REPO_ROOT))
    value["StartInterval"] = 12
    with pytest.raises(host.HostV6Error, match="plist_binding"):
        host.validate_plist(plistlib.dumps(value), repo_root=host.REPO_ROOT)


def _valid_activation(
    repo_root: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    source_map = {
        "tools/brain_senses_gecko_rotating_scout_v1.py": "1" * 64,
    }
    manifest = {
        "source_commit": "a" * 40,
        "source_manifest_sha256": "2" * 64,
        "source_map": source_map,
    }
    policy = {
        "policy_sha256": "3" * 64,
        "source": {"population_sha256": "4" * 64},
    }
    epoch = {"epoch_sha256": "5" * 64}
    storage = {
        "root_id": "6" * 32,
        "volume_uuid": "12345678-1234-1234-1234-123456789abc",
        "device": 2,
        "inode": 7,
    }
    core = {
        "schema": host.ACTIVATION_SCHEMA,
        "schema_version": 6,
        "activation_id": "7" * 32,
        "source_commit": manifest["source_commit"],
        "source_manifest_sha256": manifest["source_manifest_sha256"],
        "policy_sha256": policy["policy_sha256"],
        "selector_sha256": source_map["tools/brain_senses_gecko_rotating_scout_v1.py"],
        "population_sha256": policy["source"]["population_sha256"],
        "release_id": "8" * 32,
        "service_root": f"{host.SERVICE_PREFIX}/{'8' * 32}",
        "epoch_sha256": epoch["epoch_sha256"],
        "storage": storage,
        "plist_sha256": host.sha256(_plist(repo_root)),
        "label": host.LABEL,
        "program_arguments": [
            str(repo_root / ".venv/bin/python"),
            "-B",
            str(repo_root / "tools/brain_senses_gecko_capture_only_v6_host.py"),
            "--scheduled-tick",
        ],
        "capture_authorized": True,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
    }
    return (
        {**core, "activation_sha256": host.sha256(host.canonical(core))},
        manifest,
        policy,
        epoch,
        storage,
    )


def test_activation_requires_exact_epoch_source_and_disabled_admission() -> None:
    activation, manifest, policy, epoch, storage = _valid_activation(host.REPO_ROOT)
    accepted = host.verify_activation_record(
        activation,
        source_manifest=manifest,
        policy=policy,
        storage=storage,
        plist_sha256=host.sha256(_plist(host.REPO_ROOT)),
        epoch=epoch,
    )
    assert accepted["capture_authorized"] is True
    for mutation in (
        {"epoch_sha256": "9" * 64},
        {"capture_authorized": False},
        {"market_source_qualified": True},
        {"economic_admission": True},
    ):
        bad = {**activation, **mutation}
        core = dict(bad)
        core.pop("activation_sha256")
        bad["activation_sha256"] = host.sha256(host.canonical(core))
        with pytest.raises(host.HostV6Error):
            host.verify_activation_record(
                bad,
                source_manifest=manifest,
                policy=policy,
                storage=storage,
                plist_sha256=host.sha256(_plist(host.REPO_ROOT)),
                epoch=epoch,
            )


def test_loaded_owner_must_be_exact_current_process_and_label() -> None:
    snapshot = {
        "loaded": True,
        "label": host.LABEL,
        "pid": os.getpid(),
        "uid": os.getuid(),
        "plist_path": host.INSTALLED_PLIST_PATH,
        "program_arguments": [
            str(host.REPO_ROOT / ".venv/bin/python"),
            "-B",
            str(host.REPO_ROOT / "tools/brain_senses_gecko_capture_only_v6_host.py"),
            "--scheduled-tick",
        ],
    }
    host.verify_loaded_owner(
        snapshot,
        pid=os.getpid(),
        uid=os.getuid(),
        repo_root=host.REPO_ROOT,
        plist_path=host.INSTALLED_PLIST_PATH,
    )
    for key, bad_value in (("pid", os.getpid() + 1), ("uid", os.getuid() + 1), ("label", "wrong")):
        bad = {**snapshot, key: bad_value}
        with pytest.raises(host.HostV6Error, match="launch_identity"):
            host.verify_loaded_owner(
                bad,
                pid=os.getpid(),
                uid=os.getuid(),
                repo_root=host.REPO_ROOT,
                plist_path=host.INSTALLED_PLIST_PATH,
            )

    with pytest.raises(host.HostV6Error, match="launch_identity"):
        host.verify_loaded_owner(
            snapshot,
            pid=os.getpid(),
            uid=os.getuid(),
            repo_root=host.REPO_ROOT,
            plist_path=host.PLIST_PATH,
        )


def test_owner_tick_refuses_unreplayed_or_malformed_binding_without_io() -> None:
    # No caller-controlled replay or activation context is accepted by entry.
    with pytest.raises(TypeError):
        host.scheduled_tick(binding_loader=lambda: {"verified": True})  # type: ignore[call-arg]


def test_transport_rejects_non_exact_endpoint_before_opener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("network opener must not be constructed for invalid URL")

    monkeypatch.setattr(host.urllib.request, "build_opener", forbidden)
    with pytest.raises(host.HostV6Error, match="endpoint_binding"):
        host.public_fetch_once(
            {
                "pool_id": "1" * 32,
                "url": "https://example.invalid/secret",
                "headers": {
                    "Accept": "application/json;version=20230203",
                    "User-Agent": "memecoin-ai-research/0.1",
                },
                "max_response_bytes": 16 * 1024,
            }
        )


def test_sixty_second_poll_has_bounded_six_request_window() -> None:
    starts = [119 + 25 * index for index in range(6)]
    durable = [start + 25 for start in starts]
    assert len(starts) == 6
    assert all(right - left >= 12 for left, right in zip(starts, starts[1:], strict=False))
    assert starts[-1] < 270
    assert durable[-1] < 300


def _run_six_pool_bucket(
    harness: Any,
    *,
    http_status: int = 200,
    transport_counter: list[int] | None = None,
    transport_impl: Any | None = None,
) -> dict[str, Any]:

    if harness.journal.exclusive_lane_lease._owner_thread is not None:
        harness._held_lane.__exit__(None, None, None)
        harness._held_lane = contextlib.nullcontext()

    clock = {"wall": harness.epoch["first_bucket_close_ns"] + 60 * 1_000_000_000}
    monotonic = {"value": 8_000_000_000}

    def wall_ns() -> int:
        clock["wall"] += 1_000_000
        return clock["wall"]

    def monotonic_ns() -> int:
        monotonic["value"] += 1_000_000
        return monotonic["value"]

    def sleep(seconds: float) -> None:
        clock["wall"] += int(seconds * 1_000_000_000)
        monotonic["value"] += int(seconds * 1_000_000_000)

    def fake_transport(_request: Any) -> tuple[int, bytes]:
        if transport_counter is not None:
            transport_counter.append(1)
        clock["wall"] += 1_000_000_000
        monotonic["value"] += 1_000_000_000
        body = json.dumps({"data": {"attributes": {"ohlcv_list": []}}}).encode()
        return http_status, body

    return host.run_scheduled_batch(
        journal=harness.journal,
        policy=harness.policy,
        epoch=harness.epoch,
        inherited_usage_rows=[],
        selector_sha256=host_selector_sha(),
        clock_check=lambda: True,
        transport=transport_impl or fake_transport,
        quiescence_proofs=lambda _events: {},
        wall_ns=wall_ns,
        monotonic_ns=monotonic_ns,
        sleep=sleep,
    )


def test_scheduled_batch_recovers_orphan_plan_before_full_replay_then_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import brain_senses_gecko_capture_only_v6 as capture_v6

    harness = Harness(monkeypatch)
    calls: list[int] = []
    try:
        close = harness.epoch["first_bucket_close_ns"]
        original_append = harness.journal._append

        def fail_after_plan_file(
            kind: str, at_ns: int, fields: dict[str, Any], **kwargs: Any
        ) -> Any:
            if kind == "plan":
                raise RuntimeError("crash_after_persisted_plan_file")
            return original_append(kind, at_ns, fields, **kwargs)

        monkeypatch.setattr(harness.journal, "_append", fail_after_plan_file)
        with pytest.raises(RuntimeError, match="crash_after_persisted_plan_file"):
            harness.journal.plan_bucket(
                policy=harness.policy,
                epoch=harness.epoch,
                close_ns=close,
                now_ns=close + 60 * 1_000_000_000,
                selector_sha256=host_selector_sha(),
            )
        monkeypatch.setattr(harness.journal, "_append", original_append)
        orphan = capture_v6._object(
            harness.writer.read_bytes(
                f"{capture_v6.PLAN_DIRECTORY}/{close}.json", max_bytes=256 * 1024
            ),
            "orphan_plan",
        )
        result = _run_six_pool_bucket(harness, transport_counter=calls)
        assert result["status"] == "bucket_processed"
        assert result["provider_io"] is True
        assert len(calls) == 6
        events = harness.journal._events()
        plan = next(event for event in events if event["kind"] == "plan")
        assert plan["fields"]["selected_pool_ids"] == orphan["selected_pool_ids"]
        assert plan["fields"]["cursor_out"] == orphan["cursor_out"]
        assert len([event for event in events if event["kind"] == "dispatch"]) == 6
    finally:
        harness.close()


def test_unproven_post_dispatch_worker_stays_unresolved_and_blocks_next_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import brain_senses_gecko_capture_only_v6 as capture_v6

    harness = Harness(monkeypatch)
    execute_calls: list[int] = []
    try:

        def unproven_transport(
            _request: Any,
            *,
            before_start: Any,
            worker_source_sha256: str,
            timeout_seconds: float = 20.0,
        ) -> tuple[int, bytes]:
            del timeout_seconds
            execute_calls.append(1)
            before_start(
                {
                    "pid": 123456,
                    "pgid": 123456,
                    "birth_id": "1790000000.000001",
                    "executable_path": "/synthetic/worker",
                    "uid": os.getuid(),
                    "attempt_nonce": "e" * 32,
                    "worker_source_sha256": worker_source_sha256,
                }
            )
            raise host.HostV6Error("v6_transport_child_group_unproven")

        monkeypatch.setattr(host.bounded_transport_once, "execute", unproven_transport)
        with pytest.raises(host.HostV6Error, match="group_unproven"):
            _run_six_pool_bucket(
                harness,
                transport_impl=host.bounded_transport_once,
            )
        events = harness.journal._events()
        assert len([event for event in events if event["kind"] == "dispatch"]) == 1
        assert len([event for event in events if event["kind"] == "intent"]) == 1
        assert not [event for event in events if event["kind"] == "outcome"]

        # On the following callback, absent an exact physical quiescence proof,
        # recovery stops before another transport invocation.
        with pytest.raises(capture_v6.CaptureV6Error, match="quiescence_set"):
            _run_six_pool_bucket(
                harness,
                transport_impl=host.bounded_transport_once,
            )
        assert execute_calls == [1]
        assert not [event for event in harness.journal._events() if event["kind"] == "outcome"]
    finally:
        harness.close()


def host_selector_sha() -> str:
    return hashlib.sha256(b"pinned selector source test binding").hexdigest()


def test_six_slot_owner_callback_replays_confirms_and_restart_does_not_repeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import brain_senses_gecko_capture_only_v6 as capture_v6
    from tools import brain_senses_gecko_rotating_scout_v1 as scout

    harness = Harness(monkeypatch)
    try:
        calls: list[int] = []
        result = _run_six_pool_bucket(harness, transport_counter=calls)
        assert result["status"] == "bucket_processed"
        assert result["provider_io"] is True
        assert len(result["attempted"]) == len(result["confirmed"]) == 6
        assert len(calls) == 6
        events = capture_v6.replay_events(
            harness.writer.read_bytes(capture_v6.EVENTS_PATH, max_bytes=64 * 1024 * 1024)
        )
        dispatches = [
            event["fields"]["request_started_ns"] for event in events if event["kind"] == "dispatch"
        ]
        assert len(dispatches) == 6
        assert all(
            right - left >= scout.MINIMUM_DISPATCH_SPACING_NS
            for left, right in zip(dispatches, dispatches[1:], strict=False)
        )
        replay = capture_v6.verify_local_journal(
            harness.writer,
            epoch=harness.epoch,
            policy=harness.policy,
            selector=scout.plan_bucket,
            selector_sha256=host_selector_sha(),
            expected_storage=harness.storage,
            observed_storage=lambda: dict(harness.storage),
            now_ns=dispatches[-1] + 2_000_000_000,
            inherited_usage_rows=[],
            resource_snapshot={
                "free_bytes": harness.policy["capacity"]["minimum_free_bytes"] + 1,
                "resident_bytes": harness.policy["capacity"]["resident_allocation_ceiling_bytes"]
                - 1,
            },
        )
        assert replay["usage"]["intents_utc_day"] == 6
        assert all(value == "intent" for value in replay["slots"].values())
        assert sum(row["confirmed"] for row in replay["usage"]["rows"]) == 6
        second = _run_six_pool_bucket(harness, transport_counter=calls)
        assert second["provider_io"] is False
        assert len(calls) == 6
    finally:
        harness.close()


def test_429_is_durable_stop_for_later_slot_and_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    try:
        calls: list[int] = []
        first = _run_six_pool_bucket(harness, http_status=429, transport_counter=calls)
        assert first["status"] == "stopped_after_non_success_terminal"
        assert len(calls) == 1
        second = _run_six_pool_bucket(harness, transport_counter=calls)
        assert second["status"] == "stopped_by_prior_terminal"
        assert len(calls) == 1
    finally:
        harness.close()


def test_late_restart_seals_every_unspent_slot_as_missed_idempotently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import brain_senses_gecko_capture_only_v6 as capture_v6
    from tools import brain_senses_gecko_rotating_scout_v1 as scout

    harness = Harness(monkeypatch)
    close = harness.epoch["first_bucket_close_ns"]
    calls: list[int] = []
    try:
        harness._held_lane.__exit__(None, None, None)
        harness._held_lane = contextlib.nullcontext()
        with harness.journal.hold_exclusive_lane():
            harness.journal.plan_bucket(
                policy=harness.policy,
                epoch=harness.epoch,
                close_ns=close,
                now_ns=close + 60 * 1_000_000_000,
                selector_sha256=host_selector_sha(),
            )
        clock = {"now": close + 301 * 1_000_000_000}

        def run_once() -> dict[str, Any]:
            return host.run_scheduled_batch(
                journal=harness.journal,
                policy=harness.policy,
                epoch=harness.epoch,
                inherited_usage_rows=[],
                selector_sha256=host_selector_sha(),
                clock_check=lambda: True,
                transport=lambda _request: (calls.append(1) or 200, b""),
                quiescence_proofs=lambda _events: {},
                wall_ns=lambda: clock["now"],
                monotonic_ns=lambda: clock["now"],
            )

        first = run_once()
        assert first["status"] == "bucket_expired"
        events = capture_v6.replay_events(
            harness.writer.read_bytes(capture_v6.EVENTS_PATH, max_bytes=64 * 1024 * 1024)
        )
        assert sum(event["kind"] == "missed" for event in events) == 6
        assert sum(event["kind"] == "intent" for event in events) == 0
        clock["now"] += 1_000_000_000
        second = run_once()
        events_after = capture_v6.replay_events(
            harness.writer.read_bytes(capture_v6.EVENTS_PATH, max_bytes=64 * 1024 * 1024)
        )
        assert second["status"] == "bucket_expired"
        assert sum(event["kind"] == "missed" for event in events_after) == 6
        assert calls == []
        assert (
            capture_v6.verify_local_journal(
                harness.writer,
                epoch=harness.epoch,
                policy=harness.policy,
                selector=scout.plan_bucket,
                selector_sha256=host_selector_sha(),
                expected_storage=harness.storage,
                observed_storage=lambda: dict(harness.storage),
                now_ns=clock["now"],
                inherited_usage_rows=[],
                resource_snapshot={
                    "free_bytes": harness.policy["capacity"]["minimum_free_bytes"] + 1,
                    "resident_bytes": harness.policy["capacity"][
                        "resident_allocation_ceiling_bytes"
                    ]
                    - 1,
                },
            )["usage"]["intents_utc_day"]
            == 0
        )
    finally:
        harness.close()


def test_clock_stop_prevents_any_slot_reservation(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    calls: list[int] = []
    try:
        harness._held_lane.__exit__(None, None, None)
        harness._held_lane = contextlib.nullcontext()
        with pytest.raises(host.HostV6Error, match="clock_stop"):
            host.run_scheduled_batch(
                journal=harness.journal,
                policy=harness.policy,
                epoch=harness.epoch,
                inherited_usage_rows=[],
                selector_sha256=host_selector_sha(),
                clock_check=lambda: False,
                transport=lambda _request: (calls.append(1) or 200, b""),
                quiescence_proofs=lambda _events: {},
                wall_ns=lambda: harness.epoch["first_bucket_close_ns"] + 60_000_000_000,
            )
        assert calls == []
        assert harness.writer.read_bytes("events.jsonl") == b""
    finally:
        harness.close()


def test_private_directory_open_rejects_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    (tmp_path / "outside").mkdir(mode=0o700)
    os.symlink(tmp_path / "outside", root / "link")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(host.HostV6Error, match="directory_unavailable"):
            host._open_private_directory(fd, "link")
    finally:
        os.close(fd)


def test_absolute_owner_file_read_rejects_parent_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "plist"
    target.write_bytes(b"safe")
    link = tmp_path / "linked"
    os.symlink(outside, link)
    with pytest.raises(host.HostV6Error, match="file_unavailable"):
        host._read_owner_file(link / "plist", maximum=32)


def test_launchctl_print_parser_binds_loaded_pid_path_and_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = [
        str(host.REPO_ROOT / ".venv/bin/python"),
        "-B",
        str(host.REPO_ROOT / "tools/brain_senses_gecko_capture_only_v6_host.py"),
        "--scheduled-tick",
    ]
    output = "\n".join(
        [
            f"path = {host.INSTALLED_PLIST_PATH}",
            "pid = 1234",
            "arguments = {",
            *(f"    {argument}" for argument in args),
            "}",
        ]
    )
    monkeypatch.setattr(
        host.subprocess,
        "run",
        lambda *_args, **_kwargs: type("Result", (), {"stdout": output})(),
    )
    snapshot = host._launchctl_print_snapshot(os.getuid())
    assert snapshot["pid"] == 1234
    assert snapshot["plist_path"] == host.INSTALLED_PLIST_PATH
    assert snapshot["program_arguments"] == args


def test_legacy_label_census_rejects_release_suffixed_collectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = """
    services = {
        ai.memecoin.brain.senses.gecko-capture-only-v6 = {
            state = running
        }
        ai.memecoin.brain.senses.gecko-capture-only-v3-aaaaaaaaaaaaaaaa = {
            state = waiting
        }
    }
    """
    monkeypatch.setattr(
        host.subprocess,
        "run",
        lambda *_args, **_kwargs: type("Result", (), {"stdout": output})(),
    )
    with pytest.raises(host.HostV6Error, match="legacy_gecko_collector_loaded"):
        host._assert_no_legacy_gecko_labels(os.getuid())


def test_legacy_label_census_allows_only_current_v6_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = f"service = {host.LABEL}\n"
    monkeypatch.setattr(
        host.subprocess,
        "run",
        lambda *_args, **_kwargs: type("Result", (), {"stdout": output})(),
    )
    host._assert_no_legacy_gecko_labels(os.getuid())


def _fake_transport_child(tmp_path: Path, *, delay: float, pid_path: Path) -> Path:
    script = tmp_path / "tools" / "brain_senses_gecko_capture_only_v6_host.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        textwrap.dedent(
            f"""
            import base64, json, os, sys, time
            envelope = json.loads(sys.stdin.buffer.readline())
            open({str(pid_path)!r}, "w").write(str(os.getpid()))
            identity = envelope["worker_identity"]
            ready = {{"schema":"brain-senses-gecko-v6-transport-ready", "pid":os.getpid(),
                     "pgid":identity["pgid"], "birth_id":identity["birth_id"],
                     "executable_path":identity["executable_path"],
                     "attempt_nonce":envelope["attempt_nonce"],
                     "request_sha256":envelope["request_sha256"],
                     "worker_source_sha256":envelope["worker_source_sha256"]}}
            ready_raw = json.dumps(ready, sort_keys=True, separators=(",", ":")) + "\\n"
            sys.stdout.buffer.write(ready_raw.encode())
            sys.stdout.buffer.flush()
            go = json.loads(sys.stdin.buffer.readline())
            expected_go = {{"command":"GO", "attempt_nonce":envelope["attempt_nonce"],
                           "request_sha256":envelope["request_sha256"]}}
            if go != expected_go:
                raise SystemExit(3)
            time.sleep({delay!r})
            noww, nowm = time.time_ns(), time.monotonic_ns()
            value = {{"schema":"brain-senses-gecko-v6-transport-result","status":200,
                     "body_b64":base64.b64encode(b"{{}}" ).decode("ascii"),
                     "started_wall_ns":noww,"started_monotonic_ns":nowm,
                     "received_wall_ns":noww,"received_monotonic_ns":nowm,
                     "attempt_nonce":envelope["attempt_nonce"],
                     "request_sha256":envelope["request_sha256"],
                     "worker_pid":os.getpid(), "worker_birth_id":identity["birth_id"]}}
            sys.stdout.buffer.write((json.dumps(value,sort_keys=True,separators=(",",":"))+"\\n").encode())
            sys.stdout.buffer.flush()
            """
        )
    )
    return script


def test_bounded_child_handshake_and_exact_once_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_path = tmp_path / "child.pid"
    _fake_transport_child(tmp_path, delay=0, pid_path=pid_path)
    monkeypatch.setattr(host, "REPO_ROOT", tmp_path)
    markers: list[tuple[int, int, dict[str, Any]]] = []

    def before_start(identity: Any) -> tuple[int, int]:
        marker = (time.time_ns(), time.monotonic_ns())
        markers.append((*marker, dict(identity)))
        return marker

    status, body = host.bounded_transport_once.execute(
        {"url": "https://api.geckoterminal.com/"},
        before_start=before_start,
        worker_source_sha256=host._transport_worker_source_hash(),
    )
    assert (status, body) == (200, b"{}")
    assert len(markers) == 1
    pid = int(pid_path.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_bounded_child_timeout_kills_and_reaps_before_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_path = tmp_path / "child.pid"
    _fake_transport_child(tmp_path, delay=5, pid_path=pid_path)
    monkeypatch.setattr(host, "REPO_ROOT", tmp_path)
    with pytest.raises(host.HostV6Error, match="timeout_or_failure"):
        host.bounded_transport_once.execute(
            {"url": "https://api.geckoterminal.com/"},
            before_start=lambda _identity: (time.time_ns(), time.monotonic_ns()),
            worker_source_sha256=host._transport_worker_source_hash(),
            timeout_seconds=0.1,
        )
    pid = int(pid_path.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_bounded_child_kill_failure_stops_at_finite_reap_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_path = tmp_path / "child.pid"
    _fake_transport_child(tmp_path, delay=30, pid_path=pid_path)
    monkeypatch.setattr(host, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(host, "CHILD_REAP_TIMEOUT_SECONDS", 0.05)

    def fail_kill(*_args: Any, **_kwargs: Any) -> None:
        raise PermissionError("synthetic kill failure")

    real_kill = os.kill
    real_killpg = os.killpg
    monkeypatch.setattr(host.os, "killpg", fail_kill)
    monkeypatch.setattr(host.os, "kill", fail_kill)
    with pytest.raises(host.HostV6Error, match="reap_unproven"):
        host.bounded_transport_once.execute(
            {"url": "https://api.geckoterminal.com/"},
            before_start=lambda _identity: (time.time_ns(), time.monotonic_ns()),
            worker_source_sha256=host._transport_worker_source_hash(),
            timeout_seconds=0.05,
        )
    pid = int(pid_path.read_text())
    monkeypatch.setattr(host.os, "kill", real_kill)
    monkeypatch.setattr(host.os, "killpg", real_killpg)
    try:
        real_killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_worker_self_terminates_at_its_own_monotonic_lifetime_deadline() -> None:
    command = [
        os.sys.executable,
        "-c",
        "from tools.brain_senses_gecko_capture_only_v6_host import "
        "_arm_child_hard_lifetime; import sys, time; "
        "assert sys.stdin.readline() == 'GO\\n'; "
        "_arm_child_hard_lifetime(0.1); time.sleep(10)",
    ]
    completed = subprocess.run(
        command,
        cwd=host.REPO_ROOT,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(host.REPO_ROOT)},
        input=b"GO\n",
        capture_output=True,
        timeout=3,
        check=False,
    )
    assert completed.returncode == 124


def test_transport_start_gate_eof_prevents_child_from_crossing_network_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_path = tmp_path / "child.pid"
    go_path = tmp_path / "go.marker"
    script = _fake_transport_child(tmp_path, delay=0, pid_path=pid_path)
    source = script.read_text().replace(
        "time.sleep(0)", f'open({str(go_path)!r}, "w").write("go")\ntime.sleep(0)'
    )
    script.write_text(source)
    monkeypatch.setattr(host, "REPO_ROOT", tmp_path)

    def fail_before_durable_dispatch(_identity: Any) -> tuple[int, int]:
        raise host.HostV6Error("synthetic_dispatch_failure")

    with pytest.raises(host.HostV6Error, match="timeout_or_failure"):
        host.bounded_transport_once.execute(
            {"url": "https://api.geckoterminal.com/"},
            before_start=fail_before_durable_dispatch,
            worker_source_sha256=host._transport_worker_source_hash(),
            timeout_seconds=1,
        )
    assert not go_path.exists()
    pid = int(pid_path.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_quiescence_proof_binds_kernel_birth_nonce_and_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import brain_senses_gecko_capture_only_v6 as capture_v6

    started_wall = time.time_ns() - 1_000_000_000
    started_mono = time.monotonic_ns() - 1_000_000_000
    dispatch = {
        "request_id": "request",
        "request_started_ns": started_wall,
        "monotonic_started_ns": started_mono,
        "worker_pid": 8123,
        "worker_pgid": 8123,
        "worker_birth_id": "1791884748.873379",
        "worker_executable_path": "/usr/bin/python3",
        "worker_uid": 501,
        "attempt_nonce": "a" * 32,
        "worker_source_sha256": "b" * 64,
    }
    events = [
        {"kind": "intent", "fields": {"request_id": "request"}},
        {"kind": "dispatch", "fields": dispatch},
    ]
    identities = iter(
        [
            {
                "pid": 8123,
                "ppid": os.getpid(),
                "pgid": 8123,
                "birth_id": dispatch["worker_birth_id"],
                "executable_path": "/usr/bin/python3",
                "uid": 501,
            },
            None,
        ]
    )
    killed: list[int] = []
    monkeypatch.setattr(host, "_kernel_process_identity", lambda _pid: next(identities))
    monkeypatch.setattr(host.os, "killpg", lambda pgid, sig: killed.append(pgid))
    monkeypatch.setattr(host, "_group_exists", lambda _pgid: False)
    proofs = host._collect_quiescence_proofs(events, worker_source_sha256="b" * 64)
    proof = proofs["request"]
    assert killed == [8123]
    assert proof["absence_verified"] is True
    assert proof["pid_reuse_observed"] is False
    assert (
        capture_v6._verify_quiescence_proof(proof, request_id="request", dispatch=dispatch) == proof
    )
    forged = {**proof, "attempt_nonce": "c" * 32}
    with pytest.raises(capture_v6.CaptureV6Error, match="quiescence_unproven"):
        capture_v6._verify_quiescence_proof(forged, request_id="request", dispatch=dispatch)


def test_quiescence_rejects_worker_that_survives_termination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch = {
        "request_id": "request",
        "request_started_ns": time.time_ns() - 1_000_000_000,
        "monotonic_started_ns": time.monotonic_ns() - 1_000_000_000,
        "worker_pid": 8123,
        "worker_pgid": 8123,
        "worker_birth_id": "1791884748.873379",
        "worker_executable_path": "/usr/bin/python3",
        "worker_uid": 501,
        "attempt_nonce": "a" * 32,
        "worker_source_sha256": "b" * 64,
    }
    events = [
        {"kind": "intent", "fields": {"request_id": "request"}},
        {"kind": "dispatch", "fields": dispatch},
    ]
    identity = {
        "pid": 8123,
        "ppid": os.getpid(),
        "pgid": 8123,
        "birth_id": dispatch["worker_birth_id"],
        "executable_path": "/usr/bin/python3",
        "uid": 501,
    }
    ticks = iter(float(value) for value in range(10))
    monkeypatch.setattr(host, "_kernel_process_identity", lambda _pid: identity)
    monkeypatch.setattr(host.os, "killpg", lambda *_args: None)
    monkeypatch.setattr(host, "_group_exists", lambda _pgid: True)
    monkeypatch.setattr(host.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(host.time, "sleep", lambda _delay: None)
    with pytest.raises(host.HostV6Error, match="process_survived"):
        host._collect_quiescence_proofs(events, worker_source_sha256="b" * 64)


def test_quiescence_pid_reuse_proves_only_the_recorded_generation_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import brain_senses_gecko_capture_only_v6 as capture_v6

    dispatch = {
        "request_id": "request",
        "request_started_ns": time.time_ns() - 1_000_000_000,
        "monotonic_started_ns": time.monotonic_ns() - 1_000_000_000,
        "worker_pid": 8123,
        "worker_pgid": 8123,
        "worker_birth_id": "1791884748.873379",
        "worker_executable_path": "/usr/bin/python3",
        "worker_uid": 501,
        "attempt_nonce": "a" * 32,
        "worker_source_sha256": "b" * 64,
    }
    monkeypatch.setattr(
        host,
        "_kernel_process_identity",
        lambda _pid: {
            "pid": 8123,
            "pgid": 8123,
            "birth_id": "1791884749.000001",
            "executable_path": "/usr/bin/python3",
            "uid": 501,
        },
    )
    monkeypatch.setattr(host, "_group_exists", lambda _pgid: False)
    proof = host._collect_quiescence_proofs(
        [
            {"kind": "intent", "fields": {"request_id": "request"}},
            {"kind": "dispatch", "fields": dispatch},
        ],
        worker_source_sha256="b" * 64,
    )["request"]
    assert proof["pid_reuse_observed"] is True
    assert (
        capture_v6._verify_quiescence_proof(proof, request_id="request", dispatch=dispatch) == proof
    )


def test_recovery_kills_go_authorized_worker_adopted_after_parent_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model an OS-adopted in-flight child from a durable GO dispatch after restart."""
    dispatch = {
        "request_id": "request-in-flight",
        "request_started_ns": time.time_ns() - 1_000_000_000,
        "monotonic_started_ns": time.monotonic_ns() - 1_000_000_000,
        "worker_pid": 8123,
        "worker_pgid": 8123,
        "worker_birth_id": "1791884748.873379",
        "worker_executable_path": "/usr/bin/python3",
        "worker_uid": os.getuid(),
        "attempt_nonce": "d" * 32,
        "worker_source_sha256": "b" * 64,
    }
    # ppid=1 represents the child after its original callback parent exited.
    identities = iter(
        [
            {
                "pid": 8123,
                "ppid": 1,
                "pgid": 8123,
                "birth_id": dispatch["worker_birth_id"],
                "executable_path": dispatch["worker_executable_path"],
                "uid": dispatch["worker_uid"],
            },
            None,
        ]
    )
    killed: list[int] = []
    monkeypatch.setattr(host, "_kernel_process_identity", lambda _pid: next(identities))
    monkeypatch.setattr(host.os, "killpg", lambda pgid, sig: killed.append(pgid))
    monkeypatch.setattr(host, "_group_exists", lambda _pgid: False)
    proof = host._collect_quiescence_proofs(
        [
            {"kind": "intent", "fields": {"request_id": "request-in-flight"}},
            {"kind": "dispatch", "fields": dispatch},
        ],
        worker_source_sha256="b" * 64,
    )["request-in-flight"]
    from tools import brain_senses_gecko_capture_only_v6 as capture_v6

    assert killed == [dispatch["worker_pgid"]]
    assert proof["absence_verified"] is True
    assert proof["descendant_count"] == 0
    assert (
        capture_v6._verify_quiescence_proof(
            proof, request_id="request-in-flight", dispatch=dispatch
        )
        == proof
    )
