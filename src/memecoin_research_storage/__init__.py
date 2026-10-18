"""Fail-closed host binding for durable research data roots.

This module does not redirect existing writers. Callers must resolve and retain a
``ResolvedRoot`` and revalidate it under their own writer lock before each dispatch.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import plistlib
import re
import secrets
import stat
import subprocess
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

CONFIG_VERSION = 1
LINUX_CONFIG_VERSION = 2
ROOT_MARKER = ".research-root.json"
LOCK_NAME = ".research-storage.lock"
MAX_ROOT_MARKER_BYTES = 16 * 1024


class StorageError(RuntimeError):
    """A storage precondition failed; callers must not fall back elsewhere."""


@dataclass(frozen=True)
class VolumeInfo:
    mount_path: Path
    volume_uuid: str
    filesystem: str
    owners_enabled: bool
    read_only: bool
    available_bytes: int
    is_mountpoint: bool
    filesystem_type: str = "apfs"
    mount_options: tuple[str, ...] = ()
    mount_id: int | None = None
    source: str = ""
    mount_root: str = "/"


class VolumeProbe(Protocol):
    def inspect(self, mount_path: Path) -> VolumeInfo: ...


class MacOSVolumeProbe:
    """Read mount identity and ownership from macOS diskutil plus statvfs."""

    def inspect(self, mount_path: Path) -> VolumeInfo:
        if os.name != "posix" or not Path("/usr/sbin/diskutil").exists():
            raise StorageError("unsupported_host: expected macOS diskutil")
        try:
            raw = subprocess.run(
                ["/usr/sbin/diskutil", "info", "-plist", str(mount_path)],
                check=True,
                capture_output=True,
                timeout=10,
            ).stdout
            info = plistlib.loads(raw)
            if not isinstance(info, dict):
                raise StorageError("invalid_diskutil_volume_info")
            mount_output = subprocess.run(
                ["/sbin/mount"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
            st = mount_path.stat()
            vfs = os.statvfs(mount_path)
        except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException) as exc:
            raise StorageError(f"volume_inspection_failed: {exc}") from exc
        observed_mount = info.get("MountPoint")
        if not isinstance(observed_mount, str):
            raise StorageError("volume_not_mounted")
        owners = info.get("GlobalPermissionsEnabled") is True
        writable = info.get("WritableVolume") is True
        readonly = not writable
        mount_options = _mount_options(mount_output, mount_path)
        available = int(vfs.f_bavail * vfs.f_frsize)
        return VolumeInfo(
            mount_path=Path(observed_mount),
            volume_uuid=str(info.get("VolumeUUID", "")),
            filesystem=str(info.get("FilesystemName", "")),
            owners_enabled=bool(owners),
            read_only=readonly,
            available_bytes=available,
            is_mountpoint=os.path.ismount(mount_path) and st.st_ino > 0,
            filesystem_type=str(info.get("FilesystemType", "")),
            mount_options=mount_options,
        )


class LinuxExt4VolumeProbe:
    """Inspect an exact ext4 mount using mountinfo and the block UUID."""

    def inspect(self, mount_path: Path) -> VolumeInfo:
        if not sys.platform.startswith("linux"):
            raise StorageError("unsupported_host: Linux ext4 probe requires Linux")
        try:
            records = _linux_mount_records()
            matches = [record for record in records if record["mount_path"] == str(mount_path)]
            if len(matches) != 1:
                raise StorageError("mountpoint_missing_or_ambiguous")
            record = matches[0]
            if record["mount_root"] != "/":
                raise StorageError("bind_or_subdirectory_mount_rejected")
            source = record["source"]
            if not source.startswith("/dev/"):
                raise StorageError("block_source_required")
            source_stat = os.stat(source, follow_symlinks=True)
            if not stat.S_ISBLK(source_stat.st_mode):
                raise StorageError("block_source_required")
            actual_dev = f"{os.major(source_stat.st_rdev)}:{os.minor(source_stat.st_rdev)}"
            if actual_dev != record["major_minor"]:
                raise StorageError("mount_source_device_mismatch")
            uuid_result = subprocess.run(
                [
                    "/usr/bin/findmnt",
                    "--noheadings",
                    "--raw",
                    "--target",
                    str(mount_path),
                    "--output",
                    "UUID",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            volume_uuid = uuid_result.stdout.strip()
            vfs = os.statvfs(mount_path)
            mount_stat = mount_path.stat()
            mount_dev = f"{os.major(mount_stat.st_dev)}:{os.minor(mount_stat.st_dev)}"
            if mount_dev != record["major_minor"]:
                raise StorageError("mount_filesystem_device_mismatch")
        except StorageError:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise StorageError(f"linux_volume_inspection_failed: {exc}") from exc
        options = tuple(option.lower() for option in record["mount_options"])
        return VolumeInfo(
            mount_path=mount_path,
            volume_uuid=volume_uuid,
            filesystem=str(record["filesystem"]),
            owners_enabled=True,
            read_only="ro" in options,
            available_bytes=int(vfs.f_bavail * vfs.f_frsize),
            is_mountpoint=os.path.ismount(mount_path) and mount_stat.st_ino > 0,
            filesystem_type=str(record["filesystem"]),
            mount_options=options,
            mount_id=int(record["mount_id"]),
            source=source,
            mount_root=str(record["mount_root"]),
        )


def _mountinfo_unescape(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


def _linux_mount_records() -> list[dict[str, object]]:
    try:
        contents = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    except OSError as exc:
        raise StorageError("mountinfo_unavailable") from exc
    return _parse_linux_mountinfo(contents)


def _parse_linux_mountinfo(contents: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line in contents.splitlines():
        before, separator, after = line.partition(" - ")
        left = before.split()
        right = after.split()
        if not separator or len(left) < 6 or len(right) < 3:
            raise StorageError("mountinfo_malformed")
        records.append(
            {
                "mount_id": left[0],
                "major_minor": left[2],
                "mount_root": _mountinfo_unescape(left[3]),
                "mount_path": _mountinfo_unescape(left[4]),
                "mount_options": tuple(left[5].split(",")),
                "filesystem": right[0],
                "source": _mountinfo_unescape(right[1]),
            }
        )
    return records


def _mount_options(mount_output: str, mount_path: Path) -> tuple[str, ...]:
    suffix = f" on {mount_path} ("
    for line in mount_output.splitlines():
        if suffix in line and line.endswith(")"):
            options = line.rsplit("(", 1)[1][:-1].split(",")
            return tuple(option.strip().lower() for option in options)
    raise StorageError("mount_options_unavailable")


def _validate_volume_policy(
    volume: VolumeInfo,
    expected_mount: Path,
    *,
    platform: str = "macos",
    require_writable: bool = True,
) -> None:
    if not volume.is_mountpoint or volume.mount_path != expected_mount:
        raise StorageError("mountpoint_directory_fallback")
    if not volume.volume_uuid:
        raise StorageError("volume_uuid_unavailable")
    if platform == "macos":
        if volume.filesystem.lower().strip() != "case-sensitive apfs":
            raise StorageError("filesystem_not_case_sensitive_apfs")
        if volume.filesystem_type.lower().strip() != "apfs":
            raise StorageError("filesystem_type_not_apfs")
        if "noowners" in volume.mount_options:
            raise StorageError("unsafe_mount_options")
        if require_writable and {"read-only", "ro", "rdonly"}.intersection(volume.mount_options):
            raise StorageError("unsafe_mount_options")
        if not volume.owners_enabled:
            raise StorageError("volume_owners_disabled")
    elif platform == "linux":
        if volume.filesystem.lower().strip() != "ext4":
            raise StorageError("filesystem_not_ext4")
        if volume.filesystem_type.lower().strip() != "ext4":
            raise StorageError("filesystem_type_not_ext4")
        if (
            require_writable and ("ro" in volume.mount_options or "rw" not in volume.mount_options)
        ) or not volume.owners_enabled:
            raise StorageError("unsafe_mount_options")
        if volume.mount_id is None or volume.source == "" or volume.mount_root != "/":
            raise StorageError("linux_mount_identity_incomplete")
    else:
        raise StorageError("unsupported_storage_platform")
    if require_writable and volume.read_only:
        raise StorageError("volume_not_writable")


def _default_probe(config: StorageConfig | None = None) -> VolumeProbe:
    if config is not None and config.platform == "linux":
        return LinuxExt4VolumeProbe()
    return MacOSVolumeProbe()


@dataclass(frozen=True)
class StorageConfig:
    root_id: str
    volume_uuid: str
    mount_path: Path
    data_root: str
    min_free_bytes: int
    version: int = CONFIG_VERSION
    platform: str = "macos"
    filesystem: str = "apfs"

    @classmethod
    def load(cls, path: Path) -> StorageConfig:
        if not path.is_absolute() or "\x00" in str(path):
            raise StorageError("invalid_config_path")
        _require_plain_path(path)
        fd = -1
        try:
            parent_fd = _open_absolute_directory(path.parent)
            parent_stat = os.fstat(parent_fd)
            if parent_stat.st_uid != os.getuid() or stat.S_IMODE(parent_stat.st_mode) != 0o700:
                os.close(parent_fd)
                raise StorageError("unsafe_config_parent_permissions")
            try:
                fd = os.open(
                    path.name,
                    os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                    dir_fd=parent_fd,
                )
            finally:
                os.close(parent_fd)
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
                raise StorageError("unsafe_config_owner_or_type")
            if stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1:
                raise StorageError("unsafe_config_permissions")
            chunks: list[bytes] = []
            while chunk := os.read(fd, 65536):
                chunks.append(chunk)
            after = os.fstat(fd)
            if (
                st.st_dev,
                st.st_ino,
                st.st_size,
                st.st_mtime_ns,
                st.st_ctime_ns,
                st.st_mode,
                st.st_uid,
                st.st_nlink,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
                after.st_mode,
                after.st_uid,
                after.st_nlink,
            ):
                raise StorageError("config_changed_during_read")
            payload = json.loads(
                b"".join(chunks).decode("utf-8"), object_pairs_hook=_unique_object_pairs
            )
        except StorageError:
            raise
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StorageError(f"invalid_config: {exc}") from exc
        finally:
            if fd >= 0:
                os.close(fd)
        if not isinstance(payload, dict):
            raise StorageError("invalid_config_fields")
        version = payload.get("version")
        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or version not in {CONFIG_VERSION, LINUX_CONFIG_VERSION}
        ):
            raise StorageError("unsupported_config_version")
        if version == CONFIG_VERSION:
            required_fields = {
                "version",
                "root_id",
                "volume_uuid",
                "mount_path",
                "data_root",
                "min_free_bytes",
            }
            platform, filesystem = "macos", "apfs"
        else:
            required_fields = {
                "version",
                "platform",
                "filesystem",
                "root_id",
                "volume_uuid",
                "mount_path",
                "data_root",
                "min_free_bytes",
            }
            platform, filesystem = payload.get("platform"), payload.get("filesystem")
            if platform != "linux" or filesystem != "ext4":
                raise StorageError("unsupported_storage_platform_or_filesystem")
        if set(payload) != required_fields:
            raise StorageError("invalid_config_fields")
        root_id = payload.get("root_id")
        volume_uuid = payload.get("volume_uuid")
        mount = payload.get("mount_path")
        data_root = payload.get("data_root")
        minimum = payload.get("min_free_bytes")
        if (
            not isinstance(root_id, str)
            or len(root_id) != 32
            or any(c not in "0123456789abcdef" for c in root_id)
        ):
            raise StorageError("invalid_root_id")
        if not isinstance(volume_uuid, str):
            raise StorageError("invalid_volume_uuid")
        volume_uuid = _validate_uuid(volume_uuid)
        if not isinstance(mount, str) or "\x00" in mount or not Path(mount).is_absolute():
            raise StorageError("invalid_mount_path")
        if ".." in Path(mount).parts or str(Path(mount)) != mount:
            raise StorageError("invalid_mount_path")
        data_root = _validate_relative(data_root, "data_root")
        if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 0:
            raise StorageError("invalid_min_free_bytes")
        return cls(
            root_id, volume_uuid, Path(mount), data_root, minimum, version, platform, filesystem
        )

    def to_json(self) -> dict[str, object]:
        payload = {
            "version": CONFIG_VERSION,
            "root_id": self.root_id,
            "volume_uuid": self.volume_uuid,
            "mount_path": str(self.mount_path),
            "data_root": self.data_root,
            "min_free_bytes": self.min_free_bytes,
        }
        if self.version == LINUX_CONFIG_VERSION and self.platform == "linux":
            payload.update(
                {
                    "version": LINUX_CONFIG_VERSION,
                    "platform": "linux",
                    "filesystem": "ext4",
                }
            )
        return payload


@dataclass(frozen=True)
class ResolvedRoot:
    root_id: str
    volume_uuid: str
    mount_path: Path
    path: Path
    available_bytes: int
    mount_device: int
    mount_inode: int
    mount_id: int | None = None


def _validate_uuid(value: object) -> str:
    if not isinstance(value, str):
        raise StorageError("invalid_volume_uuid")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise StorageError("invalid_volume_uuid") from exc
    if str(parsed) != value.lower():
        raise StorageError("invalid_volume_uuid")
    return str(parsed)


def _fd_mount_id(fd: int) -> int | None:
    if not sys.platform.startswith("linux"):
        return None
    try:
        contents = Path(f"/proc/self/fdinfo/{fd}").read_text(encoding="ascii")
    except OSError as exc:
        raise StorageError("mount_id_unavailable") from exc
    for line in contents.splitlines():
        key, separator, value = line.partition(":")
        if separator and key == "mnt_id" and value.strip().isdecimal():
            return int(value.strip())
    raise StorageError("mount_id_unavailable")


def _verify_linux_case_sensitive_directory(directory_fd: int) -> None:
    if not sys.platform.startswith("linux"):
        raise StorageError("unsupported_host: Linux filesystem policy requires Linux")
    import array

    flags = array.array("l", [0])
    try:
        import fcntl

        fcntl.ioctl(directory_fd, 0x80086601, flags, True)  # FS_IOC_GETFLAGS
    except OSError as exc:
        raise StorageError("filesystem_case_sensitivity_unavailable") from exc
    if flags[0] & 0x40000000:  # FS_CASEFOLD_FL
        raise StorageError("filesystem_casefold_enabled")


def _probe_case_sensitive_names(directory_fd: int) -> None:
    lower = f".brain-case-probe-{secrets.token_hex(12)}-a"
    upper = lower[:-1] + "A"
    first_fd = second_fd = -1
    try:
        first_fd = os.open(
            lower,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        os.fsync(first_fd)
        try:
            second_fd = os.open(
                upper,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
        except FileExistsError as exc:
            raise StorageError("filesystem_not_case_sensitive") from exc
        os.fsync(second_fd)
        _verify_linux_case_sensitive_directory(directory_fd)
    except StorageError:
        raise
    except OSError as exc:
        raise StorageError(f"filesystem_case_probe_failed: {exc}") from exc
    finally:
        if second_fd >= 0:
            os.close(second_fd)
        if first_fd >= 0:
            os.close(first_fd)
        for name in (upper, lower):
            try:
                os.unlink(name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise StorageError(f"filesystem_case_probe_cleanup_failed: {exc}") from exc
        os.fsync(directory_fd)


def _unique_object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise StorageError("duplicate_json_key")
        result[key] = value
    return result


def _validate_relative(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise StorageError(f"invalid_{label}")
    p = Path(value)
    if p.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise StorageError(f"unsafe_{label}")
    return value


def _require_plain_path(path: Path) -> None:
    if path.is_symlink():
        raise StorageError("symlink_path_rejected")
    # Reject symlinks in every existing ancestor, including the config parent.
    absolute = Path(os.path.abspath(path))
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise StorageError("symlink_path_rejected")


def _mount_is_plain(path: Path) -> None:
    _require_plain_path(path)
    if not path.is_dir():
        raise StorageError("mount_missing")


def inspect_config(
    config: StorageConfig,
    *,
    min_free_bytes: int | None = None,
    probe: VolumeProbe | None = None,
) -> ResolvedRoot:
    if min_free_bytes is not None:
        _validate_min_free(min_free_bytes)
    _mount_is_plain(config.mount_path)
    volume = (probe or _default_probe(config)).inspect(config.mount_path)
    _validate_volume_policy(volume, config.mount_path, platform=config.platform)
    if volume.volume_uuid.lower() != config.volume_uuid.lower():
        raise StorageError("volume_uuid_mismatch")
    if not os.access(config.mount_path, os.W_OK):
        raise StorageError("volume_not_writable")
    required = (
        config.min_free_bytes
        if min_free_bytes is None
        else max(config.min_free_bytes, min_free_bytes)
    )
    if volume.available_bytes < required:
        raise StorageError("insufficient_free_space")
    mount_fd = _open_directory(config.mount_path)
    try:
        mount_stat = os.fstat(mount_fd)
        mount_id = _fd_mount_id(mount_fd)
        if config.platform == "linux" and mount_id != volume.mount_id:
            raise StorageError("mount_changed_during_probe")
    finally:
        os.close(mount_fd)
    target = config.mount_path.joinpath(*config.data_root.split("/"))
    _check_root_components(config.mount_path, config.data_root, allow_missing=True)
    return ResolvedRoot(
        config.root_id,
        config.volume_uuid,
        config.mount_path,
        target,
        volume.available_bytes,
        mount_stat.st_dev,
        mount_stat.st_ino,
        mount_id,
    )


def _inspect_readonly_config(
    config: StorageConfig, *, probe: VolumeProbe | None = None
) -> ResolvedRoot:
    """Resolve a marked APFS root without requiring write access or capacity headroom."""
    _mount_is_plain(config.mount_path)
    volume = (probe or _default_probe(config)).inspect(config.mount_path)
    if not volume.is_mountpoint or volume.mount_path != config.mount_path:
        raise StorageError("mountpoint_directory_fallback")
    if not volume.volume_uuid:
        raise StorageError("volume_uuid_unavailable")
    _validate_volume_policy(
        volume, config.mount_path, platform=config.platform, require_writable=False
    )
    if volume.volume_uuid.lower() != config.volume_uuid.lower():
        raise StorageError("volume_uuid_mismatch")

    mount_fd = _open_directory(config.mount_path)
    try:
        mount_stat = os.fstat(mount_fd)
        mount_id = _fd_mount_id(mount_fd)
        if config.platform == "linux" and mount_id != volume.mount_id:
            raise StorageError("mount_changed_during_probe")
        root_fd = _open_relative_directory(mount_fd, config.data_root)
        try:
            _verify_marker_in_root_fd(root_fd, config)
            root_stat = os.fstat(root_fd)
            if root_stat.st_dev != mount_stat.st_dev:
                raise StorageError("research_root_device_mismatch")
        finally:
            os.close(root_fd)
    finally:
        os.close(mount_fd)
    return ResolvedRoot(
        config.root_id,
        config.volume_uuid,
        config.mount_path,
        config.mount_path.joinpath(*config.data_root.split("/")),
        volume.available_bytes,
        mount_stat.st_dev,
        mount_stat.st_ino,
        mount_id,
    )


def _validate_min_free(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise StorageError("invalid_min_free_bytes")
    return value


def _assert_no_symlink_beneath(base: Path, target: Path) -> None:
    try:
        relative = target.relative_to(base)
    except ValueError as exc:
        raise StorageError("root_escapes_mount") from exc
    cursor = base
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise StorageError("symlink_root_component")


def _permission_probe(mount_fd: int) -> None:
    """Explicit disposable qualification against a pinned mount directory fd."""
    fd = -1
    name = f".research-storage-probe-{secrets.token_hex(12)}"
    try:
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=mount_fd,
        )
        os.fchmod(fd, 0o600)
        os.fsync(fd)
        st = os.fstat(fd)
        if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600:
            raise StorageError("owner_mode_semantics_unverified")
    except StorageError:
        raise
    except OSError as exc:
        raise StorageError(f"permission_probe_failed: {exc}") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        if fd >= 0:
            try:
                os.unlink(name, dir_fd=mount_fd)
                os.fsync(mount_fd)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise StorageError(f"permission_probe_cleanup_failed: {exc}") from exc


def qualify_volume(
    mount_path: Path,
    expected_uuid: str,
    *,
    min_free_bytes: int = 0,
    probe: VolumeProbe | None = None,
    platform: str = "macos",
) -> VolumeInfo:
    """Run an explicit disposable owner/mode probe after read-only volume checks."""
    expected_uuid = _validate_uuid(expected_uuid)
    min_free_bytes = _validate_min_free(min_free_bytes)
    _mount_is_plain(mount_path)
    volume_probe = probe or (LinuxExt4VolumeProbe() if platform == "linux" else MacOSVolumeProbe())
    volume = volume_probe.inspect(mount_path)
    _validate_volume_policy(volume, mount_path, platform=platform)
    if volume.volume_uuid.lower() != expected_uuid:
        raise StorageError("volume_uuid_mismatch")
    if not os.access(mount_path, os.W_OK):
        raise StorageError("volume_not_writable")
    if volume.available_bytes < min_free_bytes:
        raise StorageError("insufficient_free_space")
    mount_fd = _open_directory(mount_path)
    try:
        pinned = os.fstat(mount_fd)
        pinned_mount_id = _fd_mount_id(mount_fd)
        if platform == "linux" and pinned_mount_id != volume.mount_id:
            raise StorageError("mounted_directory_replaced")
        current_volume = volume_probe.inspect(mount_path)
        _validate_volume_policy(current_volume, mount_path, platform=platform)
        if current_volume.volume_uuid.lower() != expected_uuid:
            raise StorageError("volume_uuid_mismatch")
        current_fd = _open_directory(mount_path)
        try:
            current = os.fstat(current_fd)
            if (pinned.st_dev, pinned.st_ino) != (current.st_dev, current.st_ino):
                raise StorageError("mounted_directory_replaced")
            if platform == "linux" and _fd_mount_id(current_fd) != current_volume.mount_id:
                raise StorageError("mounted_directory_replaced")
        finally:
            os.close(current_fd)
        if current_volume.available_bytes < min_free_bytes:
            raise StorageError("insufficient_free_space")
        _permission_probe(mount_fd)
        final_volume = volume_probe.inspect(mount_path)
        _validate_volume_policy(final_volume, mount_path, platform=platform)
        if final_volume.volume_uuid.lower() != expected_uuid:
            raise StorageError("volume_uuid_mismatch")
        final_fd = _open_directory(mount_path)
        try:
            final_stat = os.fstat(final_fd)
            if (pinned.st_dev, pinned.st_ino) != (final_stat.st_dev, final_stat.st_ino):
                raise StorageError("mounted_directory_replaced")
            if platform == "linux" and _fd_mount_id(final_fd) != final_volume.mount_id:
                raise StorageError("mounted_directory_replaced")
        finally:
            os.close(final_fd)
        if final_volume.available_bytes < min_free_bytes:
            raise StorageError("insufficient_free_space")
        return final_volume
    finally:
        os.close(mount_fd)


def _check_root_components(base: Path, relative: str, *, allow_missing: bool) -> None:
    cursor = _open_directory(base)
    try:
        for part in relative.split("/"):
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=cursor)
            except FileNotFoundError as exc:
                if allow_missing:
                    return
                raise StorageError("research_root_missing") from exc
            except OSError as exc:
                try:
                    component_stat = os.stat(part, dir_fd=cursor, follow_symlinks=False)
                except OSError:
                    component_stat = None
                if exc.errno == errno.ELOOP or (
                    component_stat is not None and stat.S_ISLNK(component_stat.st_mode)
                ):
                    raise StorageError("symlink_root_component") from exc
                raise StorageError("unsafe_root_ancestor") from exc
            st = os.fstat(child)
            if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
                os.close(child)
                raise StorageError("unsafe_root_owner_or_mode")
            os.close(cursor)
            cursor = child
        # Existing configured root must already be a private owner-owned directory.
        st = os.fstat(cursor)
        if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
            raise StorageError("unsafe_root_owner_or_mode")
    finally:
        os.close(cursor)


def _open_directory(path: Path) -> int:
    try:
        return _open_absolute_directory(path)
    except OSError as exc:
        raise StorageError(f"mount_directory_open_failed: {exc}") from exc


def _open_absolute_directory(path: Path) -> int:
    if not path.is_absolute() or "\x00" in str(path) or ".." in path.parts:
        raise StorageError("unsafe_absolute_directory")
    current = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = child
        return current
    except OSError:
        os.close(current)
        raise


def _make_private_directory(path: Path) -> int:
    """Create/check a private config parent without traversing symlinks."""
    if not path.is_absolute() or "\x00" in str(path):
        raise StorageError("invalid_config_parent")
    if ".." in path.parts:
        raise StorageError("invalid_config_parent")
    normalized = Path(os.path.normpath(path))
    parts = normalized.parts[1:]
    if not parts:
        raise StorageError("config_parent_cannot_be_filesystem_root")
    current = os.open(normalized.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, part in enumerate(parts):
            final = index == len(parts) - 1
            created = False
            try:
                os.mkdir(part, 0o700, dir_fd=current)
                os.fsync(current)
                created = True
            except FileExistsError:
                pass
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            if created:
                os.fchmod(child, 0o700)
            st = os.fstat(child)
            if final and (st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700):
                os.close(child)
                raise StorageError("unsafe_config_parent_permissions")
            os.close(current)
            current = child
        return current
    except StorageError:
        os.close(current)
        raise
    except OSError as exc:
        os.close(current)
        raise StorageError(f"unsafe_config_parent: {exc}") from exc
    except BaseException:
        os.close(current)
        raise


def _ensure_config_parent_on_other_device(path: Path, mount_device: int) -> None:
    """Find an existing host-side ancestor without creating anything."""
    candidate = path
    while True:
        try:
            fd = _open_absolute_directory(candidate)
        except FileNotFoundError:
            if candidate.parent == candidate:
                raise StorageError("config_parent_unavailable") from None
            candidate = candidate.parent
            continue
        except OSError as exc:
            raise StorageError("config_parent_unavailable") from exc
        try:
            if os.fstat(fd).st_dev == mount_device:
                raise StorageError("config_must_be_outside_bound_volume")
            return
        finally:
            os.close(fd)


def _open_relative_directory(base_fd: int, relative: str) -> int:
    current = os.dup(base_fd)
    try:
        for part in relative.split("/"):
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            st = os.fstat(child)
            if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
                os.close(child)
                raise StorageError("unsafe_root_owner_or_mode")
            os.close(current)
            current = child
        return current
    except StorageError:
        os.close(current)
        raise
    except FileNotFoundError as exc:
        os.close(current)
        raise StorageError("research_root_missing") from exc
    except OSError as exc:
        os.close(current)
        if exc.errno == errno.ELOOP:
            raise StorageError("symlink_root_component") from exc
        raise StorageError("unsafe_root_ancestor") from exc


def _read_marker_fd(marker_fd: int, config: StorageConfig) -> None:
    before = os.fstat(marker_fd)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.getuid()
        or stat.S_IMODE(before.st_mode) != 0o600
        or before.st_nlink != 1
    ):
        raise StorageError("unsafe_root_marker")
    chunks: list[bytes] = []
    total = 0
    os.lseek(marker_fd, 0, os.SEEK_SET)
    while chunk := os.read(marker_fd, 65536):
        total += len(chunk)
        if total > MAX_ROOT_MARKER_BYTES:
            raise StorageError("root_marker_too_large")
        chunks.append(chunk)
    after = os.fstat(marker_fd)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
        before.st_mode,
        before.st_uid,
        before.st_nlink,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
        after.st_mode,
        after.st_uid,
        after.st_nlink,
    ):
        raise StorageError("root_marker_changed_during_read")
    try:
        body = json.loads(b"".join(chunks).decode("utf-8"), object_pairs_hook=_unique_object_pairs)
    except (ValueError, UnicodeDecodeError) as exc:
        raise StorageError(f"invalid_root_marker: {exc}") from exc
    expected = {
        "version": 1,
        "root_id": config.root_id,
        "volume_uuid": config.volume_uuid,
        "data_root": config.data_root,
    }
    if (
        not isinstance(body, dict)
        or set(body) != set(expected)
        or type(body.get("version")) is not int
        or any(type(body.get(key)) is not str for key in ("root_id", "volume_uuid", "data_root"))
        or body != expected
    ):
        raise StorageError("root_marker_binding_mismatch")


def _verify_root_at(mount_fd: int, config: StorageConfig) -> None:
    root_fd = _open_relative_directory(mount_fd, config.data_root)
    try:
        _verify_marker_in_root_fd(root_fd, config)
    except StorageError:
        raise
    except OSError as exc:
        raise StorageError(f"root_marker_unavailable: {exc}") from exc
    finally:
        os.close(root_fd)


def _verify_marker_in_root_fd(root_fd: int, config: StorageConfig) -> None:
    if config.platform == "linux":
        _verify_linux_case_sensitive_directory(root_fd)
    marker_fd = os.open(
        ROOT_MARKER,
        os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
        dir_fd=root_fd,
    )
    try:
        _read_marker_fd(marker_fd, config)
    finally:
        os.close(marker_fd)


def _validate_lock_stat(lock_stat: os.stat_result) -> None:
    if (
        not stat.S_ISREG(lock_stat.st_mode)
        or lock_stat.st_uid != os.getuid()
        or stat.S_IMODE(lock_stat.st_mode) != 0o600
        or lock_stat.st_nlink != 1
    ):
        raise StorageError("unsafe_writer_lock")


def resolve(
    config_path: Path, *, min_free_bytes: int | None = None, probe: VolumeProbe | None = None
) -> ResolvedRoot:
    return inspect_config(
        StorageConfig.load(config_path), min_free_bytes=min_free_bytes, probe=probe
    )


class StorageGuard:
    """A locked root handle that can revalidate immediately before each dispatch."""

    read_only = False

    def __init__(
        self,
        config_path: Path,
        config: StorageConfig,
        root: ResolvedRoot,
        mount_fd: int,
        lock_fd: int,
        probe: VolumeProbe | None,
        *,
        bootstrap: bool,
        min_free_bytes: int | None,
    ) -> None:
        self.config_path = config_path
        self.config = config
        self.root = root
        self.mount_fd = mount_fd
        self.lock_fd = lock_fd
        self.probe = probe
        self.bootstrap = bootstrap
        self.min_free_bytes = min_free_bytes

    @property
    def root_id(self) -> str:
        return self.root.root_id

    @property
    def volume_uuid(self) -> str:
        return self.root.volume_uuid

    @property
    def mount_path(self) -> Path:
        return self.root.mount_path

    @property
    def path(self) -> Path:
        return self.root.path

    @property
    def available_bytes(self) -> int:
        return self.root.available_bytes

    def revalidate_identity(self, *, min_free_bytes: int | None = None) -> ResolvedRoot:
        """Recheck a held binding without diskutil or mount subprocesses.

        This fast path is intended for migration inventory/copy loops. Live writer
        dispatches continue to use ``revalidate`` for a full volume inspection.
        """
        if min_free_bytes is not None:
            min_free_bytes = _validate_min_free(min_free_bytes)
        required = self.config.min_free_bytes
        if self.min_free_bytes is not None:
            required = max(required, self.min_free_bytes)
        if min_free_bytes is not None:
            required = max(required, min_free_bytes)

        if StorageConfig.load(self.config_path) != self.config:
            raise StorageError("binding_changed_under_lock")
        pinned_stat = os.fstat(self.mount_fd)
        pinned_mount_id = _fd_mount_id(self.mount_fd)
        lock_fd_stat = os.fstat(self.lock_fd)
        _validate_lock_stat(lock_fd_stat)
        if not os.path.ismount(self.config.mount_path):
            raise StorageError("mountpoint_directory_fallback")

        try:
            current_mount_fd = _open_directory(self.config.mount_path)
        except StorageError:
            raise
        try:
            current_mount_stat = os.fstat(current_mount_fd)
            identity = (pinned_stat.st_dev, pinned_stat.st_ino)
            if identity != (current_mount_stat.st_dev, current_mount_stat.st_ino):
                raise StorageError("mounted_directory_replaced")
            current_mount_id = _fd_mount_id(current_mount_fd)
            if self.config.platform == "linux" and current_mount_id != pinned_mount_id:
                raise StorageError("mounted_directory_replaced")

            try:
                lock_path_stat = os.stat(
                    LOCK_NAME,
                    dir_fd=current_mount_fd,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise StorageError("writer_lock_replaced") from exc
            _validate_lock_stat(lock_path_stat)
            if (lock_fd_stat.st_dev, lock_fd_stat.st_ino) != (
                lock_path_stat.st_dev,
                lock_path_stat.st_ino,
            ):
                raise StorageError("writer_lock_replaced")

            if not self.bootstrap:
                root_fd = _open_relative_directory(self.mount_fd, self.config.data_root)
                try:
                    _verify_marker_in_root_fd(root_fd, self.config)
                finally:
                    os.close(root_fd)

            vfs = os.fstatvfs(current_mount_fd)
            if vfs.f_flag & getattr(os, "ST_RDONLY", 1):
                raise StorageError("volume_not_writable")
            if not os.access(self.config.mount_path, os.W_OK):
                raise StorageError("volume_not_writable")
            available = int(vfs.f_bavail * vfs.f_frsize)
            if available < required:
                raise StorageError("insufficient_free_space")
            if not os.path.ismount(self.config.mount_path):
                raise StorageError("mountpoint_directory_fallback")

            result = replace(
                self.root,
                available_bytes=available,
                mount_id=current_mount_id,
            )
            self.root = result
            return result
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError(f"identity_revalidation_failed: {exc}") from exc
        finally:
            os.close(current_mount_fd)

    def revalidate(self, *, min_free_bytes: int | None = None) -> ResolvedRoot:
        if min_free_bytes is not None:
            min_free_bytes = _validate_min_free(min_free_bytes)
        if self.min_free_bytes is not None:
            min_free_bytes = max(self.min_free_bytes, min_free_bytes or 0)
        current_config = StorageConfig.load(self.config_path)
        if current_config != self.config:
            raise StorageError("binding_changed_under_lock")
        mount_fd_stat = os.fstat(self.mount_fd)
        pinned_mount_id = _fd_mount_id(self.mount_fd)
        current = resolve(self.config_path, min_free_bytes=min_free_bytes, probe=self.probe)
        if (mount_fd_stat.st_dev, mount_fd_stat.st_ino) != (
            current.mount_device,
            current.mount_inode,
        ):
            raise StorageError("mounted_directory_replaced")
        if self.config.platform == "linux" and (
            pinned_mount_id is None or current.mount_id != pinned_mount_id
        ):
            raise StorageError("mounted_directory_replaced")
        lock_fd_stat = os.fstat(self.lock_fd)
        current_mount_fd = _open_directory(current.mount_path)
        try:
            current_mount_stat = os.fstat(current_mount_fd)
            if (current_mount_stat.st_dev, current_mount_stat.st_ino) != (
                current.mount_device,
                current.mount_inode,
            ):
                raise StorageError("mounted_directory_replaced")
            if (
                self.config.platform == "linux"
                and _fd_mount_id(current_mount_fd) != pinned_mount_id
            ):
                raise StorageError("mounted_directory_replaced")
            try:
                lock_path_stat = os.stat(LOCK_NAME, dir_fd=current_mount_fd, follow_symlinks=False)
            except OSError as exc:
                raise StorageError("writer_lock_replaced") from exc
            if (lock_fd_stat.st_dev, lock_fd_stat.st_ino) != (
                lock_path_stat.st_dev,
                lock_path_stat.st_ino,
            ):
                raise StorageError("writer_lock_replaced")
            if (
                not stat.S_ISREG(lock_path_stat.st_mode)
                or lock_path_stat.st_uid != os.getuid()
                or stat.S_IMODE(lock_path_stat.st_mode) != 0o600
                or lock_path_stat.st_nlink != 1
            ):
                raise StorageError("unsafe_writer_lock")
        finally:
            os.close(current_mount_fd)
        if not self.bootstrap:
            _verify_root_at(self.mount_fd, self.config)
        self.root = current
        return current

    def create_directory(self, relative_path: str, *, mode: int = 0o700) -> Path:
        """Create private directories below the marked root using anchored openat calls."""
        if self.bootstrap:
            raise StorageError("bootstrap_guard_cannot_write_data")
        self.revalidate()
        relative_path = _validate_relative(relative_path, "relative_directory")
        if mode != 0o700:
            raise StorageError("unsafe_directory_mode")
        root_fd = _open_relative_directory(self.mount_fd, self.config.data_root)
        current_fd = root_fd
        try:
            _verify_marker_in_root_fd(root_fd, self.config)
            for part in relative_path.split("/"):
                created = False
                try:
                    os.mkdir(part, mode=0o700, dir_fd=current_fd)
                    os.fsync(current_fd)
                    created = True
                except FileExistsError:
                    pass
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current_fd,
                )
                if created:
                    os.fchmod(next_fd, 0o700)
                st = os.fstat(next_fd)
                if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
                    os.close(next_fd)
                    raise StorageError("unsafe_relative_directory")
                if current_fd != root_fd:
                    os.close(current_fd)
                current_fd = next_fd
            os.fsync(current_fd)
        except OSError as exc:
            raise StorageError(f"relative_directory_creation_failed: {exc}") from exc
        finally:
            if current_fd != root_fd:
                os.close(current_fd)
            os.close(root_fd)
        self.revalidate()
        return self.root.path.joinpath(*relative_path.split("/"))


class ReadOnlyStorageGuard:
    """Pinned storage root for verification without locks, writes, or free-space floor."""

    read_only = True
    bootstrap = False

    def __init__(
        self,
        config_path: Path,
        config: StorageConfig,
        root: ResolvedRoot,
        mount_fd: int,
        root_fd: int,
        probe: VolumeProbe | None,
    ) -> None:
        self.config_path = config_path
        self.config = config
        self.root = root
        self.mount_fd = mount_fd
        self.root_fd = root_fd
        self.probe = probe

    @property
    def root_id(self) -> str:
        return self.root.root_id

    @property
    def volume_uuid(self) -> str:
        return self.root.volume_uuid

    @property
    def mount_path(self) -> Path:
        return self.root.mount_path

    @property
    def path(self) -> Path:
        return self.root.path

    @property
    def available_bytes(self) -> int:
        return self.root.available_bytes

    def revalidate(self) -> ResolvedRoot:
        if StorageConfig.load(self.config_path) != self.config:
            raise StorageError("binding_changed_during_read")
        current = _inspect_readonly_config(self.config, probe=self.probe)
        pinned_mount = os.fstat(self.mount_fd)
        pinned_mount_id = _fd_mount_id(self.mount_fd)
        pinned_root = os.fstat(self.root_fd)
        current_mount_fd = _open_directory(current.mount_path)
        try:
            current_mount = os.fstat(current_mount_fd)
            if (pinned_mount.st_dev, pinned_mount.st_ino) != (
                current_mount.st_dev,
                current_mount.st_ino,
            ) or (current.mount_device, current.mount_inode) != (
                current_mount.st_dev,
                current_mount.st_ino,
            ):
                raise StorageError("mounted_directory_replaced")
            if self.config.platform == "linux" and (
                pinned_mount_id is None
                or current.mount_id != pinned_mount_id
                or _fd_mount_id(current_mount_fd) != pinned_mount_id
            ):
                raise StorageError("mounted_directory_replaced")
            current_root_fd = _open_relative_directory(current_mount_fd, self.config.data_root)
            try:
                current_root = os.fstat(current_root_fd)
                if (pinned_root.st_dev, pinned_root.st_ino) != (
                    current_root.st_dev,
                    current_root.st_ino,
                ):
                    raise StorageError("research_root_replaced")
                _verify_marker_in_root_fd(self.root_fd, self.config)
                _verify_marker_in_root_fd(current_root_fd, self.config)
            finally:
                os.close(current_root_fd)
        finally:
            os.close(current_mount_fd)
        self.root = current
        return current

    def revalidate_identity(self) -> ResolvedRoot:
        return self.revalidate()

    def forbid_write(self) -> None:
        raise StorageError("read_only_storage_guard_write_forbidden")

    def close(self) -> None:
        if self.root_fd >= 0:
            os.close(self.root_fd)
            self.root_fd = -1
        if self.mount_fd >= 0:
            os.close(self.mount_fd)
            self.mount_fd = -1


@contextlib.contextmanager
def read_only_guard(
    config_path: Path,
    *,
    probe: VolumeProbe | None = None,
) -> Iterator[ReadOnlyStorageGuard]:
    """Hold a read-only verified storage binding without creating a lock or checking headroom."""
    config = StorageConfig.load(config_path)
    initial = _inspect_readonly_config(config, probe=probe)
    mount_fd = _open_directory(initial.mount_path)
    root_fd = -1
    guard: ReadOnlyStorageGuard | None = None
    try:
        mount_stat = os.fstat(mount_fd)
        if config.platform == "linux" and _fd_mount_id(mount_fd) != initial.mount_id:
            raise StorageError("mounted_directory_replaced")
        if (mount_stat.st_dev, mount_stat.st_ino) != (initial.mount_device, initial.mount_inode):
            raise StorageError("mounted_directory_replaced")
        root_fd = _open_relative_directory(mount_fd, config.data_root)
        _verify_marker_in_root_fd(root_fd, config)
        guard = ReadOnlyStorageGuard(config_path, config, initial, mount_fd, root_fd, probe)
        guard.revalidate()
        yield guard
    finally:
        if guard is not None:
            guard.close()
        else:
            if root_fd >= 0:
                os.close(root_fd)
            os.close(mount_fd)


@contextlib.contextmanager
def _locked_guard(
    config_path: Path,
    *,
    min_free_bytes: int | None,
    probe: VolumeProbe | None,
    bootstrap: bool,
) -> Iterator[StorageGuard]:
    config = StorageConfig.load(config_path)
    initial = inspect_config(config, min_free_bytes=min_free_bytes, probe=probe)
    mount_fd = _open_directory(initial.mount_path)
    lock_fd = -1
    try:
        mount_stat = os.fstat(mount_fd)
        if config.platform == "linux" and _fd_mount_id(mount_fd) != initial.mount_id:
            raise StorageError("mounted_directory_replaced")
        if (mount_stat.st_dev, mount_stat.st_ino) != (initial.mount_device, initial.mount_inode):
            raise StorageError("mounted_directory_replaced")
        if not bootstrap:
            _verify_root_at(mount_fd, config)
        lock_created = False
        try:
            lock_fd = os.open(
                LOCK_NAME,
                os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW,
                0o600,
                dir_fd=mount_fd,
            )
            lock_created = True
        except FileExistsError:
            lock_fd = os.open(LOCK_NAME, os.O_RDWR | os.O_NOFOLLOW, dir_fd=mount_fd)
        lock_stat = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(lock_stat.st_mode)
            or lock_stat.st_uid != os.getuid()
            or stat.S_IMODE(lock_stat.st_mode) != 0o600
            or lock_stat.st_nlink != 1
        ):
            raise StorageError("unsafe_writer_lock")
        if lock_created:
            os.fsync(mount_fd)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        guard = StorageGuard(
            config_path,
            config,
            initial,
            mount_fd,
            lock_fd,
            probe,
            bootstrap=bootstrap,
            min_free_bytes=min_free_bytes,
        )
        guard.revalidate(min_free_bytes=min_free_bytes)
        yield guard
    finally:
        if lock_fd >= 0:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        os.close(mount_fd)


def writer_guard(
    config_path: Path,
    *,
    min_free_bytes: int | None = None,
    probe: VolumeProbe | None = None,
) -> contextlib.AbstractContextManager[StorageGuard]:
    """Lock a previously initialized root; marker and directory must already be valid."""
    return _locked_guard(
        config_path,
        min_free_bytes=min_free_bytes,
        probe=probe,
        bootstrap=False,
    )


def bootstrap_guard(
    config_path: Path,
    *,
    probe: VolumeProbe | None = None,
) -> contextlib.AbstractContextManager[StorageGuard]:
    """Lock only the host binding while create_root initializes the new root."""
    return _locked_guard(config_path, min_free_bytes=None, probe=probe, bootstrap=True)


def configure(
    config_path: Path,
    mount_path: Path,
    data_root: str,
    *,
    min_free_bytes: int,
    volume_uuid: str | None = None,
    probe: VolumeProbe | None = None,
    platform: str = "macos",
) -> StorageConfig:
    """Write a create-only owner-only binding after checking the mounted volume."""
    _validate_relative(data_root, "data_root")
    min_free_bytes = _validate_min_free(min_free_bytes)
    if not config_path.is_absolute() or "\x00" in str(config_path):
        raise StorageError("invalid_config_path")
    _require_plain_path(config_path.parent)
    if not mount_path.is_absolute() or "\x00" in str(mount_path) or ".." in mount_path.parts:
        raise StorageError("invalid_mount_path")
    _require_plain_path(mount_path)
    if config_path.exists() or config_path.is_symlink():
        raise StorageError("config_already_exists")
    if platform not in {"macos", "linux"}:
        raise StorageError("unsupported_storage_platform")
    if platform == "linux" and sys.platform != "linux" and probe is None:
        raise StorageError("unsupported_host: Linux storage config requires Linux")
    volume_probe = probe or (LinuxExt4VolumeProbe() if platform == "linux" else MacOSVolumeProbe())
    if volume_uuid is None:
        info = volume_probe.inspect(mount_path)
        volume_uuid = info.volume_uuid
    volume_uuid = _validate_uuid(volume_uuid)
    version = LINUX_CONFIG_VERSION if platform == "linux" else CONFIG_VERSION
    filesystem = "ext4" if platform == "linux" else "apfs"
    config = StorageConfig(
        secrets.token_hex(16),
        volume_uuid,
        mount_path,
        data_root,
        min_free_bytes,
        version,
        platform,
        filesystem,
    )
    qualify_volume(
        mount_path,
        volume_uuid,
        min_free_bytes=min_free_bytes,
        probe=volume_probe,
        platform=platform,
    )
    if platform == "linux":
        mount_fd = _open_directory(mount_path)
        try:
            _ensure_config_parent_on_other_device(config_path.parent, os.fstat(mount_fd).st_dev)
        finally:
            os.close(mount_fd)
    parent_fd = _make_private_directory(config_path.parent)
    try:
        fd = os.open(
            config_path.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        try:
            os.fchmod(fd, 0o600)
            payload = json.dumps(config.to_json(), sort_keys=True, separators=(",", ":")) + "\n"
            _write_all(fd, payload.encode("utf-8"))
            os.fsync(fd)
            st = os.fstat(fd)
            if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1:
                raise StorageError("unsafe_config_permissions")
        finally:
            os.close(fd)
        os.fsync(parent_fd)
        current_parent_fd = _open_absolute_directory(config_path.parent)
        try:
            pinned_parent_stat = os.fstat(parent_fd)
            current_parent_stat = os.fstat(current_parent_fd)
            if (pinned_parent_stat.st_dev, pinned_parent_stat.st_ino) != (
                current_parent_stat.st_dev,
                current_parent_stat.st_ino,
            ):
                raise StorageError("config_parent_replaced")
        finally:
            os.close(current_parent_fd)
        if StorageConfig.load(config_path) != config:
            raise StorageError("config_binding_changed_after_write")
    finally:
        os.close(parent_fd)
    return config


def bind_existing(
    config_path: Path,
    mount_path: Path,
    data_root: str,
    *,
    expected_volume_uuid: str,
    expected_root_id: str,
    min_free_bytes: int,
    probe: VolumeProbe | None = None,
) -> StorageConfig:
    """Bind an already existing root from trusted recovery identity pins.

    This performs read-only volume/root checks. Its only write is a create-only
    host config file outside the mounted volume; it never creates a volume probe,
    root directory, lock, or marker.
    """
    _validate_relative(data_root, "data_root")
    minimum = _validate_min_free(min_free_bytes)
    volume_uuid = _validate_uuid(expected_volume_uuid)
    if (
        not isinstance(expected_root_id, str)
        or len(expected_root_id) != 32
        or any(char not in "0123456789abcdef" for char in expected_root_id)
    ):
        raise StorageError("invalid_expected_root_id")
    if not config_path.is_absolute() or "\x00" in str(config_path):
        raise StorageError("invalid_config_path")
    if (
        ".." in config_path.parts
        or Path(os.path.normpath(config_path)) != config_path
        or config_path.name in {"", ".", ".."}
    ):
        raise StorageError("invalid_config_path")
    if (
        not mount_path.is_absolute()
        or "\x00" in str(mount_path)
        or ".." in mount_path.parts
        or Path(os.path.normpath(mount_path)) != mount_path
    ):
        raise StorageError("invalid_mount_path")
    _require_plain_path(config_path.parent)
    _mount_is_plain(mount_path)
    try:
        config_path.relative_to(mount_path)
    except ValueError:
        pass
    else:
        raise StorageError("config_must_be_outside_bound_volume")
    if config_path.exists() or config_path.is_symlink():
        raise StorageError("config_already_exists")

    config = StorageConfig(expected_root_id, volume_uuid, mount_path, data_root, minimum)
    volume_probe = probe or MacOSVolumeProbe()
    first_volume = volume_probe.inspect(mount_path)
    _validate_volume_policy(first_volume, mount_path)
    if first_volume.volume_uuid.lower() != volume_uuid:
        raise StorageError("volume_uuid_mismatch")
    if first_volume.available_bytes < minimum:
        raise StorageError("insufficient_free_space")
    if not os.access(mount_path, os.W_OK):
        raise StorageError("volume_not_writable")

    mount_fd = _open_directory(mount_path)
    root_fd = marker_fd = -1
    try:
        mount_before = os.fstat(mount_fd)
        _ensure_config_parent_on_other_device(config_path.parent, mount_before.st_dev)
        root_fd = _open_relative_directory(mount_fd, data_root)
        root_before = os.fstat(root_fd)
        try:
            marker_fd = os.open(
                ROOT_MARKER,
                os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
        except FileNotFoundError as exc:
            raise StorageError("root_marker_unavailable") from exc
        except OSError as exc:
            raise StorageError("unsafe_root_marker") from exc
        marker_before = os.fstat(marker_fd)
        _read_marker_fd(marker_fd, config)

        second_volume = volume_probe.inspect(mount_path)
        _validate_volume_policy(second_volume, mount_path)
        if second_volume.volume_uuid.lower() != volume_uuid:
            raise StorageError("volume_uuid_mismatch")
        if second_volume.available_bytes < minimum:
            raise StorageError("insufficient_free_space")
        current_mount_fd = _open_directory(mount_path)
        try:
            current_mount = os.fstat(current_mount_fd)
            if (mount_before.st_dev, mount_before.st_ino) != (
                current_mount.st_dev,
                current_mount.st_ino,
            ):
                raise StorageError("mounted_directory_replaced")
            current_root_fd = _open_relative_directory(current_mount_fd, data_root)
            try:
                current_root = os.fstat(current_root_fd)
                if (root_before.st_dev, root_before.st_ino) != (
                    current_root.st_dev,
                    current_root.st_ino,
                ):
                    raise StorageError("research_root_replaced")
                try:
                    current_marker_fd = os.open(
                        ROOT_MARKER,
                        os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                        dir_fd=current_root_fd,
                    )
                except FileNotFoundError as exc:
                    raise StorageError("root_marker_unavailable") from exc
                except OSError as exc:
                    raise StorageError("unsafe_root_marker") from exc
                try:
                    current_marker = os.fstat(current_marker_fd)
                    if (marker_before.st_dev, marker_before.st_ino) != (
                        current_marker.st_dev,
                        current_marker.st_ino,
                    ):
                        raise StorageError("root_marker_replaced")
                    _read_marker_fd(current_marker_fd, config)
                finally:
                    os.close(current_marker_fd)
            finally:
                os.close(current_root_fd)
        finally:
            os.close(current_mount_fd)

        parent_fd = _make_private_directory(config_path.parent)
        try:
            _ensure_config_parent_on_other_device(config_path.parent, mount_before.st_dev)
            config_fd = os.open(
                config_path.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            try:
                os.fchmod(config_fd, 0o600)
                payload = json.dumps(config.to_json(), sort_keys=True, separators=(",", ":"))
                _write_all(config_fd, (payload + "\n").encode("utf-8"))
                os.fsync(config_fd)
                written = os.fstat(config_fd)
                if (
                    written.st_uid != os.getuid()
                    or stat.S_IMODE(written.st_mode) != 0o600
                    or written.st_nlink != 1
                ):
                    raise StorageError("unsafe_config_permissions")
            finally:
                os.close(config_fd)
            os.fsync(parent_fd)
            current_parent_fd = _open_absolute_directory(config_path.parent)
            try:
                if (os.fstat(parent_fd).st_dev, os.fstat(parent_fd).st_ino) != (
                    os.fstat(current_parent_fd).st_dev,
                    os.fstat(current_parent_fd).st_ino,
                ):
                    raise StorageError("config_parent_replaced")
            finally:
                os.close(current_parent_fd)
        finally:
            os.close(parent_fd)
    except OSError as exc:
        raise StorageError(f"existing_root_binding_failed: {exc}") from exc
    finally:
        if marker_fd >= 0:
            os.close(marker_fd)
        if root_fd >= 0:
            os.close(root_fd)
        os.close(mount_fd)

    if StorageConfig.load(config_path) != config:
        raise StorageError("config_binding_changed_after_write")
    verify_root(config_path, min_free_bytes=minimum, probe=probe)
    return config


def create_root(config_path: Path, *, probe: VolumeProbe | None = None) -> ResolvedRoot:
    """Create the configured data root exactly once, with a bound immutable marker."""
    with bootstrap_guard(config_path, probe=probe) as guard:
        mount_fd = guard.mount_fd
        current_fd = os.dup(mount_fd)
        try:
            parts = guard.config.data_root.split("/")
            for index, part in enumerate(parts):
                is_root = index == len(parts) - 1
                created = False
                try:
                    os.mkdir(part, 0o700, dir_fd=current_fd)
                    os.fsync(current_fd)
                    created = True
                except FileExistsError as exc:
                    if is_root:
                        raise StorageError("research_root_already_exists") from exc
                os.fsync(current_fd)
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current_fd,
                )
                if created or is_root:
                    os.fchmod(next_fd, 0o700)
                st = os.fstat(next_fd)
                if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
                    os.close(next_fd)
                    raise StorageError("unsafe_root_owner_or_mode")
                os.close(current_fd)
                current_fd = next_fd
            if guard.config.platform == "linux":
                _probe_case_sensitive_names(current_fd)
            marker_fd = os.open(
                ROOT_MARKER,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=current_fd,
            )
            try:
                os.fchmod(marker_fd, 0o600)
                body = {
                    "version": 1,
                    "root_id": guard.config.root_id,
                    "volume_uuid": guard.config.volume_uuid,
                    "data_root": guard.config.data_root,
                }
                _write_all(marker_fd, (json.dumps(body, sort_keys=True) + "\n").encode())
                os.fsync(marker_fd)
            finally:
                os.close(marker_fd)
            os.fsync(current_fd)
            guard.revalidate()
            _verify_root_at(mount_fd, guard.config)
            return guard.root
        except OSError as exc:
            raise StorageError(f"root_creation_failed: {exc}") from exc
        finally:
            os.close(current_fd)


def verify_root(
    config_path: Path,
    *,
    min_free_bytes: int | None = None,
    probe: VolumeProbe | None = None,
) -> ResolvedRoot:
    config = StorageConfig.load(config_path)
    resolved = inspect_config(config, min_free_bytes=min_free_bytes, probe=probe)
    mount_fd = _open_directory(config.mount_path)
    try:
        mount_stat = os.fstat(mount_fd)
        if config.platform == "linux" and _fd_mount_id(mount_fd) != resolved.mount_id:
            raise StorageError("mounted_directory_replaced")
        if (mount_stat.st_dev, mount_stat.st_ino) != (
            resolved.mount_device,
            resolved.mount_inode,
        ):
            raise StorageError("mounted_directory_replaced")
        _verify_root_at(mount_fd, config)
        return resolved
    finally:
        os.close(mount_fd)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short_write")
        view = view[written:]
