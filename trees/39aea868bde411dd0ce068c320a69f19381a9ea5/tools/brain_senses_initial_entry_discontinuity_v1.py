"""Build an offline initial-entry/discontinuity certificate and inert lease proposal.

This tool does not initialize a lease, authorize activation, load credentials, or call a provider.
The c4 receipt is verified through its existing metadata-only verifier; response bodies stay opaque.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
CONTRACT = "brain_senses_initial_entry_discontinuity_certificate_v1"
PROPOSAL_CONTRACT = "brain_senses_initial_entry_lease_proposal_v1"
FRONTIER = 455430160
STOP_TIME_NS = 1791677100418875000


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _gap_id(prefix: str, identity: Any) -> str:
    return f"{prefix}-{_sha(canonical_bytes(identity))}"


def derive_certificate(receipt: dict[str, Any], receipt_sha256: str) -> dict[str, Any]:
    evidence = receipt["evidence"]
    stop = evidence["stop"]
    last = evidence["last_committed"]
    unknown = evidence["uncovered_continuation"]
    if (
        receipt_sha256 != EXPECTED_RECEIPT_SHA256
        or receipt.get("status") != "abnormal_disk_terminal_proven"
        or stop.get("reason") != "minimum_free_disk_floor_plus_frame_envelope"
        or stop.get("sha256") != abnormal.EXPECTED_STOP_SHA256
        or stop.get("time_ns") != STOP_TIME_NS
        or last.get("frontier_slot") != FRONTIER
        or evidence.get("zero_post_stop_io", {}).get("open_or_ambiguous_reservations") != 0
        or unknown
        != {
            "cause": "abnormal_disk_stop_without_successor_anchor",
            "cause_detail": stop["reason"],
            "closure_rule": (
                "only a future finalized anchor-only bootstrap may establish end_exclusive"
            ),
            "end_exclusive": None,
            "end_known": False,
            "start_frontier_slot": FRONTIER,
        }
    ):
        raise ValueError("initial_entry_terminal_contract_mismatch")
    original_gaps = last.get("preexisting_gaps")
    if not isinstance(original_gaps, list) or len(original_gaps) != 3:
        raise ValueError("initial_entry_gap_inventory_mismatch")
    inherited = []
    for gap in original_gaps:
        inherited.append(
            {
                "id": _gap_id("c4-gap", {"receipt_sha256": receipt_sha256, "gap": gap}),
                "source": "c4_preexisting_gap",
                "source_gap": gap,
            }
        )
    stop_gap = {
        "interval_start": FRONTIER,
        "interval_end_exclusive": None,
        "reason": "c4_abnormal_stop_discontinuity_unknown_end",
        "stop_sha256": stop["sha256"],
        "stop_time_ns": stop["time_ns"],
    }
    inherited.append(
        {
            "id": _gap_id(
                "c4-stop-discontinuity", {"receipt_sha256": receipt_sha256, "gap": stop_gap}
            ),
            "source": "c4_abnormal_stop_discontinuity",
            "source_gap": stop_gap,
        }
    )
    observation_times = [
        receipt["created_ns"],
        stop["time_ns"],
        receipt["corrected_unified_log_receipt"]["captured_at_ns"],
        *(row["observed_at_ns"] for row in receipt["observations"]),
    ]
    return {
        "schema": CONTRACT,
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
        "c4_campaign_id": receipt["evidence"]["campaign_id"],
        "terminal_observation_ns": max(observation_times),
        "stop": stop,
        "open_or_ambiguous_reservations": 0,
        "frontier_slot": FRONTIER,
        "anchor_slot": last["anchor_slot"],
        "gaps": inherited,
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


def build_certificate(
    receipt_path: Path = RECEIPT, campaign_root: Path | None = None
) -> dict[str, Any]:
    if receipt_path.resolve(strict=True) != RECEIPT.resolve(strict=True):
        raise ValueError("initial_entry_receipt_path_not_pinned")
    digest = _sha(receipt_path.read_bytes())
    if digest != EXPECTED_RECEIPT_SHA256:
        raise ValueError("initial_entry_receipt_hash_mismatch")
    root = campaign_root or abnormal.EXPECTED_ROOT
    verified = abnormal.verify(receipt_path, root)
    return derive_certificate(verified, digest)


def build_proposal(certificate: dict[str, Any], lease: dict[str, Any]) -> dict[str, Any]:
    from tools.brain_senses_initial_entry_discontinuity_verifier_v1 import (
        verify_certificate_data,
        verify_proposal_data,
    )

    receipt_bytes = RECEIPT.read_bytes()
    receipt_sha256 = _sha(receipt_bytes)
    if receipt_sha256 != EXPECTED_RECEIPT_SHA256:
        raise ValueError("initial_entry_receipt_hash_mismatch")
    verify_certificate_data(certificate, json.loads(receipt_bytes), receipt_sha256)
    cert_bytes = canonical_bytes(certificate)
    predecessor_digest_sha256 = _sha(cert_bytes)
    proposal = {
        "schema": PROPOSAL_CONTRACT,
        "status": "inert_successor_proposal",
        "units": {
            "timestamps": "UTC nanoseconds",
            "frontier_anchor_and_gap_endpoints": "Solana slot heights",
        },
        "predecessor_digest_sha256": predecessor_digest_sha256,
        "predecessor_digest_kind": "initial_entry_discontinuity_certificate",
        "lease": lease,
        "activation_authorized": False,
    }
    verify_proposal_data(certificate, proposal)
    return proposal


def write_create_only(path: Path, value: dict[str, Any]) -> None:
    payload = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False).encode() + b"\n"
    evidence_root = (REPO_ROOT / "docs/evidence").resolve(strict=True)
    resolved_parent = path.parent.resolve(strict=False)
    if not resolved_parent.is_relative_to(evidence_root):
        raise ValueError("initial_entry_output_must_be_under_repo_evidence")
    if any(
        parent.is_symlink() for parent in (path.parent, *path.parent.parents) if parent.exists()
    ):
        raise ValueError("initial_entry_output_symlink_parent")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path)
    args = parser.parse_args()
    write_create_only(args.output, build_certificate(campaign_root=args.campaign_root))
    print(json.dumps({"status": "verified_initial_discontinuity", "activation_authorized": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
