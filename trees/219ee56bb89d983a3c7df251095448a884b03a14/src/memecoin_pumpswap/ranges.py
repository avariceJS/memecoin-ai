"""Append-only bounded pagination progress. Reaching an anchor is not provider completeness."""

from pathlib import Path

from memecoin_ml.data import sha
from memecoin_registry.contracts import digest, verify


def progress(ledger):
    events = ledger.audit()
    pages = [e["payload"] for e in events if e["kind"] == "page"]
    return dict(
        pages=len(pages),
        cursor=pages[-1]["next_cursor"] if pages else None,
        anchor_reached=any(p["anchor_reached"] for p in pages),
        history_complete=False,
        online_backfill_allowed=False,
    )


def append_page(ledger, raw_path: Path, rows: list, requested_cursor, next_cursor):
    """A page enters progress only after immutable raw has been sealed."""
    verify(raw_path)
    state = progress(ledger)
    if state["pages"] >= ledger.binding["max_pages"]:
        raise ValueError("page_budget_exhausted")
    if state["pages"] and requested_cursor != state["cursor"]:
        raise ValueError("cursor_binding")
    if state["pages"] and state["cursor"] is None:
        raise ValueError("pagination_already_exhausted")
    if not state["pages"] and requested_cursor is not None:
        raise ValueError("unexpected_initial_cursor")
    if next_cursor is not None and (
        not isinstance(next_cursor, str) or not next_cursor or len(next_cursor) > 1024
    ):
        raise ValueError("cursor_schema")
    if next_cursor is not None and (not rows or next_cursor == requested_cursor):
        raise ValueError("cursor_no_progress")
    seen = {r["signature"]: r for e in ledger.events for r in e["payload"]["rows"]}
    previous = next(
        (e["payload"]["rows"][-1] for e in reversed(ledger.events) if e["payload"]["rows"]), None
    )
    normalized = []
    for tx in rows:
        signature = tx["transaction"]["signatures"][0]
        slot, index = tx["slot"], tx["transactionIndex"]
        if type(slot) is not int or type(index) is not int or min(slot, index) < 0:
            raise ValueError("position_schema")
        row = dict(signature=signature, slot=slot, index=index, semantic_sha=digest(tx))
        if signature in seen and seen[signature] != row:
            raise ValueError("transaction_revision")
        if previous and (slot, index) > (previous["slot"], previous["index"]):
            raise ValueError("descending_order")
        if (
            previous
            and (slot, index) == (previous["slot"], previous["index"])
            and signature != previous["signature"]
        ):
            raise ValueError("position_collision")
        normalized.append(row)
        seen[signature] = row
        previous = row
    unique_new = {r["signature"] for r in normalized} - {
        r["signature"] for e in ledger.events for r in e["payload"]["rows"]
    }
    if state["pages"] and rows and not unique_new:
        raise ValueError("page_no_new_records")
    return ledger.append(
        "page",
        raw_path=str(raw_path.resolve()),
        manifest_sha=sha(raw_path / "manifest.json"),
        requested_cursor=requested_cursor,
        next_cursor=next_cursor,
        rows=normalized,
        anchor_reached=any(
            r["signature"] == ledger.binding["anchor_signature"] for r in normalized
        ),
    )
