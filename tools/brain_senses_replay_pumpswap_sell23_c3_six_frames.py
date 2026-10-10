from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

repo = Path.cwd()
sys.path.insert(0, str(repo / "src"))
sys.path.insert(0, str(repo / "tools"))

from brain_senses_census_pumpswap_sell_ledger import _capture  # noqa: E402

from memecoin_pumpswap.decoder import PROGRAM, TOKEN  # noqa: E402
from memecoin_pumpswap.economic_v2 import diagnose_pumpswap_sell_v2  # noqa: E402
from memecoin_pumpswap.normalize import normalize_compiled_transaction  # noqa: E402
from memecoin_pumpswap.schema import SCHEMA  # noqa: E402

parser = argparse.ArgumentParser(
    description="Offline replay of the frozen sell23 ledger v2 over c3 frames 05:00–10:00Z."
)
parser.add_argument(
    "--epoch-root",
    type=Path,
    default=repo / "work/brain-senses-live-helius-inventory-v3-20261010-3/epochs",
)
parser.add_argument(
    "--output", type=Path, default=repo / "work/brain-senses-sell23-c3-six-frame-ledger-replay.json"
)
args = parser.parse_args()
root = args.epoch_root
frame_ids = [f"20261010T{hour:02d}0000Z" for hour in range(5, 11)]
code_paths = [
    "src/memecoin_pumpswap/compiled_economics.py",
    "src/memecoin_pumpswap/economic_v2.py",
    "src/memecoin_pumpswap/legacy_token_ledger.py",
    "src/memecoin_pumpswap/normalize.py",
    "src/memecoin_pumpswap/decoder.py",
    "src/memecoin_pumpswap/schema.py",
    "tools/brain_senses_select_pumpswap_sell_sample.py",
    "tools/brain_senses_census_pumpswap_sell_ledger.py",
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


disc = {bytes(v["discriminator"]): k for k, v in SCHEMA["instructions"].items()}
sell_disc = bytes(SCHEMA["instructions"]["sell"]["discriminator"])
frames = {}
global_pool = defaultdict(Counter)
global_outcomes = Counter()
blocks_total = 0
for frame_id in frame_ids:
    d = root / frame_id
    plan = json.loads((d / "epoch-plan.json").read_text())
    summary = json.loads((d / "epoch-summary.json").read_text())
    if (
        summary.get("status") != "frame_complete"
        or plan.get("epoch_id") != frame_id
        or summary.get("epoch_id") != frame_id
    ):
        raise RuntimeError(f"{frame_id}:not_complete")
    receipt_paths = sorted((d / "captures").glob("*/receipt.json"))
    block_rows = []
    for rp in receipt_paths:
        rec = json.loads(rp.read_text())
        if rec.get("method") != "getBlock":
            continue
        req = json.loads(rec["request_json"])
        params = req.get("params")
        opts = params[1] if isinstance(params, list) and len(params) > 1 else None
        if not isinstance(opts, dict) or opts.get("transactionDetails") != "full":
            continue
        block = json.loads(_capture(rp, rec)).get("result")
        if not isinstance(block, dict) or not isinstance(block.get("transactions"), list):
            raise RuntimeError("full_block_invalid")
        block_rows.append((rp, rec, params[0], block))
    fc = Counter()
    pool_c = defaultdict(Counter)
    matched = set()
    full_blocks = len(block_rows)
    for _rp, _rec, _slot, block in block_rows:
        for tx in block["transactions"]:
            n = normalize_compiled_transaction(tx)
            calls = []
            for ix in [*n["outer_instructions"], *n["inner_instructions"]]:
                if ix["program_id"] != PROGRAM:
                    continue
                raw = bytes.fromhex(ix["data_hex"])
                kind = disc.get(raw[:8])
                if kind is not None:
                    calls.append((ix, kind, raw))
            if len(calls) != 1:
                continue
            ix, kind, data = calls[0]
            if (
                kind != "sell"
                or data[:8] != sell_disc
                or len(data) != 24
                or len(ix["accounts"]) != 23
                or ix["accounts"][11] != TOKEN
                or ix["accounts"][12] != TOKEN
            ):
                continue
            sigs = n.get("transaction_signatures")
            sig = sigs[0] if isinstance(sigs, list) and sigs else None
            if not isinstance(sig, str):
                continue
            sig_h = hashlib.sha256(sig.encode()).hexdigest()
            roles = dict(
                zip(SCHEMA["instructions"]["sell"]["accounts"], ix["accounts"][:21], strict=True)
            )
            pool = roles["pool"]
            is_failed = (tx.get("meta") or {}).get("err") is not None
            if is_failed:
                fc["failed_exact_sell23_structural_refs"] += 1
                pool_c[pool]["failed_exact_sell23_structural_refs"] += 1
                global_pool[pool]["failed_exact_sell23_structural_refs"] += 1
                continue
            if sig_h in matched:
                raise RuntimeError("duplicate_successful_candidate_signature")
            matched.add(sig_h)
            fc["eligible_successful_sell23_candidates"] += 1
            pool_c[pool]["eligible_successful_sell23_candidates"] += 1
            result = diagnose_pumpswap_sell_v2(tx)
            status = result.get("status", "status_missing")
            fc["ledger_outcome:" + status] += 1
            pool_c[pool]["ledger_outcome:" + status] += 1
            global_outcomes[status] += 1
            ledger = result.get("account_ledger") or {}
            ls = ledger.get("status", "status_missing")
            fc["account_ledger:" + ls] += 1
            pool_c[pool]["account_ledger:" + ls] += 1
            fc["target_accounts_exact"] += int(ledger.get("exact_account_count", 0))
            fc["target_accounts_censored"] += int(ledger.get("censored_account_count", 0))
            fc["target_accounts_unknown"] += int(ledger.get("unknown_account_count", 0))
            fc["target_accounts_total"] += int(ledger.get("target_account_count", 0))
            if (
                result.get("transfer_fact_state")
                == "exact_checked_inner_transfer_and_event_equations"
            ):
                for name, passed in result.get("equations", {}).items():
                    label = f"equation:{name}:pass" if passed else f"equation:{name}:fail"
                    fc[label] += 1
                    pool_c[pool][label] += 1
            settle = result.get("account_settlement_state")
            if settle:
                fc["settlement:" + settle] += 1
                pool_c[pool]["settlement:" + settle] += 1
            for reason, count in (ledger.get("censored_reasons") or {}).items():
                fc["censored_reason:" + reason] += count
                pool_c[pool]["censored_reason:" + reason] += count
            for reason, count in (ledger.get("unknown_reasons") or {}).items():
                fc["unknown_reason:" + reason] += count
                pool_c[pool]["unknown_reason:" + reason] += count
            global_pool[pool].update({f"ledger_outcome:{status}": 1, f"account_ledger:{ls}": 1})
            global_pool[pool]["eligible_successful_sell23_candidates"] += 1
            global_pool[pool]["target_accounts_exact"] += int(ledger.get("exact_account_count", 0))
            global_pool[pool]["target_accounts_censored"] += int(
                ledger.get("censored_account_count", 0)
            )
            global_pool[pool]["target_accounts_unknown"] += int(
                ledger.get("unknown_account_count", 0)
            )
    fc["unique_pools"] = len(pool_c)
    blocks_total += full_blocks
    frames[frame_id] = {
        "plan_sha256": sha(d / "epoch-plan.json"),
        "summary_sha256": sha(d / "epoch-summary.json"),
        "frame_sha256": sha(d / "frame.json"),
        "stop_sha256": sha(d / "STOP"),
        "summary_status": summary["status"],
        "selected_full_blocks_verified": full_blocks,
        "receipt_count": len(receipt_paths),
        "receipt_manifest_sha256": hashlib.sha256(
            "\n".join(f"{rp.relative_to(repo)} {sha(rp)}" for rp in receipt_paths).encode()
        ).hexdigest(),
        "counts": dict(sorted(fc.items())),
        "pool_counts": {p: dict(sorted(c.items())) for p, c in sorted(pool_c.items())},
    }
assert blocks_total == 96, f"expected 96 full blocks, got {blocks_total}"
all_candidates = sum(
    v["counts"].get("eligible_successful_sell23_candidates", 0) for v in frames.values()
)
report = {
    "schema_version": 1,
    "experiment": "pumpswap_sell23_ledger_v2_c3_six_frame_frozen_replay",
    "status": "complete_offline_replay",
    "git_commit": subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip(),
    "offline_replay": True,
    "helius_requests_made": 0,
    "c3_terminal_failed_1100_epoch_excluded": True,
    "frame_ids": frame_ids,
    "selected_full_blocks_verified": blocks_total,
    "eligible_successful_sell23_candidates": all_candidates,
    "global_ledger_outcomes": dict(sorted(global_outcomes.items())),
    "frames": frames,
    "pooled_outcome_counts": {p: dict(sorted(c.items())) for p, c in sorted(global_pool.items())},
    "code_sha256": {p: sha(repo / p) for p in code_paths},
    "replay_script_sha256": sha(Path(__file__)),
    "scope": (
        "replay of the frozen sell23 ledger v2 diagnostic on six successfully captured c3 frames; "
        "captured-class only, not global PumpSwap completeness, wallet-net execution price, "
        "training eligibility, or economic admission"
    ),
}
out = args.output
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
out.chmod(0o600)
print(
    json.dumps(
        {
            k: report[k]
            for k in (
                "status",
                "git_commit",
                "helius_requests_made",
                "selected_full_blocks_verified",
                "eligible_successful_sell23_candidates",
                "global_ledger_outcomes",
            )
        },
        sort_keys=True,
    )
)
