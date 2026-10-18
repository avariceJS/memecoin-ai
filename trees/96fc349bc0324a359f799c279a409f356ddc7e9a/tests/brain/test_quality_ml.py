import time

import pytest

pytest.importorskip("torch", reason="optional ML environment")

from memecoin_brain import model, shadow  # noqa: E402
from memecoin_ml.data import read, write  # noqa: E402
from memecoin_registry.contracts import seal  # noqa: E402


def test_quarantined_pool_cannot_reach_any_candidate_inference(tmp_path, monkeypatch):
    (tmp_path / "shadow").mkdir()
    artifact = tmp_path / "model"
    artifact.mkdir()
    now = time.time_ns()
    end = now // (300 * 10**9) * 300
    write(
        artifact / "model-card.json",
        dict(
            finished_ns=now - 1000,
            training_as_of_ns=now - 2000,
            baseline={"1": [-0.1, 0, 0.1], "3": [-0.2, 0, 0.2]},
        ),
    )
    seal(artifact)
    rows = [
        dict(
            pool=p,
            open_s=end - (8 - i) * 300,
            ohlcv=[10, 12, 9, 11, 1],
            available_ns=now - 100,
            capture_sha="fixture",
        )
        for p in ("allowed", "blocked")
        for i in range(8)
    ]
    calls = []

    def infer(path, bundle, samples):
        calls.extend(s["pool"] for s in samples)
        assert all(s["pool"] == "allowed" for s in samples)
        assert all(t["log_return"] is None for s in samples for t in s["targets"])
        return [[-0.1, 0, 0.1] for s in samples for t in s["targets"]]

    monkeypatch.setattr(model, "predict", infer)
    monkeypatch.setattr(model, "ensemble_predict", lambda p, k, b, s: infer(p, b, s))
    result = shadow.tick(tmp_path, rows, dict(as_of_ns=now), artifact, prediction_pools={"allowed"})
    assert result["new_predictions"] == 2 and len(calls) == 3
    for path in (tmp_path / "shadow/predictions").glob("*.json"):
        assert read(path)["pool"] == "allowed"
