#!/usr/bin/env python3
"""Reproducible bounded D44 engineering matrix. No network or D43 writes."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path

from memecoin_ml.data import Split, export_window, load_dataset, sha, synthetic, write
from memecoin_ml.models import KINDS, ModelConfig
from memecoin_ml.training import TrainConfig, source_identity, train


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--sealed-window", type=Path)
    args = parser.parse_args()
    root = args.output
    root.mkdir(parents=True, exist_ok=False)
    synthetic(root / "synthetic")
    splits = {"synthetic": Split(0, 60 * 10**9, 90 * 10**9, 124 * 10**9)}
    if args.sealed_window:
        export_window(args.sealed_window, root / "real", context=8, max_gap_ns=15 * 10**9)
        bundle = load_dataset(root / "real")
        times = sorted({sample["decision_ns"] for sample in bundle["samples"]})
        # Smoke-only 50/75% chronological cuts use timestamps, never label values.
        splits["real"] = Split(
            min(times),
            times[len(times) // 2],
            times[3 * len(times) // 4],
            max(sample["maturity_ns"] for sample in bundle["samples"]) + 1,
        )
    cases = [
        dict(
            dataset=dataset,
            split=asdict(split),
            training=asdict(TrainConfig(ModelConfig(kind), seed=seed, epochs=3)),
        )
        for dataset, split in splits.items()
        for kind in KINDS
        for seed in ((7, 19) if dataset == "synthetic" else (7,))
    ]
    write(
        root / "plan.json",
        dict(
            qualification="engineering_only",
            source=source_identity(),
            datasets={name: sha(root / name / "dataset.json") for name in splits},
            cases=cases,
            failure_budget="stop matrix after first failure",
        ),
    )
    reports = []
    for index, case in enumerate(cases):
        recipe = dict(case["training"])
        recipe["model"] = ModelConfig(**recipe["model"])
        try:
            report = train(
                root / case["dataset"],
                root / f"run-{index:02d}",
                Split(**case["split"]),
                TrainConfig(**recipe),
            )
        except Exception as exc:
            write(
                root / "failure.json",
                dict(case=index, category="ml_smoke_failure", exception=type(exc).__name__),
            )
            print(
                f"STOP case={index} category=ml_smoke_failure exception={type(exc).__name__}",
                flush=True,
            )
            raise
        reports.append(
            dict(case=index, dataset=case["dataset"], training=case["training"], report=report)
        )
        print(
            f"OK case={index} dataset={case['dataset']} "
            f"model={recipe['model'].kind} seed={recipe['seed']}",
            flush=True,
        )
    write(root / "summary.json", dict(qualification="engineering_only", reports=reports))


if __name__ == "__main__":
    main()
