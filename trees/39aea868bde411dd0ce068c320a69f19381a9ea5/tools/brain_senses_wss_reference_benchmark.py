#!/usr/bin/env python3
"""Replay selected captured blocks to estimate Pump logsSubscribe payload volume."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memecoin_collector.wss_reference import PUMP_BONDING_CURVE_PROGRAM  # noqa: E402
from tools.brain_senses_program_exposure_ledger_v1 import (  # noqa: E402
    _load_specs,
    _paired_blocks,
    _Reader,
)

SCHEMA = "senses_pump_wss_reference_payload_benchmark_v1"
DATASET_IDS = {
    "pair64",
    *(f"c3:20261010T{hour:02d}0000Z" for hour in range(5, 11)),
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _mention_program(transaction: dict[str, Any]) -> bool:
    tx = transaction.get("transaction")
    message = tx.get("message") if isinstance(tx, dict) else None
    meta = transaction.get("meta")
    if not isinstance(message, dict) or not isinstance(meta, dict):
        raise ValueError("benchmark_transaction_shape_unsupported")
    keys = message.get("accountKeys")
    loaded = meta.get("loadedAddresses")
    if not isinstance(keys, list) or (loaded is not None and not isinstance(loaded, dict)):
        raise ValueError("benchmark_account_keys_shape_unsupported")
    if any(not isinstance(key, str) for key in keys):
        raise ValueError("benchmark_account_key_encoding_unsupported")
    if PUMP_BONDING_CURVE_PROGRAM in keys:
        return True
    for side in ("writable", "readonly"):
        values = loaded.get(side, []) if isinstance(loaded, dict) else []
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError("benchmark_loaded_address_shape_unsupported")
        if PUMP_BONDING_CURVE_PROGRAM in values:
            return True
    return False


def _notification(slot: int, signature: str, err: Any, logs: Any) -> bytes:
    if type(slot) is not int or slot < 0 or not isinstance(signature, str) or not signature:
        raise ValueError("benchmark_notification_identity_invalid")
    if logs is not None and (
        not isinstance(logs, list) or any(not isinstance(line, str) for line in logs)
    ):
        raise ValueError("benchmark_logs_shape_unsupported")
    value = {"signature": signature, "err": err, "logs": logs}
    message = {
        "jsonrpc": "2.0",
        "method": "logsNotification",
        "params": {
            "result": {"context": {"slot": slot}, "value": value},
            "subscription": 1,
        },
    }
    return json.dumps(message, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def build_receipt(*, decision_time_ns: int | None = None) -> dict[str, Any]:
    zstd = shutil.which("zstd")
    if zstd is None:
        raise ValueError("benchmark_zstd_unavailable")
    started = time.monotonic_ns()
    reader = _Reader()
    specs = _load_specs(reader, zstd)
    selected = {spec["dataset_id"]: spec for spec in specs if spec["dataset_id"] in DATASET_IDS}
    if set(selected) != DATASET_IDS:
        raise ValueError("benchmark_dataset_set_mismatch")
    datasets: dict[str, dict[str, Any]] = {}
    combined = defaultdict(int)
    max_payload_bytes = 0
    min_payload_bytes: int | None = None

    for dataset_id in sorted(DATASET_IDS):
        spec = selected[dataset_id]
        counts: dict[str, Any] = {
            "selected_blocks": 0,
            "all_transactions": 0,
            "program_mentions": 0,
            "unsupported_mention_rows": 0,
            "failed_transactions": 0,
            "null_logs": 0,
            "truncated_log_notifications": 0,
            "notification_payload_bytes": 0,
            "minimum_notification_payload_bytes": None,
            "maximum_notification_payload_bytes": 0,
        }
        for block in _paired_blocks(reader, spec):
            counts["selected_blocks"] += 1
            for transaction in block["full"]["transactions"]:
                counts["all_transactions"] += 1
                if not _mention_program(transaction):
                    continue
                tx = transaction["transaction"]
                signatures = tx.get("signatures")
                meta = transaction.get("meta")
                if (
                    not isinstance(signatures, list)
                    or not signatures
                    or not isinstance(signatures[0], str)
                    or not isinstance(meta, dict)
                    or "err" not in meta
                    or "logMessages" not in meta
                ):
                    counts["unsupported_mention_rows"] += 1
                    continue
                logs = meta["logMessages"]
                if logs is None:
                    counts["null_logs"] += 1
                elif any("Log truncated" in line for line in logs):
                    counts["truncated_log_notifications"] += 1
                if meta["err"] is not None:
                    counts["failed_transactions"] += 1
                payload = _notification(block["slot"], signatures[0], meta["err"], logs)
                size = len(payload)
                counts["program_mentions"] += 1
                counts["notification_payload_bytes"] += size
                counts["maximum_notification_payload_bytes"] = max(
                    counts["maximum_notification_payload_bytes"], size
                )
                current_min = counts["minimum_notification_payload_bytes"]
                counts["minimum_notification_payload_bytes"] = (
                    size if current_min is None else min(current_min, size)
                )
                max_payload_bytes = max(max_payload_bytes, size)
                min_payload_bytes = (
                    size if min_payload_bytes is None else min(min_payload_bytes, size)
                )
        datasets[dataset_id] = {
            "counts": counts,
            "source_lineage": spec["lineage"],
        }
        for key in (
            "selected_blocks",
            "all_transactions",
            "program_mentions",
            "unsupported_mention_rows",
            "failed_transactions",
            "null_logs",
            "truncated_log_notifications",
            "notification_payload_bytes",
        ):
            combined[key] += counts[key]

    elapsed_ns = time.monotonic_ns() - started
    return {
        "schema": SCHEMA,
        "decision_time_ns": decision_time_ns or time.time_ns(),
        "method": {
            "scope": ("selected captured Helius full blocks: Pair64 and c3 frames 05:00-10:00 UTC"),
            "mention_rule": (
                "program address in static message accountKeys or loaded "
                "writable/readonly addresses"
            ),
            "payload_shape": (
                "compact standard logsNotification envelope; subscription ID placeholder 1"
            ),
            "payload_bytes": (
                "UTF-8 application JSON bytes; excludes WebSocket framing, "
                "TLS and provider overhead"
            ),
            "interpretation": (
                "sampled selected-block payload measurement; not a live "
                "rate or completeness estimate"
            ),
        },
        "source_code": {
            "benchmark_tool_sha256": _sha(Path(__file__).read_bytes()),
            "program_exposure_ledger_sha256": _sha(
                (ROOT / "tools/brain_senses_program_exposure_ledger_v1.py").read_bytes()
            ),
            "wss_reference_sha256": _sha(
                (ROOT / "src/memecoin_collector/wss_reference.py").read_bytes()
            ),
        },
        "program_id": PUMP_BONDING_CURVE_PROGRAM,
        "datasets": datasets,
        "combined_counts": dict(combined),
        "notification_payload_bytes_minimum": min_payload_bytes,
        "notification_payload_bytes_maximum": max_payload_bytes,
        "replay_measurements": {
            "compressed_and_receipt_bytes_read": reader.bytes_read,
            "elapsed_ns": elapsed_ns,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--decision-time-ns", type=int)
    args = parser.parse_args(argv)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = _canonical(build_receipt(decision_time_ns=args.decision_time_ns)) + b"\n"
    try:
        with output.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
    except FileExistsError as exc:
        raise ValueError("benchmark_output_exists_refusing_overwrite") from exc
    print(json.dumps({"status": "complete", "output": str(output), "bytes": len(encoded)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
