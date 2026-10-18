from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from memecoin_ai.domain import canonical
from memecoin_ai.sources import Capture
from tools import brain_senses_block_epoch_v3 as child_v3
from tools import brain_senses_calendar_supervisor_v1 as supervisor
from tools import brain_senses_finite_calendar_v3 as calendar
from tools import brain_senses_operation_bundle_v2 as bridge

DAY = bridge.UTC_DAY_NS
FLOOR = 50 * 1024**3


def _plan(now: int, buckets: list[dict] | None = None) -> dict:
    if buckets is None:
        buckets = [
            {"bucket_id": "anchor", "kind": "anchor", "start_ns": now, "end_ns": now + DAY},
            {
                "bucket_id": "frame",
                "kind": "frame",
                "start_ns": now + DAY,
                "end_ns": now + 2 * DAY,
            },
        ]
    return {
        "schema": bridge.SCHEMA,
        "calendar_contract": calendar.contract_binding(),
        "lease_id": "synthetic-supervisor-lease",
        "created_ns": now,
        "expires_ns": now + 3 * DAY,
        "source_sha256": {"fixture": "a" * 64},
        "caps": {
            "requests": 100,
            "raw_bytes": 12_000_000_000,
            "stored_bytes": 12_000_000_000,
            "physical_bytes": FLOOR,
            "width": 1_000,
            "daily_requests": 100,
            "daily_raw_bytes": 12_000_000_000,
            "daily_stored_bytes": 12_000_000_000,
        },
        "buckets": buckets,
        "predecessor": {"terminal_sha256": "b" * 64, "frontier_slot": 84, "gaps": []},
    }


@pytest.fixture
def host(monkeypatch: pytest.MonkeyPatch):
    wall = [1_800_000_000_000_000_000]
    monkeypatch.setattr(bridge.time, "time_ns", lambda: wall[0])
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _: shutil._ntuple_diskusage(200 * 1024**3, 100 * 1024**3, 100 * 1024**3),
    )
    return wall


def _init(root: Path, now: int, buckets: list[dict] | None = None) -> dict:
    plan = _plan(now, buckets)
    calendar.initialize(root, plan)
    supervisor.bind(
        root,
        expected_plan_sha256=hashlib.sha256((root / "plan-v2.json").read_bytes()).hexdigest(),
        expected_source_sha256=supervisor.source_hashes(),
    )
    return plan


class FakeRPC:
    def __init__(self, *, anchor: int, start: int | None = None, end: int | None = None):
        self.anchor, self.start, self.end = anchor, start, end
        self.calls: list[str] = []

    def fetch(self, method: str, params: list):
        self.calls.append(method)
        if method == "getSlot":
            value = self.anchor
        elif method == "getBlocks":
            value = list(range(self.start or 0, self.end or 0))
        elif params[1]["transactionDetails"] == "signatures":
            slot = params[0]
            value = {
                "blockhash": f"hash-{slot}",
                "parentSlot": slot - 1,
                "previousBlockhash": f"hash-{slot - 1}",
                "signatures": [f"sig-{slot}"],
            }
        else:
            slot = params[0]
            value = {
                "blockhash": f"hash-{slot}",
                "parentSlot": slot - 1,
                "previousBlockhash": f"hash-{slot - 1}",
                "blockTime": 1_800_000_000 + slot,
                "transactions": [
                    {
                        "transaction": {
                            "signatures": [f"sig-{slot}"],
                            "message": {
                                "accountKeys": ["11111111111111111111111111111111"],
                                "instructions": [],
                            },
                        },
                        "version": "legacy",
                        "meta": {
                            "err": None,
                            "innerInstructions": [],
                            "loadedAddresses": {"writable": [], "readonly": []},
                        },
                    }
                ],
            }
        stamp = time.time_ns()
        return Capture(
            "helius-free",
            method,
            canonical({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}),
            stamp,
            stamp,
            canonical({"jsonrpc": "2.0", "id": 1, "result": value}).encode(),
        )

    def close(self):
        pass


def _child_plan(parent: Path, bucket: dict, bucket_id: str) -> dict:
    return child_v3.build_epoch_plan(
        epoch_id=bucket_id,
        bucket=bucket_id,
        schedule_id="synthetic-owner-schedule",
        schedule_sha256="c" * 64,
        seed="synthetic-fixture",
        bootstrap=bucket["kind"] == "anchor",
        next_start=None if bucket["kind"] == "anchor" else 85,
        frame_width_cap=64,
        selection_count=16,
        request_budget=34,
        raw_byte_budget=256_000_000,
        stored_byte_budget=256_000_000,
        minimum_free_disk_bytes=FLOOR,
        capture_storage_codec="raw",
    )


def test_supervisor_runs_synthetic_anchor_then_34_call_frame(tmp_path: Path, host):
    midnight = (host[0] // DAY + 1) * DAY
    start = midnight - 5_000_000_000
    host[0] = start
    root = tmp_path / "lease"
    _init(
        root,
        start,
        [
            {"bucket_id": "anchor", "kind": "anchor", "start_ns": start, "end_ns": midnight},
            {
                "bucket_id": "frame",
                "kind": "frame",
                "start_ns": midnight,
                "end_ns": midnight + 60_000_000_000,
            },
        ],
    )
    outcome = supervisor.advance_once(
        root,
        now_ns=start,
        runner=child_v3.run_epoch,
        clients={"helius-free": FakeRPC(anchor=84)},
        child_root=lambda parent, bucket_id: parent / "children" / bucket_id,
        child_plan=_child_plan,
    )
    assert outcome["decision"]["status"] == "captured_bundle"
    host[0] = midnight
    frame_client = FakeRPC(anchor=102, start=85, end=103)
    outcome = supervisor.advance_once(
        root,
        now_ns=midnight,
        runner=child_v3.run_epoch,
        clients={"helius-free": frame_client},
        child_root=lambda parent, bucket_id: parent / "children" / bucket_id,
        child_plan=_child_plan,
    )
    assert outcome["decision"]["status"] == "captured_bundle"
    assert len(frame_client.calls) == 34
    assert outcome["status"]["sealed_operation_count"] == 2
    assert outcome["status"]["activation_authorized"] is False


def test_missed_anchor_records_gaps_then_later_anchor_is_next(tmp_path: Path, host):
    t = host[0]
    root = tmp_path / "lease"
    _init(
        root,
        t,
        [
            {"bucket_id": "a0", "kind": "anchor", "start_ns": t, "end_ns": t + 10},
            {"bucket_id": "f0", "kind": "frame", "start_ns": t + 10, "end_ns": t + 20},
            {"bucket_id": "a1", "kind": "anchor", "start_ns": t + 20, "end_ns": t + 30},
        ],
    )
    host[0] = t + 25
    report = supervisor.advance_once(root, now_ns=host[0])["status"]
    assert report["next_bucket"]["bucket_id"] == "a1"
    assert report["status"] == "eligible"
    assert [g["bucket_id"] for g in report["gaps"]] == ["a0", "f0"]


def test_status_is_read_only_and_old_snapshot_becomes_stale(tmp_path: Path, host):
    t = host[0]
    root = tmp_path / "lease"
    _init(root, t, [{"bucket_id": "a0", "kind": "anchor", "start_ns": t, "end_ns": t + 10}])
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    snapshot = supervisor.status(root, now_ns=t)
    assert supervisor.verify_status(snapshot, root, now_ns=t) == {
        "valid": True,
        "stale": False,
        "reason": None,
    }
    assert before == {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    host[0] = t + 10
    supervisor.advance_once(root, now_ns=host[0])
    stale = supervisor.verify_status(snapshot, root, now_ns=t)
    assert stale["valid"] is False and stale["stale"] is True


def test_plan_or_cap_mutation_breaks_immutable_binding(tmp_path: Path, host):
    root = tmp_path / "lease"
    plan = _init(root, host[0])
    plan["caps"]["requests"] += 1
    (root / "plan-v2.json").write_bytes(bridge._canonical(plan) + b"\n")
    with pytest.raises(supervisor.SupervisorError, match="pin_or_source_changed"):
        supervisor.status(root, now_ns=host[0])


def test_disk_floor_drop_fails_closed_before_any_operation(tmp_path: Path, host, monkeypatch):
    root = tmp_path / "lease"
    _init(root, host[0])
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _: shutil._ntuple_diskusage(1, 1, 0),
    )
    report = supervisor.status(root, now_ns=host[0])
    assert report["status"] == "disk_floor_failed"
    out = supervisor.advance_once(root, now_ns=host[0])
    assert out["status"]["terminal_reason"] == "disk_floor_failed"
    assert not any(row["kind"] == "reservation" for row in calendar._rows(root))


def test_expiry_is_a_verified_terminal_reason(tmp_path: Path, host):
    root = tmp_path / "lease"
    plan = _init(
        root,
        host[0],
        [{"bucket_id": "a0", "kind": "anchor", "start_ns": host[0], "end_ns": host[0] + 10}],
    )
    host[0] = plan["expires_ns"]
    report = supervisor.advance_once(root, now_ns=host[0])["status"]
    assert report["status"] == "terminal"
    assert report["terminal_reason"] == "lease_expired"
    assert calendar.verify_handoff(root)["valid"]


def test_durable_stop_without_handoff_is_reconciled_without_dispatch(tmp_path: Path, host):
    root = tmp_path / "lease"
    _init(root, host[0])
    bridge._stop(root, "synthetic-stop", "unknown", "fixture_stop")
    before = supervisor.status(root, now_ns=host[0])
    assert before["status"] == "stopped"
    assert before["terminal_reason"] == "fixture_stop"
    assert not any(row["kind"] == "reservation" for row in calendar._rows(root))
    reconciled = supervisor.advance_once(root, now_ns=host[0])["status"]
    assert reconciled["status"] == "terminal"
    assert reconciled["terminal_reason"] == "stopped"
    assert calendar.verify_handoff(root)["valid"]
    assert not any(row["kind"] == "reservation" for row in calendar._rows(root))


def test_source_pin_mismatch_and_rebinding_are_rejected(tmp_path: Path, host):
    root = tmp_path / "lease"
    _init(root, host[0])
    with pytest.raises(supervisor.SupervisorError, match="caller_pin_or_contract_mismatch"):
        supervisor.bind(
            root,
            expected_plan_sha256="0" * 64,
            expected_source_sha256=supervisor.source_hashes(),
        )
    binding_path = root / supervisor.BINDING
    binding = json.loads(binding_path.read_bytes())
    binding["plan_sha256"] = "f" * 64
    binding_path.write_bytes(bridge._canonical(binding) + b"\n")
    with pytest.raises(supervisor.SupervisorError, match="pin_or_source_changed"):
        supervisor.status(root, now_ns=host[0])


@pytest.mark.parametrize(
    "crash_at",
    [
        "after_reservation",
        "after_start_before_dispatch",
        "after_capture_fsync",
        "before_parent_seal",
    ],
)
def test_crash_prefixes_stop_without_replay(tmp_path: Path, host, crash_at: str):
    root = tmp_path / "lease"
    plan = _init(
        root,
        host[0],
        [{"bucket_id": "anchor", "kind": "anchor", "start_ns": host[0], "end_ns": host[0] + DAY}],
    )
    child_root = root / "children" / "anchor"
    fake = FakeRPC(anchor=84)
    bridge.reserve_and_run(
        root,
        bucket_id="anchor",
        operation_id="crashed-anchor",
        child_root=child_root,
        child_plan=_child_plan(root, plan["buckets"][0], "anchor"),
        runner=child_v3.run_epoch,
        clients={"helius-free": fake},
        crash_at=crash_at,
    )
    calls_before = len(fake.calls)
    outcome = supervisor.advance_once(root, now_ns=host[0])
    assert outcome["status"]["terminal_reason"] in {
        "stopped",
        "abnormal_unsealed_prefix",
    }
    assert len(fake.calls) == calls_before
    assert outcome["status"]["unknown_intervals"]


def test_concurrent_supervisor_invocation_is_excluded_by_shared_calendar_lock(
    tmp_path: Path, host, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "lease"
    _init(root, host[0])
    entered = threading.Event()
    release = threading.Event()
    original = calendar._drive_once

    def hold(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(calendar, "_drive_once", hold)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(supervisor.advance_once, root, now_ns=host[0])
        assert entered.wait(timeout=5)
        with pytest.raises(calendar.CalendarError, match="writer_already_active"):
            supervisor.advance_once(root, now_ns=host[0])
        release.set()
        assert first.result(timeout=5)["decision"]["status"] == "eligible"


def test_default_clock_is_sampled_after_lock_across_bucket_boundary(
    tmp_path: Path, host, monkeypatch: pytest.MonkeyPatch
):
    t = host[0]
    root = tmp_path / "lease"
    _init(
        root,
        t,
        [
            {"bucket_id": "a0", "kind": "anchor", "start_ns": t, "end_ns": t + 10},
            {"bucket_id": "a1", "kind": "anchor", "start_ns": t + 10, "end_ns": t + 20},
        ],
    )

    @contextmanager
    def cross_boundary(_root):
        host[0] = t + 10
        yield None

    monkeypatch.setattr(calendar, "_driver_lock", cross_boundary)
    result = supervisor.advance_once(root)
    assert result["decision"] == {"status": "eligible", "bucket_id": "a1", "kind": "anchor"}
    assert [
        (row["bucket_id"], row["kind"]) for row in calendar._rows(root) if row["kind"] == "gap"
    ] == [("a0", "gap")]


def test_injected_clock_older_than_post_lock_wall_is_rejected_without_mutation(
    tmp_path: Path, host
):
    root = tmp_path / "lease"
    _init(root, host[0])
    before = (root / "events-v2.jsonl").read_bytes()
    host[0] += 1
    with pytest.raises(supervisor.SupervisorError, match="injected_clock_must_match_post_lock"):
        supervisor.advance_once(root, now_ns=host[0] - 1)
    assert (root / "events-v2.jsonl").read_bytes() == before


def test_post_operation_wall_regression_is_reported_without_clamping(
    tmp_path: Path, host, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "lease"
    _init(root, host[0])
    original = calendar._drive_once

    def regress_after_decision(*args, **kwargs):
        decision = original(*args, **kwargs)
        host[0] -= 1
        return decision

    monkeypatch.setattr(calendar, "_drive_once", regress_after_decision)
    outcome = supervisor.advance_once(root, now_ns=host[0])
    assert outcome["decision"]["status"] == "eligible"
    assert outcome["status"]["status"] == "clock_fault"
    assert outcome["status"]["terminal_reason"] == "utc_clock_regressed"


def test_post_clock_before_decision_is_fault_even_after_last_durable_time(
    tmp_path: Path, host, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "lease"
    t = host[0]
    _init(
        root,
        t,
        [{"bucket_id": "a0", "kind": "anchor", "start_ns": t + 100, "end_ns": t + DAY}],
    )
    values = iter((t + 10, t + 5))
    monkeypatch.setattr(supervisor.time, "time_ns", lambda: next(values))
    outcome = supervisor.advance_once(root, now_ns=t + 10)
    assert outcome["decision"]["status"] == "waiting"
    assert outcome["status"]["evaluated_at_ns"] == t + 5
    assert outcome["status"]["status"] == "clock_fault"
    assert outcome["status"]["terminal_reason"] == "utc_clock_regressed"
    assert outcome["status"]["post_operation_clock_regressed"] is True
    assert outcome["status"]["decision_at_ns"] == t + 10
    assert supervisor.verify_status(outcome["status"], root, now_ns=t + 5)["valid"] is True


def test_no_live_client_factory_or_secret_path_in_supervisor_source_or_status(tmp_path: Path, host):
    root = tmp_path / "lease"
    _init(root, host[0])
    source = Path(supervisor.__file__).read_text()
    assert "ClientSession" not in source
    assert "AsyncClient(" not in source
    assert "os.environ" not in source
    report = supervisor.status(root, now_ns=host[0])
    encoded = json.dumps(report, sort_keys=True)
    assert "credential" not in encoded.lower()
    assert "api_key" not in encoded.lower()
    assert report["activation_authorized"] is False


def test_cli_is_read_only_status_and_has_no_execution_arguments(tmp_path: Path, host, capsys):
    root = tmp_path / "lease"
    _init(root, host[0])
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    assert supervisor.main([str(root), "--now-ns", str(host[0])]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "eligible"
    assert report["activation_authorized"] is False
    assert before == {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}


def test_supervisor_process_restart_verifies_capture_and_never_replays_started_child(
    tmp_path: Path,
):
    now = time.time_ns()
    root = tmp_path / "lease"
    _init(
        root,
        now,
        [
            {"bucket_id": "anchor", "kind": "anchor", "start_ns": now, "end_ns": now + DAY},
            {
                "bucket_id": "frame",
                "kind": "frame",
                "start_ns": now + DAY,
                "end_ns": now + 2 * DAY,
            },
        ],
    )
    decision_ns = max(now, supervisor._latest_durable_time(calendar._rows(root))) + 1_000_000_000
    source = r"""
import importlib.util, json, os, shutil, sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "supervisor_test_fixture", sys.argv[4]
)
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)

root = Path(sys.argv[1])
now = int(sys.argv[2])
action = sys.argv[3]
t.supervisor.time.time_ns = lambda: now
t.shutil.disk_usage = lambda _: shutil._ntuple_diskusage(
    300 * 1024**3, 100 * 1024**3, 200 * 1024**3
)

if action == "capture-anchor":
    client = t.FakeRPC(anchor=84)
    result = t.supervisor.advance_once(
        root,
        runner=t.child_v3.run_epoch,
        clients={"helius-free": client},
        child_root=lambda parent, bucket_id: parent / "children" / bucket_id,
        child_plan=t._child_plan,
    )
    print(json.dumps({"decision": result["decision"], "calls": len(client.calls)}))
elif action == "inspect":
    result = t.supervisor.status(root, now_ns=now)
    check = t.supervisor.verify_status(result, root, now_ns=now)
    print(
        json.dumps(
            {
                "status": result["status"],
                "sealed": result["sealed_operation_count"],
                "check": check,
            }
        )
    )
elif action == "crash-frame-before-dispatch":
    plan = t.calendar._plan(root)
    bucket = plan["buckets"][1]
    client = t.FakeRPC(anchor=100)
    result = t.bridge.reserve_and_run(
        root,
        bucket_id=bucket["bucket_id"],
        operation_id="process-crash-frame",
        child_root=root / "children" / bucket["bucket_id"],
        child_plan=t._child_plan(root, bucket, bucket["bucket_id"]),
        runner=t.child_v3.run_epoch,
        clients={"helius-free": client},
        crash_at="after_start_before_dispatch",
    )
    assert result["status"] == "crash_injected_after_start_before_child_dispatch"
    assert client.calls == []
    os._exit(73)
elif action == "restart-no-replay":
    client = t.FakeRPC(anchor=100)
    result = t.supervisor.advance_once(
        root,
        runner=t.child_v3.run_epoch,
        clients={"helius-free": client},
        child_root=lambda parent, bucket_id: parent / "children" / bucket_id,
        child_plan=t._child_plan,
    )
    print(
        json.dumps(
            {
                "status": result["status"]["status"],
                "decision": result["decision"],
                "calls": len(client.calls),
                "unknown": result["status"]["unknown_intervals"],
                "terminal_reason": result["status"]["terminal_reason"],
                "handoff_valid": t.calendar.verify_handoff(root)["valid"],
            }
        )
    )
else:
    raise AssertionError("unknown test action")
"""

    def invoke(action: str, clock_ns: int) -> dict:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                source,
                str(root),
                str(clock_ns),
                action,
                str(Path(__file__).resolve()),
            ],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            check=False,
            text=True,
            timeout=45,
        )
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout)

    captured = invoke("capture-anchor", decision_ns)
    assert captured["decision"]["status"] == "captured_bundle"
    assert captured["calls"] > 0

    reopened = invoke("inspect", decision_ns)
    assert reopened["status"] == "waiting"
    assert reopened["sealed"] == 1
    assert reopened["check"] == {"valid": True, "stale": False, "reason": None}

    frame_clock_ns = decision_ns + DAY
    crashed = subprocess.run(
        [
            sys.executable,
            "-c",
            source,
            str(root),
            str(frame_clock_ns),
            "crash-frame-before-dispatch",
            str(Path(__file__).resolve()),
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert crashed.returncode == 73

    recovered = invoke("restart-no-replay", frame_clock_ns)
    assert recovered["status"] == "terminal"
    assert recovered["calls"] == 0
    assert recovered["unknown"]
    assert recovered["terminal_reason"] == "abnormal_unsealed_prefix"
    assert recovered["handoff_valid"] is True
