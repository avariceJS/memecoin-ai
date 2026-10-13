from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import brain_senses_gecko_new_pool_discovery_host_v2 as host
from tools import brain_senses_gecko_new_pool_discovery_runtime_v2 as runtime
from tools import brain_senses_gecko_new_pool_discovery_v1 as discovery
from tools.brain_senses_external_writer_v1 import DescriptorWriter

UUID = "00000000-0000-0000-0000-000000000001"
NS = 1_000_000_000


class _Guard:
    def __init__(self, mount_path: Path, root: Path, binding: dict):
        self.config = SimpleNamespace(mount_path=mount_path, data_root=Path("data"))
        self.root_id = binding["root_id"]
        self.volume_uuid = binding["volume_uuid"]
        self._identity = (binding["device"], binding["inode"])

    def revalidate(self):
        path = self.config.mount_path / self.config.data_root
        current = path.stat()
        if (current.st_dev, current.st_ino) != self._identity:
            raise RuntimeError("bound root changed")


def _writer(tmp_path: Path, *, fault_hook=None):
    mount = tmp_path / "mount"
    root = mount / "data"
    root.mkdir(parents=True, mode=0o700)
    os.chmod(root, 0o700)
    st = root.stat()
    binding = {
        "root_id": "a" * 32,
        "volume_uuid": UUID,
        "device": st.st_dev,
        "inode": st.st_ino,
    }
    guard = _Guard(mount, root, binding)
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        writer = DescriptorWriter(root_fd, fault_hook=fault_hook)
    finally:
        os.close(root_fd)
    return writer, guard, binding, root


def _install_epoch_handoff_fixture(writer, guard, binding, policy, epoch):
    pointer = host._active_epoch_core(
        release_id=epoch["release_id"],
        epoch=epoch,
        generation=0,
        previous_pointer_sha256=None,
        transition_sha256=None,
    )
    host._create_json(writer, host.ACTIVE_EPOCH_PATH, pointer)
    host._create_epoch_standby(
        writer=writer,
        guard=guard,
        binding=binding,
        policy=policy,
        epoch=epoch,
        prepared_at_ns=epoch["epoch_start_ns"] - 120 * NS,
        predecessor_frontier=None,
    )
    next_index = 1
    next_policy = discovery.compile_policy(
        first_scan_ns=epoch["epoch_end_ns"],
        scan_interval_seconds=300,
        pages=3,
        page_spacing_seconds=12,
        sample_numerator=0,
        sample_denominator=1,
        source_spec_sha256=epoch["source_sha256"],
        max_scan_count=864,
        max_raw_bytes_per_page=policy["caps"]["max_raw_bytes_per_page"],
    )
    seed = f"{epoch['release_id']}:epoch:{next_index}"
    prospective = runtime.epoch_envelope(
        release_id=epoch["release_id"],
        lease_id=runtime.sha256(f"{seed}:lease".encode())[:32],
        root_id=runtime.sha256(f"{seed}:root".encode())[:32],
        release_sha256=epoch["release_sha256"],
        source_sha256=epoch["source_sha256"],
        policy=next_policy,
        epoch_index=next_index,
        epoch_start_ns=epoch["epoch_end_ns"],
        previous_epoch_sha256=None,
        lifetime_requests_before=epoch["lifetime_requests_before"],
        lifetime_physical_before=epoch["lifetime_physical_before"],
        prior_allocated_physical=epoch["prior_allocated_physical"],
        prior_pending_physical=epoch["prior_pending_physical"],
    )
    gap = host._record_unplanned_gap(
        writer,
        guard,
        binding,
        epoch_index=0,
        scan_index=0,
        scheduled_ns=epoch["epoch_start_ns"],
        observed_ns=epoch["epoch_start_ns"] + discovery.SCAN_EXPIRY_NS,
    )
    frontier = host._frontier_from_rows(
        [
            {
                "epoch_index": 0,
                "terminal_scan_index": 0,
                "kind": "gap",
                "artifact_sha256": runtime.sha256(runtime.canonical(gap)),
            }
        ]
    )
    return host._create_epoch_standby(
        writer=writer,
        guard=guard,
        binding=binding,
        policy=next_policy,
        epoch=prospective,
        prepared_at_ns=epoch["epoch_end_ns"] - 10 * 60 * NS,
        predecessor_frontier=frontier,
        predecessor_epoch=epoch,
    )


def test_unplanned_expired_scan_is_null_identity_gap_without_provider_io(tmp_path):
    writer, guard, binding, root = _writer(tmp_path)
    try:
        gap = host._record_unplanned_gap(
            writer,
            guard,
            binding,
            epoch_index=2,
            scan_index=11,
            scheduled_ns=1_800_000_000_000_000_000,
            observed_ns=1_800_000_061_000_000_000,
        )
        path = root / "services/gecko-new-pool-discovery-v1/runtime-v2/epochs/000002/gaps/0011.json"
        assert json.loads(path.read_text()) == gap
        assert gap["selected_pool_ids"] is None
        assert gap["exposure"] == "unknown"
        assert gap["provider_io"] is False
        assert gap["cursor_advanced"] is False
    finally:
        writer.close()


def test_frontier_rejects_terminal_evidence_after_an_interior_hole(tmp_path):
    writer, guard, binding, _root = _writer(tmp_path)
    first_scan_ns = 1_800_000_000 * NS
    policy = discovery.compile_policy(
        first_scan_ns=first_scan_ns,
        scan_interval_seconds=300,
        pages=3,
        page_spacing_seconds=12,
        sample_numerator=0,
        sample_denominator=1,
        source_spec_sha256="b" * 64,
        max_scan_count=864,
    )
    epoch = runtime.epoch_envelope(
        release_id="c" * 32,
        lease_id="e" * 32,
        root_id="f" * 32,
        release_sha256="d" * 64,
        source_sha256="b" * 64,
        policy=policy,
        epoch_index=0,
        epoch_start_ns=first_scan_ns,
        previous_epoch_sha256=None,
        lifetime_requests_before=0,
        lifetime_physical_before=0,
        prior_allocated_physical=0,
        prior_pending_physical=0,
    )
    try:
        for scan_index in (0, 2):
            scheduled_ns = first_scan_ns + scan_index * 300 * NS
            host._record_unplanned_gap(
                writer,
                guard,
                binding,
                epoch_index=0,
                scan_index=scan_index,
                scheduled_ns=scheduled_ns,
                observed_ns=scheduled_ns + discovery.SCAN_EXPIRY_NS,
            )
        with pytest.raises(host.DiscoveryHostError, match="frontier_interior_missing"):
            host._terminal_frontier_rows(writer, policy, epoch)
    finally:
        writer.close()


def test_source_manifest_pins_descriptor_writer_implementation_closure():
    assert "src/memecoin_research_storage/live_writer.py" in host.SOURCE_PATHS
    assert "tools/brain_senses_external_schema_v1.py" in host.SOURCE_PATHS
    assert host.SOURCE_MANIFEST_SCHEMA.endswith("source-manifest-v3")


def test_gap_write_after_root_replacement_never_falls_back_to_underlay(tmp_path):
    mount = tmp_path / "mount"
    root = mount / "data"
    moved = mount / "held-data"
    detached = False

    def detach(operation: str):
        nonlocal detached
        if operation == "create" and root.exists() and not detached:
            detached = True
            os.rename(root, moved)
            root.mkdir(mode=0o700)

    writer, guard, binding, _root = _writer(tmp_path, fault_hook=detach)
    try:
        with pytest.raises(host.DiscoveryHostError, match="revalidation"):
            host._record_unplanned_gap(
                writer,
                guard,
                binding,
                epoch_index=0,
                scan_index=0,
                scheduled_ns=1_800_000_000_000_000_000,
                observed_ns=1_800_000_061_000_000_000,
            )
        assert not [path for path in root.rglob("*") if path.is_file()]
        assert (
            moved / "services/gecko-new-pool-discovery-v1/runtime-v2/epochs/000000/gaps/0000.json"
        ).is_file()
    finally:
        writer.close()


def test_restart_seals_elapsed_unplanned_slots_as_idempotent_unknown_gaps(tmp_path):
    writer, guard, binding, root = _writer(tmp_path)
    first_scan = 1_800_000_000 * NS
    policy = discovery.compile_policy(
        first_scan_ns=first_scan,
        scan_interval_seconds=300,
        pages=3,
        page_spacing_seconds=12,
        sample_numerator=1,
        sample_denominator=1,
        source_spec_sha256="b" * 64,
        max_scan_count=864,
    )
    epoch = runtime.epoch_envelope(
        release_id="c" * 32,
        lease_id="d" * 32,
        root_id="e" * 32,
        release_sha256="f" * 64,
        source_sha256="b" * 64,
        policy=policy,
        epoch_index=0,
        epoch_start_ns=first_scan,
        previous_epoch_sha256=None,
        lifetime_requests_before=0,
        lifetime_physical_before=0,
        prior_allocated_physical=0,
        prior_pending_physical=0,
    )
    try:
        now = first_scan + 3 * 300 * NS + 2 * 60 * NS
        gaps = host._close_elapsed_gaps(
            writer=writer,
            guard=guard,
            binding=binding,
            policy=policy,
            epoch=epoch,
            through_scan_index=3,
            now_ns=now,
        )
        assert [gap["scan_index"] for gap in gaps] == [0, 1, 2]
        assert all(gap["selected_pool_ids"] is None for gap in gaps)
        assert all(gap["exposure"] == "unknown" for gap in gaps)
        repeated = host._close_elapsed_gaps(
            writer=writer,
            guard=guard,
            binding=binding,
            policy=policy,
            epoch=epoch,
            through_scan_index=3,
            now_ns=now + 1,
        )
        assert repeated == gaps
        assert not list((root / "services").rglob("page-*.intent.json"))
    finally:
        writer.close()


class _CapacityRecovery:
    def __init__(self, state: str):
        self.state = state
        self.settlements = []

    def reservation_status(self, **_kwargs):
        if self.state == "reserved":
            return {"state": "reserved", "tail_at_ns": NS * 100}
        return {"state": self.state, "tail_at_ns": NS * 100}

    def settle(self, **kwargs):
        self.settlements.append(kwargs)


def test_reservation_without_intent_is_released_once_and_slot_is_missed(tmp_path):
    writer, guard, binding, root = _writer(tmp_path)
    capacity = _CapacityRecovery("reserved")
    request_id = "a" * 64
    plan = {
        "epoch_index": 0,
        "scan_index": 2,
        "expires_ns": NS * 200,
        "page_slots": [{"page": 1, "request_id": request_id}],
    }
    try:
        host._recover_scan(
            writer=writer,
            guard=guard,
            binding=binding,
            plan=plan,
            capacity=capacity,
            release_id="b" * 32,
            now_ns=NS * 150,
        )
        result_path = host._page_path(0, 2, 1, "result.json")
        confirm_path = host._page_path(0, 2, 1, "confirmation.json")
        result = json.loads((root / result_path).read_text())
        confirmation = json.loads((root / confirm_path).read_text())
        assert result["status"] == "missed"
        assert confirmation["provider_io"] is False
        assert confirmation["reservation_id"] == host._reservation_id(request_id, "b" * 32)
        assert len(capacity.settlements) == 1
        assert capacity.settlements[0]["release"] is True
        assert not writer.exists(host._page_path(0, 2, 1, "intent.json"))
        assert not writer.exists(host._page_path(0, 2, 1, "dispatch.json"))
    finally:
        writer.close()


def test_missing_reservation_keeps_live_slot_open(tmp_path):
    writer, guard, binding, _root = _writer(tmp_path)
    capacity = _CapacityRecovery("absent")
    plan = {
        "epoch_index": 0,
        "scan_index": 2,
        "expires_ns": NS * 200,
        "page_slots": [{"page": 1, "request_id": "a" * 64}],
    }
    try:
        host._recover_scan(
            writer=writer,
            guard=guard,
            binding=binding,
            plan=plan,
            capacity=capacity,
            release_id="b" * 32,
            now_ns=NS * 150,
        )
        assert not writer.exists(host._page_path(0, 2, 1, "result.json"))
        assert not capacity.settlements
    finally:
        writer.close()


def test_unresolved_prior_dispatch_blocks_later_page_reservations(tmp_path, monkeypatch):
    writer, guard, binding, root = _writer(tmp_path)
    first_scan_ns = 1_800_000_000 * NS
    policy = discovery.compile_policy(
        first_scan_ns=first_scan_ns,
        scan_interval_seconds=300,
        pages=3,
        page_spacing_seconds=12,
        sample_numerator=0,
        sample_denominator=1,
        source_spec_sha256="b" * 64,
        max_scan_count=864,
    )
    epoch = runtime.epoch_envelope(
        release_id="c" * 32,
        lease_id="e" * 32,
        root_id="f" * 32,
        release_sha256="d" * 64,
        source_sha256=policy["source_spec_sha256"],
        policy=policy,
        epoch_index=0,
        epoch_start_ns=first_scan_ns,
        previous_epoch_sha256=None,
        lifetime_requests_before=0,
        lifetime_physical_before=0,
        prior_allocated_physical=0,
        prior_pending_physical=0,
    )
    monkeypatch.setattr(
        host.v3,
        "_v2_policy",
        lambda _raw: {"service_lock_paths": {"gecko": "services/legacy-gecko/.lane.lock"}},
    )
    writer.create(host.v2.POLICY_PATH, b"fixture")
    now = first_scan_ns + 25 * 1_000_000_000
    plan = runtime.compile_epoch_scan_plan(
        policy=policy,
        epoch=epoch,
        scan_index=0,
        scheduled_ns=first_scan_ns,
    )
    host._save_plan(writer, guard, binding, plan)
    first = plan["page_slots"][0]
    intent_path = host._page_path(0, 0, first["page"], "intent.json")
    dispatch_path = host._page_path(0, 0, first["page"], "dispatch.json")
    reservation_id = host._reservation_id(first["request_id"], epoch["release_id"])
    host._create_json(
        writer,
        intent_path,
        {
            "schema": runtime.PAGE_INTENT_SCHEMA,
            "schema_version": 2,
            "request_id": first["request_id"],
            "reservation_id": reservation_id,
            "scan_index": 0,
            "page": first["page"],
            "planned_dispatch_ns": first["planned_dispatch_ns"],
            "method": "GET",
            "url": first["url"],
            "accept": first["accept"],
            "provider_io_possible": False,
        },
    )
    host._create_json(
        writer,
        dispatch_path,
        {
            "schema": runtime.PAGE_DISPATCH_SCHEMA,
            "schema_version": 2,
            "request_id": first["request_id"],
            "reservation_id": reservation_id,
            "dispatch_ns": first["planned_dispatch_ns"],
            "provider_io_possible": True,
        },
    )

    class CapacityMustNotReserveLaterPage:
        def reservation_status(self, **_kwargs):
            return {"state": "absent", "tail_at_ns": 0}

        def reserve(self, **_kwargs):
            raise AssertionError("a later page was reserved behind unresolved dispatch")

    try:
        result = host.run_scan(
            writer=writer,
            guard=guard,
            binding=binding,
            policy=policy,
            epoch=epoch,
            scan_index=0,
            scheduled_ns=first_scan_ns,
            now_ns=lambda: now,
            fetch_page=lambda _url: (_ for _ in ()).throw(
                AssertionError("provider I/O must not happen behind unresolved dispatch")
            ),
            capacity=CapacityMustNotReserveLaterPage(),
            priority_gate=lambda: False,
        )
        assert result["complete"] is False
        assert result["completed_pages"] == 0
        assert writer.exists(intent_path)
        assert not writer.exists(host._page_path(0, 0, 2, "intent.json"))
        assert not writer.exists(host._page_path(0, 0, 3, "intent.json"))
    finally:
        writer.close()


def test_one_provider_request_is_started_per_scheduled_tick(tmp_path, monkeypatch):
    writer, guard, binding, _root = _writer(tmp_path)
    first_scan_ns = 1_800_000_000 * NS
    policy = discovery.compile_policy(
        first_scan_ns=first_scan_ns,
        scan_interval_seconds=300,
        pages=3,
        page_spacing_seconds=12,
        sample_numerator=0,
        sample_denominator=1,
        source_spec_sha256="b" * 64,
        max_scan_count=864,
    )
    epoch = runtime.epoch_envelope(
        release_id="c" * 32,
        lease_id="e" * 32,
        root_id="f" * 32,
        release_sha256="d" * 64,
        source_sha256=policy["source_spec_sha256"],
        policy=policy,
        epoch_index=0,
        epoch_start_ns=first_scan_ns,
        previous_epoch_sha256=None,
        lifetime_requests_before=0,
        lifetime_physical_before=0,
        prior_allocated_physical=0,
        prior_pending_physical=0,
    )
    monkeypatch.setattr(
        host.v3,
        "_v2_policy",
        lambda _raw: {"service_lock_paths": {"gecko": "services/legacy-gecko/.lane.lock"}},
    )
    monkeypatch.setattr(host.v2, "physical_inventory_bytes", lambda *_a, **_k: 0)
    writer.create(host.v2.POLICY_PATH, b"fixture")
    now = first_scan_ns + NS
    request_log = []
    fetch_log = []

    class Capacity:
        def reservation_status(self, **_kwargs):
            return {"state": "absent", "tail_at_ns": 0}

        def reserve(self, **kwargs):
            request_log.append(kwargs["request_id"])

        def settle(self, **kwargs):
            del kwargs

    try:
        result = host.run_scan(
            writer=writer,
            guard=guard,
            binding=binding,
            policy=policy,
            epoch=epoch,
            scan_index=0,
            scheduled_ns=first_scan_ns,
            now_ns=lambda: now,
            fetch_page=lambda _url: (
                fetch_log.append("request") or (200, b"{}", now)
            ),
            capacity=Capacity(),
            priority_gate=lambda: False,
        )
        assert result["complete"] is False
        assert result["completed_pages"] == 1
        assert len(request_log) == 1
        assert fetch_log == ["request"]
        assert writer.exists(host._page_path(0, 0, 1, "intent.json"))
        assert not writer.exists(host._page_path(0, 0, 2, "intent.json"))
        assert not writer.exists(host._page_path(0, 0, 3, "intent.json"))
    finally:
        writer.close()


def test_owner_fast_path_is_read_only_until_the_next_planned_trigger(tmp_path, monkeypatch):
    writer, guard, binding, root = _writer(tmp_path)
    first_scan_ns = 1_800_000_000 * NS
    policy = discovery.compile_policy(
        first_scan_ns=first_scan_ns,
        scan_interval_seconds=300,
        pages=3,
        page_spacing_seconds=12,
        sample_numerator=0,
        sample_denominator=1,
        source_spec_sha256="b" * 64,
        max_scan_count=864,
    )
    epoch = runtime.epoch_envelope(
        release_id="c" * 32,
        lease_id="e" * 32,
        root_id="f" * 32,
        release_sha256="d" * 64,
        source_sha256=policy["source_spec_sha256"],
        policy=policy,
        epoch_index=0,
        epoch_start_ns=first_scan_ns,
        previous_epoch_sha256=None,
        lifetime_requests_before=0,
        lifetime_physical_before=0,
        prior_allocated_physical=0,
        prior_pending_physical=0,
    )
    pointer = host._active_epoch_core(
        release_id=epoch["release_id"],
        epoch=epoch,
        generation=0,
        previous_pointer_sha256=None,
        transition_sha256=None,
    )
    host._create_json(writer, host.ACTIVE_EPOCH_PATH, pointer)
    host._create_json(writer, host._epoch_policy_path(0), policy)
    host._create_json(writer, host._epoch_document_path(0), epoch)
    host._create_json(
        writer,
        host.ACTIVATION_PATH,
        {"armed": True, "release_id": epoch["release_id"], "epoch_index": 0},
    )
    monkeypatch.setattr(
        host,
        "compile_source_manifest",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("idle path must stay cheap")),
    )
    before = sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())
    try:
        assert host._owner_next_full_tick_ns(
            writer=writer,
            now_ns=first_scan_ns - 20 * NS,
        ) == first_scan_ns
        after = sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())
        assert after == before
        assert host._owner_next_full_tick_ns(
            writer=writer,
            now_ns=first_scan_ns + 1,
        ) is None
        writer.create(host._standby_path(1), b"standby-exists", mode=0o600)
        assert host._owner_next_full_tick_ns(
            writer=writer,
            now_ns=epoch["epoch_end_ns"] - 30 * 60 * NS,
        ) == epoch["epoch_end_ns"]
    finally:
        writer.close()


@pytest.mark.parametrize("priority_race", [False, True])
def test_rate_blocked_tick_does_not_send_or_retry_page(tmp_path, monkeypatch, priority_race):
    writer, guard, binding, _root = _writer(tmp_path)
    first_scan_ns = 1_800_000_000 * NS
    policy = discovery.compile_policy(
        first_scan_ns=first_scan_ns,
        scan_interval_seconds=300,
        pages=3,
        page_spacing_seconds=12,
        sample_numerator=0,
        sample_denominator=1,
        source_spec_sha256="b" * 64,
        max_scan_count=864,
    )
    epoch = runtime.epoch_envelope(
        release_id="c" * 32,
        lease_id="e" * 32,
        root_id="f" * 32,
        release_sha256="d" * 64,
        source_sha256=policy["source_spec_sha256"],
        policy=policy,
        epoch_index=0,
        epoch_start_ns=first_scan_ns,
        previous_epoch_sha256=None,
        lifetime_requests_before=0,
        lifetime_physical_before=0,
        prior_allocated_physical=0,
        prior_pending_physical=0,
    )
    monkeypatch.setattr(
        host.v3,
        "_v2_policy",
        lambda _raw: {"service_lock_paths": {"gecko": "services/legacy-gecko/.lane.lock"}},
    )
    writer.create(host.v2.POLICY_PATH, b"fixture")
    now = first_scan_ns + 20 * NS
    if not priority_race:
        host._record_rate_dispatch(writer, request_id="a" * 64, dispatch_ns=now - 5 * NS)
    priority_results = iter([False, True] if priority_race else [False, False])
    reservations = []
    settlements = []
    fetches = []

    class Capacity:
        def reservation_status(self, **_kwargs):
            return {"state": "absent", "tail_at_ns": 0}

        def reserve(self, **kwargs):
            reservations.append(kwargs)

        def settle(self, **kwargs):
            settlements.append(kwargs)

    try:
        result = host.run_scan(
            writer=writer,
            guard=guard,
            binding=binding,
            policy=policy,
            epoch=epoch,
            scan_index=0,
            scheduled_ns=first_scan_ns,
            now_ns=lambda: now,
            fetch_page=lambda _url: (
                fetches.append("request")
                or (_ for _ in ()).throw(
                    AssertionError("a rate-blocked wake-up must not send provider I/O")
                )
            ),
            capacity=Capacity(),
            priority_gate=lambda: next(priority_results),
        )
        assert result["provider_io"] is False
        assert fetches == []
        if priority_race:
            assert result["completed_pages"] == 1
            assert len(reservations) == 1
            assert len(settlements) == 1 and settlements[0]["release"] is True
            assert writer.exists(host._page_path(0, 0, 1, "intent.json"))
            assert writer.exists(host._page_path(0, 0, 1, "result.json"))
            assert not writer.exists(host._page_path(0, 0, 1, "dispatch.json"))
        else:
            assert result["completed_pages"] == 0
            assert not reservations
            assert not writer.exists(host._page_path(0, 0, 1, "intent.json"))
    finally:
        writer.close()


def test_release_preparation_is_inert_and_records_zero_followup_sample(tmp_path, monkeypatch):
    writer, guard, binding, root = _writer(tmp_path)
    manifest_core = {
        "schema": host.SOURCE_MANIFEST_SCHEMA,
        "schema_version": 2,
        "source_commit": "1" * 40,
        "files": [],
        "source_order": list(host.SOURCE_PATHS),
    }
    source_sha = runtime.sha256(runtime.canonical(manifest_core))
    manifest = {**manifest_core, "manifest_sha256": source_sha}
    monkeypatch.setattr(host, "compile_source_manifest", lambda _repo: manifest)
    try:
        candidate = host.prepare_release_material(
            writer=writer,
            guard=guard,
            binding=binding,
            now_ns=1_800_000_000_000_000_000,
            loaded_labels=set(),
        )
        policy = json.loads((root / host._epoch_policy_path(0)).read_text())
        assert policy["sample"] == {
            "method": "sha256(policy_sha256 || NUL || network || NUL || pool_id) threshold",
            "numerator": 0,
            "denominator": 1,
            "population": "first_seen_valid_pool_ids_in_observed_scan_cohort",
            "replacement": False,
        }
        assert candidate["activation_authorized"] is False
        assert candidate["provider_io"] is False
        assert not writer.exists(host.ACTIVATION_PATH)
        assert json.loads((root / f"{host.RUNTIME_ROOT}/release-candidate.json").read_text()) == (
            candidate
        )
    finally:
        writer.close()


def test_competing_process_identity_is_rejected_without_exposing_argv():
    with pytest.raises(host.DiscoveryHostError, match="competing_gecko_process"):
        host._assert_no_competing_gecko_processes(
            ps_output=(
                f"{os.getpid()} {os.getuid()} python -m unrelated\n"
                "99999 501 python -m tools.brain_senses_gecko_new_pool_discovery_host_v2"
            ),
            current_pid=os.getpid(),
        )


def test_expired_epoch_seals_gaps_and_creates_continuous_successor(tmp_path, monkeypatch):
    writer, guard, binding, root = _writer(tmp_path)
    monkeypatch.setattr(host.v2, "POLICY_PATH", "test-v2-policy.json")
    monkeypatch.setattr(
        host.v3,
        "_v2_policy",
        lambda _raw: {"service_lock_paths": {"gecko": "services/legacy-gecko/.lane.lock"}},
    )
    writer.create(host.v2.POLICY_PATH, b"fixture")
    first_scan_ns = 1_800_000_000 * NS
    source_sha256 = "b" * 64
    policy = discovery.compile_policy(
        first_scan_ns=first_scan_ns,
        scan_interval_seconds=300,
        pages=3,
        page_spacing_seconds=12,
        sample_numerator=0,
        sample_denominator=1,
        source_spec_sha256=source_sha256,
        max_scan_count=864,
    )
    release_id = "c" * 32
    release_sha256 = "d" * 64
    epoch = runtime.epoch_envelope(
        release_id=release_id,
        lease_id="e" * 32,
        root_id="f" * 32,
        release_sha256=release_sha256,
        source_sha256=source_sha256,
        policy=policy,
        epoch_index=0,
        epoch_start_ns=first_scan_ns,
        previous_epoch_sha256=None,
        lifetime_requests_before=62,
        lifetime_physical_before=1_015_808,
        prior_allocated_physical=1_015_808,
        prior_pending_physical=0,
    )
    host.initialize_runtime(
        writer=writer,
        guard=guard,
        binding=binding,
        release_id=release_id,
        release_sha256=release_sha256,
        source_sha256=source_sha256,
        policy=policy,
        epoch=epoch,
    )
    _install_epoch_handoff_fixture(writer, guard, binding, policy, epoch)
    monkeypatch.setattr(host, "_next_coordinator_time", lambda _w, _g, now: now)
    monkeypatch.setattr(host.V3CapacityPort, "open_epoch_hold", lambda *a, **k: None)
    monkeypatch.setattr(host.V3CapacityPort, "close_epoch_hold", lambda *a, **k: {})
    monkeypatch.setattr(
        host,
        "_current_capacity_context",
        lambda _writer, _guard: {
            "ledger_bytes": b"verified-v3-ledger-prefix",
            "policy_bytes": b"verified-v3-policy",
            "bridge": {"verified": True},
            "free_bytes": 100 * 1024**3,
        },
    )
    monkeypatch.setattr(host.v3, "verify_v3_ledger", lambda *_a, **_k: [])
    monkeypatch.setattr(host.v2, "physical_inventory_bytes", lambda *_a, **_k: 1_015_808)
    release = {
        "policy": policy,
        "epoch": epoch,
        "activation": {"release_id": release_id},
    }
    renewal_time_ns = epoch["epoch_end_ns"] + 2 * 60 * NS
    try:
        for scan_index in range(864):
            scheduled_ns = first_scan_ns + scan_index * 300 * NS
            host._record_unplanned_gap(
                writer,
                guard,
                binding,
                epoch_index=0,
                scan_index=scan_index,
                scheduled_ns=scheduled_ns,
                observed_ns=max(renewal_time_ns, scheduled_ns + discovery.SCAN_EXPIRY_NS),
            )

        terminal_rows = host._terminal_frontier_rows(
            writer, policy, epoch, require_complete=True
        )
        assert len(terminal_rows) == 864
        assert host._frontier_from_rows(terminal_rows)["terminal_scan_index"] == 863
        assert host._frontier_from_rows(terminal_rows)["kind"] == "gap"

        next_policy, next_epoch = host._renew_epochs_if_due(
            writer=writer,
            guard=guard,
            binding=binding,
            release=release,
            now_ns=renewal_time_ns,
        )

        seal = json.loads((root / host._epoch_root(0) / "seal.json").read_text())
        assert seal["scan_count"] == 864
        assert seal["unresolved_count"] == 0
        assert seal["lifetime_requests_after"] == 62
        assert seal["lifetime_physical_after"] == 1_015_808
        assert seal["transport_quiescent"] is True
        assert next_epoch["epoch_index"] == 1
        assert next_epoch["epoch_start_ns"] == epoch["epoch_end_ns"]
        assert next_epoch["lifetime_requests_before"] == 62
        assert next_epoch["lifetime_physical_before"] == 1_015_808
        assert next_epoch["prior_allocated_physical"] == 1_015_808
        assert next_epoch["previous_epoch_sha256"] == seal["seal_sha256"]
        assert next_policy["first_scan_ns"] == next_epoch["epoch_start_ns"]
        assert next_policy["sample"]["numerator"] == 0
        successor_standby = json.loads((root / host._standby_path(1)).read_text())
        assert successor_standby["schema_version"] == 3
        assert successor_standby["predecessor_frontier"]["kind"] == "gap"
        assert successor_standby["predecessor_frontier"]["terminal_scan_index"] == 0
        assert successor_standby["predecessor_frontier"]["prefix_sha256"] == runtime.sha256(
            runtime.canonical(
                [
                    {
                        "epoch_index": 0,
                        "terminal_scan_index": 0,
                        "kind": "gap",
                        "artifact_sha256": runtime.sha256(
                            runtime.canonical(
                                json.loads(
                                    (root / host._epoch_root(0) / "gaps/0000.json").read_text()
                                )
                            )
                        ),
                    }
                ]
            )
        )
        history = host._load_epoch_history(writer, current_epoch_index=1)
        assert history["epoch_count"] == 2
        assert history["current_epoch_index"] == 1
        assert (
            next_epoch["predecessor_standby_sha256"]
            == json.loads((root / host._standby_path(1)).read_text())["standby_sha256"]
        )
        assert host._read_active_epoch_pointer(writer, release_id=release_id)["epoch_index"] == 1
        retry_policy, retry_epoch = host._renew_epochs_if_due(
            writer=writer,
            guard=guard,
            binding=binding,
            release=release,
            now_ns=renewal_time_ns,
        )
        assert retry_policy == next_policy
        assert retry_epoch == next_epoch
        assert host._read_active_epoch_pointer(writer, release_id=release_id)["generation"] == 1

    finally:
        writer.close()


def test_epoch_renewal_fails_before_seal_when_runway_or_floor_is_missing(tmp_path, monkeypatch):
    writer, guard, binding, root = _writer(tmp_path)
    monkeypatch.setattr(host.v2, "POLICY_PATH", "test-v2-policy.json")
    monkeypatch.setattr(
        host.v3,
        "_v2_policy",
        lambda _raw: {"service_lock_paths": {"gecko": "services/legacy-gecko/.lane.lock"}},
    )
    writer.create(host.v2.POLICY_PATH, b"fixture")
    first_scan_ns = 1_800_000_000 * NS
    source_sha256 = "b" * 64
    policy = discovery.compile_policy(
        first_scan_ns=first_scan_ns,
        scan_interval_seconds=300,
        pages=3,
        page_spacing_seconds=12,
        sample_numerator=0,
        sample_denominator=1,
        source_spec_sha256=source_sha256,
        max_scan_count=864,
    )
    epoch = runtime.epoch_envelope(
        release_id="c" * 32,
        lease_id="e" * 32,
        root_id="f" * 32,
        release_sha256="d" * 64,
        source_sha256=source_sha256,
        policy=policy,
        epoch_index=0,
        epoch_start_ns=first_scan_ns,
        previous_epoch_sha256=None,
        lifetime_requests_before=0,
        lifetime_physical_before=0,
        prior_allocated_physical=0,
        prior_pending_physical=0,
    )
    host.initialize_runtime(
        writer=writer,
        guard=guard,
        binding=binding,
        release_id=epoch["release_id"],
        release_sha256=epoch["release_sha256"],
        source_sha256=source_sha256,
        policy=policy,
        epoch=epoch,
    )
    _install_epoch_handoff_fixture(writer, guard, binding, policy, epoch)
    monkeypatch.setattr(host, "_next_coordinator_time", lambda _w, _g, now: now)
    monkeypatch.setattr(host.V3CapacityPort, "open_epoch_hold", lambda *a, **k: None)
    monkeypatch.setattr(host.V3CapacityPort, "close_epoch_hold", lambda *a, **k: {})
    monkeypatch.setattr(
        host,
        "_current_capacity_context",
        lambda _writer, _guard: {
            "ledger_bytes": b"verified-v3-ledger-prefix",
            "policy_bytes": b"verified-v3-policy",
            "bridge": {"verified": True},
            "free_bytes": 50 * 1024**3 - 1,
        },
    )
    monkeypatch.setattr(host.v3, "verify_v3_ledger", lambda *_a, **_k: [])
    monkeypatch.setattr(host.v2, "physical_inventory_bytes", lambda *_a, **_k: 0)
    renewal_time_ns = epoch["epoch_end_ns"] + 2 * 60 * NS
    try:
        for scan_index in range(864):
            scheduled_ns = first_scan_ns + scan_index * 300 * NS
            host._record_unplanned_gap(
                writer,
                guard,
                binding,
                epoch_index=0,
                scan_index=scan_index,
                scheduled_ns=scheduled_ns,
                observed_ns=max(renewal_time_ns, scheduled_ns + discovery.SCAN_EXPIRY_NS),
            )
        with pytest.raises(host.DiscoveryHostError, match="next_runway_unavailable"):
            host._renew_epochs_if_due(
                writer=writer,
                guard=guard,
                binding=binding,
                release={
                    "policy": policy,
                    "epoch": epoch,
                    "activation": {"release_id": epoch["release_id"]},
                },
                now_ns=renewal_time_ns,
            )
        assert not writer.exists(f"{host._epoch_root(0)}/seal.json")
        assert not writer.exists(host._epoch_document_path(1))
    finally:
        writer.close()
