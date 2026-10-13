from __future__ import annotations

import contextlib
import copy
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO))

from tools import brain_senses_capacity_coordinator_v2 as capacity  # noqa: E402
from tools import brain_senses_gecko_capture_only_v2_health_v1 as health  # noqa: E402
from tools import brain_senses_gecko_capture_only_v2_terminal_usage_v1 as terminal  # noqa: E402
from tools import brain_senses_gecko_usage_inventory_v1 as inventory  # noqa: E402


def _receipt() -> dict[str, object]:
    return {
        "schema": terminal.SCHEMA,
        "schema_version": 2,
        "verifier": {
            "module": "tools.brain_senses_gecko_capture_only_v2_terminal_usage_v1",
            "schema": terminal.SCHEMA,
            "version": 2,
        },
        "release_id": terminal.RELEASE_ID,
        "request_root": terminal.ROOT,
        "health_root": terminal.HEALTH_ROOT,
        "storage": dict(terminal.EXPECTED_STORAGE),
        "global_root_census": {"paths": [terminal.ROOT, terminal.HEALTH_ROOT], "sha256": "f" * 64},
        "source": {
            "source_commit": terminal.SOURCE_COMMIT,
            "source_sha256": "a" * 64,
            "release_sha256": terminal.EXPECTED_RELEASE_SHA256,
            "policy_sha256": terminal.EXPECTED_POLICY_SHA256,
            "binding_sha256": terminal.EXPECTED_BINDING_SHA256,
            "policy_source_sha256": terminal.EXPECTED_SOURCE_SHA256,
            "population_sha256": terminal.EXPECTED_POPULATION_SHA256,
            "plan_record_sha256": terminal.EXPECTED_PLAN_RECORD_SHA256,
        },
        "health_stop": {
            "snapshot_sha256": terminal.EXPECTED_HEALTH_SNAPSHOT_SHA256,
            "source_sha256": terminal.EXPECTED_SOURCE_SHA256,
            "status": "stopped",
            "reason": "manual_reconciliation_required",
            "stop_latched": True,
            "observed_ns": terminal.EXPECTED_STOP_NS,
            "transition_tail_sha256": terminal.EXPECTED_STOP_EVENT_SHA256,
            "request_prefix_sha256": terminal.EXPECTED_REQUEST_PREFIX_SHA256,
            "health_prefix_sha256": terminal.EXPECTED_HEALTH_PREFIX_SHA256,
        },
        "census": {
            "scheduled_slots": 6,
            "distinct_selected_pools": list(terminal.EXPECTED_SLOTS),
            "intents": 4,
            "dispatches": 4,
            "http_200_received_results": 4,
            "durable_confirmations": 4,
            "skips": 0,
            "unresolved": 0,
            "no_intent_no_skip_unknown_slots": 2,
            "raw_response_bytes": 3041,
            "attempts": [],
            "request_prefix_sha256": terminal.EXPECTED_REQUEST_PREFIX_SHA256,
            "coordinator_sequence": 134,
            "coordinator_tail_sha256": "b" * 64,
            "latest_request_mtime_ns": terminal.EXPECTED_STOP_NS,
            "stop_observed_ns": terminal.EXPECTED_STOP_NS,
        },
        "exclusion": {
            "launchd_label": terminal.LABEL,
            "launchd_loaded": False,
            "shared_service_lock_held_during_replay": True,
            "old_writer_excluded": True,
            "post_stop_request_artifacts": 0,
            "cursor_carryover": False,
            "provider_io_during_reconciliation": False,
        },
        "authority": {
            "retry_authorized": False,
            "dispatch_authorized": False,
            "market_source_qualified": False,
            "training_admission": False,
            "economic_admission": False,
        },
    }


def test_exact_receipt_accepts_recomputed_shape_and_rejects_tampering() -> None:
    expected = _receipt()
    terminal.validate_receipt(expected, expected=expected)

    cases = [
        ("source", "source_commit", "e7564f0"),
        ("source", "source_sha256", "c" * 64),
        ("storage", "volume_uuid", "00000000-0000-0000-0000-000000000000"),
        ("global_root_census", "sha256", "9" * 64),
        ("health_stop", "transition_tail_sha256", "d" * 64),
        ("health_stop", "observed_ns", terminal.EXPECTED_STOP_NS + 1),
        ("census", "no_intent_no_skip_unknown_slots", 1),
        ("census", "raw_response_bytes", 3040),
        ("census", "coordinator_tail_sha256", "e" * 64),
        ("exclusion", "launchd_loaded", True),
        ("exclusion", "old_writer_excluded", False),
        ("exclusion", "post_stop_request_artifacts", 1),
        ("authority", "dispatch_authorized", True),
    ]
    for section, key, value in cases:
        changed = copy.deepcopy(expected)
        changed[section][key] = value  # type: ignore[index]
        with pytest.raises(terminal.TerminalUsageError):
            terminal.validate_receipt(changed, expected=expected)


def test_receipt_requires_full_source_commit_and_verifier_identity() -> None:
    receipt = _receipt()
    assert receipt["source"]["source_commit"] == "e7564f0eac31a9e11588c3fdbd4f2b0a1ee90769"  # type: ignore[index]
    assert receipt["verifier"]["version"] == 2  # type: ignore[index]
    receipt["verifier"]["version"] = 1  # type: ignore[index]
    with pytest.raises(terminal.TerminalUsageError):
        terminal.validate_receipt(receipt, expected=_receipt())


def test_root_census_must_be_stable_and_match_outer_inventory() -> None:
    terminal._verify_root_census(
        [terminal.ROOT, terminal.HEALTH_ROOT],
        "a" * 64,
        [terminal.ROOT, terminal.HEALTH_ROOT],
        "a" * 64,
        expected_sha256="a" * 64,
    )
    with pytest.raises(terminal.TerminalUsageError, match="a078_global_root_census_changed"):
        terminal._verify_root_census(
            [terminal.ROOT], "a" * 64, [terminal.ROOT, terminal.HEALTH_ROOT], "a" * 64
        )
    with pytest.raises(terminal.TerminalUsageError, match="a078_global_root_census_input"):
        terminal._verify_root_census(
            [terminal.ROOT],
            "a" * 64,
            [terminal.ROOT],
            "a" * 64,
            expected_sha256="b" * 64,
        )


def test_pinned_metadata_receipt_replays_source_and_denominator_pins() -> None:
    receipt = terminal.load_pinned_receipt()
    assert receipt["source"]["source_commit"] == terminal.SOURCE_COMMIT
    assert (
        terminal.sha256(terminal.canonical_json(receipt["source"]["source_sha256"]))
        == terminal.EXPECTED_SOURCE_SHA256
    )
    assert receipt["census"]["intents"] == 4
    assert receipt["census"]["no_intent_no_skip_unknown_slots"] == 2
    assert receipt["census"]["raw_response_bytes"] == 3041
    assert receipt["global_root_census"]["paths"]


def test_reconciliation_rejects_any_other_health_root_before_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def should_not_run(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("unexpected root access")

    monkeypatch.setattr(terminal, "build_receipt", should_not_run)
    with pytest.raises(terminal.TerminalUsageError, match="a078_health_root_not_allowlisted"):
        terminal.reconcile_stopped_health_root(
            object(),  # type: ignore[arg-type]
            root="services/gecko-market-v1/host-health-v1/another-release",
            root_inventory_sha256="f" * 64,
            storage=terminal.EXPECTED_STORAGE,
            receipt=_receipt(),
        )


def test_reconciliation_returns_only_verified_none_for_exact_replayed_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _receipt()
    monkeypatch.setattr(terminal, "launchd_label_loaded", lambda: False)
    monkeypatch.setattr(terminal, "build_receipt", lambda *_args, **_kwargs: expected)
    monkeypatch.setattr(terminal, "load_pinned_receipt", lambda: expected)

    result = terminal.reconcile_stopped_health_root(
        object(),  # type: ignore[arg-type]
        root=terminal.HEALTH_ROOT,
        root_inventory_sha256="f" * 64,
        storage=terminal.EXPECTED_STORAGE,
    )
    assert result["status"] == "verified_none"
    assert result["attempts"] == []
    assert result["root"] == terminal.HEALTH_ROOT


def test_tampered_pinned_receipt_cannot_reconcile(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _receipt()
    tampered = copy.deepcopy(expected)
    tampered["census"]["intents"] = 5  # type: ignore[index]
    monkeypatch.setattr(terminal, "launchd_label_loaded", lambda: False)
    monkeypatch.setattr(terminal, "build_receipt", lambda *_args, **_kwargs: expected)

    with pytest.raises(terminal.TerminalUsageError, match="a078_receipt_census_mismatch"):
        terminal.reconcile_stopped_health_root(
            object(),  # type: ignore[arg-type]
            root=terminal.HEALTH_ROOT,
            root_inventory_sha256="f" * 64,
            storage=terminal.EXPECTED_STORAGE,
            receipt=tampered,
        )


def _install_health_inventory_fakes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, status: str
) -> SimpleNamespace:
    root_fd = os.open(tmp_path, os.O_RDONLY)
    lock_path = tmp_path / "test-lock"
    lock_path.touch(mode=0o600)
    lock_fd = os.open(lock_path, os.O_RDONLY)
    device = os.fstat(root_fd).st_dev
    storage = {"device": device, "inode": os.fstat(root_fd).st_ino}

    @contextlib.contextmanager
    def writer_lock(_path: str, *, blocking: bool = True):
        del blocking
        yield None

    writer = SimpleNamespace(
        device=device,
        root_fd=root_fd,
        lock=writer_lock,
        read_bytes=lambda _path, max_bytes: capacity.canonical({"root_binding": storage})[
            :max_bytes
        ],
    )
    monkeypatch.setattr(inventory, "_shared_root_lock", lambda *_args: os.dup(lock_fd))
    monkeypatch.setattr(inventory, "_open_relative_directory", lambda *_args: os.dup(root_fd))
    monkeypatch.setattr(
        health,
        "read_snapshot",
        lambda *_args, **_kwargs: {"observed_ns": 1, "status": status, "stop_latched": True},
    )
    return SimpleNamespace(writer=writer, storage=storage, root_fd=root_fd, lock_fd=lock_fd)


def test_usage_inventory_routes_only_exact_stopped_a078_root_to_receipt_replay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install_health_inventory_fakes(monkeypatch, tmp_path, status="stopped")
    calls: list[str] = []

    def reconcile(_writer: object, **kwargs: object) -> dict[str, object]:
        calls.append(str(kwargs["root"]))
        return {"root": kwargs["root"], "status": "verified_none", "attempts": []}

    monkeypatch.setattr(terminal, "reconcile_stopped_health_root", reconcile)
    try:
        result = inventory._load_present_root(
            fake.writer,
            root=terminal.HEALTH_ROOT,
            root_inventory_sha256="a" * 64,
        )
        assert result["status"] == "verified_none"
        assert calls == [terminal.HEALTH_ROOT]
        calls.clear()
        with pytest.raises(inventory.InventoryError, match="gecko_health_stop_latched"):
            inventory._load_present_root(
                fake.writer,
                root="services/gecko-market-v1/host-health-v1/other-release",
                root_inventory_sha256="a" * 64,
            )
        assert calls == []
    finally:
        os.close(fake.root_fd)
        os.close(fake.lock_fd)
