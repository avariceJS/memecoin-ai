"""Create-only activation record and restart trust gate for Gecko capture.

This control record lives outside inventory-closed capture service roots. It is
an identity gate only: it grants no provider, launchd, or storage mutation by
itself. Callers must verify it before reading a fresh chain tail or clock.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import stat
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from memecoin_research_storage import StorageError
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json

SCHEMA = "brain-senses-gecko-capture-activation-v1"
SCHEMA_VERSION = 1
ACTIVATION_DIRECTORY = "control/gecko-capture-activation-v1"
ACTIVATION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
PHASES = frozenset({"canary", "continuous"})
MAX_SOURCE_FILES = 128
MAX_SOURCE_BYTES = 32 * 1024 * 1024


class ActivationError(ValueError):
    """Activation material is absent, stale, malformed, or mismatched."""


@dataclass(frozen=True)
class VerifiedActivation:
    activation_id: str
    record_sha256: str
    record: Mapping[str, Any]


def _sha(value: Any) -> bool:
    return type(value) is str and SHA_RE.fullmatch(value) is not None


def _commit(value: Any) -> bool:
    return type(value) is str and COMMIT_RE.fullmatch(value) is not None


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ActivationError("activation_duplicate_json_key")
        result[key] = value
    return result


def _canonical(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, ActivationError) as exc:
        raise ActivationError("activation_record_invalid_json") from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise ActivationError("activation_record_not_canonical")
    return value


def activation_path(activation_id: str) -> str:
    if type(activation_id) is not str or ACTIVATION_ID_RE.fullmatch(activation_id) is None:
        raise ActivationError("activation_id_invalid")
    return f"{ACTIVATION_DIRECTORY}/{activation_id}.json"


def _safe_source_map(source_map: Mapping[str, str]) -> dict[str, str]:
    if type(source_map) is not dict or not 0 < len(source_map) <= MAX_SOURCE_FILES:
        raise ActivationError("activation_source_map_invalid")
    result: dict[str, str] = {}
    for path, digest in source_map.items():
        if (
            type(path) is not str
            or path.startswith("/")
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or not _sha(digest)
        ):
            raise ActivationError("activation_source_map_invalid")
        result[path] = digest
    return dict(sorted(result.items()))


def _run_git(repo_root: Path, args: Sequence[str], *, max_bytes: int = MAX_SOURCE_BYTES) -> bytes:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ActivationError("activation_git_evidence_unavailable") from exc
    if len(proc.stdout) > max_bytes:
        raise ActivationError("activation_git_output_over_limit")
    return proc.stdout


def _read_source_nofollow(repo_root: Path, relative: str) -> bytes:
    """Read a bounded regular source file through no-follow directory fds."""
    root_fd = os.open(repo_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    current_fd = root_fd
    file_fd = -1
    try:
        components = relative.split("/")
        for component in components[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            if current_fd != root_fd:
                os.close(current_fd)
            current_fd = next_fd
        file_fd = os.open(components[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=current_fd)
        info = os.fstat(file_fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_size < 0
            or info.st_size > MAX_SOURCE_BYTES
        ):
            raise ActivationError("activation_source_not_regular")
        output = bytearray()
        while len(output) <= info.st_size:
            chunk = os.read(file_fd, min(1024 * 1024, info.st_size + 1 - len(output)))
            if not chunk:
                break
            output.extend(chunk)
        if len(output) != info.st_size:
            raise ActivationError("activation_source_changed_during_read")
        return bytes(output)
    except OSError as exc:
        raise ActivationError("activation_source_unavailable") from exc
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if current_fd != root_fd:
            os.close(current_fd)
        os.close(root_fd)


def verify_source_commit(
    *, repo_root: Path, source_commit: str, source_map: Mapping[str, str]
) -> None:
    """Require a clean checkout and exact equality with every pinned Git blob."""
    if not _commit(source_commit):
        raise ActivationError("activation_source_commit_invalid")
    expected = _safe_source_map(cast(Mapping[str, str], dict(source_map)))
    head = _run_git(repo_root, ["rev-parse", "HEAD"], max_bytes=256).decode("ascii").strip()
    if head != source_commit:
        raise ActivationError("activation_source_commit_mismatch")
    status = _run_git(
        repo_root,
        ["status", "--porcelain=v1", "--untracked-files=all", "--", *expected],
        max_bytes=1024 * 1024,
    )
    if status:
        raise ActivationError("activation_source_tree_dirty")
    total = 0
    for path, digest in expected.items():
        live_bytes = _read_source_nofollow(repo_root, path)
        blob = _run_git(repo_root, ["show", f"{source_commit}:{path}"])
        total += len(blob)
        if (
            total > MAX_SOURCE_BYTES
            or hashlib.sha256(blob).hexdigest() != digest
            or hashlib.sha256(live_bytes).hexdigest() != digest
            or len(live_bytes) != len(blob)
        ):
            raise ActivationError("activation_source_blob_mismatch")


def _verify_manifest(
    raw: bytes,
    *,
    source_commit: str,
    source_manifest_sha256: str,
    source_map: Mapping[str, str],
) -> None:
    if hashlib.sha256(raw).hexdigest() != source_manifest_sha256:
        raise ActivationError("activation_manifest_hash_mismatch")
    manifest = _canonical(raw)
    sources = manifest.get("sources")
    manifest_core = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
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
        or manifest.get("schema") != "brain-senses-gecko-capture-source-manifest-v2"
        or manifest.get("schema_version") != 2
        or manifest.get("source_commit") != source_commit
        or manifest.get("source_paths") != sorted(source_map)
        or manifest.get("manifest_sha256")
        != hashlib.sha256(canonical_json(manifest_core)).hexdigest()
        or type(sources) is not dict
        or set(sources) != set(source_map)
        or any(
            type(sources[path]) is not dict
            or set(sources[path]) != {"bytes", "sha256"}
            or sources[path].get("sha256") != digest
            or type(sources[path].get("bytes")) is not int
            or sources[path]["bytes"] < 0
            for path, digest in source_map.items()
        )
    ):
        raise ActivationError("activation_manifest_binding_mismatch")


def _storage_identity(
    storage: Mapping[str, Any], *, data_root_fd: int, writer: DescriptorWriter
) -> dict[str, Any]:
    keys = {"root_id", "volume_uuid", "device", "inode", "data_root"}
    if type(storage) is not dict or set(storage) != keys:
        raise ActivationError("activation_storage_shape_invalid")
    root_stat = os.fstat(data_root_fd)
    writer_stat = os.fstat(writer.root_fd)
    try:
        normalized_uuid = str(uuid.UUID(cast(str, storage.get("volume_uuid"))))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ActivationError("activation_storage_uuid_invalid") from exc
    root_id = storage.get("root_id")
    data_root = storage.get("data_root")
    if (
        normalized_uuid != storage.get("volume_uuid")
        or type(root_id) is not str
        or re.fullmatch(r"[0-9a-f]{32}", root_id) is None
        or type(data_root) is not str
        or data_root.startswith("/")
        or any(part in {"", ".", ".."} for part in data_root.split("/"))
        or type(storage.get("device")) is not int
        or type(storage.get("inode")) is not int
        or (root_stat.st_dev, root_stat.st_ino) != (storage["device"], storage["inode"])
        or (writer_stat.st_dev, writer_stat.st_ino) != (root_stat.st_dev, root_stat.st_ino)
        or root_stat.st_uid != os.getuid()
        or stat.S_IMODE(root_stat.st_mode) != 0o700
    ):
        raise ActivationError("activation_storage_identity_mismatch")
    return dict(storage)


def _verified_guard_storage(
    *,
    guard: Any,
    data_root_fd: int,
    writer: DescriptorWriter,
    revalidate_storage: Callable[[], Any],
) -> dict[str, Any]:
    try:
        revalidate_storage()
        config = guard.config
        mount_stat = os.fstat(guard.mount_fd)
        root_stat = os.fstat(data_root_fd)
        observed = {
            "root_id": config.root_id,
            "volume_uuid": config.volume_uuid,
            "device": root_stat.st_dev,
            "inode": root_stat.st_ino,
            "data_root": config.data_root,
        }
        _storage_identity(observed, data_root_fd=data_root_fd, writer=writer)
        if mount_stat.st_dev != root_stat.st_dev:
            raise ActivationError("activation_mount_device_mismatch")
        revalidate_storage()
        if (os.fstat(data_root_fd).st_dev, os.fstat(data_root_fd).st_ino) != (
            observed["device"],
            observed["inode"],
        ):
            raise ActivationError("activation_data_root_changed")
        return observed
    except ActivationError:
        raise
    except Exception as exc:
        raise ActivationError("activation_storage_guard_failed") from exc


def _launch_definition(raw: bytes, *, label: str, arguments: Sequence[str]) -> dict[str, Any]:
    try:
        plist = plistlib.loads(raw)
    except (plistlib.InvalidFileException, ValueError, TypeError) as exc:
        raise ActivationError("activation_plist_invalid") from exc
    if (
        type(plist) is not dict
        or plist.get("Label") != label
        or plist.get("ProgramArguments") != list(arguments)
        or type(label) is not str
        or not label
        or type(arguments) not in {list, tuple}
        or len(arguments) != 4
        or any(type(arg) is not str or not arg or "\x00" in arg for arg in arguments)
        or list(arguments)[2] != "--activation-id"
    ):
        raise ActivationError("activation_plist_binding_mismatch")
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "label": label,
        "program_arguments": list(arguments),
    }


def _cert_hash(value: Any, *, nullable: bool) -> bool:
    return (nullable and value is None) or _sha(value)


def prepare_activation_record(
    *,
    activation_id: str,
    phase: str,
    source_commit: str,
    source_manifest_raw: bytes,
    source_manifest_sha256: str,
    source_map: Mapping[str, str],
    repo_root: Path,
    service_root: str,
    binding: Mapping[str, Any],
    certificate_sha256: str,
    policy_sha256: str,
    population_sha256: str,
    plan_sha256: str,
    storage: Mapping[str, Any],
    data_root_fd: int,
    writer: DescriptorWriter,
    storage_guard: Any,
    collector_plist_raw: bytes,
    collector_plist_path: str,
    collector_loaded_path: str,
    collector_label: str,
    collector_arguments: Sequence[str],
    watchdog_plist_raw: bytes,
    watchdog_plist_path: str,
    watchdog_loaded_path: str,
    watchdog_label: str,
    watchdog_arguments: Sequence[str],
    predecessor_certificate_sha256: str | None,
    supervisor_certificate_sha256: str | None,
    revalidate_storage: Callable[[], Any],
) -> dict[str, Any]:
    """Build a record only after commit, source, descriptor, and plist checks."""
    activation_path(activation_id)
    if phase not in PHASES:
        raise ActivationError("activation_phase_invalid")
    if any(
        not _sha(value)
        for value in (
            source_manifest_sha256,
            certificate_sha256,
            policy_sha256,
            population_sha256,
            plan_sha256,
        )
    ):
        raise ActivationError("activation_hash_invalid")
    if not _cert_hash(predecessor_certificate_sha256, nullable=True) or not _cert_hash(
        supervisor_certificate_sha256, nullable=True
    ):
        raise ActivationError("activation_predecessor_hash_invalid")
    if predecessor_certificate_sha256 is None or supervisor_certificate_sha256 is None:
        raise ActivationError("activation_trust_chain_incomplete")
    safe_sources = _safe_source_map(cast(Mapping[str, str], dict(source_map)))
    verify_source_commit(repo_root=repo_root, source_commit=source_commit, source_map=safe_sources)
    _verify_manifest(
        source_manifest_raw,
        source_commit=source_commit,
        source_manifest_sha256=source_manifest_sha256,
        source_map=safe_sources,
    )
    storage_id = _verified_guard_storage(
        guard=storage_guard,
        data_root_fd=data_root_fd,
        writer=writer,
        revalidate_storage=revalidate_storage,
    )
    if dict(storage) != storage_id:
        raise ActivationError("activation_storage_input_mismatch")
    if (
        type(service_root) is not str
        or service_root.startswith("/")
        or any(part in {"", ".", ".."} for part in service_root.split("/"))
    ):
        raise ActivationError("activation_service_root_invalid")
    if (
        type(binding) is not dict
        or binding.get("root") != service_root
        or binding.get("schema") != "brain-senses-gecko-scout-chain-v2-binding"
        or binding.get("schema_version") != 2
        or binding.get("storage", {}).get("root_id") != storage_id["root_id"]
        or binding.get("storage", {}).get("volume_uuid") != storage_id["volume_uuid"]
        or binding.get("storage", {}).get("device") != os.fstat(data_root_fd).st_dev
        or binding.get("storage", {}).get("inode") != os.fstat(data_root_fd).st_ino
        or binding.get("policy_sha256") != policy_sha256
        or binding.get("population_sha256") != population_sha256
        or binding.get("cutover_certificate_sha256") != certificate_sha256
    ):
        raise ActivationError("activation_binding_mismatch")
    collector = _launch_definition(
        collector_plist_raw, label=collector_label, arguments=collector_arguments
    )
    watchdog = _launch_definition(
        watchdog_plist_raw, label=watchdog_label, arguments=watchdog_arguments
    )
    if collector_label == watchdog_label or collector_plist_path == watchdog_plist_path:
        raise ActivationError("activation_service_identity_duplicate")
    for loaded_path in (collector_loaded_path, watchdog_loaded_path):
        if (
            type(loaded_path) is not str
            or not loaded_path.startswith("/")
            or any(part in {"", ".", ".."} for part in loaded_path[1:].split("/"))
        ):
            raise ActivationError("activation_loaded_plist_path_invalid")
    if (
        collector_plist_path not in safe_sources
        or watchdog_plist_path not in safe_sources
        or collector_loaded_path == watchdog_loaded_path
        or safe_sources[collector_plist_path] != collector["sha256"]
        or safe_sources[watchdog_plist_path] != watchdog["sha256"]
    ):
        raise ActivationError("activation_plist_source_pin_mismatch")
    for args in (collector_arguments, watchdog_arguments):
        if "--activation-id" not in args:
            raise ActivationError("activation_argv_missing_id")
        index = list(args).index("--activation-id")
        if index + 1 >= len(args) or args[index + 1] != activation_id:
            raise ActivationError("activation_argv_id_mismatch")
    revalidate_storage()
    if (
        _verified_guard_storage(
            guard=storage_guard,
            data_root_fd=data_root_fd,
            writer=writer,
            revalidate_storage=revalidate_storage,
        )
        != storage_id
    ):
        raise ActivationError("activation_storage_changed")
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "activation_id": activation_id,
        "activation_path": activation_path(activation_id),
        "phase": phase,
        "source_commit": source_commit,
        "source_manifest_sha256": source_manifest_sha256,
        "source_map": safe_sources,
        "service_root": service_root,
        "binding_sha256": hashlib.sha256(canonical_json(dict(binding))).hexdigest(),
        "certificate_sha256": certificate_sha256,
        "policy_sha256": policy_sha256,
        "population_sha256": population_sha256,
        "plan_sha256": plan_sha256,
        "storage": storage_id,
        "collector": {
            "source_path": collector_plist_path,
            "loaded_path": collector_loaded_path,
            **collector,
        },
        "watchdog": {
            "source_path": watchdog_plist_path,
            "loaded_path": watchdog_loaded_path,
            **watchdog,
        },
        "predecessor_certificate_sha256": predecessor_certificate_sha256,
        "supervisor_certificate_sha256": supervisor_certificate_sha256,
    }


def validate_activation_record(
    *,
    record_raw: bytes,
    expected_activation_id: str,
    source_manifest_raw: bytes,
    repo_root: Path,
    data_root_fd: int,
    writer: DescriptorWriter,
    storage_guard: Any,
    revalidate_storage: Callable[[], Any],
    collector_plist_raw: bytes,
    watchdog_plist_raw: bytes,
    launch_snapshot: Mapping[str, Any],
) -> VerifiedActivation:
    """Validate persisted identity and loaded service definitions before runtime reads."""
    record = _canonical(record_raw)
    expected_keys = {
        "schema",
        "schema_version",
        "activation_id",
        "activation_path",
        "phase",
        "source_commit",
        "source_manifest_sha256",
        "source_map",
        "service_root",
        "binding_sha256",
        "certificate_sha256",
        "policy_sha256",
        "population_sha256",
        "plan_sha256",
        "storage",
        "collector",
        "watchdog",
        "predecessor_certificate_sha256",
        "supervisor_certificate_sha256",
    }
    source_commit = record.get("source_commit")
    try:
        source_map = _safe_source_map(cast(Mapping[str, str], record.get("source_map")))
    except (ActivationError, TypeError) as exc:
        raise ActivationError("activation_record_source_map_invalid") from exc
    if (
        set(record) != expected_keys
        or record.get("schema") != SCHEMA
        or record.get("schema_version") != SCHEMA_VERSION
        or record.get("activation_id") != expected_activation_id
        or record.get("activation_path") != activation_path(expected_activation_id)
        or record.get("phase") not in PHASES
        or not _commit(source_commit)
        or record.get("source_map") != source_map
        or record.get("source_manifest_sha256") != hashlib.sha256(source_manifest_raw).hexdigest()
        or type(record.get("service_root")) is not str
        or record["service_root"].startswith("/")
        or any(part in {"", ".", ".."} for part in record["service_root"].split("/"))
        or not all(
            _sha(record.get(field))
            for field in (
                "binding_sha256",
                "certificate_sha256",
                "policy_sha256",
                "population_sha256",
                "plan_sha256",
            )
        )
        or not _sha(record.get("predecessor_certificate_sha256"))
        or not _sha(record.get("supervisor_certificate_sha256"))
    ):
        raise ActivationError("activation_record_binding_mismatch")
    verify_source_commit(
        repo_root=repo_root, source_commit=cast(str, source_commit), source_map=source_map
    )
    _verify_manifest(
        source_manifest_raw,
        source_commit=cast(str, source_commit),
        source_manifest_sha256=cast(str, record["source_manifest_sha256"]),
        source_map=source_map,
    )
    observed_storage = _verified_guard_storage(
        guard=storage_guard,
        data_root_fd=data_root_fd,
        writer=writer,
        revalidate_storage=revalidate_storage,
    )
    if record.get("storage") != observed_storage:
        raise ActivationError("activation_storage_identity_mismatch")
    collector = record.get("collector")
    watchdog = record.get("watchdog")
    if (
        type(collector) is not dict
        or set(collector) != {"source_path", "loaded_path", "sha256", "label", "program_arguments"}
        or type(watchdog) is not dict
        or set(watchdog) != {"source_path", "loaded_path", "sha256", "label", "program_arguments"}
        or hashlib.sha256(collector_plist_raw).hexdigest() != collector.get("sha256")
        or hashlib.sha256(watchdog_plist_raw).hexdigest() != watchdog.get("sha256")
    ):
        raise ActivationError("activation_plist_hash_mismatch")
    if (
        collector["source_path"] not in source_map
        or watchdog["source_path"] not in source_map
        or source_map[collector["source_path"]] != collector["sha256"]
        or source_map[watchdog["source_path"]] != watchdog["sha256"]
        or type(collector["loaded_path"]) is not str
        or not collector["loaded_path"].startswith("/")
        or any(part in {"", ".", ".."} for part in collector["loaded_path"][1:].split("/"))
        or type(watchdog["loaded_path"]) is not str
        or not watchdog["loaded_path"].startswith("/")
        or any(part in {"", ".", ".."} for part in watchdog["loaded_path"][1:].split("/"))
        or collector["loaded_path"] == watchdog["loaded_path"]
    ):
        raise ActivationError("activation_plist_source_pin_mismatch")
    _launch_definition(
        collector_plist_raw,
        label=cast(str, collector["label"]),
        arguments=cast(Sequence[str], collector["program_arguments"]),
    )
    _launch_definition(
        watchdog_plist_raw,
        label=cast(str, watchdog["label"]),
        arguments=cast(Sequence[str], watchdog["program_arguments"]),
    )
    for role in (collector, watchdog):
        args = role["program_arguments"]
        if type(args) is not list or args[2:] != ["--activation-id", expected_activation_id]:
            raise ActivationError("activation_argv_id_mismatch")
    loaded = launch_snapshot.get("loaded_services") if type(launch_snapshot) is dict else None
    expected_loaded = [
        {
            "label": collector["label"],
            "plist_path": collector["loaded_path"],
            "program_arguments": collector["program_arguments"],
            "uid": os.getuid(),
        },
        {
            "label": watchdog["label"],
            "plist_path": watchdog["loaded_path"],
            "program_arguments": watchdog["program_arguments"],
            "uid": os.getuid(),
        },
    ]
    if (
        type(loaded) is not list
        or any(
            type(row) is not dict or set(row) != {"label", "plist_path", "program_arguments", "uid"}
            for row in loaded
        )
        or sorted(loaded, key=lambda row: row["label"])
        != sorted(expected_loaded, key=lambda row: row["label"])
    ):
        raise ActivationError("activation_loaded_services_mismatch")
    if (
        _verified_guard_storage(
            guard=storage_guard,
            data_root_fd=data_root_fd,
            writer=writer,
            revalidate_storage=revalidate_storage,
        )
        != observed_storage
    ):
        raise ActivationError("activation_storage_changed_during_gate")
    return VerifiedActivation(
        activation_id=expected_activation_id,
        record_sha256=hashlib.sha256(record_raw).hexdigest(),
        record=record,
    )


def verify_activation_lineage(
    activation: VerifiedActivation,
    *,
    binding: Mapping[str, Any],
    certificate_sha256: str,
    policy_sha256: str,
    population_sha256: str,
    plan_sha256: str,
) -> None:
    """Join fresh capture replay to the pre-chain activation gate afterward."""
    record = activation.record
    if (
        hashlib.sha256(canonical_json(dict(record))).hexdigest() != activation.record_sha256
        or record.get("service_root") != binding.get("root")
        or record.get("binding_sha256") != hashlib.sha256(canonical_json(dict(binding))).hexdigest()
        or record.get("certificate_sha256") != certificate_sha256
        or record.get("policy_sha256") != policy_sha256
        or record.get("population_sha256") != population_sha256
        or record.get("plan_sha256") != plan_sha256
    ):
        raise ActivationError("activation_fresh_lineage_mismatch")


def bind_verified_activation(activation: VerifiedActivation, binding: Any) -> Any:
    """Attach dispatch authority only after fresh capture replay matches the record."""
    record = activation.record
    if (
        not hasattr(binding, "service_root")
        or hashlib.sha256(canonical_json(dict(record))).hexdigest() != activation.record_sha256
        or record.get("service_root") != binding.service_root
        or record.get("binding_sha256") != binding.binding_sha256
        or record.get("source_manifest_sha256") != binding.source_manifest_sha256
        or record.get("source_map") != dict(binding.source_bindings)
        or record.get("storage") != dict(binding.mount_storage)
        or record.get("policy_sha256") != binding.policy_sha256
        or record.get("certificate_sha256") != binding.certificate_sha256
        or record.get("plan_sha256") != binding.plan_sha256
    ):
        raise ActivationError("activation_owner_binding_mismatch")
    return replace(binding, activation_record_sha256=activation.record_sha256)


def create_activation_record(
    *,
    writer: DescriptorWriter,
    record: Mapping[str, Any],
    data_root_fd: int,
    revalidate_storage: Callable[[], Any],
) -> str:
    """Create a predeclared control record once and verify descriptor readback."""
    if type(record) is not dict or record.get("schema") != SCHEMA:
        raise ActivationError("activation_record_schema_invalid")
    relative = activation_path(cast(str, record.get("activation_id")))
    required = {
        "schema",
        "schema_version",
        "activation_id",
        "activation_path",
        "phase",
        "source_commit",
        "source_manifest_sha256",
        "source_map",
        "service_root",
        "binding_sha256",
        "certificate_sha256",
        "policy_sha256",
        "population_sha256",
        "plan_sha256",
        "storage",
        "collector",
        "watchdog",
        "predecessor_certificate_sha256",
        "supervisor_certificate_sha256",
    }
    if set(record) != required or record.get("activation_path") != relative:
        raise ActivationError("activation_record_shape_invalid")
    try:
        writer.list_names(ACTIVATION_DIRECTORY)
    except Exception as exc:
        raise ActivationError("activation_control_namespace_unavailable") from exc
    writer_stat = os.fstat(writer.root_fd)
    data_stat = os.fstat(data_root_fd)
    if (writer_stat.st_dev, writer_stat.st_ino) != (data_stat.st_dev, data_stat.st_ino):
        raise ActivationError("activation_writer_root_mismatch")
    raw = canonical_json(dict(record))
    revalidate_storage()
    try:
        writer.atomic_write(relative, raw, create_only=True)
    except StorageError as exc:
        if "external_writer_create_only_collision" in exc.args:
            raise ActivationError("activation_record_create_only_collision") from exc
        raise ActivationError("activation_record_create_failed") from exc
    except Exception as exc:
        raise ActivationError("activation_record_create_failed") from exc
    revalidate_storage()
    observed = writer.read_bytes(relative, max_bytes=128 * 1024)
    if observed != raw:
        raise ActivationError("activation_record_readback_mismatch")
    return hashlib.sha256(raw).hexdigest()


def read_activation_record(writer: DescriptorWriter, activation_id: str) -> bytes:
    """Read the unique pinned control record; absence is an authorization failure."""
    try:
        return cast(bytes, writer.read_bytes(activation_path(activation_id), max_bytes=128 * 1024))
    except (OSError, ValueError) as exc:
        raise ActivationError("activation_record_unavailable") from exc


__all__ = [
    "ACTIVATION_DIRECTORY",
    "ActivationError",
    "SCHEMA",
    "VerifiedActivation",
    "activation_path",
    "bind_verified_activation",
    "create_activation_record",
    "prepare_activation_record",
    "read_activation_record",
    "validate_activation_record",
    "verify_source_commit",
    "verify_activation_lineage",
]
