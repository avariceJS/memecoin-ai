from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from memecoin_ml.data import read, sha, write
from memecoin_operational.contract import FLOOR, RESERVE, GateError, authorize
from memecoin_operational.supervisor import Watch, child, disk_guard


def test_clock_jumps_and_suspend():
    w, m = [100.0], [20.0]
    watch = Watch(600, wall=lambda: w[0], mono=lambda: m[0])
    w[0] += 1
    m[0] += 1
    assert watch.remaining() == 599
    w[0] += 10
    with pytest.raises(GateError, match="suspend"):
        watch.remaining()
    w, m = [100.0], [20.0]
    watch = Watch(600, wall=lambda: w[0], mono=lambda: m[0])
    w[0] -= 0.001
    with pytest.raises(GateError, match="clock"):
        watch.remaining()


def test_disk_floor_size_and_symlink(tmp_path, monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "disk_usage", lambda _: SimpleNamespace(free=FLOOR + RESERVE - 1))
    with pytest.raises(GateError, match="disk_headroom"):
        disk_guard(tmp_path, floor=FLOOR, limit=RESERVE)
    monkeypatch.setattr(shutil, "disk_usage", lambda _: SimpleNamespace(free=100000))
    (tmp_path / "large").write_bytes(b"12345")
    with pytest.raises(GateError, match="storage_ceiling"):
        disk_guard(tmp_path, floor=1, limit=4)
    (tmp_path / "symlink").symlink_to(tmp_path / "large")
    with pytest.raises(GateError, match="symlink"):
        disk_guard(tmp_path, floor=1, limit=100)


def test_deadline_really_kills_worker_preserves_raw(tmp_path):
    script = "import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text('raw'); time.sleep(60)"
    result = child(
        [sys.executable, "-c", script, str(tmp_path / "raw")], tmp_path / "log", 0.25, lambda: None
    )
    assert result["category"] == "child_deadline" and result["exit_code"] != 0
    assert (tmp_path / "raw").read_text() == "raw"


def test_guard_failure_before_spawn_and_during_child(tmp_path):
    def fail():
        raise GateError("runtime_tampered")

    with pytest.raises(GateError, match="tampered"):
        child([sys.executable, "-c", "pass"], tmp_path / "before", 1, fail)
    assert not (tmp_path / "before").exists()
    n = [0]

    def gate():
        n[0] += 1
        if n[0] > 2:
            raise GateError("disk_headroom")

    r = child([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path / "during", 2, gate)
    assert r["category"] == "disk_headroom"


def test_orphan_worker_stops_on_lease_eof(tmp_path):
    # A real parent dies abruptly; its child may no longer produce heartbeat bytes.
    child_script = (
        "import os,sys,time; from pathlib import Path; "
        "from memecoin_operational.worker import lease_watchdog; "
        "lease_watchdog(int(os.environ['MEMECOIN_LEASE_FD'])); "
        "p=Path(sys.argv[1]); p.write_text('started'); "
        "time.sleep(1); p.write_text('ORPHAN_CONTINUED')"
    )
    parent_script = (
        "import os,subprocess,sys,time; "
        "r,w=os.pipe(); env=dict(os.environ,MEMECOIN_LEASE_FD=str(r)); "
        "p=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],env=env,pass_fds=(r,)); "
        "time.sleep(.3); os._exit(0)"
    )
    env = dict(os.environ, PYTHONPATH=str(Path("src").resolve()))
    subprocess.run(
        [sys.executable, "-c", parent_script, child_script, str(tmp_path / "heartbeat")],
        env=env,
        check=True,
        timeout=3,
    )
    time.sleep(1.1)
    assert (tmp_path / "heartbeat").read_text() == "started"


def test_receipt_does_not_follow_zero_usd_summary(tmp_path, monkeypatch):
    import memecoin_operational.contract as c
    from memecoin_collector.qualification import identity

    config = dict(
        mode="prospective", credential_sha=identity("fixture-secret"), proposal_sha="proposal"
    )
    monkeypatch.setattr(c, "check_bundle", lambda *_: config)
    with pytest.raises(GateError, match="decision_required"):
        authorize(
            tmp_path, "bundle", None, None, scope="calendar", credential="fixture-secret", now_ns=10
        )
    path = tmp_path / "receipt"
    write(
        path,
        dict(
            scope="calendar",
            bundle_sha="bundle",
            proposal_sha="proposal",
            decision="approved",
            decision_reference="fixture-only",
            budget_review=dict(usd="0.00"),
        ),
    )
    with pytest.raises(GateError, match="unreviewed"):
        authorize(
            tmp_path,
            "bundle",
            path,
            sha(path),
            scope="calendar",
            credential="fixture-secret",
            now_ns=10,
        )


def test_receipt_expiry_rotation_quota_and_pins(tmp_path, monkeypatch):
    import memecoin_operational.contract as c
    from memecoin_collector.qualification import identity

    config = dict(
        mode="prospective", credential_sha=identity("fixture-secret"), proposal_sha="proposal"
    )
    monkeypatch.setattr(c, "check_bundle", lambda *_: config)
    evidence = tmp_path / "synthetic-proof"
    evidence.write_text("NOT REAL BILLING EVIDENCE")
    review = dict(
        credential_sha=config["credential_sha"],
        plan="free",
        paid_limit_usd=0,
        no_automatic_paid_overage=True,
        account_id="fixture",
        review_reference="fixture-only",
        mapping_evidence_sha=sha(evidence),
        mapping_evidence_path=str(evidence),
        billing_evidence_sha=sha(evidence),
        billing_evidence_path=str(evidence),
        observed_ns=1,
        valid_until_ns=20,
        reserved_available_cu=1209600,
    )
    receipt = dict(
        scope="calendar",
        bundle_sha="bundle",
        proposal_sha="proposal",
        decision="approved",
        decision_reference="fixture-only",
        budget_review=review,
        max_rpc=60480,
    )
    path = tmp_path / "receipt"
    write(path, receipt)
    authorize(
        tmp_path,
        "bundle",
        path,
        sha(path),
        scope="calendar",
        credential="fixture-secret",
        now_ns=10,
    )
    with pytest.raises(GateError, match="expired"):
        authorize(
            tmp_path,
            "bundle",
            path,
            sha(path),
            scope="calendar",
            credential="fixture-secret",
            now_ns=20,
        )
    with pytest.raises(GateError, match="unreviewed"):
        authorize(
            tmp_path, "bundle", path, sha(path), scope="calendar", credential="rotation", now_ns=10
        )
    with pytest.raises(GateError, match="unreviewed"):
        authorize(
            tmp_path,
            "bundle",
            path,
            sha(path),
            scope="bounded_qualification",
            credential="fixture-secret",
            now_ns=10,
        )
    review["reserved_available_cu"] = 1
    path.write_text(json.dumps(receipt))
    with pytest.raises(GateError, match="quota"):
        authorize(
            tmp_path,
            "bundle",
            path,
            sha(path),
            scope="calendar",
            credential="fixture-secret",
            now_ns=10,
        )


def test_supervisor_wires_two_segment_caps_and_total(tmp_path, monkeypatch):
    import memecoin_operational.supervisor as s

    c = dict(mode="synthetic_fixture", free_floor=0, window_bytes=10000000)
    monkeypatch.setattr(s, "check_bundle", lambda *_: c)
    monkeypatch.setattr(s, "disk_guard", lambda *a, **kw: None)
    ticks = [0.0]
    monkeypatch.setattr(
        s, "Watch", lambda _: Watch(600, wall=lambda: ticks[0], mono=lambda: ticks[0])
    )
    res = dict(index=0, started_ns=1000, reserved_rpc=180)
    write(tmp_path / "reservation.json", res)
    caps = []

    def fake(command, log, seconds, guard):
        guard()
        caps.append(seconds)
        job = read(Path(command[-1]))
        ticks[0] += 170
        write(
            tmp_path / (job["phase"] + "-result.json"), dict(status="completed", finished_ns=1001)
        )
        return dict(exit_code=0, category="completed")

    monkeypatch.setattr(s, "child", fake)
    result = s.window(tmp_path, "pin", tmp_path, res)
    # init170 + segments340 =>90s left for QA; total exceed cannot succeed.
    assert caps == [600, 210, 210, 90]
    assert result["status"] == "failed"  # Final wall guard must also run after last child.


def test_kill_process_group_includes_descendant(tmp_path):
    descendant = (
        "import time,pathlib,sys; time.sleep(1); pathlib.Path(sys.argv[1]).write_text('leaked')"
    )
    parent = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); time.sleep(60)"
    )
    r = child(
        [sys.executable, "-c", parent, descendant, str(tmp_path / "leak")],
        tmp_path / "process-group.log",
        0.25,
        lambda: None,
    )
    assert r["category"] == "child_deadline"
    time.sleep(1.1)
    assert not (tmp_path / "leak").exists()


def test_bundle_rehashed_code_change_and_separate_activation(tmp_path, monkeypatch):
    import memecoin_operational.contract as c
    from memecoin_registry.contracts import inventory
    from memecoin_sampling.contract import freeze

    proposal = tmp_path / "proposal"
    spec = read(Path("configs/research-f01-v1.json"))
    freeze(
        spec,
        proposal,
        anchor_ns=1791331200000000000,
        now_ns=1791040000000000000,
        source_root=tmp_path / "source",
    )
    pools = spec["source_semantics"]["campaign"]["pools"]

    class Dummy:
        def __init__(self, i):
            self.request_json = json.dumps(dict(params=[[pools[i]]]))

        @staticmethod
        def load(p):
            return Dummy(int(p.name))

        def save(self, p):
            p.mkdir(parents=True)
            write(p / "fixture.json", dict(synthetic=True))

    monkeypatch.setattr(c, "Capture", Dummy)
    bundle = tmp_path / "bundle"
    c.stage(proposal, bundle, [tmp_path / str(i) for i in range(3)], mode="synthetic_fixture")
    pin = sha(bundle / "manifest.json")
    c.check_bundle(bundle, pin)
    path = bundle / "runtime/memecoin_operational/worker.py"
    path.write_text(path.read_text() + "\n# mutation\n")
    with pytest.raises(ValueError):
        c.check_bundle(bundle, pin)
    (bundle / "manifest.json").write_text(json.dumps(inventory(bundle, exclude="manifest.json")))
    with pytest.raises(GateError, match="pin_changed"):
        c.check_bundle(bundle, pin)
    with pytest.raises(GateError, match="runtime_contract_changed"):
        c.check_bundle(bundle, sha(bundle / "manifest.json"))
    assert not (tmp_path / "source").exists()


def test_worker_failure_does_not_start_next_phase(tmp_path, monkeypatch):
    import memecoin_operational.supervisor as s

    monkeypatch.setattr(
        s,
        "check_bundle",
        lambda *_: dict(mode="synthetic_fixture", free_floor=0, window_bytes=10000),
    )
    monkeypatch.setattr(s, "disk_guard", lambda *a, **k: None)
    res = dict(index=0, started_ns=100, reserved_rpc=180)
    write(tmp_path / "reservation.json", res)
    calls = []

    def fake(command, log, seconds, guard):
        job = read(Path(command[-1]))
        calls.append(job["phase"])
        if job["phase"] == "segment0":
            return dict(exit_code=1, category="worker_failed")
        write(tmp_path / (job["phase"] + "-result.json"), dict(status="completed", finished_ns=101))
        return dict(exit_code=0, category="completed")

    monkeypatch.setattr(s, "child", fake)
    result = s.window(tmp_path, "pin", tmp_path, res)
    assert result["status"] == "failed" and calls == ["init", "segment0"]
    assert not (tmp_path / "segment1-job.json").exists()


def test_unreviewed_live_window_sends_nothing(tmp_path, monkeypatch):
    import memecoin_operational.supervisor as s

    monkeypatch.setattr(s, "check_bundle", lambda *_: dict(mode="prospective"))
    res = dict(index=0, started_ns=100, reserved_rpc=180)
    write(tmp_path / "reservation.json", res)
    with pytest.raises(GateError, match="live_guard_required"):
        s.window(tmp_path, "pin", tmp_path, res)
    assert not (tmp_path / "init-job.json").exists()


def test_one_receipt_cannot_fund_second_qualification_directory(tmp_path):
    from memecoin_operational.contract import check_attempt

    attempt = tmp_path / "first"
    attempt.mkdir()
    c = dict(proposal_sha="p")
    r = dict(attempt_root=str(attempt))
    write(attempt / "reservation.json", dict(plan_sha256="p", reserved_rpc=180))
    check_attempt(tmp_path, c, r, attempt, "bounded_qualification")
    # Denied before any read of a different attempt root.
    with pytest.raises(GateError, match="attempt_reuse"):
        check_attempt(tmp_path, c, r, tmp_path / "second", "bounded_qualification")


def test_calendar_attempt_due_identity_before_rpc(tmp_path, monkeypatch):
    import memecoin_operational.contract as c

    due = 1000000000000
    monkeypatch.setattr(c, "load", lambda _: (dict(windows=[dict(due_ns=due)]), {}))
    root = tmp_path / "windows/000"
    root.mkdir(parents=True)
    cfg = dict(proposal_sha="p", source_root=str(tmp_path))
    res = dict(plan_sha256="p", reserved_rpc=180, index=0, due_ns=due, started_ns=due)
    path = root / "reservation.json"
    write(path, res)
    c.check_attempt(tmp_path, cfg, {}, root, "calendar")
    res["started_ns"] += 901 * 10**9
    path.write_text(json.dumps(res))
    with pytest.raises(GateError, match="binding_changed"):
        c.check_attempt(tmp_path, cfg, {}, root, "calendar")
