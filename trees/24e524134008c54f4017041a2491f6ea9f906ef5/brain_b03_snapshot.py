"""Seal B03 before/after coverage and mature paired shadow; no network or fitting."""

import argparse
import time
from pathlib import Path

from memecoin_brain.data import build, save_dataset
from memecoin_brain_quality.policy import states
from memecoin_brain_scale.audit import coverage, merge, select_captures, shadow_snapshot
from memecoin_ml.data import read, sha, write
from memecoin_registry.contracts import seal


def run(base: Path, output: Path):
    primary = base / "brain-b03/prospective01"
    parent = base / "brain-b02/prospective01"
    output.mkdir(exist_ok=False)
    as_of = time.time_ns()
    universe = read(primary / "universe.json")
    plan = read(base / "brain-b02/history01/plan.json")
    assert sha(primary / "universe.json") == sha(parent / "universe.json")
    assert sha(primary / "universe.json") == plan["primary_universe_sha"]
    pools = {p["pool"] for p in universe["pools"]}
    inventories, reports, all_rows = {}, {}, {}
    for name, root in (("before", parent), ("after", primary)):
        rejected = []
        captures = select_captures([root], pools, as_of, rejected)
        rows, provenance = merge(captures, as_of)
        inventories[name] = dict(provenance=provenance, rejected=rejected)
        all_rows[name] = rows
        reports[name] = coverage(rows, universe, plan, provenance)
    rows = all_rows["after"]
    provenance = inventories["after"]["provenance"]
    floor = plan["anchor_s"] - 10 * 86400
    provenance.update(universe_sha=sha(primary / "universe.json"), window_floor_s=floor)
    filtered = [r for r in rows if r["open_s"] >= floor]
    bundle = build(filtered, provenance)
    reports["dataset"] = dict(
        samples=len(bundle["samples"]),
        bars=len(filtered),
        censored_targets=sum(
            t["log_return"] is None for s in bundle["samples"] for t in s["targets"]
        ),
    )
    reports["as_of_ns"] = as_of
    reports["quality_states"] = states(primary)
    reports["quarantine"] = inventories["after"]["rejected"]
    write(output / "coverage.json", reports)
    write(output / "inventories.json", inventories)
    write(output / "rows.json", rows)
    write(output / "universe.json", universe)
    save_dataset(output / "dataset", bundle)
    shadow_snapshot(primary, output / "shadow", rows, as_of)
    seal(output)
    print(reports["dataset"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    run(Path(__file__).resolve().parents[1] / "work", args.output)
