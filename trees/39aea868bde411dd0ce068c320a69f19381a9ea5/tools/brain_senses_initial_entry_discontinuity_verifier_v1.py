"""Independent offline verifier for the initial-entry/discontinuity-v1 certificate."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import brain_senses_c4_abnormal_terminal_qualifier as abnormal  # noqa: E402

RECEIPT = REPO_ROOT / "docs/evidence/helius-inventory-v3-c4-abnormal-terminal-2026-10-11.json"
RECEIPT_REPO_PATH = "docs/evidence/helius-inventory-v3-c4-abnormal-terminal-2026-10-11.json"
EXPECTED_RECEIPT_SHA256 = "8b97dd4936ddcd6e8494d35e06cb2a86d01929589e4c4c4c5ff7ba91825cbe65"
CERTIFICATE_SCHEMA = "brain_senses_initial_entry_discontinuity_certificate_v1"
PROPOSAL_SCHEMA = "brain_senses_initial_entry_lease_proposal_v1"
FRONTIER = 455430160
STOP_TIME_NS = 1791677100418875000


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _gap_id(prefix: str, identity: Any) -> str:
    return f"{prefix}-{_sha(_canonical(identity))}"


def _expected_certificate(receipt: dict[str, Any], receipt_sha256: str) -> dict[str, Any]:
    evidence = receipt["evidence"]
    stop = evidence["stop"]
    last = evidence["last_committed"]
    unknown = evidence["uncovered_continuation"]
    expected_unknown = {
        "cause": "abnormal_disk_stop_without_successor_anchor",
        "cause_detail": "minimum_free_disk_floor_plus_frame_envelope",
        "closure_rule": "only a future finalized anchor-only bootstrap may establish end_exclusive",
        "end_exclusive": None,
        "end_known": False,
        "start_frontier_slot": FRONTIER,
    }
    if (
        receipt_sha256 != EXPECTED_RECEIPT_SHA256
        or receipt.get("status") != "abnormal_disk_terminal_proven"
        or receipt.get("contract") != "brain_senses_c4_abnormal_disk_terminal_v1"
        or stop.get("sha256") != abnormal.EXPECTED_STOP_SHA256
        or stop.get("reason") != "minimum_free_disk_floor_plus_frame_envelope"
        or stop.get("attempt_count") != 11
        or stop.get("time_ns") != STOP_TIME_NS
        or stop.get("active_reservation_status") != "reconciled"
        or last.get("frontier_slot") != FRONTIER
        or last.get("anchor_slot") != FRONTIER - 1
        or evidence.get("zero_post_stop_io", {}).get("open_or_ambiguous_reservations") != 0
        or evidence.get("zero_post_stop_io", {}).get("post_stop_rpc_requests") != 0
        or evidence.get("zero_post_stop_io", {}).get("request_journal_check") != "passed"
        or unknown != expected_unknown
    ):
        raise ValueError("certificate_terminal_evidence_mismatch")
    source_gaps = last.get("preexisting_gaps")
    if not isinstance(source_gaps, list) or len(source_gaps) != 3:
        raise ValueError("certificate_source_gaps_mismatch")
    gaps = [
        {
            "id": _gap_id("c4-gap", {"receipt_sha256": receipt_sha256, "gap": gap}),
            "source": "c4_preexisting_gap",
            "source_gap": gap,
        }
        for gap in source_gaps
    ]
    stop_gap = {
        "interval_start": FRONTIER,
        "interval_end_exclusive": None,
        "reason": "c4_abnormal_stop_discontinuity_unknown_end",
        "stop_sha256": stop["sha256"],
        "stop_time_ns": stop["time_ns"],
    }
    gaps.append(
        {
            "id": _gap_id(
                "c4-stop-discontinuity", {"receipt_sha256": receipt_sha256, "gap": stop_gap}
            ),
            "source": "c4_abnormal_stop_discontinuity",
            "source_gap": stop_gap,
        }
    )
    watermark = max(
        receipt["created_ns"],
        stop["time_ns"],
        receipt["corrected_unified_log_receipt"]["captured_at_ns"],
        *(row["observed_at_ns"] for row in receipt["observations"]),
    )
    return {
        "schema": CERTIFICATE_SCHEMA,
        "status": "verified_initial_discontinuity",
        "units": {
            "timestamps": "UTC nanoseconds",
            "frontier_anchor_and_gap_endpoints": "Solana slot heights",
        },
        "predecessor_digest_kind": "initial_entry_discontinuity_certificate",
        "source_receipt": {
            "path": RECEIPT_REPO_PATH,
            "sha256": receipt_sha256,
            "qualifier_sha256": receipt["qualifier_sha256"],
            "normal_terminal_verifier_sha256": receipt["normal_terminal_verifier_sha256"],
        },
        "c4_campaign_id": evidence["campaign_id"],
        "terminal_observation_ns": watermark,
        "stop": stop,
        "open_or_ambiguous_reservations": 0,
        "frontier_slot": FRONTIER,
        "anchor_slot": last["anchor_slot"],
        "gaps": gaps,
        "unknown_intervals": [unknown],
        "normal_v3_handoff": False,
        "activation_authorized": False,
        "limitations": [
            "This certificate is not a normal v3 terminal handoff.",
            "The c4 response bodies and frozen 23:00Z tail2 body were not replayed or opened.",
            "The c4 continuation end remains unknown; this protocol cannot close it.",
            "No provider-side silence, account headroom, or activation authority is established.",
        ],
    }


def verify_certificate_data(
    certificate: dict[str, Any], receipt: dict[str, Any], receipt_sha256: str
) -> None:
    if certificate != _expected_certificate(receipt, receipt_sha256):
        raise ValueError("certificate_contents_mismatch")


def verify_proposal_data(certificate: dict[str, Any], proposal: dict[str, Any]) -> None:
    pinned_bytes = RECEIPT.read_bytes()
    pinned_sha256 = _sha(pinned_bytes)
    if pinned_sha256 != EXPECTED_RECEIPT_SHA256 or certificate != _expected_certificate(
        json.loads(pinned_bytes), pinned_sha256
    ):
        raise ValueError("proposal_certificate_not_pinned")
    if (
        set(proposal)
        != {
            "schema",
            "status",
            "units",
            "predecessor_digest_sha256",
            "predecessor_digest_kind",
            "lease",
            "activation_authorized",
        }
        or certificate.get("schema") != CERTIFICATE_SCHEMA
        or certificate.get("status") != "verified_initial_discontinuity"
        or certificate.get("activation_authorized") is not False
        or certificate.get("normal_v3_handoff") is not False
        or certificate.get("unknown_intervals", [{}])[0].get("end_exclusive") is not None
        or proposal.get("schema") != PROPOSAL_SCHEMA
        or proposal.get("status") != "inert_successor_proposal"
        or proposal.get("units")
        != {
            "timestamps": "UTC nanoseconds",
            "frontier_anchor_and_gap_endpoints": "Solana slot heights",
        }
        or proposal.get("activation_authorized") is not False
        or proposal.get("predecessor_digest_kind") != "initial_entry_discontinuity_certificate"
        or proposal.get("predecessor_digest_sha256") != _sha(_canonical(certificate))
        or any(key in proposal for key in ("terminal_sha256", "terminal_receipt_sha256", "handoff"))
    ):
        raise ValueError("proposal_not_bound_to_initial_discontinuity_certificate")
    lease = proposal.get("lease")
    if not isinstance(lease, dict):
        raise ValueError("proposal_lease_missing")
    allowed = {"lease_id", "created_ns", "expires_ns", "predecessor", "buckets"}
    if set(lease) != allowed:
        raise ValueError("proposal_lease_schema_mismatch")
    if (
        not isinstance(lease["lease_id"], str)
        or not lease["lease_id"]
        or lease["lease_id"] == certificate["c4_campaign_id"]
        or type(lease["created_ns"]) is not int
        or type(lease["expires_ns"]) is not int
        or lease["created_ns"] <= certificate["terminal_observation_ns"]
        or lease["expires_ns"] <= lease["created_ns"]
    ):
        raise ValueError("proposal_lease_identity_or_time_invalid")
    expected_predecessor = {
        "predecessor_digest_sha256": proposal["predecessor_digest_sha256"],
        "frontier_slot": certificate["frontier_slot"],
        "gap_ids": [gap["id"] for gap in certificate["gaps"]],
        "unknown_intervals": certificate["unknown_intervals"],
    }
    if lease["predecessor"] != expected_predecessor:
        raise ValueError("proposal_predecessor_lineage_mismatch")
    buckets = lease["buckets"]
    if not isinstance(buckets, list) or not buckets or not isinstance(buckets[0], dict):
        raise ValueError("proposal_schedule_missing")
    prior_end = lease["created_ns"]
    bucket_ids: set[str] = set()
    for index, bucket in enumerate(buckets):
        if (
            set(bucket) != {"bucket_id", "kind", "start_ns", "end_ns"}
            or not isinstance(bucket["bucket_id"], str)
            or not bucket["bucket_id"]
            or bucket["bucket_id"] in bucket_ids
            or bucket["kind"] not in {"anchor", "frame"}
            or type(bucket["start_ns"]) is not int
            or type(bucket["end_ns"]) is not int
            or bucket["start_ns"] <= certificate["terminal_observation_ns"]
            or bucket["start_ns"] < prior_end
            or (index == 0 and bucket["start_ns"] <= lease["created_ns"])
            or bucket["end_ns"] <= bucket["start_ns"]
            or bucket["end_ns"] > lease["expires_ns"]
            or (index == 0 and bucket["kind"] != "anchor")
        ):
            raise ValueError("proposal_schedule_invalid")
        bucket_ids.add(bucket["bucket_id"])
        prior_end = bucket["end_ns"]
    forbidden = {"anchor_result", "anchor_receipt", "closure_record", "closed_unknown_interval"}
    if forbidden.intersection(lease):
        raise ValueError("proposal_contains_unverified_anchor_authority")


def verify_files(certificate_path: Path, proposal_path: Path | None = None) -> dict[str, Any]:
    if RECEIPT.is_symlink() or RECEIPT.resolve(strict=True) != RECEIPT:
        raise ValueError("pinned_receipt_path_invalid")
    receipt_bytes = RECEIPT.read_bytes()
    receipt_sha256 = _sha(receipt_bytes)
    if receipt_sha256 != EXPECTED_RECEIPT_SHA256:
        raise ValueError("pinned_receipt_hash_mismatch")
    campaign_root = abnormal.EXPECTED_ROOT
    if campaign_root.is_symlink():
        raise ValueError("campaign_root_symlink")
    verified = abnormal.verify(RECEIPT, campaign_root)
    certificate = json.loads(certificate_path.read_text())
    verify_certificate_data(certificate, verified, receipt_sha256)
    certificate_file_sha256 = _sha(certificate_path.read_bytes())
    predecessor_digest_sha256 = _sha(_canonical(certificate))
    result: dict[str, Any] = {
        "certificate_file_sha256": certificate_file_sha256,
        "predecessor_digest_sha256": predecessor_digest_sha256,
        "status": "verified_initial_discontinuity",
        "activation_authorized": False,
    }
    if proposal_path is not None:
        proposal = json.loads(proposal_path.read_text())
        verify_proposal_data(certificate, proposal)
        result["proposal_status"] = "inert_successor_proposal"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("certificate", type=Path)
    parser.add_argument("--proposal", type=Path)
    args = parser.parse_args()
    result = verify_files(args.certificate, args.proposal)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
