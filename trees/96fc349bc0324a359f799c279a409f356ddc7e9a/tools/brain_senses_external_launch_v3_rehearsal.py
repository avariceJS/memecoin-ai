#!/usr/bin/env python3
"""Run one inert, source-pinned synthetic launch-v3 failure rehearsal.

This entry has no provider, credential, transaction, schedule, or production
activation path. Its fixed synthetic envelope and journal are reviewed test
fixtures only. The CLI accepts filesystem paths and a temporary launchd label.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import stat
import sys
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FORBIDDEN_IMPORT_PREFIXES = (
    "tools.brain_senses_external_launch_v2",
    "tools.brain_senses_service_v1_runner",
    "memecoin_ai.sources",
    "memecoin_ai.credentials",
    "memecoin_ai.rpc",
    "httpx",
    "requests",
    "solana",
    "solders",
)
BLOCKED_IMPORT_ATTEMPTS: list[str] = []


def _guard_import(event: str, args: tuple[Any, ...]) -> None:
    if event != "import" or not args or not isinstance(args[0], str):
        return
    name = args[0]
    if any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORT_PREFIXES):
        BLOCKED_IMPORT_ATTEMPTS.append(name)
        raise ImportError("provider_or_credential_import_blocked")


if __name__ == "__main__":
    sys.addaudithook(_guard_import)

from memecoin_research_storage import (  # noqa: E402
    MacOSVolumeProbe,
    StorageConfig,
    StorageGuard,
    VolumeInfo,
    writer_guard,
)
from tools import brain_senses_external_launch_v3 as launch_v3  # noqa: E402
from tools import brain_senses_launch_v3_synthetic_fixture_v1 as fixture  # noqa: E402
from tools.brain_senses_external_verify_v2 import DescriptorReaderV2  # noqa: E402

JOURNAL_NAME = "launch-v3-synthetic-journal-v1.json"
RESULT_SCHEMA = "brain-senses-launch-v3-synthetic-rehearsal-result-v1"
_LABEL_PREFIX = "ai.memecoin.brain.senses.launch-v3-rehearsal-"


class DisposableImageProbe:
    """Qualify only the noowners option of the harness-owned disposable image."""

    def __init__(self, expected_uuid: str) -> None:
        self.expected_uuid = expected_uuid
        self.raw = MacOSVolumeProbe()

    def inspect(self, mount_path: Path) -> VolumeInfo:
        info = self.raw.inspect(mount_path)
        if (
            info.volume_uuid.lower() == self.expected_uuid
            and info.filesystem.lower().strip() == "case-sensitive apfs"
            and info.is_mountpoint
            and not info.read_only
        ):
            options = tuple(option for option in info.mount_options if option != "noowners")
            return replace(info, owners_enabled=True, mount_options=options)
        return info


class RehearsalError(RuntimeError):
    """Sanitized local rehearsal failure."""


def _open_service(guard: Any, relative: str) -> int:
    if not relative.startswith("services/") or ".." in relative.split("/"):
        raise RehearsalError("synthetic_service_path_invalid")
    current = os.dup(guard.mount_fd)
    try:
        device = os.fstat(current).st_dev
        for component in [*guard.config.data_root.split("/"), *relative.split("/")]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            child_stat = os.fstat(child)
            if (
                child_stat.st_dev != device
                or child_stat.st_uid != os.getuid()
                or stat.S_IMODE(child_stat.st_mode) != 0o700
            ):
                os.close(child)
                raise RehearsalError("synthetic_service_directory_identity")
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


@contextlib.contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise RehearsalError("unsafe_lock_file")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def seed_fixture(config_path: Path, probe: DisposableImageProbe) -> str:
    """Create the synthetic service/journal through the configured volume guard."""
    relative = f"services/{fixture.SERVICE_ID}"
    with writer_guard(config_path, min_free_bytes=0, probe=probe) as guard:
        guard.create_directory("services")
        guard.create_directory(relative)
        service_fd = _open_service(guard, relative)
        try:
            with DescriptorReaderV2(service_fd) as reader:
                try:
                    existing = reader.read_bytes(JOURNAL_NAME)
                except FileNotFoundError:
                    existing = None
            if existing is not None:
                if existing != fixture.JOURNAL_BYTES:
                    raise RehearsalError("synthetic_journal_conflict")
            else:
                os.close(service_fd)
                service_fd = _open_service(guard, relative)
                from tools.brain_senses_external_writer_v1 import DescriptorWriter

                def revalidate_before_write() -> None:
                    guard.revalidate()

                with DescriptorWriter(service_fd, before_write=revalidate_before_write) as writer:
                    writer.create(JOURNAL_NAME, fixture.JOURNAL_BYTES, mode=0o600)
            return hashlib.sha256(fixture.JOURNAL_BYTES).hexdigest()
        finally:
            os.close(service_fd)


def _persist_result(path: Path, payload: bytes) -> bool:
    """Create result once; an existing result marks a restart with no replay."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        existing = path.read_bytes()
        try:
            value = json.loads(existing)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RehearsalError("existing_result_invalid") from exc
        if (
            value.get("schema") != RESULT_SCHEMA
            or value.get("synthetic") is not True
            or value.get("status") != "pre_intent_failure_receipted"
        ):
            raise RehearsalError("existing_result_conflict") from None
        return False
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise RehearsalError("result_short_write")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


def run(
    *,
    config_path: Path,
    state_dir: Path,
    result_path: Path,
    label: str,
    probe: DisposableImageProbe | None = None,
) -> int:
    """Run one callback; subsequent process starts stop at the create-only marker."""
    if not label.startswith(_LABEL_PREFIX) or len(label) > 128:
        raise RehearsalError("temporary_label_invalid")
    if (
        not config_path.is_absolute()
        or not state_dir.is_absolute()
        or not result_path.is_absolute()
    ):
        raise RehearsalError("paths_must_be_absolute")
    if result_path.exists():
        _persist_result(result_path, b"")
        return 0
    probe = probe or DisposableImageProbe(_expected_uuid(config_path))

    def guard_factory(path: Path) -> contextlib.AbstractContextManager[StorageGuard]:
        return writer_guard(path, min_free_bytes=0, probe=probe)

    active_guard: list[Any] = []

    @contextlib.contextmanager
    def storage_lock() -> Iterator[None]:
        with guard_factory(config_path) as held:
            active_guard.append(held)
            try:
                yield
            finally:
                active_guard.pop()

    @contextlib.contextmanager
    def host_lock() -> Iterator[None]:
        with _file_lock(state_dir / "host.lock"):
            yield

    @contextlib.contextmanager
    def predecessor_lock() -> Iterator[None]:
        with _file_lock(state_dir / "predecessor.lock"):
            yield

    @contextlib.contextmanager
    def service_lock() -> Iterator[None]:
        with _file_lock(state_dir / "service.lock"):
            yield

    locks = launch_v3.OrderedLocks(host_lock, storage_lock, predecessor_lock, service_lock)

    def snapshot_reader(binding: launch_v3.PlanBinding) -> launch_v3.JournalSnapshot:
        if len(active_guard) != 1:
            raise RehearsalError("storage_guard_not_held")
        service_fd = _open_service(active_guard[0], f"services/{fixture.SERVICE_ID}")
        try:
            with DescriptorReaderV2(service_fd) as reader:
                raw = reader.read_bytes(JOURNAL_NAME)
        finally:
            os.close(service_fd)
        return fixture.verify_journal(raw, binding)

    # Independently capture the exact descriptor-bound fixture bytes before the
    # callback; the receipt writer can only report verified_none after two
    # locked verifier reads still match the literal journal digest.
    with locks.acquire():
        before = snapshot_reader(
            fixture.validate_activation(fixture.ACTIVATION_BYTES, time.time_ns())
        )
        before_tail = before.event_tail_sha256

    def dispatch(_binding: launch_v3.PlanBinding) -> None:
        raise launch_v3.V3DispatchFailure(
            launch_v3.TypedFailure("pre-dispatch", "pre_dispatch_failed")
        )

    failure_sink = launch_v3.descriptor_failure_sink(
        config_path=config_path,
        host_lock=host_lock,
        guard_factory=guard_factory,
    )
    try:
        launch_v3.dispatch_once(
            fixture.PLAN_BYTES,
            plan_validator=lambda raw, started: fixture.validate_synthetic_plan(
                raw, started.utc_ns
            ),
            locks=locks,
            dispatch=dispatch,
            proof_locks=locks,
            snapshot_reader=snapshot_reader,
            failure_sink=failure_sink,
        )
    except launch_v3.V3DispatchFailure:
        pass
    else:
        raise RehearsalError("synthetic_dispatch_did_not_fail")

    binding = fixture.validate_activation(fixture.ACTIVATION_BYTES, time.time_ns())
    with locks.acquire():
        after = snapshot_reader(binding)
        journal_after_sha = hashlib.sha256(fixture.JOURNAL_BYTES).hexdigest()
        if before_tail != after.event_tail_sha256 or journal_after_sha != fixture.JOURNAL_SHA256:
            raise RehearsalError("synthetic_journal_changed")

    # Read the receipt under a fresh descriptor-bound storage guard and validate
    # its fixed schema independently of the writer's success return value.
    receipt_raw = _read_receipt(config_path, probe, fixture.SERVICE_ID, fixture.BUCKET)
    from tools.brain_senses_failure_receipt_v1 import validate_record

    receipt = json.loads(receipt_raw)
    validate_record(receipt)
    if (
        receipt["intent_state"] != "verified_none"
        or receipt["verified_event_tail_sha256"] != before_tail
    ):
        raise RehearsalError("receipt_did_not_verify_empty_journal")
    result = {
        "schema": RESULT_SCHEMA,
        "synthetic": True,
        "label": label,
        "pid": os.getpid(),
        "callback_start_utc_ns": receipt["callback_start_utc_ns"],
        "receipt_utc_ns": receipt["receipt_utc_ns"],
        "dispatch_attempts": 1,
        "restart_dispatch_attempts": 0,
        "intent_state": receipt["intent_state"],
        "journal_sha256_before": fixture.JOURNAL_SHA256,
        "journal_sha256_after": journal_after_sha,
        "receipt_sha256": hashlib.sha256(receipt_raw).hexdigest(),
        "status": "pre_intent_failure_receipted",
        "provider_calls": 0,
        "credential_reads": 0,
        "blocked_import_attempts": list(BLOCKED_IMPORT_ATTEMPTS),
        "loaded_forbidden_imports": sorted(
            name
            for name in sys.modules
            if any(
                name == prefix or name.startswith(prefix + ".")
                for prefix in FORBIDDEN_IMPORT_PREFIXES
            )
        ),
    }
    _persist_result(result_path, _canonical(result))
    return 0


def _expected_uuid(config_path: Path) -> str:
    volume_uuid = StorageConfig.load(config_path).volume_uuid
    if not isinstance(volume_uuid, str):
        raise RehearsalError("storage_config_uuid_invalid")
    return volume_uuid


def _read_receipt(
    config_path: Path, probe: DisposableImageProbe, service_id: str, bucket: str
) -> bytes:
    with writer_guard(config_path, min_free_bytes=0, probe=probe) as held:
        root_fd = os.dup(held.mount_fd)
        try:
            for part in held.config.data_root.split("/"):
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
                os.close(root_fd)
                root_fd = child
            path = f"diagnostics/external-launch-v1/{service_id}/failure-{bucket}.json"
            with DescriptorReaderV2(root_fd) as reader:
                receipt = reader.read_bytes(path, limit=4096)
                if not isinstance(receipt, bytes):
                    raise RehearsalError("receipt_reader_contract")
                return receipt
        finally:
            os.close(root_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--label", required=True)
    args = parser.parse_args(argv)
    try:
        return run(
            config_path=args.config,
            state_dir=args.state_dir,
            result_path=args.result,
            label=args.label,
        )
    except Exception as exc:
        if isinstance(exc, (RehearsalError, launch_v3.LaunchV3Error)) and exc.args:
            detail = str(exc.args[0])
        else:
            detail = type(exc).__name__
        print(f"synthetic launch-v3 rehearsal failed: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
