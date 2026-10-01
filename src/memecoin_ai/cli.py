from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from memecoin_ai.collection import BudgetExhausted, PilotCollector, PilotStore
from memecoin_ai.config import RunConfig
from memecoin_ai.domain import Event, ValidationError, canonical
from memecoin_ai.experiments import RunRecorder, summarize, verify_run
from memecoin_ai.fixtures import ScriptedFixturePolicy, synthetic_events
from memecoin_ai.protocol import write_protocol_audit
from memecoin_ai.qualification import qualify_cpmm, verify_qualification
from memecoin_ai.raydium import checkpoint_inventory
from memecoin_ai.reconciliation import export_pilot, verify_pilot, write_audit
from memecoin_ai.replay import CashPolicy, ReferenceReplay
from memecoin_ai.sources import Capture, ReadOnlyRPC, SourceError, probe_rpc
from memecoin_ai.storage import EventStore, audit, load_bundle, write_bundle
from memecoin_ai.witness import write_liquidity_witness


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
    witness = commands.add_parser(
        "audit-liquidity-witness",
        help="Event-conditioned arithmetic check; not historical state reconstruction",
    )
    witness.add_argument("capture", type=Path)
    witness.add_argument("--out", type=Path, required=True)
    for command in ("collect-pilot", "recover-pilot"):
        pilot = commands.add_parser(command, help="Bounded durable finalized collection/recovery")
        pilot.add_argument("--store", type=Path, required=True)
        pilot.add_argument("--source-alias", required=True)
        pilot.add_argument("--endpoint-env", default="MEMECOIN_RPC_URL")
        pilot.add_argument("--budget", type=int, default=100)
        pilot.add_argument("--rpc-interval", type=float, default=1.2)
        pilot.add_argument("--recovery-page-size", type=int)
        pilot.add_argument(
            "--full-blocks",
            action="store_true",
            help="Recover parsed blocks instead of individual transactions; 32 MB bound",
        )
        if command == "collect-pilot":
            pilot.add_argument("--pool", action="append")
            pilot.add_argument("--page-size", type=int, default=100)
            pilot.add_argument("--rounds", type=int, default=3)
            pilot.add_argument("--interval", type=float, default=10)
            pilot.add_argument("--snapshots-only", action="store_true")
            pilot.add_argument(
                "--dependency-history",
                action="store_true",
                help="Scan config/mints too; requires --token-2022",
            )
            pilot.add_argument(
                "--token-2022",
                action="store_true",
                help="Use allowlisted seven-account checkpoints",
            )
    audit_pilot = commands.add_parser(
        "audit-pilot", help="Offline checkpoint interval reconciliation"
    )
    audit_pilot.add_argument("--store", type=Path, required=True)
    audit_pilot.add_argument("--out", type=Path, required=True)
    export_capture = commands.add_parser(
        "export-pilot", help="Freeze journal, audit and decoder source"
    )
    export_capture.add_argument("--store", type=Path, required=True)
    export_capture.add_argument("--out", type=Path, required=True)
    verify_capture = commands.add_parser(
        "verify-pilot", help="Offline verification of an exported pilot"
    )
    verify_capture.add_argument("path", type=Path)
    protocol = commands.add_parser(
        "audit-protocol", help="Offline CPMM transitions and quote checks"
    )
    protocol.add_argument("--store", type=Path, required=True)
    protocol.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "audit-protocol":
            pilot_store = PilotStore(args.store, read_only=True)
            try:
                report = write_protocol_audit(pilot_store, args.out)
                print(
                    canonical(
                        {
                            k: report[k]
                            for k in (
                                "all_intervals_consistent",
                                "economic_scope",
                                "transaction_execution_qualified",
                            )
                        }
                    )
                )
                return 0 if report["all_intervals_consistent"] else 1
            finally:
                pilot_store.close()
        elif args.command == "verify-pilot":
            print(canonical(verify_pilot(args.path)))
        elif args.command == "export-pilot":
            pilot_store = PilotStore(args.store, read_only=True)
            try:
                print(canonical(export_pilot(pilot_store, args.out)))
            finally:
                pilot_store.close()
        elif args.command in ("collect-pilot", "recover-pilot", "audit-pilot"):
            if args.command != "collect-pilot" and not args.store.is_file():
                raise ValidationError("pilot journal does not exist")
            pilot_store = PilotStore(args.store, read_only=args.command == "audit-pilot")
            try:
                if args.command == "audit-pilot":
                    report = write_audit(pilot_store, args.out)
                    print(
                        canonical(
                            {
                                k: report[k]
                                for k in (
                                    "checkpoints",
                                    "all_intervals_consistent",
                                    "market_dataset_qualified",
                                )
                            }
                        )
                    )
                    return 0 if report["all_intervals_consistent"] else 1
                endpoint = os.environ.get(args.endpoint_env)
                if not endpoint:
                    raise ValidationError("RPC endpoint environment variable is not set")
                if args.command == "collect-pilot" and args.pool:
                    pilot_store.configure(
                        args.pool,
                        args.source_alias,
                        args.page_size,
                        token2022=args.token_2022,
                        dependency_history=args.dependency_history,
                    )
                collector = PilotCollector(
                    pilot_store,
                    ReadOnlyRPC(
                        endpoint,
                        args.source_alias,
                        max_bytes=32_000_000 if args.full_blocks else 4_000_000,
                    ),
                    full_blocks=args.full_blocks,
                    budget=args.budget,
                    delay=args.rpc_interval,
                    page_size=args.recovery_page_size,
                )
                if args.command == "collect-pilot":
                    collector.poll(args.rounds, args.interval, recover=not args.snapshots_only)
                else:
                    try:
                        collector.recover()
                    except BudgetExhausted:
                        pilot_store.issue(
                            "budget", "request budget exhausted; recovery remains pending"
                        )
                pending = pilot_store.pending()
                print(
                    canonical(
                        dict(
                            request_budget_remaining=collector.remaining,
                            pending=pending,
                            market_dataset_qualified=False,
                        )
                    )
                )
                return 0 if not any(pending.values()) else 1
            finally:
                pilot_store.close()
        elif args.command == "audit-liquidity-witness":
            print(canonical(write_liquidity_witness(Capture.load(args.capture), args.out)))
        elif args.command == "inspect-cpmm":
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
