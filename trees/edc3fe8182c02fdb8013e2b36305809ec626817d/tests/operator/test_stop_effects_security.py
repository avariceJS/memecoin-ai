"""Black-box safety of durable Stop all acknowledgement.

A failed or interrupted adapter effect must stay visible and block resume.
These tests use the public command/status/tick/initialize surface and a
temporary stub. They do not call launchctl or a live operator.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from memecoin_operator import control

NS = 1_000_000_000
HASH = "ab" * 16

ADAPTER = """
import json
import time
from pathlib import Path

STATE = Path({state!r})
LOG = Path({log!r})

def _state():
    return json.loads(STATE.read_text())

def _log(full):
    rows = json.loads(LOG.read_text()) if LOG.exists() else []
    rows.append({{"full": bool(full)}})
    LOG.write_text(json.dumps(rows))

def inspect(root):
    return {{
        "source": {{"matured_samples": 500, "healthy": True, "provider": "geckoterminal"}},
        "datasets": [{{
            "dataset_id": "snapA",
            "snapshot_hash": "{hash}",
            "matured_samples": 500,
        }}],
        "candidates": [{{"candidate_id": "candA", "status": "completed"}}],
        "exams": [],
        "paper": {{"decisions": []}},
        "coverage": {{"bars": 1}},
        "legacy": [],
        "limits": {{"eligible": True, "domain_stop": False, "max_open_exams": 4}},
    }}

def run(root, action, params, output):
    if action == "train":
        return {{
            "ok": True,
            "candidate_id": "candX",
            "snapshot_id": params.get("snapshot_id"),
            "snapshot_hash": "{hash}",
        }}
    if action == "collect":
        return {{"ok": True, "profile": params.get("profile", "balanced")}}
    return {{"ok": True, "dataset_id": "snapZ", "snapshot_hash": "{hash}", "matured_samples": 500}}

def maintain(root, state):
    return {{"ok": True}}

def stop(root, full=False):
    data = _state()
    _log(full)
    Path(data["entered"]).write_text("1")
    mode = data.get("mode", "true")
    if mode == "block":
        release = Path(data["release"])
        deadline = time.time() + 15
        while time.time() < deadline and not release.exists():
            time.sleep(0.05)
        if release.exists():
            mode = release.read_text().strip() or "false"
    if mode == "raise":
        raise RuntimeError("stop failed")
    if mode == "none":
        return None
    if mode == "ok":
        return {{"ok": True}}
    if mode == "false":
        return {{"stopped": False, "ok": True}}
    return {{"stopped": True}}
"""


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state_path = tmp_path / "state.json"
    log_path = tmp_path / "effects.json"
    entered = tmp_path / "entered"
    release = tmp_path / "release"
    state_path.write_text(
        json.dumps({"mode": "true", "entered": str(entered), "release": str(release)})
    )
    adapter = tmp_path / "adapter.py"
    adapter.write_text(ADAPTER.format(state=str(state_path), log=str(log_path), hash=HASH))
    monkeypatch.setenv("MEMECOIN_OPERATOR_ADAPTER", str(adapter))

    def update(**values: object) -> None:
        current = json.loads(state_path.read_text())
        current.update(values)
        state_path.write_text(json.dumps(current))

    def calls() -> list[dict]:
        if not log_path.exists():
            return []
        return json.loads(log_path.read_text())

    return {
        "base": tmp_path,
        "entered": entered,
        "release": release,
        "update": update,
        "calls": calls,
    }


def _boot(world: dict, name: str, **config: object) -> Path:
    now = time.time_ns()
    root = world["base"] / name
    payload: dict[str, object] = {
        "expires_ns": now + 3 * 86400 * NS,
        "created_ns": now,
        "max_training_runs": 6,
        "min_refit_interval_s": 86400,
        "minimum_new_matured": 100,
        "run_envelope_s": 7 * 86400,
        "paid_budget_usd": 0,
        "real_execution_enabled": False,
    }
    payload.update(config)
    started = control.initialize(root, payload)
    assert started["ok"] is True
    return root


def _effects(path: Path) -> dict:
    body = control.status(path)
    effects = body.get("stop_effects")
    assert isinstance(effects, dict)
    assert effects.get("phase") in {"none", "pending", "retrying", "applied", "failed"}
    assert isinstance(effects.get("attempts"), int)
    assert isinstance(effects.get("confirmed"), bool)
    return effects


def _reasons(path: Path) -> set[str]:
    payload = control.events(path, limit=200)
    return {item["reason"] for item in payload["events"]}


def _finish_train(path: Path) -> None:
    deadline = time.time() + 5
    while time.time() < deadline:
        jobs = [job for job in control.status(path)["jobs"] if job["action"] == "train"]
        if jobs and jobs[-1]["status"] in {"completed", "failed", "interrupted"}:
            assert jobs[-1]["status"] == "completed"
            return
        control.tick(path)
        time.sleep(0.05)
    raise AssertionError("train did not finish")


def _stop_all(path: Path, request_id: str) -> dict:
    return control.command(path, request_id, "stop_all", {"confirm": True})


def test_crash_before_ack_retries_on_restart_and_blocks_resume(world):
    """A persisted Stop all whose adapter call dies must run again after restart."""
    path = world["base"] / "crash"
    world["update"](mode="block")
    now = str(time.time_ns())
    script = textwrap.dedent(
        """
        import os
        from pathlib import Path
        from memecoin_operator import control
        root = Path(os.environ["ROOT"])
        now = int(os.environ["NOW"])
        day = 86400 * 10**9
        control.initialize(root, {
            "expires_ns": now + 3 * day,
            "created_ns": now,
            "max_training_runs": 6,
            "min_refit_interval_s": 86400,
            "minimum_new_matured": 100,
            "run_envelope_s": 7 * 86400,
            "paid_budget_usd": 0,
            "real_execution_enabled": False,
        })
        control.command(root, "crash-stop", "stop_all", {"confirm": True})
        """
    )
    env = os.environ.copy()
    env["ROOT"] = str(path)
    env["NOW"] = now
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        start_new_session=True,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.time() + 5
        while time.time() < deadline and not world["entered"].exists():
            if proc.poll() is not None:
                raise AssertionError(proc.stderr.read())
            time.sleep(0.05)
        assert world["entered"].exists()
        observed = control.status(path)
        assert observed["mode"] == "stopped"
        assert observed["stop_effects"]["confirmed"] is False
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=3)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=3)
    at_death = len(world["calls"]())
    assert at_death >= 1
    assert all(call["full"] for call in world["calls"]())
    world["update"](mode="true")
    assert control.initialize(path)["ok"] is True
    if control.status(path)["stop_effects"]["confirmed"] is not True:
        resumed = control.command(path, "resume-too-soon", "resume", {})
        assert resumed["ok"] is False
        assert control.status(path)["mode"] == "stopped"
        control.tick(path)
    assert len(world["calls"]()) > at_death
    effects = _effects(path)
    assert effects["phase"] == "applied"
    assert effects["confirmed"] is True
    assert effects["kind"] == "full"
    assert control.status(path)["mode"] == "stopped"


def test_explicit_false_exception_and_none_are_not_acknowledgements(world):
    """stopped False, a raised stop, and None stay unconfirmed and visible."""
    path = _boot(world, "false")
    world["update"](mode="false")
    assert _stop_all(path, "false-stop")["ok"] is True
    for _ in range(4):
        control.tick(path)
    effects = _effects(path)
    assert effects["phase"] == "failed"
    assert effects["confirmed"] is False
    assert effects["needs_operator"] is True
    assert effects["attempts"] == 3
    assert len(effects["attempt_ns"]) == 3
    assert effects["reason"] == "not_confirmed"
    assert len(world["calls"]()) == 3
    assert "stop_failed" in _reasons(path)
    assert "stop_applied" not in _reasons(path)
    blocked = control.command(path, "resume-false", "resume", {})
    assert blocked["ok"] is False
    assert control.status(path)["mode"] == "stopped"
    control.tick(path)
    assert len(world["calls"]()) == 3
    world["update"](mode="true")
    retried = _stop_all(path, "retry-false")
    assert retried["ok"] is True
    assert retried["reason"] != "already_stopped"
    assert len(world["calls"]()) == 4
    assert _effects(path)["confirmed"] is True

    raised = _boot(world, "raise")
    world["update"](mode="raise")
    before = len(world["calls"]())
    assert _stop_all(raised, "raise-stop")["ok"] is True
    effects = _effects(raised)
    assert effects["phase"] == "failed"
    assert effects["confirmed"] is False
    assert effects["reason"] == "adapter_error"
    assert len(world["calls"]()) == before + 3
    assert "stop_failed" in _reasons(raised)
    assert control.command(raised, "resume-raise", "resume", {})["ok"] is False

    legacy_none = _boot(world, "none")
    world["update"](mode="none")
    before = len(world["calls"]())
    assert _stop_all(legacy_none, "none-stop")["ok"] is True
    effects = _effects(legacy_none)
    assert effects["confirmed"] is False
    assert effects["phase"] == "failed"
    assert len(world["calls"]()) == before + 3


def test_automatic_attempts_are_bounded_until_a_new_request(world):
    """Three failed attempts become a visible failure; only a new id tries again."""
    path = _boot(world, "bound")
    world["update"](mode="false")
    _stop_all(path, "bound-stop")
    for _ in range(6):
        control.tick(path)
    assert len(world["calls"]()) == 3
    effects = _effects(path)
    assert effects["phase"] == "failed"
    assert effects["attempts"] == 3
    assert effects["max_attempts"] == 3
    assert effects["needs_operator"] is True
    control.tick(path)
    assert len(world["calls"]()) == 3
    world["update"](mode="true")
    again = _stop_all(path, "bound-retry")
    assert again["reason"] != "already_stopped"
    assert len(world["calls"]()) == 4
    assert _effects(path)["phase"] == "applied"


def test_resume_and_train_cannot_pass_an_inflight_stop_all(world):
    """Resume and a new train lose while Stop all has not been acknowledged."""
    path = _boot(world, "race", min_refit_interval_s=0)
    world["update"](mode="block")
    holder: dict[str, dict] = {}

    def issue() -> None:
        holder["body"] = _stop_all(path, "inflight")

    thread = threading.Thread(target=issue)
    thread.start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline and not world["entered"].exists():
            time.sleep(0.05)
        assert world["entered"].exists()
        resume = control.command(path, "resume-race", "resume", {})
        train = control.command(path, "train-race", "train", {"snapshot_id": "snapA"})
        assert resume["ok"] is False
        assert train["ok"] is False
        assert control.status(path)["mode"] == "stopped"
        assert [job for job in control.status(path)["jobs"] if job["action"] == "train"] == []
        second = _stop_all(path, "inflight-2")
        assert second["ok"] is True
        assert second["reason"] != "already_stopped"
        world["release"].write_text("false")
        thread.join(timeout=5)
    finally:
        if thread.is_alive():
            world["release"].write_text("false")
            thread.join(timeout=5)
    assert not thread.is_alive()
    effects = _effects(path)
    assert effects["request_id"] == "inflight-2"
    assert effects["confirmed"] is False
    assert effects["phase"] == "failed"
    assert len(world["calls"]()) >= 2
    assert control.command(path, "resume-after", "resume", {})["ok"] is False


def test_acknowledged_stop_is_idempotent_and_keeps_training_allowance(world):
    """An ack is not repeated, and restart does not zero the training quota."""
    path = _boot(world, "ack", max_training_runs=1, min_refit_interval_s=0)
    world["update"](mode="ok")
    assert control.command(path, "train-1", "train", {"snapshot_id": "snapA"})["ok"] is True
    control.tick(path)
    _finish_train(path)
    assert control.status(path)["gates"]["runs_used"] == 1
    assert control.status(path)["gates"]["runs_max"] == 1
    assert _stop_all(path, "ack-stop")["ok"] is True
    effects = _effects(path)
    assert effects["phase"] == "applied"
    assert effects["confirmed"] is True
    assert effects["attempts"] == 1
    assert len(world["calls"]()) == 1
    assert "stop_applied" in _reasons(path)
    duplicate = _stop_all(path, "ack-stop")
    assert duplicate["duplicate"] is True
    assert len(world["calls"]()) == 1
    silent = _stop_all(path, "ack-again")
    assert silent["reason"] == "already_stopped"
    assert len(world["calls"]()) == 1
    assert control.initialize(path)["ok"] is True
    control.tick(path)
    assert len(world["calls"]()) == 1
    restarted = control.status(path)
    assert restarted["gates"]["runs_used"] == 1
    assert restarted["gates"]["runs_max"] == 1
    assert restarted["stop_effects"]["confirmed"] is True
    resumed = control.command(path, "resume-ack", "resume", {})
    assert resumed["ok"] is True
    denied = control.command(path, "train-2", "train", {"snapshot_id": "snapA"})
    assert denied["ok"] is False
    assert denied["reason"] == "quota"
