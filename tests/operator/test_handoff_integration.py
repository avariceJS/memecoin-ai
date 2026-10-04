"""Cross-service expiry handoff. Control, adapters, and acquisition stay real.

Background start and control workers are stubs: they record commands and do not
launch processes, LaunchAgents, caffeinate, or provider HTTP. Time for control
gates is frozen; adapters keep the wall clock, and both stay inside the same
7-day operator window. Maintain is due on the first tick because the stamp is
absent. A later tick clears that stamp instead of waiting out the 15s interval.
"""

import json
import time

import pytest

from memecoin_continuous.hot import NS, authorization
from memecoin_continuous.runtime import guard_source
from memecoin_continuous.store import connect, ingest, view
from memecoin_ml.data import read
from memecoin_operator import adapters, control
from memecoin_operator.acquisition import (
    current_source,
    stage_successor_bundle,
    successor_pending,
)
from memecoin_operator.control import command, events, initialize, set_clock, status, tick
from memecoin_operator.deployment import stage as stage_operator
from memecoin_runner.ledger import publish

from .test_acquisition import capture, make_old, write_config

DAY = 24 * 3600 * NS
WEEK = 7 * DAY
CHAMPION = "ab" * 32
ENVELOPE_S = 7 * 24 * 3600


class _Idle:
    pid = 424242

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0


@pytest.fixture
def handoff(monkeypatch):
    monkeypatch.delenv("MEMECOIN_OPERATOR_ADAPTER", raising=False)
    recorded = []
    spawned = []

    def fake_start(self, key, command, seconds):
        recorded.append({"key": key, "command": list(command), "seconds": seconds})
        return {"started": False, "key": key, "reason": "stub"}

    def fake_stop(self):
        return {"stopped": []}

    def fake_spawn(root, job_id, log_handle):
        spawned.append(job_id)
        return _Idle()

    def refuse_network(*args, **kwargs):
        raise AssertionError("network")

    monkeypatch.setattr("memecoin_operator.background.Manager.start", fake_start)
    monkeypatch.setattr("memecoin_operator.background.Manager.stop", fake_stop)
    monkeypatch.setattr(control, "_spawn_worker", fake_spawn)
    monkeypatch.setattr("urllib.request.urlopen", refuse_network)
    yield {"started": recorded, "spawned": spawned}
    set_clock(None)
    control._CHILDREN.clear()
    adapters._MANAGERS.clear()
    adapters._AWAKE.clear()


def _checks(body):
    return {item["id"]: item for item in body["gates"]["checks"]}


def _due(root):
    db = control._connect(root)
    try:
        control._meta_set(db, "last_maintain_ns", "0")
    finally:
        db.close()


def _mark_collect_done(root):
    db = control._connect(root)
    try:
        control._schema(db)
        control._meta_set(db, "initial_collect_done", "1")
    finally:
        db.close()


def _enter_auto(root):
    _mark_collect_done(root)
    entered = command(root, "enter-auto", "auto", {"profile": "balanced"})
    assert entered["ok"] is True, entered
    return entered


def _known_stop(old, stamp):
    name = f"incident-{stamp}.json"
    publish(
        old / name,
        {"exit_code": -9, "category": "source_stop_or_deadline", "elapsed_seconds": 1},
    )
    publish(old / "STOP", {"reason": "source_worker_failure", "incident": name})
    return (old / "STOP").read_bytes()


def _fatal_stop(old, reason):
    publish(old / "STOP", {"reason": reason})
    return (old / "STOP").read_bytes()


def _quiet(started):
    blob = json.dumps(started)
    assert "LaunchAgents" not in blob
    assert "caffeinate" not in blob
    assert "launchctl" not in blob
    assert "geckoterminal.com" not in blob
    assert adapters._AWAKE == {}


def _build(tmp_path, *, old_offset_ns, stop):
    moment = time.time_ns()
    set_clock(lambda: moment)
    old_expires = moment + old_offset_ns
    old = tmp_path / "old"
    make_old(old, old_expires)
    body = read(old / "config.json")
    body["http_timeout_s"] = 20
    write_config(old / "config.json", body)
    stage_successor_bundle(old, tmp_path / "old-runtime")
    stop_bytes = _known_stop(old, old_expires) if stop == "known" else None
    init = moment - 2 * DAY
    expires = init + 6 * DAY
    root = tmp_path / "operator"
    root.mkdir()
    write_config(
        root / "config.json",
        {
            "product": "operator",
            "mode": "operator",
            "provider": "geckoterminal",
            "profile": "balanced",
            "init_ns": init,
            "created_ns": init,
            "expires_ns": expires,
            "paid_budget_usd": 0,
            "additional_requests": 30000,
            "max_background_requests": 6000,
            "source_root": str(old.resolve()),
            "champion_model_sha": CHAMPION,
            "keep_awake_on_ac": False,
            "real_execution_enabled": False,
        },
    )
    stage_operator(root, tmp_path / "operator-runtime")
    assert initialize(root)["ok"] is True
    assert expires - init == 6 * DAY
    assert expires - init <= WEEK
    return root, old, moment, stop_bytes


def _successor_budget(root):
    source = root / "source"
    config = read(source / "config.json")
    seed = read(source / "provider-seed.json")
    qualification = read(source / "qualification.json")
    return config, seed, qualification


def test_known_expiry_handoff_keeps_auto_and_one_budget(tmp_path, handoff):
    root, old, moment, stop_bytes = _build(tmp_path, old_offset_ns=0, stop="known")
    old_seed = (old / "provider-seed.json").read_bytes()
    operator_config = (root / "config.json").read_bytes()
    raw = adapters.inspect(root)
    assert raw["source"]["stopped"] is True
    assert raw["source"]["succession_pending"] is True
    assert raw["source"]["borrowed"] is True
    assert raw["source"]["max_requests"] == 25000
    assert raw["limits"]["domain_stop"] is False
    assert raw["limits"]["eligible"] is False
    assert raw["limits"]["paid_budget_usd"] == 0
    assert successor_pending(root, now_ns=moment) is True
    _enter_auto(root)
    before = status(root)
    gates = _checks(before)
    assert before["mode"] == "auto"
    assert before["domain_stop"] is False
    assert before["limits"]["eligible"] is False
    assert before["expired"] is False
    assert before["gates"]["envelope_s"] == ENVELOPE_S
    assert gates["domain_stop"]["pass"] is True
    assert gates["eligible"]["pass"] is False
    assert gates["expiry"]["pass"] is True
    assert "domain_stop" not in before["gates"]["auto"]["reasons"]
    assert "ineligible" in before["gates"]["auto"]["reasons"]
    assert before["gates"]["auto"]["eligible"] is False

    after = tick(root)
    assert after["mode"] == "auto"
    assert after["auto_reason"] is None
    assert after["domain_stop"] is False
    assert after["limits"]["eligible"] is True
    assert _checks(after)["eligible"]["pass"] is True
    assert _checks(after)["domain_stop"]["pass"] is True
    assert after["jobs"] == []
    assert handoff["spawned"] == []
    assert [item["key"] for item in handoff["started"]] == ["source", "source-audit"]
    _quiet(handoff["started"])
    assert list(root.rglob("*.plist")) == []

    source = current_source(root)
    assert source == root / "source"
    guard_source(source)
    config, seed, qualification = _successor_budget(root)
    assert config["max_requests"] == 30000
    assert config["additional_requests"] == 30000
    assert config["prior_provider_attempts"] == 900
    assert config["http_timeout_s"] == 20
    assert config["hot_pools"] == list("abcdef")
    assert config["hot_interval_s"] == 120
    assert config["cold_interval_s"] == 1800
    assert config["request_spacing_s"] == 12
    assert config["history_floor_s"] == 1_700_000_000
    assert config["paid_budget_usd"] == 0
    assert config["measurement"] is False
    assert seed["attempts"] == 0
    assert seed["bytes"] == 0
    assert seed["prior_provider_attempts"] == 900
    assert qualification["approved"] is True
    assert qualification["measurement"] is False
    assert qualification["freshness_remeasured"] is False
    assert qualification["kind"] == "successor_operator_authorization"
    authorization(source, config, time.time_ns(), 0)
    seen = adapters.inspect(root)
    assert seen["source"]["stopped"] is False
    assert seen["source"]["succession_pending"] is False
    assert seen["source"]["borrowed"] is False
    assert seen["source"]["max_requests"] == 30000
    assert seen["limits"]["eligible"] is True
    assert seen["limits"]["domain_stop"] is False
    assert successor_pending(root, now_ns=time.time_ns()) is False
    assert (old / "STOP").read_bytes() == stop_bytes
    assert (old / "provider-seed.json").read_bytes() == old_seed

    sealed_config = (source / "config.json").read_bytes()
    sealed_seed = (source / "provider-seed.json").read_bytes()
    capture(source / "captures" / "b", 2000 * NS, open_s=300)
    db = connect(source / "index.sqlite")
    try:
        ingest(db, source / "captures" / "b", "b")
    finally:
        db.close()
    assert current_source(root) == source
    guard_source(source)
    rows, _meta = view(source, time.time_ns(), since=0)
    assert {row["open_s"] for row in rows} == {0, 300}
    _due(root)
    restarted = tick(root)
    assert restarted["mode"] == "auto"
    assert restarted["auto_reason"] is None
    assert restarted["limits"]["eligible"] is True
    assert current_source(root) == source
    assert (source / "config.json").read_bytes() == sealed_config
    assert (source / "provider-seed.json").read_bytes() == sealed_seed
    assert sorted(path.name for path in (root / "staging").iterdir()) == ["attempt-0001"]
    assert (root / "config.json").read_bytes() == operator_config
    assert (old / "STOP").read_bytes() == stop_bytes
    assert (old / "provider-seed.json").read_bytes() == old_seed
    _quiet(handoff["started"])


def test_early_stop_pauses_without_takeover(tmp_path, handoff):
    root, old, _moment, _unused = _build(tmp_path, old_offset_ns=3600 * NS, stop=None)
    _enter_auto(root)
    held = adapters.inspect(root)
    assert held["source"]["stopped"] is False
    assert held["source"]["succession_pending"] is False
    assert held["limits"]["domain_stop"] is False
    assert held["limits"]["eligible"] is True
    assert successor_pending(root) is False
    borrowed = adapters.maintain(
        root,
        {"mode": "auto", "phase": "waiting", "collect_profile": "balanced"},
    )
    assert borrowed["status"] == "running"
    assert borrowed["source_succession"]["phase"] == "borrowed"
    assert borrowed["source_succession"]["created"] is False
    assert not (root / "source").exists()
    assert handoff["started"] == []

    stop_bytes = _fatal_stop(old, "provider_auth")
    blocked = adapters.inspect(root)
    assert blocked["source"]["stopped"] is True
    assert blocked["source"]["succession_pending"] is False
    assert blocked["limits"]["domain_stop"] is True
    assert blocked["limits"]["eligible"] is False
    preview = status(root)
    assert preview["mode"] == "auto"
    assert preview["domain_stop"] is True
    assert _checks(preview)["domain_stop"]["pass"] is False
    assert _checks(preview)["eligible"]["pass"] is False
    assert "domain_stop" in preview["gates"]["auto"]["reasons"]

    paused = tick(root)
    assert paused["mode"] == "paused"
    assert paused["auto_reason"] == "domain_stop"
    assert paused["resume_mode"] == "auto"
    assert not (root / "source").exists()
    assert not (root / "successor-ready.json").exists()
    assert (old / "STOP").read_bytes() == stop_bytes
    assert handoff["started"] == []
    assert handoff["spawned"] == []
    _quiet(handoff["started"])


def test_unknown_stop_after_expiry_pauses_without_activation(tmp_path, handoff):
    root, old, moment, _unused = _build(tmp_path, old_offset_ns=0, stop=None)
    waiting = adapters.inspect(root)
    assert waiting["source"]["stopped"] is False
    assert waiting["source"]["succession_pending"] is True
    assert waiting["limits"]["domain_stop"] is False
    assert waiting["limits"]["eligible"] is False
    assert successor_pending(root, now_ns=moment) is True
    _enter_auto(root)
    entered = status(root)
    assert entered["mode"] == "auto"
    assert _checks(entered)["domain_stop"]["pass"] is True
    assert _checks(entered)["eligible"]["pass"] is False
    assert "ineligible" in entered["gates"]["auto"]["reasons"]
    assert "domain_stop" not in entered["gates"]["auto"]["reasons"]

    stop_bytes = _fatal_stop(old, "worker_failed")
    blocked = adapters.inspect(root)
    assert blocked["source"]["stopped"] is True
    assert blocked["source"]["succession_pending"] is False
    assert blocked["limits"]["domain_stop"] is True
    assert blocked["limits"]["eligible"] is False
    assert successor_pending(root, now_ns=time.time_ns()) is False
    paused = tick(root)
    assert paused["mode"] == "paused"
    assert paused["auto_reason"] == "domain_stop"
    assert not (root / "source").exists()
    assert not (root / "successor-ready.json").exists()
    assert (old / "STOP").read_bytes() == stop_bytes
    assert handoff["started"] == []
    _quiet(handoff["started"])


def test_failed_raw_audit_does_not_activate(tmp_path, handoff):
    root, old, moment, stop_bytes = _build(tmp_path, old_offset_ns=0, stop="known")
    db = connect(old / "index.sqlite")
    db.execute("update firsts set payload='{}'")
    db.commit()
    db.close()
    assert successor_pending(root, now_ns=moment) is True
    _enter_auto(root)
    outcome = tick(root)
    assert not (root / "source").exists()
    assert not (root / "successor-ready.json").exists()
    assert not (root / "source" / "activation.json").exists()
    assert not (root / "source" / "qualification.json").exists()
    assert (old / "STOP").read_bytes() == stop_bytes
    assert handoff["started"] == []
    assert "adapter_error" in [item["reason"] for item in events(root)["events"]]
    assert outcome["mode"] == "auto"
    _quiet(handoff["started"])


def test_user_pause_does_not_autoresume_or_authorize(tmp_path, handoff):
    root, old, _moment, stop_bytes = _build(tmp_path, old_offset_ns=0, stop="known")
    operator_config = (root / "config.json").read_bytes()
    _enter_auto(root)
    paused = command(root, "user-pause", "pause", {})
    assert paused["ok"] is True, paused
    assert status(root)["mode"] == "paused"
    assert status(root)["resume_mode"] == "auto"
    first = tick(root)
    assert first["mode"] == "paused"
    assert first["auto_reason"] is None
    _due(root)
    second = tick(root)
    assert second["mode"] == "paused"
    assert second["resume_mode"] == "auto"
    assert second["auto_reason"] is None
    assert not (root / "source").exists()
    assert not (root / "successor-ready.json").exists()
    assert handoff["started"] == []
    assert handoff["spawned"] == []
    assert (old / "STOP").read_bytes() == stop_bytes
    assert (root / "config.json").read_bytes() == operator_config
    assert successor_pending(root, now_ns=time.time_ns()) is True
    _quiet(handoff["started"])
