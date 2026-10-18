#!/usr/bin/env python3
"""D46 frozen protocol/planning/readiness; no trainer or holdout release command."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from memecoin_ml.data import read, write
from memecoin_research.framework import (
    assess_registry,
    freeze,
    load_protocol,
    materialize,
    select_validation,
    trial_plan,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("spec", type=Path)
    f.add_argument("output", type=Path)
    for name in ("plan", "materialize", "assess", "select"):
        p = sub.add_parser(name)
        p.add_argument("protocol", type=Path)
        p.add_argument("output", type=Path)
        if name == "materialize":
            p.add_argument("--anchor-ns", type=int, required=True)
        if name == "assess":
            p.add_argument("--registry", type=Path, required=True)
            p.add_argument("--revision", type=int, required=True)
        if name == "select":
            p.add_argument("--phase", required=True)
            p.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "freeze":
        result = freeze(args.spec, args.output)
    elif args.command == "assess":
        result = assess_registry(args.protocol, args.registry, args.revision, args.output)[
            "verdict"
        ]
    else:
        spec = load_protocol(args.protocol)
        if args.command == "plan":
            result = trial_plan(spec)
        elif args.command == "materialize":
            result = materialize(spec, anchor_ns=args.anchor_ns, created_ns=time.time_ns())
        else:
            result = select_validation(spec, args.phase, read(args.results))
        write(args.output, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
