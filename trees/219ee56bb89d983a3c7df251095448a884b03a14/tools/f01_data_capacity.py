"""Bounded synthetic-shape capacity check, never coverage, research or power evidence."""

from __future__ import annotations

import argparse
import copy
import json
import resource
import subprocess
import sys
import time
from pathlib import Path

from memecoin_ml.data import load_dataset, save_dataset, sha, write
from memecoin_research.framework import DAY
from memecoin_runner.contracts import partition


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    if args.worker:
        bundle = load_dataset(args.dataset)
        anchor = min(s["decision_ns"] for s in bundle["samples"])
        bounds = dict(
            start=anchor,
            train_end=anchor + 28 * DAY,
            validation_end=anchor + 35 * DAY,
            test_end=anchor + 42 * DAY,
            embargo=30 * 10**9,
        )
        parts = partition(bundle, bounds)
        result = dict(
            qualification="synthetic_shape_capacity_only",
            windows=224,
            decisions=len(bundle["samples"]),
            scenarios=sum(len(s["targets"]) for s in bundle["samples"]),
            dataset_bytes=(args.dataset / "dataset.json").stat().st_size,
            elapsed_load_validate_partition_seconds=time.monotonic() - started,
            peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            * (1 if sys.platform == "darwin" else 1024),
            python=sys.version.split()[0],
            parts={k: len(v) for k, v in parts.items()},
            training_runs=0,
            does_not_measure_fit_budget=True,
            does_not_establish_corpus_sufficiency=True,
        )
        write(args.output, result)
        print(json.dumps(result, sort_keys=True))
        return
    args.output.mkdir(parents=True, exist_ok=False)
    template = load_dataset(args.dataset)
    if len(template["samples"]) > 180 or (args.dataset / "dataset.json").stat().st_size > 2_000_000:
        raise ValueError("bounded_template_required")
    # Every replicated row is an explicit shape fixture, with independent Python objects.
    frames = {}
    samples = []
    old = min(f["decision_ns"] for f in template["frames"].values())
    anchor = 1791331200 * 10**9
    for index in range(224):
        prefix = f"shape-fixture:{index}:"
        offset = anchor + (index // 4) * DAY + (1800 + (index % 4) * 21600) * 10**9 - old
        for key, f in template["frames"].items():
            frames[prefix + key] = {
                **copy.deepcopy(f),
                "group": prefix + f["group"],
                "decision_ns": f["decision_ns"] + offset,
                "available_ns": f["available_ns"] + offset,
            }
        for s in template["samples"]:
            sample = copy.deepcopy(s)
            sample.update(
                id=prefix + s["id"],
                group=prefix + s["group"],
                window_index=index,
                decision_ns=s["decision_ns"] + offset,
                maturity_ns=s["maturity_ns"] + offset,
                history=[prefix + k for k in s["history"]],
            )
            for target in sample["targets"]:
                target["available_ns"] += offset
                target["reason"] = "synthetic_shape_fixture_not_prospective"
            samples.append(sample)
    bundle = dict(
        schema=template["schema"],
        qualification="engineering_only",
        feature_names=template["feature_names"],
        history_policy=template["history_policy"],
        frames=frames,
        samples=samples,
        source=dict(
            kind="synthetic_shape_fixture",
            template_sha256=sha(args.dataset / "dataset.json"),
            copies=224,
            not_market_evidence=True,
        ),
    )
    save_dataset(args.output / "fixture", bundle)
    command = [
        sys.executable,
        __file__,
        str(args.output / "fixture"),
        str(args.output / "report.json"),
        "--worker",
    ]
    with (args.output / "worker.log").open("xb") as stream:
        subprocess.run(command, stdout=stream, stderr=stream, timeout=120, check=True)
    print((args.output / "report.json").read_text())


if __name__ == "__main__":
    main()
