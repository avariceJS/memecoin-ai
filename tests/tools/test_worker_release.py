"""Synthetic maintenance rehearsal: code rollback retains later ledger events."""

import importlib.util
import io
import json
import plistlib
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from memecoin_operator.deployment import stage

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location("worker_release", TOOLS / "worker_release.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def setup(tmp_path):
    source, root = tmp_path / "source", tmp_path / "op"
    source.mkdir()
    root.mkdir()
    now = time.time_ns()
    r.write(source / "config.json", dict(synthetic=True))
    r.write(
        root / "config.json",
        dict(
            created_ns=now,
            expires_ns=now + 10**12,
            source_root=str(source),
            real_execution_enabled=False,
            paid_budget_usd=0,
            port=8792,
        ),
    )
    with sqlite3.connect(root / "operator.sqlite") as db:
        db.execute("CREATE TABLE revisions (revision INTEGER, mode TEXT)")
        db.execute("INSERT INTO revisions VALUES (1,'paused')")
        db.execute("CREATE TABLE jobs (status TEXT)")
        db.execute("CREATE TABLE events (value TEXT)")
        db.execute("INSERT INTO events VALUES ('reservation preserved')")
    (root / "operator").mkdir()
    old = stage(root, tmp_path / "old")
    new = stage(root, tmp_path / "new", activate=False)
    plist = tmp_path / "operator.plist"
    plist.write_bytes(
        plistlib.dumps(
            dict(
                Label=r.LABEL,
                ProgramArguments=[sys.executable, str(Path(old["bundle"]) / "runtime")],
            )
        )
    )
    record = dict(root=str(root), previous=old, proposed=new)
    return root, plist, record


def test_switch_and_rollback_keep_new_events_and_frozen_pins(tmp_path):
    root, plist, record = setup(tmp_path)
    exam = root / "paper" / "old-exam"
    (exam / "ledger").mkdir(parents=True)
    r.write(exam / "config.json", dict(expires_ns=1, model_pin="frozen"))
    r.write(exam / "deployment.json", dict(pin="old-paper-code"))
    r.write(exam / "ledger" / "event-0001.json", dict(event="old"))
    r.write(root / "STOP", dict(reason="preserve"))
    before = r.quiescent_state(root)
    r.switch_stopped(record, plist, tmp_path / "activate")
    assert r.read_json(root / "deployment.json") == record["proposed"]
    assert r.quiescent_state(root) == before
    with sqlite3.connect(root / "operator.sqlite") as db:
        db.execute("INSERT INTO events VALUES ('new event after release')")
    r.write(exam / "ledger" / "event-0002.json", dict(event="later"))
    latest = r.quiescent_state(root)
    r.switch_stopped(record, plist, tmp_path / "rollback", rollback=True)
    assert r.quiescent_state(root) == latest
    assert r.read_json(root / "deployment.json") == record["previous"]


@pytest.mark.parametrize("reason", ["busy", "auto", "exam", "expired", "tamper"])
def test_refusal_leaves_deployment_and_plist_unchanged(tmp_path, reason):
    root, plist, record = setup(tmp_path)
    if reason in {"busy", "auto"}:
        with sqlite3.connect(root / "operator.sqlite") as db:
            if reason == "busy":
                db.execute("INSERT INTO jobs VALUES ('running')")
            else:
                db.execute("UPDATE revisions SET mode='auto'")
    if reason == "exam":
        exam = root / "paper" / "active"
        exam.mkdir(parents=True)
        r.write(exam / "config.json", dict(expires_ns=time.time_ns() + 10**12))
    if reason == "expired":
        config = r.read_json(root / "config.json")
        config["expires_ns"] = 1
        (root / "config.json").write_text(json.dumps(config))
    if reason == "tamper":
        (Path(record["proposed"]["bundle"]) / "runtime/memecoin_operator/deployment.py").write_text(
            "tamper"
        )
    before = (root / "deployment.json").read_bytes(), plist.read_bytes()
    with pytest.raises(ValueError):
        r.switch_stopped(record, plist, tmp_path / "attempt")
    assert before == ((root / "deployment.json").read_bytes(), plist.read_bytes())


def test_partial_pointer_failure_restores_only_code(tmp_path, monkeypatch):
    root, plist, record = setup(tmp_path)
    before = r.quiescent_state(root)
    real_replace = r.replace
    calls = 0

    def fail_once(path, raw):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated disk failure")
        real_replace(path, raw)

    monkeypatch.setattr(r, "replace", fail_once)
    with pytest.raises(OSError):
        r.switch_stopped(record, plist, tmp_path / "failed")
    assert r.read_json(root / "deployment.json") == record["previous"]
    assert r.quiescent_state(root) == before


def test_owner_lock_blocks_switch(tmp_path):
    root, plist, record = setup(tmp_path)
    with r.maintenance_locks(root), pytest.raises(ValueError, match="runtime_busy"):
        r.switch_stopped(record, plist, tmp_path / "failed")


def test_prepare_requires_exact_revision(tmp_path):
    with pytest.raises(ValueError, match="exact_git_revision"):
        r.prepare(tmp_path, "main", tmp_path / "release")


@pytest.mark.parametrize(
    "failure",
    [
        "none",
        "bootstrap",
        "expired",
        "foreign-service",
        "bootout-timeout",
        "bootout-error",
        "switch-expired",
    ],
)
def test_activation_health_and_failed_start_recovery(tmp_path, monkeypatch, failure):
    root, original_plist, record = setup(tmp_path)
    home = tmp_path / "home"
    agents = home / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    plist = agents / (r.LABEL + ".plist")
    plist.write_bytes(original_plist.read_bytes())
    release = tmp_path / "release"
    release.mkdir()
    r.write(release / "release.json", dict(synthetic=True))
    record.update(release=str(release), release_sha256=r.sha(release / "release.json"))
    record_path = tmp_path / "record.json"
    r.write(record_path, record)
    monkeypatch.setattr(r.Path, "home", lambda: home)
    monkeypatch.setattr(r, "verify_release", lambda path: None)
    real_run = r.subprocess.run
    bootstraps = 0
    bootouts = 0

    def launch(args, **kwargs):
        nonlocal bootstraps, bootouts
        if args[0] != "launchctl":
            return real_run(args, **kwargs)
        if args[1] == "bootstrap":
            bootstraps += 1
            if (
                failure in {"bootstrap", "expired", "bootout-timeout", "bootout-error"}
                and bootstraps == 1
            ):
                if failure == "expired":
                    expiry = r.read_json(root / "config.json")["expires_ns"]
                    monkeypatch.setattr(r.time, "time_ns", lambda: expiry + 1)
                raise r.subprocess.CalledProcessError(1, args)
        if args[1] == "bootout":
            bootouts += 1
            if failure == "bootout-timeout" and bootouts == 2:
                raise r.subprocess.TimeoutExpired(args, 20)
            if failure == "bootout-error" and bootouts == 2:
                return r.subprocess.CompletedProcess(args, 5)
        return r.subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(r.subprocess, "run", launch)
    monkeypatch.setattr(r, "owns_listener", lambda *args: failure != "foreign-service")
    monkeypatch.setattr(r.time, "sleep", lambda *args: None)

    class Opener:
        def open(self, url, timeout):
            assert url == "http://127.0.0.1:8792/api/status"
            return io.BytesIO(b'{"schema":"operator-status-v1","ok":true,"mode":"paused"}')

    monkeypatch.setattr(r.urllib.request, "build_opener", lambda *args: Opener())
    before = r.quiescent_state(root)
    original_state = r.quiescent_state
    state_calls = 0
    if failure == "switch-expired":

        def expiring_state(root, **kwargs):
            nonlocal state_calls
            state_calls += 1
            if state_calls == 3:
                expiry = r.read_json(root / "config.json")["expires_ns"]
                monkeypatch.setattr(r.time, "time_ns", lambda: expiry + 1)
            return original_state(root, **kwargs)

        monkeypatch.setattr(r, "quiescent_state", expiring_state)
    if failure != "none":
        with pytest.raises(
            (r.subprocess.CalledProcessError, r.subprocess.TimeoutExpired, ValueError)
        ):
            r.activate(record_path, tmp_path / "journal")
        assert (
            r.read_json(root / "deployment.json")
            == record["proposed" if failure in {"bootout-timeout", "bootout-error"} else "previous"]
        )
        assert bootstraps == (
            0
            if failure == "switch-expired"
            else 1
            if failure in {"expired", "bootout-timeout", "bootout-error"}
            else 2
        )
        assert (
            tmp_path
            / "journal"
            / ("switch-failed.json" if failure == "switch-expired" else "startup-failed.json")
        ).is_file()
        if failure == "bootout-timeout":
            assert r.read_json(tmp_path / "journal/startup-failed.json")["phase"] == "bootout"
    else:
        assert r.activate(record_path, tmp_path / "journal")["health_check"] == "passed"
        assert r.read_json(root / "deployment.json") == record["proposed"]
    assert r.quiescent_state(root, allow_expired=True) == before


def test_real_replace_failure_does_not_leave_blocking_temp(tmp_path, monkeypatch):
    path = tmp_path / "target"
    path.write_bytes(b"old")
    real_replace = r.os.replace

    def fail(*args):
        raise OSError("rename failed")

    monkeypatch.setattr(r.os, "replace", fail)
    with pytest.raises(OSError):
        r.replace(path, b"new")
    assert list(tmp_path.iterdir()) == [path]
    assert path.read_bytes() == b"old"
    monkeypatch.setattr(r.os, "replace", real_replace)
    r.replace(path, b"retry")
    assert path.read_bytes() == b"retry"


@pytest.mark.parametrize("listener", ["p42\n", "p77\n", "p42\np77\n"])
def test_health_requires_launchd_pid_owns_listener(tmp_path, monkeypatch, listener):
    target = dict(bundle=str(tmp_path))
    outputs = iter(
        ["service = {\nstate = running\npid = 42\n}\n", listener, str(tmp_path / "runtime")]
    )
    monkeypatch.setattr(
        r.subprocess,
        "run",
        lambda *args, **kw: r.subprocess.CompletedProcess(args, 0, stdout=next(outputs)),
    )
    assert r.owns_listener("gui/1", 8792, target) is (listener == "p42\n")


@pytest.mark.parametrize("failed_check", [None, 0, 1, 2, 3, 4])
def test_prepare_checks_and_seals_exact_extracted_code(tmp_path, monkeypatch, failed_check):
    raw = io.BytesIO()
    with r.zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("README.md", "synthetic code fixture")
    revision = "a" * 40
    monkeypatch.setattr(
        r,
        "git",
        lambda repo, *args: revision.encode() if args[0] == "rev-parse" else raw.getvalue(),
    )
    count = 0

    def check(args, **kwargs):
        nonlocal count
        rc = 1 if count == failed_check else 0
        count += 1
        return r.subprocess.CompletedProcess(args, rc)

    monkeypatch.setattr(r.subprocess, "run", check)
    release = tmp_path / "prepared"
    if failed_check is not None:
        with pytest.raises(ValueError, match="release_check_failed"):
            r.prepare(tmp_path, revision, release)
        assert not (release / "release.json").exists()
        assert (release / "FAILED.json").exists()
    else:
        receipt = r.prepare(tmp_path, revision, release)
        assert count == 5
        assert r.verify_release(release) == receipt
        (release / "code/README.md").write_text("changed")
        with pytest.raises(ValueError, match="code_integrity"):
            r.verify_release(release)


def test_prepare_adds_explicit_dev_pythonpath_to_exact_runtime(tmp_path, monkeypatch):
    raw = io.BytesIO()
    with r.zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("README.md", "synthetic code fixture")
    revision = "b" * 40
    monkeypatch.setattr(
        r,
        "git",
        lambda repo, *args: revision.encode() if args[0] == "rev-parse" else raw.getvalue(),
    )
    site = tmp_path / "site-packages"
    site.mkdir()
    environments = []

    def check(args, **kwargs):
        environments.append(kwargs["env"])
        return r.subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(r.subprocess, "run", check)
    receipt = r.prepare(tmp_path, revision, tmp_path / "prepared", [site])

    assert all(
        env["PYTHONPATH"].split(r.os.pathsep) == [str(tmp_path / "prepared/code/src"), str(site)]
        for env in environments
    )
    assert receipt["extra_pythonpath"] == [str(site)]
