import json
import shutil

import pytest

np = pytest.importorskip("numpy")
torch = pytest.importorskip("torch")

from test_experience_learning import CONFIG, PLAN  # noqa: E402
from test_experience_schema import custom_samples  # noqa: E402

from memecoin_experience import evaluation, generations  # noqa: E402
from memecoin_experience.learning import batches, train  # noqa: E402
from memecoin_experience.predict import Predictor  # noqa: E402
from memecoin_experience.storage import Snapshot, build, file_hash  # noqa: E402
from memecoin_experience.transfer import replicate  # noqa: E402


def dataset(tmp_path, *, evaluation_only=False, start=0):
    schema, rows = custom_samples()
    for row in rows:
        for key in ("decision_ns", "input_start_ns", "input_available_ns"):
            row[key] += start * 10**9
        for key in ("target_maturity_ns", "target_available_ns"):
            row[key] = [t + start * 10**9 for t in row[key]]
        for dep in row["input_dependencies"]:
            dep["event_ns"] += start * 10**9
            dep["available_ns"] += start * 10**9
    store = tmp_path / "store"
    report = build(
        store,
        rows,
        sources=[dict(synthetic=True)],
        schema=schema,
        cutoff=10**18,
        role="research_evaluation" if evaluation_only else "train",
    )
    return store, report["snapshot"]


@pytest.mark.parametrize("kind", ["gru-quantiles-v1", "temporal-mlp-quantiles-v1"])
def test_portable_prediction_and_strict_model_input(tmp_path, kind):
    store, pin = dataset(tmp_path)
    train(store, pin, tmp_path / "fit", plan=PLAN, config=dict(CONFIG, model_kind=kind))
    artifact = tmp_path / "fit/artifact"
    ref = file_hash(artifact / "manifest.json")
    first = Predictor(artifact, ref)
    x = next(batches(Snapshot(store, pin), PLAN, "test", 32))[0]
    expected = first.predict(x)
    moved = tmp_path / "portable"
    shutil.copytree(artifact, moved)
    shutil.rmtree(store)
    assert np.array_equal(Predictor(moved, ref).predict(x), expected)
    with pytest.raises(ValueError, match="model_input"):
        first.predict(x[:, :, :1])
    with pytest.raises(ValueError, match="model_input"):
        first.predict(x[:, :1, :])


def test_generation_resume_parent_and_lineage(tmp_path):
    store, pin = dataset(tmp_path)
    root = tmp_path / "generations"
    args = dict(name="V1", owner="research", store=store, snapshot=pin, split=PLAN, config=CONFIG)
    stopped = generations.run(root, **args, stop_after=1)
    assert stopped["status"] == "interrupted" and not list((root / "generations").iterdir())
    done = generations.run(root, **args)
    record, artifact = generations.get(root, done["generation"])
    assert record["declaration"]["owner"] == "research"
    with pytest.raises(ValueError, match="immutable_conflict"):
        generations.run(root, **dict(args, config=dict(CONFIG, epochs=3)))
    parent = dict(path=str(artifact), pin=done["artifact"])
    second = generations.run(root, **dict(args, name="V2"), parent=parent)
    record2, artifact2 = generations.get(root, second["generation"])
    assert record2["ancestors"] == [done["artifact"]]
    model1, model2 = Predictor(artifact, done["artifact"]), Predictor(artifact2, second["artifact"])
    assert model2.stats["mean"] == model1.stats["mean"]
    assert model2.stats["normalizer_parent"] == done["artifact"]
    assert (
        model2.report["binding"]["initialization"]
        == "parent_weights_frozen_normalizer_fresh_optimizer"
    )
    full = train(store, pin, tmp_path / "child-full", plan=PLAN, config=CONFIG, parent=parent)
    train(
        store, pin, tmp_path / "child-resume", plan=PLAN, config=CONFIG, parent=parent, stop_after=1
    )
    resumed = train(store, pin, tmp_path / "child-resume", plan=PLAN, config=CONFIG, parent=parent)
    assert full == resumed
    a = torch.load(tmp_path / "child-full/artifact/weights.pt", weights_only=True)
    b = torch.load(tmp_path / "child-resume/artifact/weights.pt", weights_only=True)
    assert all(torch.equal(v, b[k]) for k, v in a.items())


def test_replication_and_registered_evaluation(tmp_path):
    store, pin = dataset(tmp_path)
    replica = tmp_path / "replica"
    assert replicate(store, replica, pin)["copied"] > 0
    assert replicate(store, replica, pin)["copied"] == 0
    report = train(replica, pin, tmp_path / "fit", plan=PLAN, config=CONFIG)
    artifact = tmp_path / "fit/artifact"
    artifact_pin = file_hash(artifact / "manifest.json")
    protocols = tmp_path / "protocols"
    with pytest.raises(ValueError, match="seen_interval"):
        evaluation.register(
            protocols,
            artifact,
            artifact_pin,
            start_s=PLAN["test_end_s"] - 1,
            end_s=200000,
            pools=["synthetic"],
        )
    protocol = evaluation.register(
        protocols,
        artifact,
        artifact_pin,
        start_s=100000,
        end_s=200000,
        pools=["synthetic", "missing-pool"],
        scope="preregistered_forward_replay",
        now_ns=1,
    )
    eval_store, eval_pin = dataset(tmp_path / "evaluation", evaluation_only=True, start=100000)
    with pytest.raises(ValueError, match="reserved_snapshot"):
        train(eval_store, eval_pin, tmp_path / "forbidden", plan=PLAN, config=CONFIG)
    result = evaluation.score(
        protocols,
        protocol["protocol"],
        eval_store,
        eval_pin,
        artifact,
        tmp_path / "score",
        batch=73,
    )
    assert result["status"] == "completed" and result["enrollment"] == 2
    assert result["supported_pools"] == 1 and result["per_pool_records"]["missing-pool"] == 0
    assert result["metrics"][0]["n"] == 310
    assert result == evaluation.score(
        protocols,
        protocol["protocol"],
        eval_store,
        eval_pin,
        artifact,
        tmp_path / "score",
        batch=73,
    )
    assert file_hash(artifact / "manifest.json") == artifact_pin
    assert report["production_eligible"] is False
    assert len((tmp_path / "score/predictions.jsonl").read_text().splitlines()) == 310
    assert json.loads((tmp_path / "score/protocol.json").read_text())["candidate"] == artifact_pin


def test_v1_retrospective_evaluation_has_input_availability(tmp_path):
    from test_experience import bars

    store = tmp_path / "v1"
    first = build(store, bars(310), sources=[dict(synthetic=True)], cutoff=10**18)
    train(store, first["snapshot"], tmp_path / "fit", plan=PLAN, config=CONFIG)
    artifact = tmp_path / "fit/artifact"
    pin = file_hash(artifact / "manifest.json")
    protocol = evaluation.register(
        tmp_path / "protocols", artifact, pin, start_s=100000, end_s=200000, pools=["synthetic"]
    )
    later = build(
        store,
        bars(310, start=100200),
        sources=[dict(synthetic=True, later=True)],
        cutoff=10**18,
        role="research_evaluation",
    )
    result = evaluation.score(
        tmp_path / "protocols",
        protocol["protocol"],
        store,
        later["snapshot"],
        artifact,
        tmp_path / "scores",
    )
    assert result["metrics"][0]["n"] > 250


def test_repeated_absolute_interrupt_and_invalid_cuda_environment_fail_early(tmp_path, monkeypatch):
    store, pin = dataset(tmp_path)
    train(store, pin, tmp_path / "job", plan=PLAN, config=CONFIG, stop_after=1)
    with pytest.raises(ValueError, match="already_reached"):
        train(store, pin, tmp_path / "job", plan=PLAN, config=CONFIG, stop_after=1)
    monkeypatch.setenv("CUBLAS_WORKSPACE_CONFIG", "invalid")
    with pytest.raises(ValueError, match="workspace_config"):
        train(store, pin, tmp_path / "cuda", plan=PLAN, config=dict(CONFIG, device="cuda"))


def test_completed_generation_never_reenters_optimizer(tmp_path, monkeypatch):
    store, pin = dataset(tmp_path)
    root = tmp_path / "registry"
    args = dict(name="V1", owner="research", store=store, snapshot=pin, split=PLAN, config=CONFIG)
    first = generations.run(root, **args)
    attempts = list((root / "jobs" / first["generation"] / "attempts").iterdir())

    def forbidden(*args, **kwargs):
        raise AssertionError("completed generation must not train")

    monkeypatch.setattr(torch.optim.AdamW, "step", forbidden)
    assert generations.run(root, **args)["artifact"] == first["artifact"]
    assert list((root / "jobs" / first["generation"] / "attempts").iterdir()) == attempts
