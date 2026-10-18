"""One non-retriable historical pass in the same request budget and single-writer lock."""

import time
from pathlib import Path

from memecoin_brain.source import SourceFailure, bars, ohlcv
from memecoin_ml.data import read, sha
from memecoin_runner.ledger import publish

from .collection import stop
from .policy import blocked, observe


def run(root: Path, output: Path, *, fetch=None) -> dict:
    fetch = ohlcv if fetch is None else fetch
    binding = read(output / "binding.json")
    pending = [dict(t) for t in binding["tasks"] if t["needed"]]
    captures, finished = [], []
    started = time.monotonic()
    try:
        while pending and len(captures) < 24 and time.monotonic() - started < 560:
            if len(blocked(root)) >= 4:
                raise SourceFailure("quality_universe_stop")
            task = pending.pop(0)
            pool = task["pool"]
            if pool in blocked(root):
                finished.append(dict(**task, status="quarantine_skipped"))
                continue
            cap = fetch(root, pool, lane="historical", before=task["cursor"] - 1)
            verdict = observe(root, pool, cap)
            captures.append(
                dict(
                    path=str(cap.resolve()),
                    manifest_sha=sha(cap / "manifest.json"),
                    pool=pool,
                    **verdict,
                )
            )
            if verdict["status"] == "schema_quarantine":
                finished.append(dict(**task, status="schema_quarantine"))
                continue
            rows = bars(cap, pool)
            if any(r["open_s"] > task["cursor"] for r in rows):
                raise SourceFailure("historical_page_after_cursor")
            older = [r["open_s"] for r in rows if r["open_s"] < task["cursor"]]
            if not older:
                finished.append(dict(**task, status="provider_no_older_rows"))
            else:
                task["cursor"] = min(older)
                if task["cursor"] <= task["floor"]:
                    finished.append(dict(**task, status="floor_reached"))
                else:
                    pending.append(task)
            print(dict(history_page=captures[-1], pending_pools=len(pending)), flush=True)
    except Exception as exc:
        failure = dict(
            category=str(exc) if isinstance(exc, SourceFailure) else "history_integrity",
            error_type=type(exc).__name__,
            at_ns=time.time_ns(),
        )
        publish(output / "failure.json", failure)
        stop(root, failure["category"])
        raise
    result = dict(
        captures=captures,
        completed=finished,
        pending=pending,
        status="completed" if not pending else "bounded_partial",
        elapsed_seconds=time.monotonic() - started,
    )
    publish(output / "history.json", result)
    return result
