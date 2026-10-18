"""Bounded D47 engineering evidence, no research admission or protected paths."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from memecoin_ml.data import read, sha, synthetic
from memecoin_registry.contracts import digest, inventory
from memecoin_runner.contracts import plan
from memecoin_runner.ledger import publish
from memecoin_runner.runner import run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--engineering-dataset", type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    protected = (Path("work/calendar-execution-01").resolve(),)
    denied: list[str] = []

    def guard(event: str, values: tuple[Any, ...]) -> None:
        if event == "open" and isinstance(values[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(values[0])).resolve()
            if any(path.is_relative_to(p) for p in protected):
                denied.append("protected_D43_open")
                raise PermissionError("protected_D43_open")

    sys.addaudithook(guard)
    spec = read(Path("configs/research-f01-v1.json"))
    if args.engineering_dataset:
        bundle = read(args.engineering_dataset)
        source = dict(
            kind="sealed_D45_development_engineering_only",
            path=str(args.engineering_dataset),
            sha256=sha(args.engineering_dataset),
        )
    else:
        synthetic(root / "synthetic-source")
        bundle = read(root / "synthetic-source/dataset.json")
        for s in bundle["samples"]:
            s["pool_id"] = "synthetic"
        source = dict(kind="synthetic_fixture")
    dataset = root / "dataset.json"
    publish(dataset, bundle)
    times = sorted({s["decision_ns"] for s in bundle["samples"]})
    # Explicit engineering cuts, never F01 folds or protected market holdout.
    bounds = dict(
        start=times[0],
        train_end=times[len(times) // 2],
        validation_end=times[len(times) * 3 // 4],
        test_end=times[-1] + 1,
        embargo=0,
    )
    binding = plan(
        spec, dataset, mode="engineering", engineering_bounds=bounds, engineering_epochs=2
    )
    publish(root / "plan.json", binding)
    result = run(spec, binding, dataset, root / "run")
    outer = read(root / "run/fold0-outer.json")
    from memecoin_runner.contracts import partition
    from memecoin_runner.scoring import bootstrap, score

    parts = partition(bundle, bounds)
    by_id = {s["id"]: s for s in bundle["samples"]}
    test = [by_id[k] for k in parts["test"]]
    reproduced = score(
        test,
        outer["predictions"],
        pools=sorted({s["pool_id"] for s in test}),
        scale=outer["scores"]["scale"],
        fold="fold0",
    )
    assert reproduced == outer["scores"]
    assert bootstrap(spec, reproduced) == outer["uncertainty"]
    evidence = dict(
        stage="D47",
        qualification="engineering_only",
        source=source,
        code=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        plan_sha256=digest(binding),
        result=result,
        partition_decisions={k: len(v) for k, v in parts.items()},
        exact_score_reproduction=True,
        D43_parent_protected_open_attempts=len(denied),
        worker_inputs="pinned engineering dataset only; workers do not access registry/D43",
        real_F01_fits=0,
        holdout_releases=0,
        outcome="No edge/model-quality inference from engineering corpus",
        artifacts=inventory(root / "run"),
    )
    publish(root / "evidence.json", evidence)
    print(
        json.dumps(
            {
                k: evidence[k]
                for k in (
                    "qualification",
                    "partition_decisions",
                    "exact_score_reproduction",
                    "real_F01_fits",
                    "holdout_releases",
                )
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
