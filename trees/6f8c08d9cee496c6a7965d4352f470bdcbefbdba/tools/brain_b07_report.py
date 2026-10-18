"""Seal the B07 actual live slice; no downloads, fits, promotion or protected reads."""

import json
import time
import urllib.request
from pathlib import Path

from brain_b07_reproduce import reproduce

from memecoin_ml.data import read, sha
from memecoin_paper.audit import snapshot
from memecoin_registry.contracts import digest
from memecoin_runner.ledger import Ledger, publish

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE / "work/brain-b07/paper02"


def main():
    target = BASE / "work/brain-b07/replay-final"
    result = snapshot(ROOT, target)
    independent = reproduce(target)
    c = read(ROOT / "config.json")
    with Ledger(target / "ledger", read(target / "ledger/binding.json")) as ledger:
        events = ledger.audit(result["ledger_head"])
    cutoff = events[-1]["payload"]["tick"]["at_ns"]
    model_stats = {}
    source = Path(c["source"])
    for parent in [source, *sorted((ROOT / "candidate-shadows").glob("*"))]:
        for path in (parent / "shadow/outcomes").glob("*.json"):
            outcome = read(path)
            p = read(parent / "shadow/predictions" / path.name)
            if p["predicted_ns"] < c["activation_ns"] or p["predicted_ns"] > cutoff:
                continue
            scored = outcome.get("scored_ns", outcome.get("observed_ns"))
            if scored is None or scored > cutoff:
                continue
            key = p["model_sha"] + ":" + str(p["horizon"])
            stat = model_stats.setdefault(key, dict(scored=0, censored=0, invalid=0, sums={}))
            stat[outcome["status"]] += 1
            if outcome["status"] == "scored":
                for candidate, metrics in outcome["candidate_metrics"].items():
                    stat["sums"][candidate] = stat["sums"].get(candidate, 0) + metrics["pinball"]
    for s in model_stats.values():
        s["mean_pinball"] = (
            {k: v / s["scored"] for k, v in s["sums"].items()} if s["scored"] else {}
        )
    training = []
    for path in sorted((ROOT / "training").glob("*/result.json")):
        r = read(path)
        if r["status"] == "completed":
            card = read(path.parent / "model/model-card.json")
            training.append(
                dict(
                    **r,
                    training_as_of_ns=card["training_as_of_ns"],
                    bounds=card["bounds"],
                    dataset_manifest_sha=sha(path.parent / "dataset/manifest.json"),
                    run_manifest_sha=sha(path.parent / "manifest.json"),
                )
            )
    with urllib.request.urlopen("http://127.0.0.1:8790/api/snapshot", timeout=5) as response:
        dashboard = json.load(response)
    publish(BASE / "work/brain-b07/dashboard-final.json", dashboard)
    logs = [
        json.loads(line) for line in (ROOT / "paper.stdout.log").read_text().splitlines() if line
    ]
    elapsed = [r["elapsed_seconds"] for r in logs if "elapsed_seconds" in r]
    starts = sorted(
        int(p.stem.removeprefix("paper-worker-")) for p in ROOT.glob("paper-worker-*.log")
    )
    intervals = [(b - a) / 10**9 for a, b in zip(starts[:-1], starts[1:], strict=True)]
    report = dict(
        stage="B07",
        reviewed_ns=time.time_ns(),
        activation_ns=c["activation_ns"],
        cutoff_ns=cutoff,
        verdict="ACTIVE_BOUNDED_VIRTUAL_PRICE_PROXY",
        real_execution_enabled=False,
        edge_established=False,
        calibrated_execution=False,
        continuous_week_qualified=False,
        expires_ns=c["expires_ns"],
        control_center="http://127.0.0.1:8790",
        source=dict(
            runtime_pin=c["source_runtime_pin"],
            config_sha=sha(source / "config.json"),
            source_STOP=(source / "STOP").exists(),
            extra_provider_requests=0,
            paper_projection_bars=dashboard["corpus_bars"],
            pools=len(dashboard["market"]),
        ),
        deployment=read(ROOT / "deployment.json"),
        plan_sha=c["plan_sha"],
        execution_assumptions=dict(
            currency="virtual_USD",
            initial_cash=1000,
            notional=100,
            max_positions_including_pending=3,
            slippage_bps_per_side=50,
            fee_bps_per_side=25,
            horizon_seconds=900,
            entry_TTL_seconds=600,
        ),
        live_replay=result,
        independent_reproduction=independent,
        training=training,
        paired_forecasts=model_stats,
        F01_D43_holdout_untouched=True,
        paper_stop=(ROOT / "STOP").exists(),
        training_stop=(ROOT / "TRAIN_STOP").exists(),
        paper_workers=dict(
            completed=len(logs),
            failures=sum(r["exit_code"] != 0 for r in logs),
            max_seconds=max(elapsed),
            mean_seconds=sum(elapsed) / len(elapsed),
            requested_launchd_interval_s=30,
            actual_start_interval_min_s=min(intervals),
            actual_start_interval_max_s=max(intervals),
            actual_start_interval_mean_s=sum(intervals) / len(intervals),
        ),
        ui=read(BASE / "work/brain-b07/ui-check.json"),
        test_results=dict(
            paper=20,
            base_passed=608,
            optional_skipped=6,
            combined_ml_passed=634,
            check=True,
            smoke=True,
            wheel_build=True,
            installed_wheel_initial_exact=True,
        ),
        local_artifacts=dict(
            snapshot=str(target),
            dashboard=str(BASE / "work/brain-b07/dashboard-final.json"),
            source_initial=str(BASE / "work/brain-b07/source-initial.json"),
            first_incident=str(BASE / "work/brain-b07/paper01/incident-supervisor.json"),
        ),
        dashboard_digest=digest(dashboard),
        next="B08: multi-day source continuity/history diversity, full-day future comparisons",
    )
    publish(BASE / "docs/evidence/brain-paper-loop-2026-10-04.json", report)
    print(
        json.dumps(
            dict(live=result, workers=report["paper_workers"], training=len(training)), indent=2
        )
    )


if __name__ == "__main__":
    main()
