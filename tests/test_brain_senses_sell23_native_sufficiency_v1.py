from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

from tools.brain_senses_sell23_native_sufficiency_v1 import (  # noqa: E402
    INPUT_SCHEMA,
    LEGACY_TOKEN_PROGRAM,
    OUTPUT_SCHEMA,
    TOKEN_2022_PROGRAM,
    WSOL_MINT,
    SufficiencyError,
    canonical_json,
    compile_envelope,
    sha256_bytes,
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _endpoints() -> dict[str, Any]:
    return {
        "source_owner_program": LEGACY_TOKEN_PROGRAM,
        "source_token_mint": WSOL_MINT,
        "source_token_pre_atomic": 1_000,
        "source_token_post_atomic": 0,
        "source_lamports_pre": 0,
        "source_lamports_post": 0,
        "rent_sysvar_lamports": 2_039_280,
        "destination_owner_program": LEGACY_TOKEN_PROGRAM,
        "destination_lamports_pre": 5_000,
        "destination_lamports_post": 8_000,
        "closed_token_pre_atomic": 0,
        "closed_token_post_atomic": 0,
    }


def _effect(kind: str, order: int, outer: int, **fields: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "order": order,
        "path": {"outer_index": outer, "inner_index": None},
        **fields,
    }


def _successful_row(candidate_id: str = "candidate-1") -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "input_status": "successful",
        "signature_sha256": _digest("signature-1"),
        "slot": 500,
        "blockhash": "block-hash-500",
        "capture_id": "capture-500",
        "layout_status": "supported_sell23",
        "source_account": "temporary-wsol-account",
        "target_token_account": "pool-wsol-account",
        "destination_account": "payer",
        "target_transfer_amount_atomic": 1_000,
        "ordered_effects_complete": True,
        "effects": [
            _effect(
                "system_create_account",
                0,
                0,
                payer="payer",
                account="temporary-wsol-account",
                lamports=2_039_280,
                space_bytes=165,
                owner_program=LEGACY_TOKEN_PROGRAM,
            ),
            _effect(
                "token_initialize_account",
                1,
                1,
                account="temporary-wsol-account",
                token_program=LEGACY_TOKEN_PROGRAM,
                mint=WSOL_MINT,
                decimals=9,
            ),
            _effect(
                "system_transfer",
                2,
                2,
                source="payer",
                destination="temporary-wsol-account",
                lamports=10_000,
            ),
            _effect(
                "sync_native",
                3,
                3,
                account="temporary-wsol-account",
                token_program=LEGACY_TOKEN_PROGRAM,
            ),
            _effect(
                "transfer_checked",
                4,
                4,
                source="temporary-wsol-account",
                destination="pool-wsol-account",
                token_program=LEGACY_TOKEN_PROGRAM,
                mint=WSOL_MINT,
                decimals=9,
                amount_atomic=1_000,
            ),
            _effect(
                "close_account",
                5,
                5,
                account="temporary-wsol-account",
                destination="payer",
                token_program=LEGACY_TOKEN_PROGRAM,
            ),
        ],
        "endpoints": _endpoints(),
    }


def _envelope(rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    candidates = rows if rows is not None else [_successful_row()]
    counts = {"successful": 0, "failed": 0, "unsupported": 0, "unknown": 0}
    for row in candidates:
        counts[row["input_status"]] += 1
    envelope: dict[str, Any] = {
        "schema": INPUT_SCHEMA,
        "schema_version": 1,
        "lineage": {
            "frame_id": "frame-20261012-0500",
            "frame_sha256": _digest("frame"),
            "plan_sha256": _digest("plan"),
            "request_sha256": _digest("request-set"),
            "source_sha256": {
                "frame_verifier": _digest("frame-verifier"),
                "candidate_parser": _digest("candidate-parser"),
            },
        },
        "frame": {
            "status": "sealed",
            "scheduled_frames": 6,
            "sealed_frames": 6,
            "missed_frames": 0,
            "selected_blocks": 96,
            "produced_blocks": 96,
        },
        "denominators": {"selected_candidates": len(candidates), "status_counts": counts},
        "captures": [
            {
                "capture_id": "capture-500",
                "capture_sha256": _digest("capture"),
                "request_sha256": _digest("request-500"),
                "slot": 500,
                "blockhash": "block-hash-500",
                "event_time_ns": 1_000,
                "receipt_ns": 2_000,
                "availability_ns": 3_000,
            }
        ],
        "selection_rows": candidates,
    }
    envelope["verification"] = {
        "status": "independently_verified",
        "verifier_sha256": _digest("upstream-verifier"),
        "envelope_sha256": sha256_bytes(canonical_json(envelope)),
    }
    return envelope


def _refresh_hash(envelope: dict[str, Any]) -> None:
    envelope["verification"]["envelope_sha256"] = sha256_bytes(
        canonical_json({k: v for k, v in envelope.items() if k != "verification"})
    )


def test_exact_source_flow_preserves_lineage_times_and_denominators_without_settlement() -> None:
    output = compile_envelope(_envelope())
    assert output["schema"] == OUTPUT_SCHEMA
    assert output["candidates"][0]["source_account_flow"] == "source_account_flow_exact"
    assert output["candidates"][0]["destination_status"] == "destination_unqualified"
    assert output["candidates"][0]["event_time_ns"] == 1_000
    assert output["candidates"][0]["receipt_ns"] == 2_000
    assert output["candidates"][0]["availability_ns"] == 3_000
    assert output["selection_denominators"]["selected_candidates"] == 1
    assert output["selection_denominators"]["status_counts"] == {
        "successful": 1,
        "failed": 0,
        "unsupported": 0,
        "unknown": 0,
    }
    assert output["lineage"]["request_sha256"] == _digest("request-set")
    assert output["decision_ns"] is None
    assert output["destination_settlement_qualified"] is False
    assert output["native_proceeds_qualified"] is False
    assert output["wallet_net_qualified"] is False
    assert output["buy_exact25_admission"] is False
    assert output["economic_admission"] is False
    assert output["training_admission"] is False
    assert "residual" not in str(output).lower()


@pytest.mark.parametrize("scope", ["sibling", "parent"])
def test_opaque_destination_touch_keeps_destination_ambiguous(scope: str) -> None:
    row = _successful_row()
    row["effects"].insert(
        5,
        _effect(
            "opaque_touch",
            5,
            6,
            accounts=["payer", "other-account"],
            scope=scope,
        ),
    )
    row["effects"][-1]["order"] = 6
    row["effects"][-1]["path"] = {"outer_index": 7, "inner_index": None}
    result = compile_envelope(_envelope([row]))["candidates"][0]
    assert result["source_account_flow"] == "source_account_flow_exact"
    assert result["destination_status"] == "destination_ambiguous"
    assert "opaque_sibling_or_parent_destination_touch" in result["blockers"]


def test_missing_rent_destination_owner_and_closed_token_endpoints_are_blockers() -> None:
    row = _successful_row()
    for key in (
        "rent_sysvar_lamports",
        "source_owner_program",
        "source_token_mint",
        "source_token_pre_atomic",
        "source_token_post_atomic",
        "closed_token_pre_atomic",
        "closed_token_post_atomic",
        "destination_owner_program",
    ):
        row["endpoints"][key] = None
    result = compile_envelope(_envelope([row]))["candidates"][0]
    assert result["source_account_flow"] == "source_account_flow_exact"
    assert {
        "historical_rent_endpoint_missing",
        "source_owner_endpoint_missing",
        "source_token_mint_endpoint_missing",
        "source_token_endpoints_missing",
        "closed_token_endpoints_missing",
        "destination_owner_endpoint_missing",
    } <= set(result["blockers"])
    assert result["destination_status"] == "destination_unqualified"


def test_missing_source_endpoints_are_blockers_but_do_not_erase_explicit_flow() -> None:
    row = _successful_row()
    row["endpoints"]["source_owner_program"] = None
    row["endpoints"]["source_token_post_atomic"] = None
    result = compile_envelope(_envelope([row]))["candidates"][0]
    assert result["source_account_flow"] == "source_account_flow_exact"
    assert "source_owner_endpoint_missing" in result["blockers"]
    assert "source_token_endpoints_missing" in result["blockers"]


def test_candidate_transfer_amount_must_match_explicit_decoded_effect() -> None:
    row = _successful_row()
    row["target_transfer_amount_atomic"] = 999
    result = compile_envelope(_envelope([row]))["candidates"][0]
    assert result["source_account_flow"] == "evidence_insufficient"
    assert "target_transfer_amount_mismatch" in result["blockers"]


def test_failed_unsupported_layout_and_token2022_remain_distinct_attempts() -> None:
    failed = _successful_row("failed-1")
    failed["input_status"] = "failed"
    failed["effects"] = []
    unsupported_layout = _successful_row("layout-1")
    unsupported_layout["input_status"] = "unsupported"
    unsupported_layout["layout_status"] = "unsupported_layout"
    unsupported_layout["effects"] = []
    token2022 = _successful_row("token-2022-1")
    transfer = next(e for e in token2022["effects"] if e["kind"] == "transfer_checked")
    transfer["token_program"] = TOKEN_2022_PROGRAM
    unsupported_instruction = _successful_row("unsupported-instruction-1")
    unsupported_instruction["effects"].append(
        _effect(
            "unsupported_instruction",
            6,
            6,
            accounts=["other-account"],
            program_id="some-program",
            instruction_name="opaque_variant",
        )
    )
    result = compile_envelope(
        _envelope([failed, unsupported_layout, token2022, unsupported_instruction])
    )
    dispositions = {row["candidate_id"]: row["source_account_flow"] for row in result["candidates"]}
    assert dispositions == {
        "failed-1": "failed_attempt",
        "layout-1": "unsupported_layout",
        "token-2022-1": "unsupported_token_2022",
        "unsupported-instruction-1": "unsupported_instruction",
    }
    assert result["selection_denominators"]["status_counts"] == {
        "successful": 2,
        "failed": 1,
        "unsupported": 1,
        "unknown": 0,
    }


def test_candidate_fork_or_capture_identity_mismatch_fails_closed() -> None:
    envelope = _envelope()
    envelope["selection_rows"][0]["blockhash"] = "sibling-fork"
    envelope["verification"]["envelope_sha256"] = sha256_bytes(
        canonical_json({k: v for k, v in envelope.items() if k != "verification"})
    )
    with pytest.raises(SufficiencyError, match="candidate_fork_identity_mismatch"):
        compile_envelope(envelope)


def test_denominator_mismatch_and_duplicate_candidate_are_rejected() -> None:
    mismatch = _envelope()
    mismatch["denominators"]["status_counts"]["failed"] = 1
    mismatch["verification"]["envelope_sha256"] = sha256_bytes(
        canonical_json({k: v for k, v in mismatch.items() if k != "verification"})
    )
    with pytest.raises(SufficiencyError, match="candidate_status_denominator_mismatch"):
        compile_envelope(mismatch)

    duplicate = _successful_row()
    with pytest.raises(SufficiencyError, match="duplicate_candidate_id"):
        compile_envelope(_envelope([duplicate, copy.deepcopy(duplicate)]))


def test_malformed_times_hashes_and_duplicate_effect_paths_are_rejected() -> None:
    bad_time = _envelope()
    bad_time["captures"][0]["availability_ns"] = 1_999
    bad_time["verification"]["envelope_sha256"] = sha256_bytes(
        canonical_json({k: v for k, v in bad_time.items() if k != "verification"})
    )
    with pytest.raises(SufficiencyError, match="capture_time_order_invalid"):
        compile_envelope(bad_time)

    zero_time = _envelope()
    zero_time["captures"][0]["event_time_ns"] = 0
    _refresh_hash(zero_time)
    with pytest.raises(SufficiencyError, match="capture_event_time_invalid"):
        compile_envelope(zero_time)

    bad_hash = _envelope()
    bad_hash["lineage"]["plan_sha256"] = "not-a-hash"
    bad_hash["verification"]["envelope_sha256"] = sha256_bytes(
        canonical_json({k: v for k, v in bad_hash.items() if k != "verification"})
    )
    with pytest.raises(SufficiencyError, match="plan_sha256_invalid"):
        compile_envelope(bad_hash)

    duplicate_path = _envelope()
    duplicate_path["selection_rows"][0]["effects"][1]["path"] = copy.deepcopy(
        duplicate_path["selection_rows"][0]["effects"][0]["path"]
    )
    duplicate_path["verification"]["envelope_sha256"] = sha256_bytes(
        canonical_json({k: v for k, v in duplicate_path.items() if k != "verification"})
    )
    with pytest.raises(SufficiencyError, match="instruction_path_duplicate"):
        compile_envelope(duplicate_path)

    path_order = _envelope()
    effects = path_order["selection_rows"][0]["effects"]
    effects[3]["path"], effects[4]["path"] = effects[4]["path"], effects[3]["path"]
    _refresh_hash(path_order)
    with pytest.raises(SufficiencyError, match="effect_path_order_mismatch"):
        compile_envelope(path_order)


@given(
    event=st.integers(min_value=1, max_value=10**18),
    receipt_delta=st.integers(min_value=0, max_value=10**12),
    availability_delta=st.integers(min_value=0, max_value=10**12),
)
@settings(max_examples=40, deadline=None)
def test_ordered_capture_times_are_preserved(
    event: int, receipt_delta: int, availability_delta: int
) -> None:
    envelope = _envelope()
    capture = envelope["captures"][0]
    capture["event_time_ns"] = event
    capture["receipt_ns"] = event + receipt_delta
    capture["availability_ns"] = event + receipt_delta + availability_delta
    _refresh_hash(envelope)
    output = compile_envelope(envelope)
    observed = output["candidates"][0]
    assert observed["event_time_ns"] == event
    assert observed["receipt_ns"] == event + receipt_delta
    assert observed["availability_ns"] == event + receipt_delta + availability_delta


@given(order=st.permutations((0, 1, 2)))
@settings(max_examples=6, deadline=None)
def test_selection_row_order_does_not_change_denominators_or_id_dispositions(
    order: tuple[int, ...],
) -> None:
    rows = [_successful_row("success"), _successful_row("failed"), _successful_row("unsupported")]
    rows[1]["input_status"] = "failed"
    rows[1]["effects"] = []
    rows[2]["input_status"] = "unsupported"
    rows[2]["layout_status"] = "unsupported_layout"
    rows[2]["effects"] = []
    baseline = compile_envelope(_envelope(rows))
    permuted = compile_envelope(_envelope([rows[index] for index in order]))
    assert baseline["selection_denominators"] == permuted["selection_denominators"]
    base_map = {row["candidate_id"]: row["source_account_flow"] for row in baseline["candidates"]}
    permuted_map = {
        row["candidate_id"]: row["source_account_flow"] for row in permuted["candidates"]
    }
    assert base_map == permuted_map
