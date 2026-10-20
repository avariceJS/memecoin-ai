import hashlib
import json
import math
import sqlite3
import time
import uuid
from collections import defaultdict

MODELS = {
    "gpt-6.1-sol": (50, 2.5, 250),
    "gpt-6-luna": (2.5, 0.25, 12.5),
    "gpt-6-astra": (250, 25, 1250),
}
FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
ZERO = dict.fromkeys(FIELDS, 0)


def _counters(value):
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        raise ValueError("counters must contain exactly the canonical fields")
    out = {}
    for key in FIELDS:
        v = value[key]
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ValueError("counters must be nonnegative integers")
        out[key] = v
    if out["total_tokens"] != out["input_tokens"] + out["output_tokens"]:
        raise ValueError("total_tokens must equal input_tokens + output_tokens")
    if (
        out["cached_input_tokens"] > out["input_tokens"]
        or out["reasoning_output_tokens"] > out["output_tokens"]
    ):
        raise ValueError("cached/reasoning counters exceed their parent counter")
    return out


def _finite_positive(v, name):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        raise ValueError(name + " must be finite and positive")
    return float(v)


def _timestamp(v, name):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(name + " must be a finite timestamp")
    return float(v)


def _json(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), allow_nan=False)


class UsageLedger:
    def __init__(self, path):
        self.path = str(path)
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 1:
            self.db.close()
            raise ValueError("usage_ledger_schema_newer_than_controller")
        known_tables = {
            "registrations",
            "events",
            "turns",
            "turn_finishes",
            "dispatches",
            "receipts",
            "receipt_conflicts",
            "outcomes",
            "rpc_ingress",
            "terminal_receipts",
            "terminal_receipt_conflicts",
            "interval_attributions",
            "external_historical_unknown",
            "external_imports",
        }
        tables = {
            row[0]
            for row in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if (version == 1 and not tables) or (tables and (
            tables - known_tables
            or not {
                "registrations",
                "events",
                "turns",
                "turn_finishes",
                "dispatches",
                "receipts",
            }
            <= tables
        )):
            self.db.close()
            raise ValueError("usage_ledger_schema_unrecognized")
        required_columns = {
            "registrations": {"identity", "epoch", "model", "baseline", "evidence", "started"},
            "events": {
                "event_id",
                "identity",
                "epoch",
                "sequence",
                "counters",
                "source",
                "observed_at",
                "event_at",
                "parent_event_id",
                "reason",
            },
            "turns": {
                "turn_id",
                "identity",
                "epoch",
                "start_counters",
                "reservation_tokens",
                "reservation_wpu",
                "deadline_at",
                "evidence",
            },
            "turn_finishes": {"turn_id", "end_counters", "receipt", "failed"},
            "dispatches": {
                "dispatch_id",
                "payload",
                "identity",
                "model",
                "estimate_tokens",
                "estimate_wpu",
                "deadline_at",
                "baseline",
                "provider_turn_id",
                "status",
                "outcome",
                "created_at",
            },
            "receipts": {
                "event_id",
                "dispatch_id",
                "provider_turn_id",
                "source",
                "receipt",
                "event_at",
                "observed_at",
            },
            "receipt_conflicts": {"conflict_id", "event_id", "payload", "observed_at"},
            "outcomes": {"event_id", "dispatch_id", "status", "receipt", "observed_at"},
            "rpc_ingress": {"ingress_id", "raw_bytes", "received_at"},
            "terminal_receipts": {
                "dispatch_id",
                "evidence_sha256",
                "evidence",
                "observed_at",
            },
            "terminal_receipt_conflicts": {
                "conflict_id",
                "dispatch_id",
                "evidence_sha256",
                "evidence",
                "observed_at",
            },
            "interval_attributions": {
                "event_id",
                "dispatch_id",
                "identity",
                "epoch",
                "model",
                "start_counters",
                "end_counters",
                "evidence",
                "created_at",
            },
            "external_historical_unknown": {
                "event_id",
                "counters",
                "collector_id",
                "evidence_sha256",
                "created_at",
            },
            "external_imports": {
                "collector_id",
                "evidence_sha256",
                "evidence_bytes",
                "imported_at",
            },
        }
        for table, required in required_columns.items():
            if table in tables:
                actual = {row[1] for row in self.db.execute(f"PRAGMA table_info({table})")}
                if not required <= actual:
                    self.db.close()
                    raise ValueError("usage_ledger_schema_unrecognized")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        try:
            self.db.executescript(
                "BEGIN IMMEDIATE;\n"
                + """
            CREATE TABLE IF NOT EXISTS registrations (
                identity TEXT NOT NULL,
                epoch TEXT NOT NULL,
                model TEXT NOT NULL,
                baseline TEXT,
                evidence TEXT NOT NULL,
                started INTEGER NOT NULL,
                PRIMARY KEY (identity, epoch)
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                identity TEXT NOT NULL,
                epoch TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                counters TEXT NOT NULL,
                source TEXT NOT NULL,
                observed_at REAL NOT NULL,
                event_at REAL,
                parent_event_id TEXT UNIQUE,
                reason TEXT,
                FOREIGN KEY (identity, epoch) REFERENCES registrations(identity, epoch)
            );
            CREATE TABLE IF NOT EXISTS turns (
                turn_id TEXT PRIMARY KEY,
                identity TEXT NOT NULL,
                epoch TEXT NOT NULL,
                start_counters TEXT NOT NULL,
                reservation_tokens REAL NOT NULL,
                reservation_wpu REAL NOT NULL,
                deadline_at REAL NOT NULL,
                evidence TEXT NOT NULL,
                FOREIGN KEY (identity, epoch) REFERENCES registrations(identity, epoch)
            );
            CREATE TABLE IF NOT EXISTS turn_finishes (
                turn_id TEXT PRIMARY KEY REFERENCES turns(turn_id),
                end_counters TEXT NOT NULL,
                receipt TEXT NOT NULL,
                failed INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dispatches (
                dispatch_id TEXT PRIMARY KEY, payload TEXT NOT NULL, identity TEXT NOT NULL,
                model TEXT NOT NULL, estimate_tokens REAL NOT NULL, estimate_wpu REAL NOT NULL,
                deadline_at REAL NOT NULL, baseline TEXT, provider_turn_id TEXT,
                status TEXT NOT NULL, outcome TEXT, created_at REAL NOT NULL,
                epoch TEXT
            );
            CREATE TABLE IF NOT EXISTS receipts (
                event_id TEXT PRIMARY KEY, dispatch_id TEXT NOT NULL,
                provider_turn_id TEXT, source TEXT NOT NULL, receipt TEXT,
                event_at REAL, observed_at REAL NOT NULL, evidence TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS receipt_conflicts (
                conflict_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, payload TEXT NOT NULL,
                observed_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outcomes (
                event_id TEXT PRIMARY KEY, dispatch_id TEXT NOT NULL,
                status TEXT NOT NULL, receipt TEXT, observed_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rpc_ingress (
                ingress_id TEXT PRIMARY KEY, raw_bytes BLOB NOT NULL,
                received_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS terminal_receipts (
                dispatch_id TEXT PRIMARY KEY, evidence_sha256 TEXT NOT NULL,
                evidence TEXT NOT NULL, observed_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS terminal_receipt_conflicts (
                conflict_id TEXT PRIMARY KEY, dispatch_id TEXT NOT NULL,
                evidence_sha256 TEXT NOT NULL, evidence TEXT NOT NULL,
                observed_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS interval_attributions (
                event_id TEXT PRIMARY KEY, dispatch_id TEXT NOT NULL,
                identity TEXT NOT NULL, epoch TEXT NOT NULL, model TEXT NOT NULL,
                start_counters TEXT NOT NULL, end_counters TEXT NOT NULL,
                evidence TEXT NOT NULL, created_at REAL NOT NULL,
                FOREIGN KEY (identity, epoch) REFERENCES registrations(identity, epoch)
            );
            CREATE TABLE IF NOT EXISTS external_historical_unknown (
                event_id TEXT PRIMARY KEY, counters TEXT NOT NULL,
                collector_id TEXT NOT NULL, evidence_sha256 TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS external_imports (
                collector_id TEXT NOT NULL, evidence_sha256 TEXT NOT NULL,
                evidence_bytes BLOB NOT NULL, imported_at REAL NOT NULL,
                PRIMARY KEY (collector_id,evidence_sha256)
            );
                """
            )
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            self.db.close()
            raise
        try:
            dispatch_columns = {row[1] for row in self.db.execute("PRAGMA table_info(dispatches)")}
            if "epoch" not in dispatch_columns:
                self.db.execute("ALTER TABLE dispatches ADD COLUMN epoch TEXT")
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(receipts)")}
            if "evidence" not in columns:
                self.db.execute(
                    "ALTER TABLE receipts ADD COLUMN evidence TEXT NOT NULL DEFAULT '{}'"
                )
            self.db.execute("PRAGMA user_version=1")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            self.db.close()
            raise

    def persist_raw_rpc(self, raw_bytes):
        if not isinstance(raw_bytes, bytes):
            raise ValueError("rpc_ingress_requires_original_bytes")
        ingress_id = uuid.uuid4().hex
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=2)
        try:
            connection.execute("PRAGMA busy_timeout=2000")
            connection.execute(
                "INSERT INTO rpc_ingress VALUES(?,?,?)",
                (ingress_id, sqlite3.Binary(raw_bytes), time.time()),
            )
        finally:
            connection.close()
        return ingress_id

    def register(self, identity, epoch, model, baseline, *, evidence, started_after_T0=False):
        if not identity or not epoch or model not in MODELS or not evidence:
            raise ValueError("identity, epoch, model, and evidence are required")
        if not isinstance(started_after_T0, bool):
            raise ValueError("started_after_T0 must be bool")
        if baseline is None:
            if started_after_T0:
                baseline = dict(ZERO)
            else:
                pass
        else:
            baseline = _counters(baseline)
        row = (
            identity,
            epoch,
            model,
            None if baseline is None else _json(baseline),
            str(evidence),
            int(started_after_T0),
        )
        self._insert_idempotent(
            "registrations",
            "identity,epoch",
            (identity, epoch),
            row,
            ("identity", "epoch", "model", "baseline", "evidence", "started"),
        )

    def observe(
        self, identity, epoch, event_id, counters, *, source, sequence, observed_at, event_at=None
    ):
        c = _counters(counters)
        if (
            not all((identity, epoch, event_id, source))
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 0
        ):
            raise ValueError("invalid observation identity/source/sequence")
        observed_at = _timestamp(observed_at, "observed_at")
        event_at = None if event_at is None else _timestamp(event_at, "event_at")
        self._require_registration(identity, epoch)
        data = (
            event_id,
            identity,
            epoch,
            sequence,
            _json(c),
            source,
            observed_at,
            event_at,
            None,
            None,
        )
        self._append_event(data)

    def correct(self, event_id, replacement_event_id, counters, *, reason, source):
        return self.correct_many(
            [
                {
                    "event_id": event_id,
                    "replacement_event_id": replacement_event_id,
                    "counters": counters,
                    "reason": reason,
                    "source": source,
                }
            ]
        )

    def correct_many(self, corrections):
        if not isinstance(corrections, list) or not corrections:
            raise ValueError("corrections must be a nonempty list")
        prepared = []
        required = {"event_id", "replacement_event_id", "counters", "reason", "source"}
        seen = set()
        for correction in corrections:
            if not isinstance(correction, dict) or set(correction) != required:
                raise ValueError("correction must contain exactly the required fields")
            event_id = correction["event_id"]
            replacement_id = correction["replacement_event_id"]
            reason, source = correction["reason"], correction["source"]
            if not event_id or not replacement_id or not reason or not source:
                raise ValueError("correction fields are required")
            if event_id in seen:
                raise ValueError("duplicate correction parent")
            seen.add(event_id)
            prepared.append(
                (
                    event_id,
                    replacement_id,
                    _json(_counters(correction["counters"])),
                    str(reason),
                    source,
                )
            )

        self.db.execute("BEGIN IMMEDIATE")
        try:
            rows_to_append = []
            affected = set()
            for event_id, replacement_id, counters_json, reason, source in prepared:
                parent = self.db.execute(
                    "SELECT * FROM events WHERE event_id=?", (event_id,)
                ).fetchone()
                if parent is None:
                    raise ValueError("unknown event")
                affected.add((parent["identity"], parent["epoch"]))
                child = self.db.execute(
                    "SELECT * FROM events WHERE parent_event_id=?", (event_id,)
                ).fetchone()
                row = (
                    replacement_id,
                    parent["identity"],
                    parent["epoch"],
                    parent["sequence"],
                    counters_json,
                    source,
                    time.time(),
                    parent["event_at"],
                    event_id,
                    str(reason),
                )
                if child:
                    expected = row[:6] + (child["observed_at"],) + row[7:]
                    actual = tuple(
                        child[key]
                        for key in (
                            "event_id",
                            "identity",
                            "epoch",
                            "sequence",
                            "counters",
                            "source",
                            "observed_at",
                            "event_at",
                            "parent_event_id",
                            "reason",
                        )
                    )
                    if actual != expected:
                        raise ValueError("event already corrected")
                    continue
                old = self.db.execute(
                    "SELECT * FROM events WHERE event_id=?", (replacement_id,)
                ).fetchone()
                if old:
                    if (
                        tuple(
                            old[key]
                            for key in (
                                "event_id",
                                "identity",
                                "epoch",
                                "sequence",
                                "counters",
                                "source",
                                "observed_at",
                                "event_at",
                                "parent_event_id",
                                "reason",
                            )
                        )
                        != row
                    ):
                        raise ValueError("event_id payload conflict")
                    continue
                rows_to_append.append(row)
            for row in rows_to_append:
                self.db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?)", row)
            for identity, epoch in affected:
                self._validate_stream(identity, epoch)
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def _append_event(self, row):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self._append_event_in_transaction(row)
            self._validate_stream(row[1], row[2])
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def _append_event_in_transaction(self, row):
        old = self.db.execute("SELECT * FROM events WHERE event_id=?", (row[0],)).fetchone()
        if old:
            old_row = tuple(
                old[k]
                for k in (
                    "event_id",
                    "identity",
                    "epoch",
                    "sequence",
                    "counters",
                    "source",
                    "observed_at",
                    "event_at",
                    "parent_event_id",
                    "reason",
                )
            )
            comparable_old = old_row[:6] + old_row[7:]
            comparable_new = row[:6] + row[7:]
            if comparable_old != comparable_new:
                raise ValueError("event_id payload conflict")
            return False
        self.db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?)", row)
        return True

    def _validate_stream(self, identity, epoch):
        rows = self.db.execute(
            "SELECT * FROM events WHERE identity=? AND epoch=? ORDER BY sequence,event_id",
            (identity, epoch),
        ).fetchall()
        positions = defaultdict(list)
        for r in rows:
            if self.db.execute(
                "SELECT 1 FROM events WHERE parent_event_id=?", (r["event_id"],)
            ).fetchone():
                continue
            positions[r["sequence"]].append(_counters(json.loads(r["counters"])))
        registration = self.db.execute(
            "SELECT baseline FROM registrations WHERE identity=? AND epoch=?",
            (identity, epoch),
        ).fetchone()
        prev = None if registration["baseline"] is None else json.loads(registration["baseline"])
        for seq in sorted(positions):
            vals = positions[seq]
            val = vals[0]
            if any(candidate != val for candidate in vals):
                prev = None
                continue
            if prev is not None:
                self._validate_interval(prev, val)
            prev = val

    @staticmethod
    def _validate_interval(before, after):
        delta = {key: after[key] - before[key] for key in FIELDS}
        if any(value < 0 for value in delta.values()):
            raise ValueError("counters decreased across sequence positions")
        _counters(delta)
        return delta

    def start_turn(
        self,
        turn_id,
        identity,
        epoch,
        counters,
        *,
        reservation_tokens,
        reservation_wpu,
        deadline_at,
        evidence,
    ):
        c = _counters(counters)
        tokens = _finite_positive(reservation_tokens, "reservation_tokens")
        wpu = _finite_positive(reservation_wpu, "reservation_wpu")
        deadline = _timestamp(deadline_at, "deadline_at")
        if not turn_id or not evidence:
            raise ValueError("turn_id and evidence are required")
        self._require_registration(identity, epoch)
        row = (turn_id, identity, epoch, _json(c), tokens, wpu, deadline, str(evidence))
        existing = self.db.execute("SELECT * FROM turns WHERE turn_id=?", (turn_id,)).fetchone()
        if existing:
            if tuple(existing) != row:
                raise ValueError("turn_id payload conflict")
            return
        self.db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?,?,?)", row)

    def finish_turn(self, turn_id, counters, *, receipt, failed=False):
        c = _counters(counters)
        if not receipt or not isinstance(failed, bool):
            raise ValueError("receipt and boolean failed are required")
        row = self.db.execute("SELECT * FROM turns WHERE turn_id=?", (turn_id,)).fetchone()
        if row is None:
            raise ValueError("unknown turn")
        end = _json(c)
        prior = self.db.execute(
            "SELECT * FROM turn_finishes WHERE turn_id=?", (turn_id,)
        ).fetchone()
        if prior:
            if (prior["end_counters"], prior["receipt"], prior["failed"]) != (
                end,
                receipt,
                int(failed),
            ):
                raise ValueError("conflicting finish")
            return
        start = json.loads(row["start_counters"])
        registration = self.db.execute(
            "SELECT baseline FROM registrations WHERE identity=? AND epoch=?",
            (row["identity"], row["epoch"]),
        ).fetchone()
        baseline = registration["baseline"]
        if baseline is not None:
            self._validate_interval(json.loads(baseline), start)
        self._validate_interval(start, c)
        self.db.execute(
            "INSERT INTO turn_finishes VALUES(?,?,?,?)", (turn_id, end, str(receipt), int(failed))
        )

    def _require_registration(self, identity, epoch):
        if not self.db.execute(
            "SELECT 1 FROM registrations WHERE identity=? AND epoch=?", (identity, epoch)
        ).fetchone():
            raise ValueError("identity epoch is not registered")

    def _insert_idempotent(self, table, keycols, keys, row, columns):
        where = " AND ".join(k + "=?" for k in keycols.split(","))
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute("SELECT * FROM " + table + " WHERE " + where, keys).fetchone()
            if old:
                if tuple(old[c] for c in columns) != row:
                    raise ValueError("registration conflict")
            else:
                self.db.execute(
                    "INSERT INTO " + table + " VALUES(" + ",".join("?" for _ in row) + ")", row
                )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def summary(self):
        owns_transaction = not self.db.in_transaction
        if owns_transaction:
            self.db.execute("BEGIN")
        succeeded = False
        try:
            result = self._summary_snapshot()
            succeeded = True
            return result
        finally:
            if owns_transaction and self.db.in_transaction:
                self.db.execute("COMMIT" if succeeded else "ROLLBACK")

    def _summary_snapshot(self):
        totals = dict(ZERO)
        by_model = {}
        unresolved = []
        registration_coverage = {}
        attributed = {
            row["event_id"]: row for row in self.db.execute("SELECT * FROM interval_attributions")
        }
        regs = self.db.execute("SELECT * FROM registrations ORDER BY identity,epoch").fetchall()
        for r in regs:
            rows = self.db.execute(
                "SELECT * FROM events WHERE identity=? AND epoch=? ORDER BY sequence,event_id",
                (r["identity"], r["epoch"]),
            ).fetchall()
            positions = defaultdict(list)
            for e in rows:
                if self.db.execute(
                    "SELECT 1 FROM events WHERE parent_event_id=?", (e["event_id"],)
                ).fetchone():
                    continue
                positions[e["sequence"]].append(json.loads(e["counters"]))
            ambiguous = any(any(v != vals[0] for v in vals) for vals in positions.values())
            if r["baseline"] is None or ambiguous:
                unresolved.append(
                    {
                        "identity": r["identity"],
                        "epoch": r["epoch"],
                        "reason": "baseline_unknown"
                        if r["baseline"] is None
                        else "ambiguous_position",
                    }
                )
                continue
            current = json.loads(r["baseline"])
            previous = current
            for seq in sorted(positions):
                value = positions[seq][0]
                event_rows = self.db.execute(
                    "SELECT e.event_id,e.source FROM events e WHERE e.identity=? AND e.epoch=? "
                    "AND e.sequence=? AND NOT EXISTS (SELECT 1 FROM events child "
                    "WHERE child.parent_event_id=e.event_id) ORDER BY e.event_id",
                    (r["identity"], r["epoch"], seq),
                ).fetchall()
                event_row = event_rows[0] if event_rows else None
                attributions = [
                    attributed[row["event_id"]]
                    for row in event_rows
                    if row["event_id"] in attributed
                ]
                attribution_models = {row["model"] for row in attributions}
                attribution_ambiguous = len(attribution_models) > 1
                # Multiple collectors can make the same-model claim with
                # different starts. Treat those as one model claim and prefer
                # the earliest evidenced start so overlap clipping remains
                # deterministic instead of manufacturing model ambiguity.
                attribution = None
                if attributions and not attribution_ambiguous:
                    attribution = min(
                        attributions,
                        key=lambda row: json.loads(row["start_counters"])["total_tokens"],
                    )
                try:
                    delta = self._validate_interval(previous, value)
                except ValueError:
                    unresolved.append(
                        {
                            "identity": r["identity"],
                            "epoch": r["epoch"],
                            "reason": "counter_decrease",
                        }
                    )
                    previous = value
                    continue
                model_delta = None if attribution_ambiguous else delta
                model = r["model"]
                if attribution_ambiguous:
                    unresolved.append(
                        {
                            "identity": r["identity"],
                            "epoch": r["epoch"],
                            "sequence": seq,
                            "event_ids": [row["event_id"] for row in event_rows],
                            "reason": "ambiguous_model_attribution",
                            "counters": delta,
                        }
                    )
                if attribution:
                    start = json.loads(attribution["start_counters"])
                    model_delta = self._validate_interval(start, value)
                    if previous != start:
                        if previous["total_tokens"] < start["total_tokens"]:
                            reason = "model_attribution_gap"
                            interval_start = previous
                            interval_end = start
                            model_delta = self._validate_interval(start, value)
                        else:
                            reason = "model_attribution_overlap"
                            interval_start = start
                            interval_end = previous
                            model_delta = self._validate_interval(previous, value)
                        try:
                            affected = self._validate_interval(interval_start, interval_end)
                        except ValueError:
                            affected = None
                        unresolved.append(
                            {
                                "identity": r["identity"],
                                "epoch": r["epoch"],
                                "event_id": event_row["event_id"],
                                "reason": reason,
                                "start": interval_start,
                                "end": interval_end,
                                "counters": affected,
                            }
                        )
                    model = attribution["model"]
                elif event_row and event_row["source"] == "codex-rpc-unattributed":
                    model_delta = None
                    unresolved.append(
                        {
                            "identity": r["identity"],
                            "epoch": r["epoch"],
                            "sequence": seq,
                            "event_ids": [row["event_id"] for row in event_rows],
                            "reason": "model_attribution_unverified",
                            "counters": delta,
                        }
                    )
                elif not attribution_ambiguous and event_row and ":" in event_row["source"]:
                    candidate = event_row["source"].rsplit(":", 1)[1]
                    if candidate in MODELS:
                        model = candidate
                for key in FIELDS:
                    totals[key] += delta[key]
                if model_delta is not None:
                    agg = by_model.setdefault(model, dict(ZERO, wpu=0.0))
                    for key in FIELDS:
                        agg[key] += model_delta[key]
                    uncached = model_delta["input_tokens"] - model_delta["cached_input_tokens"]
                    rates = MODELS[model]
                    agg["wpu"] += (
                        uncached * rates[0]
                        + model_delta["cached_input_tokens"] * rates[1]
                        + model_delta["output_tokens"] * rates[2]
                    ) / 1_000_000
                previous = value
            registration_coverage[(r["identity"], r["epoch"])] = previous
        reservations = []
        turns = {}
        for r in regs:
            registration_coverage.setdefault((r["identity"], r["epoch"]), None)
        for t in self.db.execute("SELECT * FROM turns ORDER BY turn_id"):
            finish = self.db.execute(
                "SELECT * FROM turn_finishes WHERE turn_id=?", (t["turn_id"],)
            ).fetchone()
            if finish is None:
                reservations.append(
                    {
                        "turn_id": t["turn_id"],
                        "reservation_tokens": t["reservation_tokens"],
                        "reservation_wpu": t["reservation_wpu"],
                        "deadline_at": t["deadline_at"],
                    }
                )
                turns[t["turn_id"]] = None
            else:
                a = json.loads(t["start_counters"])
                b = json.loads(finish["end_counters"])
                turns[t["turn_id"]] = {k: b[k] - a[k] for k in FIELDS}
                observed = registration_coverage.get((t["identity"], t["epoch"]))
                covered = False
                if observed is not None:
                    try:
                        self._validate_interval(b, observed)
                    except ValueError:
                        pass  # Conflicting subsets retain the coverage obligation.
                    else:
                        covered = True
                if not covered:
                    reservations.append(
                        {
                            "turn_id": t["turn_id"],
                            "reservation_tokens": t["reservation_tokens"],
                            "reservation_wpu": t["reservation_wpu"],
                            "deadline_at": t["deadline_at"],
                        }
                    )
                    unresolved.append(
                        {
                            "identity": t["identity"],
                            "epoch": t["epoch"],
                            "turn_id": t["turn_id"],
                            "reason": "turn_usage_uncovered",
                        }
                    )
        for d in self.db.execute(
            "SELECT * FROM dispatches WHERE status NOT IN "
            "('covered','settled') ORDER BY dispatch_id"
        ):
            payload = json.loads(d["payload"])
            context = payload.get("context")
            team = context.get("team") if isinstance(context, dict) else None
            reservations.append(
                {
                    "dispatch_id": d["dispatch_id"],
                    "team": team,
                    "reservation_tokens": d["estimate_tokens"],
                    "reservation_wpu": d["estimate_wpu"],
                    "deadline_at": d["deadline_at"],
                    "status": d["status"],
                    "baseline": json.loads(d["baseline"]) if d["baseline"] else None,
                }
            )
        historical_unknown = []
        for item in self.db.execute(
            "SELECT event_id,counters FROM external_historical_unknown ORDER BY event_id"
        ):
            historical_unknown.append(
                {
                    "event_id": item["event_id"],
                    "status": "unattributed_historical_usage",
                    "counters": json.loads(item["counters"]),
                }
            )
        return {
            "incremental": totals,
            "by_model": by_model,
            "unresolved": unresolved,
            "historical_unknown": historical_unknown,
            "reservations": reservations,
            "turns": turns,
        }

    def reserve(
        self,
        dispatch_id,
        identity,
        model,
        estimate,
        deadline_at,
        *,
        context,
        baseline=None,
        admission_percent=0,
    ):
        if not dispatch_id or not identity or model not in MODELS:
            raise ValueError("invalid dispatch")
        tokens = _finite_positive(estimate["tokens"], "tokens")
        wpu = _finite_positive(estimate["wpu"], "wpu")
        deadline = _timestamp(deadline_at, "deadline_at")
        base = None if baseline is None else _json(_counters(baseline))
        payload = _json(
            {
                "identity": identity,
                "model": model,
                "estimate": {"tokens": tokens, "wpu": wpu},
                "deadline_at": deadline,
                "context": context,
                "baseline": base,
            }
        )
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT payload FROM dispatches WHERE dispatch_id=?", (dispatch_id,)
            ).fetchone()
            if row:
                if row["payload"] != payload:
                    raise RuntimeError("dispatch_id_payload_conflict")
            else:
                if (
                    self.db.execute(
                        "SELECT 1 FROM dispatches "
                        "WHERE status NOT IN ('covered','settled') AND model=? LIMIT 1",
                        (model,),
                    ).fetchone()
                    and self.db.execute(
                        "SELECT count(*) FROM dispatches "
                        "WHERE status NOT IN ('covered','settled') AND model=?",
                        (model,),
                    ).fetchone()[0]
                    >= 2
                ):
                    raise RuntimeError("model_dispatch_slots_full")
                active_teams = {
                    item[0]
                    for item in self.db.execute(
                        "SELECT DISTINCT json_extract(payload,'$.context.team') FROM dispatches "
                        "WHERE status NOT IN ('covered','settled')"
                    )
                    if item[0]
                }
                team = context.get("team") if isinstance(context, dict) else None
                if team and team not in active_teams and len(active_teams) >= 2:
                    raise RuntimeError("team_dispatch_slots_full")
                if admission_percent >= 97 and active_teams and team not in active_teams:
                    raise RuntimeError("quota_97_single_team_only")
                self.db.execute(
                    "INSERT INTO dispatches "
                    "(dispatch_id,payload,identity,model,estimate_tokens,estimate_wpu,deadline_at,"
                    "baseline,provider_turn_id,status,outcome,created_at,epoch) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                    (
                        dispatch_id,
                        payload,
                        identity,
                        model,
                        tokens,
                        wpu,
                        deadline,
                        base,
                        None,
                        "reserved",
                        None,
                        time.time(),
                    ),
                )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return self.db.execute(
            "SELECT * FROM dispatches WHERE dispatch_id=?", (dispatch_id,)
        ).fetchone()

    def bind(self, dispatch_id, identity, provider_turn_id, receipt=None, *, epoch=None):
        if not provider_turn_id:
            raise ValueError("provider turn id required")
        dispatch = self.db.execute(
            "SELECT baseline FROM dispatches WHERE dispatch_id=? AND identity=?",
            (dispatch_id, identity),
        ).fetchone()
        if dispatch is None:
            raise ValueError("dispatch binding conflict")
        if epoch is None:
            baseline = dispatch["baseline"]
            candidates = self.db.execute(
                "SELECT epoch FROM registrations WHERE identity=? "
                "AND model=(SELECT model FROM dispatches WHERE dispatch_id=?) "
                "AND baseline IS ? ORDER BY epoch",
                (identity, dispatch_id, baseline),
            ).fetchall()
            if len(candidates) > 1:
                raise ValueError("dispatch_epoch_ambiguous")
            epoch = candidates[0]["epoch"] if candidates else None
        cur = self.db.execute(
            "UPDATE dispatches SET provider_turn_id=?,status='bound',epoch=COALESCE(epoch,?) "
            "WHERE dispatch_id=? AND identity=? "
            "AND (provider_turn_id IS NULL OR provider_turn_id=?)",
            (provider_turn_id, epoch, dispatch_id, identity, provider_turn_id),
        )
        if cur.rowcount != 1:
            raise ValueError("dispatch binding conflict")

    def record_outcome(self, dispatch_id, status, receipt=None):
        if status not in ("completed", "failed", "timeout", "unknown"):
            raise ValueError("invalid outcome")
        encoded = _json(receipt) if receipt is not None else None
        observed = time.time()
        event_id = dispatch_id + ":" + status
        self.db.execute("BEGIN IMMEDIATE")
        try:
            prior = self.db.execute(
                "SELECT * FROM outcomes WHERE event_id=?", (event_id,)
            ).fetchone()
            row = (event_id, dispatch_id, status, encoded, observed)
            if prior:
                if tuple(prior)[:4] != row[:4]:
                    raise ValueError("outcome event payload conflict")
                observed = prior["observed_at"]
            else:
                self.db.execute("INSERT INTO outcomes VALUES(?,?,?,?,?)", row)
            self.db.execute(
                "UPDATE dispatches SET status=?,outcome=? WHERE dispatch_id=?",
                (status, encoded, dispatch_id),
            )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        self.observe_receipt(event_id, dispatch_id, receipt, None, status, None, observed, {})

    def settle_certified_start(self, dispatch_id, thread_id):
        if not dispatch_id.startswith("start:") or not thread_id:
            raise ValueError("certified_start_identity_invalid")
        row = self.db.execute(
            "SELECT status,outcome FROM dispatches WHERE dispatch_id=?", (dispatch_id,)
        ).fetchone()
        if row is None or row["status"] != "completed" or not row["outcome"]:
            raise ValueError("certified_start_outcome_missing")
        receipt = json.loads(row["outcome"])
        if receipt != {"thread_id": thread_id}:
            raise ValueError("certified_start_receipt_mismatch")
        registration = self.db.execute(
            "SELECT 1 FROM registrations WHERE identity=? AND model=(SELECT model FROM dispatches "
            "WHERE dispatch_id=?) AND started=1 AND evidence=?",
            (thread_id, dispatch_id, "provider-certified-new-thread:" + dispatch_id),
        ).fetchone()
        if registration is None:
            raise ValueError("certified_start_registration_missing")
        self.db.execute(
            "UPDATE dispatches SET status='settled' WHERE dispatch_id=? AND status='completed'",
            (dispatch_id,),
        )

    def observe_receipt(
        self,
        event_id,
        dispatch_id,
        counters,
        provider_turn_id,
        source,
        event_at,
        observed_at,
        evidence,
    ):
        value = None if counters is None else _json(counters)
        if not event_id or not dispatch_id or not source:
            raise ValueError("receipt identity and source are required")
        encoded_evidence = _json(evidence)
        observed = _timestamp(observed_at, "observed_at")
        row = (
            event_id,
            dispatch_id,
            provider_turn_id,
            source,
            value,
            event_at,
            observed,
            encoded_evidence,
        )
        prior = self.db.execute("SELECT * FROM receipts WHERE event_id=?", (event_id,)).fetchone()
        if prior:
            prior_values = tuple(
                prior[k]
                for k in (
                    "event_id",
                    "dispatch_id",
                    "provider_turn_id",
                    "source",
                    "receipt",
                    "event_at",
                    "observed_at",
                    "evidence",
                )
            )
            # Duplicate delivery may have a later local receipt timestamp; raw
            # evidence and provider event identity define idempotence.
            if prior_values[:6] + prior_values[7:] != row[:6] + row[7:]:
                conflict = _json({"row": row, "prior": prior_values})
                import hashlib

                conflict_id = hashlib.sha256((event_id + conflict).encode()).hexdigest()
                self.db.execute(
                    "INSERT OR IGNORE INTO receipt_conflicts VALUES(?,?,?,?)",
                    (conflict_id, event_id, conflict, observed),
                )
                raise ValueError("receipt event payload conflict")
            return
        self.db.execute("INSERT INTO receipts VALUES(?,?,?,?,?,?,?,?)", row)

    def attribute_dispatch(
        self, dispatch_id, identity, epoch, model, start, end, *, evidence, sequence=None
    ):
        if not dispatch_id or model not in MODELS or not isinstance(evidence, dict):
            raise ValueError("external attribution identity invalid")
        before, after = _counters(start), _counters(end)
        self._validate_interval(before, after)
        event_sequence = after["total_tokens"] if sequence is None else sequence
        if (
            isinstance(event_sequence, bool)
            or not isinstance(event_sequence, int)
            or event_sequence < 0
        ):
            raise ValueError("invalid attribution sequence")
        self._require_registration(identity, epoch)
        event_id = evidence.get("event_id")
        if not event_id:
            raise ValueError("external attribution event identity missing")
        prior = self.db.execute(
            "SELECT * FROM interval_attributions WHERE event_id=?", (event_id,)
        ).fetchone()
        expected = (
            dispatch_id,
            identity,
            epoch,
            model,
            _json(before),
            _json(after),
            _json(evidence),
        )
        if prior:
            actual = tuple(
                prior[key]
                for key in (
                    "dispatch_id",
                    "identity",
                    "epoch",
                    "model",
                    "start_counters",
                    "end_counters",
                    "evidence",
                )
            )
            if actual != expected:
                raise ValueError("interval_attribution_conflict")
            return dispatch_id
        existing_event = self.db.execute(
            "SELECT identity,epoch,sequence,counters FROM events WHERE event_id=?", (event_id,)
        ).fetchone()
        if existing_event:
            if (
                existing_event["identity"],
                existing_event["epoch"],
                existing_event["sequence"],
                json.loads(existing_event["counters"]),
            ) != (identity, epoch, event_sequence, after):
                raise ValueError("interval_attribution_event_conflict")
        else:
            self.observe(
                identity,
                epoch,
                event_id,
                after,
                source="external:" + model,
                sequence=event_sequence,
                observed_at=time.time(),
            )
        row = (
            event_id,
            dispatch_id,
            identity,
            epoch,
            model,
            _json(before),
            _json(after),
            _json(evidence),
            time.time(),
        )
        self.db.execute("BEGIN IMMEDIATE")
        try:
            prior = self.db.execute(
                "SELECT * FROM interval_attributions WHERE event_id=?", (event_id,)
            ).fetchone()
            if prior:
                expected = tuple(
                    prior[key]
                    for key in (
                        "event_id",
                        "dispatch_id",
                        "identity",
                        "epoch",
                        "model",
                        "start_counters",
                        "end_counters",
                        "evidence",
                    )
                )
                if expected != row[:8]:
                    raise ValueError("interval_attribution_conflict")
            else:
                self.db.execute("INSERT INTO interval_attributions VALUES(?,?,?,?,?,?,?,?,?)", row)
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return dispatch_id

    def import_external(
        self, batch, collector_id, evidence_bytes, evidence_sha256, *, external_allowlist
    ):
        if collector_id not in external_allowlist:
            raise ValueError("external_collector_not_allowlisted")
        if not isinstance(batch, list) or not batch:
            raise ValueError("external_batch_must_be_nonempty")
        canonical = json.dumps(
            batch, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        if not isinstance(evidence_bytes, bytes) or canonical != evidence_bytes:
            raise ValueError("external_evidence_bytes_mismatch")
        digest = hashlib.sha256(canonical).hexdigest()
        if evidence_sha256 != digest:
            raise ValueError("external_evidence_hash_mismatch")
        starts = {}
        intervals = []
        historical_unknown = []
        event_payloads = {}
        for item in batch:
            if not isinstance(item, dict):
                raise ValueError("external_event_invalid")
            event_id = item.get("event_id")
            if event_id:
                encoded_item = _json(item)
                prior_payload = event_payloads.get(event_id)
                if prior_payload is not None and prior_payload != encoded_item:
                    raise ValueError("external_event_id_conflict")
                event_payloads[event_id] = encoded_item
            kind = item.get("kind")
            if kind == "thread_start":
                if not item.get("event_id") or not item.get("thread_id") or not item.get("epoch"):
                    raise ValueError("external_start_identity_missing")
                if item.get("started_after_T0") is not True:
                    raise ValueError("external_start_not_post_t0")
                starts[item["event_id"]] = item
            elif kind == "turn_interval":
                intervals.append(item)
            elif kind == "active_reservation":
                if (
                    not item.get("event_id")
                    or not item.get("team")
                    or item.get("model") not in MODELS
                    or item.get("status") != "active"
                ):
                    raise ValueError("external_active_reservation_invalid")
                _finite_positive(item.get("tokens"), "tokens")
                _finite_positive(item.get("wpu"), "wpu")
                _timestamp(item.get("deadline_at"), "deadline_at")
            elif kind == "historical_unknown":
                if not item.get("event_id"):
                    raise ValueError("external_historical_identity_missing")
                historical_unknown.append((item["event_id"], _counters(item.get("counters"))))
            else:
                raise ValueError("external_endpoint_only_or_unknown_kind")
        if bool(starts) != bool(intervals):
            raise ValueError("external_interval_requires_evidenced_start")
        prepared = []
        for item in intervals:
            start = starts.get(item.get("start_event_id"))
            if (
                not start
                or start["thread_id"] != item.get("thread_id")
                or start["epoch"] != item.get("epoch")
            ):
                raise ValueError("external_interval_start_link_invalid")
            if (
                item.get("model") not in MODELS
                or not item.get("event_id")
                or not item.get("turn_id")
            ):
                raise ValueError("external_interval_identity_invalid")
            before, after = _counters(item.get("start")), _counters(item.get("end"))
            self._validate_interval(before, after)
            prepared.append((item, before, after))
        # Reject endpoint-only or malformed batches before writing any rows.
        for _event_id, start in starts.items():
            _counters(start.get("counters"))
        for start in starts.values():
            start_sha256 = hashlib.sha256(_json(start).encode("utf-8")).hexdigest()
            self.register(
                start["thread_id"],
                start["epoch"],
                start.get("model"),
                _counters(start["counters"]),
                evidence=_json(
                    {
                        "collector_id": collector_id,
                        "event_id": start["event_id"],
                        "event_sha256": start_sha256,
                    }
                ),
                started_after_T0=True,
            )
            self.observe(
                start["thread_id"],
                start["epoch"],
                start["event_id"],
                _counters(start["counters"]),
                source="external-thread-start",
                sequence=0,
                observed_at=time.time(),
            )
        for item, before, after in prepared:
            event_sha256 = hashlib.sha256(_json(item).encode("utf-8")).hexdigest()
            self.attribute_dispatch(
                "external:" + item["turn_id"],
                item["thread_id"],
                item["epoch"],
                item["model"],
                before,
                after,
                evidence={
                    "event_id": item["event_id"],
                    "turn_id": item["turn_id"],
                    "collector_id": collector_id,
                    "event_sha256": event_sha256,
                },
            )
        for item in batch:
            if item.get("kind") == "active_reservation":
                self.reserve(
                    "external:" + item["event_id"],
                    "external:" + item["event_id"],
                    item["model"],
                    {"tokens": item["tokens"], "wpu": item["wpu"]},
                    item["deadline_at"],
                    context={"team": item["team"], "external": True},
                )
        for event_id, counters in historical_unknown:
            source_item = next(
                item
                for item in batch
                if item.get("kind") == "historical_unknown" and item["event_id"] == event_id
            )
            event_sha256 = hashlib.sha256(_json(source_item).encode("utf-8")).hexdigest()
            row = (event_id, _json(counters), collector_id, event_sha256)
            prior = self.db.execute(
                "SELECT event_id,counters,collector_id,evidence_sha256 "
                "FROM external_historical_unknown WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if prior:
                if tuple(prior) != row:
                    raise ValueError("external_historical_event_conflict")
            else:
                self.db.execute(
                    "INSERT INTO external_historical_unknown VALUES(?,?,?,?,?)",
                    (*row, time.time()),
                )
        imported = self.db.execute(
            "SELECT evidence_bytes FROM external_imports "
            "WHERE collector_id=? AND evidence_sha256=?",
            (collector_id, digest),
        ).fetchone()
        if imported:
            if bytes(imported["evidence_bytes"]) != evidence_bytes:
                raise ValueError("external_batch_digest_conflict")
        else:
            self.db.execute(
                "INSERT INTO external_imports VALUES(?,?,?,?)",
                (collector_id, digest, evidence_bytes, time.time()),
            )
        return {"collector_id": collector_id, "sha256": digest, "intervals": len(prepared)}

    def consume_dispatch(self, dispatch_id):
        cur = self.db.execute(
            "UPDATE dispatches SET status='dispatched' WHERE dispatch_id=? AND status='reserved'",
            (dispatch_id,),
        )
        return cur.rowcount == 1

    def dispatch(self, dispatch_id):
        row = self.db.execute(
            "SELECT * FROM dispatches WHERE dispatch_id=?", (dispatch_id,)
        ).fetchone()
        return dict(row) if row else None

    def trusted_terminal_baseline(self, identity, epoch):
        registration = self.db.execute(
            "SELECT baseline FROM registrations WHERE identity=? AND epoch=?",
            (identity, epoch),
        ).fetchone()
        if registration is None:
            return None
        pending = self.db.execute(
            "SELECT 1 FROM dispatches d WHERE d.identity=? AND (d.epoch=? OR d.epoch IS NULL) "
            "AND d.status IN ('dispatched','bound','completed','unknown','failed','timeout') "
            "AND NOT EXISTS (SELECT 1 FROM terminal_receipts t "
            "WHERE t.dispatch_id=d.dispatch_id) LIMIT 1",
            (identity, epoch),
        ).fetchone()
        if pending is not None:
            return None
        terminals = self.db.execute(
            "SELECT t.evidence FROM terminal_receipts t JOIN dispatches d "
            "ON d.dispatch_id=t.dispatch_id WHERE d.identity=? AND d.epoch=? "
            "ORDER BY d.created_at,t.observed_at",
            (identity, epoch),
        ).fetchall()
        if terminals:
            ends = [_counters(json.loads(item["evidence"])["end"]) for item in terminals]
            greatest = max(item["total_tokens"] for item in ends)
            candidates = {tuple(item[key] for key in FIELDS) for item in ends}
            top = {item for item in candidates if item[-1] == greatest}
            if len(top) != 1:
                return None
            baseline = next(item for item in ends if item["total_tokens"] == greatest)
            for end in ends:
                try:
                    self._validate_interval(end, baseline)
                except ValueError:
                    return None
        else:
            baseline = (
                json.loads(registration["baseline"])
                if registration["baseline"] is not None
                else None
            )
        if baseline is None:
            return None
        latest = self.db.execute(
            "SELECT e.counters FROM events e WHERE e.identity=? AND e.epoch=? "
            "AND e.sequence=(SELECT MAX(last.sequence) FROM events last "
            "WHERE last.identity=e.identity AND last.epoch=e.epoch) "
            "AND NOT EXISTS (SELECT 1 FROM events child WHERE child.parent_event_id=e.event_id)",
            (identity, epoch),
        ).fetchall()
        if latest:
            points = {tuple(json.loads(item["counters"]).values()) for item in latest}
            if len(points) != 1 or _counters(json.loads(latest[0]["counters"])) != baseline:
                return None
        return baseline

    def close(self):
        self.db.close()
