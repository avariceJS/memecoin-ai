"""Inert, source-bound owner-host input loader for Gecko capture v2.

Only read-only source/plist loading and launchd inspection are exposed here.
The descriptor publisher is create-only and requires an already-open writer;
it never installs a plist, starts a service, or authorizes dispatch.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from memecoin_research_storage import _open_relative_directory
from tools import brain_senses_gecko_capture_owner_preflight_v2 as preflight
from tools.brain_senses_external_writer_v1 import canonical_json

REPO_ROOT = Path(__file__).resolve().parents[1]
COLLECTOR_PLIST_REL = "ops/launchd/ai.memecoin.brain.senses.gecko-capture-only-v5.plist"
WATCHDOG_PLIST_REL = "ops/launchd/ai.memecoin.brain.senses.gecko-capture-watchdog-v3.plist"
WATCHDOG_PLIST_TEMPLATE_REL = WATCHDOG_PLIST_REL + ".template"
LAUNCHD_DOMAIN = f"gui/{os.getuid()}"
LAUNCHD_LIST_TIMEOUT_SECONDS = 5
LAUNCHD_PRINT_TIMEOUT_SECONDS = 5
MAX_LAUNCHD_OUTPUT_BYTES = 128 * 1024
LABEL_RE = re.compile(r"ai\.memecoin\.brain\.senses\.gecko-[a-z0-9-]+-v[0-9]+(?:-[0-9a-f]{32})?\Z")


class OwnerHostError(ValueError):
    """Owner-host input was missing, changed, or could not be verified."""


def _bounded_text(result: subprocess.CompletedProcess[str], reason: str) -> str:
    if (
        type(result.returncode) is not int
        or result.returncode != 0
        or type(result.stdout) is not str
        or len(result.stdout.encode("utf-8", errors="strict")) > MAX_LAUNCHD_OUTPUT_BYTES
    ):
        raise OwnerHostError(reason)
    return result.stdout


def load_fixed_host_definitions(
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Read exact checked-in collector/watchdog plists through no-follow fds."""
    collector = preflight._read_repo_file(repo_root, COLLECTOR_PLIST_REL)
    watchdog = preflight._read_repo_file(repo_root, WATCHDOG_PLIST_REL)
    collector_obj = preflight._plist_object(collector, "owner_collector_plist_invalid")
    watchdog_obj = preflight._plist_object(watchdog, "owner_watchdog_plist_invalid")
    if (
        collector_obj.get("StartInterval") != 12
        or watchdog_obj.get("StartInterval") != 15
        or watchdog_obj.get("KeepAlive") != {"SuccessfulExit": False}
        or watchdog_obj.get("RunAtLoad") is not True
        or watchdog_obj.get("Label") != "ai.memecoin.brain.senses.gecko-capture-watchdog-v3"
        or type(watchdog_obj.get("ProgramArguments")) is not list
        or len(watchdog_obj["ProgramArguments"]) != 4
        or not str(watchdog_obj["ProgramArguments"][2]).endswith(
            "/tools/brain_senses_gecko_capture_watchdog_v3.py"
        )
        or watchdog_obj["ProgramArguments"][3] != "--owner-adapter-required"
    ):
        raise OwnerHostError("owner_host_definition_cadence_invalid")
    return {
        "collector_path": COLLECTOR_PLIST_REL,
        "collector_bytes": collector,
        "collector_sha256": preflight.sha256(collector),
        "collector": collector_obj,
        "watchdog_path": WATCHDOG_PLIST_REL,
        "watchdog_bytes": watchdog,
        "watchdog_sha256": preflight.sha256(watchdog),
        "watchdog": watchdog_obj,
    }


def _parse_launchctl_list(raw: str) -> dict[str, tuple[int | None, int | None]]:
    rows: dict[str, tuple[int | None, int | None]] = {}
    lines = raw.splitlines()
    if not lines or not lines[0].strip().startswith("PID"):
        raise OwnerHostError("owner_launchctl_list_shape")
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 3:
            raise OwnerHostError("owner_launchctl_list_row")
        pid_text, status_text, label = fields
        if not label or label in rows:
            raise OwnerHostError("owner_launchctl_list_duplicate")
        try:
            pid = None if pid_text.strip() in {"", "-"} else int(pid_text)
            status = None if status_text.strip() in {"", "-"} else int(status_text)
        except ValueError as exc:
            raise OwnerHostError("owner_launchctl_list_number") from exc
        if pid is not None and pid <= 0:
            raise OwnerHostError("owner_launchctl_list_pid")
        rows[label] = (pid, status)
    return rows


def _parse_launchctl_print(raw: str, *, uid: int, label: str) -> dict[str, Any]:
    """Parse the narrow launchctl print fields used by the owner gate."""
    lines = raw.splitlines()
    if not lines or lines[0].strip() != f"{LAUNCHD_DOMAIN}/{label} = {{":
        raise OwnerHostError("owner_launchctl_print_header")
    path_values: list[str] = []
    pid_values: list[int] = []
    argument_values: list[str] = []
    in_arguments = False
    saw_argument_block = False
    for raw_line in lines[1:]:
        line = raw_line.strip()
        if line == "arguments = {":
            if in_arguments or saw_argument_block:
                raise OwnerHostError("owner_launchctl_print_arguments")
            in_arguments = True
            saw_argument_block = True
            continue
        if in_arguments and line == "}":
            in_arguments = False
            continue
        if in_arguments:
            match = re.fullmatch(r"[0-9]+ = (.+)", line)
            if match is None:
                raise OwnerHostError("owner_launchctl_print_argument_row")
            index = int(line.split(" = ", 1)[0])
            if index != len(argument_values):
                raise OwnerHostError("owner_launchctl_print_argument_order")
            token = match.group(1)
            try:
                parsed = shlex.split(token, posix=True)
            except ValueError as exc:
                raise OwnerHostError("owner_launchctl_print_argument_token") from exc
            if len(parsed) != 1:
                raise OwnerHostError("owner_launchctl_print_argument_token")
            argument_values.append(parsed[0])
            continue
        path_match = re.fullmatch(r"path = (.+)", line)
        if path_match is not None:
            path_values.append(path_match.group(1))
        pid_match = re.fullmatch(r"pid = ([0-9]+)", line)
        if pid_match is not None:
            pid_values.append(int(pid_match.group(1)))
    if (
        in_arguments
        or len(path_values) != 1
        or len(pid_values) != 1
        or pid_values[0] <= 0
        or not saw_argument_block
        or not argument_values
    ):
        raise OwnerHostError("owner_launchctl_print_fields")
    return {
        "loaded": True,
        "label": label,
        "plist_path": path_values[0],
        "pid": pid_values[0],
        "program_arguments": argument_values,
        "uid": uid,
    }


def query_launchd_snapshot(
    *,
    label: str,
    expected_plist_path: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Read launchd state; never bootout, bootstrap, enable, or disable jobs."""
    if LABEL_RE.fullmatch(label) is None or not os.path.isabs(expected_plist_path):
        raise OwnerHostError("owner_launchd_query_input")
    try:
        listed = runner(
            ["launchctl", "list"],
            check=False,
            capture_output=True,
            text=True,
            timeout=LAUNCHD_LIST_TIMEOUT_SECONDS,
        )
        all_rows = _parse_launchctl_list(_bounded_text(listed, "owner_launchctl_list_failed"))
        competitors = sorted(
            candidate
            for candidate in all_rows
            if LABEL_RE.fullmatch(candidate) is not None and candidate != label
        )
        target = all_rows.get(label)
        if target is None:
            return {"active_gecko_labels": competitors, "loaded": False, "label": label}
        pid, _status = target
        if pid is None:
            raise OwnerHostError("owner_launchd_loaded_pid_missing")
        printed = runner(
            ["launchctl", "print", f"{LAUNCHD_DOMAIN}/{label}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=LAUNCHD_PRINT_TIMEOUT_SECONDS,
        )
        fields = _parse_launchctl_print(
            _bounded_text(printed, "owner_launchctl_print_failed"), uid=os.getuid(), label=label
        )
        if fields["plist_path"] != expected_plist_path or fields["pid"] != pid:
            raise OwnerHostError("owner_launchd_plist_path_mismatch")
        owner_result = runner(
            ["ps", "-o", "uid=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
            timeout=LAUNCHD_PRINT_TIMEOUT_SECONDS,
        )
        owner_text = _bounded_text(owner_result, "owner_process_uid_failed").strip()
        if not owner_text.isdigit() or int(owner_text) != os.getuid():
            raise OwnerHostError("owner_process_uid_mismatch")
        return {
            "active_gecko_labels": competitors,
            **fields,
            "pid": pid,
        }
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise OwnerHostError("owner_launchd_query_failed") from exc


def collect_inert_launch_snapshot(
    *,
    collector_label: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, list[str]]:
    """Prove no Gecko collector is loaded before plan-only preflight."""
    if LABEL_RE.fullmatch(collector_label) is None:
        raise OwnerHostError("owner_launchd_query_input")
    try:
        result = runner(
            ["launchctl", "list"],
            check=False,
            capture_output=True,
            text=True,
            timeout=LAUNCHD_LIST_TIMEOUT_SECONDS,
        )
        rows = _parse_launchctl_list(_bounded_text(result, "owner_launchctl_list_failed"))
        gecko_labels = sorted(
            label for label in rows if label.startswith("ai.memecoin.brain.senses.gecko-")
        )
        if collector_label in rows or gecko_labels:
            raise OwnerHostError("owner_gecko_collector_already_loaded")
        return {"active_gecko_labels": []}
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise OwnerHostError("owner_launchd_query_failed") from exc


def publish_inert_release_pair(
    writer: Any,
    *,
    relative_root: str,
    release: Mapping[str, Any],
    preflight_result: Mapping[str, Any],
    expected_storage: Mapping[str, Any],
    data_root_fd: int,
    storage_guard: Any,
    revalidate: Callable[[], Any],
) -> dict[str, str]:
    """Create release then preflight under a supplied root-bound writer.

    The destination directory must already exist. A partial pair remains inert;
    conflicts never overwrite an existing immutable artifact.
    """
    if (
        not relative_root
        or relative_root.startswith("/")
        or any(part in {"", ".", ".."} for part in relative_root.split("/"))
        or release.get("activation_authorized") is not False
        or release.get("dispatch_authorized") is not False
        or preflight_result.get("activation_authorized") is not False
        or preflight_result.get("dispatch_authorized") is not False
        or preflight_result.get("status") != "verified_inert_preflight"
    ):
        raise OwnerHostError("owner_release_pair_invalid")

    def validate_writer_binding() -> None:
        try:
            resolved = storage_guard.revalidate(min_free_bytes=preflight.MIN_FREE_BYTES)
            mount_stat = os.fstat(storage_guard.mount_fd)
            data_stat = os.fstat(data_root_fd)
            writer_stat = os.fstat(writer.root_fd)
            configured_root_fd = _open_relative_directory(
                storage_guard.mount_fd, storage_guard.config.data_root
            )
            try:
                configured_root_stat = os.fstat(configured_root_fd)
            finally:
                os.close(configured_root_fd)
        except Exception as exc:
            raise OwnerHostError("owner_release_storage_revalidation") from exc
        config = storage_guard.config
        if (
            set(expected_storage) != {"root_id", "volume_uuid", "device", "inode", "data_root"}
            or config.root_id != expected_storage.get("root_id")
            or config.volume_uuid != expected_storage.get("volume_uuid")
            or config.data_root != expected_storage.get("data_root")
            or type(resolved.available_bytes) is not int
            or resolved.available_bytes < preflight.MIN_FREE_BYTES
            or (mount_stat.st_dev, mount_stat.st_ino)
            != (expected_storage.get("device"), expected_storage.get("inode"))
            or (resolved.mount_device, resolved.mount_inode)
            != (mount_stat.st_dev, mount_stat.st_ino)
            or (writer_stat.st_dev, writer_stat.st_ino) != (data_stat.st_dev, data_stat.st_ino)
            or (data_stat.st_dev, data_stat.st_ino)
            != (configured_root_stat.st_dev, configured_root_stat.st_ino)
            or data_stat.st_dev != mount_stat.st_dev
            or data_stat.st_uid != os.getuid()
            or stat.S_IMODE(data_stat.st_mode) != 0o700
        ):
            raise OwnerHostError("owner_release_writer_binding_mismatch")

    payloads = {
        f"{relative_root}/release.json": canonical_json(dict(release)),
        f"{relative_root}/preflight.json": canonical_json(dict(preflight_result)),
    }
    hashes: dict[str, str] = {}
    for path, payload in payloads.items():
        validate_writer_binding()
        revalidate()
        try:
            writer.atomic_write(path, payload, create_only=True)
        except Exception:
            # A write guard may detect detach inside the descriptor writer.
            # Revalidate so that a mount failure is classified explicitly,
            # while an ordinary create-only collision remains unchanged.
            validate_writer_binding()
            raise
        revalidate()
        validate_writer_binding()
        actual = writer.read_bytes(path, max_bytes=max(4096, len(payload)))
        if actual != payload:
            raise OwnerHostError("owner_release_pair_readback")
        hashes[path] = preflight.sha256(actual)
    return hashes


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    manifest_parser = sub.add_parser("source-manifest")
    manifest_parser.add_argument("--source-commit", required=True)
    definitions_parser = sub.add_parser("host-definitions")
    definitions_parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    launchd_parser = sub.add_parser("launchd-snapshot")
    launchd_parser.add_argument("--label", required=True)
    launchd_parser.add_argument("--plist-path", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "source-manifest":
            result = preflight.compile_source_manifest(REPO_ROOT, source_commit=args.source_commit)
        elif args.command == "host-definitions":
            definitions = load_fixed_host_definitions(args.repo_root)
            result = {
                key: value
                for key, value in definitions.items()
                if not key.endswith("_bytes") and key not in {"collector", "watchdog"}
            }
        else:
            result = query_launchd_snapshot(label=args.label, expected_plist_path=args.plist_path)
        sys.stdout.buffer.write(canonical_json(result))
        return 0
    except (OwnerHostError, preflight.PreflightError) as exc:
        sys.stderr.write(f"owner_preflight_failed:{type(exc).__name__}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
