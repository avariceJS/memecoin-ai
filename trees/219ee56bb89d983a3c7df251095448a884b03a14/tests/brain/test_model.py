import time

import pytest

torch = pytest.importorskip("torch", reason="optional ML environment")

from memecoin_brain.data import build, split  # noqa: E402
from memecoin_brain.model import fit_one, load_neural, scoring  # noqa: E402


def test_quantile_training_checkpoint_and_test_label_isolation(tmp_path):
    torch.set_num_threads(1)
    rows = [
        dict(
            pool="fixture",
            open_s=i * 300,
            ohlcv=[10 + i * 0.01, 11 + i * 0.01, 9 + i * 0.01, 10.1 + i * 0.01, 100 + i],
            available_ns=50000 * 10**9,
            capture_sha="fixture",
            lane="synthetic",
        )
        for i in range(100)
    ]
    bundle = build(rows, dict(as_of_ns=60000 * 10**9, kind="synthetic_fixture"))
    parts, _ = split(bundle)
    config = dict(kind="linear", seed=7, width=16, context=8)
    fit_one(bundle, parts, config, tmp_path / "first", deadline=time.monotonic() + 60)
    expected = load_neural(tmp_path / "first", bundle, parts["validation"])
    for s in parts["test"]:
        for t in s["targets"]:
            t["log_return"] = 999.0
    fit_one(bundle, parts, config, tmp_path / "second", deadline=time.monotonic() + 60)
    assert load_neural(tmp_path / "second", bundle, parts["validation"]) == expected
    assert scoring(parts["validation"], expected)["confirmatory"] is False
    with (tmp_path / "first/weights.pt").open("ab") as f:
        f.write(b"tamper")
    with pytest.raises(ValueError):
        load_neural(tmp_path / "first", bundle, parts["validation"])


def test_ordered_quantile_scoring_rejects_mismatch():
    with pytest.raises(ValueError):
        scoring([dict(targets=[{}])], [])
