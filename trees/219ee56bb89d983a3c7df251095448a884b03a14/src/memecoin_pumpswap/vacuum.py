"""Bounded event-oriented historical acquisition; immutable pages and charged restarts."""

import os
import time

from memecoin_ml.data import read, sha
from memecoin_registry.contracts import digest, seal, verify
from memecoin_runner.ledger import Ledger, publish

from .births import decode_births


def run(root, client, pacing=1):
    binding = read(root / "binding.json")
    attempts = root / "attempts"
    attempts.mkdir(exist_ok=True)
    with Ledger(root / "journal", binding) as ledger:
        if (root / "STOP").exists() or (root / "result.json").exists():
            raise ValueError("campaign_closed")
        pages = [e["payload"] for e in ledger.events if e["kind"] == "page"]
        reserved = [e for e in ledger.events if e["kind"] == "reserved"]
        closed = {p["reservation"] for p in pages}
        if any(e["sha256"] not in closed for e in reserved):
            publish(
                root / "STOP",
                dict(
                    category="unresolved_reservation",
                    charged_cu=len(reserved) * 100,
                    automatic_retry=False,
                ),
            )
            client.close()
            return
        seen = {s: h for page in pages for s, h in page["transactions"].items()}
        previous_call = 0
        try:
            for case_id, case in enumerate(binding["cases"]):
                done = [p for p in pages if p["case_id"] == case_id]
                if done and done[-1]["cursor"] is None:
                    continue
                cursor = done[-1]["cursor"] if done else None
                previous_position = (
                    tuple(done[-1]["last_position"]) if done and done[-1]["last_position"] else None
                )
                for _ in range(len(done), binding["pages_per_case"]):
                    if (
                        len(reserved) >= binding["max_rpc"]
                        or (len(reserved) + 1) * 100 > binding["max_cu"]
                        or time.time_ns() >= binding["expires_ns"]
                    ):
                        raise ValueError("campaign_budget_or_expiry")
                    time.sleep(max(0, pacing - (time.monotonic() - previous_call)))
                    cfg = dict(
                        transactionDetails="full",
                        encoding="jsonParsed",
                        commitment="finalized",
                        maxSupportedTransactionVersion=0,
                        limit=100,
                        sortOrder="asc",
                        filters=dict(blockTime=dict(gte=case["start"], lt=case["end"])),
                    )
                    if cursor is not None:
                        cfg["paginationToken"] = cursor
                    reservation = ledger.append(
                        "reserved",
                        case_id=case_id,
                        cursor=cursor,
                        cu=100,
                        params=[case["address"], cfg],
                    )
                    reserved.append(reservation)
                    path = attempts / f"{len(reserved) - 1:06d}"
                    path.mkdir(exist_ok=False)
                    previous_call = time.monotonic()
                    cap = client.fetch("getTransactionsForAddress", [case["address"], cfg])
                    cap.save(path / "capture")
                    for f in (path / "capture").iterdir():
                        with f.open("rb") as stream:
                            os.fsync(stream.fileno())
                    publish(path / "availability.json", dict(available_ns=time.time_ns()))
                    seal(path)
                    value = cap.result()
                    rows = value.get("data", value.get("transactions"))
                    next_cursor = value.get("paginationToken")
                    if not isinstance(rows, list) or len(rows) > 100:
                        raise ValueError("page_schema")
                    if next_cursor is not None and (
                        not isinstance(next_cursor, str)
                        or not 1 <= len(next_cursor) <= 2048
                        or next_cursor == cursor
                        or not rows
                    ):
                        raise ValueError("page_cursor_nonprogress")
                    births, statuses, hashes = [], {}, {}
                    for tx in rows:
                        sig = tx["transaction"]["signatures"][0]
                        position = (tx["slot"], tx["transactionIndex"])
                        keys = [
                            k["pubkey"] if isinstance(k, dict) else k
                            for k in tx["transaction"]["message"]["accountKeys"]
                        ]
                        if (
                            case["address"] not in keys
                            or not case["start"] <= tx["blockTime"] < case["end"]
                        ):
                            raise ValueError("page_membership")
                        if previous_position and position <= previous_position:
                            raise ValueError("page_order_or_duplicate")
                        previous_position = position
                        pin = digest(tx)
                        if sig in seen and seen[sig] != pin:
                            raise ValueError("transaction_revision")
                        if sig in hashes:
                            raise ValueError("page_duplicate")
                        hashes[sig] = pin
                        decoded = decode_births(tx)
                        statuses[decoded["status"]] = statuses.get(decoded["status"], 0) + 1
                        if sig not in seen:
                            births.extend(decoded["births"])
                    if rows and not (set(hashes) - set(seen)):
                        raise ValueError("page_no_new_information")
                    seen.update(hashes)
                    canonical = root / f"births-{len(pages):06d}.json"
                    publish(
                        canonical,
                        dict(
                            schema="pumpswap-birth-metadata-v1",
                            births=births,
                            capture=str(path.resolve()),
                            raw_sha=sha(path / "manifest.json"),
                            availability=read(path / "availability.json"),
                            historical_online_availability_known=False,
                        ),
                    )
                    payload = dict(
                        reservation=reservation["sha256"],
                        case_id=case_id,
                        requested_cursor=cursor,
                        cursor=next_cursor,
                        last_position=previous_position,
                        transactions=hashes,
                        statuses=statuses,
                        births=len(births),
                        canonical=str(canonical.resolve()),
                        canonical_sha=sha(canonical),
                        raw=str(path.resolve()),
                        raw_sha=sha(path / "manifest.json"),
                        bytes=sum(f.stat().st_size for f in (path / "capture").iterdir()),
                    )
                    ledger.append("page", **payload)
                    pages.append(payload)
                    cursor = next_cursor
                    if cursor is None:
                        break
            debt = []
            for i, case in enumerate(binding["cases"]):
                ps = [p for p in pages if p["case_id"] == i]
                debt.append(
                    dict(
                        case=case,
                        pages=len(ps),
                        cursor=ps[-1]["cursor"] if ps else None,
                        remaining_range_unknown=not ps or ps[-1]["cursor"] is not None,
                        provider_completeness_verified=False,
                    )
                )
            publish(
                root / "result.json",
                dict(
                    pages=len(pages),
                    transactions=len(seen),
                    births=sum(p["births"] for p in pages),
                    nominal_cu=100 * len(reserved),
                    bytes=sum(p["bytes"] for p in pages),
                    gap_debt=debt,
                    history_complete=False,
                    training_eligible=False,
                    journal_head=ledger.head,
                ),
            )
        except Exception as exc:
            category = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            publish(
                root / "STOP",
                dict(
                    category=category,
                    charged_cu=100 * len(reserved),
                    automatic_retry=False,
                    journal_head=ledger.head,
                ),
            )
        finally:
            client.close()


def catalogue(root, output):
    """Single-writer derived SQLite catalogue; raw/pages remain authority, never overwritten."""
    import sqlite3

    verify(root)
    output.mkdir(parents=True, exist_ok=False)
    with sqlite3.connect(output / "catalogue.sqlite") as db:
        db.execute(
            "create table births(pool text primary key, signature text, event_s integer, "
            "base_mint text, quote_mint text, canonical_sha text, payload text)"
        )
        db.execute("create index birth_date on births(event_s)")
        import json

        for path in sorted(root.glob("births-*.json")):
            for b in read(path)["births"]:
                db.execute(
                    "insert into births values (?,?,?,?,?,?,?)",
                    (
                        b["pool"],
                        b["signature"],
                        b["event_s"],
                        b["base_mint"],
                        b["quote_mint"],
                        sha(path),
                        json.dumps(b, sort_keys=True),
                    ),
                )
        db.commit()
        count = db.execute("select count(*) from births").fetchone()[0]
    publish(
        output / "provenance.json",
        dict(
            campaign=str(root.resolve()),
            manifest_sha=sha(root / "manifest.json"),
            pools=count,
            training_eligible=False,
            selection="historical_migration_authority_calendar_pages",
            bias="graduation_and_authority_coverage",
            history_complete=False,
        ),
    )
    seal(output)
    return count
