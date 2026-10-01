from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from memecoin_ai.config import RunConfig
from memecoin_ai.domain import Event, ValidationError, canonical
from memecoin_ai.experiments import RunRecorder, summarize, verify_run
from memecoin_ai.fixtures import ScriptedFixturePolicy, synthetic_events
from memecoin_ai.qualification import qualify_cpmm, verify_qualification
from memecoin_ai.raydium import checkpoint_inventory
from memecoin_ai.replay import CashPolicy, ReferenceReplay
from memecoin_ai.sources import Capture, ReadOnlyRPC, SourceError, probe_rpc
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
    demo = commands.add_parser("fixture", help="Create a clearly synthetic data bundle")
    demo.add_argument("--out", type=Path, required=True)
    run = commands.add_parser("run", help="Run a reference policy on synthetic data only")
    run.add_argument("bundle", type=Path)
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--repo", type=Path, default=Path.cwd())
    verify = commands.add_parser("verify-run", help="Verify completed run artifacts")
    verify.add_argument("path", type=Path)
    probe = commands.add_parser("probe-rpc", help="Bounded read-only source probe; no transactions")
    probe.add_argument("--source-alias", required=True)
    probe.add_argument("--endpoint-env", default="MEMECOIN_RPC_URL")
    probe.add_argument("--out", type=Path, required=True)
    qualify = commands.add_parser(
        "qualify-cpmm", help="Bounded real CPMM account/transaction audit"
    )
    qualify.add_argument("--source-alias", required=True)
    qualify.add_argument("--endpoint-env", default="MEMECOIN_RPC_URL")
    qualify.add_argument("--limit", type=int, default=12)
    qualify.add_argument("--address", default="CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C")
    qualify.add_argument("--out", type=Path, required=True)
    verify_source = commands.add_parser(
        "verify-qualification", help="Offline reproduction of captured source audit"
    )
    verify_source.add_argument("path", type=Path)
    inspect = commands.add_parser(
        "inspect-cpmm", help="Offline structural inventory; no executable quotes"
    )
    inspect.add_argument("capture", type=Path)
    inspect.add_argument("--pool", required=True)
    args = parser.parse_args()
    try:
        if args.command == "inspect-cpmm":
            print(canonical(checkpoint_inventory(Capture.load(args.capture), args.pool)))
        elif args.command == "verify-qualification":
            print(canonical(verify_qualification(args.path)))
        elif args.command in ("probe-rpc", "qualify-cpmm"):
            endpoint = os.environ.get(args.endpoint_env)
            if not endpoint:
                raise ValidationError("RPC endpoint environment variable is not set")
            client = ReadOnlyRPC(endpoint, args.source_alias)
            if args.command == "qualify-cpmm":
                report = qualify_cpmm(
                    client, args.out, limit=args.limit, discovery_address=args.address
                )
                print(canonical(report))
                return 0 if report["checkpoints"] and not report["failures"] else 1
            report = probe_rpc(client, args.out)
            print(canonical(report))
            results = report["results"]
            assert isinstance(results, dict)
            if any(isinstance(value, dict) and "error" in value for value in results.values()):
                return 1
        elif args.command == "ingest":
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
        elif args.command == "fixture":
            bundle = write_bundle(args.out, synthetic_events(), "synthetic engineering fixture v1")
            print(canonical(audit(bundle)))
        elif args.command == "run":
            bundle = load_bundle(args.bundle)
            config = RunConfig.load(args.config)
            recorder = RunRecorder(args.repo, args.out, bundle.identity, asdict(config))
            try:
                replay = ReferenceReplay(
                    bundle.events,
                    config.execution,
                    config.risk,
                    quote_asset=config.quote_asset,
                    initial_cash=config.initial_cash,
                    allow_assumed=config.allow_assumed,
                )
                policy = CashPolicy() if config.policy == "cash" else ScriptedFixturePolicy(config)
                result = replay.run(policy, config.decision_times_ns, config.end_ns)
                recorder.complete(result, config.initial_cash)
            except Exception as exc:
                recorder.fail(type(exc).__name__)
                raise
            print(canonical(summarize(result, config.initial_cash)))
        elif args.command == "verify-run":
            print(canonical(verify_run(args.path)))
        else:
            print(canonical(audit(load_bundle(args.bundle))))
    except (ValueError, TypeError, KeyError, OSError, json.JSONDecodeError, SourceError) as exc:
        parser.exit(2, f"error: {exc}\n")
    return 0
