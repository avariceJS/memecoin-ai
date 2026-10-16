#!/usr/bin/env python3
"""Small descriptor-only create-once writer for Brain DR capture groups.

The writer composes an existing locked ``StorageGuard`` with held directory
descriptors. It never uses the guard's returned Path for I/O. Each object is a
single sequential stream, is fsynced and freshly read back before it is accepted.
Partial output stays under its private nonce directory. COMPLETE is published
last as a candidate seal; a later independent verification failure can leave an
unaccepted COMPLETE candidate that must not be treated as authority.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from memecoin_research_storage import _open_relative_directory

MAX_CHUNK_BYTES = 1024 * 1024
CAPTURE_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
FILE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
CAPTURE_PARENT = "data-view-captures"


class CaptureWriterError(ValueError):
    """A DR capture write failed closed or could not be independently read back."""


def _file_identity(st: os.stat_result) -> tuple[int, ...]:
    return (
        st.st_dev,
        st.st_ino,
        st.st_mode,
        st.st_uid,
        st.st_gid,
        st.st_nlink,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
    )


def _stable_file_identity(st: os.stat_result) -> tuple[int, ...]:
    return (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_gid, st.st_nlink)


def _stable_file_identity_from_tuple(value: tuple[int, ...]) -> tuple[int, ...]:
    return (value[0], value[1], value[2], value[3], value[4], value[5])


def _write_all(
    fd: int,
    data: memoryview,
    *,
    before_write: Callable[[], None] | None = None,
) -> None:
    offset = 0
    while offset < len(data):
        if before_write is not None:
            before_write()
        try:
            written = os.write(fd, data[offset:])
        except InterruptedError:
            continue
        except OSError as exc:
            raise CaptureWriterError("destination_write_failed") from exc
        if written <= 0:
            raise CaptureWriterError("destination_short_write")
        offset += written


def _fsync(fd: int) -> None:
    try:
        os.fsync(fd)
    except OSError as exc:
        raise CaptureWriterError("destination_fsync_failed") from exc


@dataclass(frozen=True)
class VerifiedObject:
    name: str
    size_bytes: int
    sha256: str
    device: int
    inode: int


class HeldKingstonCapture:
    """Create-only byte-group directory on a previously pinned Kingston binding.

    The marker is a candidate seal only; callers must independently verify its
    bound manifest and every group member before treating the group as accepted.
    """

    def __init__(
        self,
        guard: Any,
        capture_id: str,
        *,
        source_volume_uuid: str,
        source_device: int,
        min_free_bytes: int = 50 * 1024**3,
    ) -> None:
        if not CAPTURE_ID_RE.fullmatch(capture_id):
            raise CaptureWriterError("capture_id_invalid")
        if not isinstance(source_volume_uuid, str) or not source_volume_uuid:
            raise CaptureWriterError("source_volume_uuid_missing")
        if source_volume_uuid.lower() == str(getattr(guard, "volume_uuid", "")).lower():
            raise CaptureWriterError("source_and_destination_volume_same")
        if type(source_device) is not int or source_device < 0:
            raise CaptureWriterError("source_device_invalid")
        if getattr(guard, "bootstrap", True):
            raise CaptureWriterError("bound_storage_writer_guard_required")
        if type(min_free_bytes) is not int or min_free_bytes < 0:
            raise CaptureWriterError("minimum_free_bytes_invalid")
        self.guard = guard
        self.capture_id = capture_id
        self.source_volume_uuid = source_volume_uuid.lower()
        self.source_device = source_device
        self.min_free_bytes = min_free_bytes
        self._closed = False
        self._complete = False
        self._manifest_finalized = False
        self._active: CaptureObject | None = None
        self._verified: dict[str, VerifiedObject] = {}
        self.root_fd = self.recovery_fd = self.captures_fd = self.capture_fd = -1

        try:
            self.guard.revalidate(min_free_bytes=min_free_bytes)
            self.root_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)
            self.root_identity = self._directory_identity(self.root_fd)
            if (
                self.root_identity[0] != guard.root.mount_device
                or self.root_identity[0] == source_device
            ):
                raise CaptureWriterError("kingston_root_device_mismatch")
            self.recovery_fd = self._open_or_create_directory(self.root_fd, "host-recovery")
            self.recovery_identity = self._directory_identity(self.recovery_fd)
            self.captures_fd = self._open_or_create_directory(self.recovery_fd, CAPTURE_PARENT)
            self.captures_identity = self._directory_identity(self.captures_fd)
            self.guard.revalidate(min_free_bytes=min_free_bytes)
            self._assert_root()
            try:
                os.mkdir(capture_id, mode=0o700, dir_fd=self.captures_fd)
            except FileExistsError as exc:
                raise CaptureWriterError("capture_directory_collision") from exc
            except OSError as exc:
                raise CaptureWriterError("capture_directory_reservation_failed") from exc
            _fsync(self.captures_fd)
            self.capture_fd = os.open(
                capture_id,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=self.captures_fd,
            )
            self.capture_identity = self._directory_identity(self.capture_fd)
            if self.capture_identity[0] != self.root_identity[0]:
                raise CaptureWriterError("capture_directory_cross_device")
            if self.capture_identity[2] != os.getuid() or self.capture_identity[3] != 0o700:
                raise CaptureWriterError("capture_directory_permissions_unsafe")
            _fsync(self.capture_fd)
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _directory_identity(fd: int) -> tuple[int, int, int, int]:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode):
            raise CaptureWriterError("destination_not_directory")
        return (st.st_dev, st.st_ino, st.st_uid, stat.S_IMODE(st.st_mode))

    def _open_or_create_directory(self, parent_fd: int, name: str) -> int:
        self._assert_root()
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        else:
            _fsync(parent_fd)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        except OSError as exc:
            raise CaptureWriterError("destination_directory_open_failed") from exc
        identity = self._directory_identity(fd)
        if (
            identity[0] != self.root_identity[0]
            or identity[2] != os.getuid()
            or identity[3] != 0o700
        ):
            os.close(fd)
            raise CaptureWriterError("destination_directory_permissions_unsafe")
        self._assert_root()
        return fd

    def _assert_root(self) -> None:
        if self._closed:
            raise CaptureWriterError("capture_writer_closed")
        if self._directory_identity(self.root_fd) != self.root_identity:
            raise CaptureWriterError("kingston_root_descriptor_changed")
        self.guard.revalidate_identity(min_free_bytes=self.min_free_bytes)
        current_root = _open_relative_directory(self.guard.mount_fd, self.guard.config.data_root)
        try:
            if self._directory_identity(current_root) != self.root_identity:
                raise CaptureWriterError("kingston_root_path_replaced")
        finally:
            os.close(current_root)

    def _assert_capture(self) -> None:
        self._assert_root()
        if self._directory_identity(self.capture_fd) != self.capture_identity:
            raise CaptureWriterError("capture_directory_descriptor_changed")
        current_recovery = os.open(
            "host-recovery", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.root_fd
        )
        try:
            if self._directory_identity(current_recovery) != self.recovery_identity:
                raise CaptureWriterError("capture_parent_path_replaced")
            current_captures = os.open(
                CAPTURE_PARENT,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current_recovery,
            )
            try:
                if self._directory_identity(current_captures) != self.captures_identity:
                    raise CaptureWriterError("capture_parent_path_replaced")
                current_capture = os.open(
                    self.capture_id,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current_captures,
                )
                try:
                    if self._directory_identity(current_capture) != self.capture_identity:
                        raise CaptureWriterError("capture_directory_path_replaced")
                finally:
                    os.close(current_capture)
            finally:
                os.close(current_captures)
        finally:
            os.close(current_recovery)

    def create_object(self, name: str) -> CaptureObject:
        if self._complete:
            raise CaptureWriterError("capture_already_complete")
        if self._manifest_finalized:
            raise CaptureWriterError("manifest_must_be_last_object")
        if not FILE_NAME_RE.fullmatch(name) or name == "COMPLETE":
            raise CaptureWriterError("object_name_invalid")
        if self._active is not None:
            raise CaptureWriterError("another_object_is_open")
        if name in self._verified:
            raise CaptureWriterError("object_name_collision")
        self.guard.revalidate(min_free_bytes=self.min_free_bytes)
        self._assert_capture()
        try:
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=self.capture_fd,
            )
        except FileExistsError as exc:
            raise CaptureWriterError("object_name_collision") from exc
        except OSError as exc:
            raise CaptureWriterError("object_create_failed") from exc
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_dev != self.root_identity[0]
            or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o600
            or st.st_nlink != 1
        ):
            os.close(fd)
            raise CaptureWriterError("object_identity_unsafe")
        sink = CaptureObject(self, name, fd, _file_identity(st))
        self._active = sink
        return sink

    def publish_complete(self, payload: bytes) -> VerifiedObject:
        """Publish a candidate group seal after object readback, last in the group."""
        if self._complete:
            raise CaptureWriterError("complete_marker_already_published")
        if self._active is not None:
            raise CaptureWriterError("object_still_open")
        if "manifest.json" not in self._verified:
            raise CaptureWriterError("manifest_must_be_verified_before_complete")
        if not isinstance(payload, bytes) or not payload or len(payload) > MAX_CHUNK_BYTES:
            raise CaptureWriterError("complete_payload_invalid")
        self.guard.revalidate(min_free_bytes=self.min_free_bytes)
        self._assert_capture()
        expected_names = set(self._verified)
        try:
            actual_names = set(os.listdir(self.capture_fd))
        except OSError as exc:
            raise CaptureWriterError("capture_membership_read_failed") from exc
        if actual_names != expected_names:
            raise CaptureWriterError("capture_membership_not_closed")
        for row in tuple(self._verified.values()):
            self._readback(row.name, row.size_bytes, row.sha256)
        try:
            fd = os.open(
                "COMPLETE",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=self.capture_fd,
            )
        except FileExistsError as exc:
            raise CaptureWriterError("complete_marker_collision") from exc
        digest = hashlib.sha256(payload).hexdigest()
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_dev != self.root_identity[0]:
                raise CaptureWriterError("complete_marker_identity_unsafe")
            _write_all(fd, memoryview(payload), before_write=self._assert_capture)
            _fsync(fd)
        finally:
            os.close(fd)
        _fsync(self.capture_fd)
        self._assert_capture()
        verified = self._readback("COMPLETE", len(payload), digest)
        self.guard.revalidate(min_free_bytes=self.min_free_bytes)
        self._complete = True
        self._verified["COMPLETE"] = verified
        return verified

    def _readback(self, name: str, expected_size: int, expected_sha256: str) -> VerifiedObject:
        if type(expected_size) is not int or expected_size < 0:
            raise CaptureWriterError("expected_size_invalid")
        if not SHA256_RE.fullmatch(expected_sha256):
            raise CaptureWriterError("expected_sha256_invalid")
        self._assert_capture()
        try:
            fd = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=self.capture_fd,
            )
        except OSError as exc:
            raise CaptureWriterError("object_readback_open_failed") from exc
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_dev != self.root_identity[0]
                or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1
            ):
                raise CaptureWriterError("object_readback_identity_mismatch")
            digest = hashlib.sha256()
            size = 0
            while True:
                try:
                    chunk = os.read(fd, MAX_CHUNK_BYTES)
                except InterruptedError:
                    continue
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
            after = os.fstat(fd)
            if _file_identity(before) != _file_identity(after):
                raise CaptureWriterError("object_changed_during_readback")
            actual_hash = digest.hexdigest()
            if size != expected_size or actual_hash != expected_sha256:
                raise CaptureWriterError("object_readback_mismatch")
            return VerifiedObject(name, size, actual_hash, after.st_dev, after.st_ino)
        finally:
            os.close(fd)

    def read_verified_object(self, name: str, *, max_bytes: int) -> bytes:
        """Read a previously verified small control object through a fresh FD."""
        if type(max_bytes) is not int or max_bytes < 0:
            raise CaptureWriterError("readback_limit_invalid")
        row = self._verified.get(name)
        if row is None or row.size_bytes > max_bytes:
            raise CaptureWriterError("control_object_not_verified_or_too_large")
        self._assert_capture()
        try:
            fd = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=self.capture_fd,
            )
        except OSError as exc:
            raise CaptureWriterError("object_readback_open_failed") from exc
        try:
            chunks: list[bytes] = []
            size = 0
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_dev != self.root_identity[0]
                or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1
                or before.st_ino != row.inode
            ):
                raise CaptureWriterError("control_object_identity_mismatch")
            while True:
                try:
                    chunk = os.read(fd, min(MAX_CHUNK_BYTES, max_bytes - size + 1))
                except InterruptedError:
                    continue
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise CaptureWriterError("control_object_too_large")
                chunks.append(chunk)
            payload = b"".join(chunks)
            after = os.fstat(fd)
            if _file_identity(before) != _file_identity(after):
                raise CaptureWriterError("object_changed_during_readback")
            if (
                size != row.size_bytes
                or hashlib.sha256(payload).hexdigest() != row.sha256
                or after.st_dev != row.device
            ):
                raise CaptureWriterError("object_readback_mismatch")
            return payload
        finally:
            os.close(fd)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        active = getattr(self, "_active", None)
        if active is not None:
            active._close_unfinished()
            self._active = None
        for name in ("capture_fd", "captures_fd", "recovery_fd", "root_fd"):
            fd = getattr(self, name, -1)
            if fd >= 0:
                os.close(fd)
                setattr(self, name, -1)

    def __enter__(self) -> HeldKingstonCapture:
        if self._closed:
            raise CaptureWriterError("capture_writer_closed")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class CaptureObject:
    """One create-only sequential object stream; never reopenable or seekable."""

    def __init__(
        self,
        parent: HeldKingstonCapture,
        name: str,
        fd: int,
        initial_identity: tuple[int, ...],
    ) -> None:
        self.parent = parent
        self.name = name
        self.fd = fd
        self.initial_identity = initial_identity
        self.size_bytes = 0
        self.digest = hashlib.sha256()
        self.closed = False
        self.verified: VerifiedObject | None = None

    def write(self, chunk: bytes) -> None:
        if self.closed or self.verified is not None:
            raise CaptureWriterError("object_stream_not_writable")
        if not isinstance(chunk, bytes) or not chunk or len(chunk) > MAX_CHUNK_BYTES:
            raise CaptureWriterError("object_chunk_invalid")
        self.parent._assert_capture()
        before = os.fstat(self.fd)
        if (
            _stable_file_identity(before) != _stable_file_identity_from_tuple(self.initial_identity)
            or before.st_size != self.size_bytes
        ):
            raise CaptureWriterError("object_descriptor_changed_before_write")
        self.parent.guard.revalidate_identity(min_free_bytes=self.parent.min_free_bytes)
        _write_all(
            self.fd,
            memoryview(chunk),
            before_write=lambda: self.parent.guard.revalidate_identity(
                min_free_bytes=self.parent.min_free_bytes
            ),
        )
        self.size_bytes += len(chunk)
        self.digest.update(chunk)
        # A regular file's mtime/ctime change on write; compare its stable
        # descriptor identity and exact byte count after each bounded chunk.
        current = os.fstat(self.fd)
        if (
            _stable_file_identity(current)
            != _stable_file_identity_from_tuple(self.initial_identity)
            or current.st_size != self.size_bytes
        ):
            raise CaptureWriterError("object_descriptor_changed_during_write")

    def finalize(self, *, expected_size: int, expected_sha256: str) -> VerifiedObject:
        if self.closed or self.verified is not None:
            raise CaptureWriterError("object_already_finalized")
        if self.size_bytes != expected_size or self.digest.hexdigest() != expected_sha256:
            raise CaptureWriterError("source_stream_digest_mismatch")
        self.parent._assert_capture()
        current = os.fstat(self.fd)
        if (
            current.st_dev != self.initial_identity[0]
            or current.st_ino != self.initial_identity[1]
            or current.st_uid != os.getuid()
            or current.st_nlink != 1
            or current.st_size != self.size_bytes
        ):
            raise CaptureWriterError("object_descriptor_changed_before_finalize")
        os.fchmod(self.fd, 0o600)
        _fsync(self.fd)
        _fsync(self.parent.capture_fd)
        final_before_close = _file_identity(os.fstat(self.fd))
        os.close(self.fd)
        self.closed = True
        self.fd = -1
        self.parent._active = None
        self.parent._assert_capture()
        verified = self.parent._readback(self.name, self.size_bytes, expected_sha256)
        if (verified.device, verified.inode) != (
            final_before_close[0],
            final_before_close[1],
        ):
            raise CaptureWriterError("object_readback_inode_changed")
        self.parent.guard.revalidate(min_free_bytes=self.parent.min_free_bytes)
        self.verified = verified
        self.parent._verified[self.name] = verified
        if self.name == "manifest.json":
            self.parent._manifest_finalized = True
        return verified

    def _close_unfinished(self) -> None:
        if not self.closed:
            os.close(self.fd)
            self.closed = True
            self.fd = -1

    def __enter__(self) -> CaptureObject:
        if self.closed:
            raise CaptureWriterError("object_stream_closed")
        return self

    def __exit__(self, *_: object) -> None:
        if self.verified is None:
            self._close_unfinished()
            self.parent._active = None
