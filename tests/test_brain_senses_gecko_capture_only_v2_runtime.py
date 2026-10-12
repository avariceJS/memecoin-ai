from __future__ import annotations

import base64
import datetime as dt
import hashlib
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

from tools import brain_senses_capacity_coordinator_v2 as capacity  # noqa: E402
from tools import brain_senses_gecko_capture_only_v2_verify as verifier  # noqa: E402
from tools import brain_senses_gecko_rotating_scout_v1 as scout  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402
from tools.brain_senses_gecko_capture_only_v2_runtime import (  # noqa: E402
    CaptureWriterError,
    DescriptorCaptureWriter,
)
from tools.brain_senses_gecko_scout_chain_v1 import DescriptorScoutJournal  # noqa: E402
from tools.brain_senses_gecko_shared_usage_v1 import assess_usage  # noqa: E402
from tools.brain_senses_gecko_usage_inventory_v1 import (  # noqa: E402
    SERVICE_ROOT,
    build_usage_inventory,
)

SERVICE_ROOTS = {
    "helius": "services/helius-v5",
    "alchemy": "services/alchemy-sentinel-v2",
    "gecko": SERVICE_ROOT,
}
LOCK_PATHS = {
    "helius": "services/locks/helius.lock",
    "alchemy": "services/locks/alchemy.lock",
    "gecko": "services/locks/gecko.lock",
}
ALLOCATIONS = {
    "helius": ("services/helius-v4", SERVICE_ROOTS["helius"]),
    "alchemy": (SERVICE_ROOTS["alchemy"],),
    "gecko": (SERVICE_ROOTS["gecko"],),
    "coordinator": (capacity.COORDINATOR_ROOT,),
}
RELEASE_ROOT = f"{SERVICE_ROOT}/capture-only-v2/{'a' * 32}"
COORDINATOR_TIME_NS = 1_900_000_000_000_000_000
FIRST_CLOSE_NS = (COORDINATOR_TIME_NS + scout.BUCKET_NS - 1) // scout.BUCKET_NS * scout.BUCKET_NS


class _CapacitySource:
    def __init__(self, root_fd: int) -> None:
        root = os.fstat(root_fd)
        self.identity = (root.st_dev, root.st_ino)

    def verify_root(self, writer: Any) -> None:
        root = os.fstat(writer.root_fd)
        if (root.st_dev, root.st_ino) != self.identity:
            raise capacity.CoordinatorError("root_identity_lost")

    def allocated_bytes(self, _writer: Any, _service: str) -> int:
        return 0

    def free_bytes(self, _writer: Any) -> int:
        return 300_000_000_000


def _policy() -> dict[str, Any]:
    source_pin = hashlib.sha256(b"v2-runtime-test-source").hexdigest()
    population = scout.pool_order(
        [
            {
                "pool_id": f"pool-{index:03d}",
                "base_mint": f"mint-{index:03d}",
                "quote_mint": "wsol",
                "rank_sha256": hashlib.sha256(
                    f"{source_pin}\0pool-{index:03d}".encode()
                ).hexdigest(),
            }
            for index in range(scout.POOL_COUNT)
        ],
        source_pin,
    )
    core = {
        "schema": scout.SCHEMA,
        "schema_version": 1,
        "policy_id": scout.POLICY_ID,
        "source": {"source_pin": source_pin},
        "population_count": scout.POOL_COUNT,
        "population_sha256": scout.population_digest(population),
        "population": population,
        "provider_io": False,
        "activation_authorized": False,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
    }
    return {**core, "policy_sha256": hashlib.sha256(scout.canonical_json(core)).hexdigest()}


class _Fixture:
    def __init__(self, path: Path, *, fault_hook: Callable[[str], None] | None = None) -> None:
        path.mkdir(mode=0o700)
        self.root_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        self.writer = DescriptorWriter(self.root_fd, fault_hook=fault_hook)
        os.close(self.root_fd)
        st = os.fstat(self.writer.root_fd)
        self.storage = {
            "root_id": "a" * 32,
            "volume_uuid": "12345678-1234-1234-1234-123456789abc",
            "device": st.st_dev,
            "inode": st.st_ino,
        }
        self.coordinator = capacity.CapacityCoordinator(
            self.writer,
            source=_CapacitySource(self.writer.root_fd),
            service_roots=SERVICE_ROOTS,
            service_lock_paths=LOCK_PATHS,
            allocation_roots=ALLOCATIONS,
            root_binding=self.storage,
            now_ns=lambda: COORDINATOR_TIME_NS,
        )
        self.coordinator.initialize()
        self.policy = _policy()
        self.journal = DescriptorScoutJournal(
            self.writer,
            root=RELEASE_ROOT,
            policy=self.policy,
            storage_binding=self.storage,
            release_sha256=hashlib.sha256(b"test-release").hexdigest(),
        )
        self.journal.initialize(first_bucket_close_ns=FIRST_CLOSE_NS)
        self.capture_writer = DescriptorCaptureWriter(
            self.writer, root=RELEASE_ROOT, binding=self.journal.binding
        )

    def close(self) -> None:
        self.writer.close()


@pytest.fixture
def fixture(tmp_path: Path) -> Any:
    value = _Fixture(tmp_path / "descriptor-root")
    try:
        yield value
    finally:
        value.close()


def _expected_prefix(fixture: _Fixture) -> str:
    inventory = build_usage_inventory(fixture.writer)
    snapshot = next(row for row in inventory["snapshots"] if row["root"] == RELEASE_ROOT)
    assert snapshot["status"] == "verified_none"
    return snapshot["source_prefix_sha256"]


def _clock(at_ns: int, monotonic_ns: int) -> dict[str, Any]:
    base_ns = at_ns - 2 * scout.NS
    date_ns = base_ns // scout.NS * scout.NS
    rows = []
    for index, host in enumerate(("www.cloudflare.com", "www.google.com")):
        sent = date_ns - scout.NS + index * scout.NS
        rows.append(
            {
                "host": host,
                "status": 200,
                "date": dt.datetime.fromtimestamp(date_ns // scout.NS + index, tz=dt.UTC).strftime(
                    "%a, %d %b %Y %H:%M:%S GMT"
                ),
                "age": None,
                "sent_ns": sent,
                "received_ns": sent + scout.NS,
                "monotonic_sent_ns": monotonic_ns - 3 * scout.NS + index * scout.NS,
                "monotonic_received_ns": monotonic_ns - 2 * scout.NS + index * scout.NS,
            }
        )
    return {
        "schema": "brain-senses-external-v1-clock",
        "schema_version": 1,
        "samples": rows,
        "checked_ns": rows[-1]["received_ns"],
        "monotonic_ns": rows[-1]["monotonic_received_ns"],
    }


def test_lane_then_root_lock_pins_census_before_attempt_directory_creation(
    fixture: _Fixture,
) -> None:
    expected = _expected_prefix(fixture)
    with fixture.coordinator.lane_lock("gecko") as lane:
        with fixture.capture_writer.runner_lock(
            coordinator=fixture.coordinator,
            lane_lease=lane,
            expected_prefix_sha256=expected,
        ) as runner:
            fixture.capture_writer.initialize_attempt_directories(runner)
            assert "clocks" not in fixture.writer.list_names(RELEASE_ROOT)
            assert fixture.writer.list_names(f"{RELEASE_ROOT}/results") == ()


def test_v2_writer_refuses_changed_prefix_after_inventory_census(fixture: _Fixture) -> None:
    expected = _expected_prefix(fixture)
    fixture.writer.create(f"{RELEASE_ROOT}/unexpected.json", b"{}")
    with fixture.coordinator.lane_lock("gecko") as lane:
        with pytest.raises(CaptureWriterError, match="prefix_changed_after_census"):
            with fixture.capture_writer.runner_lock(
                coordinator=fixture.coordinator,
                lane_lease=lane,
                expected_prefix_sha256=expected,
            ):
                pytest.fail("changed root must not obtain a writer lease")


def test_v2_writer_refuses_busy_root_without_lane_lock_bypass(fixture: _Fixture) -> None:
    expected = _expected_prefix(fixture)
    with fixture.writer.lock(f"{RELEASE_ROOT}/.runner.lock", blocking=False):
        with fixture.coordinator.lane_lock("gecko") as lane:
            with pytest.raises(CaptureWriterError, match="root_busy"):
                with fixture.capture_writer.runner_lock(
                    coordinator=fixture.coordinator,
                    lane_lease=lane,
                    expected_prefix_sha256=expected,
                ):
                    pytest.fail("contended root must abort before a write")


def test_synthetic_intent_dispatch_outcome_settlement_and_confirmation_replay(
    fixture: _Fixture,
) -> None:
    close_ns = FIRST_CLOSE_NS
    plan = fixture.journal.ensure_plan_for_current_bucket(
        now_ns=close_ns + 70 * scout.NS,
        bucket_close_ns=close_ns,
    )
    request = verifier.expected_request(
        fixture.policy, fixture.journal.binding["binding_sha256"], plan, 0
    )
    decision_ns = close_ns + 70 * scout.NS
    inventory = build_usage_inventory(fixture.writer)
    roots = [row["root"] for row in inventory["snapshots"]]
    admission = assess_usage(
        inventory["snapshots"],
        expected_roots=roots,
        checked_ns=decision_ns,
        prospective_request_id=request["request_id"],
        prospective_maximum_response_bytes=scout.MAX_RESPONSE_BYTES,
    )
    intent = verifier.build_intent(
        root=RELEASE_ROOT,
        policy=fixture.policy,
        binding=fixture.journal.binding,
        plan_record=plan,
        slot_index=0,
        decision_ns=decision_ns,
        decision_monotonic_ns=80 * scout.NS,
        clock_record=_clock(decision_ns, 80 * scout.NS),
        admission=admission,
    )
    expected_prefix = next(
        row["source_prefix_sha256"] for row in inventory["snapshots"] if row["root"] == RELEASE_ROOT
    )
    with fixture.coordinator.lane_lock("gecko") as lane:
        with fixture.capture_writer.runner_lock(
            coordinator=fixture.coordinator,
            lane_lease=lane,
            expected_prefix_sha256=expected_prefix,
        ) as runner:
            fixture.capture_writer.initialize_attempt_directories(runner)
            reservation = fixture.capture_writer.reserve_intent(
                runner,
                coordinator=fixture.coordinator,
                lane_lease=lane,
                intent=intent,
            )
            dispatch = fixture.capture_writer.append_dispatch(
                runner,
                request_id=request["request_id"],
                intent_sha256=intent["intent_sha256"],
                dispatch_ns=decision_ns + scout.NS,
            )
            body = b'{"data":{"attributes":{"ohlcv_list":[]}}}'
            outcome = {
                "schema": verifier.TERMINAL_SCHEMA,
                "schema_version": 1,
                "request_id": request["request_id"],
                "intent_sha256": intent["intent_sha256"],
                "plan_sha256": request["plan_record_sha256"],
                "dispatch_record_sha256": dispatch["record_sha256"],
                "dispatch_ns": dispatch["dispatch_ns"],
                "request_started_ns": dispatch["dispatch_ns"],
                "receipt_ns": dispatch["dispatch_ns"] + scout.NS,
                "status": "empty_response",
                "http_status": 200,
                "transport_error_class": None,
                "retry_after_seconds": None,
                "cooldown_until_ns": None,
                "stop_reason": None,
                "provider_io": True,
                "raw_body_b64": base64.b64encode(body).decode("ascii"),
                "response_bytes": len(body),
                "response_sha256": hashlib.sha256(body).hexdigest(),
                "bars": [],
                "provider_charge_units": None,
                "provider_charge_status": "unknown_unverified",
            }
            terminal_event = fixture.capture_writer.settle_outcome(
                runner,
                coordinator=fixture.coordinator,
                lane_lease=lane,
                reservation=reservation,
                outcome=outcome,
                ambiguous=False,
            )
            availability_ns = outcome["receipt_ns"] + 4 * scout.NS
            confirmation = fixture.capture_writer.write_confirmation(
                runner,
                request_id=request["request_id"],
                outcome_sha256=hashlib.sha256(scout.canonical_json(outcome)).hexdigest(),
                availability_ns=availability_ns,
                availability_monotonic_ns=90 * scout.NS,
                availability_clock=_clock(availability_ns, 90 * scout.NS),
                coordinator_terminal=terminal_event,
            )
    assert confirmation["coordinator_event_sha256"] == terminal_event["event_sha256"]
    assert "clocks" not in fixture.writer.list_names(RELEASE_ROOT)
    replay = build_usage_inventory(fixture.writer)
    snapshot = next(row for row in replay["snapshots"] if row["root"] == RELEASE_ROOT)
    assert snapshot["status"] == "verified_prefix"
    assert snapshot["attempts"][0]["response_bytes"] == len(body)


def test_forced_detach_during_intent_publication_never_writes_replacement_mount(
    tmp_path: Path,
) -> None:
    mount = tmp_path / "mounted-root"
    detached = tmp_path / "detached-root"
    detached_once = False

    def detach_at_intent_create(operation: str) -> None:
        nonlocal detached_once
        if operation != "atomic_create" or detached_once:
            return
        detached_once = True
        os.rename(mount, detached)
        mount.mkdir(mode=0o700)
        sentinel = mount / "internal-fallback-sentinel"
        sentinel.write_text("leave untouched")
        sentinel.chmod(0o600)

    fixture = _Fixture(mount, fault_hook=detach_at_intent_create)
    try:
        close_ns = FIRST_CLOSE_NS
        plan = fixture.journal.ensure_plan_for_current_bucket(
            now_ns=close_ns + 70 * scout.NS,
            bucket_close_ns=close_ns,
        )
        request = verifier.expected_request(
            fixture.policy, fixture.journal.binding["binding_sha256"], plan, 0
        )
        decision_ns = close_ns + 70 * scout.NS
        inventory = build_usage_inventory(fixture.writer)
        roots = [row["root"] for row in inventory["snapshots"]]
        admission = assess_usage(
            inventory["snapshots"],
            expected_roots=roots,
            checked_ns=decision_ns,
            prospective_request_id=request["request_id"],
            prospective_maximum_response_bytes=scout.MAX_RESPONSE_BYTES,
        )
        intent = verifier.build_intent(
            root=RELEASE_ROOT,
            policy=fixture.policy,
            binding=fixture.journal.binding,
            plan_record=plan,
            slot_index=0,
            decision_ns=decision_ns,
            decision_monotonic_ns=80 * scout.NS,
            clock_record=_clock(decision_ns, 80 * scout.NS),
            admission=admission,
        )
        prefix = next(
            row["source_prefix_sha256"]
            for row in inventory["snapshots"]
            if row["root"] == RELEASE_ROOT
        )
        with fixture.coordinator.lane_lock("gecko") as lane:
            with fixture.capture_writer.runner_lock(
                coordinator=fixture.coordinator,
                lane_lease=lane,
                expected_prefix_sha256=prefix,
            ) as runner:
                fixture.capture_writer.initialize_attempt_directories(runner)
                fixture.capture_writer.reserve_intent(
                    runner,
                    coordinator=fixture.coordinator,
                    lane_lease=lane,
                    intent=intent,
                )

        assert detached_once
        held_intent = detached / RELEASE_ROOT / "intents" / f"{request['request_id']}.json"
        assert held_intent.is_file()
        assert list(mount.iterdir()) == [mount / "internal-fallback-sentinel"]
        assert not (mount / RELEASE_ROOT).exists()
    finally:
        fixture.close()
