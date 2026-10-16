#!/usr/bin/env python3
"""Production-gated v5 capture factory for externally pinned held Data and storage."""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import stat
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    _REPO_ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_REPO_ROOT))
    sys.path.insert(0, str(_REPO_ROOT / "src"))

import memecoin_research_storage as storage
from memecoin_research_storage import ReadOnlyStorageGuard, StorageConfig, StorageGuard
from tools import brain_host_tree_capture_v5 as capture
from tools.brain_host_held_data_t_v1 import (
    REQUIRED_ROOT_ROLES,
    DarwinDataOS,
    HeldDataT,
    approved_data_plan_sha256,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

SCHEMA = "brain-host-tree-capture-v5-production-session"
PRODUCTION_FREE_SPACE_FLOOR_BYTES = 8 * 1024**3
VERIFY_SCRATCH_DB_RESERVE_BYTES = capture.MAX_SCRATCH_BYTES
VERIFY_SCRATCH_FREE_FLOOR_BYTES = 256 * 1024**2
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_ROOT_ID = re.compile(r"[0-9a-f]{32}\Z")


class ProductionCaptureV5Error(ValueError):
    """Fail-closed source/storage binding errors for the v5 production gate."""


def _open_absolute_directory_nofollow(path: Path) -> tuple[int, tuple[tuple[int, ...], ...]]:
    if (
        not path.is_absolute()
        or ".." in path.parts
        or Path(os.path.normpath(path)) != path
        or path == Path("/")
    ):
        raise ProductionCaptureV5Error("scratch_path_not_canonical")
    current = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    identities: list[tuple[int, ...]] = [_scratch_path_sig(os.fstat(current))]
    try:
        for component in path.parts[1:]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            child_stat = os.fstat(child)
            if not stat.S_ISDIR(child_stat.st_mode):
                os.close(child)
                raise ProductionCaptureV5Error("scratch_path_component_not_directory")
            identities.append(_scratch_path_sig(child_stat))
            os.close(current)
            current = child
        return current, tuple(identities)
    except BaseException:
        os.close(current)
        raise


def _scratch_path_sig(st: os.stat_result) -> tuple[int, ...]:
    return st.st_dev, st.st_ino, st.st_mode, st.st_uid


@dataclass
class PinnedScratchV5:
    """Held writable RecoveryOS scratch directory with a pinned no-follow path chain."""

    path: Path
    fd: int
    identity: tuple[int, int]
    chain_identities: tuple[tuple[int, ...], ...]
    filesystem_identity: tuple[str, tuple[int, ...], str, str]
    volume_uuid: str | None

    @classmethod
    def open(cls, path: Path) -> PinnedScratchV5:
        try:
            held_fd, chain = _open_absolute_directory_nofollow(Path(path))
        except ProductionCaptureV5Error:
            raise
        except Exception as exc:
            raise ProductionCaptureV5Error("scratch_path_open_failed") from exc
        try:
            st = os.fstat(held_fd)
            if (
                not stat.S_ISDIR(st.st_mode)
                or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) != 0o700
            ):
                raise ProductionCaptureV5Error("scratch_directory_private_identity_invalid")
            if capture._acl_bytes(held_fd) is not None:
                raise ProductionCaptureV5Error("scratch_directory_acl_present")
            fs = DarwinDataOS().filesystem(held_fd)
            fs_identity = _scratch_filesystem_identity(fs)
            volume_uuid = None
            if fs_identity[0] in {"apfs", "hfs"}:
                try:
                    info = DarwinDataOS().disk_info(fs_identity[2])
                    candidate_uuid = info.get("VolumeUUID", info.get("APFSVolumeUUID"))
                    if isinstance(candidate_uuid, str) and _UUID.fullmatch(candidate_uuid.lower()):
                        volume_uuid = candidate_uuid.lower()
                except Exception as exc:
                    raise ProductionCaptureV5Error("scratch_volume_uuid_unresolved") from exc
            return cls(
                Path(path),
                held_fd,
                (st.st_dev, st.st_ino),
                chain,
                fs_identity,
                volume_uuid,
            )
        except BaseException:
            os.close(held_fd)
            raise

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def validate(
        self,
        *,
        source_device: int | None,
        target_device: int,
        required_free_bytes: int,
    ) -> int:
        if type(required_free_bytes) is not int or required_free_bytes < 0:
            raise ProductionCaptureV5Error("scratch_capacity_request_invalid")
        try:
            current_fd, current_chain = _open_absolute_directory_nofollow(self.path)
        except Exception as exc:
            raise ProductionCaptureV5Error("scratch_path_revalidation_failed") from exc
        try:
            st = os.fstat(self.fd)
            current = os.fstat(current_fd)
            fs = DarwinDataOS().filesystem(self.fd)
            vfs = os.fstatvfs(self.fd)
            free_bytes = int(vfs.f_bavail * vfs.f_frsize)
            if (
                (st.st_dev, st.st_ino) != self.identity
                or (current.st_dev, current.st_ino) != self.identity
                or current_chain != self.chain_identities
                or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) != 0o700
                or capture._acl_bytes(self.fd) is not None
                or _scratch_filesystem_identity(fs) != self.filesystem_identity
                or fs.get("flags", 0) & getattr(os, "ST_RDONLY", 1)
                or vfs.f_flag & getattr(os, "ST_RDONLY", 1)
                or free_bytes < required_free_bytes
            ):
                raise ProductionCaptureV5Error("scratch_identity_or_capacity_invalid")
            if source_device is None:
                _validate_scratch_target_separation(st.st_dev, target_device)
            else:
                _validate_scratch_devices(st.st_dev, source_device, target_device)
            mountpoint = Path(self.filesystem_identity[2])
            if not mountpoint.is_absolute() or mountpoint.stat().st_dev != st.st_dev:
                raise ProductionCaptureV5Error("scratch_mountpoint_identity_invalid")
            return free_bytes
        except ProductionCaptureV5Error:
            raise
        except Exception as exc:
            raise ProductionCaptureV5Error("scratch_revalidation_failed") from exc
        finally:
            os.close(current_fd)


def _scratch_filesystem_identity(fs: Mapping[str, Any]) -> tuple[str, tuple[int, ...], str, str]:
    fs_type = fs.get("fs_type")
    fsid = fs.get("fsid")
    mountpoint = fs.get("mountpoint")
    source = fs.get("source")
    if (
        fs_type not in {"apfs", "hfs"}
        or not isinstance(fsid, tuple)
        or len(fsid) != 2
        or any(type(value) is not int for value in fsid)
        or fsid == (0, 0)
        or not isinstance(mountpoint, str)
        or not mountpoint.startswith("/")
        or not isinstance(source, str)
        or not source
    ):
        raise ProductionCaptureV5Error("scratch_filesystem_identity_invalid")
    return fs_type, fsid, mountpoint, source


def _validate_scratch_devices(scratch_device: int, source_device: int, target_device: int) -> None:
    if (
        any(
            type(device) is not int or device <= 0
            for device in (scratch_device, source_device, target_device)
        )
        or len({scratch_device, source_device, target_device}) != 3
    ):
        raise ProductionCaptureV5Error("scratch_device_separation_invalid")


def _validate_scratch_target_separation(scratch_device: int, target_device: int) -> None:
    if (
        type(scratch_device) is not int
        or scratch_device <= 0
        or type(target_device) is not int
        or target_device <= 0
        or scratch_device == target_device
    ):
        raise ProductionCaptureV5Error("scratch_device_separation_invalid")


def _canonical_config_path(value: Path) -> Path:
    path = Path(value)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or Path(os.path.normpath(path)) != path
        or path.resolve(strict=True) != path
    ):
        raise ProductionCaptureV5Error("storage_config_path_not_canonical")
    return path


def _validate_pins(volume_uuid: str, root_id: str) -> tuple[str, str]:
    normalized_uuid = volume_uuid.lower() if isinstance(volume_uuid, str) else ""
    if (
        not _UUID.fullmatch(normalized_uuid)
        or not isinstance(root_id, str)
        or not _ROOT_ID.fullmatch(root_id)
    ):
        raise ProductionCaptureV5Error("target_storage_pin_invalid")
    return normalized_uuid, root_id


def _require_real_held_view(view: HeldDataT, expected_plan_sha256: str) -> int:
    if (
        type(view) is not HeldDataT
        or view.synthetic is not False
        or type(view.os_api) is not DarwinDataOS
        or not _valid_sha256(expected_plan_sha256)
        or view.trusted_plan_sha256 != expected_plan_sha256
        or approved_data_plan_sha256(view.plan) != view.trusted_plan_sha256
    ):
        raise ProductionCaptureV5Error("production_source_view_not_real_or_unpinned")
    try:
        view.revalidate()
    except Exception as exc:
        raise ProductionCaptureV5Error("production_source_revalidation_failed") from exc
    return os.fstat(view.mount_fd).st_dev


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _preflight_policy_roster(
    view: HeldDataT,
    role_policies: Mapping[str, capture.RoleCapturePolicy],
    *,
    plan_sha256: str,
    expected_policy_roster_sha256: str,
) -> str:
    present_roles = {
        role for role, declaration in view.plan.root_roster.items() if declaration.present
    }
    if set(role_policies) != present_roles:
        raise ProductionCaptureV5Error("production_policy_roster_membership_invalid")
    rows: list[Mapping[str, Any]] = []
    for role in sorted(REQUIRED_ROOT_ROLES):
        declaration = view.plan.root_roster[role]
        if declaration.present:
            policy_spec = role_policies[role]
            try:
                _policy, _raw, actual_digest = capture._validate_policy_pin(policy_spec)
            except Exception as exc:
                raise ProductionCaptureV5Error("production_policy_file_pin_invalid") from exc
            rows.append(
                {
                    "role": role,
                    "present": True,
                    "scope": policy_spec.scope,
                    "policy_sha256": actual_digest,
                }
            )
        else:
            unresolved_reason = declaration.unresolved_reason
            if not isinstance(unresolved_reason, str) or not unresolved_reason:
                raise ProductionCaptureV5Error("production_absent_role_reason_invalid")
            rows.append(
                {
                    "role": role,
                    "present": False,
                    "disposition": "absent_unresolved",
                    "unresolved_reason_sha256": sha256(unresolved_reason.encode("utf-8")),
                    "scope": None,
                    "policy_sha256": None,
                }
            )
    try:
        digest = capture.policy_roster_sha256_v5(plan_sha256, rows)
    except Exception as exc:
        raise ProductionCaptureV5Error("production_policy_roster_invalid") from exc
    if not _valid_sha256(expected_policy_roster_sha256) or digest != expected_policy_roster_sha256:
        raise ProductionCaptureV5Error("production_policy_roster_pin_mismatch")
    return digest


def _assert_guard_pins(
    guard: StorageGuard | ReadOnlyStorageGuard,
    config: StorageConfig,
    expected_volume_uuid: str,
    expected_root_id: str,
) -> None:
    if (
        not isinstance(guard, (StorageGuard, ReadOnlyStorageGuard))
        or getattr(guard, "bootstrap", True)
        or guard.config != config
        or str(guard.volume_uuid).lower() != expected_volume_uuid
        or guard.root_id != expected_root_id
        or str(config.volume_uuid).lower() != expected_volume_uuid
        or config.root_id != expected_root_id
    ):
        raise ProductionCaptureV5Error("target_storage_binding_pin_mismatch")


def _assert_writer_source_separation(
    view: HeldDataT, writer: DescriptorWriter, source_device: int
) -> None:
    if (
        view.plan.data_volume_uuid.lower() == writer.storage_volume_uuid
        or source_device == writer.device
    ):
        raise ProductionCaptureV5Error("source_storage_not_distinct")
    for fd in view.root_fds.values():
        if os.fstat(fd).st_dev != source_device:
            raise ProductionCaptureV5Error("source_root_device_mismatch")


def _assert_writer_identity(
    guard: StorageGuard,
    writer: DescriptorWriter,
    required_free_bytes: int,
    source_device: int,
) -> None:
    try:
        resolved = guard.revalidate_identity(min_free_bytes=required_free_bytes)
        held = os.fstat(writer.root_fd)
        if (
            resolved.available_bytes < required_free_bytes
            or held.st_dev != writer.device
            or writer.device == source_device
        ):
            raise ProductionCaptureV5Error("target_capacity_or_source_binding_invalid")
    except ProductionCaptureV5Error:
        raise
    except Exception as exc:
        raise ProductionCaptureV5Error("target_storage_fast_revalidation_failed") from exc


def _full_boundary(
    view: HeldDataT,
    guard: StorageGuard,
    writer: DescriptorWriter,
    expected_volume_uuid: str,
    expected_root_id: str,
    source_device: int,
    required_reserve_bytes: int,
) -> None:
    required = PRODUCTION_FREE_SPACE_FLOOR_BYTES + required_reserve_bytes
    try:
        view.revalidate()
        _assert_guard_pins(guard, guard.config, expected_volume_uuid, expected_root_id)
        result = guard.revalidate(min_free_bytes=required)
        writer.assert_storage_binding(guard)
        _assert_writer_source_separation(view, writer, source_device)
        if result.available_bytes < required:
            raise ProductionCaptureV5Error("production_capacity_below_required_reserve")
    except ProductionCaptureV5Error:
        raise
    except Exception as exc:
        raise ProductionCaptureV5Error("production_boundary_revalidation_failed") from exc


def _required_boundary_callback(
    view: HeldDataT,
    guard: StorageGuard,
    writer: DescriptorWriter,
    expected_volume_uuid: str,
    expected_root_id: str,
    source_device: int,
) -> Callable[[int], None]:
    """Build the argument-bearing callback used around capture payload work."""

    def check(required_reserve_bytes: int) -> None:
        if type(required_reserve_bytes) is not int or required_reserve_bytes < 0:
            raise ProductionCaptureV5Error("production_reserve_value_invalid")
        _full_boundary(
            view,
            guard,
            writer,
            expected_volume_uuid,
            expected_root_id,
            source_device,
            required_reserve_bytes,
        )

    return check


class ProductionCaptureV5:
    """Bound production candidate session; its context owns the writer and spool."""

    def __init__(
        self,
        view: HeldDataT,
        guard: StorageGuard,
        writer: DescriptorWriter,
        inventory: capture.GlobalInventoryV5,
        *,
        expected_volume_uuid: str,
        expected_root_id: str,
        source_device: int,
    ) -> None:
        self.view = view
        self.guard = guard
        self.writer = writer
        self.inventory = inventory
        self.expected_volume_uuid = expected_volume_uuid
        self.expected_root_id = expected_root_id
        self.source_device = source_device
        self._published = False

    def capture_candidate(self) -> dict[str, Any]:
        if self._published:
            raise ProductionCaptureV5Error("production_candidate_already_published")
        required = (
            self.inventory.scratch_reserve_bytes
            + self.inventory.metadata_reserve_bytes
            + self.inventory.payload_bytes_remaining
            + self.inventory.payload_reserve_bytes
        )
        _full_boundary(
            self.view,
            self.guard,
            self.writer,
            self.expected_volume_uuid,
            self.expected_root_id,
            self.source_device,
            required,
        )
        receipt = capture.capture_candidate_v5(self.inventory)
        _full_boundary(
            self.view,
            self.guard,
            self.writer,
            self.expected_volume_uuid,
            self.expected_root_id,
            self.source_device,
            0,
        )
        self._published = True
        if receipt.get("synthetic_fixture_only") is not False:
            raise ProductionCaptureV5Error("production_candidate_source_mode_invalid")
        return receipt


@contextlib.contextmanager
def production_capture_v5(
    view: HeldDataT,
    storage_config_path: Path,
    *,
    expected_plan_sha256: str,
    expected_policy_roster_sha256: str,
    expected_target_volume_uuid: str,
    expected_target_root_id: str,
    role_policies: Mapping[str, capture.RoleCapturePolicy],
    scratch_reserve_bytes: int,
    payload_reserve_bytes: int = 0,
    metadata_reserve_bytes: int = capture.MAX_METADATA_TOTAL_BYTES,
    max_entries: int = capture.MAX_ENTRIES,
) -> Iterator[ProductionCaptureV5]:
    """Open a fully pinned real-source/target session, then yield candidate tools.

    This publishes only a candidate. It cannot mint T, COMPLETE, restore,
    durability, or erase claims.
    """
    target_uuid, target_root_id = _validate_pins(
        expected_target_volume_uuid, expected_target_root_id
    )
    source_device = _require_real_held_view(view, expected_plan_sha256)
    plan_sha256 = approved_data_plan_sha256(view.plan)
    if plan_sha256 != expected_plan_sha256:
        raise ProductionCaptureV5Error("production_external_plan_pin_mismatch")
    policy_roster_sha256 = _preflight_policy_roster(
        view,
        role_policies,
        plan_sha256=plan_sha256,
        expected_policy_roster_sha256=expected_policy_roster_sha256,
    )
    if view.plan.data_volume_uuid.lower() == target_uuid:
        raise ProductionCaptureV5Error("source_storage_uuid_not_distinct")
    config_path = _canonical_config_path(storage_config_path)
    try:
        config = StorageConfig.load(config_path)
    except Exception as exc:
        raise ProductionCaptureV5Error("canonical_storage_config_invalid") from exc
    if config.root_id != target_root_id or str(config.volume_uuid).lower() != target_uuid:
        raise ProductionCaptureV5Error("target_storage_config_pin_mismatch")

    writer: DescriptorWriter | None = None
    inventory: capture.GlobalInventoryV5 | None = None
    with storage.writer_guard(
        config_path,
        min_free_bytes=PRODUCTION_FREE_SPACE_FLOOR_BYTES,
    ) as guard:
        _assert_guard_pins(guard, config, target_uuid, target_root_id)
        try:
            guard.revalidate(min_free_bytes=PRODUCTION_FREE_SPACE_FLOOR_BYTES)
            writer = DescriptorWriter.from_storage_guard(guard)
            _assert_writer_source_separation(view, writer, source_device)

            def reserve_capacity(required_reserve_bytes: int) -> None:
                if type(required_reserve_bytes) is not int or required_reserve_bytes < 0:
                    raise ProductionCaptureV5Error("production_reserve_value_invalid")
                _assert_writer_identity(
                    guard,
                    writer,
                    PRODUCTION_FREE_SPACE_FLOOR_BYTES + required_reserve_bytes,
                    source_device,
                )

            def full_root_check() -> None:
                reserve = scratch_reserve_bytes + metadata_reserve_bytes + payload_reserve_bytes
                _full_boundary(
                    view,
                    guard,
                    writer,
                    target_uuid,
                    target_root_id,
                    source_device,
                    reserve,
                )

            boundary_check = _required_boundary_callback(
                view,
                guard,
                writer,
                target_uuid,
                target_root_id,
                source_device,
            )

            def fast_check(required_reserve_bytes: int) -> None:
                view.revalidate_mount_fast()
                _assert_writer_identity(
                    guard,
                    writer,
                    PRODUCTION_FREE_SPACE_FLOOR_BYTES + required_reserve_bytes,
                    source_device,
                )

            cadence_entries = 0
            cadence_bytes = 0
            last_full_check = time.monotonic()

            def cadence_check(
                required_reserve_bytes: int,
                entries_delta: int,
                bytes_delta: int,
            ) -> None:
                nonlocal cadence_entries, cadence_bytes, last_full_check
                if (
                    type(required_reserve_bytes) is not int
                    or required_reserve_bytes < 0
                    or type(entries_delta) is not int
                    or entries_delta < 0
                    or type(bytes_delta) is not int
                    or bytes_delta < 0
                ):
                    raise ProductionCaptureV5Error("production_cadence_value_invalid")
                fast_check(required_reserve_bytes)
                cadence_entries += entries_delta
                cadence_bytes += bytes_delta
                now = time.monotonic()
                if (
                    cadence_entries >= 10_000
                    or cadence_bytes >= 256 * 1024**2
                    or now - last_full_check >= 60
                ):
                    _full_boundary(
                        view,
                        guard,
                        writer,
                        target_uuid,
                        target_root_id,
                        source_device,
                        required_reserve_bytes,
                    )
                    cadence_entries = 0
                    cadence_bytes = 0
                    last_full_check = time.monotonic()

            _full_boundary(
                view,
                guard,
                writer,
                target_uuid,
                target_root_id,
                source_device,
                scratch_reserve_bytes + metadata_reserve_bytes + payload_reserve_bytes,
            )
            inventory = capture._inventory_held_data_v5_core(
                view,
                writer,
                role_policies=role_policies,
                reserve_capacity=reserve_capacity,
                scratch_reserve_bytes=scratch_reserve_bytes,
                payload_reserve_bytes=payload_reserve_bytes,
                metadata_reserve_bytes=metadata_reserve_bytes,
                max_entries=max_entries,
                expected_plan_sha256=expected_plan_sha256,
                expected_policy_roster_sha256=policy_roster_sha256,
                production_authority=capture._PRODUCTION_CAPTURE_AUTHORITY,
                production_root_check=full_root_check,
                production_fast_check=fast_check,
                production_boundary_check=boundary_check,
                production_cadence_check=cadence_check,
            )
            _assert_writer_source_separation(view, writer, source_device)
            yield ProductionCaptureV5(
                view,
                guard,
                writer,
                inventory,
                expected_volume_uuid=target_uuid,
                expected_root_id=target_root_id,
                source_device=source_device,
            )
        finally:
            if inventory is not None:
                inventory.close()
            if writer is not None:
                writer.close()


def verify_production_candidate_v5(
    writer: DescriptorWriter,
    candidate_id: str,
    *,
    expected_target_volume_uuid: str,
    expected_target_root_id: str,
    scratch: PinnedScratchV5,
    expected_index_sha256: str,
    expected_plan_sha256: str,
    source_view: HeldDataT | None = None,
    expected_source_data_volume_uuid: str | None = None,
) -> dict[str, Any]:
    """Verify with an optional held source view; absent source is explicitly un-attested."""
    target_uuid, target_root_id = _validate_pins(
        expected_target_volume_uuid, expected_target_root_id
    )
    guard = writer.storage_guard
    if not isinstance(guard, ReadOnlyStorageGuard) or not writer._read_only:
        raise ProductionCaptureV5Error("production_verify_read_only_candidate_guard_required")
    _assert_guard_pins(guard, guard.config, target_uuid, target_root_id)
    if not _valid_sha256(expected_plan_sha256):
        raise ProductionCaptureV5Error("production_verify_plan_pin_invalid")
    source_device: int | None = None
    if source_view is not None:
        source_device = _require_real_held_view(source_view, expected_plan_sha256)
        if source_view.plan.data_volume_uuid.lower() == target_uuid:
            raise ProductionCaptureV5Error("source_storage_uuid_not_distinct")
    if not isinstance(scratch, PinnedScratchV5) or scratch.fd < 0:
        raise ProductionCaptureV5Error("production_verify_scratch_capability_invalid")
    if os.fstatvfs(guard.mount_fd).f_flag & getattr(os, "ST_RDONLY", 1) == 0:
        raise ProductionCaptureV5Error("production_verify_candidate_target_not_read_only")
    writer.assert_storage_binding(guard)
    if scratch.filesystem_identity[0] in {"apfs", "hfs"}:
        if scratch.volume_uuid is None or scratch.volume_uuid == target_uuid:
            raise ProductionCaptureV5Error("verify_scratch_target_volume_alias")
        if (
            source_view is not None
            and scratch.volume_uuid == source_view.plan.data_volume_uuid.lower()
        ):
            raise ProductionCaptureV5Error("verify_scratch_source_volume_alias")
    if source_device is None:
        # Independent verification currently accepts separately pinned APFS/HFS
        # scratch only. A RecoveryOS tmpfs capability needs platform qualification.
        if (
            not isinstance(expected_source_data_volume_uuid, str)
            or not _UUID.fullmatch(expected_source_data_volume_uuid.lower())
            or scratch.volume_uuid is None
            or scratch.volume_uuid == expected_source_data_volume_uuid.lower()
        ):
            raise ProductionCaptureV5Error("independent_verify_scratch_volume_uuid_pin_required")
        scratch.validate(
            source_device=None,
            target_device=writer.device,
            required_free_bytes=VERIFY_SCRATCH_FREE_FLOOR_BYTES,
        )
        source_status = "source_not_present_or_not_attested"
    else:
        scratch.validate(
            source_device=source_device,
            target_device=writer.device,
            required_free_bytes=VERIFY_SCRATCH_FREE_FLOOR_BYTES,
        )
        source_status = "held_source_view"

    def check_scratch_capacity(used_bytes: int, reserve_bytes: int) -> None:
        if (
            type(used_bytes) is not int
            or type(reserve_bytes) is not int
            or not 0 <= used_bytes <= reserve_bytes <= VERIFY_SCRATCH_DB_RESERVE_BYTES
        ):
            raise ProductionCaptureV5Error("production_verify_scratch_usage_invalid")
        required_remaining = max(0, reserve_bytes - used_bytes)
        if source_device is None:
            scratch.validate(
                source_device=None,
                target_device=writer.device,
                required_free_bytes=VERIFY_SCRATCH_FREE_FLOOR_BYTES + required_remaining,
            )
        else:
            scratch.validate(
                source_device=source_device,
                target_device=writer.device,
                required_free_bytes=VERIFY_SCRATCH_FREE_FLOOR_BYTES + required_remaining,
            )

    result = capture.verify_candidate_v5(
        writer,
        candidate_id,
        expected_index_sha256=expected_index_sha256,
        scratch_parent_fd=scratch.fd,
        scratch_db_limit_bytes=VERIFY_SCRATCH_DB_RESERVE_BYTES,
        scratch_capacity_check=check_scratch_capacity,
        expected_plan_sha256=expected_plan_sha256,
    )
    if result.get("synthetic_fixture_only") is not False:
        raise ProductionCaptureV5Error("production_candidate_source_mode_invalid")
    guard.revalidate()
    writer.assert_storage_binding(guard)
    if source_device is not None:
        scratch.validate(
            source_device=source_device,
            target_device=writer.device,
            required_free_bytes=VERIFY_SCRATCH_FREE_FLOOR_BYTES,
        )
    else:
        _validate_scratch_target_separation(os.fstat(scratch.fd).st_dev, writer.device)
        scratch.validate(
            source_device=None,
            target_device=writer.device,
            required_free_bytes=VERIFY_SCRATCH_FREE_FLOOR_BYTES,
        )
    result["source_status"] = source_status
    if source_view is not None:
        source_view.revalidate()
    return result


def capture_session_verify(
    view: HeldDataT,
    writer: DescriptorWriter,
    candidate_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Verify during capture while deriving source device from the held view."""
    return verify_production_candidate_v5(writer, candidate_id, source_view=view, **kwargs)


def independent_target_verify(
    writer: DescriptorWriter,
    candidate_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Verify an existing target with no source handle and no caller device hint."""
    return verify_production_candidate_v5(writer, candidate_id, source_view=None, **kwargs)


def main(argv: list[str] | None = None) -> int:
    """Read-only verifier CLI with explicit target pins and scratch capability."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify", help="verify one existing candidate read-only")
    verify.add_argument("candidate_id")
    verify.add_argument("--index-sha256", required=True)
    verify.add_argument("--storage-config", required=True, type=Path)
    verify.add_argument("--target-volume-uuid", required=True)
    verify.add_argument("--target-root-id", required=True)
    verify.add_argument("--plan-sha256", required=True)
    verify.add_argument("--source-data-volume-uuid")
    verify.add_argument("--scratch-parent", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command != "verify":
        parser.error("unsupported command")
    try:
        _validate_pins(args.target_volume_uuid, args.target_root_id)
        if (
            len(args.candidate_id) != 32
            or any(ch not in "0123456789abcdef" for ch in args.candidate_id)
            or len(args.index_sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in args.index_sha256)
            or not _valid_sha256(args.plan_sha256)
        ):
            raise ProductionCaptureV5Error("production_verify_cli_pin_invalid")
        config_path = _canonical_config_path(args.storage_config)
        if __package__ in {None, ""}:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        with storage.read_only_guard(config_path) as guard:
            _assert_guard_pins(
                guard,
                guard.config,
                args.target_volume_uuid.lower(),
                args.target_root_id,
            )
            reader = DescriptorWriter.read_only_from_storage_guard(guard)
            scratch = PinnedScratchV5.open(args.scratch_parent)
            try:
                result = verify_production_candidate_v5(
                    reader,
                    args.candidate_id,
                    expected_target_volume_uuid=args.target_volume_uuid,
                    expected_target_root_id=args.target_root_id,
                    scratch=scratch,
                    expected_index_sha256=args.index_sha256,
                    expected_plan_sha256=args.plan_sha256,
                    expected_source_data_volume_uuid=args.source_data_volume_uuid,
                )
                guard.revalidate()
            finally:
                scratch.close()
                reader.close()
        sys.stdout.buffer.write(canonical_json(result))
        return 0
    except Exception as exc:
        message = str(exc)
        code = (
            message if message.isascii() and message.replace("_", "").isalnum() else "verify_failed"
        )
        sys.stderr.write(f"brain-host-tree-capture-v5 verify failed: {code}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
