from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

from tools.brain_senses_sell23_raw_capture_replay_v1 import (  # noqa: E402
    FRAME_IDS,
    PREDICATE_SHA256,
    ReplayError,
    _verify_c4_identity_chain,
    independently_check_adapter,
    verify_report,
)


def _effect(kind: str, order: int, outer: int, **fields: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "order": order,
        "path": {"outer_index": outer, "inner_index": None},
        **fields,
    }


def _exact_candidate() -> dict[str, Any]:
    source = "temporary-wsol"
    destination = "payer"
    return {
        "input_status": "successful",
        "source_account_flow": "source_account_flow_exact",
        "source_account": source,
        "target_token_account": "pool-token",
        "target_transfer_amount_atomic": 7,
        "close_destination_account": destination,
        "destination_status": "destination_ambiguous",
        "endpoints": {"source_lamports_pre": 0, "source_lamports_post": 0},
        "effects": [
            _effect(
                "system_create_account",
                0,
                0,
                payer=destination,
                account=source,
                lamports=9,
                space_bytes=165,
                owner_program="TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
            ),
            _effect(
                "token_initialize_account",
                1,
                1,
                account=source,
                token_program="TokenkegQfeZyiNwAJbN bGKPFXCWuBvf9Ss623VQ5DA".replace(" ", ""),
                mint="So11111111111111111111111111111111111111112",
                decimals=9,
            ),
            _effect(
                "system_transfer",
                2,
                2,
                source=destination,
                destination=source,
                lamports=13,
            ),
            _effect(
                "sync_native",
                3,
                3,
                account=source,
                token_program="TokenkegQfeZyiNwAJbN bGKPFXCWuBvf9Ss623VQ5DA".replace(" ", ""),
            ),
            _effect(
                "transfer_checked",
                4,
                4,
                source=source,
                destination="pool-token",
                token_program="TokenkegQfeZyiNwAJbN bGKPFXCWuBvf9Ss623VQ5DA".replace(" ", ""),
                mint="So11111111111111111111111111111111111111112",
                decimals=9,
                amount_atomic=7,
            ),
            _effect(
                "close_account",
                5,
                5,
                account=source,
                destination=destination,
                token_program="TokenkegQfeZyiNwAJbN bGKPFXCWuBvf9Ss623VQ5DA".replace(" ", ""),
            ),
            _effect(
                "opaque_touch",
                6,
                6,
                accounts=[destination, "another-account"],
                scope="unknown",
            ),
        ],
    }


def _report() -> dict[str, Any]:
    frames = []
    for frame_index, bucket in enumerate(FRAME_IDS):
        count = 85 if frame_index < 2 else 86
        candidates = [_exact_candidate() for _ in range(count)]
        if frame_index == 3:
            candidates[-1]["source_account_flow"] = "unsupported_instruction"
            candidates[-1]["effects"].append(
                _effect(
                    "unsupported_instruction",
                    7,
                    7,
                    accounts=["temporary-wsol"],
                    program_id="unknown-program",
                    instruction_name="unsupported",
                )
            )
        dispositions = {"source_account_flow_exact": count - (1 if frame_index == 3 else 0)}
        if frame_index == 3:
            dispositions["unsupported_instruction"] = 1
        frames.append(
            {
                "summary": {"bucket": bucket},
                "adapter_output": {"candidates": candidates},
                "adapter_disposition_counts": dispositions,
                "adapter_destination_counts": {"destination_ambiguous": count},
                "adapter_blocker_counts": {},
            }
        )
    return {
        "schema": "brain-senses-sell23-raw-capture-replay-v1",
        "schema_version": 1,
        "predicate_sha256": PREDICATE_SHA256,
        "status": "complete_c4_first_four_raw_capture_replay",
        "frames": frames,
        "denominators": {
            "native_close_rows": 342,
            "structural_sell23_references": 388,
            "failed_structural_references": 1,
            "outside_native_close_gate": 45,
            "source_flow_exact": 341,
            "source_flow_unsupported": 1,
            "destinations_ambiguous": 342,
            "adapter_input_candidates": 342,
            "adapter_evidence_insufficient": 0,
            "adapter_unsupported_instruction": 1,
            "adapter_destination_counts": {"destination_ambiguous": 342},
            "adapter_blocker_counts": {},
        },
    }


def _identity_chain() -> tuple[
    Path, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]
]:
    root = REPO / "work/brain-senses-live-helius-inventory-v3-20261010-4"
    campaign = "helius-inventory-v3-20261010-4"
    stop = "e8666a111d5e9aadd9c12044490e2d0ad4d42b0a204d57fbae0295ba036a9f00"
    anchor = 455249112
    availability = 1_791_633_901_559_773_000
    schedule = {"schedule_id": campaign, "provider": "helius-free"}
    lease = {
        "campaign_id": campaign,
        "provider": "helius-free",
        "lease_id": "helius-c3-abnormal-stop-recovery-v1",
        "root": str(root),
        "predecessor": {
            "campaign_id": "helius-inventory-v3-20261010-3",
            "status": "permanently_terminal_abnormal_anchor_failure",
            "stop_sha256": stop,
            "last_trusted_frontier": 455216025,
        },
        "bootstrap": {"receipt_sha256": "a" * 64},
    }
    bootstrap = {
        "campaign_id": campaign,
        "lease_id": lease["lease_id"],
        "bucket": "20261010T120000Z",
        "method": "getSlot(finalized)",
        "predecessor_stop_sha256": stop,
        "request_identity": {
            "campaign_id": campaign,
            "phase": "bootstrap_anchor",
            "sequence": 1,
            "utc_bucket": "20261010T120000Z",
        },
        "observed_anchor_slot": anchor,
        "availability_ns": availability,
        "uncovered_gap": {
            "reason": "uncovered_since_c3_trusted_frontier_no_backfill",
            "bootstrap_bucket": "20261010T120000Z",
            "start": 455216025,
            "end_exclusive": anchor + 1,
            "from_c3_stop_sha256": stop,
        },
    }
    lineage = {
        "campaign_id": campaign,
        "provider": "helius-free",
        "predecessor_campaign_id": lease["predecessor"]["campaign_id"],
        "predecessor_stop_sha256": stop,
        "bootstrap_receipt_sha256": lease["bootstrap"]["receipt_sha256"],
        "predecessor_status": "c3_permanently_terminal; c4 bootstrap is new observation",
        "last_attempt_bucket": bootstrap["bucket"],
        "last_observed_anchor_slot": anchor,
        "last_observed_anchor_receipt_ns": availability,
    }
    reservation = {
        "campaign_id": campaign,
        "lease_id": lease["lease_id"],
        "bucket": bootstrap["bucket"],
        "phase": "bootstrap_anchor",
        "sequence": 1,
        "status": "reserved_before_io",
        "request_limit": 1,
    }
    return root, schedule, lease, lineage, bootstrap, reservation


def test_independent_lamport_equation_and_destination_touch_are_rechecked() -> None:
    counts = independently_check_adapter({"candidates": [_exact_candidate()]})
    assert counts == {"source_flow_exact": 1}

    row = _exact_candidate()
    row["destination_status"] = "destination_settled"
    with pytest.raises(ReplayError, match="destination_disposition_invalid"):
        independently_check_adapter({"candidates": [row]})


def test_independent_lifecycle_order_and_source_equation_inputs_are_required() -> None:
    row = _exact_candidate()
    row["effects"][3]["order"] = 0
    with pytest.raises(ReplayError, match="adapter_exact_source_fact_mismatch"):
        independently_check_adapter({"candidates": [row]})

    row = _exact_candidate()
    row["endpoints"]["source_lamports_pre"] = None
    with pytest.raises(ReplayError, match="adapter_exact_source_fact_mismatch"):
        independently_check_adapter({"candidates": [row]})


def test_verified_report_preserves_full_native_denominator_and_withholds_claims() -> None:
    report = _report()
    assert verify_report(report) == {
        "status": "verified",
        "source_flow_exact": 341,
        "unsupported": 1,
        "destinations_ambiguous": 342,
    }

    changed = copy.deepcopy(report)
    changed["denominators"]["destinations_ambiguous"] = 341
    with pytest.raises(ReplayError, match="report_denominator_mismatch"):
        verify_report(changed)


def test_report_rejects_frame_reordering_or_missing_frame() -> None:
    report = _report()
    report["frames"].reverse()
    with pytest.raises(ReplayError, match="report_frame_scope_invalid"):
        verify_report(report)


def test_c4_metadata_lineage_binds_root_lease_bootstrap_and_gap() -> None:
    _verify_c4_identity_chain(*_identity_chain())

    root, schedule, lease, lineage, bootstrap, reservation = _identity_chain()
    lease["root"] = str(root / "elsewhere")
    with pytest.raises(ReplayError, match="c4_lease_root_binding_mismatch"):
        _verify_c4_identity_chain(root, schedule, lease, lineage, bootstrap, reservation)

    root, schedule, lease, lineage, bootstrap, reservation = _identity_chain()
    bootstrap["uncovered_gap"]["start"] += 1
    with pytest.raises(ReplayError, match="c4_bootstrap_gap_binding_mismatch"):
        _verify_c4_identity_chain(root, schedule, lease, lineage, bootstrap, reservation)


def test_c4_metadata_lineage_rejects_swapped_predecessor_or_bootstrap_reservation() -> None:
    args = _identity_chain()
    args[3]["predecessor_stop_sha256"] = "f" * 64
    with pytest.raises(ReplayError, match="c4_predecessor_lineage_binding_mismatch"):
        _verify_c4_identity_chain(*args)

    args = _identity_chain()
    args[5]["sequence"] = 2
    with pytest.raises(ReplayError, match="c4_bootstrap_reservation_binding_mismatch"):
        _verify_c4_identity_chain(*args)

    report = _report()
    report["frames"].pop()
    with pytest.raises(ReplayError, match="report_frame_scope_invalid"):
        verify_report(report)
