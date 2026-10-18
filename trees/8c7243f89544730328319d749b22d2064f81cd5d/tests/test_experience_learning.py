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
    recipe,
    tensors,
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
    assert all(len(row[3]) == 13 for row in ordered[:-1])


def test_batches_span_partitions_and_resume_at_every_boundary(tmp_path):
    _, _, snapshot = dataset(tmp_path)
    # Partitions have <=43 rows; requested GPU batch size must not be limited by that layout.
    for batch in (32, 64, 256):
        records = list(batches(snapshot, PLAN, "train", batch, seed=42))
        assert all(len(r[3]) == batch for r in records[:-1])
        assert len(records[0][3]) == min(batch, 91)
        for index, row in enumerate(records):
            rest = list(batches(snapshot, PLAN, "train", batch, seed=42, cursor=row[4]))
            assert [i for r in rest for i in r[3]] == [
                i for r in records[index + 1 :] for i in r[3]
            ]


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


def test_partial_count_loss_uses_only_the_guaranteed_lower_bound():
    p = torch.zeros((1, 2, 3), requires_grad=True)
    y = torch.tensor([[0.0, 0.0]])
    exact = torch.tensor([[False, True]])
    lower = torch.tensor([[2.0, 0.0]])
    censored = torch.tensor([[True, False]])
    value = loss(p, y, exact, lower_bound=lower, censored=censored)
    assert value.item() == pytest.approx(
        0.5
    )  # bound envelope averaged with the separate exact head
    value.backward()
    assert torch.all(p.grad[0, 0] < 0)  # Predictions below L are contradicted.
    assert torch.any(p.grad[0, 1] != 0)  # An exact zero still receives ordinary pinball gradients.


def test_log_count_transform_is_reversed_for_reported_quantiles():
    from memecoin_experience.learning import inverse_predictions, transform_targets

    values = np.array([[0.0, 3.0]])
    transformed = transform_targets(values, ["identity", "log1p_nonnegative"])
    np.testing.assert_allclose(transformed, [[0.0, np.log(4.0)]])
    predictions = np.repeat(transformed[:, :, None], 3, axis=2)
    restored = inverse_predictions(
        predictions,
        {"target_transforms": ["identity", "log1p_nonnegative"]},
    )
    np.testing.assert_allclose(restored[0, 1], [3.0, 3.0, 3.0])


def test_legacy_recipe_keeps_default_objective_and_target_units():
    from memecoin_experience.learning import recipe, transform_targets

    assert recipe({})["objective"] == "masked_pinball_v1"
    np.testing.assert_array_equal(
        transform_targets(np.array([[0.5]]), ["identity"]), np.array([[0.5]])
    )


def test_nonfinite_input_not_reclassified_as_missing():
    x, y = np.zeros((1, 8, 12)), np.zeros((1, 2))
    mask = np.ones((1, 2), dtype=bool)
    stats = dict(mean=[0] * 12, scale=[1] * 12)
    x[0, 0, 0] = np.inf
    with pytest.raises(ValueError, match="nonfinite_learning_input"):
        tensors(x, y, mask, stats, "cpu")
    x[0, 0, 0], y[0, 0] = np.nan, np.nan
    with pytest.raises(ValueError, match="nonfinite_learning_input"):
        tensors(x, y, mask, stats, "cpu")
    mask[0, 0] = False
    tx, ty, _ = tensors(x, y, mask, stats, "cpu")
    assert torch.isfinite(tx).all() and torch.isfinite(ty).all()


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
    assert report["seen_end_s"] == PLAN["test_end_s"]
    pin_before = file_hash(first / "artifact/manifest.json")
    assert train(store, pin, first, plan=PLAN, config=CONFIG) == report
    assert file_hash(first / "artifact/manifest.json") == pin_before
    binding_pin = file_hash(resumed / "binding.json")
    with pytest.raises(ValueError, match="immutable_conflict"):
        train(store, pin, resumed, plan=PLAN, config=dict(CONFIG, width=16))
    assert file_hash(resumed / "binding.json") == binding_pin
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


def test_incomplete_epoch_never_publishes(tmp_path):
    store, pin, snapshot = dataset(tmp_path)
    output = tmp_path / "run"
    train(store, pin, output, plan=PLAN, config=CONFIG, stop_after=1)
    checkpoint = output / "checkpoint.pt"
    state = torch.load(checkpoint, weights_only=True)
    state["cursor"] = (len(snapshot.parts), 0)
    torch.save(state, checkpoint)
    with pytest.raises(ValueError, match="incomplete_epoch"):
        train(store, pin, output, plan=PLAN, config=CONFIG)
    assert not (output / "artifact").exists()


@pytest.mark.parametrize("lr", ["0.001", None, [], float("nan")])
def test_bad_recipe_uses_contract_error(lr):
    with pytest.raises(ValueError, match="training_config"):
        recipe(dict(lr=lr))


def test_statistics_are_bound_before_first_checkpoint(tmp_path, monkeypatch):
    store, pin, _ = dataset(tmp_path)
    output = tmp_path / "run"

    def crash(*args, **kwargs):
        raise RuntimeError("first optimizer update interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(torch.optim.AdamW, "step", crash)
        with pytest.raises(RuntimeError):
            train(store, pin, output, plan=PLAN, config=CONFIG)
    assert not (output / "checkpoint.pt").exists()
    stats = json.loads((output / "statistics.json").read_text())
    stats["mean"][0] += 1
    (output / "statistics.json").write_text(json.dumps(stats))
    with pytest.raises(ValueError, match="statistics_binding"):
        train(store, pin, output, plan=PLAN, config=CONFIG)
    assert not (output / "artifact").exists()


def test_uncommitted_statistics_recomputed_after_crash(tmp_path, monkeypatch):
    from memecoin_experience import learning

    store, pin, _ = dataset(tmp_path)
    expected = train(store, pin, tmp_path / "full", plan=PLAN, config=CONFIG)
    output = tmp_path / "run"
    original = learning.atomic_json

    def crash(path, value, **kwargs):
        if path.name == "binding.json":
            raise RuntimeError("statistics commit interrupted")
        return original(path, value, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(learning, "atomic_json", crash)
        with pytest.raises(RuntimeError):
            train(store, pin, output, plan=PLAN, config=CONFIG)
    assert not (output / "binding.json").exists()
    (output / "statistics.json").write_text("{}")
    assert train(store, pin, output, plan=PLAN, config=CONFIG) == expected


@pytest.mark.skipif(not torch.cuda.is_available(), reason="requires actual CUDA qualification host")
def test_cuda_training_and_resume(tmp_path):
    store, pin, _ = dataset(tmp_path)
    config = dict(CONFIG, device="cuda")
    expected = train(store, pin, tmp_path / "full", plan=PLAN, config=config)
    train(store, pin, tmp_path / "resumed", plan=PLAN, config=config, stop_after=4)
    actual = train(store, pin, tmp_path / "resumed", plan=PLAN, config=config)
    assert actual == expected
