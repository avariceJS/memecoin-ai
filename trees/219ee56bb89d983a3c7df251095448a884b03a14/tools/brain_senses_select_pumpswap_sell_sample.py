#!/usr/bin/env python3
"""Select disjoint seeded development and sealed audit sets from immutable block captures."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from memecoin_pumpswap.decoder import PROGRAM, TOKEN  # noqa: E402
from memecoin_pumpswap.normalize import normalize_compiled_transaction  # noqa: E402
from memecoin_pumpswap.schema import SCHEMA  # noqa: E402


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _block(receipt_path: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    storage = receipt.get("storage")
    if not isinstance(storage, dict) or not isinstance(storage.get("path"), str):
        raise ValueError("capture_storage_path_missing")
    payload = receipt_path.parent / storage["path"]
    encoded = payload.read_bytes()
    if hashlib.sha256(encoded).hexdigest() != storage.get("sha256"):
        raise ValueError("stored_capture_hash_mismatch")
    if payload.suffix == ".zst":
        raw = subprocess.run(
            ["zstd", "-q", "-d", "-c", str(payload)], check=True, capture_output=True
        ).stdout
    else:
        raw = encoded
    if hashlib.sha256(raw).hexdigest() != receipt.get("response_sha256") or len(raw) != receipt.get(
        "response_bytes"
    ):
        raise ValueError("raw_capture_hash_or_size_mismatch")
    result = json.loads(raw).get("result")
    if not isinstance(result, dict) or not isinstance(result.get("transactions"), list):
        raise ValueError("full_block_transactions_missing")
    return result


def eligible_sell23(capture_root: Path) -> list[dict[str, Any]]:
    instructions = SCHEMA["instructions"]
    discriminators = {bytes(value["discriminator"]): name for name, value in instructions.items()}
    sell_discriminator = bytes(instructions["sell"]["discriminator"])
    candidates = []
    for receipt_path in sorted(capture_root.glob("*/receipt.json")):
        receipt = _read_json(receipt_path)
        if receipt.get("method") != "getBlock":
            continue
        request = json.loads(receipt["request_json"])
        params = request.get("params")
        options = params[1] if isinstance(params, list) and len(params) > 1 else None
        if (
            not isinstance(params, list)
            or not params
            or type(params[0]) is not int
            or not isinstance(options, dict)
            or options.get("transactionDetails") != "full"
        ):
            continue
        slot = params[0]
        block = _block(receipt_path, receipt)
        for transaction in block["transactions"]:
            if (transaction.get("meta") or {}).get("err") is not None:
                continue
            normalized = normalize_compiled_transaction(transaction)
            pump_calls = []
            for ix in [*normalized["outer_instructions"], *normalized["inner_instructions"]]:
                if ix["program_id"] != PROGRAM:
                    continue
                data = bytes.fromhex(ix["data_hex"])
                kind = discriminators.get(data[:8])
                if kind is not None:
                    pump_calls.append((ix, kind, data))
            if len(pump_calls) != 1:
                continue
            ix, kind, data = pump_calls[0]
            if (
                kind != "sell"
                or data[:8] != sell_discriminator
                or len(data) != 24
                or len(ix["accounts"]) != 23
                or ix["accounts"][11] != TOKEN
                or ix["accounts"][12] != TOKEN
            ):
                continue
            roles = dict(zip(instructions["sell"]["accounts"], ix["accounts"][:21], strict=True))
            signatures = normalized.get("transaction_signatures")
            signature = signatures[0] if isinstance(signatures, list) and signatures else None
            if not isinstance(signature, str):
                continue
            candidates.append(
                {
                    "slot": slot,
                    "signature": signature,
                    "pool": roles["pool"],
                    "recipient": ix["accounts"][21],
                    "quote_mint": roles["quote_mint"],
                    "path": ix["path"],
                    "account_count": len(ix["accounts"]),
                    "token_program": "legacy_legacy",
                }
            )
    return candidates


def _choose(
    population: list[dict[str, Any]], seed: str, excluded: set[str], count: int
) -> list[dict[str, Any]]:
    rows = [dict(row) for row in population if row["signature"] not in excluded]
    for row in rows:
        row["_rank"] = hashlib.sha256(f"{seed}|{row['signature']}".encode()).hexdigest()
    rows.sort(key=lambda row: row["_rank"])
    selected: list[dict[str, Any]] = []
    pool_counts: Counter[str] = Counter()
    time_counts: Counter[int] = Counter()
    while rows and len(selected) < count:
        row = min(
            rows,
            key=lambda item: (
                pool_counts[item["pool"]],
                time_counts[item["slot"] // 250],
                item["_rank"],
            ),
        )
        selected.append(row)
        rows.remove(row)
        pool_counts[row["pool"]] += 1
        time_counts[row["slot"] // 250] += 1
    if len(selected) != count:
        raise ValueError("eligible_population_too_small")
    return selected


def _manifest(
    name: str,
    rows: list[dict[str, Any]],
    seed: str,
    population_count: int,
    paired_slots: set[int],
) -> dict[str, Any]:
    items = []
    for row in rows:
        item = {key: value for key, value in row.items() if key not in ("_rank", "signature")}
        item["signature_sha256"] = hashlib.sha256(row["signature"].encode()).hexdigest()
        item["alchemy_same_slot_block_captured"] = item["slot"] in paired_slots
        items.append(item)
    return {
        "schema_version": 1,
        "sample": name,
        "status": "selected_not_decoded",
        "selection_seed_sha256": hashlib.sha256(seed.encode()).hexdigest(),
        "selection_rule": (
            "one successful transaction, exactly one recognized PumpSwap instruction, sell "
            "discriminator, 24-byte payload, 23 accounts, legacy Token programs; pool/time "
            "spread by seeded hash; no event/fee/outcome predicates"
        ),
        "eligible_population": population_count,
        "selected_count": len(items),
        "unique_pools": len({item["pool"] for item in items}),
        "unique_slots": len({item["slot"] for item in items}),
        "slot_bins_250": dict(Counter(item["slot"] // 250 for item in items)),
        "same_slot_alchemy_block_captures": sum(
            item["alchemy_same_slot_block_captured"] for item in items
        ),
        "items": items,
    }


def select(
    capture_root: Path, paired_block_root: Path, output_dir: Path, count: int = 50
) -> dict[str, Any]:
    candidates = eligible_sell23(capture_root)
    paired_slots = set()
    for receipt_path in paired_block_root.glob("*/receipt.json"):
        receipt = _read_json(receipt_path)
        if receipt.get("method") != "getBlock":
            continue
        params = json.loads(receipt["request_json"]).get("params")
        options = params[1] if isinstance(params, list) and len(params) > 1 else None
        if (
            isinstance(params, list)
            and params
            and type(params[0]) is int
            and isinstance(options, dict)
            and options.get("transactionDetails") == "full"
        ):
            paired_slots.add(params[0])

    seed_prefix = "pumpswap-sell23-sample-20261009-v1"
    audit = _choose(candidates, f"{seed_prefix}-audit", set(), count)
    development = _choose(
        candidates,
        f"{seed_prefix}-development",
        {row["signature"] for row in audit},
        count,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir.chmod(0o700)
    for name, rows, seed in (
        ("development", development, f"{seed_prefix}-development"),
        ("audit_sealed", audit, f"{seed_prefix}-audit"),
    ):
        target = output_dir / f"{name}.json"
        target.write_text(
            json.dumps(
                _manifest(name, rows, seed, len(candidates), paired_slots), indent=2, sort_keys=True
            )
            + "\n"
        )
        target.chmod(0o600)
    return {
        "eligible_population": len(candidates),
        "unique_pools": len({row["pool"] for row in candidates}),
        "unique_slots": len({row["slot"] for row in candidates}),
        "development_count": len(development),
        "audit_count": len(audit),
        "signature_overlap": 0,
        "helius_requests_made": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--paired-block-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--count-per-sample", type=int, default=50)
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                select(
                    args.capture_root,
                    args.paired_block_root,
                    args.output_dir,
                    args.count_per_sample,
                ),
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(f"sample selection failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
