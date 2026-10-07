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
    if set(plan) != required:
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
                pools=[p["pool"] for p in plan["evaluation_source"]["pools"]],
                scope="retrospective",
            )
            protocol_pin = registered["protocol"]
            atomic_json(protocol_step, dict(protocol=protocol_pin))
        protocol = json.loads((root / "protocols" / (protocol_pin + ".json")).read_text())
        expected = dict(
            candidate=record["artifact"],
            start_s=plan["evaluation_source"]["start_s"],
            end_s=plan["evaluation_source"]["end_s"],
            pools=sorted(p["pool"] for p in plan["evaluation_source"]["pools"]),
            scope="retrospective",
            schema=schema,
        )
        if digest(protocol) != protocol_pin or any(protocol[k] != v for k, v in expected.items()):
            raise ValueError("experience_pipeline_protocol_binding")
        eval_pin, evaluation_source = dataset("evaluation_source")
        evaluated = score(
            root / "protocols", protocol_pin, store, eval_pin, artifact, root / "evaluation"
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
        atomic_json(root / "receipt.json", receipt)
        return receipt
