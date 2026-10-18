"""Recorded B03 qualification; no network, no protected data, no model fitting."""

import shutil
import time
from pathlib import Path

from memecoin_brain_quality.collection import collect
from memecoin_brain_quality.policy import inspect_capture
from memecoin_ml.data import read, sha, write
from memecoin_registry.contracts import seal

BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / "work/brain-b02/prospective01"
OUT = BASE / "work/brain-b03/recorded01"


def main():
    universe = read(SOURCE / "universe.json")
    chosen, conflicts = {}, {}
    for cap in sorted((SOURCE / "requests").iterdir()):
        if not (cap / "receipt.json").exists():
            continue
        request = read(cap / "request.json")
        if request["lane"] == "discovery":
            continue
        pool = request["url"].split("/pools/")[1].split("/")[0]
        verdict = inspect_capture(cap, pool)
        if verdict["status"] == "schema_quarantine":
            conflicts[pool] = cap
        elif verdict["status"] == "valid_fresh":
            chosen[pool] = cap
    assert len(chosen) == 12 and 1 <= len(conflicts) <= 3
    chosen.update(conflicts)
    OUT.mkdir(exist_ok=False)
    for name in ("requests", "quality", "shadow", "models", "cycles"):
        (OUT / name).mkdir()
    seed = dict(
        created_ns=time.time_ns(),
        pools={
            u["pool"]: dict(status="active", probes=0, streak=0, pending=None, next_probe_ns=0)
            for u in universe["pools"]
        },
    )
    write(OUT / "universe.json", universe)
    write(OUT / "quality-seed.json", seed)
    write(OUT / "activation.json", dict(quality_seed_sha=sha(OUT / "quality-seed.json")))
    mapped = {}
    for i, (pool, path) in enumerate(chosen.items()):
        target = OUT / "requests" / f"{i:06d}"
        shutil.copytree(path, target)
        mapped[pool] = target
    cycle = OUT / "cycles/recorded"
    cycle.mkdir()
    report = collect(OUT, cycle, fetch=lambda r, p, **kw: mapped[p])
    assert report["denominator"] == 12 and report["fresh"] == 12 - len(conflicts)
    assert report["quarantined_pools"] == sorted(conflicts)
    assert report["shadow"]["new_predictions"] == 0
    result = dict(
        data_kind="recorded_replay_engineering",
        network_requests=0,
        fits=0,
        full_cycle_pass=True,
        report=report,
        raw={p: dict(path=str(c), sha=sha(c / "manifest.json")) for p, c in chosen.items()},
        completed_ns=time.time_ns(),
    )
    write(OUT / "result.json", result)
    seal(OUT)
    print({k: v for k, v in result.items() if k not in ("raw", "report")})


if __name__ == "__main__":
    main()
