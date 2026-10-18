"""Compile an inert finite-calendar-v3/operation-envelope-v2 candidate from the c4 certificate.

The compiler verifies only the pinned initial-entry certificate and emits JSON to stdout. It does
not initialize a lease, write runtime state, load credentials, build clients, or activate anything.
All schedule, cap, source, lease, and clock-pin values are caller supplied.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import (  # noqa: E402, I001
    brain_senses_finite_calendar_v3 as calendar,
    brain_senses_initial_entry_discontinuity_verifier_v1 as certificate_verify,  # noqa: E501
    brain_senses_operation_bundle_v2 as bridge,
)

COMPILER_SCHEMA = "brain_senses_initial_entry_calendar_candidate_v1"
PREDECESSOR_KIND = "initial_entry_discontinuity_certificate_v1"
EXPECTED_CERTIFICATE_FILE_SHA256 = (
    "1fa16d3379798ba633ed4cb1c209c98e2f865b443e70dc8748392cb3abad3da5"
)
EXPECTED_PREDECESSOR_DIGEST_SHA256 = (
    "6f10576acf93609d3beecbf2a6949332d2ae47895406c26e846cfba3f1d90f7d"
)
MINIMUM_PHYSICAL_BYTES = 50 * 1024**3
CAP_NAMES = {
    "requests",
    "raw_bytes",
    "stored_bytes",
    "physical_bytes",
    "width",
    "daily_requests",
    "daily_raw_bytes",
    "daily_stored_bytes",
}
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _candidate_code_binding() -> dict[str, str]:
    return {
        "compiler_sha256": _sha(Path(__file__).read_bytes()),
        "verifier_sha256": _sha(
            (REPO_ROOT / "tools/brain_senses_initial_entry_calendar_verifier_v1.py").read_bytes()
        ),
        "child_v3_sha256": _sha((REPO_ROOT / "tools/brain_senses_block_epoch_v3.py").read_bytes()),
    }


def _valid_sha_map(value: Any) -> bool:
    return (
        bool(value)
        and isinstance(value, dict)
        and all(
            isinstance(key, str)
            and key
            and isinstance(digest, str)
            and len(digest) == 64
            and all(char in "0123456789abcdef" for char in digest)
            for key, digest in value.items()
        )
    )


def validate_inputs(certificate: dict[str, Any], inputs: dict[str, Any]) -> None:
    required = {
        "lease_id",
        "created_ns",
        "expires_ns",
        "clock_source_id",
        "clock_observed_ns",
        "buckets",
        "caps",
        "source_sha256",
    }
    if not isinstance(inputs, dict) or set(inputs) != required:
        raise ValueError("candidate_inputs_schema_invalid")
    watermark = certificate["terminal_observation_ns"]
    if (
        not isinstance(inputs["lease_id"], str)
        or not _SAFE_ID.fullmatch(inputs["lease_id"])
        or inputs["lease_id"] == certificate["c4_campaign_id"]
        or type(inputs["created_ns"]) is not int
        or type(inputs["expires_ns"]) is not int
        or type(inputs["clock_observed_ns"]) is not int
        or inputs["clock_observed_ns"] <= watermark
        or inputs["created_ns"] < inputs["clock_observed_ns"]
        or inputs["expires_ns"] <= inputs["created_ns"]
        or not isinstance(inputs["clock_source_id"], str)
        or not inputs["clock_source_id"].strip()
    ):
        raise ValueError("candidate_lease_or_clock_pin_invalid")
    caps = inputs["caps"]
    if (
        not isinstance(caps, dict)
        or set(caps) != CAP_NAMES
        or any(type(caps[name]) is not int or caps[name] <= 0 for name in CAP_NAMES)
        or caps["physical_bytes"] < MINIMUM_PHYSICAL_BYTES
    ):
        raise ValueError("candidate_caps_invalid")
    if not _valid_sha_map(inputs["source_sha256"]):
        raise ValueError("candidate_source_hashes_invalid")
    buckets = inputs["buckets"]
    if not isinstance(buckets, list) or not buckets:
        raise ValueError("candidate_schedule_missing")
    bucket_ids: set[str] = set()
    previous_end = inputs["created_ns"]
    for index, bucket in enumerate(buckets):
        if (
            not isinstance(bucket, dict)
            or set(bucket) != {"bucket_id", "kind", "start_ns", "end_ns"}
            or not isinstance(bucket["bucket_id"], str)
            or not _SAFE_ID.fullmatch(bucket["bucket_id"])
            or bucket["bucket_id"] in bucket_ids
            or bucket["kind"] not in {"anchor", "frame"}
            or type(bucket["start_ns"]) is not int
            or type(bucket["end_ns"]) is not int
            or bucket["start_ns"] <= watermark
            or (index == 0 and bucket["start_ns"] <= inputs["created_ns"])
            or bucket["start_ns"] < previous_end
            or bucket["end_ns"] <= bucket["start_ns"]
            or bucket["end_ns"] > inputs["expires_ns"]
            or (index == 0 and bucket["kind"] != "anchor")
        ):
            raise ValueError("candidate_schedule_invalid")
        bucket_ids.add(bucket["bucket_id"])
        previous_end = bucket["end_ns"]


def plan_from_verified_certificate(
    certificate: dict[str, Any], certificate_file_sha256: str, inputs: dict[str, Any]
) -> dict[str, Any]:
    """Pure mapping helper; caller must supply output of the independent cert verifier."""
    predecessor_digest = _sha(_canonical(certificate))
    if (
        certificate_file_sha256 != EXPECTED_CERTIFICATE_FILE_SHA256
        or predecessor_digest != EXPECTED_PREDECESSOR_DIGEST_SHA256
        or certificate.get("activation_authorized") is not False
        or certificate.get("normal_v3_handoff") is not False
        or certificate.get("frontier_slot") != 455430160
        or certificate.get("unknown_intervals", [{}])[0].get("end_exclusive") is not None
    ):
        raise ValueError("candidate_certificate_binding_invalid")
    validate_inputs(certificate, inputs)
    gap_ids = [gap["id"] for gap in certificate["gaps"]]
    if len(gap_ids) != 4 or len(set(gap_ids)) != 4:
        raise ValueError("candidate_certificate_gap_ids_invalid")
    predecessor = {
        "terminal_sha256": predecessor_digest,
        "certificate_file_sha256": certificate_file_sha256,
        "frontier_slot": certificate["frontier_slot"],
        "gaps": gap_ids,
        "unknown_intervals": copy.deepcopy(certificate["unknown_intervals"]),
    }
    return {
        "schema": bridge.SCHEMA,
        "calendar_contract": calendar.contract_binding(),
        "candidate_protocol": COMPILER_SCHEMA,
        "candidate_code_sha256": _candidate_code_binding(),
        "activation_authorized": False,
        "predecessor_digest_kind": PREDECESSOR_KIND,
        "initial_entry_certificate": {
            "certificate_file_sha256": certificate_file_sha256,
            "predecessor_digest_sha256": predecessor_digest,
            "source_receipt_sha256": certificate["source_receipt"]["sha256"],
        },
        "lease_id": inputs["lease_id"],
        "created_ns": inputs["created_ns"],
        "expires_ns": inputs["expires_ns"],
        "clock_source_id": inputs["clock_source_id"],
        "clock_observed_ns": inputs["clock_observed_ns"],
        "source_sha256": copy.deepcopy(inputs["source_sha256"]),
        "caps": copy.deepcopy(inputs["caps"]),
        "buckets": copy.deepcopy(inputs["buckets"]),
        "predecessor": predecessor,
    }


def _candidate_digest(body: dict[str, Any]) -> str:
    return _sha(_canonical(body))


def compile_candidate(certificate_path: Path, inputs: dict[str, Any]) -> dict[str, Any]:
    """Verify the certificate and return a hash-bound but inert envelope-plan candidate."""
    result = certificate_verify.verify_files(certificate_path)
    certificate_bytes = certificate_path.read_bytes()
    file_sha256 = _sha(certificate_bytes)
    if file_sha256 != result["certificate_file_sha256"]:
        raise ValueError("candidate_certificate_file_changed")
    certificate = json.loads(certificate_bytes)
    plan = plan_from_verified_certificate(certificate, file_sha256, inputs)
    body = {
        "schema": COMPILER_SCHEMA,
        "status": "inert_candidate",
        "certificate_file_sha256": file_sha256,
        "predecessor_digest_sha256": result["predecessor_digest_sha256"],
        "plan_sha256": _sha(_canonical(plan)),
        "plan": plan,
    }
    return {**body, "candidate_sha256": _candidate_digest(body)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("certificate", type=Path)
    parser.add_argument("inputs_json", type=Path)
    args = parser.parse_args()
    inputs = json.loads(args.inputs_json.read_text())
    candidate = compile_candidate(args.certificate, inputs)
    print(json.dumps(candidate, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
