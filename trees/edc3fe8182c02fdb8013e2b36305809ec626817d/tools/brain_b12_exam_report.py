"""Seal actual future exposure and independently reproduce virtual accounting/outcomes."""

import fcntl
import json
import shutil
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

from brain_b08_reproduce import reproduce

from memecoin_brain.shadow import evaluate_prediction
from memecoin_continuous.runtime import guard_source
from memecoin_continuous.store import view
from memecoin_ml.data import read, sha
from memecoin_paper.audit import snapshot
from memecoin_paper.health import operational
from memecoin_paper.runtime import guard
from memecoin_registry.contracts import digest, seal, verify
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "work/brain-b12"
PAPER = OUT / "paper01"
CONTROL = BASE / "work/brain-b10/paper01"
SOURCE = BASE / "work/brain-b09/source01"


def main():
    evidence = OUT / "exam-evidence01"
    evidence.mkdir()
    with (PAPER / ".paper.lock").open("a") as lock:
        deadline = time.monotonic() + 60
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("exam_snapshot_lock") from None
                time.sleep(0.25)
        paper = snapshot(PAPER, evidence / "paper")
        shutil.copytree(PAPER / "candidate-shadows", evidence / "candidate-shadows")
        shutil.copyfile(PAPER / "dashboard.json", evidence / "dashboard.json")
        cut = time.time_ns()
    index = evidence / "source-index"
    index.mkdir()
    with sqlite3.connect(SOURCE / "index.sqlite") as src:
        with sqlite3.connect(index / "index.sqlite") as dst:
            src.backup(dst)
            dst.execute("pragma wal_checkpoint(truncate)")
    src.close()
    dst.close()
    publish(index / "seed.json", dict(parent=str(SOURCE), backup_completed_ns=time.time_ns()))
    independent = reproduce(evidence / "paper", index)
    # The legacy helper calls no-migration ticks "carried". B12 starts new experimental books.
    independent.pop("carried_ticks")
    independent.pop("new_ticks")
    rows, _ = view(index, cut)
    config = read(PAPER / "config.json")
    statuses = Counter()
    paired = defaultdict(dict)
    models = {}
    times = []
    exact = 0
    for lane in (evidence / "candidate-shadows").iterdir():
        for p in (lane / "shadow/predictions").glob("*.json"):
            pred = read(p)
            model = Path(pred["model_path"])
            if pred["model_sha"] not in models:
                verify(model)
                assert sha(model / "manifest.json") == pred["model_sha"]
                models[pred["model_sha"]] = read(model / "model-card.json")
            card = models[pred["model_sha"]]
            assert card["training_as_of_ns"] <= card["finished_ns"] < config["activation_ns"]
            assert (
                config["activation_ns"]
                <= pred["event_close_s"] * 10**9
                <= pred["input_available_ns"]
                <= pred["predicted_ns"]
                <= cut
            )
            assert all(
                f["available_ns"] <= pred["predicted_ns"] for f in pred["input_frames"].values()
            )
            times.append(pred["predicted_ns"])
            o = lane / "shadow/outcomes" / p.name
            outcome = read(o) if o.exists() else None
            statuses[outcome["status"] if outcome else "pending"] += 1
            if outcome and outcome["status"] == "scored":
                expected = evaluate_prediction(pred, rows, now_ns=outcome["scored_ns"])
                assert outcome == dict(prediction_sha=sha(p), **expected)
                exact += 1
            key = (
                pred["pool"],
                pred["event_close_s"],
                pred["horizon"],
                digest(pred["input_frames"]),
            )
            paired[key][pred["model_sha"]] = outcome
    group_count = sum(len(v) == 4 for v in paired.values())
    mature_count = sum(
        len(v) == 4 and all(o and o["status"] == "scored" for o in v.values())
        for v in paired.values()
    )
    b10 = guard(CONTROL)
    guard_source(SOURCE, b10["source_runtime_pin"])
    guard(PAPER)
    failures = [
        dict(request=p.parent.name, **read(p))
        for p in sorted(SOURCE.glob("requests/*/failure.json"))
    ]
    durations = [
        r["elapsed_seconds"]
        for line in (PAPER / "paper.stdout.log").read_text().splitlines()
        if line.startswith("{") and (r := json.loads(line)).get("category") == "completed"
    ]
    report = dict(
        cutoff_ns=cut,
        paper=paper,
        independent=independent,
        statuses=dict(statuses),
        paired_observation_groups=group_count,
        paired_mature_groups=mature_count,
        matured_outcomes_reproduced=exact,
        earliest_prediction_ns=min(times, default=None),
        latest_prediction_ns=max(times, default=None),
        models=list(models),
        healthy=operational(PAPER),
        control=operational(CONTROL),
        runtime_pin=read(PAPER / "deployment.json")["pin"],
        source_pin=read(SOURCE / "deployment.json")["pin"],
        control_pin=read(CONTROL / "deployment.json")["pin"],
        source_failures=failures,
        worker_seconds=dict(n=len(durations), max=max(durations, default=0)),
        config=config,
        ui=read(OUT / "ui-check.json"),
        prediction_reproduction=read(OUT / "prediction-reproduction.json"),
        separate_provider_requests=0,
        whole_future_day_measured=False,
        edge_established=False,
        champion_promoted=False,
        new_training_in_exam=0,
        control_training_reservations=len(list((CONTROL / "training").iterdir())),
    )
    publish(evidence / "result.json", report)
    seal(evidence)
    verify(evidence)
    publish(
        BASE / "docs/evidence/brain-curriculum-exam-2026-10-04.json",
        dict(
            **report,
            evidence_root=str(evidence),
            evidence_manifest_sha=sha(evidence / "manifest.json"),
        ),
    )
    print(
        json.dumps(
            {
                k: report[k]
                for k in [
                    "paper",
                    "independent",
                    "statuses",
                    "paired_observation_groups",
                    "paired_mature_groups",
                    "healthy",
                    "control",
                ]
            }
        )
    )


if __name__ == "__main__":
    main()
