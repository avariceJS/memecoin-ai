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
from memecoin_experience.storage import (  # noqa: E402
    Snapshot,
    atomic_json,
    digest,
    file_hash,
    seal_files,
)

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


def test_statistics_contract_and_legacy_optional_transform(tmp_path):
    from memecoin_experience.learning import validate_statistics

    _, _, snapshot = dataset(tmp_path)
    stats = prepare(snapshot, PLAN, tmp_path, 17)

    def wrong_mean_dimension(value):
        value["mean"].pop()

    def nonfinite_mean(value):
        value["mean"][0] = float("nan")

    def zero_scale(value):
        value["scale"][0] = 0

    def infinite_scale(value):
        value["scale"][0] = float("inf")

    def wrong_baseline_dimension(value):
        value["baseline"].pop()

    def wrong_baseline_quantiles(value):
        value["baseline"][0] = [1.0, 2.0]

    def nonfinite_baseline(value):
        value["baseline"][0][0] = float("inf")

    def unordered_baseline(value):
        value["baseline"][0] = [3.0, 2.0, 1.0]

    def wrong_transform_dimension(value):
        value["target_transforms"].pop()

    def unsupported_transform(value):
        value["target_transforms"][0] = "log10"

    for corrupt in (
        wrong_mean_dimension,
        nonfinite_mean,
        zero_scale,
        infinite_scale,
        wrong_baseline_dimension,
        wrong_baseline_quantiles,
        nonfinite_baseline,
        unordered_baseline,
        wrong_transform_dimension,
        unsupported_transform,
    ):
        changed = json.loads(json.dumps(stats))
        corrupt(changed)
        with pytest.raises(ValueError, match="experience_statistics_contract"):
            validate_statistics(changed, snapshot.schema)

    legacy = json.loads(json.dumps(stats))
    legacy.pop("target_transforms")
    legacy["baseline"][0] = None
    validate_statistics(legacy, snapshot.schema)


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


def test_training_rejects_nonfinite_forward_before_optimizer_step(tmp_path, monkeypatch):
    from memecoin_experience import learning

    store, pin, snapshot = dataset(tmp_path)
    target_count = len(learning.target_names(snapshot.schema))

    class NonfiniteModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.bias = torch.nn.Parameter(torch.zeros(()))

        def forward(self, value):
            return self.bias + torch.full(
                (len(value), target_count, 3),
                float("inf"),
                device=value.device,
            )

    monkeypatch.setattr(learning, "construct", lambda spec: NonfiniteModel())
    optimizer_steps = []
    monkeypatch.setattr(
        torch.optim.AdamW, "step", lambda *args, **kwargs: optimizer_steps.append(1)
    )
    output = tmp_path / "nonfinite-forward"
    with pytest.raises(ValueError, match="experience_nonfinite_prediction"):
        train(store, pin, output, plan=PLAN, config=CONFIG)
    assert not optimizer_steps
    assert not (output / "checkpoint.pt").exists()
    assert not (output / "artifact").exists()


def test_cpu_training_restores_torch_process_state_on_all_exits(tmp_path, monkeypatch):
    from memecoin_experience import learning

    store, pin, _ = dataset(tmp_path)

    def capture():
        return dict(
            rng=torch.get_rng_state().clone(),
            threads=torch.get_num_threads(),
            deterministic=torch.are_deterministic_algorithms_enabled(),
            warn_only=torch.is_deterministic_algorithms_warn_only_enabled(),
            cudnn_benchmark=torch.backends.cudnn.benchmark,
            allow_tf32=torch.backends.cuda.matmul.allow_tf32,
        )

    def assert_restored(expected):
        actual = capture()
        assert torch.equal(actual.pop("rng"), expected["rng"])
        assert actual == {key: value for key, value in expected.items() if key != "rng"}

    original = capture()
    try:
        torch.random.default_generator.manual_seed(20261015)
        torch.set_num_threads(2)
        torch.use_deterministic_algorithms(False, warn_only=True)
        torch.backends.cudnn.benchmark = True
        torch.backends.cuda.matmul.allow_tf32 = True
        expected = capture()

        train(store, pin, tmp_path / "complete", plan=PLAN, config=CONFIG)
        assert_restored(expected)

        train(store, pin, tmp_path / "interrupted", plan=PLAN, config=CONFIG, stop_after=1)
        assert_restored(expected)

        def fail_construction(spec):
            raise RuntimeError("synthetic model construction failure")

        with monkeypatch.context() as patch:
            patch.setattr(learning, "construct", fail_construction)
            with pytest.raises(RuntimeError, match="synthetic model construction failure"):
                train(store, pin, tmp_path / "failed", plan=PLAN, config=CONFIG)
        assert_restored(expected)
    finally:
        learning._restore_cpu_training_process_state(original)


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


def test_checkpoint_optimizer_state_must_match_recipe_and_parameters(tmp_path, monkeypatch):
    store, pin, _ = dataset(tmp_path)

    def change_lr(state):
        state["optimizer"]["param_groups"][0]["lr"] *= 2

    def change_weight_decay(state):
        state["optimizer"]["param_groups"][0]["weight_decay"] *= 2

    def change_betas(state):
        state["optimizer"]["param_groups"][0]["betas"] = (0.8, 0.99)

    def swap_parameter_mapping(state):
        state["optimizer"]["param_groups"][0]["params"].reverse()

    def remove_parameter_mapping(state):
        state["optimizer"]["param_groups"][0]["params"].pop()

    def remove_parameter_state(state):
        state["optimizer"]["state"].pop(next(iter(state["optimizer"]["state"])))

    def nonfinite_moment(state):
        moment = next(iter(state["optimizer"]["state"].values()))["exp_avg"]
        moment.reshape(-1)[0] = float("nan")

    def stale_parameter_step(state):
        step = next(iter(state["optimizer"]["state"].values()))["step"]
        step.sub_(1)

    def wrong_moment_dtype(state):
        parameter_state = next(iter(state["optimizer"]["state"].values()))
        parameter_state["exp_avg"] = parameter_state["exp_avg"].to(torch.float64)

    def wrong_moment_shape(state):
        parameter_state = next(iter(state["optimizer"]["state"].values()))
        parameter_state["exp_avg_sq"] = parameter_state["exp_avg_sq"].reshape(-1)[:-1]

    corruptions = (
        change_lr,
        change_weight_decay,
        change_betas,
        swap_parameter_mapping,
        remove_parameter_mapping,
        remove_parameter_state,
        stale_parameter_step,
        nonfinite_moment,
        wrong_moment_dtype,
        wrong_moment_shape,
    )
    for index, corrupt in enumerate(corruptions):
        output = tmp_path / f"corrupt-optimizer-{index}"
        train(store, pin, output, plan=PLAN, config=CONFIG, stop_after=2)
        checkpoint = output / "checkpoint.pt"
        saved = torch.load(checkpoint, map_location="cpu", weights_only=True)
        corrupt(saved)
        torch.save(saved, checkpoint)
        changed_checkpoint_pin = file_hash(checkpoint)

        def optimizer_restore_must_not_run(*args, **kwargs):
            raise AssertionError("optimizer state restored before validation")

        with monkeypatch.context() as patch:
            patch.setattr(torch.optim.AdamW, "load_state_dict", optimizer_restore_must_not_run)
            with pytest.raises(ValueError, match="experience_checkpoint_optimizer"):
                train(store, pin, output, plan=PLAN, config=CONFIG)
        assert file_hash(checkpoint) == changed_checkpoint_pin
        assert not (output / "artifact").exists()


def test_checkpoint_model_and_best_weights_must_be_finite(tmp_path, monkeypatch):
    store, pin, _ = dataset(tmp_path)
    for name, field, replacement in (
        ("model", "model", float("nan")),
        ("best", "best", float("inf")),
    ):
        output = tmp_path / f"nonfinite-checkpoint-{name}"
        train(store, pin, output, plan=PLAN, config=CONFIG, stop_after=5)
        checkpoint = output / "checkpoint.pt"
        saved = torch.load(checkpoint, map_location="cpu", weights_only=True)
        assert saved[field] is not None
        for tensor in saved[field].values():
            if torch.is_floating_point(tensor):
                tensor.reshape(-1)[0] = replacement
                break
        else:
            raise AssertionError(f"checkpoint {field} has no floating-point tensors")
        torch.save(saved, checkpoint)
        checkpoint_pin = file_hash(checkpoint)

        def state_restore_must_not_run(*args, **kwargs):
            raise AssertionError("checkpoint state restored before finite-value validation")

        with monkeypatch.context() as patch:
            patch.setattr(torch.nn.Module, "load_state_dict", state_restore_must_not_run)
            with pytest.raises(ValueError, match="experience_checkpoint_progress"):
                train(store, pin, output, plan=PLAN, config=CONFIG)
        assert file_hash(checkpoint) == checkpoint_pin
        assert not (output / "artifact").exists()


def test_completed_training_artifact_reuse_revalidates_bound_contents(tmp_path):
    store, pin, _ = dataset(tmp_path)
    output = tmp_path / "completed-job"
    report = train(store, pin, output, plan=PLAN, config=CONFIG)
    artifact = output / "artifact"
    names = ("manifest.json", "model.json", "report.json", "statistics.json", "weights.pt")
    original = {name: (artifact / name).read_bytes() for name in names}

    def reseal():
        (artifact / "manifest.json").unlink()
        seal_files(artifact)

    def change_report_status():
        changed = json.loads(original["report.json"])
        changed["status"] = "failed"
        atomic_json(artifact / "report.json", changed, replace=True)

    def make_production_eligible():
        changed = json.loads(original["report.json"])
        changed["production_eligible"] = True
        atomic_json(artifact / "report.json", changed, replace=True)

    def mismatch_model():
        changed = json.loads(original["model.json"])
        changed["width"] += 1
        atomic_json(artifact / "model.json", changed, replace=True)

    def mismatch_bound_statistics():
        statistics = json.loads(original["statistics.json"])
        statistics["mean"][0] += 1
        changed = json.loads(original["report.json"])
        changed["statistics"] = statistics
        changed["binding"]["statistics"] = digest(statistics)
        atomic_json(artifact / "statistics.json", statistics, replace=True)
        atomic_json(artifact / "report.json", changed, replace=True)

    def nonfinite_weights():
        weights = torch.load(artifact / "weights.pt", map_location="cpu", weights_only=True)
        for tensor in weights.values():
            if torch.is_floating_point(tensor):
                tensor.reshape(-1)[0] = float("nan")
                break
        else:
            raise AssertionError("trained artifact has no floating-point weights")
        torch.save(weights, artifact / "weights.pt")

    def wrong_report_epochs():
        changed = json.loads(original["report.json"])
        changed["epochs"] -= 1
        atomic_json(artifact / "report.json", changed, replace=True)

    def wrong_report_steps():
        changed = json.loads(original["report.json"])
        changed["steps"] += 1
        atomic_json(artifact / "report.json", changed, replace=True)

    def wrong_report_rows_seen():
        changed = json.loads(original["report.json"])
        changed["rows_seen"] += 1
        atomic_json(artifact / "report.json", changed, replace=True)

    def short_report_history():
        changed = json.loads(original["report.json"])
        changed["history"].pop()
        atomic_json(artifact / "report.json", changed, replace=True)

    def wrong_report_history_epoch():
        changed = json.loads(original["report.json"])
        changed["history"][0]["epoch"] = 8
        atomic_json(artifact / "report.json", changed, replace=True)

    def nonfinite_report_score():
        changed = json.loads(original["report.json"])
        changed["history"][0]["validation"]["selection_score"] = float("nan")
        (artifact / "report.json").write_text(json.dumps(changed, allow_nan=True))

    for corrupt in (
        change_report_status,
        make_production_eligible,
        mismatch_model,
        mismatch_bound_statistics,
        nonfinite_weights,
        wrong_report_epochs,
        wrong_report_steps,
        wrong_report_rows_seen,
        short_report_history,
        wrong_report_history_epoch,
        nonfinite_report_score,
    ):
        for name, contents in original.items():
            (artifact / name).write_bytes(contents)
        corrupt()
        reseal()
        changed = {name: (artifact / name).read_bytes() for name in names}
        with pytest.raises(ValueError):
            train(store, pin, output, plan=PLAN, config=CONFIG)
        assert changed == {name: (artifact / name).read_bytes() for name in names}

    for name, contents in original.items():
        (artifact / name).write_bytes(contents)
    assert train(store, pin, output, plan=PLAN, config=CONFIG) == report


def test_completed_training_artifact_reuse_rejects_manifest_symlink(tmp_path):
    store, pin, _ = dataset(tmp_path)
    output = tmp_path / "completed-job"
    train(store, pin, output, plan=PLAN, config=CONFIG)
    manifest = output / "artifact/manifest.json"
    original = manifest.read_bytes()
    external = tmp_path / "external-manifest.json"
    external.write_bytes(original)
    manifest.unlink()
    manifest.symlink_to(external)

    with pytest.raises(ValueError, match="experience_symlink"):
        train(store, pin, output, plan=PLAN, config=CONFIG)
    assert manifest.is_symlink()
    assert external.read_bytes() == original


def test_empty_optimizer_checkpoint_state_is_valid_before_first_step():
    from memecoin_experience import learning

    model = torch.nn.Linear(2, 1)
    optimizer = torch.optim.AdamW(model.parameters(), lr=recipe(CONFIG)["lr"])
    learning._validate_checkpoint_optimizer(optimizer.state_dict(), optimizer, 0)


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


def test_inconsistent_checkpoint_cursor_never_publishes(tmp_path):
    store, pin, snapshot = dataset(tmp_path)
    output = tmp_path / "run"
    train(store, pin, output, plan=PLAN, config=CONFIG, stop_after=1)
    checkpoint = output / "checkpoint.pt"
    state = torch.load(checkpoint, weights_only=True)
    state["cursor"] = (len(snapshot.parts), 0)
    torch.save(state, checkpoint)
    with pytest.raises(ValueError, match="experience_checkpoint_progress"):
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


def test_invalid_bound_statistics_reject_resume_before_optimizer_restore(tmp_path, monkeypatch):
    store, pin, _ = dataset(tmp_path)
    output = tmp_path / "run"
    train(store, pin, output, plan=PLAN, config=CONFIG, stop_after=1)
    checkpoint = output / "checkpoint.pt"
    checkpoint_hash = file_hash(checkpoint)
    stats = json.loads((output / "statistics.json").read_text())
    stats["scale"][0] = 0
    binding = json.loads((output / "binding.json").read_text())
    binding["statistics"] = digest(stats)
    atomic_json(output / "statistics.json", stats, replace=True)
    atomic_json(output / "binding.json", binding, replace=True)

    def optimizer_restore_must_not_run(*args, **kwargs):
        raise AssertionError("optimizer restored before statistics validation")

    with monkeypatch.context() as patch:
        patch.setattr(torch.optim.AdamW, "load_state_dict", optimizer_restore_must_not_run)
        with pytest.raises(ValueError, match="experience_statistics_contract"):
            train(store, pin, output, plan=PLAN, config=CONFIG)
    assert file_hash(checkpoint) == checkpoint_hash
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


def test_model_spec_validation_and_legacy_bindings(tmp_path, monkeypatch):
    from memecoin_experience import models

    _, _, snapshot = dataset(tmp_path)
    spec = models.model_spec(snapshot.schema, 8)
    assert isinstance(models.construct(spec), torch.nn.Module)

    legacy = dict(spec)
    legacy.pop("implementation_sha")
    legacy.pop("input_encoding")
    assert isinstance(models.construct(legacy), torch.nn.Module)

    malformed = (
        None,
        {},
        dict(spec, width=True),
        dict(spec, quantiles=(0.1, 0.5, 0.9)),
        dict(spec, unexpected=True),
    )

    def construction_must_not_run(*args, **kwargs):
        raise AssertionError("module construction ran before spec validation")

    for value in malformed:
        with monkeypatch.context() as patch:
            patch.setattr(models, "GRU", construction_must_not_run)
            with pytest.raises(ValueError, match="experience_model_spec"):
                models.construct(value)

    for field in ("implementation_sha", "input_encoding"):
        changed = dict(spec, **{field: "mismatched-binding"})
        with pytest.raises(ValueError, match="experience_model_implementation"):
            models.construct(changed)
