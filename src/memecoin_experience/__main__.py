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
    builder = commands.add_parser("build")
    builder.add_argument("--plan", required=True, type=Path)
    builder.add_argument("--store", required=True, type=Path)
    inspector = commands.add_parser("inspect")
    inspector.add_argument("--store", required=True, type=Path)
    inspector.add_argument("--snapshot", required=True)
    trainer = commands.add_parser("train")
    trainer.add_argument("--store", required=True, type=Path)
    trainer.add_argument("--snapshot", required=True)
    trainer.add_argument("--plan", required=True, type=Path)
    trainer.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "build":
        plan = json.loads(args.plan.read_text())
        if set(plan) != {"sources", "cutoff_ns", "context", "chunk"}:
            raise ValueError("experience_build_plan_schema")
        counters = Counter()
        result = build(
            args.store,
            inputs(plan["sources"], plan["cutoff_ns"], counters),
            sources=plan["sources"],
            cutoff=plan["cutoff_ns"],
            context=plan["context"],
            chunk=plan["chunk"],
            source_counts=counters,
        )
        result = {k: v for k, v in result.items() if k not in {"partitions", "binding"}}
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
        if set(plan) != {"split", "config"}:
            raise ValueError("experience_train_plan_schema")
        report = train(
            args.store, args.snapshot, args.output, plan=plan["split"], config=plan["config"]
        )
        result = {k: report[k] for k in ("status", "steps", "rows_seen", "parameters")}
        result["artifact"] = str(args.output / "artifact")
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
