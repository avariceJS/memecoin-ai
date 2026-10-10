from __future__ import annotations

import fcntl
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from memecoin_ai.domain import canonical
from memecoin_ai.sources import Capture, TransportSourceError
from tools import brain_senses_block_epoch_v3_schedule as schedule_v3
from tools import brain_senses_service_v1 as service


def _ns(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp() * 1_000_000_000)


def _proof(root: Path) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    stop_path = root / "c4-stop.json"
    receipt_path = root / "terminal-receipt.json"
    stop = {
        "campaign_id": "helius-inventory-v3-20261010-4",
        "reason": "finite_attempt_cap_reached",
        "attempt_count": 24,
        "active_reservation_status": "reconciled",
        "active_bucket": "20261010T230000Z",
        "accounting_error_class": None,
        "epoch_summary_sha256": "a" * 64,
        "frame_reservation_sha256": "b" * 64,
        "observed_anchor_slot": 99,
        "observed_frontier": 100,
        "observed_anchor_receipt_ns": _ns("2026-10-09T23:00:00"),
        "last_inventoried_interval": None,
        "gap_intervals": [],
    }
    stop_path.write_text(json.dumps(stop, sort_keys=True))
    receipt_path.write_text(
        json.dumps(
            {
                "campaign_id": stop["campaign_id"],
                "status": "normal_finite_terminal",
                "attempt_count": 24,
                "active_reservation_status": "reconciled",
                "accounting_error_count": 0,
                "stop_sha256": service._sha(stop_path.read_bytes()),
                "post_stop_rpc_requests": 0,
            },
            sort_keys=True,
        )
    )
    return stop_path, receipt_path


@pytest.fixture(autouse=True)
def _stub_terminal_verification_for_service_policy_tests(monkeypatch: pytest.MonkeyPatch):
    # These tests exercise service policy/recovery with deliberately small proof
    # fixtures. Deep c4 terminal qualification has its own tests.
    calls: list[tuple[Path, Path | None]] = []

    def verify(root: Path, receipt: Path | None = None) -> dict:
        calls.append((root, receipt))
        return {"status": "normal_finite_terminal"}

    monkeypatch.setattr(service.c4_terminal, "verify_receipt", verify)
    yield calls


def _initialize(root: Path, *, start: str = "2026-10-10T00:00:00") -> int:
    start_ns = _ns(start)
    stop_path, receipt_path = _proof(root / "predecessor")
    service.initialize(
        root,
        service_id="helius-senses-service-test",
        authorized_at_ns=start_ns,
        expires_at_ns=start_ns + 7 * 86400 * 1_000_000_000,
        predecessor_stop_path=stop_path,
        terminal_receipt_path=receipt_path,
    )
    return start_ns


class _SlotClient:
    def __init__(self, slot: int = 100, *, fail: bool = False) -> None:
        self.slot = slot
        self.fail = fail
        self.calls = 0

    def fetch(self, method: str, params: list) -> Capture:
        self.calls += 1
        if self.fail:
            raise TransportSourceError("timeout")
        now = service.time.time_ns()
        request = canonical({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        response = canonical({"jsonrpc": "2.0", "id": 1, "result": self.slot}).encode()
        return Capture("helius-free", method, request, now, now + 1, response)


class _EmptyInventoryClient:
    def __init__(self, slot: int) -> None:
        self.slot = slot
        self.calls = 0

    def fetch(self, method: str, params: list) -> Capture:
        self.calls += 1
        now = service.time.time_ns()
        request = canonical({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        result = self.slot if method == "getSlot" else []
        response = canonical({"jsonrpc": "2.0", "id": 1, "result": result}).encode()
        return Capture("helius-free", method, request, now, now + 1, response)


def _run_bootstrap(root: Path, reservation: dict, *, slot: int = 100, fail: bool = False):
    client = _SlotClient(slot, fail=fail)
    sealed = service.run_reserved_bootstrap(
        root,
        reservation_id=reservation["reservation_id"],
        at_ns=reservation["bucket_ns"],
        clients={"helius-free": client},
    )
    return sealed, client


def _run_frame(root: Path, reservation: dict, *, slot: int):
    campaign_root = root / "campaigns" / reservation["campaign_id"]
    service.mark_epoch_started(
        root,
        reservation_id=reservation["reservation_id"],
        at_ns=reservation["bucket_ns"],
    )
    client = _EmptyInventoryClient(slot)
    result = schedule_v3.run_bucket(
        campaign_root,
        lineage_path=campaign_root / "predecessor-lineage-v1.json",
        now=datetime.fromtimestamp(reservation["bucket_ns"] / 1e9, UTC),
        clients={"helius-free": client},
    )
    sealed = service.seal_from_epoch(
        root,
        reservation_id=reservation["reservation_id"],
        at_ns=reservation["bucket_ns"] + 1,
    )
    return result, sealed, client


def test_predecessor_requires_verified_normal_stop(
    tmp_path: Path, _stub_terminal_verification_for_service_policy_tests
) -> None:
    stop, receipt = _proof(tmp_path / "proof")
    service._predecessor_proof(stop, receipt)
    assert _stub_terminal_verification_for_service_policy_tests == [(stop.parent, receipt)]
    value = json.loads(stop.read_text())
    value["reason"] = "epoch_requires_review_before_next_launch"
    stop.write_text(json.dumps(value, sort_keys=True))
    with pytest.raises(ValueError, match="predecessor_must_have_reconciled_finite_normal_stop"):
        service._predecessor_proof(stop, receipt)


@pytest.mark.parametrize("failure_point", ["manifest", "controller", "ledger"])
def test_initialize_repairs_only_the_durable_unreserved_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_point: str
) -> None:
    root = tmp_path / failure_point
    start = _ns("2026-10-10T00:00:00")
    stop, receipt = _proof(root / "predecessor")
    original_write = service._write
    original_append = service._append
    fired = False

    def fail_write(path, value, *, exclusive=False):
        nonlocal fired
        original_write(path, value, exclusive=exclusive)
        wanted = {
            "manifest": "service-manifest-v2.json",
            "controller": "controller-v2.json",
        }.get(failure_point)
        if not fired and wanted and path.name == wanted:
            fired = True
            raise RuntimeError("injected initialization crash")

    def fail_append(path, event):
        nonlocal fired
        result = original_append(path, event)
        if not fired and failure_point == "ledger" and event.get("kind") == "initialized":
            fired = True
            raise RuntimeError("injected initialization crash")
        return result

    monkeypatch.setattr(service, "_write", fail_write)
    monkeypatch.setattr(service, "_append", fail_append)
    with pytest.raises(RuntimeError, match="injected initialization crash"):
        service.initialize(
            root,
            service_id="helius-senses-service-test",
            authorized_at_ns=start,
            expires_at_ns=start + 7 * 86400 * 1_000_000_000,
            predecessor_stop_path=stop,
            terminal_receipt_path=receipt,
        )
    monkeypatch.setattr(service, "_write", original_write)
    monkeypatch.setattr(service, "_append", original_append)
    service.initialize(
        root,
        service_id="helius-senses-service-test",
        authorized_at_ns=start,
        expires_at_ns=start + 7 * 86400 * 1_000_000_000,
        predecessor_stop_path=stop,
        terminal_receipt_path=receipt,
    )
    events = service._load_ledger(root)
    assert [event["kind"] for event in events] == ["initialized"]
    assert service.read_status(root)["usage"]["outstanding"] == 0


def test_bootstrap_reservation_is_durable_and_restart_never_reissues(tmp_path: Path) -> None:
    root = tmp_path / "service"
    start = _initialize(root)
    result = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    assert result["campaign_id"].endswith("-20261010-g01")
    assert result["action"] == "initial_bootstrap"
    before = service.read_status(root)
    assert before["usage"]["requests"] == 1
    assert before["usage"]["outstanding"] == 1
    with pytest.raises(ValueError, match="bootstrap_requires_offline_reconciliation"):
        service.reserve_bootstrap(
            root,
            at_ns=start + 1,
            free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
        )
    assert len([e for e in service._load_ledger(root) if e["kind"] == "reservation"]) == 1
    assert service.read_status(root)["ledger_events"] == before["ledger_events"]


def test_provably_unstarted_bootstrap_releases_only_unstarted_capacity(tmp_path: Path) -> None:
    root = tmp_path / "service"
    start = _initialize(root)
    reservation = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    service.mark_epoch_started(root, reservation_id=reservation["reservation_id"], at_ns=start)
    seal = service.seal_from_epoch(
        root, reservation_id=reservation["reservation_id"], at_ns=start + 1
    )
    assert seal["outcome"] == "unstarted"
    assert seal["actual_requests"] == 0
    assert seal["evidence"]["unstarted_request_capacity"] == 1
    status = service.read_status(root)
    assert status["usage"] == {"requests": 0, "raw_bytes": 0, "stored_bytes": 0, "outstanding": 0}
    assert status["missed_bucket_gaps"][0]["reason"] == "scheduled_bucket_provably_unstarted"
    with pytest.raises(ValueError, match="bootstrap_must_be_reconciled_before_buckets"):
        service.reserve_bucket(
            root,
            bucket_ns=start + 3600 * 1_000_000_000,
            free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
            request_time_ns=start + 3600 * 1_000_000_000,
        )


@pytest.mark.parametrize("crash_after", ["reservation_append", "controller_write"])
def test_reserve_crash_before_writer_is_sealed_unstarted_without_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_after: str
) -> None:
    root = tmp_path / crash_after
    start = _initialize(root)
    original_write = service._write
    original_append = service._append
    fired = False

    def fail_write(path, value, *, exclusive=False):
        nonlocal fired
        original_write(path, value, exclusive=exclusive)
        if not fired and crash_after == "controller_write" and path.name == "controller-v2.json":
            fired = True
            raise RuntimeError("injected reserve crash")

    def fail_append(path, event):
        nonlocal fired
        result = original_append(path, event)
        if not fired and crash_after == "reservation_append" and event.get("kind") == "reservation":
            fired = True
            raise RuntimeError("injected reserve crash")
        return result

    monkeypatch.setattr(service, "_write", fail_write)
    monkeypatch.setattr(service, "_append", fail_append)
    with pytest.raises(RuntimeError, match="injected reserve crash"):
        service.reserve_bootstrap(
            root,
            at_ns=start,
            free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
        )
    monkeypatch.setattr(service, "_write", original_write)
    monkeypatch.setattr(service, "_append", original_append)
    with pytest.raises(ValueError, match="bucket_identity_already_spent_or_nonmonotonic"):
        service.reserve_bootstrap(
            root,
            at_ns=start,
            free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
        )
    seals = [event for event in service._load_ledger(root) if event.get("kind") == "seal"]
    assert len(seals) == 1
    assert seals[0]["outcome"] == "unstarted"
    assert seals[0]["actual_requests"] == 0
    assert not (root / "writer-active-v2.json").exists()
    assert service.read_status(root)["usage"] == {
        "requests": 0,
        "raw_bytes": 0,
        "stored_bytes": 0,
        "outstanding": 0,
    }


def test_reserved_not_started_frame_recovers_as_unstarted_without_replay(tmp_path: Path) -> None:
    root = tmp_path / "service"
    start = _initialize(root)
    bootstrap = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    bootstrap_seal, bootstrap_client = _run_bootstrap(root, bootstrap)
    assert bootstrap_seal["outcome"] == "bootstrap_complete"
    assert bootstrap_client.calls == 1

    first_bucket = start + 3600 * 1_000_000_000
    frame = service.reserve_bucket(
        root,
        bucket_ns=first_bucket,
        free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
        request_time_ns=first_bucket,
    )
    assert frame["action"] == "frame"
    assert service._read(root / "writer-active-v2.json")["status"] == "reserved_not_started"

    seal = service.seal_unstarted_frame_reservation(
        root, reservation_id=frame["reservation_id"], at_ns=first_bucket + 1
    )
    assert seal["outcome"] == "unstarted"
    next_bucket = first_bucket + 3600 * 1_000_000_000
    next_frame = service.reserve_bucket(
        root,
        bucket_ns=next_bucket,
        free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
        request_time_ns=next_bucket,
    )
    seals = [event for event in service._load_ledger(root) if event.get("kind") == "seal"]
    frame_seal = next(
        event for event in seals if event["reservation_id"] == frame["reservation_id"]
    )
    assert frame_seal["outcome"] == "unstarted"
    assert frame_seal["actual_requests"] == 0
    assert next_frame["action"] == "sleep_gap_anchor"
    assert (
        service._read(root / "writer-active-v2.json")["reservation_id"]
        == next_frame["reservation_id"]
    )
    assert next_frame["bucket_ns"] == next_bucket


@pytest.mark.parametrize("crash_after", ["controller_write", "seal_append"])
def test_unstarted_frame_seal_recovers_each_durable_crash_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_after: str
) -> None:
    root = tmp_path / crash_after
    start = _initialize(root)
    bootstrap = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    service.run_reserved_bootstrap(
        root,
        reservation_id=bootstrap["reservation_id"],
        at_ns=start,
        clients={"helius-free": _SlotClient()},
    )
    bucket = start + 3600 * 1_000_000_000
    frame = service.reserve_bucket(
        root,
        bucket_ns=bucket,
        free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
        request_time_ns=bucket,
    )
    original_write = service._write
    original_append = service._append
    fired = False

    def fail_write(path, value, *, exclusive=False):
        nonlocal fired
        result = original_write(path, value, exclusive=exclusive)
        if not fired and crash_after == "controller_write" and path.name == "controller-v2.json":
            fired = True
            raise RuntimeError("injected unstarted controller crash")
        return result

    def fail_append(path, event):
        nonlocal fired
        result = original_append(path, event)
        if not fired and crash_after == "seal_append" and event.get("kind") == "seal":
            fired = True
            raise RuntimeError("injected unstarted seal crash")
        return result

    monkeypatch.setattr(service, "_write", fail_write)
    monkeypatch.setattr(service, "_append", fail_append)
    with pytest.raises(RuntimeError, match="injected unstarted"):
        service.seal_unstarted_frame_reservation(
            root, reservation_id=frame["reservation_id"], at_ns=bucket + 1
        )
    monkeypatch.setattr(service, "_write", original_write)
    monkeypatch.setattr(service, "_append", original_append)

    recovered = service.seal_from_epoch(
        root, reservation_id=frame["reservation_id"], at_ns=bucket + 2
    )
    assert recovered["outcome"] == "unstarted"
    assert service.read_status(root)["usage"]["outstanding"] == 0
    assert not (root / "writer-active-v2.json").exists()
    next_frame = service.reserve_bucket(
        root,
        bucket_ns=bucket + 3600 * 1_000_000_000,
        free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
        request_time_ns=bucket + 3600 * 1_000_000_000,
    )
    assert next_frame["action"] == "sleep_gap_anchor"


def test_append_before_writer_frame_prefix_recovers_without_rpc(tmp_path: Path) -> None:
    root = tmp_path / "service"
    start = _initialize(root)
    bootstrap = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    service.run_reserved_bootstrap(
        root,
        reservation_id=bootstrap["reservation_id"],
        at_ns=start,
        clients={"helius-free": _SlotClient()},
    )
    bucket = start + 3600 * 1_000_000_000
    frame = service.reserve_bucket(
        root,
        bucket_ns=bucket,
        free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
        request_time_ns=bucket,
    )
    (root / "writer-active-v2.json").unlink()
    seal = service.seal_from_epoch(root, reservation_id=frame["reservation_id"], at_ns=bucket + 1)
    assert seal["outcome"] == "unstarted"
    assert service.read_status(root)["usage"]["requests"] == 1
    next_frame = service.reserve_bucket(
        root,
        bucket_ns=bucket + 3600 * 1_000_000_000,
        free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
        request_time_ns=bucket + 3600 * 1_000_000_000,
    )
    assert next_frame["action"] == "sleep_gap_anchor"


@pytest.mark.parametrize("tamper", ["started", "plan", "activity"])
def test_unstarted_frame_reconciliation_rejects_started_or_mismatched_state(
    tmp_path: Path, tamper: str
) -> None:
    root = tmp_path / tamper
    start = _initialize(root)
    bootstrap = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    service.run_reserved_bootstrap(
        root,
        reservation_id=bootstrap["reservation_id"],
        at_ns=start,
        clients={"helius-free": _SlotClient()},
    )
    bucket = start + 3600 * 1_000_000_000
    frame = service.reserve_bucket(
        root,
        bucket_ns=bucket,
        free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
        request_time_ns=bucket,
    )
    writer_path = root / "writer-active-v2.json"
    writer = service._read(writer_path)
    if tamper == "started":
        writer["status"] = "epoch_started"
        service._write(writer_path, writer)
    elif tamper == "plan":
        writer["plan_sha256"] = "0" * 64
        service._write(writer_path, writer)
    else:
        epoch = root / "campaigns" / frame["campaign_id"] / "epochs" / frame["plan"]["bucket"]
        epoch.mkdir(parents=True)
        (epoch / "unexpected.json").write_text("{}")

    with pytest.raises(ValueError):
        service.seal_unstarted_frame_reservation(
            root, reservation_id=frame["reservation_id"], at_ns=bucket + 1
        )
    assert not any(
        event.get("kind") == "seal" and event.get("reservation_id") == frame["reservation_id"]
        for event in service._load_ledger(root)
    )
    if tamper == "activity":
        assert (root / "STOP").is_file()
    else:
        assert not (root / "STOP").exists()


def test_append_before_writer_with_epoch_activity_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "service"
    start = _initialize(root)
    bootstrap = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    service.run_reserved_bootstrap(
        root,
        reservation_id=bootstrap["reservation_id"],
        at_ns=start,
        clients={"helius-free": _SlotClient()},
    )
    bucket = start + 3600 * 1_000_000_000
    frame = service.reserve_bucket(
        root,
        bucket_ns=bucket,
        free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
        request_time_ns=bucket,
    )
    (root / "writer-active-v2.json").unlink()
    epoch = root / "campaigns" / frame["campaign_id"] / "epochs" / frame["plan"]["bucket"]
    epoch.mkdir(parents=True)
    (epoch / "unexpected.json").write_text("{}")

    with pytest.raises(ValueError, match="epoch_tree_reconciliation_failed_closed"):
        service.seal_from_epoch(root, reservation_id=frame["reservation_id"], at_ns=bucket + 1)
    assert (root / "STOP").is_file()
    assert not any(
        event.get("kind") == "seal" and event.get("reservation_id") == frame["reservation_id"]
        for event in service._load_ledger(root)
    )


def test_external_bootstrap_seals_lineage_before_a_later_frame_bucket(tmp_path: Path) -> None:
    root = tmp_path / "service"
    start = _initialize(root)
    reservation = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    sealed, client = _run_bootstrap(root, reservation, slot=100)
    assert sealed["outcome"] == "bootstrap_complete"
    assert client.calls == 1
    campaign_root = root / "campaigns" / reservation["campaign_id"]
    lineage = service._read(campaign_root / "predecessor-lineage-v1.json")
    assert lineage["last_observed_anchor_slot"] == 100
    assert lineage["last_attempt_bucket"] == "20261010T000000Z"
    assert lineage["gap_intervals"] == [
        {
            "start": 100,
            "end_exclusive": 101,
            "reason": "successor_bootstrap_uncovered_interval",
        }
    ]
    assert (campaign_root / "schedule-v3.json").is_file()
    later = service.reserve_bucket(
        root,
        bucket_ns=start + 3600 * 1_000_000_000,
        free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
        request_time_ns=start + 3600 * 1_000_000_000,
    )
    assert later["action"] == "frame"
    assert later["bucket_ns"] > reservation["bucket_ns"]


def test_adjacent_utc_day_closes_old_root_and_initializes_no_rpc_frame_lineage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "service"
    start = _initialize(root, start="2026-10-10T23:00:00")
    bootstrap = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    first, client = _run_bootstrap(root, bootstrap, slot=455_300_000)
    assert first["outcome"] == "bootstrap_complete"
    assert client.calls == 1
    midnight = start + 3600 * 1_000_000_000
    rollover = service.reserve_bucket(
        root,
        bucket_ns=midnight,
        free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
        request_time_ns=midnight,
    )
    assert rollover["action"] == "frame"
    assert rollover["campaign_id"].endswith("-20261011-g01")
    old_root = root / "campaigns" / bootstrap["campaign_id"]
    new_root = root / "campaigns" / rollover["campaign_id"]
    stop = service._read(old_root / "STOP")
    closure = service._read(old_root / "rollover-closure-v1.json")
    lineage = service._read(new_root / "predecessor-lineage-v1.json")
    assert stop["reason"] == "service_utc_day_rollover_closed"
    assert stop["administrative_no_rpc"] is True
    assert closure["stop_sha256"] == service._sha((old_root / "STOP").read_bytes())
    assert closure["no_rpc"] is True
    assert lineage["rollover"]["previous_campaign_id"] == bootstrap["campaign_id"]
    assert lineage["rollover"]["previous_closure_sha256"] == service._sha(
        (old_root / "rollover-closure-v1.json").read_bytes()
    )
    assert lineage["last_attempt_bucket"] == "20261010T230000Z"
    assert lineage["last_observed_anchor_slot"] == 455_300_000
    assert (new_root / "schedule-v3.json").is_file()
    assert not (new_root / "bootstrap-epochs").exists()
    with pytest.raises(ValueError, match="service_writer_pending_offline_reconciliation"):
        service.reserve_bucket(
            root,
            bucket_ns=midnight,
            free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
            request_time_ns=midnight,
        )
    assert client.calls == 1


def test_full_utc_day_campaign_can_handoff_without_extra_anchor(tmp_path: Path) -> None:
    root = tmp_path / "service"
    start = _initialize(root, start="2026-10-10T00:00:00")
    bootstrap = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    initial, _ = _run_bootstrap(root, bootstrap, slot=100)
    assert initial["outcome"] == "bootstrap_complete"

    last_seal = None
    for hour in range(1, 24):
        tick = start + hour * 3600 * 1_000_000_000
        reservation = service.reserve_bucket(
            root,
            bucket_ns=tick,
            free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
            request_time_ns=tick,
        )
        assert reservation["action"] == "frame"
        outcome, last_seal, _ = _run_frame(root, reservation, slot=101 + hour)
        assert outcome["status"] == "epoch_finished"
        assert last_seal["outcome"] == "complete"
    assert last_seal is not None
    assert service.read_status(root)["usage"]["requests"] <= service.DAILY_REQUESTS_MAX
    assert service.read_status(root)["usage"]["requests"] == 47

    midnight = start + 24 * 3600 * 1_000_000_000
    reservation = service.reserve_bucket(
        root,
        bucket_ns=midnight,
        free_disk_bytes=service.DISK_FLOOR_BYTES + 3 * service.DISK_PEAK_MARGIN_BYTES,
        request_time_ns=midnight,
    )
    assert reservation["action"] == "frame"
    assert reservation["campaign_id"].endswith("-20261011-g01")
    assert service.read_status(root)["usage"]["requests"] == 81


def test_missed_midnight_uses_anchor_path_without_inventing_rollover_lineage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "service"
    start = _initialize(root, start="2026-10-10T22:00:00")
    bootstrap = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    _run_bootstrap(root, bootstrap, slot=455_300_000)
    midnight = start + 2 * 3600 * 1_000_000_000
    anchor = service.reserve_bucket(
        root,
        bucket_ns=midnight,
        free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
        request_time_ns=midnight,
    )
    assert anchor["action"] == "sleep_gap_anchor"
    assert anchor["missed_buckets"] == ["20261010T230000Z"]
    old_root = root / "campaigns" / bootstrap["campaign_id"]
    assert not (old_root / "STOP").exists()
    assert not (old_root / "rollover-closure-v1.json").exists()


def test_rollover_refuses_active_v3_writer_then_closes_after_lock_release(tmp_path: Path) -> None:
    root = tmp_path / "service"
    start = _initialize(root, start="2026-10-10T23:00:00")
    bootstrap = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    _run_bootstrap(root, bootstrap, slot=455_300_000)
    midnight = start + 3600 * 1_000_000_000
    old_root = root / "campaigns" / bootstrap["campaign_id"]
    lock = (old_root / ".schedule-v3.lock").open("a")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(ValueError, match="rollover_v3_writer_active"):
            service.reserve_bucket(
                root,
                bucket_ns=midnight,
                free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
                request_time_ns=midnight,
            )
        assert not (old_root / "STOP").exists()
        assert not (old_root / "rollover-close-intent-v1.json").exists()
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
    result = service.reserve_bucket(
        root,
        bucket_ns=midnight,
        free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
        request_time_ns=midnight,
    )
    assert result["action"] == "frame"
    assert (old_root / "STOP").exists()


def test_recovery_generation_rolls_over_without_a_second_anchor_request(tmp_path: Path) -> None:
    root = tmp_path / "service"
    start = _initialize(root, start="2026-10-10T21:00:00")
    bootstrap = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    _run_bootstrap(root, bootstrap, slot=455_300_000)
    frame_time = start + 3600 * 1_000_000_000
    frame = service.reserve_bucket(
        root,
        bucket_ns=frame_time,
        free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
        request_time_ns=frame_time,
    )
    service.mark_epoch_started(root, reservation_id=frame["reservation_id"], at_ns=frame_time)
    frame_root = root / "campaigns" / frame["campaign_id"]
    schedule_v3.run_bucket(
        frame_root,
        lineage_path=frame_root / "predecessor-lineage-v1.json",
        now=datetime.fromtimestamp(frame_time / 1e9, UTC),
        clients={"helius-free": _SlotClient(fail=True)},
    )
    ambiguous = service.seal_from_epoch(
        root, reservation_id=frame["reservation_id"], at_ns=frame_time + 1
    )
    assert ambiguous["outcome"] == "ambiguous"

    recovery_time = start + 2 * 3600 * 1_000_000_000
    recovery = service.reserve_bucket(
        root,
        bucket_ns=recovery_time,
        free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
        request_time_ns=recovery_time,
    )
    assert recovery["campaign_id"].endswith("-g02")
    seal, client = _run_bootstrap(root, recovery, slot=455_300_010)
    assert seal["outcome"] == "anchor_only"
    assert client.calls == 1
    midnight = start + 3 * 3600 * 1_000_000_000
    next_frame = service.reserve_bucket(
        root,
        bucket_ns=midnight,
        free_disk_bytes=service.DISK_FLOOR_BYTES + 3 * service.DISK_PEAK_MARGIN_BYTES,
        request_time_ns=midnight,
    )
    assert next_frame["action"] == "frame"
    assert next_frame["campaign_id"].endswith("-20261011-g01")
    new_root = root / "campaigns" / next_frame["campaign_id"]
    rollover = service._read(new_root / "predecessor-lineage-v1.json")["rollover"]
    assert rollover["previous_campaign_id"] == recovery["campaign_id"]
    assert rollover["previous_bucket"] == "20261010T230000Z"
    assert client.calls == 1


@pytest.mark.parametrize("crash_point", ["closure", "new_v3_init", "reservation_append"])
def test_rollover_crash_boundaries_reconcile_without_duplicate_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_point: str
) -> None:
    root = tmp_path / crash_point
    start = _initialize(root, start="2026-10-10T23:00:00")
    bootstrap = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    _run_bootstrap(root, bootstrap, slot=455_300_000)
    midnight = start + 3600 * 1_000_000_000
    original_write = service._write
    original_append = service._append
    original_initialize = schedule_v3.initialize
    fired = False

    def fail_write(path, value, *, exclusive=False):
        nonlocal fired
        original_write(path, value, exclusive=exclusive)
        if not fired and crash_point == "closure" and path.name == "rollover-closure-v1.json":
            fired = True
            raise RuntimeError("injected rollover crash")

    def fail_append(path, event):
        nonlocal fired
        result = original_append(path, event)
        if not fired and crash_point == "reservation_append" and event.get("kind") == "reservation":
            fired = True
            raise RuntimeError("injected rollover crash")
        return result

    def fail_initialize(*args, **kwargs):
        nonlocal fired
        result = original_initialize(*args, **kwargs)
        if not fired and crash_point == "new_v3_init":
            fired = True
            raise RuntimeError("injected rollover crash")
        return result

    monkeypatch.setattr(service, "_write", fail_write)
    monkeypatch.setattr(service, "_append", fail_append)
    monkeypatch.setattr(schedule_v3, "initialize", fail_initialize)
    with pytest.raises(RuntimeError, match="injected rollover crash"):
        service.reserve_bucket(
            root,
            bucket_ns=midnight,
            free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
            request_time_ns=midnight,
        )
    monkeypatch.setattr(service, "_write", original_write)
    monkeypatch.setattr(service, "_append", original_append)
    monkeypatch.setattr(schedule_v3, "initialize", original_initialize)

    if crash_point == "reservation_append":
        with pytest.raises(ValueError, match="bucket_identity_already_spent_or_nonmonotonic"):
            service.reserve_bucket(
                root,
                bucket_ns=midnight,
                free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
                request_time_ns=midnight,
            )
        events = service._load_ledger(root)
        midnight_reservations = [
            row
            for row in events
            if row.get("kind") == "reservation" and row.get("bucket_ns") == midnight
        ]
        assert len(midnight_reservations) == 1
        assert service.read_status(root)["usage"]["outstanding"] == 0
    else:
        resumed = service.reserve_bucket(
            root,
            bucket_ns=midnight,
            free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
            request_time_ns=midnight,
        )
        assert resumed["action"] == "frame"
        assert resumed["campaign_id"].endswith("-20261011-g01")
        assert (
            len(
                [
                    row
                    for row in service._load_ledger(root)
                    if row.get("kind") == "reservation" and row.get("bucket_ns") == midnight
                ]
            )
            == 1
        )


def test_bootstrap_ambiguity_stops_and_restart_does_not_replay(tmp_path: Path) -> None:
    root = tmp_path / "service"
    start = _initialize(root)
    reservation = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    sealed, client = _run_bootstrap(root, reservation, fail=True)
    assert sealed["outcome"] == "ambiguous"
    assert client.calls == 1
    assert service.read_status(root)["stop"]["reason"] == (
        "bootstrap_or_second_ambiguous_transport_circuit_breaker"
    )
    with pytest.raises(ValueError, match="service_stopped"):
        service.run_reserved_bootstrap(
            root,
            reservation_id=reservation["reservation_id"],
            at_ns=start + 1,
            clients={"helius-free": _SlotClient()},
        )


def test_bootstrap_result_and_lineage_hash_drift_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "service"
    start = _initialize(root)
    reservation = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    sealed, _ = _run_bootstrap(root, reservation)
    assert sealed["outcome"] == "bootstrap_complete"
    result = next(
        (
            root
            / "campaigns"
            / reservation["campaign_id"]
            / "bootstrap-epochs"
            / reservation["plan"]["bucket"]
            / "request-results"
        ).glob("*.json")
    )
    changed = json.loads(result.read_text())
    changed["slot_value"] += 1
    result.write_text(json.dumps(changed, sort_keys=True))
    # A subsequent reconciliation detects modified bytes and fails closed.
    with pytest.raises(ValueError, match="epoch_tree_reconciliation_failed_closed"):
        service.seal_from_epoch(root, reservation_id=reservation["reservation_id"], at_ns=start + 2)
    assert service.read_status(root)["stop"]["reason"] == "epoch_tree_reconciliation_failed_closed"


def test_status_preserves_separate_event_receipt_availability_and_decision_times(
    tmp_path: Path,
) -> None:
    root = tmp_path / "service"
    start = _initialize(root)
    bootstrap = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    bootstrap_seal, _ = _run_bootstrap(root, bootstrap)
    frame_time = start + 3600 * 1_000_000_000
    frame = service.reserve_bucket(
        root,
        bucket_ns=frame_time,
        free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
        request_time_ns=frame_time,
    )
    _, frame_seal, _ = _run_frame(root, frame, slot=100)

    records = service.read_status(root)["sealed_evidence_timestamps"]
    bootstrap_record = next(
        item for item in records if item["reservation_id"] == bootstrap["reservation_id"]
    )
    assert bootstrap_seal["outcome"] == "bootstrap_complete"
    assert bootstrap_record["native_slot"] == 100
    assert bootstrap_record["event_time_state"] == "unknown"
    assert bootstrap_record["block_time_seconds"] is None
    assert type(bootstrap_record["request_started_ns"]) is int
    assert type(bootstrap_record["receipt_ns"]) is int
    assert type(bootstrap_record["availability_ns"]) is int
    assert type(bootstrap_record["decision_time_ns"]) is int
    assert bootstrap_record["request_started_ns"] <= bootstrap_record["receipt_ns"]
    assert bootstrap_record["receipt_ns"] <= bootstrap_record["availability_ns"]

    frame_records = [item for item in records if item["reservation_id"] == frame["reservation_id"]]
    assert frame_seal["outcome"] == "sampled_gap"
    assert len(frame_records) == 1
    anchor_record = next(item for item in frame_records if item["request_phase"] == "anchor")
    assert anchor_record["native_slot"] == 100
    assert type(anchor_record["request_started_ns"]) is int
    assert type(anchor_record["receipt_ns"]) is int
    assert type(anchor_record["availability_ns"]) is int
    assert anchor_record["request_start_time_state"] == "known"
    assert anchor_record["receipt_time_state"] == "known"
    assert anchor_record["availability_time_state"] == "known"


def test_status_keeps_ambiguous_request_start_and_unknown_receipt_times(
    tmp_path: Path,
) -> None:
    root = tmp_path / "service"
    start = _initialize(root)
    bootstrap = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    _run_bootstrap(root, bootstrap)
    frame_time = start + 3600 * 1_000_000_000
    frame = service.reserve_bucket(
        root,
        bucket_ns=frame_time,
        free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
        request_time_ns=frame_time,
    )
    service.mark_epoch_started(root, reservation_id=frame["reservation_id"], at_ns=frame_time)
    campaign_root = root / "campaigns" / frame["campaign_id"]
    schedule_v3.run_bucket(
        campaign_root,
        lineage_path=campaign_root / "predecessor-lineage-v1.json",
        now=datetime.fromtimestamp(frame_time / 1e9, UTC),
        clients={"helius-free": _SlotClient(fail=True)},
    )
    seal = service.seal_from_epoch(
        root, reservation_id=frame["reservation_id"], at_ns=frame_time + 1
    )

    assert seal["outcome"] == "ambiguous"
    record = next(
        item
        for item in service.read_status(root)["sealed_evidence_timestamps"]
        if item["reservation_id"] == frame["reservation_id"]
    )
    assert record["request_started_ns"] is None
    assert record["request_start_time_state"] == "unknown"
    assert record["receipt_ns"] is None
    assert record["availability_ns"] is None
    assert record["receipt_time_state"] == "unknown"
    assert record["availability_time_state"] == "unknown"
    assert record["decision_time_ns"] == seal["evidence"]["decision_time_ns"]


def test_status_does_not_fill_missing_request_time_from_neighboring_anchor() -> None:
    records = service._sealed_timestamp_records(
        [
            {
                "reservation_id": "frame-1",
                "outcome": "complete",
                "at_ns": 99,
                "evidence": {
                    "anchor_receipt_ns": 20,
                    "availability_ns": 21,
                    "decision_time_ns": 30,
                    "request_outcomes": [
                        {
                            "sequence": 1,
                            "phase": "anchor",
                            "status": "captured",
                            "slot": 100,
                            "request_started_ns": 10,
                            "receipt_ns": 20,
                            "availability_ns": 21,
                        },
                        {
                            "sequence": 2,
                            "phase": "inventory",
                            "status": "captured",
                        },
                    ],
                },
            }
        ]
    )

    assert records[0]["receipt_ns"] == 20
    assert records[1]["request_started_ns"] is None
    assert records[1]["receipt_ns"] is None
    assert records[1]["availability_ns"] is None
    assert records[1]["receipt_time_state"] == "unknown"
    assert records[1]["availability_time_state"] == "unknown"


def test_external_bootstrap_rejects_drift_in_a_pinned_scheduler_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "service"
    start = _initialize(root)
    reservation = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    _run_bootstrap(root, reservation)
    original_bindings = schedule_v3._bindings
    changed = original_bindings()
    changed["src/memecoin_ai/domain.py"] = "0" * 64
    monkeypatch.setattr(schedule_v3, "_bindings", lambda: changed)
    controller = service._read(root / "controller-v2.json")
    with pytest.raises(ValueError, match="bootstrap_epoch_source_binding_mismatch"):
        service._external_bootstrap_evidence(root, reservation, controller)


def test_ambiguous_frame_recovers_in_new_generation_then_frames_next_hour(tmp_path: Path) -> None:
    root = tmp_path / "service"
    start = _initialize(root)
    bootstrap = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    sealed, _ = _run_bootstrap(root, bootstrap, slot=100)
    assert sealed["outcome"] == "bootstrap_complete"

    frame_time = start + 3600 * 1_000_000_000
    frame = service.reserve_bucket(
        root,
        bucket_ns=frame_time,
        free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
        request_time_ns=frame_time,
    )
    service.mark_epoch_started(root, reservation_id=frame["reservation_id"], at_ns=frame_time)
    campaign_root = root / "campaigns" / frame["campaign_id"]
    schedule_v3.run_bucket(
        campaign_root,
        lineage_path=campaign_root / "predecessor-lineage-v1.json",
        now=datetime.fromtimestamp(frame_time / 1e9, UTC),
        clients={"helius-free": _SlotClient(fail=True)},
    )
    ambiguous = service.seal_from_epoch(
        root, reservation_id=frame["reservation_id"], at_ns=frame_time + 1
    )
    assert ambiguous["outcome"] == "ambiguous"
    assert service.read_status(root)["frontier_slot"] == 100
    assert service.read_status(root)["usage"]["requests"] == 2

    recovery_time = start + 2 * 3600 * 1_000_000_000
    recovery = service.reserve_bucket(
        root,
        bucket_ns=recovery_time,
        free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
        request_time_ns=recovery_time,
    )
    assert recovery["action"] == "recovery_anchor"
    assert recovery["campaign_id"].endswith("-g02")
    anchor_seal, _ = _run_bootstrap(root, recovery, slot=105)
    assert anchor_seal["outcome"] == "anchor_only"
    lineage = service._read(
        root / "campaigns" / recovery["campaign_id"] / "predecessor-lineage-v1.json"
    )
    assert lineage["gap_intervals"][-1] == {
        "start": 101,
        "end_exclusive": 106,
        "reason": "ambiguous_epoch_uncovered_interval",
    }

    next_frame_time = start + 3 * 3600 * 1_000_000_000
    next_frame = service.reserve_bucket(
        root,
        bucket_ns=next_frame_time,
        free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
        request_time_ns=next_frame_time,
    )
    assert next_frame["action"] == "frame"
    assert next_frame["campaign_id"] == recovery["campaign_id"]


def test_bootstrap_seal_is_idempotent_and_does_not_fetch_twice(tmp_path: Path) -> None:
    root = tmp_path / "service"
    start = _initialize(root)
    reservation = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    first, client = _run_bootstrap(root, reservation)
    second = service.seal_from_epoch(
        root, reservation_id=reservation["reservation_id"], at_ns=start + 2
    )
    assert second == first
    assert client.calls == 1


def test_crash_after_request_reservation_is_spent_and_never_replayed(tmp_path: Path) -> None:
    class CrashClient:
        calls = 0

        def fetch(self, method: str, params: list) -> Capture:
            self.calls += 1
            raise KeyboardInterrupt

    root = tmp_path / "service"
    start = _initialize(root)
    reservation = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    client = CrashClient()
    with pytest.raises(KeyboardInterrupt):
        service.run_reserved_bootstrap(
            root,
            reservation_id=reservation["reservation_id"],
            at_ns=start,
            clients={"helius-free": client},
        )
    assert client.calls == 1
    sealed = service.seal_from_epoch(
        root, reservation_id=reservation["reservation_id"], at_ns=start + 1
    )
    assert sealed["outcome"] == "ambiguous"
    assert sealed["actual_requests"] == 1
    assert service.read_status(root)["usage"]["requests"] == 1
    with pytest.raises(ValueError, match="service_stopped"):
        service.run_reserved_bootstrap(
            root,
            reservation_id=reservation["reservation_id"],
            at_ns=start + 2,
            clients={"helius-free": client},
        )
    assert client.calls == 1


def test_repeated_sleep_gaps_each_get_future_anchor_without_spending_ambiguity(
    tmp_path: Path,
) -> None:
    root = tmp_path / "service"
    start = _initialize(root)
    first = service.reserve_bootstrap(
        root,
        at_ns=start,
        free_disk_bytes=service.DISK_FLOOR_BYTES + service.DISK_PEAK_MARGIN_BYTES + 1,
    )
    first_seal, _ = _run_bootstrap(root, first, slot=100)
    assert first_seal["outcome"] == "bootstrap_complete"

    for hour, slot in ((2, 104), (5, 109)):
        tick = start + hour * 3600 * 1_000_000_000
        result = service.reserve_bucket(
            root,
            bucket_ns=tick,
            free_disk_bytes=service.DISK_FLOOR_BYTES + 2 * service.DISK_PEAK_MARGIN_BYTES,
            request_time_ns=tick,
        )
        assert result["action"] == "sleep_gap_anchor"
        seal, _ = _run_bootstrap(root, result, slot=slot)
        assert seal["outcome"] == "anchor_only"
        assert service.read_status(root)["ambiguous_recovery_used"] is False

    ids = service.read_status(root)["campaign_ids"]
    assert ids == [
        "helius-inventory-v3-service-helius-senses-service-test-20261010-g01",
        "helius-inventory-v3-service-helius-senses-service-test-20261010-g02",
        "helius-inventory-v3-service-helius-senses-service-test-20261010-g03",
    ]


def test_campaign_generation_ids_are_unique_and_bounded(tmp_path: Path) -> None:
    root = tmp_path / "service"
    start = _initialize(root)
    controller = service._read(root / "controller-v2.json")
    ids = [
        service._campaign_id(controller["service_id"], start, generation)
        for generation in range(1, service.DAILY_CAMPAIGNS_MAX + 1)
    ]
    assert len(set(ids)) == service.DAILY_CAMPAIGNS_MAX
    assert all("20261010-g" in value for value in ids)
    with pytest.raises(ValueError, match="campaign_generation_outside_bound"):
        service._campaign_id(controller["service_id"], start, service.DAILY_CAMPAIGNS_MAX + 1)


def test_ledger_charges_ambiguous_started_attempts_and_releases_unstarted_capacity() -> None:
    events = [
        {
            "kind": "reservation",
            "reservation_id": "ambiguous-frame",
            "requests": 34,
            "raw_bytes": service.FRAME_RAW_BYTES,
            "stored_bytes": service.FRAME_STORED_BYTES,
        },
        {
            "kind": "seal",
            "reservation_id": "ambiguous-frame",
            "outcome": "ambiguous",
            "actual_requests": 2,
            "actual_raw_bytes": 4096,
            "actual_stored_bytes": 1024,
        },
    ]
    assert service._usage(events) == {
        "requests": 2,
        "raw_bytes": 4096,
        "stored_bytes": 1024,
        "outstanding": 0,
    }


def test_lock_prevents_two_service_writers(tmp_path: Path) -> None:
    root = tmp_path / "service"
    _initialize(root)
    lock = service._lock(root)
    try:
        with pytest.raises(ValueError, match="service_controller_already_active"):
            service._lock(root)
    finally:
        lock.close()


def test_campaign_tree_hash_excludes_only_root_rollover_files(tmp_path: Path) -> None:
    root = tmp_path / "campaign"
    nested = root / "epochs" / "20261010T230000Z"
    nested.mkdir(parents=True)
    (root / "STOP").write_text("root stop")
    (root / ".schedule-v3.lock").write_text("")
    (nested / "STOP").write_text("epoch stop v1")
    first = service._campaign_tree_sha256(root)
    (root / "STOP").write_text("changed root stop")
    assert service._campaign_tree_sha256(root) == first
    (nested / "STOP").write_text("epoch stop v2")
    assert service._campaign_tree_sha256(root) != first


def test_budget_invariant_exhaustive_boundaries() -> None:
    # Deterministic stdlib state-space check; the repository has no Hypothesis dependency.
    caps = (817, 4_000_000_000, 1_000_000_000)
    required = (34, 256_000_000, 128_000_000)

    def fits(usage: tuple[int, int, int]) -> bool:
        return service._fits_budget(
            dict(zip(("requests", "raw_bytes", "stored_bytes"), usage, strict=True)),
            requests=required[0],
            raw_bytes=required[1],
            stored_bytes=required[2],
            request_cap=caps[0],
            raw_cap=caps[1],
            stored_cap=caps[2],
        )

    levels = [
        (0, 1, cap - need - 1, cap - need, cap - need + 1, cap)
        for cap, need in zip(caps, required, strict=True)
    ]
    increments = (
        (0, 0, 0),
        (1, 0, 0),
        (0, 1, 0),
        (0, 0, 1),
        (required[0], 0, 0),
        (0, required[1], 0),
        (0, 0, required[2]),
        required,
    )
    for requests in levels[0]:
        for raw in levels[1]:
            for stored in levels[2]:
                usage = (requests, raw, stored)
                base_feasible = fits(usage)
                for increment in increments:
                    increased = tuple(
                        value + delta for value, delta in zip(usage, increment, strict=True)
                    )
                    if fits(increased):
                        assert base_feasible

    for index, (cap, need) in enumerate(zip(caps, required, strict=True)):
        boundary = [0, 0, 0]
        boundary[index] = cap - need
        assert fits(tuple(boundary))
        boundary[index] += 1
        assert not fits(tuple(boundary))
