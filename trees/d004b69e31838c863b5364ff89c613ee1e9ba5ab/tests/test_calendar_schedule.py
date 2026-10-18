"""Regression: setup latency cannot shorten a preregistered calendar separation."""

import runpy
from pathlib import Path

import pytest


def load(monkeypatch, name):
    tools = Path(__file__).parents[1] / "tools"
    monkeypatch.syspath_prepend(str(tools))
    return runpy.run_path(str(tools / name))


def test_deadline_uses_actual_first_campaign_not_runner_start(monkeypatch):
    due = load(monkeypatch, "corpus_windows.py")["window_due_ns"]
    root = 1_000_000_000
    first = root + 13_650_000
    assert due(root, None, 0, 600) == root
    assert due(root, first, 1, 600) == first + 600 * 10**9
    with pytest.raises(ValueError, match="first campaign"):
        due(root, None, 1, 600)


def test_audit_preserves_even_millisecond_violation_without_rounding(monkeypatch):
    audit = load(monkeypatch, "calendar_report.py")["calendar_adherence"]
    shortfall = 5_916_000
    r = audit(0, 600 * 10**9 - shortfall, 600)
    assert not r["met"] and r["shortfall_ns"] == shortfall
    assert audit(0, 600 * 10**9, 600)["met"]
