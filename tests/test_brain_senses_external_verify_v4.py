from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from tools import brain_senses_external_rolling_policy_v1 as policy
from tools import brain_senses_external_schema_v4 as schema
from tools import brain_senses_external_verify_v4 as verify

EPOCH_A = "20261012T110000Z-0123456789ab"
EPOCH_B = "20261013T110000Z-abcdef012345"
TAIL_A = "a" * 64
TAIL_B = "b" * 64
TERM_A = b"complete-a\n"
TERM_B = b"complete-b\n"


def _plan(epoch_id: str, anchor_ns: int) -> bytes:
    return schema.canonical({"epoch_id": epoch_id, "anchor_ns": anchor_ns})


def _chain(rows: list[tuple[str, int, dict[str, Any]]]) -> bytes:
    records = []
    previous = None
    for seq, (kind, at_ns, fields) in enumerate(rows):
        record = schema.event_record(
            schema=schema.CONTROL_SCHEMA,
            seq=seq,
            kind=kind,
            at_ns=at_ns,
            previous_sha256=previous,
            fields=fields,
        )
        records.append(record)
        previous = record["event_sha256"]
    return b"".join(schema.canonical(row) for row in records)


def _validator(payload: bytes, epoch_id: str) -> Mapping[str, Any]:
    value = schema.decode(payload)
    assert isinstance(value, dict)
    assert value["epoch_id"] == epoch_id
    return value


def _open(epoch_id: str, anchor_ns: int, at_ns: int) -> dict[str, Any]:
    payload = _plan(epoch_id, anchor_ns)
    return {
        "kind": "epoch_open",
        "at_ns": at_ns,
        "fields": {
            "epoch_id": epoch_id,
            "plan_sha256": schema.sha(payload),
            "anchor_ns": anchor_ns,
        },
    }


def _close(epoch_id: str, terminal: bytes, tail: str, at_ns: int) -> dict[str, Any]:
    return {
        "kind": "epoch_close",
        "at_ns": at_ns,
        "fields": {
            "epoch_id": epoch_id,
            "terminal_sha256": schema.sha(terminal),
            "event_tail_sha256": tail,
        },
    }


def _term(terminal: bytes, tail: str, status: str = "complete") -> verify.VerifiedTerminal:
    return verify.VerifiedTerminal(schema.sha(terminal), tail, status)  # type: ignore[arg-type]


def test_normal_terminal_allows_a_distinct_forward_epoch_without_30d_expiry() -> None:
    control = _chain(
        [
            tuple(_open(EPOCH_A, 100, 10).values()),
            tuple(_close(EPOCH_A, TERM_A, TAIL_A, 200).values()),
            tuple(_open(EPOCH_B, 300, 250).values()),
        ]
    )
    state = verify.verify_control_chain(
        control,
        plans={EPOCH_A: _plan(EPOCH_A, 100), EPOCH_B: _plan(EPOCH_B, 300)},
        terminals={EPOCH_A: _term(TERM_A, TAIL_A)},
        budget_evidence={},
        plan_validator=_validator,
    )
    assert state.active_epoch_id == EPOCH_B
    assert state.budget_paused is False
    assert state.epoch_ids == (EPOCH_A, EPOCH_B)


def test_open_epoch_requires_independently_verified_exact_terminal() -> None:
    control = _chain(
        [
            tuple(_open(EPOCH_A, 100, 10).values()),
            tuple(_close(EPOCH_A, TERM_A, TAIL_A, 200).values()),
        ]
    )
    with pytest.raises(verify.VerifyError, match="epoch_terminal_binding"):
        verify.verify_control_chain(
            control,
            plans={EPOCH_A: _plan(EPOCH_A, 100)},
            terminals={},
            budget_evidence={},
            plan_validator=_validator,
        )


def test_terminal_receipt_published_before_control_close_is_recoverable_without_io() -> None:
    control = _chain([tuple(_open(EPOCH_A, 100, 10).values())])
    state = verify.verify_control_chain(
        control,
        plans={EPOCH_A: _plan(EPOCH_A, 100)},
        terminals={EPOCH_A: _term(TERM_A, TAIL_A)},
        budget_evidence={},
        plan_validator=_validator,
    )
    assert state.active_epoch_id == EPOCH_A
    assert state.pending_terminal_epoch_id == EPOCH_A


def test_duplicate_epoch_open_is_rejected_even_if_plan_hash_matches() -> None:
    opened = _open(EPOCH_A, 100, 10)
    control = _chain([tuple(opened.values()), tuple(_open(EPOCH_A, 200, 20).values())])
    with pytest.raises(verify.VerifyError, match="epoch_open_transition"):
        verify.verify_control_chain(
            control,
            plans={EPOCH_A: _plan(EPOCH_A, 100)},
            terminals={},
            budget_evidence={},
            plan_validator=_validator,
        )


def test_abandoned_epoch_requires_cooldown_before_next_fresh_anchor() -> None:
    cooldown = 200 + 60 * 60 * 1_000_000_000
    abandon = {
        "kind": "epoch_abandon",
        "at_ns": 200,
        "fields": {
            "epoch_id": EPOCH_A,
            "terminal_sha256": schema.sha(TERM_A),
            "event_tail_sha256": TAIL_A,
            "cooldown_until_ns": cooldown,
        },
    }
    terminal = verify.VerifiedTerminal(schema.sha(TERM_A), TAIL_A, "abandoned")
    too_early = _open(EPOCH_B, cooldown - 1, cooldown - 100)
    control = _chain(
        [
            tuple(_open(EPOCH_A, 100, 10).values()),
            tuple(abandon.values()),
            tuple(too_early.values()),
        ]
    )
    with pytest.raises(verify.VerifyError, match="epoch_open_transition"):
        verify.verify_control_chain(
            control,
            plans={EPOCH_A: _plan(EPOCH_A, 100), EPOCH_B: _plan(EPOCH_B, cooldown - 1)},
            terminals={EPOCH_A: terminal},
            budget_evidence={},
            plan_validator=_validator,
        )


def test_budget_pause_requires_reason_bound_resume() -> None:
    control = _chain(
        [
            ("budget_pause", 10, {"reason": "rolling_credit_cap", "evidence_sha256": "d" * 64}),
            (
                "budget_resume",
                20,
                {"reason": "rolling_credit_cap", "evidence_sha256": "c" * 64},
            ),
            tuple(_open(EPOCH_A, 100, 30).values()),
        ]
    )
    state = verify.verify_control_chain(
        control,
        plans={EPOCH_A: _plan(EPOCH_A, 100)},
        terminals={},
        budget_evidence={
            "d" * 64: "exceeded:rolling_credit_cap",
            "c" * 64: "headroom:rolling_credit_cap",
        },
        plan_validator=_validator,
    )
    assert state.active_epoch_id == EPOCH_A
    assert not state.budget_paused

    mismatched = _chain(
        [
            ("budget_pause", 10, {"reason": "rolling_credit_cap", "evidence_sha256": "d" * 64}),
            (
                "budget_resume",
                20,
                {"reason": "storage_floor", "evidence_sha256": "c" * 64},
            ),
        ]
    )
    with pytest.raises(verify.VerifyError, match="budget_resume_transition"):
        verify.verify_control_chain(
            mismatched,
            plans={},
            terminals={},
            budget_evidence={"d" * 64: "exceeded:rolling_credit_cap"},
            plan_validator=_validator,
        )


def test_plan_rederives_schedule_policy_sources_and_storage_identity() -> None:
    created = 1_700_000_000_000_000_000
    anchor = (
        (created + 2 * policy.MINUTE_NS + policy.HOUR_NS - 1) // policy.HOUR_NS
    ) * policy.HOUR_NS
    epoch_id = schema.epoch_id(anchor, "0123456789ab")
    sources = {"tools/runner.py": "a" * 64, "tools/verifier.py": "b" * 64}
    plan = schema.build_plan(
        epoch=epoch_id,
        created_ns=created,
        anchor_ns=anchor,
        root_id="0" * 32,
        volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
        source_bindings=sources,
    )
    raw = schema.canonical(plan)
    checked = verify.verify_plan(
        raw,
        expected_sources=sources,
        expected_root_id="0" * 32,
        expected_volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
    )
    assert checked["windows"][0]["kind"] == "anchor"
    assert len(checked["windows"]) == 25
    assert "predecessor" not in checked

    changed_window = dict(plan)
    changed_window["windows"] = list(plan["windows"])
    changed_window["windows"][1] = {**changed_window["windows"][1], "start_ns": anchor}
    with pytest.raises(verify.VerifyError, match="plan_schedule_invalid"):
        verify.verify_plan(
            schema.canonical(changed_window),
            expected_sources=sources,
            expected_root_id="0" * 32,
            expected_volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
        )

    with pytest.raises(verify.VerifyError, match="plan_identity_invalid"):
        verify.verify_plan(
            raw,
            expected_sources={"tools/runner.py": "a" * 64},
            expected_root_id="0" * 32,
            expected_volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
        )


def _epoch_plan() -> dict[str, Any]:
    created = 1_700_000_000_000_000_000
    anchor = (
        (created + 2 * policy.MINUTE_NS + policy.HOUR_NS - 1) // policy.HOUR_NS
    ) * policy.HOUR_NS
    return schema.build_plan(
        epoch=schema.epoch_id(anchor, "0123456789ab"),
        created_ns=created,
        anchor_ns=anchor,
        root_id="0" * 32,
        volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
        source_bindings={"tools/runner.py": "a" * 64},
    )


def _epoch_events(plan: Mapping[str, Any], rows: list[tuple[str, int, dict[str, Any]]]) -> bytes:
    records = []
    previous = None
    all_rows = [
        (
            "epoch_started",
            plan["created_ns"],
            {
                "epoch_id": plan["epoch_id"],
                "plan_sha256": schema.sha(schema.canonical(dict(plan))),
                "anchor_ns": plan["anchor_ns"],
            },
        ),
        *rows,
    ]
    for seq, (kind, at_ns, fields) in enumerate(all_rows):
        record = schema.event_record(
            schema=schema.EVENT_SCHEMA,
            seq=seq,
            kind=kind,
            at_ns=at_ns,
            previous_sha256=previous,
            fields=fields,
        )
        records.append(record)
        previous = record["event_sha256"]
    return b"".join(schema.canonical(row) for row in records)


def _request_rows(
    plan: Mapping[str, Any], index: int, mode: str, method: str = "getSlot"
) -> list[tuple[str, int, dict[str, Any]]]:
    window = plan["windows"][index]
    start = window["start_ns"]
    bucket = window["bucket"]
    params = [{"commitment": "finalized"}]
    request_id = f"{bucket}-00"
    wire = schema.canonical({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).rstrip(
        b"\n"
    )
    return [
        (
            "window_reserved",
            start + 1_000_000_000,
            {
                "index": index,
                "bucket": bucket,
                "kind": window["kind"],
                "mode": mode,
                "request_cap": window["request_cap"],
            },
        ),
        (
            "request_intent",
            start + 2_000_000_000,
            {
                "request_id": request_id,
                "bucket": bucket,
                "ordinal": 0,
                "method": method,
                "params": params,
                "wire_request_sha256": schema.sha(wire),
                "decision_ns": start + 2_000_000_000,
            },
        ),
        (
            "request_captured",
            start + 4_000_000_000,
            {
                "request_id": request_id,
                "response_sha256": "e" * 64,
                "response_bytes": 32,
                "availability_ns": start + 4_000_000_001,
            },
        ),
        (
            "frame_sealed",
            start + 5_000_000_000,
            {"index": index, "bucket": bucket, "request_ids": [request_id], "request_count": 1},
        ),
    ]


def test_missed_window_requires_a_fresh_anchor_reset_before_inventory() -> None:
    plan = _epoch_plan()
    missed_window = plan["windows"][1]
    rows = _request_rows(plan, 0, "anchor") + [
        (
            "window_missed",
            missed_window["end_ns"],
            {
                "index": 1,
                "bucket": missed_window["bucket"],
                "kind": missed_window["kind"],
                "start_ns": missed_window["start_ns"],
                "end_ns": missed_window["end_ns"],
            },
        )
    ]
    rows += _request_rows(plan, 2, "anchor_reset")
    projection = verify.verify_epoch_events(plan, _epoch_events(plan, rows))
    assert projection.next_window_index == 3
    assert projection.missed_buckets == (missed_window["bucket"],)
    assert projection.reset_anchor_required is False
    assert len(projection.sealed_buckets) == 2


def test_unresolved_intent_is_possibly_spent_and_cannot_be_followed_by_retry() -> None:
    plan = _epoch_plan()
    row = plan["windows"][0]
    params = [{"commitment": "finalized"}]
    wire = schema.canonical(
        {"jsonrpc": "2.0", "id": 1, "method": "getSlot", "params": params}
    ).rstrip(b"\n")
    first = (
        "window_reserved",
        row["start_ns"] + 1,
        {
            "index": 0,
            "bucket": row["bucket"],
            "kind": row["kind"],
            "mode": "anchor",
            "request_cap": 1,
        },
    )
    intent = (
        "request_intent",
        row["start_ns"] + 2,
        {
            "request_id": f"{row['bucket']}-00",
            "bucket": row["bucket"],
            "ordinal": 0,
            "method": "getSlot",
            "params": params,
            "wire_request_sha256": schema.sha(wire),
            "decision_ns": row["start_ns"] + 2,
        },
    )
    with pytest.raises(verify.VerifyError, match="request_intent_binding"):
        verify.verify_epoch_events(plan, _epoch_events(plan, [first, intent, intent]))
    projection = verify.verify_epoch_events(plan, _epoch_events(plan, [first, intent]))
    assert projection.request_states == ((f"{row['bucket']}-00", "possibly_spent"),)
