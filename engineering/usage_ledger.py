import json
import math
import sqlite3
import time
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
UNCLASSIFIED_MODEL = "unclassified"
EXTERNAL_COLLECTORS = frozenset({"codex-rollout-intervals-v1"})


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
    if isinstance(v, bool) or type(v) not in (int, float) or v <= 0:
        raise ValueError(name + " must be finite and positive")
    if type(v) is int and v.bit_length() > 1023:
        raise ValueError(name + " must be finite and positive")
    value = float(v)
    if not math.isfinite(value):
        raise ValueError(name + " must be finite and positive")
    return value


def _timestamp(v, name):
    if isinstance(v, bool) or type(v) not in (int, float):
        raise ValueError(name + " must be a finite timestamp")
    if type(v) is int and v.bit_length() > 1023:
        raise ValueError(name + " must be a finite timestamp")
    value = float(v)
    if not math.isfinite(value):
        raise ValueError(name + " must be a finite timestamp")
    return value


def _json(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), allow_nan=False)


class UsageLedger:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(
            """
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
                status TEXT NOT NULL, outcome TEXT, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS receipts (
                event_id TEXT PRIMARY KEY, dispatch_id TEXT NOT NULL,
                provider_turn_id TEXT, source TEXT NOT NULL, receipt TEXT,
                event_at REAL, observed_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS external_batches (
                evidence_hash TEXT PRIMARY KEY, collector_id TEXT NOT NULL,
                payload TEXT NOT NULL, imported_at REAL NOT NULL,
                interval_count INTEGER NOT NULL, unresolved_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS model_intervals (
                interval_key TEXT PRIMARY KEY, identity TEXT NOT NULL, epoch TEXT NOT NULL,
                model TEXT NOT NULL, start_counters TEXT NOT NULL, end_counters TEXT NOT NULL,
                start_total INTEGER NOT NULL, end_total INTEGER NOT NULL,
                evidence_hash TEXT NOT NULL, source TEXT NOT NULL, event_at REAL,
                observed_at REAL NOT NULL, evidence TEXT NOT NULL,
                UNIQUE(identity,epoch,start_total,end_total)
            );
            CREATE TABLE IF NOT EXISTS external_unresolved (
                evidence_hash TEXT NOT NULL, ordinal INTEGER NOT NULL, identity TEXT NOT NULL,
                reason TEXT NOT NULL, evidence TEXT NOT NULL,
                PRIMARY KEY(evidence_hash,ordinal)
            );
            """
        )
        receipt_columns = {
            row["name"] for row in self.db.execute("PRAGMA table_info(receipts)").fetchall()
        }
        if "evidence" not in receipt_columns:
            self.db.execute("ALTER TABLE receipts ADD COLUMN evidence TEXT NOT NULL DEFAULT '{}'")

    @classmethod
    def readonly_summary(cls, path):
        from pathlib import Path

        value = Path(path).resolve()
        if not value.is_file():
            return None
        db = sqlite3.connect(value.as_uri() + "?mode=ro", uri=True, isolation_level=None)
        db.row_factory = sqlite3.Row
        ledger = cls.__new__(cls)
        ledger.db = db
        try:
            return ledger.summary()
        finally:
            db.close()

    def current_snapshot(self, identity):
        """Return the sole unambiguous current cumulative vector, or None."""
        owns_transaction = not self.db.in_transaction
        if owns_transaction:
            self.db.execute("BEGIN")
        succeeded = False
        try:
            registrations = self.db.execute(
                "SELECT * FROM registrations WHERE identity=? ORDER BY epoch", (identity,)
            ).fetchall()
            if len(registrations) != 1:
                succeeded = True
                return None
            registration = registrations[0]
            if registration["baseline"] is None:
                succeeded = True
                return None
            rows = self.db.execute(
                "SELECT * FROM events WHERE identity=? AND epoch=? ORDER BY sequence,event_id",
                (identity, registration["epoch"]),
            ).fetchall()
            positions = defaultdict(list)
            for event in rows:
                if self.db.execute(
                    "SELECT 1 FROM events WHERE parent_event_id=?", (event["event_id"],)
                ).fetchone():
                    continue
                positions[event["sequence"]].append(_counters(json.loads(event["counters"])))
            if any(any(value != values[0] for value in values) for values in positions.values()):
                succeeded = True
                return None
            current = json.loads(registration["baseline"])
            for sequence in sorted(positions):
                next_value = positions[sequence][0]
                try:
                    self._validate_interval(current, next_value)
                except ValueError:
                    succeeded = True
                    return None
                current = next_value
            succeeded = True
            return {"epoch": registration["epoch"], "counters": current}
        finally:
            if owns_transaction and self.db.in_transaction:
                self.db.execute("COMMIT" if succeeded else "ROLLBACK")

    def cover_dispatch(
        self,
        dispatch_id,
        *,
        thread_id,
        turn_id,
        model,
        counters,
        terminal_qualified,
        event_at=None,
        observed_at=None,
    ):
        """Cover a completed reservation only with qualified exact-turn terminal counters."""
        if terminal_qualified is not True:
            raise ValueError("terminal_usage_not_qualified")
        if type(thread_id) is not str or not thread_id or type(turn_id) is not str or not turn_id:
            raise ValueError("terminal_attribution_identity_invalid")
        if type(model) is not str or model not in MODELS:
            raise ValueError("terminal_attribution_model_invalid")
        end = _counters(counters)
        event_time = None if event_at is None else _timestamp(event_at, "event_at")
        observed_time = (
            time.time() if observed_at is None else _timestamp(observed_at, "observed_at")
        )
        self.db.execute("BEGIN IMMEDIATE")
        try:
            dispatch = self.db.execute(
                "SELECT * FROM dispatches WHERE dispatch_id=?", (dispatch_id,)
            ).fetchone()
            if dispatch is None or dispatch["identity"] != thread_id:
                raise ValueError("terminal_attribution_dispatch_mismatch")
            if dispatch["provider_turn_id"] != turn_id:
                raise ValueError("terminal_attribution_turn_mismatch")
            if dispatch["status"] != "completed":
                raise ValueError("terminal_attribution_dispatch_not_completed")
            if dispatch["baseline"] is None:
                raise ValueError("terminal_attribution_baseline_unknown")
            receipt_row = self.db.execute(
                "SELECT * FROM receipts WHERE dispatch_id=? AND provider_turn_id=? "
                "AND source='turn_completed' ORDER BY observed_at DESC,event_id DESC LIMIT 1",
                (dispatch_id, turn_id),
            ).fetchone()
            if receipt_row is None:
                raise ValueError("terminal_attribution_receipt_missing")
            terminal_evidence = json.loads(receipt_row["evidence"])
            if type(terminal_evidence) is not dict:
                raise ValueError("terminal_attribution_receipt_invalid")
            raw_end = terminal_evidence.get("usage_end")
            rpc_keys = {
                "inputTokens": "input_tokens",
                "cachedInputTokens": "cached_input_tokens",
                "outputTokens": "output_tokens",
                "reasoningOutputTokens": "reasoning_output_tokens",
                "totalTokens": "total_tokens",
            }
            if (
                terminal_evidence.get("provider_thread_id") != thread_id
                or terminal_evidence.get("turn_id") != turn_id
                or terminal_evidence.get("status") != "completed"
                or terminal_evidence.get("usage_terminal") is not True
                or type(raw_end) is not dict
                or set(raw_end) != set(rpc_keys)
                or {target: raw_end[source] for source, target in rpc_keys.items()} != end
            ):
                raise ValueError("terminal_attribution_receipt_invalid")
            baseline = _counters(json.loads(dispatch["baseline"]))
            registrations = self.db.execute(
                "SELECT * FROM registrations WHERE identity=? ORDER BY epoch", (thread_id,)
            ).fetchall()
            if len(registrations) != 1:
                raise ValueError("terminal_attribution_epoch_unknown")
            registration = registrations[0]
            if registration["baseline"] is None:
                raise ValueError("terminal_attribution_baseline_unknown")
            if registration["epoch"] == "" or not registration["baseline"]:
                raise ValueError("terminal_attribution_epoch_unknown")
            current = self._current_snapshot_in_transaction(registration)
            if current is None or current != baseline:
                raise ValueError("terminal_attribution_baseline_conflict")
            self._validate_interval(baseline, end)
            event_id = (
                "terminal:"
                + __import__("hashlib")
                .sha256(_json([dispatch_id, thread_id, turn_id, end]).encode())
                .hexdigest()
            )
            event_row = (
                event_id,
                thread_id,
                registration["epoch"],
                end["total_tokens"],
                _json(end),
                "qualified-terminal:" + model,
                observed_time,
                event_time,
                None,
                None,
            )
            self._append_event_in_transaction(event_row)
            interval_key = (
                __import__("hashlib")
                .sha256(_json([thread_id, registration["epoch"], baseline, end]).encode())
                .hexdigest()
            )
            old = self.db.execute(
                "SELECT * FROM model_intervals WHERE identity=? AND epoch=? "
                "AND start_total=? AND end_total=?",
                (thread_id, registration["epoch"], baseline["total_tokens"], end["total_tokens"]),
            ).fetchone()
            if old:
                if (old["model"], old["start_counters"], old["end_counters"]) != (
                    model,
                    _json(baseline),
                    _json(end),
                ):
                    raise ValueError("terminal_attribution_model_conflict")
            else:
                self.db.execute(
                    "INSERT INTO model_intervals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        interval_key,
                        thread_id,
                        registration["epoch"],
                        model,
                        _json(baseline),
                        _json(end),
                        baseline["total_tokens"],
                        end["total_tokens"],
                        "dispatch:" + dispatch_id,
                        "qualified-terminal",
                        event_time,
                        observed_time,
                        _json({"dispatch_id": dispatch_id, "turn_id": turn_id}),
                    ),
                )
            self._validate_stream(thread_id, registration["epoch"])
            self.db.execute(
                "UPDATE dispatches SET status='covered' WHERE dispatch_id=?", (dispatch_id,)
            )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def _current_snapshot_in_transaction(self, registration):
        if registration["baseline"] is None:
            return None
        rows = self.db.execute(
            "SELECT * FROM events WHERE identity=? AND epoch=? ORDER BY sequence,event_id",
            (registration["identity"], registration["epoch"]),
        ).fetchall()
        positions = defaultdict(list)
        for event in rows:
            if self.db.execute(
                "SELECT 1 FROM events WHERE parent_event_id=?", (event["event_id"],)
            ).fetchone():
                continue
            positions[event["sequence"]].append(_counters(json.loads(event["counters"])))
        if any(any(value != values[0] for value in values) for values in positions.values()):
            return None
        current = json.loads(registration["baseline"])
        for sequence in sorted(positions):
            next_value = positions[sequence][0]
            try:
                self._validate_interval(current, next_value)
            except ValueError:
                return None
            current = next_value
        return current

    def register(self, identity, epoch, model, baseline, *, evidence, started_after_T0=False):
        if not identity or not epoch or model not in {*MODELS, UNCLASSIFIED_MODEL} or not evidence:
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

    @staticmethod
    def external_batch_hash(batch):
        return __import__("hashlib").sha256(_json(batch).encode()).hexdigest()

    def import_external(self, batch, collector_id, evidence_hash):
        """Import allowlisted cumulative receipts and model intervals atomically."""
        if type(collector_id) is not str or collector_id not in EXTERNAL_COLLECTORS:
            raise ValueError("external_collector_not_allowed")
        if (
            type(evidence_hash) is not str
            or len(evidence_hash) != 64
            or any(ch not in "0123456789abcdef" for ch in evidence_hash)
            or self.external_batch_hash(batch) != evidence_hash
        ):
            raise ValueError("external_evidence_hash_mismatch")
        if (
            type(batch) is not dict
            or set(batch) != {"schema", "streams", "unresolved"}
            or batch["schema"] != "brain-org-usage-import-v1"
            or type(batch["streams"]) is not list
            or len(batch["streams"]) > 10000
            or type(batch["unresolved"]) is not list
            or len(batch["unresolved"]) > 10000
        ):
            raise ValueError("external_batch_shape")

        prepared_streams = []
        seen_streams = set()
        interval_count = 0
        for stream in batch["streams"]:
            if (
                type(stream) is not dict
                or set(stream) != {"identity", "epoch", "baseline", "intervals"}
                or type(stream["identity"]) is not str
                or not stream["identity"]
                or type(stream["epoch"]) is not str
                or not stream["epoch"]
                or type(stream["intervals"]) is not list
                or len(stream["intervals"]) > 10000
            ):
                raise ValueError("external_stream_shape")
            key = (stream["identity"], stream["epoch"])
            if key in seen_streams:
                raise ValueError("external_duplicate_stream")
            seen_streams.add(key)
            baseline = None if stream["baseline"] is None else _counters(stream["baseline"])
            prepared_intervals = []
            interval_ids = set()
            for interval in stream["intervals"]:
                fields = {"id", "model", "start", "end", "event_at", "source", "evidence"}
                if (
                    type(interval) is not dict
                    or set(interval) != fields
                    or type(interval["id"]) is not str
                    or not interval["id"]
                    or interval["id"] in interval_ids
                    or type(interval["model"]) is not str
                    or interval["model"] not in MODELS
                    or type(interval["source"]) is not str
                    or not interval["source"]
                ):
                    raise ValueError("external_interval_shape")
                interval_ids.add(interval["id"])
                start = _counters(interval["start"])
                end = _counters(interval["end"])
                self._validate_interval(start, end)
                event_at = _timestamp(interval["event_at"], "event_at")
                evidence_json = _json(interval["evidence"])
                prepared_intervals.append(
                    {
                        "id": interval["id"],
                        "model": interval["model"],
                        "start": start,
                        "end": end,
                        "event_at": event_at,
                        "source": interval["source"],
                        "evidence": evidence_json,
                    }
                )
            prepared_intervals.sort(key=lambda item: (item["event_at"], item["id"]))
            if baseline is None and prepared_intervals:
                raise ValueError("external_unknown_baseline_has_intervals")
            if baseline is not None and not prepared_intervals:
                raise ValueError("external_known_baseline_has_no_intervals")
            previous = baseline
            for interval in prepared_intervals:
                if previous is not None and previous != interval["start"]:
                    raise ValueError("external_intervals_not_contiguous")
                previous = interval["end"]
            prepared_streams.append(
                {
                    "identity": key[0],
                    "epoch": key[1],
                    "baseline": baseline,
                    "intervals": prepared_intervals,
                }
            )
            interval_count += len(prepared_intervals)

        prepared_unknown = []
        for item in batch["unresolved"]:
            if (
                type(item) is not dict
                or set(item) != {"identity", "reason", "evidence"}
                or type(item["identity"]) is not str
                or not item["identity"]
                or type(item["reason"]) is not str
                or not item["reason"]
            ):
                raise ValueError("external_unresolved_shape")
            prepared_unknown.append((item["identity"], item["reason"], _json(item["evidence"])))

        payload = _json(batch)
        imported_at = time.time()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            prior = self.db.execute(
                "SELECT * FROM external_batches WHERE evidence_hash=?", (evidence_hash,)
            ).fetchone()
            if prior:
                if prior["collector_id"] != collector_id or prior["payload"] != payload:
                    raise ValueError("external_batch_conflict")
                self.db.execute("COMMIT")
                return {
                    "evidence_hash": evidence_hash,
                    "intervals": prior["interval_count"],
                    "unresolved": prior["unresolved_count"],
                    "duplicate": True,
                }

            self.db.execute(
                "INSERT INTO external_batches VALUES(?,?,?,?,?,?)",
                (
                    evidence_hash,
                    collector_id,
                    payload,
                    imported_at,
                    interval_count,
                    len(prepared_unknown),
                ),
            )
            for stream in prepared_streams:
                identity, epoch, baseline = (
                    stream["identity"],
                    stream["epoch"],
                    stream["baseline"],
                )
                existing = self.db.execute(
                    "SELECT * FROM registrations WHERE identity=? AND epoch=?",
                    (identity, epoch),
                ).fetchone()
                baseline_json = None if baseline is None else _json(baseline)
                registration = (
                    identity,
                    epoch,
                    UNCLASSIFIED_MODEL,
                    baseline_json,
                    "external:" + collector_id + ":" + evidence_hash,
                    0,
                )
                if existing:
                    if existing["baseline"] != baseline_json or existing["model"] not in (
                        UNCLASSIFIED_MODEL,
                        *MODELS,
                    ):
                        raise ValueError("external_registration_conflict")
                else:
                    self.db.execute("INSERT INTO registrations VALUES(?,?,?,?,?,?)", registration)
                for interval in stream["intervals"]:
                    start, end = interval["start"], interval["end"]
                    event_id = (
                        "external:"
                        + __import__("hashlib")
                        .sha256(_json([evidence_hash, identity, epoch, interval["id"]]).encode())
                        .hexdigest()
                    )
                    event_row = (
                        event_id,
                        identity,
                        epoch,
                        end["total_tokens"],
                        _json(end),
                        collector_id + ":" + interval["source"],
                        imported_at,
                        interval["event_at"],
                        None,
                        None,
                    )
                    self._append_event_in_transaction(event_row)
                    interval_key = (
                        __import__("hashlib")
                        .sha256(_json([identity, epoch, start, end]).encode())
                        .hexdigest()
                    )
                    interval_payload = (
                        interval_key,
                        identity,
                        epoch,
                        interval["model"],
                        _json(start),
                        _json(end),
                        start["total_tokens"],
                        end["total_tokens"],
                        evidence_hash,
                        interval["source"],
                        interval["event_at"],
                        imported_at,
                        interval["evidence"],
                    )
                    existing_interval = self.db.execute(
                        "SELECT * FROM model_intervals WHERE identity=? AND epoch=? "
                        "AND start_total=? AND end_total=?",
                        (identity, epoch, start["total_tokens"], end["total_tokens"]),
                    ).fetchone()
                    if existing_interval:
                        if (
                            existing_interval["model"] != interval["model"]
                            or existing_interval["start_counters"] != _json(start)
                            or existing_interval["end_counters"] != _json(end)
                        ):
                            raise ValueError("external_model_interval_conflict")
                    else:
                        self.db.execute(
                            "INSERT INTO model_intervals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            interval_payload,
                        )
                if baseline is not None:
                    self._validate_stream(identity, epoch)
            for ordinal, (identity, reason, evidence) in enumerate(prepared_unknown):
                self.db.execute(
                    "INSERT INTO external_unresolved VALUES(?,?,?,?,?)",
                    (evidence_hash, ordinal, identity, reason, evidence),
                )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return {
            "evidence_hash": evidence_hash,
            "intervals": interval_count,
            "unresolved": len(prepared_unknown),
            "duplicate": False,
        }

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
            if (
                tuple(
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
                != row
            ):
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
            baseline = json.loads(r["baseline"])
            current = baseline
            for seq in sorted(positions):
                current = positions[seq][0]
            delta = {k: current[k] - json.loads(r["baseline"])[k] for k in FIELDS}
            if any(v < 0 for v in delta.values()):
                unresolved.append(
                    {"identity": r["identity"], "epoch": r["epoch"], "reason": "counter_decrease"}
                )
                continue
            registration_coverage[(r["identity"], r["epoch"])] = current
            for k in FIELDS:
                totals[k] += delta[k]
            intervals = self.db.execute(
                "SELECT * FROM model_intervals WHERE identity=? AND epoch=? "
                "ORDER BY start_total,end_total,interval_key",
                (r["identity"], r["epoch"]),
            ).fetchall()
            attributed = []
            cursor = baseline
            conflict = False
            for interval in intervals:
                start = json.loads(interval["start_counters"])
                end = json.loads(interval["end_counters"])
                try:
                    self._validate_interval(start, end)
                    if interval["model"] not in MODELS or any(
                        start[key] < cursor[key] or end[key] > current[key] for key in FIELDS
                    ):
                        raise ValueError("model interval outside observed stream")
                    gap = {key: start[key] - cursor[key] for key in FIELDS}
                    self._validate_interval(cursor, start)
                except ValueError:
                    conflict = True
                    break
                if any(gap.values()):
                    attributed.append((UNCLASSIFIED_MODEL, gap))
                attributed.append((interval["model"], self._delta(start, end)))
                cursor = end
            if not conflict:
                try:
                    tail = self._delta(cursor, current)
                except ValueError:
                    conflict = True
                else:
                    if any(tail.values()):
                        attributed.append((UNCLASSIFIED_MODEL, tail))
            if conflict:
                attributed = [(UNCLASSIFIED_MODEL, delta)]
                unresolved.append(
                    {
                        "identity": r["identity"],
                        "epoch": r["epoch"],
                        "reason": "model_interval_conflict",
                    }
                )
            if not intervals:
                attributed = [(r["model"], delta)]
            for model, model_delta in attributed:
                agg = by_model.setdefault(
                    model, dict(ZERO, wpu=None if model == UNCLASSIFIED_MODEL else 0.0)
                )
                for k in FIELDS:
                    agg[k] += model_delta[k]
                if model in MODELS:
                    uncached = model_delta["input_tokens"] - model_delta["cached_input_tokens"]
                    rates = MODELS[model]
                    agg["wpu"] += (
                        uncached * rates[0]
                        + model_delta["cached_input_tokens"] * rates[1]
                        + model_delta["output_tokens"] * rates[2]
                    ) / 1_000_000
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
            reservations.append(
                {
                    "dispatch_id": d["dispatch_id"],
                    "reservation_tokens": d["estimate_tokens"],
                    "reservation_wpu": d["estimate_wpu"],
                    "deadline_at": d["deadline_at"],
                    "status": d["status"],
                    "baseline": json.loads(d["baseline"]) if d["baseline"] else None,
                }
            )
            unresolved.append(
                {
                    "identity": d["identity"],
                    "dispatch_id": d["dispatch_id"],
                    "reason": "dispatch_" + d["status"],
                }
            )
        receipts = [
            {
                "event_id": row["event_id"],
                "dispatch_id": row["dispatch_id"],
                "provider_turn_id": row["provider_turn_id"],
                "source": row["source"],
                "receipt": json.loads(row["receipt"]) if row["receipt"] else None,
                "event_at": row["event_at"],
                "observed_at": row["observed_at"],
                "evidence": json.loads(row["evidence"]) if row["evidence"] else {},
            }
            for row in self.db.execute("SELECT * FROM receipts ORDER BY observed_at,event_id")
        ]
        external_imports = [
            {
                "evidence_hash": row["evidence_hash"],
                "collector_id": row["collector_id"],
                "imported_at": row["imported_at"],
                "interval_count": row["interval_count"],
                "unresolved_count": row["unresolved_count"],
            }
            for row in self.db.execute(
                "SELECT * FROM external_batches ORDER BY imported_at,evidence_hash"
            )
        ]
        external_unresolved = [
            {
                "evidence_hash": row["evidence_hash"],
                "identity": row["identity"],
                "reason": row["reason"],
                "evidence": json.loads(row["evidence"]),
            }
            for row in self.db.execute(
                "SELECT * FROM external_unresolved ORDER BY evidence_hash,ordinal"
            )
        ]
        return {
            "incremental": totals,
            "by_model": by_model,
            "unresolved": unresolved,
            "reservations": reservations,
            "turns": turns,
            "receipts": receipts,
            "external_imports": external_imports,
            "external_unresolved": external_unresolved,
        }

    @staticmethod
    def _delta(before, after):
        delta = {key: after[key] - before[key] for key in FIELDS}
        if any(value < 0 for value in delta.values()):
            raise ValueError("counters decreased across interval")
        return _counters(delta)

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
        max_active_teams=2,
        max_model_dispatches=2,
    ):
        if not dispatch_id or not identity or model not in MODELS or type(context) is not dict:
            raise ValueError("invalid dispatch")
        if (
            isinstance(max_active_teams, bool)
            or not isinstance(max_active_teams, int)
            or not 1 <= max_active_teams <= 2
            or isinstance(max_model_dispatches, bool)
            or not isinstance(max_model_dispatches, int)
            or not 1 <= max_model_dispatches <= 2
        ):
            raise ValueError("invalid dispatch capacity")
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
                    raise ValueError("dispatch_id_payload_conflict")
            else:
                active = self.db.execute(
                    "SELECT model,payload,identity FROM dispatches "
                    "WHERE status NOT IN ('covered','settled')"
                ).fetchall()
                if any(active_row["identity"] == identity for active_row in active):
                    raise ValueError("unresolved_stream_dispatch")
                active_teams = set()
                model_count = 0
                for active_row in active:
                    active_context = json.loads(active_row["payload"]).get("context", {})
                    active_team = active_context.get("team")
                    if type(active_team) is str and active_team:
                        active_teams.add(active_team)
                    if active_row["model"] == model:
                        model_count += 1
                requested_team = context.get("team")
                if type(requested_team) is not str or not requested_team:
                    raise ValueError("dispatch_team_required")
                if requested_team not in active_teams and len(active_teams) >= max_active_teams:
                    raise ValueError("active_team_limit")
                if model_count >= max_model_dispatches:
                    raise ValueError("active_model_limit")
                self.db.execute(
                    "INSERT INTO dispatches VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
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

    def bind(self, dispatch_id, identity, provider_turn_id, receipt=None):
        if type(provider_turn_id) is not str or not provider_turn_id:
            raise ValueError("provider turn id required")
        row = self.dispatch(dispatch_id)
        if not row or row["identity"] != identity:
            raise ValueError("dispatch binding conflict")
        if row["provider_turn_id"] not in (None, provider_turn_id):
            raise ValueError("dispatch binding conflict")
        if row["provider_turn_id"] == provider_turn_id:
            return
        if row["status"] not in ("reserved", "dispatched"):
            raise ValueError("dispatch binding conflict")
        self.db.execute(
            "UPDATE dispatches SET provider_turn_id=?,status='bound' WHERE dispatch_id=?",
            (provider_turn_id, dispatch_id),
        )
        self.observe_receipt(
            dispatch_id + ":bind",
            dispatch_id,
            None,
            provider_turn_id,
            "started",
            None,
            time.time(),
            {"identity": identity, "receipt": receipt},
        )

    def settle_new_stream(self, dispatch_id, thread_id, *, evidence):
        if type(thread_id) is not str or not thread_id or not evidence:
            raise ValueError("new stream evidence required")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT * FROM dispatches WHERE dispatch_id=?", (dispatch_id,)
            ).fetchone()
            if row is None or row["status"] != "dispatched":
                raise ValueError("new stream dispatch not consumed")
            if row["provider_turn_id"] is not None:
                raise ValueError("new stream dispatch already bound")
            existing = self.db.execute(
                "SELECT 1 FROM registrations WHERE identity=?", (thread_id,)
            ).fetchone()
            if existing:
                raise ValueError("new stream identity already registered")
            registration = (thread_id, "initial", row["model"], _json(ZERO), str(evidence), 1)
            self.db.execute("INSERT INTO registrations VALUES(?,?,?,?,?,?)", registration)
            self.db.execute(
                "UPDATE dispatches SET status='settled',outcome=? WHERE dispatch_id=?",
                (_json({"thread_id": thread_id, "new_unused_stream": True}), dispatch_id),
            )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def record_outcome(self, dispatch_id, status, receipt=None):
        if status not in ("completed", "failed", "timeout", "unknown"):
            raise ValueError("invalid outcome")
        row = self.dispatch(dispatch_id)
        if row is None:
            raise ValueError("unknown dispatch")
        receipt_json = _json(receipt) if receipt is not None else None
        if row["status"] in ("completed", "failed", "timeout", "unknown", "settled"):
            if row["status"] != status or row["outcome"] != receipt_json:
                raise ValueError("dispatch outcome conflict")
            return
        self.db.execute(
            "UPDATE dispatches SET status=?,outcome=? WHERE dispatch_id=?",
            (status, receipt_json, dispatch_id),
        )
        self.observe_receipt(
            dispatch_id + ":" + status,
            dispatch_id,
            None,
            row["provider_turn_id"],
            status,
            None,
            time.time(),
            {"identity": row["identity"], "receipt": receipt},
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
        if (
            type(event_id) is not str
            or not event_id
            or type(dispatch_id) is not str
            or not dispatch_id
        ):
            raise ValueError("invalid receipt identity")
        if type(source) is not str or not source:
            raise ValueError("invalid receipt source")
        value = None if counters is None else _json(_counters(counters))
        evidence_json = _json(evidence)
        observed = _timestamp(observed_at, "observed_at")
        event = None if event_at is None else _timestamp(event_at, "event_at")
        payload = (
            dispatch_id,
            provider_turn_id,
            source,
            value,
            event,
            observed,
            evidence_json,
        )
        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing = self.db.execute(
                "SELECT dispatch_id,provider_turn_id,source,receipt,event_at,observed_at,evidence "
                "FROM receipts WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if existing:
                current = tuple(existing)
                if current != payload:
                    raise ValueError("receipt_id_payload_conflict")
            else:
                self.db.execute(
                    "INSERT INTO receipts "
                    "(event_id,dispatch_id,provider_turn_id,source,receipt,event_at,observed_at,"
                    "evidence) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (event_id, *payload),
                )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

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

    def close(self):
        self.db.close()
