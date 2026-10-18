"""Offline D40 transport/coverage QA; no network, no training or execution claims."""

import json
import statistics
from collections import Counter
from pathlib import Path

from memecoin_ai.causal import observed_checkpoint
from memecoin_ai.domain import ValidationError, canonical, digest
from memecoin_ai.hybrid import verify_segment
from memecoin_ai.sources import Capture


def distribution(values):
    v = sorted(values)
    return (
        dict(
            count=len(v),
            min=v[0],
            p50=statistics.median(v),
            p95=v[min(len(v) - 1, int(0.95 * len(v)))],
            max=v[-1],
        )
        if v
        else dict(count=0)
    )


def analyze(root):
    validation = verify_segment(root)
    plan = json.loads((root / "plan.json").read_text())
    report = json.loads((root / "report.json").read_text())
    derived = json.loads((root / "derived.json").read_text())
    run = json.loads((root / "run.json").read_text())
    events = [json.loads(x)["event"] for x in (root / "events.jsonl").read_text().splitlines()]
    result = dict(
        path=str(root),
        manifest_sha256=digest((root / "manifest.json").read_bytes()),
        source_sha256=digest((root / "source.json").read_bytes()),
        run=run,
        verification=validation,
        arms={},
        stream=report["stream"],
        stream_stats=derived["stream_stats"],
        complete=report["complete"],
        failure=report["failure"],
        wall_seconds=report["wall_seconds"],
        cpu_seconds=report["cpu_seconds"],
        peak_rss_bytes_macos=report["peak_rss_native_units"],
    )
    for arm in ("http", "hybrid"):
        rows = derived[arm]["features"]
        snaps = [e for e in events if e["kind"] == "snapshot" and e["arm"] == arm]
        m = [m for m in report["measurements"] if m["arm"] == arm]
        last = {}
        changes = Counter()
        states = set()
        bypool = {}
        stamps = []
        for e in snaps:
            cap = Capture.load(root / e["capture"])
            obs = observed_checkpoint(cap, e["pool"], e["available_ns"], config_layout_version=2)
            pool = e["pool"]
            h = digest(canonical(obs["economic"]).encode())
            states.add((pool, h))
            if pool in last and last[pool] != h:
                changes[pool] += 1
            last[pool] = h
            bypool.setdefault(pool, []).append(obs["slot"])
            stamps.append(cap.received_ns)
        result["arms"][arm] = dict(
            http_requests=report["requests"].get(arm, 0),
            accepted_snapshots=len(snaps),
            distinct_banks=sum(len(set(v)) for v in bypool.values()),
            distinct_economic_states=len(states),
            observed_economic_changes=dict(changes),
            feature_rows=len(rows),
            usable=sum(r["usable"] for r in rows),
            usable_fraction=sum(r["usable"] for r in rows) / len(rows) if rows else 0,
            masks=dict(Counter(x for r in rows for x in r["reasons"])),
            targets=dict(Counter(t["partition"] for t in derived[arm]["targets"])),
            response_bytes=sum(x["bytes"] for x in m),
            capture_and_decode_seconds=distribution([x["response_seconds"] for x in m]),
            http_transport_seconds=distribution([x["transport"]["total_ns"] / 1e9 for x in m]),
            chain_age_seconds=distribution(
                [
                    r["values"]["chain_clock_age_seconds"]
                    for r in rows
                    if r["values"]["chain_clock_age_seconds"] is not None
                ]
            ),
            receipt_age_seconds=distribution(
                [
                    r["values"]["receipt_age_seconds"]
                    for r in rows
                    if r["values"]["receipt_age_seconds"] is not None
                ]
            ),
            receipt_span_seconds=(max(stamps) - min(stamps)) / 1e9 if stamps else 0,
            request_reasons=dict(Counter(e["reason"] for e in snaps)),
        )
    gaps = [e for e in events if e["kind"] == "gap"]
    recovery = []
    for e in gaps:
        later = [
            s
            for s in events
            if s["kind"] == "snapshot"
            and s["arm"] == e["arm"]
            and s["available_ns"] > e["available_ns"]
        ]
        first = {}
        for s in later:
            first.setdefault(s["pool"], s["available_ns"])
        recovery.append(
            dict(
                reason=e["reason"],
                gap_available_ns=e["available_ns"],
                all_pool_new_snapshot_seconds=(max(first.values()) - e["available_ns"]) / 1e9
                if len(first) == len(plan["cohort"])
                else None,
                historical_recovery=False,
            )
        )
    result["recovery"] = recovery
    result["raw_stream_audit"] = audit_raw_stream(root, events, report)
    result["notification_chain_age_seconds"] = None
    result["notification_chain_age_reason"] = (
        "account/root notification has no source wall timestamp"
    )
    result["hybrid_total_received_payload_bytes"] = (
        result["arms"]["hybrid"]["response_bytes"] + report["stream"]["bytes"]
    )
    result["same_bank_cross_provider_agreement"] = "not tested; polls at different banks"
    result["cpu_scope"] = "whole paired process including public collector thread; not per arm"
    for arm in ("http", "hybrid"):
        frames = [e for e in events if e["kind"] == "frame" and e["arm"] == arm]
        gap_times = {e["available_ns"] for e in gaps if e["arm"] == arm}
        regular = [
            e
            for e in frames
            if not any(g <= e["as_of_ns"] and e["as_of_ns"] - g < 100_000_000 for g in gap_times)
        ]
        times = {e["as_of_ns"] for e in regular}
        regular_rows = [r for r in derived[arm]["features"] if r["as_of_ns"] in times]
        result["arms"][arm]["scheduled_rows"] = len(regular_rows)
        result["arms"][arm]["scheduled_usable_fraction"] = sum(
            r["usable"] for r in regular_rows
        ) / len(regular_rows)
    # This sensitivity excludes explicit extra gap frames, not warmup or bad rows.

    result["http_reduction_fraction"] = (
        1 - result["arms"]["hybrid"]["http_requests"] / result["arms"]["http"]["http_requests"]
    )
    result["usable_fraction_delta"] = (
        result["arms"]["hybrid"]["usable_fraction"] - result["arms"]["http"]["usable_fraction"]
    )
    result["load_criterion_met"] = (
        result["http_reduction_fraction"] >= 0.2 and result["usable_fraction_delta"] >= -0.05
    )
    return result


def audit_raw_stream(root, events, report):
    receipts = {}
    last_time = 0
    for path in sorted(root.glob("stream-*/receipts.jsonl")):
        for line in path.read_text().splitlines():
            row = json.loads(line)
            relative = str((path.parent / row["file"]).relative_to(root))
            raw = (root / relative).read_bytes()
            if (
                relative in receipts
                or len(raw) != row["bytes"]
                or digest(raw) != row["sha256"]
                or row["received_ns"] < last_time
            ):
                raise ValidationError("stream receipt integrity violation")
            last_time = row["received_ns"]
            receipts[relative] = row
    if (
        len(receipts) != report["stream"]["messages"]
        or sum(r["bytes"] for r in receipts.values()) != report["stream"]["bytes"]
    ):
        raise ValidationError("stream accounting mismatch")
    pending = {}
    subscriptions = {}
    counts = Counter()
    values = {}
    processed = set()
    for event in events:
        if event["kind"] == "connect":
            pending = {int(k): v for k, v in event["requests"].items()}
            subscriptions = {}
        if event["kind"] != "stream":
            continue
        name = event["file"]
        row = receipts[name]
        if (
            event["received_ns"] != row["received_ns"]
            or event["sha256"] != row["sha256"]
            or name in processed
        ):
            raise ValidationError("processed stream differs from raw receipt")
        processed.add(name)
        msg = json.loads((root / name).read_bytes())
        if "id" in msg:
            subscriptions[msg["result"]] = pending.pop(msg["id"])
        elif msg["method"] == "accountNotification":
            params = msg["params"]
            address = subscriptions[params["subscription"]]
            counts[address] += 1
            values.setdefault(address, set()).add(canonical(params["result"]["value"]))
    return dict(
        receipts_verified=len(receipts),
        processed=len(processed),
        raw_not_processed=len(receipts) - len(processed),
        per_account=[
            dict(address=a, notifications=n, distinct_values=len(values[a]))
            for a, n in counts.most_common()
        ],
    )


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    results = [
        analyze(p) for p in sorted(args.root.glob("segment-*")) if (p / "manifest.json").exists()
    ]
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
