"""Seal actual bounded qualification, failure history and offline reproduction. No network."""

import json
import subprocess
import time
from collections import Counter
from pathlib import Path

from memecoin_continuous.store import connect, view
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE / "work/brain-b08"
SOURCE = ROOT / "source01"


def main():
    output = ROOT / "evidence-final"
    output.mkdir()
    q = read(SOURCE / "qualification-v2.json")
    rows, _ = view(SOURCE, q["cutoff_ns"])
    coverage = []
    health = read(SOURCE / "health.json")
    for p in read(SOURCE / "universe.json")["pools"]:
        bars = [r for r in rows if r["pool"] == p["pool"]]
        coverage.append(
            dict(
                pool=p["pool"],
                dex=p["dex"],
                admitted_ns=p["admitted_ns"],
                bars=len(bars),
                earliest_open_s=min(r["open_s"] for r in bars),
                latest_open_s=max(r["open_s"] for r in bars),
                quality=health["pools"][p["pool"]]["status"],
                fresh_at_qualification=p["pool"] in q["fresh"],
            )
        )
    lanes, failures = Counter(), []
    captures = []
    for p in sorted((SOURCE / "requests").iterdir()):
        request = read(p / "request.json")
        failed = (p / "failure.json").exists()
        lanes[request["lane"] + ("_failed" if failed else "_success")] += 1
        if failed:
            failures.append(dict(capture=str(p), **read(p / "failure.json")))
        captures.append(dict(path=str(p), manifest_sha=sha(p / "manifest.json")))
    changed, repeated = set(), 0
    first = {(r["pool"], r["open_s"]): r["ohlcv"] for r in rows}
    with connect(SOURCE / "index.sqlite") as db:
        for (payload,) in db.execute(
            "select payload from versions where available<=?", (q["cutoff_ns"],)
        ):
            r = json.loads(payload)
            key = (r["pool"], r["open_s"])
            if first[key] != r["ohlcv"]:
                changed.add(key)
                repeated += 1
    evidence = dict(
        stage="B08",
        created_ns=time.time_ns(),
        code_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=BASE, text=True
        ).strip(),
        verdict="activation_rejected_freshness_gate",
        activated=False,
        qualifier_v1=read(SOURCE / "qualification.json"),
        qualifier_v2=q,
        source_pin=read(SOURCE / "deployment.json")["pin"],
        config=read(SOURCE / "config.json"),
        config_sha=sha(SOURCE / "config.json"),
        universe_sha=sha(SOURCE / "universe.json"),
        limits=dict(
            qualification_get=32,
            total_discovery_get=4,
            total_get=36,
            paid_usd=0,
            rpc_requests=0,
            new_fits=0,
            post_qualification_get=0,
        ),
        transport=dict(
            lanes=dict(lanes),
            failures=failures,
            inherited_rate_events=read(SOURCE / "provider-seed.json")["rate_events"],
            initial_discovery_success=3,
            initial_discovery_429=1,
            B04_separate_429_capture=str(BASE / "work/brain-b04/prospective01/requests/000441"),
            B04_STOP=read(BASE / "work/brain-b04/prospective01/STOP"),
            source_STOP=read(SOURCE / "STOP"),
        ),
        coverage=coverage,
        source_captures=captures,
        revised_pool_candles=len(changed),
        version_rows_different_from_first=repeated,
        revision_scope=(
            "imported_B04_plus_B08; values immutable, not all version rows are revisions"
        ),
        benchmark=read(ROOT / "index-qualification-v2.json"),
        reproduction=read(ROOT / "reproduction.json"),
        paper_replay=read(ROOT / "replay-final/result.json"),
        paper_snapshot_sha=sha(ROOT / "replay-final/manifest.json"),
        installed_wheel=read(ROOT / "wheel-reproduction.json"),
        ui=read(ROOT / "ui-verification.json"),
        checks=dict(
            base_pass=616,
            optional_skips=6,
            combined_ml_pass=642,
            focused_pass=28,
            check=True,
            smoke=True,
            build=True,
        ),
        limitations=[
            "No weekly or monthly uptime measured",
            "No B08 migration or activation",
            "B07 UI/accounting alive; B04 source STOP; new entries paused",
            "24 observed is not 24 fresh; frozen gate failed 5<6",
            "History target 90d not achieved; discovery/current-universe sampling bias",
            "Full ledger replay scaling remains unqualified for weeks",
            "No causal availability backdating, no execution calibration, no edge",
        ],
    )
    publish(output / "result.json", evidence)
    seal(output)
    public = dict(
        **evidence, evidence_manifest_sha=sha(output / "manifest.json"), evidence_root=str(output)
    )
    publish(BASE / "docs/evidence/brain-continuity-2026-10-04.json", public)
    print(
        json.dumps(
            dict(
                verdict=evidence["verdict"],
                lanes=dict(lanes),
                revised=len(changed),
                bars=len(rows),
                manifest=public["evidence_manifest_sha"],
            )
        )
    )


if __name__ == "__main__":
    main()
