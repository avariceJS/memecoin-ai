"""Source-bound, no-provider proof of the Gecko LaunchAgent Python handoff."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import plistlib
import re
import select
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

SCHEMA = "brain-senses-gecko-interpreter-handoff-v1"
VERSION = 1
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_MAX_RECEIPT = 1024 * 1024
_MAX_SYMLINKS = 16
_MAX_PINNED_EXECUTABLE = 512 * 1024 * 1024


class HandoffError(RuntimeError):
    """Fail-closed interpreter provenance or process-identity error."""


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _metadata(path: str, *, owner_uid: int) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != owner_uid
            or before.st_nlink != 1
            or before.st_mode & 0o022
            or before.st_size < 0
            or before.st_size > _MAX_PINNED_EXECUTABLE
        ):
            raise HandoffError("handoff_file_metadata")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
        after = os.fstat(fd)
        identity = _stat_identity(before)
        if identity != _stat_identity(after) or size != before.st_size:
            raise HandoffError("handoff_file_changed_during_hash")
        return {**identity, "sha256": digest.hexdigest()}
    finally:
        os.close(fd)


def _stat_identity(info: os.stat_result) -> dict[str, Any]:
    return {
        "dev": info.st_dev,
        "ino": info.st_ino,
        "mode": stat.S_IMODE(info.st_mode),
        "uid": info.st_uid,
        "gid": info.st_gid,
        "nlink": info.st_nlink,
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
    }


def _read_small_file(path: str, *, owner_uid: int) -> tuple[bytes, dict[str, Any]]:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != owner_uid
            or before.st_nlink != 1
            or before.st_mode & 0o022
            or before.st_size > _MAX_RECEIPT
        ):
            raise HandoffError("handoff_small_file_metadata")
        chunks: list[bytes] = []
        size = 0
        while size <= _MAX_RECEIPT:
            chunk = os.read(fd, min(65536, _MAX_RECEIPT + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        after = os.fstat(fd)
        raw = b"".join(chunks)
        if (
            size > _MAX_RECEIPT
            or size != before.st_size
            or _stat_identity(before) != _stat_identity(after)
        ):
            raise HandoffError("handoff_small_file_changed")
        return raw, {**_stat_identity(before), "sha256": sha256(raw)}
    finally:
        os.close(fd)


def _symlink_chain(path: str) -> tuple[str, list[dict[str, Any]]]:
    """Resolve a path while recording every symlink encountered in its chain."""
    current = os.path.abspath(path)
    hops: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _ in range(_MAX_SYMLINKS + 1):
        if current in seen:
            raise HandoffError("handoff_symlink_cycle")
        seen.add(current)
        parts = Path(current).parts
        prefix = parts[0]
        restart = False
        for index, part in enumerate(parts[1:], start=1):
            prefix = os.path.join(prefix, part)
            try:
                info = os.lstat(prefix)
            except OSError as exc:
                raise HandoffError("handoff_path_unavailable") from exc
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(prefix)
                hops.append(
                    {
                        "path": prefix,
                        "target": target,
                        "dev": info.st_dev,
                        "ino": info.st_ino,
                        "mode": stat.S_IMODE(info.st_mode),
                        "uid": info.st_uid,
                        "gid": info.st_gid,
                    }
                )
                if len(hops) > _MAX_SYMLINKS:
                    raise HandoffError("handoff_symlink_depth")
                replacement = (
                    target
                    if os.path.isabs(target)
                    else os.path.join(os.path.dirname(prefix), target)
                )
                remaining = parts[index + 1 :]
                current = os.path.normpath(os.path.join(replacement, *remaining))
                restart = True
                break
        if restart:
            continue
        resolved = os.path.realpath(current)
        if resolved != current:
            current = resolved
            continue
        return resolved, hops
    raise HandoffError("handoff_symlink_depth")


def _proc_pidpath(pid: int) -> str:
    if sys.platform != "darwin":
        raise HandoffError("handoff_proc_pidpath_unavailable")
    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        proc_pidpath = libproc.proc_pidpath
        proc_pidpath.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32)
        proc_pidpath.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(4096)
        result = proc_pidpath(pid, buffer, len(buffer))
        if result <= 0:
            raise HandoffError("handoff_proc_pidpath_failed")
        return os.fsdecode(buffer.value)
    except (AttributeError, OSError) as exc:
        raise HandoffError("handoff_proc_pidpath_unavailable") from exc


_PROBE_CODE = (
    "import json,sys,time; "
    "print(json.dumps({'executable':sys.executable,'prefix':sys.prefix,"
    "'base_prefix':sys.base_prefix,'version':list(sys.version_info[:3])}),flush=True); "
    "time.sleep(15)"
)


def _spawn_probe(program: str, pidpath: Callable[[int], str]) -> tuple[dict[str, Any], str]:
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "PYTHONNOUSERSITE": "1"}
    try:
        process = subprocess.Popen(
            [program, "-B", "-c", _PROBE_CODE],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env=env,
        )
    except OSError as exc:
        raise HandoffError("handoff_probe_spawn_failed") from exc
    try:
        assert process.stdout is not None
        ready, _, _ = select.select([process.stdout], [], [], 8.0)
        if not ready:
            raise HandoffError("handoff_probe_timeout")
        line = process.stdout.readline(_MAX_RECEIPT + 1)
        if len(line) > _MAX_RECEIPT or not line.endswith(b"\n"):
            raise HandoffError("handoff_probe_output")
        try:
            probe = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HandoffError("handoff_probe_output") from exc
        if not isinstance(probe, dict) or set(probe) != {
            "executable",
            "prefix",
            "base_prefix",
            "version",
        }:
            raise HandoffError("handoff_probe_output")
        path = pidpath(process.pid)
        return probe, path
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)
        if process.poll() is None:
            raise HandoffError("handoff_probe_child_survived")


def _expected_paths(program: str, probe: Mapping[str, Any], proc_path: str) -> dict[str, str]:
    cli_path, _ = _symlink_chain(program)
    prefix = probe.get("prefix")
    base_prefix = probe.get("base_prefix")
    executable = probe.get("executable")
    if not all(
        isinstance(value, str) and os.path.isabs(value)
        for value in (prefix, base_prefix, executable)
    ):
        raise HandoffError("handoff_probe_paths")
    cfg_path = os.path.join(os.path.dirname(os.path.dirname(program)), "pyvenv.cfg")
    app_path = os.path.join(str(base_prefix), "Resources/Python.app/Contents/MacOS/Python")
    app_resolved, _ = _symlink_chain(app_path)
    proc_resolved, _ = _symlink_chain(proc_path)
    if app_resolved != proc_resolved or cli_path != os.path.realpath(program):
        raise HandoffError("handoff_kernel_path_unexpected")
    return {
        "program": program,
        "cli_executable": cli_path,
        "venv_config": os.path.realpath(cfg_path),
        "python_app_executable": app_resolved,
        "sys_executable": executable,
        "sys_prefix": str(prefix),
        "sys_base_prefix": str(base_prefix),
    }


def _file_snapshot(paths: Mapping[str, str], *, owner_uid: int) -> dict[str, Any]:
    chain_program, program_hops = _symlink_chain(paths["program"])
    if chain_program != paths["cli_executable"]:
        raise HandoffError("handoff_cli_chain_changed")
    chain_app, app_hops = _symlink_chain(paths["python_app_executable"])
    if chain_app != paths["python_app_executable"]:
        raise HandoffError("handoff_app_chain_changed")
    return {
        "program_symlinks": program_hops,
        "python_app_symlinks": app_hops,
        "cli_executable": _metadata(paths["cli_executable"], owner_uid=owner_uid),
        "venv_config": _metadata(paths["venv_config"], owner_uid=owner_uid),
        "python_app_executable": _metadata(paths["python_app_executable"], owner_uid=owner_uid),
    }


def build_receipt(
    *,
    plist_path: str,
    expected_label: str,
    expected_argv: Sequence[str],
    owner_uid: int | None = None,
    pidpath: Callable[[int], str] = _proc_pidpath,
    spawn_probe: Callable[[str, Callable[[int], str]], tuple[dict[str, Any], str]] = _spawn_probe,
    observed_ns: int | None = None,
) -> dict[str, Any]:
    """Run a bounded no-provider probe of the exact plist interpreter and attest its handoff."""
    uid = os.getuid() if owner_uid is None else owner_uid
    plist_raw, plist_stat = _read_small_file(plist_path, owner_uid=uid)
    try:
        plist = plistlib.loads(plist_raw)
    except (plistlib.InvalidFileException, ValueError) as exc:
        raise HandoffError("handoff_plist_invalid") from exc
    if (
        not isinstance(plist, dict)
        or plist.get("Label") != expected_label
        or plist.get("ProgramArguments") != list(expected_argv)
        or not expected_argv
        or not isinstance(expected_argv[0], str)
        or not os.path.isabs(expected_argv[0])
    ):
        raise HandoffError("handoff_plist_binding")
    probe, proc_path = spawn_probe(expected_argv[0], pidpath)
    paths = _expected_paths(expected_argv[0], probe, proc_path)
    before = _file_snapshot(paths, owner_uid=uid)
    plist_digest = sha256(plist_raw)
    after = _file_snapshot(paths, owner_uid=uid)
    after_plist_raw, after_plist_stat = _read_small_file(plist_path, owner_uid=uid)
    if before != after or after_plist_raw != plist_raw or after_plist_stat != plist_stat:
        raise HandoffError("handoff_inputs_changed")
    version = probe.get("version")
    if (
        not isinstance(version, list)
        or len(version) != 3
        or any(type(part) is not int for part in version)
        or version[0] != 3
        or version[1] != 13
        or version[2] < 0
    ):
        raise HandoffError("handoff_python_version")
    return {
        "schema": SCHEMA,
        "schema_version": VERSION,
        "label": expected_label,
        "owner_uid": uid,
        "plist_path": os.path.realpath(plist_path),
        "plist_sha256": plist_digest,
        "plist_file": plist_stat,
        "program_arguments": list(expected_argv),
        "probe": {
            "sys_executable": probe["executable"],
            "sys_prefix": probe["prefix"],
            "sys_base_prefix": probe["base_prefix"],
            "python_version": version,
            "proc_pidpath": paths["python_app_executable"],
        },
        "paths": paths,
        "files": before,
        "observed_ns": time.time_ns() if observed_ns is None else observed_ns,
    }


def verify_receipt(
    receipt: Mapping[str, Any],
    *,
    plist_path: str,
    expected_label: str,
    expected_argv: Sequence[str],
    owner_uid: int,
    proc_path: str | None = None,
) -> None:
    expected_keys = {
        "schema",
        "schema_version",
        "label",
        "owner_uid",
        "plist_path",
        "plist_sha256",
        "plist_file",
        "program_arguments",
        "probe",
        "paths",
        "files",
        "observed_ns",
    }
    if not isinstance(receipt, Mapping) or set(receipt) != expected_keys:
        raise HandoffError("handoff_receipt_shape")
    if (
        receipt.get("schema") != SCHEMA
        or receipt.get("schema_version") != VERSION
        or receipt.get("label") != expected_label
        or receipt.get("owner_uid") != owner_uid
        or receipt.get("plist_path") != os.path.realpath(plist_path)
        or receipt.get("program_arguments") != list(expected_argv)
        or not _SHA.fullmatch(str(receipt.get("plist_sha256")))
        or type(receipt.get("observed_ns")) is not int
        or receipt["observed_ns"] <= 0
    ):
        raise HandoffError("handoff_receipt_binding")
    plist_raw, current_plist_file = _read_small_file(plist_path, owner_uid=owner_uid)
    if sha256(plist_raw) != receipt["plist_sha256"] or current_plist_file != receipt["plist_file"]:
        raise HandoffError("handoff_plist_changed")
    try:
        current_plist = plistlib.loads(plist_raw)
    except (plistlib.InvalidFileException, ValueError) as exc:
        raise HandoffError("handoff_plist_invalid") from exc
    if (
        not isinstance(current_plist, dict)
        or current_plist.get("Label") != expected_label
        or current_plist.get("ProgramArguments") != list(expected_argv)
    ):
        raise HandoffError("handoff_plist_binding")
    probe = receipt.get("probe")
    paths = receipt.get("paths")
    files = receipt.get("files")
    if (
        not isinstance(probe, Mapping)
        or not isinstance(paths, Mapping)
        or not isinstance(files, Mapping)
    ):
        raise HandoffError("handoff_receipt_shape")
    if (
        set(probe)
        != {
            "sys_executable",
            "sys_prefix",
            "sys_base_prefix",
            "python_version",
            "proc_pidpath",
        }
        or set(paths)
        != {
            "program",
            "cli_executable",
            "venv_config",
            "python_app_executable",
            "sys_executable",
            "sys_prefix",
            "sys_base_prefix",
        }
        or set(files)
        != {
            "program_symlinks",
            "python_app_symlinks",
            "cli_executable",
            "venv_config",
            "python_app_executable",
        }
    ):
        raise HandoffError("handoff_receipt_shape")
    version = probe.get("python_version")
    if (
        not isinstance(version, list)
        or len(version) != 3
        or any(type(part) is not int for part in version)
        or version[0] != 3
        or version[1] != 13
        or version[2] < 0
    ):
        raise HandoffError("handoff_python_version")
    expected_cli, expected_hops = _symlink_chain(expected_argv[0])
    expected_cfg = os.path.realpath(
        os.path.join(os.path.dirname(os.path.dirname(expected_argv[0])), "pyvenv.cfg")
    )
    expected_app = os.path.realpath(
        os.path.join(
            str(probe["sys_base_prefix"]),
            "Resources/Python.app/Contents/MacOS/Python",
        )
    )
    if (
        paths["program"] != expected_argv[0]
        or paths["cli_executable"] != expected_cli
        or paths["venv_config"] != expected_cfg
        or paths["python_app_executable"] != expected_app
        or files["program_symlinks"] != expected_hops
        or paths["sys_executable"] != probe["sys_executable"]
        or paths["sys_prefix"] != probe["sys_prefix"]
        or paths["sys_base_prefix"] != probe["sys_base_prefix"]
        or paths["python_app_executable"] != probe["proc_pidpath"]
        or proc_path is not None
        and proc_path != probe["proc_pidpath"]
    ):
        raise HandoffError("handoff_runtime_path_mismatch")
    current = _file_snapshot(paths, owner_uid=owner_uid)
    if current != files:
        raise HandoffError("handoff_executable_changed")


def load_receipt(path: str, *, owner_uid: int) -> tuple[dict[str, Any], bytes]:
    parent = os.path.dirname(os.path.abspath(path))
    try:
        parent_info = os.lstat(parent)
    except OSError as exc:
        raise HandoffError("handoff_receipt_parent_unavailable") from exc
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or stat.S_ISLNK(parent_info.st_mode)
        or parent_info.st_uid != owner_uid
        or stat.S_IMODE(parent_info.st_mode) != 0o700
    ):
        raise HandoffError("handoff_receipt_parent_metadata")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != owner_uid
            or stat.S_IMODE(st.st_mode) != 0o600
            or st.st_nlink != 1
            or st.st_size > _MAX_RECEIPT
        ):
            raise HandoffError("handoff_receipt_file_metadata")
        raw = b""
        while len(raw) <= _MAX_RECEIPT:
            chunk = os.read(fd, min(65536, _MAX_RECEIPT + 1 - len(raw)))
            if not chunk:
                break
            raw += chunk
        if len(raw) > _MAX_RECEIPT:
            raise HandoffError("handoff_receipt_too_large")
        after = os.fstat(fd)
        if _stat_identity(st) != _stat_identity(after) or len(raw) != st.st_size:
            raise HandoffError("handoff_receipt_changed_during_read")
    finally:
        os.close(fd)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HandoffError("handoff_receipt_invalid_json") from exc
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise HandoffError("handoff_receipt_not_canonical")
    return value, raw
