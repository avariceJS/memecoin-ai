"""Reproduce the D42 calendar corpus report without network requests."""

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from execution_uncertainty import reproduce

from memecoin_ai.corpus_quality import verify_quality
from memecoin_ai.coverage import verify_support
from memecoin_ai.domain import ValidationError, digest
from memecoin_ai.prospective import hashes, read, verify_campaign
from memecoin_ai.sources import Capture
from memecoin_ai.tokens import decode_supported_checkpoint


def sealed(root):
    actual = hashes(root)
    actual.pop("manifest.json")
    if actual != read(root / "manifest.json"):
        raise ValidationError("calendar/diagnostic manifest differs")


def iso(ns):
    return datetime.fromtimestamp(ns / 1e9, tz=UTC).isoformat()


def calendar_adherence(first_ns, second_ns, minimum_seconds):
    delta = second_ns - first_ns
    required = minimum_seconds * 10**9
    return dict(
        met=delta >= required,
        actual_separation_ns=delta,
        required_separation_ns=required,
        shortfall_ns=max(0, required - delta),
    )


def report(root, diagnostic):
    sealed(root)
    sealed(diagnostic)
    trials = []
    for p in sorted(diagnostic.glob("trial-*")):
        phase = [json.loads(x) for x in (p / "phases.jsonl").read_text().splitlines()]
        for capdir in sorted(p.glob("capture-*")):
            cap = Capture.load(capdir)
            decode_supported_checkpoint(
                cap, json.loads(cap.request_json)["params"][0][0], config_layout_version=2
            )
        trials.append(
            dict(
                path=str(p),
                reservation=read(p / "reservation.json"),
                result=read(p / "result.json"),
                phases=phase,
            )
        )
    windows = []
    for p in sorted(root.glob("window-*")):
        if not p.is_dir() or "-targets" in p.name or "-quality" in p.name or "-support" in p.name:
            continue
        state = verify_campaign(p)
        targets = p.with_name(p.name + "-targets")
        quality = verify_quality(p, targets, p.with_name(p.name + "-quality"))
        support = verify_support(p, targets, p.with_name(p.name + "-support"))
        uncertainty = reproduce(p, targets)
        attempts = []
        bounds = []
        for directory in sorted((p / "segments").iterdir()):
            rows = [
                json.loads(x)
                for x in (directory / "corpus/attempts.jsonl").read_text().splitlines()
            ]
            attempts.extend(rows)
            cp = read(directory / "corpus/plan.json")
            bounds.append(
                dict(
                    started_ns=cp["started_ns"],
                    last_available_ns=max(r["available_ns"] for r in rows),
                    requests=len(rows),
                    manifest_sha256=digest((directory / "corpus/manifest.json").read_bytes()),
                )
            )
        transports = [
            a["transport_measurement"] for a in attempts if a.get("transport_measurement")
        ]
        windows.append(
            dict(
                path=str(p),
                plan=state["plan"],
                complete=state["complete"],
                accepted=state["successful_snapshots"],
                segments=bounds,
                started_utc=iso(bounds[0]["started_ns"]),
                last_receipt_utc=iso(bounds[-1]["last_available_ns"]),
                quality=quality,
                support=support,
                uncertainty=uncertainty,
                errors=dict(Counter(r.get("category") for r in attempts if r["status"] == "error")),
                cold_requests=sum(not t["reused_connection"] for t in transports),
                warm_requests=sum(bool(t["reused_connection"]) for t in transports),
            )
        )
    separation = (
        (windows[1]["plan"]["created_ns"] - windows[0]["plan"]["created_ns"]) / 1e9
        if len(windows) > 1
        else None
    )
    adherence = (
        calendar_adherence(
            windows[0]["plan"]["created_ns"],
            windows[1]["plan"]["created_ns"],
            read(root / "plan.json")["plan"]["corpus"]["minimum_start_separation_seconds"],
        )
        if len(windows) > 1
        else dict(met=False, reason="second window absent")
    )
    return dict(
        stage="D42",
        seed=None,
        plan=read(root / "plan.json"),
        diagnostic=trials,
        diagnostic_decision=read(diagnostic / "decision.json"),
        windows=windows,
        start_separation_seconds=separation,
        calendar_plan_adherence=adherence,
        accepted_corpus_snapshots=sum(w["accepted"] for w in windows),
        summed_observed_segment_span_seconds=sum(
            w["quality"]["summed_segment_span_seconds"] for w in windows
        ),
        usable_rows=sum(w["quality"]["usable_features"] for w in windows),
        training_ready=False,
        execution_qualified=False,
        edge_established=False,
        split_limitation="Per-window engineering cutoffs; no global train/test split or holdout",
        calendar_limitation="Minute-scale windows in one session; not independent regimes",
        corpus_manifest_sha256=digest((root / "manifest.json").read_bytes()),
        diagnostic_manifest_sha256=digest((diagnostic / "manifest.json").read_bytes()),
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("--diagnostic", type=Path, default=Path("work/cold-connection-01"))
    a = p.parse_args()
    print(json.dumps(report(a.root, a.diagnostic), indent=2))


if __name__ == "__main__":
    main()
