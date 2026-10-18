"""Bounded finalized block-range coverage, independent of address-index pagination."""

from __future__ import annotations

import json
from typing import Any

from memecoin_ai.collection import (
    PilotCollector,
    PilotStore,
    block_signatures,
    captured_transaction,
    scan_addresses,
)
from memecoin_ai.domain import ValidationError, integer
from memecoin_ai.sources import Capture, SourceError


def listed_slots(cap: Capture, start: int, end: int) -> list[int]:
    req = json.loads(cap.request_json)
    slots = cap.result()
    if (
        cap.method != "getBlocks"
        or req["params"] != [start + 1, end, {"commitment": "finalized"}]
        or not isinstance(slots, list)
        or not 0 < end - start <= 256
    ):
        raise ValidationError("invalid bounded block enumeration")
    for slot in slots:
        integer(slot, "listed block slot", start + 1)
        if slot > end:
            raise ValidationError("listed block outside interval")
    if not slots or slots[-1] != end:
        raise ValidationError("block enumeration omits the known endpoint bank")
    if slots != sorted(set(slots)):
        raise ValidationError("duplicate/unordered block enumeration")
    # No slot is silently assumed skipped: omissions remain conditional on this provider's list.
    return slots


def recover_ranges(collector: PilotCollector) -> None:
    store, db = collector.store, collector.store.db
    for pool in store.spec()["pools"]:
        rows = db.execute(
            "SELECT * FROM checkpoints WHERE pool=? ORDER BY slot", (pool,)
        ).fetchall()
        for left, right in zip(rows, rows[1:], strict=False):
            try:
                if not 0 < right["slot"] - left["slot"] <= 256:
                    raise ValidationError(
                        "block-range recovery requires intervals of at most 256 slots"
                    )
                prior = db.execute(
                    "SELECT * FROM range_scans WHERE start_id=? AND end_id=?",
                    (left["id"], right["id"]),
                ).fetchone()
                if prior is None:
                    cid, _ = collector.fetch(
                        "getBlocks", [left["slot"] + 1, right["slot"], dict(commitment="finalized")]
                    )
                    listed_slots(store.capture(cid), left["slot"], right["slot"])
                    with db:
                        db.execute(
                            "INSERT INTO range_scans VALUES (?,?,?,0)",
                            (left["id"], right["id"], cid),
                        )
                else:
                    cid = prior["capture_id"]
                slots = listed_slots(store.capture(cid), left["slot"], right["slot"])
                watched = set(scan_addresses(json.loads(left["body"]), store.spec()))
                for slot in slots:
                    old = db.execute(
                        "SELECT capture_id FROM blocks WHERE slot=?", (slot,)
                    ).fetchone()
                    cap = store.capture(old[0]) if old else None
                    if (
                        cap is None
                        or json.loads(cap.request_json)["params"][1].get("transactionDetails")
                        != "full"
                    ):
                        bid, _ = collector.fetch(
                            "getBlock",
                            [
                                slot,
                                dict(
                                    encoding="jsonParsed",
                                    commitment="finalized",
                                    transactionDetails="full",
                                    rewards=False,
                                    maxSupportedTransactionVersion=1,
                                ),
                            ],
                        )
                        new = store.capture(bid)
                        if cap is not None and block_signatures(cap, slot) != block_signatures(
                            new, slot
                        ):
                            raise ValidationError("conflicting finalized block order")
                        cap = new
                    else:
                        bid = old[0]
                    selected = matching_transactions(cap, slot, watched)
                    with db:
                        db.execute("INSERT OR REPLACE INTO blocks VALUES (?,?)", (slot, bid))
                        db.executemany(
                            "INSERT OR IGNORE INTO transactions VALUES (?,?,?)",
                            [(sig, slot, bid) for sig in selected],
                        )
                with db:
                    db.execute(
                        "UPDATE range_scans SET complete=1 WHERE start_id=? AND end_id=?",
                        (left["id"], right["id"]),
                    )
            except (SourceError, ValidationError, KeyError, TypeError) as exc:
                store.issue("block-range:" + pool, type(exc).__name__ + ":" + str(exc))


def matching_transactions(cap: Capture, slot: int, watched: set[str]) -> dict[str, dict[str, Any]]:
    from memecoin_ai.collection import _parsed_block

    if json.loads(cap.request_json)["params"][1].get("transactionDetails") != "full":
        raise ValidationError("range coverage requires full blocks")
    signatures = block_signatures(cap, slot)
    result = _parsed_block(cap)
    selected = {}
    for sig, tx in zip(signatures, result["transactions"], strict=True):
        if watched & {k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"]}:
            selected[sig] = captured_transaction(cap, sig, slot)
    return selected


def range_evidence(
    store: PilotStore, left_id: int, right_id: int, left: dict[str, Any], right: dict[str, Any]
) -> tuple[dict[str, int], int] | None:
    db = store.db
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='range_scans'"
    ).fetchone():
        return None
    row = db.execute(
        "SELECT * FROM range_scans WHERE start_id=? AND end_id=?", (left_id, right_id)
    ).fetchone()
    if row is None or not row["complete"]:
        return None
    slots = listed_slots(
        store.capture(row["capture_id"]), left["context_slot"], right["context_slot"]
    )
    watched = set(scan_addresses(left, store.spec()))
    signatures = {}
    for slot in slots:
        block = db.execute("SELECT capture_id FROM blocks WHERE slot=?", (slot,)).fetchone()
        if block is None:
            raise ValidationError("completed range is missing a block")
        cap = store.capture(block[0])
        for sig, tx in matching_transactions(cap, slot, watched).items():
            stored = db.execute(
                "SELECT capture_id,slot FROM transactions WHERE signature=?", (sig,)
            ).fetchone()
            if stored is None or stored["slot"] != slot:
                raise ValidationError("range transaction missing from cache")
            cached = captured_transaction(store.capture(stored["capture_id"]), sig, slot)
            # RPC getTransaction may add fields such as transactionIndex. Compare shared evidence.
            if any(
                cached.get(k) != tx.get(k) for k in ("transaction", "meta", "version", "blockTime")
            ):
                raise ValidationError("transaction evidence disagrees with full block")
            signatures[sig] = slot
    return signatures, row["capture_id"]
