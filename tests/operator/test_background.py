"""Process-group supervision. Synthetic commands only; no network and no launchd."""

import importlib.util
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from memecoin_ml.data import sha
from memecoin_operator.background import (
    Manager,
    history,
    paper_command,
    source_command,
    source_main,
)

SRC = Path(__file__).resolve().parents[2] / "src"
TICK = """
import os, sys, time
from pathlib import Path
def _deny(event, args):
    if event == "socket.connect":
        raise RuntimeError("network")
sys.addaudithook(_deny)
from memecoin_operational.worker import lease_watchdog
lease_watchdog(int(os.environ["MEMECOIN_LEASE_FD"]))
path = Path(sys.argv[1])
with path.open("a") as handle:
    handle.write(sys.argv[2] + "\\n")
if sys.argv[2] == "sleep":
    time.sleep(30)
    with path.open("a") as handle:
        handle.write("finished\\n")
raise SystemExit(0 if sys.argv[2] == "sleep" else int(sys.argv[2]))
"""
SLEEPER = """
import os, sys, time
from pathlib import Path
from memecoin_operational.worker import lease_watchdog
lease_watchdog(int(os.environ["MEMECOIN_LEASE_FD"]))
folder = Path(sys.argv[1])
with (folder / "pids").open("a") as handle:
    handle.write(str(os.getpid()) + "\\n")
(folder / "ready").write_text("1")
time.sleep(60)
"""
DEADLINE = """
import os, sys, time
from pathlib import Path
folder = Path(sys.argv[1])
pid = os.fork()
if pid == 0:
    (folder / "grand.pid").write_text(str(os.getpid()))
    time.sleep(60)
    (folder / "grand.done").write_text("1")
    os._exit(0)
from memecoin_operational.worker import lease_watchdog
lease_watchdog(int(os.environ["MEMECOIN_LEASE_FD"]))
for _ in range(200):
    if (folder / "grand.pid").exists():
        break
    time.sleep(0.01)
(folder / "child.pid").write_text(str(os.getpid()))
(folder / "ready").write_text("1")
time.sleep(60)
"""
ORPHAN_WORKER = """
import os, sys, time
from pathlib import Path
from memecoin_operational.worker import lease_watchdog
lease_watchdog(int(os.environ["MEMECOIN_LEASE_FD"]))
path = Path(sys.argv[1])
path.write_text("started")
path.with_name("worker.pid").write_text(str(os.getpid()))
time.sleep(1)
path.write_text("ORPHAN")
"""
ORPHAN_PARENT = """
import os, sys, time
from pathlib import Path
from memecoin_operator.background import Manager
root, marker, pidfile, worker, src = sys.argv[1:]
pinned = "import sys\\nsys.path.insert(0, sys.argv.pop(1))\\n" + worker
manager = Manager(Path(root))
result = manager.start(
    "job", [sys.executable, "-I", "-B", "-c", pinned, src, marker], 30
)
Path(pidfile).write_text(str(result["supervisor_pid"]))
for _ in range(100):
    if Path(marker).exists() and Path(marker).read_text() == "started":
        os._exit(0)
    time.sleep(0.05)
os._exit(2)
"""


def _env() -> dict[str, str]:
    env = dict(os.environ)
    prior = [part for part in env.get("PYTHONPATH", "").split(os.pathsep) if part]
    src = str(SRC)
    if src not in prior:
        prior.insert(0, src)
    env["PYTHONPATH"] = os.pathsep.join(prior)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _kill(pid: int) -> None:
    if pid <= 0:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _wait_until(predicate, timeout: float = 15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    return None


def _command(script: str, *args: str) -> list[str]:
    """Fixture argv. Pins this checkout and does not follow PYTHONPATH or cwd."""
    pinned = "import sys\nsys.path.insert(0, sys.argv.pop(1))\n" + script
    return [sys.executable, "-I", "-B", "-c", pinned, str(SRC), *args]


def _mode(root: Path, mode: str) -> None:
    db = sqlite3.connect(root / "operator.sqlite")
    db.execute(
        """CREATE TABLE IF NOT EXISTS revisions (
            revision INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            phase TEXT NOT NULL,
            collect_profile TEXT NOT NULL,
            cycle_json TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_ns INTEGER NOT NULL
        )"""
    )
    db.execute(
        """INSERT INTO revisions(mode, phase, collect_profile, cycle_json, reason, created_ns)
           VALUES (?, ?, 'balanced', '{}', 'test', ?)""",
        (mode, mode, time.time_ns()),
    )
    db.commit()
    db.close()


def _config(root: Path, source: Path, *, init: int, expires: int) -> None:
    body = {
        "additional_requests": 10,
        "expires_ns": expires,
        "init_ns": init,
        "max_background_requests": 10,
        "mode": "operator",
        "paid_budget_usd": 0,
        "product": "operator",
        "profile": "balanced",
        "provider": "geckoterminal",
        "source_root": str(source.resolve()),
    }
    (root / "config.json").write_text(json.dumps(body, sort_keys=True) + "\n")


def _run_main(root: Path) -> subprocess.CompletedProcess[bytes]:
    read_fd, write_fd = os.pipe()
    env = _env()
    env["PYTHONPATH"] = str(root / "planted")
    env["MEMECOIN_LEASE_FD"] = str(read_fd)
    proc = subprocess.Popen(
        source_command(root),
        env=env,
        cwd=root,
        pass_fds=(read_fd,),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    os.close(read_fd)
    try:
        stdout, stderr = proc.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        os.close(write_fd)
        proc.kill()
        stdout, stderr = proc.communicate()
        raise AssertionError(stderr.decode()[:500]) from None
    os.close(write_fd)
    return subprocess.CompletedProcess(proc.args, proc.returncode or 0, stdout, stderr)


def _acq():
    path = Path(__file__).with_name("test_acquisition.py")
    spec = importlib.util.spec_from_file_location("operator_acq_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("acquisition helpers missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(root: Path) -> dict[str, str]:
    rows = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            rows[path.relative_to(root).as_posix()] = sha(path)
    return rows


def _no_uptime(payload: object) -> None:
    raw = json.dumps(payload, sort_keys=True)
    assert "uptime" not in raw
    assert "elapsed" not in raw


def _reap(manager: Manager, key: str, timeout: float = 15) -> dict:
    found = None
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        state = manager.poll()
        for row in state["reaped"]:
            if row["key"] == key:
                found = row
        if found is not None:
            return found
        if not any(row["key"] == key for row in state["running"]):
            break
        time.sleep(0.05)
    raise AssertionError(f"still running {key}")


def test_deadline_kills_group_without_pidfile_or_uptime(tmp_path: Path) -> None:
    innocent = subprocess.Popen(_command("import time; time.sleep(60)"))
    try:
        (tmp_path / "background").mkdir()
        (tmp_path / "slot").mkdir()
        (tmp_path / "background" / "job.pid").write_text(str(innocent.pid))
        manager = Manager(tmp_path)
        started = manager.start("job", _command(DEADLINE, str(tmp_path / "slot")), 12)
        assert started["started"] is True
        assert _wait_until(lambda: (tmp_path / "slot" / "ready").exists())
        receipt = _reap(manager, "job", timeout=20)
        assert receipt["category"] == "child_deadline"
        assert receipt["phase"] == "interrupted"
        _no_uptime(receipt)
        _no_uptime(history(tmp_path, "job"))
        assert history(tmp_path, "job")[0]["phase"] == "begun"
        assert (
            type(receipt["started_ns"]) is int and receipt["finished_ns"] >= receipt["started_ns"]
        )
        child_pid = int((tmp_path / "slot" / "child.pid").read_text())
        grand_pid = int((tmp_path / "slot" / "grand.pid").read_text())
        assert _wait_until(lambda: not _alive(child_pid) and not _alive(grand_pid))
        assert not (tmp_path / "slot" / "grand.done").exists()
        assert (tmp_path / receipt["log"]).is_file()
        assert _alive(innocent.pid)
        blob = (tmp_path / "background" / "history" / "job.jsonl").read_bytes()
        assert manager.poll() == {"running": [], "reaped": []}
        assert (tmp_path / "background" / "history" / "job.jsonl").read_bytes() == blob
    finally:
        manager.stop()
        _kill(innocent.pid)
        innocent.wait(timeout=5)


def test_no_duplicate_across_restart_and_stop_uses_handles(tmp_path: Path) -> None:
    folder = tmp_path / "slot"
    folder.mkdir()
    innocent = subprocess.Popen(_command("import time; time.sleep(60)"))
    manager = Manager(tmp_path)
    other = Manager(tmp_path)
    try:
        (tmp_path / "background" / "pids").mkdir()
        (tmp_path / "background" / "pids" / "job.pid").write_text(str(innocent.pid))
        first = manager.start("job", _command(SLEEPER, str(folder)), 30)
        assert first["started"] is True
        assert _wait_until(lambda: (folder / "ready").exists())
        second = manager.start("job", _command(SLEEPER, str(folder)), 30)
        third = other.start("job", _command(SLEEPER, str(folder)), 30)
        assert second["reason"] == "already_running" and third["reason"] == "already_running"
        assert (folder / "pids").read_text().count("\n") == 1
        (tmp_path / "other").mkdir()
        extra = manager.start("audit", _command(SLEEPER, str(tmp_path / "other")), 30)
        assert extra["started"] is True
        stopped = other.stop()
        assert stopped["cancelled"] == []
        assert _alive(int((folder / "pids").read_text().strip()))
        result = manager.stop()
        assert set(result["cancelled"]) == {"job", "audit"}
        categories = {row["key"]: row["category"] for row in result["tasks"]}
        assert categories == {"job": "operator_cancelled", "audit": "operator_cancelled"}
        _no_uptime(result["tasks"])
        assert _wait_until(lambda: not _alive(int((folder / "pids").read_text().strip())))
        assert _alive(innocent.pid)
        assert not (tmp_path / "STOP").exists()
    finally:
        manager.stop()
        other.stop()
        _kill(innocent.pid)
        innocent.wait(timeout=5)


def test_operator_crash_does_not_leave_the_worker(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    pidfile = tmp_path / "supervisor.pid"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            "import sys\nsys.path.insert(0, sys.argv.pop(1))\n" + ORPHAN_PARENT,
            str(SRC),
            str(tmp_path),
            str(marker),
            str(pidfile),
            ORPHAN_WORKER,
            str(SRC),
        ],
        env=_env(),
    )
    supervisor = 0
    worker = 0
    try:
        proc.wait(timeout=15)
        assert proc.returncode == 0
        supervisor = int(pidfile.read_text())
        assert _wait_until(lambda: not _alive(supervisor), timeout=10)
        assert marker.read_text() == "started"
        worker = int((tmp_path / "worker.pid").read_text())
        assert not _alive(worker)
        rows = history(tmp_path, "job")
        assert [row["phase"] for row in rows] == ["begun", "interrupted"]
        assert rows[-1]["category"] == "operator_interrupted"
        _no_uptime(rows)
    finally:
        proc.kill()
        _kill(supervisor)
        _kill(worker)


def test_completed_attempt_can_run_again(tmp_path: Path) -> None:
    script = """
import os, sys
from pathlib import Path
from memecoin_operational.worker import lease_watchdog
lease_watchdog(int(os.environ["MEMECOIN_LEASE_FD"]))
Path(sys.argv[1]).write_text("ok")
"""
    manager = Manager(tmp_path)
    try:
        first = manager.start("job", _command(script, str(tmp_path / "out")), 10)
        assert first["started"] is True
        receipt = _reap(manager, "job")
        assert receipt["category"] == "completed" and receipt["phase"] == "completed"
        assert (tmp_path / "out").read_text() == "ok"
        _no_uptime(history(tmp_path, "job"))
        before = (tmp_path / "background" / "history" / "job.jsonl").read_bytes()
        assert manager.poll()["reaped"] == []
        assert (tmp_path / "background" / "history" / "job.jsonl").read_bytes() == before
        again = manager.start("job", _command(script, str(tmp_path / "out2")), 10)
        assert again["started"] is True
        second = _reap(manager, "job")
        assert second["category"] == "completed"
        assert not (tmp_path / "background" / "source-fail-closed.json").exists()
    finally:
        manager.stop()


def test_rejects_bad_deadline_and_key(tmp_path: Path) -> None:
    manager = Manager(tmp_path)
    with pytest.raises(ValueError, match="invalid_deadline"):
        manager.start("job", [sys.executable, "-c", "pass"], 601)
    with pytest.raises(ValueError, match="invalid_deadline"):
        manager.start("job", [sys.executable, "-c", "pass"], 0)
    with pytest.raises(ValueError, match="invalid_key"):
        manager.start("../job", [sys.executable, "-c", "pass"], 1)
    assert manager.poll() == {"running": [], "reaped": []}
    assert manager.stop()["cancelled"] == []


def test_source_skips_original_expiry_and_operator_stop(tmp_path: Path) -> None:
    original = tmp_path / "original"
    original.mkdir()
    (original / "sentinel").write_text("keep")
    root = tmp_path / "operator"
    root.mkdir()
    now = time.time_ns()
    _config(root, original, init=now - 50_000_000_000, expires=now + 50_000_000_000)
    _mode(root, "collect")
    (root / "source").mkdir()
    (root / "source" / "sentinel").write_text("owned")
    borrowed = _run_main(root)
    assert borrowed.returncode == 12, borrowed.stderr.decode()[:400]
    assert not (root / "successor-ready.json").exists()
    assert not (root / "background" / "source-fail-closed.json").exists()
    assert (original / "sentinel").read_text() == "keep"
    assert not (root / "source" / "STOP").exists()
    _config(root, original, init=now - 50_000_000_000, expires=now - 1_000_000_000)
    expired = _run_main(root)
    assert expired.returncode == 11, expired.stderr.decode()[:400]
    assert (root / "source" / "sentinel").read_text() == "owned"
    _config(root, original, init=now - 50_000_000_000, expires=now + 50_000_000_000)
    (root / "STOP").write_text(json.dumps({"reason": "operator_stop"}))
    stopped = _run_main(root)
    assert stopped.returncode == 12, stopped.stderr.decode()[:400]
    assert not (root / "source" / "STOP").exists()
    missing = source_main(root)
    assert missing == 1
    assert not (root / "source" / "STOP").exists()


def test_source_policy_pause_cancel_and_unknown_failure(tmp_path: Path, monkeypatch) -> None:
    helpers = _acq()
    now = time.time_ns()
    root, old = helpers.make_operator(tmp_path, old_expires=now, now=now)
    helpers.ensure_successor(root, now_ns=now)
    _mode(root, "collect")
    config = json.loads((root / "config.json").read_text())
    config["data_kind"] = "synthetic_fixture"
    (root / "config.json").write_text(json.dumps(config, sort_keys=True) + "\n")
    (root / "source" / "SYNTHETIC").write_text("synthetic_fixture\n")
    marker = tmp_path / "ticks"
    monkeypatch.setenv("MEMECOIN_SYNTHETIC_TICK", json.dumps(_command(TICK, str(marker), "sleep")))
    old_files = _snapshot(old)
    ready = (root / "successor-ready.json").read_bytes()
    source_config = (root / "source" / "config.json").read_bytes()
    manager = Manager(root)
    try:
        activation = root / "source" / "activation.json"
        hidden = root / "source" / "activation.json.aside"
        activation.rename(hidden)
        denied = manager.start("source", source_command(root), 90)
        assert denied["started"] is True
        inactive = _reap(manager, "source", timeout=25)
        assert inactive["category"] == "inactive"
        assert not marker.exists()
        assert not (root / "source" / "STOP").exists()
        assert not (root / "source" / "requests").exists()
        hidden.rename(activation)
        helpers.authorize_successor(root, now_ns=now + 5)
        paused = manager.start("source", source_command(root), 90)
        assert paused["started"] is True
        assert _wait_until(lambda: marker.exists() and "sleep" in marker.read_text())
        _mode(root, "paused")
        pause_receipt = _reap(manager, "source")
        assert pause_receipt["category"] == "operator_cancelled"
        assert "finished" not in marker.read_text()
        assert not (root / "source" / "STOP").exists()
        _mode(root, "collect")
        monkeypatch.setenv(
            "MEMECOIN_SYNTHETIC_TICK", json.dumps(_command(TICK, str(marker), "sleep"))
        )
        running = manager.start("source", source_command(root), 90)
        assert running["started"] is True
        assert _wait_until(lambda: marker.read_text().count("sleep") == 2)
        stopped = manager.stop()
        assert stopped["tasks"][0]["category"] == "operator_cancelled"
        assert not (root / "source" / "STOP").exists()
        assert "finished" not in marker.read_text()
        monkeypatch.setenv("MEMECOIN_SYNTHETIC_TICK", json.dumps(_command(TICK, str(marker), "0")))
        ok = manager.start("source", source_command(root), 90)
        assert ok["started"] is True
        assert _reap(manager, "source")["category"] == "completed"
        assert not (root / "source" / "STOP").exists()
        monkeypatch.setenv("MEMECOIN_SYNTHETIC_TICK", json.dumps(_command(TICK, str(marker), "1")))
        failed = manager.start("source", source_command(root), 90)
        assert failed["started"] is True
        failure = _reap(manager, "source")
        assert failure["category"] == "worker_failed"
        assert failure["phase"] == "interrupted"
        stop = json.loads((root / "source" / "STOP").read_text())
        assert stop["reason"] == "source_worker_failure"
        assert (root / "background" / "source-fail-closed.json").is_file()
        assert any(path.name.startswith("incident-") for path in (root / "source").iterdir())
        assert (root / "incidents").is_dir()
        blocked = manager.start("source", source_command(root), 90)
        assert blocked["started"] is False and blocked["reason"] == "source_fail_closed"
        assert marker.read_text().count("\n") == marker.read_text().count(
            "1"
        ) + marker.read_text().count("sleep") + marker.read_text().count("0")
        assert "1\n" in marker.read_text()
        assert marker.read_text().count("1\n") == 1
        assert _snapshot(old) == old_files
        assert (root / "successor-ready.json").read_bytes() == ready
        assert (root / "source" / "config.json").read_bytes() == source_config
        assert not (root / "source" / "requests").exists()
        assert not (root / "source" / "journal").exists()
        _no_uptime(history(root, "source"))
    finally:
        manager.stop()


def test_commands_name_the_domain_entry() -> None:
    root = Path("/tmp/operator")
    source = source_command(root)
    assert source[:3] == [sys.executable, "-I", "-B"]
    body = source[4]
    assert source[3] == "-c"
    assert body.index("sys.path.insert") < body.index("source_main")
    assert source[5] == str(SRC)
    assert source[6] == str(root.resolve())
    paper = paper_command(Path("/tmp/runtime"), Path("/tmp/exam"))
    assert paper[:3] == [sys.executable, "-I", "-B"]
    script = paper[4]
    assert script.index("sys.path.insert") < script.index("lease_watchdog")
    assert script.index("lease_watchdog") < script.index("memecoin_paper.runtime")
    assert paper[-3:] == [
        str(Path("/tmp/runtime").resolve()),
        str(Path("/tmp/exam").resolve()),
        "paper",
    ]


def _plant(directory: Path, token: str) -> None:
    files = {
        "memecoin_operator/__init__.py": "",
        "memecoin_operator/background.py": (
            "import os\nfrom pathlib import Path\n"
            f"Path(os.environ['EVIL_MARKER']).write_text({token!r})\n"
            "def supervise(_spec):\n    return 0\n"
            "def source_main(_root):\n    return 0\n"
        ),
        "memecoin_operational/__init__.py": "",
        "memecoin_operational/worker.py": (
            "import os\nfrom pathlib import Path\n"
            "def lease_watchdog(_fd):\n"
            f"    Path(os.environ['EVIL_MARKER']).write_text({token!r})\n"
            "    raise SystemExit(9)\n"
        ),
        "memecoin_paper/__init__.py": "",
        "memecoin_paper/runtime.py": (
            "import os\nfrom pathlib import Path\n"
            "def main():\n"
            f"    Path(os.environ['PIN_MARKER']).write_text('evil-' + {token!r})\n"
        ),
    }
    for name, body in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)


def _runtime(directory: Path) -> None:
    worker = directory / "memecoin_operational"
    paper = directory / "memecoin_paper"
    worker.mkdir(parents=True)
    paper.mkdir()
    (worker / "__init__.py").write_text("")
    (paper / "__init__.py").write_text("")
    (worker / "worker.py").write_text(
        "import os, threading\n"
        "def lease_watchdog(fd):\n"
        "    def watch():\n"
        "        try:\n"
        "            os.read(fd, 1)\n"
        "        finally:\n"
        "            os._exit(125)\n"
        "    threading.Thread(target=watch, daemon=True).start()\n"
    )
    (paper / "runtime.py").write_text(
        "import os, time\n"
        "from pathlib import Path\n"
        "def main():\n"
        "    Path(os.environ['PIN_MARKER']).write_text(\n"
        "        'pinned\\n' + os.environ.get('PYTHONPATH', '')\n"
        "    )\n"
        "    time.sleep(30)\n"
    )


def test_pinned_child_ignores_pythonpath_and_cwd(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "operator"
    root.mkdir()
    planted = tmp_path / "pythonpath"
    runtime = tmp_path / "runtime"
    exam = tmp_path / "exam"
    exam.mkdir()
    evil = tmp_path / "evil"
    pin = tmp_path / "pin"
    _plant(root, "cwd")
    _plant(planted, "pythonpath")
    _runtime(runtime)
    monkeypatch.setenv("PYTHONPATH", str(planted))
    monkeypatch.setenv("EVIL_MARKER", str(evil))
    monkeypatch.setenv("PIN_MARKER", str(pin))
    now = time.time_ns()
    original = tmp_path / "original"
    original.mkdir()
    _config(root, original, init=now - 50_000_000_000, expires=now + 50_000_000_000)
    _mode(root, "collect")
    manager = Manager(root)
    try:
        started = manager.start("exam-pin", paper_command(runtime, exam), 150)
        assert started["started"] is True
        assert _wait_until(lambda: pin.is_file(), timeout=20)
        text = pin.read_text()
        assert text.startswith("pinned\n") and str(planted) in text
        assert not evil.exists()
        command = subprocess.check_output(
            ["ps", "-p", str(started["supervisor_pid"]), "-ww", "-o", "command="],
            text=True,
        )
        assert "-I" in command.split() and str(SRC) in command
        stopped = manager.stop()
        assert stopped["tasks"][0]["category"] == "operator_cancelled"
        assert not evil.exists()
        source = manager.start("source", source_command(root), 270)
        assert source["started"] is True
        receipt = _reap(manager, "source", timeout=25)
        assert receipt["category"] == "inactive"
        assert not (root / "source" / "STOP").exists()
        assert not (root / "background" / "source-fail-closed.json").exists()
        assert not evil.exists()
    finally:
        manager.stop()


NESTED = """
import os, signal, sys, threading, time
from pathlib import Path
def _deny(event, args):
    if event == "socket.connect":
        raise RuntimeError("network")
sys.addaudithook(_deny)
folder = Path(sys.argv[1])
pid = os.fork()
if pid == 0:
    (folder / "grand.pid").write_text(str(os.getpid()))
    time.sleep(60)
    (folder / "grand.done").write_text("1")
    os._exit(0)
def _watch():
    try:
        os.read(int(os.environ["MEMECOIN_LEASE_FD"]), 1)
    finally:
        try:
            os.killpg(os.getpid(), signal.SIGKILL)
        finally:
            os._exit(125)
threading.Thread(target=_watch, daemon=True).start()
(folder / "child.pid").write_text(str(os.getpid()))
(folder / "ready").write_text("1")
time.sleep(45)
(folder / "finished").write_text("1")
"""
CRASH_PARENT = """
import os, sys, time
from pathlib import Path
from memecoin_operator.background import Manager, source_command
root, ready, pidfile = sys.argv[1:]
manager = Manager(Path(root))
result = manager.start("source", source_command(Path(root)), 270)
Path(pidfile).write_text(str(result.get("supervisor_pid") or 0))
if not result.get("started"):
    os._exit(3)
for _ in range(400):
    if Path(ready).exists():
        os._exit(0)
    time.sleep(0.05)
os._exit(2)
"""


def test_nested_stop_and_crash_leave_no_stop_or_orphan(tmp_path: Path, monkeypatch) -> None:
    helpers = _acq()
    now = time.time_ns()
    root, old = helpers.make_operator(tmp_path, old_expires=now, now=now)
    helpers.ensure_successor(root, now_ns=now)
    _mode(root, "collect")
    config = json.loads((root / "config.json").read_text())
    config["data_kind"] = "synthetic_fixture"
    (root / "config.json").write_text(json.dumps(config, sort_keys=True) + "\n")
    (root / "source" / "SYNTHETIC").write_text("synthetic_fixture\n")
    old_files = _snapshot(old)
    ready_bytes = (root / "successor-ready.json").read_bytes()
    slot = tmp_path / "slot-stop"
    slot.mkdir()
    monkeypatch.setenv("MEMECOIN_SYNTHETIC_TICK", json.dumps(_command(NESTED, str(slot))))
    manager = Manager(root)
    supervisor = child = grand = 0
    proc = None
    try:
        started = manager.start("source", source_command(root), 270)
        assert started["started"] is True
        assert _wait_until(
            lambda: (slot / "ready").is_file() and (slot / "grand.pid").is_file(), timeout=25
        )
        child = int((slot / "child.pid").read_text())
        grand = int((slot / "grand.pid").read_text())
        stopped = manager.stop()
        assert stopped["tasks"][0]["category"] == "operator_cancelled"
        assert _wait_until(lambda: not _alive(child) and not _alive(grand))
        assert not (slot / "finished").exists() and not (slot / "grand.done").exists()
        assert not (root / "source" / "STOP").exists()
        assert not (root / "background" / "source-fail-closed.json").exists()
        crash = tmp_path / "slot-crash"
        crash.mkdir()
        env = _env()
        env["PYTHONPATH"] = str(tmp_path / "not-a-package")
        env["MEMECOIN_SYNTHETIC_TICK"] = json.dumps(_command(NESTED, str(crash)))
        pidfile = tmp_path / "supervisor.pid"
        proc = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                "import sys\nsys.path.insert(0, sys.argv.pop(1))\n" + CRASH_PARENT,
                str(SRC),
                str(root),
                str(crash / "ready"),
                str(pidfile),
            ],
            env=env,
        )
        proc.wait(timeout=30)
        assert proc.returncode == 0, proc.returncode
        supervisor = int(pidfile.read_text())
        assert _wait_until(
            lambda: (crash / "child.pid").is_file() and (crash / "grand.pid").is_file()
        )
        child = int((crash / "child.pid").read_text())
        grand = int((crash / "grand.pid").read_text())
        assert _wait_until(
            lambda: not _alive(supervisor) and not _alive(child) and not _alive(grand), timeout=15
        )
        assert not (crash / "finished").exists() and not (crash / "grand.done").exists()
        assert history(root, "source")[-1]["category"] == "operator_interrupted"
        assert not (root / "source" / "STOP").exists()
        assert not (root / "background" / "source-fail-closed.json").exists()
        monkeypatch.setenv(
            "MEMECOIN_SYNTHETIC_TICK", json.dumps(_command(TICK, str(tmp_path / "fail"), "1"))
        )
        failed = manager.start("source", source_command(root), 270)
        assert failed["started"] is True
        failure = _reap(manager, "source", timeout=25)
        assert failure["category"] == "worker_failed"
        stop = json.loads((root / "source" / "STOP").read_text())
        assert stop["reason"] == "source_worker_failure"
        assert (root / "background" / "source-fail-closed.json").is_file()
        blocked = manager.start("source", source_command(root), 270)
        assert blocked["started"] is False and blocked["reason"] == "source_fail_closed"
        assert _snapshot(old) == old_files
        assert (root / "successor-ready.json").read_bytes() == ready_bytes
        assert not (root / "source" / "requests").exists()
    finally:
        manager.stop()
        if proc is not None and proc.poll() is None:
            proc.kill()
        _kill(supervisor)
        _kill(child)
        _kill(grand)
