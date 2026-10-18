"""Bounded window adapter around the unchanged prospective corpus engine."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from memecoin_ai.execution_targets import export_execution_targets, verify_execution_targets
from memecoin_ai.prospective import create_campaign, resume_campaign, verify_campaign
from memecoin_ai.sources import Capture, ReadOnlyRPC
from memecoin_registry.contracts import inventory
from memecoin_sampling.contract import NS

from .scheduler import durable

Row = dict[str, Any]


def collect_window(
    root: Path,
    references: list[Capture],
    client: ReadOnlyRPC,
    *,
    data_kind: str,
    clock: Callable[[], int] = time.time_ns,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Row:
    """Must run under a killable parent wall deadline in production.

    Qualification fixture calls this directly with a deterministic clock. The D49
    package intentionally has no long-term service or activation API.
    """
    if data_kind not in ("prospective", "synthetic_fixture"):
        raise ValueError("unknown_data_kind")
    reservation = json.loads((root / "reservation.json").read_text())
    if reservation["reserved_rpc"] != 180:
        raise ValueError("window_reservation_changed")
    start = reservation["started_ns"]
    create_campaign(
        root / "campaign",
        references,
        source_alias="calendar-alchemy",
        endpoint_origin="https://solana-mainnet.g.alchemy.com",
        transport_profile="stdlib_persistent_v1",
        segments=2,
        rounds=30,
        interval_seconds=5,
        clock=clock,
    )
    status, reason = "completed", None
    for _ in range(2):
        if not start <= clock() < start + (600 - 210) * NS:
            status, reason = "failed", "window_deadline_or_clock"
            break
        state = resume_campaign(
            root / "campaign", client, clock=clock, monotonic=monotonic, sleep=sleep
        )
        if not state["segments"][-1]["report"]["complete"]:
            status, reason = "failed", "first_segment_error_no_resume"
            break
    if status == "completed":
        state = verify_campaign(root / "campaign")
        if not state["complete"] or state["successful_snapshots"] != 180:
            raise ValueError("incomplete_window")
        export_execution_targets(root / "campaign", root / "campaign-targets")
        verify_execution_targets(root / "campaign", root / "campaign-targets")
    if not start <= clock() <= start + 600 * NS:
        status, reason = "failed", "window_deadline_or_clock"
    outcome = dict(
        index=reservation["index"],
        status=status,
        reason=reason,
        data_kind=data_kind,
        finished_ns=clock(),
        corpus_exit_code=0 if status == "completed" else 1,
        execution_or_collection_exit_code=0 if status == "completed" else 1,
        qa_exit_code=0 if status == "completed" else 1,
    )
    durable(root / "seal.json", dict(outcome=outcome, files=inventory(root)))
    return outcome
