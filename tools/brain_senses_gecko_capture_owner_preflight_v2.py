"""Read-only owner-host source and release preflight for Gecko capture v2.

This module compiles and checks the code/protocol closure without creating files,
opening providers, or changing launchd state. A successful result is explicitly
inert; a separately reviewed activation integration is still required.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import plistlib
import re
import stat
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, cast

from memecoin_research_storage import _open_relative_directory
from tools import brain_senses_gecko_capture_only_v3_verify as capture_verify
from tools import brain_senses_gecko_scout_chain_v2 as chain
from tools import brain_senses_gecko_usage_inventory_v4 as usage_inventory
from tools.brain_senses_external_clock_v1 import ClockError, validate_progress
from tools.brain_senses_external_writer_v1 import canonical_json

SCHEMA = "brain-senses-gecko-capture-owner-preflight-v2"
MANIFEST_SCHEMA = "brain-senses-gecko-capture-source-manifest-v2"
VERSION = 2
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MIN_FREE_BYTES = 58 * 1024**3
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
ROOT_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
SERVICE_LABEL_RE = re.compile(r"ai\.memecoin\.brain\.senses\.gecko-capture-only-v[0-9]+\Z")
WATCHDOG_LABEL_RE = re.compile(r"ai\.memecoin\.brain\.senses\.gecko-capture-watchdog-v[0-9]+\Z")

# This is the transitive local Python import closure of the v2 host and writer.
# Protocol data (policy, predecessor certificate, chain, plan, plist and watchdog)
# is separately content-bound in the preflight result.
SOURCE_PATHS = (
    "src/memecoin_research_storage/__init__.py",
    "tools/brain_senses_capacity_coordinator_v2.py",
    "tools/brain_senses_external_clock_v1.py",
    "tools/brain_senses_external_launch_v2.py",
    "tools/brain_senses_external_orchestrator_v2.py",
    "tools/brain_senses_external_verify_v1.py",
    "tools/brain_senses_external_verify_v2.py",
    "tools/brain_senses_external_schema_v1.py",
    "tools/brain_senses_external_schema_v2.py",
    "tools/brain_senses_external_writer_v1.py",
    "tools/brain_senses_gecko_capture_descriptor_v2.py",
    "tools/brain_senses_gecko_capture_host_v2.py",
    "tools/brain_senses_gecko_capture_only_v2_handoff_v1.py",
    "tools/brain_senses_gecko_capture_only_v2_host.py",
    "tools/brain_senses_gecko_capture_only_v2_health_v1.py",
    "tools/brain_senses_gecko_capture_only_v2_legacy_timeout_v1.py",
    "tools/brain_senses_gecko_capture_only_v2_runtime.py",
    "tools/brain_senses_gecko_capture_only_v2_scheduler.py",
    "tools/brain_senses_gecko_capture_only_v2_terminal_usage_v1.py",
    "tools/brain_senses_gecko_capture_only_v2_terminal_usage_v3.py",
    "tools/brain_senses_gecko_capture_only_v2_terminal_usage_v4.py",
    "tools/brain_senses_gecko_capture_only_v2_service.py",
    "tools/brain_senses_gecko_capture_only_v2_verify.py",
    "tools/brain_senses_gecko_capture_only_runtime_v1.py",
    "tools/brain_senses_gecko_capture_only_monitor_v1.py",
    "tools/brain_senses_gecko_capture_only_verify_v1.py",
    "tools/brain_senses_gecko_capture_only_v3_verify.py",
    "tools/brain_senses_gecko_capture_only_v3_host.py",
    "tools/brain_senses_gecko_capture_owner_preflight_v2.py",
    "tools/brain_senses_gecko_capture_owner_host_v2.py",
    "tools/brain_senses_gecko_capture_owner_runtime_v3.py",
    "tools/brain_senses_gecko_capture_activation_v1.py",
    "tools/brain_senses_gecko_capture_activation_v2.py",
    "tools/brain_senses_gecko_capture_replay_context_v1.py",
    "tools/brain_senses_gecko_capture_owner_entry_v2.py",
    "tools/brain_senses_gecko_capture_watchdog_v3.py",
    "tools/brain_senses_gecko_capture_handoff_supervisor_v1.py",
    "tools/brain_senses_gecko_canary_watchdog_v1.py",
    "tools/brain_senses_gecko_lane_engine_v3.py",
    "tools/brain_senses_gecko_lane_engine_v4.py",
    "tools/brain_senses_gecko_lane_policy_v3.py",
    "tools/brain_senses_gecko_lane_policy_v4.py",
    "tools/brain_senses_gecko_lane_live_sources_v2.py",
    "tools/brain_senses_gecko_lane_monitor_v2.py",
    "tools/brain_senses_gecko_lane_runtime_v3.py",
    "tools/brain_senses_gecko_lane_verify_v3.py",
    "tools/brain_senses_gecko_lane_verify_v4.py",
    "tools/brain_senses_gecko_lane_verify_v5.py",
    "tools/brain_senses_gecko_lane_runtime_v4.py",
    "tools/brain_senses_gecko_rotating_scout_v1.py",
    "tools/brain_senses_gecko_scout_chain_v1.py",
    "tools/brain_senses_gecko_scout_chain_v2.py",
    "tools/brain_senses_gecko_shared_usage_v1.py",
    "tools/brain_senses_gecko_interpreter_handoff_v1.py",
    "tools/brain_senses_service_v1_runner.py",
    "tools/brain_senses_service_v1.py",
    "tools/brain_senses_block_epoch_v2.py",
    "tools/brain_senses_block_epoch.py",
    "tools/brain_senses_block_epoch_v2_schedule.py",
    "tools/brain_senses_block_epoch_v3_schedule.py",
    "tools/brain_senses_block_epoch_v3_launch.py",
    "tools/brain_senses_block_epoch_schedule.py",
    "tools/brain_senses_block_provider_pilot.py",
    "tools/brain_senses_c4_terminal_qualifier.py",
    "tools/brain_senses_campaign4_recovery.py",
    "tools/brain_senses_program_watch.py",
    "tools/brain_senses_gecko_usage_inventory_v4.py",
    "tools/brain_senses_gecko_usage_inventory_v1.py",
    "tools/brain_senses_gecko_usage_inventory_v2.py",
    "tools/brain_senses_gecko_v4_usage_reconciliation_v1.py",
)


class PreflightError(ValueError):
    """Source, storage, launch, clock, or replay binding failed closed."""


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PreflightError("preflight_duplicate_json_key")
        result[key] = value
    return result


def _json_object(raw: bytes, reason: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreflightError(reason) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise PreflightError(reason)
    return value


def _safe_relative(path: str) -> tuple[str, ...]:
    parsed = PurePosixPath(path)
    if (
        not path
        or parsed.is_absolute()
        or parsed.as_posix() != path
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise PreflightError("preflight_source_path_invalid")
    return parsed.parts


def _read_repo_file(repo_root: Path, relative_path: str) -> bytes:
    """Read a bounded regular source file through no-follow directory descriptors."""
    parts = _safe_relative(relative_path)
    root_fd = os.open(repo_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    current_fd = root_fd
    owned_fds: list[int] = []
    try:
        for part in parts[:-1]:
            current_fd = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current_fd
            )
            owned_fds.append(current_fd)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current_fd)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) & 0o022
                or before.st_nlink != 1
                or before.st_size < 0
                or before.st_size > MAX_SOURCE_BYTES
            ):
                raise PreflightError("preflight_source_file_untrusted")
            chunks: list[bytes] = []
            remaining = MAX_SOURCE_BYTES + 1
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            after = os.fstat(fd)
            if len(raw) != before.st_size or (
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
                raise PreflightError("preflight_source_changed_during_read")
            return raw
        finally:
            os.close(fd)
    except OSError as exc:
        raise PreflightError("preflight_source_read_failed") from exc
    finally:
        for fd in reversed(owned_fds):
            os.close(fd)
        os.close(root_fd)


def _assert_import_closure(repo_root: Path) -> None:
    """Fail if a newly introduced local import is absent from the pinned closure."""
    known = set(SOURCE_PATHS)
    aliases = {Path(path).stem for path in SOURCE_PATHS}
    for relative in SOURCE_PATHS:
        try:
            tree = ast.parse(_read_repo_file(repo_root, relative))
        except (OSError, SyntaxError) as exc:
            raise PreflightError("preflight_source_parse_failed") from exc
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "tools":
                    for alias in node.names:
                        target = f"tools/{alias.name}.py"
                        if (repo_root / target).exists() and target not in known:
                            raise PreflightError("preflight_import_closure_incomplete")
                modules.append(module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            for module in modules:
                if module.startswith("tools."):
                    target = module.replace(".", "/") + ".py"
                    if (repo_root / target).exists() and target not in known:
                        raise PreflightError("preflight_import_closure_incomplete")
                elif module.startswith("brain_senses_") and module not in aliases:
                    target = f"tools/{module}.py"
                    if (repo_root / target).exists() and target not in known:
                        raise PreflightError("preflight_import_closure_incomplete")
                elif module == "memecoin_research_storage":
                    if "src/memecoin_research_storage/__init__.py" not in known:
                        raise PreflightError("preflight_import_closure_incomplete")


def compile_source_manifest(repo_root: Path, *, source_commit: str) -> dict[str, Any]:
    """Compile deterministic hash/length pins for every local imported source byte."""
    if type(source_commit) is not str or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise PreflightError("preflight_source_commit_invalid")
    _assert_import_closure(repo_root)
    sources = {
        path: {"bytes": len(raw), "sha256": sha256(raw)}
        for path in SOURCE_PATHS
        for raw in [_read_repo_file(repo_root, path)]
    }
    core = {
        "schema": MANIFEST_SCHEMA,
        "schema_version": VERSION,
        "source_commit": source_commit,
        "source_paths": list(SOURCE_PATHS),
        "sources": sources,
    }
    return {**core, "manifest_sha256": sha256(canonical_json(core))}


def verify_source_manifest(
    manifest_raw: bytes,
    *,
    repo_root: Path,
    expected_manifest_sha256: str,
    expected_source_commit: str,
) -> dict[str, Any]:
    manifest = _json_object(manifest_raw, "preflight_source_manifest_invalid")
    core = dict(manifest)
    claimed = core.pop("manifest_sha256", None)
    if (
        set(manifest)
        != {
            "schema",
            "schema_version",
            "source_commit",
            "source_paths",
            "sources",
            "manifest_sha256",
        }
        or manifest.get("schema") != MANIFEST_SCHEMA
        or manifest.get("schema_version") != VERSION
        or manifest.get("source_paths") != list(SOURCE_PATHS)
        or manifest.get("source_commit") != expected_source_commit
        or claimed != expected_manifest_sha256
        or claimed != sha256(canonical_json(core))
    ):
        raise PreflightError("preflight_source_manifest_binding")
    _assert_import_closure(repo_root)
    expected_sources = manifest.get("sources")
    if type(expected_sources) is not dict or set(expected_sources) != set(SOURCE_PATHS):
        raise PreflightError("preflight_source_manifest_closure")
    actual_sources: dict[str, dict[str, Any]] = {}
    for path in SOURCE_PATHS:
        raw = _read_repo_file(repo_root, path)
        actual_sources[path] = {"bytes": len(raw), "sha256": sha256(raw)}
    if actual_sources != expected_sources:
        raise PreflightError("preflight_source_drift")
    return manifest


def compile_owner_release(
    *,
    manifest: Mapping[str, Any],
    storage: Mapping[str, Any],
    plan_record: Mapping[str, Any],
    certificate: Mapping[str, Any],
    binding: Mapping[str, Any],
    policy: Mapping[str, Any],
    chain_tail_sha256: str,
    plist_raw: bytes,
    watchdog_raw: bytes,
    clock_record: Mapping[str, Any],
    label: str,
    program_arguments: Sequence[str],
    watchdog_label: str,
    watchdog_program_arguments: Sequence[str],
) -> dict[str, Any]:
    """Compile an immutable, in-memory release description with no activation grant."""
    if (
        manifest.get("schema") != MANIFEST_SCHEMA
        or manifest.get("schema_version") != VERSION
        or type(manifest.get("manifest_sha256")) is not str
        or manifest.get("manifest_sha256")
        != sha256(canonical_json({k: v for k, v in manifest.items() if k != "manifest_sha256"}))
        or not isinstance(manifest.get("sources"), dict)
        or not all(
            type(entry) is dict
            and type(entry.get("sha256")) is str
            and HASH_RE.fullmatch(entry["sha256"]) is not None
            and type(entry.get("bytes")) is int
            and entry["bytes"] >= 0
            for entry in cast(dict[str, Any], manifest["sources"]).values()
        )
        or type(chain_tail_sha256) is not str
        or HASH_RE.fullmatch(chain_tail_sha256) is None
        or type(label) is not str
        or SERVICE_LABEL_RE.fullmatch(label) is None
        or type(watchdog_label) is not str
        or WATCHDOG_LABEL_RE.fullmatch(watchdog_label) is None
        or type(certificate.get("certificate_sha256")) is not str
        or HASH_RE.fullmatch(cast(str, certificate.get("certificate_sha256"))) is None
        or type(binding.get("binding_sha256")) is not str
        or HASH_RE.fullmatch(cast(str, binding.get("binding_sha256"))) is None
        or type(policy.get("policy_sha256")) is not str
        or HASH_RE.fullmatch(cast(str, policy.get("policy_sha256"))) is None
        or type(policy.get("population_sha256")) is not str
        or HASH_RE.fullmatch(cast(str, policy.get("population_sha256"))) is None
        or len(program_arguments) < 3
        or len(watchdog_program_arguments) < 3
    ):
        raise PreflightError("preflight_release_compile_inputs")
    core = {
        "schema": "brain-senses-gecko-capture-owner-release-v2",
        "schema_version": VERSION,
        "source_commit": manifest["source_commit"],
        "source_manifest_sha256": manifest["manifest_sha256"],
        "source_bindings": {
            path: entry["sha256"]
            for path, entry in cast(dict[str, Any], manifest["sources"]).items()
        },
        "storage": dict(storage),
        "plan_sha256": sha256(canonical_json(dict(plan_record))),
        "certificate_sha256": certificate.get("certificate_sha256"),
        "binding_sha256": binding.get("binding_sha256"),
        "policy_sha256": policy.get("policy_sha256"),
        "population_sha256": policy.get("population_sha256"),
        "chain_tail_sha256": chain_tail_sha256,
        "plist_sha256": sha256(plist_raw),
        "watchdog_sha256": sha256(watchdog_raw),
        "clock_sha256": sha256(canonical_json(dict(clock_record))),
        "label": label,
        "program_arguments": list(program_arguments),
        "watchdog_program_arguments": list(watchdog_program_arguments),
        "cadence_seconds": 12,
        "watchdog_label": watchdog_label,
        "capture_authorized": True,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
        "provider_io": False,
        "dispatch_authorized": False,
        "activation_authorized": False,
    }
    return {**core, "release_sha256": sha256(canonical_json(core))}


def _check_storage_descriptors(
    *,
    storage: Mapping[str, Any],
    storage_guard: Any,
    mount_fd: int,
    data_root_fd: int,
    service_fd: int,
    source_reader_fd: int,
    writer_root_fd: int,
) -> dict[str, Any]:
    try:
        canonical_uuid = str(uuid.UUID(cast(str, storage.get("volume_uuid"))))
    except (ValueError, TypeError, AttributeError) as exc:
        raise PreflightError("preflight_storage_binding_invalid") from exc
    if (
        set(storage) != {"root_id", "volume_uuid", "device", "inode", "data_root"}
        or type(storage.get("root_id")) is not str
        or ROOT_ID_RE.fullmatch(cast(str, storage.get("root_id"))) is None
        or type(storage.get("volume_uuid")) is not str
        or canonical_uuid != storage.get("volume_uuid")
        or type(storage.get("device")) is not int
        or type(storage.get("inode")) is not int
    ):
        raise PreflightError("preflight_storage_binding_invalid")
    try:
        resolved = storage_guard.revalidate(min_free_bytes=MIN_FREE_BYTES)
        available_bytes = resolved.available_bytes
    except Exception as exc:
        raise PreflightError("preflight_storage_revalidation_failed") from exc
    config = storage_guard.config
    if (
        config.root_id != storage["root_id"]
        or config.volume_uuid != storage["volume_uuid"]
        or config.data_root != storage["data_root"]
        or type(available_bytes) is not int
        or available_bytes < MIN_FREE_BYTES
    ):
        raise PreflightError("preflight_storage_config_mismatch")
    mount_stat, root_stat = os.fstat(mount_fd), os.fstat(data_root_fd)
    try:
        configured_root_fd = _open_relative_directory(mount_fd, cast(str, config.data_root))
    except Exception as exc:
        raise PreflightError("preflight_configured_data_root_unavailable") from exc
    try:
        configured_root_stat = os.fstat(configured_root_fd)
    finally:
        os.close(configured_root_fd)
    service_stat, source_stat, writer_stat = (
        os.fstat(service_fd),
        os.fstat(source_reader_fd),
        os.fstat(writer_root_fd),
    )
    identity = (storage["device"], storage["inode"])
    if (mount_stat.st_dev, mount_stat.st_ino) != identity:
        raise PreflightError("preflight_mount_descriptor_mismatch")
    if (
        resolved.mount_device != mount_stat.st_dev
        or resolved.mount_inode != mount_stat.st_ino
        or os.fstat(storage_guard.mount_fd).st_ino != mount_stat.st_ino
    ):
        raise PreflightError("preflight_storage_guard_descriptor_mismatch")
    if (
        root_stat.st_dev != mount_stat.st_dev
        or (root_stat.st_dev, root_stat.st_ino)
        != (configured_root_stat.st_dev, configured_root_stat.st_ino)
        or root_stat.st_uid != os.getuid()
        or stat.S_IMODE(root_stat.st_mode) != 0o700
        or service_stat.st_dev != root_stat.st_dev
        or service_stat.st_uid != os.getuid()
        or stat.S_IMODE(service_stat.st_mode) != 0o700
        or (service_stat.st_dev, service_stat.st_ino) != (source_stat.st_dev, source_stat.st_ino)
        or (service_stat.st_dev, service_stat.st_ino) != (writer_stat.st_dev, writer_stat.st_ino)
    ):
        raise PreflightError("preflight_data_descriptor_mismatch")
    return {
        "root_id": storage["root_id"],
        "volume_uuid": storage["volume_uuid"],
        "device": mount_stat.st_dev,
        "inode": mount_stat.st_ino,
        "data_root_device": root_stat.st_dev,
        "data_root_inode": root_stat.st_ino,
        "service_device": service_stat.st_dev,
        "service_inode": service_stat.st_ino,
        "available_bytes": available_bytes,
    }


def _plist_object(raw: bytes, reason: str) -> dict[str, Any]:
    try:
        value = plistlib.loads(raw)
    except (plistlib.InvalidFileException, ValueError, TypeError) as exc:
        raise PreflightError(reason) from exc
    if type(value) is not dict:
        raise PreflightError(reason)
    return value


def verify_loaded_launch_identity(
    *,
    launch_snapshot: Mapping[str, Any],
    watchdog_snapshot: Mapping[str, Any],
    expected_label: str,
    expected_plist_path: str,
    expected_program_arguments: Sequence[str],
    expected_watchdog_label: str,
    expected_watchdog_arguments: Sequence[str],
) -> None:
    """Verify an already-loaded pair; this helper performs no launchd operation."""
    if (
        set(launch_snapshot)
        != {
            "loaded",
            "label",
            "plist_path",
            "pid",
            "uid",
            "program_arguments",
            "competing_gecko_labels",
        }
        or launch_snapshot.get("loaded") is not True
        or SERVICE_LABEL_RE.fullmatch(expected_label) is None
        or launch_snapshot.get("label") != expected_label
        or launch_snapshot.get("plist_path") != expected_plist_path
        or type(launch_snapshot.get("pid")) is not int
        or launch_snapshot["pid"] <= 0
        or launch_snapshot.get("uid") != os.getuid()
        or launch_snapshot.get("program_arguments") != list(expected_program_arguments)
        or launch_snapshot.get("competing_gecko_labels") != []
    ):
        raise PreflightError("preflight_launchd_identity_or_competitor")
    if (
        set(watchdog_snapshot)
        != {"loaded", "label", "pid", "uid", "program_arguments", "schedule_interval_seconds"}
        or watchdog_snapshot.get("loaded") is not True
        or WATCHDOG_LABEL_RE.fullmatch(expected_watchdog_label) is None
        or watchdog_snapshot.get("label") != expected_watchdog_label
        or type(watchdog_snapshot.get("pid")) is not int
        or watchdog_snapshot["pid"] <= 0
        or watchdog_snapshot.get("uid") != os.getuid()
        or watchdog_snapshot.get("program_arguments") != list(expected_watchdog_arguments)
        or watchdog_snapshot.get("schedule_interval_seconds") != 15
    ):
        raise PreflightError("preflight_watchdog_identity")


def preflight_owner_host(
    *,
    repo_root: Path,
    source_manifest_raw: bytes,
    expected_source_manifest_sha256: str,
    source_commit: str,
    release: Mapping[str, Any],
    expected_storage: Mapping[str, Any],
    mount_fd: int,
    data_root_fd: int,
    service_fd: int,
    source_reader_fd: int,
    writer_root_fd: int,
    storage_guard: Any,
    plist_raw: bytes,
    watchdog_raw: bytes,
    launch_snapshot: Mapping[str, Any],
    policy: Mapping[str, Any],
    certificate: Mapping[str, Any],
    certificate_inputs: Mapping[str, Any],
    chain_records: Sequence[Mapping[str, Any]],
    plan_record: Mapping[str, Any],
    capture_inputs: Mapping[str, Any],
    inventory_inputs: Mapping[str, Any],
    clock_record: Mapping[str, Any],
    now_ns: int,
    monotonic_ns: int,
    expected_label: str,
    expected_program_arguments: Sequence[str],
) -> dict[str, Any]:
    """Read-only owner gate. It deliberately never grants activation authority."""
    manifest = verify_source_manifest(
        source_manifest_raw,
        repo_root=repo_root,
        expected_manifest_sha256=expected_source_manifest_sha256,
        expected_source_commit=source_commit,
    )
    source_map = {
        path: entry["sha256"] for path, entry in cast(dict[str, Any], manifest["sources"]).items()
    }
    storage = dict(expected_storage)
    storage_observed = _check_storage_descriptors(
        storage=storage,
        storage_guard=storage_guard,
        mount_fd=mount_fd,
        data_root_fd=data_root_fd,
        service_fd=service_fd,
        source_reader_fd=source_reader_fd,
        writer_root_fd=writer_root_fd,
    )
    release_core = dict(release)
    claimed_release = release_core.pop("release_sha256", None)
    release_keys = {
        "schema",
        "schema_version",
        "source_commit",
        "source_manifest_sha256",
        "source_bindings",
        "storage",
        "plan_sha256",
        "certificate_sha256",
        "binding_sha256",
        "policy_sha256",
        "population_sha256",
        "chain_tail_sha256",
        "plist_sha256",
        "watchdog_sha256",
        "clock_sha256",
        "label",
        "program_arguments",
        "watchdog_program_arguments",
        "cadence_seconds",
        "watchdog_label",
        "capture_authorized",
        "market_source_qualified",
        "training_admission",
        "economic_admission",
        "provider_io",
        "dispatch_authorized",
        "activation_authorized",
        "release_sha256",
    }
    if (
        set(release) != release_keys
        or release.get("schema") != "brain-senses-gecko-capture-owner-release-v2"
        or release.get("schema_version") != VERSION
        or type(claimed_release) is not str
        or claimed_release != sha256(canonical_json(release_core))
        or release.get("source_commit") != source_commit
        or release.get("source_bindings") != source_map
        or release.get("source_manifest_sha256") != expected_source_manifest_sha256
        or release.get("storage") != storage
        or release.get("plan_sha256") != sha256(canonical_json(dict(plan_record)))
        or release.get("certificate_sha256") != certificate.get("certificate_sha256")
        or release.get("binding_sha256") != capture_inputs.get("binding", {}).get("binding_sha256")
        or release.get("policy_sha256") != policy.get("policy_sha256")
        or release.get("population_sha256") != policy.get("population_sha256")
        or not chain_records
        or release.get("chain_tail_sha256") != chain_records[-1].get("record_sha256")
        or release.get("plist_sha256") != sha256(plist_raw)
        or release.get("watchdog_sha256") != sha256(watchdog_raw)
        or release.get("label") != expected_label
        or release.get("program_arguments") != list(expected_program_arguments)
        or type(release.get("watchdog_program_arguments")) is not list
        or not release.get("watchdog_program_arguments")
        or release.get("cadence_seconds") != 12
        or release.get("provider_io") is not False
        or release.get("dispatch_authorized") is not False
        or release.get("activation_authorized") is not False
    ):
        raise PreflightError("preflight_release_source_binding")
    if release.get("storage") != storage or release.get("capture_authorized") is not True:
        raise PreflightError("preflight_release_storage_binding")
    admission_flags = ("market_source_qualified", "training_admission", "economic_admission")
    if any(release.get(key) is not False for key in admission_flags):
        raise PreflightError("preflight_release_admission_flags")

    plist = _plist_object(plist_raw, "preflight_plist_invalid")
    watchdog = _plist_object(watchdog_raw, "preflight_watchdog_invalid")
    if (
        SERVICE_LABEL_RE.fullmatch(expected_label) is None
        or plist.get("Label") != expected_label
        or plist.get("ProgramArguments") != list(expected_program_arguments)
        or type(plist.get("StartInterval")) is not int
        or plist.get("StartInterval") != 12
        or plist.get("RunAtLoad") is not True
        or plist.get("KeepAlive") is not False
        or plist.get("ProcessType") != "Background"
        or plist.get("StandardOutPath") != "/dev/null"
        or plist.get("StandardErrorPath") != "/dev/null"
        or hashlib.sha256(plist_raw).hexdigest() != release.get("plist_sha256")
        or watchdog.get("Label") != release.get("watchdog_label")
        or type(watchdog.get("StartInterval")) is not int
        or watchdog.get("KeepAlive") != {"SuccessfulExit": False}
        or watchdog.get("RunAtLoad") is not True
        or watchdog.get("ProgramArguments") != release.get("watchdog_program_arguments")
        or hashlib.sha256(canonical_json(dict(clock_record))).hexdigest()
        != release.get("clock_sha256")
        or hashlib.sha256(watchdog_raw).hexdigest() != release.get("watchdog_sha256")
    ):
        raise PreflightError("preflight_launch_definition_drift")
    if (
        set(launch_snapshot) != {"active_gecko_labels"}
        or launch_snapshot.get("active_gecko_labels") != []
    ):
        raise PreflightError("preflight_competing_collector")
    if (
        WATCHDOG_LABEL_RE.fullmatch(cast(str, release.get("watchdog_label"))) is None
        or plist.get("StartInterval") != release.get("cadence_seconds")
        or watchdog.get("Label") != release.get("watchdog_label")
        or watchdog.get("StartInterval") != 15
        or hashlib.sha256(canonical_json(dict(clock_record))).hexdigest()
        != release.get("clock_sha256")
    ):
        raise PreflightError("preflight_schedule_or_protocol_binding")

    try:
        validate_progress(dict(clock_record), wall_ns=now_ns, monotonic_ns=monotonic_ns)
        chain_result = chain.verify_chain(
            policy, certificate, chain_records, certificate_inputs=certificate_inputs
        )
        close_ns = plan_record.get("bucket_close_ns")
        if (
            not chain_records
            or dict(chain_records[-1]) != dict(plan_record)
            or plan_record.get("schema") != chain.PLAN_SCHEMA
            or type(close_ns) is not int
            or close_ns + 240 * 1_000_000_000 <= now_ns
        ):
            raise PreflightError("preflight_plan_not_current")
        capture_result = capture_verify.verify_capture_prefix_v3(
            **dict(capture_inputs), observed_now_ns=now_ns
        )
        required_inventory_keys = {
            "writer",
            "required_successor_root",
            "expected_handoffs",
            "expected_v2_chains",
        }
        if set(inventory_inputs) != required_inventory_keys:
            raise PreflightError("preflight_inventory_context_shape")
        expected_successor_root = certificate.get("successor_root")
        if (
            type(expected_successor_root) is not str
            or inventory_inputs.get("required_successor_root") != expected_successor_root
        ):
            raise PreflightError("preflight_inventory_successor_binding")
        inventory_result = usage_inventory.build_usage_inventory(
            inventory_inputs["writer"],
            required_successor_root=expected_successor_root,
            expected_handoffs=cast(
                Mapping[str, Mapping[str, Any]], inventory_inputs["expected_handoffs"]
            ),
            expected_v2_chains=cast(
                Mapping[str, Mapping[str, Any]], inventory_inputs["expected_v2_chains"]
            ),
            observed_now_ns=now_ns,
        )
    except (ClockError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, PreflightError):
            raise
        raise PreflightError("preflight_clock_or_lineage_replay_failed") from exc
    if (
        capture_result.get("status") != "verified_prefix"
        or capture_result.get("dispatch_blocked") is True
        or capture_result.get("unresolved_count") != 0
        or capture_result.get("expired_unaccounted_slots")
        or capture_inputs.get("chain_records") != list(chain_records)
        or inventory_result.get("provider_io") is not False
        or inventory_result.get("dispatch_authorized") is not False
        or inventory_result.get("root_inventory_sha256") is None
        or not any(
            row.get("root") == certificate.get("successor_root")
            and row.get("status") in {"verified_none", "verified_prefix"}
            for row in inventory_result.get("snapshots", [])
            if type(row) is dict
        )
    ):
        raise PreflightError("preflight_capture_prefix_not_dispatchable")

    core = {
        "schema": SCHEMA,
        "schema_version": VERSION,
        "status": "verified_inert_preflight",
        "source_manifest_sha256": expected_source_manifest_sha256,
        "source_bindings": source_map,
        "source_commit": source_commit,
        "storage": storage_observed,
        "plan_sha256": sha256(canonical_json(dict(plan_record))),
        "certificate_sha256": certificate.get("certificate_sha256"),
        "chain_tail_sha256": chain_result.get("last_record_sha256"),
        "capture_prefix_status": capture_result.get("status"),
        "capture_prefix_sha256": sha256(canonical_json(dict(capture_result))),
        "global_inventory_sha256": inventory_result["root_inventory_sha256"],
        "global_inventory_snapshot_sha256": sha256(canonical_json(dict(inventory_result))),
        "plist_sha256": sha256(plist_raw),
        "watchdog_sha256": sha256(watchdog_raw),
        "clock_sha256": sha256(canonical_json(dict(clock_record))),
        "cadence_seconds": plist["StartInterval"],
        "launchd_label": expected_label,
        "launchd_state": "inactive_no_competitors",
        "watchdog_label": release["watchdog_label"],
        "provider_io": False,
        "dispatch_authorized": False,
        "activation_authorized": False,
    }
    return {**core, "preflight_sha256": sha256(canonical_json(core))}
