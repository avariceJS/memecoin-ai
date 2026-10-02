from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from memecoin_ai.blockrange import recover_ranges
from memecoin_ai.collection import BudgetExhausted, PilotCollector, PilotStore
from memecoin_ai.config import RunConfig
from memecoin_ai.datasets import export_economic_dataset, verify_economic_dataset
from memecoin_ai.domain import Event, ValidationError, canonical
from memecoin_ai.execution_targets import export_execution_targets, verify_execution_targets
from memecoin_ai.experiments import RunRecorder, summarize, verify_run
from memecoin_ai.fixtures import ScriptedFixturePolicy, synthetic_events
from memecoin_ai.forward import (
    collect_forward,
    refine_forward_targets,
    verify_forward,
    verify_forward_targets,
)
from memecoin_ai.prospective import create_campaign, resume_campaign, verify_campaign
from memecoin_ai.protocol import write_protocol_audit
from memecoin_ai.provider_audit import (
    audit_provider_range,
    compare_captures,
    crosscheck,
    crosscheck_chunked_range,
    measure_latency,
    verify_provider_bundle,
)
from memecoin_ai.qualification import qualify_cpmm, verify_qualification
from memecoin_ai.raydium import checkpoint_inventory
from memecoin_ai.reconciliation import export_pilot, verify_pilot, write_audit
from memecoin_ai.replay import CashPolicy, ReferenceReplay
from memecoin_ai.sources import Capture, ReadOnlyRPC, SourceError, probe_rpc
from memecoin_ai.storage import EventStore, audit, load_bundle, write_bundle
from memecoin_ai.witness import verify_collection_witness, write_liquidity_witness


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
    collection_witness = commands.add_parser(
        "audit-collection-witness", help="Observed fee transfers; no inferred counter prestate"
    )
    collection_witness.add_argument("capture", type=Path)
    collection_witness.add_argument("--out", type=Path, required=True)
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
            "--block-range",
            action="store_true",
            help="Recover <=256-slot intervals from complete block enumeration",
        )
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
    dataset = commands.add_parser(
        "export-economic-dataset", help="Offline CPMM state tape and conditional quote grid"
    )
    dataset.add_argument("--store", type=Path, required=True)
    dataset.add_argument("--out", type=Path, required=True)
    dataset.add_argument("--size-ppm", type=int, action="append", required=True)
    verify_dataset = commands.add_parser(
        "verify-economic-dataset", help="Reproduce economic dataset from its frozen journal"
    )
    verify_dataset.add_argument("path", type=Path)
    execution = commands.add_parser(
        "export-execution-targets", help="Conditional snapshot probes; no actual fills"
    )
    execution.add_argument("parent", type=Path)
    execution.add_argument("--out", type=Path, required=True)
    execution_verify = commands.add_parser(
        "verify-execution-targets", help="Reproduce conditional targets offline"
    )
    execution_verify.add_argument("parent", type=Path)
    execution_verify.add_argument("path", type=Path)
    campaign = commands.add_parser(
        "create-prospective-campaign", help="Freeze bounded segment campaign"
    )
    campaign.add_argument("--checkpoint", type=Path, action="append", required=True)
    campaign.add_argument("--source-alias", required=True)
    campaign.add_argument("--endpoint-origin", required=True)
    campaign.add_argument("--segments", type=int, default=4)
    campaign.add_argument("--rounds", type=int, default=30)
    campaign.add_argument("--interval", type=int, default=5)
    campaign.add_argument("--out", type=Path, required=True)
    resume = commands.add_parser(
        "resume-prospective-campaign", help="Collect one new bounded segment"
    )
    resume.add_argument("path", type=Path)
    resume.add_argument("--endpoint-env", required=True)
    campaign_verify = commands.add_parser(
        "verify-prospective-campaign", help="Offline campaign reproduction"
    )
    campaign_verify.add_argument("path", type=Path)
    forward = commands.add_parser(
        "collect-forward-corpus", help="Bounded causal finalized snapshots; no trading"
    )
    forward.add_argument("--checkpoint", type=Path, action="append", required=True)
    forward.add_argument("--source-alias", required=True)
    forward.add_argument("--endpoint-env", default="MEMECOIN_HELIUS_RPC_URL")
    forward.add_argument("--rounds", type=int, default=12)
    forward.add_argument("--interval", type=int, default=10)
    forward.add_argument("--out", type=Path, required=True)
    verify_forward_command = commands.add_parser(
        "verify-forward-corpus", help="Reproduce online causal features and targets offline"
    )
    verify_forward_command.add_argument("path", type=Path)
    verify_forward_command.add_argument(
        "--compatible-version",
        action="store_true",
        help="Permit code-version change only when all archived values reproduce exactly",
    )
    refined = commands.add_parser(
        "refine-forward-targets", help="Derive fresh-snapshot targets offline"
    )
    refined.add_argument("parent", type=Path)
    refined.add_argument("--out", type=Path, required=True)
    refined_verify = commands.add_parser(
        "verify-forward-targets", help="Reproduce refined targets and parent"
    )
    refined_verify.add_argument("parent", type=Path)
    refined_verify.add_argument("path", type=Path)
    latency = commands.add_parser(
        "measure-rpc-latency", help="Bounded Clock sampling; response time is not execution latency"
    )
    latency.add_argument("--source-alias", required=True)
    latency.add_argument("--endpoint-env", default="MEMECOIN_RPC_URL")
    latency.add_argument("--rounds", type=int, default=12)
    latency.add_argument("--interval", type=float, default=3)
    latency.add_argument("--out", type=Path, required=True)
    cross = commands.add_parser(
        "crosscheck-captures", help="Repeat exact finalized requests on another provider"
    )
    cross.add_argument("captures", nargs="+", type=Path)
    cross.add_argument("--source-alias", required=True)
    cross.add_argument("--endpoint-env", default="MEMECOIN_SECONDARY_RPC_URL")
    cross.add_argument("--out", type=Path, required=True)
    compare = commands.add_parser(
        "compare-rpc-captures", help="Offline full comparison with bounded difference diagnostics"
    )
    compare.add_argument("left", type=Path)
    compare.add_argument("right", type=Path)
    compare.add_argument("--out", type=Path, required=True)
    provider_verify = commands.add_parser(
        "verify-provider-audit", help="Offline capture/measurement verification"
    )
    provider_verify.add_argument("path", type=Path)
    chunks = commands.add_parser(
        "crosscheck-range", help="Compare a finalized range via bounded chunks"
    )
    chunks.add_argument("capture", type=Path)
    chunks.add_argument("--source-alias", required=True)
    chunks.add_argument("--endpoint-env", default="MEMECOIN_SECONDARY_RPC_URL")
    chunks.add_argument("--max-slots", type=int, default=5)
    chunks.add_argument("--out", type=Path, required=True)
    for rpc_command in (latency, cross, chunks):
        rpc_command.add_argument(
            "--timeout", type=float, default=10, help="RPC timeout in seconds, (0, 60]"
        )
    range_audit = commands.add_parser(
        "audit-provider-range", help="Verify enumeration and full block agreement"
    )
    range_audit.add_argument("enumeration", type=Path)
    range_audit.add_argument("blocks", type=Path)
    range_audit.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command in ("measure-rpc-latency", "crosscheck-captures", "crosscheck-range"):
            endpoint = os.environ.get(args.endpoint_env)
            if not endpoint:
                raise ValidationError("RPC endpoint environment variable is not set")
            client = ReadOnlyRPC(
                endpoint, args.source_alias, timeout=args.timeout, max_bytes=32_000_000
            )
            if args.command == "measure-rpc-latency":
                report = measure_latency(
                    client, args.out, rounds=args.rounds, interval=args.interval
                )
            elif args.command == "crosscheck-range":
                report = crosscheck_chunked_range(
                    Capture.load(args.capture), client, args.out, max_slots=args.max_slots
                )
            else:
                report = crosscheck([Capture.load(p) for p in args.captures], client, args.out)
            print(canonical(report))
            return (
                0
                if not report.get("errors", 0)
                and report.get("all_requested_observations_match", True)
                and report.get("enumeration_match", True)
                else 1
            )
        elif args.command == "compare-rpc-captures":
            report = compare_captures(Capture.load(args.left), Capture.load(args.right))
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("x") as stream:
                stream.write(canonical(report) + "\n")
            print(canonical(report))
            return 0 if report["status"] == "match" else 1
        elif args.command == "audit-provider-range":
            report = audit_provider_range(args.enumeration, args.blocks)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("x") as stream:
                stream.write(canonical(report) + "\n")
            print(canonical(report))
        elif args.command == "verify-provider-audit":
            print(canonical(verify_provider_bundle(args.path)))
        elif args.command in ("export-execution-targets", "verify-execution-targets"):
            report = (
                export_execution_targets(args.parent, args.out)
                if args.command == "export-execution-targets"
                else verify_execution_targets(args.parent, args.path)
            )
            print(canonical(report))
        elif args.command == "create-prospective-campaign":
            print(
                canonical(
                    create_campaign(
                        args.out,
                        [Capture.load(p) for p in args.checkpoint],
                        source_alias=args.source_alias,
                        endpoint_origin=args.endpoint_origin,
                        segments=args.segments,
                        rounds=args.rounds,
                        interval_seconds=args.interval,
                    )
                )
            )
        elif args.command in ("resume-prospective-campaign", "verify-prospective-campaign"):
            if args.command == "verify-prospective-campaign":
                report = verify_campaign(args.path)
            else:
                endpoint = os.environ.get(args.endpoint_env)
                if not endpoint:
                    raise ValidationError("RPC endpoint environment variable is not set")
                plan = json.loads((args.path / "plan.json").read_text())
                report = resume_campaign(
                    args.path,
                    ReadOnlyRPC(
                        endpoint,
                        plan["source_alias"],
                        timeout=plan["timeout_seconds"],
                        max_bytes=plan["max_response_bytes"],
                    ),
                    emit=lambda row: print(canonical(row), flush=True),
                )
            print(canonical(report))
        elif args.command in ("refine-forward-targets", "verify-forward-targets"):
            report = (
                refine_forward_targets(args.parent, args.out)
                if args.command == "refine-forward-targets"
                else verify_forward_targets(args.parent, args.path)
            )
            print(canonical(report))
        elif args.command in ("collect-forward-corpus", "verify-forward-corpus"):
            if args.command == "verify-forward-corpus":
                report = verify_forward(
                    args.path, reproduce_compatible_version=args.compatible_version
                )
            else:
                endpoint = os.environ.get(args.endpoint_env)
                if not endpoint:
                    raise ValidationError("RPC endpoint environment variable is not set")
                report = collect_forward(
                    ReadOnlyRPC(endpoint, args.source_alias, timeout=10, max_bytes=1_000_000),
                    [Capture.load(p) for p in args.checkpoint],
                    args.out,
                    rounds=args.rounds,
                    interval_seconds=args.interval,
                    emit=lambda row: print(canonical(row), flush=True),
                )
            print(canonical(report))
            return 0 if report["complete"] else 1
        elif args.command in ("export-economic-dataset", "verify-economic-dataset"):
            if args.command == "verify-economic-dataset":
                report = verify_economic_dataset(args.path)
            else:
                if not args.store.is_file():
                    raise ValidationError("pilot journal does not exist")
                pilot_store = PilotStore(args.store, read_only=True)
                try:
                    report = export_economic_dataset(pilot_store, args.out, sizes_ppm=args.size_ppm)
                finally:
                    pilot_store.close()
            print(
                canonical(
                    {k: report[k] for k in ("counts", "training_ready", "execution_qualified")}
                )
            )
            return 0 if report["protocol_audit"]["all_intervals_consistent"] else 1
        elif args.command == "audit-protocol":
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
                        max_bytes=32_000_000 if args.full_blocks or args.block_range else 4_000_000,
                    ),
                    full_blocks=args.full_blocks,
                    budget=args.budget,
                    delay=args.rpc_interval,
                    page_size=args.recovery_page_size,
                )
                if args.command == "collect-pilot" and args.block_range:
                    raise ValidationError("use --block-range with recover-pilot after checkpoints")
                if args.command == "collect-pilot":
                    collector.poll(args.rounds, args.interval, recover=not args.snapshots_only)
                else:
                    try:
                        if args.block_range:
                            recover_ranges(collector)
                        else:
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
        elif args.command == "audit-collection-witness":
            report = verify_collection_witness(Capture.load(args.capture))
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("x") as stream:
                stream.write(canonical(report) + "\n")
            print(canonical(report))
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
