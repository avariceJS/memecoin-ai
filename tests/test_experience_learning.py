import json

import pytest

np = pytest.importorskip("numpy")
torch = pytest.importorskip("torch")

from test_experience import create  # noqa: E402

from memecoin_experience.learning import (  # noqa: E402
    Network,
    batches,
    loss,
    prepare,
    train,
)
from memecoin_experience.storage import Snapshot, file_hash  # noqa: E402

PLAN = dict(train_start_s=0, train_end_s=30000, validation_end_s=60000, test_end_s=90000)
CONFIG = dict(width=8, batch=32, epochs=2, seed=42, device="cpu")


def dataset(tmp_path):
    store = tmp_path / "store"
    result = create(store, n=310, chunk=43)
    return store, result["snapshot"], Snapshot(store, result["snapshot"])


def test_complete_epochs_and_cursor(tmp_path):
    _, _, snapshot = dataset(tmp_path)
    ordered = list(batches(snapshot, PLAN, "train", 13, seed=42))
    ids = [i for row in ordered for i in row[3]]
    other = [i for row in batches(snapshot, PLAN, "train", 13, seed=42, epoch=1) for i in row[3]]
    assert len(ids) == len(set(ids)) > 80
    assert set(ids) == set(other) and ids != other
    resumed = list(batches(snapshot, PLAN, "train", 13, seed=42, cursor=ordered[2][4]))
    assert [i for row in resumed for i in row[3]] == [i for row in ordered[3:] for i in row[3]]


def test_stats_train_only_and_quantiles(tmp_path):
    _, _, snapshot = dataset(tmp_path)
    stats = prepare(snapshot, PLAN, tmp_path, 17)
    parts = list(batches(snapshot, PLAN, "train", 17))
    x = np.concatenate([p[0].reshape(-1, 12) for p in parts])
    np.testing.assert_allclose(stats["mean"], np.nanmean(x, axis=0), atol=1e-12)
    np.testing.assert_allclose(stats["scale"], np.maximum(np.nanstd(x, axis=0), 1e-6), atol=1e-12)
    for h in range(2):
        y = sorted(v for p in parts for v in p[1][p[2][:, h], h])
        assert stats["baseline"][h][1] == y[int(np.ceil(0.5 * len(y))) - 1]
    assert stats["population"]["purged"] > 0


def test_partial_loss_and_horizon_heads():
    p = torch.zeros((3, 2, 3), requires_grad=True)
    y = torch.ones((3, 2))
    mask = torch.tensor([[True, False]] * 3)
    value = loss(p, y, mask)
    value.backward()
    assert torch.isfinite(value)
    assert p.grad[:, 1].abs().sum() == 0
    model = Network(8)
    out = model(torch.zeros((4, 16, 25)))
    assert out.shape == (4, 2, 3)
    assert torch.all(out[:, :, 0] <= out[:, :, 1])
    assert torch.all(out[:, :, 1] <= out[:, :, 2])


def test_exact_cpu_resume_immutable_result_and_binding(tmp_path):
    store, pin, _ = dataset(tmp_path)
    first, resumed = tmp_path / "first", tmp_path / "resumed"
    report = train(store, pin, first, plan=PLAN, config=CONFIG)
    pause = train(store, pin, resumed, plan=PLAN, config=CONFIG, stop_after=2)
    assert pause["status"] == "interrupted" and not (resumed / "artifact").exists()
    again = train(store, pin, resumed, plan=PLAN, config=CONFIG)
    assert again == report
    weights = torch.load(first / "artifact/weights.pt", weights_only=True)
    other = torch.load(resumed / "artifact/weights.pt", weights_only=True)
    assert all(torch.equal(v, other[k]) for k, v in weights.items())
    assert report["rows_seen"] == 2 * report["statistics"]["population"]["train_usable"]
    assert report["production_eligible"] is False
    pin_before = file_hash(first / "artifact/manifest.json")
    assert train(store, pin, first, plan=PLAN, config=CONFIG) == report
    assert file_hash(first / "artifact/manifest.json") == pin_before
    with pytest.raises(ValueError, match="immutable_conflict"):
        train(store, pin, resumed, plan=PLAN, config=dict(CONFIG, width=16))
    (first / "artifact/report.json").write_text(json.dumps({"wrong": True}))
    with pytest.raises(ValueError, match="checksum"):
        train(store, pin, first, plan=PLAN, config=CONFIG)


def test_crash_replays_uncheckpointed_steps(tmp_path, monkeypatch):
    store, pin, _ = dataset(tmp_path)
    config = dict(CONFIG, checkpoint_every=2)
    expected = train(store, pin, tmp_path / "complete", plan=PLAN, config=config)
    original = torch.optim.AdamW.step
    steps = 0

    def broken(optimizer, *args, **kwargs):
        nonlocal steps
        result = original(optimizer, *args, **kwargs)
        steps += 1
        if steps == 4:
            raise RuntimeError("synthetic process interruption after optimizer mutation")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(torch.optim.AdamW, "step", broken)
        with pytest.raises(RuntimeError):
            train(store, pin, tmp_path / "crashed", plan=PLAN, config=config)
    actual = train(store, pin, tmp_path / "crashed", plan=PLAN, config=config)
    assert actual == expected
    left = torch.load(tmp_path / "complete/artifact/weights.pt", weights_only=True)
    right = torch.load(tmp_path / "crashed/artifact/weights.pt", weights_only=True)
    assert all(torch.equal(v, right[k]) for k, v in left.items())


def test_empty_split_and_unowned_output_fail_closed(tmp_path):
    store, pin, _ = dataset(tmp_path)
    tiny = dict(PLAN, train_end_s=300)
    with pytest.raises(ValueError, match="empty_training"):
        train(store, pin, tmp_path / "empty", plan=tiny, config=CONFIG)
    with pytest.raises(ValueError, match="unowned"):
        train(store, pin, tmp_path, plan=PLAN, config=CONFIG)


def test_cli_trains_larger_context_and_width(tmp_path, monkeypatch, capsys):
    from memecoin_experience.__main__ import main

    store = tmp_path / "store"
    result = create(store, n=310, context=32)
    output, plan = tmp_path / "run", tmp_path / "plan.json"
    plan.write_text(json.dumps(dict(split=PLAN, config=dict(CONFIG, width=64, epochs=1))))
    monkeypatch.setattr(
        "sys.argv",
        [
            "experience",
            "train",
            "--store",
            str(store),
            "--snapshot",
            result["snapshot"],
            "--plan",
            str(plan),
            "--output",
            str(output),
        ],
    )
    main()
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "completed"
    assert report["parameters"] > 2174
    assert (output / "artifact/manifest.json").exists()
