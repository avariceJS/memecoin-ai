"""One cycle keeps every admitted pool in its denominator, including skipped quarantine."""

import time
from pathlib import Path

from memecoin_brain.source import SourceFailure, ohlcv
from memecoin_brain_scale.audit import safe_corpus
from memecoin_ml.data import read, sha
from memecoin_runner.ledger import publish

from .policy import blocked, due, event, observe, states


def stop(root: Path, reason: str) -> None:
    if not (root / "STOP").exists():
        publish(root / "STOP", dict(reason=reason, at_ns=time.time_ns()))


def collect(root: Path, output: Path, *, fetch=None, shadow=None, model=None) -> dict:
    if fetch is None:
        fetch = ohlcv
    if shadow is None:
        from memecoin_brain.shadow import tick

        shadow = tick
    universe = read(root / "universe.json")["pools"]
    directory = output / "pools"
    directory.mkdir()
    publish(output / "denominator.json", dict(pools=[p["pool"] for p in universe]))
    statuses = []
    for u in universe:
        pool = u["pool"]
        state, _ = states(root)
        if len(blocked(root)) >= 4:
            stop(root, "four_quarantined_pools")
            raise SourceFailure("quality_universe_stop")
        row = dict(pool=pool, status="quarantine_skipped", attempted=False)
        if due(state[pool], time.time_ns()):
            probe = (
                event(root, pool, "reserve_probe")
                if state[pool]["status"] == "quarantined"
                else None
            )
            try:
                cap = fetch(root, pool, lane="prospective", limit=100)
                verdict = observe(root, pool, cap, probe)
                row.update(
                    verdict,
                    attempted=True,
                    capture=str(cap.resolve()),
                    capture_sha=sha(cap / "manifest.json"),
                    recovery_probe=probe,
                )
            except Exception as exc:
                publish(
                    directory / f"{pool}.json",
                    dict(
                        pool=pool,
                        status="global_failure",
                        attempted=True,
                        error_type=type(exc).__name__,
                    ),
                )
                raise
        publish(directory / f"{pool}.json", row)
        statuses.append(row)
    excluded = blocked(root)
    if len(excluded) >= 4:
        stop(root, "four_quarantined_pools")
        raise SourceFailure("quality_universe_stop")
    rows, provenance = safe_corpus(root, time.time_ns())
    result = dict(
        denominator=len(universe),
        statuses=statuses,
        attempted=sum(r["attempted"] for r in statuses),
        fresh=sum(r["status"] == "valid_fresh" for r in statuses),
        quarantined_pools=sorted(excluded),
        quality_head=states(root)[1],
        bars=len(rows),
        pools=len({r["pool"] for r in rows}),
        revisions=len(provenance["revisions"]),
        rejected_captures=provenance["quarantined"],
        shadow=shadow(
            root, rows, provenance, model, prediction_pools={p["pool"] for p in universe} - excluded
        ),
    )
    publish(output / "collection.json", result)
    return result
