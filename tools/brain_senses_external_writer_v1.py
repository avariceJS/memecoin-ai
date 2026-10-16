#!/usr/bin/env python3
"""Descriptor-bound writer for the separately versioned external Senses lease.

This module never accepts an absolute data path. All paths are validated as
relative components and every traversal and mutation is rooted at a held fd.
The frozen service-v1 and campaign writers are not used for external writes.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import json
import os
import secrets
import stat
import subprocess
from collections.abc import Callable, Iterable, Iterator
from pathlib import PurePosixPath
from typing import Any

from memecoin_research_storage import (
    ReadOnlyStorageGuard,
    StorageError,
    StorageGuard,
    _open_relative_directory,
)
from tools.brain_senses_external_schema_v1 import RECEIPT_SCHEMA

SCHEMA_VERSION = 1


def _parts(relative: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(relative, str) or "\x00" in relative or "\\" in relative:
        raise StorageError("external_writer_invalid_relative_path")
    if relative == "" and allow_empty:
        return ()
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(p in {"", ".", ".."} for p in path.parts):
        raise StorageError("external_writer_invalid_relative_path")
    if str(path) != relative:
        raise StorageError("external_writer_noncanonical_relative_path")
    return path.parts


def _private_dir(st: os.stat_result, *, device: int) -> None:
    if (
        not stat.S_ISDIR(st.st_mode)
        or st.st_uid != os.getuid()
        or stat.S_IMODE(st.st_mode) != 0o700
        or st.st_dev != device
    ):
        raise StorageError("external_writer_unsafe_directory")


def _private_file(st: os.stat_result, *, device: int) -> None:
    if (
        not stat.S_ISREG(st.st_mode)
        or st.st_uid != os.getuid()
        or stat.S_IMODE(st.st_mode) != 0o600
        or st.st_nlink != 1
        or st.st_dev != device
    ):
        raise StorageError("external_writer_unsafe_file")


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise OSError("short write")
        view = view[count:]


class DescriptorWriter:
    """A single-root storage capability; callers retain and close its root fd."""

    def __init__(
        self,
        root_fd: int,
        *,
        before_write: Callable[[], None] | None = None,
        fault_hook: Callable[[str], None] | None = None,
        storage_guard: Any | None = None,
        read_only: bool = False,
    ) -> None:
        if isinstance(storage_guard, ReadOnlyStorageGuard) and not read_only:
            raise StorageError("read_only_guard_requires_reader_factory")
        if read_only and not isinstance(storage_guard, ReadOnlyStorageGuard):
            raise StorageError("read_only_reader_requires_storage_guard")
        self.root_fd = os.dup(root_fd)
        self._root_stat = os.fstat(self.root_fd)
        if not stat.S_ISDIR(self._root_stat.st_mode):
            os.close(self.root_fd)
            raise StorageError("external_writer_root_not_directory")
        if self._root_stat.st_uid != os.getuid() or stat.S_IMODE(self._root_stat.st_mode) != 0o700:
            os.close(self.root_fd)
            raise StorageError("external_writer_unsafe_root")
        self.device = self._root_stat.st_dev
        self._before_write = before_write
        self._fault_hook = fault_hook
        self.storage_guard = storage_guard
        self._read_only = read_only
        self.storage_volume_uuid = (
            str(storage_guard.volume_uuid).lower() if storage_guard is not None else None
        )
        self.storage_root_id = str(storage_guard.root_id) if storage_guard is not None else None
        self._closed = False

    @classmethod
    def from_storage_guard(cls, guard: Any) -> DescriptorWriter:
        """Create a writer bound to a previously initialized storage guard."""
        if not isinstance(guard, StorageGuard) or getattr(guard, "read_only", True):
            raise StorageError("bound_writer_requires_initialized_storage_guard")
        guard.revalidate()
        root_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)
        try:

            def before_write() -> None:
                guard.revalidate_identity()

            writer = cls(
                root_fd,
                before_write=before_write,
                storage_guard=guard,
            )
            writer.assert_storage_binding(guard)
            return writer
        finally:
            os.close(root_fd)

    @classmethod
    def read_only_from_storage_guard(cls, guard: ReadOnlyStorageGuard) -> DescriptorWriter:
        """Create a verification reader whose complete mutation API is disabled."""
        if not isinstance(guard, ReadOnlyStorageGuard):
            raise StorageError("bound_reader_requires_read_only_storage_guard")
        guard.revalidate()
        root_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)
        try:
            reader = cls(
                root_fd,
                before_write=guard.forbid_write,
                storage_guard=guard,
                read_only=True,
            )
            reader.assert_storage_binding(guard)
            return reader
        finally:
            os.close(root_fd)

    def assert_storage_binding(self, guard: Any) -> None:
        """Revalidate the exact Kingston binding and BrainData root descriptor."""
        bound_guard = self.storage_guard
        if bound_guard is None:
            raise StorageError("bound_writer_storage_guard_mismatch")
        if (
            bound_guard is not guard
            or self.storage_volume_uuid != str(guard.volume_uuid).lower()
            or self.storage_root_id != str(guard.root_id)
            or getattr(guard, "bootstrap", True)
        ):
            raise StorageError("bound_writer_storage_guard_mismatch")
        guard.revalidate()
        current_root_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)
        try:
            current = os.fstat(current_root_fd)
            held = os.fstat(self.root_fd)
            if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
                raise StorageError("bound_writer_root_identity_changed")
        finally:
            os.close(current_root_fd)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            os.close(self.root_fd)

    def __enter__(self) -> DescriptorWriter:
        if self._closed:
            raise StorageError("external_writer_closed")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _assert_root(self) -> None:
        if self._closed:
            raise StorageError("external_writer_closed")
        current = os.fstat(self.root_fd)
        if (current.st_dev, current.st_ino) != (self._root_stat.st_dev, self._root_stat.st_ino):
            raise StorageError("external_writer_root_replaced")

    def _prewrite(self, operation: str) -> None:
        self._assert_root()
        self._assert_writable()
        if self._before_write is not None:
            self._before_write()
        if self._fault_hook is not None:
            self._fault_hook(operation)
        # A hook can model a mount transition; descriptor identity remains the
        # authority for the operation that follows.
        self._assert_root()

    def _assert_writable(self) -> None:
        if self._read_only:
            raise StorageError("read_only_storage_guard_write_forbidden")

    def _directory(self, relative: str, *, create: bool = False) -> int:
        if create:
            self._assert_writable()
        parts = _parts(relative, allow_empty=True)
        current = os.dup(self.root_fd)
        try:
            for component in parts:
                if create:
                    try:
                        self._prewrite("mkdir")
                        os.mkdir(component, mode=0o700, dir_fd=current)
                        os.fsync(current)
                    except FileExistsError:
                        pass
                child = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current,
                )
                _private_dir(os.fstat(child), device=self.device)
                os.close(current)
                current = child
            return current
        except OSError as exc:
            os.close(current)
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise StorageError("external_writer_symlink_rejected") from exc
            raise
        except BaseException:
            os.close(current)
            raise

    def open_directory(self, relative: str, *, create: bool = False) -> int:
        """Open a private BrainData descendant by descriptor-relative traversal."""
        return self._directory(relative, create=create)

    @staticmethod
    def _parent_and_name(relative: str) -> tuple[str, str]:
        parts = _parts(relative)
        return "/".join(parts[:-1]), parts[-1]

    def mkdir(self, relative: str) -> None:
        self._assert_writable()
        parts = _parts(relative)
        current = os.dup(self.root_fd)
        try:
            for component in parts:
                try:
                    self._prewrite("mkdir")
                    os.mkdir(component, mode=0o700, dir_fd=current)
                    os.fsync(current)
                except FileExistsError:
                    pass
                child = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current,
                )
                _private_dir(os.fstat(child), device=self.device)
                os.close(current)
                current = child
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise StorageError("external_writer_symlink_rejected") from exc
            raise
        finally:
            os.close(current)

    def read_bytes(self, relative: str, *, max_bytes: int = 64 * 1024 * 1024) -> bytes:
        if type(max_bytes) is not int or max_bytes < 0:
            raise StorageError("external_writer_invalid_read_limit")
        parent, name = self._parent_and_name(relative)
        parent_fd = self._directory(parent)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent_fd)
        except OSError as exc:
            os.close(parent_fd)
            if exc.errno == errno.ELOOP:
                raise StorageError("external_writer_symlink_rejected") from exc
            raise
        try:
            before = os.fstat(fd)
            _private_file(before, device=self.device)
            if before.st_size > max_bytes:
                raise StorageError("external_writer_file_too_large")
            chunks: list[bytes] = []
            total = 0
            while chunk := os.read(fd, min(1024 * 1024, max_bytes + 1 - total)):
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise StorageError("external_writer_file_too_large")
            after = os.fstat(fd)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise StorageError("external_writer_file_changed_during_read")
            return b"".join(chunks)
        finally:
            os.close(fd)
            os.close(parent_fd)

    def exists(self, relative: str) -> bool:
        parent, name = self._parent_and_name(relative)
        try:
            parent_fd = self._directory(parent)
        except FileNotFoundError:
            return False
        try:
            try:
                st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return False
            if stat.S_ISLNK(st.st_mode):
                raise StorageError("external_writer_symlink_rejected")
            _private_file(st, device=self.device)
            return True
        finally:
            os.close(parent_fd)

    def stat(self, relative: str) -> os.stat_result:
        parent, name = self._parent_and_name(relative)
        parent_fd = self._directory(parent)
        try:
            result = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if stat.S_ISLNK(result.st_mode):
                raise StorageError("external_writer_symlink_rejected")
            if stat.S_ISDIR(result.st_mode):
                _private_dir(result, device=self.device)
            else:
                _private_file(result, device=self.device)
            return result
        finally:
            os.close(parent_fd)

    def list_files(self) -> list[str]:
        """Return sorted root-relative regular files; reject links and unsafe entries."""
        found: list[str] = []

        def visit(directory_fd: int, prefix: str) -> None:
            for name in sorted(os.listdir(directory_fd)):
                _parts(name)
                entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                relative = f"{prefix}/{name}" if prefix else name
                if stat.S_ISDIR(entry.st_mode):
                    _private_dir(entry, device=self.device)
                    child_fd = os.open(
                        name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=directory_fd,
                    )
                    try:
                        visit(child_fd, relative)
                    finally:
                        os.close(child_fd)
                elif stat.S_ISLNK(entry.st_mode):
                    raise StorageError("external_writer_symlink_rejected")
                else:
                    _private_file(entry, device=self.device)
                    found.append(relative)

        directory_fd = self._directory("")
        try:
            visit(directory_fd, "")
        finally:
            os.close(directory_fd)
        return found

    def list_names(self, relative: str = "") -> tuple[str, ...]:
        directory_fd = self._directory(relative)
        try:
            return tuple(sorted(os.listdir(directory_fd)))
        finally:
            os.close(directory_fd)

    def create(self, relative: str, payload: bytes, *, mode: int = 0o600) -> None:
        self._assert_writable()
        if mode != 0o600:
            raise StorageError("external_writer_unsafe_file_mode")
        parent, name = self._parent_and_name(relative)
        parent_fd = self._directory(parent, create=True)
        try:
            self._prewrite("create")
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                mode,
                dir_fd=parent_fd,
            )
            try:
                _private_file(os.fstat(fd), device=self.device)
                _write_all(fd, payload)
                os.fchmod(fd, mode)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)

    def atomic_write(self, relative: str, payload: bytes, *, create_only: bool = False) -> None:
        self._assert_writable()
        parent, name = self._parent_and_name(relative)
        parent_fd = self._directory(parent, create=True)
        temp = f".{name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        fd = -1
        try:
            if create_only:
                try:
                    os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise StorageError("external_writer_create_only_collision")
            else:
                try:
                    existing = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    if stat.S_ISLNK(existing.st_mode):
                        raise StorageError("external_writer_symlink_rejected")
                    _private_file(existing, device=self.device)
            self._prewrite("atomic_create")
            fd = os.open(
                temp,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            _write_all(fd, payload)
            os.fchmod(fd, 0o600)
            _private_file(os.fstat(fd), device=self.device)
            os.fsync(fd)
            os.close(fd)
            fd = -1
            self._prewrite("atomic_rename")
            if create_only:
                os.link(
                    temp,
                    name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                os.unlink(temp, dir_fd=parent_fd)
            else:
                os.replace(temp, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError as exc:
            raise StorageError("external_writer_create_only_collision") from exc
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(temp, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            os.close(parent_fd)

    def atomic_write_stream(
        self,
        relative: str,
        chunks: Iterable[bytes],
        *,
        max_bytes: int,
        create_only: bool = True,
        before_chunk: Callable[[], None] | None = None,
    ) -> tuple[int, str]:
        """Atomically persist bounded chunks and return byte count and SHA-256.

        The destination is visible only after a complete fsynced temporary file
        is linked or renamed into place. ``before_chunk`` lets a caller recheck
        an independently held source view while a long stream is in progress.
        """
        self._assert_writable()
        if type(max_bytes) is not int or max_bytes < 0:
            raise StorageError("external_writer_invalid_stream_limit")
        parent, name = self._parent_and_name(relative)
        parent_fd = self._directory(parent, create=True)
        temp = f".{name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        fd = -1
        total = 0
        digest = hashlib.sha256()
        try:
            if create_only:
                try:
                    os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise StorageError("external_writer_create_only_collision")
            self._prewrite("atomic_stream_create")
            fd = os.open(
                temp,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            _private_file(os.fstat(fd), device=self.device)
            for chunk in chunks:
                if not isinstance(chunk, bytes):
                    raise StorageError("external_writer_stream_chunk_not_bytes")
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise StorageError("external_writer_stream_file_too_large")
                self._prewrite("atomic_stream_write")
                if before_chunk is not None:
                    before_chunk()
                _write_all(fd, chunk)
                digest.update(chunk)
            os.fchmod(fd, 0o600)
            _private_file(os.fstat(fd), device=self.device)
            os.fsync(fd)
            os.close(fd)
            fd = -1
            self._prewrite("atomic_stream_publish")
            if create_only:
                os.link(
                    temp,
                    name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                os.unlink(temp, dir_fd=parent_fd)
            else:
                os.replace(temp, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
            return total, digest.hexdigest()
        except FileExistsError as exc:
            raise StorageError("external_writer_create_only_collision") from exc
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(temp, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            os.close(parent_fd)

    def append(self, relative: str, payload: bytes) -> None:
        self._assert_writable()
        parent, name = self._parent_and_name(relative)
        parent_fd = self._directory(parent, create=True)
        try:
            self._prewrite("append_open")
            fd = os.open(
                name,
                os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            try:
                _private_file(os.fstat(fd), device=self.device)
                self._prewrite("append_write")
                _write_all(fd, payload)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)

    def unlink(self, relative: str, *, missing_ok: bool = False) -> None:
        self._assert_writable()
        parent, name = self._parent_and_name(relative)
        parent_fd = self._directory(parent)
        try:
            try:
                st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                if missing_ok:
                    return
                raise
            if stat.S_ISLNK(st.st_mode):
                raise StorageError("external_writer_symlink_rejected")
            _private_file(st, device=self.device)
            self._prewrite("unlink")
            os.unlink(name, dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)

    @contextlib.contextmanager
    def lock(self, relative: str = ".writer.lock", *, blocking: bool = False) -> Iterator[int]:
        self._assert_writable()
        parent, name = self._parent_and_name(relative)
        parent_fd = self._directory(parent, create=True)
        try:
            try:
                os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                created = False
            except FileNotFoundError:
                created = True
            self._prewrite("lock_open")
            fd = os.open(
                name,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            if created:
                os.fsync(parent_fd)
            _private_file(os.fstat(fd), device=self.device)
            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(fd, flags)
            except BlockingIOError as exc:
                os.close(fd)
                raise StorageError("external_writer_already_active") from exc
            try:
                yield fd
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
        finally:
            os.close(parent_fd)


def canonical_json(value: Any) -> bytes:
    try:
        return (
            json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode()
    except (TypeError, ValueError) as exc:
        raise StorageError("external_writer_invalid_json") from exc


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def semantic_request_sha256(method: str, params: list[Any]) -> str:
    return sha256(canonical_json({"method": method, "params": params}))


def wire_request_sha256(method: str, params: list[Any]) -> str:
    return sha256(
        canonical_json({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).rstrip(
            b"\n"
        )
    )


def write_capture(
    writer: DescriptorWriter,
    *,
    capture: Any,
    provider: str,
    request_id: str,
    method: str,
    params: list[Any],
    request_sha256: str,
    wire_request_sha256: str,
    decision_ns: int,
    relative_directory: str,
    storage_codec: str = "raw",
) -> dict[str, Any]:
    """Persist response and receipt without calling path-based ``Capture.save``."""
    from memecoin_ai.sources import _zstd_executable, verify_capture_storage_codec

    if not isinstance(capture.request_json, str):
        raise StorageError("external_writer_capture_request_identity_mismatch")
    expected_wire = (
        canonical_json({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        .rstrip(b"\n")
        .decode()
    )
    actual_wire_sha256 = sha256(capture.request_json.encode())
    if (
        capture.source_alias != provider
        or capture.method != method
        or capture.request_json != expected_wire
        or semantic_request_sha256(method, params) != request_sha256
        or actual_wire_sha256 != wire_request_sha256
    ):
        raise StorageError("external_writer_capture_request_identity_mismatch")
    verify_capture_storage_codec(storage_codec)
    response = bytes(capture.response)
    body = response
    file_name = "response.json"
    codec_version: str | None = None
    if storage_codec == "zstd":
        executable, codec_version = _zstd_executable()
        result = subprocess.run(
            [executable, "--quiet", "--fast=3", "--stdout"],
            input=response,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise StorageError("external_writer_capture_compression_failed")
        body = result.stdout
        file_name = "response.json.zst"

    writer.mkdir(relative_directory)
    response_path = f"{relative_directory}/{file_name}"
    writer.atomic_write(response_path, body, create_only=True)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "provider": provider,
        "request_id": request_id,
        "request_sha256": request_sha256,
        "wire_request_sha256": actual_wire_sha256,
        "request_started_ns": int(capture.request_started_ns),
        "received_ns": int(capture.received_ns),
        "availability_ns": None,
        "decision_ns": decision_ns,
        "response_sha256": sha256(response),
        "response_bytes": len(response),
        "storage": {
            "codec": storage_codec,
            "path": file_name,
            "sha256": sha256(body),
            "bytes": len(body),
            "codec_version": codec_version,
        },
    }
    writer.atomic_write(
        f"{relative_directory}/receipt.json", canonical_json(receipt), create_only=True
    )
    return receipt
