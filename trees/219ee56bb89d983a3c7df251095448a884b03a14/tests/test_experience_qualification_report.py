import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "experience_qualification_report",
    Path(__file__).parents[1] / "tools/experience_qualification_report.py",
)
reporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reporter)


def test_funnel_accounts_for_initial_context_gaps_and_origin_window():
    rows = [dict(pool="A", open_s=t) for t in (0, 300, 600, 1200, 1500, 1800, 2100)]
    result = reporter.origin_funnel(
        rows, pools=["A", "missing"], context=3, start_s=600, end_s=2400
    )
    assert result["counts"] == dict(
        raw_bars=7,
        outside_origin_interval=2,
        initial_context=1,
        gap_context=2,
        persistable_origins=2,
    )
    assert result["per_pool"]["missing"] == {}
    assert sum(v for k, v in result["counts"].items() if k != "raw_bars") == 7
    with pytest.raises(ValueError, match="membership_or_order"):
        reporter.origin_funnel(rows + rows, pools=["A"], context=3, start_s=0, end_s=3000)
    with pytest.raises(ValueError, match="membership_or_order"):
        reporter.origin_funnel(rows, pools=["B"], context=3, start_s=0, end_s=3000)
