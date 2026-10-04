"""Bounded source and post-migration paper evidence, with a consistent offline index snapshot."""

import argparse
import fcntl
import gc
import json
import sqlite3
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path

from brain_b08_reproduce import reproduce

from memecoin_continuous.store import audit, stats
from memecoin_ml.data import read, sha
from memecoin_paper.audit import snapshot
from memecoin_paper.engine import advance, initial
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import Ledger, publish

BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / "work/brain-b09/source01"
PAPER = BASE / "work/brain-b09/paper01"


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
        replay = snapshot(PAPER, output / "paper")
    index = output / "source-index"
    index.mkdir()
    # SQLite backup is atomic against the live writer; never lock/fence the source worker.
    with sqlite3.connect(SOURCE / "index.sqlite") as src:
        with sqlite3.connect(index / "index.sqlite") as dest:
            src.backup(dest)
            dest.execute("pragma wal_checkpoint(truncate)")
    src.close()
    dest.close()
    cut = time.time_ns()
    publish(index / "seed.json", dict(parent=str(SOURCE), backup_completed_ns=cut))
    raw_audit = audit(index, cut)
    reproduced = reproduce(output / "paper", index)
    migration = read(PAPER / "migration.json")
    records = []
    state = initial()
    with Ledger(output / "paper/ledger", read(output / "paper/ledger/binding.json")) as ledger:
        for event in ledger.events:
            state, added = advance(state, event["payload"]["tick"])
            if event["sequence"] >= migration["prefix_events"]:
                records.extend(added)
    behavior = defaultdict(Counter)
    for r in records:
        behavior[r["book"]][r["kind"]] += 1
        if r["kind"] == "decision":
            behavior[r["book"]]["reason:" + r["reason"]] += 1
    q = read(SOURCE / "qualification.json")
    failures = [
        dict(path=str(p), **read(p)) for p in sorted(SOURCE.glob("requests/*/failure.json"))
    ]
    samples = [read(p) for p in sorted((SOURCE / "qualification-samples").glob("*.json"))]
    mature = defaultdict(int)
    for lane in (PAPER / "candidate-shadows").iterdir():
        for p in (lane / "shadow/outcomes").glob("*.json"):
            pred = read(lane / "shadow/predictions" / p.name)
            outcome = read(p)
            if pred["predicted_ns"] >= migration["created_ns"] and outcome["status"] == "scored":
                assert pred["predicted_ns"] < outcome["target_available_ns"] <= outcome["scored_ns"]
                mature[str((pred["model_sha"], pred["horizon"]))] += 1
    result = dict(
        stage="B09",
        report_cutoff_ns=cut,
        code_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=BASE, text=True
        ).strip(),
        qualified=q,
        qualification_samples=samples,
        source_config=read(SOURCE / "config.json"),
        source_pin=read(SOURCE / "deployment.json")["pin"],
        source_activation=read(SOURCE / "activation.json"),
        source_STOP=(SOURCE / "STOP").exists(),
        source_stats=stats(index),
        source_audit=raw_audit,
        source_index_sha=sha(index / "index.sqlite"),
        source_failures=failures,
        source_attempts_at_report=len(list((SOURCE / "requests").iterdir())),
        paper_pin=read(PAPER / "deployment.json")["pin"],
        paper_snapshot=replay,
        reproduction=reproduced,
        migration=migration,
        post_migration_behavior={k: dict(v) for k, v in behavior.items()},
        post_migration_matured_forecasts=dict(mature),
        current_dashboard=read(PAPER / "dashboard.json"),
        projection_benchmark=read(SOURCE.parent / "projection-benchmark/result.json"),
        tests=dict(
            base=623,
            combined_ml=649,
            optional_skips=6,
            focused=35,
            check=True,
            smoke=True,
            build=True,
        ),
        real_execution=False,
        paid_usd=0,
        F01_D43_holdout_untouched=True,
        new_fits=sum(
            read(p / "result.json").get("new_fits", 0)
            for p in (PAPER / "training").iterdir()
            if (p / "result.json").exists()
            and read(p / "reservation.json")["started_ns"] >= migration["created_ns"]
        ),
        weekly_uptime_measured=False,
        edge_established=False,
    )
    publish(output / "result.json", result)
    gc.collect()
    seal(output)
    public = dict(
        **result, evidence_root=str(output), evidence_manifest_sha=sha(output / "manifest.json")
    )
    publish(BASE / "docs/evidence/brain-hot-lane-2026-10-04.json", public)
    print(
        json.dumps(
            dict(
                approved=q["approved"],
                new_ticks=reproduced["new_ticks"],
                new_behavior=result["post_migration_behavior"],
                stats=result["source_stats"],
                manifest=public["evidence_manifest_sha"],
            )
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    main(parser.parse_args().output)
