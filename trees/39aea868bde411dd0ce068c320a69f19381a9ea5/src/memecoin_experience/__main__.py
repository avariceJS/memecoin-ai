"""Explicit opt-in research jobs: python -m memecoin_experience --help."""

import argparse
import json
from collections import Counter
from pathlib import Path

from .sources import inputs
from .storage import Snapshot, build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in (
        "publish-observations",
        "build-sparse",
        "observations-from-continuous",
        "replicate-observations",
    ):
        command = commands.add_parser(action)
        command.add_argument("--job", required=True, type=Path)
    exporter = commands.add_parser("export-continuous")
    exporter.add_argument("--index", required=True, type=Path)
    exporter.add_argument("--plan", required=True, type=Path)
    exporter.add_argument("--output", required=True, type=Path)
    builder = commands.add_parser("build")
    builder.add_argument("--plan", required=True, type=Path)
    builder.add_argument("--store", required=True, type=Path)
    admission = commands.add_parser("admit")
    admission.add_argument("--store", required=True, type=Path)
    admission.add_argument("--snapshot", required=True)
    admission.add_argument("--plan", required=True, type=Path)
    inspector = commands.add_parser("inspect")
    inspector.add_argument("--store", required=True, type=Path)
    inspector.add_argument("--snapshot", required=True)
    trainer = commands.add_parser("train")
    trainer.add_argument("--store", required=True, type=Path)
    trainer.add_argument("--snapshot", required=True)
    trainer.add_argument("--plan", required=True, type=Path)
    trainer.add_argument("--output", required=True, type=Path)
    for name in ("pipeline", "generation", "register-evaluation"):
        command = commands.add_parser(name)
        command.add_argument("--root", required=True, type=Path)
        command.add_argument("--plan", required=True, type=Path)
    evaluator = commands.add_parser("evaluate")
    evaluator.add_argument("--plan", required=True, type=Path)
    evaluator.add_argument("--output", required=True, type=Path)
    transfer = commands.add_parser("replicate")
    transfer.add_argument("--source", required=True, type=Path)
    transfer.add_argument("--target", required=True, type=Path)
    transfer.add_argument("--snapshot", required=True)
    composer = commands.add_parser("compose")
    composer.add_argument("--store", required=True, type=Path)
    composer.add_argument("--snapshots", nargs="+", required=True)
    predictor = commands.add_parser("predict")
    predictor.add_argument("--artifact", required=True, type=Path)
    predictor.add_argument("--pin", required=True)
    predictor.add_argument("--frames", required=True, type=Path)
    args = parser.parse_args()
    if args.action in {
        "publish-observations",
        "build-sparse",
        "observations-from-continuous",
        "replicate-observations",
    }:
        from contextlib import ExitStack

        from . import observation_adapters, observations, sparse

        job = json.loads(args.job.read_text())
        with ExitStack() as opened:
            if args.action == "publish-observations":
                stream = opened.enter_context(Path(job.pop("rows_path")).open())
                result = dict(
                    source=observations.publish(rows=(json.loads(line) for line in stream), **job)
                )
            elif args.action == "build-sparse":
                built = sparse.materialize(**job)
                result = {k: built[k] for k in ("snapshot", "decisions", "target_statuses")}
            elif args.action == "replicate-observations":
                result = observations.replicate(**job)
            else:
                if "lifecycle_path" in job:
                    stream = opened.enter_context(Path(job.pop("lifecycle_path")).open())
                    job["lifecycle"] = (json.loads(line) for line in stream)
                result = dict(source=observation_adapters.from_continuous(**job))
    elif args.action == "admit":
        from .admission import create

        result = create(args.store, args.snapshot, **json.loads(args.plan.read_text()))
    elif args.action == "pipeline":
        from .pipeline import run

        result = run(args.root, json.loads(args.plan.read_text()))
    elif args.action == "generation":
        from .generations import run

        result = run(args.root, **json.loads(args.plan.read_text()))
    elif args.action == "register-evaluation":
        from .evaluation import register

        result = register(args.root, **json.loads(args.plan.read_text()))
    elif args.action == "evaluate":
        from .evaluation import score

        result = score(output=args.output, **json.loads(args.plan.read_text()))
    elif args.action == "replicate":
        from .transfer import replicate

        result = replicate(args.source, args.target, args.snapshot)
    elif args.action == "compose":
        from .composition import compose

        report = compose(args.store, args.snapshots)
        result = dict(snapshot=report["snapshot"], decisions=report["decisions"])
    elif args.action == "predict":
        from .predict import Predictor

        model = Predictor(args.artifact, args.pin)
        result = dict(
            artifact=args.pin,
            predictions=model.predict(json.loads(args.frames.read_text())).tolist(),
        )
    elif args.action == "export-continuous":
        from .continuous import export

        result = export(args.index, args.output, json.loads(args.plan.read_text()))
    elif args.action == "build":
        plan = json.loads(args.plan.read_text())
        required = {"sources", "cutoff_ns", "context", "chunk"}
        if not required <= set(plan) or set(plan) - required - {
            "representation",
            "role",
            "origin_interval_s",
        }:
            raise ValueError("experience_build_plan_schema")
        counters = Counter()
        role = plan.get("role", "train")
        rows = inputs(plan["sources"], plan["cutoff_ns"], counters, role=role)
        schema = None
        if "representation" in plan:
            from .representations import ohlcv_samples, ohlcv_schema

            representation = plan["representation"]
            if (
                set(representation) != {"name", "horizons"}
                or representation["name"] != "ohlcv-sequence-v2"
            ):
                raise ValueError("experience_representation")
            schema = ohlcv_schema(plan["context"], tuple(representation["horizons"]))
            rows = ohlcv_samples(rows, plan["cutoff_ns"], schema)
        selection = {}
        if "origin_interval_s" in plan:
            if schema is None:
                raise ValueError("experience_interval_requires_v2")
            interval = plan["origin_interval_s"]
            if (
                len(interval) != 2
                or any(type(t) is not int for t in interval)
                or not 0 <= interval[0] < interval[1]
            ):
                raise ValueError("experience_origin_interval")
            selection = dict(origin_interval_s=interval)
            rows = (
                r for r in rows if interval[0] * 10**9 <= r["decision_ns"] < interval[1] * 10**9
            )
        result = build(
            args.store,
            rows,
            sources=plan["sources"],
            cutoff=plan["cutoff_ns"],
            context=plan["context"],
            chunk=plan["chunk"],
            source_counts=counters,
            schema=schema,
            role=role,
            selection=selection,
        )
        result = {
            k: v for k, v in result.items() if k not in {"partitions", "partition_pages", "binding"}
        }
    elif args.action == "inspect":
        snapshot = Snapshot(args.store, args.snapshot)
        for i in range(len(snapshot.parts)):
            snapshot.partition(i)
        result = dict(
            snapshot=args.snapshot,
            decisions=snapshot.manifest["decisions"],
            partitions=len(snapshot.parts),
            verified=True,
        )
    else:
        from .learning import train

        plan = json.loads(args.plan.read_text())
        if not {"split", "config"} <= set(plan) or set(plan) - {
            "split",
            "config",
            "parent",
            "diversity",
        }:
            raise ValueError("experience_train_plan_schema")
        report = train(
            args.store,
            args.snapshot,
            args.output,
            plan=plan["split"],
            config=plan["config"],
            parent=plan.get("parent"),
            diversity=plan.get("diversity"),
        )
        result = {k: report[k] for k in ("status", "steps", "rows_seen", "parameters")}
        result["artifact"] = str(args.output / "artifact")
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
