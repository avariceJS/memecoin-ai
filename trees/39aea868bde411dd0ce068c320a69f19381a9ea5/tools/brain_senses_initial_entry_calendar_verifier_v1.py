"""Independent read-only verifier for initial-entry finite-calendar candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import (  # noqa: E402, I001
    brain_senses_initial_entry_discontinuity_verifier_v1 as certificate_verify,  # noqa: E501
    brain_senses_operation_bundle_verifier_v2 as bridge_verify,
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
INPUT_KEYS = {
    "lease_id",
    "created_ns",
    "expires_ns",
    "clock_source_id",
    "clock_observed_ns",
    "buckets",
    "caps",
    "source_sha256",
}
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _calendar_binding() -> dict[str, str]:
    tools = REPO_ROOT / "tools"
    return {
        "schema": "brain-senses-finite-calendar-v3",
        "handoff_schema": "brain-senses-finite-calendar-handoff-v3",
        "verifier_protocol": "brain-senses-finite-calendar-handoff-verifier-v3",
        "driver_sha256": _sha((tools / "brain_senses_finite_calendar_v3.py").read_bytes()),
        "bridge_sha256": _sha((tools / "brain_senses_operation_bundle_v2.py").read_bytes()),
        "bridge_verifier_sha256": _sha(
            (tools / "brain_senses_operation_bundle_verifier_v2.py").read_bytes()
        ),
        "handoff_verifier_sha256": _sha(
            (tools / "brain_senses_finite_calendar_verifier_v3.py").read_bytes()
        ),
    }


def _candidate_code_binding() -> dict[str, str]:
    tools = REPO_ROOT / "tools"
    return {
        "compiler_sha256": _sha(
            (tools / "brain_senses_initial_entry_calendar_compiler_v1.py").read_bytes()
        ),
        "verifier_sha256": _sha(
            (tools / "brain_senses_initial_entry_calendar_verifier_v1.py").read_bytes()
        ),
        "child_v3_sha256": _sha((tools / "brain_senses_block_epoch_v3.py").read_bytes()),
    }


def _pins_valid(certificate: dict[str, Any], inputs: dict[str, Any]) -> None:
    if not isinstance(inputs, dict) or set(inputs) != INPUT_KEYS:
        raise ValueError("candidate_inputs_schema_invalid")
    watermark = certificate["terminal_observation_ns"]
    lease_id = inputs["lease_id"]
    if (
        not isinstance(lease_id, str)
        or not _SAFE_ID.fullmatch(lease_id)
        or lease_id == certificate["c4_campaign_id"]
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
    sources = inputs["source_sha256"]
    if (
        not isinstance(sources, dict)
        or not sources
        or any(
            not isinstance(key, str)
            or not key
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            for key, digest in sources.items()
        )
    ):
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


def _expected_plan(
    certificate: dict[str, Any], file_sha256: str, inputs: dict[str, Any]
) -> dict[str, Any]:
    _pins_valid(certificate, inputs)
    predecessor_digest = _sha(_canonical(certificate))
    if (
        file_sha256 != EXPECTED_CERTIFICATE_FILE_SHA256
        or predecessor_digest != EXPECTED_PREDECESSOR_DIGEST_SHA256
        or certificate.get("activation_authorized") is not False
        or certificate.get("normal_v3_handoff") is not False
        or certificate.get("frontier_slot") != 455430160
        or certificate.get("unknown_intervals", [{}])[0].get("end_exclusive") is not None
    ):
        raise ValueError("candidate_certificate_binding_invalid")
    gaps = [row["id"] for row in certificate["gaps"]]
    if len(gaps) != 4 or len(set(gaps)) != 4:
        raise ValueError("candidate_certificate_gap_ids_invalid")
    return {
        "schema": "brain-senses-prospective-operation-envelope-v2",
        "calendar_contract": _calendar_binding(),
        "candidate_protocol": COMPILER_SCHEMA,
        "candidate_code_sha256": _candidate_code_binding(),
        "activation_authorized": False,
        "predecessor_digest_kind": PREDECESSOR_KIND,
        "initial_entry_certificate": {
            "certificate_file_sha256": file_sha256,
            "predecessor_digest_sha256": predecessor_digest,
            "source_receipt_sha256": certificate["source_receipt"]["sha256"],
        },
        "lease_id": inputs["lease_id"],
        "created_ns": inputs["created_ns"],
        "expires_ns": inputs["expires_ns"],
        "clock_source_id": inputs["clock_source_id"],
        "clock_observed_ns": inputs["clock_observed_ns"],
        "source_sha256": inputs["source_sha256"],
        "caps": inputs["caps"],
        "buckets": inputs["buckets"],
        "predecessor": {
            "terminal_sha256": predecessor_digest,
            "certificate_file_sha256": file_sha256,
            "frontier_slot": certificate["frontier_slot"],
            "gaps": gaps,
            "unknown_intervals": certificate["unknown_intervals"],
        },
    }


def verify_candidate_data(
    candidate: dict[str, Any],
    certificate: dict[str, Any],
    certificate_file_sha256: str,
    expected_inputs: dict[str, Any],
) -> dict[str, Any]:
    allowed = {
        "schema",
        "status",
        "certificate_file_sha256",
        "predecessor_digest_sha256",
        "plan_sha256",
        "plan",
        "candidate_sha256",
    }
    if not isinstance(candidate, dict) or set(candidate) != allowed:
        raise ValueError("candidate_wrapper_schema_invalid")
    body = {key: value for key, value in candidate.items() if key != "candidate_sha256"}
    if (
        candidate.get("schema") != COMPILER_SCHEMA
        or candidate.get("status") != "inert_candidate"
        or candidate.get("candidate_sha256") != _sha(_canonical(body))
        or candidate.get("certificate_file_sha256") != certificate_file_sha256
    ):
        raise ValueError("candidate_wrapper_binding_invalid")
    expected_plan = _expected_plan(certificate, certificate_file_sha256, expected_inputs)
    plan = candidate.get("plan")
    if (
        plan != expected_plan
        or candidate.get("predecessor_digest_sha256") != EXPECTED_PREDECESSOR_DIGEST_SHA256
        or candidate.get("plan_sha256") != _sha(_canonical(expected_plan))
    ):
        raise ValueError("candidate_plan_or_pins_mismatch")
    bridge_verify._validate_plan(plan, _canonical(plan) + b"\n")
    if (
        plan.get("predecessor_digest_kind") != PREDECESSOR_KIND
        or plan.get("predecessor", {}).get("terminal_sha256")
        != candidate["predecessor_digest_sha256"]
        or plan.get("predecessor", {}).get("unknown_intervals") != certificate["unknown_intervals"]
    ):
        raise ValueError("candidate_initial_entry_transfer_mismatch")
    return {
        "status": "verified_inert_candidate",
        "activation_authorized": False,
        "candidate_sha256": candidate["candidate_sha256"],
        "plan_sha256": candidate["plan_sha256"],
        "certificate_file_sha256": certificate_file_sha256,
        "predecessor_digest_sha256": candidate["predecessor_digest_sha256"],
    }


def verify_candidate_files(
    candidate_path: Path, certificate_path: Path, expected_inputs: dict[str, Any]
) -> dict[str, Any]:
    certificate_proof = certificate_verify.verify_files(certificate_path)
    certificate_bytes = certificate_path.read_bytes()
    certificate = json.loads(certificate_bytes)
    file_sha256 = _sha(certificate_bytes)
    if (
        file_sha256 != certificate_proof["certificate_file_sha256"]
        or _sha(_canonical(certificate)) != certificate_proof["predecessor_digest_sha256"]
    ):
        raise ValueError("candidate_certificate_verification_changed")
    candidate = json.loads(candidate_path.read_text())
    return verify_candidate_data(candidate, certificate, file_sha256, expected_inputs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("certificate", type=Path)
    parser.add_argument("inputs_json", type=Path)
    args = parser.parse_args()
    result = verify_candidate_files(
        args.candidate, args.certificate, json.loads(args.inputs_json.read_text())
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
