"""Offline composition gate for synthetic attempt, account, and witness records.

The envelope composes three independent synthetic validators. It is not a
runtime witness, source-identity join, settlement result, or economic signal.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tools import brain_senses_agave_commit_to_root_join_v1 as commit_join
from tools import brain_senses_candidate_position_witness_check_v1 as candidate_check
from tools import brain_senses_raw_account_fixture_v1 as raw_fixture

REPO = Path(__file__).resolve().parents[1]
SCHEMA = "candidate-position-witness-envelope-v2"
SCHEMA_VERSION = 2
RECEIPT_SCHEMA = "candidate-position-witness-envelope-v2-verification"
FALSE_CLAIMS = {
    "runtime_source": False,
    "runtime_law_qualified": False,
    "candidate_position_admitted": False,
    "settlement_qualified": False,
    "buyExact25_qualified": False,
    "economic_admission": False,
    "point_in_time_eligible": False,
}
ENVELOPE_FIELDS = {
    "schema",
    "schema_version",
    "source_sha256",
    "commit_to_root",
    "raw_account_fixture",
    "candidate_witnesses",
    "synthetic_crosswalk",
    "claims",
}


class EnvelopeRejected(ValueError):
    """A synthetic composition cannot be independently replayed."""


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EnvelopeRejected("envelope_not_canonicalizable") from exc


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def source_bindings() -> dict[str, str]:
    paths = {
        "tools/brain_senses_agave_commit_to_root_join_v1.py": REPO
        / "tools/brain_senses_agave_commit_to_root_join_v1.py",
        "tools/brain_senses_candidate_position_witness_check_v1.py": REPO
        / "tools/brain_senses_candidate_position_witness_check_v1.py",
        "tools/brain_senses_candidate_position_witness_envelope_v1.py": Path(__file__),
        "tools/brain_senses_raw_account_fixture_schema_v1.py": REPO
        / "tools/brain_senses_raw_account_fixture_schema_v1.py",
        "tools/brain_senses_raw_account_fixture_v1.py": REPO
        / "tools/brain_senses_raw_account_fixture_v1.py",
    }
    return {name: sha256(path.read_bytes()) for name, path in sorted(paths.items())}


def _exact(value: Any, fields: set[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise EnvelopeRejected(code)
    return value


def _digest(value: Any, code: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or value != value.lower():
        raise EnvelopeRejected(code)
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise EnvelopeRejected(code) from exc
    return value


def _attempt_rows(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = payload.get("attempts")
    if not isinstance(rows, list):
        raise EnvelopeRejected("commit_attempts_missing")
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("attempt_id"), str):
            raise EnvelopeRejected("commit_attempt_invalid")
        attempt_id = row["attempt_id"]
        if attempt_id in indexed:
            raise EnvelopeRejected("commit_attempt_duplicate")
        indexed[attempt_id] = row
    return indexed


def _summarize_witness(
    witness_id: str,
    witness: Mapping[str, Any],
) -> dict[str, Any]:
    checked = candidate_check.check_witness(witness)
    if checked.get("synthetic_contract_valid") is not True:
        raise EnvelopeRejected("candidate_witness_rejected")
    execution = witness.get("execution")
    if not isinstance(execution, Mapping):
        raise EnvelopeRejected("witness_execution_missing")
    witness_disposition = execution.get("disposition")
    if witness_disposition not in {"successful_transaction", "failed_attempt_exact"}:
        raise EnvelopeRejected("witness_execution_disposition_mismatch")
    signature = witness.get("signature")
    message_sha256 = witness.get("message_sha256")
    if not isinstance(signature, str) or not signature:
        raise EnvelopeRejected("witness_signature_missing")
    _digest(message_sha256, "witness_message_hash")
    return {
        "witness_id": witness_id,
        "candidate_witness_status": "synthetic_contract_consistent",
        "execution_disposition": witness_disposition,
        "signature_sha256": hashlib.sha256(signature.encode("utf-8")).hexdigest(),
        "message_sha256": message_sha256,
        "cross_component_linkage": "unverified_pending_semantic_decision",
        "witness_availability_upper_bound_utc_ms": checked.get(
            "admission_availability_upper_bound_utc_ms"
        ),
        "event_time_ns": None,
        "provider_receipt_ns": None,
        "durable_available_ns": None,
        "decision_ns": None,
        "availability_status": checked.get("availability_status", "unknown"),
        "unit_contract": dict(witness.get("unit_contract", {})),
        "candidate_position_status": "unknown_or_exact_frame_state_only",
        "hook_effect_status": "unknown_or_unsupported",
    }


def verify_envelope(
    value: Any, *, expected_sources: Mapping[str, str] | None = None
) -> dict[str, Any]:
    envelope = _exact(value, ENVELOPE_FIELDS, "envelope_shape")
    if envelope["schema"] != SCHEMA or envelope["schema_version"] != SCHEMA_VERSION:
        raise EnvelopeRejected("envelope_schema")
    if envelope["claims"] != FALSE_CLAIMS:
        raise EnvelopeRejected("qualification_claim_present")
    sources = source_bindings()
    expected = dict(expected_sources) if expected_sources is not None else sources
    if (
        not isinstance(envelope["source_sha256"], Mapping)
        or dict(envelope["source_sha256"]) != expected
        or expected != sources
    ):
        raise EnvelopeRejected("source_binding_mismatch")

    commit_record = _exact(
        envelope["commit_to_root"], {"payload", "sha256"}, "commit_component_shape"
    )
    commit_payload = commit_record["payload"]
    if sha256(commit_join.canonical_json_bytes(commit_payload)) != _digest(
        commit_record["sha256"], "commit_component_hash"
    ):
        raise EnvelopeRejected("commit_component_hash")
    try:
        commit_verdict = commit_join.verify(commit_payload)
    except (commit_join.JoinError, TypeError, KeyError, ValueError) as exc:
        raise EnvelopeRejected("commit_component_rejected") from exc
    attempts = _attempt_rows(commit_payload)
    joined_rows = {
        row["attempt_id"]: row for row in commit_verdict["joins"] if isinstance(row, Mapping)
    }
    if set(joined_rows) != set(attempts):
        raise EnvelopeRejected("commit_join_coverage_mismatch")

    raw_record = _exact(
        envelope["raw_account_fixture"], {"bundle", "sha256"}, "raw_component_shape"
    )
    raw_bundle = raw_record["bundle"]
    raw_bytes = raw_fixture.canonical(raw_bundle)
    if sha256(raw_bytes) != _digest(raw_record["sha256"], "raw_component_hash"):
        raise EnvelopeRejected("raw_component_hash")
    try:
        raw_verdict = raw_fixture.verify_bundle_bytes(raw_bytes)
    except (raw_fixture.FixtureRejected, TypeError, KeyError, ValueError) as exc:
        raise EnvelopeRejected("raw_component_rejected") from exc

    witness_rows = envelope["candidate_witnesses"]
    if not isinstance(witness_rows, list):
        raise EnvelopeRejected("witness_rows_invalid")
    witness_ids: set[str] = set()
    witness_signatures: set[str] = set()
    crosschecked: list[dict[str, Any]] = []
    for row in witness_rows:
        record = _exact(row, {"witness_id", "witness", "sha256"}, "witness_row_shape")
        witness_id = record["witness_id"]
        if not isinstance(witness_id, str) or not witness_id or witness_id in witness_ids:
            raise EnvelopeRejected("witness_attempt_duplicate_or_invalid")
        witness_ids.add(witness_id)
        witness = record["witness"]
        if not isinstance(witness, Mapping):
            raise EnvelopeRejected("witness_not_object")
        if sha256(candidate_check.canonical_json_bytes(witness)) != _digest(
            record["sha256"], "witness_hash"
        ):
            raise EnvelopeRejected("witness_hash")
        signature = witness.get("signature")
        if not isinstance(signature, str) or signature in witness_signatures:
            raise EnvelopeRejected("witness_signature_duplicate_or_invalid")
        witness_signatures.add(signature)
        try:
            crosschecked.append(_summarize_witness(witness_id, witness))
        except (TypeError, KeyError, ValueError, OverflowError) as exc:
            raise EnvelopeRejected("candidate_witness_rejected") from exc
    if not crosschecked:
        raise EnvelopeRejected("candidate_witnesses_empty")
    raw_rows = raw_verdict["vectors"]
    raw_by_case = {row["case_id"]: row for row in raw_rows}
    witness_payload_by_id = {row["witness_id"]: row["witness"] for row in witness_rows}
    crosswalk_rows = envelope["synthetic_crosswalk"]
    if not isinstance(crosswalk_rows, list) or not crosswalk_rows:
        raise EnvelopeRejected("synthetic_crosswalk_empty")
    crosswalk_ids: dict[str, set[str]] = {
        key: set() for key in ("attempt_id", "witness_id", "raw_case_id", "raw_capture_id")
    }
    crosswalk_receipts: list[dict[str, Any]] = []
    for row in crosswalk_rows:
        crosswalk = _exact(
            row,
            {
                "attempt_id",
                "witness_id",
                "raw_case_id",
                "raw_capture_id",
                "slot",
                "blockhash",
                "claimed_signature_sha256",
                "claimed_message_sha256",
            },
            "synthetic_crosswalk_shape",
        )
        attempt_id = crosswalk["attempt_id"]
        witness_id = crosswalk["witness_id"]
        case_id = crosswalk["raw_case_id"]
        capture_id = crosswalk["raw_capture_id"]
        if (
            not isinstance(attempt_id, str)
            or attempt_id not in attempts
            or not isinstance(witness_id, str)
            or witness_id not in witness_payload_by_id
            or not isinstance(case_id, str)
            or case_id not in raw_by_case
            or not isinstance(capture_id, str)
            or type(crosswalk["slot"]) is not int
            or crosswalk["slot"] < 0
            or not isinstance(crosswalk["blockhash"], str)
            or not crosswalk["blockhash"]
        ):
            raise EnvelopeRejected("synthetic_crosswalk_reference_invalid")
        for key, value in (
            ("attempt_id", attempt_id),
            ("witness_id", witness_id),
            ("raw_case_id", case_id),
            ("raw_capture_id", capture_id),
        ):
            if value in crosswalk_ids[key]:
                raise EnvelopeRejected("synthetic_crosswalk_duplicate_or_spliced")
            crosswalk_ids[key].add(value)

        attempt = attempts[attempt_id]
        witness = witness_payload_by_id[witness_id]
        raw = raw_by_case[case_id]
        frame = raw["frame"]
        commit_join_row = joined_rows[attempt_id]
        witness_receipts = {
            receipt["type"]: receipt["payload"] for receipt in witness.get("receipts", [])
        }
        bank_commit = witness_receipts.get("bank_commit", {})
        block_join = witness_receipts.get("block_join", {})
        root_status = witness_receipts.get("root_status", {})
        signature = witness.get("signature")
        message_sha256 = witness.get("message_sha256")
        if (
            commit_join_row.get("disposition") not in {"executed_success", "executed_failed"}
            or attempt.get("signature") != signature
            or attempt.get("message_hash") != message_sha256
            or _digest(crosswalk["claimed_signature_sha256"], "crosswalk_signature_hash")
            != hashlib.sha256(signature.encode("utf-8")).hexdigest()
            or _digest(crosswalk["claimed_message_sha256"], "crosswalk_message_hash")
            != message_sha256
            or raw_fixture.sha256(
                raw_fixture.canonical({"slot": frame["slot"], "blockhash": frame["blockhash"]})
            )
            != capture_id
            or crosswalk["slot"] != frame.get("slot")
            or crosswalk["blockhash"] != frame.get("blockhash")
            or crosswalk["slot"] != bank_commit.get("slot")
            or crosswalk["slot"] != block_join.get("slot")
            or crosswalk["slot"] != root_status.get("slot")
            or crosswalk["blockhash"] != commit_join_row.get("block_hash")
            or crosswalk["blockhash"] != block_join.get("ledger_blockhash")
            or crosswalk["blockhash"] != root_status.get("ledger_blockhash")
        ):
            raise EnvelopeRejected("synthetic_crosswalk_conflict")
        crosswalk_receipts.append(
            {
                "attempt_id": attempt_id,
                "witness_id": witness_id,
                "raw_case_id": case_id,
                "raw_capture_id": capture_id,
                "slot": crosswalk["slot"],
                "blockhash": crosswalk["blockhash"],
                "association_status": "synthetic_unverified_hypothesis_only",
            }
        )
    raw_times = [
        {
            "case_id": row["case_id"],
            "capture_id": raw_fixture.sha256(
                raw_fixture.canonical(
                    {"slot": row["frame"]["slot"], "blockhash": row["frame"]["blockhash"]}
                )
            ),
            "slot": row["frame"]["slot"],
            "blockhash": row["frame"]["blockhash"],
            "transaction_status": row["transaction_status"],
            "event_time_ns": row["frame"]["event_time_ns"],
            "receipt_ns": row["frame"]["receipt_ns"],
            "durable_available_ns": row["frame"]["durable_available_ns"],
            "decision_ns": None,
            "point_in_time_eligible": False,
        }
        for row in raw_rows
    ]
    return {
        "schema": RECEIPT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "status": "synthetic_components_consistent",
        "envelope_sha256": sha256(canonical_json_bytes(dict(envelope))),
        "source_sha256": sources,
        "attempt_count": len(attempts),
        "unique_rooted_join_count": len(joined_rows),
        "candidate_witness_count": len(crosschecked),
        "candidate_witnesses": sorted(crosschecked, key=lambda row: row["witness_id"]),
        "synthetic_crosswalk_count": len(crosswalk_receipts),
        "synthetic_crosswalk": sorted(crosswalk_receipts, key=lambda row: row["attempt_id"]),
        "attempt_dispositions": sorted(
            [
                {
                    "attempt_id": attempt_id,
                    "disposition": joined_rows[attempt_id]["disposition"],
                    "candidate_position_status": (
                        "unavailable_fees_only"
                        if joined_rows[attempt_id]["disposition"] == "fees_only"
                        else "unknown_candidate_witness_unlinked"
                    ),
                }
                for attempt_id in attempts
            ],
            key=lambda row: row["attempt_id"],
        ),
        "raw_account_vector_count": len(raw_rows),
        "raw_account_vectors": sorted(raw_times, key=lambda row: row["case_id"]),
        "raw_execution_linkage": "unknown_raw_contract_has_no_transaction_identity",
        "raw_execution_association": "unknown_synthetic_unverified_crosswalk",
        "cross_component_linkage": "synthetic_unverified_hypothesis_only",
        "failed_attempts_are_attempted_only": True,
        "fees_only_rows_have_no_candidate_witness": True,
        "claims": dict(FALSE_CLAIMS),
    }


def verify_bytes(
    raw: bytes, *, expected_sources: Mapping[str, str] | None = None
) -> dict[str, Any]:
    if not isinstance(raw, bytes) or len(raw) > 16_000_000:
        raise EnvelopeRejected("envelope_size")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise EnvelopeRejected("envelope_duplicate_json_key")
            result[key] = item
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except EnvelopeRejected:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnvelopeRejected("envelope_json") from exc
    if canonical_json_bytes(value) != raw:
        raise EnvelopeRejected("envelope_not_canonical")
    return verify_envelope(value, expected_sources=expected_sources)
