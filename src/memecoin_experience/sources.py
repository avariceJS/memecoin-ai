"""Streaming adapter for sealed, admitted USD corpora. No provider or runtime access."""

import heapq
import itertools
import json
from functools import lru_cache

from memecoin_brain.source import EXCLUDED
from memecoin_curriculum.admission import NAMESPACE, qualify

from .records import checked_rows
from .storage import file_hash, safe_path, verify_files


def admitted(reference, cutoff):
    root = safe_path(reference["path"])
    # Check role before opening/hashing any market payload, including reserved evaluation data.
    report = json.loads((root / "admission.json").read_text())
    if report["role"] != "train" or any(r["pool"] in EXCLUDED for r in report["records"]):
        raise ValueError("experience_reserved_source")
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
    """K-way merge; earliest receipt wins. Revisions remain recoverable in pinned sources."""
    if not references or any(r.get("kind") != "admitted" for r in references):
        raise ValueError("experience_source_kind")
    streams = [admitted(r, cutoff) for r in references]
    merged = heapq.merge(*streams, key=lambda r: (r["pool"], r["open_s"]))
    for _, group in itertools.groupby(merged, key=lambda r: (r["pool"], r["open_s"])):
        versions = list(group)  # At most one row per source, never the full market corpus.
        chosen = min(versions, key=lambda r: (r["available_ns"], r["capture_sha"]))
        counters["source_rows"] += len(versions)
        counters["duplicate_rows"] += len(versions) - 1
        counters["revised_keys"] += int(any(r["ohlcv"] != chosen["ohlcv"] for r in versions))
        yield chosen
