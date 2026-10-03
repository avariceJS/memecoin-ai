"""Offline sampling proposal/ingestion/dossier CLI. No network or launchd operations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from memecoin_research.framework import load_protocol
from memecoin_sampling.contract import freeze
from memecoin_sampling.dossier import export_dossier, reproduce_dossier
from memecoin_sampling.registry import ingest, initialize, verify_registry


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("freeze")
    create.add_argument("protocol", type=Path)
    create.add_argument("output", type=Path)
    create.add_argument("--anchor-ns", type=int, required=True)
    create.add_argument("--source-root", type=Path, required=True)
    init = sub.add_parser("init")
    init.add_argument("proposal", type=Path)
    init.add_argument("root", type=Path)
    add = sub.add_parser("ingest")
    add.add_argument("root", type=Path)
    add.add_argument("index", type=int)
    add.add_argument("--source", type=Path)
    add.add_argument("--status", choices=["completed", "failed", "missed"], default="completed")
    for command in ("dossier", "reproduce"):
        cmd = sub.add_parser(command)
        cmd.add_argument("root", type=Path)
        cmd.add_argument("output", type=Path)
    audit = sub.add_parser("audit")
    audit.add_argument("root", type=Path)
    args = parser.parse_args()
    if args.command == "freeze":
        value = freeze(
            load_protocol(args.protocol),
            args.output,
            anchor_ns=args.anchor_ns,
            source_root=args.source_root,
        )
        result = dict(
            status=value["status"],
            windows=len(value["windows"]),
            total_rpc_ceiling=value["policy"]["total_max_rpc"],
            collection_authorized=False,
        )
    elif args.command == "init":
        initialize(args.proposal, args.root)
        result = dict(status="initialized_empty", collection_authorized=False)
    elif args.command == "ingest":
        result = ingest(args.root, args.index, source=args.source, status=args.status)
    elif args.command == "dossier":
        report = export_dossier(args.root, args.output)
        result = dict(
            numeric_design_passed=report["numeric_design_passed"],
            blockers=len(report["blockers"]),
            research_launch_authorized=False,
        )
    elif args.command == "reproduce":
        result = reproduce_dossier(args.root, args.output)
    else:
        value = verify_registry(args.root)
        result = dict(
            sequence=value["sequence"],
            head=value["head"],
            development_completed=sum(r["status"] == "completed" for r in value["records"]),
            holdout_opened=False,
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
