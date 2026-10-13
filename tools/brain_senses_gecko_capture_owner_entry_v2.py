"""Source-derived live activation gate for Gecko capture activation-v2.

The entry accepts only an opaque activation ID and already-open descriptor-bound
storage objects. It obtains launchd state itself and derives every expectation
from the create-only activation record before exposing a verified activation.
It does not create, load, dispatch, or contact a provider.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from tools import brain_senses_external_clock_v1 as clock
from tools import brain_senses_gecko_capture_activation_v1 as activation_v1
from tools import brain_senses_gecko_capture_activation_v2 as activation_v2
from tools import brain_senses_gecko_capture_owner_host_v2 as owner_host
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json

MAX_PLIST_BYTES = 256 * 1024


class OwnerEntryError(ValueError):
    """Live activation identity, source, clock, or storage check failed."""


def _parse_loaded_print(raw: str, *, label: str) -> dict[str, Any]:
    """Parse the exact loaded definition while allowing an idle watchdog with no PID."""
    lines = raw.splitlines()
    if not lines or lines[0].strip() != f"{owner_host.LAUNCHD_DOMAIN}/{label} = {{":
        raise OwnerEntryError("owner_entry_print_header")
    paths: list[str] = []
    pids: list[int] = []
    args: list[str] = []
    in_args = False
    saw_args = False
    for raw_line in lines[1:]:
        line = raw_line.strip()
        if line == "arguments = {":
            if in_args or saw_args:
                raise OwnerEntryError("owner_entry_print_arguments")
            in_args = True
            saw_args = True
            continue
        if in_args and line == "}":
            in_args = False
            continue
        if in_args:
            match = re.fullmatch(r"[0-9]+ = (.+)", line)
            if match is None or int(line.split(" = ", 1)[0]) != len(args):
                raise OwnerEntryError("owner_entry_print_argument_row")
            try:
                token = shlex.split(match.group(1), posix=True)
            except ValueError as exc:
                raise OwnerEntryError("owner_entry_print_argument_token") from exc
            if len(token) != 1:
                raise OwnerEntryError("owner_entry_print_argument_token")
            args.append(token[0])
            continue
        path_match = re.fullmatch(r"path = (.+)", line)
        if path_match:
            paths.append(path_match.group(1))
        pid_match = re.fullmatch(r"pid = ([0-9]+)", line)
        if pid_match:
            pids.append(int(pid_match.group(1)))
    if in_args or not saw_args or len(paths) != 1 or len(pids) > 1 or not args:
        raise OwnerEntryError("owner_entry_print_fields")
    if pids and pids[0] <= 0:
        raise OwnerEntryError("owner_entry_print_pid")
    return {"plist_path": paths[0], "pid": pids[0] if pids else None, "program_arguments": args}


def _read_absolute_regular(path: str) -> bytes:
    if (
        type(path) is not str
        or not path.startswith("/")
        or any(part in {"", ".", ".."} for part in path[1:].split("/"))
    ):
        raise OwnerEntryError("owner_entry_loaded_path_invalid")
    current = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    file_fd = -1
    try:
        parts = path[1:].split("/")
        for part in parts[:-1]:
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = next_fd
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
        before = os.fstat(file_fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_size < 0
            or before.st_size > MAX_PLIST_BYTES
        ):
            raise OwnerEntryError("owner_entry_loaded_plist_untrusted")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(file_fd, min(65536, MAX_PLIST_BYTES + 1 - total)):
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_PLIST_BYTES:
                raise OwnerEntryError("owner_entry_loaded_plist_too_large")
        after = os.fstat(file_fd)
        if len(b"".join(chunks)) != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise OwnerEntryError("owner_entry_loaded_plist_changed")
        return b"".join(chunks)
    except OSError as exc:
        raise OwnerEntryError("owner_entry_loaded_plist_unavailable") from exc
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        os.close(current)


def _source_manifest_from_record(record: dict[str, Any], repo_root: Path) -> bytes:
    source_map = activation_v1._safe_source_map(cast(dict[str, str], record.get("source_map")))
    source_commit = cast(str, record.get("source_commit"))
    activation_v1.verify_source_commit(
        repo_root=repo_root, source_commit=source_commit, source_map=source_map
    )
    sources: dict[str, dict[str, Any]] = {}
    for path, digest in source_map.items():
        raw = activation_v1._read_source_nofollow(repo_root, path)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise OwnerEntryError("owner_entry_source_hash_mismatch")
        sources[path] = {"bytes": len(raw), "sha256": digest}
    core = {
        "schema": "brain-senses-gecko-capture-source-manifest-v2",
        "schema_version": 2,
        "source_commit": source_commit,
        "source_paths": sorted(source_map),
        "sources": sources,
    }
    return canonical_json(
        {
            **core,
            "manifest_sha256": hashlib.sha256(canonical_json(core)).hexdigest(),
        }
    )


def _query_loaded_services(
    *, record: dict[str, Any], runner: Callable[..., subprocess.CompletedProcess[str]]
) -> dict[str, Any]:
    collector = cast(dict[str, Any], record["collector"])
    watchdog = cast(dict[str, Any], record["watchdog"])
    expected = {
        cast(str, collector["label"]): collector,
        cast(str, watchdog["label"]): watchdog,
    }
    try:
        listed = runner(
            ["launchctl", "list"],
            check=False,
            capture_output=True,
            text=True,
            timeout=owner_host.LAUNCHD_LIST_TIMEOUT_SECONDS,
        )
        rows = owner_host._parse_launchctl_list(
            owner_host._bounded_text(listed, "entry_list_failed")
        )
        gecko_labels = {
            label for label in rows if label.startswith("ai.memecoin.brain.senses.gecko-")
        }
        if gecko_labels != set(expected):
            raise OwnerEntryError("owner_entry_loaded_label_set_mismatch")
        loaded: list[dict[str, Any]] = []
        loaded_pids: dict[str, int | None] = {}
        for label in sorted(expected):
            definition = expected[label]
            pid, _status = rows[label]
            is_collector = label == cast(str, collector["label"])
            if is_collector and (pid is None or pid != os.getpid()):
                raise OwnerEntryError("owner_entry_collector_pid_mismatch")
            printed = runner(
                ["launchctl", "print", f"{owner_host.LAUNCHD_DOMAIN}/{label}"],
                check=False,
                capture_output=True,
                text=True,
                timeout=owner_host.LAUNCHD_PRINT_TIMEOUT_SECONDS,
            )
            fields = _parse_loaded_print(
                owner_host._bounded_text(printed, "entry_print_failed"), label=label
            )
            if (
                fields["pid"] != pid
                or fields["plist_path"] != definition["loaded_path"]
                or fields["program_arguments"] != definition["program_arguments"]
            ):
                raise OwnerEntryError("owner_entry_loaded_definition_mismatch")
            if pid is not None:
                owner = runner(
                    ["ps", "-o", "uid=", "-p", str(pid)],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=owner_host.LAUNCHD_PRINT_TIMEOUT_SECONDS,
                )
                owner_text = owner_host._bounded_text(owner, "entry_uid_failed").strip()
                if not owner_text.isdigit() or int(owner_text) != os.getuid():
                    raise OwnerEntryError("owner_entry_loaded_uid_mismatch")
            loaded.append(
                {
                    "label": label,
                    "plist_path": definition["loaded_path"],
                    "program_arguments": definition["program_arguments"],
                    "uid": os.getuid(),
                }
            )
            loaded_pids[label] = pid
        return {"loaded_services": loaded, "loaded_pids": loaded_pids}
    except OwnerEntryError:
        raise
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError) as exc:
        raise OwnerEntryError("owner_entry_launchd_unavailable") from exc


def verify_live_activation_v2(
    *,
    writer: DescriptorWriter,
    activation_id: str,
    repo_root: Path,
    storage_guard: Any,
    data_root_fd: int,
    revalidate_storage: Callable[[], Any],
    clock_record: dict[str, Any],
    wall_ns: int,
    monotonic_ns: int,
    launchd_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> activation_v2.VerifiedActivationV2:
    """Verify record-derived launchd/storage/source/context expectations before dispatch."""
    try:
        revalidate_storage()
        record_raw = writer.read_bytes(
            activation_v2.activation_path(activation_id), max_bytes=activation_v2.MAX_RECORD_BYTES
        )
        record = activation_v2._parse(record_raw)
        if record.get("activation_id") != activation_id:
            raise OwnerEntryError("owner_entry_activation_identity_mismatch")
        observed_storage = activation_v1._verified_guard_storage(
            guard=storage_guard,
            data_root_fd=data_root_fd,
            writer=writer,
            revalidate_storage=revalidate_storage,
        )
        if record.get("storage") != observed_storage:
            raise OwnerEntryError("owner_entry_storage_binding_mismatch")
        manifest_raw = _source_manifest_from_record(record, repo_root)
        collector = cast(dict[str, Any], record["collector"])
        watchdog = cast(dict[str, Any], record["watchdog"])
        collector_raw = _read_absolute_regular(cast(str, collector["loaded_path"]))
        watchdog_raw = _read_absolute_regular(cast(str, watchdog["loaded_path"]))
        snapshot = _query_loaded_services(record=record, runner=launchd_runner)
        clock.validate_progress(clock_record, wall_ns=wall_ns, monotonic_ns=monotonic_ns)
        context_raw = writer.read_bytes(
            cast(str, record["replay_context_path"]), max_bytes=2 * 1024 * 1024
        )
        result = activation_v2.validate_activation_record_v2(
            record_raw=record_raw,
            expected_activation_id=activation_id,
            replay_context_raw=context_raw,
            observed_now_ns=wall_ns,
            source_manifest_raw=manifest_raw,
            repo_root=repo_root,
            data_root_fd=data_root_fd,
            writer=writer,
            storage_guard=storage_guard,
            revalidate_storage=revalidate_storage,
            collector_plist_raw=collector_raw,
            watchdog_plist_raw=watchdog_raw,
            launch_snapshot=snapshot,
        )
        revalidate_storage()
        # Close the time-of-check window immediately before handing the verified binding
        # to the one-shot caller. The loaded process identities, installed plist bytes,
        # repository source bytes, and storage identity must still match the record.
        final_manifest = _source_manifest_from_record(record, repo_root)
        final_collector = _read_absolute_regular(cast(str, collector["loaded_path"]))
        final_watchdog = _read_absolute_regular(cast(str, watchdog["loaded_path"]))
        final_snapshot = _query_loaded_services(record=record, runner=launchd_runner)
        if (
            final_manifest != manifest_raw
            or final_collector != collector_raw
            or final_watchdog != watchdog_raw
            or final_snapshot != snapshot
        ):
            raise OwnerEntryError("owner_entry_live_identity_changed")
        revalidate_storage()
        return result
    except (activation_v2.ActivationV2Error, OwnerEntryError):
        raise
    except (OSError, TypeError, ValueError, KeyError) as exc:
        raise OwnerEntryError("owner_entry_live_gate_failed") from exc


__all__ = ["OwnerEntryError", "verify_live_activation_v2"]
