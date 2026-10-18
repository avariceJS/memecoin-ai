"""Synthetic launchctl results cannot turn a failed restore into a healthy service."""

from pathlib import Path
from types import SimpleNamespace

from memecoin_ml.data import read
from memecoin_operator import adapters
from memecoin_runner.ledger import publish


def setup_root(tmp_path, monkeypatch):
    root = tmp_path / "operator"
    root.mkdir()
    home = tmp_path / "home"
    folder = home / "Library/LaunchAgents"
    folder.mkdir(parents=True)
    label = "ai.memecoin.brain.b09r1.source"
    (folder / (label + ".plist")).write_text("synthetic placeholder")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    publish(root / "legacy-paused.json", dict(paused=True, labels=[label]))
    return root


def test_failed_launch_restore_preserves_pause_and_is_bounded(tmp_path, monkeypatch):
    root = setup_root(tmp_path, monkeypatch)
    calls = []

    def fail(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(adapters.subprocess, "run", fail)
    clock = [100 * adapters.NS]
    monkeypatch.setattr(adapters.time, "time_ns", lambda: clock[0])
    for _ in range(3):
        result = adapters.resume_legacy(root)
        assert result["status"] == "blocked"
        assert read(root / "legacy-paused.json")["paused"]
        clock[0] += 61 * adapters.NS
    total = len(calls)
    assert adapters.resume_legacy(root)["attempts"] == 3
    assert len(calls) == total
    assert len(list((root / "incidents").glob("*.json"))) == 3


def test_already_loaded_service_is_accepted_only_after_print(tmp_path, monkeypatch):
    root = setup_root(tmp_path, monkeypatch)
    calls = []

    def reply(args, **kwargs):
        calls.append(args[1])
        return SimpleNamespace(returncode=5 if args[1] == "bootstrap" else 0)

    monkeypatch.setattr(adapters.subprocess, "run", reply)
    assert adapters.resume_legacy(root)["status"] == "ready"
    assert calls == ["enable", "bootstrap", "print"]
    assert not read(root / "legacy-paused.json")["paused"]


def test_full_stop_reports_unconfirmed_service_as_failure(tmp_path, monkeypatch):
    root = setup_root(tmp_path, monkeypatch)
    publish(root / "config.json", dict(managed_legacy_labels=["ai.memecoin.brain.b09r1.source"]))
    monkeypatch.setattr(
        adapters.subprocess,
        "run",
        lambda args, **kwargs: SimpleNamespace(returncode=1 if args[1] == "bootout" else 0),
    )
    result = adapters.stop(root, full=True)
    assert not result["stopped"]
    assert not result["legacy_paused"]
    assert read(root / "legacy-paused.json")["paused"]
    assert result["services"][0]["stopped"] is False


def test_full_stop_does_not_ignore_loaded_label_with_missing_plist(tmp_path, monkeypatch):
    root = setup_root(tmp_path, monkeypatch)
    label = "ai.memecoin.brain.b09r1.source"
    publish(root / "config.json", dict(managed_legacy_labels=[label]))
    (Path.home() / "Library/LaunchAgents" / (label + ".plist")).unlink()
    calls = []

    def reply(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=113 if args[1] == "print" else 0)

    monkeypatch.setattr(adapters.subprocess, "run", reply)
    result = adapters.stop(root, full=True)
    assert result["stopped"] is True
    assert [args[1] for args in calls] == ["disable", "bootout", "print"]
    assert read(root / "legacy-paused.json")["labels"] == [label]
    assert adapters.resume_legacy(root)["services"][0]["reason"] == "service_plist_missing"
