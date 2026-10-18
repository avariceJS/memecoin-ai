"""Guard failures, exact process identity and argv privacy; synthetic only."""

import sqlite3
import struct

import pytest

from memecoin_curriculum import research_guard as guard
from memecoin_ml.data import sha
from memecoin_registry.contracts import digest


def test_procargs_preserves_spaces_and_empty_args_but_never_environment():
    raw = struct.pack("=i", 4) + b"/python\0\0/python\0-c\0print('a b')\0\0SECRET=value\0"
    assert guard.decode_procargs(raw) == ["/python", "-c", "print('a b')", ""]
    assert guard.argv_identity(["/python", "a b"]) != guard.argv_identity(["/python", "a", "b"])


@pytest.mark.parametrize("raw", [b"", struct.pack("=i", 0), struct.pack("=i", 2) + b"x\0x\0"])
def test_partial_procargs_fail_closed(raw):
    with pytest.raises(ValueError):
        guard.decode_procargs(raw)


def test_unknown_python_is_blocked_even_if_argv_ends_ui(tmp_path, monkeypatch):
    exe = tmp_path / "Python"
    exe.write_bytes(b"synthetic")
    shown = f"99999 1 501 Wed Oct 7 10:00:00 2026 {exe} {exe} unrelated ui"
    monkeypatch.setattr(guard.subprocess, "check_output", lambda *a, **k: shown)
    argv = [str(exe), "secret", "ui"]
    monkeypatch.setattr(guard, "process_argv", lambda pid: argv)
    report = guard.inventory([])
    assert not report["ready"]
    assert "secret" not in str(report)
    rule = dict(argv_sha=digest(argv[1:]), executables=[sha(exe)], role="paper_ui", label="fixture")
    assert guard.inventory([rule])["ready"]
    argv[1] = "different"
    assert not guard.inventory([rule])["ready"]


@pytest.mark.parametrize(
    "change", ["revision", "busy", "exam", "pin", "expiry", "cooldown", "none"]
)
def test_retired_owner_envelope_fail_closed(tmp_path, monkeypatch, change):
    now = 10**18
    monkeypatch.setattr(guard.time, "time_ns", lambda: now)
    p = tmp_path / "config.json"
    p.write_text("{}")
    dbpath = tmp_path / "operator.sqlite"
    with sqlite3.connect(dbpath) as db:
        db.execute("create table revisions(revision integer,mode text,phase text)")
        db.execute("insert into revisions values(33,'paused','paused')")
        db.execute("create table jobs(status text)")
        if change == "revision":
            db.execute("insert into revisions values(34,'auto','waiting')")
        if change == "busy":
            db.execute("insert into jobs values('running')")
    owner = dict(
        pins={str(p): sha(p)},
        source_expires_ns=now - 1,
        cooldown_until_ns=now - 1,
        operator_db=str(dbpath),
        revision=33,
        exam_expiries_ns=[now - 1],
    )
    plan = dict(retired_owner=owner, expires_ns=now + 1000)
    if change == "exam":
        owner["exam_expiries_ns"] = [now + 1]
    if change == "pin":
        p.write_text("changed")
    if change == "expiry":
        plan["expires_ns"] = now
    if change == "cooldown":
        owner["cooldown_until_ns"] = now + 1
    if change == "none":
        assert guard.owner_guard(plan)
    else:
        with pytest.raises(ValueError, match="research_"):
            guard.owner_guard(plan)


def test_research_lease_rejects_overlap_and_preserves_file(tmp_path):
    p = tmp_path / "lease"
    with guard.research_lease(p), pytest.raises(BlockingIOError), guard.research_lease(p):
        pytest.fail("overlap admitted")
    assert p.exists()
    with guard.research_lease(p):
        pass


def test_unknown_inventory_saved_before_failure(tmp_path, monkeypatch):
    from memecoin_ml.data import read

    observation = dict(ready=False, processes=[dict(pid=99, allowed=False, role="unknown")])
    monkeypatch.setattr(guard, "inventory", lambda rules: observation)
    receipt = tmp_path / "consumers.json"
    with pytest.raises(ValueError, match="research_consumer_block"):
        guard.require_consumers(dict(consumer_rules=[]), receipt)
    assert read(receipt) == observation


def test_source_event_append_stops_retired_envelope(tmp_path):
    journal = tmp_path / "journal"
    journal.mkdir()
    first = journal / "event-000000.json"
    first.write_text("{}")
    plan = dict(
        expires_ns=10**30,
        retired_owner=dict(
            pins={str(first): sha(first)},
            source_journal=str(journal),
            source_last_event=str(first),
        ),
    )
    (journal / "event-000001.json").write_text("{}")
    with pytest.raises(ValueError, match="research_source_progressed"):
        guard.owner_guard(plan)


def test_changed_executable_does_not_match_even_with_exact_argv(tmp_path, monkeypatch):
    exe = tmp_path / "Python"
    exe.write_bytes(b"before")
    argv = [str(exe), "-I", "fixture"]
    rule = dict(
        argv_sha=guard.argv_identity(argv), executables=[sha(exe)], role="fixture", label="fixture"
    )
    exe.write_bytes(b"after")
    monkeypatch.setattr(
        guard.subprocess,
        "check_output",
        lambda *a, **k: f"99999 1 501 Wed Oct 7 10:00:00 2026 {exe} {exe}",
    )
    monkeypatch.setattr(guard, "process_argv", lambda pid: argv)
    assert not guard.inventory([rule])["ready"]
