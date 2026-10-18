"""Owner-side descriptor writer and one-scan runner for discovery runtime v2.

The module can be exercised against a disposable DescriptorWriter in tests.
The scheduled entrypoint remains fail-closed until a separately reviewed,
source-bound release record exists. It never accepts a storage path, endpoint,
credential, or expected hash from argv.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import plistlib
import re
import ssl
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from tools import brain_senses_capacity_coordinator_v2 as v2
from tools import brain_senses_capacity_coordinator_v3 as v3
from tools import brain_senses_capacity_coordinator_v3_host as v3_host
from tools import brain_senses_gecko_new_pool_discovery_runtime_v2 as runtime
from tools import brain_senses_gecko_new_pool_discovery_v1 as discovery
from tools.brain_senses_external_writer_v1 import DescriptorWriter

SERVICE_ROOT = v3.DISCOVERY_ROOT
RUNTIME_ROOT = f"{SERVICE_ROOT}/runtime-v3"
ACTIVATION_PATH = "control/gecko-new-pool-discovery-v3/activation.json"
ACTIVE_EPOCH_PATH = f"{RUNTIME_ROOT}/active-epoch.json"
EPOCHS_ROOT = f"{RUNTIME_ROOT}/epochs"
STOP_PATH = f"{RUNTIME_ROOT}/stop.json"
RATE_LOCK_PATH = f"{RUNTIME_ROOT}/.provider-rate.lock"
RATE_DIR = f"{RUNTIME_ROOT}/provider-rate"
LANE_LOCK_PATH = f"{RUNTIME_ROOT}/.lane.lock"
MAX_PAGE_BODY = discovery.MAX_RAW_BYTES_PER_PAGE
HTTP_TIMEOUT_SECONDS = 20
NS = 1_000_000_000
USER_AGENT = "memecoin-research-gecko-discovery/2"
REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (REPO_ROOT, REPO_ROOT / "src", REPO_ROOT / "tools"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from memecoin_research_storage import _open_relative_directory, writer_guard  # noqa: E402
from memecoin_research_storage.live_writer import revalidate_descriptor_write  # noqa: E402

CONFIG_PATH = Path.home() / ".config/brain-research/storage-v1.json"
PLIST_RELATIVE_PATH = (
    "ops/launchd/ai.memecoin.brain.senses.gecko-new-pool-discovery-v3.plist.template"
)
LABEL = "ai.memecoin.brain.senses.gecko-new-pool-discovery-v3"
INSTALLED_PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
SOURCE_PATHS = (
    "tools/brain_senses_gecko_new_pool_discovery_runtime_v2.py",
    "tools/brain_senses_gecko_new_pool_discovery_host_v2.py",
    "tools/brain_senses_gecko_new_pool_discovery_v1.py",
    "tools/brain_senses_gecko_discovery_umbrella_v1.py",
    "tools/brain_senses_capacity_coordinator_v2.py",
    "tools/brain_senses_capacity_coordinator_v3.py",
    "tools/brain_senses_capacity_coordinator_v3_host.py",
    "tools/brain_senses_external_writer_v1.py",
    "tools/brain_senses_external_schema_v1.py",
    "src/memecoin_research_storage/__init__.py",
    "src/memecoin_research_storage/live_writer.py",
    PLIST_RELATIVE_PATH,
)
SOURCE_MANIFEST_SCHEMA = f"{runtime.SCHEMA}-source-manifest-v3"
STANDBY_SCHEMA = f"{runtime.SCHEMA}-epoch-standby-v3"
ACTIVE_EPOCH_SCHEMA = f"{runtime.SCHEMA}-active-epoch"
EPOCH_TRANSITION_SCHEMA = f"{runtime.SCHEMA}-epoch-transition"
PAGE_RESERVE_BYTES = discovery.MAX_RAW_BYTES_PER_PAGE * discovery.PHYSICAL_RESERVE_MULTIPLIER
EPOCH_PAGE_COUNT = 864 * 3


class DiscoveryHostError(RuntimeError):
    """Fail-closed owner, storage, provider, or release condition."""


def _raise(reason: str) -> None:
    raise DiscoveryHostError(reason)


def _json(raw: bytes) -> Any:
    try:
        value = json.loads(
            raw.decode("utf-8"), parse_constant=lambda _: _raise("invalid_json_constant")
        )
    except DiscoveryHostError:
        raise
    except Exception as exc:
        raise DiscoveryHostError("discovery_json_invalid") from exc
    if runtime.canonical(value) != raw:
        _raise("discovery_json_noncanonical")
    return value


def _read_json(writer: Any, path: str, *, limit: int = 8_000_000) -> Any:
    return _json(writer.read_bytes(path, max_bytes=limit))


def _create_json(writer: Any, path: str, value: Mapping[str, Any]) -> bytes:
    payload = runtime.canonical(dict(value))
    try:
        writer.create(path, payload, mode=0o600)
    except Exception as exc:
        try:
            existing = writer.read_bytes(path, max_bytes=max(8_000_000, len(payload)))
        except Exception:
            raise DiscoveryHostError("discovery_create_only_collision") from exc
        if existing != payload:
            raise DiscoveryHostError("discovery_create_only_conflict") from exc
    if writer.read_bytes(path, max_bytes=max(8_000_000, len(payload))) != payload:
        _raise("discovery_create_readback")
    return payload


def compile_source_manifest(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Hash the complete runtime/policy/storage/coordinator/plist source closure."""
    source_commit = _verify_clean_source_commit(repo_root)
    files = []
    for relative in SOURCE_PATHS:
        raw = _read_source_checkout_file(repo_root / relative)
        files.append({"path": relative, "size": len(raw), "sha256": runtime.sha256(raw)})
    core = {
        "schema": SOURCE_MANIFEST_SCHEMA,
        "schema_version": 3,
        "source_commit": source_commit,
        "files": files,
        "source_order": list(SOURCE_PATHS),
    }
    return {**core, "manifest_sha256": runtime.sha256(runtime.canonical(core))}


def _read_source_checkout_file(path: Path) -> bytes:
    """Read a pinned checkout file through a no-follow regular-file descriptor."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except Exception as exc:
        raise DiscoveryHostError("discovery_source_file_open_failed") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 8_000_000:
            _raise("discovery_source_file_invalid")
        chunks = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(fd, min(1_000_000, remaining))
            if not chunk:
                _raise("discovery_source_changed_during_read")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            _raise("discovery_source_changed_during_read")
        return b"".join(chunks)
    finally:
        os.close(fd)


def verify_source_manifest(repo_root: Path, manifest: Mapping[str, Any]) -> None:
    """Verify immutable release bytes against both Git trees and the no-follow checkout.

    The release commit may be an ancestor of HEAD so unrelated descendant work can
    continue while the service is loaded. The pinned closure itself remains clean
    and byte-identical at the release commit, current HEAD, and checkout.
    """
    if type(manifest) is not dict or set(manifest) != {
        "schema",
        "schema_version",
        "source_commit",
        "files",
        "source_order",
        "manifest_sha256",
    }:
        _raise("discovery_source_manifest_shape")
    commit = manifest.get("source_commit")
    if (
        manifest.get("schema") != SOURCE_MANIFEST_SCHEMA
        or manifest.get("schema_version") != 3
        or not isinstance(commit, str)
        or not re.fullmatch(r"[0-9a-f]{40}", commit)
        or manifest.get("source_order") != list(SOURCE_PATHS)
    ):
        _raise("discovery_source_manifest_schema")
    files = manifest.get("files")
    if type(files) is not list or len(files) != len(SOURCE_PATHS):
        _raise("discovery_source_manifest_files")
    core = {
        key: manifest[key]
        for key in ("schema", "schema_version", "source_commit", "files", "source_order")
    }
    if manifest.get("manifest_sha256") != runtime.sha256(runtime.canonical(core)):
        _raise("discovery_source_manifest_hash")

    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, head],
            cwd=repo_root,
            check=True,
            capture_output=True,
            timeout=10,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", *SOURCE_PATHS],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise DiscoveryHostError("discovery_source_commit_not_ancestor") from exc
    except Exception as exc:
        raise DiscoveryHostError("discovery_source_git_unavailable") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", head) or status:
        _raise("discovery_source_not_clean_committed")

    for relative, entry in zip(SOURCE_PATHS, files, strict=True):
        if type(entry) is not dict or set(entry) != {"path", "size", "sha256"}:
            _raise("discovery_source_manifest_file_shape")
        if entry["path"] != relative or type(entry["size"]) is not int or entry["size"] < 0:
            _raise("discovery_source_manifest_file_order")
        if not isinstance(entry["sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", entry["sha256"]
        ):
            _raise("discovery_source_manifest_file_hash")
        try:
            checkout = _read_source_checkout_file(repo_root / relative)
            release_blob = subprocess.run(
                ["git", "show", f"{commit}:{relative}"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                timeout=10,
            ).stdout
            head_blob = subprocess.run(
                ["git", "show", f"{head}:{relative}"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                timeout=10,
            ).stdout
        except DiscoveryHostError:
            raise
        except Exception as exc:
            raise DiscoveryHostError("discovery_source_blob_unavailable") from exc
        expected = (entry["size"], entry["sha256"])
        if any(
            (len(raw), runtime.sha256(raw)) != expected
            for raw in (release_blob, head_blob, checkout)
        ):
            _raise("discovery_source_manifest_drift")
    try:
        final_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        final_status = subprocess.run(
            ["git", "status", "--porcelain", "--", *SOURCE_PATHS],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except Exception as exc:
        raise DiscoveryHostError("discovery_source_git_unavailable") from exc
    if final_head != head or final_status:
        _raise("discovery_source_changed_during_verification")


def _verify_clean_source_commit(repo_root: Path) -> str:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", *SOURCE_PATHS],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except Exception as exc:
        raise DiscoveryHostError("discovery_source_git_unavailable") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", head) or status:
        _raise("discovery_source_not_clean_committed")
    return head


def verify_release_bundle(
    *,
    writer: Any,
    guard: Any,
    binding: Mapping[str, Any],
    repo_root: Path = REPO_ROOT,
    installed_plist_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Verify fixed activation/source/policy/epoch/plist bytes before any write."""
    activation_raw = writer.read_bytes(ACTIVATION_PATH, max_bytes=128_000)
    activation = _json(activation_raw)
    keys = {
        "schema",
        "schema_version",
        "release_id",
        "release_sha256",
        "source_manifest_sha256",
        "source_commit",
        "source_sha256",
        "epoch_index",
        "policy_sha256",
        "epoch_sha256",
        "plist_sha256",
        "root_binding",
        "armed",
    }
    if type(activation) is not dict or set(activation) != keys:
        _raise("discovery_activation_shape")
    if activation["schema"] != f"{runtime.SCHEMA}-activation" or activation["schema_version"] != 3:
        _raise("discovery_activation_schema")
    if activation["root_binding"] != dict(binding) or activation["armed"] is not True:
        _raise("discovery_activation_binding")
    manifest_raw = writer.read_bytes(f"{RUNTIME_ROOT}/source-manifest.json", max_bytes=512_000)
    manifest = _json(manifest_raw)
    if runtime.sha256(manifest_raw) != activation["source_manifest_sha256"]:
        _raise("discovery_activation_manifest_hash")
    verify_source_manifest(repo_root, manifest)
    if activation["source_commit"] != manifest.get("source_commit") or activation[
        "source_sha256"
    ] != manifest.get("manifest_sha256"):
        _raise("discovery_activation_source_identity")
    genesis_index = activation["epoch_index"]
    if type(genesis_index) is not int or genesis_index != 0:
        _raise("discovery_activation_epoch_index")
    policy_raw = writer.read_bytes(_epoch_policy_path(0), max_bytes=512_000)
    epoch_raw = writer.read_bytes(_epoch_document_path(0), max_bytes=512_000)
    policy = _json(policy_raw)
    epoch = _json(epoch_raw)
    plist_raw = (repo_root / PLIST_RELATIVE_PATH).read_bytes()
    if installed_plist_bytes is None:
        installed_plist_bytes = INSTALLED_PLIST.read_bytes()
    if (
        policy.get("policy_sha256") != activation["policy_sha256"]
        or epoch.get("epoch_sha256") != activation["epoch_sha256"]
        or runtime.sha256(plist_raw) != activation["plist_sha256"]
        or installed_plist_bytes != plist_raw
    ):
        _raise("discovery_release_material_drift")
    discovery.verify_policy(policy)
    runtime.verify_epoch_envelope(epoch, policy=policy)
    epoch_names = writer.list_names(EPOCHS_ROOT)
    if not epoch_names or any(not re.fullmatch(r"[0-9]{6}", name) for name in epoch_names):
        _raise("discovery_epoch_directory_set")
    active_pointer = _read_active_epoch_pointer(writer, release_id=activation["release_id"])
    current_epoch_index = active_pointer["epoch_index"]
    epoch_history = _load_epoch_history(writer, current_epoch_index=current_epoch_index)
    policy = _read_json(writer, _epoch_policy_path(current_epoch_index), limit=512_000)
    epoch = _read_json(writer, _epoch_document_path(current_epoch_index), limit=512_000)
    active_standby = _read_epoch_standby(writer, epoch)
    if active_standby["root_binding"] != dict(binding):
        _raise("discovery_epoch_standby_storage_binding")
    if (
        epoch_history["current_policy_sha256"] != policy["policy_sha256"]
        or epoch["epoch_index"] != current_epoch_index
        or epoch["release_id"] != activation["release_id"]
    ):
        _raise("discovery_epoch_history_current_policy")
    _check_guard(guard, writer, binding)
    return {
        "activation": activation,
        "policy": policy,
        "epoch": epoch,
        "epoch_history": epoch_history,
        "active_epoch_pointer": active_pointer,
        "genesis_policy": _json(policy_raw),
        "genesis_epoch": _json(epoch_raw),
        "source_manifest": manifest,
        "provider_io": False,
    }


def _load_epoch_history(writer: Any, *, current_epoch_index: int) -> dict[str, Any]:
    """Replay every epoch using its own immutable policy bytes and predecessor seal."""
    if type(current_epoch_index) is not int or current_epoch_index < 0:
        _raise("discovery_epoch_history_index")
    entries = []
    for index in range(current_epoch_index + 1):
        policy = _read_json(writer, _epoch_policy_path(index), limit=512_000)
        epoch = _read_json(writer, _epoch_document_path(index), limit=512_000)
        seal_path = f"{_epoch_root(index)}/seal.json"
        seal = _read_json(writer, seal_path, limit=64_000) if writer.exists(seal_path) else None
        entries.append({"policy": policy, "epoch": epoch, "seal": seal})
    return runtime.verify_epoch_history(entries)


def _check_guard(guard: Any, writer: Any, binding: Mapping[str, Any]) -> None:
    try:
        guard.revalidate()
        root_stat = os.fstat(writer.root_fd)
        if (
            guard.root_id != binding["root_id"]
            or guard.volume_uuid.lower() != binding["volume_uuid"].lower()
            or root_stat.st_dev != binding["device"]
            or root_stat.st_ino != binding["inode"]
        ):
            _raise("discovery_storage_binding_changed")
        expected = guard.config.mount_path / guard.config.data_root
        if expected.is_symlink() or not expected.is_dir():
            _raise("discovery_data_root_invalid")
        expected_stat = expected.stat()
        if (expected_stat.st_dev, expected_stat.st_ino) != (root_stat.st_dev, root_stat.st_ino):
            _raise("discovery_writer_descriptor_mismatch")
    except DiscoveryHostError:
        raise
    except Exception as exc:
        raise DiscoveryHostError("discovery_storage_revalidation_failed") from exc


def _current_capacity_context(writer: Any, guard: Any) -> dict[str, Any]:
    """Rebuild the exact frozen v2 bridge and current v3 replay from descriptors."""
    try:
        v2_policy_bytes, v2_ledger_bytes, v2_inventory, v2_artifacts = v3_host._read_v2_snapshot(
            writer, guard
        )
        bridge = v3.build_v2_bridge(
            v2_policy_bytes=v2_policy_bytes,
            v2_ledger_bytes=v2_ledger_bytes,
            v2_root_inventory=v2_inventory,
            artifact_sha256_by_path=v2_artifacts,
        )
        policy_bytes = writer.read_bytes(v3.POLICY_PATH, max_bytes=4_000_000)
        pointer_bytes = writer.read_bytes(v3.POINTER_PATH, max_bytes=4_000_000)
        ledger_bytes = writer.read_bytes(v3.LEDGER_PATH, max_bytes=v3.MAX_LEDGER_BYTES)
        v3.verify_pointer_and_ledger(
            pointer_bytes=pointer_bytes,
            ledger_bytes=ledger_bytes,
            policy_bytes=policy_bytes,
            bridge=bridge,
        )
        roots = v3_host._all_v3_inventory(writer, policy_bytes)
        statvfs = os.fstatvfs(writer.root_fd)
        free_bytes = statvfs.f_bavail * statvfs.f_frsize
        return {
            "v2_policy_bytes": v2_policy_bytes,
            "v2_ledger_bytes": v2_ledger_bytes,
            "v2_inventory": v2_inventory,
            "v2_artifacts": v2_artifacts,
            "bridge": bridge,
            "policy_bytes": policy_bytes,
            "pointer_bytes": pointer_bytes,
            "ledger_bytes": ledger_bytes,
            "roots": roots,
            "free_bytes": free_bytes,
        }
    except Exception as exc:
        raise DiscoveryHostError("discovery_v3_context_unverified") from exc


class V3CapacityPort:
    """V3 coordinator adapter; caller holds Gecko lane then v3 shared lock."""

    def __init__(
        self,
        writer: Any,
        guard: Any,
        binding: Mapping[str, Any],
        *,
        epoch: Mapping[str, Any] | None = None,
        standby: Mapping[str, Any] | None = None,
    ) -> None:
        self.writer = writer
        self.guard = guard
        self.binding = dict(binding)
        self.epoch = dict(epoch) if epoch is not None else None
        self.standby = dict(standby) if standby is not None else None

    def _append(self, callback: Callable[..., bytes], **kwargs: Any) -> None:
        with self.writer.lock(v3.SHARED_LOCK_PATH, blocking=False):
            _check_guard(self.guard, self.writer, self.binding)
            context = _current_capacity_context(self.writer, self.guard)
            if callback is v3.append_reservation:
                next_ledger = callback(
                    ledger_bytes=context["ledger_bytes"],
                    policy_bytes=context["policy_bytes"],
                    pointer_bytes=context["pointer_bytes"],
                    bridge=context["bridge"],
                    v2_policy_bytes=context["v2_policy_bytes"],
                    current_v2_ledger_bytes=context["v2_ledger_bytes"],
                    current_v2_root_inventory=context["v2_inventory"],
                    v2_artifact_sha256_by_path=context["v2_artifacts"],
                    current_root_inventory=context["roots"],
                    free_bytes=context["free_bytes"],
                    **kwargs,
                )
            elif callback is v3.append_epoch_hold_open:
                next_ledger = callback(
                    ledger_bytes=context["ledger_bytes"],
                    policy_bytes=context["policy_bytes"],
                    pointer_bytes=context["pointer_bytes"],
                    bridge=context["bridge"],
                    v2_policy_bytes=context["v2_policy_bytes"],
                    current_v2_ledger_bytes=context["v2_ledger_bytes"],
                    current_v2_root_inventory=context["v2_inventory"],
                    v2_artifact_sha256_by_path=context["v2_artifacts"],
                    current_root_inventory=context["roots"],
                    free_bytes=context["free_bytes"],
                    **kwargs,
                )
            elif callback is v3.append_page_reserved_from_hold:
                kwargs.setdefault("allocated_before_bytes", context["roots"].get(SERVICE_ROOT, 0))
                next_ledger = callback(
                    ledger_bytes=context["ledger_bytes"],
                    policy_bytes=context["policy_bytes"],
                    pointer_bytes=context["pointer_bytes"],
                    bridge=context["bridge"],
                    **kwargs,
                )
            elif callback is v3.append_epoch_hold_close:
                next_ledger = callback(
                    ledger_bytes=context["ledger_bytes"],
                    policy_bytes=context["policy_bytes"],
                    pointer_bytes=context["pointer_bytes"],
                    bridge=context["bridge"],
                    **kwargs,
                )
            else:
                next_ledger = callback(
                    ledger_bytes=context["ledger_bytes"],
                    policy_bytes=context["policy_bytes"],
                    bridge=context["bridge"],
                    **kwargs,
                )
            _check_guard(self.guard, self.writer, self.binding)
            self.writer.atomic_write(v3.LEDGER_PATH, next_ledger)
            _check_guard(self.guard, self.writer, self.binding)
            if self.writer.read_bytes(v3.LEDGER_PATH, max_bytes=v3.MAX_LEDGER_BYTES) != next_ledger:
                _raise("discovery_v3_ledger_readback")

    def reserve(
        self,
        *,
        request_id: str,
        page_ordinal: int,
        reservation_id: str,
        intent_path: str,
        outcome_path: str,
        intent_bytes: bytes,
        at_ns: int,
    ) -> None:
        if self.epoch is not None and self.standby is not None:
            self._append(
                v3.append_page_reserved_from_hold,
                at_ns=at_ns,
                hold_id=self.standby["hold_id"],
                standby_sha256=self.standby["standby_sha256"],
                reservation_id=reservation_id,
                request_id=request_id,
                page_ordinal=page_ordinal,
                intent_path=intent_path,
                outcome_path=outcome_path,
                intent_sha256=runtime.sha256(intent_bytes),
                reserved_bytes=PAGE_RESERVE_BYTES,
            )
            return
        self._append(
            v3.append_reservation,
            at_ns=at_ns,
            service="gecko_discovery",
            reservation_id=reservation_id,
            request_id=request_id,
            intent_path=intent_path,
            outcome_path=outcome_path,
            intent_sha256=runtime.sha256(intent_bytes),
            reserve_bytes=PAGE_RESERVE_BYTES,
        )

    def open_epoch_hold(self, *, standby: Mapping[str, Any], at_ns: int) -> None:
        standby_bytes = runtime.canonical(dict(standby))
        existing = self._hold_status(standby["hold_id"])
        if existing is not None:
            if existing["standby_sha256"] != standby["standby_sha256"]:
                _raise("discovery_epoch_hold_conflict")
            return
        self._append(
            v3.append_epoch_hold_open,
            at_ns=at_ns,
            hold_id=standby["hold_id"],
            successor_epoch_id=standby["successor_epoch_id"],
            standby_sha256=standby["standby_sha256"],
            intent_path=standby["path"],
            outcome_path=standby["outcome_path"],
            intent_sha256=runtime.sha256(standby_bytes),
            reserved_bytes=standby["max_bytes"],
            page_request_ids=standby["page_request_ids"],
        )

    def close_epoch_hold(
        self, *, standby: Mapping[str, Any], at_ns: int, reason: str
    ) -> dict[str, Any]:
        status = self._hold_status(standby["hold_id"])
        if status is None:
            _raise("discovery_epoch_hold_missing")
        if status["closed"]:
            return status["close_record"]
        record_core = {
            "schema": f"{runtime.SCHEMA}-epoch-hold-close",
            "schema_version": 2,
            "hold_id": standby["hold_id"],
            "standby_sha256": standby["standby_sha256"],
            "remaining_bytes": status["remaining_bytes"],
            "unconsumed_request_ids": status["unconsumed_request_ids"],
            "reason": reason,
            "provider_io": False,
        }
        record = {
            **record_core,
            "record_sha256": runtime.sha256(runtime.canonical(record_core)),
        }
        _create_json(self.writer, standby["outcome_path"], record)
        self._append(
            v3.append_epoch_hold_close,
            at_ns=at_ns,
            hold_id=standby["hold_id"],
            standby_sha256=standby["standby_sha256"],
            remaining_bytes=status["remaining_bytes"],
            outcome_sha256=runtime.sha256(runtime.canonical(record)),
        )
        return record

    def _hold_status(self, hold_id: str) -> dict[str, Any] | None:
        with self.writer.lock(v3.SHARED_LOCK_PATH, blocking=False):
            _check_guard(self.guard, self.writer, self.binding)
            context = _current_capacity_context(self.writer, self.guard)
            events = v3.verify_v3_ledger(
                context["ledger_bytes"],
                expected_policy_bytes=context["policy_bytes"],
                bridge=context["bridge"],
            )
            opened = next(
                (
                    event
                    for event in events
                    if event["kind"] == "epoch_hold_open"
                    and event["fields"].get("hold_id") == hold_id
                ),
                None,
            )
            if opened is None:
                return None
            fields = opened["fields"]
            consumed = [
                event["fields"]["request_id"]
                for event in events
                if event["kind"] == "page_reserved_from_hold"
                and event["fields"].get("hold_id") == hold_id
            ]
            closed = next(
                (
                    event
                    for event in events
                    if event["kind"] == "epoch_hold_close"
                    and event["fields"].get("hold_id") == hold_id
                ),
                None,
            )
            remaining = fields["reserved_bytes"] - sum(
                event["fields"]["reserved_bytes"]
                for event in events
                if event["kind"] == "page_reserved_from_hold"
                and event["fields"].get("hold_id") == hold_id
            )
            close_record = None
            if closed is not None:
                close_record = _read_json(self.writer, fields["outcome_path"], limit=4_000_000)
                raw = self.writer.read_bytes(fields["outcome_path"], max_bytes=4_000_000)
                if runtime.sha256(raw) != closed["fields"]["outcome_sha256"]:
                    _raise("discovery_hold_close_outcome_hash")
            standby_raw = self.writer.read_bytes(fields["intent_path"], max_bytes=4_000_000)
            if runtime.sha256(standby_raw) != fields["intent_sha256"]:
                _raise("discovery_hold_standby_hash")
            return {
                "standby_sha256": fields["standby_sha256"],
                "remaining_bytes": remaining,
                "unconsumed_request_ids": [
                    request_id
                    for request_id in fields["page_request_ids"]
                    if request_id not in consumed
                ],
                "closed": closed is not None,
                "close_record": close_record,
            }

    def reservation_status(
        self,
        *,
        request_id: str,
        reservation_id: str,
        intent_path: str,
        outcome_path: str,
    ) -> dict[str, Any]:
        """Replay one deterministic Gecko reservation while holding v3's lock."""
        with self.writer.lock(v3.SHARED_LOCK_PATH, blocking=False):
            _check_guard(self.guard, self.writer, self.binding)
            context = _current_capacity_context(self.writer, self.guard)
            events = v3.verify_v3_ledger(
                context["ledger_bytes"],
                expected_policy_bytes=context["policy_bytes"],
                bridge=context["bridge"],
            )
            reservations = [
                event
                for event in events
                if event["kind"] in {"reserved", "page_reserved_from_hold"}
                and event["fields"].get("service") == "gecko_discovery"
                and event["fields"].get("request_id") == request_id
            ]
            if not reservations:
                return {"state": "absent", "tail_at_ns": events[-1]["at_ns"]}
            if len(reservations) != 1:
                _raise("discovery_reservation_duplicate")
            reserved = reservations[0]
            fields = reserved["fields"]
            if (
                fields.get("reservation_id") != reservation_id
                or fields.get("intent_path") != intent_path
                or fields.get("outcome_path") != outcome_path
            ):
                _raise("discovery_reservation_binding")
            terminals = [
                event
                for event in events
                if event["kind"] in {"settled", "ambiguous_terminal", "released"}
                and event["fields"].get("reservation_id") == reservation_id
            ]
            if len(terminals) > 1:
                _raise("discovery_reservation_terminal_duplicate")
            return {
                "state": "terminal" if terminals else "reserved",
                "reservation": reserved,
                "terminal": terminals[0] if terminals else None,
                "tail_at_ns": events[-1]["at_ns"],
            }

    def settle(
        self,
        *,
        reservation_id: str,
        outcome: str,
        outcome_bytes: bytes,
        allocated_bytes: int,
        at_ns: int,
        ambiguous: bool = False,
        release: bool = False,
    ) -> None:
        outcome_path = f"{RUNTIME_ROOT}/outcomes/{reservation_id}.json"
        _create_json(self.writer, outcome_path, _json(outcome_bytes))
        context = _current_capacity_context(self.writer, self.guard)
        events = v3.verify_v3_ledger(
            context["ledger_bytes"],
            expected_policy_bytes=context["policy_bytes"],
            bridge=context["bridge"],
        )
        desired_kind = "released" if release else "ambiguous_terminal" if ambiguous else "settled"
        terminals = [
            event
            for event in events
            if event["kind"] in {"settled", "ambiguous_terminal", "released"}
            and event["fields"].get("reservation_id") == reservation_id
        ]
        if terminals:
            terminal = terminals[0]
            if (
                terminal["kind"] != desired_kind
                or terminal["fields"]["outcome_sha256"] != runtime.sha256(outcome_bytes)
                or terminal["fields"]["outcome"] != outcome
            ):
                _raise("discovery_capacity_terminal_conflict")
            return
        self._append(
            v3.append_terminal,
            at_ns=at_ns,
            reservation_id=reservation_id,
            outcome=outcome,
            outcome_sha256=runtime.sha256(outcome_bytes),
            actual_allocated_bytes=allocated_bytes,
            kind=desired_kind,
        )


def initialize_runtime(
    *,
    writer: Any,
    guard: Any,
    binding: Mapping[str, Any],
    release_id: str,
    release_sha256: str,
    source_sha256: str,
    policy: Mapping[str, Any],
    epoch: Mapping[str, Any],
) -> None:
    """Create-only inert runtime prefix; no request or LaunchAgent operation."""
    if not runtime._is_id(release_id) or epoch.get("release_id") != release_id:
        _raise("discovery_init_release_binding")
    checked_policy = discovery.verify_policy(policy)
    runtime.verify_epoch_envelope(epoch, policy=checked_policy)
    if epoch["release_sha256"] != release_sha256 or epoch["source_sha256"] != source_sha256:
        _raise("discovery_init_source_binding")
    if checked_policy["source_spec_sha256"] != source_sha256:
        _raise("discovery_init_policy_source")
    _check_guard(guard, writer, binding)
    epoch_index = epoch["epoch_index"]
    writer.mkdir(_epoch_root(epoch_index))
    expected = {
        _epoch_policy_path(epoch_index): runtime.canonical(dict(policy)),
        _epoch_document_path(epoch_index): runtime.canonical(dict(epoch)),
        _epoch_seen_path(epoch_index): runtime.canonical(
            {
                "schema": f"{runtime.SCHEMA}-seen",
                "schema_version": 2,
                "pool_ids": [],
                "seen_sha256": runtime.sha256(runtime.canonical([])),
            }
        ),
    }
    for path, payload in expected.items():
        _check_guard(guard, writer, binding)
        try:
            writer.create(path, payload, mode=0o600)
        except Exception as exc:
            if writer.read_bytes(path, max_bytes=8_000_000) != payload:
                raise DiscoveryHostError("discovery_init_conflicting_prefix") from exc
        _check_guard(guard, writer, binding)
        if writer.read_bytes(path, max_bytes=8_000_000) != payload:
            _raise("discovery_init_readback")
    # A fixed stop marker is intentionally absent until a terminal STOP.


def _epoch_root(epoch_index: int) -> str:
    return f"{EPOCHS_ROOT}/{epoch_index:06d}"


def _epoch_policy_path(epoch_index: int) -> str:
    return f"{_epoch_root(epoch_index)}/policy.json"


def _epoch_document_path(epoch_index: int) -> str:
    return f"{_epoch_root(epoch_index)}/epoch.json"


def _epoch_seen_path(epoch_index: int) -> str:
    return f"{_epoch_root(epoch_index)}/seen-pools.json"


def _standby_path(epoch_index: int) -> str:
    return f"{_epoch_root(epoch_index)}/standby.json"


def _epoch_transition_path(epoch_index: int) -> str:
    return f"{_epoch_root(epoch_index)}/transition.json"


def _epoch_page_request_ids(*, policy: Mapping[str, Any], epoch: Mapping[str, Any]) -> list[str]:
    request_ids: list[str] = []
    for scan_index in range(policy["max_scan_count"]):
        scheduled_ns = policy["first_scan_ns"] + scan_index * policy["scan_interval_seconds"] * NS
        plan = runtime.compile_epoch_scan_plan(
            policy=policy,
            epoch=epoch,
            scan_index=scan_index,
            scheduled_ns=scheduled_ns,
        )
        request_ids.extend(slot["request_id"] for slot in plan["page_slots"])
    if len(request_ids) != EPOCH_PAGE_COUNT or len(set(request_ids)) != len(request_ids):
        _raise("discovery_epoch_page_set_invalid")
    return request_ids


def _create_epoch_standby(
    *,
    writer: Any,
    guard: Any,
    binding: Mapping[str, Any],
    policy: Mapping[str, Any],
    epoch: Mapping[str, Any],
    prepared_at_ns: int,
    predecessor_frontier: Mapping[str, Any] | None,
    predecessor_epoch: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a deterministic, inactive epoch-runway manifest and v3 hold."""
    checked_policy = discovery.verify_policy(policy)
    runtime.verify_epoch_envelope(epoch, policy=checked_policy)
    index = epoch["epoch_index"]
    request_ids = _epoch_page_request_ids(policy=checked_policy, epoch=epoch)
    hold_id = runtime.sha256(f"{epoch['release_id']}:epoch:{index}:hold".encode())[:32]
    core = {
        "schema": STANDBY_SCHEMA,
        "schema_version": 3,
        "release_id": epoch["release_id"],
        "release_sha256": epoch["release_sha256"],
        "successor_epoch_id": epoch["root_id"],
        "epoch_index": index,
        "epoch_start_ns": epoch["epoch_start_ns"],
        "epoch_end_ns": epoch["epoch_end_ns"],
        "first_scan_ns": checked_policy["first_scan_ns"],
        "scan_interval_seconds": checked_policy["scan_interval_seconds"],
        "scan_count": checked_policy["max_scan_count"],
        "pages": [1, 2, 3],
        "page_spacing_seconds": checked_policy["page_spacing_seconds"],
        "policy_sha256": checked_policy["policy_sha256"],
        "policy_contract_sha256": runtime.policy_contract_sha256(checked_policy),
        "source_sha256": epoch["source_sha256"],
        "api_accept_sha256": runtime.sha256(discovery.API_ACCEPT.encode()),
        "predecessor_epoch_sha256": (
            predecessor_epoch["epoch_sha256"] if predecessor_epoch is not None else None
        ),
        "predecessor_frontier": dict(predecessor_frontier) if predecessor_frontier else None,
        "root_binding": dict(binding),
        "caps": dict(checked_policy["caps"]),
        "hold_id": hold_id,
        "max_bytes": runtime.EPOCH_REQUEST_RUNWAY_BYTES,
        "page_request_ids": request_ids,
        "inactive": True,
        "provider_io_forbidden": True,
        "cursor_reset": False,
        "lifetime_counters_reset": False,
    }
    standby_path = _standby_path(index)
    outcome_path = f"{RUNTIME_ROOT}/outcomes/epoch-hold-{hold_id}.json"
    expected = {**core, "path": standby_path, "outcome_path": outcome_path}
    if writer.exists(_epoch_policy_path(index)):
        existing_policy = _read_json(writer, _epoch_policy_path(index), limit=512_000)
        if existing_policy != checked_policy:
            _raise("discovery_standby_policy_conflict")
    else:
        _create_json(writer, _epoch_policy_path(index), checked_policy)
    if writer.exists(standby_path):
        standby = _read_json(writer, standby_path, limit=4_000_000)
        created = standby.get("prepared_at_ns")
        digest = standby.get("standby_sha256")
        standby_core = dict(standby)
        standby_core.pop("standby_sha256", None)
        if (
            type(created) is not int
            or created <= 0
            or (
                index > 0
                and not epoch["epoch_start_ns"] - 60 * 60 * NS <= created < epoch["epoch_start_ns"]
            )
            or (index == 0 and created >= epoch["epoch_start_ns"])
            or standby_core.pop("prepared_at_ns", None) != created
            or standby_core != expected
            or digest != runtime.sha256(runtime.canonical({**expected, "prepared_at_ns": created}))
        ):
            _raise("discovery_standby_conflict")
    else:
        if index > 0 and not (
            epoch["epoch_start_ns"] - 60 * 60 * NS <= prepared_at_ns < epoch["epoch_start_ns"]
        ):
            _raise("discovery_standby_outside_prepare_window")
        if index == 0 and prepared_at_ns >= epoch["epoch_start_ns"]:
            _raise("discovery_genesis_hold_not_future")
        standby_core = {**expected, "prepared_at_ns": prepared_at_ns}
        standby = {
            **standby_core,
            "standby_sha256": runtime.sha256(runtime.canonical(standby_core)),
        }
        _check_guard(guard, writer, binding)
        _create_json(writer, standby_path, standby)
        _check_guard(guard, writer, binding)
    return standby


def _read_epoch_standby(writer: Any, epoch: Mapping[str, Any]) -> dict[str, Any]:
    path = _standby_path(epoch["epoch_index"])
    standby = _read_json(writer, path, limit=4_000_000)
    core = dict(standby)
    digest = core.pop("standby_sha256", None)
    required = {
        "schema",
        "schema_version",
        "release_id",
        "release_sha256",
        "successor_epoch_id",
        "epoch_index",
        "epoch_start_ns",
        "epoch_end_ns",
        "first_scan_ns",
        "scan_interval_seconds",
        "scan_count",
        "pages",
        "page_spacing_seconds",
        "policy_sha256",
        "policy_contract_sha256",
        "source_sha256",
        "api_accept_sha256",
        "predecessor_epoch_sha256",
        "predecessor_frontier",
        "root_binding",
        "caps",
        "hold_id",
        "max_bytes",
        "page_request_ids",
        "inactive",
        "provider_io_forbidden",
        "cursor_reset",
        "lifetime_counters_reset",
        "path",
        "outcome_path",
        "prepared_at_ns",
        "standby_sha256",
    }
    if (
        type(standby) is not dict
        or set(standby) != required
        or digest != runtime.sha256(runtime.canonical(core))
        or standby.get("schema") != STANDBY_SCHEMA
        or standby.get("schema_version") != 3
        or standby.get("path") != path
        or standby.get("release_id") != epoch.get("release_id")
        or standby.get("successor_epoch_id") != epoch.get("root_id")
        or standby.get("epoch_index") != epoch.get("epoch_index")
        or standby.get("epoch_start_ns") != epoch.get("epoch_start_ns")
        or standby.get("epoch_end_ns") != epoch.get("epoch_end_ns")
        or standby.get("policy_sha256") != epoch.get("policy_sha256")
        or standby.get("hold_id")
        != runtime.sha256(f"{epoch['release_id']}:epoch:{epoch['epoch_index']}:hold".encode())[:32]
        or standby.get("max_bytes") != runtime.EPOCH_REQUEST_RUNWAY_BYTES
        or standby.get("inactive") is not True
        or standby.get("provider_io_forbidden") is not True
        or standby.get("cursor_reset") is not False
        or standby.get("lifetime_counters_reset") is not False
    ):
        _raise("discovery_epoch_standby_binding")
    frontier = standby.get("predecessor_frontier")
    if epoch["epoch_index"] == 0:
        if frontier is not None:
            _raise("discovery_epoch_standby_genesis_frontier")
    elif (
        type(frontier) is not dict
        or set(frontier)
        not in (
            {
                "epoch_index",
                "terminal_scan_index",
                "kind",
                "artifact_sha256",
                "prefix_sha256",
            },
            {
                "epoch_index",
                "terminal_scan_index",
                "kind",
                "artifact_sha256",
                "prefix_sha256",
                "plan_id",
                "plan_sha256",
            },
        )
        or frontier.get("epoch_index") != epoch["epoch_index"] - 1
        or type(frontier.get("terminal_scan_index")) is not int
        or not 0 <= frontier["terminal_scan_index"] < 864
        or frontier.get("kind") not in {"plan_summary", "gap"}
        or not runtime._is_hash(frontier.get("artifact_sha256"))
        or not runtime._is_hash(frontier.get("prefix_sha256"))
        or (frontier.get("kind") == "plan_summary")
        != ("plan_id" in frontier and "plan_sha256" in frontier)
        or ("plan_id" in frontier and not runtime._is_id(frontier["plan_id"]))
        or ("plan_sha256" in frontier and not runtime._is_hash(frontier["plan_sha256"]))
    ):
        _raise("discovery_epoch_standby_frontier_shape")
    if standby.get("policy_contract_sha256") != runtime.policy_contract_sha256(
        discovery.verify_policy(
            _read_json(writer, _epoch_policy_path(epoch["epoch_index"]), limit=512_000)
        )
    ):
        _raise("discovery_epoch_standby_policy_binding")
    policy = discovery.verify_policy(
        _read_json(writer, _epoch_policy_path(epoch["epoch_index"]), limit=512_000)
    )
    if (
        standby.get("release_sha256") != epoch.get("release_sha256")
        or standby.get("source_sha256") != epoch.get("source_sha256")
        or standby.get("first_scan_ns") != policy["first_scan_ns"]
        or standby.get("scan_interval_seconds") != policy["scan_interval_seconds"]
        or standby.get("scan_count") != policy["max_scan_count"]
        or standby.get("pages") != [1, 2, 3]
        or standby.get("page_spacing_seconds") != policy["page_spacing_seconds"]
        or standby.get("api_accept_sha256") != runtime.sha256(discovery.API_ACCEPT.encode())
        or standby.get("caps") != policy["caps"]
        or type(standby.get("root_binding")) is not dict
        or set(standby["root_binding"]) != {"root_id", "volume_uuid", "device", "inode"}
        or not runtime._is_id(standby.get("root_binding", {}).get("root_id"))
        or not re.fullmatch(
            r"[0-9a-f-]{36}", standby.get("root_binding", {}).get("volume_uuid", "")
        )
        or standby.get("page_request_ids") != _epoch_page_request_ids(policy=policy, epoch=epoch)
    ):
        _raise("discovery_epoch_standby_page_set")
    return standby


def _next_coordinator_time(writer: Any, guard: Any, now_ns: int) -> int:
    context = _current_capacity_context(writer, guard)
    events = v3.verify_v3_ledger(
        context["ledger_bytes"],
        expected_policy_bytes=context["policy_bytes"],
        bridge=context["bridge"],
    )
    return max(now_ns, events[-1]["at_ns"] + 1)


def _prepare_next_epoch_standby(
    *,
    writer: Any,
    guard: Any,
    binding: Mapping[str, Any],
    policy: Mapping[str, Any],
    epoch: Mapping[str, Any],
    now_ns: int,
) -> dict[str, Any] | None:
    """Reserve successor runway one hour before the immutable epoch boundary."""
    next_index = epoch["epoch_index"] + 1
    if writer.exists(_standby_path(next_index)):
        prospective_policy = _read_json(writer, _epoch_policy_path(next_index), limit=512_000)
        discovery.verify_policy(prospective_policy)
        seed = f"{epoch['release_id']}:epoch:{next_index}"
        prospective = runtime.epoch_envelope(
            release_id=epoch["release_id"],
            lease_id=runtime.sha256(f"{seed}:lease".encode())[:32],
            root_id=runtime.sha256(f"{seed}:root".encode())[:32],
            release_sha256=epoch["release_sha256"],
            source_sha256=epoch["source_sha256"],
            policy=prospective_policy,
            epoch_index=next_index,
            epoch_start_ns=epoch["epoch_end_ns"],
            previous_epoch_sha256=None,
            lifetime_requests_before=epoch["lifetime_requests_before"],
            lifetime_physical_before=epoch["lifetime_physical_before"],
            prior_allocated_physical=epoch["prior_allocated_physical"],
            prior_pending_physical=epoch["prior_pending_physical"],
        )
        existing = _read_json(writer, _standby_path(next_index), limit=4_000_000)
        standby = _create_epoch_standby(
            writer=writer,
            guard=guard,
            binding=binding,
            policy=prospective_policy,
            epoch=prospective,
            prepared_at_ns=now_ns,
            predecessor_frontier=existing.get("predecessor_frontier"),
            predecessor_epoch=epoch,
        )
        capacity = V3CapacityPort(writer, guard, binding)
        capacity.open_epoch_hold(
            standby=standby, at_ns=_next_coordinator_time(writer, guard, now_ns)
        )
        return standby
    if now_ns < epoch["epoch_end_ns"] - 60 * 60 * NS:
        return None
    if now_ns >= epoch["epoch_end_ns"]:
        _raise("discovery_successor_standby_missed")
    with ExitStack() as stack:
        stack.enter_context(writer.lock(LANE_LOCK_PATH, blocking=False))
        v2_policy = v3._v2_policy(writer.read_bytes(v2.POLICY_PATH, max_bytes=4_000_000))
        stack.enter_context(writer.lock(v2_policy["service_lock_paths"]["gecko"], blocking=False))
        _check_guard(guard, writer, binding)
        next_policy = discovery.compile_policy(
            first_scan_ns=epoch["epoch_end_ns"],
            scan_interval_seconds=300,
            pages=3,
            page_spacing_seconds=12,
            sample_numerator=policy["sample"]["numerator"],
            sample_denominator=policy["sample"]["denominator"],
            source_spec_sha256=policy["source_spec_sha256"],
            max_scan_count=864,
            max_raw_bytes_per_page=policy["caps"]["max_raw_bytes_per_page"],
        )
        seed = f"{epoch['release_id']}:epoch:{next_index}"
        prospective = runtime.epoch_envelope(
            release_id=epoch["release_id"],
            lease_id=runtime.sha256(f"{seed}:lease".encode())[:32],
            root_id=runtime.sha256(f"{seed}:root".encode())[:32],
            release_sha256=epoch["release_sha256"],
            source_sha256=epoch["source_sha256"],
            policy=next_policy,
            epoch_index=next_index,
            epoch_start_ns=epoch["epoch_end_ns"],
            previous_epoch_sha256=None,
            lifetime_requests_before=epoch["lifetime_requests_before"],
            lifetime_physical_before=epoch["lifetime_physical_before"],
            prior_allocated_physical=epoch["prior_allocated_physical"],
            prior_pending_physical=epoch["prior_pending_physical"],
        )
        due_count = max(
            0,
            min(
                864,
                (now_ns - policy["first_scan_ns"] - discovery.SCAN_EXPIRY_NS)
                // (policy["scan_interval_seconds"] * NS)
                + 1,
            ),
        )
        _close_elapsed_gaps(
            writer=writer,
            guard=guard,
            binding=binding,
            policy=policy,
            epoch=epoch,
            through_scan_index=due_count,
            now_ns=now_ns,
        )
        frontier_rows = _terminal_frontier_rows(writer, policy, epoch)
        predecessor_frontier = _frontier_from_rows(frontier_rows)
        if epoch["epoch_index"] > 0 and predecessor_frontier is None:
            _raise("discovery_standby_predecessor_frontier_missing")
        standby = _create_epoch_standby(
            writer=writer,
            guard=guard,
            binding=binding,
            policy=next_policy,
            epoch=prospective,
            prepared_at_ns=now_ns,
            predecessor_frontier=predecessor_frontier,
            predecessor_epoch=epoch,
        )
        capacity = V3CapacityPort(writer, guard, binding)
        capacity.open_epoch_hold(
            standby=standby, at_ns=_next_coordinator_time(writer, guard, now_ns)
        )
        _check_guard(guard, writer, binding)
        return standby


def _terminal_frontier_rows(
    writer: Any,
    policy: Mapping[str, Any],
    epoch: Mapping[str, Any],
    *,
    require_complete: bool = False,
) -> list[dict[str, Any]]:
    """Replay the contiguous terminal plan/gap prefix without inventing exposure."""
    epoch_index = epoch["epoch_index"]
    try:
        scan_names = writer.list_names(_epoch_scans_root(epoch_index))
    except FileNotFoundError:
        scan_names = []
    try:
        gap_names = writer.list_names(f"{_epoch_root(epoch_index)}/gaps")
    except FileNotFoundError:
        gap_names = []
    observed_indices: set[int] = set()
    for name in scan_names:
        if not re.fullmatch(r"[0-9]{4}", name):
            _raise("discovery_scan_directory_name")
        observed_indices.add(int(name))
    for name in gap_names:
        if not re.fullmatch(r"[0-9]{4}\.json", name):
            _raise("discovery_gap_filename")
        observed_indices.add(int(name[:4]))
    if any(index >= 864 for index in observed_indices):
        _raise("discovery_epoch_scan_index_range")
    rows: list[dict[str, Any]] = []
    seen = _load_seen_pool_ids(
        writer, policy, epoch=epoch, epoch_index=epoch_index, before_scan_index=0
    )
    for scan_index in range(864):
        plan_path = _scan_plan_path(epoch_index, scan_index)
        gap_path = f"{_epoch_root(epoch_index)}/gaps/{scan_index:04d}.json"
        has_plan, has_gap = writer.exists(plan_path), writer.exists(gap_path)
        if has_plan == has_gap:
            if has_plan:
                _raise("discovery_epoch_scan_terminal_partition")
            if any(index > scan_index for index in observed_indices):
                _raise("discovery_epoch_frontier_interior_missing")
            if require_complete:
                _raise("discovery_epoch_frontier_incomplete")
            break
        scheduled_ns = policy["first_scan_ns"] + scan_index * policy["scan_interval_seconds"] * NS
        if has_gap:
            gap = _read_json(writer, gap_path, limit=16_384)
            if (
                type(gap) is not dict
                or set(gap)
                != {
                    "schema",
                    "schema_version",
                    "scan_index",
                    "scheduled_ns",
                    "observed_ns",
                    "reason",
                    "exposure",
                    "selected_pool_ids",
                    "provider_io",
                    "cursor_advanced",
                }
                or gap.get("schema") != f"{runtime.SCHEMA}-unplanned-gap"
                or gap.get("schema_version") != 2
                or gap.get("scan_index") != scan_index
                or gap.get("scheduled_ns") != scheduled_ns
                or type(gap.get("observed_ns")) is not int
                or gap["observed_ns"] < scheduled_ns + discovery.SCAN_EXPIRY_NS
                or gap.get("reason") != "expired_before_plan_or_dispatch"
                or gap.get("exposure") != "unknown"
                or gap.get("selected_pool_ids") is not None
                or gap.get("provider_io") is not False
                or gap.get("cursor_advanced") is not False
            ):
                _raise("discovery_epoch_gap_invalid")
            rows.append(
                {
                    "epoch_index": epoch_index,
                    "terminal_scan_index": scan_index,
                    "kind": "gap",
                    "artifact_sha256": runtime.sha256(runtime.canonical(gap)),
                }
            )
            continue
        plan = _read_json(writer, plan_path)
        runtime.verify_epoch_scan_plan(plan, policy=policy, epoch=epoch)
        if plan.get("scan_index") != scan_index:
            _raise("discovery_epoch_frontier_plan_index")
        records, raws = _load_scan_records(writer, plan)
        if len(records) != len(plan["page_slots"]):
            if any(index > scan_index for index in observed_indices):
                _raise("discovery_epoch_frontier_interior_incomplete")
            if require_complete:
                _raise("discovery_epoch_frontier_incomplete")
            break
        replayed = runtime.verify_persisted_scan(
            policy=policy,
            plan=plan,
            page_records=records,
            raw_by_request_id=raws,
            previously_seen_pool_ids=seen,
        )
        summary_path = f"{_page_directory(epoch_index, scan_index)}/summary.json"
        if not writer.exists(summary_path):
            break
        summary = _read_json(writer, summary_path, limit=16_000_000)
        if summary != replayed:
            _raise("discovery_scan_summary_replay_mismatch")
        summary_sha = runtime.sha256(runtime.canonical(summary))
        rows.append(
            {
                "epoch_index": epoch_index,
                "terminal_scan_index": scan_index,
                "kind": "plan_summary",
                "artifact_sha256": summary_sha,
                "plan_id": plan["scan_id"],
                "plan_sha256": plan["plan_sha256"],
            }
        )
        seen.extend(replayed["first_seen_pool_ids"])
    return rows


def _frontier_from_rows(
    rows: list[dict[str, Any]], terminal_scan_index: int | None = None
) -> dict[str, Any] | None:
    if not rows:
        return None
    index = len(rows) - 1 if terminal_scan_index is None else terminal_scan_index
    if type(index) is not int or not 0 <= index < len(rows):
        _raise("discovery_epoch_frontier_index")
    row = rows[index]
    return {
        **row,
        "prefix_sha256": runtime.sha256(runtime.canonical(rows[: index + 1])),
    }


def _verify_successor_frontier(
    standby: Mapping[str, Any], *, predecessor_epoch: Mapping[str, Any], rows: list[dict[str, Any]]
) -> None:
    core = dict(standby)
    digest = core.pop("standby_sha256", None)
    frontier = standby.get("predecessor_frontier")
    if (
        digest != runtime.sha256(runtime.canonical(core))
        or standby.get("schema") != STANDBY_SCHEMA
        or standby.get("schema_version") != 3
        or standby.get("epoch_index") != predecessor_epoch["epoch_index"] + 1
        or standby.get("predecessor_epoch_sha256") != predecessor_epoch["epoch_sha256"]
        or standby.get("inactive") is not True
        or type(frontier) is not dict
        or type(frontier.get("terminal_scan_index")) is not int
        or _frontier_from_rows(rows, frontier["terminal_scan_index"]) != frontier
    ):
        _raise("discovery_epoch_standby_frontier_changed")


def _active_epoch_core(
    *,
    release_id: str,
    epoch: Mapping[str, Any],
    generation: int,
    previous_pointer_sha256: str | None,
    transition_sha256: str | None,
) -> dict[str, Any]:
    if (
        type(generation) is not int
        or generation != epoch["epoch_index"]
        or (previous_pointer_sha256 is not None and not runtime._is_hash(previous_pointer_sha256))
        or (transition_sha256 is not None and not runtime._is_hash(transition_sha256))
    ):
        _raise("discovery_active_epoch_pointer_inputs")
    core = {
        "schema": ACTIVE_EPOCH_SCHEMA,
        "schema_version": 2,
        "release_id": release_id,
        "generation": generation,
        "epoch_index": epoch["epoch_index"],
        "epoch_sha256": epoch["epoch_sha256"],
        "previous_pointer_sha256": previous_pointer_sha256,
        "transition_sha256": transition_sha256,
    }
    return {**core, "pointer_sha256": runtime.sha256(runtime.canonical(core))}


def _publish_epoch_transition(
    *,
    writer: Any,
    guard: Any,
    binding: Mapping[str, Any],
    previous_epoch: Mapping[str, Any],
    previous_seal: Mapping[str, Any],
    successor_epoch: Mapping[str, Any],
    standby: Mapping[str, Any],
) -> None:
    """Publish an immutable transition, then CAS the active pointer last."""
    old_pointer = _read_active_epoch_pointer(writer, release_id=previous_epoch["release_id"])
    if old_pointer["epoch_index"] != previous_epoch["epoch_index"]:
        if (
            old_pointer["epoch_index"] == successor_epoch["epoch_index"]
            and old_pointer["epoch_sha256"] == successor_epoch["epoch_sha256"]
        ):
            return
        _raise("discovery_epoch_pointer_compare_failed")
    transition_core = {
        "schema": EPOCH_TRANSITION_SCHEMA,
        "schema_version": 2,
        "release_id": successor_epoch["release_id"],
        "from_epoch_sha256": previous_epoch["epoch_sha256"],
        "to_epoch_sha256": successor_epoch["epoch_sha256"],
        "seal_sha256": previous_seal["seal_sha256"],
        "standby_sha256": standby["standby_sha256"],
        "from_pointer_sha256": old_pointer["pointer_sha256"],
        "generation": successor_epoch["epoch_index"],
    }
    transition = {
        **transition_core,
        "transition_sha256": runtime.sha256(runtime.canonical(transition_core)),
    }
    transition_path = _epoch_transition_path(successor_epoch["epoch_index"])
    _check_guard(guard, writer, binding)
    _create_json(writer, transition_path, transition)
    _check_guard(guard, writer, binding)
    pointer = _active_epoch_core(
        release_id=successor_epoch["release_id"],
        epoch=successor_epoch,
        generation=successor_epoch["epoch_index"],
        previous_pointer_sha256=old_pointer["pointer_sha256"],
        transition_sha256=transition["transition_sha256"],
    )
    current = _read_active_epoch_pointer(writer, release_id=successor_epoch["release_id"])
    if current != old_pointer:
        if current == pointer:
            return
        _raise("discovery_epoch_pointer_compare_failed")
    _check_guard(guard, writer, binding)
    writer.atomic_write(ACTIVE_EPOCH_PATH, runtime.canonical(pointer))
    _check_guard(guard, writer, binding)
    if _read_active_epoch_pointer(writer, release_id=successor_epoch["release_id"]) != pointer:
        _raise("discovery_epoch_pointer_readback")


def _read_active_epoch_pointer(writer: Any, *, release_id: str) -> dict[str, Any]:
    pointer = _read_json(writer, ACTIVE_EPOCH_PATH, limit=32_000)
    if (
        type(pointer) is not dict
        or set(pointer)
        != {
            "schema",
            "schema_version",
            "release_id",
            "generation",
            "epoch_index",
            "epoch_sha256",
            "previous_pointer_sha256",
            "transition_sha256",
            "pointer_sha256",
        }
        or pointer.get("schema") != ACTIVE_EPOCH_SCHEMA
        or pointer.get("schema_version") != 2
        or pointer.get("release_id") != release_id
        or type(pointer.get("epoch_index")) is not int
        or pointer.get("generation") != pointer.get("epoch_index")
    ):
        _raise("discovery_active_epoch_pointer_shape")
    core = dict(pointer)
    digest = core.pop("pointer_sha256")
    if digest != runtime.sha256(runtime.canonical(core)):
        _raise("discovery_active_epoch_pointer_hash")
    epoch_index = pointer["epoch_index"]
    previous_pointer_sha: str | None = None
    for index in range(epoch_index + 1):
        policy = _read_json(writer, _epoch_policy_path(index), limit=512_000)
        epoch = _read_json(writer, _epoch_document_path(index), limit=512_000)
        runtime.verify_epoch_envelope(epoch, policy=policy)
        if epoch["epoch_index"] != index or epoch["release_id"] != release_id:
            _raise("discovery_active_epoch_history_identity")
        if index == 0:
            if (
                epoch.get("previous_epoch_sha256") is not None
                or epoch.get("predecessor_standby_sha256") is not None
            ):
                _raise("discovery_active_epoch_genesis_link")
            expected = _active_epoch_core(
                release_id=release_id,
                epoch=epoch,
                generation=0,
                previous_pointer_sha256=None,
                transition_sha256=None,
            )
        else:
            prior_policy = _read_json(writer, _epoch_policy_path(index - 1), limit=512_000)
            prior_epoch = _read_json(writer, _epoch_document_path(index - 1), limit=512_000)
            runtime.verify_epoch_envelope(prior_epoch, policy=prior_policy)
            prior_seal = _read_json(writer, f"{_epoch_root(index - 1)}/seal.json", limit=64_000)
            runtime._verify_epoch_seal(prior_seal, epoch=prior_epoch)
            standby = _read_epoch_standby(writer, epoch)
            standby_sha = standby["standby_sha256"]
            transition = _read_json(writer, _epoch_transition_path(index), limit=32_000)
            transition_core = dict(transition)
            transition_sha = transition_core.pop("transition_sha256", None)
            expected_transition_keys = {
                "schema",
                "schema_version",
                "release_id",
                "from_epoch_sha256",
                "to_epoch_sha256",
                "seal_sha256",
                "standby_sha256",
                "from_pointer_sha256",
                "generation",
            }
            if (
                epoch.get("predecessor_standby_sha256") != standby_sha
                or standby.get("predecessor_epoch_sha256") != prior_epoch["epoch_sha256"]
                or prior_seal.get("seal_sha256") != transition_core.get("seal_sha256")
                or set(transition_core) != expected_transition_keys
                or transition_core.get("schema") != EPOCH_TRANSITION_SCHEMA
                or transition_core.get("schema_version") != 2
                or transition_core.get("release_id") != release_id
                or transition_core.get("from_epoch_sha256") != prior_epoch["epoch_sha256"]
                or transition_core.get("to_epoch_sha256") != epoch["epoch_sha256"]
                or transition_core.get("standby_sha256") != standby_sha
                or transition_core.get("generation") != index
                or transition_core.get("from_pointer_sha256") != previous_pointer_sha
                or transition_sha != runtime.sha256(runtime.canonical(transition_core))
            ):
                _raise("discovery_active_epoch_transition_mismatch")
            expected = _active_epoch_core(
                release_id=release_id,
                epoch=epoch,
                generation=index,
                previous_pointer_sha256=previous_pointer_sha,
                transition_sha256=transition_sha,
            )
        previous_pointer_sha = expected["pointer_sha256"]
    if expected != pointer:
        _raise("discovery_active_epoch_pointer_chain_mismatch")
    return pointer


def _epoch_scans_root(epoch_index: int) -> str:
    return f"{_epoch_root(epoch_index)}/scans"


def _page_directory(epoch_index: int, scan_index: int) -> str:
    return f"{_epoch_scans_root(epoch_index)}/{scan_index:04d}"


def _page_path(epoch_index: int, scan_index: int, page: int, suffix: str) -> str:
    return f"{_page_directory(epoch_index, scan_index)}/page-{page:02d}.{suffix}"


def _scan_plan_path(epoch_index: int, scan_index: int) -> str:
    return f"{_page_directory(epoch_index, scan_index)}/plan.json"


def _record_unplanned_gap(
    writer: Any,
    guard: Any,
    binding: Mapping[str, Any],
    *,
    epoch_index: int,
    scan_index: int,
    scheduled_ns: int,
    observed_ns: int,
) -> dict[str, Any]:
    gap_path = f"{_epoch_root(epoch_index)}/gaps/{scan_index:04d}.json"
    if writer.exists(gap_path):
        existing = _read_json(writer, gap_path, limit=16_384)
        if (
            type(existing) is not dict
            or set(existing)
            != {
                "schema",
                "schema_version",
                "scan_index",
                "scheduled_ns",
                "observed_ns",
                "reason",
                "exposure",
                "selected_pool_ids",
                "provider_io",
                "cursor_advanced",
            }
            or existing.get("schema") != f"{runtime.SCHEMA}-unplanned-gap"
            or existing.get("schema_version") != 2
            or existing.get("scan_index") != scan_index
            or existing.get("scheduled_ns") != scheduled_ns
            or type(existing.get("observed_ns")) is not int
            or existing["observed_ns"] < scheduled_ns + discovery.SCAN_EXPIRY_NS
            or existing.get("reason") != "expired_before_plan_or_dispatch"
            or existing.get("exposure") != "unknown"
            or existing.get("selected_pool_ids") is not None
            or existing.get("provider_io") is not False
            or existing.get("cursor_advanced") is not False
        ):
            _raise("discovery_existing_gap_invalid")
        return existing
    value = {
        "schema": f"{runtime.SCHEMA}-unplanned-gap",
        "schema_version": 2,
        "scan_index": scan_index,
        "scheduled_ns": scheduled_ns,
        "observed_ns": observed_ns,
        "reason": "expired_before_plan_or_dispatch",
        "exposure": "unknown",
        "selected_pool_ids": None,
        "provider_io": False,
        "cursor_advanced": False,
    }
    _check_guard(guard, writer, binding)
    _create_json(
        writer,
        gap_path,
        value,
    )
    _check_guard(guard, writer, binding)
    return value


def _close_elapsed_gaps(
    *,
    writer: Any,
    guard: Any,
    binding: Mapping[str, Any],
    policy: Mapping[str, Any],
    epoch: Mapping[str, Any],
    through_scan_index: int,
    now_ns: int,
) -> list[dict[str, Any]]:
    """Seal unplanned elapsed slots as unknown gaps before a current scan."""
    epoch_index = epoch["epoch_index"]
    gaps: list[dict[str, Any]] = []
    for scan_index in range(through_scan_index):
        scheduled_ns = policy["first_scan_ns"] + scan_index * policy["scan_interval_seconds"] * NS
        plan_path = _scan_plan_path(epoch_index, scan_index)
        if writer.exists(plan_path):
            plan = _read_json(writer, plan_path)
            runtime.verify_epoch_scan_plan(plan, policy=policy, epoch=epoch)
            records, _raws = _load_scan_records(writer, plan)
            if len(records) != len(plan["page_slots"]):
                _raise("discovery_expired_prior_plan_unresolved")
            continue
        if now_ns < scheduled_ns + discovery.SCAN_EXPIRY_NS:
            _raise("discovery_prior_slot_not_yet_expired")
        gaps.append(
            _record_unplanned_gap(
                writer,
                guard,
                binding,
                epoch_index=epoch_index,
                scan_index=scan_index,
                scheduled_ns=scheduled_ns,
                observed_ns=now_ns,
            )
        )
    return gaps


def _save_plan(
    writer: Any, guard: Any, binding: Mapping[str, Any], plan: Mapping[str, Any]
) -> None:
    path = _scan_plan_path(plan["epoch_index"], plan["scan_index"])
    _check_guard(guard, writer, binding)
    _create_json(writer, path, plan)
    _check_guard(guard, writer, binding)


def _provider_fetch(url: str) -> tuple[int, bytes, int]:
    """Single direct HTTPS GET; fixed host/path and no proxy or redirect support."""
    expected_prefix = (
        f"https://api.geckoterminal.com{discovery.ENDPOINT}?include={discovery.INCLUDE}&page="
    )
    if not url.startswith(expected_prefix) or not url[len(expected_prefix) :].isdigit():
        _raise("discovery_provider_url_rejected")
    started = time.time_ns()
    monotonic_started = time.monotonic_ns()
    connection = http.client.HTTPSConnection(
        "api.geckoterminal.com",
        port=443,
        timeout=HTTP_TIMEOUT_SECONDS,
        context=ssl.create_default_context(),
    )
    body = b""
    status = 0
    try:
        path = url.removeprefix("https://api.geckoterminal.com")
        connection.request(
            "GET", path, headers={"Accept": discovery.API_ACCEPT, "User-Agent": USER_AGENT}
        )
        response = connection.getresponse()
        status = response.status
        body = response.read(MAX_PAGE_BODY + 1)
    finally:
        connection.close()
    elapsed = time.monotonic_ns() - monotonic_started
    if elapsed > HTTP_TIMEOUT_SECONDS * NS:
        raise TimeoutError("provider_total_deadline")
    return status, body, started


def _rate_events(writer: Any) -> list[dict[str, Any]]:
    try:
        writer.list_names(RATE_DIR)
    except FileNotFoundError:
        return []
    rows = []
    for name in writer.list_names(RATE_DIR):
        if not re.fullmatch(r"[0-9]{20}-[0-9a-f]{64}\.json", name):
            _raise("discovery_rate_journal_name")
        row = _read_json(writer, f"{RATE_DIR}/{name}", limit=16_384)
        if type(row) is not dict or set(row) != {
            "schema",
            "schema_version",
            "request_id",
            "dispatch_ns",
            "sha256",
        }:
            _raise("discovery_rate_event_shape")
        core = dict(row)
        digest = core.pop("sha256")
        if row["schema"] != f"{runtime.SCHEMA}-rate-event" or row["schema_version"] != 2:
            _raise("discovery_rate_event_schema")
        if digest != runtime.sha256(runtime.canonical(core)) or not runtime._is_hash(
            row["request_id"]
        ):
            _raise("discovery_rate_event_hash")
        if type(row["dispatch_ns"]) is not int or row["dispatch_ns"] <= 0:
            _raise("discovery_rate_event_time")
        rows.append(row)
    return sorted(rows, key=lambda item: item["dispatch_ns"])


def _check_rate_gate(writer: Any, *, now_ns: int, priority_gate: Callable[[], bool] | None) -> None:
    if priority_gate is None:
        _raise("discovery_v7_priority_unverified")
    if priority_gate():
        _raise("discovery_v7_priority_active")
    rows = _rate_events(writer)
    if rows and now_ns - rows[-1]["dispatch_ns"] < 12 * NS:
        _raise("discovery_global_min_spacing")
    recent = [row for row in rows if now_ns - 60 * NS < row["dispatch_ns"] <= now_ns]
    if len(recent) >= 5:
        _raise("discovery_global_rolling_rate_cap")
    return None


def _record_rate_dispatch(writer: Any, *, request_id: str, dispatch_ns: int) -> None:
    core = {
        "schema": f"{runtime.SCHEMA}-rate-event",
        "schema_version": 2,
        "request_id": request_id,
        "dispatch_ns": dispatch_ns,
    }
    row = {**core, "sha256": runtime.sha256(runtime.canonical(core))}
    _create_json(writer, f"{RATE_DIR}/{dispatch_ns:020d}-{request_id}.json", row)


def _load_scan_records(
    writer: Any, plan: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    records: list[dict[str, Any]] = []
    raws: dict[str, bytes] = {}
    for expected in plan["page_slots"]:
        page = expected["page"]
        result_path = _page_path(plan["epoch_index"], plan["scan_index"], page, "result.json")
        if not writer.exists(result_path):
            continue
        row = _read_json(writer, result_path)
        if row.get("request_id") != expected["request_id"]:
            _raise("discovery_result_plan_binding")
        _verify_page_transaction(writer, plan, expected, row)
        if row.get("raw_sha256") is not None:
            raw_path = _page_path(plan["epoch_index"], plan["scan_index"], page, "raw.bin")
            raw = writer.read_bytes(raw_path, max_bytes=MAX_PAGE_BODY)
            if runtime.sha256(raw) != row["raw_sha256"] or len(raw) != row["raw_bytes"]:
                _raise("discovery_raw_result_binding")
            raws[row["request_id"]] = raw
        elif writer.exists(_page_path(plan["epoch_index"], plan["scan_index"], page, "raw.bin")):
            orphan_path = _page_path(
                plan["epoch_index"], plan["scan_index"], page, "orphan-raw.json"
            )
            if not writer.exists(orphan_path):
                _raise("discovery_unbound_orphan_raw")
            raw = writer.read_bytes(
                _page_path(plan["epoch_index"], plan["scan_index"], page, "raw.bin"),
                max_bytes=MAX_PAGE_BODY,
            )
            marker = _read_json(writer, orphan_path, limit=16_384)
            if (
                marker.get("request_id") != row["request_id"]
                or marker.get("raw_sha256") != runtime.sha256(raw)
                or marker.get("raw_bytes") != len(raw)
            ):
                _raise("discovery_orphan_raw_binding")
        records.append(row)
    return records, raws


def _verify_page_transaction(
    writer: Any, plan: Mapping[str, Any], expected: Mapping[str, Any], result: Mapping[str, Any]
) -> None:
    required_result = {
        "request_id",
        "status",
        "request_started_ns",
        "receipt_ns",
        "durable_available_ns",
        "raw_body",
        "raw_sha256",
        "raw_bytes",
        "http_status",
        "body_truncated",
    }
    if set(result) != required_result or result["raw_body"] is not None:
        _raise("discovery_result_schema")
    page = expected["page"]
    base = _page_directory(plan["epoch_index"], plan["scan_index"])
    intent_path = f"{base}/page-{page:02d}.intent.json"
    dispatch_path = f"{base}/page-{page:02d}.dispatch.json"
    confirm_path = f"{base}/page-{page:02d}.confirmation.json"
    is_missed = result["status"] == "missed"
    if not writer.exists(confirm_path):
        _raise("discovery_result_missing_confirmation")
    confirmation = _read_json(writer, confirm_path)
    if (
        confirmation.get("schema") != runtime.PAGE_CONFIRM_SCHEMA
        or confirmation.get("schema_version") != 2
        or confirmation.get("request_id") != expected["request_id"]
    ):
        _raise("discovery_confirmation_binding")
    if is_missed:
        if writer.exists(dispatch_path):
            _raise("discovery_missed_has_dispatch")
        if writer.exists(intent_path):
            intent = _read_json(writer, intent_path)
            if not writer.exists(f"{base}/page-{page:02d}.dispatch.json"):
                if confirmation.get("provider_io") is not False:
                    _raise("discovery_missed_intent_confirmation")
                if not runtime._is_id(intent.get("reservation_id")):
                    _raise("discovery_missed_intent_identity")
    else:
        if not writer.exists(intent_path) or not writer.exists(dispatch_path):
            _raise("discovery_attempt_missing_durable_intent")
        intent = _read_json(writer, intent_path)
        dispatch = _read_json(writer, dispatch_path)
        if (
            intent.get("schema") != runtime.PAGE_INTENT_SCHEMA
            or intent.get("request_id") != expected["request_id"]
            or intent.get("reservation_id") != dispatch.get("reservation_id")
            or dispatch.get("schema") != runtime.PAGE_DISPATCH_SCHEMA
            or dispatch.get("request_id") != expected["request_id"]
            or type(dispatch.get("dispatch_ns")) is not int
            or dispatch["dispatch_ns"] <= 0
            or type(result.get("request_started_ns")) is not int
            or result["request_started_ns"] < dispatch["dispatch_ns"]
            or (
                result.get("receipt_ns") is not None
                and result["request_started_ns"] > result["receipt_ns"]
            )
            or confirmation.get("provider_io") is not True
        ):
            _raise("discovery_dispatch_intent_join")
    # Confirmation carries the outcome digest; resolve the reservation from the
    # intent/dispatch when present. A genuinely unattempted missed slot has no
    # outcome artifact.
    if not is_missed or writer.exists(intent_path):
        reservation_id = (intent if writer.exists(intent_path) else dispatch)["reservation_id"]
        outcome_path = f"{RUNTIME_ROOT}/outcomes/{reservation_id}.json"
        if not writer.exists(outcome_path):
            _raise("discovery_result_missing_coordinator_outcome")
        outcome_raw = writer.read_bytes(outcome_path, max_bytes=32_768)
        if runtime.sha256(outcome_raw) != confirmation.get("outcome_sha256"):
            _raise("discovery_confirmation_outcome_hash")
        outcome = _json(outcome_raw)
        if outcome.get("request_id") != expected["request_id"]:
            _raise("discovery_outcome_request_binding")


def _load_seen_pool_ids(
    writer: Any,
    policy: Mapping[str, Any],
    *,
    epoch: Mapping[str, Any],
    epoch_index: int,
    before_scan_index: int,
) -> list[str]:
    """Rebuild first-seen history across all sealed epochs and current prefix."""
    seen: list[str] = []
    for replay_epoch_index in range(epoch_index + 1):
        if replay_epoch_index == epoch_index:
            replay_policy, replay_epoch = policy, epoch
            limit = before_scan_index
        else:
            replay_policy = _read_json(
                writer, _epoch_policy_path(replay_epoch_index), limit=512_000
            )
            replay_epoch = _read_json(
                writer, _epoch_document_path(replay_epoch_index), limit=512_000
            )
            runtime.verify_epoch_envelope(replay_epoch, policy=replay_policy)
            if not writer.exists(f"{_epoch_root(replay_epoch_index)}/seal.json"):
                _raise("discovery_seen_unsealed_prior_epoch")
            limit = replay_policy["max_scan_count"]
        try:
            scan_names = writer.list_names(_epoch_scans_root(replay_epoch_index))
        except FileNotFoundError:
            scan_names = []
        for name in sorted(scan_names):
            if not re.fullmatch(r"[0-9]{4}", name):
                _raise("discovery_scan_directory_name")
            scan_index = int(name)
            if scan_index >= limit:
                continue
            plan = _read_json(writer, _scan_plan_path(replay_epoch_index, scan_index))
            runtime.verify_epoch_scan_plan(plan, policy=replay_policy, epoch=replay_epoch)
            if plan.get("scan_index") != scan_index:
                _raise("discovery_seen_plan_index")
            records, raws = _load_scan_records(writer, plan)
            if len(records) != len(plan["page_slots"]):
                _raise("discovery_seen_prior_scan_incomplete")
            replayed = runtime.verify_persisted_scan(
                policy=replay_policy,
                plan=plan,
                page_records=records,
                raw_by_request_id=raws,
                previously_seen_pool_ids=seen,
            )
            summary_path = f"{_page_directory(replay_epoch_index, scan_index)}/summary.json"
            if writer.exists(summary_path):
                summary_raw = writer.read_bytes(summary_path, max_bytes=16_000_000)
                if _json(summary_raw) != replayed:
                    _raise("discovery_scan_summary_replay_mismatch")
            else:
                _create_json(writer, summary_path, replayed)
            seen.extend(replayed["first_seen_pool_ids"])
    if len(seen) != len(set(seen)):
        _raise("discovery_seen_pool_duplicate")
    return seen


def _persist_summary(
    writer: Any,
    guard: Any,
    binding: Mapping[str, Any],
    plan: Mapping[str, Any],
    result: Mapping[str, Any],
) -> None:
    path = f"{_page_directory(plan['epoch_index'], plan['scan_index'])}/summary.json"
    _check_guard(guard, writer, binding)
    _create_json(writer, path, result)
    _check_guard(guard, writer, binding)


def _recover_prior_scans(
    *,
    writer: Any,
    guard: Any,
    binding: Mapping[str, Any],
    before_scan_index: int,
    epoch_index: int,
    policy: Mapping[str, Any],
    epoch: Mapping[str, Any],
    capacity: V3CapacityPort,
    now_ns: int,
) -> None:
    try:
        names = writer.list_names(_epoch_scans_root(epoch_index))
    except FileNotFoundError:
        return
    for name in sorted(names):
        if not re.fullmatch(r"[0-9]{4}", name):
            _raise("discovery_scan_directory_name")
        index = int(name)
        if index >= before_scan_index:
            continue
        plan = _read_json(writer, _scan_plan_path(epoch_index, index))
        runtime.verify_epoch_scan_plan(plan, policy=policy, epoch=epoch)
        if plan.get("scan_index") != index:
            _raise("discovery_prior_plan_index")
        _recover_scan(
            writer=writer,
            guard=guard,
            binding=binding,
            plan=plan,
            capacity=capacity,
            release_id=epoch["release_id"],
            now_ns=now_ns,
        )
        records, raws = _load_scan_records(writer, plan)
        if len(records) != len(plan["page_slots"]):
            _raise("discovery_prior_scan_still_open")
        previous_seen = _load_seen_pool_ids(
            writer,
            policy,
            epoch=epoch,
            epoch_index=epoch_index,
            before_scan_index=index,
        )
        replayed = runtime.verify_persisted_scan(
            policy=policy,
            plan=plan,
            page_records=records,
            raw_by_request_id=raws,
            previously_seen_pool_ids=previous_seen,
        )
        summary_path = f"{_page_directory(epoch_index, index)}/summary.json"
        if writer.exists(summary_path):
            if _read_json(writer, summary_path, limit=16_000_000) != replayed:
                _raise("discovery_prior_summary_changed")
        else:
            _persist_summary(writer, guard, binding, plan, replayed)


def _recover_scan(
    *,
    writer: Any,
    guard: Any,
    binding: Mapping[str, Any],
    plan: Mapping[str, Any],
    capacity: V3CapacityPort,
    release_id: str,
    now_ns: int,
) -> None:
    """Close interrupted prefixes without replaying a possibly sent request."""
    for expected in plan["page_slots"]:
        page = expected["page"]
        intent_path = _page_path(plan["epoch_index"], plan["scan_index"], page, "intent.json")
        dispatch_path = _page_path(plan["epoch_index"], plan["scan_index"], page, "dispatch.json")
        result_path = _page_path(plan["epoch_index"], plan["scan_index"], page, "result.json")
        reservation_id = _reservation_id(expected["request_id"], release_id)
        outcome_path = f"{RUNTIME_ROOT}/outcomes/{reservation_id}.json"
        if writer.exists(result_path):
            if not writer.exists(
                _page_path(plan["epoch_index"], plan["scan_index"], page, "confirmation.json")
            ):
                result = _read_json(writer, result_path)
                intent = _read_json(writer, intent_path) if writer.exists(intent_path) else None
                dispatch = (
                    _read_json(writer, dispatch_path) if writer.exists(dispatch_path) else None
                )
                if intent is not None:
                    reservation_id = intent.get("reservation_id")
                    if not runtime._is_id(reservation_id):
                        _raise("discovery_recovery_result_intent_invalid")
                    if result.get("status") == "missed":
                        outcome_status, ambiguous, release = "pre_intent_absent", False, True
                    else:
                        outcome_status, ambiguous, _fatal, cooldown = _classify_result(
                            result.get("status"), result.get("http_status"), now_ns=now_ns
                        )
                        release = False
                    outcome = (
                        _pre_intent_absent_outcome(expected["request_id"])
                        if release
                        else {
                            "schema": f"{runtime.SCHEMA}-outcome",
                            "schema_version": 2,
                            "request_id": expected["request_id"],
                            "status": outcome_status,
                            "http_status": result.get("http_status"),
                            "result_sha256": runtime.sha256(runtime.canonical(result)),
                            "receipt_ns": result.get("receipt_ns"),
                            "available_ns": result.get("durable_available_ns"),
                            "cooldown_until_ns": cooldown,
                        }
                    )
                    outcome_raw = runtime.canonical(outcome)
                    capacity.settle(
                        reservation_id=reservation_id,
                        outcome=outcome_status,
                        outcome_bytes=outcome_raw,
                        allocated_bytes=v2.physical_inventory_bytes(writer, SERVICE_ROOT),
                        at_ns=max(now_ns, (result.get("receipt_ns") or 0) + 1),
                        ambiguous=ambiguous,
                        release=release,
                    )
                    _create_json(
                        writer,
                        _page_path(
                            plan["epoch_index"], plan["scan_index"], page, "confirmation.json"
                        ),
                        {
                            "schema": runtime.PAGE_CONFIRM_SCHEMA,
                            "schema_version": 2,
                            "request_id": expected["request_id"],
                            "availability_ns": result.get("durable_available_ns"),
                            "confirmed_ns": now_ns,
                            "outcome_sha256": runtime.sha256(outcome_raw),
                            "provider_io": dispatch is not None,
                            "reservation_id": reservation_id,
                        },
                    )
                elif result.get("status") == "missed":
                    _write_unattempted_missed(
                        writer, plan, expected, now_ns, "recovered_missed_result"
                    )
                else:
                    _raise("discovery_result_without_intent")
            continue
        if writer.exists(dispatch_path):
            if now_ns < plan["expires_ns"]:
                continue
            dispatch = _read_json(writer, dispatch_path)
            dispatch_ns = dispatch.get("dispatch_ns")
            if type(dispatch_ns) is not int or dispatch_ns <= 0:
                _raise("discovery_recovery_dispatch_invalid")
            row = runtime.make_page_record(
                request_id=expected["request_id"],
                status="transport_ambiguous",
                # A surviving dispatch without a returned fetch marker proves
                # only that sending was possible. Preserve its durable lower
                # bound; never represent it as a receipt or durable availability.
                request_started_ns=dispatch_ns,
                receipt_ns=None,
                durable_available_ns=None,
                raw_body=None,
                http_status=None,
                body_truncated=False,
            )
            raw_path = _page_path(plan["epoch_index"], plan["scan_index"], page, "raw.bin")
            if writer.exists(raw_path):
                orphan_raw = writer.read_bytes(raw_path, max_bytes=MAX_PAGE_BODY)
                _create_json(
                    writer,
                    _page_path(plan["epoch_index"], plan["scan_index"], page, "orphan-raw.json"),
                    {
                        "schema": f"{runtime.SCHEMA}-orphan-raw",
                        "schema_version": 2,
                        "request_id": expected["request_id"],
                        "raw_sha256": runtime.sha256(orphan_raw),
                        "raw_bytes": len(orphan_raw),
                        "retained_as_response": False,
                    },
                )
            _create_json(writer, result_path, row)
            reservation_id = dispatch["reservation_id"]
            outcome = {
                "schema": f"{runtime.SCHEMA}-outcome",
                "schema_version": 2,
                "request_id": expected["request_id"],
                "status": "ambiguous_unclassified",
                "reason": "restart_after_dispatch_without_terminal",
            }
            capacity.settle(
                reservation_id=reservation_id,
                outcome="ambiguous_unclassified",
                outcome_bytes=runtime.canonical(outcome),
                allocated_bytes=0,
                at_ns=max(now_ns, dispatch_ns + 1),
                ambiguous=True,
            )
            _create_json(
                writer,
                _page_path(plan["epoch_index"], plan["scan_index"], page, "confirmation.json"),
                {
                    "schema": runtime.PAGE_CONFIRM_SCHEMA,
                    "schema_version": 2,
                    "request_id": expected["request_id"],
                    "availability_ns": None,
                    "outcome_sha256": runtime.sha256(runtime.canonical(outcome)),
                    "recovered_at_ns": now_ns,
                    "provider_io": True,
                    "reservation_id": reservation_id,
                },
            )
            _create_stop(
                writer, now_ns, "ambiguous_request_requires_review", expected["request_id"]
            )
        elif writer.exists(intent_path):
            if now_ns < plan["expires_ns"]:
                continue
            intent = _read_json(writer, intent_path)
            if intent.get("reservation_id") != reservation_id:
                _raise("discovery_recovery_intent_invalid")
            status = capacity.reservation_status(
                request_id=expected["request_id"],
                reservation_id=reservation_id,
                intent_path=intent_path,
                outcome_path=outcome_path,
            )
            if status["state"] == "absent":
                _raise("discovery_recovery_intent_without_reservation")
            missed = runtime.make_page_record(
                request_id=expected["request_id"],
                status="missed",
                request_started_ns=None,
                receipt_ns=None,
                durable_available_ns=None,
                raw_body=None,
                http_status=None,
                body_truncated=False,
            )
            _create_json(writer, result_path, missed)
            outcome = _pre_intent_absent_outcome(expected["request_id"])
            capacity.settle(
                reservation_id=reservation_id,
                outcome="pre_intent_absent",
                outcome_bytes=runtime.canonical(outcome),
                allocated_bytes=0,
                at_ns=max(now_ns, status["tail_at_ns"] + 1),
                release=True,
            )
            _create_json(
                writer,
                _page_path(plan["epoch_index"], plan["scan_index"], page, "confirmation.json"),
                {
                    "schema": runtime.PAGE_CONFIRM_SCHEMA,
                    "schema_version": 2,
                    "request_id": expected["request_id"],
                    "availability_ns": now_ns,
                    "outcome_sha256": runtime.sha256(runtime.canonical(outcome)),
                    "recovered_at_ns": now_ns,
                    "provider_io": False,
                    "reservation_id": reservation_id,
                },
            )
        else:
            status = capacity.reservation_status(
                request_id=expected["request_id"],
                reservation_id=reservation_id,
                intent_path=intent_path,
                outcome_path=outcome_path,
            )
            if status["state"] == "reserved":
                missed = runtime.make_page_record(
                    request_id=expected["request_id"],
                    status="missed",
                    request_started_ns=None,
                    receipt_ns=None,
                    durable_available_ns=None,
                    raw_body=None,
                    http_status=None,
                    body_truncated=False,
                )
                _create_json(writer, result_path, missed)
                outcome = _pre_intent_absent_outcome(expected["request_id"])
                capacity.settle(
                    reservation_id=reservation_id,
                    outcome="pre_intent_absent",
                    outcome_bytes=runtime.canonical(outcome),
                    allocated_bytes=v2.physical_inventory_bytes(writer, SERVICE_ROOT),
                    at_ns=max(now_ns, status["tail_at_ns"] + 1),
                    release=True,
                )
                _create_json(
                    writer,
                    _page_path(plan["epoch_index"], plan["scan_index"], page, "confirmation.json"),
                    {
                        "schema": runtime.PAGE_CONFIRM_SCHEMA,
                        "schema_version": 2,
                        "request_id": expected["request_id"],
                        "availability_ns": None,
                        "confirmed_ns": now_ns,
                        "outcome_sha256": runtime.sha256(runtime.canonical(outcome)),
                        "provider_io": False,
                        "reservation_id": reservation_id,
                    },
                )
            elif status["state"] == "terminal":
                terminal = status["terminal"]
                if (
                    terminal["kind"] != "released"
                    or terminal["fields"].get("outcome") != "pre_intent_absent"
                ):
                    _raise("discovery_orphan_reservation_terminal")
                outcome = _pre_intent_absent_outcome(expected["request_id"])
                _create_json(writer, outcome_path, outcome)
                missed = runtime.make_page_record(
                    request_id=expected["request_id"],
                    status="missed",
                    request_started_ns=None,
                    receipt_ns=None,
                    durable_available_ns=None,
                    raw_body=None,
                    http_status=None,
                    body_truncated=False,
                )
                _create_json(writer, result_path, missed)
                _create_json(
                    writer,
                    _page_path(plan["epoch_index"], plan["scan_index"], page, "confirmation.json"),
                    {
                        "schema": runtime.PAGE_CONFIRM_SCHEMA,
                        "schema_version": 2,
                        "request_id": expected["request_id"],
                        "availability_ns": None,
                        "confirmed_ns": now_ns,
                        "outcome_sha256": runtime.sha256(runtime.canonical(outcome)),
                        "provider_io": False,
                        "reservation_id": reservation_id,
                    },
                )
            else:
                if now_ns < plan["expires_ns"]:
                    continue
                missed = runtime.make_page_record(
                    request_id=expected["request_id"],
                    status="missed",
                    request_started_ns=None,
                    receipt_ns=None,
                    durable_available_ns=None,
                    raw_body=None,
                    http_status=None,
                    body_truncated=False,
                )
                _create_json(writer, result_path, missed)
                _write_unattempted_missed(
                    writer, plan, expected, now_ns, "expired_without_intent_or_dispatch"
                )


def _reservation_id(request_id: str, release_id: str) -> str:
    return hashlib.sha256(
        runtime.canonical({"request_id": request_id, "release_id": release_id})
    ).hexdigest()[:32]


def _pre_intent_absent_outcome(request_id: str) -> dict[str, Any]:
    return {
        "schema": f"{runtime.SCHEMA}-outcome",
        "schema_version": 2,
        "request_id": request_id,
        "status": "pre_intent_absent",
    }


def _create_stop(writer: Any, now_ns: int, reason: str, request_id: str) -> None:
    value = {
        "schema": f"{runtime.SCHEMA}-stop",
        "schema_version": 2,
        "at_ns": now_ns,
        "reason": reason,
        "request_id": request_id,
    }
    _create_json(writer, STOP_PATH, value)


def _write_unattempted_missed(
    writer: Any, plan: Mapping[str, Any], slot: Mapping[str, Any], at_ns: int, reason: str
) -> None:
    outcome = {
        "schema": f"{runtime.SCHEMA}-outcome",
        "schema_version": 2,
        "request_id": slot["request_id"],
        "status": "missed",
        "reason": reason,
    }
    _create_json(
        writer,
        _page_path(plan["epoch_index"], plan["scan_index"], slot["page"], "confirmation.json"),
        {
            "schema": runtime.PAGE_CONFIRM_SCHEMA,
            "schema_version": 2,
            "request_id": slot["request_id"],
            "availability_ns": at_ns,
            "outcome_sha256": runtime.sha256(runtime.canonical(outcome)),
            "recovered_at_ns": at_ns,
            "provider_io": False,
            "reservation_id": None,
        },
    )


def run_scan(
    *,
    writer: Any,
    guard: Any,
    binding: Mapping[str, Any],
    policy: Mapping[str, Any],
    epoch: Mapping[str, Any],
    scan_index: int,
    scheduled_ns: int,
    now_ns: Callable[[], int],
    fetch_page: Callable[[str], tuple[int, bytes, int]],
    capacity: V3CapacityPort,
    priority_gate: Callable[[], bool] | None,
) -> dict[str, Any]:
    """Replay, recover, then execute one scheduled scan under exclusive locks."""
    checked_policy = discovery.verify_policy(policy)
    runtime.verify_epoch_envelope(epoch, policy=checked_policy)
    _check_guard(guard, writer, binding)
    epoch_index = epoch["epoch_index"]
    current_time_ns = now_ns()
    epoch_due_index = max(
        0,
        (current_time_ns - checked_policy["first_scan_ns"])
        // (checked_policy["scan_interval_seconds"] * NS),
    )
    epoch_due_index = min(epoch_due_index, checked_policy["max_scan_count"])
    plan = runtime.compile_epoch_scan_plan(
        policy=checked_policy,
        epoch=epoch,
        scan_index=scan_index,
        scheduled_ns=scheduled_ns,
    )
    with ExitStack() as stack:
        stack.enter_context(writer.lock(LANE_LOCK_PATH, blocking=False))
        # This existing cross-release gecko lane lock makes discovery mutually
        # exclusive with legacy Gecko mutators; the v3 global lock is acquired
        # inside each coordinator operation after the lane lock.
        v2_policy = v3._v2_policy(writer.read_bytes(v2.POLICY_PATH, max_bytes=4_000_000))
        gecko_lane = v2_policy["service_lock_paths"]["gecko"]
        stack.enter_context(writer.lock(gecko_lane, blocking=False))
        _check_guard(guard, writer, binding)
        current_time_ns = now_ns()
        epoch_due_index = max(
            0,
            (current_time_ns - checked_policy["first_scan_ns"])
            // (checked_policy["scan_interval_seconds"] * NS),
        )
        epoch_due_index = min(epoch_due_index, checked_policy["max_scan_count"])
        # Full local replay precedes any plan decision or capacity mutation.
        _recover_prior_scans(
            writer=writer,
            guard=guard,
            binding=binding,
            before_scan_index=min(epoch_due_index, checked_policy["max_scan_count"]),
            epoch_index=epoch_index,
            policy=checked_policy,
            epoch=epoch,
            capacity=capacity,
            now_ns=now_ns(),
        )
        observed_ns = now_ns()
        if observed_ns >= checked_policy["first_scan_ns"]:
            _close_elapsed_gaps(
                writer=writer,
                guard=guard,
                binding=binding,
                policy=checked_policy,
                epoch=epoch,
                through_scan_index=epoch_due_index,
                now_ns=observed_ns,
            )
        if epoch_due_index >= checked_policy["max_scan_count"]:
            return {
                "scan_id": None,
                "complete": False,
                "epoch_elapsed": True,
                "provider_io": False,
                "next_epoch_required": True,
            }
        if scan_index < epoch_due_index:
            return {
                "scan_id": None,
                "complete": False,
                "stale_scan_refused": True,
                "next_scan_index": epoch_due_index,
                "provider_io": False,
            }
        if scan_index > epoch_due_index:
            return {
                "scan_id": None,
                "complete": False,
                "idle": True,
                "provider_io": False,
            }
        plan_path = _scan_plan_path(epoch_index, scan_index)
        observed_ns = now_ns()
        if not writer.exists(plan_path):
            if observed_ns < scheduled_ns:
                return {"scan_id": None, "complete": False, "idle": True, "provider_io": False}
            if observed_ns >= scheduled_ns + discovery.SCAN_EXPIRY_NS:
                return _record_unplanned_gap(
                    writer,
                    guard,
                    binding,
                    epoch_index=epoch_index,
                    scan_index=scan_index,
                    scheduled_ns=scheduled_ns,
                    observed_ns=observed_ns,
                )
        if writer.exists(plan_path):
            stored_plan = _read_json(writer, plan_path)
            runtime.verify_epoch_scan_plan(stored_plan, policy=checked_policy, epoch=epoch)
            if stored_plan != plan:
                _raise("discovery_existing_plan_conflict")
        else:
            _save_plan(writer, guard, binding, plan)
        _recover_scan(
            writer=writer,
            guard=guard,
            binding=binding,
            plan=plan,
            capacity=capacity,
            release_id=epoch["release_id"],
            now_ns=now_ns(),
        )
        if writer.exists(STOP_PATH) and now_ns() < plan["expires_ns"]:
            return {
                "scan_id": plan["scan_id"],
                "complete": False,
                "stopped": True,
                "provider_io": False,
            }
        previous_seen = _load_seen_pool_ids(
            writer,
            checked_policy,
            epoch=epoch,
            epoch_index=epoch_index,
            before_scan_index=scan_index,
        )
        records, raw_by_id = _load_scan_records(writer, plan)
        completed = {row["request_id"] for row in records}
        if len(records) == len(plan["page_slots"]):
            replayed = runtime.verify_persisted_scan(
                policy=checked_policy,
                plan=plan,
                page_records=records,
                raw_by_request_id=raw_by_id,
                previously_seen_pool_ids=previous_seen,
            )
            _persist_summary(writer, guard, binding, plan, replayed)
            return replayed
        unresolved_prior_slot = False
        for slot in plan["page_slots"]:
            if unresolved_prior_slot:
                break
            if slot["request_id"] in completed:
                continue
            # Durable intent or dispatch is never sent a second time. Recovery
            # either closes the no-dispatch reservation at expiry or terminalizes
            # a possibly-sent request as ambiguous.
            if writer.exists(_page_path(epoch_index, scan_index, slot["page"], "intent.json")):
                unresolved_prior_slot = True
                continue
            current_ns = now_ns()
            if current_ns >= plan["expires_ns"]:
                continue
            if current_ns < slot["planned_dispatch_ns"]:
                break
            try:
                with writer.lock(RATE_LOCK_PATH, blocking=False):
                    _check_rate_gate(writer, now_ns=current_ns, priority_gate=priority_gate)
            except DiscoveryHostError as exc:
                if str(exc) in {
                    "discovery_v7_priority_active",
                    "discovery_global_min_spacing",
                    "discovery_global_rolling_rate_cap",
                }:
                    # A deferred page has not reserved capacity or created an
                    # intent. A later wake-up may attempt it only inside this
                    # same immutable plan window.
                    break
                raise
            _check_guard(guard, writer, binding)
            reservation_id = _reservation_id(slot["request_id"], epoch["release_id"])
            intent = {
                "schema": runtime.PAGE_INTENT_SCHEMA,
                "schema_version": 2,
                "request_id": slot["request_id"],
                "reservation_id": reservation_id,
                "scan_index": scan_index,
                "page": slot["page"],
                "planned_dispatch_ns": slot["planned_dispatch_ns"],
                "method": "GET",
                "url": slot["url"],
                "accept": slot["accept"],
                "provider_io_possible": False,
            }
            intent_bytes = runtime.canonical(intent)
            intent_path = _page_path(epoch_index, scan_index, slot["page"], "intent.json")
            outcome_path = f"{RUNTIME_ROOT}/outcomes/{reservation_id}.json"
            try:
                capacity.reserve(
                    request_id=slot["request_id"],
                    page_ordinal=(scan_index * len(plan["page_slots"]) + slot["page"]),
                    reservation_id=reservation_id,
                    intent_path=intent_path,
                    outcome_path=outcome_path,
                    intent_bytes=intent_bytes,
                    at_ns=current_ns,
                )
            except Exception as exc:
                _create_stop(writer, now_ns(), "capacity_admission_failed", slot["request_id"])
                raise DiscoveryHostError("discovery_capacity_admission_failed") from exc
            _check_guard(guard, writer, binding)
            _create_json(writer, intent_path, intent)
            started = now_ns()
            if started < slot["planned_dispatch_ns"] or started >= plan["expires_ns"]:
                outcome = {
                    "schema": f"{runtime.SCHEMA}-outcome",
                    "schema_version": 2,
                    "request_id": slot["request_id"],
                    "status": "pre_intent_absent",
                }
                capacity.settle(
                    reservation_id=reservation_id,
                    outcome="pre_intent_absent",
                    outcome_bytes=runtime.canonical(outcome),
                    allocated_bytes=0,
                    at_ns=now_ns(),
                    release=True,
                )
                missed = runtime.make_page_record(
                    request_id=slot["request_id"],
                    status="missed",
                    request_started_ns=None,
                    receipt_ns=None,
                    durable_available_ns=None,
                    raw_body=None,
                    http_status=None,
                    body_truncated=False,
                )
                _create_json(
                    writer,
                    _page_path(epoch_index, scan_index, slot["page"], "result.json"),
                    missed,
                )
                _create_json(
                    writer,
                    _page_path(epoch_index, scan_index, slot["page"], "confirmation.json"),
                    {
                        "schema": runtime.PAGE_CONFIRM_SCHEMA,
                        "schema_version": 2,
                        "request_id": slot["request_id"],
                        "availability_ns": now_ns(),
                        "outcome_sha256": runtime.sha256(runtime.canonical(outcome)),
                        "recovered_at_ns": now_ns(),
                        "provider_io": False,
                        "reservation_id": reservation_id,
                    },
                )
                continue
            dispatch = {
                "schema": runtime.PAGE_DISPATCH_SCHEMA,
                "schema_version": 2,
                "request_id": slot["request_id"],
                "reservation_id": reservation_id,
                "dispatch_ns": started,
                "provider_io_possible": True,
            }
            gate_error: DiscoveryHostError | None = None
            try:
                with writer.lock(RATE_LOCK_PATH, blocking=False):
                    _check_rate_gate(writer, now_ns=started, priority_gate=priority_gate)
                    _record_rate_dispatch(
                        writer, request_id=slot["request_id"], dispatch_ns=started
                    )
                    _create_json(
                        writer,
                        _page_path(epoch_index, scan_index, slot["page"], "dispatch.json"),
                        dispatch,
                    )
            except DiscoveryHostError as exc:
                if str(exc) not in {
                    "discovery_v7_priority_active",
                    "discovery_global_min_spacing",
                    "discovery_global_rolling_rate_cap",
                }:
                    raise
                gate_error = exc
            if gate_error is not None:
                # A priority/rate race after the durable intent is terminally
                # recorded as a no-dispatch miss. The deterministic request ID
                # remains spent; this slot is never retried or backfilled.
                outcome = _pre_intent_absent_outcome(slot["request_id"])
                _create_json(writer, outcome_path, outcome)
                capacity.settle(
                    reservation_id=reservation_id,
                    outcome="pre_intent_absent",
                    outcome_bytes=runtime.canonical(outcome),
                    allocated_bytes=0,
                    at_ns=max(now_ns(), started),
                    release=True,
                )
                missed = runtime.make_page_record(
                    request_id=slot["request_id"],
                    status="missed",
                    request_started_ns=None,
                    receipt_ns=None,
                    durable_available_ns=None,
                    raw_body=None,
                    http_status=None,
                    body_truncated=False,
                )
                _create_json(
                    writer,
                    _page_path(epoch_index, scan_index, slot["page"], "result.json"),
                    missed,
                )
                _create_json(
                    writer,
                    _page_path(epoch_index, scan_index, slot["page"], "confirmation.json"),
                    {
                        "schema": runtime.PAGE_CONFIRM_SCHEMA,
                        "schema_version": 2,
                        "request_id": slot["request_id"],
                        "availability_ns": None,
                        "confirmed_ns": now_ns(),
                        "outcome_sha256": runtime.sha256(runtime.canonical(outcome)),
                        "provider_io": False,
                        "reservation_id": reservation_id,
                    },
                )
                break
            _fetch_started = None
            try:
                fetch_result = fetch_page(slot["url"])
                if len(fetch_result) == 4:
                    status, response_body, _fetch_started, _headers = fetch_result
                else:
                    status, response_body, _fetch_started = fetch_result
                response_body = bytes(response_body)
                receipt_ns = now_ns()
                if len(response_body) > MAX_PAGE_BODY:
                    retained = response_body[:MAX_PAGE_BODY]
                    result_status = "oversize_response"
                    truncated = True
                else:
                    retained = response_body
                    result_status = "received" if status == 200 else "http_error"
                    truncated = False
            except Exception:
                status = None
                retained = None
                receipt_ns = None
                result_status = "transport_ambiguous"
                truncated = False
            raw_path = _page_path(epoch_index, scan_index, slot["page"], "raw.bin")
            if retained is not None:
                _check_guard(guard, writer, binding)
                writer.create(raw_path, retained, mode=0o600)
                _check_guard(guard, writer, binding)
            available_ns = now_ns() if retained is not None else None
            record = runtime.make_page_record(
                request_id=slot["request_id"],
                status=result_status,
                request_started_ns=(
                    _fetch_started
                    if type(_fetch_started) is int
                    and started <= _fetch_started <= (receipt_ns or _fetch_started)
                    else None
                )
                if result_status != "transport_ambiguous"
                else started,
                receipt_ns=receipt_ns,
                durable_available_ns=available_ns,
                raw_body=retained,
                http_status=status,
                body_truncated=truncated,
            )
            _check_guard(guard, writer, binding)
            _create_json(
                writer,
                _page_path(epoch_index, scan_index, slot["page"], "result.json"),
                {**record, "raw_body": None},
            )
            outcome_status, ambiguous, fatal, cooldown = _classify_result(
                result_status, status, now_ns=receipt_ns or started
            )
            outcome = {
                "schema": f"{runtime.SCHEMA}-outcome",
                "schema_version": 2,
                "request_id": slot["request_id"],
                "status": outcome_status,
                "http_status": status,
                "result_sha256": runtime.sha256(runtime.canonical({**record, "raw_body": None})),
                "receipt_ns": receipt_ns,
                "available_ns": available_ns,
                "cooldown_until_ns": cooldown,
            }
            outcome_path = f"{RUNTIME_ROOT}/outcomes/{reservation_id}.json"
            _check_guard(guard, writer, binding)
            _create_json(writer, outcome_path, outcome)
            allocated = v2.physical_inventory_bytes(writer, SERVICE_ROOT)
            capacity.settle(
                reservation_id=reservation_id,
                outcome=outcome_status,
                outcome_bytes=runtime.canonical(outcome),
                allocated_bytes=allocated,
                at_ns=max(now_ns(), receipt_ns or started),
                ambiguous=ambiguous,
            )
            _create_json(
                writer,
                _page_path(epoch_index, scan_index, slot["page"], "confirmation.json"),
                {
                    "schema": runtime.PAGE_CONFIRM_SCHEMA,
                    "schema_version": 2,
                    "request_id": slot["request_id"],
                    "availability_ns": available_ns,
                    "confirmed_ns": now_ns(),
                    "outcome_sha256": runtime.sha256(runtime.canonical(outcome)),
                    "provider_io": True,
                    "reservation_id": reservation_id,
                },
            )
            if ambiguous or fatal:
                _create_stop(writer, now_ns(), outcome_status, slot["request_id"])
                break
            # One provider request per LaunchAgent tick. launchd firings are
            # wake-up hints and may be skipped while this callback is running;
            # later planned slots get their own callback and keep their UTC
            # windows instead of being compressed into a burst.
            break
        # Close any slots whose expiry has passed. Unattempted future pages in a
        # still-live scan remain in the exact plan denominator for the next tick.
        if now_ns() >= plan["expires_ns"]:
            _recover_scan(
                writer=writer,
                guard=guard,
                binding=binding,
                plan=plan,
                capacity=capacity,
                release_id=epoch["release_id"],
                now_ns=now_ns(),
            )
        records, raw_by_id = _load_scan_records(writer, plan)
        replayed = (
            runtime.verify_persisted_scan(
                policy=checked_policy,
                plan=plan,
                page_records=records,
                raw_by_request_id=raw_by_id,
                previously_seen_pool_ids=previous_seen,
            )
            if len(records) == len(plan["page_slots"])
            else None
        )
        if replayed is not None:
            _persist_summary(writer, guard, binding, plan, replayed)
            return replayed
        return {
            "scan_id": plan["scan_id"],
            "complete": False,
            "completed_pages": len(records),
            "expected_pages": len(plan["page_slots"]),
            "provider_io": any(
                writer.exists(_page_path(epoch_index, scan_index, slot["page"], "dispatch.json"))
                for slot in plan["page_slots"]
            ),
        }


def _classify_result(
    status: str, http_status: int | None, *, now_ns: int
) -> tuple[str, bool, bool, int | None]:
    if status == "received":
        return "confirmed", False, False, None
    if status == "transport_ambiguous":
        return "ambiguous_unclassified", True, False, None
    if status == "oversize_response":
        return "fatal_stop", False, True, None
    if type(http_status) is not int:
        return "fatal_stop", False, True, None
    del now_ns
    if 400 <= http_status <= 599:
        return "fatal_stop", False, True, None
    return "fatal_stop", False, True, None


def _launchctl_labels() -> set[str]:
    try:
        result = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=10, check=True
        )
    except Exception as exc:
        raise DiscoveryHostError("discovery_launchctl_list_unavailable") from exc
    labels = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[-1].startswith("ai.memecoin.brain.senses.gecko"):
            labels.add(fields[-1])
    return labels


def _assert_no_competing_gecko_processes(*, ps_output: str, current_pid: int) -> None:
    """Reject manually started or stale Gecko collectors without logging argv."""
    for line in ps_output.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3:
            continue
        try:
            pid = int(fields[0])
            uid = int(fields[1])
        except ValueError:
            _raise("discovery_process_census_malformed")
        command = fields[2]
        if (
            "brain_senses_gecko_new_pool_discovery_host_v2" in command
            or "brain_senses_gecko_capture_only" in command
            or "brain_senses_gecko_lane" in command
        ) and (pid != current_pid or uid != os.getuid()):
            _raise("discovery_competing_gecko_process")


def _ps_snapshot() -> str:
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,uid=,command="],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except Exception as exc:
        raise DiscoveryHostError("discovery_process_census_unavailable") from exc
    return result.stdout


def _assert_exclusive_gecko_label(labels: set[str], *, allow_current: bool) -> None:
    allowed = {LABEL} if allow_current else set()
    if not labels.issubset(allowed):
        _raise("discovery_competing_gecko_label")


def _verify_loaded_identity(installed_bytes: bytes) -> None:
    """Bind this transient callback to the exact installed launchd definition."""
    try:
        value = plistlib.loads(installed_bytes)
    except Exception as exc:
        raise DiscoveryHostError("discovery_installed_plist_invalid") from exc
    expected_args = [
        sys.executable,
        "-m",
        "tools.brain_senses_gecko_new_pool_discovery_host_v2",
        "--scheduled-tick",
    ]
    if (
        type(value) is not dict
        or value.get("Label") != LABEL
        or value.get("ProgramArguments") != expected_args
        or value.get("WorkingDirectory") != str(REPO_ROOT)
        or value.get("StartInterval") != 10
        or value.get("RunAtLoad") is not True
        or value.get("KeepAlive") is not False
    ):
        _raise("discovery_plist_contract")
    labels = _launchctl_labels()
    if LABEL not in labels:
        _raise("discovery_launchd_label_unloaded")
    _assert_exclusive_gecko_label(labels, allow_current=True)
    _assert_no_competing_gecko_processes(ps_output=_ps_snapshot(), current_pid=os.getpid())
    try:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except Exception as exc:
        raise DiscoveryHostError("discovery_launchd_print_unavailable") from exc
    output = result.stdout
    match = re.search(r"(?m)^\s*path\s*=\s*([^\n]+)$", output)
    pid_match = re.search(r"(?m)^\s*pid\s*=\s*([0-9]+)\s*$", output)
    if (
        match is None
        or Path(match.group(1).strip().strip('"')).resolve() != INSTALLED_PLIST.resolve()
        or pid_match is None
        or int(pid_match.group(1)) != os.getpid()
    ):
        _raise("discovery_launchd_loaded_identity")
    args_block = re.search(r"(?ms)^\s*arguments\s*=\s*\{(.*?)^\s*\}", output)
    if args_block is None:
        _raise("discovery_launchd_arguments_missing")
    loaded_args = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', args_block.group(1))
    if loaded_args != expected_args:
        _raise("discovery_launchd_arguments_mismatch")


def prepare_release_material(
    *,
    writer: Any,
    guard: Any,
    binding: Mapping[str, Any],
    now_ns: int,
    repo_root: Path = REPO_ROOT,
    loaded_labels: set[str] | None = None,
) -> dict[str, Any]:
    """Create a no-authority source/policy candidate; activation is separate."""
    labels = _launchctl_labels() if loaded_labels is None else set(loaded_labels)
    _assert_exclusive_gecko_label(labels, allow_current=False)
    source_manifest = compile_source_manifest(repo_root)
    source_sha = source_manifest["manifest_sha256"]
    first_scan_ns = ((now_ns + 60 * NS + 300 * NS - 1) // (300 * NS)) * (300 * NS)
    policy = discovery.compile_policy(
        first_scan_ns=first_scan_ns,
        scan_interval_seconds=300,
        pages=3,
        page_spacing_seconds=12,
        sample_numerator=0,
        sample_denominator=1,
        source_spec_sha256=source_sha,
        max_scan_count=864,
    )
    identity = {
        "source_sha256": source_sha,
        "policy_sha256": policy["policy_sha256"],
        "first_scan_ns": first_scan_ns,
        "root_binding": dict(binding),
    }
    release_id = runtime.sha256(runtime.canonical(identity))[:32]
    release_sha = runtime.sha256(runtime.canonical({"release_id": release_id, **identity}))
    epoch = runtime.epoch_envelope(
        release_id=release_id,
        lease_id=runtime.sha256(f"{release_id}:lease:0".encode())[:32],
        root_id=runtime.sha256(f"{release_id}:root:0".encode())[:32],
        release_sha256=release_sha,
        source_sha256=source_sha,
        policy=policy,
        epoch_index=0,
        epoch_start_ns=first_scan_ns,
        previous_epoch_sha256=None,
        lifetime_requests_before=0,
        lifetime_physical_before=0,
        prior_allocated_physical=0,
        prior_pending_physical=0,
    )
    _check_guard(guard, writer, binding)
    _create_json(writer, f"{RUNTIME_ROOT}/source-manifest.json", source_manifest)
    initialize_runtime(
        writer=writer,
        guard=guard,
        binding=binding,
        release_id=release_id,
        release_sha256=release_sha,
        source_sha256=source_sha,
        policy=policy,
        epoch=epoch,
    )
    plist_raw = (repo_root / PLIST_RELATIVE_PATH).read_bytes()
    candidate_core = {
        "schema": f"{runtime.SCHEMA}-release-candidate",
        "schema_version": 2,
        "release_id": release_id,
        "release_sha256": release_sha,
        "source_commit": source_manifest["source_commit"],
        "source_sha256": source_sha,
        "source_manifest_sha256": runtime.sha256(runtime.canonical(source_manifest)),
        "epoch_index": 0,
        "policy_sha256": policy["policy_sha256"],
        "epoch_sha256": epoch["epoch_sha256"],
        "plist_sha256": runtime.sha256(plist_raw),
        "root_binding": dict(binding),
        "activation_authorized": False,
        "provider_io": False,
    }
    candidate = {
        **candidate_core,
        "candidate_sha256": runtime.sha256(runtime.canonical(candidate_core)),
    }
    _check_guard(guard, writer, binding)
    _create_json(writer, f"{RUNTIME_ROOT}/release-candidate.json", candidate)
    _check_guard(guard, writer, binding)
    return candidate


def activate_prepared_release(
    *,
    writer: Any,
    guard: Any,
    binding: Mapping[str, Any],
    repo_root: Path = REPO_ROOT,
    loaded_labels: set[str] | None = None,
) -> dict[str, Any]:
    """Create the final authority record after external source/release review."""
    labels = _launchctl_labels() if loaded_labels is None else set(loaded_labels)
    _assert_exclusive_gecko_label(labels, allow_current=False)
    candidate_path = f"{RUNTIME_ROOT}/release-candidate.json"
    candidate = _read_json(writer, candidate_path, limit=128_000)
    candidate_core = dict(candidate)
    candidate_sha = candidate_core.pop("candidate_sha256", None)
    if (
        type(candidate) is not dict
        or set(candidate_core)
        != {
            "schema",
            "schema_version",
            "release_id",
            "release_sha256",
            "source_commit",
            "source_sha256",
            "source_manifest_sha256",
            "epoch_index",
            "policy_sha256",
            "epoch_sha256",
            "plist_sha256",
            "root_binding",
            "activation_authorized",
            "provider_io",
        }
        or candidate["schema"] != f"{runtime.SCHEMA}-release-candidate"
        or candidate["schema_version"] != 2
        or candidate["activation_authorized"] is not False
        or candidate["provider_io"] is not False
        or candidate["root_binding"] != dict(binding)
        or candidate_sha != runtime.sha256(runtime.canonical(candidate_core))
    ):
        _raise("discovery_release_candidate_invalid")
    manifest_raw = writer.read_bytes(f"{RUNTIME_ROOT}/source-manifest.json", max_bytes=512_000)
    manifest = _json(manifest_raw)
    verify_source_manifest(repo_root, manifest)
    if (
        runtime.sha256(manifest_raw) != candidate["source_manifest_sha256"]
        or manifest["source_commit"] != candidate["source_commit"]
        or manifest["manifest_sha256"] != candidate["source_sha256"]
    ):
        _raise("discovery_release_candidate_source_changed")
    policy = _read_json(writer, _epoch_policy_path(0), limit=512_000)
    epoch = _read_json(writer, _epoch_document_path(0), limit=512_000)
    runtime.verify_epoch_envelope(epoch, policy=policy)
    plist_raw = (repo_root / PLIST_RELATIVE_PATH).read_bytes()
    if (
        policy["policy_sha256"] != candidate["policy_sha256"]
        or epoch["epoch_sha256"] != candidate["epoch_sha256"]
        or runtime.sha256(plist_raw) != candidate["plist_sha256"]
    ):
        _raise("discovery_release_candidate_material_changed")
    if writer.exists(ACTIVATION_PATH):
        _raise("discovery_activation_already_exists")
    v2_policy = v3._v2_policy(writer.read_bytes(v2.POLICY_PATH, max_bytes=4_000_000))
    with ExitStack() as stack:
        stack.enter_context(writer.lock(LANE_LOCK_PATH, blocking=False))
        stack.enter_context(writer.lock(v2_policy["service_lock_paths"]["gecko"], blocking=False))
        standby = _create_epoch_standby(
            writer=writer,
            guard=guard,
            binding=binding,
            policy=policy,
            epoch=epoch,
            prepared_at_ns=time.time_ns(),
            predecessor_frontier=None,
        )
        capacity = V3CapacityPort(writer, guard, binding)
        capacity.open_epoch_hold(
            standby=standby,
            at_ns=_next_coordinator_time(writer, guard, time.time_ns()),
        )
        initial_pointer = _active_epoch_core(
            release_id=candidate["release_id"],
            epoch=epoch,
            generation=0,
            previous_pointer_sha256=None,
            transition_sha256=None,
        )
        _check_guard(guard, writer, binding)
        _create_json(writer, ACTIVE_EPOCH_PATH, initial_pointer)
        _check_guard(guard, writer, binding)
        activation = {
            "schema": f"{runtime.SCHEMA}-activation",
            "schema_version": 3,
            "release_id": candidate["release_id"],
            "release_sha256": candidate["release_sha256"],
            "source_manifest_sha256": candidate["source_manifest_sha256"],
            "source_commit": candidate["source_commit"],
            "source_sha256": candidate["source_sha256"],
            "epoch_index": 0,
            "policy_sha256": candidate["policy_sha256"],
            "epoch_sha256": candidate["epoch_sha256"],
            "plist_sha256": candidate["plist_sha256"],
            "root_binding": dict(binding),
            "armed": True,
        }
        _check_guard(guard, writer, binding)
        _create_json(writer, ACTIVATION_PATH, activation)
        _check_guard(guard, writer, binding)
    return {"activation": activation, "provider_io": False}


def _owner_scheduled_tick() -> dict[str, Any]:
    try:
        with writer_guard(CONFIG_PATH, min_free_bytes=50 * 1024**3) as guard:
            root_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)
            try:
                root_stat = os.fstat(root_fd)
                binding = {
                    "root_id": guard.root_id,
                    "volume_uuid": guard.volume_uuid.lower(),
                    "device": root_stat.st_dev,
                    "inode": root_stat.st_ino,
                }

                def before_write() -> None:
                    revalidate_descriptor_write(guard)

                with DescriptorWriter(root_fd, before_write=before_write) as writer:
                    observed_ns = time.time_ns()
                    next_trigger_ns = _owner_next_full_tick_ns(
                        writer=writer,
                        now_ns=observed_ns,
                    )
                    if next_trigger_ns is not None and observed_ns < next_trigger_ns:
                        return {
                            "status": "idle",
                            "provider_io": False,
                            "next_trigger_ns": next_trigger_ns,
                        }
                    installed = INSTALLED_PLIST.read_bytes()
                    _verify_loaded_identity(installed)
                    release = verify_release_bundle(
                        writer=writer,
                        guard=guard,
                        binding=binding,
                        installed_plist_bytes=installed,
                    )
                    now = time.time_ns()
                    _prepare_next_epoch_standby(
                        writer=writer,
                        guard=guard,
                        binding=binding,
                        policy=release["policy"],
                        epoch=release["epoch"],
                        now_ns=now,
                    )
                    policy, epoch = _renew_epochs_if_due(
                        writer=writer,
                        guard=guard,
                        binding=binding,
                        release=release,
                        now_ns=now,
                    )
                    interval_ns = policy["scan_interval_seconds"] * NS
                    scan_index = max(0, (now - policy["first_scan_ns"]) // interval_ns)
                    if scan_index >= policy["max_scan_count"]:
                        return {
                            "status": "epoch_terminal_requires_renewal",
                            "provider_io": False,
                            "scan_index": scan_index,
                        }
                    scheduled_ns = policy["first_scan_ns"] + scan_index * interval_ns
                    standby = _read_epoch_standby(writer, epoch)
                    capacity = V3CapacityPort(writer, guard, binding, epoch=epoch, standby=standby)

                    def v7_lane_active() -> bool:
                        active = _launchctl_labels()
                        return any(
                            label.startswith("ai.memecoin.brain.senses.gecko-lane-v7")
                            or label.startswith("ai.memecoin.brain.senses.gecko-v7")
                            for label in active
                        )

                    result = run_scan(
                        writer=writer,
                        guard=guard,
                        binding=binding,
                        policy=policy,
                        epoch=epoch,
                        scan_index=scan_index,
                        scheduled_ns=scheduled_ns,
                        capacity=capacity,
                        fetch_page=_provider_fetch,
                        priority_gate=v7_lane_active,
                        now_ns=time.time_ns,
                    )
                    return {"status": "processed", **result, "provider_io": result["provider_io"]}
            finally:
                os.close(root_fd)
    except Exception as exc:
        raise DiscoveryHostError("discovery_owner_tick_failed") from exc


def _owner_next_full_tick_ns(*, writer: Any, now_ns: int) -> int | None:
    """Return a cheap read-only wake-up hint; ``None`` requires full replay now.

    launchd ticks are only wake-up hints. This fast path reads the fixed active
    epoch and current page prefix but does not advance it, reserve capacity, or
    make provider requests. Any uncertainty falls through to the full release
    verifier before the owner can mutate state or send a request.
    """
    try:
        if writer.exists(STOP_PATH):
            return now_ns + 10 * NS
        activation = _read_json(writer, ACTIVATION_PATH, limit=128_000)
        pointer = _read_active_epoch_pointer_hint(writer)
        epoch_index = pointer["epoch_index"]
        policy = _read_json(writer, _epoch_policy_path(epoch_index), limit=512_000)
        epoch = _read_json(writer, _epoch_document_path(epoch_index), limit=512_000)
        policy = discovery.verify_policy(policy)
        runtime.verify_epoch_envelope(epoch, policy=policy)
        if (
            type(activation) is not dict
            or activation.get("armed") is not True
            or activation.get("release_id") != epoch.get("release_id")
            or activation.get("epoch_index") != epoch_index
        ):
            return None
        if now_ns >= epoch["epoch_end_ns"]:
            return None
        if now_ns >= epoch["epoch_end_ns"] - 60 * 60 * NS:
            # The first tick at the T−1h gate prepares the standby. Once its
            # immutable file exists, defer further full replay until handoff.
            # A corrupt standby will fail the full verifier at transition.
            return epoch["epoch_end_ns"] if writer.exists(_standby_path(epoch_index + 1)) else None
        if now_ns < policy["first_scan_ns"]:
            return policy["first_scan_ns"]
        interval_ns = policy["scan_interval_seconds"] * NS
        scan_index = (now_ns - policy["first_scan_ns"]) // interval_ns
        if scan_index >= policy["max_scan_count"]:
            return None
        scheduled_ns = policy["first_scan_ns"] + scan_index * interval_ns
        plan_path = _scan_plan_path(epoch_index, scan_index)
        if not writer.exists(plan_path):
            return None if now_ns >= scheduled_ns else scheduled_ns
        plan = _read_json(writer, plan_path)
        runtime.verify_epoch_scan_plan(plan, policy=policy, epoch=epoch)
        if now_ns >= plan["expires_ns"]:
            return None
        for slot in plan["page_slots"]:
            result_path = _page_path(epoch_index, scan_index, slot["page"], "result.json")
            if not writer.exists(result_path):
                dispatch_path = _page_path(epoch_index, scan_index, slot["page"], "dispatch.json")
                if writer.exists(dispatch_path):
                    return plan["expires_ns"]
                return slot["planned_dispatch_ns"]
        summary_path = f"{_page_directory(epoch_index, scan_index)}/summary.json"
        if not writer.exists(summary_path):
            return None
        next_index = scan_index + 1
        if next_index >= policy["max_scan_count"]:
            return None
        return policy["first_scan_ns"] + next_index * interval_ns
    except Exception:
        return None


def _read_active_epoch_pointer_hint(writer: Any) -> dict[str, Any]:
    pointer_raw = writer.read_bytes(ACTIVE_EPOCH_PATH, max_bytes=32_000)
    pointer = _json(pointer_raw)
    if (
        type(pointer) is not dict
        or pointer.get("schema") != ACTIVE_EPOCH_SCHEMA
        or pointer.get("schema_version") != 2
        or type(pointer.get("epoch_index")) is not int
        or pointer.get("epoch_index") < 0
    ):
        _raise("discovery_active_epoch_pointer_hint_invalid")
    core = dict(pointer)
    digest = core.pop("pointer_sha256", None)
    if digest != runtime.sha256(runtime.canonical(core)):
        _raise("discovery_active_epoch_pointer_hint_hash")
    return pointer


def _renew_epochs_if_due(
    *,
    writer: Any,
    guard: Any,
    binding: Mapping[str, Any],
    release: Mapping[str, Any],
    now_ns: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Seal/renew while holding the cross-release and legacy Gecko lane locks."""
    with ExitStack() as stack:
        stack.enter_context(writer.lock(LANE_LOCK_PATH, blocking=False))
        v2_policy = v3._v2_policy(writer.read_bytes(v2.POLICY_PATH, max_bytes=4_000_000))
        stack.enter_context(writer.lock(v2_policy["service_lock_paths"]["gecko"], blocking=False))
        _check_guard(guard, writer, binding)
        return _renew_epochs_locked(
            writer=writer,
            guard=guard,
            binding=binding,
            release=release,
            now_ns=now_ns,
        )


def _renew_epochs_locked(
    *,
    writer: Any,
    guard: Any,
    binding: Mapping[str, Any],
    release: Mapping[str, Any],
    now_ns: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Seal complete 72h epochs and create unchanged-contract successors."""
    policy = dict(release["policy"])
    epoch = dict(release["epoch"])
    capacity = V3CapacityPort(writer, guard, binding)
    while now_ns >= epoch["epoch_end_ns"]:
        epoch_index = epoch["epoch_index"]
        seal_path = f"{_epoch_root(epoch_index)}/seal.json"
        if writer.exists(seal_path):
            seal = _read_json(writer, seal_path, limit=64_000)
        else:
            _recover_prior_scans(
                writer=writer,
                guard=guard,
                binding=binding,
                before_scan_index=864,
                epoch_index=epoch_index,
                policy=policy,
                epoch=epoch,
                capacity=capacity,
                now_ns=now_ns,
            )
            _close_elapsed_gaps(
                writer=writer,
                guard=guard,
                binding=binding,
                policy=policy,
                epoch=epoch,
                through_scan_index=864,
                now_ns=now_ns,
            )
            seen = _load_seen_pool_ids(
                writer,
                policy,
                epoch=epoch,
                epoch_index=epoch_index,
                before_scan_index=864,
            )
            del seen  # replay above verifies and preserves the complete cohort
            transcript = []
            attempted = 0
            for scan_index in range(864):
                plan_path = _scan_plan_path(epoch_index, scan_index)
                gap_path = f"{_epoch_root(epoch_index)}/gaps/{scan_index:04d}.json"
                if writer.exists(plan_path) == writer.exists(gap_path):
                    _raise("discovery_epoch_scan_terminal_partition")
                if writer.exists(plan_path):
                    plan = _read_json(writer, plan_path)
                    records, _raws = _load_scan_records(writer, plan)
                    if len(records) != len(plan["page_slots"]):
                        _raise("discovery_epoch_scan_incomplete")
                    summary = _read_json(
                        writer,
                        f"{_page_directory(epoch_index, scan_index)}/summary.json",
                        limit=16_000_000,
                    )
                    transcript.append(
                        {
                            "scan_index": scan_index,
                            "kind": "plan",
                            "summary_sha256": runtime.sha256(runtime.canonical(summary)),
                        }
                    )
                    attempted += sum(
                        writer.exists(
                            _page_path(epoch_index, scan_index, slot["page"], "dispatch.json")
                        )
                        for slot in plan["page_slots"]
                    )
                else:
                    gap = _read_json(writer, gap_path, limit=16_384)
                    if (
                        gap.get("scan_index") != scan_index
                        or gap.get("selected_pool_ids") is not None
                        or gap.get("exposure") != "unknown"
                        or gap.get("provider_io") is not False
                        or gap.get("cursor_advanced") is not False
                    ):
                        _raise("discovery_epoch_gap_invalid")
                    transcript.append(
                        {
                            "scan_index": scan_index,
                            "kind": "gap",
                            "gap_sha256": runtime.sha256(runtime.canonical(gap)),
                        }
                    )
            frontier_rows = _terminal_frontier_rows(writer, policy, epoch, require_complete=True)
            successor_standby = _read_json(writer, _standby_path(epoch_index + 1), limit=4_000_000)
            _verify_successor_frontier(
                successor_standby, predecessor_epoch=epoch, rows=frontier_rows
            )
            current_standby = _read_epoch_standby(writer, epoch)
            capacity.close_epoch_hold(
                standby=current_standby,
                at_ns=_next_coordinator_time(writer, guard, now_ns),
                reason="epoch_terminal_all_slots_closed",
            )
            context = _current_capacity_context(writer, guard)
            events = v3.verify_v3_ledger(
                context["ledger_bytes"],
                expected_policy_bytes=context["policy_bytes"],
                bridge=context["bridge"],
            )
            reserved = {
                event["fields"]["reservation_id"]: event["fields"]
                for event in events
                if event["kind"] in {"reserved", "page_reserved_from_hold"}
                and event["fields"].get("service") == "gecko_discovery"
            }
            terminal = {
                event["fields"]["reservation_id"]: event
                for event in events
                if event["kind"] in {"settled", "ambiguous_terminal", "released"}
            }
            pending = sum(
                fields["reserved_bytes"]
                for reservation_id, fields in reserved.items()
                if reservation_id not in terminal
                or terminal[reservation_id]["kind"] == "ambiguous_terminal"
            )
            holds: dict[str, dict[str, Any]] = {}
            for event in events:
                fields = event["fields"]
                if event["kind"] == "epoch_hold_open":
                    holds[fields["hold_id"]] = {
                        "remaining": fields["reserved_bytes"],
                        "closed": False,
                    }
                elif event["kind"] == "page_reserved_from_hold":
                    holds[fields["hold_id"]]["remaining"] -= fields["reserved_bytes"]
                elif event["kind"] == "epoch_hold_close":
                    holds[fields["hold_id"]]["closed"] = True
            pending += sum(hold["remaining"] for hold in holds.values() if not hold["closed"])
            allocated = v2.physical_inventory_bytes(writer, SERVICE_ROOT)
            lifetime_physical = allocated + pending
            if lifetime_physical > runtime.LIFETIME_PHYSICAL_CAP_BYTES:
                _raise("discovery_epoch_lifetime_cap")
            if context["free_bytes"] < 50 * 1024**3 + pending:
                _raise("discovery_epoch_next_runway_unavailable")
            seal = runtime.make_epoch_seal(
                epoch=epoch,
                scan_count=864,
                unresolved_count=0,
                terminal_replay_sha256=runtime.sha256(runtime.canonical(transcript)),
                coordinator_prefix_sha256=runtime.sha256(context["ledger_bytes"]),
                lifetime_requests_after=epoch["lifetime_requests_before"] + attempted,
                lifetime_physical_after=lifetime_physical,
                allocated_physical_after=allocated,
                pending_physical_after=pending,
                transport_quiescent=True,
                sealed_at_ns=max(now_ns, epoch["epoch_end_ns"]),
            )
            _check_guard(guard, writer, binding)
            _create_json(writer, seal_path, seal)
            _check_guard(guard, writer, binding)
        runtime._verify_epoch_seal(seal, epoch=epoch)
        frontier_rows = _terminal_frontier_rows(writer, policy, epoch, require_complete=True)
        next_index = epoch["epoch_index"] + 1
        next_standby = _read_json(writer, _standby_path(next_index), limit=4_000_000)
        standby_core = dict(next_standby)
        standby_sha = standby_core.pop("standby_sha256", None)
        if (
            standby_sha != runtime.sha256(runtime.canonical(standby_core))
            or next_standby.get("predecessor_epoch_sha256") != epoch["epoch_sha256"]
            or next_standby.get("inactive") is not True
        ):
            _raise("discovery_successor_standby_unverified")
        _verify_successor_frontier(next_standby, predecessor_epoch=epoch, rows=frontier_rows)
        next_epoch_path = _epoch_document_path(next_index)
        previous_epoch = epoch
        if writer.exists(next_epoch_path):
            epoch = _read_json(writer, next_epoch_path, limit=512_000)
            policy = _read_json(writer, _epoch_policy_path(next_index), limit=512_000)
            runtime.verify_epoch_envelope(epoch, policy=policy)
            if epoch.get("predecessor_standby_sha256") != standby_sha:
                _raise("discovery_successor_standby_epoch_mismatch")
        else:
            seed = f"{epoch['release_id']}:epoch:{next_index}"
            previous_policy = policy
            expected_next_policy = _read_json(writer, _epoch_policy_path(next_index), limit=512_000)
            epoch = runtime.successor_epoch(
                previous_epoch=epoch,
                seal=seal,
                policy=previous_policy,
                lease_id=runtime.sha256(f"{seed}:lease".encode())[:32],
                root_id=runtime.sha256(f"{seed}:root".encode())[:32],
                predecessor_standby_sha256=standby_sha,
            )
            policy = expected_next_policy
            if epoch["policy_sha256"] != policy["policy_sha256"]:
                _raise("discovery_successor_policy_mismatch")
            initialize_runtime(
                writer=writer,
                guard=guard,
                binding=binding,
                release_id=epoch["release_id"],
                release_sha256=epoch["release_sha256"],
                source_sha256=epoch["source_sha256"],
                policy=policy,
                epoch=epoch,
            )
        _publish_epoch_transition(
            writer=writer,
            guard=guard,
            binding=binding,
            previous_epoch=previous_epoch,
            previous_seal=seal,
            successor_epoch=epoch,
            standby=next_standby,
        )
        _load_epoch_history(writer, current_epoch_index=next_index)
        epoch = _read_json(writer, _epoch_document_path(next_index), limit=512_000)
        policy = _read_json(writer, _epoch_policy_path(next_index), limit=512_000)
        active = _read_active_epoch_pointer(writer, release_id=release["activation"]["release_id"])
        if active["epoch_index"] != next_index:
            _raise("discovery_epoch_pointer_switch_missing")
    return policy, epoch


def _owner_prepare_release() -> dict[str, Any]:
    try:
        with writer_guard(CONFIG_PATH, min_free_bytes=50 * 1024**3) as guard:
            root_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)
            try:
                root_stat = os.fstat(root_fd)
                binding = {
                    "root_id": guard.root_id,
                    "volume_uuid": guard.volume_uuid.lower(),
                    "device": root_stat.st_dev,
                    "inode": root_stat.st_ino,
                }

                def before_write() -> None:
                    revalidate_descriptor_write(guard)

                with DescriptorWriter(root_fd, before_write=before_write) as writer:
                    candidate = prepare_release_material(
                        writer=writer,
                        guard=guard,
                        binding=binding,
                        now_ns=time.time_ns(),
                    )
                    return {
                        "status": "release_candidate_prepared",
                        "release_id": candidate["release_id"],
                        "candidate_sha256": candidate["candidate_sha256"],
                        "activation_authorized": False,
                        "provider_io": False,
                    }
            finally:
                os.close(root_fd)
    except Exception as exc:
        raise DiscoveryHostError("discovery_release_preparation_failed") from exc


def main() -> int:
    """Fixed-path owner entry; no caller can provide paths, hashes, or endpoints."""
    import argparse

    parser = argparse.ArgumentParser(prog="gecko-new-pool-discovery-v3")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare-release", action="store_true")
    modes.add_argument("--scheduled-tick", action="store_true")
    args = parser.parse_args()
    try:
        result = _owner_prepare_release() if args.prepare_release else _owner_scheduled_tick()
    except Exception as exc:
        # stdout/stderr are launchd-suppressed; callers receive only a bounded
        # exit state and must use descriptor-bound receipts for diagnosis.
        del exc
        return 1
    if args.prepare_release:
        return 0 if result.get("status") == "release_candidate_prepared" else 1
    return (
        0 if result.get("status") in {"idle", "processed", "epoch_terminal_requires_renewal"} else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
