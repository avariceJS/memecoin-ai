"""Resumable research orchestration over pinned stage outputs. Never starts acquisition/AUTO."""

import json
from collections import Counter

from .continuous import export, source_plan
from .representations import ohlcv_samples, ohlcv_schema
from .sources import inputs
from .storage import (
    Snapshot,
    atomic_json,
    build,
    code_identity,
    digest,
    file_hash,
    lock,
    logical_sources,
    safe_path,
    verify_files,
)


# Accepted-base equivalence review, attempt 3. Scope: the controller-authored
# accepted-base context for refs/heads/brain/development at projected SHA
# 1973022ee1ced91550dca3e9c82399cc67ba6c22, binding
# 4fd10e247224c042972599c2d1bbb4d749994b72278475ea1cbe8b2fc0d410e7;
# context note 00ac4377-4f8d-4180-a8fc-2e7e4e5ea301 (context SHA
# 302a94ec35310e0c6d26766e17bb5ed001368356c486ca238178e1490fcca7b2).
# Reviewed only its exact admitted paths; the rejected retry seed was excluded.
# In accepted-base pipeline.py, run() at line 23 checks plan roles, evaluation
# boundary and schema, then dataset() at line 54 exports/reads/builds inputs;
# the remainder fits a generation and scores evaluation. Accepted-base test
# lines 10-47 cover composition reuse/overlap, and lines 50 onward cover full
# run recovery. Neither path supplies a payload-free plan preflight. Reuse the
# pure source_plan/schema validation, but retain this separate non-executing
# preflight; do not call run(). This is bounded to the two paths and makes no
# claim about equivalents elsewhere in the repository.
def preflight(plan, input_artifacts):
    """Describe a research plan and supplied immutable inputs without opening payloads."""
    required = {
        "name",
        "owner",
        "index",
        "training_source",
        "evaluation_source",
        "representation",
        "split",
        "config",
    }
    if not required <= set(plan) or set(plan) - required - {"diversity", "cohorts"}:
        raise ValueError("experience_pipeline_plan")

    sources = {}
    for name, role in (("training_source", "train"), ("evaluation_source", "research_evaluation")):
        source = plan[name]
        normalized = source_plan(source)
        if normalized["role"] != role:
            raise ValueError("experience_pipeline_source_role")
        sources[name] = normalized
    if set(input_artifacts) != set(sources) or any(
        not isinstance(identity, str) or not identity.strip()
        for identity in input_artifacts.values()
    ):
        raise ValueError("experience_pipeline_input_artifacts")
    if plan["evaluation_source"]["start_s"] < plan["split"]["test_end_s"]:
        raise ValueError("experience_evaluation_seen_interval")

    representation = plan["representation"]
    if set(representation) != {"context", "horizons"}:
        raise ValueError("experience_pipeline_representation")
    schema = ohlcv_schema(representation["context"], tuple(representation["horizons"]))

    return {
        "version": "brain-research-preflight-v1",
        "execution": {"dry_run": True, "payloads_opened": False, "models_constructed": False, "training_started": False},
        "roles": {name: source["role"] for name, source in sources.items()},
        # Declaration hashes describe the normalized source declarations only.
        # Input artifact identities are supplied by the caller; preflight does
        # not resolve paths or open payloads to discover or verify them.
        "declaration_hashes": {name: digest(source) for name, source in sources.items()},
        "input_artifact_pins": dict(input_artifacts),
        "dimensions": {
            "context": representation["context"],
            "horizons": list(representation["horizons"]),
            "schema": schema,
        },
        "budget": {
            "training_interval_s": [plan["training_source"]["start_s"], plan["training_source"]["end_s"]],
            "evaluation_interval_s": [plan["evaluation_source"]["start_s"], plan["evaluation_source"]["end_s"]],
            "model_config": dict(plan["config"]),
        },
    }


def run(root, plan):
    required = {
        "name",
        "owner",
        "index",
        "training_source",
        "evaluation_source",
        "representation",
        "split",
        "config",
    }
    if not required <= set(plan) or set(plan) - required - {"diversity", "cohorts"}:
        raise ValueError("experience_pipeline_plan")
    for name, role in (("training_source", "train"), ("evaluation_source", "research_evaluation")):
        if source_plan(plan[name])["role"] != role:
            raise ValueError("experience_pipeline_source_role")
    if plan["evaluation_source"]["start_s"] < plan["split"]["test_end_s"]:
        raise ValueError("experience_evaluation_seen_interval")
    representation = plan["representation"]
    if set(representation) != {"context", "horizons"}:
        raise ValueError("experience_pipeline_representation")
    schema = ohlcv_schema(representation["context"], tuple(representation["horizons"]))
    root = safe_path(root)
    root.mkdir(parents=True, exist_ok=True)
    declaration = dict(plan=plan, code=code_identity())
    with lock(root / ".pipeline.lock"):
        atomic_json(root / "declaration.json", declaration)
        stage = root / "stages"
        stage.mkdir(exist_ok=True)
        store = root / "experience"

        def dataset(name):
            source = plan[name]
            path = root / name
            if not path.exists():
                export(plan["index"], path, source)
            pin = file_hash(path / "meta/manifest.json")
            verify_files(path / "meta", pin)
            report = json.loads((path / "meta/source.json").read_text())
            if report["plan"] != source:
                raise ValueError("experience_pipeline_source_binding")
            reference = dict(kind="continuous", path=str(path), pin=pin)
            step = stage / (name + ".json")
            if step.exists():
                snapshot_pin = json.loads(step.read_text())["snapshot"]
                snapshot = Snapshot(store, snapshot_pin)
                if (
                    snapshot.manifest["binding"]["sources"] != logical_sources([reference])
                    or snapshot.schema != schema
                    or snapshot.manifest["binding"].get("role") != source["role"]
                    or snapshot.manifest["binding"]["cutoff_ns"] != source["cutoff_ns"]
                    or snapshot.manifest["binding"].get("selection") != {}
                    or snapshot.manifest["binding"]["code"] != declaration["code"]
                ):
                    raise ValueError("experience_pipeline_snapshot_binding")
            else:
                counters = Counter()
                rows = inputs([reference], source["cutoff_ns"], counters, role=source["role"])
                samples = ohlcv_samples(rows, source["cutoff_ns"], schema)
                result = build(
                    store,
                    samples,
                    schema=schema,
                    sources=[reference],
                    cutoff=source["cutoff_ns"],
                    source_counts=counters,
                    role=source["role"],
                )
                snapshot_pin = result["snapshot"]
                atomic_json(step, dict(snapshot=snapshot_pin, source=pin))
            return snapshot_pin, report

        train_pin, training_source = dataset("training_source")
        diversity_ref = None
        if "diversity" in plan:
            from memecoin_population.diversity import audit

            audit(
                store,
                train_pin,
                output=root / "diversity",
                split=plan["split"],
                **plan["diversity"],
            )
            diversity_ref = dict(
                path=str(root / "diversity"), pin=file_hash(root / "diversity/manifest.json")
            )
        from .generations import get
        from .generations import run as generation

        fitted = generation(
            root / "models",
            name=plan["name"],
            owner=plan["owner"],
            store=store,
            snapshot=train_pin,
            split=plan["split"],
            config=plan["config"],
            diversity=diversity_ref,
        )
        record, artifact = get(root / "models", fitted["generation"])
        from .evaluation import register, score

        protocol_step = stage / "protocol.json"
        if protocol_step.exists():
            protocol_pin = json.loads(protocol_step.read_text())["protocol"]
        else:
            registered = register(
                root / "protocols",
                artifact,
                record["artifact"],
                start_s=plan["evaluation_source"]["start_s"],
                end_s=plan["evaluation_source"]["end_s"],
                pools=None
                if "cohorts" in plan
                else [p["pool"] for p in plan["evaluation_source"]["pools"]],
                cohorts=plan.get("cohorts"),
                scope="retrospective",
            )
            protocol_pin = registered["protocol"]
            atomic_json(protocol_step, dict(protocol=protocol_pin))
        protocol = json.loads((root / "protocols" / (protocol_pin + ".json")).read_text())
        expected = dict(
            candidate=record["artifact"],
            start_s=plan["evaluation_source"]["start_s"],
            end_s=plan["evaluation_source"]["end_s"],
            pools=[]
            if "cohorts" in plan
            else sorted(p["pool"] for p in plan["evaluation_source"]["pools"]),
            scope="retrospective",
            schema=schema,
        )
        if "cohorts" in plan:
            from memecoin_population.evaluation import contract

            c = plan["cohorts"]
            expected["cohorts"] = contract(
                c["store"], c["population"], c["config"], protocol["registered_ns"]
            )
        if digest(protocol) != protocol_pin or any(protocol[k] != v for k, v in expected.items()):
            raise ValueError("experience_pipeline_protocol_binding")
        eval_pin, evaluation_source = dataset("evaluation_source")
        evaluated = score(
            root / "protocols",
            protocol_pin,
            store,
            eval_pin,
            artifact,
            root / "evaluation",
            population_store=plan["cohorts"]["store"] if "cohorts" in plan else None,
        )
        receipt = dict(
            version="brain-research-pipeline-v1",
            declaration=digest(declaration),
            training_source=training_source,
            evaluation_source=evaluation_source,
            train_snapshot=train_pin,
            evaluation_snapshot=eval_pin,
            generation=fitted["generation"],
            artifact=record["artifact"],
            protocol=protocol_pin,
            evaluation=file_hash(root / "evaluation/manifest.json"),
            qualified_bars=training_source["bars"],
            persisted_examples=Snapshot(store, train_pin).manifest["decisions"],
            rows_seen=record["rows_seen"],
            metrics=evaluated["metrics"],
            production_eligible=False,
        )
        if diversity_ref:
            receipt["diversity"] = diversity_ref["pin"]
            receipt["exposure"] = json.loads((artifact / "report.json").read_text())["exposure"]
        atomic_json(root / "receipt.json", receipt)
        return receipt
