from __future__ import annotations

import fcntl
import json
import sys
from pathlib import Path

import pytest

from memecoin_collector.process import run_child
from memecoin_collector.qualification import economics, endpoint
from memecoin_collector.scheduler import action, run_due
from memecoin_ml.data import read
from memecoin_sampling.contract import NS, make_binding


@pytest.fixture
def binding(tmp_path):
    return make_binding(
        read(Path("configs/research-f01-v1.json")),
        anchor_ns=1791331200000000000,
        created_ns=1791040000000000000,
        source_root=tmp_path / "source",
    )


def invoke(binding, now, runner):
    return run_due(
        Path(binding["source_root"]), binding, "proposal-sha", now=lambda: now, runner=runner
    )


def test_due_boundary_and_no_catchup(binding):
    due = binding["windows"][0]["due_ns"]
    assert action(due, due - 1) == "pending"
    assert action(due, due + 900 * NS) == "collect"
    assert action(due, due + 900 * NS + 1) == "missed"

    def never(*_):
        pytest.fail("network on missed/pending window")

    assert invoke(binding, due - 1, never) == []
    rows = invoke(binding, due + 901 * NS, never)
    assert len(rows) == 1 and rows[0]["reserved_rpc"] == 0
    assert invoke(binding, due + 902 * NS, never) == rows


def test_reservation_before_work_and_idempotency(binding):
    due = binding["windows"][0]["due_ns"]

    def runner(root, reservation):
        assert read(root / "reservation.json") == reservation
        assert reservation["reserved_rpc"] == 180
        assert reservation["plan_sha256"] == "proposal-sha"
        return dict(status="completed")

    rows = invoke(binding, due, runner)
    assert rows[0]["status"] == "completed"
    assert invoke(binding, due, lambda *_: pytest.fail("retry")) == rows


def test_crash_is_full_charge_no_retry(binding):
    due = binding["windows"][0]["due_ns"]

    def crash(root, _):
        (root / "partial-raw").write_bytes(b"original")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        invoke(binding, due, crash)
    rows = invoke(binding, due + NS, lambda *_: pytest.fail("crash retried"))
    assert rows[0]["status"] == "failed" and rows[0]["reserved_rpc"] == 180
    assert (Path(binding["source_root"]) / "windows/000/partial-raw").read_bytes() == b"original"


def test_eight_failures_close_schedule(binding):
    calls = []

    def fail(root, _):
        calls.append(root)
        raise TimeoutError("credential must not appear in ledger")

    for slot in binding["windows"][:9]:
        rows = invoke(binding, slot["due_ns"], fail)
    assert len(calls) == len(rows) == 8
    assert sum(r["reserved_rpc"] for r in rows) == 1440
    assert "credential" not in json.dumps(rows)


def test_clock_binding_and_journal_tamper_fail_before_worker(binding):
    due = binding["windows"][0]["due_ns"]
    invoke(binding, due, lambda *_: dict(status="completed"))

    def never(*_):
        pytest.fail("unexpected worker")

    with pytest.raises(ValueError, match="clock_regression"):
        invoke(binding, due - 1, never)
    original = binding["windows"][0]["due_ns"]
    binding["windows"][0]["due_ns"] += 1
    with pytest.raises(ValueError, match="binding_changed"):
        invoke(binding, due, never)
    binding["windows"][0]["due_ns"] = original
    path = Path(binding["source_root"]) / "journal/000.json"
    row = read(path)
    row["status"] = "failed"
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError, match="chain_changed"):
        invoke(binding, due, never)


def test_stop_and_writer_exclusion(binding):
    root = Path(binding["source_root"])
    root.mkdir()
    (root / "STOP").touch()

    def never(*_):
        pytest.fail("worker after STOP")

    assert invoke(binding, binding["windows"][0]["due_ns"], never) == []
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            invoke(binding, binding["windows"][0]["due_ns"], never)


def test_late_worker_not_success(binding):
    due = binding["windows"][0]["due_ns"]
    clock = [due]

    def slow(*_):
        clock[0] += 601 * NS
        return dict(status="completed")

    result = run_due(
        Path(binding["source_root"]), binding, "proposal-sha", now=lambda: clock[0], runner=slow
    )
    assert result[0]["status"] == "failed"


def test_real_kill_boundary_preserves_partial_bytes(tmp_path):
    partial = tmp_path / "partial"
    script = "import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text('kept'); time.sleep(60)"
    code = run_child(
        [sys.executable, "-c", script, str(partial)], tmp_path / "child.log", timeout=0.5
    )
    assert code == 124 and partial.read_text() == "kept"
    assert run_child([sys.executable, "-c", "pass"], tmp_path / "good.log", timeout=2) == 0


def test_exact_monthly_costs_and_unknown_monetary_gate(binding):
    budget = economics(binding)
    assert [v["cu"] for v in budget["monthly"].values()] == [360000, 432000, 417600]
    assert sum(v["rpc"] for v in budget["monthly"].values()) == 60480
    assert budget["actual_future_usd"] is None and budget["authorized_paid_usd"] == 0
    assert budget["source_plus_registry_raw_upper_bytes"] == 100800000000


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://solana-mainnet.g.alchemy.com/v2/x",
        "https://other.invalid/v2/x",
        "https://solana-mainnet.g.alchemy.com/v2/",
    ],
)
def test_invalid_credentials_safe(tmp_path, url):
    path = tmp_path / "settings"
    path.write_text("MEMECOIN_ALCHEMY_HTTP_URL=" + url)
    with pytest.raises(ValueError, match="runtime"):
        endpoint(path)


def test_activation_exact_binding_is_not_authorization(tmp_path, binding, monkeypatch):
    import memecoin_collector.qualification as q
    from memecoin_ml.data import write
    from memecoin_registry.contracts import inventory
    from memecoin_sampling.contract import freeze

    proposal, output = tmp_path / "proposal", tmp_path / "candidate"
    freeze(
        read(Path("configs/research-f01-v1.json")),
        proposal,
        anchor_ns=binding["anchor_ns"],
        source_root=Path(binding["source_root"]),
        now_ns=binding["created_ns"],
    )
    pools = binding["source_semantics"]["campaign"]["pools"]

    class DummyCapture:
        def __init__(self, index):
            self.request_json = json.dumps(dict(params=[[pools[index]]]))

        @staticmethod
        def load(path):
            return DummyCapture(int(path.name))

        def save(self, path):
            path.mkdir()
            write(path / "fixture.json", dict(explicit_synthetic=True))

    monkeypatch.setattr(q, "Capture", DummyCapture)
    monkeypatch.setattr(q, "runtime_identity", lambda: dict(code="version1"))
    evidence = tmp_path / "evidence.json"
    write(evidence, dict(plan=None))
    credential = "synthetic-secret-not-real"
    result = q.stage(
        proposal, output, [tmp_path / str(i) for i in range(3)], credential, [evidence]
    )
    assert not result["collection_authorized"] and result["activation_decision"] is None
    assert credential not in (output / "candidate.json").read_text()
    q.verify_candidate(output, credential)
    with pytest.raises(ValueError, match="binding_changed"):
        q.verify_candidate(output, "rotated")
    evidence.write_text("{}")
    with pytest.raises(ValueError, match="binding_changed"):
        q.verify_candidate(output, credential)
    evidence.unlink()
    write(evidence, dict(plan=None))
    monkeypatch.setattr(q, "runtime_identity", lambda: dict(code="version2"))
    with pytest.raises(ValueError, match="binding_changed"):
        q.verify_candidate(output, credential)
    monkeypatch.setattr(q, "runtime_identity", lambda: dict(code="version1"))
    result["collection_authorized"] = True
    (output / "candidate.json").write_text(json.dumps(result))
    (output / "manifest.json").write_text(json.dumps(inventory(output, exclude="manifest.json")))
    with pytest.raises(ValueError, match="binding_changed"):
        q.verify_candidate(output, credential)


def test_first_segment_failure_never_calls_second(tmp_path, monkeypatch):
    import memecoin_collector.window as w
    from memecoin_collector.scheduler import durable

    calls = []
    durable(tmp_path / "reservation.json", dict(index=0, started_ns=1000, reserved_rpc=180))
    monkeypatch.setattr(w, "create_campaign", lambda *a, **kw: None)

    def fail(*args, **kwargs):
        calls.append(1)
        return dict(segments=[dict(report=dict(complete=False))])

    monkeypatch.setattr(w, "resume_campaign", fail)
    result = w.collect_window(tmp_path, [], None, data_kind="synthetic_fixture", clock=lambda: 1001)
    assert result["status"] == "failed" and len(calls) == 1
    assert read(tmp_path / "seal.json")["outcome"]["status"] == "failed"


def test_partial_journal_write_is_not_repaired_or_retried(binding):
    root = Path(binding["source_root"])
    (root / "journal").mkdir(parents=True)
    (root / "journal/000.json").write_text('{"unfinished":')
    with pytest.raises(ValueError):
        invoke(binding, binding["windows"][0]["due_ns"], lambda *_: pytest.fail("retry"))
    assert (root / "journal/000.json").read_text() == '{"unfinished":'
