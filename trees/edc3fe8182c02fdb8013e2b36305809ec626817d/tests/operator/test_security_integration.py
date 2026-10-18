"""Adversarial API and recovery contract for the local operator.

These tests encode the security properties. A failure is a defect: the parent
fixes production code. The adapter is a synthetic stub reached only through
MEMECOIN_OPERATOR_ADAPTER, never through the HTTP body or config.json.
"""

from __future__ import annotations

import http.client
import json
import os
import signal
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from memecoin_operator import control, deployment
from memecoin_operator.web import make_server

NS = 1_000_000_000
T0 = 1_800_000_000 * NS
HASH_A = "ab" * 16
HASH_B = "cd" * 16
SECRET = "SECRETABSPATH"

ADAPTER = """
import json
import os
import time
from pathlib import Path

STATE = Path({state!r})

def _s():
    return json.loads(STATE.read_text())

def _put(update):
    data = _s()
    data.update(update)
    STATE.write_text(json.dumps(data))

def inspect(root):
    data = _s()
    if data.get("inspect_raise"):
        raise RuntimeError("inspect down")
    return {{
        "source": {{
            "matured_samples": data.get("matured", 500),
            "healthy": True,
            "provider": "geckoterminal",
            "artifact": "{secret}/source",
        }},
        "datasets": data.get("datasets", []),
        "candidates": data.get("candidates", []),
        "exams": data.get("exams", []),
        "paper": {{
            "decisions": [{{
                "action": "learned_exit",
                "reason": "learned_exit",
                "pool": "poolA",
            }}]
        }},
        "coverage": {{"bars": 4, "pools": 1, "path": "{secret}/coverage"}},
        "legacy": [{{"id": "b10", "port": 8790, "root": "{secret}/legacy"}}],
        "limits": {{
            "eligible": data.get("eligible", True),
            "domain_stop": data.get("domain_stop", False),
            "max_open_exams": data.get("max_open_exams", 4),
        }},
    }}

def run(root, action, params, output):
    data = _s()
    report = os.environ.get("OPERATOR_SECURITY_REPORT")
    if report:
        import memecoin_operator.control as operator_control
        Path(report).write_text(operator_control.__file__)
    time.sleep(data.get("sleep") or 0)
    if data.get("fail"):
        return {{"ok": False, "reason": "adapter_error"}}
    if action == "collect":
        body = {{"profile": params.get("profile")}}
    elif action == "snapshot":
        body = {{
            "dataset_id": data.get("snapshot_id", "snapZ"),
            "snapshot_hash": "{hash_a}",
            "matured_samples": data.get("matured", 500),
        }}
    elif action == "train":
        body = {{
            "candidate_id": "candX",
            "snapshot_id": params.get("snapshot_id"),
            "snapshot_hash": "{hash_a}",
        }}
    elif action == "paper":
        body = {{
            "exam_id": data.get("exam_id", "exam1"),
            "candidate_id": params.get("candidate_id"),
        }}
    else:
        return {{"ok": False, "reason": "adapter_error"}}
    if "ok_value" in data:
        body["ok"] = data["ok_value"]
    else:
        body["ok"] = True
    if "promoted" in data:
        body["promoted"] = data["promoted"]
    if data.get("champion"):
        body["champion"] = None
    if data.get("partial"):
        body.pop("dataset_id", None)
        body.pop("candidate_id", None)
        body.pop("exam_id", None)
    return body

def maintain(root, state):
    progress = {{"step": 1, "steps": 4, "checkpoint_id": "linear-7"}}
    return {{"ok": True, "training_progress": progress}}

def stop(root, full=False):
    _put({{"stopped": {{"full": bool(full)}}}})
    return {{"stopped": True}}
"""


@pytest.fixture(autouse=True)
def _reset_clock():
    yield
    control.set_clock(None)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state_path = tmp_path / "adapter-state.json"
    adapter_path = tmp_path / "adapter.py"
    state_path.write_text(
        json.dumps(
            {
                "domain_stop": False,
                "eligible": True,
                "inspect_raise": False,
                "sleep": 0,
                "fail": False,
                "matured": 500,
                "datasets": [
                    {"dataset_id": "snapA", "snapshot_hash": HASH_A, "matured_samples": 500},
                    {"dataset_id": "snapB", "snapshot_hash": HASH_B, "matured_samples": 500},
                ],
                "candidates": [
                    {"candidate_id": "candA", "status": "completed"},
                    {"candidate_id": "candB", "status": "completed"},
                ],
                "exams": [],
                "max_open_exams": 4,
            }
        )
    )
    adapter_path.write_text(ADAPTER.format(state=str(state_path), secret=SECRET, hash_a=HASH_A))
    monkeypatch.setenv("MEMECOIN_OPERATOR_ADAPTER", str(adapter_path))
    monkeypatch.delenv("OPERATOR_SECURITY_REPORT", raising=False)

    def update(**values: object) -> dict:
        current = json.loads(state_path.read_text())
        current.update(values)
        state_path.write_text(json.dumps(current))
        return current

    return {"root_base": tmp_path, "state": state_path, "update": update, "adapter": adapter_path}


def _clock_at(moment: list[int]) -> None:
    control.set_clock(lambda: moment[0])


def _boot(world: dict, name: str, moment: list[int] | None = None, **config: object) -> Path:
    when = moment if moment is not None else [T0]
    _clock_at(when)
    root = world["root_base"] / name
    payload = {
        "expires_ns": when[0] + 3 * 86400 * NS,
        "created_ns": when[0],
        "max_training_runs": 6,
        "min_refit_interval_s": 86400,
        "minimum_new_matured": 100,
        "run_envelope_s": 7 * 86400,
        "paid_budget_usd": 0,
        "real_execution_enabled": False,
    }
    payload.update(config)
    control.initialize(root, payload)
    return root


def _rows(root: Path) -> list[sqlite3.Row]:
    db = sqlite3.connect(root / "operator.sqlite")
    db.row_factory = sqlite3.Row
    rows = list(db.execute("SELECT * FROM jobs ORDER BY created_ns, job_id"))
    db.close()
    return rows


def _pubs(root: Path) -> list[sqlite3.Row]:
    db = sqlite3.connect(root / "operator.sqlite")
    db.row_factory = sqlite3.Row
    rows = list(db.execute("SELECT * FROM publications ORDER BY published_ns"))
    db.close()
    return rows


def _cycle(root: Path) -> dict:
    db = sqlite3.connect(root / "operator.sqlite")
    row = db.execute("SELECT cycle_json FROM revisions ORDER BY revision DESC LIMIT 1").fetchone()
    db.close()
    return json.loads(row[0])


def _kill(root: Path) -> None:
    database = root / "operator.sqlite"
    if not database.exists():
        return
    db = sqlite3.connect(database)
    pids = [row[0] for row in db.execute("SELECT pid FROM jobs WHERE pid IS NOT NULL")]
    db.close()
    for pid in pids:
        try:
            os.killpg(int(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            continue


def _dead(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _finish(root: Path, index: int) -> sqlite3.Row:
    return _finish_id(root, _rows(root)[index]["job_id"])


def _finish_id(root: Path, job_id: str) -> sqlite3.Row:
    row = next(item for item in _rows(root) if item["job_id"] == job_id)
    if row["status"] in {"completed", "failed", "interrupted"}:
        return row
    if row["pid"] and not _dead(int(row["pid"])):
        end = time.time() + 8
        while time.time() < end and not _dead(int(row["pid"])):
            time.sleep(0.05)
    control.tick(root)
    return next(item for item in _rows(root) if item["job_id"] == job_id)


def _action_jobs(root: Path, action: str) -> list[sqlite3.Row]:
    return [row for row in _rows(root) if row["action"] == action]


@pytest.fixture
def root(world: dict):
    moment = [T0]
    path = _boot(world, "op", moment)
    path_holder = {"path": path, "now": moment}
    yield path_holder
    _kill(path_holder["path"])


def test_initialize_survives_schema_and_rejected_command_rolls_back(root):
    """executescript used to commit the caller's BEGIN and then fail COMMIT."""
    path = root["path"]
    again = control.initialize(path)
    assert again["ok"] is True
    assert again["revision"] >= 1
    before = control.status(path)["revision"]
    denied = control.command(path, "bad id", "snapshot", {})
    assert denied["reason"] == "bad_id"
    assert control.status(path)["revision"] == before
    assert _rows(path) == []


def test_host_origin_csrf_content_type_body_method_and_path(world):
    moment = [time.time_ns()]
    path = _boot(world, "http", moment, expires_ns=moment[0] + 86400 * NS)
    server = make_server(path, 0)
    port = int(server.server_address[1])
    assert server.server_address[0] == "127.0.0.1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        token = _get(port, "/api/bootstrap")
        assert token["token"]
        assert "Access-Control-Allow-Origin" not in _headers(port, "GET", "/api/bootstrap")
        page = _raw(port, "GET", "/")
        assert page.status == 200
        assert token["token"].encode() in page.body
        assert SECRET.encode() not in page.body

        blocked = _raw(
            port,
            "POST",
            "/api/command",
            _body("no-token", "pause"),
            host=f"evil.example:{port}",
            origin=f"http://127.0.0.1:{port}",
            token=token["token"],
        )
        assert blocked.status == 403
        assert json.loads(blocked.body)["reason"] == "bad_host"

        no_origin = _raw(
            port,
            "POST",
            "/api/command",
            _body("no-origin", "pause"),
            token=token["token"],
            origin=None,
        )
        assert no_origin.status == 403
        assert json.loads(no_origin.body)["reason"] == "bad_origin"

        evil = _raw(
            port,
            "POST",
            "/api/command",
            _body("evil-origin", "pause"),
            token=token["token"],
            origin="http://evil.example",
        )
        assert evil.status == 403
        null_origin = _raw(
            port,
            "POST",
            "/api/command",
            _body("null-origin", "pause"),
            token=token["token"],
            origin="null",
        )
        assert null_origin.status == 403
        slash = _raw(
            port,
            "POST",
            "/api/command",
            _body("slash-origin", "pause"),
            token=token["token"],
            origin=f"http://127.0.0.1:{port}/",
        )
        assert slash.status == 403

        bad_token = _raw(
            port,
            "POST",
            "/api/command",
            _body("bad-token", "pause"),
            token="not-the-token",
        )
        assert bad_token.status == 403
        assert json.loads(bad_token.body)["reason"] == "bad_token"

        plain = _raw(
            port,
            "POST",
            "/api/command",
            _body("plain", "pause"),
            token=token["token"],
            content_type="text/plain",
        )
        assert plain.status == 400
        assert json.loads(plain.body)["reason"] == "bad_content_type"

        charset = _raw(
            port,
            "POST",
            "/api/command",
            _body("charset", "pause"),
            token=token["token"],
            content_type="application/json; charset=utf-8",
        )
        assert charset.status == 200
        assert json.loads(charset.body)["reason"] == "paused"

        huge = json.dumps(
            {"request_id": "huge", "action": "resume", "params": {}, "pad": "A" * 70000}
        ).encode()
        assert len(huge) > 65536
        limited = _raw(port, "POST", "/api/command", huge, token=token["token"])
        assert limited.status == 400
        assert json.loads(limited.body)["reason"] == "body_limit"
        assert control.status(path)["mode"] == "paused"

        missing_len = _raw(
            port,
            "POST",
            "/api/command",
            b"",
            token=token["token"],
            content_length="0",
        )
        assert missing_len.status == 400

        put = _raw(port, "PUT", "/api/command", _body("put", "resume"), token=token["token"])
        assert put.status == 405
        delete = _raw(port, "DELETE", "/api/status", b"", token=token["token"])
        assert delete.status == 405
        get_cmd = _raw(port, "GET", "/api/command")
        assert get_cmd.status == 405
        post_status = _raw(
            port, "POST", "/api/status", _body("status", "resume"), token=token["token"]
        )
        assert post_status.status == 404
        queried = _raw(
            port,
            "POST",
            "/api/command?next=/etc/passwd",
            _body("query", "resume"),
            token=token["token"],
        )
        assert queried.status == 404
        traversal = _raw(port, "GET", "/api/../../etc/passwd")
        assert traversal.status == 404
        assert b"root:" not in traversal.body
        assert str(path).encode() not in traversal.body
        files = _raw(port, "GET", "/operator.sqlite")
        assert files.status == 404
        extra = _raw(port, "GET", "/api/events?after=0&limit=1&path=../config.json")
        assert extra.status == 400
        forwarded = _raw(
            port,
            "POST",
            "/api/command",
            _body("forwarded", "resume"),
            token=token["token"],
            extra={"X-Forwarded-Host": "evil.example"},
        )
        assert forwarded.status == 200
        status = _get(port, "/api/status")
        assert SECRET not in json.dumps(status)
        assert status["promotion"] is False
        assert status["paper"]["decisions"][0]["action"] is None
        assert status["engine"]["learned_exit"] is False
        assert _rows(path) == []
    finally:
        server.shutdown_background()
        _kill(path)


def test_duplicate_request_is_single_effect_and_payload_conflict(root):
    path = root["path"]
    results: list[dict] = []

    def once() -> None:
        results.append(control.command(path, "same-id", "pause", {}))

    threads = [threading.Thread(target=once) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    originals = [body for body in results if body.get("duplicate") is False]
    assert len(originals) == 1
    assert originals[0]["ok"] is True
    assert all(body["reason"] in {"paused", "already_paused"} for body in results)
    assert control.status(path)["mode"] == "paused"
    conflict = control.command(path, "same-id", "resume", {})
    assert conflict["http_status"] == 409
    assert conflict["reason"] == "conflict"
    assert control.status(path)["mode"] == "paused"
    replay = control.command(path, "same-id", "pause", {})
    assert replay["duplicate"] is True
    assert control.status(path)["revision"] == originals[0]["revision"]


def test_shell_paths_and_unknown_actions_do_not_execute(root):
    path = root["path"]
    touched = root["path"].parent / "pwned"
    for request_id, action, params in (
        ("sh1", "snapshot; touch " + str(touched), {}),
        ("sh2", "os.system", {}),
        ("sh3", "../snapshot", {}),
        ("sh4", "train", {"snapshot_id": "../etc/passwd"}),
        ("sh5", "collect", {"profile": "balanced; touch " + str(touched)}),
        ("sh6", "paper", {"candidate_id": "candA", "adapter": str(touched)}),
        ("sh7", "auto", {"MEMECOIN_OPERATOR_ADAPTER": str(touched)}),
    ):
        body = control.command(path, request_id, action, params)
        assert body["ok"] is False
        assert body["job_id"] is None
    assert not touched.exists()
    assert _rows(path) == []
    quoted = control.command(path, "id' OR 1=1", "pause", {})
    assert quoted["reason"] == "bad_id"


def test_pause_stop_and_domain_stop_block_new_work_but_resume_keeps_domain(world, root):
    path = root["path"]
    assert control.command(path, "p", "pause", {})["reason"] == "paused"
    assert control.command(path, "s", "snapshot", {})["reason"] == "paused"
    assert control.command(path, "st", "stop", {})["reason"] == "not_confirmed"
    assert control.status(path)["mode"] == "paused"
    stopped = control.command(path, "stop", "stop", {"confirm": True})
    assert stopped["ok"] is True
    assert json.loads(world["state"].read_text())["stopped"]["full"] is False
    marker = path / "evidence.txt"
    marker.write_text("keep")
    control.command(path, "all", "stop_all", {"confirm": True})
    assert marker.read_text() == "keep"
    assert json.loads(world["state"].read_text())["stopped"]["full"] is True
    world["update"](domain_stop=True)
    (path / "STOP").write_text('{"reason":"operator_stop"}\n')
    resumed = control.command(path, "re", "resume", {})
    assert resumed["ok"] is True
    assert (path / "STOP").exists()
    blocked = control.command(path, "c", "collect", {"profile": "balanced"})
    assert blocked["reason"] == "domain_stop"
    train = control.command(path, "t", "train", {"snapshot_id": "snapA"})
    paper = control.command(path, "pa", "paper", {"candidate_id": "candA"})
    assert train["reason"] == "domain_stop"
    assert paper["reason"] == "domain_stop"


def test_domain_stop_blocks_snapshot(world, root):
    world["update"](domain_stop=True)
    body = control.command(root["path"], "snap", "snapshot", {})
    assert body["ok"] is False
    assert body["reason"] == "domain_stop"
    assert _rows(root["path"]) == []


def test_queued_work_cannot_start_after_expiry_via_resume(root):
    path = root["path"]
    assert control.command(path, "q", "snapshot", {})["ok"] is True
    assert _rows(path)[0]["status"] == "queued"
    control.command(path, "p", "pause", {})
    root["now"][0] = T0 + 5 * 86400 * NS
    assert control.status(path)["expired"] is True
    control.command(path, "re", "resume", {})
    control.tick(path)
    job = _rows(path)[0]
    assert job["status"] in {"queued", "interrupted"}
    assert job["pid"] is None


def test_queued_work_cannot_start_after_domain_stop_via_resume(world, root):
    path = root["path"]
    assert control.command(path, "c", "collect", {"profile": "history"})["ok"] is True
    control.command(path, "p", "pause", {})
    world["update"](domain_stop=True)
    control.command(path, "re", "resume", {})
    control.tick(path)
    queued = _rows(path)[0]
    assert queued["status"] in {"queued", "interrupted"}
    assert queued["pid"] is None


def test_manual_interval_and_quota_hold_under_pause_resume(world, root):
    path = root["path"]
    world["update"](sleep=0)
    first = control.command(path, "t1", "train", {"snapshot_id": "snapA"})
    assert first["ok"] is True
    control.tick(path)
    _finish(path, 0)
    root["now"][0] = T0 + 3600 * NS
    second = control.command(path, "t2", "train", {"snapshot_id": "snapB"})
    assert second["reason"] == "interval"
    assert len(_rows(path)) == 1

    tight = _boot(world, "quota", [T0], max_training_runs=1, min_refit_interval_s=0)
    try:
        world["update"](fail=True)
        assert control.command(tight, "f", "train", {"snapshot_id": "snapA"})["ok"] is True
        control.tick(tight)
        failed = _finish(tight, 0)
        assert failed["status"] == "failed"
        assert failed["error_reason"] == "adapter_error"
        control.command(tight, "p", "pause", {})
        control.command(tight, "re", "resume", {})
        again = control.command(tight, "f2", "train", {"snapshot_id": "snapB"})
        assert again["reason"] == "quota"
        assert control.status(tight)["gates"]["runs_used"] == 1
    finally:
        _kill(tight)


def test_auto_train_cannot_bypass_24h_after_a_manual_reservation(world, root):
    path = _drive_auto_snapshot(world, root["path"])
    assert _cycle(path)["expect"] == "train"
    assert _cycle(path)["snapshot_id"] == "snapZ"
    assert control.command(path, "re", "resume", {})["mode"] == "auto"
    manual = control.command(path, "manual", "train", {"snapshot_id": "snapB"})
    assert manual["ok"] is True
    control.tick(path)
    finished = _finish_id(path, manual["job_id"])
    assert finished["status"] == "completed"
    assert finished["origin"] == "manual"
    control.tick(path)
    trains = _action_jobs(path, "train")
    assert [row["origin"] for row in trains] == ["manual"]


def test_auto_train_respects_ineligible(world, root):
    path = _drive_auto_snapshot(world, root["path"])
    world["update"](eligible=False)
    control.command(path, "re", "resume", {})
    control.tick(path)
    assert sorted(row["action"] for row in _rows(path)) == ["collect", "snapshot"]
    assert _action_jobs(path, "train") == []
    assert control.status(path)["mode"] == "paused"


def test_unreadable_config_fails_closed(root):
    path = root["path"]
    (path / "config.json").write_text("{")
    denied = control.command(path, "g", "snapshot", {})
    assert denied["ok"] is False
    assert denied["reason"] in {"config_unreadable", "expired"}
    assert control.status(path)["expired"] is True
    assert _rows(path) == []


def test_non_integer_expiry_fails_closed(root):
    path = root["path"]
    (path / "config.json").write_text(
        json.dumps({"expires_ns": "later", "max_training_runs": 100, "min_refit_interval_s": 0})
    )
    body = control.command(path, "s", "snapshot", {})
    assert body["ok"] is False
    assert control.status(path)["expired"] is True
    assert control.status(path)["gates"]["runs_max"] == 6


def test_runtime_refuses_config_that_breaks_the_deployment_pin(world, tmp_path: Path):
    moment = [T0]
    source = tmp_path / "source"
    source.mkdir()
    (source / "config.json").write_text("{}\n")
    path = _boot(
        world,
        "pinned",
        moment,
        source_root=str(source),
        expires_ns=T0 + 2 * 86400 * NS,
        created_ns=T0,
    )
    try:
        pinned = deployment.stage(path, tmp_path / "bundle")
        moment[0] = T0 + 2 * 86400 * NS + 1
        assert control.command(path, "old", "snapshot", {})["reason"] == "expired"
        config = json.loads((path / "config.json").read_text())
        config["expires_ns"] = T0 + 4 * 86400 * NS
        config["max_training_runs"] = 100
        config["min_refit_interval_s"] = 0
        (path / "config.json").write_text(json.dumps(config))
        with pytest.raises(ValueError, match="operator_runtime"):
            deployment.guard(path)
        moment[0] = T0 + 3 * 86400 * NS
        forged = control.command(path, "forged", "snapshot", {})
        assert forged["ok"] is False
        assert control.status(path)["expired"] is True
        assert pinned["pin"]
    finally:
        _kill(path)


def test_runtime_refuses_real_execution_and_paid_budget(world):
    moment = [T0]
    path = _boot(world, "live", moment, real_execution_enabled=True, paid_budget_usd=5)
    try:
        with pytest.raises(ValueError, match="operator_virtual_only"):
            deployment.stage(path, world["root_base"] / "live-bundle")
        body = control.command(path, "go", "snapshot", {})
        assert body["ok"] is False
    finally:
        _kill(path)


def test_boolean_promoted_and_champion_are_rejected(world, root):
    path = root["path"]
    world["update"](promoted=True)
    assert control.command(path, "p", "snapshot", {})["ok"] is True
    control.tick(path)
    done = _finish(path, 0)
    assert done["status"] == "failed"
    assert _pubs(path) == []

    champion = _boot(world, "champion", root["now"])
    try:
        world["update"](promoted=False, champion=True)
        control.command(champion, "c", "snapshot", {})
        control.tick(champion)
        assert _finish(champion, 0)["status"] == "failed"
        assert _pubs(champion) == []
    finally:
        _kill(champion)


def test_numeric_promoted_is_rejected(world, root):
    path = root["path"]
    world["update"](champion=False, promoted=1)
    control.command(path, "n", "snapshot", {})
    control.tick(path)
    assert _finish(path, 0)["status"] == "failed"
    assert _pubs(path) == []


def test_zero_ok_is_rejected(world, root):
    path = root["path"]
    world["update"](promoted=False, ok_value=0)
    control.command(path, "z", "snapshot", {})
    control.tick(path)
    assert _finish(path, 0)["status"] == "failed"
    assert _pubs(path) == []


def test_partial_result_is_not_published(world, root):
    path = root["path"]
    world["update"](ok_value=True, partial=True)
    control.command(path, "part", "snapshot", {})
    control.tick(path)
    assert _finish(path, 0)["status"] == "failed"
    assert _pubs(path) == []


def test_success_without_matching_marker_is_not_published(world, root):
    path = root["path"]
    world["update"](sleep=0.4)
    control.command(path, "s", "snapshot", {})
    control.tick(path)
    job = _rows(path)[0]
    art = path / "operator" / "jobs" / job["job_id"]
    end = time.time() + 5
    while time.time() < end and not (art / "operator-published.marker").exists():
        time.sleep(0.05)
    (art / "operator-published.marker").unlink()
    classified = _finish(path, 0)
    assert classified["status"] == "failed"
    assert classified["error_reason"] == "invalid_result"
    assert _pubs(path) == []


def test_tick_classifies_adapter_failure(world, root):
    path = root["path"]
    world["update"](fail=True, sleep=0)
    control.command(path, "f", "snapshot", {})
    control.tick(path)
    failed = _finish(path, 0)
    assert failed["status"] == "failed"
    assert failed["error_reason"] == "adapter_error"
    assert _pubs(path) == []


def test_recover_preserves_adapter_failure(world, root):
    path = root["path"]
    world["update"](fail=True, sleep=0.8)
    control.command(path, "f2", "snapshot", {})
    control.tick(path)
    job = _rows(path)[0]
    art = path / "operator" / "jobs" / job["job_id"]
    end = time.time() + 5
    while time.time() < end and not (art / "operator-result.json").exists():
        time.sleep(0.05)
    assert json.loads((art / "operator-result.json").read_text())["ok"] is False
    assert not (art / "operator-published.marker").exists()
    control.recover(path)
    recovered = _rows(path)[0]
    assert recovered["status"] == "failed"
    assert recovered["error_reason"] == "adapter_error"
    assert _pubs(path) == []


def test_crash_recover_does_not_publish_a_successful_marker(world, root):
    path = root["path"]
    world["update"](sleep=0.4, fail=False)
    control.command(path, "s", "snapshot", {})
    control.tick(path)
    job = _rows(path)[0]
    art = path / "operator" / "jobs" / job["job_id"]
    end = time.time() + 5
    while time.time() < end and not (art / "operator-published.marker").exists():
        time.sleep(0.05)
    assert (art / "operator-published.marker").exists()
    control.recover(path)
    assert _pubs(path) == []
    assert _rows(path)[0]["published"] == 0


def test_same_exam_id_is_not_double_counted(world, root):
    path = root["path"]
    world["update"](max_open_exams=2)
    assert control.command(path, "p1", "paper", {"candidate_id": "candA"})["ok"] is True
    control.tick(path)
    _finish(path, 0)
    world["update"](
        exams=[
            {"exam_id": "exam1", "status": "running", "candidate_id": "candA"},
            {"exam_id": "exam1", "status": "running", "candidate_id": "candA"},
        ]
    )
    assert control.command(path, "p2", "paper", {"candidate_id": "candB"})["ok"] is True


def test_omitted_exam_does_not_consume_capacity_forever(world, root):
    path = root["path"]
    world["update"](max_open_exams=1, exams=[])
    control.command(path, "p", "paper", {"candidate_id": "candA"})
    control.tick(path)
    _finish(path, 0)
    world["update"](exams=[])
    opened = control.command(path, "p2", "paper", {"candidate_id": "candB"})
    assert opened["ok"] is True


def test_past_expires_ns_does_not_count_as_open(world, root):
    path = root["path"]
    world["update"](max_open_exams=1, exams=[])
    control.command(path, "p", "paper", {"candidate_id": "candA"})
    control.tick(path)
    _finish(path, 0)
    world["update"](
        exams=[{"exam_id": "exam1", "status": "running", "expires_ns": 1, "candidate_id": "candA"}]
    )
    body = control.command(path, "p2", "paper", {"candidate_id": "candB"})
    assert body["ok"] is True


def test_exam_projection_follows_adapter_status(world, root):
    path = root["path"]
    control.command(path, "p", "paper", {"candidate_id": "candA"})
    control.tick(path)
    _finish(path, 0)
    world["update"](
        exams=[{"exam_id": "exam1", "status": "expired", "expires_ns": 1, "candidate_id": "candA"}]
    )
    rows = control.status(path)["exams"]
    assert [row["exam_id"] for row in rows] == ["exam1"]
    assert rows[0]["status"] == "expired"


def test_inspect_failure_fails_closed(world, root):
    path = root["path"]
    control.command(path, "s", "snapshot", {})
    control.tick(path)
    _finish(path, 0)
    world["update"](inspect_raise=True)
    denied = control.command(path, "t", "train", {"snapshot_id": "snapZ"})
    assert denied["ok"] is False
    assert denied["reason"] in {"ineligible", "adapter_unavailable", "domain_stop"}
    assert len(_rows(path)) == 1


def test_adapter_override_is_not_api_configurable(world, root):
    path = root["path"]
    planted = root["path"].parent / "other-adapter.py"
    planted.write_text("def inspect(root):\n    raise RuntimeError('planted')\n")
    (path / "config.json").write_text(
        json.dumps(
            {
                "expires_ns": T0 + 86400 * NS,
                "MEMECOIN_OPERATOR_ADAPTER": str(planted),
                "adapter": str(planted),
            }
        )
    )
    body = control.command(path, "s", "snapshot", {"adapter": str(planted)})
    assert body["reason"] == "bad_params"
    accepted = control.command(path, "s2", "snapshot", {})
    assert accepted["ok"] is True
    control.tick(path)
    assert _finish(path, 0)["status"] == "completed"


def test_worker_uses_pinned_runtime_not_the_data_root(world, tmp_path: Path):
    moment = [time.time_ns()]
    source = tmp_path / "source"
    source.mkdir()
    (source / "config.json").write_text("{}\n")
    path = _boot(
        world,
        "pin-worker",
        moment,
        source_root=str(source),
        expires_ns=moment[0] + 2 * 86400 * NS,
        created_ns=moment[0],
    )
    report = tmp_path / "imported.txt"
    try:
        pinned = deployment.stage(path, tmp_path / "pin-bundle")
        os.environ["OPERATOR_SECURITY_REPORT"] = str(report)
        world["update"](sleep=0)
        control.command(path, "s", "snapshot", {})
        control.tick(path)
        _finish(path, 0)
        imported = Path(report.read_text().strip()).resolve()
        bundle_root = Path(pinned["bundle"]) / "runtime"
        assert imported.is_relative_to(bundle_root.resolve())
    finally:
        os.environ.pop("OPERATOR_SECURITY_REPORT", None)
        _kill(path)


def test_data_root_cannot_shadow_the_worker(world, root):
    path = root["path"]
    package = path / "memecoin_operator"
    package.mkdir()
    (package / "__init__.py").write_text("")
    marker = path / "shadow-ran"
    (package / "runtime.py").write_text(
        textwrap.dedent(
            f"""
            from pathlib import Path
            Path({str(marker)!r}).write_text("shadow")
            raise SystemExit(0)
            """
        )
    )
    control.command(path, "s", "snapshot", {})
    control.tick(path)
    time.sleep(0.4)
    control.tick(path)
    assert not marker.exists()


def test_symlink_job_dir_cannot_escape_the_root(world, root):
    path = root["path"]
    outside = root["path"].parent / "outside"
    outside.mkdir()
    jobs = path / "operator" / "jobs"
    jobs.parent.mkdir(parents=True, exist_ok=True)
    jobs.symlink_to(outside, target_is_directory=True)
    control.command(path, "s", "snapshot", {})
    control.tick(path)
    assert list(outside.iterdir()) == []
    assert _rows(path)[0]["status"] in {"failed", "interrupted", "queued"}


def test_second_process_cannot_command_an_owned_root(world, root):
    path = root["path"]
    script = textwrap.dedent(
        """
        import json, os
        from pathlib import Path
        from memecoin_operator import control
        print(json.dumps(control.command(Path(os.environ["ROOT"]), "other-proc", "pause", {})))
        """
    )
    env = os.environ.copy()
    env["ROOT"] = str(path)
    env["MEMECOIN_OPERATOR_ADAPTER"] = str(world["adapter"])
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    body = json.loads(completed.stdout)
    assert body["ok"] is False
    assert control.status(path)["mode"] == "idle"


def test_orphan_worker_exits_without_marker_when_lease_expires(world):
    moment = [time.time_ns()]
    path = _boot(world, "lease", moment, expires_ns=moment[0] + 86400 * NS)
    world["update"](sleep=30)
    job_id = "job_lease_orphan"
    art = path / "operator" / "jobs" / job_id
    art.mkdir(parents=True)
    (art / "operator-request.json").write_text(
        json.dumps({"job_id": job_id, "action": "snapshot", "params": {}})
    )
    lease = path / "operator" / "leases" / f"{job_id}.json"
    lease.parent.mkdir(parents=True)
    lease.write_text(json.dumps({"job_id": job_id, "expires_ns": time.time_ns() + 400_000_000}))
    env = os.environ.copy()
    env["MEMECOIN_OPERATOR_ADAPTER"] = str(world["adapter"])
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "memecoin_operator.runtime",
            "--worker",
            "--root",
            str(path),
            "--job",
            job_id,
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        code = proc.wait(timeout=4)
        assert code == 3
        assert not (art / "operator-published.marker").exists()
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)


def test_missing_expiry_fails_closed(root):
    path = root["path"]
    (path / "config.json").write_text(json.dumps({"max_training_runs": 6}))
    denied = control.command(path, "missing-expiry", "snapshot", {})
    assert denied["ok"] is False
    assert denied["reason"] in {"config_unreadable", "expired"}
    assert control.status(path)["expired"] is True
    assert _rows(path) == []


def test_orphan_dies_after_parent_exit_even_if_lease_is_extended(world):
    now = time.time_ns()
    path = world["root_base"] / "parent-dead"
    world["update"](sleep=30)
    script = textwrap.dedent(
        """
        import json, os, time
        from pathlib import Path
        from memecoin_operator import control
        root = Path(os.environ["ROOT"])
        now = int(os.environ["NOW"])
        control.initialize(root, {
            "expires_ns": now + 86400 * 10**9,
            "created_ns": now,
            "max_training_runs": 6,
            "min_refit_interval_s": 86400,
            "minimum_new_matured": 100,
            "run_envelope_s": 7 * 86400,
            "paid_budget_usd": 0,
            "real_execution_enabled": False,
        })
        body = control.command(root, "queued", "snapshot", {})
        if not body.get("ok"):
            raise SystemExit("command rejected: " + json.dumps(body))
        control.tick(root)
        import sqlite3
        db = sqlite3.connect(root / "operator.sqlite")
        row = db.execute("SELECT pid FROM jobs WHERE status='running'").fetchone()
        db.close()
        if row is None or row[0] is None:
            raise SystemExit("worker was not started")
        pid = int(row[0])
        lease = next((root / "operator" / "leases").glob("*.json"))
        payload = {"job_id": lease.stem, "expires_ns": time.time_ns() + 3600 * 10**9}
        lease.write_text(json.dumps(payload))
        print(pid, flush=True)
        os._exit(0)
        """
    )
    env = os.environ.copy()
    env["ROOT"] = str(path)
    env["NOW"] = str(now)
    env["MEMECOIN_OPERATOR_ADAPTER"] = str(world["adapter"])
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    pid = None
    try:
        assert completed.returncode == 0, completed.stderr
        pid = int(completed.stdout.strip())
        time.sleep(1.5)
        assert _dead(pid)
        arts = list((path / "operator" / "jobs").glob("*/operator-published.marker"))
        assert arts == []
    finally:
        if pid is not None and not _dead(pid):
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        _kill(path)


def _drive_auto_snapshot(world: dict, path: Path) -> Path:
    world["update"](sleep=0, fail=False, domain_stop=False, eligible=True, inspect_raise=False)
    accepted = control.command(path, "collect", "collect", {"profile": "balanced"})
    assert accepted["ok"] is True
    control.tick(path)
    assert _finish_id(path, accepted["job_id"])["status"] == "completed"
    assert control.command(path, "auto", "auto", {})["mode"] == "auto"
    world["update"](sleep=1.5)
    control.tick(path)
    snapshots = _action_jobs(path, "snapshot")
    assert len(snapshots) == 1
    assert snapshots[0]["status"] == "running"
    assert snapshots[0]["origin"] == "auto"
    assert control.command(path, "pause", "pause", {})["mode"] == "paused"
    assert _finish_id(path, snapshots[0]["job_id"])["status"] == "completed"
    assert _cycle(path)["expect"] == "train"
    assert _cycle(path)["snapshot_id"] == "snapZ"
    assert control.status(path)["mode"] == "paused"
    return path


class _Response:
    def __init__(self, status: int, body: bytes, headers: http.client.HTTPMessage) -> None:
        self.status = status
        self.body = body
        self.headers = headers


def _headers(port: int, method: str, target: str) -> dict[str, str]:
    response = _raw(port, method, target)
    return {key.lower(): value for key, value in response.headers.items()}


def _get(port: int, target: str) -> dict:
    response = _raw(port, "GET", target)
    assert response.status == 200
    return json.loads(response.body)


def _body(request_id: str, action: str) -> bytes:
    return json.dumps({"request_id": request_id, "action": action, "params": {}}).encode()


def _raw(
    port: int,
    method: str,
    target: str,
    payload: bytes = b"",
    *,
    host: str | None = None,
    origin: str | None = "AUTO",
    token: str | None = None,
    content_type: str = "application/json",
    content_length: str | None = None,
    extra: dict[str, str] | None = None,
) -> _Response:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.putrequest(method, target, skip_host=True)
    connection.putheader("Host", host or f"127.0.0.1:{port}")
    if origin == "AUTO":
        connection.putheader("Origin", f"http://127.0.0.1:{port}")
    elif origin is not None:
        connection.putheader("Origin", origin)
    if token is not None:
        connection.putheader("X-Operator-Token", token)
    if method in {"POST", "PUT", "DELETE"} or payload:
        connection.putheader("Content-Type", content_type)
        connection.putheader(
            "Content-Length", content_length if content_length is not None else str(len(payload))
        )
    for key, value in (extra or {}).items():
        connection.putheader(key, value)
    connection.endheaders(payload if payload else None)
    reply = connection.getresponse()
    body = reply.read()
    headers = reply.headers
    connection.close()
    return _Response(reply.status, body, headers)
