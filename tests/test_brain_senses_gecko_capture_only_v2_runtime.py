from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

from tools import brain_senses_capacity_coordinator_v2 as capacity  # noqa: E402
from tools import brain_senses_gecko_capture_only_v2_host as host_release  # noqa: E402
from tools import brain_senses_gecko_capture_only_v2_scheduler as scheduler  # noqa: E402
from tools import brain_senses_gecko_capture_only_v2_service as service  # noqa: E402
from tools import brain_senses_gecko_capture_only_v2_verify as verifier  # noqa: E402
from tools import brain_senses_gecko_rotating_scout_v1 as scout  # noqa: E402
from tools import brain_senses_gecko_scout_chain_v1 as chain  # noqa: E402
from tools import brain_senses_gecko_shared_usage_v1 as shared_usage  # noqa: E402
from tools import brain_senses_gecko_usage_inventory_v1 as usage_inventory  # noqa: E402
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
                "retry_after_header_count": 0,
                "retry_after_evidence": [],
                "retry_after_class": "absent",
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


def test_scheduler_consumes_one_fixed_slot_per_callback_and_never_replaces(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path / "gecko-v2-scheduler")
    try:
        close_ns = FIRST_CLOSE_NS
        plan = fixture.journal.ensure_plan_for_current_bucket(
            now_ns=close_ns + 70 * scout.NS,
            bucket_close_ns=close_ns,
        )
        assert scheduler.due_slot(plan, [], [], now_ns=close_ns + 60 * scout.NS) == 0
        first_intent = {
            "request": {
                "plan_sequence": plan["sequence"],
                "slot_index": 0,
                "pool_id": plan["selection"]["selected_pool_ids"][0],
            }
        }
        assert scheduler.due_slot(plan, [first_intent], [], now_ns=close_ns + 72 * scout.NS) == 1
        first_skip = {
            "schema": scheduler.SKIP_SCHEMA,
            "plan_sequence": plan["sequence"],
            "slot_index": 1,
            "pool_id": plan["selection"]["selected_pool_ids"][1],
            "bucket_close_ns": close_ns,
            "at_ns": close_ns + 270 * scout.NS,
            "reason": "missed_dispatch",
        }
        assert (
            scheduler.due_slot(plan, [first_intent], [first_skip], now_ns=close_ns + 84 * scout.NS)
            == 2
        )
        assert scheduler.validate_slot_state(plan, [first_intent], [first_skip])
    finally:
        fixture.close()


def test_scheduler_window_boundaries_and_explicit_terminal_misses(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path / "gecko-v2-scheduler-window")
    try:
        close_ns = FIRST_CLOSE_NS
        plan = fixture.journal.ensure_plan_for_current_bucket(
            now_ns=close_ns + 70 * scout.NS,
            bucket_close_ns=close_ns,
        )
        assert scheduler.due_slot(plan, [], [], now_ns=close_ns + 60 * scout.NS - 1) is None
        assert scheduler.due_slot(plan, [], [], now_ns=close_ns + 270 * scout.NS) is None
        skips = scheduler.expired_slot_skips(plan, [], [], now_ns=close_ns + 270 * scout.NS)
        assert [row["slot_index"] for row in skips] == list(range(scout.SLOTS_PER_BUCKET))
        assert all(row["reason"] == "missed_dispatch" for row in skips)
        assert scheduler.due_slot(plan, [], skips, now_ns=close_ns + 271 * scout.NS) is None
    finally:
        fixture.close()


def test_writer_persists_expired_miss_under_descriptor_bound_runner_lock(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path / "gecko-v2-skip-writer")
    try:
        close_ns = FIRST_CLOSE_NS
        plan = fixture.journal.ensure_plan_for_current_bucket(
            now_ns=close_ns + 70 * scout.NS,
            bucket_close_ns=close_ns,
        )
        prefix = fixture.capture_writer.prefix_sha256()
        with fixture.coordinator.lane_lock("gecko", blocking=False) as lane:
            with fixture.capture_writer.runner_lock(
                coordinator=fixture.coordinator,
                lane_lease=lane,
                expected_prefix_sha256=prefix,
            ) as runner:
                fixture.capture_writer.initialize_attempt_directories(runner)
                record = fixture.capture_writer.append_missed_skip(
                    runner,
                    plan=plan,
                    slot_index=0,
                    at_ns=close_ns + 270 * scout.NS,
                )
        path = fixture.capture_writer._path(f"skips/{plan['sequence']}-00.json")
        assert json.loads(fixture.writer.read_bytes(path)) == record
        assert scheduler.due_slot(plan, [], [record], now_ns=close_ns + 271 * scout.NS) is None
    finally:
        fixture.close()


def test_pacing_binds_cutover_quiet_time_spacing_and_rolling_minute() -> None:
    cutover = 1_800_000_000_000_000_000
    assert not scheduler.dispatch_allowed(
        cutover + 59 * scout.NS, [], cutover_last_dispatch_ns=cutover
    )
    assert scheduler.dispatch_allowed(
        cutover + 60 * scout.NS, [], cutover_last_dispatch_ns=cutover
    )
    prior = [
        {"dispatch_ns": cutover + 60 * scout.NS + index * 12 * scout.NS}
        for index in range(5)
    ]
    assert scheduler.dispatch_allowed(
        cutover + 60 * scout.NS + 60 * scout.NS,
        prior,
        cutover_last_dispatch_ns=cutover,
    )
    assert not scheduler.dispatch_allowed(
        prior[-1]["dispatch_ns"] + 11 * scout.NS,
        prior,
        cutover_last_dispatch_ns=cutover,
    )


def test_expired_plan_slots_become_explicit_misses_without_provider_io(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path / "gecko-v2-expired-slots")
    try:
        close_ns = FIRST_CLOSE_NS
        plan = fixture.journal.ensure_plan_for_current_bucket(
            now_ns=close_ns + 70 * scout.NS,
            bucket_close_ns=close_ns,
        )
        count = host_release.mark_expired_slots(
            fixture.writer,
            coordinator=fixture.coordinator,
            capture_writer=fixture.capture_writer,
            plan=plan,
            now_ns=close_ns + 270 * scout.NS,
        )
        assert count == scout.SLOTS_PER_BUCKET
        inventory = build_usage_inventory(fixture.writer)
        capture = next(row for row in inventory["snapshots"] if row["root"] == RELEASE_ROOT)
        assert capture["status"] == "verified_none"
        final_state = host_release.read_capture_state(fixture.writer, RELEASE_ROOT)
        assert len(final_state["skips"]) == scout.SLOTS_PER_BUCKET
        assert not final_state["dispatches"]
    finally:
        fixture.close()


def test_host_release_initialization_is_inert_and_inventory_visible(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path / "gecko-v2-host-release")
    try:
        now_ns = time.time_ns()
        first_close = (now_ns // scout.BUCKET_NS + 1) * scout.BUCKET_NS
        receipt = host_release.initialize_release(
            fixture.writer,
            policy=fixture.policy,
            release_id="b" * 32,
            release_sha256="b" * 64,
            source_sha256="c" * 64,
            first_bucket_close_ns=first_close,
            coordinator=fixture.coordinator,
            now_ns=lambda: now_ns,
            monotonic_ns=lambda: time.monotonic_ns(),
        )
        assert receipt["provider_io"] is False
        assert receipt["activation_authorized"] is False
        inventory = build_usage_inventory(fixture.writer)
        roots = {row["root"]: row for row in inventory["snapshots"]}
        assert roots[receipt["root"]]["status"] == "verified_none"
        assert roots[receipt["health_root"]]["status"] == "verified_none"
        assert host_release.read_capture_state(fixture.writer, receipt["root"])[
            "dispatches"
        ] == []
        release_journal = DescriptorScoutJournal(
            fixture.writer,
            root=receipt["root"],
            policy=fixture.policy,
            storage_binding=fixture.storage,
            release_sha256="b" * 64,
        )
        release_writer = DescriptorCaptureWriter(
            fixture.writer, root=receipt["root"], binding=release_journal.binding
        )
        state = host_release.read_capture_state(fixture.writer, receipt["root"])
        refreshed = host_release.publish_health(
            fixture.writer,
            release={"release_id": receipt["release_id"], "binding": release_journal.binding},
            coordinator=fixture.coordinator,
            capture_writer=release_writer,
            plan=release_journal.records()[0],
            state=state,
            inventory=None,
            status="paused",
            reason="outside_window",
            now_ns=lambda: now_ns + scout.NS,
            monotonic_ns=lambda: time.monotonic_ns(),
        )
        assert refreshed["generation"] == 1
        assert refreshed["request_prefix_sha256"] == release_writer.prefix_sha256()
        with pytest.raises(host_release.HostReleaseError, match="release_root_already_exists"):
            host_release.initialize_release(
                fixture.writer,
                policy=fixture.policy,
                release_id="b" * 32,
                release_sha256="b" * 64,
                source_sha256="c" * 64,
                first_bucket_close_ns=first_close,
                coordinator=fixture.coordinator,
            )
    finally:
        fixture.close()


def test_release_identity_is_deterministic_and_binds_code_and_source_maps() -> None:
    policy = {"policy_sha256": "a" * 64}
    sources = {"tools/runtime.py": "b" * 64, "tools/selector.py": "c" * 64}
    first = host_release.release_identity(policy=policy, source_bindings=sources)
    second = host_release.release_identity(
        policy=policy, source_bindings=dict(reversed(list(sources.items())))
    )
    changed = host_release.release_identity(
        policy=policy, source_bindings=sources | {"tools/host.py": "d" * 64}
    )
    assert first == second
    assert first[0] == first[1][:32]
    assert first != changed


def test_recovery_gate_stops_after_possible_spend_or_missing_confirmation() -> None:
    base = {"dispatches": [], "results": [], "confirmations": []}
    assert host_release.recovery_gate(base) == "recovered"
    attempted = {"dispatches": [{"request_id": "request"}], "results": [], "confirmations": []}
    assert host_release.recovery_gate(attempted) == "possibly_spent_unresolved"
    terminal = {
        "dispatches": [{"request_id": "request"}],
        "results": [{"request_id": "request"}],
        "confirmations": [],
    }
    assert host_release.recovery_gate(terminal) == "durable_confirmation_missing"
    confirmed = terminal | {"confirmations": [{"request_id": "request"}]}
    assert host_release.recovery_gate(confirmed) == "recovered"


def test_launchd_plist_is_path_only_interval_bound_and_nonexpiring() -> None:
    release_id = "e" * 32
    raw = host_release.render_launchd_plist(release_id)
    document = plistlib.loads(raw)
    assert document["StartInterval"] == 12
    assert document["RunAtLoad"] is True
    assert document["KeepAlive"] is False
    assert document["ProgramArguments"][-2:] == ["--release-id", release_id]
    assert "EnvironmentVariables" not in document
    assert "StartCalendarInterval" not in document
    assert host_release.verify_launchd_plist(raw, release_id) == hashlib.sha256(raw).hexdigest()
    with pytest.raises(host_release.HostReleaseError, match="launchd_plist_binding_changed"):
        tampered = raw.replace(b"<integer>12</integer>", b"<integer>60</integer>")
        host_release.verify_launchd_plist(tampered, release_id)


def test_launchd_entrypoint_imports_without_pythonpath_or_repo_working_directory(
    tmp_path: Path,
) -> None:
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(REPO / "tools/brain_senses_gecko_capture_only_v2_host.py"),
            "--help",
        ],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "--scheduled-tick" in result.stdout


def test_callback_outside_window_is_a_health_only_tick(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path / "gecko-v2-host-callback")
    try:
        now_ns = FIRST_CLOSE_NS + 300 * scout.NS
        receipt = host_release.initialize_release(
            fixture.writer,
            policy=fixture.policy,
            release_id="b" * 32,
            release_sha256="b" * 64,
            source_sha256="c" * 64,
            first_bucket_close_ns=FIRST_CLOSE_NS,
            coordinator=fixture.coordinator,
            now_ns=lambda: now_ns,
            monotonic_ns=lambda: time.monotonic_ns(),
        )
        journal = DescriptorScoutJournal(
            fixture.writer,
            root=receipt["root"],
            policy=fixture.policy,
            storage_binding=fixture.storage,
            release_sha256="b" * 64,
        )
        release = {
            **receipt,
            "policy": fixture.policy,
            "binding": journal.binding,
            "journal": journal,
            "health": {
                "status": "paused",
                "stop_latched": False,
                "cooldown_until_ns": None,
            },
        }
        called: list[bool] = []
        result = host_release.run_callback(
            fixture.writer,
            release=release,
            coordinator=fixture.coordinator,
            now_ns=lambda: now_ns,
            monotonic_ns=time.monotonic_ns,
            clock_probe=lambda: pytest.fail(
                "outside-window callback must not probe external clocks"
            ),
            fetch_once=lambda _request: called.append(True),
        )
        assert result["status"] == "outside_window"
        assert result["provider_io"] is False
        assert called == []
        assert journal.records()[-1]["schema"] == chain.GAP_SCHEMA
        assert journal.records()[-1]["bucket_close_ns"] == FIRST_CLOSE_NS
    finally:
        fixture.close()


def test_attempt_one_slot_replays_inventory_and_writes_one_mocked_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _Fixture(tmp_path / "gecko-v2-host-attempt")
    try:
        close_ns = FIRST_CLOSE_NS
        plan = fixture.journal.ensure_plan_for_current_bucket(
            now_ns=close_ns + 70 * scout.NS,
            bucket_close_ns=close_ns,
        )
        original_inventory = usage_inventory.build_usage_inventory

        def inventory_with_cutover(writer: DescriptorWriter) -> dict[str, Any]:
            inventory = original_inventory(writer)
            old_root = "services/gecko-market-v1/capture-only-v1/f4412b7a531d4e3cb3414a2862b8ee76"
            root_hash = inventory["root_inventory_sha256"]
            attempts = [
                {
                    "request_id": hashlib.sha256(b"last-v4-attempt").hexdigest(),
                    "decision_ns": host_release.CUTOVER_LAST_DISPATCH_NS,
                    "maximum_response_bytes": scout.MAX_RESPONSE_BYTES,
                    "response_bytes": 100,
                    "response_sha256": hashlib.sha256(b"legacy").hexdigest(),
                }
            ]
            inventory["snapshots"].append(
                shared_usage.make_verified_root_snapshot(
                    root=old_root,
                    status="verified_prefix",
                    root_inventory_sha256=root_hash,
                    source_prefix_sha256=host_release.CUTOVER_PREFIX_SHA256,
                    attempts=attempts,
                )
            )
            inventory["snapshots"].sort(key=lambda row: row["root"])
            return inventory

        monkeypatch.setattr(usage_inventory, "build_usage_inventory", inventory_with_cutover)
        wall = iter(close_ns + value * scout.NS for value in (70, 71, 72, 73, 74, 75, 76, 77))
        mono = iter(value * scout.NS for value in (81, 82, 87))
        called: list[dict[str, Any]] = []

        def fetch(request: Mapping[str, Any]) -> service.FetchResult:
            called.append(dict(request))
            return service.FetchResult(
                200,
                b'{"data":{"attributes":{"ohlcv_list":[]}}}',
                {},
            )

        result = host_release.attempt_one_slot(
            fixture.writer,
            coordinator=fixture.coordinator,
            capture_writer=fixture.capture_writer,
            policy=fixture.policy,
            binding=fixture.journal.binding,
            plan=plan,
            now_ns=lambda: next(wall),
            monotonic_ns=lambda: next(mono),
            clock_probe=lambda: _clock(close_ns + 71 * scout.NS, 81 * scout.NS),
            fetch_once=fetch,
        )
        assert result["status"] == "empty_response"
        assert result["provider_io"] is True
        assert len(called) == 1
        assert called[0]["pool_id"] == plan["selection"]["selected_pool_ids"][0]
        replay = original_inventory(fixture.writer)
        capture = next(row for row in replay["snapshots"] if row["root"] == RELEASE_ROOT)
        assert capture["status"] == "verified_prefix"
        assert len(capture["attempts"]) == 1
    finally:
        fixture.close()


def test_rate_limit_is_durable_bucket_stop_after_capture_writer_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _Fixture(tmp_path / "gecko-v2-rate-limit-restart")
    try:
        close_ns = FIRST_CLOSE_NS
        plan = fixture.journal.ensure_plan_for_current_bucket(
            now_ns=close_ns + 70 * scout.NS,
            bucket_close_ns=close_ns,
        )
        original_inventory = usage_inventory.build_usage_inventory

        def inventory_with_cutover(writer: DescriptorWriter) -> dict[str, Any]:
            inventory = original_inventory(writer)
            inventory["snapshots"].append(
                shared_usage.make_verified_root_snapshot(
                    root="services/gecko-market-v1/capture-only-v1/f4412b7a531d4e3cb3414a2862b8ee76",
                    status="verified_prefix",
                    root_inventory_sha256=inventory["root_inventory_sha256"],
                    source_prefix_sha256=host_release.CUTOVER_PREFIX_SHA256,
                    attempts=[
                        {
                            "request_id": hashlib.sha256(b"last-v4-attempt").hexdigest(),
                            "decision_ns": host_release.CUTOVER_LAST_DISPATCH_NS,
                            "maximum_response_bytes": scout.MAX_RESPONSE_BYTES,
                            "response_bytes": 100,
                            "response_sha256": hashlib.sha256(b"legacy").hexdigest(),
                        }
                    ],
                )
            )
            inventory["snapshots"].sort(key=lambda row: row["root"])
            return inventory

        monkeypatch.setattr(usage_inventory, "build_usage_inventory", inventory_with_cutover)
        wall = iter(close_ns + value * scout.NS for value in (70, 71, 72, 73, 74, 75, 76, 77))
        first = host_release.attempt_one_slot(
            fixture.writer,
            coordinator=fixture.coordinator,
            capture_writer=fixture.capture_writer,
            policy=fixture.policy,
            binding=fixture.journal.binding,
            plan=plan,
            now_ns=lambda: next(wall),
            monotonic_ns=iter(
                (81 * scout.NS, 82 * scout.NS, 87 * scout.NS)
            ).__next__,
            clock_probe=lambda: _clock(close_ns + 71 * scout.NS, 81 * scout.NS),
            fetch_once=lambda _request: service.FetchResult(429, None, {}),
        )
        assert first["status"] == "rate_limited"
        assert first["cooldown_until_ns"] == close_ns + 3676 * scout.NS

        restarted_writer = DescriptorCaptureWriter(
            fixture.writer, root=RELEASE_ROOT, binding=fixture.journal.binding
        )
        calls: list[bool] = []
        second = host_release.attempt_one_slot(
            fixture.writer,
            coordinator=fixture.coordinator,
            capture_writer=restarted_writer,
            policy=fixture.policy,
            binding=fixture.journal.binding,
            plan=plan,
            now_ns=lambda: close_ns + 100 * scout.NS,
            monotonic_ns=lambda: 200 * scout.NS,
            clock_probe=lambda: pytest.fail("durable bucket stop must precede new clock probe"),
            fetch_once=lambda _request: calls.append(True),
        )
        assert second["status"] == "bucket_stopped"
        assert second["provider_io"] is False
        assert calls == []

        probe_close = close_ns + 13 * scout.BUCKET_NS
        probe_plan = fixture.journal.ensure_plan_for_current_bucket(
            now_ns=probe_close + 70 * scout.NS,
            bucket_close_ns=probe_close,
        )
        probe_wall = iter(
            probe_close + value * scout.NS for value in (70, 71, 72, 73, 74, 75, 76, 77)
        )
        probe_mono = iter((81 * scout.NS, 82 * scout.NS, 87 * scout.NS))
        probe_calls: list[dict[str, Any]] = []

        def successful_probe(request: Mapping[str, Any]) -> service.FetchResult:
            probe_calls.append(dict(request))
            return service.FetchResult(
                200,
                b'{"data":{"attributes":{"ohlcv_list":[]}}}',
                {},
            )

        probe = host_release.attempt_one_slot(
            fixture.writer,
            coordinator=fixture.coordinator,
            capture_writer=restarted_writer,
            policy=fixture.policy,
            binding=fixture.journal.binding,
            plan=probe_plan,
            cooldown_until_ns=first["cooldown_until_ns"],
            now_ns=lambda: next(probe_wall),
            monotonic_ns=lambda: next(probe_mono),
            clock_probe=lambda: _clock(probe_close + 71 * scout.NS, 81 * scout.NS),
            fetch_once=successful_probe,
        )
        assert probe["status"] == "empty_response"
        assert probe["request_id"] != first["request_id"]
        assert len(probe_calls) == 1

        after_probe = host_release.attempt_one_slot(
            fixture.writer,
            coordinator=fixture.coordinator,
            capture_writer=DescriptorCaptureWriter(
                fixture.writer, root=RELEASE_ROOT, binding=fixture.journal.binding
            ),
            policy=fixture.policy,
            binding=fixture.journal.binding,
            plan=probe_plan,
            cooldown_until_ns=first["cooldown_until_ns"],
            now_ns=lambda: probe_close + 100 * scout.NS,
            monotonic_ns=lambda: 200 * scout.NS,
            clock_probe=lambda: pytest.fail("only one post-cooldown probe is allowed per bucket"),
            fetch_once=lambda _request: calls.append(True),
        )
        assert after_probe["status"] == "bucket_stopped"
        assert after_probe["stop_reason"] == "recovery_probe_already_used"
        assert calls == []
    finally:
        fixture.close()
