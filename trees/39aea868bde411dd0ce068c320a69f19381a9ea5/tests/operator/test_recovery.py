"""Exact frozen-exam recovery. Synthetic ledger only; the original STOP stays."""

import sqlite3
import time
from pathlib import Path

import pytest

from memecoin_ml.data import read, sha
from memecoin_operator.recovery import recover_exam
from memecoin_paper.audit import replay
from memecoin_paper.engine import advance, initial
from memecoin_registry.contracts import digest
from memecoin_runner.ledger import Ledger, publish

NS = 10**9
PREFIX = "ai.memecoin.brain.operator.exam.rev."


def walk(root):
    state = initial()
    binding = read(root / "ledger" / "binding.json")
    with Ledger(root / "ledger", binding) as ledger:
        for event in ledger.audit():
            state, _records = advance(state, event["payload"]["tick"])
    return state


def book(state):
    assert len(state["books"]) == 1
    return next(iter(state["books"].values()))


def commit(ledger, state, tick):
    state, records = advance(state, tick)
    ledger.append("tick", tick=tick, result_sha=digest(dict(state=state, records=records)))
    return state


def observation(pool, close_s, now):
    return dict(
        pool=pool,
        price="1.25",
        close_s=close_s,
        available_ns=close_s * NS + NS,
        quality="active",
        capture_sha=f"synthetic-{pool}-{close_s}",
    )


def prediction(pool, now, model):
    return dict(
        id=f"synthetic-{pool}-{now}",
        pool=pool,
        predicted_ns=now,
        input_available_ns=now - 1,
        event_close_s=now // NS,
        model_sha=model,
        q_by_candidate=dict(gru=[-0.01, 0.05, 0.2]),
    )


def stopped_exam(tmp):
    old = tmp / "old-exam"
    old.mkdir()
    model = "synthetic-model"
    close1 = 1_700_000_000
    now1 = close1 * NS + 5 * NS
    close2 = close1 + 300
    now2 = close2 * NS + 5 * NS
    now3 = close2 * NS + 20 * NS
    expires = time.time_ns() + 2 * 86400 * NS
    frozen = [dict(path="/synthetic/frozen-model", sha="ab" * 32, label="exam-synthetic")]
    publish(
        old / "config.json",
        dict(
            frozen_exam_models=frozen,
            training_max_runs=0,
            expires_ns=expires,
            activation_ns=now1 - 1000,
            exam_start_ns=now1 - 1000,
            real_execution_enabled=False,
            port=0,
            service_prefix="ai.memecoin.brain.operator.exam.",
        ),
    )
    publish(old / "activation.json", dict(at_ns=now1, mode="frozen_future_exam", synthetic=True))
    (old / "STOP").write_bytes(b'{"reason":"synthetic_worker_failed"}\n')
    (old / "training").mkdir()
    (old / "training" / "marker.txt").write_text("synthetic-keep")
    (old / "candidate-shadows").mkdir()
    (old / "candidate-shadows" / "lane.txt").write_text("shadow")
    database = sqlite3.connect(old / "soak.sqlite")
    database.execute("create table soak(k text)")
    database.execute("insert into soak values ('synthetic')")
    database.commit()
    database.close()
    state = initial()
    binding = dict(scope="synthetic-virtual-exam", trial_ids=[])
    with Ledger(old / "ledger", binding) as ledger:
        state = commit(
            ledger,
            state,
            dict(
                at_ns=now1,
                activation_ns=now1 - 1000,
                market=[observation("poola", close1, now1)],
                predictions=[prediction("poola", now1, model)],
                source_ok=True,
                deployment_config_sha="synthetic",
            ),
        )
        state = commit(
            ledger,
            state,
            dict(
                at_ns=now2,
                activation_ns=now1 - 1000,
                market=[observation("poola", close2, now2), observation("poolb", close2, now2)],
                predictions=[],
                source_ok=True,
                deployment_config_sha="synthetic",
            ),
        )
        state = commit(
            ledger,
            state,
            dict(
                at_ns=now3,
                activation_ns=now1 - 1000,
                market=[observation("poola", close2, now3), observation("poolb", close2, now3)],
                predictions=[prediction("poolb", now3, model)],
                source_ok=True,
                deployment_config_sha="synthetic",
            ),
        )
    held = book(state)
    assert len(held["positions"]) == 1
    assert len(held["pending"]) == 1
    assert held["cash"] != "1000"
    return old, state


def test_recovery_copies_cash_position_pending_and_leaves_the_original(tmp_path):
    old, state = stopped_exam(tmp_path)
    before_config = (old / "config.json").read_bytes()
    before_stop = (old / "STOP").read_bytes()
    before_activation = (old / "activation.json").read_bytes()
    before_frozen = read(old / "config.json")["frozen_exam_models"]
    before_expiry = read(old / "config.json")["expires_ns"]
    before_events = {path.name: path.read_bytes() for path in (old / "ledger").glob("event-*.json")}
    new = tmp_path / "revision"
    carried = recover_exam(old, new, PREFIX, 4242)
    assert (old / "config.json").read_bytes() == before_config
    assert (old / "STOP").read_bytes() == before_stop
    assert (old / "activation.json").read_bytes() == before_activation
    assert (old / "STOP").exists()
    assert not (new / "STOP").exists()
    restored = read(new / "config.json")
    assert restored["expires_ns"] == before_expiry
    assert restored["frozen_exam_models"] == before_frozen
    assert restored["activation_ns"] == read(old / "config.json")["activation_ns"]
    assert restored["exam_start_ns"] == read(old / "config.json")["exam_start_ns"]
    assert restored["service_prefix"] == PREFIX
    assert restored["port"] == 4242
    assert read(new / "migration.json")["no_cash_reset"] is True
    assert read(new / "migration.json")["no_synthetic_exit"] is True
    assert read(new / "migration.json")["parent_stop_sha"] == sha(old / "STOP")
    assert read(new / "activation.json")["models_and_exam_start_unchanged"] is True
    assert read(new / "activation.json")["expires_ns"] == before_expiry
    after_events = {path.name: path.read_bytes() for path in (new / "ledger").glob("event-*.json")}
    assert after_events == before_events
    assert replay(old, carried["ledger_head"]) == replay(new, carried["ledger_head"])
    original = book(state)
    copied = book(walk(new))
    assert copied["cash"] == original["cash"]
    assert copied["positions"] == original["positions"]
    assert copied["pending"] == original["pending"]
    assert copied["realized"] == original["realized"]
    assert carried["pending"] == 1
    assert carried["open_positions"] == 1
    assert (new / "training" / "marker.txt").read_text() == "synthetic-keep"
    assert (new / "candidate-shadows" / "lane.txt").read_text() == "shadow"
    with sqlite3.connect(new / "soak.sqlite") as database:
        assert database.execute("select k from soak").fetchone()[0] == "synthetic"


def test_recovery_refuses_a_live_expired_or_trainable_exam(tmp_path):
    old, _state = stopped_exam(tmp_path)
    stop = (old / "STOP").read_bytes()
    (old / "STOP").unlink()
    with pytest.raises(ValueError, match="recovery_requires_stopped_exam"):
        recover_exam(old, tmp_path / "missing-stop", PREFIX, 1)
    assert not (tmp_path / "missing-stop").exists()
    (old / "STOP").write_bytes(stop)
    config = read(old / "config.json")
    config["expires_ns"] = time.time_ns() - NS
    (old / "config.json").unlink()
    publish(old / "config.json", config)
    with pytest.raises(ValueError, match="recovery_exam_expired"):
        recover_exam(old, tmp_path / "expired", PREFIX, 1)
    assert not (tmp_path / "expired").exists()
    assert (old / "STOP").read_bytes() == stop
    config["expires_ns"] = time.time_ns() + 86400 * NS
    config["training_max_runs"] = 1
    (old / "config.json").unlink()
    publish(old / "config.json", config)
    with pytest.raises(ValueError, match="recovery_only_frozen_exam"):
        recover_exam(old, tmp_path / "trainable", PREFIX, 1)
    config["training_max_runs"] = 0
    config["frozen_exam_models"] = []
    (old / "config.json").unlink()
    publish(old / "config.json", config)
    with pytest.raises(ValueError, match="recovery_only_frozen_exam"):
        recover_exam(old, tmp_path / "unfrozen", PREFIX, 1)
    assert (old / "STOP").read_bytes() == stop
    assert not Path(tmp_path / "unfrozen").exists()
