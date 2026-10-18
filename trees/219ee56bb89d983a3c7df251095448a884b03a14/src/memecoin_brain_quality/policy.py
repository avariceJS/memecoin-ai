"""Durable, hash-linked quarantine transitions; probe reservations survive crashes."""

from __future__ import annotations

import time
from pathlib import Path

from memecoin_brain.source import NS, bars
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import digest, verify
from memecoin_runner.ledger import publish

SCHEMA_ERRORS = {"duplicate_bar_in_response", "invalid_OHLCV", "bar_schema_or_alignment"}
HOUR = 3600 * NS


def inspect_capture(path: Path, pool: str) -> dict:
    verify(path)  # Integrity errors can never become a local schema exclusion.
    try:
        rows = bars(path, pool)
    except ValueError as exc:
        if str(exc) not in SCHEMA_ERRORS:
            raise
        return dict(status="schema_quarantine", reason=str(exc), rows=0)
    receipt = read(path / "receipt.json")
    age = receipt["received_ns"] / NS - max((r["open_s"] + 300 for r in rows), default=0)
    return dict(
        status="valid_fresh" if rows and 0 <= age <= 600 else "valid_stale_or_empty",
        rows=len(rows),
        latest_nominal_age_seconds=age if rows else None,
    )


def states(root: Path) -> tuple[dict, str]:
    seed = read(root / "quality-seed.json")
    if sha(root / "quality-seed.json") != read(root / "activation.json")["quality_seed_sha"]:
        raise ValueError("quality_seed_binding")
    state = {p: dict(v) for p, v in seed["pools"].items()}
    head, last_ns = digest(seed), seed["created_ns"]
    for i, path in enumerate(sorted((root / "quality").glob("*.json"))):
        e = read(path)
        checksum = e.pop("sha256")
        if (
            path.name != f"{i:06d}.json"
            or e["previous"] != head
            or digest(e) != checksum
            or e["at_ns"] < last_ns
        ):
            raise ValueError("quality_ledger_integrity")
        s = state[e["pool"]]
        action = e["action"]
        if action == "quarantine":
            s.update(status="quarantined", streak=0, pending=None, next_probe_ns=e["at_ns"] + HOUR)
        elif action == "reserve_probe":
            if s["status"] != "quarantined" or e["at_ns"] < s["next_probe_ns"] or s["probes"] >= 3:
                raise ValueError("invalid_probe_reservation")
            if s["pending"] is not None:
                s["streak"] = 0  # Previous reserved probe was interrupted, not successful.
            s.update(probes=s["probes"] + 1, pending=checksum, next_probe_ns=e["at_ns"] + HOUR)
        elif action in ("probe_valid", "probe_failed"):
            if s["pending"] != e["probe"]:
                raise ValueError("probe_outcome_without_reservation")
            s["pending"] = None
            s["streak"] = s["streak"] + 1 if action == "probe_valid" else 0
            if s["streak"] >= 2:
                s["status"] = "active"
        else:
            raise ValueError("unknown_quality_transition")
        head, last_ns = checksum, e["at_ns"]
    return state, head


def event(root: Path, pool: str, action: str, *, now_ns: int | None = None, **evidence) -> str:
    current, head = states(root)
    if pool not in current:
        raise ValueError("unadmitted_quality_pool")
    row = dict(
        previous=head,
        at_ns=time.time_ns() if now_ns is None else now_ns,
        pool=pool,
        action=action,
        **evidence,
    )
    checksum = digest(row)
    folder = root / "quality"
    publish(folder / f"{len(list(folder.glob('*.json'))):06d}.json", dict(**row, sha256=checksum))
    states(root)  # Reject illegal transitions before any subsequent request.
    return checksum


def due(state: dict, now_ns: int) -> bool:
    return state["status"] == "active" or (state["probes"] < 3 and now_ns >= state["next_probe_ns"])


def blocked(root: Path) -> set[str]:
    return {p for p, s in states(root)[0].items() if s["status"] == "quarantined"}


def observe(root: Path, pool: str, capture: Path, probe: str | None = None) -> dict:
    result = inspect_capture(capture, pool)
    evidence = dict(capture_sha=sha(capture / "manifest.json"), capture=str(capture.resolve()))
    if probe is not None:
        event(
            root,
            pool,
            "probe_valid" if result["status"] == "valid_fresh" else "probe_failed",
            probe=probe,
            result=result,
            **evidence,
        )
    elif result["status"] == "schema_quarantine":
        event(root, pool, "quarantine", result=result, **evidence)
    return result
