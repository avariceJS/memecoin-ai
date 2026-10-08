import json

import pytest

np = pytest.importorskip("numpy")
torch = pytest.importorskip("torch")

from test_experience_learning import CONFIG, PLAN, dataset  # noqa: E402
from test_population import config, definition, member  # noqa: E402

from memecoin_experience.learning import train  # noqa: E402
from memecoin_experience.predict import Predictor  # noqa: E402
from memecoin_experience.storage import file_hash  # noqa: E402
from memecoin_population import catalog, diversity  # noqa: E402


def audit(tmp_path, store, pin, snapshot, **changes):
    name = snapshot.partition(0)[1][0]["pool"]
    pop = catalog.publish(
        tmp_path / "facts",
        "population",
        [member(name, end=10**18, known=0, venue="synthetic")],
        cutoff_ns=snapshot.manifest["binding"]["cutoff_ns"],
        definition=definition(),
    )
    path = tmp_path / "audit"
    cfg = config()
    cfg.update(changes)
    diversity.audit(store, pin, tmp_path / "facts", pop, path, split=PLAN, config=cfg)
    return dict(path=str(path), pin=file_hash(path / "manifest.json"))


def test_exposure_exact_replay_portable_model_and_objective_mass(tmp_path, monkeypatch):
    store, pin, snapshot = dataset(tmp_path)
    ref = audit(tmp_path, store, pin, snapshot)
    cfg = dict(CONFIG, checkpoint_every=2)
    report = train(store, pin, tmp_path / "full", plan=PLAN, config=cfg, diversity=ref)
    original = torch.optim.AdamW.step
    calls = 0

    def crash(optimizer, *args, **kwargs):
        nonlocal calls
        result = original(optimizer, *args, **kwargs)
        calls += 1
        if calls == 4:
            raise RuntimeError("interrupted optimizer update")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(torch.optim.AdamW, "step", crash)
        with pytest.raises(RuntimeError):
            train(store, pin, tmp_path / "resumed", plan=PLAN, config=cfg, diversity=ref)
    assert list((tmp_path / "resumed/exposure").glob("*/*000003.json"))
    actual = train(store, pin, tmp_path / "resumed", plan=PLAN, config=cfg, diversity=ref)
    assert actual == report
    a = Predictor(tmp_path / "full/artifact", file_hash(tmp_path / "full/artifact/manifest.json"))
    b = Predictor(
        tmp_path / "resumed/artifact", file_hash(tmp_path / "resumed/artifact/manifest.json")
    )
    assert all(torch.equal(v, b.model.state_dict()[k]) for k, v in a.model.state_dict().items())
    summary = report["exposure"]
    assert summary["draw_distribution"]['["@entity"]']["total"] == report["rows_seen"]
    assert summary["objective_distribution"]['["@entity"]']["total"] == pytest.approx(
        report["steps"]
    )
    assert len(list((tmp_path / "resumed/exposure").glob("*/*.json"))) == report["steps"]


def test_gate_and_corrupt_checkpoint_exposure(tmp_path):
    store, pin, snapshot = dataset(tmp_path)
    ref = audit(tmp_path, store, pin, snapshot, max_entity_share=0.5)
    with pytest.raises(ValueError, match="training_gate"):
        train(store, pin, tmp_path / "blocked", plan=PLAN, config=CONFIG, diversity=ref)
    assert not (tmp_path / "blocked/checkpoint.pt").exists()
    other = tmp_path / "allowed"
    other.mkdir()
    ref = audit(other, store, pin, snapshot)
    run = tmp_path / "run"
    train(store, pin, run, plan=PLAN, config=CONFIG, diversity=ref, stop_after=2)
    step = next((run / "exposure").glob("*/*000001.json"))
    value = json.loads(step.read_text())
    value["rows"] += 1
    step.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="exposure_chain|checkpoint_exposure"):
        train(store, pin, run, plan=PLAN, config=CONFIG, diversity=ref)


def test_generation_binds_diversity_and_population_evaluation(tmp_path):
    from test_experience_generations import dataset as generic_dataset

    from memecoin_experience import evaluation, generations
    from memecoin_experience.storage import Snapshot

    store, pin = generic_dataset(tmp_path)
    ref = audit(tmp_path, store, pin, Snapshot(store, pin))
    done = generations.run(
        tmp_path / "generations",
        name="V1",
        owner="synthetic",
        store=store,
        snapshot=pin,
        split=PLAN,
        config=CONFIG,
        diversity=ref,
    )
    record, artifact = generations.get(tmp_path / "generations", done["generation"])
    assert record["declaration"]["diversity"] == ref["pin"]
    facts = tmp_path / "eval-facts"
    pop = catalog.publish(
        facts,
        "population",
        [
            member("synthetic", end=10**18, known=1, venue="X"),
            member("missing", end=10**18, known=1, venue="Y"),
        ],
        cutoff_ns=1,
        definition=definition("research_evaluation"),
    )
    cfg = config()
    cfg = {k: cfg[k] for k in ("entity_prefix", "knowledge", "axes", "time_bucket_ns", "selection")}
    protocols = tmp_path / "protocols"
    protocol = evaluation.register(
        protocols,
        artifact,
        done["artifact"],
        start_s=100000,
        end_s=200000,
        cohorts=dict(store=facts, population=pop, config=cfg),
    )
    eval_store, eval_pin = generic_dataset(tmp_path / "eval", evaluation_only=True, start=100000)
    out = tmp_path / "scores"
    result = evaluation.score(
        protocols, protocol["protocol"], eval_store, eval_pin, artifact, out, population_store=facts
    )
    assert result["enrollment"] == 2 and result["supported_pools"] == 1
    assert result["metrics"][0]["n"] == 310
    rows = [
        json.loads(line) for line in (out / "population-support.jsonl").read_text().splitlines()
    ]
    assert next(r for r in rows if r["entity"].endswith("missing"))["usable"] == 0
    strata = [json.loads(line) for line in (out / "strata.jsonl").read_text().splitlines()]
    assert any("venue" in row["stratum"] for row in strata)
    with pytest.raises(ValueError, match="population_transport"):
        evaluation.score(
            protocols, protocol["protocol"], eval_store, eval_pin, artifact, tmp_path / "bad"
        )
