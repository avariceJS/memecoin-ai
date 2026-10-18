"""Simulate the release transaction and rollback; no real LaunchAgent or HTTP operations."""

import importlib.util
import plistlib
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from memecoin_ml.data import read
from memecoin_operator.deployment import stage
from memecoin_registry.contracts import verify

from .test_adapters import bind_source, operator_config
from .test_historical_training import historical


def module():
    path = Path(__file__).resolve().parents[2] / "tools/brain_expansion_cutover.py"
    spec = importlib.util.spec_from_file_location("cutover", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize("fail", [False, True])
def test_exact_release_or_rollback_preserves_config_source_and_books(tmp_path, monkeypatch, fail):
    tool = module()
    now = time.time_ns()
    source = bind_source(tmp_path / "source", now + 86400 * 10**9)
    root = tmp_path / "operator"
    operator_config(root, source, now + 86400 * 10**9, port=8792)
    original = stage(root, tmp_path / "old-runtime")
    before = (root / "config.json").read_bytes()
    source_before = (source / "config.json").read_bytes()
    historical(tmp_path, now // 10**9 // 300 * 300)
    home = tmp_path / "home"
    launch = home / "Library/LaunchAgents"
    launch.mkdir(parents=True)
    plist = launch / "ai.memecoin.brain.operator.plist"
    plist.write_bytes(
        plistlib.dumps(
            dict(ProgramArguments=[sys.executable, str(tmp_path / "old-runtime/runtime")])
        )
    )
    old_plist = plist.read_bytes()
    monkeypatch.setattr(tool.Path, "home", lambda: home)
    state = dict(
        mode="auto",
        phase="waiting",
        gates=dict(busy=False, auto=dict(eligible=False), runs_used=2),
        source=dict(healthy=True),
    )
    monkeypatch.setattr(tool, "status", lambda port: state)
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        return SimpleNamespace(
            returncode=1 if fail and args[1] == "bootstrap" and len(commands) == 2 else 0
        )

    monkeypatch.setattr(tool.subprocess, "run", run)
    (root / "background/locks").mkdir(parents=True)
    (root / "background/locks/exam.lock").touch()
    book = root / "paper/exam/ledger"
    book.mkdir(parents=True)
    event = book / "event-000000.json"
    event.write_text('{"synthetic":"preserved book"}')
    original_event = event.read_bytes()
    runtime = tmp_path / "new-runtime"
    if fail:
        with pytest.raises(ValueError, match="bootstrap_failed"):
            tool.install(root, tmp_path / "corpus", runtime)
        assert read(root / "deployment.json") == original
        assert plist.read_bytes() == old_plist
        assert (root / "revisions/new-runtime/rollback.json").exists()
    else:
        tool.install(root, tmp_path / "corpus", runtime)
        assert read(root / "deployment.json")["bundle"] == str(runtime)
        assert str(runtime / "runtime") in plistlib.loads(plist.read_bytes())["ProgramArguments"]
        assert read(root / "revisions/new-runtime/activation.json")["preserved_events"] == 1
    assert event.read_bytes() == original_event
    assert (root / "config.json").read_bytes() == before
    assert (source / "config.json").read_bytes() == source_before
    verify(root / "revisions/new-runtime")
