#!/usr/bin/env python3
"""Offline D45 registry; writes only its own artifact tree, no D43 service actions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from memecoin_registry.contracts import RegistryError
from memecoin_registry.registry import export_fold, initialize, sync, verify_registry


def main() -> None:
    parser = argparse.ArgumentParser(description="D45 engineering-only corpus registry")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("root", type=Path)
    init.add_argument("--calendar", type=Path, required=True)
    for name in ("sync", "audit"):
        cmd = sub.add_parser(name)
        cmd.add_argument("root", type=Path)
    export = sub.add_parser("export")
    export.add_argument("root", type=Path)
    export.add_argument("output", type=Path)
    export.add_argument("--revision", type=int, required=True)
    export.add_argument("--fold", required=True)
    export.add_argument("--partition", choices=("train", "validation", "test"), required=True)
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = initialize(args.root, args.calendar)
        elif args.command == "sync":
            result = sync(args.root)
            result.pop("qualification", None)
        elif args.command == "audit":
            latest = verify_registry(args.root)
            result = (
                dict(revision=None)
                if latest is None
                else dict(revision=latest["path"], qualification=latest["report"])
            )
        else:
            result = export_fold(
                args.root,
                args.output,
                revision=args.revision,
                fold=args.fold,
                partition=args.partition,
            )
        print(json.dumps(result, indent=2))
    except Exception as exc:
        category = str(exc) if isinstance(exc, RegistryError) else type(exc).__name__
        print(json.dumps(dict(status="stopped", category=category, no_retry=True)), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
