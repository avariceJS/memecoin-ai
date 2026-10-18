"""Read-only operational evidence: immutable snapshots, exact replay, actual gaps."""

import argparse
import fcntl
import gc
import json
import shutil
import sqlite3
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path

from brain_b08_reproduce import reproduce

from memecoin_brain.shadow import evaluate_prediction
from memecoin_continuous.store import audit, stats, view
from memecoin_ml.data import read, sha
from memecoin_paper.audit import snapshot
from memecoin_paper.engine import advance, initial
from memecoin_paper.health import operational
from memecoin_registry.contracts import seal, verify
from memecoin_runner.ledger import Ledger, publish

BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / "work/brain-b09/source01"
OLD = BASE / "work/brain-b09/paper01"
PAPER = BASE / "work/brain-b10/paper01"


def main(output):
    output.mkdir()
    with (PAPER / ".paper.lock").open("a") as lock:
        deadline = time.monotonic() + 120
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        paper = snapshot(PAPER, output / "paper")
        shutil.copytree(PAPER / "soak", output / "soak")
        shutil.copytree(PAPER / "candidate-shadows", output / "candidate-shadows")
        shutil.copyfile(PAPER / "dashboard.json", output / "dashboard.json")
        cut = time.time_ns()
        health = operational(PAPER, cut)
    index = output / "source-index"
    index.mkdir()
    with sqlite3.connect(SOURCE / "index.sqlite") as src:
        with sqlite3.connect(index / "index.sqlite") as dst:
            src.backup(dst)
            dst.execute("pragma wal_checkpoint(truncate)")
    src.close()
    dst.close()
    publish(index / "seed.json", dict(parent=str(SOURCE), backup_completed_ns=time.time_ns()))
    raw = audit(index, cut)
    reproduced = reproduce(output / "paper", index)
    migration = read(PAPER / "migration.json")
    records, state = [], initial()
    with Ledger(output / "paper/ledger", read(output / "paper/ledger/binding.json")) as ledger:
        for e in ledger.events:
            state, added = advance(state, e["payload"]["tick"])
            if e["sequence"] >= migration["prefix_events"]:
                records.extend(added)
    behavior = defaultdict(Counter)
    for r in records:
        behavior[r["book"]][r["kind"]] += 1
        if r["kind"] == "exit":
            behavior[r["book"]]["net_pnl"] += float(r["net_pnl"])
    samples = [read(p) for p in sorted((output / "soak").glob("*.json"))]
    new = [s for s in samples if s["at_ns"] >= migration["created_ns"]]
    gaps = [(b["at_ns"] - a["at_ns"]) / 1e9 for a, b in zip(samples, samples[1:], strict=False)]
    b09_start = read(OLD / "migration.json")["created_ns"]
    scores, censoring = {}, Counter()
    rows, _ = view(index, cut)
    reproduced_outcomes = 0
    for lane in (output / "candidate-shadows").iterdir():
        for p in (lane / "shadow/predictions").glob("*.json"):
            pred = read(p)
            if not b09_start <= pred["predicted_ns"] <= cut:
                continue
            outcome = lane / "shadow/outcomes" / p.name
            o = read(outcome) if outcome.exists() else None
            censoring[o["status"] if o else "pending"] += 1
            if not o or o["status"] != "scored" or o["scored_ns"] > cut:
                continue
            assert pred["predicted_ns"] < o["target_available_ns"] <= o["scored_ns"]
            expected = evaluate_prediction(pred, rows, now_ns=o["scored_ns"])
            if o != dict(prediction_sha=sha(p), **expected):
                raise ValueError("matured_outcome_reproduction")
            reproduced_outcomes += 1
            key = str((pred["model_sha"], pred["horizon"]))
            g = scores.setdefault(key, dict(n=0, loss_sums={}))
            g["n"] += 1
            for name, m in o["candidate_metrics"].items():
                g["loss_sums"][name] = g["loss_sums"].get(name, 0) + m["pinball"]
    diagnostic = read(PAPER.parent / "incident-evidence/diagnostic.json")
    assert all(sha(SOURCE / k) == v for k, v in diagnostic["source_bindings"].items())
    verify(PAPER.parent / "incident-evidence")
    with sqlite3.connect(index / "index.sqlite") as db:
        coverage = [
            dict(pool=p, bars=n, earliest_open_s=a, latest_open_s=b)
            for p, n, a, b in db.execute(
                "select pool,count(*),min(open_s),max(open_s) from firsts group by pool"
            )
        ]
        downtime_received = db.execute(
            "select count(*) from captures where available>? and available<?",
            (read(OLD / "paper-health.json")["at_ns"], migration["created_ns"]),
        ).fetchone()[0]
        statuses = dict(db.execute("select status,count(*) from captures group by status"))
    db.close()
    failures = [
        dict(request=p.parent.name, **read(p))
        for p in sorted(SOURCE.glob("requests/*/failure.json"))
    ]
    incidents = [dict(file=p.name, **read(p)) for p in sorted(PAPER.glob("incident-*.json"))]
    training = [read(p / "reservation.json") for p in sorted((PAPER / "training").iterdir())]
    completed = [
        json.loads(line)
        for line in (PAPER / "paper.stdout.log").read_text().splitlines()
        if line.startswith("{")
    ]
    durations = sorted(r["elapsed_seconds"] for r in completed if r.get("category") == "completed")
    result = dict(
        stage="B10",
        cutoff_ns=cut,
        code_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=BASE, text=True
        ).strip(),
        original_incident=read(OLD / "incident-paper-1791120945079608000.json"),
        original_stop_preserved=(OLD / "STOP").exists(),
        cause="unknown_guard_exception",
        diagnostic=diagnostic,
        incident_evidence_sha=sha(PAPER.parent / "incident-evidence/manifest.json"),
        migration=migration,
        paper_pin=read(PAPER / "deployment.json")["pin"],
        source_pin=read(SOURCE / "deployment.json")["pin"],
        source_bindings_unchanged=True,
        source_config=read(SOURCE / "config.json"),
        source_stats=stats(index),
        source_audit=raw,
        source_capture_statuses=statuses,
        coverage=coverage,
        captures_received_during_paper_stop=downtime_received,
        source_failures=failures,
        paper_snapshot=paper,
        reproduction=reproduced,
        post_recovery_behavior={k: dict(v) for k, v in behavior.items()},
        b09_onward_paired_scores=scores,
        b09_onward_forecast_statuses=dict(censoring),
        matured_outcomes_reproduced=reproduced_outcomes,
        scoring_note="B09+B10 actual observations; descriptive, paired within bundle; no selection",
        operational=health,
        paper_incidents=incidents,
        completed_workers=len(durations),
        worker_seconds=dict(
            p50=durations[len(durations) // 2],
            p95=durations[min(len(durations) - 1, int(len(durations) * 0.95))],
            max=max(durations),
        )
        if durations
        else None,
        training_reservations=training,
        new_fits=0 if all(r["started_ns"] < migration["created_ns"] for r in training) else None,
        soak=dict(
            samples=len(samples),
            max_gap_s=max(gaps, default=0),
            post_recovery_samples=len(new),
            post_recovery_span_s=(new[-1]["at_ns"] - new[0]["at_ns"]) / 1e9 if new else 0,
            post_recovery_source_ok_fraction=sum(s["source_ok"] for s in new) / len(new)
            if new
            else None,
            post_recovery_one_fresh_fraction=sum(s["fresh_hot"] >= 1 for s in new) / len(new)
            if new
            else None,
            day_series=read(output / "dashboard.json")["soak"],
        ),
        separate_provider_requests=0,
        real_execution=False,
        paid_usd=0,
        F01_D43_holdout_untouched=True,
        weekly_activation=False,
        edge_established=False,
    )
    publish(output / "result.json", result)
    gc.collect()
    seal(output)
    verify(output)
    public = dict(
        **result, evidence_root=str(output), evidence_manifest_sha=sha(output / "manifest.json")
    )
    publish(BASE / "docs/evidence/brain-operational-recovery-2026-10-04.json", public)
    print(
        json.dumps(
            dict(
                soak=result["soak"],
                replay=reproduced,
                stats=result["source_stats"],
                manifest=public["evidence_manifest_sha"],
            )
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    main(parser.parse_args().output)
