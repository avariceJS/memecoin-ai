import errno
import json

import pytest

from memecoin_ml.data import read
from memecoin_paper import supervision as s
from memecoin_paper.audit import replay
from memecoin_paper.engine import advance, initial
from memecoin_paper.projection import save
from memecoin_registry.contracts import digest
from memecoin_runner.ledger import Ledger, publish


def test_safe_guard_diagnostics_never_contain_exception_text():
    def broken():
        raise OSError(errno.EIO, "https://secret-token.invalid/private")

    with pytest.raises(s.GuardFailure) as caught:
        s.checked("source_inventory_binding", broken)
    d = caught.value.detail
    assert d == dict(
        stage="source_inventory_binding", error_type="OSError", errno=5, reason="unclassified"
    )
    assert "secret-token" not in json.dumps(d)
    assert s.safe_error(ValueError("runtime_pin"), "paper")["reason"] == "runtime_pin"


def make_ledger(root, cache=True):
    state = initial()
    tick = dict(
        at_ns=320 * 10**9, activation_ns=310 * 10**9, market=[], predictions=[], source_ok=True
    )
    with Ledger(root / "ledger", dict(fixture=True)) as ledger:
        state, records = advance(state, tick)
        ledger.append("tick", tick=tick, result_sha=digest(dict(state=state, records=records)))
        if cache:
            save(root, ledger.events, state, records, records)
        tick["at_ns"] += 10**9
        state, records = advance(state, tick)
        ledger.append("tick", tick=tick, result_sha=digest(dict(state=state, records=records)))
        # Simulate kill after commit, before the next projection/dashboard publication.


@pytest.mark.parametrize("cache", [False, True])
def test_signal_recovery_replays_tail_and_never_changes_history(tmp_path, cache):
    make_ledger(tmp_path, cache)
    before = replay(tmp_path)
    error = dict(category="worker_failed", exit_code=-9)
    incident = s.incidents(tmp_path, "paper", error)
    assert s.reserve(tmp_path, "paper", error, incident, 100)
    assert not s.ready(tmp_path, 60 * 10**9)
    assert s.ready(tmp_path, 60 * 10**9 + 100)
    assert replay(tmp_path) == before
    s.complete(tmp_path, 61 * 10**9)
    assert s.pending(tmp_path) is None
    # A restart sees consumed allowance even after successful recovery.
    assert len(s.reservations(tmp_path)) == 1
    assert s.reserve(tmp_path, "paper", error, incident, 70 * 10**9)
    assert not s.reserve(tmp_path, "paper", error, incident, 80 * 10**9)
    assert len(s.reservations(tmp_path)) == 2


def test_corrupt_checkpoint_and_ledger_fail_closed_on_recovery(tmp_path):
    make_ledger(tmp_path)
    error = dict(category="worker_failed", exit_code=-15)
    incident = s.incidents(tmp_path, "paper", error)
    assert s.reserve(tmp_path, "paper", error, incident, 0)
    (tmp_path / "projection.json").write_text("{}")
    with pytest.raises((ValueError, KeyError)):
        s.ready(tmp_path, 61 * 10**9)
    (tmp_path / "projection.json").unlink()
    e = tmp_path / "ledger/event-000001.json"
    value = read(e)
    value["payload"]["tick"]["source_ok"] = False
    e.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="ledger_chain_invalid"):
        s.ready(tmp_path, 61 * 10**9)


@pytest.mark.parametrize(
    "phase,category,code",
    [
        ("train", "worker_failed", -9),
        ("paper", "supervisor_guard", -9),
        ("paper", "paper_guard_failure", -9),
        ("paper", "worker_failed", 1),
        ("paper", "clock_jump_or_suspend", -9),
        ("paper", "child_deadline", -9),
    ],
)
def test_unknown_integrity_training_and_deadlines_are_not_retried(tmp_path, phase, category, code):
    assert not s.reserve(
        tmp_path, phase, dict(category=category, exit_code=code), tmp_path / "incident", 0
    )


def test_partial_model_is_not_exposed(tmp_path):
    from memecoin_ml.data import sha
    from memecoin_paper.service import models
    from memecoin_registry.contracts import seal

    p = tmp_path / "training/000000"
    (p / "model").mkdir(parents=True)
    seal(p / "model")
    assert models(tmp_path) == []
    publish(p / "result.json", dict(status="completed", model_sha=sha(p / "model/manifest.json")))
    assert models(tmp_path) == []
    seal(p)
    assert models(tmp_path) == [p / "model"]


def test_new_candidate_waits_for_atomic_publication_after_full_seal(tmp_path):
    from memecoin_ml.data import sha
    from memecoin_paper.service import models
    from memecoin_registry.contracts import seal

    p = tmp_path / "training/000000"
    (p / "model").mkdir(parents=True)
    seal(p / "model")
    publish(p / "reservation.json", dict(candidate_publication=2))
    publish(p / "result.json", dict(status="completed", model_sha=sha(p / "model/manifest.json")))
    seal(p)
    assert models(tmp_path) == []
    (tmp_path / "candidate-ready").mkdir()
    publish(
        tmp_path / "candidate-ready/000000.json", dict(run_manifest_sha=sha(p / "manifest.json"))
    )
    assert models(tmp_path) == [p / "model"]


def test_real_child_sigkill_after_commit_has_exact_recovery(tmp_path):
    import sys

    from memecoin_operational.supervisor import child

    script = """
import os, signal, sys
from pathlib import Path
from memecoin_paper.engine import advance, initial
from memecoin_registry.contracts import digest
from memecoin_runner.ledger import Ledger
tick = dict(at_ns=100, activation_ns=0, market=[], predictions=[], source_ok=True)
state, records = advance(initial(), tick)
with Ledger(Path(sys.argv[1]) / "ledger", dict(fixture=True)) as ledger:
    ledger.append("tick", tick=tick,
                  result_sha=digest(dict(state=state, records=records)))
os.kill(os.getpid(), signal.SIGKILL)
"""
    result = child(
        [sys.executable, "-c", script, str(tmp_path)], tmp_path / "fixture.log", 10, lambda: None
    )
    assert result["category"] == "worker_failed" and result["exit_code"] == -9
    before = replay(tmp_path)
    assert before["ticks"] == 1
    incident = s.incidents(tmp_path, "paper", result)
    assert s.reserve(tmp_path, "paper", result, incident, 0)
    assert s.ready(tmp_path, 60 * 10**9)
    assert replay(tmp_path) == before
