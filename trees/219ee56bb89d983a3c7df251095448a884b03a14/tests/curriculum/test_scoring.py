import pytest

from memecoin_curriculum.scoring import paired


def test_paired_day_support_gate_and_common_resampling():
    rows = [
        dict(pool=p, day=day, losses=dict(a=float(day + 1), b=0.9 * (day + 1)))
        for p in ("x", "y")
        for day in range(6)
    ]
    short = paired([r for r in rows if r["day"] < 2], "a", "b")
    assert short["ci"] is None and short["relative_gain"] == pytest.approx(0.1)
    supported = paired(rows, "a", "b")
    assert supported["ci"] == pytest.approx([0.1, 0.1])
    assert supported == paired(rows, "a", "b")
