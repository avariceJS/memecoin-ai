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
            CREATE TABLE IF NOT EXISTS outcomes (
                event_id TEXT PRIMARY KEY, dispatch_id TEXT NOT NULL,
                status TEXT NOT NULL, receipt TEXT, observed_at REAL NOT NULL
            );
            """
        )

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
            current = json.loads(r["baseline"])
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
            model = r["model"]
            agg = by_model.setdefault(model, dict(ZERO, wpu=0.0))
            for k in FIELDS:
                agg[k] += delta[k]
            uncached = delta["input_tokens"] - delta["cached_input_tokens"]
            rates = MODELS[model]
            agg["wpu"] += (
                uncached * rates[0]
                + delta["cached_input_tokens"] * rates[1]
                + delta["output_tokens"] * rates[2]
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
        return {
            "incremental": totals,
            "by_model": by_model,
            "unresolved": unresolved,
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
                    raise ValueError("dispatch_id_payload_conflict")
            else:
                if (
                    self.db.execute(
                        "SELECT 1 FROM dispatches "
                        "WHERE status IN ('reserved','dispatched','bound') AND model=? LIMIT 1",
                        (model,),
                    ).fetchone()
                    and self.db.execute(
                        "SELECT count(*) FROM dispatches "
                        "WHERE status IN ('reserved','dispatched','bound') AND model=?",
                        (model,),
                    ).fetchone()[0]
                    >= 2
                ):
                    raise RuntimeError("model_dispatch_slots_full")
                active_teams = {
                    item[0]
                    for item in self.db.execute(
                        "SELECT DISTINCT json_extract(payload,'$.context.team') FROM dispatches "
                        "WHERE status IN ('reserved','dispatched','bound')"
                    )
                    if item[0]
                }
                team = context.get("team") if isinstance(context, dict) else None
                if team and team not in active_teams and len(active_teams) >= 2:
                    raise RuntimeError("team_dispatch_slots_full")
                if admission_percent >= 97 and active_teams and team not in active_teams:
                    raise RuntimeError("quota_97_single_team_only")
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
        if not provider_turn_id:
            raise ValueError("provider turn id required")
        cur = self.db.execute(
            "UPDATE dispatches SET provider_turn_id=?,status='bound' "
            "WHERE dispatch_id=? AND identity=? "
            "AND (provider_turn_id IS NULL OR provider_turn_id=?)",
            (provider_turn_id, dispatch_id, identity, provider_turn_id),
        )
        if cur.rowcount != 1:
            raise ValueError("dispatch binding conflict")
        self.observe_receipt(
            dispatch_id + ":bind",
            dispatch_id,
            receipt,
            provider_turn_id,
            "started",
            None,
            time.time(),
            {},
        )

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
        _json(evidence)
        row = (event_id, dispatch_id, provider_turn_id, source, value, event_at, observed_at)
        prior = self.db.execute("SELECT * FROM receipts WHERE event_id=?", (event_id,)).fetchone()
        if prior:
            if tuple(prior) != row:
                raise ValueError("receipt event payload conflict")
            return
        self.db.execute("INSERT INTO receipts VALUES(?,?,?,?,?,?,?)", row)

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
