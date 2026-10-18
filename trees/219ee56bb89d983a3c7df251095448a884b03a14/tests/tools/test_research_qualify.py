"""Synthetic launcher metadata only; no live service/control/provider operations."""

import importlib.util
import plistlib
from pathlib import Path

import pytest

from memecoin_ml.data import sha
from memecoin_runner.ledger import publish

spec = importlib.util.spec_from_file_location(
    "research_qualify", Path(__file__).resolve().parents[2] / "tools/brain_research_qualify.py"
)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


@pytest.mark.parametrize("kind", ["paper", "b04", "unknown", "active", "modified_launcher"])
def test_only_exact_known_retired_launchers(tmp_path, monkeypatch, kind):
    exe = tmp_path / "Python"
    exe.write_bytes(b"synthetic")
    root = tmp_path / "owner"
    root.mkdir()
    publish(root / "config.json", dict(expires_ns=10**30 if kind == "active" else 0))
    module = "memecoin_brain_b04" if kind == "b04" else "memecoin_paper"
    runtime = tmp_path / "bundle/runtime"
    (runtime / module).mkdir(parents=True)
    entry = "service" if kind == "b04" else "runtime"
    (runtime / module / f"{entry}.py").write_text("# synthetic")
    publish(root / "deployment.json", dict(bundle=str(runtime.parent), pin="fixture"))
    code = (
        "import sys; sys.path.insert(0,sys.argv.pop(1)); "
        "from memecoin_brain_b04.service import main; main()"
        if kind == "b04"
        else "import sys;sys.path.insert(0,sys.argv.pop(1));"
        "from memecoin_paper.runtime import main;main()"
    )
    if kind == "modified_launcher":
        code += ";print('unexpected')"
    if kind == "unknown":
        code = "unexpected"
    args = [
        str(exe),
        "-I",
        "-B",
        "-c",
        code,
        str(runtime),
        str(root),
        "fixture" if kind == "b04" else "paper",
    ]
    p = tmp_path / "ai.memecoin.fixture.plist"
    p.write_bytes(plistlib.dumps(dict(Label="ai.memecoin.fixture", ProgramArguments=args)))
    monkeypatch.setattr(
        tool.subprocess, "check_output", lambda *a, **k: "PID Status Label\n- 0 ai.memecoin.fixture"
    )
    if kind in {"unknown", "active", "modified_launcher"}:
        with pytest.raises(ValueError):
            tool.rules_from_launchagents(tmp_path)
    else:
        rules, pins = tool.rules_from_launchagents(tmp_path)
        assert len(rules) == 1
        assert rules[0]["role"].startswith("retired_")
        assert pins[str(p)] == sha(p)
        assert "import sys" not in str(rules)


def test_inactive_unknown_plist_does_not_expand_allowlist(tmp_path, monkeypatch):
    p = tmp_path / "ai.memecoin.unknown.plist"
    p.write_bytes(plistlib.dumps(dict(Label="ai.memecoin.unknown", ProgramArguments=["invalid"])))
    monkeypatch.setattr(tool.subprocess, "check_output", lambda *a, **k: "PID Status Label\n")
    assert tool.rules_from_launchagents(tmp_path) == ([], {})


@pytest.mark.parametrize("active", [False, True])
def test_calendar_admission_reads_schedule_not_protected_outcomes(tmp_path, monkeypatch, active):
    exe = tmp_path / "Python"
    exe.write_bytes(b"synthetic")
    root = tmp_path / "calendar"
    script = root / "runtime/tools/calendar_schedule.py"
    script.parent.mkdir(parents=True)
    script.write_bytes(Path(tool.__file__).with_name("calendar_schedule.py").read_bytes())
    publish(
        root / "plan.json", dict(due_ns=[10**30 if active else 1], spec=dict(lateness_seconds=0))
    )
    # No references, windows or outcomes exist: qualification must never need them.
    p = tmp_path / "ai.memecoin.d43.calendar01.plist"
    p.write_bytes(
        plistlib.dumps(
            dict(
                Label="ai.memecoin.d43.calendar01",
                ProgramArguments=[str(exe), "-B", str(script), "run-due", str(root)],
            )
        )
    )
    monkeypatch.setattr(
        tool.subprocess,
        "check_output",
        lambda *a, **k: "PID Status Label\n- 0 ai.memecoin.d43.calendar01",
    )
    if active:
        with pytest.raises(ValueError, match="calendar_not_retired"):
            tool.rules_from_launchagents(tmp_path)
    else:
        rules, pins = tool.rules_from_launchagents(tmp_path)
        assert rules[0]["role"] == "retired_calendar_supervisor"
        assert all("outcomes" not in p and "references" not in p for p in pins)
