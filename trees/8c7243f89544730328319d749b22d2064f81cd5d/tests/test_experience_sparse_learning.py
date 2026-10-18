"""Generic sparse price/count/lifecycle integration; synthetic markets only."""

import importlib.util
import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
torch = pytest.importorskip("torch")

from test_experience_sparse import NS, contract, facts, materialized, plan, source  # noqa: E402

from memecoin_experience import evaluation, generations, observations, sparse  # noqa: E402
from memecoin_experience.learning import _censor_bounds, batches, train  # noqa: E402
from memecoin_experience.predict import Predictor  # noqa: E402
from memecoin_experience.storage import Snapshot, file_hash  # noqa: E402
from memecoin_population import catalog, diversity  # noqa: E402


def test_partial_count_batch_keeps_censored_count_separate_from_exact_mask(tmp_path):
    rows = facts()
    rows[-1]["status"] = "unknown"
    projection = plan()
    projection["targets"] = [projection["targets"][1]]
    source_data = source(tmp_path, rows)
    built = sparse.materialize(
        source_data.store,
        source_data.pin,
        tmp_path / "snapshot",
        plan=projection,
        chunk=2,
    )
    snapshot = Snapshot(tmp_path / "snapshot", built["snapshot"])
    split = dict(train_start_s=0, train_end_s=50, validation_end_s=60, test_end_s=70)
    batches_out = list(batches(snapshot, split, "train", 16, with_censoring=True))
    assert batches_out
    _, _, exact_mask, lower, censored, ids, _ = batches_out[0]
    i = ids.index("event:ticks:0")
    assert exact_mask[i].tolist() == [False]
    assert lower[i].tolist() == [1.0]
    assert censored[i].tolist() == [True]


def test_late_point_in_time_censor_bound_is_not_train_supervision(tmp_path):
    rows = facts()
    rows[-1]["status"] = "unknown"
    next(r for r in rows if r["kind"] == "event" and r["event_ns"] == 11 * NS)["available_ns"] = (
        55 * NS
    )
    _, samples, _ = materialized(tmp_path, rows, availability="point_in_time")
    schema = sparse.schema(source(tmp_path / "schema", rows, availability="point_in_time"), plan())
    split = dict(train_start_s=0, train_end_s=50, validation_end_s=60, test_end_s=70)
    assert not any(_censor_bounds(samples[1], schema, "train", split)[1])


def test_namespaced_protected_evaluation_rejected_before_artifact_read(tmp_path):
    from memecoin_brain.source import EXCLUDED
    from memecoin_experience.storage import atomic_json, digest

    with pytest.raises(ValueError, match="evaluation_protocol"):
        evaluation.register(
            tmp_path / "protocols",
            tmp_path / "absent-artifact",
            "a" * 64,
            start_s=10,
            end_s=20,
            pools=["solana/pool/" + next(iter(EXCLUDED))],
        )
    protocol = dict(
        version="brain-research-evaluation-v1", pools=["solana/pool/" + next(iter(EXCLUDED))]
    )
    pin = digest(protocol)
    atomic_json(tmp_path / (pin + ".json"), protocol)
    with pytest.raises(ValueError, match="evaluation_protocol_pin"):
        evaluation.score(
            tmp_path,
            pin,
            tmp_path / "absent-store",
            "b" * 64,
            tmp_path / "absent-artifact",
            tmp_path / "output",
        )


def data(store, *, eval_only=False, support_status="complete"):
    rows = []
    members = []
    for phase in [3] if eval_only else [0, 1, 2]:
        for market in range(3):
            start = phase * 1000
            end = start + 100
            entity = f"test/market/{phase}-{market}"
            common = dict(entity=entity, evidence=["a" * 64])
            rows.append(
                dict(
                    common,
                    kind="enrollment",
                    start_ns=start * NS,
                    end_ns=end * NS,
                    available_ns=start * NS,
                )
            )
            rows.append(
                dict(
                    common,
                    kind="lifecycle",
                    id="birth",
                    event_ns=start * NS,
                    available_ns=start * NS,
                    transition="birth",
                )
            )
            # One zero-event market remains a supervised lifecycle/count origin.
            if market:
                for i in range(1, 50):
                    if i % 5 == 0:
                        continue
                    t = (start + i * 2) * NS
                    rows.append(
                        dict(
                            common,
                            kind="event",
                            stream="ticks",
                            id=str(i),
                            event_ns=t,
                            available_ns=t,
                            values=[10 + i * 0.01 + market * 0.1],
                        )
                    )
            rows.append(
                dict(
                    common,
                    kind="support",
                    stream="ticks",
                    start_ns=start * NS,
                    end_ns=end * NS,
                    available_ns=end * NS,
                    status=support_status,
                )
            )
            members.append(
                dict(
                    entity=entity,
                    start_ns=start * NS,
                    end_ns=end * NS,
                    known_ns=start * NS,
                    attributes=dict(lifecycle="enrolled"),
                    evidence=["a" * 64],
                )
            )
    role = "research_evaluation" if eval_only else "train"
    pin = observations.publish(
        store,
        sorted(rows, key=observations.key),
        schema=contract(role=role),
        cutoff=5000 * NS,
        sources=[dict(synthetic=True)],
        chunk=16,
    )
    return pin, members


def test_sparse_generation_exposure_resume_replica_evaluation(tmp_path):
    source = tmp_path / "sources"
    pin, members = data(source)
    cfg = plan()
    cfg.update(context=8, max_history_ns=50 * NS)
    store = tmp_path / "experience"
    built = sparse.materialize(source, pin, store, plan=cfg, chunk=16)
    split = dict(train_start_s=0, train_end_s=1000, validation_end_s=2000, test_end_s=3000)
    population = catalog.publish(
        tmp_path / "population",
        "population",
        members,
        cutoff_ns=5000 * NS,
        definition=dict(synthetic=True, role="train"),
    )
    policy = dict(
        entity_prefix="",
        knowledge="at_decision",
        axes=[["@entity"], ["lifecycle"]],
        time_bucket_ns=100 * NS,
        selection=dict(salt="fixed", numerator=1, denominator=1),
        enforcement="require",
        min_train_entities=3,
        max_entity_share=1,
        max_unknown_fraction=0,
        objective=diversity.OBJECTIVE,
    )
    audit = diversity.audit(
        store,
        built["snapshot"],
        tmp_path / "population",
        population,
        tmp_path / "audit",
        split=split,
        config=policy,
    )
    assert audit["unmapped_rows"] == 0
    assert audit["train_distribution"]['["@entity"]']["supported_values"] == 3
    ref = dict(path=str(tmp_path / "audit"), pin=file_hash(tmp_path / "audit/manifest.json"))
    config = dict(
        width=8,
        epochs=2,
        batch=16,
        seed=19,
        cpu_threads=1,
        checkpoint_every=2,
        normalization="constant_feature_scale_one_v1",
    )
    kwargs = dict(
        owner="test",
        store=store,
        snapshot=built["snapshot"],
        split=split,
        config=config,
        diversity=ref,
    )
    root = tmp_path / "models"
    assert generations.run(root, name="resumed", stop_after=1, **kwargs)["status"] == "interrupted"
    resumed = generations.run(root, name="resumed", **kwargs)
    fresh = generations.run(root, name="fresh", **kwargs)
    _, ra = generations.get(root, resumed["generation"])
    _, fa = generations.get(root, fresh["generation"])
    a = torch.load(ra / "weights.pt", weights_only=True)
    b = torch.load(fa / "weights.pt", weights_only=True)
    assert all(torch.equal(a[k], b[k]) for k in a)
    fitted = json.loads((ra / "report.json").read_text())
    assert fitted["rows_seen"] == 2 * audit["population"]["train_usable"]
    assert fitted["exposure"]["draw_distribution"]['["@entity"]']["supported_values"] == 3
    replica = tmp_path / "source-replica"
    observations.replicate(source, replica, pin, role="train")
    assert (
        sparse.materialize(replica, pin, tmp_path / "copied-data", plan=cfg, chunk=16)["snapshot"]
        == built["snapshot"]
    )
    eval_pin, _ = data(tmp_path / "eval-source", eval_only=True)
    registered = evaluation.register(
        tmp_path / "protocols",
        ra,
        resumed["artifact"],
        start_s=3000,
        end_s=3100,
        pools=[f"test/market/3-{i}" for i in range(3)],
    )
    eval_data = sparse.materialize(
        tmp_path / "eval-source", eval_pin, store, plan=cfg, role="research_evaluation"
    )
    scored = evaluation.score(
        tmp_path / "protocols",
        registered["protocol"],
        store,
        eval_data["snapshot"],
        ra,
        tmp_path / "evaluation",
    )
    assert scored["supported_pools"] == 3
    assert len(scored["metrics"]) == 2
    # Censored administrative tails are retained, never exact-valued labels.
    assert any("censored" in k for k in scored["denominators"])
    x = next(batches(Snapshot(store, built["snapshot"]), split, "test", 16))[0]
    assert np.isfinite(Predictor(ra, resumed["artifact"]).predict(x)).all()


def test_partial_count_objective_trains_only_identified_positive_bounds(tmp_path):
    source = tmp_path / "sources"
    pin, _ = data(source, support_status="unknown")
    cfg = plan()
    cfg.update(context=8, max_history_ns=50 * NS)
    store = tmp_path / "experience"
    built = sparse.materialize(source, pin, store, plan=cfg, chunk=16)
    split = dict(train_start_s=0, train_end_s=1000, validation_end_s=2000, test_end_s=3000)
    config = dict(
        width=8,
        epochs=1,
        batch=16,
        seed=19,
        cpu_threads=1,
        checkpoint_every=2,
        normalization="constant_feature_scale_one_v1",
        objective="partial_count_lower_bound_pinball_v1",
    )
    interrupted = train(
        store, built["snapshot"], tmp_path / "partial-run", plan=split, config=config, stop_after=1
    )
    assert interrupted["status"] == "interrupted"
    report = train(store, built["snapshot"], tmp_path / "partial-run", plan=split, config=config)
    train(store, built["snapshot"], tmp_path / "fresh-partial-run", plan=split, config=config)
    resumed_weights = torch.load(tmp_path / "partial-run/artifact/weights.pt", weights_only=True)
    fresh_weights = torch.load(
        tmp_path / "fresh-partial-run/artifact/weights.pt", weights_only=True
    )
    assert all(torch.equal(resumed_weights[k], fresh_weights[k]) for k in fresh_weights)
    stats = report["statistics"]
    assert stats["target_transforms"][1] == "log1p_nonnegative"
    assert stats["baseline"][1] is None  # No complete process window; no fabricated baseline.
    assert stats["population"]["train_partial_count_usable"] > 0
    assert report["rows_seen"] == stats["population"]["train_objective_usable"]
    bounds = report["test"]["partial_count_bounds"][0]
    assert bounds["cells"] > 0
    assert report["test"]["model"][1]["n"] == 0
    eval_source = tmp_path / "evaluation-source"
    eval_pin, _ = data(eval_source, eval_only=True, support_status="unknown")
    eval_data = sparse.materialize(
        eval_source,
        eval_pin,
        store,
        plan=cfg,
        role="research_evaluation",
    )
    artifact = tmp_path / "partial-run/artifact"
    artifact_pin = file_hash(artifact / "manifest.json")
    registered = evaluation.register(
        tmp_path / "protocols",
        artifact,
        artifact_pin,
        start_s=3000,
        end_s=3100,
        pools=[f"test/market/3-{i}" for i in range(3)],
    )
    scored = evaluation.score(
        tmp_path / "protocols",
        registered["protocol"],
        store,
        eval_data["snapshot"],
        artifact,
        tmp_path / "partial-evaluation",
    )
    assert scored["metrics"][1]["n"] == 0
    assert scored["metrics"][1]["empirical_pinball"] is None
    x = next(batches(Snapshot(store, built["snapshot"]), split, "test", 16))[0]
    prediction = Predictor(artifact, artifact_pin).predict(x)
    assert np.isfinite(prediction).all()
    assert (prediction[:, 1, :] >= 0).all()
    assert (prediction[:, 1, 0] <= prediction[:, 1, 1]).all()
    assert (prediction[:, 1, 1] <= prediction[:, 1, 2]).all()


def test_qualification_resumes_conversion_without_rewriting_measurements(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "sparse_qualify", Path(__file__).parents[1] / "tools/sparse_qualify.py"
    )
    qualifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qualifier)
    projection = plan()
    projection.update(context=4, relative_features=[])
    projection["targets"] = [
        dict(name="count", kind="observed_process_count", horizon_ns=10 * NS, feature=None)
    ]
    job = dict(
        mode="synthetic",
        entities=9,
        events=8,
        projection=projection,
        split=dict(train_start_s=0, train_end_s=100000, validation_end_s=200000, test_end_s=300000),
        train=dict(
            width=8,
            epochs=1,
            batch=16,
            seed=19,
            cpu_threads=1,
            checkpoint_every=2,
            normalization="constant_feature_scale_one_v1",
        ),
    )
    original = generations.run

    def crash(*args, **kwargs):
        raise RuntimeError("injected after durable conversion")

    monkeypatch.setattr(generations, "run", crash)
    with pytest.raises(RuntimeError, match="injected"):
        qualifier.run(job, tmp_path)
    before = (tmp_path / "conversion.json").read_bytes()
    monkeypatch.setattr(generations, "run", original)
    first = qualifier.run(job, tmp_path)
    assert qualifier.run(job, tmp_path) == first
    assert (tmp_path / "conversion.json").read_bytes() == before
    with pytest.raises(ValueError, match="checkpoint_mismatch"):
        qualifier.checkpoint(tmp_path / "result.json", dict(first, source="tampered"))
