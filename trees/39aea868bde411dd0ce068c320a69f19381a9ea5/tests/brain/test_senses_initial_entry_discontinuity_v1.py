from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools import brain_senses_c4_abnormal_terminal_qualifier as abnormal
from tools import brain_senses_initial_entry_discontinuity_v1 as builder
from tools import brain_senses_initial_entry_discontinuity_verifier_v1 as verifier


def _evidence() -> tuple[dict, str, dict]:
    receipt = json.loads(builder.RECEIPT.read_text())
    digest = builder._sha(builder.RECEIPT.read_bytes())
    return receipt, digest, builder.derive_certificate(receipt, digest)


def _proposal(certificate: dict) -> dict:
    return builder.build_proposal(
        certificate,
        {
            "lease_id": "fresh-future-lease",
            "created_ns": certificate["terminal_observation_ns"] + 10,
            "expires_ns": certificate["terminal_observation_ns"] + 10_000,
            "predecessor": {
                "predecessor_digest_sha256": builder._sha(builder.canonical_bytes(certificate)),
                "frontier_slot": certificate["frontier_slot"],
                "gap_ids": [gap["id"] for gap in certificate["gaps"]],
                "unknown_intervals": certificate["unknown_intervals"],
            },
            "buckets": [
                {
                    "bucket_id": "caller-selected-future-anchor",
                    "kind": "anchor",
                    "start_ns": certificate["terminal_observation_ns"] + 100,
                    "end_ns": certificate["terminal_observation_ns"] + 200,
                },
                {
                    "bucket_id": "caller-selected-future-frame",
                    "kind": "frame",
                    "start_ns": certificate["terminal_observation_ns"] + 300,
                    "end_ns": certificate["terminal_observation_ns"] + 400,
                },
            ],
        },
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda cert: cert["stop"].update(sha256="0" * 64),
        lambda cert: cert.update(frontier_slot=cert["frontier_slot"] + 1),
        lambda cert: cert["gaps"].pop(0),
        lambda cert: cert["gaps"][-1]["source_gap"].update(reason="forged"),
        lambda cert: cert["unknown_intervals"][0].update(end_exclusive=123),
        lambda cert: cert.update(normal_v3_handoff=True),
    ],
)
def test_certificate_mutations_rejected(mutate):
    receipt, digest, certificate = _evidence()
    changed = copy.deepcopy(certificate)
    mutate(changed)
    with pytest.raises(ValueError):
        verifier.verify_certificate_data(changed, receipt, digest)


def test_receipt_mutations_rejected():
    receipt, digest, certificate = _evidence()
    for path, value in (
        ("stop", "sha256"),
        ("stop", "time_ns"),
        ("frontier", "frontier_slot"),
        ("gaps", "preexisting_gaps"),
        ("unknown", "uncovered_continuation"),
    ):
        changed = copy.deepcopy(receipt)
        if path == "stop":
            changed["evidence"]["stop"][value] = "0" * 64
        elif path == "frontier":
            changed["evidence"]["last_committed"][value] += 1
        elif path == "gaps":
            changed["evidence"]["last_committed"][value].pop()
        else:
            changed["evidence"][value]["end_exclusive"] = 123
        with pytest.raises(ValueError):
            verifier.verify_certificate_data(certificate, changed, digest)


def test_missing_discontinuity_gap_and_fabricated_normal_handoff_rejected():
    _, _, certificate = _evidence()
    missing = copy.deepcopy(certificate)
    missing["gaps"] = missing["gaps"][:-1]
    proposal = _proposal(certificate)
    proposal["predecessor_digest_sha256"] = builder._sha(builder.canonical_bytes(missing))
    with pytest.raises(ValueError):
        verifier.verify_proposal_data(missing, proposal)
    fabricated = copy.deepcopy(proposal)
    fabricated["terminal_sha256"] = "a" * 64
    with pytest.raises(ValueError):
        verifier.verify_proposal_data(certificate, fabricated)


@pytest.mark.parametrize(
    "mutation",
    [
        "frame_first",
        "earlier",
        "created_boundary",
        "duplicate_lease",
        "duplicate_bucket",
        "closure",
        "wrong_units",
    ],
)
def test_proposal_schedule_and_identity_mutations_rejected(mutation):
    _, _, certificate = _evidence()
    proposal = _proposal(certificate)
    lease = proposal["lease"]
    if mutation == "frame_first":
        lease["buckets"][0]["kind"] = "frame"
    elif mutation == "earlier":
        lease["buckets"][0]["start_ns"] = certificate["terminal_observation_ns"]
    elif mutation == "created_boundary":
        lease["buckets"][0]["start_ns"] = lease["created_ns"]
    elif mutation == "duplicate_lease":
        lease["lease_id"] = certificate["c4_campaign_id"]
    elif mutation == "duplicate_bucket":
        lease["buckets"][1]["bucket_id"] = lease["buckets"][0]["bucket_id"]
    elif mutation == "wrong_units":
        proposal["units"]["timestamps"] = "seconds"
    else:
        lease["closure_record"] = {"anchor_slot": 455430161, "status": "finalized"}
    with pytest.raises(ValueError):
        verifier.verify_proposal_data(certificate, proposal)


@pytest.mark.parametrize("result", ["failed", "unsealed", "regressed"])
def test_anchor_outcome_or_unknown_closure_is_not_authorized(result):
    _, _, certificate = _evidence()
    proposal = _proposal(certificate)
    proposal["anchor_result"] = {"status": result, "slot": 455430158}
    with pytest.raises(ValueError):
        verifier.verify_proposal_data(certificate, proposal)


def test_actual_receipt_and_metadata_qualifier_are_replayed_without_body_reads(monkeypatch):
    original_hash = abnormal.normal._stable_hash
    seen: list[str] = []

    def guarded_hash(path: Path):
        name = path.name
        seen.append(name)
        assert name not in {"response.json", "response.json.zst"}
        assert "tail2" not in str(path).lower()
        return original_hash(path)

    monkeypatch.setattr(abnormal.normal, "_stable_hash", guarded_hash)
    certificate = builder.build_certificate()
    assert certificate["frontier_slot"] == 455430160
    assert certificate["unknown_intervals"][0]["end_exclusive"] is None
    assert certificate["activation_authorized"] is False
    assert seen


def test_source_receipt_is_exactly_pinned():
    assert builder._sha(builder.RECEIPT.read_bytes()) == builder.EXPECTED_RECEIPT_SHA256
    assert builder.RECEIPT == verifier.RECEIPT
    assert verifier.EXPECTED_RECEIPT_SHA256 == builder.EXPECTED_RECEIPT_SHA256
    receipt, digest, certificate = _evidence()
    with pytest.raises(ValueError):
        verifier.verify_certificate_data(certificate, receipt, "0" * 64)


def test_later_anchor_reset_is_allowed_in_bounded_schedule():
    _, _, certificate = _evidence()
    proposal = _proposal(certificate)
    proposal["lease"]["buckets"][1]["kind"] = "anchor"
    verifier.verify_proposal_data(certificate, proposal)
