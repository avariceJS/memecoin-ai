"""Successor acquisition stays inside the operator envelope and does not touch the old root."""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from memecoin_continuous.hot import NS, authorization
from memecoin_continuous.hot import choose as hot_choose
from memecoin_continuous.runtime import guard_source
from memecoin_continuous.source import reserve, seed_state
from memecoin_continuous.store import connect, ingest, view
from memecoin_ml.data import read, sha
from memecoin_operator import acquisition
from memecoin_operator.acquisition import (
    AUTHORIZATION_STATEMENT,
    INHERITED_STATEMENT,
    authorize_successor,
    choose,
    current_source,
    ensure_successor,
    logical_index,
    run_source,
    source_status,
    stage_successor_bundle,
    successor_pending,
    worker_command,
)
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import Ledger, publish

WEEK = 7 * 24 * 3600 * NS


def capture(path, available, open_s=0):
    path.mkdir(parents=True)
    publish(path / "request.json", dict(started_ns=available - 2, lane="historical", url="fixture"))
    raw = [[open_s, 1, 1, 1, 1, 1]]
    (path / "response.bin").write_text(json.dumps(dict(data=dict(attributes=dict(ohlcv_list=raw)))))
    publish(path / "receipt.json", dict(received_ns=available - 1, available_ns=available))
    seal(path)


def pool_state(cursor_base):
    pools = {}
    for i, name in enumerate("abcdefg"):
        pools[name] = dict(
            next_ns=0,
            status="quarantined" if name == "c" else "active",
            streak=0,
            probes=0,
            probe_day=-1,
            cursor=cursor_base + i,
            history_pages=i,
            history_done=i % 2 == 0,
        )
    return pools


def write_config(path, body):
    path.write_text(json.dumps(body, sort_keys=True) + "\n")


def old_config(expires):
    return dict(
        expires_ns=expires,
        history_floor_s=1_700_000_000,
        hot_pools=list("abcdef"),
        hot_interval_s=90,
        cold_interval_s=900,
        request_spacing_s=30,
        max_requests=25000,
        paid_budget_usd=0,
    )


def operator_body(old, init, expires, profile="history"):
    return dict(
        product="operator",
        mode="operator",
        provider="geckoterminal",
        profile=profile,
        init_ns=init,
        expires_ns=expires,
        paid_budget_usd=0,
        additional_requests=30000,
        max_background_requests=6000,
        source_root=str(old.resolve()),
    )


def make_old(old, expires, *, journal=True, index=True, attempts=900, cooldown_ns=80 * NS):
    old.mkdir()
    publish(old / "config.json", old_config(expires))
    universe = {
        "pools": [
            {"pool": name, "pool_created_ns": 0, "api_key": "super-secret"} for name in "abcdefg"
        ]
    }
    publish(old / "universe.json", universe)
    publish(
        old / "qualification.json",
        dict(approved=False, measurement=True, note="b09-historical"),
    )
    if index:
        capture(old / "captures" / "a", 1000 * NS)
        db = connect(old / "index.sqlite")
        ingest(db, old / "captures" / "a", "a")
        db.execute("pragma wal_checkpoint(truncate)")
        db.close()
    state = dict(
        last_ns=50 * NS,
        cooldown_ns=cooldown_ns,
        failures=2,
        rate_events=[10 * NS, 20 * NS],
        attempts=attempts,
        bytes=12345,
        pools=pool_state(1000),
    )
    if journal:
        publish(
            old / "seed.json",
            dict(quality={}, scheduler_state=pool_state(1)),
        )
        publish(
            old / "provider-seed.json",
            dict(last_ns=1, cooldown_ns=1, failures=0, rate_events=[], attempts=1, bytes=1),
        )
        binding = dict(config_sha=sha(old / "config.json"), scope="B08_source")
        with Ledger(old / "journal", binding) as ledger:
            ledger.append("outcome", state=state, job=dict(pool="b"), status="valid")
    else:
        publish(old / "seed.json", dict(quality={}, scheduler_state=pool_state(4242)))
        publish(
            old / "provider-seed.json",
            dict(
                last_ns=50 * NS,
                cooldown_ns=80 * NS,
                failures=2,
                rate_events=[10 * NS, 20 * NS],
                attempts=attempts,
                bytes=12345,
            ),
        )
    return state


def make_operator(
    tmp_path, *, old_expires, now, profile="history", journal=True, index=True, cooldown_ns=80 * NS
):
    old = tmp_path / "old"
    root = tmp_path / "operator"
    root.mkdir()
    init = now - 2 * 24 * 3600 * NS
    expires = init + 6 * 24 * 3600 * NS
    make_old(old, old_expires, journal=journal, index=index, cooldown_ns=cooldown_ns)
    write_config(root / "config.json", operator_body(old, init, expires, profile))
    return root, old


def test_rejects_expiry_extension_paid_and_request_cap(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    publish(old / "config.json", old_config(10))
    root = tmp_path / "operator"
    root.mkdir()
    init = 1000
    body = operator_body(old, init, init + WEEK)
    body["expires_ns"] = init + WEEK + 1
    write_config(root / "config.json", body)
    with pytest.raises(ValueError, match="operator_expiry_bound"):
        ensure_successor(root, now_ns=init + 1)
    body["expires_ns"] = init + WEEK
    body["paid_budget_usd"] = 1
    write_config(root / "config.json", body)
    with pytest.raises(ValueError, match="operator_paid_forbidden"):
        ensure_successor(root, now_ns=init + 1)
    body["paid_budget_usd"] = 0
    body["additional_requests"] = 30001
    write_config(root / "config.json", body)
    with pytest.raises(ValueError, match="operator_request_cap"):
        ensure_successor(root, now_ns=init + 1)
    assert not (root / "source").exists()


def test_no_early_takeover_and_profile_stays_pending(tmp_path, monkeypatch):
    now = time.time_ns()
    root, old = make_operator(tmp_path, old_expires=now + 3600 * NS, now=now)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")),
    )
    sidecars = {".lock", ".source.lock", "index.sqlite-wal", "index.sqlite-shm"}
    before = {
        path.relative_to(old).as_posix(): sha(path)
        for path in old.rglob("*")
        if path.is_file() and path.name not in sidecars
    }
    index_hash = sha(old / "index.sqlite")
    report = ensure_successor(root, now_ns=now)
    status = source_status(root, now_ns=now)
    assert report["phase"] == "borrowed" and report["network"] is False
    assert report["expires_ns"] == now + 3600 * NS
    assert current_source(root) == old
    assert not (root / "source").exists() and not (root / "staging").exists()
    assert status["ok"] is True and status["profile_effect"] == "pending"
    assert "профиль" in status["message"].lower() or "Профиль" in status["message"]
    assert "super-secret" not in json.dumps(status)
    after = {
        path.relative_to(old).as_posix(): sha(path)
        for path in old.rglob("*")
        if path.is_file() and path.name not in sidecars
    }
    assert before == after
    assert sha(old / "index.sqlite") == index_hash
    borrowed = run_source(root)
    assert borrowed["ticked"] is False and borrowed["phase"] == "borrowed"


def test_fatal_stop_is_not_cleared(tmp_path):
    now = time.time_ns()
    root, old = make_operator(tmp_path, old_expires=now, now=now)
    publish(old / "STOP", dict(reason="provider_auth"))
    raw = (old / "STOP").read_bytes()
    with pytest.raises(ValueError, match="old_source_fatal_stop:provider_auth"):
        ensure_successor(root, now_ns=now)
    assert (old / "STOP").read_bytes() == raw
    assert not (root / "source").exists()
    (old / "STOP").write_text("not-json")
    raw = (old / "STOP").read_bytes()
    with pytest.raises(ValueError, match="old_source_fatal_stop:unknown"):
        ensure_successor(root, now_ns=now)
    assert (old / "STOP").read_bytes() == raw
    status = source_status(root, now_ns=now)
    assert status["ok"] is False
    assert "STOP" in status["message"]


def test_successor_preserves_history_backup_and_one_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "memecoin_continuous.runtime.stage_source",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("stage_source")),
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")),
    )
    now = time.time_ns()
    root, old = make_operator(tmp_path, old_expires=now, now=now)
    index_hash = sha(old / "index.sqlite")
    raw_hash = sha(old / "captures" / "a" / "response.bin")
    qualification = (old / "qualification.json").read_bytes()
    journal = {path.name: sha(path) for path in (old / "journal").glob("event-*.json")}
    operator_hash = sha(root / "config.json")
    text = Path(acquisition.__file__).read_text()
    assert "Library/LaunchAgents" not in text
    assert "plistlib" not in text and "stage_source" not in text
    assert "immutable=1" not in text
    assert "index.sqlite-wal" not in text and "index.sqlite-shm" not in text

    # Crash after the directories exist and before any budget file: resume the same attempt.
    (root / "staging" / "attempt-0001").mkdir(parents=True)
    (root / "source").mkdir()
    report = ensure_successor(root, now_ns=now)
    assert report["resumed"] is True and report["created"] is False
    assert report["prior_provider_attempts"] == 900
    assert report["max_requests"] == 30000 and report["additional_requests"] == 30000
    assert report["authorization"] == "bound"
    assert report["network"] is False
    source = root / "source"
    assert current_source(root) == source
    seed = read(source / "provider-seed.json")
    config = read(source / "config.json")
    assert seed["attempts"] == 0 and seed["failures"] == 2
    assert seed["rate_events"] == [10 * NS, 20 * NS]
    assert seed["cooldown_ns"] == 80 * NS and seed["last_ns"] == 50 * NS
    assert seed["prior_provider_attempts"] == 900
    assert config["prior_provider_attempts"] == 900
    assert config["max_requests"] == 30000
    assert config["expires_ns"] == read(root / "config.json")["expires_ns"]
    assert config["hot_interval_s"] == 120
    assert config["cold_interval_s"] == 1800
    assert config["request_spacing_s"] == 12
    assert config["http_timeout_s"] == 15
    assert config["history_floor_s"] == 1_700_000_000
    assert config["hot_pools"] == list("abcdef")
    assert config["measurement"] is False
    assert "super-secret" not in json.dumps(read(source / "universe.json"))
    assert "api_key" in json.dumps(read(old / "universe.json"))
    inherited = seed_state(source)
    assert inherited["pools"]["b"]["cursor"] == 1001
    assert inherited["pools"]["b"]["history_pages"] == 1
    assert inherited["pools"]["b"]["history_done"] is False
    assert inherited["pools"]["c"]["status"] == "quarantined"
    assert read(old / "seed.json")["scheduler_state"]["b"]["cursor"] == 2
    assert not (source / "requests").exists()
    assert not (source / "captures").exists()
    rows, _ = view(source, 2000 * NS)
    assert len(rows) == 1 and rows[0]["pool"] == "a"
    assert sha(old / "index.sqlite") == index_hash
    assert sha(old / "captures" / "a" / "response.bin") == raw_hash
    assert (old / "qualification.json").read_bytes() == qualification
    assert {path.name: sha(path) for path in (old / "journal").glob("event-*.json")} == journal
    assert sha(root / "config.json") == operator_hash
    assert current_source(root) == source
    guard_source(source)
    bundle = Path(read(source / "deployment.json")["bundle"])
    src = Path(acquisition.__file__).resolve().parents[1]
    expected = sorted(path.relative_to(src).as_posix() for path in src.glob("memecoin_*/*.py"))
    copied = sorted(
        path.relative_to(bundle / "runtime").as_posix()
        for path in (bundle / "runtime").rglob("*.py")
    )
    assert copied == expected
    assert read(bundle / "binding.json")["python_sha"] == sha(Path(sys.executable).resolve())
    script = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "import memecoin_operator.acquisition as module;"
        "raise SystemExit(0 if module.__file__.startswith(sys.argv[1]) else 1)"
    )
    subprocess.check_call([sys.executable, "-I", "-B", "-c", script, str(bundle / "runtime")])
    authorization(source, read(source / "config.json"), config["created_ns"] + 10**12, 0)
    assert read(source / "qualification.json")["measurement"] is False
    assert read(source / "qualification.json")["freshness_remeasured"] is False
    assert read(source / "qualification.json")["statement"] == AUTHORIZATION_STATEMENT

    again = ensure_successor(root, now_ns=now + 5)
    assert again["reused"] is True and again["created"] is False
    assert read(source / "provider-seed.json") == seed
    assert read(source / "config.json") == config
    assert [path.name for path in (root / "staging").iterdir()] == ["attempt-0001"]

    status = source_status(root, now_ns=now + 5)
    assert status["phase"] == "successor" and status["profile_effect"] == "active"
    assert status["authorization"] == "bound" and status["ok"] is True
    assert status["recorded_profile"] == "history" and status["profile"] == "balanced"
    assert status["inherited"]["failures"] == 2
    assert "не сброшен" in status["inherited"]["projected_block"]
    assert status["bounds"]["prior_provider_attempts"] == 900
    assert status["bounds"]["http_timeout_s"] == 15
    assert status["bounds"]["enforced"]["http_timeout_s"] == 15
    assert status["bounds"]["planned_successor"]["http_timeout_s"] == 15
    assert status["bars"] == 1 and "c" in status["inherited"]["quarantined"]
    assert "super-secret" not in json.dumps(status)
    assert INHERITED_STATEMENT in read(source / "inheritance.json")["statement"]
    decision = authorize_successor(root, now_ns=now + 6)
    assert decision["measurement"] is False
    assert read(source / "qualification.json")["inherited_qualification_approved"] is False
    assert (old / "qualification.json").read_bytes() == qualification
    repeat = authorize_successor(root, now_ns=now + 7)
    assert repeat["qualification_sha"] == decision["qualification_sha"]

    publish(root / "collection-profile.json", dict(profile="history", at_ns=now))
    seed_rows = logical_index(source / "index.sqlite")
    capture(source / "captures" / "extra", 1500 * NS, open_s=300)
    db = connect(source / "index.sqlite")
    ingest(db, source / "captures" / "extra", "a")
    db.commit()
    db.close()
    assert logical_index(source / "index.sqlite") != seed_rows
    assert read(root / "staging" / "attempt-0001" / "stage.json")["index_id"] == seed_rows
    assert current_source(root) == source
    guard_source(source)
    grown, _ = view(source, 2000 * NS)
    assert {row["open_s"] for row in grown} == {0, 300}

    import memecoin_continuous.hot as hot

    original = hot.choose
    seen = []

    def tick(path):
        seen.append(path)
        state = json.loads(json.dumps(inherited))
        for name, quality in state["pools"].items():
            quality["next_ns"] = 0 if name == "g" else 10**18
        pools = read(source / "universe.json")["pools"]
        job = hot.choose(state, pools, 10_000 * NS, read(source / "config.json"))
        assert job["role"] == "history"
        assert (
            choose(state, pools, 10_000 * NS, read(source / "config.json"), profile="balanced")[
                "role"
            ]
            == "cold"
        )

    monkeypatch.setattr("memecoin_continuous.source.tick", tick)
    # run_source imports the module attribute at call time.
    ran = run_source(root)
    assert ran["ticked"] is True and seen == [source]
    assert hot.choose is original
    command = worker_command(root)
    assert "run_source" in command[4]
    assert "LaunchAgents" not in " ".join(command)
    assert command[-1] == str(root.resolve())


def test_seed_fallback_and_corrupt_index_does_not_open_second_budget(tmp_path):
    now = time.time_ns()
    root, old = make_operator(tmp_path, old_expires=now, now=now, journal=False, profile="balanced")
    ensure_successor(root, now_ns=now)
    source = root / "source"
    assert seed_state(source)["pools"]["b"]["cursor"] == 4243
    assert read(source / "provider-seed.json")["prior_provider_attempts"] == 900
    budget = (source / "provider-seed.json").read_bytes()
    config = (source / "config.json").read_bytes()
    seed_id = read(root / "staging" / "attempt-0001" / "stage.json")["index_id"]
    capture(source / "captures" / "extra", 1500 * NS, open_s=300)
    db = connect(source / "index.sqlite")
    ingest(db, source / "captures" / "extra", "a")
    db.commit()
    db.close()
    assert logical_index(source / "index.sqlite") != seed_id
    assert current_source(root) == source
    again = ensure_successor(root, now_ns=now + 1)
    assert again["reused"] is True
    assert (source / "provider-seed.json").read_bytes() == budget
    assert (source / "config.json").read_bytes() == config
    assert [path.name for path in (root / "staging").iterdir()] == ["attempt-0001"]
    body = read(source / "config.json")
    body["hot_interval_s"] = 121
    (source / "config.json").write_text(json.dumps(body) + "\n")
    with pytest.raises(ValueError, match="successor_ready_integrity"):
        current_source(root)
    assert [path.name for path in (root / "staging").iterdir()] == ["attempt-0001"]


def test_existing_budget_file_is_not_overwritten(tmp_path):
    now = time.time_ns()
    root, _old = make_operator(tmp_path, old_expires=now, now=now)
    source = root / "source"
    source.mkdir()
    bad = dict(attempts=5, failures=0, rate_events=[], last_ns=0, cooldown_ns=0, bytes=0)
    publish(source / "provider-seed.json", bad)
    raw = (source / "provider-seed.json").read_bytes()
    with pytest.raises(ValueError, match="successor_integrity"):
        ensure_successor(root, now_ns=now)
    assert (source / "provider-seed.json").read_bytes() == raw
    assert not (root / "successor-ready.json").exists()
    assert [path.name for path in (root / "staging").iterdir()] == ["attempt-0001"]


def test_profile_changes_background_share_only(tmp_path):
    names = list("abcdef")
    universe = [dict(pool=pool) for pool in names + ["cold"]]

    def blank():
        return dict(
            last_ns=0,
            bytes=0,
            attempts=0,
            background_attempts=0,
            pools={
                pool: dict(
                    next_ns=0,
                    status="active",
                    probes=0,
                    probe_day=-1,
                    history_pages=0,
                    history_done=False,
                    cursor=100,
                )
                for pool in names + ["cold"]
            },
        )

    config = dict(
        hot_pools=names,
        hot_interval_s=120,
        cold_interval_s=1800,
        max_background_requests=20,
        request_spacing_s=12,
    )
    state = blank()
    for seconds in range(1000, 1072, 12):
        now = seconds * NS
        base = hot_choose(state, universe, now, config)
        assert choose(state, universe, now, config, profile="balanced") == base
        assert choose(state, universe, now, config, profile="history") == base
        assert base["role"] == "hot"
        state = reserve(state, base, now)
    blocked = blank()
    for quality in blocked["pools"].values():
        quality["next_ns"] = 2000 * NS
    blocked["pools"]["a"]["next_ns"] = 1034 * NS
    assert choose(blocked, universe, 1000 * NS, config, profile="history") is None
    assert choose(blocked, universe, 1000 * NS, config, profile="balanced") is None

    def background_ready():
        state = blank()
        for name, quality in state["pools"].items():
            quality["next_ns"] = 0 if name == "cold" else 10**18
        return state

    def recharge(state):
        state["pools"]["cold"]["next_ns"] = 0
        for name, quality in state["pools"].items():
            if name != "cold":
                quality["history_done"] = False
                quality["history_pages"] = 0
                quality["next_ns"] = 10**18

    far = background_ready()
    balanced_state = background_ready()
    roles_h, roles_b = [], []
    frozen = json.dumps(config, sort_keys=True)
    for i in range(12):
        now = (10_000 + i * 60) * NS
        history = choose(far, universe, now, config, profile="history")
        base = choose(balanced_state, universe, now, config, profile="balanced")
        assert history is not None and base is not None
        assert history["background"] is True and base["background"] is True
        assert "request_spacing_s" not in history
        roles_h.append(history["role"])
        roles_b.append(base["role"])
        far = reserve(far, history, now)
        balanced_state = reserve(balanced_state, base, now)
        recharge(far)
        recharge(balanced_state)
    assert json.dumps(config, sort_keys=True) == frozen
    assert roles_h.count("history") > roles_b.count("history")
    assert "cold" in roles_h and "history" in roles_h
    assert roles_h[2] == "cold"
    assert all(roles_h[i : i + 3] != ["history", "history", "history"] for i in range(10))
    assert far["background_next_ns"] - (10_000 + 11 * 60) * NS == 60 * NS
    assert source_status(tmp_path, now_ns=1)["phase"] == "blocked"


def test_collection_profile_rejects_extra_flags_and_ignores_other_files(tmp_path):
    now = time.time_ns()
    root, _old = make_operator(tmp_path, old_expires=now + 3600 * NS, now=now)
    publish(root / "collection-profile.json", dict(profile="history", disable_guards=True))
    (root / "ui-config.json").write_text('{"profile":"history"}\n')
    status = source_status(root, now_ns=now)
    assert status["ok"] is False
    assert any(item["code"] == "operator_profile" for item in status["errors"])
    (root / "collection-profile.json").unlink()
    publish(root / "collection-profile.json", dict(profile="history", at_ns=now))
    status = source_status(root, now_ns=now)
    assert status["ok"] is True and status["profile"] == "history"
    assert status["profile_effect"] == "pending"


def test_future_cooldown_is_projected_without_reset(tmp_path):
    now = time.time_ns()
    root, old = make_operator(
        tmp_path, old_expires=now + 3600 * NS, now=now, cooldown_ns=now + 5 * NS
    )
    status = source_status(root, now_ns=now)
    assert status["inherited"]["cooldown_ns"] == now + 5 * NS
    assert status["inherited"]["failures"] == 2
    assert "не сброшены" in status["inherited"]["projected_block"]
    assert read(old / "provider-seed.json")["cooldown_ns"] == 1


def test_expiry_stop_classifier_rejects_early_and_unknown(tmp_path):
    now = time.time_ns()
    expires = now + 3600 * NS
    root, old = make_operator(tmp_path, old_expires=expires, now=now)
    deadline = f"incident-{expires}.json"
    publish(
        old / deadline,
        dict(exit_code=-9, category="source_stop_or_deadline", elapsed_seconds=1),
    )
    publish(old / "STOP", dict(reason="source_worker_failure", incident=deadline))
    raw = (old / "STOP").read_bytes()
    report = ensure_successor(root, now_ns=now)
    assert report["phase"] == "borrowed" and not (root / "source").exists()
    assert (old / "STOP").read_bytes() == raw

    with pytest.raises(ValueError, match="old_source_fatal_stop:guard_binding"):
        ensure_successor(root, now_ns=expires)
    assert not (root / "source").exists()
    assert (old / "STOP").read_bytes() == raw

    early = f"incident-{expires - 1}.json"
    publish(
        old / early,
        dict(exit_code=-9, category="source_stop_or_deadline", elapsed_seconds=1),
    )
    (old / "STOP").unlink()
    publish(old / "STOP", dict(reason="source_worker_failure", incident=early))
    raw = (old / "STOP").read_bytes()
    with pytest.raises(ValueError, match="old_source_fatal_stop:earlier_stop"):
        ensure_successor(root, now_ns=expires)
    assert (old / "STOP").read_bytes() == raw and not (root / "source").exists()

    for category in (
        "worker_failed",
        "clock_jump_or_suspend",
        "supervisor_guard",
        "child_deadline",
    ):
        (old / deadline).unlink(missing_ok=True)
        publish(old / deadline, dict(exit_code=-9, category=category, elapsed_seconds=1))
        (old / "STOP").unlink()
        publish(old / "STOP", dict(reason="source_worker_failure", incident=deadline))
        raw = (old / "STOP").read_bytes()
        with pytest.raises(ValueError, match=f"old_source_fatal_stop:{category}"):
            ensure_successor(root, now_ns=expires)
        assert (old / "STOP").read_bytes() == raw
        assert not (root / "source").exists()


def test_inexact_deadline_snapshot_is_not_admitted(tmp_path):
    now = time.time_ns()
    root, old = make_operator(tmp_path, old_expires=now, now=now)
    stage_successor_bundle(old, tmp_path / "old-runtime")
    db = connect(old / "index.sqlite")
    db.execute("update firsts set payload='{}'")
    db.commit()
    db.close()
    name = f"incident-{now}.json"
    publish(old / name, dict(exit_code=-9, category="source_stop_or_deadline", elapsed_seconds=1))
    publish(old / "STOP", dict(reason="source_worker_failure", incident=name))
    raw = (old / "STOP").read_bytes()
    assert successor_pending(root, now_ns=now) is True
    assert (old / "STOP").read_bytes() == raw
    with pytest.raises(ValueError, match="old_source_fatal_stop:index_snapshot"):
        ensure_successor(root, now_ns=now)
    assert not (root / "source").exists() and not (root / "successor-ready.json").exists()
    assert (old / "STOP").read_bytes() == raw


def test_expected_deadline_admits_a_new_budget(tmp_path):
    now = time.time_ns()
    root, old = make_operator(tmp_path, old_expires=now, now=now)
    stage_successor_bundle(old, tmp_path / "old-runtime")
    name = f"incident-{now}.json"
    publish(old / name, dict(exit_code=-9, category="source_stop_or_deadline", elapsed_seconds=1))
    publish(old / "STOP", dict(reason="source_worker_failure", incident=name))
    raw = (old / "STOP").read_bytes()
    index_hash = sha(old / "index.sqlite")
    report = ensure_successor(root, now_ns=now)
    source = root / "source"
    admission = read(source / "inheritance.json")["expiry_admission"]
    assert report["phase"] == "successor" and report["authorization"] == "bound"
    assert report["max_requests"] == 30000 and report["prior_provider_attempts"] == 900
    assert read(source / "provider-seed.json")["attempts"] == 0
    assert admission["not_a_resume"] is True
    assert admission["category"] == "source_stop_or_deadline"
    assert admission["stop_sha"] == sha(old / "STOP")
    assert admission["incident_sha"] == sha(old / name)
    assert "b09" not in json.dumps(admission).lower()
    assert (old / "STOP").read_bytes() == raw
    assert sha(old / "index.sqlite") == index_hash
    assert current_source(root) == source


def test_writer_lock_blocks_backup_without_a_second_budget(tmp_path):
    now = time.time_ns()
    root, old = make_operator(tmp_path, old_expires=now, now=now)
    script = (
        "import fcntl,sys,time\n"
        "f=open(sys.argv[1],'a')\n"
        "fcntl.flock(f,fcntl.LOCK_EX)\n"
        "print('locked',flush=True)\n"
        "time.sleep(30)\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", script, str(old / ".source.lock")],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout.readline().strip() == "locked"
    try:
        with pytest.raises(ValueError, match="successor_source_busy"):
            ensure_successor(root, now_ns=now)
    finally:
        holder.kill()
        holder.wait(timeout=5)
    assert not (root / "successor-ready.json").exists()
    report = ensure_successor(root, now_ns=now)
    assert report["authorization"] == "bound"
    assert read(root / "source" / "provider-seed.json")["attempts"] == 0
    assert [path.name for path in (root / "staging").iterdir()] == ["attempt-0001"]


def test_direct_tick_exception_persists_unknown_stop(tmp_path, monkeypatch):
    now = time.time_ns()
    root, old = make_operator(tmp_path, old_expires=now, now=now)
    ensure_successor(root, now_ns=now)
    source = root / "source"

    def boom(path):
        raise RuntimeError("boom")

    monkeypatch.setattr("memecoin_continuous.source.tick", boom)
    with pytest.raises(RuntimeError, match="boom"):
        run_source(root)
    assert read(source / "STOP")["reason"] == "unknown"
    assert not (old / "STOP").exists()
    with pytest.raises(ValueError, match="successor_stop"):
        run_source(root)
    assert read(source / "STOP")["reason"] == "unknown"


def _replace(path, body):
    if path.exists():
        path.unlink()
    publish(path, body)


def test_successor_pending_then_ingest_does_not_reset_budget(tmp_path):
    now = time.time_ns()
    root, old = make_operator(tmp_path, old_expires=now, now=now)
    body = read(old / "config.json")
    body["http_timeout_s"] = 20
    _replace(old / "config.json", body)
    deadline = f"incident-{now}.json"
    publish(old / deadline, dict(exit_code=-9, category="worker_failed", elapsed_seconds=1))
    publish(old / "STOP", dict(reason="source_worker_failure", incident=deadline))
    assert successor_pending(root, now_ns=now) is False
    early = f"incident-{now - 1}.json"
    publish(old / early, dict(exit_code=-9, category="source_stop_or_deadline", elapsed_seconds=1))
    _replace(old / "STOP", dict(reason="source_worker_failure", incident=early))
    assert successor_pending(root, now_ns=now) is False
    assert successor_pending(root, now_ns=now - NS) is False
    stage_successor_bundle(old, tmp_path / "old-runtime")
    _replace(
        old / deadline,
        dict(exit_code=-9, category="source_stop_or_deadline", elapsed_seconds=1),
    )
    _replace(old / "STOP", dict(reason="source_worker_failure", incident=deadline))
    raw = (old / "STOP").read_bytes()
    config_hash = sha(old / "config.json")
    assert successor_pending(root, now_ns=now) is True
    assert not (root / "source").exists()
    assert (old / "STOP").read_bytes() == raw and sha(old / "config.json") == config_hash
    operator_expires = read(root / "config.json")["expires_ns"]
    assert successor_pending(root, now_ns=operator_expires) is False
    report = ensure_successor(root, now_ns=now)
    source = root / "source"
    assert report["authorization"] == "bound" and current_source(root) == source
    assert read(source / "config.json")["http_timeout_s"] == 20
    assert source_status(root, now_ns=now)["bounds"]["enforced"]["http_timeout_s"] == 20
    budget = (source / "provider-seed.json").read_bytes()
    sealed = (source / "config.json").read_bytes()
    capture(source / "captures" / "extra", 1500 * NS, open_s=300)
    db = connect(source / "index.sqlite")
    ingest(db, source / "captures" / "extra", "a")
    db.commit()
    db.close()
    assert successor_pending(root, now_ns=now) is False
    assert current_source(root) == source
    assert (source / "provider-seed.json").read_bytes() == budget
    assert (source / "config.json").read_bytes() == sealed
    assert read(source / "provider-seed.json")["attempts"] == 0
    assert read(source / "config.json")["max_requests"] == 30000
    assert (old / "STOP").read_bytes() == raw


def test_invalid_http_timeout_does_not_invent_a_bound(tmp_path):
    now = time.time_ns()
    root, old = make_operator(tmp_path, old_expires=now, now=now)
    body = read(old / "config.json")
    body["http_timeout_s"] = -1
    _replace(old / "config.json", body)
    with pytest.raises(ValueError, match="successor_http_timeout"):
        ensure_successor(root, now_ns=now)
    assert not (root / "source").exists()
    assert successor_pending(root, now_ns=now) is False


def test_resource_priority_suppresses_history_only(tmp_path):
    names = list("abcdef")
    universe = [dict(pool=pool) for pool in names + ["cold"]]
    config = dict(
        hot_pools=names,
        hot_interval_s=120,
        cold_interval_s=1800,
        max_background_requests=20,
        request_spacing_s=12,
    )

    def state(next_cold=True):
        return dict(
            last_ns=0,
            bytes=0,
            attempts=0,
            failures=2,
            background_attempts=0,
            pools={
                pool: dict(
                    next_ns=0 if pool == "cold" and next_cold else 10**18,
                    status="active",
                    probes=0,
                    probe_day=-1,
                    history_pages=0,
                    history_done=False,
                    cursor=100,
                )
                for pool in names + ["cold"]
            },
        )

    history_state = state()
    frozen = json.dumps(history_state)
    usual = choose(history_state, universe, 10_000 * NS, config, profile="history")
    assert usual["role"] == "history" and usual["lane"] == "historical"
    for phase in ("snapshot", "train", "paper"):
        suppressed = choose(
            history_state, universe, 10_000 * NS, config, profile="history", priority=phase
        )
        assert suppressed is None
    waiting = choose(
        history_state, universe, 10_000 * NS, config, profile="history", priority="waiting"
    )
    assert waiting == usual
    assert (
        choose(history_state, universe, 10_000 * NS, config, profile="history", priority="collect")
        == usual
    )
    cold = choose(
        history_state, universe, 10_000 * NS, config, profile="balanced", priority="paper"
    )
    assert cold["role"] == "cold" and cold["background"] is True
    assert "request_spacing_s" not in cold
    hot_state = state(next_cold=False)
    for quality in hot_state["pools"].values():
        quality["next_ns"] = 0
    hot = choose(hot_state, universe, 10_000 * NS, config, profile="history", priority="train")
    assert hot["role"] == "hot" and hot["interval_s"] == 120
    assert json.dumps(history_state) == frozen
    with pytest.raises(ValueError, match="resource_priority"):
        choose(history_state, universe, 10_000 * NS, config, profile="history", priority="ui")

    now = time.time_ns()
    root, old = make_operator(tmp_path, old_expires=now + 3600 * NS, now=now)
    before = sha(old / "config.json")
    publish(root / "resource-priority.json", dict(phase="train", disable_hot=True, at_ns=now))
    status = source_status(root, now_ns=now)
    assert status["ok"] is False and status["priority"] is None
    assert any(item["code"] == "resource_priority" for item in status["errors"])
    with pytest.raises(ValueError, match="resource_priority"):
        run_source(root)
    assert sha(old / "config.json") == before
    assert not (old / "STOP").exists() and not (root / "source").exists()
    _replace(root / "resource-priority.json", dict(phase="train", at_ns=now))
    status = source_status(root, now_ns=now)
    assert status["ok"] is True and status["priority"] == "train"
    assert status["profile_effect"] == "pending"
