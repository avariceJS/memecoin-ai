from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any

import pytest

from tools import brain_senses_external_cutover_usage_v5 as cutover
from tools import brain_senses_external_orchestrator_v4 as v4_runtime
from tools import brain_senses_external_rolling_policy_v5 as policy
from tools import brain_senses_external_schema_v4 as v4_schema
from tools import brain_senses_external_schema_v5 as schema
from tools.brain_senses_external_writer_v1 import DescriptorWriter

V4_SOURCES = {"tools/external_v4.py": "a" * 64}
START = 1_791_763_200_000_000_000
END = START + 10_000_000_000
GENERATED = END + 1


def _campaign(count: int, start: int, end: int, response: int, physical: int) -> dict[str, Any]:
    return {
        "request_count": count,
        "dispatch_start_ns": start,
        "dispatch_end_ns": end,
        "response_bytes_upper": response,
        "physical_bytes_upper": physical,
        "evidence_sha256": "b" * 64,
        "evidence_path": "docs/evidence/synthetic.json",
        "attribution": "possible_same_account",
    }


def _row(
    request_id: str, dispatch: int, outcome: str, response: int, physical: int
) -> dict[str, Any]:
    return {
        "campaign_id": "rolling_v4",
        "request_id": request_id,
        "intent_sha256": "c" * 64,
        "outcome_sha256": "d" * 64,
        "decision_ns": dispatch - 1,
        "dispatch_ns": None if outcome == "possibly_spent" else dispatch,
        "latest_possible_dispatch_ns": dispatch,
        "operation": "getSlot",
        "outcome": outcome,
        "request_debit": 0 if outcome == "pre_intent_released" else 1,
        "modeled_credit_debit": 0 if outcome == "pre_intent_released" else 10,
        "response_bytes_debit": response,
        "physical_bytes_debit": physical,
        "source_event_sha256": "e" * 64,
    }


def _receipt(*, c4_exact: bool = True) -> dict[str, Any]:
    rows = [
        _row("c4-request-1", START + 1, "captured", 50_000, 70_000),
        _row(
            "c4-request-2",
            START + 2,
            "possibly_spent",
            policy.MAX_RESPONSE_BYTES,
            policy.MAX_RESPONSE_BYTES + policy.PER_REQUEST_METADATA_RESERVE_BYTES,
        ),
    ]
    rolling_count = 2 if c4_exact else v4_runtime.policy.MAX_EPOCH_REQUESTS
    rolling_response = (
        sum(row["response_bytes_debit"] for row in rows)
        if c4_exact
        else v4_runtime.policy.MAX_EPOCH_RESPONSE_BYTES
    )
    rolling_physical = (
        sum(row["physical_bytes_debit"] for row in rows)
        if c4_exact
        else v4_runtime.policy.MAX_EPOCH_PHYSICAL_BYTES
    )
    return {
        "schema": cutover.RECEIPT_SCHEMA,
        "schema_version": cutover.RECEIPT_VERSION,
        "generated_ns": GENERATED,
        "service": "helius",
        "source_v4": {
            "service_root": "services/external-rolling-v4",
            "plan_sha256": "1" * 64,
            "terminal_sha256": "2" * 64,
            "event_tail_sha256": "3" * 64,
            "allocation_manifest_sha256": "4" * 64,
            "source_bindings": V4_SOURCES,
            "source_bindings_sha256": schema.sha(schema.canonical(V4_SOURCES)),
            "terminal_status": "complete",
            "verified_interval_start_ns": START,
            "verified_interval_end_ns": END,
        },
        "legacy_campaigns": {
            "c1": _legacy_test_campaign("c1"),
            "c2": _legacy_test_campaign("c2"),
            "c3": _legacy_test_campaign("c3"),
            "c4": deepcopy(cutover.HISTORICAL_C4),
        },
        "rolling_v4_request_rows": rows if c4_exact else [],
        "rolling_v4_accounting": {
            "exact": c4_exact,
            "request_count": rolling_count,
            "dispatch_start_ns": START + 1 if c4_exact else START,
            "dispatch_end_ns": START + 2,
            "response_bytes_upper": rolling_response,
            "physical_bytes_upper": rolling_physical,
        },
        "accounting": {
            "historical_c4_exact_count": True,
            "historical_c4_debit_requests": 375,
            "rolling_v4_exact": c4_exact,
            "rolling_v4_debit_requests": rolling_count,
            "legacy_possible_same_account": True,
            "provider_charge_units": None,
            "provider_charge_status": "unknown_unverified",
        },
    }


def _encode(value: dict[str, Any]) -> bytes:
    return schema.canonical(value)


class _AnchorClient:
    def __init__(self, request_started_ns: int) -> None:
        self.request_started_ns = request_started_ns

    def fetch(self, method: str, params: list[object]) -> Any:
        from memecoin_ai.sources import Capture

        assert method == "getSlot"
        response = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": 100},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        start = self.request_started_ns
        request = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            sort_keys=True,
            separators=(",", ":"),
        )
        return Capture("helius-free", method, request, start, start + 1_000_000, response)


class _NoResponseBodyReader:
    """Proxy that fails if the cutover builder attempts a response-body read."""

    def __init__(self, writer: DescriptorWriter) -> None:
        self.writer = writer

    def read_bytes(self, relative: str, *, max_bytes: int = 32_000_000) -> bytes:
        if relative.endswith("/response.json"):
            raise AssertionError("cutover builder attempted a raw response read")
        return self.writer.read_bytes(relative, max_bytes=max_bytes)

    def list_names(self, relative: str) -> list[str]:
        return self.writer.list_names(relative)

    def stat(self, relative: str) -> os.stat_result:
        return self.writer.stat(relative)


def _prior_campaigns() -> dict[str, dict[str, Any]]:
    return {name: _legacy_test_campaign(name) for name in ("c1", "c2", "c3")}


def _legacy_test_campaign(name: str) -> dict[str, Any]:
    pin = cutover.LEGACY_SOURCE_BINDINGS[name]
    count = cutover.LEGACY_COUNTS[name]
    cap = 0 if count == 0 else 4_000_000_000
    return {
        "request_count": count,
        "dispatch_start_ns": cutover.LEGACY_CAMPAIGN_START_NS,
        "dispatch_end_ns": cutover.LEGACY_CAMPAIGN_END_NS,
        "response_bytes_upper": cap,
        "physical_bytes_upper": cap,
        "evidence_sha256": pin["evidence_sha256"],
        "evidence_path": pin["evidence_path"],
        "attribution": "possible_same_account",
    }


def _seed_terminal_v4(
    tmp_path: Any, *, with_capture: bool, terminal: bool = True
) -> DescriptorWriter:
    root = tmp_path / "v4-root"
    root.mkdir(mode=0o700)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(fd)
    os.close(fd)
    service = v4_schema.SERVICE_RELATIVE_ROOT
    writer.mkdir(service)
    writer.mkdir(f"{service}/epochs")
    available = 200 * 2**30
    writer.create(
        f"{service}/service-allocation-cap.json",
        v4_schema.canonical(
            {
                "schema": "brain-senses-external-rolling-v4-allocation-cap",
                "schema_version": 4,
                "created_ns": START,
                "root_id": "a" * 32,
                "volume_uuid": "8f8f1081-9be4-4914-9111-75f1b90255bf",
                "available_at_activation_bytes": available,
                "service_allocation_cap_bytes": v4_runtime.policy.activation_allocation_cap(
                    available
                ),
            }
        ),
    )
    epoch_id = v4_runtime._open_epoch(
        writer,
        root_id="a" * 32,
        volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
        now_ns=START,
    )
    if with_capture:
        plan = v4_schema.decode(
            writer.read_bytes(f"{v4_runtime._epoch_rel(epoch_id)}/{v4_schema.PLAN_FILE}")
        )
        window = plan["windows"][0]
        clock = iter(
            range(window["start_ns"] + 10_000_000, window["start_ns"] + 200_000_000, 10_000_000)
        )
        v4_runtime._current_frame(
            writer,
            epoch_id=epoch_id,
            plan=plan,
            window_index=0,
            mode="anchor",
            now_ns=lambda: next(clock),
            client=_AnchorClient(window["start_ns"] + 25_000_000),
            guard_check=lambda: None,
            sleep=lambda _seconds: None,
        )
        closed_ns = window["start_ns"] + 200_000_000
    else:
        plan_bytes = writer.read_bytes(f"{v4_runtime._epoch_rel(epoch_id)}/{v4_schema.PLAN_FILE}")
        plan = v4_schema.decode(plan_bytes)
        window = plan["windows"][0]
        bucket = window["bucket"]
        v4_runtime._window_event(
            writer,
            epoch_id,
            kind="window_reserved",
            at_ns=window["start_ns"] + 1,
            fields={
                "index": 0,
                "bucket": bucket,
                "kind": "anchor",
                "mode": "anchor",
                "request_cap": 1,
            },
        )
        v4_runtime._window_event(
            writer,
            epoch_id,
            kind="request_intent",
            at_ns=window["start_ns"] + 2,
            fields={
                "request_id": f"{bucket}-00",
                "bucket": bucket,
                "ordinal": 0,
                "method": "getSlot",
                "params": [{"commitment": "finalized"}],
                "wire_request_sha256": v4_runtime.wire_request_sha256(
                    "getSlot", [{"commitment": "finalized"}]
                ),
                "decision_ns": window["start_ns"] + 2,
            },
        )
        closed_ns = window["start_ns"] + 3
    if terminal:
        v4_runtime._finish_epoch(
            writer,
            epoch_id=epoch_id,
            plan_bytes=writer.read_bytes(
                f"{v4_runtime._epoch_rel(epoch_id)}/{v4_schema.PLAN_FILE}"
            ),
            status="abandoned",
            reason="synthetic_cutover_fixture",
            pause_reason=None,
            now_ns=closed_ns,
        )
    return writer


def test_exact_cutover_receipt_replays_conservative_usage() -> None:
    raw = _encode(_receipt())
    verified = cutover.verify_receipt(
        raw,
        expected_sha256=schema.sha(raw),
        expected_v4_sources=V4_SOURCES,
    )
    usage = verified.usage(now_ns=GENERATED + 10)
    assert verified.legacy_possible_same_account is True
    assert verified.rolling_v4_exact is True
    assert _receipt()["legacy_campaigns"]["c4"]["request_count"] == 375
    assert len(_receipt()["rolling_v4_request_rows"]) == 2
    assert usage.requests == 583
    assert usage.modeled_credits == 5_830
    assert usage.response_bytes == (12_000_000_000 + 50_000 + policy.MAX_RESPONSE_BYTES)
    assert (
        usage.allocated_bytes
        == 12_000_000_000
        + 70_000
        + policy.MAX_RESPONSE_BYTES
        + policy.PER_REQUEST_METADATA_RESERVE_BYTES
    )


def test_unknown_rolling_v4_charges_full_epoch_envelope_until_ages_out() -> None:
    value = _receipt(c4_exact=False)
    value["rolling_v4_accounting"]["dispatch_end_ns"] = END
    raw = _encode(value)
    verified = cutover.verify_receipt(
        raw,
        expected_sha256=schema.sha(raw),
        expected_v4_sources=V4_SOURCES,
    )
    usage = verified.usage(now_ns=GENERATED + 1)
    assert verified.rolling_v4_exact is False
    assert usage.requests == 1 + 205 + 375 + v4_runtime.policy.MAX_EPOCH_REQUESTS
    assert usage.response_bytes == (12_000_000_000 + v4_runtime.policy.MAX_EPOCH_RESPONSE_BYTES)
    assert usage.allocated_bytes == (12_000_000_000 + v4_runtime.policy.MAX_EPOCH_PHYSICAL_BYTES)
    old = verified.usage(now_ns=END + policy.ROLLING_WINDOW_NS + 2)
    assert old.requests == 0


def test_cutover_receipt_digest_source_and_duplicate_rows_fail_closed() -> None:
    value = _receipt()
    raw = _encode(value)
    with pytest.raises(cutover.CutoverUsageError, match="receipt_digest_mismatch"):
        cutover.verify_receipt(raw, expected_sha256="f" * 64, expected_v4_sources=V4_SOURCES)
    with pytest.raises(cutover.CutoverUsageError, match="source_binding_mismatch"):
        cutover.verify_receipt(
            raw, expected_sha256=schema.sha(raw), expected_v4_sources={"other.py": "a" * 64}
        )
    duplicate = deepcopy(value)
    duplicate["rolling_v4_request_rows"][1]["request_id"] = duplicate["rolling_v4_request_rows"][0][
        "request_id"
    ]
    raw_duplicate = _encode(duplicate)
    with pytest.raises(cutover.CutoverUsageError, match="request_id_duplicate_or_invalid"):
        cutover.verify_receipt(
            raw_duplicate,
            expected_sha256=schema.sha(raw_duplicate),
            expected_v4_sources=V4_SOURCES,
        )


def test_ambiguous_and_campaign_upper_bounds_cannot_be_understated() -> None:
    ambiguous = _receipt()
    ambiguous["rolling_v4_request_rows"][1]["response_bytes_debit"] = 1
    raw = _encode(ambiguous)
    with pytest.raises(cutover.CutoverUsageError, match="ambiguous_request_reserve_understated"):
        cutover.verify_receipt(raw, expected_sha256=schema.sha(raw), expected_v4_sources=V4_SOURCES)


def test_legacy_campaign_sources_and_historical_timeline_are_pinned() -> None:
    value = _receipt()
    value["legacy_campaigns"]["c4"]["dispatch_start_ns"] += 60_000_000_000
    raw = _encode(value)
    with pytest.raises(cutover.CutoverUsageError, match="historical_c4_binding_invalid"):
        cutover.verify_receipt(raw, expected_sha256=schema.sha(raw), expected_v4_sources=V4_SOURCES)
    value = _receipt()
    value["legacy_campaigns"]["c3"]["evidence_sha256"] = "f" * 64
    raw = _encode(value)
    with pytest.raises(cutover.CutoverUsageError, match="legacy_campaign_source_pin"):
        cutover.verify_receipt(raw, expected_sha256=schema.sha(raw), expected_v4_sources=V4_SOURCES)


@pytest.mark.parametrize("with_capture", [False, True])
def test_metadata_builder_reopens_terminal_v4_without_response_body_reads(
    tmp_path: Any, with_capture: bool
) -> None:
    writer = _seed_terminal_v4(tmp_path, with_capture=with_capture)
    try:
        generated = START + 4 * policy.HOUR_NS
        raw = cutover.build_from_v4_metadata(
            _NoResponseBodyReader(writer),
            root_id="a" * 32,
            volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
            generated_ns=generated,
            prior_campaigns=_prior_campaigns(),
        )
        value = schema.decode(raw)
        verified = cutover.verify_receipt(
            raw,
            expected_sha256=schema.sha(raw),
            expected_v4_sources=v4_runtime.source_bindings(),
        )
        assert value["source_v4"]["terminal_status"] == "abandoned"
        assert value["legacy_campaigns"]["c4"]["request_count"] == 375
        assert verified.rolling_v4_exact is True
        assert verified.rolling_v4_debit_requests == 1
        if with_capture:
            row = value["rolling_v4_request_rows"][0]
            assert row["outcome"] == "captured"
            assert row["response_bytes_debit"] < policy.MAX_RESPONSE_BYTES
            assert row["dispatch_ns"] >= row["decision_ns"]
            assert row["latest_possible_dispatch_ns"] == row["dispatch_ns"]
        else:
            row = value["rolling_v4_request_rows"][0]
            assert row["outcome"] == "possibly_spent"
            assert row["response_bytes_debit"] == policy.MAX_RESPONSE_BYTES
            assert row["dispatch_ns"] is None
            assert row["decision_ns"] <= row["latest_possible_dispatch_ns"]
    finally:
        writer.close()


def test_cutover_publication_is_create_only_and_idempotent(tmp_path: Any) -> None:
    source = _seed_terminal_v4(tmp_path, with_capture=False)
    output_root = tmp_path / "v5-root"
    output_root.mkdir(mode=0o700)
    fd = os.open(output_root, os.O_RDONLY | os.O_DIRECTORY)
    output = DescriptorWriter(fd)
    os.close(fd)
    try:
        output.mkdir("services/external-rolling-v5")
        raw = cutover.build_from_v4_metadata(
            _NoResponseBodyReader(source),
            root_id="a" * 32,
            volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
            generated_ns=START + 4 * policy.HOUR_NS,
            prior_campaigns=_prior_campaigns(),
        )
        assert cutover.publish_create_only(output, raw) == "created"
        assert cutover.publish_create_only(output, raw) == "existing_identical"
        path = cutover.RECEIPT_PATH
        assert output.read_bytes(path) == raw
        changed = schema.decode(raw)
        changed["generated_ns"] += 1
        with pytest.raises(cutover.CutoverUsageError, match="create_only_conflict"):
            cutover.publish_create_only(output, schema.canonical(changed))
        assert output.read_bytes(path) == raw
    finally:
        source.close()
        output.close()
    c4 = _receipt()
    c4["legacy_campaigns"]["c4"]["response_bytes_upper"] = 100
    raw = _encode(c4)
    with pytest.raises(cutover.CutoverUsageError, match="historical_c4_binding_invalid"):
        cutover.verify_receipt(raw, expected_sha256=schema.sha(raw), expected_v4_sources=V4_SOURCES)


def test_builder_rejects_unpinned_prior_campaign_metadata(tmp_path: Any) -> None:
    writer = _seed_terminal_v4(tmp_path, with_capture=False)
    prior = _prior_campaigns()
    prior["c2"]["evidence_sha256"] = "f" * 64
    try:
        with pytest.raises(cutover.CutoverUsageError, match="prior_campaign_source_pin"):
            cutover.build_from_v4_metadata(
                _NoResponseBodyReader(writer),
                root_id="a" * 32,
                volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
                generated_ns=START + 4 * policy.HOUR_NS,
                prior_campaigns=prior,
            )
    finally:
        writer.close()


def test_builder_refuses_active_pending_v4_epoch_before_any_output(tmp_path: Any) -> None:
    writer = _seed_terminal_v4(tmp_path, with_capture=False, terminal=False)
    try:
        with pytest.raises(cutover.CutoverUsageError, match="v4_epoch_not_terminal"):
            cutover.build_from_v4_metadata(
                _NoResponseBodyReader(writer),
                root_id="a" * 32,
                volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
                generated_ns=START + 4 * policy.HOUR_NS,
                prior_campaigns=_prior_campaigns(),
            )
        assert not writer.exists(cutover.RECEIPT_PATH)
    finally:
        writer.close()
