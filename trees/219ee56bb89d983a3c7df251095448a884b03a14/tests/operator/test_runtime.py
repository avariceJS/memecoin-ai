"""Service entry, in-process worker, and restart without retraining."""

import importlib.util
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
from http.client import HTTPConnection
from pathlib import Path

from memecoin_operator.control import (
    artifact_dir,
    atomic_write,
    command,
    lease_path,
    status,
    tick,
    wall_ns,
)
from memecoin_operator.runtime import execute_job, main

_spec = importlib.util.spec_from_file_location(
    "operator_test_control_for_runtime", Path(__file__).with_name("test_control.py")
)
assert _spec is not None and _spec.loader is not None
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)
HASH_A = _helpers.HASH_A
make_root = _helpers.make_root


def test_init_and_worker_round_trip(adapter, tmp_path):
    root = make_root(
        tmp_path,
        run={
            "snapshot": {
                "result": {"ok": True, "dataset_id": "from_worker", "snapshot_hash": HASH_A}
            }
        },
    )
    assert main(["--root", str(root), "--init"]) == 0
    assert main(["--root", str(root), "--worker"]) == 2
    job_id = "job_runtime_worker"
    art = artifact_dir(root, job_id)
    art.mkdir(parents=True)
    atomic_write(
        art / "operator-request.json",
        json.dumps({"job_id": job_id, "action": "snapshot", "params": {}}).encode(),
    )
    atomic_write(
        lease_path(root, job_id),
        json.dumps({"job_id": job_id, "expires_ns": wall_ns() + 30_000_000_000}).encode(),
    )
    assert execute_job(root, job_id) == 0
    assert (art / "operator-published.marker").exists()
    partial = "job_runtime_partial"
    part = artifact_dir(root, partial)
    part.mkdir(parents=True)
    atomic_write(
        part / "operator-request.json",
        json.dumps(
            {"job_id": partial, "action": "train", "params": {"snapshot_id": "from_worker"}}
        ).encode(),
    )
    atomic_write(
        lease_path(root, partial),
        json.dumps({"job_id": partial, "expires_ns": wall_ns() + 30_000_000_000}).encode(),
    )
    (root / "adapter_run.json").write_text(json.dumps({"train": {"result": {"ok": True}}}))
    assert execute_job(root, partial) == 2
    assert not (part / "operator-published.marker").exists()


def test_serve_and_restart_interrupts_inflight(adapter, tmp_path):
    root = make_root(
        tmp_path, run={"collect": {"sleep": 30, "result": {"ok": True, "profile": "balanced"}}}
    )
    assert command(root, "c1", "collect", {"profile": "balanced"})["ok"]
    tick(root)
    assert status(root)["jobs"][-1]["status"] == "running"
    from memecoin_operator.web import make_server

    server = make_server(root, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/status")
        response = conn.getresponse()
        body = json.loads(response.read())
        conn.close()
        assert response.status == 200
        assert body["jobs"][-1]["status"] == "running"
        assert body["candidates"] == []
        assert main(["--root", str(root), "--port", "0"]) == 2
    finally:
        server.shutdown_background()
        thread.join(timeout=3)
        database = sqlite3.connect(root / "operator.sqlite")
        pid = database.execute(
            "SELECT pid FROM jobs WHERE status='running' AND pid IS NOT NULL"
        ).fetchone()
        database.close()
        if pid is not None:
            try:
                os.killpg(int(pid[0]), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
    assert status(root)["jobs"][-1]["status"] == "running"
    tick(root)
    assert [job["action"] for job in status(root)["jobs"]].count("collect") == 1


def test_second_process_cannot_initialize_an_owned_root(adapter, tmp_path):
    root = make_root(tmp_path)
    script = (
        "import json, os\n"
        "from pathlib import Path\n"
        "from memecoin_operator.runtime import main\n"
        "raise SystemExit(main(['--root', os.environ['ROOT'], '--init']))\n"
    )
    env = os.environ.copy()
    env["ROOT"] = str(root)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 3, completed.stderr
    assert status(root)["mode"] == "idle"
