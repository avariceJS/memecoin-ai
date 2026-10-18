"""Offline matched cadence audit. All comparisons are engineering observations."""

import argparse
import json
from collections import Counter
from pathlib import Path

from alchemy_pilot_report import audit_raw_stream, distribution

from memecoin_ai.causal import observed_checkpoint
from memecoin_ai.domain import ValidationError, canonical, digest
from memecoin_ai.hybrid import verify_segment
from memecoin_ai.sources import Capture


def analyze(root):
    verification = verify_segment(root)
    plan = json.loads((root / "plan.json").read_text())
    report = json.loads((root / "report.json").read_text())
    rows = json.loads((root / "derived.json").read_text())
    events = [
        json.loads(line)["event"] for line in (root / "events.jsonl").read_text().splitlines()
    ]
    result = dict(
        path=str(root),
        complete=report["complete"],
        failure=report["failure"],
        verification=verification,
        mode=plan["segment_modes"][int(root.name[-2:])],
        manifest_sha256=digest((root / "manifest.json").read_bytes()),
        source_sha256=digest((root / "source.json").read_bytes()),
        run=json.loads((root / "run.json").read_text()),
        stream=report["stream"],
        stream_stats=rows["stream_stats"],
        arms={},
        raw_audit=audit_raw_stream(root, events, report),
        cpu_seconds_whole_process=report["cpu_seconds"],
        peak_rss_bytes_macos=report["peak_rss_native_units"],
    )
    schedule = [e for e in events if e["kind"] == "schedule_start"]
    result["schedule_start_ns"] = schedule[0]["available_ns"] if schedule else None
    result["calibration_end_ns"] = (result["schedule_start_ns"] + 60 * 10**9) if schedule else None
    if not report["complete"]:
        result["comparison_available"] = False
        return result
    frame_times = {}
    all_ticks = {}
    for arm in plan["arms"]:
        snaps = [e for e in events if e["kind"] == "snapshot" and e["arm"] == arm]
        measurements = [m for m in report["measurements"] if m["arm"] == arm]
        features = rows[arm]["features"]
        last = {}
        changes = Counter()
        times = {}
        for event in snaps:
            cap = Capture.load(root / event["capture"])
            obs = observed_checkpoint(
                cap, event["pool"], event["available_ns"], config_layout_version=2
            )
            fingerprint = digest(canonical(obs["economic"]).encode())
            pool = event["pool"]
            if pool in last and last[pool] != fingerprint:
                changes[pool] += 1
            last[pool] = fingerprint
            times.setdefault(pool, []).append(cap.request_started_ns)
        tick_sets = {}
        for m in measurements:
            cap = Capture.load(root / m["capture"])
            pool = json.loads(cap.request_json)["params"][0][0]
            tick_sets.setdefault(pool, []).append(m["tick"])
        all_ticks[arm] = tick_sets
        frame_times[arm] = [
            e["as_of_ns"] for e in events if e["kind"] == "frame" and e["arm"] == arm
        ]
        http_bytes = sum(m["bytes"] for m in measurements)
        result["arms"][arm] = dict(
            requests=report["requests"].get(arm, 0),
            accepted=len(snaps),
            feature_rows=len(features),
            usable=sum(r["usable"] for r in features),
            usable_fraction=sum(r["usable"] for r in features) / len(features),
            masks=dict(Counter(x for r in features for x in r["reasons"])),
            targets=dict(Counter(t["partition"] for t in rows[arm]["targets"])),
            http_payload_bytes=http_bytes,
            standalone_payload_bytes=http_bytes
            + (report["stream"]["bytes"] if arm in plan["stream_arms"] else 0),
            observed_economic_changes=sum(changes.values()),
            changes_by_pool=dict(changes),
            http_transport_seconds=distribution(
                [m["transport"]["total_ns"] / 1e9 for m in measurements]
            ),
            chain_age_seconds=distribution(
                [
                    r["values"]["chain_clock_age_seconds"]
                    for r in features
                    if r["values"]["chain_clock_age_seconds"] is not None
                ]
            ),
            request_spacing_seconds=distribution(
                [(b - a) / 1e9 for v in times.values() for a, b in zip(v, v[1:], strict=False)]
            ),
            scheduling_lateness_seconds=distribution(
                [m["scheduling_lateness_seconds"] for m in measurements]
            ),
            reasons=dict(Counter(e["reason"] for e in snaps)),
            ticks_by_pool=tick_sets,
        )
    if any(frame_times[a] != frame_times["http5"] for a in plan["arms"]):
        raise ValidationError("feature grids differ")
    if all_ticks["http10"] != all_ticks["wss10"]:
        raise ValidationError("matched cadence differs")
    for arm in ("http5", "http10", "wss10"):
        expected = [t for t in range(plan["ticks"]) if t % plan["period_ticks"][arm] == 0]
        if result["mode"] == "symmetric_stress":
            expected.remove(plan["stress_tick"])
            expected = sorted(set(expected + [plan["stress_recovery_tick"]]))
        if any(ticks != expected for ticks in all_ticks[arm].values()):
            raise ValidationError("fixed cadence changed")
    h5, h10, adaptive = [result["arms"][a] for a in ("http5", "http10", "adaptive")]
    result["cadence_checks"] = dict(matched10=True, common_feature_grid=True, absolute_ticks=True)
    result["comparison_available"] = True
    result["http_saving_vs_5"] = 1 - adaptive["requests"] / h5["requests"]
    result["usable_delta_vs_5"] = adaptive["usable_fraction"] - h5["usable_fraction"]
    result["payload_saving_vs_10"] = (
        1 - adaptive["standalone_payload_bytes"] / h10["standalone_payload_bytes"]
    )
    more_changes = (
        adaptive["observed_economic_changes"] > h10["observed_economic_changes"]
        and adaptive["observed_economic_changes"] >= 1.2 * h10["observed_economic_changes"]
    )
    result["primary_criterion_met"] = (
        result["mode"] == "steady"
        and result["http_saving_vs_5"] >= 0.2
        and result["usable_delta_vs_5"] >= -0.05
        and adaptive["observed_economic_changes"] >= h10["observed_economic_changes"]
        and (
            result["payload_saving_vs_10"] >= 0.2
            or (
                more_changes
                and adaptive["standalone_payload_bytes"] <= h5["standalone_payload_bytes"]
            )
        )
    )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            [
                analyze(p)
                for p in sorted(args.root.glob("segment-*"))
                if (p / "manifest.json").exists()
            ],
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
