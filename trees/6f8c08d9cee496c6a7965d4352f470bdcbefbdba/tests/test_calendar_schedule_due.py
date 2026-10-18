"""Finite calendar admission: missed means missing, never catch-up."""

import runpy
from pathlib import Path

import pytest

from memecoin_ai.domain import ValidationError
from memecoin_ai.prospective import write

m = runpy.run_path(str(Path(__file__).parents[1] / "tools/calendar_schedule.py"))


def test_due_window_boundaries():
    f = m["due_action"]
    due = 10**12
    assert f(due, due - 1, 900) == "pending"
    assert f(due, due, 900) == "collect"
    assert f(due, due + 900 * 10**9, 900) == "collect"
    assert f(due, due + 900 * 10**9 + 1, 900) == "missed"


def test_runtime_mutation_rejected_before_network(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "x.py").write_text("original")
    write(tmp_path / "runtime-manifest.json", m["snapshot_hashes"](runtime))
    (runtime / "x.py").write_text("changed")
    with pytest.raises(ValidationError, match="pinned runtime"):
        m["check_runtime"](tmp_path)


@pytest.mark.parametrize("case", ["missed", "interrupted", "failure_budget"])
def test_resume_never_spends_network_for_ineligible_window(tmp_path, case, monkeypatch):
    (tmp_path / "windows").mkdir()
    (tmp_path / "outcomes").mkdir()
    write(
        tmp_path / "plan.json",
        dict(due_ns=[0, 10**30], spec=dict(lateness_seconds=1, max_failed_windows=2)),
    )
    runner = m["run_due"]
    g = runner.__globals__
    monkeypatch.setitem(g, "check_runtime", lambda p: None)

    def forbidden(*args):
        raise AssertionError("unexpected network window")

    monkeypatch.setitem(g, "run_window", forbidden)
    if case == "interrupted":
        (tmp_path / "windows/000").mkdir()
    if case == "failure_budget":
        for i in range(2):
            write(tmp_path / "outcomes" / f"{i:03}.json", dict(index=i, status="failed"))
    runner(tmp_path)
    if case == "failure_budget":
        assert (tmp_path / "closed.json").exists()
    else:
        from memecoin_ai.prospective import read

        assert read(tmp_path / "outcomes/000.json")["status"] == (
            "missed" if case == "missed" else "failed"
        )
