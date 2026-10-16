#!/usr/bin/env python3
"""Typed, inert absence-gap v3 candidate writer.

This module deliberately does not import the v5 publisher or verifier. It
rechecks the pinned same-view absence and path-only inventory before writing,
then requires an independent read-only verifier before creating INDEX.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import secrets
import sqlite3
import stat
import subprocess
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from tools.brain_host_absence_gap_chain_v2 import (
    AbsenceGapChainError,
    validate_capacity_intent_v2,
    validate_capacity_receipt_v2,
    validate_capture_spec_v2,
)
from tools.brain_host_absence_gap_chain_v3 import (
    CAPTURE_STATUS,
    INDEX_FIELDS_V3,
    INDEX_FLAGS,
    INDEX_V3_SCHEMA,
    MEMBER_MODES,
    PRESENT_ROLES,
    PROTECTED_CLASSES,
    PROTECTED_CUSTODY_MODE,
    ROLE_MODES,
    approved_operation_v3_sha256,
    effective_policy_sha256,
    policy_roster_v3_sha256,
    target_measurement_v3_sha256,
    validate_candidate_index_v3,
    validate_protected_overlay_v3,
)
from tools.brain_host_held_data_absence_witness_v2 import (
    AbsenceGapPlanV2,
    build_absence_witness_v2,
    validate_absence_witness_v2,
)
from tools.brain_host_held_data_t_v1 import HeldDataT
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256


class CaptureV3Error(RuntimeError):
    """Production v3 capture failed closed before or during candidate creation."""


@dataclass(frozen=True)
class ScratchDatabaseV3:
    fd: int
    identity: tuple[int, int]
    max_bytes: int


class ScratchSessionV3(Protocol):
    """Externally pinned writable APFS/HFS scratch capability.

    Implementations must bind their held directory and filesystem identity,
    create each database relative to the directory descriptor, unlink its name
    before returning the still-open descriptor, keep it separate from source
    and target, enforce the shared free floor, and account all live databases.
    There is no default-temp fallback.
    """

    volume_uuid: str
    root_id: str
    root_path: Path
    root_identity: tuple[int, int]
    device: int
    filesystem_type: str
    free_floor_bytes: int
    max_total_bytes: int

    def create_database(
        self, prefix: str, *, max_bytes: int, source_device: int, target_device: int
    ) -> ScratchDatabaseV3: ...

    def revalidate(
        self,
        *,
        required_free_bytes: int,
        source_device: int,
        target_device: int,
    ) -> None: ...

    def remove_database(self, database: ScratchDatabaseV3) -> None: ...

    def used_bytes(self) -> int: ...

    def close(self) -> None: ...


class ProductionScratchSessionV3:
    """Production scratch adapter using unlinked, descriptor-held SQLite files.

    The caller must supply externally pinned volume and root identities. This
    adapter reuses v5's held-directory and Darwin filesystem validation, but
    owns v3's database descriptor lifecycle. Scratch databases are disposable
    and are never recovery evidence.
    """

    def __init__(
        self,
        pinned: Any,
        *,
        volume_uuid: str,
        root_id: str,
        source_volume_uuid: str,
        target_volume_uuid: str,
        free_floor_bytes: int,
        max_total_bytes: int,
        source_device: int,
        target_device: int,
    ) -> None:
        root_st = os.fstat(pinned.fd)
        if (
            pinned.fd < 0
            or not isinstance(volume_uuid, str)
            or not _UUID_RE.fullmatch(volume_uuid)
            or not isinstance(root_id, str)
            or not re.fullmatch(r"[0-9a-f]{32}", root_id)
            or pinned.volume_uuid is None
            or pinned.volume_uuid.lower() != volume_uuid.lower()
            or not isinstance(source_volume_uuid, str)
            or not _UUID_RE.fullmatch(source_volume_uuid)
            or not isinstance(target_volume_uuid, str)
            or not _UUID_RE.fullmatch(target_volume_uuid)
            or volume_uuid.lower() in {source_volume_uuid.lower(), target_volume_uuid.lower()}
            or pinned.filesystem_identity[0] not in {"apfs", "hfs"}
            or type(free_floor_bytes) is not int
            or free_floor_bytes < 0
            or type(max_total_bytes) is not int
            or max_total_bytes < 4096
            or type(source_device) is not int
            or type(target_device) is not int
            or len({root_st.st_dev, source_device, target_device}) != 3
            or not stat.S_ISDIR(root_st.st_mode)
            or root_st.st_uid != os.getuid()
            or stat.S_IMODE(root_st.st_mode) != 0o700
        ):
            raise CaptureV3Error("pinned_scratch_capability_invalid")
        self._pinned = pinned
        self.volume_uuid = volume_uuid.lower()
        self.root_id = root_id
        self.root_path = Path(pinned.path)
        self.root_identity = (root_st.st_dev, root_st.st_ino)
        self.root_fd = pinned.fd
        # root_id is caller metadata; the held descriptor identity is the live root pin.
        self.device = root_st.st_dev
        self.filesystem_type = pinned.filesystem_identity[0]
        self.free_floor_bytes = free_floor_bytes
        self.max_total_bytes = max_total_bytes
        self._source_device = source_device
        self._target_device = target_device
        self._source_volume_uuid = source_volume_uuid.lower()
        self._target_volume_uuid = target_volume_uuid.lower()
        self._databases: dict[int, ScratchDatabaseV3] = {}
        self._lock = RLock()
        self._closed = False

    @classmethod
    def open(
        cls,
        path: Path,
        *,
        expected_volume_uuid: str,
        root_id: str,
        source_volume_uuid: str,
        target_volume_uuid: str,
        free_floor_bytes: int,
        max_total_bytes: int,
        source_device: int,
        target_device: int,
    ) -> ProductionScratchSessionV3:
        from tools.brain_host_tree_capture_v5_production import PinnedScratchV5

        pinned = PinnedScratchV5.open(path)
        try:
            session = cls(
                pinned,
                volume_uuid=expected_volume_uuid,
                root_id=root_id,
                source_volume_uuid=source_volume_uuid,
                target_volume_uuid=target_volume_uuid,
                free_floor_bytes=free_floor_bytes,
                max_total_bytes=max_total_bytes,
                source_device=source_device,
                target_device=target_device,
            )
            session.revalidate(
                required_free_bytes=free_floor_bytes + max_total_bytes,
                source_device=source_device,
                target_device=target_device,
            )
            return session
        except BaseException:
            pinned.close()
            raise

    def _require_open(self) -> None:
        if self._closed or self._pinned.fd < 0:
            raise CaptureV3Error("pinned_scratch_session_closed")

    def create_database(
        self, prefix: str, *, max_bytes: int, source_device: int, target_device: int
    ) -> ScratchDatabaseV3:
        if (
            not isinstance(prefix, str)
            or re.fullmatch(r"[a-z0-9-]{1,48}", prefix) is None
            or type(max_bytes) is not int
            or max_bytes < 4096
            or max_bytes > self.max_total_bytes
        ):
            raise CaptureV3Error("pinned_scratch_database_arguments_invalid")
        self.revalidate(
            required_free_bytes=self.free_floor_bytes + max_bytes,
            source_device=source_device,
            target_device=target_device,
        )
        with self._lock:
            self._require_open()
            reserved_bytes = sum(item.max_bytes for item in self._databases.values())
            if reserved_bytes + max_bytes > self.max_total_bytes:
                raise CaptureV3Error("pinned_scratch_budget_exceeded")
            for _ in range(16):
                name = f"{prefix}-{secrets.token_hex(16)}.sqlite"
                try:
                    fd = os.open(
                        name,
                        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                        0o600,
                        dir_fd=self._pinned.fd,
                    )
                    break
                except FileExistsError:
                    continue
            else:
                raise CaptureV3Error("pinned_scratch_database_name_exhausted")
            unlink_attempted = False
            entry_unlinked = False
            try:
                st = os.fstat(fd)
                named_st = os.stat(name, dir_fd=self._pinned.fd, follow_symlinks=False)
                if (
                    not stat.S_ISREG(st.st_mode)
                    or st.st_uid != os.getuid()
                    or stat.S_IMODE(st.st_mode) != 0o600
                    or st.st_nlink != 1
                    or not stat.S_ISREG(named_st.st_mode)
                    or (named_st.st_dev, named_st.st_ino) != (st.st_dev, st.st_ino)
                    or named_st.st_nlink != 1
                ):
                    raise CaptureV3Error("pinned_scratch_database_identity_invalid")
                unlink_attempted = True
                os.unlink(name, dir_fd=self._pinned.fd)
                entry_unlinked = True
                unlinked_st = os.fstat(fd)
                if (unlinked_st.st_dev, unlinked_st.st_ino) != (
                    st.st_dev,
                    st.st_ino,
                ) or unlinked_st.st_nlink != 0:
                    raise CaptureV3Error("pinned_scratch_database_unlink_invalid")
                database = ScratchDatabaseV3(fd, (st.st_dev, st.st_ino), max_bytes)
                self._databases[fd] = database
                return database
            except BaseException as exc:
                try:
                    os.close(fd)
                except OSError as close_exc:
                    raise CaptureV3Error(
                        "pinned_scratch_database_cleanup_incomplete"
                    ) from close_exc
                if unlink_attempted and not entry_unlinked:
                    raise CaptureV3Error("pinned_scratch_database_unlink_failed") from exc
                raise

    def revalidate(
        self,
        *,
        required_free_bytes: int,
        source_device: int,
        target_device: int,
    ) -> None:
        if (
            type(required_free_bytes) is not int
            or required_free_bytes < self.free_floor_bytes
            or source_device != self._source_device
            or target_device != self._target_device
            or self.device in {source_device, target_device}
        ):
            raise CaptureV3Error("pinned_scratch_revalidation_arguments_invalid")
        with self._lock:
            self._require_open()
            try:
                self._pinned.validate(
                    source_device=source_device,
                    target_device=target_device,
                    required_free_bytes=required_free_bytes,
                )
                root_st = os.fstat(self._pinned.fd)
                if (root_st.st_dev, root_st.st_ino) != self.root_identity:
                    raise CaptureV3Error("pinned_scratch_root_replaced")
                for database in self._databases.values():
                    st = os.fstat(database.fd)
                    if (
                        (st.st_dev, st.st_ino) != database.identity
                        or not stat.S_ISREG(st.st_mode)
                        or st.st_nlink != 0
                        or st.st_uid != os.getuid()
                        or stat.S_IMODE(st.st_mode) != 0o600
                    ):
                        raise CaptureV3Error("pinned_scratch_database_replaced")
            except CaptureV3Error:
                raise
            except Exception as exc:
                raise CaptureV3Error("pinned_scratch_revalidation_failed") from exc

    def used_bytes(self) -> int:
        total = 0
        with self._lock:
            self._require_open()
            try:
                for database in self._databases.values():
                    st = os.fstat(database.fd)
                    if (st.st_dev, st.st_ino) != database.identity or st.st_nlink != 0:
                        raise CaptureV3Error("pinned_scratch_database_replaced")
                    total += st.st_blocks * 512
            except OSError as exc:
                raise CaptureV3Error("pinned_scratch_database_missing") from exc
        if total > self.max_total_bytes:
            raise CaptureV3Error("pinned_scratch_budget_exceeded")
        return total

    def remove_database(self, database: ScratchDatabaseV3) -> None:
        with self._lock:
            held = self._databases.get(database.fd)
            if held != database:
                raise CaptureV3Error("pinned_scratch_database_handle_mismatch")
            try:
                st = os.fstat(database.fd)
            except OSError as exc:
                raise CaptureV3Error("pinned_scratch_database_missing") from exc
            if (st.st_dev, st.st_ino) != database.identity:
                raise CaptureV3Error("pinned_scratch_database_replaced")
            self._databases.pop(database.fd)
            os.close(database.fd)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._databases:
                raise CaptureV3Error("pinned_scratch_databases_still_open")
            self._pinned.close()
            self._closed = True


class _SQLiteSpoolV3:
    """Shared bounded database lifecycle; all paths come from pinned scratch."""

    def __init__(
        self,
        scratch: ScratchSessionV3,
        *,
        prefix: str,
        source_device: int,
        target_device: int,
    ) -> None:
        if (
            not isinstance(scratch.volume_uuid, str)
            or not _UUID_RE.fullmatch(scratch.volume_uuid)
            or not isinstance(scratch.root_id, str)
            or not re.fullmatch(r"[0-9a-f]{32}", scratch.root_id)
            or not isinstance(scratch.root_path, Path)
            or not scratch.root_path.is_absolute()
            or not isinstance(scratch.root_identity, tuple)
            or len(scratch.root_identity) != 2
            or any(type(item) is not int for item in scratch.root_identity)
            or type(scratch.device) is not int
            or scratch.filesystem_type not in {"apfs", "hfs"}
            or type(scratch.free_floor_bytes) is not int
            or scratch.free_floor_bytes < 0
            or type(scratch.max_total_bytes) is not int
            or scratch.max_total_bytes < 4096
            or type(source_device) is not int
            or type(target_device) is not int
            or scratch.device in {source_device, target_device}
        ):
            raise CaptureV3Error("pinned_scratch_capability_invalid")
        self.scratch = scratch
        self.source_device = source_device
        self.target_device = target_device
        used_before_create = scratch.used_bytes()
        if (
            type(used_before_create) is not int
            or used_before_create < 0
            or used_before_create > scratch.max_total_bytes
        ):
            raise CaptureV3Error("pinned_scratch_budget_exceeded")
        scratch.revalidate(
            required_free_bytes=scratch.free_floor_bytes
            + max(0, scratch.max_total_bytes - used_before_create),
            source_device=source_device,
            target_device=target_device,
        )
        self.database = scratch.create_database(
            prefix,
            max_bytes=max(4096, scratch.max_total_bytes // 3),
            source_device=source_device,
            target_device=target_device,
        )
        self.db: sqlite3.Connection | None = None
        self._closed = False
        self._page_size = 4096
        self._page_limit = 0
        try:
            if (
                not isinstance(self.database, ScratchDatabaseV3)
                or type(self.database.fd) is not int
                or self.database.fd < 0
                or self.database.max_bytes < 4096
                or self.database.max_bytes > scratch.max_total_bytes
            ):
                raise CaptureV3Error("pinned_scratch_database_invalid")
            self._page_limit = self.database.max_bytes // self._page_size
            self._guard()
            if not os.path.exists(f"/dev/fd/{self.database.fd}"):
                raise CaptureV3Error("descriptor_sqlite_alias_unavailable")
            self.db = sqlite3.connect(f"/dev/fd/{self.database.fd}")
            self.db.execute("PRAGMA page_size=4096")
            page_size = self.db.execute("PRAGMA page_size").fetchone()
            journal_mode = self.db.execute("PRAGMA journal_mode=OFF").fetchone()
            self.db.execute("PRAGMA synchronous=OFF")
            self.db.execute("PRAGMA temp_store=MEMORY")
            synchronous = self.db.execute("PRAGMA synchronous").fetchone()
            temp_store = self.db.execute("PRAGMA temp_store").fetchone()
            if (
                page_size != (self._page_size,)
                or journal_mode != ("off",)
                or synchronous != (0,)
                or temp_store != (2,)
            ):
                raise CaptureV3Error("descriptor_sqlite_policy_rejected")
            observed_page_limit = self.db.execute(
                f"PRAGMA max_page_count={self._page_limit}"
            ).fetchone()
            if observed_page_limit != (self._page_limit,):
                raise CaptureV3Error("descriptor_sqlite_page_limit_rejected")
            database_list = self.db.execute("PRAGMA database_list").fetchall()
            if database_list != [(0, "main", f"/dev/fd/{self.database.fd}")]:
                raise CaptureV3Error("descriptor_sqlite_main_database_invalid")
        except BaseException:
            if self.db is not None:
                self.db.close()
                self.db = None
            if isinstance(self.database, ScratchDatabaseV3):
                scratch.remove_database(self.database)
            self._closed = True
            raise

    def _guard(self) -> None:
        try:
            st = os.fstat(self.database.fd)
            root_fd = getattr(self.scratch, "root_fd", None)
            root_st = (
                os.fstat(root_fd)
                if type(root_fd) is int and root_fd >= 0
                else os.stat(self.scratch.root_path, follow_symlinks=False)
            )
        except OSError as exc:
            raise CaptureV3Error("pinned_scratch_database_replaced") from exc
        if (
            not stat.S_ISDIR(root_st.st_mode)
            or (root_st.st_dev, root_st.st_ino) != self.scratch.root_identity
            or root_st.st_uid != os.getuid()
            or stat.S_IMODE(root_st.st_mode) != 0o700
            or not stat.S_ISREG(st.st_mode)
            or (st.st_dev, st.st_ino) != self.database.identity
            or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o600
            or st.st_nlink != 0
        ):
            raise CaptureV3Error("pinned_scratch_database_replaced")
        if self.db is not None:
            try:
                page_size = self.db.execute("PRAGMA page_size").fetchone()
                page_count = self.db.execute("PRAGMA page_count").fetchone()
                max_page_count = self.db.execute("PRAGMA max_page_count").fetchone()
            except sqlite3.Error as exc:
                raise CaptureV3Error("descriptor_sqlite_identity_check_failed") from exc
            if (
                page_size != (self._page_size,)
                or page_count is None
                or type(page_count[0]) is not int
                or page_count[0] < 0
                or max_page_count != (self._page_limit,)
                or page_count[0] * self._page_size > self.database.max_bytes
            ):
                raise CaptureV3Error("descriptor_sqlite_budget_exceeded")
        used = self.scratch.used_bytes()
        if type(used) is not int or used < 0 or used > self.scratch.max_total_bytes:
            raise CaptureV3Error("pinned_scratch_budget_exceeded")
        required = self.scratch.free_floor_bytes + max(0, self.scratch.max_total_bytes - used)
        self.scratch.revalidate(
            required_free_bytes=required,
            source_device=self.source_device,
            target_device=self.target_device,
        )

    def _write(self, operation: Callable[[], Any]) -> Any:
        self._guard()
        if self.db is None:
            raise CaptureV3Error("pinned_scratch_database_closed")
        try:
            result = operation()
        except sqlite3.OperationalError as exc:
            if "full" in str(exc).lower():
                raise CaptureV3Error("pinned_scratch_database_budget_exhausted") from exc
            raise
        self._guard()
        return result

    def close(self) -> None:
        if self._closed:
            return
        if self.db is not None:
            self.db.close()
            self.db = None
        self.scratch.remove_database(self.database)
        self._closed = True

    def __enter__(self) -> _SQLiteSpoolV3:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


_ROLE_POLICY_SCHEMA = "brain-host-role-policy-v3"
_GIT_RECEIPT_SCHEMA = "brain-host-git-reconstruction-receipt-v1"
_PIN_RE = re.compile(r"[0-9a-f]{64}\Z")
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z", re.IGNORECASE
)
_CANDIDATE_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_ROLE_POLICY_FIELDS = {
    "schema",
    "version",
    "role",
    "default_disposition",
    "path_rules",
    "launch_quarantine_required",
    "role_policy_sha256",
}


def _canonical_digest(value: Mapping[str, Any], field_name: str) -> str:
    digest = value.get(field_name)
    if not isinstance(digest, str) or not _PIN_RE.fullmatch(digest):
        raise CaptureV3Error("pinned_document_digest_invalid")
    body = {key: item for key, item in value.items() if key != field_name}
    if hashlib.sha256(canonical_json(body)).hexdigest() != digest:
        raise CaptureV3Error("pinned_document_digest_mismatch")
    return digest


@dataclass(frozen=True)
class RolePolicyV3:
    role: str
    document: Mapping[str, Any]
    policy_sha256: str
    _token: object = field(repr=False, compare=False)


_ROLE_POLICY_TOKEN = object()


def load_role_policy_v3(path: Path, *, role: str, expected_sha256: str) -> RolePolicyV3:
    """Load a private role policy by exact external pin; reject loose schemas."""
    if (
        role not in PRESENT_ROLES
        or not isinstance(expected_sha256, str)
        or not _PIN_RE.fullmatch(expected_sha256)
    ):
        raise CaptureV3Error("role_policy_external_pin_invalid")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise CaptureV3Error("role_policy_open_failed") from exc
    try:
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o600
            or st.st_nlink != 1
            or not 0 < st.st_size <= 256 * 1024
        ):
            raise CaptureV3Error("role_policy_file_invalid")
        raw = os.read(fd, st.st_size + 1)
        if len(raw) != st.st_size:
            raise CaptureV3Error("role_policy_size_changed")
    finally:
        os.close(fd)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_pairs)
    except (UnicodeDecodeError, ValueError) as exc:
        raise CaptureV3Error("role_policy_json_invalid") from exc
    if not isinstance(value, dict) or set(value) != _ROLE_POLICY_FIELDS:
        raise CaptureV3Error("role_policy_schema_fields_invalid")
    if (
        value["schema"] != _ROLE_POLICY_SCHEMA
        or value["version"] != 3
        or value["role"] != role
        or value["launch_quarantine_required"] is not (role == "launch_agents")
        or not isinstance(value["path_rules"], list)
    ):
        raise CaptureV3Error("role_policy_schema_invalid")
    default = value["default_disposition"]
    if default is not None and (not isinstance(default, str) or default not in MEMBER_MODES):
        raise CaptureV3Error("role_policy_default_invalid")
    seen: set[str] = set()
    for row in value["path_rules"]:
        if (
            not isinstance(row, Mapping)
            or set(row) != {"relative_prefix", "disposition"}
            or not isinstance(row["relative_prefix"], str)
            or not row["relative_prefix"]
            or row["relative_prefix"].startswith("/")
            or any(part in {"", ".", ".."} for part in row["relative_prefix"].split("/"))
            or not isinstance(row["disposition"], str)
            or row["disposition"] not in MEMBER_MODES
            or row["relative_prefix"] in seen
        ):
            raise CaptureV3Error("role_policy_rule_invalid")
        seen.add(row["relative_prefix"])
    if role in {"codex_mcp_config", "cursor_mcp_config", "helius_mcp_wrapper"} and (
        default != "exclude_reissue"
        or any(row["disposition"] != "exclude_reissue" for row in value["path_rules"])
    ):
        raise CaptureV3Error("mcp_or_wrapper_reissue_only_policy_required")
    if role == "launch_agents" and value["launch_quarantine_required"] is not True:
        raise CaptureV3Error("launch_definition_quarantine_required")
    actual = _canonical_digest(value, "role_policy_sha256")
    if actual != expected_sha256:
        raise CaptureV3Error("role_policy_pin_mismatch")
    return RolePolicyV3(role, value, actual, _ROLE_POLICY_TOKEN)


def _no_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


@dataclass(frozen=True)
class GitReconstructionV3:
    receipt_sha256: str
    role: str
    locator: str
    commit: str
    _token: object = field(repr=False, compare=False)


_GIT_TOKEN = object()


def validate_git_reconstruction_receipt_v3(
    value: Mapping[str, Any], *, expected_sha256: str
) -> GitReconstructionV3:
    fields = {"schema", "role", "relative_locator", "commit", "receipt_sha256"}
    if not isinstance(value, Mapping) or set(value) != fields:
        raise CaptureV3Error("git_reconstruction_receipt_schema_invalid")
    if value["schema"] != _GIT_RECEIPT_SCHEMA or value["role"] != "research_checkout":
        raise CaptureV3Error("git_reconstruction_receipt_schema_invalid")
    if (
        not isinstance(value["relative_locator"], str)
        or not isinstance(value["commit"], str)
        or not re.fullmatch(r"[0-9a-f]{40}", value["commit"])
        or not isinstance(expected_sha256, str)
        or not _PIN_RE.fullmatch(expected_sha256)
    ):
        raise CaptureV3Error("git_reconstruction_receipt_pin_invalid")
    digest = _canonical_digest(value, "receipt_sha256")
    if digest != expected_sha256:
        raise CaptureV3Error("git_reconstruction_receipt_pin_mismatch")
    return GitReconstructionV3(
        digest,
        value["role"],
        value["relative_locator"],
        value["commit"],
        _GIT_TOKEN,
    )


@dataclass(frozen=True)
class InventoryEntryV3:
    role: str
    path: str
    kind: str
    disposition: str
    size_bytes: int
    signature: tuple[int, ...]
    source_device: int
    source_inode: int
    source_nlink: int
    symlink_target: bytes | None = None
    parent_identity_chain: tuple[tuple[int, int], ...] = ()
    protection_class: str | None = None
    overlay_rule_sha256: str | None = None
    authorization_receipt_sha256: str | None = None

    def wire(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "path": self.path,
            "kind": self.kind,
            "disposition": self.disposition,
            "size_bytes": self.size_bytes,
            "signature": list(self.signature),
            "source_device": self.source_device,
            "source_inode": self.source_inode,
            "source_nlink": self.source_nlink,
            "symlink_target_sha256": (
                sha256(self.symlink_target) if self.symlink_target is not None else None
            ),
            "parent_identity_chain": [list(identity) for identity in self.parent_identity_chain],
            "protection_class": self.protection_class,
            "overlay_rule_sha256": self.overlay_rule_sha256,
            "authorization_receipt_sha256": self.authorization_receipt_sha256,
        }


class _SQLiteInventoryEntriesV3(_SQLiteSpoolV3):
    """Disk-backed ordered entry spool; iteration never materializes the roster."""

    def __init__(
        self, scratch: ScratchSessionV3, *, source_device: int, target_device: int
    ) -> None:
        super().__init__(
            scratch,
            prefix="brain-absence-inventory-v3",
            source_device=source_device,
            target_device=target_device,
        )
        try:
            self._write(
                lambda: self.db.execute(
                    "CREATE TABLE entries ("
                    "role TEXT NOT NULL, path TEXT NOT NULL, kind TEXT NOT NULL, "
                    "size_bytes INTEGER NOT NULL, "
                    "source_device INTEGER NOT NULL, source_inode INTEGER NOT NULL, "
                    "source_nlink INTEGER NOT NULL, disposition TEXT NOT NULL, body BLOB NOT NULL, "
                    "PRIMARY KEY(role, path)) WITHOUT ROWID"
                )
            )
            self._write(
                lambda: self.db.execute(
                    "CREATE TABLE candidate_members ("
                    "role TEXT NOT NULL, path TEXT NOT NULL, target_path TEXT, "
                    "sha256 TEXT NOT NULL, "
                    "size_bytes INTEGER NOT NULL, PRIMARY KEY(role,path)) WITHOUT ROWID"
                )
            )
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _encode(entry: InventoryEntryV3) -> bytes:
        body = entry.wire()
        body["symlink_target_hex"] = (
            entry.symlink_target.hex() if entry.symlink_target is not None else None
        )
        return canonical_json(body)

    @staticmethod
    def _decode(raw: bytes) -> InventoryEntryV3:
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_pairs)
        target_hex = body.pop("symlink_target_hex")
        target = bytes.fromhex(target_hex) if target_hex is not None else None
        signature = body["signature"]
        parents = body["parent_identity_chain"]
        return InventoryEntryV3(
            body["role"],
            body["path"],
            body["kind"],
            body["disposition"],
            body["size_bytes"],
            tuple(signature),
            body["source_device"],
            body["source_inode"],
            body["source_nlink"],
            target,
            tuple(tuple(item) for item in parents),
            body["protection_class"],
            body["overlay_rule_sha256"],
            body["authorization_receipt_sha256"],
        )

    def add(self, entry: InventoryEntryV3) -> None:
        try:
            self._write(
                lambda: self.db.execute(
                    "INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        entry.role,
                        entry.path,
                        entry.kind,
                        entry.size_bytes,
                        entry.source_device,
                        entry.source_inode,
                        entry.source_nlink,
                        entry.disposition,
                        self._encode(entry),
                    ),
                )
            )
        except sqlite3.IntegrityError as exc:
            raise CaptureV3Error("source_duplicate_member_path") from exc

    def finish(self) -> None:
        self._write(self.db.commit)
        invalid = self.db.execute(
            "SELECT 1 FROM entries WHERE kind = 'file' AND source_nlink > 1 "
            "GROUP BY source_device, source_inode "
            "HAVING COUNT(*) != MIN(source_nlink) OR MIN(source_nlink) != MAX(source_nlink) "
            "OR MIN(disposition) != MAX(disposition) LIMIT 1"
        ).fetchone()
        if invalid is not None:
            raise CaptureV3Error("hardlink_group_incomplete_or_policy_mixed")

    def __iter__(self) -> Iterator[InventoryEntryV3]:
        cursor = self.db.execute("SELECT body FROM entries ORDER BY role, path")
        try:
            for (raw,) in cursor:
                yield self._decode(raw)
        finally:
            cursor.close()

    def iter_role(self, role: str) -> Iterator[InventoryEntryV3]:
        cursor = self.db.execute("SELECT body FROM entries WHERE role = ? ORDER BY path", (role,))
        try:
            for (raw,) in cursor:
                yield self._decode(raw)
        finally:
            cursor.close()

    def add_candidate_member(self, row: Mapping[str, Any]) -> None:
        self._write(
            lambda: self.db.execute(
                "INSERT INTO candidate_members VALUES (?, ?, ?, ?, ?)",
                (row["role"], row["path"], row["target_path"], row["sha256"], row["size_bytes"]),
            )
        )

    def candidate_members_sha256(self) -> str:
        digest = hashlib.sha256()
        digest.update(b"[")
        cursor = self.db.execute(
            "SELECT role,path,target_path,sha256,size_bytes "
            "FROM candidate_members ORDER BY role,path"
        )
        first = True
        try:
            for role, path, target_path, member_sha, size in cursor:
                if not first:
                    digest.update(b",")
                first = False
                digest.update(
                    canonical_json(
                        {
                            "role": role,
                            "path": path,
                            "target_path": target_path,
                            "sha256": member_sha,
                            "size_bytes": size,
                        }
                    ).rstrip(b"\n")
                )
        finally:
            cursor.close()
        digest.update(b"]\n")
        return digest.hexdigest()

    def __len__(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM entries").fetchone()[0])


class _SQLiteVerifierRecordsV3(_SQLiteSpoolV3):
    """Disk-backed verifier membership and summary spool for large manifests."""

    def __init__(
        self, scratch: ScratchSessionV3, *, source_device: int, target_device: int
    ) -> None:
        super().__init__(
            scratch,
            prefix="brain-absence-verifier-v3",
            source_device=source_device,
            target_device=target_device,
        )
        try:
            self._write(
                lambda: self.db.executescript(
                    "CREATE TABLE actual_paths(path TEXT PRIMARY KEY) WITHOUT ROWID;"
                    "CREATE TABLE expected_paths(path TEXT PRIMARY KEY) WITHOUT ROWID;"
                    "CREATE TABLE role_rows("
                    "role TEXT NOT NULL, path TEXT NOT NULL, body BLOB NOT NULL, "
                    "PRIMARY KEY(role,path)) WITHOUT ROWID;"
                    "CREATE TABLE members(role TEXT NOT NULL, path TEXT NOT NULL, "
                    "target_path TEXT, "
                    "sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL, "
                    "PRIMARY KEY(role,path)) WITHOUT ROWID;"
                )
            )
        except BaseException:
            self.close()
            raise

    def add_actual(self, path: str) -> None:
        try:
            self._write(lambda: self.db.execute("INSERT INTO actual_paths VALUES (?)", (path,)))
        except sqlite3.IntegrityError as exc:
            raise CaptureV3Error("candidate_actual_path_duplicate") from exc

    def add_expected(self, path: str) -> None:
        try:
            self._write(lambda: self.db.execute("INSERT INTO expected_paths VALUES (?)", (path,)))
        except sqlite3.IntegrityError as exc:
            raise CaptureV3Error("candidate_expected_path_duplicate") from exc

    def add_role_row(self, role: str, path: str, body: Mapping[str, Any]) -> None:
        self._write(
            lambda: self.db.execute(
                "INSERT INTO role_rows VALUES (?, ?, ?)",
                (role, path, canonical_json(body)),
            )
        )

    def add_member(self, row: Mapping[str, Any]) -> None:
        self._write(
            lambda: self.db.execute(
                "INSERT INTO members VALUES (?, ?, ?, ?, ?)",
                (row["role"], row["path"], row["target_path"], row["sha256"], row["size_bytes"]),
            )
        )

    def iter_role(self, role: str) -> Iterator[Mapping[str, Any]]:
        cursor = self.db.execute("SELECT body FROM role_rows WHERE role = ? ORDER BY path", (role,))
        try:
            for (body,) in cursor:
                yield json.loads(body.decode("utf-8"), object_pairs_hook=_no_duplicate_pairs)
        finally:
            cursor.close()

    def assert_membership_exact(self) -> None:
        mismatch = self.db.execute(
            "SELECT path FROM actual_paths EXCEPT SELECT path FROM expected_paths LIMIT 1"
        ).fetchone()
        if mismatch is None:
            mismatch = self.db.execute(
                "SELECT path FROM expected_paths EXCEPT SELECT path FROM actual_paths LIMIT 1"
            ).fetchone()
        if mismatch is not None:
            raise CaptureV3Error("independent_verifier_candidate_membership_mismatch")

    def members_sha256(self) -> str:
        digest = hashlib.sha256()
        digest.update(b"[")
        cursor = self.db.execute(
            "SELECT role,path,target_path,sha256,size_bytes FROM members ORDER BY role,path"
        )
        first = True
        try:
            for role, path, target_path, member_sha, size in cursor:
                if not first:
                    digest.update(b",")
                first = False
                digest.update(
                    canonical_json(
                        {
                            "role": role,
                            "path": path,
                            "target_path": target_path,
                            "sha256": member_sha,
                            "size_bytes": size,
                        }
                    ).rstrip(b"\n")
                )
        finally:
            cursor.close()
        digest.update(b"]\n")
        return digest.hexdigest()


class _SQLiteDirectoryQueueV3(_SQLiteSpoolV3):
    """FIFO directory frontier stored on disk, not proportional Python memory."""

    def __init__(
        self, scratch: ScratchSessionV3, *, source_device: int, target_device: int
    ) -> None:
        super().__init__(
            scratch,
            prefix="brain-absence-walk-v3",
            source_device=source_device,
            target_device=target_device,
        )
        try:
            self._write(
                lambda: self.db.execute(
                    "CREATE TABLE pending ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT, chain BLOB)"
                )
            )
        except BaseException:
            self.close()
            raise

    def push(self, path: str, chain: tuple[tuple[int, int], ...]) -> None:
        self._write(
            lambda: self.db.execute(
                "INSERT INTO pending(path,chain) VALUES (?,?)", (path, canonical_json(chain))
            )
        )

    def pop(self) -> tuple[str, tuple[tuple[int, int], ...]] | None:
        row = self.db.execute("SELECT id,path,chain FROM pending ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return None
        self._write(lambda: self.db.execute("DELETE FROM pending WHERE id = ?", (row[0],)))
        return row[1], tuple(tuple(item) for item in json.loads(row[2].decode("utf-8")))


@dataclass(frozen=True)
class InventorySnapshotV3:
    entries: _SQLiteInventoryEntriesV3
    inventory_sha256: str
    role_dispositions: tuple[dict[str, Any], ...]
    payload_logical_bytes: int

    def close(self) -> None:
        self.entries.close()

    def __enter__(self) -> InventorySnapshotV3:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class HeldViewV3(Protocol):
    """Descriptor-held, revalidatable view consumed by the private inventory core."""

    plan: Any
    trusted_plan_sha256: str
    mount_fd: int
    root_fds: Mapping[str, int]

    def revalidate(self) -> None: ...

    def borrow_root(self, role: str) -> AbstractContextManager[int]: ...


class TargetSessionV3(Protocol):
    """Already pinned create-only target capability; no path-based writes."""

    target_device: int
    volume_uuid: str

    def revalidate(self, required_free_bytes: int) -> TargetMeasurementV3: ...

    def begin_new_candidate(self, required_free_bytes: int) -> str: ...

    def create_member(self, candidate_id: str, relative_path: str, payload: bytes) -> None: ...

    def prepare_member_parent(
        self, candidate_id: str, relative_path: str, required_free_bytes: int
    ) -> None: ...

    def read_member(self, candidate_id: str, relative_path: str) -> bytes: ...

    def member_allocated_bytes(self, candidate_id: str, relative_path: str) -> int | None: ...


_TARGET_CAPABILITY_TOKEN = object()
_CANDIDATE_BASE = "host-recovery/absence-gap-v3/candidates"


@dataclass(frozen=True)
class TargetMeasurementV3:
    target_volume_uuid: str
    target_root_id: str
    target_container_reference: str
    target_device_identifier: str
    available_bytes: int
    observed_at_utc: str
    monotonic_start_ns: int
    monotonic_end_ns: int
    measurement_sha256: str

    @classmethod
    def stamp(
        cls,
        *,
        volume_uuid: str,
        root_id: str,
        container_reference: str,
        device_identifier: str,
        available_bytes: int,
        observed_at_utc: str,
        monotonic_start_ns: int,
        monotonic_end_ns: int,
    ) -> TargetMeasurementV3:
        if (
            not isinstance(volume_uuid, str)
            or not isinstance(root_id, str)
            or not isinstance(container_reference, str)
            or not isinstance(device_identifier, str)
            or type(available_bytes) is not int
            or available_bytes < 0
            or type(monotonic_start_ns) is not int
            or type(monotonic_end_ns) is not int
            or monotonic_start_ns < 0
            or monotonic_end_ns < monotonic_start_ns
        ):
            raise CaptureV3Error("target_measurement_fields_invalid")
        try:
            datetime.fromisoformat(observed_at_utc.replace("Z", "+00:00"))
        except (ValueError, AttributeError) as exc:
            raise CaptureV3Error("target_measurement_time_invalid") from exc
        body = {
            "domain": "brain-host-target-measurement-v3",
            "target_volume_uuid": volume_uuid.lower(),
            "target_root_id": root_id,
            "target_container_reference": container_reference,
            "target_device_identifier": device_identifier,
            "available_bytes": available_bytes,
            "observed_at_utc": observed_at_utc,
            "monotonic_start_ns": monotonic_start_ns,
            "monotonic_end_ns": monotonic_end_ns,
        }
        return cls(
            volume_uuid.lower(),
            root_id,
            container_reference,
            device_identifier,
            available_bytes,
            observed_at_utc,
            monotonic_start_ns,
            monotonic_end_ns,
            hashlib.sha256(canonical_json(body)).hexdigest(),
        )


@dataclass
class _TargetReserveV3:
    """Track only allocation proven by a durable no-follow member stat."""

    full_ceiling_bytes: int
    credited_allocated_bytes: int = 0

    def remaining_bytes(self) -> int:
        if type(self.full_ceiling_bytes) is not int or self.full_ceiling_bytes < 0:
            raise CaptureV3Error("target_reserve_ceiling_invalid")
        return max(0, self.full_ceiling_bytes - self.credited_allocated_bytes)

    def credit_member(self, allocated_bytes: int | None) -> None:
        if allocated_bytes is None:
            return
        if type(allocated_bytes) is not int or allocated_bytes < 0:
            raise CaptureV3Error("target_allocated_blocks_invalid")
        self.credited_allocated_bytes += allocated_bytes


@dataclass(frozen=True)
class ApprovedOperationV3:
    """Externally pinned authority to bind historical M0 to a specific operation."""

    approval_sha256: str
    operation_id: str
    operation_sha256: str
    m0: TargetMeasurementV3
    capacity_receipt_sha256: str
    _token: object = field(repr=False, compare=False)


_APPROVED_OPERATION_TOKEN = object()


def validate_approved_operation_v3(
    value: Mapping[str, Any],
    *,
    expected_sha256: str,
    capacity_receipt: Mapping[str, Any],
    capacity_receipt_sha256: str,
    capture_spec: Mapping[str, Any],
    capture_spec_sha256: str,
    source_data_plan: Any,
) -> ApprovedOperationV3:
    """Load a full, externally pinned M0 identity artifact and join it to v2 receipts."""
    fields = {
        "schema",
        "version",
        "operation_id",
        "operation_sha256",
        "capacity_receipt_sha256",
        "m0_measurement",
        "m0_measurement_sha256",
        "approval_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise CaptureV3Error("approved_operation_schema_invalid")
    if (
        value["schema"] != "brain-host-approved-operation-v3"
        or value["version"] != 3
        or not isinstance(value["operation_id"], str)
        or not value["operation_id"]
        or not isinstance(expected_sha256, str)
        or not _PIN_RE.fullmatch(expected_sha256)
        or value["capacity_receipt_sha256"] != capacity_receipt_sha256
        or value["approval_sha256"] != expected_sha256
    ):
        raise CaptureV3Error("approved_operation_binding_invalid")
    body = {key: item for key, item in value.items() if key != "approval_sha256"}
    approval_sha = hashlib.sha256(canonical_json(body)).hexdigest()
    if approval_sha != expected_sha256:
        raise CaptureV3Error("approved_operation_pin_mismatch")
    try:
        m0_body = value["m0_measurement"]
        m0_sha = target_measurement_v3_sha256(m0_body)
        m0 = TargetMeasurementV3.stamp(
            volume_uuid=m0_body["target_volume_uuid"],
            root_id=m0_body["target_root_id"],
            container_reference=m0_body["target_container_reference"],
            device_identifier=m0_body["target_device_identifier"],
            available_bytes=m0_body["available_bytes"],
            observed_at_utc=m0_body["observed_at_utc"],
            monotonic_start_ns=m0_body["monotonic_start_ns"],
            monotonic_end_ns=m0_body["monotonic_end_ns"],
        )
    except (KeyError, AbsenceGapChainError, TypeError) as exc:
        raise CaptureV3Error("approved_operation_m0_invalid") from exc
    measured_at = datetime.fromisoformat(m0.observed_at_utc.replace("Z", "+00:00"))
    receipt_time = datetime.fromisoformat(
        capacity_receipt["kingston_measurement_observed_at_utc"].replace("Z", "+00:00")
    )
    if (
        m0.measurement_sha256 != m0_sha
        or m0.measurement_sha256 != value["m0_measurement_sha256"]
        or m0.measurement_sha256 != capacity_receipt["kingston_measurement_sha256"]
        or m0.observed_at_utc != capacity_receipt["kingston_measurement_observed_at_utc"]
        or measured_at != receipt_time
        or m0.target_volume_uuid.lower() != capacity_receipt["kingston_volume_uuid"].lower()
        or m0.target_root_id != capacity_receipt["kingston_root_id"]
        or m0.available_bytes != capacity_receipt["F_live_available_bytes"]
        or m0.target_volume_uuid.lower() != capture_spec["target_volume_uuid"].lower()
        or m0.target_root_id != capture_spec["target_root_id"]
        or m0.target_volume_uuid.lower() == source_data_plan.data_volume_uuid.lower()
        or m0.target_container_reference == source_data_plan.data_container_reference
        or m0.target_device_identifier == source_data_plan.data_device_identifier
        or value["capacity_receipt_sha256"] != capacity_receipt_sha256
        or value["operation_sha256"]
        != approved_operation_v3_sha256(value["operation_id"], capture_spec_sha256)
        or measured_at
        <= datetime.fromisoformat(
            capture_spec["personal_backup_transition_completed_at_utc"].replace("Z", "+00:00")
        )
    ):
        raise CaptureV3Error("approved_operation_m0_join_invalid")
    return ApprovedOperationV3(
        expected_sha256,
        value["operation_id"],
        value["operation_sha256"],
        m0,
        capacity_receipt_sha256,
        _APPROVED_OPERATION_TOKEN,
    )


class TargetIdentityProbe(Protocol):
    def observe(self, mount_path: Path, expected_volume_uuid: str) -> tuple[str, str]: ...


class DarwinTargetIdentityProbe:
    """Read APFS container and device identity using diskutil."""

    def observe(self, mount_path: Path, expected_volume_uuid: str) -> tuple[str, str]:
        try:
            result = subprocess.run(
                ["/usr/sbin/diskutil", "info", "-plist", str(mount_path)],
                capture_output=True,
                check=False,
                timeout=20,
                stdin=subprocess.DEVNULL,
                env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C", "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CaptureV3Error("target_diskutil_identity_probe_failed") from exc
        if result.returncode != 0:
            raise CaptureV3Error("target_diskutil_identity_probe_failed")
        try:
            value = plistlib.loads(result.stdout)
        except (plistlib.InvalidFileException, ValueError, TypeError) as exc:
            raise CaptureV3Error("target_diskutil_identity_probe_invalid") from exc
        container = value.get("APFSContainerReference") if isinstance(value, Mapping) else None
        device = value.get("DeviceIdentifier") if isinstance(value, Mapping) else None
        volume_uuid = (
            value.get("APFSVolumeUUID", value.get("VolumeUUID"))
            if isinstance(value, Mapping)
            else None
        )
        if (
            not isinstance(container, str)
            or not re.fullmatch(r"disk[0-9]+", container)
            or not isinstance(device, str)
            or not re.fullmatch(r"disk[0-9]+s[0-9]+", device)
            or not isinstance(volume_uuid, str)
            or volume_uuid.lower() != expected_volume_uuid.lower()
        ):
            raise CaptureV3Error("target_diskutil_identity_unresolved")
        return container, device


class DescriptorBoundTargetSessionV3:
    """Create-only target capability bound to a live storage guard and root fd."""

    def __init__(
        self,
        writer: DescriptorWriter,
        *,
        volume_uuid: str,
        root_id: str,
    ) -> None:
        guard = writer.storage_guard if isinstance(writer, DescriptorWriter) else None
        if (
            guard is None
            or writer._read_only
            or str(guard.volume_uuid).lower() != volume_uuid.lower()
            or str(guard.root_id) != root_id
        ):
            raise CaptureV3Error("descriptor_target_binding_required")
        self.writer = writer
        self.target_device = writer.device
        self.guard = guard
        self.volume_uuid = volume_uuid.lower()
        self.root_id = root_id
        self.identity_probe = DarwinTargetIdentityProbe()
        self._candidate_fds: dict[str, tuple[int, tuple[int, int]]] = {}
        self._token = _TARGET_CAPABILITY_TOKEN

    def revalidate(self, required_free_bytes: int) -> TargetMeasurementV3:
        if type(required_free_bytes) is not int or required_free_bytes < 0:
            raise CaptureV3Error("target_reserve_invalid")
        monotonic_start = time.monotonic_ns()
        self.writer.assert_storage_binding(self.guard)
        resolved = self.guard.revalidate(min_free_bytes=required_free_bytes)
        available = self.guard.available_bytes
        if type(available) is not int or available < required_free_bytes:
            raise CaptureV3Error("target_live_capacity_insufficient")
        container, device = self.identity_probe.observe(
            self.guard.mount_path, str(self.guard.volume_uuid)
        )
        if resolved.mount_device != os.fstat(self.guard.mount_fd).st_dev:
            raise CaptureV3Error("target_mount_device_drift")
        observed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        measurement = TargetMeasurementV3.stamp(
            volume_uuid=str(self.guard.volume_uuid),
            root_id=str(self.guard.root_id),
            container_reference=container,
            device_identifier=device,
            available_bytes=available,
            observed_at_utc=observed_at,
            monotonic_start_ns=monotonic_start,
            monotonic_end_ns=time.monotonic_ns(),
        )
        self.writer.assert_storage_binding(self.guard)
        return measurement

    def _ensure_directory(self, relative: str, required_free_bytes: int) -> int:
        if (
            not isinstance(relative, str)
            or relative.startswith("/")
            or any(part in {"", ".", ".."} for part in relative.split("/"))
        ):
            raise CaptureV3Error("target_directory_path_invalid")
        current = os.dup(self.writer.root_fd)
        try:
            for component in relative.split("/"):
                try:
                    child = os.open(
                        component,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=current,
                    )
                except FileNotFoundError:
                    self.revalidate(required_free_bytes)
                    try:
                        os.mkdir(component, mode=0o700, dir_fd=current)
                    except FileExistsError:
                        pass
                    else:
                        os.fsync(current)
                    self.revalidate(required_free_bytes)
                    child = os.open(
                        component,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=current,
                    )
                st = os.fstat(child)
                if (
                    not stat.S_ISDIR(st.st_mode)
                    or st.st_dev != self.writer.device
                    or st.st_uid != os.getuid()
                    or stat.S_IMODE(st.st_mode) != 0o700
                ):
                    os.close(child)
                    raise CaptureV3Error("target_directory_identity_invalid")
                os.close(current)
                current = child
            return current
        except BaseException:
            os.close(current)
            raise

    def begin_new_candidate(self, required_free_bytes: int) -> str:
        parent_fd = self._ensure_directory(_CANDIDATE_BASE, required_free_bytes)
        try:
            for _attempt in range(8):
                candidate_id = secrets.token_hex(16)
                self.revalidate(required_free_bytes)
                try:
                    os.mkdir(candidate_id, mode=0o700, dir_fd=parent_fd)
                except FileExistsError:
                    continue
                os.fsync(parent_fd)
                self.revalidate(required_free_bytes)
                fd = os.open(
                    candidate_id,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent_fd,
                )
                st = os.fstat(fd)
                if not stat.S_ISDIR(st.st_mode) or st.st_dev != self.writer.device:
                    os.close(fd)
                    raise CaptureV3Error("candidate_directory_identity_invalid")
                self._candidate_fds[candidate_id] = (fd, (st.st_dev, st.st_ino))
                return candidate_id
            raise CaptureV3Error("candidate_id_collision_limit")
        finally:
            os.close(parent_fd)

    def _candidate_path(self, candidate_id: str, relative_path: str) -> str:
        if (
            self._token is not _TARGET_CAPABILITY_TOKEN
            or not _CANDIDATE_ID_RE.fullmatch(candidate_id)
            or candidate_id not in self._candidate_fds
            or not isinstance(relative_path, str)
            or relative_path.startswith("/")
            or any(part in {"", ".", ".."} for part in relative_path.split("/"))
        ):
            raise CaptureV3Error("candidate_member_path_invalid")
        self._assert_candidate(candidate_id)
        return f"{_CANDIDATE_BASE}/{candidate_id}/{relative_path}"

    def _assert_candidate(self, candidate_id: str) -> None:
        fd, identity = self._candidate_fds[candidate_id]
        st = os.fstat(fd)
        parent_fd = self.writer.open_directory(_CANDIDATE_BASE)
        try:
            current = os.stat(candidate_id, dir_fd=parent_fd, follow_symlinks=False)
        finally:
            os.close(parent_fd)
        if (st.st_dev, st.st_ino) != identity or (current.st_dev, current.st_ino) != identity:
            raise CaptureV3Error("candidate_directory_replaced")
        self.writer.assert_storage_binding(self.guard)

    def create_member(self, candidate_id: str, relative_path: str, payload: bytes) -> None:
        self.revalidate(0)
        path = self._candidate_path(candidate_id, relative_path)
        self.writer.create(path, payload)
        self._assert_candidate(candidate_id)

    def prepare_member_parent(
        self, candidate_id: str, relative_path: str, required_free_bytes: int
    ) -> None:
        full_path = self._candidate_path(candidate_id, relative_path)
        parent = full_path.rpartition("/")[0]
        fd = self._ensure_directory(parent, required_free_bytes)
        try:
            self._assert_candidate(candidate_id)
        finally:
            os.close(fd)

    def read_member(self, candidate_id: str, relative_path: str) -> bytes:
        self._assert_candidate(candidate_id)
        value = self.writer.read_bytes(self._candidate_path(candidate_id, relative_path))
        self._assert_candidate(candidate_id)
        return value

    def member_allocated_bytes(self, candidate_id: str, relative_path: str) -> int | None:
        """Measure durable blocks after reopening the create-only member no-follow."""
        self._assert_candidate(candidate_id)
        st = self.writer.stat(self._candidate_path(candidate_id, relative_path))
        self._assert_candidate(candidate_id)
        blocks = getattr(st, "st_blocks", None)
        if type(blocks) is not int or blocks < 0:
            return None
        return blocks * 512

    def close(self) -> None:
        for fd, _identity in self._candidate_fds.values():
            os.close(fd)
        self._candidate_fds.clear()


class ReadOnlyCandidateVerifierV3:
    """Prepublication verifier with a read-only descriptor capability only."""

    def __init__(
        self,
        reader: DescriptorWriter,
        *,
        scratch: ScratchSessionV3,
        source_device: int,
    ) -> None:
        guard = reader.storage_guard if isinstance(reader, DescriptorWriter) else None
        if guard is None or not reader._read_only or not getattr(guard, "read_only", False):
            raise CaptureV3Error("readonly_verifier_capability_required")
        self.reader = reader
        self.guard = guard
        target_device = reader.device
        self.scratch = scratch
        self.source_device = source_device
        self.target_device = target_device
        if (
            not isinstance(getattr(scratch, "volume_uuid", None), str)
            or not isinstance(getattr(scratch, "root_id", None), str)
            or type(getattr(scratch, "device", None)) is not int
            or getattr(scratch, "filesystem_type", None) not in {"apfs", "hfs"}
            or type(getattr(scratch, "free_floor_bytes", None)) is not int
            or type(getattr(scratch, "max_total_bytes", None)) is not int
            or type(source_device) is not int
            or type(target_device) is not int
            or scratch.device in {source_device, target_device}
        ):
            raise CaptureV3Error("pinned_scratch_volume_alias")

    @staticmethod
    def _manifest_path(role: str, path: str) -> str:
        if role not in PRESENT_ROLES or not isinstance(path, str) or "\x00" in path:
            raise CaptureV3Error("independent_verifier_member_locator_invalid")
        return f"manifest/{role}/{hashlib.sha256(path.encode()).hexdigest()}.json"

    def _iter_files(self, candidate_id: str) -> Iterator[str]:
        if not _CANDIDATE_ID_RE.fullmatch(candidate_id):
            raise CaptureV3Error("candidate_id_invalid")
        root = self.reader.open_directory(f"{_CANDIDATE_BASE}/{candidate_id}")

        def descend(prefix: str, directory_fd: int, depth: int) -> Iterator[str]:
            if depth > 256:
                raise CaptureV3Error("candidate_directory_depth_limit")
            with os.scandir(directory_fd) as listing:
                for item in listing:
                    name = item.name
                    if name in {".", ".."} or "/" in name or "\x00" in name:
                        raise CaptureV3Error("candidate_member_path_invalid")
                    relative = f"{prefix}/{name}" if prefix else name
                    st = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if stat.S_ISDIR(st.st_mode):
                        child = os.open(
                            name,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory_fd,
                        )
                        try:
                            child_stat = os.fstat(child)
                            if (child_stat.st_ino, child_stat.st_dev) != (st.st_ino, st.st_dev):
                                raise CaptureV3Error("candidate_directory_replaced")
                            yield from descend(relative, child, depth + 1)
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
                        yield relative
                    else:
                        raise CaptureV3Error("candidate_member_type_invalid")

        try:
            yield from descend("", root, 0)
        finally:
            os.close(root)

    def verify_prepublication(self, **kwargs: Any) -> Mapping[str, Any]:
        records = _SQLiteVerifierRecordsV3(
            self.scratch,
            source_device=self.source_device,
            target_device=self.target_device,
        )
        try:
            return self._verify_prepublication_with_records(_records=records, **kwargs)
        finally:
            records.close()

    def _verify_prepublication_with_records(
        self,
        *,
        _records: _SQLiteVerifierRecordsV3,
        candidate_id: str,
        expected_plan_sha256: str,
        expected_policy_sha256: str,
        expected_overlay_sha256: str,
        expected_capture_spec_sha256: str,
        expected_approved_operation_sha256: str,
        expected_approved_operation_artifact_sha256: str,
        expected_candidate_payload_manifest_sha256: str,
        expected_candidate_index_sha256: str,
        candidate_index: Mapping[str, Any],
        policy: Mapping[str, Any],
        protected_overlay: Mapping[str, Any],
        role_policies: Mapping[str, RolePolicyV3],
        git_reconstruction: GitReconstructionV3 | None,
        expected_role_dispositions: Sequence[Mapping[str, Any]],
        allow_published_index: bool = False,
    ) -> Mapping[str, Any]:
        """Recompute file, payload and role summaries from physically read manifests."""
        for digest in (
            expected_plan_sha256,
            expected_policy_sha256,
            expected_overlay_sha256,
            expected_capture_spec_sha256,
            expected_approved_operation_sha256,
            expected_approved_operation_artifact_sha256,
            expected_candidate_payload_manifest_sha256,
            expected_candidate_index_sha256,
        ):
            if not isinstance(digest, str) or not _PIN_RE.fullmatch(digest):
                raise CaptureV3Error("independent_verifier_external_pin_invalid")
        if policy_roster_v3_sha256(policy) != expected_policy_sha256:
            raise CaptureV3Error("independent_verifier_policy_pin_mismatch")
        if policy.get("plan_sha256") != expected_plan_sha256:
            raise CaptureV3Error("independent_verifier_plan_join_mismatch")
        validate_protected_overlay_v3(protected_overlay, expected_overlay_sha256)
        if policy.get("protected_overlay_sha256") != expected_overlay_sha256:
            raise CaptureV3Error("independent_verifier_overlay_join_mismatch")
        if set(role_policies) != set(PRESENT_ROLES):
            raise CaptureV3Error("independent_verifier_role_policy_roster_invalid")
        if (
            not isinstance(candidate_index, Mapping)
            or candidate_index.get("candidate_index_sha256") != expected_candidate_index_sha256
            or hashlib.sha256(
                canonical_json(
                    {
                        key: value
                        for key, value in candidate_index.items()
                        if key != "candidate_index_sha256"
                    }
                )
            ).hexdigest()
            != expected_candidate_index_sha256
            or candidate_index.get("plan_sha256") != expected_plan_sha256
            or candidate_index.get("policy_roster_sha256") != expected_policy_sha256
            or candidate_index.get("protected_overlay_sha256") != expected_overlay_sha256
            or candidate_index.get("capture_spec_sha256") != expected_capture_spec_sha256
            or candidate_index.get("approved_operation_sha256")
            != expected_approved_operation_sha256
            or candidate_index.get("approved_operation_artifact_sha256")
            != expected_approved_operation_artifact_sha256
        ):
            raise CaptureV3Error("independent_verifier_index_pin_mismatch")
        self.guard.revalidate()
        records = _records
        manifest_count = 0
        index_seen = False
        projections = {row["role"]: row for row in policy["roles"]}
        for manifest_path in self._iter_files(candidate_id):
            records.add_actual(manifest_path)
            if manifest_path == "INDEX.json":
                index_seen = True
                if not allow_published_index:
                    raise CaptureV3Error("late_unaccepted_candidate_index")
                continue
            if not manifest_path.startswith("manifest/"):
                continue
            if not manifest_path.endswith(".json"):
                raise CaptureV3Error("independent_verifier_manifest_set_invalid")
            manifest_count += 1
            records.add_expected(manifest_path)
            raw = self.reader.read_bytes(f"{_CANDIDATE_BASE}/{candidate_id}/{manifest_path}")
            try:
                body = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_pairs)
            except (UnicodeDecodeError, ValueError) as exc:
                raise CaptureV3Error("independent_verifier_manifest_json_invalid") from exc
            if not isinstance(body, Mapping):
                raise CaptureV3Error("independent_verifier_manifest_row_invalid")
            role, path = body.get("role"), body.get("path")
            if role not in PRESENT_ROLES or not isinstance(path, str):
                raise CaptureV3Error("independent_verifier_manifest_locator_invalid")
            if (
                "\x00" in path
                or "\\" in path
                or path.startswith("/")
                or any(part in {"", ".", ".."} for part in path.split("/"))
                and path != ""
            ):
                raise CaptureV3Error("independent_verifier_manifest_locator_invalid")
            if manifest_path != self._manifest_path(role, path):
                raise CaptureV3Error("independent_verifier_manifest_path_mismatch")
            projection = projections[role]
            role_policy = role_policies[role]
            if (
                not isinstance(role_policy, RolePolicyV3)
                or role_policy.role != role
                or _canonical_digest(role_policy.document, "role_policy_sha256")
                != projection.get("role_policy_sha256")
            ):
                raise CaptureV3Error("independent_verifier_role_policy_pin_mismatch")
            calculated_effective = effective_policy_sha256(
                role=role,
                locator=projection["relative_locator"],
                kind=projection["kind"],
                scope=projection["scope"],
                mode=projection["mode"],
                role_policy_sha256=projection["role_policy_sha256"],
                protected_overlay_sha256=expected_overlay_sha256,
            )
            if calculated_effective != projection.get("effective_policy_sha256"):
                raise CaptureV3Error("independent_verifier_effective_policy_mismatch")
            core_fields = {
                "role",
                "path",
                "kind",
                "disposition",
                "size_bytes",
                "signature",
                "source_device",
                "source_inode",
                "source_nlink",
                "symlink_target_sha256",
                "parent_identity_chain",
                "protection_class",
                "overlay_rule_sha256",
                "authorization_receipt_sha256",
            }
            expected_body_fields = core_fields | {"target_path", "payload"}
            if body.get("kind") == "symlink" and body.get("disposition") == "include_opaque":
                expected_body_fields = core_fields | {"target_path", "symlink_target_hex"}
            if set(body) != expected_body_fields:
                raise CaptureV3Error("independent_verifier_manifest_schema_invalid")
            signature = body.get("signature")
            if (
                not isinstance(signature, list)
                or len(signature) != 10
                or any(type(value) is not int for value in signature)
                or type(body.get("size_bytes")) is not int
                or body["size_bytes"] < 0
                or type(body.get("source_device")) is not int
                or type(body.get("source_inode")) is not int
                or type(body.get("source_nlink")) is not int
                or not isinstance(body.get("parent_identity_chain"), list)
                or any(
                    not isinstance(identity, list)
                    or len(identity) != 2
                    or any(type(value) is not int for value in identity)
                    for identity in body["parent_identity_chain"]
                )
            ):
                raise CaptureV3Error("independent_verifier_manifest_metadata_invalid")
            if (
                body.get("disposition") not in MEMBER_MODES
                or body.get("kind") not in {"file", "directory", "symlink"}
                or body.get("disposition") == "include_opaque"
                and body.get("size_bytes") > 0
                and body.get("kind") != "file"
                or body.get("disposition") == "include_opaque"
                and body.get("target_path") != _member_path(role, path)
                or body.get("disposition") != "include_opaque"
                and body.get("target_path") is not None
                or body.get("role_mode") is not None
            ):
                raise CaptureV3Error("independent_verifier_manifest_policy_invalid")
            if (
                projection.get("relative_locator") is None
                or projection.get("mode") != ROLE_MODES[role]
                or projection.get("kind") not in {"directory", "file"}
                or not _PIN_RE.fullmatch(projection.get("effective_policy_sha256", ""))
            ):
                raise CaptureV3Error("independent_verifier_policy_projection_invalid")
            if body["disposition"] == "include_opaque" and body["kind"] == "symlink":
                try:
                    symlink_bytes = bytes.fromhex(body["symlink_target_hex"])
                except (TypeError, ValueError) as exc:
                    raise CaptureV3Error("independent_verifier_symlink_manifest_invalid") from exc
                if sha256(symlink_bytes) != body["symlink_target_sha256"]:
                    raise CaptureV3Error("independent_verifier_symlink_hash_mismatch")
            if (
                body["disposition"] == "include_opaque"
                and projection["scope"] == "protected_opaque"
            ):
                if not any(
                    row["role"] == role
                    and row["disposition"] == PROTECTED_CUSTODY_MODE
                    and _under(path, row["relative_prefix"])
                    for row in protected_overlay["constraints"]
                ):
                    raise CaptureV3Error("independent_verifier_protected_inclusion_unapproved")
            expected_protection = _protection_metadata(
                role, path, protected_overlay, body["disposition"]
            )
            observed_protection = (
                body["protection_class"],
                body["overlay_rule_sha256"],
                body["authorization_receipt_sha256"],
            )
            if observed_protection != expected_protection:
                raise CaptureV3Error("independent_verifier_protection_lineage_mismatch")
            expected_disposition = _recompute_member_disposition_v3(
                role=role,
                path=path,
                scope=projection["scope"],
                role_policy=role_policy,
                overlay=protected_overlay,
                git_reconstruction=git_reconstruction,
            )
            if body["disposition"] != expected_disposition:
                raise CaptureV3Error("independent_verifier_member_disposition_mismatch")
            records.add_role_row(role, path, body)
            payload = body.get("payload")
            if payload is not None:
                if (
                    body.get("kind") != "file"
                    or not isinstance(payload, Mapping)
                    or set(payload) != {"payload_sha256", "size_bytes", "pages"}
                ):
                    raise CaptureV3Error("independent_verifier_payload_manifest_invalid")
                page_digest = hashlib.sha256()
                page_size = 0
                for page in payload["pages"]:
                    if not isinstance(page, Mapping) or set(page) != {
                        "path",
                        "sha256",
                        "size_bytes",
                    }:
                        raise CaptureV3Error("independent_verifier_payload_page_invalid")
                    page_path = page["path"]
                    if (
                        not isinstance(page_path, str)
                        or not page_path.startswith(f"payload/{role}/")
                        or type(page["size_bytes"]) is not int
                        or page["size_bytes"] < 0
                    ):
                        raise CaptureV3Error("independent_verifier_payload_page_invalid")
                    records.add_expected(page_path)
                    page_raw = self.reader.read_bytes(
                        f"{_CANDIDATE_BASE}/{candidate_id}/{page_path}"
                    )
                    if sha256(page_raw) != page["sha256"] or len(page_raw) != page["size_bytes"]:
                        raise CaptureV3Error("independent_verifier_payload_page_mismatch")
                    page_digest.update(page_raw)
                    page_size += len(page_raw)
                if (
                    page_digest.hexdigest() != payload["payload_sha256"]
                    or page_size != payload["size_bytes"]
                ):
                    raise CaptureV3Error("independent_verifier_payload_digest_mismatch")
            elif body.get("disposition") == "include_opaque" and body.get("kind") == "file":
                raise CaptureV3Error("independent_verifier_payload_missing")
            records.add_member(
                {
                    "role": role,
                    "path": path,
                    "target_path": body.get("target_path"),
                    "sha256": sha256(raw),
                    "size_bytes": len(raw),
                }
            )
        if manifest_count == 0:
            raise CaptureV3Error("independent_verifier_manifest_set_invalid")
        if allow_published_index:
            if not index_seen:
                raise CaptureV3Error("published_candidate_index_missing")
            records.add_expected("INDEX.json")
        records.assert_membership_exact()
        recomputed_roles = _recompute_role_dispositions_v3(
            {role: records.iter_role(role) for role in PRESENT_ROLES},
            projections,
        )
        if canonical_json(recomputed_roles) != canonical_json(list(expected_role_dispositions)):
            raise CaptureV3Error("independent_verifier_role_summary_mismatch")
        members_sha = records.members_sha256()
        if members_sha != expected_candidate_payload_manifest_sha256:
            raise CaptureV3Error("independent_verifier_payload_manifest_pin_mismatch")
        receipt = {
            "schema": "brain-host-absence-gap-v3-independent-verifier-receipt",
            "candidate_id": candidate_id,
            "plan_sha256": expected_plan_sha256,
            "policy_roster_sha256": expected_policy_sha256,
            "protected_overlay_sha256": expected_overlay_sha256,
            "capture_spec_sha256": expected_capture_spec_sha256,
            "approved_operation_sha256": expected_approved_operation_sha256,
            "candidate_payload_manifest_sha256": expected_candidate_payload_manifest_sha256,
            "candidate_index_sha256": expected_candidate_index_sha256,
            "members_sha256": members_sha,
            "role_dispositions_sha256": hashlib.sha256(
                canonical_json(recomputed_roles)
            ).hexdigest(),
            "verified": True,
        }
        receipt["receipt_sha256"] = hashlib.sha256(canonical_json(receipt)).hexdigest()
        self.guard.revalidate()
        return receipt

    def verify_published_candidate(
        self,
        *,
        candidate_id: str,
        expected_plan_sha256: str,
        expected_policy_sha256: str,
        expected_overlay_sha256: str,
        expected_capture_spec_sha256: str,
        expected_approved_operation_sha256: str,
        expected_approved_operation_artifact_sha256: str,
        expected_candidate_index_sha256: str,
        policy: Mapping[str, Any],
        protected_overlay: Mapping[str, Any],
        role_policies: Mapping[str, RolePolicyV3],
        git_reconstruction: GitReconstructionV3 | None,
    ) -> Mapping[str, Any]:
        """Fresh read-only pass sourced from stored INDEX and physical members."""
        if not isinstance(candidate_id, str) or not _CANDIDATE_ID_RE.fullmatch(candidate_id):
            raise CaptureV3Error("published_candidate_id_invalid")
        raw_index = self.reader.read_bytes(f"{_CANDIDATE_BASE}/{candidate_id}/INDEX.json")
        try:
            stored_index = json.loads(
                raw_index.decode("utf-8"), object_pairs_hook=_no_duplicate_pairs
            )
        except (UnicodeDecodeError, ValueError) as exc:
            raise CaptureV3Error("published_candidate_index_invalid") from exc
        if not isinstance(stored_index, Mapping):
            raise CaptureV3Error("published_candidate_index_invalid")
        if (
            frozenset(stored_index) != INDEX_FIELDS_V3
            or stored_index.get("schema") != INDEX_V3_SCHEMA
            or stored_index.get("candidate_status") != CAPTURE_STATUS
            or any(stored_index.get(field) is not False for field in INDEX_FLAGS)
        ):
            raise CaptureV3Error("published_candidate_index_schema_invalid")
        stored_sha = hashlib.sha256(
            canonical_json(
                {
                    key: value
                    for key, value in stored_index.items()
                    if key != "candidate_index_sha256"
                }
            )
        ).hexdigest()
        if (
            stored_index.get("candidate_index_sha256") != expected_candidate_index_sha256
            or stored_sha != expected_candidate_index_sha256
            or not isinstance(stored_index.get("role_dispositions"), list)
            or not isinstance(stored_index.get("candidate_payload_manifest_sha256"), str)
        ):
            raise CaptureV3Error("published_candidate_index_pin_mismatch")
        result = self.verify_prepublication(
            candidate_id=candidate_id,
            expected_plan_sha256=expected_plan_sha256,
            expected_policy_sha256=expected_policy_sha256,
            expected_overlay_sha256=expected_overlay_sha256,
            expected_capture_spec_sha256=expected_capture_spec_sha256,
            expected_approved_operation_sha256=expected_approved_operation_sha256,
            expected_approved_operation_artifact_sha256=expected_approved_operation_artifact_sha256,
            expected_candidate_payload_manifest_sha256=stored_index[
                "candidate_payload_manifest_sha256"
            ],
            expected_candidate_index_sha256=expected_candidate_index_sha256,
            candidate_index=stored_index,
            policy=policy,
            protected_overlay=protected_overlay,
            role_policies=role_policies,
            git_reconstruction=git_reconstruction,
            expected_role_dispositions=stored_index["role_dispositions"],
            allow_published_index=True,
        )
        if self.reader.read_bytes(f"{_CANDIDATE_BASE}/{candidate_id}/INDEX.json") != raw_index:
            raise CaptureV3Error("published_candidate_index_changed_during_verification")
        return result


def _publish_then_verify_index_v3(
    *,
    publish_index: Callable[[], None],
    final_verifier_factory: Callable[[], ReadOnlyCandidateVerifierV3],
    prepublication_verifier: ReadOnlyCandidateVerifierV3,
    source_device: int,
    candidate_id: str,
    expected_plan_sha256: str,
    expected_policy_sha256: str,
    expected_overlay_sha256: str,
    expected_capture_spec_sha256: str,
    expected_approved_operation_sha256: str,
    expected_approved_operation_artifact_sha256: str,
    expected_candidate_index_sha256: str,
    expected_candidate_payload_manifest_sha256: str,
    policy: Mapping[str, Any],
    protected_overlay: Mapping[str, Any],
    role_policies: Mapping[str, RolePolicyV3],
    git_reconstruction: GitReconstructionV3 | None,
) -> Mapping[str, Any]:
    """Publish the final index, then verify it through a fresh RO descriptor."""
    publish_index()
    final_verifier = final_verifier_factory()
    try:
        if (
            not isinstance(final_verifier, ReadOnlyCandidateVerifierV3)
            or final_verifier is prepublication_verifier
            or final_verifier.reader is prepublication_verifier.reader
            or final_verifier.source_device != source_device
        ):
            raise CaptureV3Error("fresh_final_verifier_capability_required")
        result = final_verifier.verify_published_candidate(
            candidate_id=candidate_id,
            expected_plan_sha256=expected_plan_sha256,
            expected_policy_sha256=expected_policy_sha256,
            expected_overlay_sha256=expected_overlay_sha256,
            expected_capture_spec_sha256=expected_capture_spec_sha256,
            expected_approved_operation_sha256=expected_approved_operation_sha256,
            expected_approved_operation_artifact_sha256=(
                expected_approved_operation_artifact_sha256
            ),
            expected_candidate_index_sha256=expected_candidate_index_sha256,
            policy=policy,
            protected_overlay=protected_overlay,
            role_policies=role_policies,
            git_reconstruction=git_reconstruction,
        )
        if (
            result.get("verified") is not True
            or result.get("candidate_index_sha256") != expected_candidate_index_sha256
            or result.get("members_sha256") != expected_candidate_payload_manifest_sha256
        ):
            raise CaptureV3Error("published_candidate_verification_failed")
        return result
    finally:
        final_verifier.reader.close()


def _recompute_role_dispositions_v3(
    entries_by_role: Mapping[str, Iterable[Mapping[str, Any]]],
    projections: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for role in sorted(PRESENT_ROLES):
        rows = entries_by_role[role]
        counts: dict[str, int | None] = {mode: 0 for mode in MEMBER_MODES}
        byte_counts: dict[str, int | None] = {mode: 0 for mode in MEMBER_MODES}
        manifest_digest = hashlib.sha256()
        manifest_digest.update(b"[")
        ranges: list[dict[str, Any]] = []
        unknown_descendants = False
        range_digest: Any = None
        range_count = 0
        range_first: str | None = None
        range_last: str | None = None
        row_count = 0
        for body in rows:
            row_count += 1
            core_fields = (
                "role",
                "path",
                "kind",
                "disposition",
                "size_bytes",
                "signature",
                "source_device",
                "source_inode",
                "source_nlink",
                "symlink_target_sha256",
                "parent_identity_chain",
                "protection_class",
                "overlay_rule_sha256",
                "authorization_receipt_sha256",
            )
            core = {key: body[key] for key in core_fields}
            path = body["path"]
            disposition = body["disposition"]
            size = body["size_bytes"]
            if type(size) is not int or size < 0:
                raise CaptureV3Error("independent_verifier_member_size_invalid")
            counts[disposition] = int(counts[disposition] or 0) + 1
            if disposition != "exclude_protected_unresolved":
                byte_counts[disposition] = int(byte_counts[disposition] or 0) + (
                    size if body["kind"] == "file" else 0
                )
            elif body["kind"] == "directory":
                unknown_descendants = True
            encoded = canonical_json(core)
            if row_count > 1:
                manifest_digest.update(b",")
            manifest_digest.update(encoded)
            if range_digest is None:
                range_digest = hashlib.sha256()
                range_digest.update(b"[")
                range_first = path
            elif range_count:
                range_digest.update(b",")
            range_digest.update(encoded)
            range_count += 1
            range_last = path
            if range_count == 512:
                ranges.append(
                    {
                        "first_path": range_first,
                        "last_path": range_last,
                        "count": range_count,
                        "sha256": range_digest.hexdigest(),
                    }
                )
                range_digest = None
                range_count = 0
        if range_digest is not None:
            ranges.append(
                {
                    "first_path": range_first,
                    "last_path": range_last,
                    "count": range_count,
                    "sha256": range_digest.hexdigest(),
                }
            )
        manifest_digest.update(b"]")
        if unknown_descendants:
            counts["exclude_protected_unresolved"] = None
            byte_counts["exclude_protected_unresolved"] = None
        included = int(counts["include_opaque"] or 0)
        excluded_modes = (
            "exclude_backup_required",
            "exclude_reissue",
            "git_reconstructible_proven",
        )
        excluded = sum(int(counts[mode] or 0) for mode in excluded_modes)
        included_bytes = int(byte_counts["include_opaque"] or 0)
        excluded_bytes = sum(int(byte_counts[mode] or 0) for mode in excluded_modes)
        projection = projections[role]
        result.append(
            {
                "role": role,
                "present": True,
                "role_mode": ROLE_MODES[role],
                "relative_locator": projection["relative_locator"],
                "kind": projection["kind"],
                "scope": projection["scope"],
                "effective_policy_sha256": projection["effective_policy_sha256"],
                "member_manifest_sha256": manifest_digest.hexdigest(),
                "observed_entry_count": row_count,
                "included_entry_count": included,
                "excluded_entry_count": excluded,
                "unresolved_entry_count": counts["exclude_protected_unresolved"],
                "included_logical_bytes": included_bytes,
                "excluded_logical_bytes": excluded_bytes,
                "unresolved_logical_bytes": byte_counts["exclude_protected_unresolved"],
                "member_modes": sorted(
                    mode for mode, count in counts.items() if count is None or count != 0
                ),
                "member_mode_counts": counts,
                "member_mode_logical_bytes": byte_counts,
                "member_ranges": ranges,
            }
        )
    absent = next(row for row in projections.values() if row["role"] == "verifier_worktree")
    result.append(
        {
            "role": "verifier_worktree",
            "present": False,
            "role_mode": ROLE_MODES["verifier_worktree"],
            "disposition": CAPTURE_STATUS,
            "witness_sha256": absent["witness_sha256"],
            "relative_locator": absent["relative_locator"],
            "expected_fetchable_commit": absent["expected_fetchable_commit"],
            "historical_non_git_state": absent["historical_non_git_state"],
        }
    )
    return result


def _sig(st: os.stat_result) -> tuple[int, ...]:
    return (
        st.st_dev,
        st.st_ino,
        st.st_mode,
        st.st_nlink,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
        st.st_uid,
        st.st_gid,
        getattr(st, "st_flags", 0),
    )


def _kind(st: os.stat_result) -> str:
    if stat.S_ISDIR(st.st_mode):
        return "directory"
    if stat.S_ISREG(st.st_mode):
        return "file"
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    raise CaptureV3Error("source_member_type_unknown")


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def _has_protected_inclusion_below(role: str, path: str, overlay: Mapping[str, Any]) -> bool:
    return any(
        row["role"] == role
        and row["disposition"] == PROTECTED_CUSTODY_MODE
        and _under(row["relative_prefix"], path)
        for row in overlay["constraints"]
    )


def _protected_inclusion_children(role: str, path: str, overlay: Mapping[str, Any]) -> set[str]:
    children: set[str] = set()
    for row in overlay["constraints"]:
        prefix = row["relative_prefix"]
        if (
            row["role"] == role
            and row["disposition"] == PROTECTED_CUSTODY_MODE
            and _under(prefix, path)
            and prefix != path
        ):
            suffix = prefix[len(path.rstrip("/")) :].lstrip("/")
            if suffix:
                children.add(suffix.split("/", 1)[0])
    return children


def _protection_metadata(
    role: str, path: str, overlay: Mapping[str, Any], disposition: str
) -> tuple[str | None, str | None, str | None]:
    if disposition != "include_opaque":
        return None, None, None
    matching = [
        row
        for row in overlay["constraints"]
        if row["role"] == role and _under(path, row["relative_prefix"])
    ]
    if not matching:
        return None, None, None
    winner = max(matching, key=lambda row: len(row["relative_prefix"]))
    if winner["disposition"] != PROTECTED_CUSTODY_MODE:
        return None, None, None
    if winner["protection_class"] not in PROTECTED_CLASSES:
        raise CaptureV3Error("protected_member_class_invalid")
    rule_sha = hashlib.sha256(canonical_json(winner)).hexdigest()
    return winner["protection_class"], rule_sha, winner["authorization_receipt_sha256"]


def _classify(
    role: str,
    path: str,
    scope: str,
    overlay: Mapping[str, Any],
    role_policy: RolePolicyV3,
    git_receipt: GitReconstructionV3 | None,
) -> str:
    if role_policy._token is not _ROLE_POLICY_TOKEN or role_policy.role != role:
        raise CaptureV3Error("role_policy_validation_token_required")
    document = role_policy.document
    matching = [row for row in document["path_rules"] if _under(path, row["relative_prefix"])]
    disposition = (
        max(matching, key=lambda row: len(row["relative_prefix"]))["disposition"]
        if matching
        else document["default_disposition"]
    )
    matching_overlay = [
        row
        for row in overlay["constraints"]
        if row["role"] == role and _under(path, row["relative_prefix"])
    ]
    if matching_overlay:
        strongest = max(matching_overlay, key=lambda row: len(row["relative_prefix"]))
        if strongest["disposition"] == PROTECTED_CUSTODY_MODE:
            # Custody authorization may resolve protected-unresolved, but may
            # never override a backup, reissue, or receipt-bound Git exclusion.
            if disposition in {
                "exclude_reissue",
                "exclude_backup_required",
                "git_reconstructible_proven",
            }:
                return disposition
            return "include_opaque"
        return "exclude_protected_unresolved"
    if scope == "protected_opaque":
        return "exclude_protected_unresolved"
    if disposition is None:
        raise CaptureV3Error("source_member_disposition_unknown")
    if disposition == "git_reconstructible_proven":
        if (
            role != "research_checkout"
            or git_receipt is None
            or git_receipt._token is not _GIT_TOKEN
            or git_receipt.role != role
            or not _under(path, ".git")
        ):
            raise CaptureV3Error("git_reconstruction_receipt_token_required")
    if role in {
        "codex_mcp_config",
        "cursor_mcp_config",
        "helius_mcp_wrapper",
    } and disposition not in {
        "exclude_reissue",
        "exclude_protected_unresolved",
    }:
        raise CaptureV3Error("mcp_or_wrapper_reissue_only_policy_required")
    return disposition


def _recompute_member_disposition_v3(
    *,
    role: str,
    path: str,
    scope: str,
    role_policy: RolePolicyV3,
    overlay: Mapping[str, Any],
    git_reconstruction: GitReconstructionV3 | None,
) -> str:
    """Verifier-side policy projection from pinned path-only policy inputs."""
    rules = role_policy.document["path_rules"]
    matches = [row for row in rules if _under(path, row["relative_prefix"])]
    disposition = (
        max(matches, key=lambda row: len(row["relative_prefix"]))["disposition"]
        if matches
        else role_policy.document["default_disposition"]
    )
    overrides = [
        row
        for row in overlay["constraints"]
        if row["role"] == role and _under(path, row["relative_prefix"])
    ]
    if overrides:
        winner = max(overrides, key=lambda row: len(row["relative_prefix"]))
        if winner["disposition"] == PROTECTED_CUSTODY_MODE:
            if disposition in {
                "exclude_reissue",
                "exclude_backup_required",
                "git_reconstructible_proven",
            }:
                return disposition
            return "include_opaque"
        return "exclude_protected_unresolved"
    if scope == "protected_opaque":
        return "exclude_protected_unresolved"
    if disposition not in MEMBER_MODES:
        raise CaptureV3Error("independent_verifier_policy_disposition_unknown")
    if disposition == "git_reconstructible_proven":
        if (
            role != "research_checkout"
            or git_reconstruction is None
            or git_reconstruction._token is not _GIT_TOKEN
            or git_reconstruction.role != role
            or not _under(path, ".git")
        ):
            raise CaptureV3Error("independent_verifier_git_receipt_required")
    if (
        role in {"helius_mcp_wrapper", "codex_mcp_config", "cursor_mcp_config"}
        and disposition != "exclude_reissue"
    ):
        raise CaptureV3Error("independent_verifier_reissue_role_policy_invalid")
    return disposition


def _member_path(role: str, path: str) -> str:
    safe_role = role.replace("/", "_")
    return f"members/{safe_role}/{path or '_root'}"


def _walk_root(
    *,
    role: str,
    root_fd: int,
    source_device: int,
    overlay: Mapping[str, Any],
    role_policy: RolePolicyV3,
    scope: str,
    git_reconstruction: GitReconstructionV3 | None,
    scratch: ScratchSessionV3,
    target_device: int,
) -> Iterator[InventoryEntryV3]:
    root_stat = os.fstat(root_fd)
    if root_stat.st_dev != source_device:
        raise CaptureV3Error("source_mount_boundary")

    def add(
        path: str,
        parent_fd: int,
        name: str | None,
        st: os.stat_result,
        parent_chain: tuple[tuple[int, int], ...],
    ) -> InventoryEntryV3:
        kind = _kind(st)
        target = None
        if kind == "symlink":
            if name is None:
                raise CaptureV3Error("source_root_symlink_unsupported")
            raw = os.readlink(name, dir_fd=parent_fd)
            target = raw if isinstance(raw, bytes) else os.fsencode(raw)
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if _sig(current) != _sig(st):
                raise CaptureV3Error("source_member_changed_during_inventory")
        if kind == "file" and st.st_dev != source_device:
            raise CaptureV3Error("source_mount_boundary")
        disposition = _classify(role, path, scope, overlay, role_policy, git_reconstruction)
        protection = _protection_metadata(role, path, overlay, disposition)
        return InventoryEntryV3(
            role,
            path,
            kind,
            disposition,
            st.st_size if kind == "file" else 0,
            _sig(st),
            st.st_dev,
            st.st_ino,
            st.st_nlink,
            target,
            parent_chain,
            *protection,
        )

    root_identity = (root_stat.st_dev, root_stat.st_ino)
    yield add("", -1, None, root_stat, (root_identity,))
    if not stat.S_ISDIR(root_stat.st_mode):
        return
    pending = _SQLiteDirectoryQueueV3(
        scratch, source_device=source_device, target_device=target_device
    )
    try:
        pending.push("", (root_identity,))
        while item := pending.pop():
            prefix, parent_chain = item
            directory_fd = _open_dir(root_fd, prefix)
            try:
                dir_sig = _sig(os.fstat(directory_fd))
                prefix_disposition = _classify(
                    role, prefix, scope, overlay, role_policy, git_reconstruction
                )
                restricted_children = (
                    _protected_inclusion_children(role, prefix, overlay)
                    if prefix_disposition == "exclude_protected_unresolved"
                    and _has_protected_inclusion_below(role, prefix, overlay)
                    else set()
                )
                with os.scandir(directory_fd) as scan:
                    for directory_entry in scan:
                        name = directory_entry.name
                        if restricted_children and name not in restricted_children:
                            continue
                        if name in {".", ".."} or "/" in name or "\x00" in name:
                            raise CaptureV3Error("source_path_invalid")
                        relative = f"{prefix}/{name}" if prefix else name
                        st = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        if st.st_dev != source_device:
                            raise CaptureV3Error("source_mount_boundary")
                        entry = add(relative, directory_fd, name, st, parent_chain)
                        yield entry
                        if entry.kind == "directory" and (
                            entry.disposition != "exclude_protected_unresolved"
                            or _has_protected_inclusion_below(role, relative, overlay)
                        ):
                            child = os.open(
                                name,
                                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory_fd,
                            )
                            try:
                                if _sig(os.fstat(child)) != _sig(st):
                                    raise CaptureV3Error("source_member_changed_during_inventory")
                            finally:
                                os.close(child)
                            pending.push(relative, (*parent_chain, (st.st_dev, st.st_ino)))
                if _sig(os.fstat(directory_fd)) != dir_sig:
                    raise CaptureV3Error("source_directory_changed_during_inventory")
            finally:
                os.close(directory_fd)
    finally:
        pending.close()
    if _sig(os.fstat(root_fd)) != _sig(root_stat):
        raise CaptureV3Error("source_root_changed_during_inventory")


def _open_dir(root_fd: int, relative: str) -> int:
    current = os.dup(root_fd)
    try:
        for part in relative.split("/") if relative else ():
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _open_dir_checked(
    root_fd: int, relative: str, expected_chain: tuple[tuple[int, int], ...]
) -> int:
    current = os.dup(root_fd)
    try:
        first = os.fstat(current)
        observed = [(first.st_dev, first.st_ino)]
        for part in relative.split("/") if relative else ():
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = child
            st = os.fstat(current)
            observed.append((st.st_dev, st.st_ino))
        if tuple(observed) != expected_chain:
            raise CaptureV3Error("source_parent_identity_changed")
        return current
    except BaseException:
        os.close(current)
        raise


def _inventory_held_view_core_v3(
    view: HeldViewV3,
    *,
    plan: AbsenceGapPlanV2,
    policy: Mapping[str, Any],
    role_policies: Mapping[str, RolePolicyV3],
    protected_overlay: Mapping[str, Any],
    protected_overlay_sha256: str,
    scratch: ScratchSessionV3,
    target_device: int,
    git_reconstruction: GitReconstructionV3 | None = None,
) -> InventorySnapshotV3:
    """Inventory names/types/stat identity only; never reads file contents."""
    if view.plan != plan.held_data_plan or view.trusted_plan_sha256 != plan.held_data_plan_sha256:
        raise CaptureV3Error("source_plan_pin_mismatch")
    try:
        validate_protected_overlay_v3(protected_overlay, protected_overlay_sha256)
        policy_sha = policy_roster_v3_sha256(policy)
    except AbsenceGapChainError as exc:
        raise CaptureV3Error("policy_validation_failed") from exc
    if policy["protected_overlay_sha256"] != protected_overlay_sha256:
        raise CaptureV3Error("protected_overlay_policy_join_mismatch")
    if set(role_policies) != set(PRESENT_ROLES):
        raise CaptureV3Error("role_policy_roster_incomplete")
    mount_device = os.fstat(view.mount_fd).st_dev
    entries = _SQLiteInventoryEntriesV3(
        scratch, source_device=mount_device, target_device=target_device
    )
    view.revalidate()
    try:
        for role in sorted(PRESENT_ROLES):
            projection = next(item for item in policy["roles"] if item["role"] == role)
            role_policy = role_policies.get(role)
            declaration = plan.document["root_roster"][role]
            if (
                not isinstance(role_policy, RolePolicyV3)
                or role_policy._token is not _ROLE_POLICY_TOKEN
                or role_policy.role != role
                or role_policy.policy_sha256 != projection["role_policy_sha256"]
                or projection["relative_locator"] != declaration["relative_locator"]
                or projection["kind"] != declaration["kind"]
            ):
                raise CaptureV3Error("source_role_projection_mismatch")
            if git_reconstruction is not None and (
                git_reconstruction._token is not _GIT_TOKEN
                or git_reconstruction.locator
                != projection["relative_locator"].rstrip("/") + "/.git"
            ):
                raise CaptureV3Error("git_reconstruction_receipt_locator_mismatch")
            with view.borrow_root(role) as root_fd:
                for entry in _walk_root(
                    role=role,
                    root_fd=root_fd,
                    source_device=mount_device,
                    overlay=protected_overlay,
                    role_policy=role_policy,
                    scope=projection["scope"],
                    git_reconstruction=git_reconstruction,
                    scratch=scratch,
                    target_device=target_device,
                ):
                    entries.add(entry)
        view.revalidate()
        entries.finish()
        entry_hash = hashlib.sha256()
        for entry in entries:
            raw = canonical_json(entry.wire())
            entry_hash.update(len(raw).to_bytes(8, "big"))
            entry_hash.update(raw)
        inventory_sha = hashlib.sha256(
            canonical_json(
                {
                    "domain": "brain-host-path-only-inventory-v3-stream-v1",
                    "plan_sha256": plan.plan_sha256,
                    "witness_sha256": policy["witness_sha256"],
                    "policy_roster_sha256": policy_sha,
                    "protected_overlay_sha256": protected_overlay_sha256,
                    "entry_count": len(entries),
                    "entries_stream_sha256": entry_hash.hexdigest(),
                }
            )
        ).hexdigest()
        dispositions = _role_disposition_rows(entries, policy)
        payload_bytes = int(
            entries.db.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) "
                "FROM entries WHERE kind = 'file' AND disposition = 'include_opaque'"
            ).fetchone()[0]
        )
        return InventorySnapshotV3(entries, inventory_sha, tuple(dispositions), payload_bytes)
    except BaseException:
        entries.close()
        raise


_TEST_HELD_VIEW_CAPABILITY = object()


def inventory_held_view_v3(
    view: HeldDataT,
    **kwargs: Any,
) -> InventorySnapshotV3:
    """Production-only entry: require the public held Data capability, non-synthetic."""
    if not isinstance(view, HeldDataT) or view._closed or view.synthetic:
        raise CaptureV3Error("production_inventory_requires_real_held_view")
    return _inventory_held_view_core_v3(view, **kwargs)


def _inventory_held_view_for_test_v3(
    view: HeldViewV3,
    *,
    _capability: object,
    **kwargs: Any,
) -> InventorySnapshotV3:
    """Private synthetic seam; no production CLI or factory can supply this token."""
    if _capability is not _TEST_HELD_VIEW_CAPABILITY:
        raise CaptureV3Error("synthetic_inventory_capability_required")
    return _inventory_held_view_core_v3(view, **kwargs)


def _role_disposition_rows(
    entries: _SQLiteInventoryEntriesV3, policy: Mapping[str, Any]
) -> list[dict[str, Any]]:
    projections = {row["role"]: row for row in policy["roles"]}
    output: list[dict[str, Any]] = []
    for role in sorted(PRESENT_ROLES):
        counts = {mode: 0 for mode in MEMBER_MODES}
        bytes_by_mode = {mode: 0 for mode in MEMBER_MODES}
        ranges: list[dict[str, Any]] = []
        unknown_descendants = False
        manifest_digest = hashlib.sha256()
        manifest_digest.update(b"[")
        range_digest: hashlib._Hash | None = None
        range_count = 0
        range_first: str | None = None
        range_last: str | None = None
        role_count = 0
        for entry in entries.iter_role(role):
            role_count += 1
            counts[entry.disposition] += 1
            if entry.disposition != "exclude_protected_unresolved":
                bytes_by_mode[entry.disposition] += entry.size_bytes if entry.kind == "file" else 0
            elif entry.kind == "directory":
                unknown_descendants = True
            wire = canonical_json(entry.wire())
            if role_count > 1:
                manifest_digest.update(b",")
            manifest_digest.update(wire)
            if range_digest is None:
                range_digest = hashlib.sha256()
                range_digest.update(b"[")
                range_first = entry.path
            elif range_count:
                range_digest.update(b",")
            range_digest.update(wire)
            range_count += 1
            range_last = entry.path
            if range_count == 512:
                ranges.append(
                    {
                        "first_path": range_first,
                        "last_path": range_last,
                        "count": range_count,
                        "sha256": range_digest.hexdigest(),
                    }
                )
                range_digest = None
                range_count = 0
        if range_digest is not None:
            ranges.append(
                {
                    "first_path": range_first,
                    "last_path": range_last,
                    "count": range_count,
                    "sha256": range_digest.hexdigest(),
                }
            )
        manifest_digest.update(b"]")
        included = counts["include_opaque"]
        excluded = (
            counts["exclude_backup_required"]
            + counts["exclude_reissue"]
            + counts["git_reconstructible_proven"]
        )
        unresolved: int | None = counts["exclude_protected_unresolved"]
        if unknown_descendants:
            unresolved = None
            counts["exclude_protected_unresolved"] = None
            bytes_by_mode["exclude_protected_unresolved"] = None
        output.append(
            {
                "role": role,
                "present": True,
                "role_mode": ROLE_MODES[role],
                "relative_locator": projections[role]["relative_locator"],
                "kind": projections[role]["kind"],
                "scope": projections[role]["scope"],
                "effective_policy_sha256": projections[role]["effective_policy_sha256"],
                "member_manifest_sha256": manifest_digest.hexdigest(),
                "observed_entry_count": role_count,
                "included_entry_count": included,
                "excluded_entry_count": excluded,
                "unresolved_entry_count": unresolved,
                "included_logical_bytes": bytes_by_mode["include_opaque"],
                "excluded_logical_bytes": sum(
                    bytes_by_mode[mode]
                    for mode in (
                        "exclude_backup_required",
                        "exclude_reissue",
                        "git_reconstructible_proven",
                    )
                ),
                "unresolved_logical_bytes": bytes_by_mode["exclude_protected_unresolved"],
                "member_modes": sorted(
                    mode for mode, count in counts.items() if count is None or count != 0
                ),
                "member_mode_counts": counts,
                "member_mode_logical_bytes": bytes_by_mode,
                "member_ranges": ranges,
            }
        )
    absent = next(row for row in policy["roles"] if row["role"] == "verifier_worktree")
    output.append(
        {
            "role": "verifier_worktree",
            "present": False,
            "role_mode": ROLE_MODES["verifier_worktree"],
            "disposition": CAPTURE_STATUS,
            "witness_sha256": absent["witness_sha256"],
            "relative_locator": absent["relative_locator"],
            "expected_fetchable_commit": absent["expected_fetchable_commit"],
            "historical_non_git_state": absent["historical_non_git_state"],
        }
    )
    return output


def stream_member_v3(
    *,
    source_root_fd: int,
    entry: InventoryEntryV3,
    writer: Callable[[str, bytes], None],
    target_relative_path: str,
    progress_check: Callable[[int], None],
    chunk_bytes: int = 1024 * 1024,
) -> dict[str, Any]:
    """Schema-neutral descriptor stream primitive for one regular member."""
    if (
        entry.kind != "file"
        or entry.disposition != "include_opaque"
        or type(chunk_bytes) is not int
        or chunk_bytes < 1
    ):
        raise CaptureV3Error("stream_member_arguments_invalid")
    parent = _open_dir_checked(
        source_root_fd,
        entry.path.rpartition("/")[0],
        entry.parent_identity_chain,
    )
    name = entry.path.rpartition("/")[2]
    fd = -1
    digest = hashlib.sha256()
    length = 0
    pages: list[dict[str, Any]] = []
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(fd)
        if _sig(before) != entry.signature:
            raise CaptureV3Error("source_member_changed_before_stream")
        page = 0
        while chunk := os.pread(fd, chunk_bytes, length):
            progress_check(len(chunk))
            relative = f"{target_relative_path}/{page:08d}.bin"
            writer(relative, chunk)
            digest.update(chunk)
            pages.append({"path": relative, "sha256": sha256(chunk), "size_bytes": len(chunk)})
            length += len(chunk)
            page += 1
        after = os.fstat(fd)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if _sig(after) != entry.signature or _sig(current) != entry.signature:
            raise CaptureV3Error("source_member_changed_during_stream")
        if length != entry.size_bytes:
            raise CaptureV3Error("source_member_size_changed")
    finally:
        if fd >= 0:
            os.close(fd)
        os.close(parent)
    return {"payload_sha256": digest.hexdigest(), "size_bytes": length, "pages": pages}


def _capture_candidate_v3_unreviewed(
    *,
    view: HeldDataT,
    plan: AbsenceGapPlanV2,
    witness: Mapping[str, Any],
    protected_overlay: Mapping[str, Any],
    policy: Mapping[str, Any],
    role_policies: Mapping[str, RolePolicyV3],
    intent: Mapping[str, Any],
    capacity_receipt: Mapping[str, Any],
    capture_spec: Mapping[str, Any],
    target: TargetSessionV3,
    verifier: ReadOnlyCandidateVerifierV3,
    final_verifier_factory: Callable[[], ReadOnlyCandidateVerifierV3],
    scratch: ScratchSessionV3,
    approved_operation: Mapping[str, Any],
    expected_approved_operation_artifact_sha256: str,
    expected_policy_sha256: str,
    expected_protected_overlay_sha256: str,
    expected_intent_sha256: str,
    expected_capacity_receipt_sha256: str,
    expected_capture_spec_sha256: str,
    git_reconstruction: GitReconstructionV3 | None = None,
) -> dict[str, Any]:
    """Create and independently verify one new inert v3 candidate; never resume."""
    inventory: InventorySnapshotV3 | None = None
    fresh_inventory: InventorySnapshotV3 | None = None
    try:
        from tools.brain_host_absence_gap_chain_v3 import validate_policy_roster_v3

        validate_absence_witness_v2(witness)
        overlay_sha = validate_protected_overlay_v3(
            protected_overlay, expected_protected_overlay_sha256
        )
        policy_sha = validate_policy_roster_v3(policy)
        if policy_sha != expected_policy_sha256:
            raise CaptureV3Error("policy_external_pin_mismatch")
        if (
            not isinstance(view, HeldDataT)
            or view._closed
            or view.synthetic
            or view.plan != plan.held_data_plan
            or witness["absence_plan_v2_sha256"] != plan.plan_sha256
            or policy["plan_sha256"] != plan.plan_sha256
            or policy["witness_sha256"] != witness["witness_sha256"]
            or policy["protected_overlay_sha256"] != overlay_sha
            or type(target.target_device) is not int
        ):
            raise CaptureV3Error("capture_source_or_policy_binding_invalid")
        source_volume_uuid = plan.held_data_plan.data_volume_uuid
        target_volume_uuid = target.volume_uuid
        scratch_source_volume_uuid = getattr(scratch, "_source_volume_uuid", source_volume_uuid)
        scratch_target_volume_uuid = getattr(scratch, "_target_volume_uuid", target_volume_uuid)
        if (
            not isinstance(scratch.volume_uuid, str)
            or not _UUID_RE.fullmatch(scratch.volume_uuid)
            or not isinstance(source_volume_uuid, str)
            or not _UUID_RE.fullmatch(source_volume_uuid)
            or not isinstance(target_volume_uuid, str)
            or not _UUID_RE.fullmatch(target_volume_uuid)
            or scratch.volume_uuid.lower()
            in {source_volume_uuid.lower(), target_volume_uuid.lower()}
            or not isinstance(scratch_source_volume_uuid, str)
            or scratch_source_volume_uuid.lower() != source_volume_uuid.lower()
            or not isinstance(scratch_target_volume_uuid, str)
            or scratch_target_volume_uuid.lower() != target_volume_uuid.lower()
        ):
            raise CaptureV3Error("capture_scratch_volume_alias")
        intent_sha = validate_capacity_intent_v2(intent, policy_sha)
        if intent_sha != expected_intent_sha256:
            raise CaptureV3Error("intent_external_pin_mismatch")
        capacity_sha = validate_capacity_receipt_v2(
            capacity_receipt,
            intent_sha256=intent_sha,
            intent=intent,
            policy_sha256=policy_sha,
        )
        if capacity_sha != expected_capacity_receipt_sha256:
            raise CaptureV3Error("capacity_receipt_external_pin_mismatch")
        source_device = os.fstat(view.mount_fd).st_dev
        inventory = inventory_held_view_v3(
            view,
            plan=plan,
            policy=policy,
            role_policies=role_policies,
            protected_overlay=protected_overlay,
            protected_overlay_sha256=overlay_sha,
            scratch=scratch,
            target_device=target.target_device,
            git_reconstruction=git_reconstruction,
        )
        capture_sha = validate_capture_spec_v2(
            capture_spec,
            plan_sha256=plan.plan_sha256,
            witness_sha256=witness["witness_sha256"],
            policy_sha256=policy_sha,
            intent_sha256=intent_sha,
            capacity_sha256=capacity_sha,
            inventory_sha256=inventory.inventory_sha256,
            capacity_receipt=capacity_receipt,
        )
        if capture_sha != expected_capture_spec_sha256:
            raise CaptureV3Error("capture_spec_external_pin_mismatch")
        approved = validate_approved_operation_v3(
            approved_operation,
            expected_sha256=expected_approved_operation_artifact_sha256,
            capacity_receipt=capacity_receipt,
            capacity_receipt_sha256=capacity_sha,
            capture_spec=capture_spec,
            capture_spec_sha256=capture_sha,
            source_data_plan=plan.held_data_plan,
        )
        if approved._token is not _APPROVED_OPERATION_TOKEN:
            raise CaptureV3Error("approved_operation_validation_token_required")
        approved_operation_sha = approved.operation_sha256
        observed = datetime.now(UTC)
        measured = datetime.fromisoformat(
            capacity_receipt["kingston_measurement_observed_at_utc"].replace("Z", "+00:00")
        )
        if (observed - measured).total_seconds() < 0 or (
            observed - measured
        ).total_seconds() > capture_spec["kingston_measurement_max_age_seconds"]:
            raise CaptureV3Error("target_measurement_stale_at_capture")
        required_free = capacity_receipt["required_new_free_bytes"]
        if not isinstance(verifier, ReadOnlyCandidateVerifierV3):
            raise CaptureV3Error("target_or_verifier_binding_invalid")

        def validate_measurement(
            measurement: TargetMeasurementV3, remaining_reserve: int
        ) -> TargetMeasurementV3:
            if not isinstance(measurement, TargetMeasurementV3):
                raise CaptureV3Error("typed_target_measurement_required")
            recomputed = TargetMeasurementV3.stamp(
                volume_uuid=measurement.target_volume_uuid,
                root_id=measurement.target_root_id,
                container_reference=measurement.target_container_reference,
                device_identifier=measurement.target_device_identifier,
                available_bytes=measurement.available_bytes,
                observed_at_utc=measurement.observed_at_utc,
                monotonic_start_ns=measurement.monotonic_start_ns,
                monotonic_end_ns=measurement.monotonic_end_ns,
            )
            measured_at = datetime.fromisoformat(measurement.observed_at_utc.replace("Z", "+00:00"))
            now = datetime.now(UTC)
            age = (now - measured_at).total_seconds()
            if (
                recomputed.measurement_sha256 != measurement.measurement_sha256
                or measurement.target_volume_uuid.lower()
                != capacity_receipt["kingston_volume_uuid"].lower()
                or measurement.target_root_id != capacity_receipt["kingston_root_id"]
                or measurement.target_volume_uuid.lower() != intent["target_volume_uuid"].lower()
                or measurement.target_root_id != intent["target_root_id"]
                or measurement.target_volume_uuid != approved.m0.target_volume_uuid
                or measurement.target_root_id != approved.m0.target_root_id
                or measurement.target_container_reference != approved.m0.target_container_reference
                or measurement.target_device_identifier != approved.m0.target_device_identifier
                or measurement.target_volume_uuid.lower()
                == plan.held_data_plan.data_volume_uuid.lower()
                or measurement.target_container_reference
                == plan.held_data_plan.data_container_reference
                or measurement.target_device_identifier
                == plan.held_data_plan.data_device_identifier
                or type(measurement.available_bytes) is not int
                or measurement.available_bytes < max(0, remaining_reserve)
                or measurement.monotonic_end_ns - measurement.monotonic_start_ns > 30_000_000_000
                or age < 0
                or age > capture_spec["kingston_measurement_max_age_seconds"]
            ):
                raise CaptureV3Error("target_live_measurement_invalid_or_stale")
            return measurement

        def revalidate_target(remaining_reserve: int) -> TargetMeasurementV3:
            measurement = target.revalidate(max(0, remaining_reserve))
            return validate_measurement(measurement, remaining_reserve)

        # M0 is the externally pinned post-PB historical baseline. M1 is a
        # fresh physical observation; its free bytes may be lower by accrued
        # writes, so only remaining reserves are enforced after each boundary.
        m1 = revalidate_target(required_free)
        fresh_witness = build_absence_witness_v2(view, plan=plan)
        fresh_inventory = inventory_held_view_v3(
            view,
            plan=plan,
            policy=policy,
            role_policies=role_policies,
            protected_overlay=protected_overlay,
            protected_overlay_sha256=overlay_sha,
            scratch=scratch,
            target_device=target.target_device,
            git_reconstruction=git_reconstruction,
        )
        if (
            fresh_witness["witness_sha256"] != witness["witness_sha256"]
            or fresh_inventory.inventory_sha256 != inventory.inventory_sha256
        ):
            raise CaptureV3Error("source_witness_or_inventory_changed_before_write")
        revalidate_target(required_free)
        candidate_id = target.begin_new_candidate(required_free)
        if not isinstance(candidate_id, str) or not _CANDIDATE_ID_RE.fullmatch(candidate_id):
            raise CaptureV3Error("candidate_id_invalid")
        roots: dict[str, int] = {}
        target_reserve = _TargetReserveV3(required_free)

        def write_member(path: str, payload: bytes) -> None:
            revalidate_target(target_reserve.remaining_bytes())
            target.prepare_member_parent(candidate_id, path, target_reserve.remaining_bytes())
            target.create_member(candidate_id, path, payload)
            allocated = target.member_allocated_bytes(candidate_id, path)
            target_reserve.credit_member(allocated)
            # Recheck the shared-pool free floor after every durable create.
            # Credit comes only from reopened member st_blocks; unavailable
            # allocation accounting leaves the full ceiling in force.
            revalidate_target(target_reserve.remaining_bytes())

        def progress_check(chunk_size: int) -> None:
            if type(chunk_size) is not int or chunk_size < 0:
                raise CaptureV3Error("stream_progress_size_invalid")
            revalidate_target(target_reserve.remaining_bytes())

        try:
            for role in sorted(PRESENT_ROLES):
                roots[role] = os.dup(view.root_fds[role])
            for entry in inventory.entries:
                revalidate_target(target_reserve.remaining_bytes())
                rel = _member_path(entry.role, entry.path)
                if entry.disposition == "include_opaque" and entry.kind == "file":
                    streamed = stream_member_v3(
                        source_root_fd=roots[entry.role],
                        entry=entry,
                        writer=write_member,
                        target_relative_path=f"payload/{entry.role}/{entry.path or '_root'}",
                        progress_check=progress_check,
                    )
                    body = {
                        **entry.wire(),
                        "target_path": rel,
                        "payload": streamed,
                    }
                elif entry.disposition == "include_opaque" and entry.kind == "symlink":
                    body = {
                        **entry.wire(),
                        "target_path": rel,
                        "symlink_target_hex": (entry.symlink_target or b"").hex(),
                    }
                else:
                    body = {**entry.wire(), "target_path": None, "payload": None}
                member_raw = canonical_json(body)
                member_rel = (
                    f"manifest/{entry.role}/{hashlib.sha256(entry.path.encode()).hexdigest()}.json"
                )
                write_member(member_rel, member_raw)
                if target.read_member(candidate_id, member_rel) != member_raw:
                    raise CaptureV3Error("member_readback_mismatch")
                inventory.entries.add_candidate_member(
                    {
                        "role": entry.role,
                        "path": entry.path,
                        "target_path": rel,
                        "sha256": sha256(member_raw),
                        "size_bytes": len(member_raw),
                    }
                )
            if (
                build_absence_witness_v2(view, plan=plan)["witness_sha256"]
                != witness["witness_sha256"]
            ):
                raise CaptureV3Error("source_witness_changed_after_stream")
            revalidate_target(target_reserve.remaining_bytes())
            index = {
                "schema": INDEX_V3_SCHEMA,
                "candidate_status": CAPTURE_STATUS,
                "plan_sha256": plan.plan_sha256,
                "witness_sha256": witness["witness_sha256"],
                "policy_roster_sha256": policy_sha,
                "intent_sha256": intent_sha,
                "capacity_receipt_sha256": capacity_sha,
                "capture_spec_sha256": capture_sha,
                "inventory_sha256": inventory.inventory_sha256,
                "protected_overlay_sha256": overlay_sha,
                "candidate_payload_manifest_sha256": inventory.entries.candidate_members_sha256(),
                "approved_operation_sha256": approved_operation_sha,
                "approved_operation_artifact_sha256": approved.approval_sha256,
                "role_dispositions": list(inventory.role_dispositions),
                "personal_backup_transition_confirmed": True,
                "kingston_measurement_sha256": capacity_receipt["kingston_measurement_sha256"],
                "kingston_measurement_observed_at_utc": capacity_receipt[
                    "kingston_measurement_observed_at_utc"
                ],
                "kingston_volume_uuid": capacity_receipt["kingston_volume_uuid"],
                "kingston_root_id": capacity_receipt["kingston_root_id"],
                "kingston_m1_measurement_sha256": m1.measurement_sha256,
                "kingston_m1_observed_at_utc": m1.observed_at_utc,
                "kingston_m1_volume_uuid": m1.target_volume_uuid,
                "kingston_m1_root_id": m1.target_root_id,
                "kingston_m1_container_reference": m1.target_container_reference,
                "kingston_m1_device_identifier": m1.target_device_identifier,
                "kingston_m1_available_bytes": m1.available_bytes,
                "kingston_m1_monotonic_start_ns": m1.monotonic_start_ns,
                "kingston_m1_monotonic_end_ns": m1.monotonic_end_ns,
                **INDEX_FLAGS,
            }
            index["candidate_index_sha256"] = hashlib.sha256(canonical_json(index)).hexdigest()
            validate_candidate_index_v3(
                index,
                plan=plan,
                witness=witness,
                policy=policy,
                intent_sha256=intent_sha,
                capacity_sha256=capacity_sha,
                capture_spec_sha256=capture_sha,
                inventory_sha256=inventory.inventory_sha256,
                capacity_receipt=capacity_receipt,
                approved_operation_sha256=approved_operation_sha,
                approved_operation_artifact_sha256=approved.approval_sha256,
            )
            proposed_index_sha = index["candidate_index_sha256"]
            independent = verifier.verify_prepublication(
                candidate_id=candidate_id,
                expected_plan_sha256=plan.plan_sha256,
                expected_policy_sha256=policy_sha,
                expected_overlay_sha256=overlay_sha,
                expected_capture_spec_sha256=capture_sha,
                expected_approved_operation_sha256=approved_operation_sha,
                expected_approved_operation_artifact_sha256=approved.approval_sha256,
                expected_candidate_payload_manifest_sha256=index[
                    "candidate_payload_manifest_sha256"
                ],
                expected_candidate_index_sha256=proposed_index_sha,
                candidate_index=index,
                policy=policy,
                protected_overlay=protected_overlay,
                role_policies=role_policies,
                git_reconstruction=git_reconstruction,
                expected_role_dispositions=inventory.role_dispositions,
            )
            if (
                independent.get("verified") is not True
                or independent.get("candidate_index_sha256") != proposed_index_sha
                or independent.get("members_sha256") != index["candidate_payload_manifest_sha256"]
            ):
                raise CaptureV3Error("independent_candidate_verification_failed")
            # INDEX is the only visibility operation and the last write.
            final_independent = _publish_then_verify_index_v3(
                publish_index=lambda: write_member("INDEX.json", canonical_json(index)),
                final_verifier_factory=final_verifier_factory,
                prepublication_verifier=verifier,
                source_device=source_device,
                candidate_id=candidate_id,
                expected_plan_sha256=plan.plan_sha256,
                expected_policy_sha256=policy_sha,
                expected_overlay_sha256=overlay_sha,
                expected_capture_spec_sha256=capture_sha,
                expected_approved_operation_sha256=approved_operation_sha,
                expected_approved_operation_artifact_sha256=approved.approval_sha256,
                expected_candidate_index_sha256=proposed_index_sha,
                expected_candidate_payload_manifest_sha256=index[
                    "candidate_payload_manifest_sha256"
                ],
                policy=policy,
                protected_overlay=protected_overlay,
                role_policies=role_policies,
                git_reconstruction=git_reconstruction,
            )
            return {
                "candidate_id": candidate_id,
                "candidate_status": CAPTURE_STATUS,
                "candidate_index_sha256": index["candidate_index_sha256"],
                "independent_verification": dict(final_independent),
                **INDEX_FLAGS,
            }
        finally:
            for fd in roots.values():
                os.close(fd)
    except CaptureV3Error:
        raise
    except (AbsenceGapChainError, OSError, ValueError, KeyError, TypeError) as exc:
        raise CaptureV3Error("capture_v3_validation_or_io_failed") from exc
    finally:
        if fresh_inventory is not None:
            fresh_inventory.close()
        if inventory is not None:
            inventory.close()


def capture_candidate_v3(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    """Unavailable until the v3 adapter and independent verifier pass Sol review."""
    raise CaptureV3Error("capture_v3_unavailable_until_review")
