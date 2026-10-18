"""Availability logging is distinct from forecast freshness and research readiness."""

from pathlib import Path

from memecoin_brain.source import SourceFailure, bars
from memecoin_brain_quality.collection import collect as original_collect
from memecoin_brain_quality.collection import stop
from memecoin_ml.data import read
from memecoin_runner.ledger import publish


def progress(root: Path, output: Path, report: dict) -> dict:
    pools = {p["pool"] for p in read(root / "universe.json")["pools"]}
    if (
        report["denominator"] != len(pools)
        or len(report["statuses"]) != len(pools)
        or {r["pool"] for r in report["statuses"]} != pools
    ):
        raise ValueError("availability_denominator")
    prior = read(root / "progress-seed.json")
    for cycle in sorted((root / "cycles").iterdir()):
        if cycle == output:
            continue
        if (cycle / "availability.json").exists():
            prior = read(cycle / "availability.json")
    latest = dict(prior["last_closes"])
    advanced = []
    valid = 0
    for item in report["statuses"]:
        if item["status"] not in ("valid_fresh", "valid_stale_or_empty"):
            continue
        valid += 1
        observed = bars(Path(item["capture"]), item["pool"])
        if not observed:
            continue
        close = max(r["open_s"] + 300 for r in observed)
        if close > latest.get(item["pool"], 0):
            advanced.append(item["pool"])
            latest[item["pool"]] = close
    stagnant = 0 if advanced else prior["stagnant_cycles"] + 1
    result = dict(
        denominator=len(pools),
        valid_captures=valid,
        fresh=report["fresh"],
        last_closes=latest,
        advanced_pools=advanced,
        stagnant_cycles=stagnant,
        inactivity_proven=False,
        training_enabled=False,
        qualifier="collector-only; not B03 fresh-universe or research readiness",
    )
    publish(output / "availability.json", result)
    if not valid:
        stop(root, "no_valid_acquisition_support")
        raise SourceFailure("no_valid_acquisition_support")
    if stagnant >= 6:
        stop(root, "six_cycles_no_event_progress")
        raise SourceFailure("six_cycles_no_event_progress")
    return result


def collect(root: Path, output: Path, **kwargs) -> dict:
    report = original_collect(root, output, **kwargs)
    progress(root, output, report)
    return report
