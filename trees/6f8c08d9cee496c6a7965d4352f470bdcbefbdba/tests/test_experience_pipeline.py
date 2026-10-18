import json

import pytest
from test_experience_schema import custom_samples

from memecoin_experience.composition import compose
from memecoin_experience.storage import Snapshot, build


def test_disjoint_composition_reuses_objects_and_rejects_overlap(tmp_path):
    schema, rows = custom_samples(60)
    store = tmp_path / "store"
    first = build(
        store,
        rows[:30],
        sources=[dict(synthetic=True, interval="first")],
        schema=schema,
        cutoff=10**18,
    )
    second = build(
        store,
        rows[30:],
        sources=[dict(synthetic=True, interval="second")],
        schema=schema,
        cutoff=10**18,
    )
    before = set((store / "objects").iterdir())
    result = compose(store, [first["snapshot"], second["snapshot"]])
    assert result["decisions"] == 60
    assert set((store / "objects").iterdir()) == before
    assert {p["pin"] for p in result["partitions"]} == {
        p["pin"] for r in (first, second) for p in r["partitions"]
    }
    snapshot = Snapshot(store, result["snapshot"])
    for i in range(len(snapshot.parts)):
        snapshot.partition(i)
    assert compose(store, [result["snapshot"], first["snapshot"]])["decisions"] == 60
    overlapping = build(
        store,
        rows[20:],
        sources=[dict(synthetic=True, interval="overlap")],
        schema=schema,
        cutoff=10**18,
    )
    with pytest.raises(ValueError, match="overlap"):
        compose(store, [first["snapshot"], overlapping["snapshot"]])


@pytest.mark.parametrize("with_diversity", [False, True])
def test_pipeline_recovers_and_repeats_from_sealed_stages(
    tmp_path, monkeypatch, capsys, with_diversity
):
    pytest.importorskip("torch")
    from curriculum.test_admission import capture

    from memecoin_continuous.store import connect, ingest
    from memecoin_experience import generations
    from memecoin_experience.__main__ import main
    from memecoin_experience.pipeline import run
    from memecoin_ml.data import sha
    from memecoin_registry.contracts import seal

    cap, birth = capture(tmp_path)
    payload = json.loads((cap / "response.bin").read_text())
    payload["data"]["attributes"]["ohlcv_list"] = [
        [300000 + i * 300, 1.0, 1.1, 0.9, 1.0 + (i % 30) * 0.001, 100.0] for i in range(400)
    ]
    (cap / "response.bin").write_text(json.dumps(payload))
    receipt = json.loads((cap / "receipt.json").read_text())
    receipt["response_sha256"] = sha(cap / "response.bin")
    (cap / "receipt.json").write_text(json.dumps(receipt))
    (cap / "manifest.json").unlink()
    seal(cap)
    index = tmp_path / "index.sqlite"
    with connect(index) as db:
        ingest(db, cap, birth["pool"])
    source = dict(
        role="train",
        cutoff_ns=10**18,
        start_s=300000,
        end_s=390000,
        selection="synthetic fixed enrollment",
        pools=[{k: birth[k] for k in ("pool", "base_mint", "quote_mint")}],
    )
    plan = dict(
        name="V1",
        owner="research",
        index=str(index),
        training_source=source,
        evaluation_source=dict(source, role="research_evaluation", start_s=390000, end_s=420000),
        representation=dict(context=8, horizons=[1, 3, 6]),
        split=dict(
            train_start_s=300000, train_end_s=330000, validation_end_s=360000, test_end_s=390000
        ),
        config=dict(width=8, epochs=1, batch=64),
    )
    if with_diversity:
        from test_population import config, definition, member

        from memecoin_population.catalog import publish

        facts = tmp_path / "facts"
        rows = [member(birth["pool"], end=10**18, known=1, venue="X")]
        pop = publish(facts, "population", rows, cutoff_ns=1, definition=definition())
        epop = publish(
            facts, "population", rows, cutoff_ns=1, definition=definition("research_evaluation")
        )
        cfg = config()
        plan["diversity"] = dict(population_store=str(facts), population=pop, config=cfg)
        keys = ("entity_prefix", "knowledge", "axes", "time_bucket_ns", "selection")
        plan["cohorts"] = dict(store=str(facts), population=epop, config={k: cfg[k] for k in keys})
    root = tmp_path / "pipeline"
    real = generations.run
    calls = []

    def fail(*args, **kwargs):
        calls.append(True)
        raise RuntimeError("synthetic crash after source and experience sealing")

    with monkeypatch.context() as patch:
        patch.setattr(generations, "run", fail)
        with pytest.raises(RuntimeError):
            run(root, plan)
    assert calls and (root / "stages/training_source.json").exists()
    assert not (root / "receipt.json").exists()
    assert generations.run is real
    result = run(root, plan)
    assert result["qualified_bars"] == 300
    assert result["persisted_examples"] == 293
    assert len(result["metrics"]) == 3
    if with_diversity:
        assert result["exposure"]["binding"]["audit"] == result["diversity"]
    assert result == run(root, plan)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    monkeypatch.setattr(
        "sys.argv", ["experience", "pipeline", "--root", str(root), "--plan", str(plan_path)]
    )
    main()
    assert json.loads(capsys.readouterr().out) == result
