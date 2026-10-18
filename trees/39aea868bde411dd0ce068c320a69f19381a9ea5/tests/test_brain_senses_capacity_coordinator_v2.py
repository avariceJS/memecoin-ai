from __future__ import annotations

import multiprocessing
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from memecoin_research_storage import StorageError
from tools import brain_senses_capacity_coordinator_v2 as coordinator_module
from tools.brain_senses_external_writer_v1 import DescriptorWriter

SERVICE_ROOTS = {
    "helius": "services/helius-v5",
    "alchemy": "services/alchemy-sentinel-v2",
    "gecko": "services/gecko-market-v1",
}
ALLOCATION_ROOTS = {
    "helius": ("services/helius-v4", SERVICE_ROOTS["helius"]),
    "alchemy": (SERVICE_ROOTS["alchemy"],),
    "gecko": (SERVICE_ROOTS["gecko"],),
    "coordinator": (coordinator_module.COORDINATOR_ROOT,),
}
ALL_ROOTS = {**SERVICE_ROOTS, "coordinator": coordinator_module.COORDINATOR_ROOT}
SERVICE_LOCK_PATHS = {
    "helius": ".external-rolling-v4.lock",
    "alchemy": ".alchemy-sentinel-v2.lock",
    "gecko": ".gecko-market-v1.lock",
}
VOLUME_UUID = "12345678-1234-1234-1234-123456789abc"


class FakeSource:
    def __init__(self, root_fd: int, *, free: int = 300_000_000_000) -> None:
        st = os.fstat(root_fd)
        self.identity = (st.st_dev, st.st_ino)
        self.free = free
        self.alive = True
        self.inventory_override: dict[str, int] = {name: 0 for name in ALL_ROOTS}
        self.use_physical = False

    def verify_root(self, writer: coordinator_module.Writer) -> None:
        st = os.fstat(writer.root_fd)
        if not self.alive or (st.st_dev, st.st_ino) != self.identity:
            raise coordinator_module.CoordinatorError("root_identity_lost")

    def allocated_bytes(self, writer: coordinator_module.Writer, service: str) -> int:
        if self.use_physical:
            return sum(
                coordinator_module.physical_inventory_bytes(writer, path)
                for path in ALLOCATION_ROOTS[service]
            )
        return self.inventory_override[service]

    def free_bytes(self, _writer: coordinator_module.Writer) -> int:
        return self.free


class Fixture:
    def __init__(self, path: Path) -> None:
        path.mkdir(parents=True, mode=0o700)
        self.path = path
        (path / "services").mkdir(mode=0o700)
        for relative in {path for paths in ALLOCATION_ROOTS.values() for path in paths}:
            (path / relative).mkdir(mode=0o700)
        self.root_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        self.writer = DescriptorWriter(self.root_fd)
        self.source = FakeSource(self.root_fd)
        st = os.fstat(self.root_fd)
        self.root_binding = {
            "root_id": "b" * 32,
            "volume_uuid": VOLUME_UUID,
            "device": st.st_dev,
            "inode": st.st_ino,
        }
        self.counter = 1_900_000_000_000_000_000

        def now() -> int:
            self.counter += 1
            return self.counter

        self.coordinator = coordinator_module.CapacityCoordinator(
            self.writer,
            source=self.source,
            service_roots=SERVICE_ROOTS,
            service_lock_paths=SERVICE_LOCK_PATHS,
            allocation_roots=ALLOCATION_ROOTS,
            root_binding=self.root_binding,
            now_ns=now,
        )
        self.coordinator.initialize()

    def close(self) -> None:
        self.writer.close()
        os.close(self.root_fd)


def _fixture(tmp_path: Path) -> Fixture:
    return Fixture(tmp_path / "kingston-root")


def _reservation(
    fixture: Fixture,
    *,
    reservation_id: str | None = None,
    request_id: str = "q-1",
    payload: bytes = b'{"decision_ns":1,"method":"getBlock"}\n',
    reserve_bytes: int = 4_000_000,
    lane_lease: coordinator_module.LaneLockLease | None = None,
    service: str = "alchemy",
) -> coordinator_module.Reservation:
    intent_path = f"{SERVICE_ROOTS[service]}/intents/{request_id}.json"
    outcome_path = f"{SERVICE_ROOTS[service]}/receipts/{request_id}.json"
    reservation_id = reservation_id or f"{service}-{request_id}"

    def reserve(lease: coordinator_module.LaneLockLease) -> coordinator_module.Reservation:
        return fixture.coordinator.reserve_and_persist_intent(
            service=service,
            reservation_id=reservation_id,
            request_id=request_id,
            intent_path=intent_path,
            outcome_path=outcome_path,
            intent_bytes=payload,
            intent_sha256=coordinator_module.sha256(payload),
            reserve_bytes=reserve_bytes,
            lane_lease=lease,
        )

    if lane_lease is not None:
        return reserve(lane_lease)
    with fixture.coordinator.lane_lock(service) as acquired_lease:
        return reserve(acquired_lease)


def _events(fixture: Fixture) -> list[dict[str, Any]]:
    raw = fixture.writer.read_bytes(coordinator_module.LEDGER_PATH)
    return coordinator_module._verify_ledger(raw)


def test_initialize_policy_is_create_only_and_ledger_has_canonical_genesis(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    try:
        assert (
            fixture.writer.read_bytes(coordinator_module.POLICY_PATH)
            == fixture.coordinator.policy_bytes
        )
        stored_policy = coordinator_module._decode(fixture.coordinator.policy_bytes)
        assert stored_policy["root_binding"] == fixture.root_binding
        assert stored_policy["service_lock_paths"] == SERVICE_LOCK_PATHS
        assert stored_policy["allocation_roots"] == {
            key: list(paths) for key, paths in ALLOCATION_ROOTS.items()
        }
        assert [event["kind"] for event in _events(fixture)] == ["genesis"]
        with pytest.raises(coordinator_module.CoordinatorError, match="policy_drift"):
            fixture.writer.atomic_write(coordinator_module.POLICY_PATH, b"{}\n", create_only=False)
            fixture.coordinator.initialize()
    finally:
        fixture.close()


def test_initialize_descriptor_creates_configured_service_roots(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    try:
        missing_root = SERVICE_ROOTS["gecko"]
        (fixture.path / missing_root).rmdir()
        fixture.coordinator.initialize()
        assert (fixture.path / missing_root).is_dir()
        assert [event["kind"] for event in _events(fixture)] == ["genesis"]
    finally:
        fixture.close()


def test_service_and_allocation_roots_must_be_disjoint(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    try:
        bad_sets = [
            ({**SERVICE_ROOTS, "alchemy": SERVICE_ROOTS["helius"]}, None),
            (
                {**SERVICE_ROOTS, "helius": "services/senses-capacity-v2/helius"},
                None,
            ),
            (
                SERVICE_ROOTS,
                {
                    **ALLOCATION_ROOTS,
                    "helius": ("services/helius-v4", SERVICE_ROOTS["alchemy"]),
                },
            ),
        ]
        for service_roots, allocation_roots in bad_sets:
            with pytest.raises(coordinator_module.CoordinatorError):
                coordinator_module.CapacityCoordinator(
                    fixture.writer,
                    source=fixture.source,
                    service_roots=service_roots,
                    service_lock_paths=SERVICE_LOCK_PATHS,
                    allocation_roots=allocation_roots,
                    root_binding=fixture.root_binding,
                    now_ns=time.time_ns,
                )
    finally:
        fixture.close()


def test_lane_paths_reject_traversal_and_noncanonical_components() -> None:
    root = SERVICE_ROOTS["helius"]
    for candidate in (
        f"{root}/../helius-v4/secret.json",
        f"{root}/./intents/frame.json",
        f"{root}//intents/frame.json",
    ):
        with pytest.raises(coordinator_module.CoordinatorError, match="lane_path_outside"):
            coordinator_module.CapacityCoordinator._validate_lane_path("helius", root, candidate)


def test_gecko_atomic_intent_callback_fails_closed_without_partial_final_file(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    try:
        path = f"{SERVICE_ROOTS['gecko']}/intents/atomic-gecko.json"
        outcome_path = f"{SERVICE_ROOTS['gecko']}/results/atomic-gecko.json"
        payload = b'{"schema":"atomic-test","request_id":"atomic-gecko"}'

        def detach_at_create(operation: str) -> None:
            if operation == "atomic_create":
                raise OSError("simulated detach at atomic publication")

        fixture.writer._fault_hook = detach_at_create
        with fixture.coordinator.lane_lock("gecko") as lease:
            with pytest.raises(
                coordinator_module.CoordinatorError, match="lane_intent_persistence_failed"
            ):
                fixture.coordinator.reserve_and_persist_intent(
                    service="gecko",
                    reservation_id="atomic-reservation",
                    request_id="atomic-gecko",
                    intent_path=path,
                    outcome_path=outcome_path,
                    intent_bytes=payload,
                    intent_sha256=coordinator_module.sha256(payload),
                    reserve_bytes=1_000_000,
                    lane_lease=lease,
                    persist_intent=lambda writer, target, body: writer.atomic_write(
                        target, body, create_only=True
                    ),
                )
        assert not fixture.writer.exists(path)
        assert not fixture.writer.exists(outcome_path)
        _, events = fixture.coordinator._load_ledger()
        reservation, terminal = coordinator_module._reservation_state(events)
        assert "atomic-reservation" in reservation
        assert terminal == {"atomic-reservation"}
        final = [
            row for row in events if row["fields"].get("reservation_id") == "atomic-reservation"
        ][-1]
        assert final["kind"] == "released"
        assert final["fields"]["outcome"] == "pre_intent_absent"
    finally:
        fixture.close()


def test_service_recovery_accepts_existing_lane_lease(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    try:
        reservation = _reservation(fixture, request_id="recover-under-lease", service="gecko")
        with fixture.coordinator.lane_lock("gecko") as lease:
            recovered = fixture.coordinator.recover_service(
                "gecko",
                intent_exists=lambda writer, path: writer.exists(path),
                lane_lease=lease,
            )
        assert recovered == {"released_pre_intent": 0, "ambiguous_terminal": 1}
        events = _events(fixture)
        terminal = next(
            row
            for row in events
            if row["kind"] == "ambiguous_terminal"
            and row["fields"].get("reservation_id") == reservation.reservation_id
        )
        assert terminal["fields"]["outcome"] == "intent_exists_after_recovery"
    finally:
        fixture.close()


def test_ledger_terminal_must_bind_predeclared_outcome_path(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    try:
        reservation = _reservation(fixture)
        wrong_path = f"{SERVICE_ROOTS['alchemy']}/receipts/other.json"
        wrong_bytes = b'{"status":"misbound"}\n'
        fixture.writer.create(wrong_path, wrong_bytes)
        with fixture.coordinator._locks("alchemy"):
            raw, events = fixture.coordinator._load_ledger()
            fixture.coordinator._append_event(
                raw,
                events,
                "ambiguous_terminal",
                {
                    "reservation_id": reservation.reservation_id,
                    "outcome": "recovered_outcome_artifact",
                    "outcome_sha256": coordinator_module.sha256(wrong_bytes),
                    "outcome_path": wrong_path,
                    "actual_allocated_bytes": 0,
                },
            )
        with pytest.raises(
            coordinator_module.CoordinatorError, match="terminal_outcome_path_mismatch"
        ):
            fixture.coordinator._load_ledger()
    finally:
        fixture.close()


def test_helius_allocation_inventory_aggregates_v4_and_v5_once(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    try:
        for path in ("services/helius-v4/legacy.bin", "services/helius-v5/current.bin"):
            fixture.writer.create(path, b"x" * 8192)
        fixture.source.use_physical = True
        expected = sum(
            coordinator_module.physical_inventory_bytes(fixture.writer, path)
            for path in ALLOCATION_ROOTS["helius"]
        )
        assert fixture.source.allocated_bytes(fixture.writer, "helius") == expected
        capacity_source = coordinator_module.DescriptorCapacitySource(
            allocation_roots=ALLOCATION_ROOTS,
            verify_root=fixture.source.verify_root,
            free_bytes=lambda _writer: fixture.source.free,
        )
        assert capacity_source.allocated_bytes(fixture.writer, "helius") == expected
        assert expected == sum(
            coordinator_module.physical_inventory_bytes(fixture.writer, path)
            for path in ("services/helius-v4", "services/helius-v5")
        )
    finally:
        fixture.close()


def test_capacity_equality_boundaries_are_inclusive_and_next_byte_fails(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    try:
        fixture.source.inventory_override["alchemy"] = (
            coordinator_module._SERVICE_CAPS["alchemy"] - 10
        )
        reservation = _reservation(fixture, reserve_bytes=10)
        assert reservation.reserved_bytes == 10
        with pytest.raises(coordinator_module.CoordinatorError, match="service_request_id_reused"):
            _reservation(
                fixture,
                reservation_id="r-duplicate-request",
                request_id="q-1",
                reserve_bytes=1,
            )
        with pytest.raises(coordinator_module.CoordinatorError, match="alchemy_capacity_exceeded"):
            _reservation(fixture, reservation_id="r-2", request_id="q-2", reserve_bytes=1)
    finally:
        fixture.close()


def test_settlement_replaces_reservation_with_actual_allocation_once(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    try:
        cap = coordinator_module._SERVICE_CAPS["alchemy"]
        fixture.source.inventory_override["alchemy"] = cap - 10_000_000
        reservation = _reservation(fixture, reserve_bytes=10_000_000)
        fixture.source.inventory_override["alchemy"] = cap - 1_000_000
        terminal = fixture.coordinator.settle(
            reservation,
            outcome="captured",
            outcome_bytes=b'{"status":"captured"}\n',
            outcome_path=reservation.outcome_path,
            ambiguous=False,
        )
        assert terminal == "settled"
        settled = _events(fixture)[-1]
        assert settled["fields"]["actual_allocated_bytes"] == cap - 1_000_000
    finally:
        fixture.close()


def test_actual_allocation_over_cap_fails_closed_after_reservation(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    try:
        cap = coordinator_module._SERVICE_CAPS["alchemy"]
        fixture.source.inventory_override["alchemy"] = cap - 10_000_000
        reservation = _reservation(fixture, reserve_bytes=10_000_000)
        fixture.source.inventory_override["alchemy"] = cap + 1
        with pytest.raises(coordinator_module.CoordinatorError, match="alchemy_capacity_exceeded"):
            fixture.coordinator.settle(
                reservation,
                outcome="captured",
                outcome_bytes=b'{"status":"captured"}\n',
                outcome_path=reservation.outcome_path,
                ambiguous=False,
            )
    finally:
        fixture.close()


def test_exact_intent_preimage_is_required_before_reservation(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    try:
        payload = b'{"request_id":"q-preimage"}\n'
        with pytest.raises(coordinator_module.CoordinatorError, match="intent_preimage_invalid"):
            fixture.coordinator.reserve_and_persist_intent(
                service="alchemy",
                reservation_id="r-preimage",
                request_id="q-preimage",
                intent_path=f"{SERVICE_ROOTS['alchemy']}/intents/q-preimage.json",
                outcome_path=f"{SERVICE_ROOTS['alchemy']}/receipts/q-preimage.json",
                intent_bytes=payload,
                intent_sha256="0" * 64,
                reserve_bytes=1,
            )
        assert [event["kind"] for event in _events(fixture)] == ["genesis"]
    finally:
        fixture.close()


def test_reservation_requires_caller_held_lane_lease(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    try:
        payload = b'{"request_id":"q-no-lease"}\n'
        with pytest.raises(coordinator_module.CoordinatorError, match="lane_lock_lease_required"):
            fixture.coordinator.reserve_and_persist_intent(
                service="alchemy",
                reservation_id="r-no-lease",
                request_id="q-no-lease",
                intent_path=f"{SERVICE_ROOTS['alchemy']}/intents/q-no-lease.json",
                outcome_path=f"{SERVICE_ROOTS['alchemy']}/receipts/q-no-lease.json",
                intent_bytes=payload,
                intent_sha256=coordinator_module.sha256(payload),
                reserve_bytes=1024,
            )
        assert [event["kind"] for event in _events(fixture)] == ["genesis"]
    finally:
        fixture.close()


def test_fresh_free_boundary_and_exactly_once_helius_cap(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    try:
        protected = (
            sum(coordinator_module._FIXED_RESERVES.values())
            + 4_000_000
            + 2 * coordinator_module.COORDINATOR_APPEND_BLOCK_BYTES
        )
        assert coordinator_module._SERVICE_CAPS["helius"] == 150_000_000_000
        assert coordinator_module._SERVICE_CAPS["gecko"] == 16_000_000_000
        assert coordinator_module._FIXED_RESERVES["helius_next_epoch"] == 4_000_000_000
        fixture.source.free = protected
        _reservation(fixture)
        fixture2 = _fixture(tmp_path / "second")
        try:
            fixture2.source.free = protected - 1
            with pytest.raises(
                coordinator_module.CoordinatorError, match="shared_free_reserve_insufficient"
            ):
                _reservation(fixture2)
        finally:
            fixture2.close()
    finally:
        fixture.close()


def test_coordinator_ledger_cap_is_reserved_for_reserved_and_terminal_records(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    try:
        service_lock_allocation = sum(
            fixture.writer.stat(path).st_blocks * 512 for path in SERVICE_LOCK_PATHS.values()
        )
        fixture.source.inventory_override["coordinator"] = (
            coordinator_module._SERVICE_CAPS["coordinator"] - 8_192 - service_lock_allocation
        )
        _reservation(fixture)
        with pytest.raises(
            coordinator_module.CoordinatorError, match="coordinator_capacity_exceeded"
        ):
            _reservation(fixture, reservation_id="r-2", request_id="q-2")
    finally:
        fixture.close()


def test_every_service_inventory_must_fit_its_own_cap_before_any_lane_dispatch(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    try:
        fixture.source.inventory_override["helius"] = coordinator_module._SERVICE_CAPS["helius"] + 1
        with pytest.raises(coordinator_module.CoordinatorError, match="helius_capacity_exceeded"):
            _reservation(fixture)
    finally:
        fixture.close()


def test_root_loss_and_corrupt_ledger_fail_closed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    try:
        fixture.source.alive = False
        with pytest.raises(coordinator_module.CoordinatorError, match="root_identity_lost"):
            _reservation(fixture)
        fixture.source.alive = True
        bad_binding = dict(fixture.root_binding)
        bad_binding["inode"] += 1
        with pytest.raises(coordinator_module.CoordinatorError, match="root_binding_mismatch"):
            coordinator_module.CapacityCoordinator(
                fixture.writer,
                source=fixture.source,
                service_roots=SERVICE_ROOTS,
                service_lock_paths=SERVICE_LOCK_PATHS,
                allocation_roots=ALLOCATION_ROOTS,
                root_binding=bad_binding,
                now_ns=time.time_ns,
            )
        fixture.writer.append(coordinator_module.LEDGER_PATH, b"{torn")
        with pytest.raises(coordinator_module.CoordinatorError, match="torn_final_record"):
            _reservation(fixture)
    finally:
        fixture.close()


def test_lane_intent_is_exact_create_only_and_terminal_settlement_follows_outcome(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    try:
        reservation = _reservation(fixture)
        assert fixture.writer.read_bytes(reservation.intent_path) == reservation.intent_bytes
        outcome = b'{"availability_ns":3,"status":"captured"}\n'
        lock_order: list[str] = []
        original_lock = fixture.writer.lock

        @contextmanager
        def tracked_lock(relative: str = ".writer.lock", *, blocking: bool = False):
            with original_lock(relative, blocking=blocking):
                lock_order.append(relative)
                yield

        fixture.writer.lock = tracked_lock  # type: ignore[method-assign]
        terminal = fixture.coordinator.settle(
            reservation,
            outcome="captured",
            outcome_bytes=outcome,
            outcome_path=reservation.outcome_path,
            ambiguous=False,
        )
        assert terminal == "settled"
        assert lock_order == [
            SERVICE_LOCK_PATHS["alchemy"],
            coordinator_module.LOCK_PATH,
            coordinator_module.LOCK_PATH,
        ]
        assert fixture.writer.read_bytes(f"{SERVICE_ROOTS['alchemy']}/receipts/q-1.json") == outcome
        assert [event["kind"] for event in _events(fixture)] == [
            "genesis",
            "reserved",
            "settled",
        ]
        with pytest.raises(coordinator_module.CoordinatorError, match="already_terminal"):
            fixture.coordinator.settle(
                reservation,
                outcome="duplicate",
                outcome_bytes=b"duplicate\n",
                outcome_path=reservation.outcome_path,
                ambiguous=False,
            )
        ambiguous_reservation = _reservation(
            fixture, reservation_id="r-ambiguous", request_id="q-ambiguous"
        )
        ambiguous_outcome = b'{"certainty":"possibly_spent"}\n'
        assert (
            fixture.coordinator.settle(
                ambiguous_reservation,
                outcome="uncertain_provider_result",
                outcome_bytes=ambiguous_outcome,
                outcome_path=ambiguous_reservation.outcome_path,
                ambiguous=True,
            )
            == "ambiguous_terminal"
        )
        with pytest.raises(coordinator_module.CoordinatorError, match="service_request_id_reused"):
            _reservation(
                fixture,
                reservation_id="r-ambiguous-retry",
                request_id="q-ambiguous",
            )
    finally:
        fixture.close()


def test_replay_rejects_missing_or_drifted_intent_and_outcome_artifacts(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    try:
        reservation = _reservation(fixture)
        fixture.writer.atomic_write(reservation.intent_path, b"mutated intent\n", create_only=False)
        with pytest.raises(coordinator_module.CoordinatorError, match="intent_artifact_drift"):
            _reservation(fixture, reservation_id="r-intent-drift", request_id="q-intent-drift")

        fixture.writer.atomic_write(
            reservation.intent_path, reservation.intent_bytes, create_only=False
        )
        outcome_path = reservation.outcome_path
        outcome = b'{"status":"captured"}\n'
        fixture.coordinator.settle(
            reservation,
            outcome="captured",
            outcome_bytes=outcome,
            outcome_path=outcome_path,
            ambiguous=False,
        )
        fixture.writer.atomic_write(outcome_path, b"mutated outcome\n", create_only=False)
        with pytest.raises(coordinator_module.CoordinatorError, match="outcome_artifact_drift"):
            _reservation(fixture, reservation_id="r-outcome-drift", request_id="q-outcome-drift")

        fixture.writer.atomic_write(outcome_path, outcome, create_only=False)
        os.unlink(fixture.path / outcome_path)
        with pytest.raises(coordinator_module.CoordinatorError, match="outcome_artifact_missing"):
            _reservation(
                fixture, reservation_id="r-outcome-missing", request_id="q-outcome-missing"
            )

        fixture.writer.create(outcome_path, outcome)
        os.unlink(fixture.path / reservation.intent_path)
        with pytest.raises(
            coordinator_module.CoordinatorError, match="outcome_artifact_without_intent"
        ):
            _reservation(fixture, reservation_id="r-intent-missing", request_id="q-intent-missing")
    finally:
        fixture.close()


def test_caller_held_lane_lock_serializes_provider_gap_without_shared_lock(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    try:
        provider_called = False
        with fixture.coordinator.lane_lock("alchemy") as lease:
            reservation = _reservation(fixture, lane_lease=lease)

            def provider_callback() -> None:
                nonlocal provider_called
                assert lease.active
                with fixture.writer.lock(coordinator_module.LOCK_PATH, blocking=False):
                    provider_called = True

            provider_callback()
            assert provider_called
            outcome = b'{"availability_ns":42,"status":"captured"}\n'
            assert (
                fixture.coordinator.settle(
                    reservation,
                    outcome="captured",
                    outcome_bytes=outcome,
                    outcome_path=reservation.outcome_path,
                    ambiguous=False,
                    lane_lease=lease,
                )
                == "settled"
            )
        assert not lease.active
    finally:
        fixture.close()


def test_nonblocking_lane_lock_fails_instead_of_queuing_callback(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    try:
        with fixture.coordinator.lane_lock("gecko"):
            with pytest.raises(StorageError):
                with fixture.coordinator.lane_lock("gecko", blocking=False):
                    pytest.fail("a second Gecko callback must not queue or enter")
    finally:
        fixture.close()


@pytest.mark.parametrize(
    ("first_service", "second_service"), [("helius", "gecko"), ("gecko", "helius")]
)
def test_cross_service_pending_reservation_protects_shared_free_margin(
    tmp_path: Path, first_service: str, second_service: str
) -> None:
    fixture = _fixture(tmp_path / f"{first_service}-then-{second_service}")
    try:
        reserve_bytes = 4_000_000
        append_reserve = 2 * coordinator_module.COORDINATOR_APPEND_BLOCK_BYTES
        fixture.source.free = (
            sum(coordinator_module._FIXED_RESERVES.values()) + reserve_bytes + append_reserve
        )
        first = _reservation(
            fixture,
            reservation_id=f"{first_service}-first",
            request_id=f"{first_service}-request",
            reserve_bytes=reserve_bytes,
            service=first_service,
        )
        assert first.service == first_service
        with pytest.raises(
            coordinator_module.CoordinatorError, match="shared_free_reserve_insufficient"
        ):
            _reservation(
                fixture,
                reservation_id=f"{second_service}-second",
                request_id=f"{second_service}-request",
                reserve_bytes=reserve_bytes,
                service=second_service,
            )
        events = _events(fixture)
        assert [event["fields"]["service"] for event in events if event["kind"] == "reserved"] == [
            first_service
        ]
    finally:
        fixture.close()


def test_recovery_waits_for_caller_held_lane_call_to_exit(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    try:
        with fixture.coordinator.lane_lock("alchemy") as lease:
            _reservation(
                fixture,
                reservation_id="r-provider-gap",
                request_id="q-provider-gap",
                lane_lease=lease,
            )
            started = threading.Event()
            completed = threading.Event()
            result: list[dict[str, int]] = []

            def recover() -> None:
                started.set()
                result.append(
                    fixture.coordinator.recover(
                        intent_exists=lambda writer, path: writer.exists(path)
                    )
                )
                completed.set()

            worker = threading.Thread(target=recover, daemon=True)
            worker.start()
            assert started.wait(timeout=2)
            assert not completed.wait(timeout=0.15)
        worker.join(timeout=3)
        assert not worker.is_alive()
        assert completed.is_set()
        assert result == [{"released_pre_intent": 0, "ambiguous_terminal": 1}]
    finally:
        fixture.close()


def _orphan_reservation(fixture: Fixture, *, with_intent: bool) -> str:
    suffix = "existing" if with_intent else "absent"
    reservation_id = f"crash-r-{suffix}"
    request_id = f"crash-q-{suffix}"
    path = f"{SERVICE_ROOTS['alchemy']}/intents/{request_id}.json"
    intent = f'{{"method":"getBlock","request_id":"{request_id}"}}\n'.encode()
    with fixture.coordinator._locks("alchemy"):
        raw, events = fixture.coordinator._load_ledger()
        fixture.coordinator._append_event(
            raw,
            events,
            "reserved",
            {
                "reservation_id": reservation_id,
                "service": "alchemy",
                "request_id": request_id,
                "intent_path": path,
                "outcome_path": f"{SERVICE_ROOTS['alchemy']}/receipts/{request_id}.json",
                "intent_sha256": coordinator_module.sha256(intent),
                "reserved_bytes": 1_000_000,
                "intent_bytes_sha256": coordinator_module.sha256(intent),
                "allocated_before_bytes": 0,
            },
        )
        if with_intent:
            fixture.writer.create(path, intent)
    return path


def test_recovery_releases_only_absent_pre_intent_and_never_replays_existing_intent(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    try:
        absent_path = _orphan_reservation(fixture, with_intent=False)
        lock_order: list[str] = []
        original_lock = fixture.writer.lock

        @contextmanager
        def tracked_lock(relative: str = ".writer.lock", *, blocking: bool = False):
            with original_lock(relative, blocking=blocking):
                lock_order.append(relative)
                yield

        fixture.writer.lock = tracked_lock  # type: ignore[method-assign]
        result = fixture.coordinator.recover(intent_exists=lambda writer, path: writer.exists(path))
        assert lock_order == [SERVICE_LOCK_PATHS[name] for name in sorted(SERVICE_LOCK_PATHS)] + [
            coordinator_module.LOCK_PATH
        ]
        assert fixture.writer.exists(SERVICE_LOCK_PATHS["helius"])
        assert not fixture.writer.exists(f"{SERVICE_ROOTS['helius']}/.capacity-service.lock")
        assert result == {"released_pre_intent": 1, "ambiguous_terminal": 0}
        assert [event["kind"] for event in _events(fixture)][-1] == "released"

        existing_path = _orphan_reservation(fixture, with_intent=True)
        lock_order.clear()
        result = fixture.coordinator.recover(intent_exists=lambda writer, path: writer.exists(path))
        assert result == {"released_pre_intent": 0, "ambiguous_terminal": 1}
        assert [event["kind"] for event in _events(fixture)][-1] == "ambiguous_terminal"
        assert fixture.writer.exists(existing_path)
        assert not fixture.writer.exists(absent_path)
    finally:
        fixture.close()


def test_recovery_links_outcome_persisted_before_terminal_append(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    try:
        with fixture.coordinator.lane_lock("alchemy") as lease:
            reservation = _reservation(
                fixture,
                reservation_id="r-crash-after-outcome",
                request_id="q-crash-after-outcome",
                lane_lease=lease,
            )
            outcome_bytes = b'{"status":"captured-before-crash"}\n'
            fixture.writer.create(reservation.outcome_path, outcome_bytes)

        intent_checks: list[str] = []

        def observe_intent(writer: coordinator_module.Writer, path: str) -> bool:
            intent_checks.append(path)
            return writer.exists(path)

        result = fixture.coordinator.recover(intent_exists=observe_intent)
        assert result == {"released_pre_intent": 0, "ambiguous_terminal": 1}
        assert intent_checks == [reservation.intent_path]
        terminal = _events(fixture)[-1]
        assert terminal["kind"] == "ambiguous_terminal"
        assert terminal["fields"]["outcome_path"] == reservation.outcome_path
        assert terminal["fields"]["outcome_sha256"] == coordinator_module.sha256(outcome_bytes)
        assert fixture.writer.read_bytes(terminal["fields"]["outcome_path"]) == outcome_bytes
        assert fixture.coordinator.recover(intent_exists=observe_intent) == {
            "released_pre_intent": 0,
            "ambiguous_terminal": 0,
        }
        assert len(_events(fixture)) == 3
    finally:
        fixture.close()


def test_service_recovery_is_independent_of_other_lane_locks_and_bytes(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    try:
        _reservation(fixture, request_id="q-helius-pending", service="helius")
        gecko = _reservation(fixture, request_id="q-gecko-pending", service="gecko")
        gecko_bytes = fixture.writer.read_bytes(gecko.intent_path)
        completed = threading.Event()
        helius_result: list[dict[str, int]] = []

        with fixture.coordinator.lane_lock("gecko"):
            thread = threading.Thread(
                target=lambda: (
                    helius_result.append(
                        fixture.coordinator.recover_service(
                            "helius",
                            intent_exists=lambda writer, path: writer.exists(path),
                        )
                    ),
                    completed.set(),
                ),
                daemon=True,
            )
            thread.start()
            assert completed.wait(timeout=2)
            thread.join(timeout=2)
            assert not thread.is_alive()
            assert fixture.writer.read_bytes(gecko.intent_path) == gecko_bytes

        assert helius_result == [{"released_pre_intent": 0, "ambiguous_terminal": 1}]
        assert fixture.coordinator.recover_service(
            "helius", intent_exists=lambda writer, path: writer.exists(path)
        ) == {"released_pre_intent": 0, "ambiguous_terminal": 0}

        helius2 = _reservation(fixture, request_id="q-helius-held", service="helius")
        gecko2 = _reservation(fixture, request_id="q-gecko-target", service="gecko")
        helius_bytes = fixture.writer.read_bytes(helius2.intent_path)
        gecko_result: list[dict[str, int]] = []
        with fixture.coordinator.lane_lock("helius"):
            thread = threading.Thread(
                target=lambda: gecko_result.append(
                    fixture.coordinator.recover_service(
                        "gecko", intent_exists=lambda writer, path: writer.exists(path)
                    )
                ),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=2)
            assert not thread.is_alive()
            assert fixture.writer.read_bytes(helius2.intent_path) == helius_bytes
        assert gecko_result == [{"released_pre_intent": 0, "ambiguous_terminal": 2}]
        assert fixture.writer.read_bytes(gecko2.intent_path) == gecko.intent_bytes
    finally:
        fixture.close()


def test_service_recovery_releases_only_absent_target_and_detects_orphan_outcome(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    try:
        target = _reservation(fixture, request_id="q-target-absent", service="helius")
        other = _reservation(fixture, request_id="q-other-pending", service="gecko")
        other_intent_bytes = fixture.writer.read_bytes(other.intent_path)
        os.unlink(fixture.path / target.intent_path)
        assert fixture.coordinator.recover_service(
            "helius", intent_exists=lambda writer, path: writer.exists(path)
        ) == {"released_pre_intent": 1, "ambiguous_terminal": 0}
        assert fixture.coordinator.recover_service(
            "helius", intent_exists=lambda writer, path: writer.exists(path)
        ) == {"released_pre_intent": 0, "ambiguous_terminal": 0}
        assert fixture.writer.read_bytes(other.intent_path) == other_intent_bytes
        assert any(
            event["kind"] == "released"
            and event["fields"]["reservation_id"] == target.reservation_id
            for event in _events(fixture)
        )

        orphan = _reservation(fixture, request_id="q-orphan-outcome", service="helius")
        os.unlink(fixture.path / orphan.intent_path)
        fixture.writer.create(orphan.outcome_path, b"orphan receipt\n")
        with pytest.raises(
            coordinator_module.CoordinatorError, match="outcome_artifact_without_intent"
        ):
            fixture.coordinator.recover_service(
                "helius", intent_exists=lambda writer, path: writer.exists(path)
            )
    finally:
        fixture.close()


def test_service_recovery_fails_closed_on_global_ledger_corruption(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    try:
        _reservation(fixture, request_id="q-global-corrupt", service="helius")
        fixture.writer.append(coordinator_module.LEDGER_PATH, b"{\n")
        with pytest.raises(coordinator_module.CoordinatorError, match="ledger_json_invalid"):
            fixture.coordinator.recover_service(
                "helius", intent_exists=lambda writer, path: writer.exists(path)
            )
    finally:
        fixture.close()


def test_all_v2_mutators_refuse_after_v3_pointer_without_side_effects(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    try:
        reservation = _reservation(fixture, request_id="pre-cutover")
        before_ledger = fixture.writer.read_bytes(coordinator_module.LEDGER_PATH)
        before_intent = fixture.writer.read_bytes(reservation.intent_path)

        fixture.writer.mkdir("services/senses-capacity-v3")
        fixture.writer.create(coordinator_module.V3_CUTOVER_POINTER_PATH, b'{"pointer":true}\n')

        with pytest.raises(
            coordinator_module.CoordinatorError,
            match="v2_mutation_disabled_after_v3_cutover",
        ):
            _reservation(fixture, request_id="post-cutover")

        with pytest.raises(
            coordinator_module.CoordinatorError,
            match="v2_mutation_disabled_after_v3_cutover",
        ):
            fixture.coordinator.settle(
                reservation,
                outcome="captured",
                outcome_bytes=b'{"ok":true}\n',
                outcome_path=reservation.outcome_path,
                ambiguous=False,
            )

        with pytest.raises(
            coordinator_module.CoordinatorError,
            match="v2_mutation_disabled_after_v3_cutover",
        ):
            fixture.coordinator.recover_service(
                "alchemy", intent_exists=lambda writer, path: writer.exists(path)
            )

        with pytest.raises(
            coordinator_module.CoordinatorError,
            match="v2_mutation_disabled_after_v3_cutover",
        ):
            fixture.coordinator.initialize()

        assert fixture.writer.read_bytes(coordinator_module.LEDGER_PATH) == before_ledger
        assert fixture.writer.read_bytes(reservation.intent_path) == before_intent
        assert not fixture.writer.exists(reservation.outcome_path)
        assert not fixture.writer.exists(f"{SERVICE_ROOTS['alchemy']}/intents/post-cutover.json")
    finally:
        fixture.close()


def _contending_reserve(root_path: str, n: int, barrier: Any, results: Any) -> None:
    root_fd = os.open(root_path, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(root_fd)
    os.close(root_fd)
    source = FakeSource(writer.root_fd)
    source.use_physical = True
    root_stat = os.fstat(writer.root_fd)
    coordinator = coordinator_module.CapacityCoordinator(
        writer,
        source=source,
        service_roots=SERVICE_ROOTS,
        service_lock_paths=SERVICE_LOCK_PATHS,
        allocation_roots=ALLOCATION_ROOTS,
        root_binding={
            "root_id": "b" * 32,
            "volume_uuid": VOLUME_UUID,
            "device": root_stat.st_dev,
            "inode": root_stat.st_ino,
        },
        now_ns=time.time_ns,
    )
    path = f"{SERVICE_ROOTS['alchemy']}/intents/parallel-{n}.json"
    intent = f'{{"parallel":{n}}}\n'.encode()
    barrier.wait(timeout=10)
    try:
        with coordinator.lane_lock("alchemy") as lease:
            coordinator.reserve_and_persist_intent(
                service="alchemy",
                reservation_id=f"parallel-r-{n}",
                request_id=f"parallel-{n}",
                intent_path=path,
                outcome_path=f"{SERVICE_ROOTS['alchemy']}/receipts/parallel-{n}.json",
                intent_bytes=intent,
                intent_sha256=coordinator_module.sha256(intent),
                reserve_bytes=150_000_000,
                lane_lease=lease,
            )
        results.put("reserved")
    except coordinator_module.CoordinatorError as exc:
        results.put(str(exc))
    finally:
        writer.close()


def test_multiprocess_race_shares_one_atomic_service_cap(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    try:
        ctx = multiprocessing.get_context("fork")
        barrier = ctx.Barrier(3)
        results = ctx.Queue()
        processes = [
            ctx.Process(target=_contending_reserve, args=(str(fixture.path), n, barrier, results))
            for n in range(2)
        ]
        for process in processes:
            process.start()
        barrier.wait(timeout=10)
        for process in processes:
            process.join(timeout=15)
            assert process.exitcode == 0
        observed = sorted([results.get(timeout=3), results.get(timeout=3)])
        assert observed == ["alchemy_capacity_exceeded", "reserved"]
        events = _events(fixture)
        assert sum(event["kind"] == "reserved" for event in events) == 1
        assert sum(event["kind"] == "released" for event in events) == 0
    finally:
        fixture.close()
