"""Source-bound owner callback for the capture-only Gecko v6 journal.

The module has no import-time storage, provider, or launchd side effects. Its CLI
is inert unless a separately reviewed create-only v6 activation record exists.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import os
import plistlib
import re
import secrets
import select
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path.home() / ".config/brain-research/storage-v1.json"
SERVICE_PREFIX = "services/gecko-market-v1/capture-only-v6"
CONTROL_RELEASE_ID = "31cf35634151805f006a2cdde943c74c"
CONTROL_RELEASE_DIRECTORY = f"control/gecko-capture-v6/releases/{CONTROL_RELEASE_ID}"
ACTIVATION_PATH = f"{CONTROL_RELEASE_DIRECTORY}/activation.json"
SOURCE_MANIFEST_PATH = f"{CONTROL_RELEASE_DIRECTORY}/source-manifest.json"
PRIOR_USAGE_CENSUS_PATH = "docs/evidence/gecko-v6-prior-usage-census-2026-10-13T1045Z.json"
SOURCE_BUNDLE_PREFIX = "brain/source-pins/gecko-train-four-v3"
PLIST_PATH = "ops/launchd/ai.memecoin.brain.senses.gecko-capture-only-v6.plist.template"
LABEL = "ai.memecoin.brain.senses.gecko-capture-only-v6"
INSTALLED_PLIST_PATH = str(Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist")
DIAGNOSTIC_DIRECTORY = Path.home() / "Library/Logs/BrainSenses"
DIAGNOSTIC_NAME = "gecko-capture-only-v6-owner.json"
SOURCE_MANIFEST_SCHEMA = "brain-senses-gecko-capture-v6-source-manifest"
ACTIVATION_SCHEMA = "brain-senses-gecko-capture-v6-activation"
CHILD_REAP_TIMEOUT_SECONDS = 5.0
CHILD_GROUP_QUIESCENCE_TIMEOUT_SECONDS = 2.0
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_SOURCE_MAP_BYTES = 1024 * 1024
TRAIN_METADATA_PREFIX = (
    "brain/sparse-observation-reference-v1/independent-population-2026-10-08/train"
)
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
RELEASE_RE = re.compile(r"[0-9a-f]{32}\Z")
DIAGNOSTIC_REASON_ALLOWLIST = frozenset(
    {
        "v6_owner_fixed_record_missing",
        "v6_owner_release_context_binding",
        "v6_owner_source_commit_mismatch",
        "v6_owner_source_paths_dirty",
        "v6_owner_source_manifest_drift",
        "v6_owner_source_blob_mismatch",
        "v6_owner_runtime_import_failed",
        "v6_owner_policy_compile",
        "v6_owner_installed_plist_drift",
        "v6_owner_plist_invalid",
        "v6_owner_plist_binding",
        "v6_owner_launchctl_unavailable",
        "v6_owner_launchctl_output_unrecognized",
        "v6_owner_launchctl_output_oversize",
        "v6_owner_launch_identity",
        "v6_owner_legacy_launch_state_unknown",
        "v6_owner_legacy_gecko_collector_loaded",
        "v6_owner_data_root_identity",
        "v6_owner_directory_identity",
        "v6_owner_directory_unavailable",
        "v6_owner_service_root_invalid",
        "v6_owner_service_root_replaced",
        "v6_owner_prior_usage_array_census_mismatch",
        "v6_owner_shared_usage_cap",
        "v6_owner_shared_usage_census_failed",
        "v6_owner_prior_usage_census_binding",
        "v6_owner_prior_usage_census_inventory",
        "v6_owner_prior_usage_census_totals",
        "v6_owner_resource_snapshot_failed",
        "v6_owner_clock_invalid",
        "v6_owner_clock_stop",
        "v6_owner_journal_stop_latched",
        "v6_owner_intent_after_dispatch_deadline",
        "v6_quiescence_absence_unproven",
        "v6_quiescence_process_survived",
        "v6_transport_child_timeout_or_failure",
        "v6_transport_start_deadline",
        "owner_runtime_error",
    }
)

# Keep source order stable: it is serialized into the source manifest and release.
SOURCE_PATHS = (
    "src/memecoin_research_storage/__init__.py",
    "tools/brain_senses_external_schema_v1.py",
    "tools/brain_senses_external_writer_v1.py",
    "tools/brain_senses_external_clock_v1.py",
    "tools/brain_senses_gecko_lane_policy_v3.py",
    "tools/brain_senses_gecko_rotating_scout_v1.py",
    "tools/brain_senses_gecko_lane_engine_v3.py",
    "tools/brain_senses_gecko_lane_policy_v6.py",
    "tools/brain_senses_gecko_capture_only_v6.py",
    "tools/brain_senses_gecko_capture_only_v6_bootstrap.py",
    "tools/brain_senses_gecko_usage_inventory_v2.py",
    "tools/brain_senses_gecko_usage_inventory_v3.py",
    "tools/brain_senses_gecko_capture_only_v6_host.py",
    PRIOR_USAGE_CENSUS_PATH,
    PLIST_PATH,
)
SOURCE_INPUTS = {
    "train_enrollment": (
        f"{TRAIN_METADATA_PREFIX}/enrollment.jsonl",
        "21b56ae5727f6ee6166b595631ddab3b3bf60cbdb3ca599c15e6fba7e4dc6054",
    ),
    "train_role_manifest": (
        f"{TRAIN_METADATA_PREFIX}/manifest.json",
        "9cd169f958e3bc4ea8aec4822dd5e2efcd68257f62a40119a88d19dc39eeb0db",
    ),
    "train_plan": (
        f"{SOURCE_BUNDLE_PREFIX}/plan.json",
        "d872e0d8d8266b2dd22b1c2a8ef7e505a12b6f93ccdf82df9b7b6f206d7e7de8",
    ),
    "population_selection": (
        f"{SOURCE_BUNDLE_PREFIX}/population-selection.json",
        "4aa13b951e11569bb893672c595783482ee7a987f57b26c93fd601150de39172",
    ),
}


class HostV6Error(ValueError):
    """Owner source, release, storage, launch, or journal gate rejected."""


def _arm_child_hard_lifetime(timeout_seconds: float) -> threading.Timer:
    """Arm a monotonic-backed self-kill for a transport child after GO."""
    if not 0 < timeout_seconds <= 20:
        raise HostV6Error("v6_transport_timeout_invalid")
    timer = threading.Timer(timeout_seconds, os._exit, args=(124,))
    timer.daemon = True
    timer.start()
    return timer


def _terminate_and_reap_child(child: subprocess.Popen[bytes]) -> None:
    """Bound termination/reaping and prove the isolated process group is empty."""
    kill_error: OSError | None = None
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as exc:
        try:
            os.kill(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as fallback_exc:
            kill_error = fallback_exc
        else:
            kill_error = exc
    try:
        child.wait(timeout=CHILD_REAP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise HostV6Error("v6_transport_child_reap_unproven") from (kill_error or exc)
    _wait_for_process_group_empty(child.pid)


def _wait_for_process_group_empty(pgid: int) -> None:
    group_deadline = time.monotonic() + CHILD_GROUP_QUIESCENCE_TIMEOUT_SECONDS
    while _group_exists(pgid) and time.monotonic() < group_deadline:
        time.sleep(0.02)
    if _group_exists(pgid):
        raise HostV6Error("v6_transport_child_group_unproven")


def _kernel_process_identity(pid: int) -> dict[str, Any] | None:
    """Return kernel PID generation identity; fail closed when the OS cannot prove it."""
    if type(pid) is not int or pid <= 0 or sys.platform != "darwin":
        return None

    class ProcBsdInfo(ctypes.Structure):
        _fields_ = [
            ("flags", ctypes.c_uint32),
            ("status", ctypes.c_uint32),
            ("xstatus", ctypes.c_uint32),
            ("pid", ctypes.c_uint32),
            ("ppid", ctypes.c_uint32),
            ("uid", ctypes.c_uint32),
            ("gid", ctypes.c_uint32),
            ("ruid", ctypes.c_uint32),
            ("rgid", ctypes.c_uint32),
            ("svuid", ctypes.c_uint32),
            ("svgid", ctypes.c_uint32),
            ("reserved", ctypes.c_uint32),
            ("comm", ctypes.c_char * 16),
            ("name", ctypes.c_char * 32),
            ("nfiles", ctypes.c_uint32),
            ("pgid", ctypes.c_uint32),
            ("pjobc", ctypes.c_uint32),
            ("tdev", ctypes.c_uint32),
            ("tpgid", ctypes.c_uint32),
            ("nice", ctypes.c_int32),
            ("start_sec", ctypes.c_uint64),
            ("start_usec", ctypes.c_uint64),
        ]

    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        proc_pidinfo = libproc.proc_pidinfo
        proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        proc_pidinfo.restype = ctypes.c_int
        proc_pidpath = libproc.proc_pidpath
        proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        proc_pidpath.restype = ctypes.c_int
        info = ProcBsdInfo()
        count = proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
        if count != ctypes.sizeof(info) or info.pid != pid:
            return None
        path_buffer = ctypes.create_string_buffer(4096)
        path_count = proc_pidpath(pid, path_buffer, ctypes.sizeof(path_buffer))
        if path_count <= 0:
            return None
        return {
            "pid": int(info.pid),
            "ppid": int(info.ppid),
            "pgid": int(info.pgid),
            "uid": int(info.uid),
            "birth_id": f"{int(info.start_sec)}.{int(info.start_usec):06d}",
            "executable_path": os.fsdecode(path_buffer.value),
        }
    except (OSError, AttributeError, ValueError):
        return None


def _transport_worker_source_hash() -> str:
    raw = _read_repo_file(REPO_ROOT, "tools/brain_senses_gecko_capture_only_v6_host.py")
    return sha256(raw)


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _collect_quiescence_proofs(
    events: Sequence[Mapping[str, Any]], *, worker_source_sha256: str
) -> dict[str, dict[str, Any]]:
    """Kill or prove absence of each exact dispatched worker generation."""
    from tools import brain_senses_gecko_capture_only_v6 as capture_v6

    intents = {
        event["fields"].get("request_id") for event in events if event.get("kind") == "intent"
    }
    outcomes = {
        event["fields"].get("request_id") for event in events if event.get("kind") == "outcome"
    }
    dispatches = {
        event["fields"].get("request_id"): event["fields"]
        for event in events
        if event.get("kind") == "dispatch"
    }
    expected = (intents & set(dispatches)) - outcomes
    proofs: dict[str, dict[str, Any]] = {}
    for request_id in sorted(expected):
        dispatch = dispatches[request_id]
        if dispatch.get("worker_source_sha256") != worker_source_sha256:
            raise HostV6Error("v6_quiescence_worker_source_drift")
        pid = dispatch.get("worker_pid")
        pgid = dispatch.get("worker_pgid")
        if type(pid) is not int or pgid != pid:
            raise HostV6Error("v6_quiescence_dispatch_identity")
        observed = _kernel_process_identity(pid)
        pid_reuse = False
        observed_birth_id: str | None = None
        if observed is not None and observed.get("birth_id") == dispatch.get("worker_birth_id"):
            if (
                observed.get("pgid") != pgid
                or observed.get("uid") != dispatch.get("worker_uid")
                or observed.get("executable_path") != dispatch.get("worker_executable_path")
            ):
                raise HostV6Error("v6_quiescence_process_group_changed")
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as exc:
                raise HostV6Error("v6_quiescence_kill_failed") from exc
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                current = _kernel_process_identity(pid)
                if current is None:
                    break
                if current.get("birth_id") != dispatch.get("worker_birth_id"):
                    pid_reuse = True
                    observed_birth_id = cast(str, current.get("birth_id"))
                    break
                if current.get("ppid") == os.getpid():
                    try:
                        reaped_pid, _status = os.waitpid(pid, os.WNOHANG)
                    except ChildProcessError:
                        reaped_pid = 0
                    if reaped_pid == pid:
                        break
                time.sleep(0.02)
            else:
                raise HostV6Error("v6_quiescence_process_survived")
        elif observed is not None:
            # PID reuse proves the recorded generation is gone, but the reused
            # group must also be absent before it is safe to certify quiescence.
            pid_reuse = True
            observed_birth_id = cast(str, observed.get("birth_id"))
        else:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            except OSError as exc:
                raise HostV6Error("v6_quiescence_absence_unproven") from exc
            else:
                raise HostV6Error("v6_quiescence_identity_unreadable")
        group_deadline = time.monotonic() + 5.0
        while _group_exists(pgid) and time.monotonic() < group_deadline:
            time.sleep(0.02)
        if _group_exists(pgid):
            raise HostV6Error("v6_quiescence_process_group_nonempty")
        wall, mono = time.time_ns(), time.monotonic_ns()
        wall_delta = wall - dispatch["request_started_ns"]
        mono_delta = mono - dispatch["monotonic_started_ns"]
        proof = {
            "schema": capture_v6.QUIESCENCE_SCHEMA,
            "request_id": request_id,
            "child_pid": pid,
            "process_group_id": pgid,
            "child_birth_id": dispatch["worker_birth_id"],
            "child_executable_path": dispatch["worker_executable_path"],
            "child_uid": dispatch["worker_uid"],
            "attempt_nonce": dispatch["attempt_nonce"],
            "worker_source_sha256": worker_source_sha256,
            "absence_verified": True,
            "pid_reuse_observed": pid_reuse,
            "observed_birth_id": observed_birth_id,
            "process_group_empty": True,
            "descendant_count": 0,
            "request_started_ns": dispatch["request_started_ns"],
            "request_started_monotonic_ns": dispatch["monotonic_started_ns"],
            "quiesced_ns": wall,
            "quiesced_monotonic_ns": mono,
            "clock_pair_verified": wall_delta >= 0
            and mono_delta >= 0
            and abs(wall_delta - mono_delta) <= 2_000_000_000,
        }
        if not proof["clock_pair_verified"]:
            raise HostV6Error("v6_quiescence_clock_pair")
        proofs[request_id] = proof
    return proofs


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def _safe_relative(value: str) -> tuple[str, ...]:
    path = PurePosixPath(value)
    if (
        type(value) is not str
        or not value
        or path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise HostV6Error("v6_owner_path_invalid")
    return path.parts


def _read_repo_file(repo_root: Path, relative: str, *, maximum: int = MAX_SOURCE_BYTES) -> bytes:
    parts = _safe_relative(relative)
    root_fd = os.open(repo_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    current = root_fd
    opened: list[int] = []
    try:
        for component in parts[:-1]:
            current = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current
            )
            opened.append(current)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_nlink != 1
                or before.st_size < 0
                or before.st_size > maximum
            ):
                raise HostV6Error("v6_owner_source_untrusted")
            data = bytearray()
            while len(data) <= maximum:
                chunk = os.read(fd, min(65536, maximum + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            after = os.fstat(fd)
            if len(data) != before.st_size or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise HostV6Error("v6_owner_source_changed_during_read")
            return bytes(data)
        finally:
            os.close(fd)
    finally:
        for fd in reversed(opened):
            os.close(fd)
        os.close(root_fd)


def _git(repo_root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HostV6Error("v6_owner_git_check_failed") from exc
    return result.stdout.strip()


def _git_show_blob(repo_root: Path, commit: str, relative_path: str) -> bytes:
    """Read one pinned Git blob without text decoding or newline normalization."""
    if not COMMIT_RE.fullmatch(commit) or relative_path not in SOURCE_PATHS:
        raise HostV6Error("v6_owner_git_blob_path_invalid")
    try:
        result = subprocess.run(
            ["git", "show", f"{commit}:{relative_path}"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=False,
            timeout=5,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HostV6Error("v6_owner_git_check_failed") from exc
    if len(result.stdout) > MAX_SOURCE_BYTES:
        raise HostV6Error("v6_owner_git_blob_oversize")
    return result.stdout


def compile_source_manifest(
    repo_root: Path,
    *,
    source_commit: str | None = None,
    git: Callable[..., str] = _git,
    git_show: Callable[[Path, str, str], bytes] = _git_show_blob,
) -> dict[str, Any]:
    """Bind exact code bytes to a clean source commit; unrelated dirty files are ignored."""
    actual_commit = git(repo_root, "rev-parse", "HEAD")
    if not COMMIT_RE.fullmatch(actual_commit) or (
        source_commit is not None and source_commit != actual_commit
    ):
        raise HostV6Error("v6_owner_source_commit_mismatch")
    status = git(repo_root, "status", "--porcelain=v1", "--", *SOURCE_PATHS)
    if status:
        raise HostV6Error("v6_owner_source_paths_dirty")
    file_map: dict[str, str] = {}
    for relative in SOURCE_PATHS:
        current = _read_repo_file(repo_root, relative)
        committed = git_show(repo_root, actual_commit, relative)
        if current != committed:
            raise HostV6Error("v6_owner_source_blob_mismatch")
        file_map[relative] = sha256(current)
    core = {
        "schema": SOURCE_MANIFEST_SCHEMA,
        "schema_version": 6,
        "source_commit": actual_commit,
        "source_paths": list(SOURCE_PATHS),
        "source_map": file_map,
    }
    return {**core, "source_manifest_sha256": sha256(canonical(core))}


def verify_source_manifest(
    repo_root: Path,
    manifest: Mapping[str, Any],
    *,
    git: Callable[..., str] = _git,
    git_show: Callable[[Path, str, str], bytes] = _git_show_blob,
) -> None:
    if type(manifest) is not dict:
        raise HostV6Error("v6_owner_source_manifest_shape")
    expected = compile_source_manifest(
        repo_root,
        source_commit=manifest.get("source_commit"),
        git=git,
        git_show=git_show,
    )
    if canonical(dict(manifest)) != canonical(expected):
        raise HostV6Error("v6_owner_source_manifest_drift")


def verify_prior_usage_census(
    receipt_raw: bytes,
    *,
    source_manifest: Mapping[str, Any],
    epoch: Mapping[str, Any],
    storage: Mapping[str, Any],
    repo_root: Path = REPO_ROOT,
) -> list[dict[str, int]]:
    """Verify committed census provenance and derive its conservative carry-in rows."""
    try:
        receipt = json.loads(receipt_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HostV6Error("v6_owner_prior_usage_census_json") from exc
    if type(receipt) is not dict or canonical(receipt) != receipt_raw:
        raise HostV6Error("v6_owner_prior_usage_census_canonical")
    required = {
        "schema",
        "schema_version",
        "source_commit",
        "verifier",
        "verifier_sha256",
        "observed_ns",
        "storage",
        "root_inventory",
        "root_inventory_sha256",
        "root_count",
        "provider_io",
        "attempt_count",
        "unresolved_response_count",
        "raw_bytes_upper_bound",
        "physical_bytes_upper_bound",
        "prior_usage_rows_sha256",
        "attempts",
        "snapshot_sha256_by_root",
    }
    digest = sha256(receipt_raw)
    epoch_binding = epoch.get("prior_usage_census")
    source_map = source_manifest.get("source_map")
    if (
        set(receipt) != required
        or receipt.get("schema") != "brain-senses-gecko-v6-prior-usage-census"
        or receipt.get("schema_version") != 1
        or type(source_map) is not dict
        or source_map.get(PRIOR_USAGE_CENSUS_PATH) != digest
        or type(epoch_binding) is not dict
        or epoch_binding
        != {
            "receipt_path": PRIOR_USAGE_CENSUS_PATH,
            "receipt_sha256": digest,
            "root_inventory_sha256": receipt.get("root_inventory_sha256"),
            "attempt_count": receipt.get("attempt_count"),
            "observed_ns": receipt.get("observed_ns"),
        }
        or type(receipt.get("source_commit")) is not str
        or not COMMIT_RE.fullmatch(receipt["source_commit"])
        or receipt.get("verifier") != "tools/brain_senses_gecko_usage_inventory_v2.py"
        or not HASH_RE.fullmatch(str(receipt.get("verifier_sha256", "")))
        or type(receipt.get("observed_ns")) is not int
        or receipt["observed_ns"] <= 0
        or receipt["observed_ns"] >= epoch.get("first_bucket_close_ns", 0)
        or receipt.get("storage")
        != {
            "root_id": storage.get("root_id"),
            "volume_uuid": storage.get("volume_uuid"),
            "device": storage.get("data_root", {}).get("device"),
            "inode": storage.get("data_root", {}).get("inode"),
        }
        or receipt.get("provider_io") is not False
    ):
        raise HostV6Error("v6_owner_prior_usage_census_binding")
    from tools import brain_senses_gecko_lane_policy_v6 as policy_v6

    inventory = receipt.get("root_inventory")
    attempts = receipt.get("attempts")
    snapshots = receipt.get("snapshot_sha256_by_root")
    if (
        type(inventory) is not dict
        or not HASH_RE.fullmatch(str(receipt.get("root_inventory_sha256", "")))
        or receipt["root_inventory_sha256"] != sha256(canonical(inventory))
        or type(inventory.get("root_paths")) is not list
        or any(type(path) is not str for path in inventory["root_paths"])
        or inventory["root_paths"] != sorted(set(inventory["root_paths"]))
        or type(receipt.get("root_count")) is not int
        or receipt["root_count"] != len(inventory["root_paths"])
        or type(snapshots) is not dict
        or set(snapshots) != set(inventory["root_paths"])
        or any(not HASH_RE.fullmatch(str(value)) for value in snapshots.values())
        or type(attempts) is not list
        or receipt.get("attempt_count") != len(attempts)
        or any(type(row) is not dict for row in attempts)
    ):
        raise HostV6Error("v6_owner_prior_usage_census_inventory")
    sorted_attempts = sorted(
        attempts, key=lambda row: (row.get("decision_ns", 0), row.get("request_id", ""))
    )
    rows: list[dict[str, int]] = []
    seen_ids: set[str] = set()
    unresolved = 0
    for attempt in attempts:
        if (
            type(attempt) is not dict
            or set(attempt)
            != {
                "root",
                "request_id",
                "decision_ns",
                "maximum_response_bytes",
                "response_bytes",
                "response_sha256",
            }
            or attempt.get("root") not in inventory["root_paths"]
            or type(attempt.get("request_id")) is not str
            or not HASH_RE.fullmatch(attempt["request_id"])
            or attempt["request_id"] in seen_ids
            or type(attempt.get("decision_ns")) is not int
            or attempt["decision_ns"] <= 0
            or attempt["decision_ns"] > receipt["observed_ns"]
            or attempt.get("maximum_response_bytes") != policy_v6.MAX_RESPONSE_BYTES
        ):
            raise HostV6Error("v6_owner_prior_usage_census_attempt")
        seen_ids.add(attempt["request_id"])
        response_bytes = attempt.get("response_bytes")
        response_sha = attempt.get("response_sha256")
        if response_bytes is None:
            if response_sha is not None:
                raise HostV6Error("v6_owner_prior_usage_census_ambiguous")
            unresolved += 1
        elif (
            type(response_bytes) is not int
            or not 0 <= response_bytes <= policy_v6.MAX_RESPONSE_BYTES
            or type(response_sha) is not str
            or not HASH_RE.fullmatch(response_sha)
        ):
            raise HostV6Error("v6_owner_prior_usage_census_response")
        rows.append(
            {
                "intent_ns": attempt["decision_ns"],
                "raw_bytes_upper_bound": attempt["maximum_response_bytes"],
                "physical_bytes_upper_bound": policy_v6.PHYSICAL_RESERVE_PER_INTENT,
            }
        )
    if (
        attempts != sorted_attempts
        or receipt.get("unresolved_response_count") != unresolved
        or receipt.get("raw_bytes_upper_bound") != sum(row["raw_bytes_upper_bound"] for row in rows)
        or receipt.get("physical_bytes_upper_bound")
        != sum(row["physical_bytes_upper_bound"] for row in rows)
        or receipt.get("prior_usage_rows_sha256") != sha256(canonical(rows))
    ):
        raise HostV6Error("v6_owner_prior_usage_census_totals")
    try:
        verifier = _git_show_blob(repo_root, receipt["source_commit"], receipt["verifier"])
        _git(
            repo_root,
            "merge-base",
            "--is-ancestor",
            receipt["source_commit"],
            str(source_manifest["source_commit"]),
        )
    except (HostV6Error, KeyError) as exc:
        raise HostV6Error("v6_owner_prior_usage_census_source") from exc
    if sha256(verifier) != receipt["verifier_sha256"]:
        raise HostV6Error("v6_owner_prior_usage_census_verifier_hash")
    return rows


def load_policy_from_writer(data_writer: Any) -> dict[str, Any]:
    from tools import brain_senses_gecko_lane_policy_v6 as policy_v6

    inputs: dict[str, bytes] = {}
    for key, (path, expected_hash) in SOURCE_INPUTS.items():
        raw = data_writer.read_bytes(path, max_bytes=4 * 1024 * 1024)
        if sha256(raw) != expected_hash:
            raise HostV6Error("v6_owner_population_input_hash")
        inputs[key] = raw
    try:
        return policy_v6.compile_policy_v6(
            enrollment_bytes=inputs["train_enrollment"],
            role_manifest_bytes=inputs["train_role_manifest"],
            plan_bytes=inputs["train_plan"],
            selection_bytes=inputs["population_selection"],
        )
    except ValueError as exc:
        raise HostV6Error("v6_owner_policy_compile") from exc


def _load_canonical_object(
    data_writer: Any, path: str, maximum: int
) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = data_writer.read_bytes(path, max_bytes=maximum)
    except Exception as exc:
        raise HostV6Error("v6_owner_fixed_record_missing") from exc

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise HostV6Error("v6_owner_duplicate_json_key")
            result[key] = value
        return result

    try:
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HostV6Error("v6_owner_json_invalid") from exc
    if type(parsed) is not dict or canonical(parsed) != raw:
        raise HostV6Error("v6_owner_json_not_canonical")
    return raw, parsed


def load_fixed_release_context(data_writer: Any) -> dict[str, Any]:
    """Read only the two fixed control records; no repo-work or path fallback exists."""
    manifest_raw, manifest = _load_canonical_object(
        data_writer, SOURCE_MANIFEST_PATH, MAX_SOURCE_MAP_BYTES
    )
    activation_raw, activation = _load_canonical_object(data_writer, ACTIVATION_PATH, 128 * 1024)
    manifest_core = dict(manifest)
    declared_manifest_hash = manifest_core.pop("source_manifest_sha256", None)
    if (
        manifest.get("schema") != SOURCE_MANIFEST_SCHEMA
        or manifest.get("schema_version") != 6
        or type(declared_manifest_hash) is not str
        or not HASH_RE.fullmatch(declared_manifest_hash)
        or declared_manifest_hash != sha256(canonical(manifest_core))
        or activation.get("source_manifest_sha256") != manifest.get("source_manifest_sha256")
        or activation.get("release_id") != CONTROL_RELEASE_ID
        or activation.get("service_root") != f"{SERVICE_PREFIX}/{CONTROL_RELEASE_ID}"
    ):
        raise HostV6Error("v6_owner_release_context_binding")
    return {
        "source_manifest": manifest,
        "activation": activation,
        "source_manifest_raw_sha256": sha256(manifest_raw),
        "activation_sha256": sha256(activation_raw),
    }


def validate_plist(raw: bytes, *, repo_root: Path) -> dict[str, Any]:
    try:
        plist = plistlib.loads(raw)
    except (ValueError, plistlib.InvalidFileException) as exc:
        raise HostV6Error("v6_owner_plist_invalid") from exc
    expected_args = [
        str(repo_root / ".venv/bin/python"),
        "-B",
        str(repo_root / "tools/brain_senses_gecko_capture_only_v6_host.py"),
        "--scheduled-tick",
    ]
    if (
        type(plist) is not dict
        or set(plist)
        != {
            "Label",
            "ProgramArguments",
            "RunAtLoad",
            "KeepAlive",
            "StartInterval",
            "ProcessType",
            "StandardOutPath",
            "StandardErrorPath",
        }
        or plist.get("Label") != LABEL
        or plist.get("ProgramArguments") != expected_args
        or plist.get("RunAtLoad") is not False
        or plist.get("KeepAlive") is not False
        or plist.get("StartInterval") != 60
        or plist.get("ProcessType") != "Background"
        or plist.get("StandardOutPath") != "/dev/null"
        or plist.get("StandardErrorPath") != "/dev/null"
    ):
        raise HostV6Error("v6_owner_plist_binding")
    return plist


def verify_loaded_owner(
    snapshot: Mapping[str, Any],
    *,
    pid: int,
    uid: int,
    repo_root: Path,
    plist_path: str,
) -> None:
    expected_args = [
        str(repo_root / ".venv/bin/python"),
        "-B",
        str(repo_root / "tools/brain_senses_gecko_capture_only_v6_host.py"),
        "--scheduled-tick",
    ]
    if (
        type(snapshot) is not dict
        or set(snapshot) != {"loaded", "label", "pid", "uid", "plist_path", "program_arguments"}
        or snapshot.get("loaded") is not True
        or snapshot.get("label") != LABEL
        or snapshot.get("pid") != pid
        or snapshot.get("uid") != uid
        or plist_path != INSTALLED_PLIST_PATH
        or snapshot.get("plist_path") != INSTALLED_PLIST_PATH
        or snapshot.get("program_arguments") != expected_args
        or uid != os.getuid()
        or pid != os.getpid()
    ):
        raise HostV6Error("v6_owner_launch_identity")


def storage_identity(
    guard: Any, data_root_fd: int, service_root_fd: int, service_root: str
) -> dict[str, Any]:
    """Return current identities for the bound BrainData and service descriptors."""
    data_stat = os.fstat(data_root_fd)
    service_stat = os.fstat(service_root_fd)
    if (
        not stat.S_ISDIR(data_stat.st_mode)
        or data_stat.st_uid != os.getuid()
        or stat.S_IMODE(data_stat.st_mode) != 0o700
        or data_stat.st_dev != os.fstat(guard.mount_fd).st_dev
        or not stat.S_ISDIR(service_stat.st_mode)
        or service_stat.st_uid != os.getuid()
        or stat.S_IMODE(service_stat.st_mode) != 0o700
        or service_stat.st_dev != data_stat.st_dev
        or service_stat.st_ino == data_stat.st_ino
        or guard.config.data_root != "BrainData"
    ):
        raise HostV6Error("v6_owner_data_root_identity")
    reopened_fd = _open_private_directory(data_root_fd, service_root)
    try:
        reopened_stat = os.fstat(reopened_fd)
        if (reopened_stat.st_dev, reopened_stat.st_ino) != (
            service_stat.st_dev,
            service_stat.st_ino,
        ):
            raise HostV6Error("v6_owner_service_root_replaced")
    finally:
        os.close(reopened_fd)
    return {
        "root_id": guard.root_id,
        "volume_uuid": guard.volume_uuid.lower(),
        "data_root": {"device": data_stat.st_dev, "inode": data_stat.st_ino},
        "service_root": {"device": service_stat.st_dev, "inode": service_stat.st_ino},
    }


def verify_activation_record(
    activation: Mapping[str, Any],
    *,
    source_manifest: Mapping[str, Any],
    policy: Mapping[str, Any],
    storage: Mapping[str, Any],
    plist_sha256: str,
    epoch: Mapping[str, Any],
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Validate the fixed v6 authority record; callers cannot supply expectations."""
    required = {
        "schema",
        "schema_version",
        "activation_id",
        "source_commit",
        "source_manifest_sha256",
        "policy_sha256",
        "selector_sha256",
        "population_sha256",
        "release_id",
        "service_root",
        "epoch_sha256",
        "storage",
        "plist_sha256",
        "label",
        "program_arguments",
        "capture_authorized",
        "market_source_qualified",
        "training_admission",
        "economic_admission",
        "activation_sha256",
    }
    if type(activation) is not dict or set(activation) != required:
        raise HostV6Error("v6_owner_activation_shape")
    core = dict(activation)
    digest = core.pop("activation_sha256")
    args = [
        str(repo_root / ".venv/bin/python"),
        "-B",
        str(repo_root / "tools/brain_senses_gecko_capture_only_v6_host.py"),
        "--scheduled-tick",
    ]
    service_root = activation.get("service_root")
    policy_source = policy.get("source")
    source_map = source_manifest.get("source_map")
    if (
        activation.get("schema") != ACTIVATION_SCHEMA
        or activation.get("schema_version") != 6
        or not RELEASE_RE.fullmatch(str(activation.get("activation_id", "")))
        or activation.get("source_commit") != source_manifest.get("source_commit")
        or activation.get("source_manifest_sha256") != source_manifest.get("source_manifest_sha256")
        or activation.get("policy_sha256") != policy.get("policy_sha256")
        or type(source_map) is not dict
        or activation.get("selector_sha256")
        != source_map.get("tools/brain_senses_gecko_rotating_scout_v1.py")
        or type(policy_source) is not dict
        or activation.get("population_sha256") != policy_source.get("population_sha256")
        or type(service_root) is not str
        or activation.get("release_id") != service_root.rsplit("/", 1)[-1]
        or not RELEASE_RE.fullmatch(str(activation.get("release_id", "")))
        or activation.get("service_root") != f"{SERVICE_PREFIX}/{activation.get('release_id')}"
        or activation.get("storage") != dict(storage)
        or activation.get("plist_sha256") != plist_sha256
        or activation.get("label") != LABEL
        or activation.get("program_arguments") != args
        or activation.get("capture_authorized") is not True
        or activation.get("market_source_qualified") is not False
        or activation.get("training_admission") is not False
        or activation.get("economic_admission") is not False
        or activation.get("epoch_sha256") != epoch.get("epoch_sha256")
        or activation.get("storage") != epoch.get("storage")
        or not HASH_RE.fullmatch(str(activation.get("epoch_sha256", "")))
        or digest != sha256(canonical(core))
    ):
        raise HostV6Error("v6_owner_activation_binding")
    return dict(activation)


def _open_private_directory(root_fd: int, relative: str) -> int:
    """Open an existing 0700 same-device directory through a held root fd."""
    parts = _safe_relative(relative)
    root_stat = os.fstat(root_fd)
    current = os.dup(root_fd)
    try:
        for part in parts:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            child_stat = os.fstat(child)
            if (
                not stat.S_ISDIR(child_stat.st_mode)
                or child_stat.st_uid != os.getuid()
                or stat.S_IMODE(child_stat.st_mode) != 0o700
                or child_stat.st_dev != root_stat.st_dev
            ):
                os.close(child)
                raise HostV6Error("v6_owner_directory_identity")
            os.close(current)
            current = child
        result = current
        current = -1
        return result
    except OSError as exc:
        raise HostV6Error("v6_owner_directory_unavailable") from exc
    finally:
        if current >= 0:
            os.close(current)


def verify_owner_preflight(
    *,
    source_manifest: Mapping[str, Any],
    activation: Mapping[str, Any],
    policy: Mapping[str, Any],
    epoch: Mapping[str, Any],
    storage: Mapping[str, Any],
    plist_bytes: bytes,
    installed_plist_bytes: bytes,
    launch_snapshot: Mapping[str, Any],
    pid: int,
    uid: int,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Pure owner gate used before journal replay or a possible request."""
    verify_source_manifest(repo_root, source_manifest)
    pinned_plist_bytes = _read_repo_file(repo_root, PLIST_PATH, maximum=64 * 1024)
    if plist_bytes != pinned_plist_bytes or installed_plist_bytes != pinned_plist_bytes:
        raise HostV6Error("v6_owner_installed_plist_drift")
    parsed_plist = validate_plist(plist_bytes, repo_root=repo_root)
    plist_hash = sha256(plist_bytes)
    verified = verify_activation_record(
        activation,
        source_manifest=source_manifest,
        policy=policy,
        storage=storage,
        plist_sha256=plist_hash,
        epoch=epoch,
        repo_root=repo_root,
    )
    verify_loaded_owner(
        launch_snapshot,
        pid=pid,
        uid=uid,
        repo_root=repo_root,
        plist_path=PLIST_PATH,
    )
    return {
        "activation": verified,
        "plist": parsed_plist,
        "source_manifest_sha256": source_manifest["source_manifest_sha256"],
        "storage": dict(storage),
    }


def _read_owner_file(path: Path, *, maximum: int) -> bytes:
    """Read one owner-owned regular file without following its final symlink."""
    if not path.is_absolute() or ".." in path.parts:
        raise HostV6Error("v6_owner_file_path_invalid")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open("/", flags)
    current = root_fd
    opened: list[int] = []
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, flags, dir_fd=current)
            opened.append(child)
            current = child
        fd = os.open(path.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_nlink != 1
                or before.st_size < 0
                or before.st_size > maximum
            ):
                raise HostV6Error("v6_owner_file_untrusted")
            chunks = bytearray()
            while len(chunks) <= maximum:
                chunk = os.read(fd, min(65536, maximum + 1 - len(chunks)))
                if not chunk:
                    break
                chunks.extend(chunk)
            after = os.fstat(fd)
            if len(chunks) != before.st_size or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise HostV6Error("v6_owner_file_changed_during_read")
            return bytes(chunks)
        finally:
            os.close(fd)
    except OSError as exc:
        raise HostV6Error("v6_owner_file_unavailable") from exc
    finally:
        for fd in reversed(opened):
            os.close(fd)
        os.close(root_fd)


def _launchctl_print_snapshot(uid: int) -> dict[str, Any]:
    """Join launchd's PID/path/argv, allowing only a tiny registration race."""
    result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(3):
        try:
            result = subprocess.run(
                ["/bin/launchctl", "print", f"gui/{uid}/{LABEL}"],
                check=True,
                capture_output=True,
                text=True,
                timeout=3,
                env={"PATH": "/usr/bin:/bin"},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            if attempt == 2:
                raise HostV6Error("v6_owner_launchctl_unavailable") from exc
            time.sleep(0.1)
            continue
        if len(result.stdout) > 64 * 1024:
            raise HostV6Error("v6_owner_launchctl_output_oversize")
        break
    if result is None:
        raise HostV6Error("v6_owner_launchctl_unavailable")
    pid_match = re.search(r"(?m)^\s*pid = ([0-9]+)\s*$", result.stdout)
    path_match = re.search(r"(?m)^\s*path = (.+?)\s*$", result.stdout)
    block = re.search(r"(?ms)^\s*arguments = \{\s*(.*?)^\s*\}", result.stdout)
    if pid_match is None or path_match is None or block is None:
        # launchd can briefly publish the job before attaching its short-lived PID.
        # Repeat only this incomplete snapshot, never a complete identity mismatch.
        for _attempt in range(2):
            time.sleep(0.1)
            try:
                result = subprocess.run(
                    ["/bin/launchctl", "print", f"gui/{uid}/{LABEL}"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=3,
                    env={"PATH": "/usr/bin:/bin"},
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise HostV6Error("v6_owner_launchctl_unavailable") from exc
            if len(result.stdout) > 64 * 1024:
                raise HostV6Error("v6_owner_launchctl_output_oversize")
            pid_match = re.search(r"(?m)^\s*pid = ([0-9]+)\s*$", result.stdout)
            path_match = re.search(r"(?m)^\s*path = (.+?)\s*$", result.stdout)
            block = re.search(r"(?ms)^\s*arguments = \{\s*(.*?)^\s*\}", result.stdout)
            if pid_match is not None and path_match is not None and block is not None:
                break
        if pid_match is None or path_match is None or block is None:
            raise HostV6Error("v6_owner_launchctl_output_unrecognized")
    arguments: list[str] = []
    for line in block.group(1).splitlines():
        value = line.strip()
        if not value:
            continue
        value = re.sub(r"^[0-9]+\s*=\s*", "", value)
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        arguments.append(value)
    return {
        "loaded": True,
        "label": LABEL,
        "pid": int(pid_match.group(1)),
        "uid": uid,
        "plist_path": path_match.group(1),
        "program_arguments": arguments,
    }


def _write_callback_diagnostic(*, reason: str) -> None:
    """Atomically replace one small owner-only diagnostic outside research storage."""
    safe_reason = reason if reason in DIAGNOSTIC_REASON_ALLOWLIST else "owner_runtime_error"
    payload = canonical(
        {
            "schema": "brain-senses-owner-callback-diagnostic-v1",
            "observed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "pid": os.getpid(),
            "release_id": CONTROL_RELEASE_ID,
            "stage": "owner_callback",
            "reason": safe_reason,
        }
    )
    if len(payload) > 1024:
        return
    # Resolve only trusted parent mounts (for example macOS /private/var); the
    # final BrainSenses directory is still opened with O_NOFOLLOW below.
    try:
        home = DIAGNOSTIC_DIRECTORY.parents[2].resolve(strict=True)
        diagnostic_parent = DIAGNOSTIC_DIRECTORY.parent.resolve(strict=True)
    except OSError:
        return
    try:
        home_stat = home.stat()
        parent_stat = diagnostic_parent.stat()
    except OSError:
        return
    if (
        not diagnostic_parent.is_absolute()
        or diagnostic_parent != home / "Library" / "Logs"
        or home_stat.st_uid != os.getuid()
        or not stat.S_ISDIR(home_stat.st_mode)
        or parent_stat.st_dev != home_stat.st_dev
    ):
        return
    root_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    current = root_fd
    opened: list[int] = []
    try:
        for component in (*diagnostic_parent.parts[1:], DIAGNOSTIC_DIRECTORY.name):
            try:
                child = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current,
                )
            except FileNotFoundError:
                os.mkdir(component, 0o700, dir_fd=current)
                child = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current,
                )
            info = os.fstat(child)
            if not stat.S_ISDIR(info.st_mode):
                os.close(child)
                return
            if component == DIAGNOSTIC_DIRECTORY.name and (
                info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700
            ):
                os.close(child)
                return
            opened.append(child)
            current = child
        # A fixed create-only staging name bounds even repeated crash prefixes to
        # one small file; a stale file makes diagnostics unavailable, never writable.
        temp_name = f".{DIAGNOSTIC_NAME}.tmp"
        fd = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=current,
        )
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
            ):
                return
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    return
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(temp_name, DIAGNOSTIC_NAME, src_dir_fd=current, dst_dir_fd=current)
        os.fsync(current)
    except OSError:
        # Diagnostics must never change the callback's fail-closed outcome.
        return
    finally:
        for fd in reversed(opened):
            os.close(fd)
        os.close(root_fd)


def _assert_no_legacy_gecko_labels(uid: int) -> None:
    try:
        result = subprocess.run(
            ["/bin/launchctl", "print", f"gui/{uid}"],
            check=True,
            capture_output=True,
            timeout=5,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HostV6Error("v6_owner_legacy_launch_state_unknown") from exc
    label_pattern = r"(?<![A-Za-z0-9_.-])ai\.memecoin\.brain\.senses\.gecko[A-Za-z0-9_.-]*"
    labels = set(re.findall(label_pattern, result.stdout))
    if labels - {LABEL}:
        raise HostV6Error("v6_owner_legacy_gecko_collector_loaded")


def _resource_snapshot(writer: Any, guard: Any, policy_module: Any) -> dict[str, int]:
    try:
        total = sum(writer.stat(path).st_size for path in writer.list_files())
        free = guard.revalidate(min_free_bytes=policy_module.MIN_FREE_BYTES).available_bytes
    except Exception as exc:
        raise HostV6Error("v6_owner_resource_snapshot_failed") from exc
    return {"free_bytes": free, "resident_bytes": total}


def scheduled_tick() -> dict[str, Any]:
    """Load every authorization input from fixed sources, then replay before work."""
    repo_root = str(REPO_ROOT)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    if str(REPO_ROOT / "src") not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        from memecoin_research_storage import writer_guard
        from tools import brain_senses_gecko_capture_only_v6 as capture_v6
        from tools import brain_senses_gecko_lane_policy_v6 as policy_v6
        from tools import brain_senses_gecko_usage_inventory_v3 as inventory_v3
        from tools.brain_senses_external_writer_v1 import DescriptorWriter
    except ImportError as exc:
        raise HostV6Error("v6_owner_runtime_import_failed") from exc

    # The guard is the only source of root/volume authority. No path, hash, or
    # launch snapshot is accepted from argv or an environment variable.
    with writer_guard(CONFIG_PATH, min_free_bytes=policy_v6.MIN_FREE_BYTES) as guard:
        guard.revalidate(min_free_bytes=policy_v6.MIN_FREE_BYTES)
        data_fd = _open_private_directory(guard.mount_fd, guard.config.data_root)
        data_writer = DescriptorWriter(
            data_fd,
            before_write=lambda: guard.revalidate(min_free_bytes=policy_v6.MIN_FREE_BYTES),
        )
        service_fd = -1
        service_writer = None
        try:
            context = load_fixed_release_context(data_writer)
            source_manifest = context["source_manifest"]
            verify_source_manifest(REPO_ROOT, source_manifest)
            policy = load_policy_from_writer(data_writer)
            epoch_raw, epoch = _load_canonical_object(
                data_writer,
                f"{SERVICE_PREFIX}/{context['activation'].get('release_id')}/{capture_v6.EPOCH_PATH}",
                256 * 1024,
            )
            del epoch_raw
            repo_plist = _read_repo_file(REPO_ROOT, PLIST_PATH, maximum=64 * 1024)
            installed_plist = _read_owner_file(Path(INSTALLED_PLIST_PATH), maximum=64 * 1024)
            if installed_plist != repo_plist:
                raise HostV6Error("v6_owner_installed_plist_drift")
            validate_plist(repo_plist, repo_root=REPO_ROOT)
            uid = os.getuid()
            launch_snapshot = _launchctl_print_snapshot(uid)
            verify_loaded_owner(
                launch_snapshot,
                pid=os.getpid(),
                uid=uid,
                repo_root=REPO_ROOT,
                plist_path=INSTALLED_PLIST_PATH,
            )
            _assert_no_legacy_gecko_labels(uid)
            service_root = context["activation"].get("service_root")
            if type(service_root) is not str:
                raise HostV6Error("v6_owner_service_root_invalid")
            service_fd = _open_private_directory(data_fd, service_root)
            storage = storage_identity(guard, data_fd, service_fd, service_root)
            activation = verify_activation_record(
                context["activation"],
                source_manifest=source_manifest,
                policy=policy,
                storage=storage,
                plist_sha256=sha256(repo_plist),
                epoch=epoch,
                repo_root=REPO_ROOT,
            )
            service_writer = DescriptorWriter(
                service_fd,
                before_write=lambda: guard.revalidate(min_free_bytes=policy_v6.MIN_FREE_BYTES),
            )
            lane_writer = data_writer
            lane_lease = capture_v6.CrossReleaseLaneLeaseV6(lane_writer)
            journal = capture_v6.LocalJournalV6(
                service_writer,
                expected_storage=storage,
                observed_storage=lambda: storage_identity(guard, data_fd, service_fd, service_root),
                revalidate_storage=lambda: guard.revalidate(
                    min_free_bytes=policy_v6.MIN_FREE_BYTES
                ),
                resource_snapshot=lambda: _resource_snapshot(service_writer, guard, policy_v6),
                exclusive_lane_lease=lane_lease,
            )
            pinned_raw, pinned_rows = capture_v6._load_pinned_prior_usage(service_writer, epoch)
            census_raw = _read_repo_file(
                REPO_ROOT, PRIOR_USAGE_CENSUS_PATH, maximum=2 * 1024 * 1024
            )
            census_rows = verify_prior_usage_census(
                census_raw,
                source_manifest=source_manifest,
                epoch=epoch,
                storage=storage,
                repo_root=REPO_ROOT,
            )
            if census_rows != pinned_rows:
                raise HostV6Error("v6_owner_prior_usage_array_census_mismatch")
            try:
                historical_receipt = json.loads(census_raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HostV6Error("v6_owner_prior_usage_census_json") from exc
            wall_origin, mono_origin = time.time_ns(), time.monotonic_ns()

            def clock_check() -> bool:
                wall_now, mono_now = time.time_ns(), time.monotonic_ns()
                wall_delta, mono_delta = wall_now - wall_origin, mono_now - mono_origin
                return (
                    wall_delta >= 0
                    and mono_delta >= 0
                    and abs(wall_delta - mono_delta) <= 2 * policy_v6.NS
                )

            def shared_usage_check(now_ns: int, prospective_request_id: str | None) -> None:
                try:
                    inventory = inventory_v3.build_usage_inventory_v3(
                        data_writer,
                        guard=guard,
                        historical_receipt=historical_receipt,
                        candidate_release_id=activation["release_id"],
                        repo_root=REPO_ROOT,
                        now_ns=now_ns,
                    )
                except inventory_v3.InventoryV3Error as exc:
                    raise HostV6Error("v6_owner_shared_usage_census_failed") from exc
                if (
                    inventory.get("provider_io") is not False
                    or inventory.get("candidate_release_present") is not True
                ):
                    raise HostV6Error("v6_owner_shared_usage_cap")
                if prospective_request_id is None:
                    return
                try:
                    assessment = inventory_v3.assess_shared_usage(
                        inventory,
                        checked_ns=now_ns,
                        prospective_request_id=prospective_request_id,
                    )
                except (inventory_v3.InventoryV3Error, ValueError) as exc:
                    raise HostV6Error("v6_owner_shared_usage_census_failed") from exc
                if assessment.get("admitted") is not True:
                    raise HostV6Error("v6_owner_shared_usage_cap")

            # run_scheduled_batch owns the lane for the whole callback. It must
            # reconcile crash prefixes with exact transport proofs before its
            # first full replay; a pre-recovery verifier here would strand an
            # otherwise recoverable intent/plan/expired-slot prefix.
            result = run_scheduled_batch(
                journal=journal,
                policy=policy,
                epoch=epoch,
                inherited_usage_rows=pinned_rows,
                selector_sha256=activation["selector_sha256"],
                clock_check=clock_check,
                transport=bounded_transport_once,
                worker_source_sha256=source_manifest["source_map"][
                    "tools/brain_senses_gecko_capture_only_v6_host.py"
                ],
                shared_usage_check=shared_usage_check,
                quiescence_proofs=lambda events: _collect_quiescence_proofs(
                    events,
                    worker_source_sha256=source_manifest["source_map"][
                        "tools/brain_senses_gecko_capture_only_v6_host.py"
                    ],
                ),
            )
            result["activation_id"] = activation["activation_id"]
            return result
        finally:
            if service_writer is not None:
                service_writer.close()
            if service_fd >= 0:
                os.close(service_fd)
            data_writer.close()
            os.close(data_fd)


def run_scheduled_batch(
    *,
    journal: Any,
    policy: dict[str, Any],
    epoch: dict[str, Any],
    inherited_usage_rows: Sequence[Mapping[str, Any]],
    selector_sha256: str,
    clock_check: Callable[[], bool],
    transport: Callable[[Mapping[str, Any]], tuple[int, bytes]],
    worker_source_sha256: str | None = None,
    shared_usage_check: Callable[[int, str | None], None] | None = None,
    quiescence_proofs: Callable[[Sequence[Mapping[str, Any]]], Mapping[str, Mapping[str, Any]]],
    wall_ns: Callable[[], int] = time.time_ns,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Recover, replay, and service one six-slot close under the global lane lease."""
    from tools import brain_senses_gecko_capture_only_v6 as capture_v6
    from tools import brain_senses_gecko_lane_policy_v6 as policy_v6
    from tools import brain_senses_gecko_rotating_scout_v1 as scout

    attempted: list[str] = []
    confirmed: list[str] = []
    pinned_worker_hash = worker_source_sha256 or _transport_worker_source_hash()
    if _transport_worker_source_hash() != pinned_worker_hash:
        raise HostV6Error("v6_transport_worker_source_drift")
    with journal.hold_exclusive_lane():
        if not clock_check():
            raise HostV6Error("v6_owner_clock_stop")
        now = wall_ns()
        if type(now) is not int or now <= 0:
            raise HostV6Error("v6_owner_clock_invalid")
        existing_events = capture_v6.replay_events(
            journal.writer.read_bytes(capture_v6.EVENTS_PATH, max_bytes=64 * 1024 * 1024)
        )
        proofs = dict(quiescence_proofs(existing_events))
        journal.recover_unresolved(now_ns=now, quiescence_proofs=proofs)
        events_after_recovery = capture_v6.replay_events(
            journal.writer.read_bytes(capture_v6.EVENTS_PATH, max_bytes=64 * 1024 * 1024)
        )
        pinned_raw, pinned_rows = capture_v6._load_pinned_prior_usage(journal.writer, epoch)
        if (
            canonical(list(inherited_usage_rows)) != pinned_raw
            or list(inherited_usage_rows) != pinned_rows
        ):
            raise HostV6Error("v6_owner_prior_usage_changed")

        # The verifier's exact closure includes the persistent per-service lock.
        # Create it under the already-held cross-release lane before the first
        # replay, including a fresh epoch that has not appended any journal event.
        with journal.writer.lock(".lane.lock", blocking=False):
            journal.revalidate_storage()

        # Recovery is complete before the first full verifier replay. This is
        # the dispatch gate for both old prefixes and fresh callbacks.
        report = capture_v6.verify_local_journal(
            journal.writer,
            epoch=epoch,
            policy=policy,
            selector=scout.plan_bucket,
            selector_sha256=selector_sha256,
            expected_storage=journal.expected_storage,
            observed_storage=journal.observed_storage,
            now_ns=now,
            inherited_usage_rows=pinned_rows,
            resource_snapshot=journal.resource_snapshot(),
        )
        if report.get("stop_latched") is True or any(
            event["kind"] == "outcome"
            and event["fields"].get("status") not in {"received", "empty_response"}
            for event in events_after_recovery
        ):
            return {
                "status": "stopped_by_prior_terminal",
                "provider_io": False,
                "attempted": [],
            }
        if shared_usage_check is not None:
            shared_usage_check(now, None)

        anchor = epoch["first_bucket_close_ns"]
        if now < anchor + scout.DISPATCH_EARLIEST_NS:
            capture_v6.verify_local_journal(
                journal.writer,
                epoch=epoch,
                policy=policy,
                selector=scout.plan_bucket,
                selector_sha256=selector_sha256,
                expected_storage=journal.expected_storage,
                observed_storage=journal.observed_storage,
                now_ns=now,
                inherited_usage_rows=pinned_rows,
                resource_snapshot=journal.resource_snapshot(),
            )
            return {"status": "idle_before_window", "provider_io": False, "attempted": []}

        latest_close = anchor + ((now - anchor) // policy_v6.BUCKET_NS) * policy_v6.BUCKET_NS
        events = capture_v6.replay_events(
            journal.writer.read_bytes(capture_v6.EVENTS_PATH, max_bytes=64 * 1024 * 1024)
        )
        completed_closes = {
            event["fields"]["close_ns"] for event in events if event["kind"] in {"plan", "gap"}
        }
        next_close = (
            max(completed_closes, default=anchor - policy_v6.BUCKET_NS) + policy_v6.BUCKET_NS
        )
        while next_close < latest_close and now >= next_close + scout.DISPATCH_END_EXCLUSIVE_NS:
            if next_close in completed_closes:
                journal.plan_bucket(
                    policy=policy,
                    epoch=epoch,
                    close_ns=next_close,
                    now_ns=now,
                    selector_sha256=selector_sha256,
                )
            else:
                journal.record_gap(
                    epoch=epoch,
                    close_ns=next_close,
                    at_ns=now,
                    reason="unobserved_no_plan",
                )
            next_close += policy_v6.BUCKET_NS

        offset = now - latest_close
        if offset < scout.DISPATCH_EARLIEST_NS:
            capture_v6.verify_local_journal(
                journal.writer,
                epoch=epoch,
                policy=policy,
                selector=scout.plan_bucket,
                selector_sha256=selector_sha256,
                expected_storage=journal.expected_storage,
                observed_storage=journal.observed_storage,
                now_ns=now,
                inherited_usage_rows=pinned_rows,
                resource_snapshot=journal.resource_snapshot(),
            )
            return {"status": "idle_before_window", "provider_io": False, "attempted": []}
        if offset >= scout.DISPATCH_END_EXCLUSIVE_NS:
            if latest_close not in completed_closes:
                journal.record_gap(
                    epoch=epoch,
                    close_ns=latest_close,
                    at_ns=now,
                    reason="unobserved_no_plan",
                )
            else:
                expired_plan = journal.plan_bucket(
                    policy=policy,
                    epoch=epoch,
                    close_ns=latest_close,
                    now_ns=now,
                    selector_sha256=selector_sha256,
                )
                terminal_slots = {
                    (event["fields"].get("close_ns"), event["fields"].get("pool_id"))
                    for event in events
                    if event["kind"] in {"intent", "missed"}
                }
                for pool_id in expired_plan["selected_pool_ids"]:
                    if (latest_close, pool_id) not in terminal_slots:
                        journal.record_missed(
                            plan=expired_plan,
                            pool_id=pool_id,
                            at_ns=now,
                        )
                capture_v6.verify_local_journal(
                    journal.writer,
                    epoch=epoch,
                    policy=policy,
                    selector=scout.plan_bucket,
                    selector_sha256=selector_sha256,
                    expected_storage=journal.expected_storage,
                    observed_storage=journal.observed_storage,
                    now_ns=now,
                    inherited_usage_rows=pinned_rows,
                    resource_snapshot=journal.resource_snapshot(),
                )
            return {"status": "bucket_expired", "provider_io": False, "attempted": []}

        report = capture_v6.verify_local_journal(
            journal.writer,
            epoch=epoch,
            policy=policy,
            selector=scout.plan_bucket,
            selector_sha256=selector_sha256,
            expected_storage=journal.expected_storage,
            observed_storage=journal.observed_storage,
            now_ns=now,
            inherited_usage_rows=pinned_rows,
            resource_snapshot=journal.resource_snapshot(),
        )
        if report.get("stop_latched") is True:
            raise HostV6Error("v6_owner_journal_stop_latched")
        plan = journal.plan_bucket(
            policy=policy,
            epoch=epoch,
            close_ns=latest_close,
            now_ns=now,
            selector_sha256=selector_sha256,
        )
        for pool_id in plan["selected_pool_ids"]:
            # Recompute the complete local chain and caps immediately before
            # every reservation. The persisted plan remains the selector source.
            now = wall_ns()
            if not clock_check():
                raise HostV6Error("v6_owner_clock_stop")
            capture_v6.verify_local_journal(
                journal.writer,
                epoch=epoch,
                policy=policy,
                selector=scout.plan_bucket,
                selector_sha256=selector_sha256,
                expected_storage=journal.expected_storage,
                observed_storage=journal.observed_storage,
                now_ns=now,
                inherited_usage_rows=pinned_rows,
                resource_snapshot=journal.resource_snapshot(),
            )
            events = capture_v6.replay_events(
                journal.writer.read_bytes(capture_v6.EVENTS_PATH, max_bytes=64 * 1024 * 1024)
            )
            if any(
                event["kind"] in {"intent", "missed"}
                and event["fields"].get("close_ns") == latest_close
                and event["fields"].get("pool_id") == pool_id
                for event in events
            ):
                continue
            if now >= latest_close + scout.DISPATCH_END_EXCLUSIVE_NS:
                journal.record_missed(plan=plan, pool_id=pool_id, at_ns=now)
                continue
            dispatch_times = [
                event["fields"]["request_started_ns"]
                for event in events
                if event["kind"] == "dispatch"
            ]
            if dispatch_times:
                earliest = max(dispatch_times) + scout.MINIMUM_DISPATCH_SPACING_NS
                if now < earliest:
                    sleep((earliest - now) / policy_v6.NS)
                    now = wall_ns()
            request_deadline = latest_close + scout.REQUEST_START_END_EXCLUSIVE_NS
            if (
                now < latest_close + scout.DISPATCH_EARLIEST_NS
                or now >= request_deadline
                or now
                + (policy_v6.REQUEST_TIMEOUT_SECONDS + policy_v6.PERSISTENCE_MARGIN_SECONDS)
                * policy_v6.NS
                >= latest_close + scout.AVAILABILITY_END_EXCLUSIVE_NS
            ):
                if now >= latest_close + scout.DISPATCH_END_EXCLUSIVE_NS:
                    journal.record_missed(plan=plan, pool_id=pool_id, at_ns=now)
                break
            if not clock_check():
                raise HostV6Error("v6_owner_clock_stop")
            request = capture_v6.request_for_pool(policy, pool_id, latest_close)
            if shared_usage_check is not None:
                shared_usage_check(now, request["request_id"])
            intent = journal.reserve_intent(
                policy=policy,
                epoch=epoch,
                plan=plan,
                pool_id=pool_id,
                intent_ns=now,
                inherited_usage_rows=pinned_rows,
            )
            dispatch_marker: dict[str, int] = {}

            def mark_dispatch(
                worker_identity: Mapping[str, Any],
                *,
                _request_id: str = intent["fields"]["request_id"],
                _deadline: int = request_deadline,
                _pool_id: str = pool_id,
                _marker: dict[str, int] = dispatch_marker,
            ) -> tuple[int, int]:
                dispatch_ns = wall_ns()
                dispatch_mono = monotonic_ns()
                if dispatch_ns >= _deadline:
                    raise HostV6Error("v6_owner_intent_after_dispatch_deadline")
                journal.append_dispatch(
                    request_id=_request_id,
                    request_started_ns=dispatch_ns,
                    monotonic_started_ns=dispatch_mono,
                    worker_identity=worker_identity,
                    close_ns=latest_close,
                    policy=policy,
                    epoch=epoch,
                    selector_sha256=selector_sha256,
                    inherited_usage_rows=pinned_rows,
                )
                _marker.update(wall=dispatch_ns, monotonic=dispatch_mono)
                attempted.append(_pool_id)
                return dispatch_ns, dispatch_mono

            try:
                if isinstance(transport, _BoundedTransport):
                    http_status, body = transport.execute(
                        request,
                        before_start=mark_dispatch,
                        worker_source_sha256=pinned_worker_hash,
                    )
                else:
                    # Injected transports are an offline test seam, not an
                    # owner-runtime process. Give them an explicitly synthetic
                    # isolated identity rather than borrowing this pytest
                    # process's PID/PGID relationship (which is not the
                    # production worker contract).
                    synthetic_pid = secrets.randbelow(1_000_000_000) + 1_000_000
                    mark_dispatch(
                        {
                            "pid": synthetic_pid,
                            "pgid": synthetic_pid,
                            "birth_id": "1790000000.000001",
                            "executable_path": "/synthetic/gecko-v6-transport-worker",
                            "uid": os.getuid(),
                            "attempt_nonce": secrets.token_hex(16),
                            "worker_source_sha256": pinned_worker_hash,
                        }
                    )
                    http_status, body = transport(request)
                if type(http_status) is not int or type(body) is not bytes:
                    raise HostV6Error("v6_owner_transport_result_shape")
            except Exception as transport_exc:
                if not dispatch_marker:
                    # An intent without a dispatch is spent-reserved; leave it
                    # unresolved for quiescent recovery and never retry it here.
                    raise
                if isinstance(transport, _BoundedTransport) and str(transport_exc) in {
                    "v6_transport_child_reap_unproven",
                    "v6_transport_child_group_unproven",
                }:
                    # No terminal event can hide a worker whose death was not
                    # independently established. Leave dispatch unresolved;
                    # the next callback must perform physical recovery first.
                    raise
                http_status, body = 0, b""
            receipt_ns = wall_ns()
            receipt_mono = monotonic_ns()
            if http_status == 200:
                try:
                    from tools.brain_senses_gecko_lane_engine_v3 import parse_response

                    status = (
                        "received"
                        if parse_response(body, latest_close, receipt_ns)
                        else "empty_response"
                    )
                except (TypeError, ValueError, OverflowError):
                    status = "malformed_response"
            elif http_status in {404, 410, 422}:
                status = "unsupported_at_receipt"
            elif http_status == 429:
                status = "rate_limited"
            elif 400 <= http_status < 500:
                status = "client_or_access_error"
            elif http_status >= 500:
                status = "provider_error"
            else:
                status = "transport_uncertain_possibly_spent"
            availability_ns = wall_ns()
            outcome = journal.persist_outcome(
                request_id=intent["fields"]["request_id"],
                status=status,
                raw_body=body if http_status != 0 else None,
                receipt_ns=receipt_ns,
                monotonic_receipt_ns=receipt_mono,
                availability_ns=availability_ns,
                close_ns=latest_close,
            )
            journal.confirm(
                request_id=intent["fields"]["request_id"],
                at_ns=wall_ns(),
                outcome_sha256=outcome["event_sha256"],
            )
            confirmed.append(pool_id)
            if status not in {"received", "empty_response"}:
                return {
                    "status": "stopped_after_non_success_terminal",
                    "provider_io": True,
                    "attempted": attempted,
                    "confirmed": confirmed,
                    "http_status": http_status,
                }
        return {
            "status": "bucket_processed",
            "provider_io": bool(attempted),
            "attempted": attempted,
            "confirmed": confirmed,
        }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, new_url: str
    ) -> None:
        return None


def public_fetch_once(request: Mapping[str, Any]) -> tuple[int, bytes]:
    """Perform one anonymous exact-host request without retries or redirects."""
    from urllib.parse import parse_qsl, urlsplit

    url = request.get("url")
    pool_id = request.get("pool_id")
    if (
        type(url) is not str
        or type(pool_id) is not str
        or not re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", pool_id)
    ):
        raise HostV6Error("v6_transport_request_shape")
    parsed = urlsplit(url)
    expected_path = f"/api/v2/networks/solana/pools/{pool_id}/ohlcv/minute"
    expected_query = {
        "aggregate": "5",
        "currency": "usd",
        "token": "base",
        "include_empty_intervals": "false",
        "limit": "6",
    }
    query_rows = parse_qsl(parsed.query, keep_blank_values=True)
    query = dict(query_rows)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "api.geckoterminal.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.path != expected_path
        or len(query_rows) != 6
        or len(query) != 6
        or any(query.get(key) != value for key, value in expected_query.items())
        or not query.get("before_timestamp", "").isdigit()
        or request.get("headers")
        != {
            "Accept": "application/json;version=20230203",
            "User-Agent": "memecoin-ai-research/0.1",
        }
        or request.get("max_response_bytes") != 16 * 1024
    ):
        raise HostV6Error("v6_transport_endpoint_binding")
    outbound = urllib.request.Request(url, headers=dict(request["headers"]), method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(outbound, timeout=20) as response:
            body = response.read(16 * 1024 + 1)
            return int(response.status), body
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(16 * 1024 + 1)
        finally:
            exc.close()
        return int(exc.code), body


def _transport_child_main() -> int:
    """Internal one-request worker. No credential or endpoint may enter its argv."""
    hard_timer: threading.Timer | None = None
    try:
        raw = sys.stdin.buffer.readline(64 * 1024 + 1)
        if len(raw) > 64 * 1024 or not raw.endswith(b"\n"):
            raise HostV6Error("v6_transport_request_oversize")
        envelope = json.loads(raw.decode("utf-8"))
        if (
            type(envelope) is not dict
            or canonical(envelope) != raw
            or set(envelope)
            != {
                "request",
                "request_sha256",
                "attempt_nonce",
                "worker_source_sha256",
                "worker_identity",
            }
            or type(envelope.get("request")) is not dict
            or envelope.get("request_sha256") != sha256(canonical(envelope["request"]))
            or type(envelope.get("attempt_nonce")) is not str
            or re.fullmatch(r"[0-9a-f]{32}", envelope["attempt_nonce"]) is None
            or envelope.get("worker_source_sha256") != _transport_worker_source_hash()
            or type(envelope.get("worker_identity")) is not dict
            or set(envelope.get("worker_identity", {}))
            != {"pid", "pgid", "ppid", "birth_id", "executable_path"}
        ):
            raise HostV6Error("v6_transport_request_invalid")
        request = envelope["request"]
        identity = envelope["worker_identity"]
        observed = _kernel_process_identity(os.getpid())
        if (
            identity.get("pid") != os.getpid()
            or identity.get("ppid") != os.getppid()
            or identity.get("pgid") != os.getpid()
            or observed is None
            or observed.get("birth_id") != identity.get("birth_id")
            or observed.get("uid") != os.getuid()
        ):
            raise HostV6Error("v6_transport_worker_identity_unavailable")
        ready = {
            "schema": "brain-senses-gecko-v6-transport-ready",
            "pid": os.getpid(),
            "pgid": identity["pgid"],
            "birth_id": identity["birth_id"],
            "executable_path": identity["executable_path"],
            "attempt_nonce": envelope["attempt_nonce"],
            "request_sha256": envelope["request_sha256"],
            "worker_source_sha256": envelope["worker_source_sha256"],
        }
        sys.stdout.buffer.write(canonical(ready))
        sys.stdout.buffer.flush()
        go_raw = sys.stdin.buffer.readline(4096)
        go = json.loads(go_raw.decode("utf-8")) if go_raw.endswith(b"\n") else None
        if (
            type(go) is not dict
            or canonical(go) != go_raw
            or go
            != {
                "attempt_nonce": envelope["attempt_nonce"],
                "request_sha256": envelope["request_sha256"],
                "command": "GO",
            }
        ):
            raise HostV6Error("v6_transport_start_not_authorized")
        # Parent death must not leave a transport running indefinitely. The
        # timer uses Python's monotonic-backed wait and terminates this process
        # independently of the callback parent.
        hard_timer = _arm_child_hard_lifetime(20.0)
        # The close boundary is embedded in the pinned query. Never start a
        # request after the strict v6 start deadline, even if the parent woke late.
        from urllib.parse import parse_qs, urlsplit

        query = parse_qs(urlsplit(str(request.get("url", ""))).query)
        close_values = query.get("before_timestamp", [])
        if len(close_values) != 1 or not close_values[0].isdigit():
            raise HostV6Error("v6_transport_request_invalid")
        close_ns = int(close_values[0]) * 1_000_000_000
        if time.time_ns() >= close_ns + 270 * 1_000_000_000:
            raise HostV6Error("v6_transport_start_deadline")
        started_wall, started_mono = time.time_ns(), time.monotonic_ns()
        status, body = public_fetch_once(request)
        received_wall, received_mono = time.time_ns(), time.monotonic_ns()
        result = {
            "schema": "brain-senses-gecko-v6-transport-result",
            "status": status,
            "body_b64": base64.b64encode(body).decode("ascii"),
            "started_wall_ns": started_wall,
            "started_monotonic_ns": started_mono,
            "received_wall_ns": received_wall,
            "received_monotonic_ns": received_mono,
            "attempt_nonce": envelope["attempt_nonce"],
            "request_sha256": envelope["request_sha256"],
            "worker_pid": os.getpid(),
            "worker_birth_id": identity["birth_id"],
        }
        sys.stdout.buffer.write(canonical(result))
        sys.stdout.buffer.flush()
        return 0
    except Exception as exc:
        # Only a fixed error class is returned; exception strings are never emitted.
        sys.stdout.buffer.write(
            canonical(
                {
                    "schema": "brain-senses-gecko-v6-transport-result",
                    "error_class": type(exc).__name__,
                }
            )
        )
        return 1
    finally:
        if hard_timer is not None:
            hard_timer.cancel()


class _BoundedTransport:
    """Fixed child runner that durably marks dispatch before authorizing network."""

    def execute(
        self,
        request: Mapping[str, Any],
        *,
        before_start: Callable[[Mapping[str, Any]], tuple[int, int]],
        worker_source_sha256: str,
        timeout_seconds: float = 20.0,
    ) -> tuple[int, bytes]:
        if not 0 < timeout_seconds <= 20:
            raise HostV6Error("v6_transport_timeout_invalid")
        if _transport_worker_source_hash() != worker_source_sha256:
            raise HostV6Error("v6_transport_worker_source_drift")
        began = time.monotonic()
        try:
            child = subprocess.Popen(
                [
                    sys.executable,
                    str(REPO_ROOT / "tools/brain_senses_gecko_capture_only_v6_host.py"),
                    "--transport-child",
                ],
                cwd=REPO_ROOT,
                env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO_ROOT)},
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise HostV6Error("v6_transport_child_start_failed") from exc
        assert child.stdin is not None and child.stdout is not None
        attempt_nonce = secrets.token_hex(16)
        request_sha256 = sha256(canonical(dict(request)))
        launched_identity = _kernel_process_identity(child.pid)
        if (
            launched_identity is None
            or launched_identity.get("ppid") != os.getpid()
            or launched_identity.get("pgid") != child.pid
            or launched_identity.get("uid") != os.getuid()
        ):
            if child.stdin is not None:
                child.stdin.close()
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                child.wait(timeout=CHILD_REAP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as exc:
                raise HostV6Error("v6_transport_child_cleanup_unproven") from exc
            raise HostV6Error("v6_transport_child_kernel_identity_unavailable")
        worker_identity = {
            "pid": child.pid,
            "pgid": child.pid,
            "ppid": os.getpid(),
            "birth_id": launched_identity["birth_id"],
            "executable_path": launched_identity["executable_path"],
        }
        try:
            child.stdin.write(
                canonical(
                    {
                        "request": dict(request),
                        "request_sha256": request_sha256,
                        "attempt_nonce": attempt_nonce,
                        "worker_source_sha256": worker_source_sha256,
                        "worker_identity": worker_identity,
                    }
                )
            )
            child.stdin.flush()
            deadline = began + timeout_seconds
            ready_raw = _read_child_line(child.stdout, deadline=deadline, maximum=4096)
            ready = json.loads(ready_raw.decode("utf-8"))
            child_identity = _kernel_process_identity(child.pid)
            if (
                type(ready) is not dict
                or canonical(ready) != ready_raw
                or set(ready)
                != {
                    "schema",
                    "pid",
                    "pgid",
                    "birth_id",
                    "executable_path",
                    "attempt_nonce",
                    "request_sha256",
                    "worker_source_sha256",
                }
                or ready.get("schema") != "brain-senses-gecko-v6-transport-ready"
                or ready.get("pid") != child.pid
                or ready.get("attempt_nonce") != attempt_nonce
                or ready.get("request_sha256") != request_sha256
                or ready.get("worker_source_sha256") != worker_source_sha256
                or child_identity is None
                or child_identity.get("birth_id") != ready.get("birth_id")
                or child_identity.get("pgid") != ready.get("pgid")
                or child_identity.get("ppid") != os.getpid()
                or child_identity.get("pgid") != child.pid
                or child_identity.get("birth_id") != worker_identity["birth_id"]
                or child_identity.get("uid") != os.getuid()
            ):
                raise HostV6Error("v6_transport_child_not_ready")
            dispatch_identity = {
                "pid": child.pid,
                "pgid": child_identity["pgid"],
                "birth_id": child_identity["birth_id"],
                "executable_path": child_identity["executable_path"],
                "uid": child_identity["uid"],
                "attempt_nonce": attempt_nonce,
                "worker_source_sha256": worker_source_sha256,
            }
            marker_wall, marker_mono = before_start(dispatch_identity)
            child.stdin.write(
                canonical(
                    {
                        "command": "GO",
                        "attempt_nonce": attempt_nonce,
                        "request_sha256": request_sha256,
                    }
                )
            )
            child.stdin.flush()
            stdout = _read_child_line(child.stdout, deadline=deadline, maximum=32 * 1024)
            child.stdin.close()
            child.wait(timeout=max(0.1, timeout_seconds - (time.monotonic() - began)))
            _wait_for_process_group_empty(child.pid)
        except (TimeoutError, subprocess.TimeoutExpired, OSError, HostV6Error) as exc:
            _terminate_and_reap_child(child)
            raise HostV6Error("v6_transport_child_timeout_or_failure") from exc
        elapsed = time.monotonic() - began
        if elapsed > timeout_seconds or child.returncode != 0 or len(stdout) > 32 * 1024:
            raise HostV6Error("v6_transport_child_failed")
        try:
            result = json.loads(stdout.decode("utf-8"))
            if (
                type(result) is not dict
                or canonical(result) != stdout
                or set(result)
                != {
                    "schema",
                    "status",
                    "body_b64",
                    "started_wall_ns",
                    "started_monotonic_ns",
                    "received_wall_ns",
                    "received_monotonic_ns",
                    "attempt_nonce",
                    "request_sha256",
                    "worker_pid",
                    "worker_birth_id",
                }
                or result.get("schema") != "brain-senses-gecko-v6-transport-result"
                or result.get("attempt_nonce") != attempt_nonce
                or result.get("request_sha256") != request_sha256
                or result.get("worker_pid") != child.pid
                or result.get("worker_birth_id") != dispatch_identity["birth_id"]
                or type(result.get("status")) is not int
                or type(result.get("started_wall_ns")) is not int
                or type(result.get("started_monotonic_ns")) is not int
                or type(result.get("received_wall_ns")) is not int
                or type(result.get("received_monotonic_ns")) is not int
                or result["started_wall_ns"] < marker_wall
                or result["started_monotonic_ns"] < marker_mono
                or result["received_wall_ns"] < result["started_wall_ns"]
                or result["received_monotonic_ns"] < result["started_monotonic_ns"]
                or abs(
                    (result["received_wall_ns"] - result["started_wall_ns"])
                    - (result["received_monotonic_ns"] - result["started_monotonic_ns"])
                )
                > 2_000_000_000
                or result["received_monotonic_ns"] - result["started_monotonic_ns"]
                > int(timeout_seconds * 1_000_000_000)
            ):
                raise HostV6Error("v6_transport_child_result_invalid")
            body = base64.b64decode(result["body_b64"], validate=True)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            raise HostV6Error("v6_transport_child_result_invalid") from exc
        if len(body) > 16 * 1024 + 1:
            raise HostV6Error("v6_transport_child_response_oversize")
        return result["status"], body


def _read_child_line(pipe: Any, *, deadline: float, maximum: int) -> bytes:
    """Drain a bounded line concurrently so a partial pipe write cannot hang."""
    fd = pipe.fileno()
    data = bytearray()
    while len(data) <= maximum:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            raise TimeoutError
        chunk = os.read(fd, min(4096, maximum + 1 - len(data)))
        if not chunk:
            raise HostV6Error("v6_transport_child_eof")
        data.extend(chunk)
        if b"\n" in chunk:
            if data.endswith(b"\n") and data.count(b"\n") == 1:
                return bytes(data)
            raise HostV6Error("v6_transport_child_framing")
    raise HostV6Error("v6_transport_child_output_oversize")


bounded_transport_once = _BoundedTransport()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--source-preflight", action="store_true")
    modes.add_argument("--scheduled-tick", action="store_true")
    modes.add_argument("--transport-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.transport_child:
        return _transport_child_main()
    if args.source_preflight:
        print(
            json.dumps(
                {"status": "failed_closed", "reason": "v6_source_preflight_requires_owner_guard"}
            )
        )
        return 2
    if args.scheduled_tick:
        try:
            result = scheduled_tick()
        except Exception as exc:
            _write_callback_diagnostic(
                reason=str(exc) if isinstance(exc, HostV6Error) else "owner_runtime_error"
            )
            # Only a fixed class identifier is emitted; no exception text/path.
            print(
                json.dumps(
                    {
                        "status": "failed_closed",
                        "reason": type(exc).__name__
                        if isinstance(exc, HostV6Error)
                        else "owner_runtime_error",
                    },
                    sort_keys=True,
                )
            )
            return 2
        print(json.dumps(result, sort_keys=True))
        return (
            0
            if result.get("status") in {"bucket_processed", "idle_before_window", "bucket_expired"}
            else 2
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
