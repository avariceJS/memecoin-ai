import pytest

pytest.importorskip("torch", reason="optional ML environment")

from memecoin_brain.data import build, save_dataset  # noqa: E402
from memecoin_brain_scale.curves import run  # noqa: E402
from memecoin_registry.contracts import verify  # noqa: E402


def test_full_curve_fixture_runs_fixed_budget_and_seals_all_trials(tmp_path):
    rows = [
        dict(
            pool="fixture",
            open_s=i * 300,
            ohlcv=[10, 12, 9, 10 + (i % 7) * 0.01, 100 + i],
            available_ns=100000 * 10**9,
            capture_sha="fixture",
        )
        for i in range(220)
    ]
    bundle = build(rows, dict(as_of_ns=100001 * 10**9, kind="synthetic_fixture"))
    dataset = tmp_path / "dataset"
    save_dataset(dataset, bundle)
    result = run(dataset, tmp_path / "output")
    assert len(result["experiments"]) == 6
    assert sum(len(e["fits"]) for e in result["experiments"]) == 24
    assert not result["architecture_selected"]
    verify(tmp_path / "output")
