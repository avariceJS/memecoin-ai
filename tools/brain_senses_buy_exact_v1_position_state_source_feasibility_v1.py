#!/usr/bin/env python3
"""Offline source-capability proof for buyExact candidate-position state.

Reads only the accepted 2,333-row identifiability ledger and pinned official documentation
snapshots. It does not open captured c4 transaction bodies or contact any provider.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "pumpswap_buy_exact_v1_position_state_source_feasibility_v1"
INPUT_RECEIPT = ROOT / (
    "docs/evidence/pumpswap-buy-exact-v1-c4-economic-identifiability-validation-v1-2026-10-11.json"
)
INPUT_RECEIPT_SHA256 = "29e9d00c326f10973dde8f953b16bac136e7774d3fe94e67190a43c6832228c2"
INPUT_ROWS = ROOT / (
    "work/pumpswap-buy-exact-v1-c4-economic-identifiability-v1-final/rows.json.zst"
)
INPUT_ROWS_ZSTD_SHA256 = "21e384721f35d67ba88e3b673b03dda06776168180f4157ba76b255072230812"
INPUT_ROWS_SHA256 = "25eca394b85ad11df4325b23d3f6a8c03d5eea5a04e81418fabf754602d47d4f"
INPUT_ROW_COUNT = 2_333
SOURCE_ROOT = ROOT / "work/solana-position-state-source-snapshots-2026-10-11"
SOURCE_PINS = (
    (
        "https://solana.com/docs/rpc/json-structures",
        "solana-rpc-json-structures.html",
        "d76da51d9e591c7025d7e108c5b705804635894c2b30363d44a1e98aee9186d9",
    ),
    (
        "https://solana.com/docs/rpc/http/getaccountinfo",
        "solana-getaccountinfo.html",
        "c50abf5bb6b261f812e268a718beeae67b6d2afba6e3f7737dd9f63935442aa6",
    ),
    (
        "https://solana.com/docs/rpc/http/simulatetransaction",
        "solana-simulatetransaction.html",
        "04c150d9734847165cbf748edaf1f41e088764f84ec69a5a1db45cee28bf1237",
    ),
    (
        "https://solana.com/docs/tokens/extensions/transfer-fees",
        "token-2022-transfer-fees.html",
        "def73946b4ac423ca9f0098107527d520941bd88e9ea8c4f0dbf6224b49744b1",
    ),
    (
        "https://solana.com/docs/tokens/extensions/transfer-hook",
        "token-2022-transfer-hook.html",
        "eb8de556d689fe76f96344f2e6865c224ea540b716cd5f365132aa73ed948685",
    ),
    (
        "https://raw.githubusercontent.com/anza-xyz/agave/v3.0.0/"
        "geyser-plugin-interface/src/geyser_plugin_interface.rs",
        "agave-v3.0.0-geyser-plugin-interface.rs",
        "1bb2590b60db1d30af95588cad23124f02d1909770e11bb5534489ae7bdac7ff",
    ),
    (
        "https://raw.githubusercontent.com/anza-xyz/agave/v3.0.0/CHANGELOG.md",
        "agave-v3.0.0-changelog.md",
        "66ce166c03725cd718d5ff30a7ac6831db2f9e98866a09cf426730ac52559f0a",
    ),
)
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
DEFAULT_OUTPUT = (
    ROOT
    / "docs/evidence/pumpswap-buy-exact-v1-position-state-source-feasibility-v1-2026-10-11.json"
)
DEFAULT_ROWS_OUTPUT = (
    ROOT
    / "work/pumpswap-buy-exact-v1-position-state-source-feasibility-v1-2026-10-11/rows.json.zst"
)


class FeasibilityError(ValueError):
    """A source, denominator, or identity did not match the frozen input contract."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    return _sha(path.read_bytes())


def _load_rows() -> tuple[list[dict[str, Any]], bytes]:
    if _file_sha(INPUT_RECEIPT) != INPUT_RECEIPT_SHA256:
        raise FeasibilityError("input_receipt_hash_mismatch")
    compressed = INPUT_ROWS.read_bytes()
    if _sha(compressed) != INPUT_ROWS_ZSTD_SHA256:
        raise FeasibilityError("input_rows_compressed_hash_mismatch")
    zstd = subprocess.run(
        ["zstd", "-q", "-d", "-c", str(INPUT_ROWS)], check=True, capture_output=True
    )
    raw = zstd.stdout
    if _sha(raw) != INPUT_ROWS_SHA256:
        raise FeasibilityError("input_rows_uncompressed_hash_mismatch")
    rows = json.loads(raw)
    if not isinstance(rows, list) or len(rows) != INPUT_ROW_COUNT:
        raise FeasibilityError("input_row_count_mismatch")
    return rows, raw


def _verify_sources() -> dict[str, str]:
    result: dict[str, str] = {}
    for url, name, digest in SOURCE_PINS:
        actual = _file_sha(SOURCE_ROOT / name)
        if actual != digest:
            raise FeasibilityError(f"official_source_hash_mismatch:{name}")
        result[url] = actual
    geyser = (SOURCE_ROOT / "agave-v3.0.0-geyser-plugin-interface.rs").read_text()
    required = (
        "pub write_version: u64",
        "pub txn: Option<&'a SanitizedTransaction>",
        "pub index: usize",
        "pub struct ReplicaAccountInfoV3",
        "pub struct ReplicaTransactionInfoV2",
    )
    if any(token not in geyser for token in required):
        raise FeasibilityError("pinned_geyser_interface_claim_tokens_missing")
    return result


def _identity_key(row: dict[str, Any]) -> tuple[int, int, tuple[int, ...], str]:
    identity = row.get("identity")
    if not isinstance(identity, dict):
        raise FeasibilityError("identity_missing")
    slot, tx_index, path, signature = (
        identity.get("slot"),
        identity.get("transaction_index"),
        identity.get("path"),
        identity.get("signature"),
    )
    if (
        type(slot) is not int
        or type(tx_index) is not int
        or not isinstance(path, list)
        or not all(type(x) is int for x in path)
        or not isinstance(signature, str)
    ):
        raise FeasibilityError("identity_shape_invalid")
    return slot, tx_index, tuple(path), signature


def _classify(row: dict[str, Any]) -> dict[str, Any]:
    category = row.get("population_category")
    status = row.get("transaction_status")
    if row.get("economic_admission") is not False:
        raise FeasibilityError("economic_admission_not_false")
    allowed_categories = {
        "failed_attempt_only": {"failed_structural_pass", "failed_structural_mismatch"},
        "successful_transaction": {
            "successful_structural_pass",
            "successful_structural_mismatch",
        },
    }
    if category not in allowed_categories.get(status, set()):
        raise FeasibilityError("population_status_category_inconsistent")
    limits = row.get("state_and_position_limits")
    if not isinstance(limits, list) or len(limits) != 4:
        raise FeasibilityError("position_limit_contract_missing")
    if status == "failed_attempt_only":
        position_state = "not_applicable_failed_attempt"
        reason = (
            "captured transaction status establishes a failed attempt only; no candidate-position "
            "account state or economic zero is inferred"
        )
        required = []
    elif category == "successful_structural_mismatch":
        position_state = "unknown"
        reason = (
            "successful transaction is outside the frozen structural pass; intended candidate "
            "process is unresolved"
        )
        required = [
            "instruction-class qualification",
            "position-specific state only after class semantics are established",
        ]
    elif status == "successful_transaction" and category == "successful_structural_pass":
        position_state = "unavailable_from_documented_sources"
        reason = (
            "the row has transaction-level ordering/boundaries, but no candidate-position account "
            "bytes or mint/token extension state"
        )
        required = [
            "pre/post candidate-position bytes for each touched token account and mint",
            "ordered candidate and sibling CPI effects with transaction-position binding",
            (
                "whole-transaction lamports cannot allocate rent, close, or wrapped-native effects "
                "to the candidate"
            ),
        ]
        roles = row.get("candidate_roles_token_programs", {})
        if TOKEN_2022 in (roles.get("base_token_program"), roles.get("quote_token_program")):
            required.extend(
                [
                    (
                        "Token-2022 mint TransferFeeConfig and epoch-selected schedule at the "
                        "candidate position"
                    ),
                    "source/destination TransferFeeAmount withheld state before/after",
                    "transfer-hook mint/program/extra-account state and hook instruction effects",
                ]
            )
        raw_evidence = row.get("raw_transaction_evidence")
        if not isinstance(raw_evidence, dict):
            raise FeasibilityError("raw_transaction_evidence_missing")
        wsol_roles = raw_evidence.get("wsol_role_names")
        if not isinstance(wsol_roles, list) or any(
            not isinstance(role, str) for role in wsol_roles
        ):
            raise FeasibilityError("wsol_role_names_invalid")
        if wsol_roles:
            required.append(
                "WSOL token amount, native lamports, rent reserve, and sync/close effects at "
                "the candidate position"
            )
    else:
        raise FeasibilityError("population_status_unrecognized")
    return {
        "identity": {
            "slot": row["identity"]["slot"],
            "transaction_index": row["identity"]["transaction_index"],
            "path": row["identity"]["path"],
            "signature": row["identity"]["signature"],
        },
        "population_category": category,
        "transaction_status": status,
        "transaction_disposition": (
            "failed_attempt_exact"
            if status == "failed_attempt_only"
            else "successful_transaction_exact"
        ),
        "candidate_position_state": position_state,
        "candidate_position_state_reason": reason,
        "required_state_or_semantics": required,
        "candidate_token_program_pair": row.get("candidate_token_program_pair"),
        "economic_admission": False,
    }


def _synthetic_boundary_alias(amount_atomic: int, start_atomic: int) -> dict[str, Any]:
    """Two legal ordered transfer sequences with equal endpoints and unequal interior states."""
    if (
        type(amount_atomic) is not int
        or amount_atomic <= 0
        or type(start_atomic) is not int
        or start_atomic < amount_atomic
    ):
        raise ValueError("amounts_must_be_positive_mint_atomic_units_and_nonnegative_balances")
    # Sequence A: candidate receives x, sibling reverses x. Sequence B: candidate sends x,
    # sibling reverses x. Amounts all have unit atomic_units_of_mint; decimals are not inferred.
    a_intermediate = start_atomic + amount_atomic
    b_intermediate = start_atomic - amount_atomic
    return {
        "unit": "atomic_units_of_mint",
        "start_atomic": start_atomic,
        "end_a_atomic": start_atomic,
        "end_b_atomic": start_atomic,
        "candidate_position_a_atomic": a_intermediate,
        "candidate_position_b_atomic": b_intermediate,
        "candidate_position_diff_atomic": a_intermediate - b_intermediate,
    }


def build_receipt() -> tuple[dict[str, Any], bytes]:
    rows, input_raw = _load_rows()
    source_hashes = _verify_sources()
    identities = [_identity_key(row) for row in rows]
    if len(set(identities)) != INPUT_ROW_COUNT:
        raise FeasibilityError("duplicate_candidate_identity")
    output_rows = [_classify(row) for row in rows]
    categories = Counter(row["population_category"] for row in output_rows)
    states = Counter(row["candidate_position_state"] for row in output_rows)
    if categories != {
        "successful_structural_pass": 1550,
        "successful_structural_mismatch": 3,
        "failed_structural_pass": 767,
        "failed_structural_mismatch": 13,
    }:
        raise FeasibilityError("candidate_population_partition_mismatch")
    if states != {
        "unavailable_from_documented_sources": 1550,
        "unknown": 3,
        "not_applicable_failed_attempt": 780,
    }:
        raise FeasibilityError("candidate_position_state_partition_mismatch")
    token_2022 = sum(
        row.get("candidate_token_program_pair") == "contains_token2022" for row in rows
    )
    receipt = {
        "contract": CONTRACT,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "scope": (
            "offline source-capability proof over accepted row ledger and pinned official sources; "
            "no raw c4 capture body opened; no provider request"
        ),
        "input": {
            "receipt_path": INPUT_RECEIPT.relative_to(ROOT).as_posix(),
            "receipt_sha256": INPUT_RECEIPT_SHA256,
            "row_artifact_path": INPUT_ROWS.relative_to(ROOT).as_posix(),
            "row_artifact_compressed_sha256": INPUT_ROWS_ZSTD_SHA256,
            "row_artifact_uncompressed_sha256": _sha(input_raw),
            "row_count": len(rows),
            "unique_identity_count": len(set(identities)),
        },
        "official_source_snapshots": {
            "file_mtime_window_utc": {
                "first_snapshot_mtime": "2026-10-11T08:50:37.050051+00:00",
                "last_snapshot_mtime": "2026-10-11T08:50:57.877670+00:00",
            },
            "local_path_by_url": {
                url: (SOURCE_ROOT / filename).relative_to(ROOT).as_posix()
                for url, filename, _ in SOURCE_PINS
            },
            "sha256_by_url": source_hashes,
        },
        "implementation": {
            "tool_path": Path(__file__).resolve().relative_to(ROOT).as_posix(),
            "tool_sha256": _file_sha(Path(__file__).resolve()),
            "test_path": (
                "tests/brain/test_senses_buy_exact_v1_position_state_source_feasibility.py"
            ),
            "test_sha256": _file_sha(
                ROOT / "tests/brain/test_senses_buy_exact_v1_position_state_source_feasibility.py"
            ),
        },
        "candidate_position_state_partition": dict(sorted(states.items())),
        "candidate_population_partition": dict(sorted(categories.items())),
        "token_program_pair_counts": {
            "contains_token2022": token_2022,
            "dual_legacy": len(rows) - token_2022,
        },
        "source_capabilities": [
            {
                "source": "Solana getBlock/getTransaction transaction metadata",
                "state": "available_exact",
                "exact_scope": [
                    "transaction pre/post lamport balances",
                    "transaction pre/post token balance rows",
                    "ordered top-level and recorded inner instructions",
                    "transaction status/fee",
                ],
                "not_provided_by_documented_shape": [
                    "account bytes at a candidate instruction/CPI position",
                    "Token-2022 extension bytes at that position",
                    "instruction-level account state checkpoints",
                ],
            },
            {
                "source": "getAccountInfo/minContextSlot",
                "state": "available_exact",
                "exact_scope": ["account response at a bank context slot"],
                "not_provided_by_documented_shape": [
                    "historical transaction index or instruction index selector",
                    "original transaction-position state",
                ],
            },
            {
                "source": "simulateTransaction",
                "state": "available_exact",
                "exact_scope": [
                    "simulation under requested commitment and current node state/configuration"
                ],
                "not_provided_by_documented_shape": [
                    "proof of original historical bank state",
                    "historical candidate-position checkpoint",
                ],
            },
            {
                "source": "Agave v3.0.0 Geyser account/transaction callbacks",
                "state": "available_exact",
                "exact_scope": [
                    "account bytes and lamports with write_version",
                    "optional transaction reference on account updates",
                    "transaction index in transaction callback",
                ],
                "not_provided_by_documented_interface": [
                    "instruction/CPI index on account updates",
                    "per-instruction account state checkpoints",
                ],
            },
            {
                "source": "custom prospective Agave runtime/plugin checkpoint instrumentation",
                "state": "prospective_only_candidate",
                "scope": (
                    "would need exact account bytes and state after each ordered instruction/CPI, "
                    "transaction/fork identity, runtime/program/feature state, and durable receipt "
                    "times; not implemented, tested, or authorized here"
                ),
            },
            {
                "source": "historical validator replay",
                "state": "requires_unavailable_input",
                "scope": (
                    "requires a specific pre-slot account snapshot, complete ordered ledger/fork, "
                    "era-correct runtime/feature state, invoked program versions, and "
                    "independently "
                    "checked replay boundaries; docs alone do not establish c4 replay feasibility"
                ),
            },
        ],
        "synthetic_boundary_alias": _synthetic_boundary_alias(7, 20),
        "interpretation_limits": [
            (
                "failed_attempt_exact is a transaction disposition, while candidate position "
                "state is not applicable to the failed attempt; it is not an economic-zero label"
            ),
            (
                "successful structural mismatches remain unknown; for successful structural "
                "passes, "
                "the captured qualified sources do not provide candidate-position state; a future "
                "custom source or instrumentation would require separate qualification"
            ),
            (
                "Token-2022 fee/hook state, WSOL/rent/close effects, and sibling instruction state "
                "are not inferred from wire order or transaction endpoints"
            ),
            (
                "custom prospective instrumentation is a future candidate, not a qualified "
                "collector or source guarantee"
            ),
            (
                "this receipt authorizes no decoder, economic predicate, admission, audit, "
                "completeness, or training change"
            ),
            (
                "the pass read only the accepted row ledger and documentation snapshots; it opened "
                "no c4 body, made no RPC/backfill, and did not open tail2"
            ),
        ],
        "row_ledger": {
            "path": DEFAULT_ROWS_OUTPUT.relative_to(ROOT).as_posix(),
            "row_count": len(output_rows),
            "contains_raw_capture_payloads": False,
        },
    }
    raw = json.dumps(output_rows, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    receipt["row_ledger"].update({"uncompressed_sha256": _sha(raw), "uncompressed_bytes": len(raw)})
    return receipt, raw


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--rows", type=Path, default=DEFAULT_ROWS_OUTPUT)
    args = parser.parse_args()
    receipt, rows_raw = build_receipt()
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.rows.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["zstd", "-q", "-19", "-f", "-o", str(args.rows)], input=rows_raw, check=True
    )
    receipt["row_ledger"].update(
        {
            "path": args.rows.relative_to(ROOT).as_posix(),
            "compressed_sha256": _file_sha(args.rows),
            "compressed_bytes": args.rows.stat().st_size,
        }
    )
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "receipt": str(args.receipt),
                "row_ledger": str(args.rows),
                "row_count": receipt["row_ledger"]["row_count"],
                "completed": completed.returncode == 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
