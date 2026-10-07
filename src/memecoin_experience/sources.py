"""Streaming adapter for sealed, admitted USD corpora. No provider or runtime access."""

import hashlib
import json
import sqlite3
import tempfile
from functools import lru_cache
from pathlib import Path

from memecoin_brain.source import EXCLUDED
from memecoin_curriculum.admission import NAMESPACE, qualify

from .records import checked_rows
from .storage import canonical, file_hash, read_manifest, safe_path, verify_files


def admitted(reference, cutoff):
    root = safe_path(reference["path"])
    # Check role before opening/hashing any market payload, including reserved evaluation data.
    report_bytes = safe_path(root / "admission.json").read_bytes()
    report = json.loads(report_bytes)
    if report["role"] != "train" or any(r["pool"] in EXCLUDED for r in report["records"]):
        raise ValueError("experience_reserved_source")
    inventory = read_manifest(root, reference["pin"])
    if hashlib.sha256(report_bytes).hexdigest() != inventory.get("admission.json"):
        raise ValueError("experience_source_report_pin")
    verify_files(root, reference["pin"])
    if report["namespace"] != NAMESPACE or report["rows_sha"] != file_hash(root / "rows.jsonl"):
        raise ValueError("experience_source_namespace")
    captures = {r["pool"]: r for r in report["records"]}
    if len(captures) != len(report["records"]):
        raise ValueError("experience_duplicate_admission")

    @lru_cache(maxsize=8)
    def raw(pool):
        record = captures[pool]
        path = safe_path(record["capture"])
        if file_hash(path / "manifest.json") != record["capture_sha"]:
            raise ValueError("experience_raw_pin")
        rows, _ = qualify(path, record["birth"])
        return {r["open_s"]: r for r in rows}

    count = 0
    with (root / "rows.jsonl").open() as stream:
        for row in checked_rows((json.loads(line) for line in stream), cutoff):
            if row["pool"] not in captures or raw(row["pool"]).get(row["open_s"]) != row:
                raise ValueError("experience_raw_row_mismatch")
            count += 1
            yield row
    if count != report["bars"]:
        raise ValueError("experience_source_count")


def inputs(references, cutoff, counters):
    """Disk merge; bounded open files even with thousands of independently admitted corpora."""
    if not references or any(r.get("kind") != "admitted" for r in references):
        raise ValueError("experience_source_kind")
    counters.update(source_rows=0, duplicate_rows=0, revised_keys=0)
    if len(references) == 1:
        for row in admitted(references[0], cutoff):
            counters["source_rows"] += 1
            yield row
        return
    with tempfile.TemporaryDirectory(prefix="experience-merge-") as directory:
        db = sqlite3.connect(Path(directory) / "merge.sqlite")
        try:
            db.execute("pragma cache_size=-8192")
            db.execute("pragma temp_store=FILE")
            db.execute(
                "create table rows(pool text, t integer, payload text, revised integer, "
                "primary key(pool,t)) without rowid"
            )
            for reference in references:
                for row in admitted(reference, cutoff):
                    counters["source_rows"] += 1
                    key = row["pool"], row["open_s"]
                    found = db.execute(
                        "select payload,revised from rows where pool=? and t=?", key
                    ).fetchone()
                    revised = 0
                    if found:
                        old, revised = json.loads(found[0]), found[1]
                        counters["duplicate_rows"] += 1
                        if not revised and old["ohlcv"] != row["ohlcv"]:
                            revised = 1
                            counters["revised_keys"] += 1
                        row = min((old, row), key=lambda r: (r["available_ns"], r["capture_sha"]))
                    db.execute(
                        "insert or replace into rows values(?,?,?,?)",
                        (*key, canonical(row).decode(), revised),
                    )
                    if counters["source_rows"] % 4096 == 0:
                        db.commit()
            db.commit()
            for (payload,) in db.execute("select payload from rows order by pool,t"):
                yield json.loads(payload)
        finally:
            db.close()
