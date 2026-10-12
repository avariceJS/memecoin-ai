from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
for _entry in (REPO / "src", REPO / "tools", REPO / "tests"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

import test_brain_senses_gecko_lane_runtime_v4 as v4_fixtures  # noqa: E402
from brain_senses_gecko_lane_engine_v4 import (  # noqa: E402
    FetchResult,
    ReservationResult,
    execute_one_shot,
    request_for_bucket,
    verify_dispatch_journal,
)
from brain_senses_gecko_lane_policy_v3 import NS, canonical  # noqa: E402
from brain_senses_gecko_lane_verify_v5 import (  # noqa: E402
    INTENT_SCHEMA_V4,
    verify_journal_v5,
)

from tools import brain_senses_external_clock_v1 as clock  # noqa: E402
from tools import brain_senses_gecko_capture_only_monitor_v1 as capture_monitor  # noqa: E402
from tools import brain_senses_gecko_capture_only_runtime_v1 as capture_runtime  # noqa: E402
from tools import brain_senses_gecko_lane_engine_v4 as capture_engine  # noqa: E402
from tools import brain_senses_gecko_lane_runtime_v4 as lane_v4  # noqa: E402
from tools.brain_senses_external_writer_v1 import (  # noqa: E402
    DescriptorWriter,
    canonical_json,
    sha256,
)


def _raw_bar(close_ns: int) -> bytes:
    return v4_fixtures.raw_one_new_bar(close_ns)


def test_capture_intent_uses_v4_clock_schema_and_independent_full_replay() -> None:
    policy = v4_fixtures.policy()
    close_ns = 1_800_000_000 * NS
    pool_id = policy["pools"][0]["pool_id"]
    request = request_for_bucket(policy, pool_id, close_ns)
    clock_record = v4_fixtures.make_clock_record(close_ns)
    clock_raw = canonical(clock_record)
    root = "services/gecko-capture-only/test"
    clock_path = f"{root}/clocks/{close_ns}-00.json"
    wall = [close_ns + 61 * NS]
    intents: list[dict[str, Any]] = []
    terminals: list[dict[str, Any]] = []
    raw_responses: dict[str, bytes] = {}
    dispatches: list[dict[str, Any]] = []
    confirmations: list[dict[str, Any]] = []

    def clock_ns() -> int:
        result = wall[0]
        wall[0] += NS
        return result

    def monotonic_ns() -> int:
        return clock_record["monotonic_ns"] + max(0, wall[0] - clock_record["checked_ns"])

    def reserve(intent: dict[str, Any], _amount: int, _end: int) -> ReservationResult:
        capture_runtime._bind_clock_intent(
            intent,
            clock_path=clock_path,
            clock_sha256=sha256(clock_raw),
            monotonic_ns=monotonic_ns,
        )
        intents.append(dict(intent))
        return ReservationResult("reserved", intent["decision_ns"])

    def persist(terminal: dict[str, Any], raw: bytes | None) -> None:
        terminal["bars"] = v4_fixtures.runtime_module._runtime_bars(terminal["bars"])
        terminal["availability_ns"] = terminal["receipt_ns"] + 1
        terminal["timeliness"] = "available"
        terminals.append(dict(terminal))
        assert raw is not None
        raw_responses[terminal["request_id"]] = raw
        row: dict[str, Any] = {
            "schema": "brain-senses-gecko-result-confirmation-v3",
            "sequence": 0,
            "request_id": terminal["request_id"],
            "outcome_sha256": sha256(canonical(terminal)),
            "confirmed_ns": terminal["availability_ns"] + 1,
            "previous_sha256": None,
        }
        row["record_sha256"] = sha256(canonical(row))
        confirmations.append(row)

    def validate(_request: dict[str, Any], _at_ns: int) -> bool:
        try:
            clock.validate_progress(clock_record, wall_ns=_at_ns, monotonic_ns=monotonic_ns())
        except clock.ClockError:
            return False
        return True

    engine_result = execute_one_shot(
        policy,
        request,
        decision_ns=close_ns + 61 * NS,
        clock_ns=clock_ns,
        lane_lock=lambda: _null_context(),
        read_dispatch_state=lambda: None,
        append_dispatch_record=dispatches.append,
        validate_dispatch_state=validate,
        reserve_intent=reserve,
        fetch_once=lambda *_args: FetchResult(200, _raw_bar(close_ns), {}),
        persist_terminal=persist,
        persist_skip=lambda _skip: pytest.fail("unexpected skip"),
    )

    assert engine_result["status"] == "received"
    assert intents[0]["schema"] == INTENT_SCHEMA_V4
    assert set(intents[0]) == {
        "schema",
        "request_id",
        "request",
        "decision_ns",
        "provider_charge_units",
        "provider_charge_status",
        "intent_sha256",
        "clock_path",
        "clock_sha256",
        "decision_monotonic_ns",
    }
    replay = verify_journal_v5(
        policy,
        intents,
        terminals,
        raw_responses,
        dispatches,
        root=root,
        clock_files={clock_path: clock_raw},
        scheduled_closes_ns=[close_ns],
        confirmations=confirmations,
    )
    assert replay["intents"] == replay["terminal_count"] == 1
    assert replay["intent_contract"] == INTENT_SCHEMA_V4
    assert replay["release_qualification_allowed"] is False


def test_capture_plan_pins_diagnostic_pilot_without_market_authority() -> None:
    policy = v4_fixtures.policy()
    pilot_close = 1_800_000_000 * NS
    predecessor = {
        "kind": "sealed_v4_pilot",
        "pilot_id": capture_runtime.PREDECESSOR_PILOT_ID,
        "service_root": lane_v4._pilot_root(capture_runtime.PREDECESSOR_PILOT_ID),
        "plan_sha256": "1" * 64,
        "pilot_receipt_sha256": "2" * 64,
        "pilot_prefix_sha256": "3" * 64,
        "pilot_bucket_close_ns": pilot_close,
        "technical_status": "diagnostic_only",
        "technical_reason": "pilot_no_eligible_new_open",
        "source_sha256": {"predecessor": "4" * 64},
    }
    sources = {
        **capture_runtime.source_bindings(),
        lane_v4.SOURCE_BINDING_KEY: "5" * 64,
    }
    plan = capture_runtime.compile_capture_plan(
        capture_id="6c4e52c7a9bb4bdc8e5f1d774ec8aa17",
        storage={"root_id": "a" * 32, "volume_uuid": "11111111-2222-3333-4444-555555555555"},
        policy=policy,
        sources=sources,
        predecessor=predecessor,
        first_bucket_close_ns=pilot_close + 900 * NS,
    )
    assert plan["authority"] == {
        "capture_authorized": False,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
        "provider_io": False,
    }
    capture_runtime.verify_capture_plan(plan)
    invalid = dict(predecessor, technical_reason="zero_market_activity")
    with pytest.raises(
        capture_runtime.CaptureRuntimeError, match="predecessor_not_technically_clean"
    ):
        capture_runtime.compile_capture_plan(
            capture_id=plan["capture_id"],
            storage=plan["storage"],
            policy=policy,
            sources=sources,
            predecessor=invalid,
            first_bucket_close_ns=pilot_close + 1800 * NS,
        )


def _capture_plan_for_test() -> dict[str, Any]:
    policy = v4_fixtures.policy()
    pilot_close = 1_800_000_000 * NS
    predecessor = {
        "kind": "sealed_v4_pilot",
        "pilot_id": capture_runtime.PREDECESSOR_PILOT_ID,
        "service_root": lane_v4._pilot_root(capture_runtime.PREDECESSOR_PILOT_ID),
        "plan_sha256": "1" * 64,
        "pilot_receipt_sha256": "2" * 64,
        "pilot_prefix_sha256": "3" * 64,
        "pilot_bucket_close_ns": pilot_close,
        "technical_status": "diagnostic_only",
        "technical_reason": "pilot_no_eligible_new_open",
        "source_sha256": {"predecessor": "4" * 64},
    }
    return capture_runtime.compile_capture_plan(
        capture_id="6c4e52c7a9bb4bdc8e5f1d774ec8aa17",
        storage={"root_id": "a" * 32, "volume_uuid": "11111111-2222-3333-4444-555555555555"},
        policy=policy,
        sources={
            **capture_runtime.source_bindings(),
            lane_v4.SOURCE_BINDING_KEY: "5" * 64,
        },
        predecessor=predecessor,
        first_bucket_close_ns=pilot_close + 900 * NS,
    )


def test_release_is_manual_review_bound_and_activation_is_separate() -> None:
    temp = tempfile.TemporaryDirectory()
    os.chmod(temp.name, 0o700)
    root_fd = os.open(temp.name, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(root_fd)
    os.close(root_fd)
    writer.mkdir("lane")
    store = _ArtifactStore(writer)
    plan = _capture_plan_for_test()
    preflight: dict[str, Any] = {
        "schema": capture_runtime.PREFLIGHT_SCHEMA,
        "schema_version": 1,
        "capture_id": plan["capture_id"],
        "plan_sha256": plan["plan_sha256"],
        "source_sha256": plan["source_sha256"],
        "predecessor_sha256": sha256(canonical_json(plan["predecessor"])),
        "storage": plan["storage"],
        "observed_ns": 100,
        "free_bytes": 100 * 1024**3,
        "minimum_free_bytes": 58 * 1024**3,
        "provider_io": False,
        "capture_authorized": False,
        "status": "preflight_passed",
    }
    preflight["preflight_sha256"] = sha256(canonical_json(preflight))
    store._create_exact("capture-preflight.json", preflight)
    callback: dict[str, Any] = {
        "schema": capture_runtime.CALLBACK_SCHEMA,
        "schema_version": 1,
        "capture_id": plan["capture_id"],
        "plan_sha256": plan["plan_sha256"],
        "source_sha256": plan["source_sha256"],
        "storage": plan["storage"],
        "predecessor_sha256": sha256(canonical_json(plan["predecessor"])),
        "preflight_sha256": sha256(canonical_json(preflight)),
        "label": capture_runtime.LABEL,
        "pid": 123,
        "argv": capture_runtime._expected_argv(plan["capture_id"]),
        "callback_started_ns": 101,
        "callback_monotonic_ns": 102,
        "callback_ns": 103,
        "bucket_close_ns": None,
        "provider_io": False,
        "capture_authorized": False,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
        "status": "no_provider_callback_verified",
    }
    callback["callback_sha256"] = sha256(canonical_json(callback))
    store._create_exact("launchd-callback.json", callback)
    with pytest.raises(FileNotFoundError):
        capture_runtime._create_release(store, plan)
    review: dict[str, Any] = {
        "schema": capture_runtime.REVIEW_SCHEMA,
        "schema_version": 1,
        "capture_id": plan["capture_id"],
        "plan_sha256": plan["plan_sha256"],
        "source_sha256": plan["source_sha256"],
        "predecessor_sha256": sha256(canonical_json(plan["predecessor"])),
        "preflight_sha256": sha256(canonical_json(preflight)),
        "callback_sha256": sha256(canonical_json(callback)),
        "storage": plan["storage"],
        "decision_reference": "sol-capture-only-continuous-release-v1",
        "integrator_verification": "passed",
        "reviewed_ns": 104,
        "status": "approved_capture_only",
        "capture_authorized": True,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
        "provider_io": False,
    }
    store._create_exact("capture-review.json", review)
    sealed = capture_runtime._create_release(store, plan)
    assert sealed["provider_io"] is False
    release = store.read_json("capture-release.json")
    capture_runtime.verify_release(plan, release)
    activation = capture_runtime._create_activation(store, plan)
    assert activation["status"] == "capture_activation_created"
    assert store.read_json("activation.json")["capture_authorized"] is True
    writer.close()
    temp.cleanup()


def test_no_provider_callback_cannot_create_release_or_activation() -> None:
    temp = tempfile.TemporaryDirectory()
    os.chmod(temp.name, 0o700)
    root_fd = os.open(temp.name, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(root_fd)
    os.close(root_fd)
    writer.mkdir("lane")
    store = _ArtifactStore(writer)
    plan = _capture_plan_for_test()
    preflight: dict[str, Any] = {
        "schema": capture_runtime.PREFLIGHT_SCHEMA,
        "schema_version": 1,
        "capture_id": plan["capture_id"],
        "plan_sha256": plan["plan_sha256"],
        "source_sha256": plan["source_sha256"],
        "predecessor_sha256": sha256(canonical_json(plan["predecessor"])),
        "storage": plan["storage"],
        "observed_ns": 100,
        "free_bytes": 100 * 1024**3,
        "minimum_free_bytes": 58 * 1024**3,
        "provider_io": False,
        "capture_authorized": False,
        "status": "preflight_passed",
    }
    preflight["preflight_sha256"] = sha256(canonical_json(preflight))
    store._create_exact("capture-preflight.json", preflight)
    result = capture_runtime._tech_callback(
        store,
        plan,
        lambda: 103,
        lambda _capture_id: (321, capture_runtime._expected_argv(plan["capture_id"])),
    )
    assert result["provider_io"] is False
    files = writer.list_files()
    assert "lane/launchd-callback.json" in files
    assert "lane/capture-release.json" not in files
    assert "lane/activation.json" not in files
    writer.close()
    temp.cleanup()


class _CaptureStore:
    def __init__(self, policy: dict[str, Any], close_ns: int, wall: list[int]) -> None:
        self.root = "services/gecko-capture-only/test"
        self._policy = policy
        self.close_ns = close_ns
        self.wall = wall
        self.intents: dict[str, dict[str, Any]] = {}
        self.terminals: list[dict[str, Any]] = []
        self.raw: dict[str, bytes] = {}
        self.dispatches: list[dict[str, Any]] = []
        self.confirmations: list[dict[str, Any]] = []
        self.skips: list[dict[str, Any]] = []
        self.clock = v4_fixtures.make_clock_record(close_ns)
        clock_bytes = canonical(self.clock)
        self.clock_path = f"{self.root}/clocks/{close_ns}-00.json"
        self.clock_sha = sha256(clock_bytes)

    def reconcile_result_confirmations(self, _now_ns) -> None:
        return None

    def intent_request_ids(self) -> set[str]:
        return set(self.intents)

    def result_rows(self) -> list[dict[str, Any]]:
        return self.terminals

    def dispatch_state(self) -> dict[str, Any] | None:
        state = verify_dispatch_journal(self.dispatches)
        if state is None:
            return None
        cooldown = max(
            (row["cooldown_until_ns"] for row in self.terminals if row.get("cooldown_until_ns")),
            default=None,
        )
        manual_statuses = {
            capture_engine.STATUS_RATE_LIMITED,
            capture_engine.STATUS_CLIENT_ERROR,
            capture_engine.STATUS_MALFORMED,
            capture_engine.STATUS_OVERSIZE,
        }
        return {
            **state,
            "cooldown_until_ns": cooldown,
            "manual_review_required": any(
                row.get("manual_review_required") is True or row.get("status") in manual_statuses
                for row in self.terminals
            ),
        }

    def clock_binding(self, close_ns: int, *, now_ns):
        assert close_ns == self.close_ns
        return self.clock_path, self.clock_sha, self.clock

    def seen_request(self, request_id: str) -> bool:
        if request_id in self.intents:
            return True
        return any(
            capture_engine.request_for_bucket(self._policy, row["pool_id"], row["bucket_close_ns"])[
                "request_id"
            ]
            == request_id
            for row in self.skips
        )

    def append_skip(self, row: dict[str, Any]) -> None:
        self.skips.append(row)

    def latest_confirmation_ns(self) -> int | None:
        return self.confirmations[-1]["confirmed_ns"] if self.confirmations else None

    def append_dispatch_record(self, row: dict[str, Any]) -> None:
        self.dispatches.append(row)

    def persist_response_and_result(self, terminal, raw, now_ns) -> bytes:
        terminal["bars"] = lane_v4._runtime_bars(terminal.get("bars", []))
        terminal["availability_ns"] = now_ns() if raw is not None else None
        terminal["durability"] = (
            "response_and_parent_fsynced" if raw is not None else "no_response_body"
        )
        terminal["timeliness"] = "available" if raw is not None else "no_response"
        self.terminals.append(terminal)
        if raw is not None:
            self.raw[terminal["request_id"]] = raw
        return canonical_json(terminal)

    def append_result_confirmation(self, request_id: str, outcome_sha256: str, confirmed_ns: int):
        row: dict[str, Any] = {
            "schema": "brain-senses-gecko-result-confirmation-v3",
            "sequence": len(self.confirmations),
            "request_id": request_id,
            "outcome_sha256": outcome_sha256,
            "confirmed_ns": confirmed_ns,
            "previous_sha256": self.confirmations[-1]["record_sha256"]
            if self.confirmations
            else None,
        }
        row["record_sha256"] = sha256(canonical(row))
        self.confirmations.append(row)


class _CaptureCoordinator:
    def __init__(self, store: _CaptureStore) -> None:
        self.store = store
        self._gecko_runtime_lease = None

    def recover_service(self, _service, *, intent_exists):
        return {"released_pre_intent": 0, "ambiguous_terminal": 0}

    @contextlib.contextmanager
    def lane_lock(self, _service):
        yield object()

    def reserve_and_persist_intent(self, **kwargs):
        row = json.loads(kwargs["intent_bytes"])
        self.store.intents[row["request_id"]] = row
        return SimpleNamespace(outcome_path=kwargs["outcome_path"])

    def settle(self, *_args, **_kwargs):
        return None


def _capture_activation(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": capture_runtime.ACTIVATION_SCHEMA,
        "schema_version": 1,
        "capture_id": plan["capture_id"],
        "plan_sha256": plan["plan_sha256"],
        "source_sha256": plan["source_sha256"],
        "release_sha256": "1" * 64,
        "callback_sha256": "2" * 64,
        "preflight_sha256": "3" * 64,
        "capture_authorized": True,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
        "provider_io": True,
    }


@pytest.mark.parametrize("deadline", [False, True])
def test_capture_tick_waits_for_result_spacing_and_never_dispatches_after_deadline(
    monkeypatch, deadline
) -> None:
    plan = _capture_plan_for_test()
    close_ns = plan["schedule"]["first_bucket_close_ns"]
    wall = [close_ns + 60 * NS]
    store = _CaptureStore(plan["policy"], close_ns, wall)
    coordinator = _CaptureCoordinator(store)
    calls: list[dict[str, Any]] = []

    def now_ns() -> int:
        value = wall[0]
        wall[0] += NS
        return value

    def monotonic_ns() -> int:
        return store.clock["monotonic_ns"] + wall[0] - store.clock["checked_ns"]

    def wait_until(target: int) -> None:
        wall[0] = max(wall[0], close_ns + 215 * NS if deadline else target)

    monkeypatch.setattr(clock, "validate_clock", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(clock, "validate_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(lane_v4, "_within_lane_budget", lambda *_args: True)
    monkeypatch.setattr(
        capture_runtime.capture_verify,
        "verify_capture_prefix",
        lambda target, _plan: {
            "intents": len(target.intents),
            "durable_results": len(target.terminals),
            "request_tail_sha256": "a" * 64,
            "result_tail_sha256": "b" * 64,
        },
    )

    def fetch(request, _limit, _timeout):
        calls.append(request)
        return FetchResult(200, _raw_bar(close_ns), {})

    result = capture_runtime.run_capture_bucket(
        store,
        plan,
        _capture_activation(plan),
        guard=SimpleNamespace(),
        now_ns=now_ns,
        monotonic_ns=monotonic_ns,
        wait_until=wait_until,
        coordinator=coordinator,
        fetch_once=fetch,
    )
    assert result["provider_io"] is bool(calls)
    if deadline:
        assert len(calls) == 1
        assert result["missing_pool_buckets_unknown"] == 3
        assert len(store.skips) == 3
    else:
        assert len(calls) == 4
        dispatched = [row["dispatch_ns"] for row in store.dispatches]
        confirmed = [row["confirmed_ns"] for row in store.confirmations]
        assert all(
            next_dispatch - prior_confirmation >= 20 * NS
            for prior_confirmation, next_dispatch in zip(confirmed, dispatched[1:], strict=False)
        )
        assert all(b - a >= 12 * NS for a, b in zip(dispatched, dispatched[1:], strict=False))
        assert result["missing_pool_buckets_unknown"] == 0


def test_capture_tick_reports_provider_io_only_for_actual_dispatch(monkeypatch) -> None:
    plan = _capture_plan_for_test()
    close_ns = plan["schedule"]["first_bucket_close_ns"]
    wall = [close_ns + 60 * NS]
    store = _CaptureStore(plan["policy"], close_ns, wall)
    coordinator = _CaptureCoordinator(store)
    monkeypatch.setattr(clock, "validate_clock", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(clock, "validate_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(lane_v4, "_within_lane_budget", lambda *_args: True)
    monkeypatch.setattr(
        capture_runtime.capture_verify,
        "verify_capture_prefix",
        lambda *_args: {
            "intents": 0,
            "durable_results": 0,
            "request_tail_sha256": "a" * 64,
            "result_tail_sha256": "b" * 64,
        },
    )
    monkeypatch.setattr(
        capture_engine,
        "execute_one_shot",
        lambda *_args, **_kwargs: {"status": "deadline_skipped", "provider_io": False},
    )
    result = capture_runtime.run_capture_bucket(
        store,
        plan,
        _capture_activation(plan),
        guard=SimpleNamespace(),
        now_ns=lambda: wall[0],
        monotonic_ns=lambda: store.clock["monotonic_ns"],
        wait_until=lambda _target: None,
        coordinator=coordinator,
        fetch_once=lambda *_args: pytest.fail("provider call after deadline skip"),
    )
    assert result["attempted_pool_ids"]
    assert result["dispatched_pool_ids"] == []
    assert result["provider_io"] is False


@pytest.mark.parametrize(
    ("http_status", "expected_status"),
    [(429, capture_engine.STATUS_RATE_LIMITED), (503, capture_engine.STATUS_PROVIDER_ERROR)],
)
def test_capture_tick_stops_after_rate_limit_or_provider_error(
    monkeypatch, http_status, expected_status
) -> None:
    plan = _capture_plan_for_test()
    close_ns = plan["schedule"]["first_bucket_close_ns"]
    wall = [close_ns + 60 * NS]
    store = _CaptureStore(plan["policy"], close_ns, wall)
    coordinator = _CaptureCoordinator(store)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(clock, "validate_clock", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(clock, "validate_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(lane_v4, "_within_lane_budget", lambda *_args: True)
    monkeypatch.setattr(
        capture_runtime.capture_verify,
        "verify_capture_prefix",
        lambda target, _plan: {
            "intents": len(target.intents),
            "durable_results": len(target.terminals),
            "request_tail_sha256": "a" * 64,
            "result_tail_sha256": "b" * 64,
        },
    )

    def fetch(request, _limit, _timeout):
        calls.append(request)
        return FetchResult(http_status, b"{}", {})

    result = capture_runtime.run_capture_bucket(
        store,
        plan,
        _capture_activation(plan),
        guard=SimpleNamespace(),
        now_ns=lambda: wall[0],
        monotonic_ns=lambda: store.clock["monotonic_ns"] + wall[0] - close_ns,
        wait_until=lambda target: wall.__setitem__(0, max(wall[0], target)),
        coordinator=coordinator,
        fetch_once=fetch,
    )
    assert len(calls) == 1
    assert result["provider_io"] is True
    assert result["status"] == "capture_bucket_stopped"
    assert result["terminal_statuses"] == [expected_status]
    assert len(store.intents) == len(store.terminals) == len(store.confirmations) == 1
    resumed = capture_runtime.run_capture_bucket(
        store,
        plan,
        _capture_activation(plan),
        guard=SimpleNamespace(),
        now_ns=lambda: wall[0],
        monotonic_ns=lambda: store.clock["monotonic_ns"] + wall[0] - close_ns,
        wait_until=lambda _target: pytest.fail("cooldown tick must not wait into dispatch"),
        coordinator=coordinator,
        fetch_once=fetch,
    )
    assert resumed["status"] == ("manual_review_stop" if http_status == 429 else "cooldown_stop")
    assert resumed["provider_io"] is False
    assert len(calls) == 1


def test_preflight_rejects_forged_lower_storage_floor() -> None:
    plan = _capture_plan_for_test()
    value: dict[str, Any] = {
        "schema": capture_runtime.PREFLIGHT_SCHEMA,
        "schema_version": 1,
        "capture_id": plan["capture_id"],
        "plan_sha256": plan["plan_sha256"],
        "source_sha256": plan["source_sha256"],
        "predecessor_sha256": sha256(canonical_json(plan["predecessor"])),
        "storage": plan["storage"],
        "observed_ns": 100,
        "free_bytes": 100 * 1024**3,
        "minimum_free_bytes": 57 * 1024**3,
        "provider_io": False,
        "capture_authorized": False,
        "status": "preflight_passed",
    }
    value["preflight_sha256"] = sha256(canonical_json(value))
    with pytest.raises(capture_runtime.CaptureRuntimeError, match="preflight_binding"):
        capture_runtime._verify_preflight(plan, value)


def _null_context():
    class Context:
        def __enter__(self):
            return None

        def __exit__(self, *_exc):
            return False

    return Context()


class _MonitorStore:
    def __init__(self, writer: DescriptorWriter) -> None:
        self.writer = writer
        self.root = "lane"

    def _path(self, name: str) -> str:
        return f"{self.root}/{name}"

    def files(self, directory: str) -> list[str]:
        prefix = self._path(directory) + "/"
        return [path[len(prefix) :] for path in self.writer.list_files() if path.startswith(prefix)]


class _ArtifactStore:
    def __init__(self, writer: DescriptorWriter) -> None:
        self.writer = writer
        self.root = "lane"

    def _path(self, name: str) -> str:
        return f"{self.root}/{name}"

    def read_json(self, name: str) -> dict[str, Any]:
        raw = self.writer.read_bytes(self._path(name), max_bytes=1024 * 1024)
        return json.loads(raw)

    def _create_exact(self, name: str, value: dict[str, Any]) -> None:
        self.writer.create(self._path(name), canonical_json(value))


def _monitor_store(*, fault_hook=None):
    temp = tempfile.TemporaryDirectory()
    os.chmod(temp.name, 0o700)
    root_fd = os.open(temp.name, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(root_fd, fault_hook=fault_hook)
    os.close(root_fd)
    writer.mkdir("lane")
    writer.mkdir("lane/capture-health-events")
    return temp, writer, _MonitorStore(writer)


def _publish_monitor(
    store,
    writer,
    *,
    now=100,
    request_tail="1",
    stopped=None,
    **kwargs,
):
    request_count = kwargs.pop("request_count", 0)
    return capture_monitor.publish(
        writer,
        store,
        now_ns=now,
        root_id="a" * 32,
        volume_uuid="11111111-2222-3333-4444-555555555555",
        request_count=request_count,
        result_count=0,
        request_tail_sha256=request_tail * 64,
        result_tail_sha256="2" * 64,
        coordinator_tail_sha256="3" * 64,
        free_bytes=1000,
        source_sha256="4" * 64,
        plan_sha256="5" * 64,
        stopped_reason=stopped,
        **kwargs,
    )


def test_monitor_allows_normal_tick_progress_and_requires_receipt_after_restart() -> None:
    temp, writer, store = _monitor_store()
    try:
        first = _publish_monitor(store, writer)
        second = _publish_monitor(
            store,
            writer,
            now=200,
            request_tail="6",
            request_count=1,
            completed_operation=True,
        )
        assert second["event_count"] == first["event_count"] == 0
        assert capture_monitor.read_snapshot(store) == second
        with pytest.raises(ValueError, match="unreceipted_recovery"):
            _publish_monitor(store, writer, now=300, request_tail="7")
        third = _publish_monitor(
            store,
            writer,
            now=300,
            request_tail="7",
            recovery_proof_sha256="8" * 64,
        )
        assert third["event_count"] == 1
        assert capture_monitor.read_events(store)[0]["kind"] == "journal_reconciled"
    finally:
        writer.close()
        temp.cleanup()


def test_monitor_records_stale_heartbeat_gap_before_healthy_restart() -> None:
    temp, writer, store = _monitor_store()
    try:
        first = _publish_monitor(store, writer, now=100)
        boundary = first["observed_at_ns"] + capture_monitor.MAX_HEARTBEAT_AGE_NS
        _publish_monitor(store, writer, now=boundary + 1)
        events = capture_monitor.read_events(store)
        assert [(row["kind"], row["reason"]) for row in events] == [
            ("alarm", "heartbeat_gap"),
            ("recovery", "heartbeat_gap"),
        ]
        recovered = capture_monitor.read_snapshot(store)
        assert recovered is not None
        assert recovered["status"] == "healthy"
        assert recovered["stop_latched"] is False
        assert capture_monitor.read_snapshot(store) == recovered
    finally:
        writer.close()
        temp.cleanup()


def test_monitor_repairs_crash_after_gap_events_before_snapshot() -> None:
    fault = {"armed": False}

    def fault_hook(operation: str) -> None:
        if fault["armed"] and operation == "atomic_rename":
            raise OSError("simulated snapshot publication crash")

    temp, writer, store = _monitor_store(fault_hook=fault_hook)
    try:
        first = _publish_monitor(store, writer, now=100)
        stale_now = first["observed_at_ns"] + capture_monitor.MAX_HEARTBEAT_AGE_NS + 1
        fault["armed"] = True
        with pytest.raises(OSError, match="publication crash"):
            _publish_monitor(store, writer, now=stale_now)
        fault["armed"] = False
        # The prior snapshot is an ancestor of the durable transition journal.
        assert capture_monitor.read_snapshot(store) == first
        assert len(capture_monitor.read_events(store)) == 2
        repaired = _publish_monitor(store, writer, now=stale_now)
        assert repaired["event_count"] == 2
        assert len(capture_monitor.read_events(store)) == 2
        assert repaired["status"] == "healthy"
    finally:
        writer.close()
        temp.cleanup()


def test_monitor_completes_missing_gap_recovery_after_alarm_only_crash() -> None:
    fault = {"armed": False, "creates": 0}

    def fault_hook(operation: str) -> None:
        if fault["armed"] and operation == "create":
            fault["creates"] += 1
            if fault["creates"] == 2:
                raise OSError("simulated after alarm event")

    temp, writer, store = _monitor_store(fault_hook=fault_hook)
    try:
        first = _publish_monitor(store, writer, now=100)
        stale_now = first["observed_at_ns"] + capture_monitor.MAX_HEARTBEAT_AGE_NS + 1
        fault["armed"] = True
        with pytest.raises(OSError, match="after alarm event"):
            _publish_monitor(store, writer, now=stale_now)
        fault["armed"] = False
        events = capture_monitor.read_events(store)
        assert [(row["kind"], row["reason"]) for row in events] == [("alarm", "heartbeat_gap")]
        assert capture_monitor.read_snapshot(store) == first
        repaired = _publish_monitor(store, writer, now=stale_now)
        events = capture_monitor.read_events(store)
        assert [(row["kind"], row["reason"]) for row in events] == [
            ("alarm", "heartbeat_gap"),
            ("recovery", "heartbeat_gap"),
        ]
        assert repaired["event_count"] == 2
        assert repaired["status"] == "healthy"
    finally:
        writer.close()
        temp.cleanup()


def test_monitor_heartbeat_at_freshness_boundary_does_not_alarm() -> None:
    temp, writer, store = _monitor_store()
    try:
        first = _publish_monitor(store, writer, now=100)
        boundary = first["observed_at_ns"] + capture_monitor.MAX_HEARTBEAT_AGE_NS
        refreshed = _publish_monitor(store, writer, now=boundary)
        assert refreshed["event_count"] == 0
        assert capture_monitor.read_events(store) == []
    finally:
        writer.close()
        temp.cleanup()


def test_monitor_rejects_wall_time_regression_without_snapshot_replacement() -> None:
    temp, writer, store = _monitor_store()
    try:
        first = _publish_monitor(store, writer, now=200)
        with pytest.raises(ValueError, match="time_regression"):
            _publish_monitor(store, writer, now=199)
        assert capture_monitor.read_snapshot(store) == first
        assert capture_monitor.read_events(store) == []
    finally:
        writer.close()
        temp.cleanup()


def test_monitor_alarm_reason_change_is_recovered_then_realarms() -> None:
    temp, writer, store = _monitor_store()
    try:
        alarm = _publish_monitor(store, writer, stopped="clock")
        assert alarm["stop_latched"] is True
        changed = _publish_monitor(store, writer, now=200, stopped="storage")
        events = capture_monitor.read_events(store)
        assert [row["kind"] for row in events] == ["alarm", "recovery", "alarm"]
        assert changed["stop_latched"] is True
        assert capture_monitor.read_snapshot(store) == changed
        altered = dict(changed)
        altered["event_count"] = 1
        altered.pop("snapshot_sha256")
        altered["snapshot_sha256"] = sha256(canonical(altered))
        writer.atomic_write(store._path(capture_monitor.SNAPSHOT), canonical_json(altered))
        with pytest.raises(ValueError, match="snapshot_event_mismatch"):
            capture_monitor.read_snapshot(store)
    finally:
        writer.close()
        temp.cleanup()


def test_recoverable_host_error_latches_integrity_alarm_or_reports_stale() -> None:
    temp, writer, store = _monitor_store()
    try:
        _publish_monitor(store, writer)
        plan = _capture_plan_for_test()
        guard = SimpleNamespace(
            root_id=plan["storage"]["root_id"],
            volume_uuid=plan["storage"]["volume_uuid"],
            revalidate=lambda: None,
        )
        assert capture_runtime._best_effort_integrity_alarm(store, plan, guard, lambda: 200)
        snapshot = capture_monitor.read_snapshot(store)
        assert snapshot is not None
        assert snapshot["status"] == "stopped"
        assert snapshot["reason"] == "integrity"
        assert snapshot["stop_latched"] is True

        broken_guard = SimpleNamespace(
            root_id=guard.root_id,
            volume_uuid=guard.volume_uuid,
            revalidate=lambda: (_ for _ in ()).throw(OSError("detached")),
        )
        assert not capture_runtime._best_effort_integrity_alarm(
            store, plan, broken_guard, lambda: 300
        )
        # A detach prevents a new trusted heartbeat; the prior one remains stale.
        assert capture_monitor.read_snapshot(store) == snapshot
    finally:
        writer.close()
        temp.cleanup()


def test_monitor_atomic_replace_detach_does_not_fall_back_to_underlay() -> None:
    with tempfile.TemporaryDirectory() as parent:
        os.chmod(parent, 0o700)
        original = Path(parent) / "mounted"
        moved = Path(parent) / "detached-volume"
        original.mkdir(mode=0o700)
        state: dict[str, Any] = {"detached": False}

        def fault(operation: str) -> None:
            if operation == "atomic_rename" and not state["detached"]:
                original.rename(moved)
                original.mkdir(mode=0o700)
                state["detached"] = True
                raise OSError("simulated detach")

        root_fd = os.open(original, os.O_RDONLY | os.O_DIRECTORY)
        writer = DescriptorWriter(root_fd, fault_hook=fault)
        os.close(root_fd)
        store = _MonitorStore(writer)
        writer.mkdir("lane")
        writer.mkdir("lane/capture-health-events")
        try:
            with pytest.raises(OSError, match="simulated detach"):
                _publish_monitor(store, writer)
            assert state["detached"] is True
            assert not (original / "lane/capture-health.json").exists()
            assert not list(original.rglob("*.tmp"))
            assert not (moved / "lane/capture-health.json").exists()
        finally:
            writer.close()
        assert not list(moved.rglob("*.tmp"))
