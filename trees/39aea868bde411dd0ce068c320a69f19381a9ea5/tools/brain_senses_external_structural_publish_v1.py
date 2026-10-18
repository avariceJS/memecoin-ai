#!/usr/bin/env python3
"""Descriptor-bound sibling-root publication for structural-input-v1.

This call-site only publishes a deterministic manifest rebuilt from sealed v2
captures. It has no provider client and never mutates the sealed source root.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import re
import stat
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

from memecoin_research_storage import StorageError, writer_guard
from tools import brain_senses_external_launch_v2 as launch
from tools import brain_senses_external_schema_v2 as lease_schema
from tools import brain_senses_external_structural_input_v1 as builder
from tools import brain_senses_external_verify_v1 as predecessor_verify
from tools import brain_senses_external_verify_v2 as source_verify
from tools.brain_senses_external_structural_input_schema_v1 import safe_relative
from tools.brain_senses_external_writer_v1 import DescriptorWriter

_SERVICE_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_OUTPUT_PREFIX = "derived/structural-input-v1"


def required_free_bytes() -> int:
    return int(
        lease_schema.DISK_FLOOR_BYTES
        + lease_schema.RAW_LEASE_BYTES
        + lease_schema.PEAK_WRITE_MARGIN_BYTES
        + lease_schema.METADATA_MARGIN_BYTES
    )


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise StorageError(reason)


def _private_directory(fd: int, device: int) -> None:
    entry = os.fstat(fd)
    _require(
        stat.S_ISDIR(entry.st_mode)
        and entry.st_uid == os.getuid()
        and stat.S_IMODE(entry.st_mode) == 0o700
        and entry.st_dev == device,
        "structural_publication_directory_identity",
    )


def _open_relative(guard: Any, relative: str, *, create: bool, minimum: int) -> int:
    """Open/create a private directory entirely beneath the held mount fd."""
    parts = [*guard.config.data_root.split("/"), *relative.split("/")]
    current = os.dup(guard.mount_fd)
    mount = os.fstat(guard.mount_fd)
    try:
        for component in parts:
            if create:
                try:
                    guard.revalidate(min_free_bytes=minimum)
                    os.mkdir(component, 0o700, dir_fd=current)
                    os.fsync(current)
                except FileExistsError:
                    pass
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            try:
                _private_directory(child, mount.st_dev)
            except BaseException:
                os.close(child)
                raise
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


@contextlib.contextmanager
def _existing_shared_lock(directory_fd: int, name: str, *, device: int) -> Iterator[int]:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    except FileNotFoundError as exc:
        raise StorageError("structural_source_lock_missing") from exc
    try:
        entry = os.fstat(fd)
        _require(
            stat.S_ISREG(entry.st_mode)
            and entry.st_dev == device
            and entry.st_uid == os.getuid()
            and entry.st_nlink == 1
            and stat.S_IMODE(entry.st_mode) == 0o600,
            "structural_source_lock_identity",
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StorageError("structural_source_writer_active") from exc
        yield fd
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _same_directory(left: int, right: int, code: str) -> None:
    a = os.fstat(left)
    b = os.fstat(right)
    _require((a.st_dev, a.st_ino) == (b.st_dev, b.st_ino), code)


def publish_after_seal(
    *,
    config_path: Path,
    service_id: str,
    predecessor_relative_root: str,
    expected_plan_sha256: str,
    expected_lease_sources: Mapping[str, str],
    guard_factory: Callable[..., Any] = writer_guard,
    host_lock: Callable[[], Any] = launch.host_lock,
) -> dict[str, Any]:
    """Verify and publish into a sibling derived root under ordered locks.

    The lease root and its source files are opened read-only. Only the sibling
    `derived/structural-input-v1/<service_id>` root receives new files.
    """
    _require(bool(_SERVICE_ID.fullmatch(service_id)), "structural_service_id")
    _require(
        safe_relative(predecessor_relative_root) == predecessor_relative_root
        and predecessor_relative_root.startswith("services/"),
        "structural_predecessor_path",
    )
    minimum = required_free_bytes()
    with host_lock(), guard_factory(config_path, min_free_bytes=minimum) as guard:
        guard.revalidate(min_free_bytes=minimum)
        live_root_id = guard.root_id
        live_volume_uuid = str(guard.volume_uuid)
        source_relative_root = f"services/{service_id}"
        source_fd = _open_relative(guard, source_relative_root, create=False, minimum=minimum)
        predecessor_fd = -1
        output_fd = -1
        try:
            # The predecessor path is independently pinned by the v2 plan. The
            # caller path only enables the required lock order before reading it.
            predecessor_fd = _open_relative(
                guard, predecessor_relative_root, create=False, minimum=minimum
            )
            with launch.predecessor_read_lock(predecessor_fd):
                with _existing_shared_lock(
                    source_fd, lease_schema.LOCK_FILE, device=os.fstat(source_fd).st_dev
                ):
                    with source_verify.DescriptorReaderV2(source_fd) as source_reader:
                        _same_directory(
                            source_reader.fd,
                            source_fd,
                            "structural_reader_fd_mismatch",
                        )
                        plan_raw = source_reader.read_bytes(lease_schema.PLAN_FILE)
                        plan = source_verify.decode(plan_raw)
                        _require(
                            source_verify.sha(plan_raw) == expected_plan_sha256,
                            "structural_plan_digest",
                        )
                        _require(
                            isinstance(plan, dict)
                            and plan.get("service_id") == service_id
                            and plan.get("storage", {}).get("relative_root") == source_relative_root
                            and plan.get("predecessor", {}).get("relative_root")
                            == predecessor_relative_root,
                            "structural_plan_path_binding",
                        )
                        _require(
                            plan.get("storage", {}).get("root_id") == live_root_id
                            and str(plan.get("storage", {}).get("volume_uuid", "")).lower()
                            == live_volume_uuid.lower(),
                            "structural_plan_storage_identity",
                        )
                        _require(plan.get("caps") == lease_schema.CAPS, "structural_plan_caps")
                        _require(
                            plan.get("code_sha256") == dict(expected_lease_sources),
                            "structural_plan_source_pins",
                        )
                        with predecessor_verify.DescriptorReader(
                            predecessor_fd
                        ) as predecessor_reader:
                            artifact = builder.compile_from_sealed_v2(
                                source_reader,
                                predecessor_reader,
                                expected_plan_sha256=expected_plan_sha256,
                                expected_lease_sources=expected_lease_sources,
                            )
                            _require(
                                artifact.get("publishable") is True,
                                "structural_artifact_not_publishable",
                            )
                            output_relative_root = f"{_OUTPUT_PREFIX}/{service_id}"
                            output_fd = _open_relative(
                                guard, output_relative_root, create=True, minimum=minimum
                            )
                            _private_directory(output_fd, os.fstat(guard.mount_fd).st_dev)
                            with DescriptorWriter(
                                output_fd,
                                before_write=lambda: guard.revalidate(min_free_bytes=minimum),
                            ) as writer:
                                _same_directory(
                                    writer.root_fd,
                                    output_fd,
                                    "structural_writer_fd_mismatch",
                                )
                                with writer.lock(".writer.lock"):
                                    guard.revalidate(min_free_bytes=minimum)
                                    return builder.publish_create_only(
                                        writer,
                                        artifact,
                                        source_reader=source_reader,
                                        predecessor_reader=predecessor_reader,
                                        expected_plan_sha256=expected_plan_sha256,
                                        expected_lease_sources=expected_lease_sources,
                                        relative_directory="",
                                        service_id=service_id,
                                        source_relative_root=source_relative_root,
                                        output_relative_root=output_relative_root,
                                        identity_check=lambda: {
                                            "root_id": guard.root_id,
                                            "volume_uuid": str(guard.volume_uuid),
                                        },
                                        revalidate=lambda: guard.revalidate(min_free_bytes=minimum),
                                    )
        finally:
            if output_fd >= 0:
                os.close(output_fd)
            if predecessor_fd >= 0:
                os.close(predecessor_fd)
            os.close(source_fd)
