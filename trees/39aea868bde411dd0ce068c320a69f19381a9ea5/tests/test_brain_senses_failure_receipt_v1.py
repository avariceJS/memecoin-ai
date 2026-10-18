from __future__ import annotations

import contextlib
import hashlib
import json
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from memecoin_research_storage import StorageError
from tools import brain_senses_failure_receipt_v1 as receipt_v1
from tools.brain_senses_external_writer_v1 import DescriptorWriter

BUCKET = "20261012T0615Z"
SERVICE = "service-v3"
PLAN_SHA = "a" * 64


class FakeGuard:
    def __init__(self, mountpoint: Path, *, detach_check: bool = False) -> None:
        self.mountpoint = mountpoint
        self.mount_fd = os.open(mountpoint, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.config = SimpleNamespace(data_root="BrainData")
        self.root_id = "synthetic-root"
        self.volume_uuid = "synthetic-volume"
        self.detach_check = detach_check

    def revalidate(self) -> None:
        if not self.detach_check:
            return
        current = os.stat(self.mountpoint)
        held = os.fstat(self.mount_fd)
        if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
            raise StorageError("volume_detached")

    def close(self) -> None:
        os.close(self.mount_fd)


class GuardContext:
    def __init__(self, guard: FakeGuard) -> None:
        self.guard = guard

    def __enter__(self) -> FakeGuard:
        return self.guard

    def __exit__(self, *_args: object) -> None:
        self.guard.close()


def _layout(base: Path) -> tuple[Path, Path, str]:
    mount = base / "mount"
    data = mount / "BrainData"
    service = data / "services" / SERVICE
    service.mkdir(mode=0o700, parents=True)
    os.chmod(mount, 0o700)
    os.chmod(data, 0o700)
    os.chmod(data / "services", 0o700)
    os.chmod(service, 0o700)
    manifest = service / "external-plan-v3.json"
    manifest.write_bytes(b'{"pinned":"service-manifest"}\n')
    os.chmod(manifest, 0o600)
    return mount, service, hashlib.sha256(manifest.read_bytes()).hexdigest()


def _proof(*, ids: tuple[str, ...] = (), bucket: str = BUCKET) -> receipt_v1.JournalProof:
    return receipt_v1.JournalProof(
        bucket=bucket,
        reservation_ids_before=ids,
        reservation_ids_after=ids,
        intent_ids_before=ids,
        intent_ids_after=ids,
        event_tail_sha256="f" * 64,
        fresh_independent_reads=True,
        required_locks_held=True,
    )


def _record(**overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "service_id": SERVICE,
        "plan_sha256": PLAN_SHA,
        "scheduled_bucket": BUCKET,
        "callback_start_utc_ns": 1_000_000_000,
        "callback_start_monotonic_ns": 10_000,
        "phase": "pre-dispatch",
        "exception": StorageError("secret-key=super-secret https://secret.invalid/path"),
        "reason_code": "storage_guard_failed",
        "journal_proof": _proof(),
        "receipt_utc_ns": 2_000_000_000,
        "receipt_monotonic_ns": 20_000,
    }
    arguments.update(overrides)
    return receipt_v1.build_record(**arguments)


def _persist(
    mount: Path,
    record: dict[str, Any],
    *,
    writer_factory: Any = DescriptorWriter,
    guard: FakeGuard | None = None,
) -> bool:
    active = guard or FakeGuard(mount)
    try:
        return receipt_v1.persist_record_with_guard(
            active,
            record,
            writer_factory=writer_factory,
        )
    finally:
        if guard is None:
            active.close()


def _best_effort(
    mount: Path,
    *,
    service_id: str = SERVICE,
    guard: FakeGuard | None = None,
    writer_factory: Any = DescriptorWriter,
) -> bool:
    @contextlib.contextmanager
    def guard_context(_path: Path) -> Any:
        active = guard or FakeGuard(mount)
        try:
            yield active
        finally:
            active.close()

    return receipt_v1.best_effort_record_failure(
        config_path=mount.parent / "storage.json",
        service_id=service_id,
        plan_sha256=PLAN_SHA,
        scheduled_bucket=BUCKET,
        callback_start_utc_ns=1_000_000_000,
        callback_start_monotonic_ns=10_000,
        phase="pre-dispatch",
        exception=StorageError("test failure"),
        reason_code="storage_guard_failed",
        guard_factory=guard_context,
        host_lock=contextlib.nullcontext,
        writer_factory=writer_factory,
    )


def test_schema_is_exact_and_exception_path_environment_are_not_serialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "env-secret-token"
    monkeypatch.setenv("DIAGNOSTIC_SECRET_FIXTURE", secret)
    record = _record(
        exception=RuntimeError(f"{secret} endpoint=https://private.invalid/key?token=secret"),
        reason_code="untrusted path /private/private-key",
    )
    raw = receipt_v1.canonical(record)
    assert set(record) == {
        "schema",
        "schema_version",
        "service_id",
        "plan_sha256",
        "scheduled_bucket",
        "callback_start_utc_ns",
        "callback_start_monotonic_ns",
        "receipt_utc_ns",
        "receipt_monotonic_ns",
        "phase",
        "exception_class",
        "reason_code",
        "intent_state",
        "verified_event_tail_sha256",
        "diagnostic_source_sha256",
    }
    assert record["exception_class"] == "RuntimeError"
    assert record["reason_code"] == "unclassified"
    assert record["intent_state"] == "verified_none"
    assert record["verified_event_tail_sha256"] == "f" * 64
    assert secret.encode() not in raw
    assert b"private.invalid" not in raw
    assert b"/private/private-key" not in raw
    assert b"DIAGNOSTIC_SECRET_FIXTURE" not in raw
    receipt_v1.validate_record(json.loads(raw))


def test_validator_rejects_malformed_unhashable_enum_values() -> None:
    record = _record()
    record["phase"] = []
    with pytest.raises(receipt_v1.FailureReceiptError):
        receipt_v1.validate_record(record)


def test_intent_state_requires_fresh_locked_unchanged_independent_proof() -> None:
    assert _record(journal_proof=None)["intent_state"] == "unknown"
    assert _record(journal_proof=_proof(ids=("reservation-1",)))["intent_state"] == "possibly_spent"
    changed = receipt_v1.JournalProof(
        bucket=BUCKET,
        reservation_ids_before=(),
        reservation_ids_after=("reservation-1",),
        intent_ids_before=(),
        intent_ids_after=("intent-1",),
        event_tail_sha256="f" * 64,
        fresh_independent_reads=True,
        required_locks_held=True,
    )
    assert _record(journal_proof=changed)["intent_state"] == "possibly_spent"
    unlocked = receipt_v1.JournalProof(**{**_proof().__dict__, "required_locks_held": False})
    assert _record(journal_proof=unlocked)["intent_state"] == "unknown"
    assert _record(scheduled_bucket=None, journal_proof=_proof())["intent_state"] == "unknown"


def test_phase_reason_and_exception_values_are_allowlisted() -> None:
    cases = (
        ("activation", "launch_gate_failed", ValueError("private"), "ValueError"),
        (
            "predecessor",
            "predecessor_verification_failed",
            RuntimeError("private"),
            "RuntimeError",
        ),
        (
            "service-verify",
            "service_verification_failed",
            type("PrivateExceptionName", (Exception,), {})("secret"),
            "Other",
        ),
        ("window", "window_missed", OSError("private"), "OSError"),
        ("pre-dispatch", "clock_preflight_failed", TimeoutError("private"), "TimeoutError"),
        ("post-intent", "post_intent_failed", StorageError("private"), "StorageError"),
    )
    for phase, reason, exception, expected_class in cases:
        record = _record(
            phase=phase,
            reason_code=reason,
            exception=exception,
        )
        assert record["phase"] == phase
        assert record["reason_code"] == reason
        assert record["exception_class"] == expected_class


def test_create_is_canonical_idempotent_and_conflicts_never_overwrite(tmp_path: Path) -> None:
    mount, service, manifest_sha = _layout(tmp_path)
    before_manifest = (service / "external-plan-v3.json").read_bytes()
    record = _record()
    path = mount / "BrainData" / receipt_v1.failure_relative_path(SERVICE, BUCKET)
    assert _persist(mount, record) is True
    first = path.read_bytes()
    assert first == receipt_v1.canonical(record)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for directory in (
        path.parent.parent.parent,
        path.parent.parent,
        path.parent,
    ):
        metadata = directory.stat()
        assert stat.S_IMODE(metadata.st_mode) == 0o700
        assert metadata.st_uid == os.getuid()
    assert _persist(mount, record) is True
    assert path.read_bytes() == first
    conflict = _record(reason_code="callback_failed")
    assert _persist(mount, conflict) is False
    assert path.read_bytes() == first
    assert (
        hashlib.sha256((service / "external-plan-v3.json").read_bytes()).hexdigest() == manifest_sha
    )
    assert (service / "external-plan-v3.json").read_bytes() == before_manifest


def test_invalid_service_or_symlink_directory_fails_closed(tmp_path: Path) -> None:
    mount, _service, _manifest_sha = _layout(tmp_path)
    assert not _best_effort(mount, service_id="../secret")

    data = mount / "BrainData"
    diagnostics = data / "diagnostics"
    diagnostics.symlink_to(tmp_path, target_is_directory=True)
    assert _best_effort(mount) is False
    assert not (tmp_path / "external-launch-v1").exists()


def test_forced_detach_after_create_leaves_underlay_unchanged_and_no_retry(
    tmp_path: Path,
) -> None:
    mount, service, manifest_sha = _layout(tmp_path)
    original_service_manifest = (service / "external-plan-v3.json").read_bytes()
    underlay = tmp_path / "underlay"
    underlay_service = underlay / "BrainData" / "services" / SERVICE
    underlay_service.mkdir(mode=0o700, parents=True)
    underlay_manifest = underlay_service / "external-plan-v3.json"
    underlay_manifest.write_bytes(b"underlay-sentinel\n")
    os.chmod(underlay_manifest, 0o600)
    baseline = receipt_v1.canonical(
        {
            path.relative_to(underlay).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in underlay.rglob("*")
            if path.is_file()
        }
    )
    guard = FakeGuard(mount, detach_check=True)
    writer_calls = 0

    def detaching_writer(root_fd: int, *, before_write: Any = None) -> Any:
        def detach_at_create(operation: str) -> None:
            nonlocal writer_calls
            if operation == "create":
                writer_calls += 1
                mounted_aside = tmp_path / "detached-volume"
                os.rename(mount, mounted_aside)
                os.rename(underlay, mount)

        return DescriptorWriter(root_fd, before_write=before_write, fault_hook=detach_at_create)

    result = _best_effort(
        mount,
        guard=guard,
        writer_factory=detaching_writer,
    )
    assert result is False
    assert writer_calls == 1
    assert not (mount / "BrainData" / "diagnostics").exists()
    assert (mount / "BrainData" / "services" / SERVICE / "external-plan-v3.json").read_bytes() == (
        b"underlay-sentinel\n"
    )
    after = receipt_v1.canonical(
        {
            path.relative_to(mount).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in mount.rglob("*")
            if path.is_file()
        }
    )
    assert after == baseline
    detached_tree = tmp_path / "detached-volume"
    assert (detached_tree / "BrainData" / "diagnostics" / "external-launch-v1" / SERVICE).exists()
    detached_manifest = detached_tree / "BrainData" / "services" / SERVICE / "external-plan-v3.json"
    assert detached_manifest.read_bytes() == original_service_manifest
    assert hashlib.sha256(detached_manifest.read_bytes()).hexdigest() == manifest_sha


def test_sink_loss_does_not_mask_original_exception_or_use_fallback(tmp_path: Path) -> None:
    mount, _service, _manifest_sha = _layout(tmp_path)

    class FailingGuard(FakeGuard):
        def revalidate(self) -> None:
            raise StorageError("synthetic storage loss with secret=do-not-log")

    @contextlib.contextmanager
    def failed_guard(_path: Path) -> Iterator[FailingGuard]:
        guard = FailingGuard(mount)
        try:
            yield guard
        finally:
            guard.close()

    original = RuntimeError("original failure with api_key=must-not-appear")
    try:
        raise original
    except RuntimeError as caught:
        result = receipt_v1.best_effort_record_failure(
            config_path=tmp_path / "storage.json",
            service_id=SERVICE,
            plan_sha256=PLAN_SHA,
            scheduled_bucket=BUCKET,
            callback_start_utc_ns=1_000_000_000,
            callback_start_monotonic_ns=10_000,
            phase="pre-dispatch",
            exception=caught,
            reason_code="storage_guard_failed",
            guard_factory=failed_guard,
            host_lock=contextlib.nullcontext,
        )
        assert result is False
        assert caught is original
        assert str(caught) == "original failure with api_key=must-not-appear"
    assert not (mount / "BrainData" / "diagnostics").exists()
