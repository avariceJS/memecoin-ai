"""Read-only WAL export into portable, independently qualified research source bundles."""

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from collections import Counter
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from memecoin_brain.source import BAR, EXCLUDED, NS, bars
from memecoin_registry.contracts import digest as legacy_digest

from .records import checked_rows
from .storage import (
    atomic_json,
    canonical,
    code_identity,
    file_hash,
    lock,
    read_manifest,
    safe_path,
    seal_files,
    sync_dir,
    verify_files,
)

VERSION = "continuous-qualified-source-v1"
NAMESPACE = "geckoterminal/usd/base/5min/first-qualified-receipt"


def source_plan(plan):
    required = {"role", "cutoff_ns", "start_s", "end_s", "selection", "pools"}
    if not required <= set(plan) or set(plan) - required - {"intervals"}:
        raise ValueError("experience_export_plan")
    if plan["role"] not in {"train", "research_evaluation"}:
        raise ValueError("experience_reserved_source")
    if (
        any(type(plan[k]) is not int for k in ("cutoff_ns", "start_s", "end_s"))
        or not 0 <= plan["start_s"] < plan["end_s"] <= plan["cutoff_ns"] // NS
        or plan["start_s"] % BAR
        or plan["end_s"] % BAR
        or not isinstance(plan["selection"], str)
        or not plan["selection"]
        or not plan["pools"]
    ):
        raise ValueError("experience_export_plan")
    seen = set()
    for pool in plan["pools"]:
        if pool.get("pool") in EXCLUDED:
            raise ValueError("experience_protected_pool")
        if (
            set(pool) != {"pool", "base_mint", "quote_mint"}
            or any(not isinstance(v, str) or not v for v in pool.values())
            or pool["pool"] in seen
        ):
            raise ValueError("experience_export_enrollment")
        seen.add(pool["pool"])
    if "intervals" in plan:
        if not isinstance(plan["intervals"], dict) or set(plan["intervals"]) != seen:
            raise ValueError("experience_export_intervals")
        for intervals in plan["intervals"].values():
            previous = plan["start_s"]
            if not isinstance(intervals, list):
                raise ValueError("experience_export_intervals")
            for pair in intervals:
                if (
                    not isinstance(pair, list)
                    or len(pair) != 2
                    or any(type(t) is not int or t % BAR for t in pair)
                    or not previous <= pair[0] < pair[1] <= plan["end_s"]
                ):
                    raise ValueError("experience_export_intervals")
                previous = pair[1]
    return plan


def selected(plan, pool, t):
    return any(
        start <= t < end
        for start, end in plan.get("intervals", {}).get(pool, [[plan["start_s"], plan["end_s"]]])
    )


def qualify_capture(path, pool):
    """One bounded capture. Namespace failures quarantine; integrity failures abort export."""
    verify_files(path)
    request = json.loads((path / "request.json").read_text())
    receipt = json.loads((path / "receipt.json").read_text())
    if receipt.get("response_sha256") != file_hash(path / "response.bin"):
        raise ValueError("experience_response_binding")
    if not request["started_ns"] <= receipt["received_ns"] <= receipt["available_ns"]:
        raise ValueError("experience_receipt_order")
    url = urlparse(request["url"])
    query = parse_qs(url.query, keep_blank_values=True)
    if (
        set(query)
        - {"aggregate", "currency", "token", "include_empty_intervals", "limit", "before_timestamp"}
        or any(
            len(query[k]) != 1 or not query[k][0].isdigit()
            for k in ("limit", "before_timestamp")
            if k in query
        )
        or url.scheme != "https"
        or url.netloc != "api.geckoterminal.com"
        or url.path != f"/api/v2/networks/solana/pools/{pool['pool']}/ohlcv/minute"
        or any(
            query.get(k) != [v]
            for k, v in dict(
                aggregate="5", currency="usd", token="base", include_empty_intervals="false"
            ).items()
        )
    ):
        return [], "unsupported_namespace"
    if receipt.get("http_status", 200) != 200:
        return [], "http_failure"
    if receipt.get("status", "received") not in {"received", "completed"}:
        return [], "capture_failure"
    try:
        payload = json.loads((path / "response.bin").read_text())
    except (ValueError, UnicodeError):
        return [], "malformed_payload"
    if not isinstance(payload, dict):
        return [], "malformed_payload"
    metadata = payload.get("meta")
    if not isinstance(metadata, dict) or any(
        not isinstance(metadata.get(side), dict)
        or metadata[side].get("address") != pool[side + "_mint"]
        for side in ("base", "quote")
    ):
        return [], "mint_mismatch"
    try:
        raw = payload["data"]["attributes"]["ohlcv_list"]
    except (KeyError, TypeError):
        return [], "malformed_payload"
    if not isinstance(raw, list) or any(
        not isinstance(r, list) or len(r) != 6 or any(type(v) not in (int, float) for v in r)
        for r in raw
    ):
        return [], "bar_schema_or_alignment"
    try:
        return bars(path, pool["pool"]), "qualified"
    except OverflowError:
        return [], "invalid_OHLCV"
    except ValueError as exc:
        if str(exc) not in {
            "invalid_OHLCV",
            "bar_schema_or_alignment",
            "duplicate_bar_in_response",
        }:
            raise
        return [], str(exc)


def _copy_capture(source, target, pin):
    verify_files(source, pin)
    if not target.exists():
        shutil.copytree(source, target)
        for path in target.iterdir():
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
        sync_dir(target)
    verify_files(target, pin)


def export(index, output, plan):
    """Snapshot indexed capture enrollment; never connect through the writer's connect()."""
    plan = source_plan(plan)  # Roles and protected pool IDs before opening any index/payload.
    index, output = safe_path(index), safe_path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with lock(output.parent / ("." + output.name + ".export.lock")):
        if output.exists():
            raise ValueError("experience_export_exists")
        staging = Path(tempfile.mkdtemp(prefix=".export-", dir=output.parent))
        db = spool = None
        try:
            db = sqlite3.connect(index.as_uri() + "?mode=ro", uri=True)
            spool = sqlite3.connect(staging / "scratch.sqlite")
            db.execute("create temp table enrolled(pool text primary key)")
            db.executemany("insert into enrolled values(?)", ((p["pool"],) for p in plan["pools"]))
            db.commit()
            db.execute("pragma query_only=ON")
            db.execute("BEGIN DEFERRED")
            # Fix the WAL read version now, including when the index is initially empty.
            db.execute("select count(*) from captures").fetchone()
            spool.execute("pragma cache_size=-8192")
            spool.execute("pragma temp_store=FILE")
            spool.execute(
                "create table rows(pool text,t integer,av integer,pin text,payload text,"
                "primary key(pool,t)) without rowid"
            )
            (staging / "captures").mkdir()
            meta = staging / "meta"
            meta.mkdir()
            totals = Counter()
            with (meta / "captures.jsonl").open("wb") as evidence:
                pools = {p["pool"]: p for p in plan["pools"]}
                for pin, path, available, status, n, raw, checksum, pool_id in db.execute(
                    "select c.pin,c.path,c.available,c.status,c.n,c.record,c.checksum,e.pool "
                    "from captures c join enrolled e on json_extract(c.record,'$.pool')=e.pool "
                    "where c.available<=? order by e.pool,c.pin",
                    (plan["cutoff_ns"],),
                ):
                    pool = pools[pool_id]
                    record = json.loads(raw)
                    if legacy_digest(record) != checksum or any(
                        record[k] != v
                        for k, v in dict(
                            pin=pin, path=path, available_ns=available, status=status, n=n
                        ).items()
                    ):
                        raise ValueError("experience_index_capture_corruption")
                    cap = safe_path(path)
                    verify_files(cap, pin)
                    rows, reason = qualify_capture(cap, pool)
                    if reason == "qualified":
                        cached = db.execute(
                            "select payload,checksum from versions where pin=? order by open_s",
                            (pin,),
                        )
                        count = 0
                        for row in rows:
                            found = next(cached, None)
                            if (
                                not found
                                or json.loads(found[0]) != row
                                or legacy_digest(row) != found[1]
                                or row["available_ns"] != available
                            ):
                                raise ValueError("experience_index_raw_mismatch")
                            count += 1
                            if not selected(plan, row["pool"], row["open_s"]):
                                continue
                            old = spool.execute(
                                "select av,pin,payload from rows where pool=? and t=?",
                                (row["pool"], row["open_s"]),
                            ).fetchone()
                            if old:
                                totals["duplicate_observations"] += 1
                                totals["equal_receipt_observations"] += int(old[0] == available)
                                totals["revised_observations"] += int(
                                    json.loads(old[2])["ohlcv"] != row["ohlcv"]
                                )
                                if (old[0], old[1]) <= (available, pin):
                                    continue
                            spool.execute(
                                "insert or replace into rows values(?,?,?,?,?)",
                                (
                                    row["pool"],
                                    row["open_s"],
                                    available,
                                    pin,
                                    canonical(row).decode(),
                                ),
                            )
                        if next(cached, None) is not None or count != n:
                            raise ValueError("experience_index_raw_mismatch")
                    _copy_capture(cap, staging / "captures" / pin, pin)
                    totals["indexed_captures"] += 1
                    totals[reason] += 1
                    evidence.write(
                        canonical(dict(pin=pin, pool=pool["pool"], reason=reason)) + b"\n"
                    )
                    spool.commit()
                evidence.flush()
                os.fsync(evidence.fileno())
            db.rollback()
            with (meta / "rows.jsonl").open("wb") as stream:
                for (payload,) in spool.execute("select payload from rows order by pool,t"):
                    stream.write(payload.encode() + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            coverage = []
            for pool in sorted(plan["pools"], key=lambda p: p["pool"]):
                n = spool.execute(
                    "select count(*) from rows where pool=?", (pool["pool"],)
                ).fetchone()[0]
                expected = sum(
                    (end - start) // BAR
                    for start, end in plan.get("intervals", {}).get(
                        pool["pool"], [[plan["start_s"], plan["end_s"]]]
                    )
                )
                coverage.append(
                    dict(
                        pool=pool["pool"],
                        bars=n,
                        expected_slots=expected,
                        missing_slots=expected - n,
                    )
                )
            report = dict(
                version=VERSION,
                namespace=NAMESPACE,
                plan=plan,
                code=code_identity(),
                bars=sum(p["bars"] for p in coverage),
                coverage=coverage,
                counts=dict(totals),
                selection_policy="first_qualified_receipt_then_capture_sha",
                denominator_scope=(
                    "enrolled_pools_and_indexed_captures; unindexed_network_failures_unknown"
                ),
                historical_online_availability_known=False,
            )
            atomic_json(meta / "source.json", report)
            pin = seal_files(meta)
            spool.close()
            (staging / "scratch.sqlite").unlink()
            sync_dir(staging / "captures")
            sync_dir(staging)
            os.rename(staging, output)
            sync_dir(output.parent)
            return dict(pin=pin, **report)
        finally:
            if db is not None:
                db.close()
            if spool is not None:
                spool.close()
            if staging.exists():
                shutil.rmtree(staging)


def source(reference, cutoff, *, role="train"):
    root = safe_path(reference["path"])
    meta = safe_path(root / "meta")
    raw = safe_path(meta / "source.json").read_bytes()
    report = json.loads(raw)
    plan = source_plan(report["plan"])
    if plan["role"] != role:
        raise ValueError("experience_reserved_source")
    inventory = read_manifest(meta, reference["pin"])
    if hashlib.sha256(raw).hexdigest() != inventory.get("source.json"):
        raise ValueError("experience_source_report_pin")
    verify_files(meta, reference["pin"])
    if report["version"] != VERSION or report["namespace"] != NAMESPACE:
        raise ValueError("experience_source_namespace")
    if cutoff < plan["cutoff_ns"]:
        raise ValueError("experience_cutoff_before_export")
    pools = {p["pool"]: p for p in plan["pools"]}
    # The source pin binds this inventory transitively, including quarantined evidence.
    from .storage import PIN

    with (meta / "captures.jsonl").open() as evidence:
        for line in evidence:
            record = json.loads(line)
            pin = record["pin"]
            if record["pool"] not in pools or not isinstance(pin, str) or not PIN.fullmatch(pin):
                raise ValueError("experience_capture_inventory")
            verify_files(safe_path(root / "captures" / pin), pin)

    @lru_cache(maxsize=8)
    def raw_rows(pin, pool):
        from .storage import PIN

        if not isinstance(pin, str) or not PIN.fullmatch(pin):
            raise ValueError("experience_raw_pin")
        cap = safe_path(root / "captures" / pin)
        verify_files(cap, pin)
        rows, reason = qualify_capture(cap, pools[pool])
        if reason != "qualified":
            raise ValueError("experience_unqualified_source")
        return {r["open_s"]: r for r in rows}

    n = 0
    with (meta / "rows.jsonl").open() as stream:
        for row in checked_rows((json.loads(line) for line in stream), cutoff):
            if (
                row["pool"] not in pools
                or not selected(plan, row["pool"], row["open_s"])
                or raw_rows(row["capture_sha"], row["pool"]).get(row["open_s"]) != row
            ):
                raise ValueError("experience_raw_row_mismatch")
            n += 1
            yield row
    if n != report["bars"]:
        raise ValueError("experience_source_count")
