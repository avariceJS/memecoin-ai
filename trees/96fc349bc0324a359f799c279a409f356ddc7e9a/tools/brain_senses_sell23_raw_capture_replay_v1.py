#!/usr/bin/env python3
"""Independent offline replay of the frozen c4 sell23 14:00–17:00Z capture set.

This bounded verifier is pinned to one immutable c4 root. It opens only the four
preselected frame payloads, validates every capture against its receipt, derives
candidate identities from full block transactions, and reports source-account
flow diagnostics. It makes no provider requests and makes no settlement,
wallet-net,
economic, or admission claim.
"""

from __future__ import annotations

import hashlib
import json
import random
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))

from brain_senses_sell23_c3_native_account_flow import classify_account  # noqa: E402
from brain_senses_sell23_c4_native_flow_audit_v2 import _sell23  # noqa: E402
from brain_senses_sell23_native_sufficiency_v1 import (  # noqa: E402
    INPUT_SCHEMA,
    canonical_json,
    compile_envelope,
)
from brain_senses_sell23_native_sufficiency_v1 import (  # noqa: E402
    SCHEMA_VERSION as ADAPTER_VERSION,
)

from memecoin_pumpswap.economic_v2 import diagnose_pumpswap_sell_v2  # noqa: E402

ROOT_REL = "work/brain-senses-live-helius-inventory-v3-20261010-4"
ROOT = REPO / ROOT_REL
FRAME_IDS = tuple(f"20261010T{hour:02d}0000Z" for hour in range(14, 18))
PREDICATE_SHA256 = "e7065d0f0a30a56650cb06e166dfa148d18a31aa20b4335e4f253dd5af673fc1"
INPUT_PINS = {
    "c4_preflight_receipt": "51a5375b499d6f03a8778f9332e217cf2fce6cf15ff105da13410946c10100fc",
    "c4_native_audit_receipt": "4a5e53cf96f880d809c8f8fa79ae71cfd0a74fd9d6cbfe54cfaa4537375b0e81",
    "schedule_file": "2253421b56b52b4636f6a15b682495cb289114887dbf40f517338d252ffc1474",
    "schedule_canonical": "f6e7090bed90b2492bdfa7f744afceb3ca24a5c6d4c87d2018a512732aaa7c30",
    "lease": "e6cf53b114d9aa306a74d79103c8e6d79df36a1742731ba6d713b2db578afcf2",
    "predecessor_lineage": "0a48d1210830a3e6dc78ae4cf4811e3cbacb521834060461041a08336cb86b79",
}
FRAME_PINS = {
    "20261010T140000Z": {
        "plan": "6f3c3e85e5589976641e21a06ac070bfa19d31922e61f48e9908c1075fefa783",
        "summary": "d308bec276c5591d490ee6b8204cf23dd6d3c1c9a0fd9103648e469ff1140187",
        "frame": "265c3f34156495002ba9ef70c3fb48181593801dde3cf80561819eda75264a4c",
        "stop": "94c94f75d5ce150d43e0e7bf4f93243190fe3770ed94176a39f98c5bb9af481e",
        "receipt_manifest": "8899590603aac53db972a76d415b65f798d0e166e123ecfb9a058ffbb60edfe0",
    },
    "20261010T150000Z": {
        "plan": "57f21dc0d7f5547e844d35c062bc5b51b24001660adec4783dd0d54edac2d5ed",
        "summary": "70c3068493f4a1a9eb4ebedb9bc0de73fe62b00a275f7c4485351581ba0923d1",
        "frame": "14380ec7ea1e2d7a958dbf5edd4fc545af7929b0e8550b2b6e8dbb60e105bd3a",
        "stop": "230a798b9594b32f5c04661bc72cd7b424519013c30ca5c6874f38ba24f9a70f",
        "receipt_manifest": "788bc6173a8a9c7fadefce2b7671172fdd82e5130a2e1eb5cad19f6cc6d3c440",
    },
    "20261010T160000Z": {
        "plan": "eb582d0e3f63d29ce13c5e3f0b48b88a5c21a19a54c0b9b604ddda2a37f0ddf6",
        "summary": "5f5f459c6e294f4305a1318d0d8fc91b44903a26bfe6cd7b3e59699b37c3acb9",
        "frame": "f238513facc0c7b4b24a80e2de25fc945a01bebc8e914e95ee30877862f25861",
        "stop": "eff75456c410f820b8c5596e7cccaadd4130697122f477a0deaed847fd7c1cd8",
        "receipt_manifest": "fa8e15f25393b992e91973f3bfda11730f8319cb0997473ac10bf82e1a549f63",
    },
    "20261010T170000Z": {
        "plan": "e8e56172a21d7adf5eab3343459284907d55f7533553366be655164f7ba63ff3",
        "summary": "abd18093f2863e13a685871f9db49b06f758fcbd653f2554a556b446f7d9dc95",
        "frame": "b66aa29dd56ca49e5bae63e241d638dac6a6fd4cfe5602441afb4a3481079406",
        "stop": "ffa257cfcc5ae65223af7eeccf1b5f07a722194be4b4d8a3ebb4cff80218dc10",
        "receipt_manifest": "c1068f85702639aa4fa28f4f78caf7188560982860526a5d7aa7c8a9114dd17a",
    },
}
SOURCE_PINS = {
    "tools/brain_senses_sell23_native_sufficiency_v1.py": (
        "006dbd922b073121e74b2de6dbfeaa42aaa2abdf8cc061269dfb60030231dfdd"
    ),
    "tools/brain_senses_sell23_c3_native_account_flow.py": (
        "29834060533973689ccac1c76d5f7a60f2b56b79e72f855a8aea1001dcf3583a"
    ),
    "tools/brain_senses_sell23_c4_native_flow_audit_v2.py": (
        "cfe4ef6b9b416dda7b3a1225c8bf6b7908b0750c045a0b1ec09881aae962ef7f"
    ),
    "tools/brain_senses_block_epoch_v2.py": (
        "c3d1d0e4d9824ebd6c67c5e292471b5378249e96c26f16c70cd6f4f26ed33555"
    ),
    "tools/brain_senses_census_pumpswap_sell_ledger.py": (
        "6f48d1534f21809681b94500bfd0c8647186316b8641b7cf215ce1a866ed7eb2"
    ),
    "tools/brain_senses_select_pumpswap_sell_sample.py": (
        "638752ab19e1b9c8e80e92b6f8d534b3072f0872af695153bbc3dd344a78a312"
    ),
    "src/memecoin_pumpswap/normalize.py": (
        "7253588560626c6f45ed65ebfd802200f20e79e2daec88fadb93d34731e84643"
    ),
    "src/memecoin_pumpswap/economic_v2.py": (
        "47e20f6bc4c480feda9eb774694f58e5a4681e9bc2b317a3d1b6e62bba52694e"
    ),
    "src/memecoin_pumpswap/decoder.py": (
        "21610a62f1e8bb6c54cbe831134e6f170938668d85492434704aa66472471a8b"
    ),
    "src/memecoin_pumpswap/schema.py": (
        "dafd0708aad54224565863c3b23deee8440ef3fbcd2c1e7b5ce7629e2a6d5ad1"
    ),
}
WSOL = "So11111111111111111111111111111111111111112"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


class ReplayError(ValueError):
    """Frozen raw evidence failed a structural or provenance check."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    return _sha(path.read_bytes())


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ReplayError("metadata_json_invalid") from exc


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _assert_hash(path: Path, expected: str) -> None:
    if not path.is_file() or _file_sha(path) != expected:
        raise ReplayError("frozen_input_hash_mismatch")


def _verify_c4_identity_chain(
    root: Path,
    schedule: dict[str, Any],
    lease: dict[str, Any],
    lineage: dict[str, Any],
    bootstrap: dict[str, Any],
    bootstrap_reservation: dict[str, Any],
) -> None:
    """Cross-check hash-pinned metadata that binds the internal c4 root and lineage."""
    campaign = "helius-inventory-v3-20261010-4"
    predecessor_campaign = "helius-inventory-v3-20261010-3"
    predecessor_stop = "e8666a111d5e9aadd9c12044490e2d0ad4d42b0a204d57fbae0295ba036a9f00"
    # The path is the root binding recorded by the immutable lease. The retained c4
    # receipts do not carry a volume UUID/root_id, so only the actual path/device/owner
    # are asserted here; the report must not claim a receipt-pinned UUID.
    if (
        schedule.get("schedule_id") != campaign
        or schedule.get("provider") != "helius-free"
        or lease.get("campaign_id") != campaign
        or lease.get("provider") != "helius-free"
        or lease.get("lease_id") != "helius-c3-abnormal-stop-recovery-v1"
        or lease.get("root") != str(root)
    ):
        raise ReplayError("c4_lease_root_binding_mismatch")
    pred = lease.get("predecessor")
    if (
        type(pred) is not dict
        or pred.get("campaign_id") != predecessor_campaign
        or pred.get("status") != "permanently_terminal_abnormal_anchor_failure"
        or pred.get("stop_sha256") != predecessor_stop
        or pred.get("last_trusted_frontier") != 455216025
    ):
        raise ReplayError("c4_lease_predecessor_binding_mismatch")
    if (
        lineage.get("campaign_id") != campaign
        or lineage.get("provider") != "helius-free"
        or lineage.get("predecessor_campaign_id") != predecessor_campaign
        or lineage.get("predecessor_stop_sha256") != predecessor_stop
        or lineage.get("bootstrap_receipt_sha256")
        != lease.get("bootstrap", {}).get("receipt_sha256")
        or lineage.get("predecessor_status")
        != "c3_permanently_terminal; c4 bootstrap is new observation"
    ):
        raise ReplayError("c4_predecessor_lineage_binding_mismatch")
    bootstrap_identity = bootstrap.get("request_identity")
    if (
        bootstrap.get("campaign_id") != campaign
        or bootstrap.get("lease_id") != lease.get("lease_id")
        or bootstrap.get("bucket") != "20261010T120000Z"
        or bootstrap.get("method") != "getSlot(finalized)"
        or bootstrap.get("predecessor_stop_sha256") != predecessor_stop
        or type(bootstrap_identity) is not dict
        or bootstrap_identity.get("campaign_id") != campaign
        or bootstrap_identity.get("phase") != "bootstrap_anchor"
        or bootstrap_identity.get("sequence") != 1
        or bootstrap_identity.get("utc_bucket") != bootstrap.get("bucket")
        or type(bootstrap.get("observed_anchor_slot")) is not int
        or bootstrap.get("observed_anchor_slot") < pred["last_trusted_frontier"]
    ):
        raise ReplayError("c4_bootstrap_identity_binding_mismatch")
    gap = bootstrap.get("uncovered_gap")
    if (
        type(gap) is not dict
        or gap.get("reason") != "uncovered_since_c3_trusted_frontier_no_backfill"
        or gap.get("bootstrap_bucket") != bootstrap.get("bucket")
        or gap.get("start") != pred["last_trusted_frontier"]
        or gap.get("end_exclusive") != bootstrap["observed_anchor_slot"] + 1
        or gap.get("from_c3_stop_sha256") != predecessor_stop
    ):
        raise ReplayError("c4_bootstrap_gap_binding_mismatch")
    if (
        lineage.get("last_attempt_bucket") != bootstrap.get("bucket")
        or lineage.get("last_observed_anchor_slot") != bootstrap.get("observed_anchor_slot")
        or lineage.get("last_observed_anchor_receipt_ns") != bootstrap.get("availability_ns")
    ):
        raise ReplayError("c4_lineage_bootstrap_observation_mismatch")
    if (
        bootstrap_reservation.get("campaign_id") != campaign
        or bootstrap_reservation.get("lease_id") != lease.get("lease_id")
        or bootstrap_reservation.get("bucket") != bootstrap.get("bucket")
        or bootstrap_reservation.get("phase") != "bootstrap_anchor"
        or bootstrap_reservation.get("sequence") != 1
        or bootstrap_reservation.get("status") != "reserved_before_io"
        or bootstrap_reservation.get("request_limit") != 1
    ):
        raise ReplayError("c4_bootstrap_reservation_binding_mismatch")


def _validate_pins(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    if root.resolve() != ROOT.resolve():
        raise ReplayError("c4_root_not_exact")
    schedule_path = root / "schedule-v3.json"
    lease_path = root / "lease-v1.json"
    lineage_path = root / "predecessor-lineage.json"
    for path, key in (
        (
            REPO / "docs/evidence/pumpswap-sell23-c4-audit-v2-metadata-preflight-2026-10-10.json",
            "c4_preflight_receipt",
        ),
        (
            REPO / "docs/evidence/pumpswap-sell23-c4-native-flow-audit-v2-2026-10-10.json",
            "c4_native_audit_receipt",
        ),
        (schedule_path, "schedule_file"),
        (lease_path, "lease"),
        (lineage_path, "predecessor_lineage"),
    ):
        _assert_hash(path, INPUT_PINS[key])
    preflight = _json(
        REPO / "docs/evidence/pumpswap-sell23-c4-audit-v2-metadata-preflight-2026-10-10.json"
    )
    audit = _json(REPO / "docs/evidence/pumpswap-sell23-c4-native-flow-audit-v2-2026-10-10.json")
    if (
        preflight.get("status") != "metadata_preflight_passed_no_transaction_cases_opened"
        or preflight.get("selected_frame_ids") != list(FRAME_IDS)
        or preflight.get("transaction_cases_opened") is not False
        or preflight.get("provider_requests_made") != 0
    ):
        raise ReplayError("preflight_scope_mismatch")
    schedule = _json(schedule_path)
    lease = _json(lease_path)
    lineage = _json(lineage_path)
    bootstrap_receipt_path = root / "bootstrap-receipt.json"
    bootstrap = _json(bootstrap_receipt_path)
    bootstrap_reservation = _json(root / "bootstrap-reservation.json")
    if _sha(_canonical(schedule)) != INPUT_PINS["schedule_canonical"]:
        raise ReplayError("canonical_schedule_pin_mismatch")
    if (
        schedule.get("provider") != "helius-free"
        or schedule.get("schedule_id") != "helius-inventory-v3-20261010-4"
    ):
        raise ReplayError("schedule_identity_mismatch")
    if lease.get("root") != str(root) or lease.get("provider") != "helius-free":
        raise ReplayError("lease_root_or_provider_mismatch")
    if root.stat().st_dev != 16777234 or root.stat().st_uid != __import__("os").getuid():
        raise ReplayError("c4_root_device_or_owner_mismatch")
    _verify_c4_identity_chain(root, schedule, lease, lineage, bootstrap, bootstrap_reservation)
    preflight_rows = {
        row.get("frame_id"): row for row in preflight.get("selected_frame_metadata", [])
    }
    audit_rows = {row.get("bucket"): row for row in audit.get("frame_summaries", [])}
    if set(preflight_rows) != set(FRAME_IDS) or set(audit_rows) != set(FRAME_IDS):
        raise ReplayError("c4_receipt_frame_scope_mismatch")
    for frame_id in FRAME_IDS:
        expected = FRAME_PINS[frame_id]
        if (
            preflight_rows[frame_id].get("epoch_plan_sha256") != expected["plan"]
            or preflight_rows[frame_id].get("epoch_summary_sha256") != expected["summary"]
            or preflight_rows[frame_id].get("stop_exists") is not True
            or audit_rows[frame_id].get("epoch_summary_sha256") != expected["summary"]
            or audit_rows[frame_id].get("frame_sha256") != expected["frame"]
        ):
            raise ReplayError("c4_receipt_frame_pin_disagreement")
    code_map = schedule.get("code_sha256")
    if type(code_map) is not dict or not code_map:
        raise ReplayError("schedule_source_map_missing")
    lease_code_map = lease.get("code_sha256")
    if type(lease_code_map) is not dict or not lease_code_map:
        raise ReplayError("lease_source_map_missing")
    for rel, expected in {**code_map, **lease_code_map}.items():
        path = REPO / rel
        if not path.is_file() or _file_sha(path) != expected:
            raise ReplayError("schedule_source_pin_mismatch")
    if _file_sha(bootstrap_receipt_path) != lease["bootstrap"]["receipt_sha256"]:
        raise ReplayError("bootstrap_receipt_pin_mismatch")
    for rel, expected in SOURCE_PINS.items():
        path = REPO / rel
        if not path.is_file() or _file_sha(path) != expected:
            raise ReplayError("replay_source_pin_mismatch")
    return schedule, lease, dict(SOURCE_PINS)


def _stored_capture(receipt_path: Path, receipt: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    storage = receipt.get("storage")
    if type(storage) is not dict or storage.get("codec") != "zstd":
        raise ReplayError("capture_storage_schema_invalid")
    rel = storage.get("path")
    if type(rel) is not str or rel.startswith("/") or ".." in Path(rel).parts:
        raise ReplayError("capture_path_invalid")
    payload = receipt_path.parent / rel
    if payload.resolve().parent != receipt_path.parent.resolve():
        raise ReplayError("capture_path_escapes")
    encoded = payload.read_bytes()
    if len(encoded) != storage.get("bytes") or _sha(encoded) != storage.get("sha256"):
        raise ReplayError("stored_capture_digest_mismatch")
    if type(receipt.get("response_bytes")) is not int or receipt["response_bytes"] > 32_000_000:
        raise ReplayError("raw_capture_size_out_of_bounds")
    try:
        raw = subprocess.run(
            ["zstd", "-q", "-d", "-c", str(payload)], check=True, capture_output=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReplayError("capture_decompression_failed") from exc
    if len(raw) != receipt.get("response_bytes") or _sha(raw) != receipt.get("response_sha256"):
        raise ReplayError("raw_capture_digest_mismatch")
    try:
        response = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReplayError("raw_capture_json_invalid") from exc
    if type(response) is not dict or response.get("jsonrpc") != "2.0" or response.get("id") != 1:
        raise ReplayError("jsonrpc_response_identity_invalid")
    return raw, response


def _path_key(path: list[int]) -> tuple[int, int]:
    return (path[0], path[1] if len(path) > 1 else -1)


def _map_effects(flow: dict[str, Any], target_amount: int | None) -> list[dict[str, Any]]:
    effects: list[dict[str, Any]] = []
    for trace in flow.get("account_touching_instruction_trace", []):
        original = trace.get("effect")
        if type(original) is not dict:
            continue
        kind = original.get("kind")
        path = original.get("path")
        order = path[0] * 1_000_000 + (path[1] + 1 if len(path) > 1 else 0)
        common = {
            "order": order,
            "path": {"outer_index": path[0], "inner_index": path[1] if len(path) > 1 else None},
        }
        if kind == "create_account":
            effects.append(
                {
                    "kind": "system_create_account",
                    **common,
                    "payer": original["payer"],
                    "account": original["account"],
                    "lamports": original["lamports"],
                    "space_bytes": original["space_bytes"],
                    "owner_program": original["owner_program"],
                }
            )
        elif kind == "system_transfer":
            effects.append(
                {
                    "kind": "system_transfer",
                    **common,
                    "source": original["source"],
                    "destination": original["destination"],
                    "lamports": original["lamports"],
                }
            )
        elif kind == "initialize_account":
            effects.append(
                {
                    "kind": "token_initialize_account",
                    **common,
                    "account": original["account"],
                    "token_program": TOKEN_PROGRAM,
                    "mint": original["mint"],
                    "decimals": 9,
                }
            )
        elif kind == "sync_native":
            effects.append(
                {
                    "kind": "sync_native",
                    **common,
                    "account": original["account"],
                    "token_program": TOKEN_PROGRAM,
                }
            )
        elif kind == "transfer_checked":
            effects.append(
                {
                    "kind": "transfer_checked",
                    **common,
                    "source": original["source"],
                    "destination": original["destination"],
                    "token_program": TOKEN_PROGRAM,
                    "mint": original["mint"],
                    "decimals": original["decimals"],
                    "amount_atomic": original["amount_atomic"],
                }
            )
        elif kind == "close_account":
            effects.append(
                {
                    "kind": "close_account",
                    **common,
                    "account": original["account"],
                    "destination": original["destination"],
                    "token_program": TOKEN_PROGRAM,
                }
            )
        elif kind == "unsupported_touch":
            effects.append(
                {
                    "kind": "unsupported_instruction",
                    **common,
                    "accounts": original.get("accounts", []),
                    "program_id": trace.get("program_id", "unknown"),
                    "instruction_name": original.get("reason", "unsupported"),
                }
            )
    close_path = next((e["path"] for e in effects if e["kind"] == "close_account"), None)
    represented_paths = {(e["path"]["outer_index"], e["path"]["inner_index"]) for e in effects}
    destination = flow.get("close_destination")
    for touch in flow.get("close_destination_touching_instruction_trace", []):
        path = touch.get("path")
        if not isinstance(path, list) or close_path is None:
            continue
        close_path_list = [close_path["outer_index"]]
        if close_path["inner_index"] is not None:
            close_path_list.append(close_path["inner_index"])
        if path == close_path_list:
            continue
        if destination not in touch.get("accounts", []):
            continue
        path_key = (path[0], path[1] if len(path) > 1 else None)
        # A recognized effect already names the instruction at this path. The
        # adapter schema permits one effect per path, so do not duplicate that
        # fact as an opaque touch; other sibling/parent paths remain explicit.
        if path_key in represented_paths:
            continue
        effects.append(
            {
                "kind": "opaque_touch",
                "order": path[0] * 1_000_000 + (path[1] + 1 if len(path) > 1 else 0),
                "path": {"outer_index": path[0], "inner_index": path[1] if len(path) > 1 else None},
                "accounts": list(touch["accounts"]),
                "scope": "unknown",
            }
        )
    effects.sort(
        key=lambda item: (
            item["order"],
            item["path"]["inner_index"] if item["path"]["inner_index"] is not None else -1,
        )
    )
    return effects


def _native_row(
    frame_id: str,
    raw_row: dict[str, Any],
    result: dict[str, Any],
    request: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    flow = raw_row["flow"]
    transfer = next(
        (
            e
            for e in flow["account_touching_instruction_trace"]
            if e.get("effect", {}).get("kind") == "transfer_checked"
        ),
        None,
    )
    if transfer is None:
        raise ReplayError("native_transfer_fact_missing")
    transfer_effect = transfer["effect"]
    event_seconds = raw_row.get("block_time_unix_seconds")
    if type(event_seconds) is not int or event_seconds <= 0:
        raise ReplayError("block_event_time_missing")
    receipt_ns = raw_row.get("capture_receipt_ns")
    availability_ns = result.get("availability_ns")
    started_ns = result.get("request_started_ns")
    if (
        not all(type(x) is int and x > 0 for x in (receipt_ns, availability_ns, started_ns))
        or not started_ns <= receipt_ns <= availability_ns
    ):
        raise ReplayError("capture_causal_times_invalid")
    capture_id = f"{frame_id}:{raw_row['signature_sha256']}"
    effects = _map_effects(flow, transfer_effect.get("amount_atomic"))
    endpoints = {
        "source_owner_program": None,
        "source_token_mint": None,
        "source_token_pre_atomic": None,
        "source_token_post_atomic": None,
        "source_lamports_pre": flow.get("pre_lamports"),
        "source_lamports_post": flow.get("post_lamports"),
        "rent_sysvar_lamports": None,
        "destination_owner_program": None,
        "destination_lamports_pre": None,
        "destination_lamports_post": None,
        "closed_token_pre_atomic": None,
        "closed_token_post_atomic": None,
    }
    candidate = {
        "candidate_id": raw_row["signature_sha256"],
        "input_status": "successful",
        "signature_sha256": raw_row["signature_sha256"],
        "slot": raw_row["slot"],
        "blockhash": raw_row.get("blockhash", "unknown"),
        "capture_id": capture_id,
        "layout_status": "supported_sell23",
        "source_account": flow["account"],
        "target_token_account": transfer_effect["destination"],
        "destination_account": flow["close_destination"],
        "target_transfer_amount_atomic": transfer_effect.get("amount_atomic"),
        "ordered_effects_complete": "inner_instruction_trace_missing"
        not in flow.get("reasons", []),
        "effects": effects,
        "endpoints": endpoints,
    }
    capture = {
        "capture_id": capture_id,
        "capture_sha256": raw_row["raw_response_sha256"],
        "request_sha256": request["request_sha256"],
        "slot": raw_row["slot"],
        "blockhash": raw_row.get("blockhash", "unknown"),
        "event_time_ns": event_seconds * 1_000_000_000,
        "receipt_ns": receipt_ns,
        "availability_ns": availability_ns,
    }
    return candidate, capture


def _frame(
    root: Path, frame_id: str, schedule: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], Counter[str]]:
    epoch = root / "epochs" / frame_id
    pin = FRAME_PINS[frame_id]
    for rel, key in (
        ("epoch-plan.json", "plan"),
        ("epoch-summary.json", "summary"),
        ("frame.json", "frame"),
        ("STOP", "stop"),
    ):
        _assert_hash(epoch / rel, pin[key])
    plan = _json(epoch / "epoch-plan.json")
    summary = _json(epoch / "epoch-summary.json")
    if (
        plan.get("epoch_id") != frame_id
        or plan.get("schedule_sha256") != _sha(_canonical(schedule))
        or summary.get("status") != "frame_complete"
    ):
        raise ReplayError("frame_plan_or_terminal_mismatch")
    receipts = sorted((epoch / "captures").glob("*/receipt.json"))
    receipt_manifest = _sha(
        "\n".join(f"{p.relative_to(REPO)} {_file_sha(p)}" for p in receipts).encode()
    )
    if receipt_manifest != pin["receipt_manifest"]:
        raise ReplayError("frame_receipt_manifest_mismatch")
    requests: dict[int, dict[str, Any]] = {}
    results: dict[int, dict[str, Any]] = {}
    for path in sorted((epoch / "requests").glob("*.json")):
        row = _json(path)
        requests[row["sequence"]] = row
    for path in sorted((epoch / "request-results").glob("*.json")):
        row = _json(path)
        results[row["sequence"]] = row
    if (
        set(requests) != set(range(1, 35))
        or set(results) != set(range(1, 35))
        or len(receipts) != 34
    ):
        raise ReplayError("frame_request_denominator_invalid")
    by_sequence: dict[int, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
    for receipt_path in receipts:
        receipt = _json(receipt_path)
        seq = int(receipt_path.parent.name)
        request = requests[seq]
        result = results[seq]
        if receipt.get("source_alias") != "helius-free" or receipt.get("method") != request.get(
            "method"
        ):
            raise ReplayError("capture_request_identity_mismatch")
        if receipt.get("request_started_ns") != result.get("request_started_ns") or receipt.get(
            "received_ns"
        ) != result.get("receipt_ns"):
            raise ReplayError("capture_time_metadata_mismatch")
        raw, response = _stored_capture(receipt_path, receipt)
        if result.get("response_sha256") != _sha(raw) or result.get("response_bytes") != len(raw):
            raise ReplayError("request_result_capture_mismatch")
        req_json = json.loads(receipt["request_json"])
        if (
            req_json.get("method") != request.get("method")
            or req_json.get("params") != request.get("params")
            or req_json.get("id") != 1
        ):
            raise ReplayError("capture_wire_request_mismatch")
        expected_wire = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": request["method"],
                "params": request["params"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if request.get("request_sha256") != _sha(expected_wire):
            raise ReplayError("request_semantic_hash_mismatch")
        result_body = response.get("result")
        if (
            response.get("error") is not None
            or result.get("status") != "captured"
            or result.get("result_status") != "parsed"
        ):
            raise ReplayError("captured_response_not_parsed")
        by_sequence[seq] = (receipt, request, result)
        if request["method"] == "getBlocks":
            if (
                seq != 2
                or type(result_body) is not list
                or result_body != result.get("inventory_slots")
            ):
                raise ReplayError("inventory_response_result_mismatch")
            start, end = request["params"][:2]
            if result_body != sorted(set(result_body)) or any(
                type(slot) is not int or not start <= slot <= end for slot in result_body
            ):
                raise ReplayError("inventory_slots_invalid")
    if requests[1].get("method") != "getSlot" or requests[2].get("method") != "getBlocks":
        raise ReplayError("frame_operation_prefix_invalid")
    frame_metadata = _json(epoch / "frame.json")
    if (
        frame_metadata.get("start") != plan.get("start")
        or type(frame_metadata.get("end_exclusive")) is not int
        or requests[1].get("params") != [{"commitment": "finalized"}]
        or requests[2].get("params")
        != [plan["start"], frame_metadata["end_exclusive"] - 1, {"commitment": "finalized"}]
        or results[1].get("slot_value") != frame_metadata.get("anchor_slot")
    ):
        raise ReplayError("anchor_inventory_request_shape_invalid")
    inventory_receipt, inventory_request, inventory_result = by_sequence[2]
    inventory_sha = inventory_result["response_sha256"]
    inventory_slots = inventory_result["inventory_slots"]
    selected_count = min(plan["selection_count"], len(inventory_slots))
    seed = f"{plan['seed']}:{frame_id}:{inventory_sha}"
    selected = (
        sorted(random.Random(seed).sample(inventory_slots, selected_count))
        if selected_count
        else []
    )
    if selected != summary.get("selected_slots"):
        raise ReplayError("summary_selection_mismatch")
    # Request sequence is ordered as signatures/full pairs for each selected slot.
    expected: list[tuple[int, int, str]] = []
    for index, slot in enumerate(selected):
        expected.extend(((3 + 2 * index, slot, "full"), (4 + 2 * index, slot, "signatures")))
    candidate_rows: list[dict[str, Any]] = []
    captures_for_adapter: list[dict[str, Any]] = []
    structural = Counter()
    full_blocks: list[tuple[int, dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for seq, slot, detail in expected:
        receipt, request, result = by_sequence[seq]
        expected_options = (
            {
                "commitment": "finalized",
                "encoding": "json",
                "transactionDetails": "full",
                "rewards": False,
                "maxSupportedTransactionVersion": 1,
            }
            if detail == "full"
            else {
                "commitment": "finalized",
                "encoding": "json",
                "transactionDetails": "signatures",
                "rewards": False,
            }
        )
        if (
            request.get("method") != "getBlock"
            or request.get("params", [None, {}])[0] != slot
            or request.get("params", [None, {}])[1] != expected_options
            or request.get("phase") != ("full_block" if detail == "full" else "signature_check")
        ):
            raise ReplayError("selected_slot_operation_mismatch")
        raw, response = _stored_capture(
            epoch / "captures" / f"{seq:012d}" / "receipt.json", receipt
        )
        if detail == "full":
            block = response.get("result")
            if (
                type(block) is not dict
                or type(block.get("transactions")) is not list
                or block.get("blockhash") is None
            ):
                raise ReplayError("full_block_response_invalid")
            if block.get("parentSlot") is None or block.get("previousBlockhash") is None:
                raise ReplayError("full_block_identity_incomplete")
            sig_seq = seq + 1
            sig_receipt = by_sequence[sig_seq][0]
            _, sig_response = _stored_capture(
                epoch / "captures" / f"{sig_seq:012d}" / "receipt.json", sig_receipt
            )
            signature_block = sig_response.get("result")
            if (
                type(signature_block) is not dict
                or type(signature_block.get("signatures")) is not list
                or any(
                    signature_block.get(key) != block.get(key)
                    for key in (
                        "blockhash",
                        "previousBlockhash",
                        "parentSlot",
                        "blockTime",
                        "blockHeight",
                    )
                )
            ):
                raise ReplayError("signature_response_invalid")
            signatures = signature_block["signatures"]
            if any(type(signature) is not str for signature in signatures):
                raise ReplayError("signature_response_invalid")
            result_sigs = signatures
            tx_sigs = [
                ((tx.get("transaction") or {}).get("signatures") or [None])[0]
                for tx in block["transactions"]
            ]
            if result_sigs != tx_sigs:
                raise ReplayError("signature_full_block_mismatch")
            full_blocks.append((slot, block, receipt, result))
    if len(selected) != 16 or len(full_blocks) != 16:
        raise ReplayError("selected_full_block_count_invalid")
    for slot, block, receipt, result in full_blocks:
        structural["scanned_transactions"] += len(block["transactions"])
        for tx_index, tx in enumerate(block["transactions"]):
            candidate = _sell23(tx)
            if candidate is None:
                continue
            normalized, target = candidate
            signatures = normalized.get("transaction_signatures")
            if type(signatures) is not list or not signatures or type(signatures[0]) is not str:
                raise ReplayError("sell23_signature_invalid")
            signature = signatures[0]
            sig_hash = _sha(signature.encode("ascii"))
            structural["structural"] += 1
            common = {
                "bucket": frame_id,
                "slot": slot,
                "blockhash": block["blockhash"],
                "block_time_unix_seconds": block.get("blockTime"),
                "transaction_index": tx_index,
                "signature": signature,
                "signature_sha256": sig_hash,
                "target_instruction_path": target["path"],
                "raw_response_sha256": receipt["response_sha256"],
                "capture_receipt_ns": receipt["received_ns"],
                "availability_ns": result["availability_ns"],
                "request_started_ns": result["request_started_ns"],
                "receipt_sequence": result["sequence"],
            }
            if (tx.get("meta") or {}).get("err") is not None:
                structural["failed"] += 1
                continue
            structural["successful"] += 1
            diagnosis = diagnose_pumpswap_sell_v2(tx)
            if diagnosis.get("account_settlement_state") != "censored_partial":
                structural["outside_native_close"] += 1
                continue
            native = [
                account
                for account, fact in diagnosis.get("account_ledger", {})
                .get("account_facts", {})
                .items()
                if fact.get("mint") == WSOL and fact.get("status") == "censored"
            ]
            if len(native) != 1:
                raise ReplayError("native_account_identity_not_unique")
            flow = classify_account(tx, native[0], target["path"])
            row = {**common, "flow": flow}
            request = requests[result["sequence"]]
            adapter_row, adapter_capture = _native_row(frame_id, row, result, request)
            # Bind the actual full block identity that adapter validation consumes.
            adapter_row["blockhash"] = block["blockhash"]
            adapter_capture["blockhash"] = block["blockhash"]
            candidate_rows.append(adapter_row)
            captures_for_adapter.append(adapter_capture)
            structural["native_close"] += 1
            structural[f"source_flow:{flow['status']}"] += 1
            structural[f"destination:{flow['close_destination_state']}"] += 1
    block_start = frame_metadata["start"]
    block_end = frame_metadata["end_exclusive"]
    summary_row = {
        "bucket": frame_id,
        "plan_sha256": _file_sha(epoch / "epoch-plan.json"),
        "summary_sha256": _file_sha(epoch / "epoch-summary.json"),
        "frame_sha256": _file_sha(epoch / "frame.json"),
        "stop_sha256": _file_sha(epoch / "STOP"),
        "inventory_response_sha256": inventory_sha,
        "inventory_denominator": len(inventory_slots),
        "selected_slots": selected,
        "produced_blocks": len(inventory_slots),
        "selected_blocks": len(selected),
        "scheduled_frame_count": 1,
        "sealed_frame_count": 1,
        "missed_frame_count": 0,
        "start_slot": block_start,
        "end_exclusive": block_end,
        "request_count": 34,
        "raw_bytes": summary["captured_response_bytes"],
        "stored_bytes": summary["stored_response_bytes"],
        "structural_counts": dict(sorted(structural.items())),
        "selection_probability": f"{len(selected)}/{len(inventory_slots)}"
        if inventory_slots
        else None,
    }
    return summary_row, candidate_rows, captures_for_adapter, structural


def _adapter_envelope(
    frame_summary: dict[str, Any],
    rows: list[dict[str, Any]],
    captures: list[dict[str, Any]],
    source_hashes: dict[str, str],
    frame_id: str,
) -> dict[str, Any]:
    statuses = Counter(row["input_status"] for row in rows)
    epoch = ROOT / "epochs" / frame_id
    request_hash = _sha(
        "\n".join(
            f"{path.name} {_file_sha(path)}" for path in sorted((epoch / "requests").glob("*.json"))
        ).encode()
    )
    raw = {
        "schema": INPUT_SCHEMA,
        "schema_version": ADAPTER_VERSION,
        "lineage": {
            "frame_id": frame_id,
            "frame_sha256": frame_summary["frame_sha256"],
            "plan_sha256": frame_summary["plan_sha256"],
            "request_sha256": request_hash,
            "source_sha256": {
                f"source_{index}": digest for index, digest in enumerate(source_hashes.values())
            },
        },
        "frame": {
            "status": "sealed",
            "scheduled_frames": 1,
            "sealed_frames": 1,
            "missed_frames": 0,
            "selected_blocks": frame_summary["selected_blocks"],
            "produced_blocks": frame_summary["produced_blocks"],
        },
        "denominators": {
            "selected_candidates": len(rows),
            "status_counts": {
                status: statuses.get(status, 0)
                for status in ("successful", "failed", "unsupported", "unknown")
            },
        },
        "captures": captures,
        "selection_rows": rows,
    }
    envelope_digest = _sha(canonical_json(raw))
    return {
        **raw,
        "verification": {
            "status": "independently_verified",
            "verifier_sha256": PREDICATE_SHA256,
            "envelope_sha256": envelope_digest,
        },
    }


def independently_check_adapter(output: dict[str, Any]) -> dict[str, Any]:
    """Recheck source-flow equations, ordering, and destination ambiguity from adapter rows."""
    dispositions = output.get("candidates")
    if type(dispositions) is not list:
        raise ReplayError("adapter_disposition_rows_missing")
    counts: Counter[str] = Counter()
    for row in dispositions:
        status = row.get("source_account_flow")
        if row.get("input_status") == "failed":
            if status != "failed_attempt":
                raise ReplayError("failed_attempt_disposition_mismatch")
            counts["failed_attempt"] += 1
            continue
        effects = row.get("effects", [])
        source = row.get("source_account")
        creates = [
            e
            for e in effects
            if e.get("kind") == "system_create_account" and e.get("account") == source
        ]
        credits = [
            e["lamports"]
            for e in effects
            if e.get("kind") == "system_transfer" and e.get("destination") == source
        ]
        debits = [
            e["lamports"]
            for e in effects
            if e.get("kind") == "system_transfer" and e.get("source") == source
        ]
        pre = row.get("endpoints", {}).get("source_lamports_pre")
        post = row.get("endpoints", {}).get("source_lamports_post")
        inits = [
            e
            for e in effects
            if e.get("kind") == "token_initialize_account" and e.get("account") == source
        ]
        syncs = [
            e for e in effects if e.get("kind") == "sync_native" and e.get("account") == source
        ]
        transfers = [
            e for e in effects if e.get("kind") == "transfer_checked" and e.get("source") == source
        ]
        closes = [
            e for e in effects if e.get("kind") == "close_account" and e.get("account") == source
        ]
        lifecycle_shape = (len(creates), len(inits), len(syncs), len(transfers), len(closes)) == (
            1,
            1,
            1,
            1,
            1,
        )
        source_fact = False
        if lifecycle_shape and type(pre) is int and type(post) is int:
            create, init, sync, transfer, close = (
                creates[0],
                inits[0],
                syncs[0],
                transfers[0],
                closes[0],
            )
            source_fact = bool(
                create.get("owner_program") == TOKEN_PROGRAM
                and create.get("space_bytes") == 165
                and init.get("mint") == WSOL
                and init.get("token_program") == TOKEN_PROGRAM
                and init.get("decimals") == 9
                and sync.get("token_program") == TOKEN_PROGRAM
                and transfer.get("token_program") == TOKEN_PROGRAM
                and transfer.get("mint") == WSOL
                and transfer.get("decimals") == 9
                and transfer.get("destination") == row.get("target_token_account")
                and transfer.get("amount_atomic") == row.get("target_transfer_amount_atomic")
                and close.get("destination") == row.get("close_destination_account")
                and all(
                    e.get("order") < close.get("order")
                    for e in effects
                    if e.get("kind") == "system_transfer"
                    and source in (e.get("source"), e.get("destination"))
                )
                and [
                    create["order"],
                    init["order"],
                    sync["order"],
                    transfer["order"],
                    close["order"],
                ]
                == sorted(
                    [
                        create["order"],
                        init["order"],
                        sync["order"],
                        transfer["order"],
                        close["order"],
                    ]
                )
            )
            equation = pre + creates[0]["lamports"] + sum(credits) - sum(debits) - post
            if equation < 0:
                raise ReplayError("negative_source_conservation_candidate")
            if source_fact:
                if row.get("destination_status") not in {
                    "destination_ambiguous",
                    "unqualified",
                }:
                    raise ReplayError("destination_disposition_invalid")
                if row.get("destination_status") == "destination_ambiguous" and not any(
                    e.get("kind") == "opaque_touch"
                    and row.get("close_destination_account") in e.get("accounts", [])
                    for e in effects
                ):
                    raise ReplayError("destination_ambiguity_without_touch")
        if status == "source_account_flow_exact" and not source_fact:
            raise ReplayError("adapter_exact_source_fact_mismatch")
        if status == "unsupported_instruction":
            if not any(e.get("kind") == "unsupported_instruction" for e in effects):
                raise ReplayError("unsupported_disposition_without_effect")
            counts["unsupported_instruction"] += 1
            continue
        if source_fact:
            counts["source_flow_exact"] += 1
        else:
            counts["evidence_insufficient"] += 1
    return dict(sorted(counts.items()))


def replay(root: Path = ROOT) -> dict[str, Any]:
    schedule, lease, source_hashes = _validate_pins(root)
    summaries: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    all_captures: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()
    for frame_id in FRAME_IDS:
        summary, rows, captures, counts = _frame(root, frame_id, schedule)
        summaries.append(summary)
        all_rows.extend(rows)
        all_captures.extend(captures)
        totals.update(counts)
    if len({row["signature_sha256"] for row in all_rows}) != len(all_rows):
        raise ReplayError("duplicate_native_candidate_across_frames")
    frames = []
    for summary in summaries:
        frame_rows = [
            row for row in all_rows if row["capture_id"].startswith(summary["bucket"] + ":")
        ]
        frame_captures = [
            row for row in all_captures if row["capture_id"].startswith(summary["bucket"] + ":")
        ]
        envelope = _adapter_envelope(
            summary, frame_rows, frame_captures, source_hashes, summary["bucket"]
        )
        adapter_output = compile_envelope(envelope)
        verified_counts = independently_check_adapter(adapter_output)
        adapter_dispositions = Counter(
            candidate["source_account_flow"] for candidate in adapter_output["candidates"]
        )
        adapter_destinations = Counter(
            candidate["destination_status"] for candidate in adapter_output["candidates"]
        )
        adapter_blockers = Counter(
            blocker
            for candidate in adapter_output["candidates"]
            for blocker in candidate["blockers"]
        )
        frames.append(
            {
                "summary": summary,
                "adapter_input_sha256": _sha(canonical_json(envelope)),
                "adapter_output": adapter_output,
                "independent_disposition_counts": verified_counts,
                "adapter_disposition_counts": dict(sorted(adapter_dispositions.items())),
                "adapter_destination_counts": dict(sorted(adapter_destinations.items())),
                "adapter_blocker_counts": dict(sorted(adapter_blockers.items())),
            }
        )
    adapter_disposition_totals: Counter[str] = Counter()
    adapter_destination_totals: Counter[str] = Counter()
    adapter_blocker_totals: Counter[str] = Counter()
    for frame in frames:
        adapter_disposition_totals.update(frame["adapter_disposition_counts"])
        adapter_destination_totals.update(frame["adapter_destination_counts"])
        adapter_blocker_totals.update(frame["adapter_blocker_counts"])
    all_output = {
        "schema": "brain-senses-sell23-raw-capture-replay-v1",
        "schema_version": 1,
        "predicate_sha256": PREDICATE_SHA256,
        "status": "complete_c4_first_four_raw_capture_replay",
        "source_root_relative": ROOT_REL,
        "input_pins": INPUT_PINS,
        "root_identity": {
            "receipt_pinned_root_path": str(root),
            "device_id": root.stat().st_dev,
            "owner_uid": root.stat().st_uid,
            "inode": root.stat().st_ino,
        },
        "source_sha256": source_hashes,
        "frames": frames,
        "denominators": {
            "scheduled_frames": 4,
            "sealed_frames": 4,
            "missed_frames": 0,
            "selected_blocks": 64,
            "produced_blocks": sum(s["produced_blocks"] for s in summaries),
            "scanned_transactions": totals["scanned_transactions"],
            "structural_sell23_references": totals["structural"],
            "failed_structural_references": totals["failed"],
            "successful_structural_references": totals["successful"],
            "outside_native_close_gate": totals["outside_native_close"],
            "native_close_rows": totals["native_close"],
            "source_flow_exact": totals["source_flow:source_account_flow_exact"],
            "source_flow_unsupported": totals["source_flow:unsupported_touch"],
            "destinations_ambiguous": totals["destination:destination_ambiguous"],
            "adapter_input_candidates": totals["native_close"],
            "adapter_evidence_insufficient": adapter_disposition_totals["evidence_insufficient"],
            "adapter_unsupported_instruction": adapter_disposition_totals[
                "unsupported_instruction"
            ],
            "adapter_destination_counts": dict(sorted(adapter_destination_totals.items())),
            "adapter_blocker_counts": dict(sorted(adapter_blocker_totals.items())),
            "provider_requests": 0,
        },
        "claims": {
            "source_account_flow_diagnostic_only": True,
            "destinations_settled": False,
            "native_proceeds_or_wallet_net": False,
            "rent_or_wrapped_amount_inferred": False,
            "economic_admission": False,
            "training_admission": False,
            "c3_bodies_opened": False,
            "c4_tail2_opened": False,
        },
        "limitations": [
            "Internal c4 original root; c3 raw copies withheld because no exact migration chain.",
            (
                "Event time, provider receipt, and local durable availability are distinct; "
                "no historical decision availability is inferred."
            ),
            (
                "This does not independently establish close destination settlement, historical "
                "Rent, wallet proceeds, or economics."
            ),
            (
                "Exact source-flow counts refer only to ordered explicit instruction facts; they "
                "do not imply exhaustive account activity, residual-to-zero, or wallet net."
            ),
        ],
    }
    if (
        all_output["denominators"]["structural_sell23_references"] != 388
        or all_output["denominators"]["failed_structural_references"] != 1
        or all_output["denominators"]["outside_native_close_gate"] != 45
        or all_output["denominators"]["native_close_rows"] != 342
        or all_output["denominators"]["source_flow_exact"] != 341
        or all_output["denominators"]["source_flow_unsupported"] != 1
        or all_output["denominators"]["destinations_ambiguous"] != 342
    ):
        raise ReplayError("c4_native_flow_expected_counts_mismatch")
    return all_output


def verify_report(report: dict[str, Any]) -> dict[str, Any]:
    """Validate report shape and independently recompute its aggregate claims."""
    if (
        report.get("schema") != "brain-senses-sell23-raw-capture-replay-v1"
        or report.get("predicate_sha256") != PREDICATE_SHA256
    ):
        raise ReplayError("report_identity_invalid")
    if report.get("status") != "complete_c4_first_four_raw_capture_replay":
        raise ReplayError("report_not_complete")
    frames = report.get("frames")
    if type(frames) is not list or [
        frame.get("summary", {}).get("bucket") for frame in frames
    ] != list(FRAME_IDS):
        raise ReplayError("report_frame_scope_invalid")
    totals: Counter[str] = Counter()
    adapter_disposition_totals: Counter[str] = Counter()
    adapter_destination_totals: Counter[str] = Counter()
    adapter_blocker_totals: Counter[str] = Counter()
    for frame in frames:
        output = frame.get("adapter_output")
        if type(output) is not dict:
            raise ReplayError("adapter_output_invalid")
        observed_dispositions = Counter(
            candidate.get("source_account_flow") for candidate in output.get("candidates", [])
        )
        observed_destinations = Counter(
            candidate.get("destination_status") for candidate in output.get("candidates", [])
        )
        observed_blockers = Counter(
            blocker
            for candidate in output.get("candidates", [])
            for blocker in candidate.get("blockers", [])
        )
        if (
            dict(sorted(observed_dispositions.items())) != frame.get("adapter_disposition_counts")
            or dict(sorted(observed_destinations.items()))
            != frame.get("adapter_destination_counts")
            or dict(sorted(observed_blockers.items())) != frame.get("adapter_blocker_counts")
        ):
            raise ReplayError("report_adapter_denominator_mismatch")
        adapter_disposition_totals.update(observed_dispositions)
        adapter_destination_totals.update(observed_destinations)
        adapter_blocker_totals.update(observed_blockers)
        for key, value in independently_check_adapter(output).items():
            totals[key] += value
    if (
        totals["source_flow_exact"] != 341
        or totals["unsupported_instruction"] != 1
        or totals["failed_attempt"] != 0
    ):
        raise ReplayError("report_adapter_totals_invalid")
    den = report.get("denominators", {})
    if (
        den.get("native_close_rows") != 342
        or den.get("structural_sell23_references") != 388
        or den.get("failed_structural_references") != 1
        or den.get("outside_native_close_gate") != 45
        or den.get("source_flow_exact") != 341
        or den.get("source_flow_unsupported") != 1
        or den.get("destinations_ambiguous") != 342
        or den.get("adapter_input_candidates") != 342
        or den.get("adapter_evidence_insufficient")
        != adapter_disposition_totals["evidence_insufficient"]
        or den.get("adapter_unsupported_instruction")
        != adapter_disposition_totals["unsupported_instruction"]
        or den.get("adapter_destination_counts") != dict(sorted(adapter_destination_totals.items()))
        or den.get("adapter_blocker_counts") != dict(sorted(adapter_blocker_totals.items()))
    ):
        raise ReplayError("report_denominator_mismatch")
    return {
        "status": "verified",
        "source_flow_exact": totals["source_flow_exact"],
        "unsupported": totals["unsupported_instruction"],
        "destinations_ambiguous": den["destinations_ambiguous"],
    }


def main() -> None:
    result = replay()
    verify_report(result)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
