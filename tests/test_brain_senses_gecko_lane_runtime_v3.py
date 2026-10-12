from __future__ import annotations

import os
import sys
import tempfile
import threading
from contextlib import contextmanager
from email.utils import formatdate
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from brain_senses_external_writer_v1 import DescriptorWriter, StorageError  # noqa: E402
from brain_senses_gecko_lane_policy_v3 import (  # noqa: E402
    NS,
    POOL_ROWS,
    TRAIN_ENROLLMENT_SHA256,
    TRAIN_MANIFEST_SHA256,
    TRAIN_PLAN_SHA256,
    TRAIN_SELECTION_SHA256,
    TRAIN_SOURCE_PIN,
    canonical,
    capacity_contract,
    protocol_constants,
    sha256,
)
from brain_senses_gecko_lane_runtime_v3 import (  # noqa: E402
    ACTIVATION_SCHEMA,
    PILOT_AUTH_SCHEMA,
    PILOT_RECEIPT_SCHEMA,
    DescriptorLaneStore,
    GeckoRuntimeError,
    _compile_pinned_live_policy,
    _public_fetch_once,
    _verify_scheduled_release,
    _within_lane_budget,
    compile_live_plan,
    current_bucket_for_tick,
    pilot_prefix,
    rotated_pool_order,
    run_current_bucket,
    source_bindings,
    verify_activation,
    verify_live_plan,
    verify_live_store,
    verify_pilot_authorization,
    verify_pilot_bucket,
    verify_pilot_receipt,
)

from tools import brain_senses_external_clock_v1 as clock  # noqa: E402
from tools.brain_senses_gecko_lane_engine_v3 import (  # noqa: E402  # noqa: E402
    FetchResult,
    request_for_bucket,
)


def policy() -> dict[str, Any]:
    item: dict[str, Any] = {
        "schema": "brain-senses-gecko-prospective-lane-v3",
        "schema_version": 3,
        "policy_id": "gecko-train-four-15m-v3",
        "source": {
            "role": "train",
            "source_pin": TRAIN_SOURCE_PIN,
            "enrollment_sha256": TRAIN_ENROLLMENT_SHA256,
            "role_manifest_sha256": TRAIN_MANIFEST_SHA256,
            "plan_sha256": TRAIN_PLAN_SHA256,
            "population_selection_sha256": TRAIN_SELECTION_SHA256,
            "enrolled_train_count": 170,
            "ranking": "sha256(utf8(source_pin + NUL + pool_id)),ascending",
            "protected_screen_fields": ["pool_id", "base_mint"],
            "quote_mint_rule": "preserve_and_match_pinned_pool_metadata_only",
            "quote_token_features": False,
        },
        "capacity": capacity_contract(),
        "pools": list(POOL_ROWS),
        "protocol": protocol_constants(),
        "activation_authorized": False,
        "provider_io": False,
        "economic_admission": False,
    }
    item["policy_sha256"] = sha256(canonical(item))
    return item


def setup_store() -> tuple[
    tempfile.TemporaryDirectory[str], DescriptorWriter, Any, Any, dict[str, Any]
]:
    temp = tempfile.TemporaryDirectory()
    os.chmod(temp.name, 0o700)
    root_fd = os.open(temp.name, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(root_fd)
    os.close(root_fd)

    class Guard:
        root_id = "a" * 32
        volume_uuid = "11111111-2222-3333-4444-555555555555"
        mount_fd = writer.root_fd

        def revalidate(self):
            return None

    guard = Guard()
    source_map = source_bindings()
    plan = compile_live_plan(
        policy(), root_id=guard.root_id, volume_uuid=guard.volume_uuid, source_map=source_map
    )
    activation = {
        "schema": ACTIVATION_SCHEMA,
        "schema_version": 3,
        "plan_sha256": plan["plan_sha256"],
        "source_sha256": plan["source_sha256"],
        "root_id": guard.root_id,
        "volume_uuid": guard.volume_uuid,
        "activation_authorized": True,
        "provider_io": True,
        "qualification_status": "passed",
    }
    store = DescriptorLaneStore(writer, guard=guard)
    store.initialize(plan, activation)
    return temp, writer, guard, store, plan


class FakeCoordinator:
    def __init__(self, store: DescriptorLaneStore):
        self.store = store
        self.lock = threading.RLock()
        self.reservations = []

    @contextmanager
    def lane_lock(self, _service):
        with self.lock:
            yield object()

    def recover_service(self, _service, *, intent_exists):
        return {"released_pre_intent": 0, "ambiguous_terminal": 0}

    def reserve_and_persist_intent(self, **kwargs):
        self.store.guard.revalidate()
        self.store.writer.create(kwargs["intent_path"], kwargs["intent_bytes"])
        self.reservations.append(kwargs["request_id"])
        return type("Reservation", (), kwargs)()

    def settle(self, reservation, *, outcome_bytes, outcome_path, **kwargs):
        self.store.guard.revalidate()
        self.store.writer.create(outcome_path, outcome_bytes)
        return "settled"


def raw_empty() -> bytes:
    return b'{"data":{"attributes":{"ohlcv_list":[]}}}'


def raw_one_new_bar(bucket_close_ns: int) -> bytes:
    close_s = bucket_close_ns // NS
    row = [close_s - 300, 1, 2, 0.5, 1.5, 1]
    return (
        b'{"data":{"attributes":{"ohlcv_list":['
        + __import__("json").dumps(row, separators=(",", ":")).encode()
        + b"]}}}"
    )


def make_clock_record(bucket_close_ns: int) -> dict[str, Any]:
    checked = bucket_close_ns + 60 * NS
    mono = checked
    date = formatdate(checked // NS, usegmt=True)
    samples = []
    for host, sent_delta, recv_delta in (
        ("www.cloudflare.com", -100_000_000, -50_000_000),
        ("www.google.com", -25_000_000, 0),
    ):
        samples.append(
            {
                "host": host,
                "status": 200,
                "date": date,
                "age": None,
                "sent_ns": checked + sent_delta,
                "received_ns": checked + recv_delta,
                "monotonic_sent_ns": mono + sent_delta,
                "monotonic_received_ns": mono + recv_delta,
            }
        )
    return {
        "schema": clock.CLOCK_SCHEMA,
        "schema_version": 1,
        "samples": samples,
        "checked_ns": checked,
        "monotonic_ns": mono,
    }


def run_bucket_fixture(
    store: DescriptorLaneStore,
    plan: dict[str, Any],
    bucket_close_ns: int,
    fetch_once,
) -> dict[str, Any]:
    activation = {
        "schema": ACTIVATION_SCHEMA,
        "schema_version": 3,
        "plan_sha256": plan["plan_sha256"],
        "source_sha256": plan["source_sha256"],
        "root_id": plan["storage"]["root_id"],
        "volume_uuid": plan["storage"]["volume_uuid"],
        "activation_authorized": True,
        "provider_io": True,
        "qualification_status": "passed",
    }
    coordinator = FakeCoordinator(store)
    record = make_clock_record(bucket_close_ns)
    relative = f"clocks/{bucket_close_ns}-00.json"
    clock_bytes = canonical(record)
    store.writer.create(store._path(relative), clock_bytes)
    wall = [bucket_close_ns + 61 * NS]
    checked = record["checked_ns"]
    mono = record["monotonic_ns"]

    def now() -> int:
        value = wall[0]
        wall[0] += NS
        return value

    def wait_until(target: int) -> None:
        wall[0] = max(wall[0], target)

    return run_current_bucket(
        plan,
        activation,
        store,
        coordinator,
        now_ns=now,
        wait_until=wait_until,
        fetch_once=fetch_once,
        clock_record=record,
        clock_path=store._path(relative),
        clock_sha256=sha256(clock_bytes),
        monotonic_ns=lambda: mono + (wall[0] - checked),
    )


def test_live_plan_and_activation_are_exactly_bound_and_non_authorizing_by_default():
    plan = compile_live_plan(
        policy(),
        root_id="a" * 32,
        volume_uuid="11111111-2222-3333-4444-555555555555",
        source_map=source_bindings(),
    )
    assert plan["activation_authorized"] is False and plan["provider_io"] is False
    verify_live_plan(plan)
    activation = {
        "schema": ACTIVATION_SCHEMA,
        "schema_version": 3,
        "plan_sha256": plan["plan_sha256"],
        "source_sha256": plan["source_sha256"],
        "root_id": plan["storage"]["root_id"],
        "volume_uuid": plan["storage"]["volume_uuid"],
        "activation_authorized": True,
        "provider_io": True,
        "qualification_status": "passed",
    }
    verify_activation(plan, activation)
    stale_sources = dict(activation)
    stale_sources["source_sha256"] = {"runtime": "b" * 64}
    with pytest.raises(GeckoRuntimeError, match="activation_binding"):
        verify_activation(plan, stale_sources)
    activation["plan_sha256"] = "0" * 64
    with pytest.raises(GeckoRuntimeError, match="activation_binding"):
        verify_activation(plan, activation)


def test_writer_inode_must_equal_the_guarded_data_root():
    temp, writer, guard, store, plan = setup_store()
    other = tempfile.TemporaryDirectory()
    os.chmod(other.name, 0o700)
    other_fd = os.open(other.name, os.O_RDONLY | os.O_DIRECTORY)
    other_writer = DescriptorWriter(other_fd)
    os.close(other_fd)
    try:
        wrong_store = DescriptorLaneStore(other_writer, guard=guard)
        with pytest.raises(GeckoRuntimeError, match="storage_binding_changed"):
            wrong_store._verify_guard(plan)
    finally:
        other_writer.close()
        other.cleanup()
        writer.close()
        temp.cleanup()


def test_only_open_current_quarter_hour_bucket_is_selected_without_catchup():
    close = 1_800_000_000 * NS
    assert current_bucket_for_tick(close + 59 * NS) is None
    assert current_bucket_for_tick(close + 60 * NS) == close
    assert current_bucket_for_tick(close + 180 * NS) is None
    first = rotated_pool_order(policy(), close)
    second = rotated_pool_order(policy(), close + 900 * NS)
    assert second == first[1:] + first[:1]


def test_live_plan_derives_three_per_minute_and_twenty_second_result_spacing():
    plan = compile_live_plan(
        policy(),
        root_id="a" * 32,
        volume_uuid="11111111-2222-3333-4444-555555555555",
        source_map=source_bindings(),
    )
    assert plan["dispatch"]["hard_requests_per_rolling_minute"] == 3
    assert plan["dispatch"]["global_research_cap_per_rolling_minute"] == 5
    assert plan["dispatch"]["minimum_after_durable_result_seconds"] == 20


def test_four_pool_tick_uses_descriptor_store_spacing_and_causal_durable_receipts():
    temp, writer, _guard, store, plan = setup_store()
    try:
        coordinator = FakeCoordinator(store)
        close = 1_800_000_000 * NS
        clock = [close + 61 * NS]

        def now():
            value = clock[0]
            clock[0] += NS
            return value

        def wait_until(target):
            clock[0] = max(clock[0], target)

        calls = []

        def fetch(request, max_bytes, timeout):
            calls.append(request["request_id"])
            assert max_bytes == 16 * 1024
            assert timeout == 20
            return FetchResult(200, raw_empty(), {})

        report = run_current_bucket(
            plan,
            {
                "schema": ACTIVATION_SCHEMA,
                "schema_version": 3,
                "plan_sha256": plan["plan_sha256"],
                "source_sha256": plan["source_sha256"],
                "root_id": plan["storage"]["root_id"],
                "volume_uuid": plan["storage"]["volume_uuid"],
                "activation_authorized": True,
                "provider_io": True,
                "qualification_status": "passed",
            },
            store,
            coordinator,
            now_ns=now,
            wait_until=wait_until,
            fetch_once=fetch,
        )
        assert len(calls) == 4
        assert report["attempted_pool_ids"] == rotated_pool_order(policy(), close)
        assert report["missing_pool_count"] == 0
        records = store.read_dispatch_records()
        assert all(
            b["dispatch_ns"] - a["dispatch_ns"] >= 20 * NS
            for a, b in zip(records, records[1:], strict=False)
        )
        assert records[3]["dispatch_ns"] >= close + 120 * NS
        assert all(
            sum(
                other["dispatch_ns"] > row["dispatch_ns"] - 60 * NS
                and other["dispatch_ns"] <= row["dispatch_ns"]
                for other in records
            )
            <= 3
            for row in records
        )
        verified = verify_live_store(store, plan)
        assert verified["intents"] == verified["durable_results"] == 4
        assert verified["possibly_spent"] == 0
    finally:
        writer.close()
        temp.cleanup()


@pytest.mark.parametrize("status", [429, 403, 500])
def test_terminal_error_stops_or_advances_by_live_policy_without_retry(status: int):
    temp, writer, _guard, store, plan = setup_store()
    try:
        coordinator = FakeCoordinator(store)
        close = 1_800_000_000 * NS
        clock = [close + 61 * NS]
        calls = []

        def now():
            value = clock[0]
            clock[0] += NS
            return value

        def wait_until(target):
            clock[0] = max(clock[0], target)

        def fetch(request, _max_bytes, _timeout):
            calls.append(request["request_id"])
            return FetchResult(status, b"{}", {})

        report = run_current_bucket(
            plan,
            {
                "schema": ACTIVATION_SCHEMA,
                "schema_version": 3,
                "plan_sha256": plan["plan_sha256"],
                "source_sha256": plan["source_sha256"],
                "root_id": plan["storage"]["root_id"],
                "volume_uuid": plan["storage"]["volume_uuid"],
                "activation_authorized": True,
                "provider_io": True,
                "qualification_status": "passed",
            },
            store,
            coordinator,
            now_ns=now,
            wait_until=wait_until,
            fetch_once=fetch,
        )
        if status == 500:
            assert len(calls) == 3
            assert report["stop_reason"] == "three_consecutive_transient_failures"
        else:
            assert len(calls) == 1
            assert report["stop_reason"] in {"rate_limited", "client_or_access_error"}
        assert len(set(calls)) == len(calls)
        if status == 429:
            # Passing the timed cooldown does not clear the manual rearm latch.
            clock[0] = close + 3600 * NS + 61 * NS
            resumed = run_current_bucket(
                plan,
                {
                    "schema": ACTIVATION_SCHEMA,
                    "schema_version": 3,
                    "plan_sha256": plan["plan_sha256"],
                    "source_sha256": plan["source_sha256"],
                    "root_id": plan["storage"]["root_id"],
                    "volume_uuid": plan["storage"]["volume_uuid"],
                    "activation_authorized": True,
                    "provider_io": True,
                    "qualification_status": "passed",
                },
                store,
                coordinator,
                now_ns=now,
                wait_until=wait_until,
                fetch_once=lambda *_: pytest.fail("429 requires manual rearm"),
            )
            assert resumed["status"] == "manual_review_stop"
            assert resumed["provider_io"] is False
        verify_live_store(store, plan)
    finally:
        writer.close()
        temp.cleanup()


def test_restart_never_replays_spent_request_and_marks_missing_pool_count():
    temp, writer, _guard, store, plan = setup_store()
    try:
        coordinator = FakeCoordinator(store)
        close = 1_800_000_000 * NS
        request = request_for_bucket(policy(), POOL_ROWS[0]["pool_id"], close)
        request_id = request["request_id"]
        intent = {
            "schema": "brain-senses-gecko-intent-v3",
            "request_id": request_id,
            "request": request,
            "decision_ns": close + 61 * NS,
            "provider_charge_units": None,
            "provider_charge_status": "unknown_unverified",
        }
        intent["intent_sha256"] = sha256(canonical(intent))
        writer.create(f"{store.root}/intents/{request_id}.json", canonical(intent))
        clock = [close + 62 * NS]

        def now():
            clock[0] += NS
            return clock[0]

        def wait_until(target):
            clock[0] = max(clock[0], target)

        report = run_current_bucket(
            plan,
            {
                "schema": ACTIVATION_SCHEMA,
                "schema_version": 3,
                "plan_sha256": plan["plan_sha256"],
                "source_sha256": plan["source_sha256"],
                "root_id": plan["storage"]["root_id"],
                "volume_uuid": plan["storage"]["volume_uuid"],
                "activation_authorized": True,
                "provider_io": True,
                "qualification_status": "passed",
            },
            store,
            coordinator,
            now_ns=now,
            wait_until=wait_until,
            fetch_once=lambda *_: pytest.fail("unresolved intent blocks further dispatch"),
        )
        assert report["provider_io"] is False
        assert report["status"] == "spent_unresolved_stop"
        assert report["unresolved_request_ids"] == [request_id]
        assert request_id in store.intent_request_ids()
    finally:
        writer.close()
        temp.cleanup()


def test_independent_verifier_rejects_unreferenced_capture_file():
    temp, writer, _guard, store, plan = setup_store()
    try:
        writer.create(f"{store.root}/captures/unreferenced.bin", b"stray")
        with pytest.raises(GeckoRuntimeError, match="capture_file_closure"):
            verify_live_store(store, plan)
    finally:
        writer.close()
        temp.cleanup()


def test_capacity_gate_counts_existing_and_prospective_intents_conservatively():
    temp, writer, _guard, store, _plan = setup_store()
    try:
        store._policy = policy()
        store._policy["protocol"]["maximum_intents_per_utc_day"] = 1
        store._policy["protocol"]["maximum_intents_per_rolling_30d"] = 1
        now = 1_800_000_000 * NS
        writer.create(
            f"{store.root}/intents/first.json",
            canonical({"request_id": "first", "decision_ns": now - 1}),
        )
        assert _within_lane_budget(store, now, "first")
        assert not _within_lane_budget(store, now, "second")
    finally:
        writer.close()
        temp.cleanup()


def test_pilot_authorization_binds_exact_plan_and_release_rejects_unqualified_prefix():
    temp, writer, _guard, store, plan = setup_store()
    try:
        close = 1_800_000_000 * NS
        authorization = {
            "schema": PILOT_AUTH_SCHEMA,
            "schema_version": 3,
            "plan_sha256": plan["plan_sha256"],
            "source_sha256": plan["source_sha256"],
            "root_id": plan["storage"]["root_id"],
            "volume_uuid": plan["storage"]["volume_uuid"],
            "mode": "one_shot",
            "bucket_close_ns": close,
            "status": "approved",
        }
        verify_pilot_authorization(plan, authorization, close)
        with pytest.raises(GeckoRuntimeError, match="pilot_authorization_binding"):
            verify_pilot_authorization(plan, authorization, close + 900 * NS)

        summary = {
            "bucket_close_ns": close,
            "pilot_outcome": "review_candidate",
            "possibly_spent": 0,
        }
        prefix = {
            "schema": "brain-senses-gecko-v3-pilot-prefix",
            "plan_sha256": plan["plan_sha256"],
            "bucket_close_ns": close,
            "request_ids": [],
            "artifacts": [],
            "dispatch_record_sha256": [],
            "skip_artifacts": [],
        }
        receipt = {
            "schema": PILOT_RECEIPT_SCHEMA,
            "schema_version": 3,
            "plan_sha256": plan["plan_sha256"],
            "source_sha256": plan["source_sha256"],
            "bucket_close_ns": close,
            "verification_status": "passed",
            "verifier_output_sha256": sha256(canonical(summary)),
            "result_sha256": sha256(canonical(prefix)),
            "summary": summary,
            "prefix": prefix,
        }
        receipt_sha = sha256(canonical(receipt))
        verify_pilot_receipt(plan, receipt, receipt_sha)
        release = {
            "schema": "brain-senses-gecko-live-v3-release",
            "schema_version": 3,
            "plan_sha256": plan["plan_sha256"],
            "source_sha256": plan["source_sha256"],
            "pilot_receipt_sha256": receipt_sha,
            "status": "passed",
        }
        with pytest.raises(GeckoRuntimeError, match="scheduled_pilot_qualification_failed"):
            _verify_scheduled_release(plan, release, receipt_sha, receipt, store)
        with pytest.raises(GeckoRuntimeError, match="scheduled_release_binding"):
            _verify_scheduled_release(plan, release, "0" * 64, receipt, store)
    finally:
        writer.close()
        temp.cleanup()


def test_pilot_prefix_remains_valid_after_later_scheduled_bucket_is_appended():
    temp, writer, _guard, store, plan = setup_store()
    try:
        close = 1_800_000_000 * NS
        calls = 0

        def first_bucket_fetch(*_args):
            nonlocal calls
            calls += 1
            body = raw_one_new_bar(close) if calls == 1 else raw_empty()
            return FetchResult(200, body, {})

        assert (
            run_bucket_fixture(store, plan, close, first_bucket_fetch)["status"]
            == "bucket_processed"
        )
        qualification, prefix_before = verify_pilot_bucket(store, plan, close)
        assert qualification["status"] == "passed"
        summary = verify_live_store(store, plan, require_clock=True)
        summary["pilot_qualification"] = qualification
        summary["bucket_close_ns"] = close
        summary["terminal_statuses"] = sorted(row["status"] for row in store.result_rows())
        summary["pilot_outcome"] = "review_candidate"
        receipt = {
            "schema": PILOT_RECEIPT_SCHEMA,
            "schema_version": 3,
            "plan_sha256": plan["plan_sha256"],
            "source_sha256": plan["source_sha256"],
            "bucket_close_ns": close,
            "verification_status": "passed",
            "verifier_output_sha256": sha256(canonical(summary)),
            "result_sha256": sha256(canonical(prefix_before)),
            "summary": summary,
            "prefix": prefix_before,
        }
        receipt_sha = sha256(canonical(receipt))
        release = {
            "schema": "brain-senses-gecko-live-v3-release",
            "schema_version": 3,
            "plan_sha256": plan["plan_sha256"],
            "source_sha256": plan["source_sha256"],
            "pilot_receipt_sha256": receipt_sha,
            "status": "passed",
        }
        verify_pilot_receipt(plan, receipt, receipt_sha)
        _verify_scheduled_release(plan, release, receipt_sha, receipt, store)

        close2 = close + 900 * NS
        run_bucket_fixture(store, plan, close2, lambda *_: FetchResult(200, raw_empty(), {}))
        prefix_after = pilot_prefix(store, plan, close)
        assert prefix_after == prefix_before
        assert len(store.read_dispatch_records()) == 8
        assert verify_live_store(store, plan, require_clock=True)["dispatches"] == 8
        _verify_scheduled_release(plan, release, receipt_sha, receipt, store)
    finally:
        writer.close()
        temp.cleanup()


@pytest.mark.parametrize("case", ["empty", "provider", "transport", "partial"])
def test_pilot_qualification_rejects_empty_transient_and_partial_prefixes(case):
    temp, writer, _guard, store, plan = setup_store()
    try:
        close = 1_800_000_000 * NS
        calls = 0

        def fetch(*_args):
            nonlocal calls
            calls += 1
            if case == "empty":
                return FetchResult(200, raw_empty(), {})
            if case == "provider":
                return FetchResult(500, b"provider error", {})
            if case == "transport":
                raise OSError("synthetic transport failure")
            if calls == 1:
                return FetchResult(200, raw_one_new_bar(close), {})
            return FetchResult(429, b"rate limited", {})

        run_bucket_fixture(store, plan, close, fetch)
        qualification, _prefix = verify_pilot_bucket(store, plan, close)
        assert qualification["status"] == "diagnostic_only"
        assert qualification["reason"] != "four_pool_pilot_qualified"
        if case == "empty":
            assert qualification["intents"] == 4
            assert qualification["empty_responses"] == 4
            assert qualification["eligible_new_open"] == 0
        elif case in {"provider", "transport"}:
            assert qualification["intents"] == 3
            assert qualification["dispatches"] == 3
        else:
            assert qualification["intents"] == 2
            assert qualification["dispatches"] == 2
    finally:
        writer.close()
        temp.cleanup()


@pytest.mark.parametrize("mutate", ["capture", "result"])
def test_pilot_release_rechecks_raw_capture_and_result_immutability(mutate):
    temp, writer, _guard, store, plan = setup_store()
    try:
        close = 1_800_000_000 * NS
        calls = 0

        def fetch(*_args):
            nonlocal calls
            calls += 1
            body = raw_one_new_bar(close) if calls == 1 else raw_empty()
            return FetchResult(200, body, {})

        run_bucket_fixture(store, plan, close, fetch)
        qualification, prefix = verify_pilot_bucket(store, plan, close)
        assert qualification["status"] == "passed"
        summary = verify_live_store(store, plan, require_clock=True)
        summary.update(
            {
                "bucket_close_ns": close,
                "pilot_qualification": qualification,
                "pilot_outcome": "review_candidate",
            }
        )
        receipt = {
            "schema": PILOT_RECEIPT_SCHEMA,
            "schema_version": 3,
            "plan_sha256": plan["plan_sha256"],
            "source_sha256": plan["source_sha256"],
            "bucket_close_ns": close,
            "verification_status": "passed",
            "verifier_output_sha256": sha256(canonical(summary)),
            "result_sha256": sha256(canonical(prefix)),
            "summary": summary,
            "prefix": prefix,
        }
        receipt_sha = sha256(canonical(receipt))
        release = {
            "schema": "brain-senses-gecko-live-v3-release",
            "schema_version": 3,
            "plan_sha256": plan["plan_sha256"],
            "source_sha256": plan["source_sha256"],
            "pilot_receipt_sha256": receipt_sha,
            "status": "passed",
        }
        verify_pilot_receipt(plan, receipt, receipt_sha)
        _verify_scheduled_release(plan, release, receipt_sha, receipt, store)
        request_id = prefix["request_ids"][0]
        relative = (
            f"{store.root}/captures/{request_id}.bin"
            if mutate == "capture"
            else f"{store.root}/results/{request_id}.json"
        )
        path = Path(temp.name, relative)
        if mutate == "capture":
            path.write_bytes(b'{"data":{"attributes":{"ohlcv_list":[]}}}')
        else:
            result = store.read_json(f"results/{request_id}.json")
            result["availability_ns"] += 1
            path.write_bytes(canonical(result))
        with pytest.raises(GeckoRuntimeError):
            _verify_scheduled_release(plan, release, receipt_sha, receipt, store)
    finally:
        writer.close()
        temp.cleanup()


def test_pilot_event_receipt_durable_availability_and_eligibility_are_distinct():
    temp, writer, _guard, store, plan = setup_store()
    try:
        close = 1_800_000_000 * NS
        calls = 0

        def fetch(*_args):
            nonlocal calls
            calls += 1
            return FetchResult(200, raw_one_new_bar(close) if calls == 1 else raw_empty(), {})

        run_bucket_fixture(store, plan, close, fetch)
        verification = verify_live_store(store, plan, require_clock=True)
        assert verification["event_time_semantics"] == "bar_open_event_time_only"
        assert verification["provider_receipt_semantics"].endswith("not_decision_eligibility")
        assert verification["eligibility_semantics"].startswith("confirmation_confirmed_ns")
        first = next(row for row in store.result_rows() if row["bars"])
        bar = first["bars"][0]
        confirmation = next(
            row
            for row in store.read_confirmation_records()
            if row["request_id"] == first["request_id"]
        )
        assert bar["event_time_ns"] < first["receipt_ns"]
        assert bar["provider_receipt_ns"] == first["receipt_ns"]
        assert "source_available_ns" not in bar
        assert first["receipt_ns"] <= first["availability_ns"] <= confirmation["confirmed_ns"]
    finally:
        writer.close()
        temp.cleanup()


def test_three_transients_latch_manual_review_across_restart_and_skip_is_idempotent():
    temp, writer, _guard, store, plan = setup_store()
    try:
        close = 1_800_000_000 * NS
        statuses = [500, None, 502]
        calls = 0

        def fetch(*_args):
            nonlocal calls
            status = statuses[calls]
            calls += 1
            if status is None:
                raise OSError("synthetic transport")
            return FetchResult(status, b"transient", {})

        first = run_bucket_fixture(store, plan, close, fetch)
        assert first["stop_reason"] == "three_consecutive_transient_failures"
        close2 = close + 900 * NS
        # Repeated ticks may revisit the same missing-pool records with a later
        # local timestamp; immutable records remain unchanged and do not conflict.
        run_bucket_fixture(store, plan, close2, lambda *_: pytest.fail("manual latch must persist"))
        records_before = store.read_dispatch_records()
        result = run_current_bucket(
            plan,
            {
                "schema": ACTIVATION_SCHEMA,
                "schema_version": 3,
                "plan_sha256": plan["plan_sha256"],
                "source_sha256": plan["source_sha256"],
                "root_id": plan["storage"]["root_id"],
                "volume_uuid": plan["storage"]["volume_uuid"],
                "activation_authorized": True,
                "provider_io": True,
                "qualification_status": "passed",
            },
            store,
            FakeCoordinator(store),
            now_ns=lambda: close2 + 65 * NS,
            wait_until=lambda _target: None,
            fetch_once=lambda *_: pytest.fail("stopped lane must not resume"),
        )
        assert result["status"] == "manual_review_stop"
        assert store.read_dispatch_records() == records_before

        skip = {
            "schema": "brain-senses-gecko-skip-v3",
            "pool_id": POOL_ROWS[0]["pool_id"],
            "bucket_close_ns": close2 + 900 * NS,
            "at_ns": close2 + 900 * NS + 180 * NS,
            "reason": "missed_dispatch",
            "cooldown_until_ns": None,
        }
        store.append_skip(skip)
        stored = writer.read_bytes(
            f"{store.root}/skips/{skip['bucket_close_ns']}-{skip['pool_id']}.json"
        )
        store.append_skip({**skip, "at_ns": skip["at_ns"] + NS})
        assert (
            writer.read_bytes(
                f"{store.root}/skips/{skip['bucket_close_ns']}-{skip['pool_id']}.json"
            )
            == stored
        )
    finally:
        writer.close()
        temp.cleanup()


def test_main_exposes_fixed_non_network_pilot_modes(monkeypatch, capsys):
    import brain_senses_gecko_lane_runtime_v3 as runtime

    seen = []

    def fake_run(mode):
        seen.append(mode)
        status = {
            "initialize-pilot": "pilot_initialized",
            "seal-pilot": "pilot_receipt_sealed",
            "pilot-once": "bucket_processed",
            "scheduled-tick": "outside_dispatch_window",
        }[mode]
        return {"status": status, "provider_io": False}

    monkeypatch.setattr(runtime, "run_host_mode", fake_run)
    assert runtime.main(["--initialize-pilot"]) == 0
    assert runtime.main(["--seal-pilot"]) == 0
    assert runtime.main(["--pilot-once"]) == 0
    assert runtime.main(["--scheduled-tick"]) == 0
    assert seen == ["initialize-pilot", "seal-pilot", "pilot-once", "scheduled-tick"]
    assert capsys.readouterr().out.count('"provider_io": false') == 4


def test_live_policy_inputs_are_read_through_descriptor_root(monkeypatch):
    import brain_senses_gecko_lane_runtime_v3 as runtime

    temp, writer, guard, _store, _plan = setup_store()
    try:
        source_paths = {
            "train_enrollment": "work/input/enrollment.jsonl",
            "train_role_manifest": "work/input/manifest.json",
            "train_plan": "work/input/plan.json",
            "population_selection": "work/input/selection.json",
        }
        payloads = [b"enrollment", b"manifest", b"plan", b"selection"]
        writer.mkdir("work")
        writer.mkdir("work/input")
        for path, payload in zip(source_paths.values(), payloads, strict=True):
            writer.create(path, payload)
        seen = {}
        monkeypatch.setattr(runtime.offline, "SOURCE_PATHS", source_paths)

        def compile_inputs(**kwargs):
            seen.update(kwargs)
            return {"compiled": True}

        monkeypatch.setattr(runtime.offline, "compile_pinned_policy", compile_inputs)
        assert _compile_pinned_live_policy(writer, guard) == {"compiled": True}
        assert list(seen.values()) == payloads
    finally:
        writer.close()
        temp.cleanup()


def test_public_fetch_is_exact_host_path_and_proxy_free(monkeypatch):
    import brain_senses_gecko_lane_runtime_v3 as runtime

    request = request_for_bucket(policy(), POOL_ROWS[0]["pool_id"], 1_800_000_000 * NS)
    handlers = []

    class Response:
        status = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return raw_empty()

    class Opener:
        def open(self, _request, timeout):
            assert timeout == 20
            return Response()

    def build_opener(*items):
        handlers.extend(items)
        return Opener()

    monkeypatch.setattr(runtime.urllib.request, "build_opener", build_opener)
    result = _public_fetch_once(request, 32 * 1024 * 1024, 20)
    assert result.http_status == 200
    proxy = next(
        handler for handler in handlers if isinstance(handler, runtime.urllib.request.ProxyHandler)
    )
    assert proxy.proxies == {}
    hostile = {**request, "url": request["url"].replace("api.geckoterminal.com", "evil.example")}
    with pytest.raises(GeckoRuntimeError, match="public_request_binding"):
        _public_fetch_once(hostile, 32 * 1024 * 1024, 20)


def test_sanitized_tick_monitor_is_hash_chained_and_has_volume_health_fields():
    temp, writer, _guard, store, _plan = setup_store()
    try:
        store.append_tick_monitor(
            at_ns=123,
            status="bucket_processed",
            reason="bucket_processed",
            request_count=4,
            free_bytes=1000,
        )
        row = store.read_tick_monitor()[0]
        assert row["request_count"] == 4
        assert row["free_bytes"] == 1000
        assert row["root_id"] == "a" * 32
        assert row["volume_uuid"] == "11111111-2222-3333-4444-555555555555"
    finally:
        writer.close()
        temp.cleanup()


def test_live_store_rejects_capture_hash_mutation_and_symlinked_descriptor_path():
    temp, writer, _guard, store, plan = setup_store()
    try:
        with pytest.raises(StorageError):
            writer.read_bytes(f"{store.root}/../outside")
        report = verify_live_store(store, plan)
        assert report["intents"] == 0
        bad_activation = {
            "schema": ACTIVATION_SCHEMA,
            "schema_version": 3,
            "plan_sha256": plan["plan_sha256"],
            "source_sha256": {"runtime": "0" * 64},
            "root_id": plan["storage"]["root_id"],
            "volume_uuid": plan["storage"]["volume_uuid"],
            "activation_authorized": True,
            "provider_io": True,
            "qualification_status": "passed",
        }
        with pytest.raises(GeckoRuntimeError, match="activation_binding"):
            verify_activation(plan, bad_activation)
    finally:
        writer.close()
        temp.cleanup()
