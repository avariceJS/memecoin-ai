from __future__ import annotations

import argparse
import json
from pathlib import Path

from memecoin_ai.domain import Event, ValidationError, canonical
from memecoin_ai.storage import EventStore, audit, load_bundle, write_bundle


def main() -> int:
    parser = argparse.ArgumentParser(prog="mcai")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest", help="Validate and atomically append normalized JSONL")
    ingest.add_argument("input", type=Path)
    ingest.add_argument("--store", type=Path, required=True)
    export = commands.add_parser("export", help="Export an immutable dataset bundle")
    export.add_argument("--store", type=Path, required=True)
    export.add_argument("--out", type=Path, required=True)
    check = commands.add_parser("audit", help="Verify integrity; report availability limitations")
    check.add_argument("bundle", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "ingest":
            events = [Event.from_json(line) for line in args.input.read_text().splitlines()]
            with EventStore(args.store) as store:
                print(canonical({"inserted": store.append(events)}))
        elif args.command == "export":
            if not args.store.is_file():
                raise ValidationError("store does not exist")
            with EventStore(args.store) as store:
                events = store.events()
                raw = {e.raw_sha256: store.raw(e.raw_sha256) for e in events if e.raw_sha256}
                bundle = write_bundle(args.out, events, "event store export", raw_blobs=raw)
            print(canonical(audit(bundle)))
        else:
            print(canonical(audit(load_bundle(args.bundle))))
    except (ValueError, TypeError, KeyError, OSError, json.JSONDecodeError) as exc:
        parser.exit(2, f"error: {exc}\n")
    return 0
