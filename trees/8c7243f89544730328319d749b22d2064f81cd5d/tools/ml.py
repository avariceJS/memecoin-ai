#!/usr/bin/env python3
"""Optional ML commands; runtime collectors never import this module."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from memecoin_ml.data import Split, export_window, read, synthetic


def main() -> None:
    parser = argparse.ArgumentParser(description="D44 engineering-only ML foundation")
    sub = parser.add_subparsers(dest="command", required=True)
    fixture = sub.add_parser("fixture")
    fixture.add_argument("output", type=Path)
    export = sub.add_parser("export")
    export.add_argument("window", type=Path)
    export.add_argument("output", type=Path)
    export.add_argument("--context", type=int, default=8)
    export.add_argument("--max-gap-seconds", type=float, required=True)
    fit = sub.add_parser("train")
    fit.add_argument("dataset", type=Path)
    fit.add_argument("output", type=Path)
    fit.add_argument("--protocol", type=Path, required=True)
    fit.add_argument("--resume", type=Path)
    bench = sub.add_parser("benchmark")
    bench.add_argument("output", type=Path)
    bench.add_argument("--iterations", type=int, default=10)
    args = parser.parse_args()
    if args.command == "fixture":
        synthetic(args.output)
    elif args.command == "export":
        export_window(
            args.window,
            args.output,
            context=args.context,
            max_gap_ns=int(args.max_gap_seconds * 1e9),
        )
    elif args.command == "train":
        from memecoin_ml.models import ModelConfig
        from memecoin_ml.training import TrainConfig, train

        protocol = read(args.protocol)
        config = protocol["training"]
        config["model"] = ModelConfig(**config["model"])
        result = train(
            args.dataset,
            args.output,
            Split(**protocol["split"]),
            TrainConfig(**config),
            resume=args.resume,
        )
        print(
            json.dumps(
                dict(
                    qualification=result["qualification"],
                    parameters=result["parameters"],
                    test_decisions=result["test"]["decisions"],
                )
            )
        )
    else:
        from memecoin_ml.benchmark import benchmark

        result = benchmark(args.output, iterations=args.iterations)
        print(
            json.dumps(
                dict(
                    cases=len(result["results"]),
                    failures=sum(r["status"] != "ok" for r in result["results"]),
                )
            )
        )


if __name__ == "__main__":
    main()
