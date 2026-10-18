"""Independent structural replay for the Gecko capture-only journal."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from tools import brain_senses_gecko_lane_verify_v5 as v4_verify
from tools.brain_senses_external_writer_v1 import sha256


def verify_capture_prefix(store: Any, plan: Mapping[str, Any]) -> dict[str, Any]:
    """Replay capture intents, raw bodies, clocks, terminal results, and confirmations."""
    intents = [
        store.read_json(f"intents/{name}")
        for name in sorted(store.files("intents"))
        if name.endswith(".json")
    ]
    terminals = [
        store.read_json(f"results/{name}")
        for name in sorted(store.files("results"))
        if name.endswith(".json")
    ]
    raw: dict[str, bytes] = {}
    for result in terminals:
        request_id = result.get("request_id")
        path = f"captures/{request_id}.bin"
        if store.writer.exists(store._path(path)):
            raw[str(request_id)] = store.writer.read_bytes(
                store._path(path), max_bytes=32 * 1024 * 1024
            )
    dispatch = store.read_dispatch_records()
    confirmations = store.read_confirmation_records()
    skips = []
    for name in sorted(store.files("skips")):
        if name.endswith(".json"):
            skips.append(store.read_json(f"skips/{name}"))
    clock_files = {
        f"{store.root}/clocks/{name}": store.writer.read_bytes(
            store._path(f"clocks/{name}"), max_bytes=64 * 1024
        )
        for name in sorted(store.files("clocks"))
        if name.endswith(".json")
    }
    closes = sorted(
        {
            row["request"]["bucket_close_ns"]
            for row in intents
            if isinstance(row.get("request"), Mapping)
        }
        | {row["bucket_close_ns"] for row in skips if type(row.get("bucket_close_ns")) is int}
    )
    result = v4_verify.verify_journal_v5(
        dict(plan["policy"]),
        intents,
        terminals,
        raw,
        dispatch,
        root=store.root,
        clock_files=clock_files,
        skips=skips,
        scheduled_closes_ns=closes,
        confirmations=confirmations,
    )
    if result.get("status") != "verified_prefix":
        raise ValueError("capture_prefix_not_verified")
    result["request_tail_sha256"] = _tail(store, "dispatch.jsonl")
    result["result_tail_sha256"] = _tail(store, "result-confirmations.jsonl")
    result["raw_response_hashes"] = {
        row["request_id"]: sha256(raw[row["request_id"]])
        for row in terminals
        if row["request_id"] in raw
    }
    result["capture_authorized"] = False
    result["market_source_qualified"] = False
    result["training_admission"] = False
    result["economic_admission"] = False
    return cast(dict[str, Any], result)


def _tail(store: Any, name: str) -> str:
    path = store._path(name)
    if not store.writer.exists(path):
        return cast(str, sha256(b""))
    return cast(str, sha256(store.writer.read_bytes(path, max_bytes=16 * 1024 * 1024)))
