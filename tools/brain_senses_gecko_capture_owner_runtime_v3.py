"""Fail-closed owner callback core for the Gecko capture-only v3 successor.

This module is intentionally not a launchd/credential/provider entry point.  It
exposes a descriptor-bound read-only preflight and a single-attempt transaction
primitive for the reviewed owner shell to compose.  The transaction never
replays an intent and re-runs its gate immediately before transport.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import pickle
import plistlib
import signal
import stat
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from tools import brain_senses_gecko_capture_owner_preflight_v2 as preflight_v2
from tools.brain_senses_external_writer_v1 import canonical_json

SCHEMA = "brain-senses-gecko-capture-owner-runtime-v3"
RELEASE_SCHEMA = "brain-senses-gecko-capture-owner-release-v2"
MAX_CHILD_SECONDS = 30
MAX_CHILD_RESULT_BYTES = 8 * 1024 * 1024
MAX_TRANSPORT_BODY_BYTES = 16 * 1024
TRANSPORT_CLOCK_DRIFT_NS = 2_000_000_000
_HASH_LEN = 64


class OwnerRuntimeError(ValueError):
    """Owner callback context or one-shot transaction failed closed."""


@dataclass(frozen=True)
class OwnerBinding:
    release_id: str
    service_root: str
    release_sha256: str
    source_bindings: Mapping[str, str]
    storage: Mapping[str, Any]
    mount_storage: Mapping[str, Any]
    storage_config: Mapping[str, Any]
    policy_sha256: str
    certificate_sha256: str
    plan_sha256: str
    source_manifest_sha256: str
    binding_sha256: str
    activation_record_sha256: str | None = None


def _hash(value: Any) -> bool:
    return (
        type(value) is str
        and len(value) == _HASH_LEN
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _canonical_object(raw: bytes, reason: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, OwnerRuntimeError) as exc:
        raise OwnerRuntimeError(reason) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise OwnerRuntimeError(reason)
    return value


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OwnerRuntimeError("owner_runtime_duplicate_json_key")
        result[key] = value
    return result


def _open_relative_directory(root_fd: int, relative: str) -> int:
    parts = relative.split("/")
    if not relative or relative.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        raise OwnerRuntimeError("owner_runtime_relative_directory_invalid")
    current = os.dup(root_fd)
    try:
        for part in parts:
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = next_fd
        result, current = current, -1
        return result
    finally:
        if current >= 0:
            os.close(current)


def _verify_clean_source_commit(repo_root: Path, expected_commit: str) -> None:
    """Require the manifest's source bytes to be from the exact clean checkout."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        status = subprocess.run(
            [
                "git",
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--",
                *preflight_v2.SOURCE_PATHS,
            ],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise OwnerRuntimeError("owner_runtime_source_git_identity_unavailable") from exc
    if head != expected_commit or status:
        raise OwnerRuntimeError("owner_runtime_source_git_identity_mismatch")


def verify_release_material(
    *,
    release_raw: bytes,
    source_manifest_raw: bytes,
    repo_root: Path,
    expected_source_manifest_sha256: str,
    expected_source_commit: str,
    plist_raw: bytes,
    watchdog_raw: bytes,
    clock_record: Mapping[str, Any],
    capture_binding: Mapping[str, Any],
    policy: Mapping[str, Any],
    certificate: Mapping[str, Any],
    chain_records: Sequence[Mapping[str, Any]],
    expected_sources: Mapping[str, str],
    storage: Mapping[str, Any],
    storage_config: Mapping[str, Any],
    mount_fd: int,
    data_root_fd: int,
    service_fd: int,
    storage_guard: Any,
    revalidate_storage: Callable[[], Any],
    preflight_context: Mapping[str, Any],
) -> OwnerBinding:
    """Adapt the preflight-v2 release to a verified capture binding, without adding files."""
    revalidate_storage()
    release = _canonical_object(release_raw, "owner_runtime_release_invalid")
    _verify_clean_source_commit(repo_root, expected_source_commit)
    try:
        source_manifest = preflight_v2.verify_source_manifest(
            source_manifest_raw,
            repo_root=repo_root,
            expected_manifest_sha256=expected_source_manifest_sha256,
            expected_source_commit=expected_source_commit,
        )
    except (preflight_v2.PreflightError, OSError) as exc:
        raise OwnerRuntimeError("owner_runtime_source_manifest_replay_failed") from exc
    manifest_core = {
        key: value for key, value in source_manifest.items() if key != "manifest_sha256"
    }
    manifest_sources = source_manifest.get("sources")
    source_paths = cast(list[Any], source_manifest.get("source_paths"))
    if (
        set(source_manifest)
        != {
            "schema",
            "schema_version",
            "source_commit",
            "source_paths",
            "sources",
            "manifest_sha256",
        }
        or type(source_manifest.get("source_commit")) is not str
        or len(source_manifest["source_commit"]) != 40
        or any(ch not in "0123456789abcdef" for ch in source_manifest["source_commit"])
        or type(source_paths) is not list
        or len(source_paths) != len(expected_sources)
        or any(type(path) is not str for path in source_paths)
        or set(source_paths) != set(expected_sources)
        or source_manifest.get("schema") != "brain-senses-gecko-capture-source-manifest-v2"
        or source_manifest.get("schema_version") != 2
        or source_manifest.get("manifest_sha256")
        != hashlib.sha256(canonical_json(manifest_core)).hexdigest()
        or source_manifest.get("manifest_sha256") != expected_source_manifest_sha256
        or source_manifest.get("source_commit") != expected_source_commit
        or type(manifest_sources) is not dict
        or set(manifest_sources) != set(expected_sources)
        or any(
            type(manifest_sources[path]) is not dict
            or set(manifest_sources[path]) != {"bytes", "sha256"}
            or manifest_sources[path].get("sha256") != digest
            or type(manifest_sources[path].get("bytes")) is not int
            or manifest_sources[path]["bytes"] < 0
            for path, digest in expected_sources.items()
        )
    ):
        raise OwnerRuntimeError("owner_runtime_source_manifest_binding_invalid")

    if (
        type(capture_binding) is not dict
        or capture_binding.get("schema") != "brain-senses-gecko-scout-chain-v2-binding"
        or capture_binding.get("schema_version") != 2
        or set(capture_binding.get("storage", {})) != {"root_id", "volume_uuid", "device", "inode"}
        or set(storage) != {"root_id", "volume_uuid", "device", "inode", "data_root"}
        or not chain_records
    ):
        raise OwnerRuntimeError("owner_runtime_capture_binding_invalid")
    root = capture_binding.get("root")
    if (
        type(root) is not str
        or not root.startswith("services/gecko-market-v1/capture-only-v2/")
        or len(root.split("/")) != 4
        or any(part in {"", ".", ".."} for part in root.split("/"))
    ):
        raise OwnerRuntimeError("owner_runtime_capture_root_invalid")
    plan = chain_records[-1]
    if plan.get("schema") != "brain-senses-gecko-scout-chain-v2-plan":
        raise OwnerRuntimeError("owner_runtime_plan_not_chain_tail")
    release_expected = preflight_v2.compile_owner_release(
        manifest=source_manifest,
        storage=storage,
        plan_record=plan,
        certificate=certificate,
        binding=capture_binding,
        policy=policy,
        chain_tail_sha256=str(plan.get("record_sha256", "")),
        plist_raw=plist_raw,
        watchdog_raw=watchdog_raw,
        clock_record=clock_record,
        label=str(release.get("label", "")),
        program_arguments=cast(Sequence[str], release.get("program_arguments", [])),
        watchdog_label=str(release.get("watchdog_label", "")),
        watchdog_program_arguments=cast(
            Sequence[str], release.get("watchdog_program_arguments", [])
        ),
    )
    if release != release_expected:
        raise OwnerRuntimeError("owner_runtime_release_preflight_v2_mismatch")
    try:
        plist = plistlib.loads(plist_raw)
        watchdog = plistlib.loads(watchdog_raw)
    except (plistlib.InvalidFileException, ValueError, TypeError) as exc:
        raise OwnerRuntimeError("owner_runtime_launch_definition_invalid") from exc
    if (
        type(plist) is not dict
        or type(watchdog) is not dict
        or plist.get("Label") != release.get("label")
        or plist.get("ProgramArguments") != release.get("program_arguments")
        or plist.get("StartInterval") != 12
        or plist.get("RunAtLoad") is not True
        or plist.get("KeepAlive") is not False
        or watchdog.get("Label") != release.get("watchdog_label")
        or watchdog.get("ProgramArguments") != release.get("watchdog_program_arguments")
        or watchdog.get("StartInterval") != 15
        or watchdog.get("RunAtLoad") is not True
        or watchdog.get("KeepAlive") != {"SuccessfulExit": False}
        or release.get("clock_sha256")
        != hashlib.sha256(canonical_json(dict(clock_record))).hexdigest()
    ):
        raise OwnerRuntimeError("owner_runtime_launch_definition_binding_invalid")
    capture_storage = cast(Mapping[str, Any], capture_binding["storage"])
    data_root_stat = os.fstat(data_root_fd)
    if (
        storage.get("root_id") != storage_config.get("root_id")
        or storage.get("volume_uuid") != storage_config.get("volume_uuid")
        or storage.get("data_root") != storage_config.get("data_root")
        or type(storage.get("volume_uuid")) is not str
        or str(uuid.UUID(cast(str, storage.get("volume_uuid")))) != storage.get("volume_uuid")
        or type(storage.get("root_id")) is not str
        or len(cast(str, storage.get("root_id"))) != 32
        or any(ch not in "0123456789abcdef" for ch in cast(str, storage.get("root_id")))
        or type(storage_config.get("data_root")) is not str
        or storage_config.get("data_root", "").startswith("/")
        or any(part in {"", ".", ".."} for part in storage_config.get("data_root", "").split("/"))
        or storage.get("device") != os.fstat(mount_fd).st_dev
        or storage.get("inode") != os.fstat(mount_fd).st_ino
        or data_root_stat.st_dev != os.fstat(mount_fd).st_dev
        or data_root_stat.st_uid != os.getuid()
        or stat.S_IMODE(data_root_stat.st_mode) != 0o700
        or capture_storage.get("root_id") != storage.get("root_id")
        or capture_storage.get("volume_uuid") != storage.get("volume_uuid")
        or capture_storage.get("device") != data_root_stat.st_dev
        or capture_storage.get("inode") != data_root_stat.st_ino
        or os.fstat(service_fd).st_dev != data_root_stat.st_dev
        or os.fstat(service_fd).st_uid != os.getuid()
        or stat.S_IMODE(os.fstat(service_fd).st_mode) != 0o700
    ):
        raise OwnerRuntimeError("owner_runtime_storage_identity_invalid")
    context_keys = {
        "repo_root",
        "source_manifest_raw",
        "expected_source_manifest_sha256",
        "source_commit",
        "release",
        "expected_storage",
        "mount_fd",
        "data_root_fd",
        "service_fd",
        "source_reader_fd",
        "writer_root_fd",
        "storage_guard",
        "plist_raw",
        "watchdog_raw",
        "launch_snapshot",
        "policy",
        "certificate",
        "certificate_inputs",
        "chain_records",
        "plan_record",
        "capture_inputs",
        "inventory_inputs",
        "clock_record",
        "now_ns",
        "monotonic_ns",
        "expected_label",
        "expected_program_arguments",
    }
    if set(preflight_context) != context_keys:
        raise OwnerRuntimeError("owner_runtime_preflight_context_shape")
    expected_context = {
        "repo_root": repo_root,
        "source_manifest_raw": source_manifest_raw,
        "expected_source_manifest_sha256": expected_source_manifest_sha256,
        "source_commit": expected_source_commit,
        "release": release,
        "expected_storage": dict(storage),
        "mount_fd": mount_fd,
        "data_root_fd": data_root_fd,
        "service_fd": service_fd,
        "source_reader_fd": service_fd,
        "writer_root_fd": service_fd,
        "storage_guard": storage_guard,
        "plist_raw": plist_raw,
        "watchdog_raw": watchdog_raw,
        "policy": policy,
        "certificate": certificate,
        "chain_records": list(chain_records),
        "plan_record": plan,
        "clock_record": dict(clock_record),
        "expected_label": release.get("label"),
        "expected_program_arguments": release.get("program_arguments"),
    }
    for key, expected_value in expected_context.items():
        observed_value = preflight_context.get(key)
        if key in {"storage_guard"}:
            matches = observed_value is expected_value
        elif key in {
            "mount_fd",
            "data_root_fd",
            "service_fd",
            "source_reader_fd",
            "writer_root_fd",
        }:
            try:
                matches = os.fstat(cast(int, observed_value)) == os.fstat(cast(int, expected_value))
            except (OSError, TypeError):
                matches = False
        else:
            matches = observed_value == expected_value
        if not matches:
            raise OwnerRuntimeError("owner_runtime_preflight_context_binding")
    if (
        preflight_context.get("launch_snapshot") != {"active_gecko_labels": []}
        or type(preflight_context.get("now_ns")) is not int
        or type(preflight_context.get("monotonic_ns")) is not int
        or type(preflight_context.get("certificate_inputs")) is not dict
        or type(preflight_context.get("capture_inputs")) is not dict
        or preflight_context["capture_inputs"].get("binding") != capture_binding
        or preflight_context["capture_inputs"].get("chain_records") != list(chain_records)
        or type(preflight_context.get("inventory_inputs")) is not dict
    ):
        raise OwnerRuntimeError("owner_runtime_preflight_context_binding")
    try:
        preflight_result = preflight_v2.preflight_owner_host(**dict(preflight_context))
    except (preflight_v2.PreflightError, OSError, TypeError, ValueError) as exc:
        raise OwnerRuntimeError("owner_runtime_preflight_v2_failed") from exc
    if (
        type(preflight_result) is not dict
        or preflight_result.get("status") != "verified_inert_preflight"
        or preflight_result.get("provider_io") is not False
        or preflight_result.get("dispatch_authorized") is not False
        or preflight_result.get("activation_authorized") is not False
        or preflight_result.get("source_manifest_sha256") != expected_source_manifest_sha256
        or preflight_result.get("plan_sha256") != hashlib.sha256(canonical_json(plan)).hexdigest()
        or preflight_result.get("certificate_sha256") != certificate.get("certificate_sha256")
    ):
        raise OwnerRuntimeError("owner_runtime_preflight_v2_result_invalid")
    revalidate_storage()
    if (os.fstat(mount_fd).st_dev, os.fstat(mount_fd).st_ino) != (
        storage.get("device"),
        storage.get("inode"),
    ):
        raise OwnerRuntimeError("owner_runtime_storage_changed")
    release_id = root.rsplit("/", 1)[-1]
    return OwnerBinding(
        release_id=release_id,
        service_root=root,
        release_sha256=cast(str, release.get("release_sha256")),
        source_bindings=dict(expected_sources),
        storage=dict(capture_storage),
        mount_storage=dict(storage),
        storage_config={"data_root": storage_config["data_root"]},
        policy_sha256=cast(str, release["policy_sha256"]),
        certificate_sha256=cast(str, release["certificate_sha256"]),
        plan_sha256=cast(str, release["plan_sha256"]),
        source_manifest_sha256=cast(str, release["source_manifest_sha256"]),
        binding_sha256=hashlib.sha256(canonical_json(dict(capture_binding))).hexdigest(),
    )


def load_and_verify_release(
    *,
    writer: Any,
    service_root: str,
    release_raw: bytes,
    source_manifest_raw: bytes,
    repo_root: Path,
    expected_source_manifest_sha256: str,
    expected_source_commit: str,
    plist_raw: bytes,
    watchdog_raw: bytes,
    clock_record: Mapping[str, Any],
    expected_sources: Mapping[str, str],
    storage_config: Mapping[str, Any],
    guard: Any,
    minimum_free_bytes: int,
    preflight_context: Mapping[str, Any],
) -> OwnerBinding:
    """Read only inventory-approved capture files; release/manifest stay host inputs."""
    if (
        type(service_root) is not str
        or service_root.startswith("/")
        or any(part in {"", ".", ".."} for part in service_root.split("/"))
    ):
        raise OwnerRuntimeError("owner_runtime_service_root_invalid")
    try:
        allowed_entries = {
            ".runner.lock",
            "binding.json",
            "policy.json",
            "predecessor-cutover.json",
            "chain",
            "intents",
            "results",
            "confirmations",
            "skips",
            "dispatch.jsonl",
        }
        entries = set(writer.list_names(service_root))
        if not {"binding.json", "policy.json", "predecessor-cutover.json", "chain"} <= entries:
            raise OwnerRuntimeError("owner_runtime_capture_root_incomplete")
        if not entries <= allowed_entries:
            raise OwnerRuntimeError("owner_runtime_capture_root_extra_files")
        payloads = {
            name: writer.read_bytes(f"{service_root}/{name}", max_bytes=4 * 1024 * 1024)
            for name in ("binding.json", "policy.json", "predecessor-cutover.json")
        }
        capture_binding = _canonical_object(
            payloads["binding.json"], "owner_runtime_capture_binding_invalid"
        )
        if capture_binding.get("root") != service_root:
            raise OwnerRuntimeError("owner_runtime_capture_root_binding_mismatch")
        chain_names = writer.list_names(f"{service_root}/chain")
        expected_chain_names = tuple(f"{index:08d}.json" for index in range(len(chain_names)))
        if not chain_names or chain_names != expected_chain_names:
            raise OwnerRuntimeError("owner_runtime_chain_file_closure")
        chain_records = [
            _canonical_object(
                writer.read_bytes(f"{service_root}/chain/{name}", max_bytes=4 * 1024 * 1024),
                "owner_runtime_chain_record_invalid",
            )
            for name in chain_names
        ]
    except Exception as exc:
        raise OwnerRuntimeError("owner_runtime_release_read_failed") from exc

    def revalidate() -> None:
        try:
            state = guard.revalidate(min_free_bytes=minimum_free_bytes)
        except Exception as exc:
            raise OwnerRuntimeError("owner_runtime_storage_revalidation_failed") from exc
        config = guard.config
        mount_stat = os.fstat(guard.mount_fd)
        capture_storage = capture_binding.get("storage", {})
        if (
            getattr(state, "available_bytes", minimum_free_bytes) < minimum_free_bytes
            or config.root_id != capture_storage.get("root_id")
            or config.volume_uuid != capture_storage.get("volume_uuid")
            or config.root_id != storage_config.get("root_id")
            or config.volume_uuid != storage_config.get("volume_uuid")
            or config.data_root != storage_config.get("data_root")
            or mount_stat.st_dev != capture_storage.get("device")
        ):
            raise OwnerRuntimeError("owner_runtime_storage_identity_changed")

    revalidate()
    data_root_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)
    service_fd = _open_relative_directory(data_root_fd, service_root)
    try:
        bound_preflight_context = dict(preflight_context)
        mount_stat = os.fstat(guard.mount_fd)
        release_storage = {
            "root_id": capture_binding["storage"]["root_id"],
            "volume_uuid": capture_binding["storage"]["volume_uuid"],
            "device": mount_stat.st_dev,
            "inode": mount_stat.st_ino,
            "data_root": guard.config.data_root,
        }
        bound_preflight_context.update(
            {
                "repo_root": repo_root,
                "source_manifest_raw": source_manifest_raw,
                "expected_source_manifest_sha256": expected_source_manifest_sha256,
                "source_commit": expected_source_commit,
                "release": _canonical_object(release_raw, "owner_runtime_release_invalid"),
                "expected_storage": release_storage,
                "mount_fd": guard.mount_fd,
                "data_root_fd": data_root_fd,
                "service_fd": service_fd,
                "source_reader_fd": service_fd,
                "writer_root_fd": service_fd,
                "storage_guard": guard,
                "plist_raw": plist_raw,
                "watchdog_raw": watchdog_raw,
                "policy": _canonical_object(
                    payloads["policy.json"], "owner_runtime_policy_invalid"
                ),
                "certificate": _canonical_object(
                    payloads["predecessor-cutover.json"], "owner_runtime_certificate_invalid"
                ),
                "chain_records": chain_records,
                "plan_record": chain_records[-1],
                "clock_record": dict(clock_record),
            }
        )
        binding = verify_release_material(
            release_raw=release_raw,
            source_manifest_raw=source_manifest_raw,
            repo_root=repo_root,
            expected_source_manifest_sha256=expected_source_manifest_sha256,
            expected_source_commit=expected_source_commit,
            plist_raw=plist_raw,
            watchdog_raw=watchdog_raw,
            clock_record=clock_record,
            capture_binding=capture_binding,
            policy=_canonical_object(payloads["policy.json"], "owner_runtime_policy_invalid"),
            certificate=_canonical_object(
                payloads["predecessor-cutover.json"], "owner_runtime_certificate_invalid"
            ),
            chain_records=chain_records,
            expected_sources=expected_sources,
            storage=release_storage,
            storage_config=storage_config,
            mount_fd=guard.mount_fd,
            data_root_fd=data_root_fd,
            service_fd=service_fd,
            storage_guard=guard,
            revalidate_storage=revalidate,
            preflight_context=bound_preflight_context,
        )
        revalidate()
        return binding
    finally:
        os.close(service_fd)
        os.close(data_root_fd)


def verify_root_descriptor(
    *,
    binding: OwnerBinding,
    writer_root_fd: int,
    data_root_fd: int,
    guard: Any,
    minimum_free_bytes: int,
) -> None:
    """Revalidate the exact configured storage identity before each mutation."""
    try:
        state = guard.revalidate(min_free_bytes=minimum_free_bytes)
    except Exception as exc:
        raise OwnerRuntimeError("owner_runtime_storage_revalidation_failed") from exc
    data_stat = os.fstat(data_root_fd)
    writer_stat = os.fstat(writer_root_fd)
    mount_stat = os.fstat(guard.mount_fd)
    expected = binding.storage
    expected_mount = binding.mount_storage
    expected_data_root = binding.storage_config.get("data_root")
    config = guard.config
    try:
        configured_fd = _open_relative_directory(guard.mount_fd, cast(str, expected_data_root))
        configured_stat = os.fstat(configured_fd)
    except (OSError, OwnerRuntimeError, TypeError) as exc:
        raise OwnerRuntimeError("owner_runtime_data_root_descriptor_invalid") from exc
    else:
        os.close(configured_fd)
    if (
        (data_stat.st_dev, data_stat.st_ino) != (writer_stat.st_dev, writer_stat.st_ino)
        or (data_stat.st_dev, data_stat.st_ino) != (configured_stat.st_dev, configured_stat.st_ino)
        or mount_stat.st_dev != expected_mount.get("device")
        or mount_stat.st_ino != expected_mount.get("inode")
        or data_stat.st_dev != mount_stat.st_dev
        or (data_stat.st_dev, data_stat.st_ino) != (expected.get("device"), expected.get("inode"))
        or data_stat.st_uid != os.getuid()
        or stat.S_IMODE(data_stat.st_mode) != 0o700
        or getattr(state, "available_bytes", minimum_free_bytes) < minimum_free_bytes
        or config.root_id != expected.get("root_id")
        or config.volume_uuid != expected.get("volume_uuid")
        or config.data_root != expected_data_root
    ):
        raise OwnerRuntimeError("owner_runtime_storage_identity_changed")


def _child_entry(
    pipe: Any, function: Callable[..., Any], args: tuple[Any, ...], timed: bool
) -> None:
    try:
        os.setsid()
        pipe.send(("ready", None))

        # The reviewed HTTP transport is a single-process urllib call. Refuse
        # helpers that try to create descendants, which keeps the kill group
        # and the request's lifetime coextensive.
        def deny_child_creation(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("child_process_creation_forbidden")

        os.setsid = deny_child_creation
        os.fork = deny_child_creation
        threading.Thread.start = deny_child_creation  # type: ignore[method-assign]
        if hasattr(os, "forkpty"):
            os.forkpty = deny_child_creation
        if hasattr(os, "posix_spawn"):
            os.posix_spawn = deny_child_creation
        if hasattr(os, "posix_spawnp"):
            os.posix_spawnp = deny_child_creation
        os.system = deny_child_creation
        subprocess.Popen = deny_child_creation  # type: ignore[assignment,misc]
        started_wall_ns = time.time_ns()
        started_monotonic_ns = time.monotonic_ns()
        error_class: str | None = None
        try:
            result = function(*args)
        except BaseException as exc:
            result = None
            error_class = type(exc).__name__
        received_wall_ns = time.time_ns()
        received_monotonic_ns = time.monotonic_ns()
        if error_class is not None:
            if timed:
                pipe.send(
                    (
                        "timed_error",
                        {
                            "error_class": error_class,
                            "request_started_ns": started_wall_ns,
                            "request_started_monotonic_ns": started_monotonic_ns,
                            "receipt_ns": received_wall_ns,
                            "receipt_monotonic_ns": received_monotonic_ns,
                        },
                    )
                )
            else:
                pipe.send(("error", error_class))
            return
        if isinstance(result, Mapping):
            for body_key in ("body", "raw_body"):
                body = result.get(body_key)
                if type(body) is bytes and len(body) > MAX_TRANSPORT_BODY_BYTES:
                    pipe.send(("error", "TransportResponseOverLimit"))
                    return
        if timed:
            result = {
                "payload": result,
                "request_started_ns": started_wall_ns,
                "request_started_monotonic_ns": started_monotonic_ns,
                "receipt_ns": received_wall_ns,
                "receipt_monotonic_ns": received_monotonic_ns,
            }
        encoded = pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL)
        if len(encoded) > MAX_CHILD_RESULT_BYTES:
            pipe.send(("error", "TransportResultOverLimit"))
        else:
            pipe.send(("ok", result))
    except BaseException as exc:  # Send a fixed class only; never exception text.
        pipe.send(("error", type(exc).__name__))
    finally:
        pipe.close()


def run_bounded_transport(
    function: Callable[..., Any],
    *args: Any,
    timeout_seconds: int = MAX_CHILD_SECONDS,
    timed: bool = False,
) -> tuple[str, Any]:
    """Execute a single transport callback in a killable child; no retry path."""
    if type(timeout_seconds) is not int or not 0 < timeout_seconds <= MAX_CHILD_SECONDS:
        raise OwnerRuntimeError("owner_runtime_deadline_invalid")
    ctx = multiprocessing.get_context("fork")
    receiver, sender = ctx.Pipe(duplex=False)
    child = ctx.Process(target=_child_entry, args=(sender, function, args, timed))
    started_ns = time.monotonic_ns()
    child.start()
    child_pid = child.pid
    if child_pid is None:
        sender.close()
        receiver.close()
        raise OwnerRuntimeError("owner_runtime_child_pid_unavailable")
    sender.close()
    deadline_ns = started_ns + timeout_seconds * 1_000_000_000
    ready_wait = max(0.0, (deadline_ns - time.monotonic_ns()) / 1_000_000_000)
    if not receiver.poll(ready_wait):
        _terminate_and_reap(child, child_pid, group_ready=False)
        receiver.close()
        return ("transport_uncertain_possibly_spent", None)
    try:
        ready = receiver.recv()
    except (EOFError, OSError):
        _terminate_and_reap(child, child_pid, group_ready=False)
        receiver.close()
        return ("transport_uncertain_possibly_spent", None)
    if ready != ("ready", None):
        _terminate_and_reap(child, child_pid, group_ready=False)
        receiver.close()
        return ("transport_uncertain_possibly_spent", None)
    result: list[Any] = []

    def drain_pipe() -> None:
        try:
            result.append(receiver.recv())
        except BaseException:
            return

    drainer = threading.Thread(target=drain_pipe, daemon=True)
    drainer.start()
    remaining = max(0.0, (deadline_ns - time.monotonic_ns()) / 1_000_000_000)
    child.join(remaining)
    if child.is_alive():
        _terminate_and_reap(child, child_pid, group_ready=True)
        drainer.join(1)
        receiver.close()
        return ("transport_uncertain_possibly_spent", None)
    drainer.join(1)
    receiver.close()
    if time.monotonic_ns() - started_ns > timeout_seconds * 1_000_000_000:
        return ("transport_uncertain_possibly_spent", None)
    message = result[0] if result else ("error", "WorkerExit")
    if not isinstance(message, tuple) or len(message) != 2:
        return ("transport_uncertain_possibly_spent", None)
    kind, value = message
    return (kind, value)


def _terminate_and_reap(child: Any, child_pid: int, *, group_ready: bool) -> None:
    """Kill both the isolated process group and the direct child, then reap it."""
    if group_ready:
        try:
            os.killpg(child_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        os.kill(child_pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    child.join(0.2)
    if child.is_alive():
        if group_ready:
            try:
                os.killpg(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        # Do not return to the caller (and release its lane lock) while this
        # transport process could still send or publish a late result.
        child.join()


def _parent_observed_uncertain_times(
    dispatch_wall_ns: Any, dispatch_monotonic_ns: Any
) -> dict[str, int]:
    """Use dispatch as conservative start and parent observation as uncertain receipt."""
    observed_wall_ns = time.time_ns()
    observed_monotonic_ns = time.monotonic_ns()
    if type(dispatch_wall_ns) is not int or type(dispatch_monotonic_ns) is not int:
        raise OwnerRuntimeError("owner_runtime_dispatch_timing_missing")
    return {
        "request_started_ns": dispatch_wall_ns,
        "request_started_monotonic_ns": dispatch_monotonic_ns,
        "receipt_ns": observed_wall_ns,
        "receipt_monotonic_ns": observed_monotonic_ns,
    }


def execute_one_shot(
    *,
    slot: Mapping[str, Any],
    binding: OwnerBinding,
    backend: Any,
    recheck: Callable[[str], Any],
    timeout_seconds: int = MAX_CHILD_SECONDS,
) -> dict[str, Any]:
    """Run one durable one-shot attempt through an injected descriptor backend.

    Backend methods are explicit persistence boundaries.  The gate is re-run
    before reserve and immediately before network; once intent exists, every
    exception is spent and this function never retries or selects another slot.
    """
    request_id = slot.get("request_id")
    if not _hash(request_id):
        raise OwnerRuntimeError("owner_runtime_request_id_invalid")
    if not isinstance(binding, OwnerBinding) or not _hash(binding.activation_record_sha256):
        raise OwnerRuntimeError("owner_runtime_activation_record_required")
    recheck("before_reservation")
    lease = backend.acquire_ordered_locks(binding)
    try:
        recheck("after_lock_before_reservation")
        reconcile_prefix_before_dispatch(binding=binding, backend=backend)
        intent = backend.build_intent(binding, slot, request_id)
        backend.revalidate_storage()
        reservation = backend.persist_intent(intent, lease)
        backend.revalidate_storage()
        dispatch = backend.persist_dispatch(intent, reservation, lease)
        provider_io_possible = False
        error_class: str | None = None
        try:
            recheck("before_network")
            backend.revalidate_storage()
            transport = backend.transport_once
            provider_io_possible = True
            status, response = run_bounded_transport(
                transport,
                dict(intent["request"]),
                timeout_seconds=timeout_seconds,
                timed=True,
            )
            if status == "error":
                error_class = response if type(response) is str else "TransportError"
                status = "transport_uncertain_possibly_spent"
        except BaseException as exc:
            if not provider_io_possible:
                # A durable intent/dispatch already owns this slot. The verifier
                # represents this crash edge as an unresolved prefix; do not
                # fabricate a provider-I/O terminal or release the slot.
                raise OwnerRuntimeError("owner_runtime_post_dispatch_gate_failed") from exc
            status, response = (
                "transport_uncertain_possibly_spent",
                None,
            )
            error_class = type(exc).__name__
        else:
            error_class = response if status == "error" and type(response) is str else None
        dispatch_wall_ns = dispatch.get("dispatch_ns")
        dispatch_monotonic_ns = dispatch.get("dispatch_monotonic_ns")
        transport_times: dict[str, int]
        if status in {"ok", "timed_error"} and type(response) is dict:
            response_times = response
            required_time_keys = {
                "request_started_ns",
                "request_started_monotonic_ns",
                "receipt_ns",
                "receipt_monotonic_ns",
            }
            if set(response_times) != (required_time_keys | {"payload"}) and status == "ok":
                raise OwnerRuntimeError("owner_runtime_transport_timing_shape")
            if status == "timed_error" and set(response_times) != (
                required_time_keys | {"error_class"}
            ):
                raise OwnerRuntimeError("owner_runtime_transport_timing_shape")
            transport_times = {
                key: response_times[key]
                for key in required_time_keys
                if type(response_times.get(key)) is int
            }
            if len(transport_times) != len(required_time_keys):
                raise OwnerRuntimeError("owner_runtime_transport_timing_invalid")
            wall_elapsed = transport_times["receipt_ns"] - transport_times["request_started_ns"]
            monotonic_elapsed = (
                transport_times["receipt_monotonic_ns"]
                - transport_times["request_started_monotonic_ns"]
            )
            if (
                type(dispatch_wall_ns) is not int
                or type(dispatch_monotonic_ns) is not int
                or transport_times["request_started_ns"] < dispatch_wall_ns
                or transport_times["request_started_monotonic_ns"] < dispatch_monotonic_ns
                or transport_times["request_started_monotonic_ns"] - dispatch_monotonic_ns
                > timeout_seconds * 1_000_000_000
                or wall_elapsed < 0
                or monotonic_elapsed < 0
                or monotonic_elapsed > timeout_seconds * 1_000_000_000
                or abs(wall_elapsed - monotonic_elapsed) > TRANSPORT_CLOCK_DRIFT_NS
            ):
                status = "transport_uncertain_possibly_spent"
                response = None
                error_class = "TransportClockOrDeadlineInvalid"
                transport_times = _parent_observed_uncertain_times(
                    dispatch_wall_ns, dispatch_monotonic_ns
                )
            elif status == "ok":
                response = response_times["payload"]
            else:
                status = "transport_uncertain_possibly_spent"
                error_class = response_times["error_class"]
                response = None
        else:
            transport_times = _parent_observed_uncertain_times(
                dispatch_wall_ns, dispatch_monotonic_ns
            )
        request = intent.get("request")
        bucket_close_ns = request.get("bucket_close_ns") if type(request) is dict else None
        if (
            type(bucket_close_ns) is not int
            or transport_times["request_started_ns"] >= bucket_close_ns + 215 * 1_000_000_000
            or transport_times["receipt_ns"] >= bucket_close_ns + 240 * 1_000_000_000
        ):
            raise OwnerRuntimeError("owner_runtime_transport_outside_window")
        terminal = backend.build_terminal(
            intent=intent,
            dispatch=dispatch,
            response=response if status == "ok" else None,
            transport_status=status,
            error_class=error_class,
            **transport_times,
        )
        backend.revalidate_storage()
        coordinator_terminal = backend.persist_terminal(
            reservation, terminal, lease, ambiguous=status != "ok"
        )
        backend.revalidate_storage()
        confirmation = backend.persist_confirmation(intent, terminal, coordinator_terminal, lease)
        if type(confirmation) is not dict:
            raise OwnerRuntimeError("owner_runtime_confirmation_unverified")
        replay = backend.verify_attempt(binding, request_id)
        if (
            type(replay) is not dict
            or replay.get("status") not in {"verified_prefix", "verified_capture_prefix_v3"}
            or replay.get("unresolved_intents") != 0
            or type(replay.get("confirmation_count")) is not int
            or replay.get("confirmation_count", 0) < 1
        ):
            raise OwnerRuntimeError("owner_runtime_attempt_replay_failed")
        return {
            "status": terminal.get("status", "unknown"),
            "request_id": request_id,
            "intent_persisted": True,
            "dispatch_persisted": True,
            "provider_io_possible": provider_io_possible,
            "confirmation_persisted": type(confirmation) is dict,
        }
    except BaseException:
        # The durable intent owns the attempt from here onward. Never retry.
        raise
    finally:
        backend.release_ordered_locks(lease)


def reconcile_prefix_before_dispatch(*, binding: Any, backend: Any) -> dict[str, Any]:
    """Re-read the full prefix under caller-held locks and recover only durable confirmations.

    The backend's recovery method must not invoke transport. Intent-only or
    dispatch-only prefixes remain unresolved and block this and later slots.
    """
    replay = backend.verify_prefix(binding)
    if (
        type(replay) is not dict
        or replay.get("status") not in {"verified_prefix", "verified_capture_prefix_v3"}
        or type(replay.get("unresolved_count")) is not int
        or replay["unresolved_count"] < 0
    ):
        raise OwnerRuntimeError("owner_runtime_prefix_replay_failed")
    if replay["unresolved_count"]:
        recover = getattr(backend, "recover_confirmations", None)
        if not callable(recover):
            raise OwnerRuntimeError("owner_runtime_prefix_unresolved")
        recovery = recover(binding)
        if type(recovery) is not dict or recovery.get("provider_io") is not False:
            raise OwnerRuntimeError("owner_runtime_recovery_must_be_read_only")
        replay = backend.verify_prefix(binding)
        if (
            type(replay) is not dict
            or replay.get("status") not in {"verified_prefix", "verified_capture_prefix_v3"}
            or type(replay.get("unresolved_count")) is not int
            or replay["unresolved_count"] != 0
        ):
            raise OwnerRuntimeError("owner_runtime_prefix_unresolved")
    if replay.get("dispatch_blocked") is True or replay.get("expired_unaccounted_slots"):
        raise OwnerRuntimeError("owner_runtime_prefix_dispatch_blocked")
    return replay


def owner_preflight_only(
    *,
    source_bytes: Mapping[str, bytes],
    expected_sources: Mapping[str, str],
    launch_snapshot: Mapping[str, Any],
    expected_label: str,
    expected_arguments: Sequence[str],
) -> dict[str, Any]:
    """Verify inert host identity and source bytes; no writes or dispatch."""
    if set(source_bytes) != set(expected_sources):
        raise OwnerRuntimeError("owner_runtime_source_set_changed")
    observed = {
        path: hashlib.sha256(source_bytes[path]).hexdigest() for path in sorted(source_bytes)
    }
    if observed != dict(expected_sources):
        raise OwnerRuntimeError("owner_runtime_source_hash_changed")
    loaded = launch_snapshot.get("loaded")
    if (
        type(expected_label) is not str
        or not expected_label
        or type(expected_arguments) not in {list, tuple}
        or not expected_arguments
        or launch_snapshot.get("active_gecko_labels") != []
        or loaded not in {True, False}
        or (
            loaded is True
            and (
                launch_snapshot.get("label") != expected_label
                or list(launch_snapshot.get("program_arguments", [])) != list(expected_arguments)
            )
        )
        or (loaded is False and any(key in launch_snapshot for key in ("pid", "program_arguments")))
    ):
        raise OwnerRuntimeError("owner_runtime_launch_preflight_invalid")
    return {
        "schema": SCHEMA,
        "status": "verified_inert_preflight",
        "source_bindings": observed,
        "label": expected_label,
        "program_arguments": list(expected_arguments),
        "launch_loaded": loaded,
        "provider_io": False,
        "activation_authorized": False,
        "dispatch_authorized": False,
    }


__all__ = [
    "MAX_CHILD_SECONDS",
    "OwnerBinding",
    "OwnerRuntimeError",
    "execute_one_shot",
    "load_and_verify_release",
    "owner_preflight_only",
    "reconcile_prefix_before_dispatch",
    "run_bounded_transport",
    "verify_release_material",
    "verify_root_descriptor",
]
