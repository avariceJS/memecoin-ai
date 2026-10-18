"""Synthetic properties: assumptions cannot silently become calibrated outcomes."""

import runpy
from pathlib import Path

import pytest

from memecoin_ai.domain import ValidationError

m = runpy.run_path(str(Path(__file__).parents[1] / "tools/execution_uncertainty.py"))


def row(delay=0, pnl=10, **updates):
    return dict(
        sample_id="s",
        pool_id="p",
        input_index=0,
        size_ppm=100,
        partition="calibration",
        status="conditional_roundtrip",
        assumed_delay_ns=delay,
        pnl_before_network_costs=pnl,
        gross_input=10000,
        net_pnl=None,
        **updates,
    )


def test_cost_sensitivity_is_monotonic_and_exact_at_break_even():
    r = m["analyze"]([row()])
    curve = r["groups"][0]["cost_sensitivity"]
    assert [x["positive_conditional_rows"] for x in curve] == [1, 1, 0, 0, 0, 0]
    assert r["net_pnl"] is None and not r["assumptions"]["calibrated"]


def test_censored_and_failed_exits_are_not_zero_returns():
    rows = [row()]
    for status in ("censored", "exit_unfilled", "entry_unfilled"):
        r = row()
        r.update(status=status, pnl_before_network_costs=None, gross_input=None)
        rows.append(r)
    r = m["analyze"](rows)
    assert r["scenario_rows"] == 4 and r["eligible_conditional_rows"] == 1
    assert r["groups"][0]["break_even_extra_cost_bps"]["minimum"] == 10


def test_pairing_never_bridges_partition_or_asset():
    a, b = row(), row(delay=5_000_000_000, pnl=-5)
    report = m["analyze"]([a, b])
    pair = report["paired_delay"][0]
    assert pair["paired_scenarios"] == 1 and pair["sign_flips"] == 1
    assert pair["delayed_minus_zero_return_bps"]["median"] == -15
    b["partition"] = "engineering_evaluation"
    assert all(x["paired_scenarios"] == 0 for x in m["analyze"]([a, b])["paired_delay"])
    b["partition"] = "calibration"
    b["input_index"] = 1
    assert all(x["paired_scenarios"] == 0 for x in m["analyze"]([a, b])["paired_delay"])


def test_permutation_invariance_and_duplicate_rejection():
    a, b = row(), row(delay=5_000_000_000, pnl=-5)
    assert m["analyze"]([a, b]) == m["analyze"]([b, a])
    with pytest.raises(ValidationError, match="duplicate"):
        m["analyze"]([a, a])
