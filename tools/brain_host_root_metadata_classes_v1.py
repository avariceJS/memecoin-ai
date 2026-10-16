#!/usr/bin/env python3
"""Read-only, path-free metadata-class census for declared Darwin host roots.

This operator qualification tool reads lstat metadata, extended-attribute names,
ACL presence, and source flags only. It never reads file payloads or xattr values,
follows symlinks, opens databases, or publishes anything to Kingston. Opaque and
credential exclusions are lstat-only and are never descended.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import plistlib
import stat
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.brain_host_tree_custody import _list_xattrs_fd

SCHEMA = "brain-host-root-metadata-classes-v1"
NOFOLLOW_DIRECTORY = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
NOFOLLOW_FILE = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
NOFOLLOW_SYMLINK = os.O_RDONLY | getattr(os, "O_SYMLINK", 0)
PROTECTED_COMPONENTS = {"f01", "d43", "holdout", "reserved", "protected"}
SENSITIVE_COMPONENTS = {".ssh", "keychains", "credentials", "secrets", "wallets"}


class MetadataCensusError(ValueError):
    """The private metadata-class scan could not establish a bounded view."""


def _sig(st: os.stat_result) -> tuple[int, ...]:
    return (
        st.st_dev,
        st.st_ino,
        st.st_mode,
        st.st_nlink,
        st.st_uid,
        st.st_gid,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
        getattr(st, "st_flags", 0),
    )


def _open_abs_dir(path: Path) -> int:
    if not path.is_absolute() or ".." in path.parts:
        raise MetadataCensusError("root_path_invalid")
    fd = os.open("/", NOFOLLOW_DIRECTORY)
    try:
        for name in path.parts[1:]:
            child = os.open(name, NOFOLLOW_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise MetadataCensusError("root_kind_invalid")
        result = fd
        fd = -1
        return result
    except OSError as exc:
        raise MetadataCensusError("root_open_failed") from exc
    finally:
        if fd >= 0:
            os.close(fd)


def _source_volume(mountpoint: Path, expected_uuid: str, device: int) -> dict[str, Any]:
    try:
        raw = subprocess.run(
            ["/usr/sbin/diskutil", "info", "-plist", str(mountpoint)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
        ).stdout
        info = plistlib.loads(raw)
    except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException) as exc:
        raise MetadataCensusError("source_volume_probe_failed") from exc
    uuid = info.get("VolumeUUID")
    current_mount = info.get("MountPoint")
    try:
        mounted_device = os.stat(mountpoint).st_dev
    except OSError as exc:
        raise MetadataCensusError("source_mount_missing") from exc
    if (
        not isinstance(uuid, str)
        or uuid.lower() != expected_uuid.lower()
        or current_mount != str(mountpoint)
        or mounted_device != device
    ):
        raise MetadataCensusError("source_volume_identity_mismatch")
    return {"volume_uuid": uuid.lower(), "mount_point": current_mount, "device": device}


def _acl_fd_present(fd: int) -> bool:
    library = "/usr/lib/libSystem.B.dylib"
    try:
        libc = ctypes.CDLL(library, use_errno=True)
        function = libc.acl_get_fd_np
        function.argtypes = [ctypes.c_int, ctypes.c_int]
        function.restype = ctypes.c_void_p
        ctypes.set_errno(0)
        acl = function(fd, 0x00000100)
        if acl:
            libc.acl_free.argtypes = [ctypes.c_void_p]
            libc.acl_free(acl)
            return True
        error = ctypes.get_errno()
        if error not in {errno.ENOENT, getattr(errno, "ENOATTR", errno.ENODATA)}:
            raise MetadataCensusError("acl_probe_failed")
        return False
    except MetadataCensusError:
        raise
    except Exception as exc:
        raise MetadataCensusError("acl_probe_unavailable") from exc


def _acl_link_present(parent_fd: int, name: str) -> bool:
    # /dev/fd anchors the parent; ACL_TYPE_EXTENDED is queried on the link itself.
    path = os.fsencode(f"/dev/fd/{parent_fd}/") + os.fsencode(name)
    before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        function = libc.acl_get_link_np
        function.argtypes = [ctypes.c_char_p, ctypes.c_int]
        function.restype = ctypes.c_void_p
        ctypes.set_errno(0)
        acl = function(path, 0x00000100)
        if acl:
            libc.acl_free.argtypes = [ctypes.c_void_p]
            libc.acl_free(acl)
            present = True
        else:
            error = ctypes.get_errno()
            if error not in {errno.ENOENT, getattr(errno, "ENOATTR", errno.ENODATA)}:
                raise MetadataCensusError("acl_link_probe_failed")
            present = False
        after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if _sig(before) != _sig(after) or not stat.S_ISLNK(after.st_mode):
            raise MetadataCensusError("acl_link_identity_changed")
        return present
    except MetadataCensusError:
        raise
    except Exception as exc:
        raise MetadataCensusError("acl_link_probe_unavailable") from exc


def _xattr_names_fd(fd: int) -> list[bytes]:
    try:
        return sorted(_list_xattrs_fd(fd))
    except OSError as exc:
        if exc.errno in {errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}:
            return []
        raise MetadataCensusError("xattr_name_probe_failed") from exc


def _root_exclusions(role: dict[str, Any]) -> tuple[set[str], bool]:
    exact = {
        item["path"]
        for item in role.get("metadata_only_exclusions", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    return exact, role.get("expected_kind", "directory") == "directory"


def _walk_root(role: dict[str, Any], source_device: int) -> dict[str, Any]:
    if (
        not isinstance(role.get("key"), str)
        or not role["key"]
        or not isinstance(role.get("path"), str)
        or not Path(role["path"]).is_absolute()
        or ".." in Path(role["path"]).parts
        or role.get("expected_kind", "directory") not in {"directory", "file"}
    ):
        raise MetadataCensusError("root_declaration_invalid")
    key = role["key"]
    path = Path(role["path"])
    digest = hashlib.sha256()
    counts: Counter[str] = Counter()
    xattrs: Counter[str] = Counter()
    flags: Counter[str] = Counter()
    acl_counts: Counter[str] = Counter()
    hardlinks: dict[tuple[int, int], tuple[int, int]] = {}
    total_bytes = largest_file = 0
    skipped: Counter[str] = Counter()
    exact_exclusions, is_tree = _root_exclusions(role)

    def add(rel: str, kind: str, st: os.stat_result, names: list[bytes], acl: bool) -> None:
        nonlocal total_bytes, largest_file
        counts[kind] += 1
        if kind == "regular_file":
            total_bytes += st.st_size
            largest_file = max(largest_file, st.st_size)
            if st.st_nlink > 1:
                ident = (st.st_dev, st.st_ino)
                old_count, old_nlink = hardlinks.get(ident, (0, st.st_nlink))
                if old_nlink != st.st_nlink:
                    raise MetadataCensusError("hardlink_identity_drift")
                hardlinks[ident] = (old_count + 1, old_nlink)
        names_text: list[str] = []
        for name in names:
            try:
                names_text.append(name.decode("utf-8", "strict"))
            except UnicodeDecodeError:
                names_text.append("sha256:" + hashlib.sha256(name).hexdigest())
        xattrs.update(names_text)
        flags[str(getattr(st, "st_flags", 0))] += 1
        acl_counts["present" if acl else "no_acl_returned"] += 1
        row = {
            "path": rel,
            "kind": kind,
            "stat": list(_sig(st)),
            "xattr_names": names_text,
            "acl": acl,
        }
        digest.update(json.dumps(row, sort_keys=True, separators=(",", ":")).encode())
        digest.update(b"\n")

    if not is_tree:
        parent_fd = _open_abs_dir(path.parent)
        try:
            before = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            fd = os.open(path.name, NOFOLLOW_FILE, dir_fd=parent_fd)
            try:
                st = os.fstat(fd)
                if (
                    _sig(before) != _sig(st)
                    or st.st_dev != source_device
                    or not stat.S_ISREG(st.st_mode)
                ):
                    raise MetadataCensusError("file_root_identity_changed")
                names = _xattr_names_fd(fd)
                acl = _acl_fd_present(fd)
                after = os.fstat(fd)
                if _sig(st) != _sig(after):
                    raise MetadataCensusError("file_root_metadata_changed")
                add("", "regular_file", st, names, acl)
            finally:
                os.close(fd)
            endpoint_parent = _open_abs_dir(path.parent)
            try:
                held_parent = os.fstat(parent_fd)
                current_parent = os.fstat(endpoint_parent)
                if (held_parent.st_dev, held_parent.st_ino) != (
                    current_parent.st_dev,
                    current_parent.st_ino,
                ):
                    raise MetadataCensusError("file_root_parent_endpoint_changed")
                endpoint = os.stat(path.name, dir_fd=endpoint_parent, follow_symlinks=False)
                if _sig(before) != _sig(endpoint) or not stat.S_ISREG(endpoint.st_mode):
                    raise MetadataCensusError("file_root_path_endpoint_changed")
            finally:
                os.close(endpoint_parent)
        finally:
            os.close(parent_fd)
    else:
        root_fd = _open_abs_dir(path)
        try:
            root_stat = os.fstat(root_fd)
            if root_stat.st_dev != source_device:
                raise MetadataCensusError("root_device_mismatch")
            add("", "directory", root_stat, _xattr_names_fd(root_fd), _acl_fd_present(root_fd))

            def walk(dir_fd: int, prefix: str) -> None:
                try:
                    with os.scandir(dir_fd) as iterator:
                        names = sorted(entry.name for entry in iterator)
                except OSError as exc:
                    raise MetadataCensusError("directory_scan_failed") from exc
                for name in names:
                    rel = f"{prefix}/{name}" if prefix else name
                    try:
                        before = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                    except OSError as exc:
                        raise MetadataCensusError("entry_stat_failed") from exc
                    if before.st_dev != source_device:
                        counts["cross_device_unresolved"] += 1
                        digest.update(
                            json.dumps([rel, "cross_device", list(_sig(before))]).encode()
                        )
                        digest.update(b"\n")
                        continue
                    if name == ".env.local":
                        skipped["credential_reissue_lstat_only"] += 1
                        digest.update(
                            json.dumps([rel, "excluded_env_local", list(_sig(before))]).encode()
                        )
                        digest.update(b"\n")
                        continue
                    if rel in exact_exclusions or name == "wallets":
                        skipped["opaque_backup_required_lstat_only"] += 1
                        digest.update(
                            json.dumps([rel, "excluded_opaque", list(_sig(before))]).encode()
                        )
                        digest.update(b"\n")
                        continue
                    components = {part.lower() for part in Path(rel).parts}
                    if components & PROTECTED_COMPONENTS:
                        skipped["protected_role_lstat_only"] += 1
                        digest.update(
                            json.dumps([rel, "excluded_protected", list(_sig(before))]).encode()
                        )
                        digest.update(b"\n")
                        continue
                    if components & SENSITIVE_COMPONENTS:
                        skipped["sensitive_role_lstat_only"] += 1
                        digest.update(
                            json.dumps([rel, "excluded_sensitive", list(_sig(before))]).encode()
                        )
                        digest.update(b"\n")
                        continue
                    if stat.S_ISDIR(before.st_mode):
                        kind = "directory"
                        child_fd = os.open(name, NOFOLLOW_DIRECTORY, dir_fd=dir_fd)
                        try:
                            st = os.fstat(child_fd)
                            if _sig(before) != _sig(st):
                                raise MetadataCensusError("directory_identity_changed")
                            add(rel, kind, st, _xattr_names_fd(child_fd), _acl_fd_present(child_fd))
                            walk(child_fd, rel)
                            if _sig(st) != _sig(os.fstat(child_fd)):
                                raise MetadataCensusError("directory_changed_during_scan")
                        finally:
                            os.close(child_fd)
                    elif stat.S_ISREG(before.st_mode):
                        fd = os.open(name, NOFOLLOW_FILE, dir_fd=dir_fd)
                        try:
                            st = os.fstat(fd)
                            if _sig(before) != _sig(st):
                                raise MetadataCensusError("file_identity_changed")
                            add(rel, "regular_file", st, _xattr_names_fd(fd), _acl_fd_present(fd))
                            if _sig(st) != _sig(os.fstat(fd)):
                                raise MetadataCensusError("file_metadata_changed_during_scan")
                        finally:
                            os.close(fd)
                    elif stat.S_ISLNK(before.st_mode):
                        if not hasattr(os, "O_SYMLINK"):
                            raise MetadataCensusError("symlink_metadata_unavailable")
                        fd = os.open(name, NOFOLLOW_SYMLINK, dir_fd=dir_fd)
                        try:
                            st = os.fstat(fd)
                            if _sig(before) != _sig(st) or not stat.S_ISLNK(st.st_mode):
                                raise MetadataCensusError("symlink_identity_changed")
                            add(
                                rel,
                                "symlink",
                                st,
                                _xattr_names_fd(fd),
                                _acl_link_present(dir_fd, name),
                            )
                            if _sig(st) != _sig(os.fstat(fd)):
                                raise MetadataCensusError("symlink_metadata_changed_during_scan")
                        finally:
                            os.close(fd)
                    else:
                        counts["special"] += 1
                        digest.update(json.dumps([rel, "special", list(_sig(before))]).encode())
                        digest.update(b"\n")

            walk(root_fd, "")
            endpoint_fd = _open_abs_dir(path)
            try:
                endpoint = os.fstat(endpoint_fd)
                if _sig(root_stat) != _sig(endpoint):
                    raise MetadataCensusError("root_path_endpoint_changed")
            finally:
                os.close(endpoint_fd)
            if _sig(root_stat) != _sig(os.fstat(root_fd)):
                raise MetadataCensusError("root_changed_during_scan")
        finally:
            os.close(root_fd)
    unobserved_alias_groups = sum(
        1 for seen, link_total in hardlinks.values() if seen != link_total
    )
    return {
        "role": key,
        "entry_counts": dict(sorted(counts.items())),
        "xattr_name_counts": dict(sorted(xattrs.items())),
        "acl_counts": dict(sorted(acl_counts.items())),
        "source_flag_counts": dict(sorted(flags.items())),
        "regular_file_lstat_bytes": total_bytes,
        "largest_regular_file_lstat_bytes": largest_file,
        "hardlink_groups_with_unobserved_aliases": unobserved_alias_groups,
        "skipped_counts": dict(sorted(skipped.items())),
        "scan_sha256": digest.hexdigest(),
    }


def scan(roster_path: Path) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise MetadataCensusError("darwin_required")
    try:
        fd = os.open(roster_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise MetadataCensusError("roster_open_failed") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1:
            raise MetadataCensusError("roster_file_identity_invalid")
        raw = bytearray()
        while chunk := os.read(fd, min(65536, 1024 * 1024 + 1 - len(raw))):
            raw.extend(chunk)
            if len(raw) > 1024 * 1024:
                raise MetadataCensusError("roster_size_limit")
        if len(raw) != st.st_size or _sig(st) != _sig(os.fstat(fd)):
            raise MetadataCensusError("roster_changed_during_read")
    finally:
        os.close(fd)
    try:
        roster = json.loads(bytes(raw))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MetadataCensusError("roster_json_invalid") from exc
    if not isinstance(roster, dict) or not isinstance(roster.get("roles"), list):
        raise MetadataCensusError("roster_schema_invalid")
    source_mount = Path(roster.get("source_mount_point", ""))
    expected_uuid = roster.get("source_volume_uuid")
    if not source_mount.is_absolute() or not isinstance(expected_uuid, str):
        raise MetadataCensusError("source_pin_invalid")
    mount_st = os.stat(source_mount)
    volume = _source_volume(source_mount, expected_uuid, mount_st.st_dev)
    if any(not isinstance(role, dict) for role in roster["roles"]):
        raise MetadataCensusError("roster_role_invalid")
    role_keys = [role.get("key") for role in roster["roles"]]
    if any(not isinstance(key, str) or not key for key in role_keys) or len(set(role_keys)) != len(
        role_keys
    ):
        raise MetadataCensusError("roster_role_duplicate_or_invalid")
    present = [role for role in roster["roles"] if role.get("state") == "present"]
    results: list[dict[str, Any]] = []
    for role in present:
        results.append(_walk_root(role, mount_st.st_dev))
    volume_after = _source_volume(source_mount, expected_uuid, mount_st.st_dev)
    if volume_after != volume:
        raise MetadataCensusError("source_volume_changed_during_scan")
    return {
        "schema": SCHEMA,
        "source_volume": volume,
        "source_volume_revalidated_after_scan": True,
        "roster_sha256": hashlib.sha256(bytes(raw)).hexdigest(),
        "root_count": len(results),
        "roots": results,
        "metadata_values_read": False,
        "file_payloads_read": False,
        "symlinks_followed": False,
        "databases_opened": False,
        "protected_semantics_read": False,
        "kingston_written": False,
        "per_object_pre_post_metadata_stable": True,
        "common_source_point_established": False,
        "source_cutoff_established": False,
        "restore_ready": False,
        "safe_to_erase": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roster", type=Path, required=True)
    parser.add_argument("--format", choices=("json",), default="json")
    args = parser.parse_args()
    try:
        result = scan(args.roster)
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except (MetadataCensusError, OSError, ValueError) as exc:
        error = str(exc) if isinstance(exc, MetadataCensusError) else type(exc).__name__
        print(json.dumps({"schema": SCHEMA, "state": "blocked", "error": error}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
