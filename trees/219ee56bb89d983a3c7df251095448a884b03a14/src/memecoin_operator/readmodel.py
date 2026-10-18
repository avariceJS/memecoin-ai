"""Read-only first-observed coverage for operator display.

Derived counts may be reused for at most ``ttl_s`` seconds. An exact cutoff, including
a dataset snapshot, uses ``ttl_s=0`` and does not read or fill that cache. Service
health and stop markers are not read here.
"""

import copy
import sqlite3
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path

NS = 10**9
DAY_NS = 86400 * NS
BAR_S = 300
_MAX_CACHE = 8
_ROWS_SQL = "SELECT pool, open_s, available FROM firsts WHERE available<=? ORDER BY pool, open_s"

_LOCK = threading.Lock()
_CACHE: OrderedDict[tuple, "_Entry"] = OrderedDict()
_FLIGHTS: dict[tuple, "_Flight"] = {}
_LAST_WALL: int | None = None
_LAST_MONO: int | None = None


class _Entry:
    __slots__ = ("computed_ns", "mono_ns", "payload", "schema", "ttl_ns")

    def __init__(self, computed_ns: int, mono_ns: int, ttl_ns: int, schema: str, payload: dict):
        self.computed_ns = computed_ns
        self.mono_ns = mono_ns
        self.ttl_ns = ttl_ns
        self.schema = schema
        self.payload = payload


class _Flight:
    __slots__ = ("cutoff", "entry", "error", "event")

    def __init__(self, cutoff: int):
        self.cutoff = cutoff
        self.event = threading.Event()
        self.entry: _Entry | None = None
        self.error: BaseException | None = None


def _clear() -> None:
    """Drop process-local cache. Used by tests and by clock regression."""
    global _LAST_WALL, _LAST_MONO
    with _LOCK:
        _CACHE.clear()
        _FLIGHTS.clear()
        _LAST_WALL = None
        _LAST_MONO = None


def _validate(now_ns: int, ttl_s: int) -> None:
    if isinstance(now_ns, bool) or not isinstance(now_ns, int):
        raise TypeError("readmodel_now_ns")
    if isinstance(ttl_s, bool) or not isinstance(ttl_s, int):
        raise TypeError("readmodel_ttl_s")
    if ttl_s < 0:
        raise ValueError("readmodel_ttl_s")


def _locate(source: Path) -> tuple[Path, Path, tuple]:
    if not isinstance(source, Path):
        if not isinstance(source, str):
            raise TypeError("readmodel_source")
        source = Path(source)
    try:
        root = source.resolve(strict=True)
    except FileNotFoundError:
        raise ValueError("readmodel_missing_database") from None
    index = root / "index.sqlite"
    try:
        st = index.stat()
    except FileNotFoundError:
        raise ValueError("readmodel_missing_database") from None
    if not index.is_file():
        raise ValueError("readmodel_missing_database")
    # Resolved root plus index inode. Replacement gets a new inode and misses.
    return root, index, (str(root), st.st_dev, st.st_ino)


def _uri(index: Path) -> str:
    """Read-only URI for a database that may gain a writer during the query.

    ``immutable=1`` is rejected: a checkpointed file can still be checkpointed again
    after this connection opens. ``mode=ro`` plus an explicit ``BEGIN`` is the snapshot.
    SQLite may create or update ``-wal``/``-shm`` as reader coordination. Those files
    are not a copy of the database and are not evidence that rows were rewritten.
    """
    return index.as_uri() + "?mode=ro"


def _ro(index: Path) -> sqlite3.Connection:
    db = sqlite3.connect(_uri(index), uri=True, timeout=30)
    db.isolation_level = None
    return db


def _schema_token(db: sqlite3.Connection) -> str:
    info = db.execute("PRAGMA table_info(firsts)").fetchall()
    if not info:
        raise ValueError("readmodel_malformed_schema")
    kinds = {row[1]: str(row[2]).upper() for row in info}
    if not {"pool", "open_s", "available"} <= set(kinds):
        raise ValueError("readmodel_malformed_schema")
    pool_type = kinds["pool"]
    if not any(token in pool_type for token in ("TEXT", "CHAR", "CLOB")):
        raise ValueError("readmodel_malformed_schema")
    if "INT" not in kinds["open_s"] or "INT" not in kinds["available"]:
        raise ValueError("readmodel_malformed_schema")
    master = db.execute("SELECT type, sql FROM sqlite_master WHERE name='firsts'").fetchone()
    if master is None or master[0] != "table" or not master[1]:
        raise ValueError("readmodel_malformed_schema")
    version = db.execute("PRAGMA schema_version").fetchone()
    return f"{version[0]}|{master[1]}"


@contextmanager
def _transaction(index: Path):
    """One deferred read transaction. Rollback closes it without a writer lock."""
    db = _ro(index)
    begun = False
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        begun = True
        yield db
    finally:
        if begun:
            try:
                db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        db.close()


def _read(index: Path, now_ns: int) -> tuple[dict, str]:
    with _transaction(index) as db:
        schema = _schema_token(db)
        rows = db.execute(_ROWS_SQL, (now_ns,)).fetchall()
    return _aggregate(rows), schema


def _peek(index: Path) -> str:
    with _transaction(index) as db:
        return _schema_token(db)


def _spans(ordered: list[int]) -> tuple[list[dict], list[dict]]:
    gaps: list[dict] = []
    ranges: list[dict] = []
    start = prev = ordered[0]
    count = 1
    for open_s in ordered[1:]:
        if open_s == prev + BAR_S:
            prev = open_s
            count += 1
            continue
        ranges.append(dict(first_s=start, last_s=prev, bars=count))
        if open_s > prev:
            # Endpoints are observed. A short or unaligned step is not a full missing bar.
            missing_bars = (open_s - prev) // BAR_S - 1
            gaps.append(
                dict(
                    after_s=prev,
                    before_s=open_s,
                    missing_bars=missing_bars if missing_bars > 0 else 0,
                )
            )
        start = prev = open_s
        count = 1
    ranges.append(dict(first_s=start, last_s=prev, bars=count))
    return gaps, ranges


def _aggregate(rows: list[tuple]) -> dict:
    by_pool: dict = {}
    event_days = set()
    receipt_days = set()
    first_s = None
    last_s = None
    for pool, open_s, available in rows:
        times = by_pool.get(pool)
        if times is None:
            times = set()
            by_pool[pool] = times
        times.add(open_s)
        event_days.add(open_s // 86400)
        receipt_days.add(available // DAY_NS)
        if first_s is None or open_s < first_s:
            first_s = open_s
        if last_s is None or open_s > last_s:
            last_s = open_s
    per_pool = []
    matured = 0
    for pool, times in sorted(by_pool.items()):
        ordered = sorted(times)
        # Origin is mature only when every bar at offsets -7..+3 exists.
        matured += sum(all(t + k * BAR_S in times for k in range(-7, 4)) for t in ordered)
        gaps, ranges = _spans(ordered)
        per_pool.append(
            dict(
                pool=pool,
                bars=len(times),
                days=len({t // 86400 for t in ordered}),
                first_s=ordered[0],
                last_s=ordered[-1],
                gaps=gaps,
                ranges=ranges,
            )
        )
    return dict(
        bars=len(rows),
        observed_pools=len(by_pool),
        matured_samples=matured,
        event_days=len(event_days),
        receipt_days=len(receipt_days),
        per_pool=per_pool,
        first_s=first_s,
        last_s=last_s,
    )


def _observe_clocks() -> bool:
    """UTC or monotonic regression drops every derived snapshot."""
    global _LAST_WALL, _LAST_MONO
    wall = time.time_ns()
    mono = time.monotonic_ns()
    with _LOCK:
        regressed = (_LAST_WALL is not None and wall < _LAST_WALL) or (
            _LAST_MONO is not None and mono < _LAST_MONO
        )
        if regressed:
            _CACHE.clear()
        _LAST_WALL = wall
        _LAST_MONO = mono
        return regressed


def _age_ns(entry: _Entry) -> int:
    return time.monotonic_ns() - entry.mono_ns


def _time_usable(entry: _Entry, now_ns: int, ttl_s: int, age: int | None = None) -> bool:
    ttl_ns = ttl_s * NS
    if age is None:
        age = _age_ns(entry)
    if age < 0 or age > entry.ttl_ns or age > ttl_ns:
        return False
    if entry.computed_ns > now_ns:
        return False
    return now_ns - entry.computed_ns <= ttl_ns


def _lookup(key: tuple, now_ns: int, ttl_s: int) -> _Entry | None:
    with _LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        age = _age_ns(entry)
        if age < 0 or age > entry.ttl_ns:
            del _CACHE[key]
            return None
        if not _time_usable(entry, now_ns, ttl_s, age):
            return None
        _CACHE.move_to_end(key)
        return entry


def _remember(key: tuple, entry: _Entry) -> None:
    with _LOCK:
        current = _CACHE.get(key)
        if current is not None and current.computed_ns > entry.computed_ns:
            age = _age_ns(current)
            if 0 <= age <= current.ttl_ns:
                _CACHE.move_to_end(key)
                return
        _CACHE[key] = entry
        _CACHE.move_to_end(key)
        while len(_CACHE) > _MAX_CACHE:
            _CACHE.popitem(last=False)


def _drop_schema(key: tuple, entry: _Entry) -> None:
    with _LOCK:
        if _CACHE.get(key) is entry:
            del _CACHE[key]


def _expose(entry: _Entry, *, fresh: bool) -> dict:
    out = copy.deepcopy(entry.payload)
    out["computed_ns"] = entry.computed_ns
    if fresh:
        out["cache_age_s"] = 0.0
    else:
        out["cache_age_s"] = _age_ns(entry) / NS
    return out


def _match_cache(key: tuple, index: Path, now_ns: int, ttl_s: int) -> dict | None:
    entry = _lookup(key, now_ns, ttl_s)
    if entry is None:
        return None
    schema = _peek(index)
    if schema != entry.schema:
        _drop_schema(key, entry)
        return None
    current = _lookup(key, now_ns, ttl_s)
    if current is None or current.schema != schema:
        return None
    return _expose(current, fresh=False)


def _join_or_lead(key: tuple, now_ns: int, ttl_s: int) -> tuple[_Flight | None, str]:
    ttl_ns = ttl_s * NS
    with _LOCK:
        flight = _FLIGHTS.get(key)
        if flight is not None and 0 <= now_ns - flight.cutoff <= ttl_ns:
            return flight, "waiter"
        if flight is None:
            created = _Flight(now_ns)
            _FLIGHTS[key] = created
            return created, "leader"
        return None, "alone"


def _release(
    key: tuple, flight: _Flight, entry: _Entry | None, error: BaseException | None
) -> None:
    flight.entry = entry
    flight.error = error
    try:
        with _LOCK:
            if _FLIGHTS.get(key) is flight:
                del _FLIGHTS[key]
    finally:
        flight.event.set()


def _store_entry(key: tuple, now_ns: int, ttl_s: int, payload: dict, schema: str) -> _Entry:
    entry = _Entry(now_ns, time.monotonic_ns(), ttl_s * NS, schema, payload)
    if not _observe_clocks():
        _remember(key, entry)
    return entry


def _compute(key: tuple, index: Path, now_ns: int, ttl_s: int) -> dict:
    payload, schema = _read(index, now_ns)
    if ttl_s <= 0:
        entry = _Entry(now_ns, time.monotonic_ns(), 0, schema, payload)
        return _expose(entry, fresh=True)
    entry = _store_entry(key, now_ns, ttl_s, payload, schema)
    return _expose(entry, fresh=True)


def _lead(key: tuple, index: Path, now_ns: int, ttl_s: int, flight: _Flight) -> dict:
    entry: _Entry | None = None
    error: BaseException | None = None
    try:
        payload, schema = _read(index, now_ns)
        entry = _store_entry(key, now_ns, ttl_s, payload, schema)
        return _expose(entry, fresh=True)
    except BaseException as exc:
        error = exc
        entry = None
        raise
    finally:
        _release(key, flight, entry, error)


def _wait(key: tuple, index: Path, now_ns: int, ttl_s: int, flight: _Flight) -> dict:
    if not flight.event.wait(30):
        raise TimeoutError("readmodel_coalesce_timeout")
    if flight.error is not None:
        raise flight.error
    cached = _match_cache(key, index, now_ns, ttl_s)
    if cached is not None:
        return cached
    if flight.entry is not None and flight.entry.schema == _peek(index):
        if _time_usable(flight.entry, now_ns, ttl_s):
            return _expose(flight.entry, fresh=False)
    return _compute(key, index, now_ns, ttl_s)


def coverage(source: Path, now_ns: int, *, ttl_s: int = 15) -> dict:
    """First-observed bars with ``available <= now_ns``.

    Cached results are an older cutoff, never a newer one, and lag visible growth by
    at most ``ttl_s``. The returned dict is private to the caller.
    """
    _validate(now_ns, ttl_s)
    _root, index, key = _locate(source)
    _observe_clocks()
    if ttl_s == 0:
        return _compute(key, index, now_ns, 0)
    cached = _match_cache(key, index, now_ns, ttl_s)
    if cached is not None:
        return cached
    flight, role = _join_or_lead(key, now_ns, ttl_s)
    if role == "leader" and flight is not None:
        return _lead(key, index, now_ns, ttl_s, flight)
    if role == "waiter" and flight is not None:
        return _wait(key, index, now_ns, ttl_s, flight)
    return _compute(key, index, now_ns, ttl_s)
