from __future__ import annotations

from pathlib import Path

import pytest

torch = pytest.importorskip("torch", reason="optional ML environment required")
from memecoin_ml.data import Split, load_dataset, synthetic  # noqa: E402
from memecoin_ml.models import INPUTS, KINDS, Brain, ModelConfig  # noqa: E402
from memecoin_ml.training import (  # noqa: E402
    Predictor,
    TrainConfig,
    load_checkpoint,
    losses,
    train,
)


@pytest.mark.parametrize("kind", KINDS)
def test_padding_causality_ordered_quantiles_and_backward(kind: str) -> None:
    torch.set_num_threads(1)
    torch.manual_seed(7)
    model = Brain(ModelConfig(kind)).eval()
    x = torch.randn(2, 8, INPUTS)
    lengths = torch.tensor([3, 8])
    action = torch.tensor([[0, 0.001, 0, 20], [1, 0.001, 5, 20]])
    q, logits = model(x, lengths, action)
    changed = x.clone()
    changed[0, 3:] = 1e6
    q2, logits2 = model(changed, lengths, action)
    torch.testing.assert_close(q, q2, rtol=0, atol=0)
    torch.testing.assert_close(logits, logits2, rtol=0, atol=0)
    assert (q[:, 0] <= q[:, 1]).all() and (q[:, 1] <= q[:, 2]).all()
    (q.square().mean() + logits.square().mean()).backward()
    assert all(torch.isfinite(p.grad).all() for p in model.parameters() if p.grad is not None)
    # Other assets/batch members cannot change a prediction.
    single, _ = model(x[:1], lengths[:1], action[:1])
    torch.testing.assert_close(q[:1], single, rtol=1e-5, atol=1e-6)


def test_censored_labels_do_not_enter_loss() -> None:
    q = torch.randn(3, 3, requires_grad=True)
    logits = torch.randn(3, 3, requires_grad=True)
    data = dict(
        returns=torch.tensor([0.1, 999.0, -999.0]),
        return_mask=torch.tensor([True, False, False]),
        classes=torch.tensor([2, -1, -1]),
    )
    a, b = losses(q, logits, data)
    (a + b).backward()
    assert (q.grad[1:] == 0).all() and (logits.grad[1:] == 0).all()
    assert torch.isfinite(a + b)


def test_exact_epoch_resume_and_inference(tmp_path: Path) -> None:
    dataset = tmp_path / "data"
    synthetic(dataset)
    split = Split(0, 60 * 10**9, 90 * 10**9, 124 * 10**9)
    model = ModelConfig("gru")
    full = train(dataset, tmp_path / "full", split, TrainConfig(model, epochs=3))
    train(dataset, tmp_path / "first", split, TrainConfig(model, epochs=1))
    resumed = train(
        dataset,
        tmp_path / "resumed",
        split,
        TrainConfig(model, epochs=3),
        resume=tmp_path / "first",
    )
    assert full["epochs"] == resumed["epochs"]
    assert full["test"] == resumed["test"]
    left, right = load_checkpoint(tmp_path / "full"), load_checkpoint(tmp_path / "resumed")
    for key in left["model"]:
        torch.testing.assert_close(left["model"][key], right["model"][key], rtol=0, atol=0)
    with pytest.raises(ValueError, match="config/source mismatch"):
        train(
            dataset,
            tmp_path / "bad",
            split,
            TrainConfig(model, epochs=4, seed=19),
            resume=tmp_path / "first",
        )
    with pytest.raises(ValueError, match="already exists"):
        train(dataset, tmp_path / "full", split, TrainConfig(model))
    predictor = Predictor(tmp_path / "full")
    bundle = load_dataset(dataset)
    sample = bundle["samples"][100]
    frames = [bundle["frames"][key] for key in sample["history"]]
    prediction = predictor.predict(frames, sample["decision_ns"], [0, 0.001, 0, 2])
    assert prediction["qualification"] == "engineering_only"
    assert sum(prediction["conditional_execution_probabilities"]) == pytest.approx(1)


def test_market_mode_gate() -> None:
    with pytest.raises(ValueError, match="separately approved"):
        TrainConfig(ModelConfig(), mode="research")
    with pytest.raises(ValueError, match="budget"):
        TrainConfig(ModelConfig(), epochs=100)


def test_test_labels_cannot_change_selected_weights(tmp_path: Path) -> None:
    from memecoin_ml.data import save_dataset

    synthetic(tmp_path / "original")
    bundle = load_dataset(tmp_path / "original")
    for sample in bundle["samples"]:
        if sample["decision_ns"] >= 90 * 10**9:
            for target in sample["targets"]:
                if target["return"] is not None:
                    target["return"] = 1000.0
    save_dataset(tmp_path / "changed", bundle)
    split = Split(0, 60 * 10**9, 90 * 10**9, 124 * 10**9)
    config = TrainConfig(ModelConfig("linear"), epochs=2)
    train(tmp_path / "original", tmp_path / "a", split, config)
    train(tmp_path / "changed", tmp_path / "b", split, config)
    a = load_checkpoint(tmp_path / "a", "inference.pt")
    b = load_checkpoint(tmp_path / "b", "inference.pt")
    for key in a["model"]:
        torch.testing.assert_close(a["model"][key], b["model"][key], rtol=0, atol=0)
    checkpoint = tmp_path / "a/inference.pt"
    with checkpoint.open("ab") as stream:
        stream.write(b"corruption")
    with pytest.raises(ValueError, match="checksum"):
        Predictor(tmp_path / "a")
