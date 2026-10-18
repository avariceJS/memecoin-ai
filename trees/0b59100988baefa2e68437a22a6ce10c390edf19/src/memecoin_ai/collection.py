"""Bounded finalized polling and durable recovery for an explicitly fixed pool cohort."""

from __future__ import annotations

import copy
import fcntl
import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from memecoin_ai.domain import ValidationError, canonical, digest, integer
from memecoin_ai.history import EpochSchedule
from memecoin_ai.raydium import decode_pool, public_key
from memecoin_ai.sources import Capture, HTTPSourceError, ReadOnlyRPC, SourceError
from memecoin_ai.tokens import CLOCK, decode_supported_checkpoint


def failure_reason(error: Exception) -> str:
    return f"HTTP {error.status}" if isinstance(error, HTTPSourceError) else type(error).__name__


class BudgetExhausted(RuntimeError):
    pass


class PilotStore:
    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        self._lock = None
        if read_only:
            self.db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys=ON")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = path.with_suffix(path.suffix + ".lock").open("a")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock.close()
            raise ValidationError("another collector owns this journal") from None
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS range_scans(
                start_id INTEGER NOT NULL REFERENCES checkpoints(id),
                end_id INTEGER NOT NULL REFERENCES checkpoints(id),
                capture_id INTEGER NOT NULL REFERENCES captures(id), complete INTEGER NOT NULL,
                PRIMARY KEY(start_id,end_id));
            CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1),
                body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS captures(
                id INTEGER PRIMARY KEY, body TEXT NOT NULL,
                response BLOB NOT NULL, sha TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS issues(
                id INTEGER PRIMARY KEY, time_ns INTEGER NOT NULL,
                stage TEXT NOT NULL, reason TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS checkpoints(
                id INTEGER PRIMARY KEY, pool TEXT NOT NULL, slot INTEGER NOT NULL,
                capture_id INTEGER NOT NULL REFERENCES captures(id), body TEXT NOT NULL,
                UNIQUE(pool,slot));
            CREATE TABLE IF NOT EXISTS scans(
                id INTEGER PRIMARY KEY, pool TEXT NOT NULL, start_id INTEGER NOT NULL
                REFERENCES checkpoints(id),
                end_id INTEGER NOT NULL REFERENCES checkpoints(id), address TEXT NOT NULL,
                before_sig TEXT, boundary INTEGER NOT NULL DEFAULT 0, last_slot INTEGER,
                UNIQUE(start_id,end_id,address));
            CREATE TABLE IF NOT EXISTS pages(
                scan_id INTEGER NOT NULL REFERENCES scans(id),
                capture_id INTEGER NOT NULL REFERENCES captures(id),
                PRIMARY KEY(scan_id,capture_id));
            CREATE TABLE IF NOT EXISTS signatures(
                scan_id INTEGER NOT NULL REFERENCES scans(id), signature TEXT NOT NULL,
                slot INTEGER NOT NULL,
                PRIMARY KEY(scan_id,signature));
            CREATE TABLE IF NOT EXISTS transactions(
                signature TEXT PRIMARY KEY, slot INTEGER NOT NULL,
                capture_id INTEGER NOT NULL REFERENCES captures(id));
            CREATE TABLE IF NOT EXISTS blocks(
                slot INTEGER PRIMARY KEY, capture_id INTEGER NOT NULL REFERENCES captures(id));
        """)

    def close(self) -> None:
        self.db.close()
        if self._lock is not None:
            self._lock.close()

    def configure(
        self,
        pools: list[str],
        source: str,
        page_size: int,
        *,
        token2022: bool = False,
        dependency_history: bool = False,
    ) -> dict[str, Any]:
        if not pools or len(pools) > 4 or len(set(pools)) != len(pools):
            raise ValidationError("pilot requires 1..4 distinct fixed pools")
        for pool in pools:
            public_key(pool)
        integer(page_size, "page_size", 1)
        if page_size > 100:
            raise ValidationError("page size exceeds pilot bound")
        spec = dict(
            schema_version=1,
            pools=pools,
            source_alias=source,
            page_size=page_size,
            selection="explicit fixed cohort; no claim of birth/death universe coverage",
            interval="(start finalized bank slot, end finalized bank slot]",
            state_scope="classic SPL vault amounts; pool fee counters not reconstructed",
        )
        if token2022:
            spec.update(
                checkpoint_version=2, state_scope="allowlisted Token-2022 spendable balances"
            )
        if dependency_history:
            if not token2022:
                raise ValidationError("dependency history requires Clock checkpoints")
            spec["dependency_history"] = True
        row = self.db.execute("SELECT body FROM settings WHERE id=1").fetchone()
        if row:
            if json.loads(row[0]) != spec:
                raise ValidationError("pilot specification is immutable")
        else:
            with self.db:
                self.db.execute("INSERT INTO settings VALUES (1,?)", (canonical(spec),))
        return spec

    def spec(self) -> dict[str, Any]:
        row = self.db.execute("SELECT body FROM settings WHERE id=1").fetchone()
        if row is None:
            raise ValidationError("pilot is not initialized")
        result: dict[str, Any] = json.loads(row[0])
        return result

    def pending(self) -> dict[str, int]:
        has_ranges = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='range_scans'"
        ).fetchone()
        query = "SELECT count(*) FROM scans s WHERE boundary=0"
        if has_ranges:
            query += (
                " AND NOT EXISTS (SELECT 1 FROM range_scans r WHERE "
                "r.start_id=s.start_id AND r.end_id=s.end_id AND r.complete=1)"
            )
        scans = self.db.execute(query).fetchone()[0]
        transactions = self.db.execute(
            "SELECT count(DISTINCT s.signature) FROM signatures s LEFT JOIN transactions t "
            "USING(signature) WHERE t.signature IS NULL"
        ).fetchone()[0]
        blocks = self.db.execute(
            "SELECT count(DISTINCT t.slot) FROM transactions t LEFT JOIN blocks b "
            "USING(slot) WHERE b.slot IS NULL"
        ).fetchone()[0]
        missing_checkpoints = sum(
            self.db.execute("SELECT count(*) FROM checkpoints WHERE pool=?", (pool,)).fetchone()[0]
            < 2
            for pool in self.spec()["pools"]
        )
        return dict(
            scans=scans,
            transactions=transactions,
            blocks=blocks,
            pools_without_interval=missing_checkpoints,
        )

    def issue(self, stage: str, reason: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO issues(time_ns,stage,reason) VALUES (?,?,?)",
                (time.time_ns(), stage, reason),
            )

    def save_capture(self, capture: Capture) -> int:
        metadata = asdict(capture)
        del metadata["response"]
        with self.db:
            row = self.db.execute(
                "INSERT INTO captures(body,response,sha) VALUES (?,?,?)",
                (canonical(metadata), capture.response, digest(capture.response)),
            )
            assert row.lastrowid is not None
            return row.lastrowid

    def capture(self, capture_id: int) -> Capture:
        row = self.db.execute("SELECT * FROM captures WHERE id=?", (capture_id,)).fetchone()
        if row is None or digest(bytes(row["response"])) != row["sha"]:
            raise ValidationError("missing/corrupt pilot capture")
        return Capture(**json.loads(row["body"]), response=bytes(row["response"]))

    def checkpoint(self, capture_id: int, pool: str) -> bool:
        decoded = decode_supported_checkpoint(self.capture(capture_id), pool)
        if decoded["schema_version"] != self.spec().get("checkpoint_version", 1):
            raise ValidationError("checkpoint version disagrees with pilot specification")
        slot = decoded["context_slot"]
        previous = self.db.execute(
            "SELECT * FROM checkpoints WHERE pool=? ORDER BY slot DESC LIMIT 1", (pool,)
        ).fetchone()
        if previous:
            old = json.loads(previous["body"])
            if decoded["raw_received_ns"] < old["raw_received_ns"]:
                raise ValidationError("checkpoint receipt clock regressed")
            if slot < previous["slot"]:
                raise ValidationError("checkpoint slot regressed")
            if slot == previous["slot"]:
                new = json.loads(canonical(decoded))
                if {k: v for k, v in old.items() if k != "raw_received_ns"} != {
                    k: v for k, v in new.items() if k != "raw_received_ns"
                }:
                    raise ValidationError("conflicting finalized checkpoint")
                return False
            if old["pool"]["vaults"] != list(decoded["pool"]["vaults"]) or old["pool"][
                "mints"
            ] != list(decoded["pool"]["mints"]):
                raise ValidationError("pool identity changed")
        with self.db:
            row = self.db.execute(
                "INSERT INTO checkpoints(pool,slot,capture_id,body) VALUES (?,?,?,?)",
                (pool, slot, capture_id, canonical(decoded)),
            )
            if previous:
                for address in scan_addresses(decoded, self.spec()):
                    self.db.execute(
                        "INSERT INTO scans(pool,start_id,end_id,address) VALUES (?,?,?,?)",
                        (pool, previous["id"], row.lastrowid, address),
                    )
        return True

    def apply_page(self, scan_id: int, capture_id: int) -> None:
        scan = self.db.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
        if scan is None or scan["boundary"]:
            raise ValidationError("scan is absent/already complete")
        start = self.db.execute(
            "SELECT slot FROM checkpoints WHERE id=?", (scan["start_id"],)
        ).fetchone()[0]
        end = self.db.execute(
            "SELECT slot FROM checkpoints WHERE id=?", (scan["end_id"],)
        ).fetchone()[0]
        capture = self.capture(capture_id)
        request = json.loads(capture.request_json)
        if capture.method != "getSignaturesForAddress" or request["params"][0] != scan["address"]:
            raise ValidationError("page address/method mismatch")
        options = request["params"][1]
        if (
            options.get("before") != scan["before_sig"]
            or options.get("commitment") != "finalized"
            or type(options.get("minContextSlot")) is not int
            or options["minContextSlot"] < end
        ):
            raise ValidationError("page cursor/context mismatch")
        items = capture.result()
        if (
            not isinstance(items, list)
            or not items
            or type(options.get("limit")) is not int
            or not 1 <= options["limit"] <= 100
            or len(items) > options["limit"]
        ):
            # Empty history can mean pruning/index lag. It is NOT proof of a covered empty interval.
            raise ValidationError("empty/invalid page before lower boundary")
        seen = set()
        last_slot = scan["last_slot"]
        boundary = False
        rows = []
        for item in items:
            slot = item["slot"]
            integer(slot, "signature slot")
            sig = item["signature"]
            if (
                not isinstance(sig, str)
                or not sig
                or sig in seen
                or item.get("confirmationStatus") != "finalized"
            ):
                raise ValidationError("invalid/duplicate/nonfinalized signature")
            if last_slot is not None and slot > last_slot:
                raise ValidationError("signature page order regressed")
            if self.db.execute(
                "SELECT 1 FROM signatures WHERE scan_id=? AND signature=?", (scan_id, sig)
            ).fetchone():
                raise ValidationError("pagination repeated a signature")
            seen.add(sig)
            last_slot = slot
            if slot <= start:
                boundary = True
            if start < slot <= end:
                rows.append((scan_id, sig, slot))
        if items[-1]["signature"] == scan["before_sig"]:
            raise ValidationError("pagination made no progress")
        # Cursor and discovered work commit together; an interrupted fetch never skips a page.
        with self.db:
            self.db.executemany("INSERT INTO signatures VALUES (?,?,?)", rows)
            self.db.execute("INSERT INTO pages VALUES (?,?)", (scan_id, capture_id))
            self.db.execute(
                "UPDATE scans SET before_sig=?,boundary=?,last_slot=? WHERE id=?",
                (items[-1]["signature"], int(boundary), last_slot, scan_id),
            )


class PilotCollector:
    def __init__(
        self,
        store: PilotStore,
        client: ReadOnlyRPC,
        *,
        budget: int = 100,
        delay: float = 1.2,
        sleep: Callable[[float], None] = time.sleep,
        page_size: int | None = None,
        full_blocks: bool = False,
    ) -> None:
        integer(budget, "request budget", 1)
        if budget > 1000 or not 0 <= delay <= 10:
            raise ValidationError("pilot budget/delay out of bounds")
        if store.spec()["source_alias"] != client.source_alias:
            raise ValidationError("source alias changed")
        self.page_size = store.spec()["page_size"] if page_size is None else page_size
        integer(self.page_size, "recovery page size", 1)
        if self.page_size > 100:
            raise ValidationError("recovery page size exceeds bound")
        self.full_blocks = full_blocks
        self.store = store
        self.client = client
        self.remaining = budget
        self.delay = delay
        self.sleep = sleep
        self.page_cache = {}
        for row in store.db.execute("SELECT DISTINCT capture_id FROM pages"):
            cap = store.capture(row[0])
            self.page_cache[cap.request_json] = row[0]

    def fetch(self, method: str, params: list[object]) -> tuple[int, Any]:
        if self.remaining <= 0:
            raise BudgetExhausted("request budget exhausted; pending work retained")
        self.remaining -= 1
        self.sleep(self.delay)
        try:
            capture = self.client.fetch(method, params)
        except HTTPSourceError as exc:
            if exc.status == 429:
                # Retain the failed job; slow subsequent attempts without a hidden retry.
                self.delay = min(10.0, max(2.0, self.delay * 2))
                self.sleep(10.0)
            raise
        capture_id = self.store.save_capture(capture)
        return capture_id, capture.result()

    def checkpoint(self, pool: str) -> None:
        if self.store.spec().get("dependency_history"):
            found = False
            for row in self.store.db.execute("SELECT id FROM captures"):
                cap = self.store.capture(row[0])
                if cap.method == "getEpochSchedule":
                    EpochSchedule.from_capture(cap)
                    found = True
                    break
            if not found:
                cid, _ = self.fetch("getEpochSchedule", [])
                EpochSchedule.from_capture(self.store.capture(cid))
        previous = self.store.db.execute(
            "SELECT body FROM checkpoints WHERE pool=? ORDER BY slot DESC LIMIT 1", (pool,)
        ).fetchone()
        if previous:
            old = json.loads(previous[0])
            addresses = [pool, old["pool"]["config"], *old["pool"]["vaults"], *old["pool"]["mints"]]
            minimum = old["context_slot"]
        else:
            _, lookup = self.fetch(
                "getAccountInfo", [pool, dict(encoding="base64", commitment="finalized")]
            )
            addresses = decode_pool(lookup["value"]).addresses(pool)
            minimum = lookup["context"]["slot"]
        if self.store.spec().get("checkpoint_version") == 2:
            addresses.append(CLOCK)
        capture_id, _ = self.fetch(
            "getMultipleAccounts",
            [addresses, dict(encoding="base64", commitment="finalized", minContextSlot=minimum)],
        )
        self.store.checkpoint(capture_id, pool)

    def recover(self) -> None:
        db = self.store.db
        for scan in db.execute("SELECT * FROM scans WHERE boundary=0 ORDER BY id").fetchall():
            try:
                while True:
                    scan = db.execute("SELECT * FROM scans WHERE id=?", (scan["id"],)).fetchone()
                    if scan["boundary"]:
                        break
                    options: dict[str, Any] = dict(
                        commitment="finalized",
                        minContextSlot=db.execute(
                            "SELECT max(slot) FROM checkpoints WHERE pool=?", (scan["pool"],)
                        ).fetchone()[0],
                        limit=self.page_size,
                    )
                    if scan["before_sig"]:
                        options["before"] = scan["before_sig"]
                    request = canonical(
                        dict(
                            jsonrpc="2.0",
                            id=1,
                            method="getSignaturesForAddress",
                            params=[scan["address"], options],
                        )
                    )
                    cid = self.page_cache.get(request)
                    if cid is None:
                        cid, _ = self.fetch("getSignaturesForAddress", [scan["address"], options])
                    self.store.apply_page(scan["id"], cid)
                    self.page_cache[request] = cid
            except (SourceError, ValidationError, KeyError, TypeError) as exc:
                self.store.issue(f"scan:{scan['id']}", failure_reason(exc))
        if self.full_blocks:
            self.recover_full_blocks()
            return
        pending = db.execute(
            "SELECT DISTINCT s.signature,s.slot FROM signatures s "
            "LEFT JOIN transactions t USING(signature) WHERE t.signature IS NULL "
            "ORDER BY s.slot,s.signature"
        ).fetchall()
        for row in pending:
            try:
                cid, tx = self.fetch(
                    "getTransaction",
                    [
                        row["signature"],
                        dict(
                            encoding="jsonParsed",
                            commitment="finalized",
                            maxSupportedTransactionVersion=1,
                        ),
                    ],
                )
                if (
                    not isinstance(tx, dict)
                    or tx["slot"] != row["slot"]
                    or tx["transaction"]["signatures"][0] != row["signature"]
                    or tx.get("meta") is None
                ):
                    raise ValidationError("missing/mismatched transaction")
                version = tx.get("version")
                if version != "legacy" and (type(version) is not int or version not in (0, 1)):
                    raise ValidationError("unsupported transaction version")
                with db:
                    db.execute(
                        "INSERT INTO transactions VALUES (?,?,?)",
                        (row["signature"], row["slot"], cid),
                    )
            except (SourceError, ValidationError, KeyError, TypeError) as exc:
                self.store.issue("transaction:" + row["signature"], failure_reason(exc))
        slots = db.execute(
            "SELECT DISTINCT t.slot FROM transactions t LEFT JOIN blocks b USING(slot) "
            "WHERE b.slot IS NULL ORDER BY t.slot"
        ).fetchall()
        for row in slots:
            try:
                cid, block = self.fetch(
                    "getBlock",
                    [
                        row["slot"],
                        dict(
                            commitment="finalized",
                            transactionDetails="signatures",
                            rewards=False,
                            maxSupportedTransactionVersion=1,
                        ),
                    ],
                )
                if (
                    not isinstance(block, dict)
                    or not isinstance(block.get("signatures"), list)
                    or len(set(block["signatures"])) != len(block["signatures"])
                ):
                    raise ValidationError("missing/ambiguous block ordering")
                with db:
                    db.execute("INSERT INTO blocks VALUES (?,?)", (row["slot"], cid))
            except (SourceError, ValidationError, KeyError, TypeError) as exc:
                self.store.issue("block:" + str(row["slot"]), failure_reason(exc))

    def recover_full_blocks(self) -> None:
        db = self.store.db
        slots = db.execute(
            "SELECT DISTINCT s.slot FROM signatures s LEFT JOIN transactions t USING(signature) "
            "LEFT JOIN blocks b ON b.slot=s.slot WHERE t.signature IS NULL OR b.slot IS NULL "
            "ORDER BY s.slot"
        ).fetchall()
        for row in slots:
            slot = row[0]
            try:
                cid, _ = self.fetch(
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
                cap = self.store.capture(cid)
                signatures = block_signatures(cap, slot)
                required = [
                    r[0]
                    for r in db.execute(
                        "SELECT DISTINCT signature FROM signatures WHERE slot=?", (slot,)
                    )
                ]
                if not set(required) <= set(signatures):
                    raise ValidationError("address index signature absent from full block")
                previous = db.execute(
                    "SELECT capture_id FROM blocks WHERE slot=?", (slot,)
                ).fetchone()
                if (
                    previous
                    and block_signatures(self.store.capture(previous[0]), slot) != signatures
                ):
                    raise ValidationError("conflicting finalized block order")
                for signature in required:
                    captured_transaction(cap, signature, slot)
                with db:
                    db.execute("INSERT OR IGNORE INTO blocks VALUES (?,?)", (slot, cid))
                    db.executemany(
                        "INSERT OR IGNORE INTO transactions VALUES (?,?,?)",
                        [(sig, slot, cid) for sig in required],
                    )
            except (SourceError, ValidationError, KeyError, TypeError) as exc:
                self.store.issue("full-block:" + str(slot), failure_reason(exc))

    def poll(self, rounds: int, interval: float, *, recover: bool = True) -> None:
        integer(rounds, "rounds", 1)
        if rounds > 120 or not 0 <= interval <= 60:
            raise ValidationError("poll rounds/interval out of bounds")
        try:
            for i in range(rounds):
                if i:
                    self.sleep(interval)
                for pool in self.store.spec()["pools"]:
                    try:
                        self.checkpoint(pool)
                    except (SourceError, ValidationError, KeyError, TypeError) as exc:
                        self.store.issue("checkpoint:" + pool, failure_reason(exc))
                if recover:
                    self.recover()
        except BudgetExhausted:
            self.store.issue("budget", "request budget exhausted; resume recovery explicitly")


def scan_addresses(decoded: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    addresses = [decoded["pool_id"], *decoded["pool"]["vaults"]]
    if spec.get("dependency_history"):
        addresses += [decoded["pool"]["config"], *decoded["pool"]["mints"]]
    return list(dict.fromkeys(addresses))


def block_signatures(cap: Capture, slot: int) -> list[str]:
    req = json.loads(cap.request_json)
    result = _parsed_block(cap)
    if (
        cap.method != "getBlock"
        or req["params"][0] != slot
        or req["params"][1].get("commitment") != "finalized"
        or not isinstance(result, dict)
    ):
        raise ValidationError("invalid finalized block capture")
    mode = req["params"][1].get("transactionDetails")
    if mode == "signatures":
        signatures = result["signatures"]
    elif mode == "full" and req["params"][1].get("encoding") == "jsonParsed":
        signatures = [t["transaction"]["signatures"][0] for t in result["transactions"]]
    else:
        raise ValidationError("unsupported block encoding/details")
    if (
        not isinstance(signatures, list)
        or not all(isinstance(s, str) and s for s in signatures)
        or len(set(signatures)) != len(signatures)
    ):
        raise ValidationError("duplicate/invalid block signatures")
    return signatures


def captured_transaction(cap: Capture, signature: str, slot: int) -> dict[str, Any]:
    req = json.loads(cap.request_json)
    result = _parsed_block(cap) if cap.method == "getBlock" else cap.result()
    if not isinstance(result, dict):
        raise ValidationError("missing transaction/block result")
    if cap.method == "getBlock":
        signatures = block_signatures(cap, slot)
        if req["params"][1].get("transactionDetails") != "full" or signature not in signatures:
            raise ValidationError("transaction absent from full block")
        tx = dict(
            copy.deepcopy(result["transactions"][signatures.index(signature)]),
            slot=slot,
            blockTime=result.get("blockTime"),
        )
    elif (
        cap.method == "getTransaction"
        and req["params"][0] == signature
        and req["params"][1].get("commitment") == "finalized"
        and req["params"][1].get("encoding") == "jsonParsed"
    ):
        tx = result
    else:
        raise ValidationError("invalid transaction capture request")
    version = tx.get("version")
    if (
        tx["slot"] != slot
        or tx["transaction"]["signatures"][0] != signature
        or tx.get("meta") is None
        or (version != "legacy" and (type(version) is not int or version not in (0, 1)))
    ):
        raise ValidationError("transaction identity/version mismatch")
    return tx


@lru_cache(maxsize=2)
def _parsed_block(cap: Capture) -> dict[str, Any]:
    # Immutable raw bytes are the cache key. Bounded to two blocks, never persisted as evidence.
    result = cap.result()
    if not isinstance(result, dict):
        raise ValidationError("missing block result")
    return result
