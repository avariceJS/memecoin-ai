#!/usr/bin/env python3
"""Path-only controller for a reviewed finite external Senses v2 lease.

The controller can inspect and run one due bucket. It cannot install, load, or
start a LaunchAgent, and its command line contains paths only.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import plistlib
import re
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from memecoin_ai.sources import PersistentHTTPTransport, ReadOnlyRPC  # noqa: E402
from memecoin_research_storage import StorageError, writer_guard  # noqa: E402
from tools import brain_senses_external_clock_v1 as clock  # noqa: E402
from tools import brain_senses_external_orchestrator_v2 as engine  # noqa: E402
from tools import brain_senses_external_verify_v2 as verify  # noqa: E402
from tools.brain_senses_external_verify_v1 import (  # noqa: E402
    DescriptorReader as V1DescriptorReader,  # noqa: E402
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402
from tools.brain_senses_service_v1_runner import _load_private_endpoint  # noqa: E402

_SERVICE_PATH = re.compile(r"services/[A-Za-z0-9_-]{1,80}\Z")
_ACTIVATION_FIELDS = {
    "schema",
    "schema_version",
    "plan_path",
    "plan_sha256",
    "storage_config_sha256",
    "review_receipt_path",
    "review_receipt_sha256",
    "plist_path",
    "plist_sha256",
    "label",
}
_REVIEW_FIELDS = {
    "schema",
    "schema_version",
    "decision",
    "plan_sha256",
    "source_sha256",
    "predecessor_certificate_sha256",
    "candidate_sha256",
    "validation_sha256",
    "completion_sha256",
    "expires_ns",
    "qualification_manifest_path",
    "qualification_manifest_sha256",
}
_QUALIFICATION_FIELDS = {
    "schema",
    "schema_version",
    "status",
    "bindings",
    "roles",
}
_V1_WRITER_RECEIPTS = {
    "storage_detach_v1": (
        "docs/evidence/senses-storage-detach-qualification-2026-10-12-v5.json",
        "research_storage_detach_qualify_v1",
    ),
    "reservation_recovery_v1": (
        "docs/evidence/senses-external-recovery-qualification-2026-10-12-v5.json",
        "research_storage_reservation_remount_no_replay_v1",
    ),
    "kingston_writer_v1": (
        "docs/evidence/senses-kingston-writer-qualification-2026-10-12-v4.json",
        "research_kingston_writer_qualify_v1",
    ),
}
V2_DETACH_RECEIPT_PATH = (
    "docs/evidence/external-v2-descriptor-detach-integration-qualification-2026-10-12-v2.json"
)
V2_DETACH_RECEIPT_ROOT = REPO
V2_DETACH_HARNESS_PATH = "tools/brain_senses_external_detach_qualify_v2.py"
_ROLE_SCHEMAS = {
    "clock": "brain-senses-external-v2-clock-qualification",
    "launch": "brain-senses-external-v2-launch-qualification",
    "sol_decision": "brain-senses-external-v2-sol-decision",
}
RETIRED_SENSES_LABELS = (
    "ai.memecoin.brain.senses.block-epochs",
    "ai.memecoin.brain.senses.decode-watch",
    "ai.memecoin.brain.senses.helius-decode-watch",
    "ai.memecoin.brain.senses.helius-inventory-controller-v1",
    "ai.memecoin.brain.senses.helius-inventory-v3-20261010-1",
    "ai.memecoin.brain.senses.helius-inventory-v3-20261010-2",
    "ai.memecoin.brain.senses.helius-inventory-v3-20261010-3",
    "ai.memecoin.brain.senses.helius-inventory-v3-20261010-4",
    "ai.memecoin.brain.senses.helius-watch-v2",
    "ai.memecoin.brain.senses.helius-watch-v3",
    "ai.memecoin.brain.senses.helius-watch",
    "ai.memecoin.brain.senses.independent-verifier-512-pair64",
    "ai.memecoin.brain.senses.independent-verifier-512",
    "ai.memecoin.brain.senses.program-watch-v3",
    "ai.memecoin.brain.senses.program-watch",
    "ai.memecoin.brain.senses.external-v1-20261011T2133Z",
)


def _require(value: bool, reason: str) -> None:
    if not value:
        raise StorageError(reason)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_safe(value: Any) -> Any:
    """Convert verifier state containers to deterministic JSON-compatible values."""
    if isinstance(value, Mapping):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(child) for child in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_json_safe(child) for child in value]
        return sorted(
            normalized,
            key=lambda child: json.dumps(child, sort_keys=True, separators=(",", ":")),
        )
    return value


def _open_absolute(path: Path) -> int:
    _require(path.is_absolute() and ".." not in path.parts, "unsafe_absolute_directory")
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read(path: Path, *, private: bool = False, max_bytes: int = 4_000_000) -> bytes:
    parent = _open_absolute(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        before = os.fstat(fd)
        _require(
            stat.S_ISREG(before.st_mode)
            and before.st_uid == os.getuid()
            and before.st_nlink == 1
            and before.st_size <= max_bytes,
            "unsafe_binding_file",
        )
        if private:
            _require(stat.S_IMODE(before.st_mode) in (0o400, 0o600), "binding_not_private")
        out = bytearray()
        while chunk := os.read(fd, 65536):
            out.extend(chunk)
            _require(len(out) <= max_bytes, "binding_file_grew")
        after = os.fstat(fd)
        _require(
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
            "binding_changed",
        )
        return bytes(out)
    finally:
        os.close(fd)


@contextlib.contextmanager
def host_lock() -> Iterator[None]:
    """Take the same host writer lock used by the earlier Senses writer."""
    parent = _open_absolute(REPO / "work")
    try:
        fd = os.open(
            ".brain-senses-host-writer-v1.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=parent,
        )
    finally:
        os.close(parent)
    try:
        entry = os.fstat(fd)
        _require(
            stat.S_ISREG(entry.st_mode)
            and entry.st_uid == os.getuid()
            and entry.st_nlink == 1
            and stat.S_IMODE(entry.st_mode) == 0o600,
            "host_lock_identity",
        )
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def open_service(guard: Any, relative: str) -> int:
    """Open an existing service directory beneath the held mount descriptor."""
    _require(bool(_SERVICE_PATH.fullmatch(relative)), "invalid_service_path")
    guard.revalidate()
    fd = os.dup(guard.mount_fd)
    try:
        pieces = [*guard.config.data_root.split("/"), *relative.split("/")]
        mount_stat = os.fstat(guard.mount_fd)
        for part in pieces:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                entry = os.fstat(child)
                _require(
                    entry.st_dev == mount_stat.st_dev
                    and entry.st_uid == os.getuid()
                    and stat.S_IMODE(entry.st_mode) == 0o700,
                    "service_directory_identity",
                )
            except BaseException:
                os.close(child)
                raise
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextlib.contextmanager
def predecessor_read_lock(service_fd: int) -> Iterator[int]:
    """Hold a shared lock on the existing v1 writer-lock inode without mutation."""
    try:
        fd = os.open(
            ".writer.lock",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=service_fd,
        )
    except FileNotFoundError as exc:
        raise StorageError("predecessor_writer_lock_missing") from exc
    try:
        entry = os.fstat(fd)
        _require(
            stat.S_ISREG(entry.st_mode)
            and entry.st_dev == os.fstat(service_fd).st_dev
            and entry.st_uid == os.getuid()
            and entry.st_nlink == 1
            and stat.S_IMODE(entry.st_mode) == 0o600,
            "predecessor_writer_lock_identity",
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StorageError("predecessor_writer_active") from exc
        yield fd
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=15, check=False)


def runtime_gate(label: str, plist: Mapping[str, Any], *, running: bool) -> None:
    """Reject manual execution, parallel Senses labels, and argv/plist drift."""
    domain = f"gui/{os.getuid()}"
    listing = _command(["/bin/launchctl", "list"])
    disabled = _command(["/bin/launchctl", "print-disabled", domain])
    _require(listing.returncode == disabled.returncode == 0, "launchd_inventory_unavailable")
    labels = {line.split()[-1] for line in listing.stdout.splitlines()[1:] if line.split()}
    for retired in RETIRED_SENSES_LABELS:
        _require(retired not in labels, "retired_agent_loaded")
        escaped = re.escape(retired)
        _require(
            re.search(r'"' + escaped + r'"\s*=>\s*(?:true|disabled)\b', disabled.stdout)
            is not None,
            "retired_agent_not_disabled",
        )
    _require(
        not any(item.startswith("ai.memecoin.brain.senses.") and item != label for item in labels),
        "another_senses_agent_loaded",
    )
    if running:
        info = _command(["/bin/launchctl", "print", f"{domain}/{label}"])
        _require(
            info.returncode == 0
            and re.search(r"\bpid = " + str(os.getpid()) + r"\b", info.stdout) is not None
            and os.getppid() == 1,
            "not_reviewed_launchd_process",
        )
        _require(
            plist.get("ProgramArguments")
            == [
                str(REPO / ".venv/bin/python"),
                str(Path(__file__).resolve()),
                *sys.argv[1:],
            ],
            "launchd_argv_mismatch",
        )


def _calendar(plan: Mapping[str, Any]) -> list[dict[str, int]]:
    windows = [plan["schedule"]["bootstrap"], *plan["schedule"]["frames"]]
    result = []
    for window in windows:
        start = window["start_ns"]
        _require(start % 60_000_000_000 == 0, "launch_bucket_not_minute_aligned")
        local = datetime.fromtimestamp(start // 1_000_000_000, UTC).astimezone()
        result.append(
            {"Month": local.month, "Day": local.day, "Hour": local.hour, "Minute": local.minute}
        )
    return result


def _artifact_path(path: str) -> Path:
    _require(isinstance(path, str) and bool(path) and "\x00" not in path, "qualification_path")
    candidate = Path(path)
    if not candidate.is_absolute():
        _require(".." not in candidate.parts, "qualification_path")
        candidate = REPO / candidate
    return candidate


def _check_artifact_ref(
    ref: Any, *, expected_schema: str, expected_status: str
) -> tuple[dict[str, Any], bytes]:
    _require(
        isinstance(ref, dict)
        and set(ref) == {"path", "sha256", "schema", "status"}
        and ref.get("schema") == expected_schema
        and ref.get("status") == expected_status
        and verify._digest(ref.get("sha256")),
        "qualification_ref_shape",
    )
    raw = _read(_artifact_path(ref["path"]))
    _require(_sha(raw) == ref["sha256"], "qualification_receipt_hash")
    body = verify.decode(raw)
    _require(
        isinstance(body, dict)
        and body.get("schema") == expected_schema
        and body.get("status") == expected_status,
        "qualification_receipt_schema_status",
    )
    return body, raw


def _verify_qualification_manifest(manifest: Any, plan: Mapping[str, Any]) -> None:
    _require(
        isinstance(manifest, dict)
        and set(manifest) == _QUALIFICATION_FIELDS
        and manifest.get("schema") == "brain-senses-external-v2-qualification-manifest"
        and manifest.get("schema_version") == 2
        and manifest.get("status") == "passed",
        "qualification_manifest_shape",
    )
    predecessor = plan["predecessor"]
    bindings = manifest.get("bindings")
    expected_bindings = {
        "plan_sha256": _sha(verify.canonical(plan)),
        "source_sha256": plan["code_sha256"],
        "predecessor_certificate_sha256": predecessor["certificate_sha256"],
        "candidate_sha256": predecessor["candidate_sha256"],
        "validation_sha256": predecessor["validation_sha256"],
        "completion_sha256": predecessor["completion_sha256"],
        "expires_ns": plan["schedule"]["expires_ns"],
    }
    _require(bindings == expected_bindings, "qualification_manifest_scope")
    roles = manifest.get("roles")
    _require(
        isinstance(roles, dict) and set(roles) == {"writer", "clock", "launch", "sol_decision"},
        "qualification_role_set",
    )
    writer = roles["writer"]
    _require(
        isinstance(writer, dict)
        and set(writer)
        == {"schema", "schema_version", "status", "scope", "mount_identity", "receipts"}
        and writer.get("schema") == "brain-senses-external-v2-writer-qualification-set"
        and writer.get("schema_version") == 3
        and writer.get("status") == "passed"
        and writer.get("scope")
        == (
            "v1 writer mechanics and Kingston scratch are reused; "
            "v2 semantic runtime is separately qualified"
        ),
        "writer_qualification_scope",
    )
    mount_identity = writer.get("mount_identity")
    _require(
        mount_identity
        == {
            "root_id": plan["storage"]["root_id"],
            "volume_uuid": plan["storage"]["volume_uuid"],
        },
        "writer_qualification_mount_identity",
    )
    writer_receipts = writer.get("receipts")
    _require(
        isinstance(writer_receipts, dict)
        and set(writer_receipts) == {*_V1_WRITER_RECEIPTS, "descriptor_detach_v2"},
        "writer_receipt_set",
    )
    for name, (expected_path, expected_contract) in _V1_WRITER_RECEIPTS.items():
        ref = writer_receipts[name]
        _require(
            isinstance(ref, dict)
            and set(ref) == {"path", "sha256", "schema", "status"}
            and ref.get("path") == expected_path
            and ref.get("schema") == expected_contract
            and ref.get("status") == "passed"
            and verify._digest(ref.get("sha256")),
            "v1_writer_receipt_ref",
        )
        raw = _read(REPO / expected_path)
        _require(_sha(raw) == ref["sha256"], "v1_writer_receipt_hash")
        body = verify.decode(raw)
        _require(
            body.get("contract") == expected_contract and body.get("status") == "passed",
            "v1_writer_receipt_schema_status",
        )
        if name == "kingston_writer_v1":
            observed = body.get("observed", {}).get("root_binding_after", {})
            _require(
                observed.get("root_id") == mount_identity["root_id"]
                and observed.get("volume_uuid") == mount_identity["volume_uuid"],
                "kingston_writer_mount_pin",
            )
    v2_ref = writer_receipts["descriptor_detach_v2"]
    _require(
        isinstance(v2_ref, dict)
        and set(v2_ref) == {"path", "sha256", "schema", "status"}
        and v2_ref.get("path") == V2_DETACH_RECEIPT_PATH
        and v2_ref.get("schema") == "external-v2-descriptor-detach-integration-qualification"
        and v2_ref.get("status") == "passed"
        and verify._digest(v2_ref.get("sha256")),
        "v2_detach_receipt_ref",
    )
    v2_raw = _read(V2_DETACH_RECEIPT_ROOT / V2_DETACH_RECEIPT_PATH)
    _require(_sha(v2_raw) == v2_ref["sha256"], "v2_detach_receipt_hash")
    v2_receipt = verify.decode(v2_raw)
    tested_identity = v2_receipt.get("tested_disposable_identity")
    requested_capacity = (
        plan["caps"]["disk_floor_bytes"]
        + plan["caps"]["stored_bytes_total"]
        + plan["caps"]["peak_write_margin_bytes"]
        + plan["caps"]["metadata_margin_bytes"]
    )
    _require(
        isinstance(tested_identity, dict)
        and set(tested_identity)
        == {
            "volume_uuid",
            "mount_path",
            "fs_type",
            "case_sensitive",
            "owners_enabled",
            "root_id",
            "relative_root",
        }
        and isinstance(tested_identity.get("volume_uuid"), str)
        and tested_identity["volume_uuid"].lower() != mount_identity["volume_uuid"].lower()
        and tested_identity["volume_uuid"].lower() != "8f8f1081-9be4-4914-9111-75f1b90255bf"
        and isinstance(tested_identity.get("mount_path"), str)
        and tested_identity["mount_path"].startswith("/private/tmp/")
        and tested_identity["mount_path"] != "/Volumes/Kingston"
        and tested_identity.get("fs_type") == "case-sensitive APFS"
        and tested_identity.get("case_sensitive") is True
        and type(tested_identity.get("owners_enabled")) is bool
        and isinstance(tested_identity.get("root_id"), str)
        and bool(tested_identity["root_id"])
        and tested_identity.get("relative_root") == "services/v2-detach-fixture"
        and v2_receipt.get("test_only_ownership_normalization") is True
        and v2_receipt.get("tested_disposable_source") == V2_DETACH_HARNESS_PATH
        and v2_receipt.get("tested_disposable_source_sha256")
        == _sha(_read(REPO / V2_DETACH_HARNESS_PATH))
        and v2_receipt.get("capacity_override_test_only") is True
        and v2_receipt.get("capacity_qualified") is False
        and v2_receipt.get("requested_live_min_free_bytes") == requested_capacity
        and v2_receipt.get("applied_min_free_bytes") == 0
        and type(v2_receipt.get("capacity_override_call_count")) is int
        and v2_receipt["capacity_override_call_count"] > 0
        and type(v2_receipt.get("actual_disposable_free_bytes")) is int
        and v2_receipt["actual_disposable_free_bytes"] < requested_capacity
        and v2_receipt.get("real_capacity_precheck_rejected") is True,
        "v2_detach_disposable_identity_and_capacity",
    )
    _require(
        v2_receipt.get("schema") == v2_ref["schema"]
        and v2_receipt.get("schema_version") == 2
        and v2_receipt.get("status") == "passed"
        and v2_receipt.get("provider_requests") == 0
        and v2_receipt.get("source_sha256") == plan["code_sha256"]
        and v2_receipt.get("writer_source_sha256") == plan["code_sha256"]
        and v2_receipt.get("mount_identity") == mount_identity
        and v2_receipt.get("descriptor_path") == plan["storage"]["relative_root"]
        and v2_receipt.get("storage") == plan["storage"]
        and v2_receipt.get("configured_kingston_touched") is False
        and v2_receipt.get("forced_detach_points")
        and all(
            item.get("underlay_unchanged") is True and item.get("no_retry_or_replay") is True
            for item in v2_receipt["forced_detach_points"]
        )
        and v2_receipt.get("fallback_artifacts") == []
        and v2_receipt.get("underlay_manifest_unchanged") is True
        and v2_receipt.get("cleanup_verified") is True
        and v2_receipt.get("temporary_root_removed") is True,
        "v2_detach_receipt_evidence",
    )
    _require(
        all(
            item.get("safe_syscall_failure") is True
            and item.get("provider_loader_calls") == 0
            and item.get("fallback_artifacts") == []
            and item.get("tested_disposable_volume_uuid") == tested_identity["volume_uuid"]
            and item.get("tested_disposable_mount_path") == tested_identity["mount_path"]
            for item in v2_receipt["forced_detach_points"]
        )
        and verify._digest(v2_receipt.get("underlay_manifest_sha256")),
        "v2_detach_point_bindings",
    )
    detach_names = {item.get("name") for item in v2_receipt["forced_detach_points"]}
    reservation_points = [
        item for item in v2_receipt["forced_detach_points"] if item.get("name") == "atomic_rename_2"
    ]
    _require(
        "storage_guard_mkdir_after_revalidation" in detach_names
        and len(reservation_points) == 1
        and reservation_points[0].get("replay_classification") == "reservation_spent"
        and reservation_points[0].get("no_retry_or_replay") is True,
        "v2_detach_required_scenarios",
    )
    role_bodies: dict[str, dict[str, Any]] = {}
    role_refs: dict[str, dict[str, Any]] = {}
    for role, status in (("clock", "passed"), ("launch", "passed"), ("sol_decision", "approved")):
        ref = roles[role]
        body, _ = _check_artifact_ref(
            ref, expected_schema=_ROLE_SCHEMAS[role], expected_status=status
        )
        role_bodies[role] = body
        role_refs[role] = ref
        _require(
            body.get("plan_sha256") == expected_bindings["plan_sha256"]
            and body.get("source_sha256") == plan["code_sha256"]
            and body.get("predecessor_certificate_sha256") == predecessor["certificate_sha256"],
            "qualification_role_scope",
        )
    sol = role_bodies["sol_decision"]
    _require(
        sol.get("candidate_sha256") == predecessor["candidate_sha256"]
        and sol.get("validation_sha256") == predecessor["validation_sha256"]
        and sol.get("completion_sha256") == predecessor["completion_sha256"]
        and sol.get("expires_ns") == plan["schedule"]["expires_ns"]
        and sol.get("qualification_refs")
        == {
            "writer_v1_and_v2": engine.sha(verify.canonical(writer)),
            "clock": {"path": role_refs["clock"]["path"], "sha256": role_refs["clock"]["sha256"]},
            "launch": {
                "path": role_refs["launch"]["path"],
                "sha256": role_refs["launch"]["sha256"],
            },
        },
        "sol_decision_cross_binding",
    )


def activation_gate(
    activation_path: Path, config_path: Path, *, running: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_activation = _read(activation_path, private=True)
    activation = verify.decode(raw_activation)
    _require(
        isinstance(activation, dict)
        and set(activation) == _ACTIVATION_FIELDS
        and activation.get("schema") == "brain-senses-external-launch-v2"
        and activation.get("schema_version") == 2,
        "activation_schema",
    )
    plan_path = Path(activation["plan_path"])
    plan_bytes = _read(plan_path, private=True)
    _require(_sha(plan_bytes) == activation["plan_sha256"], "activation_plan_hash")
    plan = verify.decode(plan_bytes)
    verify.verify_plan(plan, expected_sources=engine.source_bindings())
    _require(
        _sha(_read(config_path, private=True)) == activation["storage_config_sha256"],
        "activation_storage_config",
    )
    review_bytes = _read(Path(activation["review_receipt_path"]))
    _require(_sha(review_bytes) == activation["review_receipt_sha256"], "review_receipt_hash")
    review = verify.decode(review_bytes)
    predecessor = plan["predecessor"]
    _require(
        isinstance(review, dict)
        and set(review) == _REVIEW_FIELDS
        and review.get("schema") == "brain-senses-external-v2-review"
        and review.get("schema_version") == 2
        and review.get("decision") == "approved_for_finite_activation"
        and review.get("plan_sha256") == activation["plan_sha256"]
        and review.get("source_sha256") == plan["code_sha256"]
        and review.get("predecessor_certificate_sha256") == predecessor["certificate_sha256"]
        and review.get("candidate_sha256") == predecessor["candidate_sha256"]
        and review.get("validation_sha256") == predecessor["validation_sha256"]
        and review.get("completion_sha256") == predecessor["completion_sha256"]
        and review.get("expires_ns") == plan["schedule"]["expires_ns"]
        and isinstance(review.get("qualification_manifest_path"), str)
        and verify._digest(review.get("qualification_manifest_sha256")),
        "review_scope",
    )
    qualification_raw = _read(_artifact_path(review["qualification_manifest_path"]))
    _require(
        _sha(qualification_raw) == review["qualification_manifest_sha256"],
        "qualification_manifest_hash",
    )
    _verify_qualification_manifest(verify.decode(qualification_raw), plan)
    plist_bytes = _read(Path(activation["plist_path"]), private=True)
    _require(_sha(plist_bytes) == activation["plist_sha256"], "activation_plist_hash")
    plist = plistlib.loads(plist_bytes)
    label = activation["label"]
    _require(
        label == f"ai.memecoin.brain.senses.{plan['service_id']}" and plist.get("Label") == label,
        "launchd_label",
    )
    _require(
        plist.get("StandardOutPath") == "/dev/null"
        and plist.get("StandardErrorPath") == "/dev/null"
        and not plist.get("EnvironmentVariables")
        and not plist.get("RunAtLoad")
        and not plist.get("KeepAlive"),
        "unsafe_launchd_configuration",
    )
    expected_argv = [
        str(REPO / ".venv/bin/python"),
        str(Path(__file__).resolve()),
        "run",
        "--activation",
        str(activation_path),
        "--storage-config",
        str(config_path),
    ]
    _require(
        plist.get("ProgramArguments") == expected_argv
        and plist.get("WorkingDirectory") == str(REPO)
        and plist.get("StartCalendarInterval") == _calendar(plan),
        "plist_entrypoint",
    )
    runtime_gate(label, plist, running=running)
    return activation, plan


def due_bucket(plan: Mapping[str, Any], now_ns: int) -> str | None:
    """Return at most one currently open window; never catch up or backfill."""
    schedule = plan["schedule"]
    windows = [schedule["bootstrap"], *schedule["frames"]]
    due = [
        row["bucket"]
        for row in windows
        if row["start_ns"] + 5_000_000_000 <= now_ns <= row["end_ns"] - 5_000_000_000
    ]
    if len(due) > 1:
        raise StorageError("overlapping_external_v2_windows")
    return due[0] if due else None


def scheduled_bucket(plan: Mapping[str, Any], now_ns: int) -> str | None:
    """Find the exact calendar window before its five-second dispatch margin."""
    windows = [plan["schedule"]["bootstrap"], *plan["schedule"]["frames"]]
    matches = [row["bucket"] for row in windows if row["start_ns"] <= now_ns < row["end_ns"]]
    if len(matches) > 1:
        raise StorageError("overlapping_external_v2_windows")
    return matches[0] if matches else None


def completion_view(
    plan: Mapping[str, Any], state: Mapping[str, Any], now_ns: int
) -> dict[str, Any]:
    """Report finite-horizon completion without mutating the journal."""
    windows = [plan["schedule"]["bootstrap"], *plan["schedule"]["frames"]]
    sealed = state.get("sealed", {})
    missed = state.get("missed", set())
    reservations = state.get("reservations", {})
    expired_unobserved = [
        row["bucket"]
        for row in windows
        if now_ns >= row["end_ns"]
        and row["bucket"] not in reservations
        and row["bucket"] not in missed
        and not sealed.get(row["bucket"], False)
    ]
    return {
        "scheduled_windows": len(windows),
        "sealed_windows": sum(bool(sealed.get(row["bucket"], False)) for row in windows),
        "missed_windows": sum(row["bucket"] in missed for row in windows),
        "expired_unobserved": expired_unobserved,
        "all_windows_clean_complete": all(sealed.get(row["bucket"], False) for row in windows),
        "successor_eligible": False,
    }


def _client(
    plan: Mapping[str, Any], writer: DescriptorWriter, guard: Any, bucket: str
) -> tuple[ReadOnlyRPC, Callable[[], None]]:
    endpoint = _load_private_endpoint()
    _require(endpoint is not None, "private_endpoint_unavailable")
    assert endpoint is not None
    _require("SSLKEYLOGFILE" not in os.environ, "tls_key_logging_forbidden")
    expected_endpoint = plan["endpoint_origin"]
    from urllib.parse import urlsplit

    parsed = urlsplit(endpoint)
    actual_origin = f"{parsed.scheme}://{parsed.netloc}"
    _require(actual_origin == expected_endpoint, "provider_origin_mismatch")
    windows = [plan["schedule"]["bootstrap"], *plan["schedule"]["frames"]]
    window = next(row for row in windows if row["bucket"] == bucket)
    backend = PersistentHTTPTransport()
    last_start = 0.0

    def transport(url: str, body: bytes, timeout: float, max_bytes: int) -> bytes:
        nonlocal last_start
        _require(url == endpoint, "transport_endpoint_changed")
        delay = 0.25 - (time.monotonic() - last_start)
        if delay > 0:
            time.sleep(delay)
        guard.revalidate(
            min_free_bytes=(
                plan["caps"]["disk_floor_bytes"]
                + plan["caps"]["stored_bytes_total"]
                + plan["caps"]["peak_write_margin_bytes"]
                + plan["caps"]["metadata_margin_bytes"]
            )
        )
        request_prefix = f"buckets/{bucket}/requests/"
        requests = sorted(
            path
            for path in writer.list_files()
            if path.startswith(request_prefix) and path.endswith(".json")
        )
        _require(bool(requests), "transport_without_intent")
        intent = verify.decode(writer.read_bytes(requests[-1]))
        _require(intent["wire_request_sha256"] == _sha(body), "transport_intent_mismatch")
        clock_bytes = writer.read_bytes(intent["clock_check_path"])
        _require(_sha(clock_bytes) == intent["clock_check_sha256"], "transport_clock_hash")
        now, monotonic = time.time_ns(), time.monotonic_ns()
        clock.validate_progress(verify.decode(clock_bytes), wall_ns=now, monotonic_ns=monotonic)
        _require(
            window["start_ns"] + 5_000_000_000
            <= now
            <= min(window["end_ns"] - 5_000_000_000, plan["schedule"]["expires_ns"]),
            "transport_outside_window",
        )
        last_start = time.monotonic()
        remaining = max(0.1, (window["end_ns"] - now) / 1_000_000_000)
        return backend(url, body, min(timeout, remaining), max_bytes)

    client = ReadOnlyRPC(
        endpoint,
        plan["provider"],
        timeout=20,
        max_bytes=plan["caps"]["max_response_bytes"],
        transport=transport,
    )
    return client, backend.close


def execute(
    command: str,
    *,
    activation_path: Path,
    config_path: Path,
    now_ns: int | None = None,
    guard_factory: Callable[..., Any] = writer_guard,
) -> dict[str, Any]:
    """Verify and run at most one scheduled bucket under all ordered locks."""
    _require(command in {"run", "status"}, "command_invalid")
    activation, plan = activation_gate(activation_path, config_path, running=command == "run")
    caps = plan["caps"]
    required = (
        caps["disk_floor_bytes"]
        + caps["stored_bytes_total"]
        + caps["peak_write_margin_bytes"]
        + caps["metadata_margin_bytes"]
    )
    with host_lock(), guard_factory(config_path, min_free_bytes=required) as guard:
        current_activation, current_plan = activation_gate(
            activation_path, config_path, running=command == "run"
        )
        _require(
            current_activation == activation and current_plan == plan,
            "activation_changed_under_lock",
        )
        _require(
            plan["storage"]["root_id"] == guard.root_id
            and plan["storage"]["volume_uuid"].lower() == guard.volume_uuid.lower(),
            "plan_storage_identity",
        )
        v1_fd = open_service(guard, plan["predecessor"]["relative_root"])
        v2_fd = -1
        try:
            with predecessor_read_lock(v1_fd):
                with V1DescriptorReader(v1_fd) as predecessor_reader:
                    v2_fd = open_service(guard, plan["storage"]["relative_root"])
                    try:

                        def before_write() -> None:
                            guard.revalidate(min_free_bytes=required)

                        with DescriptorWriter(v2_fd, before_write=before_write) as writer:
                            with verify.DescriptorReaderV2(v2_fd) as reader:
                                state = verify.verify(
                                    reader,
                                    expected_plan_sha256=activation["plan_sha256"],
                                    expected_sources=engine.source_bindings(),
                                    predecessor_reader=predecessor_reader,
                                )
                            if command == "status" or state["stop"]:
                                current = time.time_ns() if now_ns is None else now_ns
                                state["completion"] = completion_view(plan, state, current)
                                return state
                            if not state["predecessor_lineage_verified"]:
                                raise StorageError("predecessor_lineage_unverified")
                            current = time.time_ns() if now_ns is None else now_ns
                            if current >= plan["schedule"]["expires_ns"]:
                                state["completion"] = completion_view(plan, state, current)
                                return state
                            bucket = scheduled_bucket(plan, current)
                            bootstrap = plan["schedule"]["bootstrap"]
                            if bucket is None:
                                # A missed mandatory bootstrap is terminal even when
                                # launchd starts the process outside its exact window.
                                if (
                                    current > bootstrap["end_ns"] - 5_000_000_000
                                    and bootstrap["bucket"] not in state["reservations"]
                                    and bootstrap["bucket"] not in state["sealed"]
                                ):
                                    return engine.run_bootstrap(
                                        writer,
                                        client_loader=lambda: _raise_no_client(),
                                        guard=guard,
                                        predecessor_reader=predecessor_reader,
                                        now_ns=current,
                                    )
                                return {
                                    "status": "not_due",
                                    "provider_io": False,
                                    "replay_authorized": False,
                                }
                            windows = [bootstrap, *plan["schedule"]["frames"]]
                            selected = next(row for row in windows if row["bucket"] == bucket)
                            delay = selected["start_ns"] + 10_000_000_000 - time.time_ns()
                            if now_ns is None and 0 < delay <= 15_000_000_000:
                                time.sleep(delay / 1_000_000_000)
                                if now_ns is None:
                                    dispatch_now = time.time_ns()
                                    if due_bucket(plan, dispatch_now) != bucket:
                                        if (
                                            bucket == bootstrap["bucket"]
                                            and dispatch_now > bootstrap["end_ns"] - 5_000_000_000
                                        ):
                                            return engine.run_bootstrap(
                                                writer,
                                                client_loader=lambda: _raise_no_client(),
                                                guard=guard,
                                                predecessor_reader=predecessor_reader,
                                                now_ns=dispatch_now,
                                            )
                                        return {
                                            "status": "not_due",
                                            "provider_io": False,
                                            "replay_authorized": False,
                                        }
                            closers: list[Callable[[], None]] = []

                            def loader() -> ReadOnlyRPC:
                                client, close = _client(plan, writer, guard, bucket)
                                closers.append(close)
                                return client

                            try:
                                if bucket == bootstrap["bucket"]:
                                    return engine.run_bootstrap(
                                        writer,
                                        client_loader=loader,
                                        guard=guard,
                                        predecessor_reader=predecessor_reader,
                                    )
                                frame = next(
                                    row
                                    for row in plan["schedule"]["frames"]
                                    if row["bucket"] == bucket
                                )
                                return engine.run_frame(
                                    writer,
                                    frame["index"],
                                    client_loader=loader,
                                    guard=guard,
                                    predecessor_reader=predecessor_reader,
                                )
                            finally:
                                for close in closers:
                                    close()
                    finally:
                        os.close(v2_fd)
                        v2_fd = -1
        finally:
            os.close(v1_fd)
            if v2_fd >= 0:
                os.close(v2_fd)


def _raise_no_client() -> Any:
    raise StorageError("provider_dispatch_forbidden")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "status"))
    parser.add_argument("--activation", type=Path, required=True)
    parser.add_argument("--storage-config", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = execute(
            args.command,
            activation_path=args.activation,
            config_path=args.storage_config,
        )
    except Exception as exc:
        # Exception strings can contain request URLs; only the class is durable/output.
        print(json.dumps({"status": "failed", "error_class": type(exc).__name__}))
        return 1
    print(json.dumps(_json_safe(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
