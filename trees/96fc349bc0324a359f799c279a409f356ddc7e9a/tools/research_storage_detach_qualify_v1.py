#!/usr/bin/env python3
"""Exercise descriptor-bound writer operations across forced APFS image detach.

This host qualification creates a disposable APFSX sparse image beneath
``/private/tmp``, mounts it over a writable internal trap tree, and injects a
forced detach after StorageGuard revalidation but immediately before selected
dirfd syscalls. It never uses the configured Kingston volume or provider APIs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memecoin_research_storage import (
    MacOSVolumeProbe,
    StorageError,
    StorageGuard,
    VolumeInfo,
    configure,
    create_root,
    writer_guard,
)
from tools import brain_senses_external_orchestrator_v1 as orchestrator
from tools import brain_senses_external_schema_v1 as external_schema
from tools.brain_senses_external_writer_v1 import (
    DescriptorWriter,
    canonical_json,
    semantic_request_sha256,
    wire_request_sha256,
    write_capture,
)

KINGSTON_UUID = "8f8f1081-9be4-4914-9111-75f1b90255bf"
DATA_ROOT = "ResearchData"
SERVICE_ROOT = "services/svc"
TRAP_FILES = {
    "append-open.jsonl": b"trap append-open\n",
    "append-write.jsonl": b"trap append-write\n",
    "atomic-rename.json": b"trap atomic rename\n",
    "unlink.json": b"trap unlink\n",
    ".writer.lock": b"trap lock\n",
    "captures/response/response.json": b"trap response\n",
    "captures/response/receipt.json": b"trap response receipt\n",
    "captures/receipt/response.json": b"trap receipt response\n",
    "captures/receipt/receipt.json": b"trap receipt\n",
}


class QualificationError(RuntimeError):
    pass


@dataclass
class MountedImage:
    root: Path
    mountpoint: Path
    image: Path
    config: Path
    device: str | None = None
    volume_uuid: str | None = None
    probe: ImageVolumeProbe | None = None
    owners_enabled: bool | None = None

    def attach(self) -> None:
        if os.path.ismount(self.mountpoint):
            raise QualificationError("mountpoint_already_mounted")
        _run(
            [
                "/usr/bin/hdiutil",
                "attach",
                "-nobrowse",
                "-noautoopen",
                "-mountpoint",
                str(self.mountpoint),
                str(self.image),
            ],
            timeout=30,
        )
        if not os.path.ismount(self.mountpoint):
            raise QualificationError("image_attach_missing_mount")
        info = _disk_info(self.mountpoint)
        self.device = str(info.get("DeviceIdentifier", ""))
        self.volume_uuid = str(info.get("VolumeUUID", "")).lower()
        self.owners_enabled = info.get("GlobalPermissionsEnabled") is True
        if not self.device.startswith("disk") or not self.volume_uuid:
            raise QualificationError("image_attach_identity_missing")
        if self.volume_uuid == KINGSTON_UUID:
            raise QualificationError("refusing_configured_volume")
        if str(info.get("FilesystemName", "")).lower().strip() != "case-sensitive apfs":
            raise QualificationError("image_not_case_sensitive_apfs")
        self.probe = ImageVolumeProbe(self.volume_uuid)

    def detach(self) -> str:
        if not os.path.ismount(self.mountpoint):
            return "already_unmounted"
        if not self.device:
            self.device = str(_disk_info(self.mountpoint).get("DeviceIdentifier", ""))
        if not self.device.startswith("disk"):
            raise QualificationError("image_detach_identity_missing")
        result = _run(
            ["/usr/bin/hdiutil", "detach", "-force", f"/dev/{self.device}"],
            timeout=30,
        )
        if os.path.ismount(self.mountpoint):
            raise QualificationError("forced_detach_left_mount_present")
        self.device = None
        return result.stdout.strip()


class ImageVolumeProbe:
    """Use live diskutil fields but normalize APFS-image noowners for this test only.

    diskutil enableOwnership requires administrator access. The disposable image
    is otherwise a real writable case-sensitive APFS mount. The guard still
    checks its actual UUID, mountpoint, filesystem type, readonly state and
    free-space values; only the image's noowners mount option is normalized.
    """

    def __init__(self, expected_uuid: str) -> None:
        self.expected_uuid = expected_uuid
        self.raw = MacOSVolumeProbe()

    def inspect(self, mount_path: Path) -> VolumeInfo:
        info = self.raw.inspect(mount_path)
        if (
            info.volume_uuid.lower() != self.expected_uuid
            or info.filesystem.lower().strip() != "case-sensitive apfs"
            or not info.is_mountpoint
            or info.read_only
        ):
            return info
        options = tuple(option for option in info.mount_options if option != "noowners")
        return replace(info, owners_enabled=True, mount_options=options)


def _run(argv: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        command = Path(argv[0]).name
        raise QualificationError(f"command_failed:{command}:{type(exc).__name__}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-500:]
        raise QualificationError(f"command_failed:{Path(argv[0]).name}:{detail}")
    return result


def _disk_info(mountpoint: Path) -> dict[str, object]:
    result = _run(["/usr/sbin/diskutil", "info", "-plist", str(mountpoint)], timeout=15)
    value = plistlib.loads(result.stdout.encode())
    if not isinstance(value, dict):
        raise QualificationError("invalid_diskutil_plist")
    return value


def _write_file(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(payload)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError("short write")
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _trap_snapshot(mountpoint: Path) -> dict[str, tuple[str, int]]:
    result: dict[str, tuple[str, int]] = {}
    for path in sorted(mountpoint.rglob("*")):
        relative = str(path.relative_to(mountpoint))
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            result[relative] = (f"symlink:{os.readlink(path)}", mode)
        elif path.is_dir():
            result[relative] = ("directory", mode)
        elif path.is_file():
            result[relative] = (hashlib.sha256(path.read_bytes()).hexdigest(), mode)
        else:
            result[relative] = ("other", mode)
    return result


def _seed_trap(image: MountedImage) -> dict[str, tuple[str, int]]:
    mirror = image.mountpoint / DATA_ROOT / SERVICE_ROOT
    mirror.mkdir(mode=0o700, parents=True)
    directories = (
        image.mountpoint / DATA_ROOT,
        image.mountpoint / DATA_ROOT / "services",
        mirror,
    )
    for directory in directories:
        directory.chmod(0o700)
    for relative, body in TRAP_FILES.items():
        _write_file(mirror / relative, body)
    probe = image.mountpoint / ".writable-underlay-probe"
    _write_file(probe, b"writable\n")
    probe.unlink()
    return _trap_snapshot(image.mountpoint)


def _new_image(base: Path) -> MountedImage:
    mountpoint = base / "mount"
    mountpoint.mkdir(mode=0o700)
    image = MountedImage(
        base,
        mountpoint,
        base / "detach-qualification.sparseimage",
        base / "config" / "storage.json",
    )
    (base / "config").mkdir(mode=0o700)
    _seed_trap(image)
    _run(
        [
            "/usr/bin/hdiutil",
            "create",
            "-size",
            "256m",
            "-fs",
            "Case-sensitive APFS",
            "-volname",
            "BrainDetachQualification",
            "-type",
            "SPARSE",
            str(image.image),
        ],
        timeout=60,
    )
    image.attach()
    cfg = configure(
        image.config,
        image.mountpoint,
        DATA_ROOT,
        min_free_bytes=0,
        volume_uuid=image.volume_uuid,
        probe=image.probe,
    )
    try:
        if cfg.volume_uuid != image.volume_uuid:
            raise QualificationError("config_image_uuid_mismatch")
        create_root(image.config, probe=image.probe)
        with writer_guard(image.config, probe=image.probe) as guard:
            guard.create_directory(SERVICE_ROOT)
            service_root = image.mountpoint / DATA_ROOT / SERVICE_ROOT
            root_fd = os.open(service_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                with DescriptorWriter(root_fd, before_write=guard.revalidate) as writer:
                    for name, body in {
                        "append-open.jsonl": b"before append-open\n",
                        "append-write.jsonl": b"before append-write\n",
                        "atomic-rename.json": b"old atomic value",
                        "unlink.json": b"remove me",
                    }.items():
                        writer.create(name, body)
                    writer.create(".writer.lock", b"lock seed")
                    writer.mkdir("captures/response")
                    writer.mkdir("captures/receipt")
            finally:
                os.close(root_fd)
        return image
    except BaseException:
        if os.path.ismount(image.mountpoint):
            image.detach()
        raise


def _assert_underlay_unchanged(
    image: MountedImage,
    baseline: dict[str, tuple[str, int]],
    *,
    context: str,
) -> None:
    if os.path.ismount(image.mountpoint):
        mounts = subprocess.run(["/sbin/mount"], capture_output=True, text=True, check=True).stdout
        entry = next(
            (line for line in mounts.splitlines() if str(image.mountpoint) in line),
            "unknown",
        )
        raise QualificationError(f"underlay_check_while_mounted:{context}:{entry}")
    current = _trap_snapshot(image.mountpoint)
    if current != baseline:
        changed = sorted(set(current) ^ set(baseline))
        changed.extend(
            key for key in sorted(set(current) & set(baseline)) if current[key] != baseline[key]
        )
        raise QualificationError(f"underlay_mutated:{','.join(changed[:12])}")


def _mounted_tree(image: MountedImage) -> tuple[Path, int]:
    root = image.mountpoint / DATA_ROOT / SERVICE_ROOT
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    return root, fd


def _force_detach_hook(
    image: MountedImage,
    baseline: dict[str, tuple[str, int]],
    trigger: str,
    *,
    occurrence: int = 1,
) -> tuple[Callable[[str], None], dict[str, object]]:
    state: dict[str, object] = {
        "prechecks": 0,
        "last_precheck_ns": 0,
        "target_prechecks": 0,
        "target_count": 0,
        "detached": False,
        "detached_after_precheck": False,
        "mount_after_detach": None,
        "mount_after_operation": None,
    }

    def hook(operation: str) -> None:
        if operation != trigger:
            return
        count = int(state["target_count"]) + 1
        state["target_count"] = count
        if count != occurrence:
            return
        if int(state["prechecks"]) <= 0:
            raise QualificationError("detach_without_storage_precheck")
        elapsed_ns = time.monotonic_ns() - int(state["last_precheck_ns"])
        if elapsed_ns > 1_000_000_000:
            raise QualificationError("detach_not_immediately_after_precheck")
        state["target_prechecks"] = int(state["target_prechecks"]) + 1
        state["detach_output"] = image.detach()
        state["detached"] = True
        state["detached_after_precheck"] = True
        state["mount_after_detach"] = os.path.ismount(image.mountpoint)
        try:
            _assert_underlay_unchanged(image, baseline, context="fault_hook")
        except QualificationError as exc:
            state["mount_after_detach"] = os.path.ismount(image.mountpoint)
            raise QualificationError(
                f"{exc};detach_output={state['detach_output']};"
                f"device={image.device};uuid={image.volume_uuid}"
            ) from exc

    return hook, state


def _before_write(guard: StorageGuard, state: dict[str, object]) -> None:
    # Keep the real full StorageGuard check in the callback path. The counter
    # only records that a successful precheck preceded the injected detach.
    guard.revalidate()
    state["prechecks"] = int(state["prechecks"]) + 1
    state["last_precheck_ns"] = time.monotonic_ns()


def _scenario(
    image: MountedImage,
    baseline: dict[str, tuple[str, int]],
    *,
    name: str,
    trigger: str,
    operation: Callable[[DescriptorWriter], None],
    occurrence: int = 1,
    expected: Callable[[Path], bool] | None = None,
) -> dict[str, object]:
    if not os.path.ismount(image.mountpoint):
        image.attach()
    hook, state = _force_detach_hook(image, baseline, trigger, occurrence=occurrence)
    outcome = "success"
    error: str | None = None
    try:
        with writer_guard(image.config, probe=image.probe) as guard:
            _, root_fd = _mounted_tree(image)
            try:
                with DescriptorWriter(
                    root_fd,
                    before_write=lambda: _before_write(guard, state),
                    fault_hook=hook,
                ) as writer:
                    try:
                        operation(writer)
                    except (OSError, StorageError) as exc:
                        outcome = "safe_error"
                        error = f"{type(exc).__name__}:{exc}"
            finally:
                os.close(root_fd)
        state["mount_after_operation"] = os.path.ismount(image.mountpoint)
        if not state["detached_after_precheck"]:
            raise QualificationError(
                f"detach_hook_not_reached:{name};outcome={outcome};error={error};state={state}"
            )
        try:
            _assert_underlay_unchanged(image, baseline, context="after_operation")
        except QualificationError as exc:
            raise QualificationError(f"{exc};state={state}") from exc
        image.attach()
        root = image.mountpoint / DATA_ROOT / SERVICE_ROOT
        expected_result = None if expected is None else bool(expected(root))
        if expected_result is False:
            raise QualificationError(f"expected_held_volume_result_missing:{name}")
        return {
            "name": name,
            "trigger": trigger,
            "occurrence": occurrence,
            "outcome": outcome,
            "error": error,
            "prechecks_before_detach": state["prechecks"],
            "detached_after_precheck": state["detached_after_precheck"],
            "underlay_unchanged": True,
            "held_volume_result": expected_result,
        }
    finally:
        if not os.path.ismount(image.mountpoint):
            image.attach()


def run_qualification() -> dict[str, object]:
    if os.uname().sysname != "Darwin":
        raise QualificationError("macos_required")
    temp_parent = Path("/private/tmp")
    if not temp_parent.is_dir():
        raise QualificationError("private_tmp_missing")
    with tempfile.TemporaryDirectory(prefix="brain-detach-qualify-v1-", dir=temp_parent) as raw:
        base = Path(raw)
        base.chmod(0o700)
        image = _new_image(base)
        try:
            return _exercise_image(image)
        finally:
            if os.path.ismount(image.mountpoint):
                image.detach()


def run_reservation_replay_qualification() -> dict[str, object]:
    """Repeat only the durable-reservation remount gate on a fresh APFS image."""
    if os.uname().sysname != "Darwin":
        raise QualificationError("macos_required")
    temp_parent = Path("/private/tmp")
    with tempfile.TemporaryDirectory(prefix="brain-detach-replay-only-v1-", dir=temp_parent) as raw:
        base = Path(raw)
        base.chmod(0o700)
        image = _new_image(base)
        try:
            image.detach()
            baseline = _trap_snapshot(image.mountpoint)
            image.attach()
            result = _reservation_remount_no_replay(image, baseline)
            if os.path.ismount(image.mountpoint):
                image.detach()
            _assert_underlay_unchanged(image, baseline, context="replay_only_final")
            return {
                "schema_version": 1,
                "contract": "research_storage_reservation_remount_no_replay_v1",
                "status": result["status"],
                "image_filesystem": "case-sensitive APFS",
                "image_uuid": image.volume_uuid,
                "image_owners_enabled_by_os": image.owners_enabled,
                "test_only_noowners_probe_normalization": True,
                "configured_kingston_touched": False,
                "provider_requests": 0,
                "source_bindings": orchestrator.source_bindings(),
                "reservation_replay": result,
                "underlay_unchanged": True,
                "limitations": [
                    "Exercises the frozen local orchestrator on a disposable APFS image only.",
                    "Does not qualify live Kingston identity or owner-enforcement policy.",
                    "Does not test provider calls or authorize lease activation.",
                ],
            }
        finally:
            if os.path.ismount(image.mountpoint):
                image.detach()


def _exercise_image(image: MountedImage) -> dict[str, object]:
    baseline = _trap_snapshot(image.mountpoint) if not os.path.ismount(image.mountpoint) else None
    # The image is mounted now; the underlay baseline was captured before
    # the first attach and can be reconstructed from the seeded trap tree.
    if baseline is None:
        image.detach()
        baseline = _trap_snapshot(image.mountpoint)
        image.attach()

    results: list[dict[str, object]] = []

    def expect_file(relative: str, expected: bytes) -> Callable[[Path], bool]:
        def check(root: Path) -> bool:
            path = root / relative
            return not path.exists() or path.read_bytes() == expected

        return check

    results.append(
        _scenario(
            image,
            baseline,
            name="mkdir",
            trigger="mkdir",
            operation=lambda writer: writer.mkdir("forced-mkdir"),
            expected=lambda root: (
                not (root / "forced-mkdir").exists() or (root / "forced-mkdir").is_dir()
            ),
        )
    )

    replay_result = _reservation_remount_no_replay(image, baseline)
    results.append(replay_result)
    results.append(
        _scenario(
            image,
            baseline,
            name="create",
            trigger="create",
            operation=lambda writer: writer.create("forced-create.json", b"create"),
            expected=lambda root: (
                not (root / "forced-create.json").exists()
                or (root / "forced-create.json").read_bytes() == b"create"
            ),
        )
    )
    results.append(
        _scenario(
            image,
            baseline,
            name="append_open",
            trigger="append_open",
            operation=lambda writer: writer.append("append-open.jsonl", b"must-not-write\n"),
            expected=expect_file("append-open.jsonl", b"before append-open\n"),
        )
    )
    results.append(
        _scenario(
            image,
            baseline,
            name="append_write",
            trigger="append_write",
            operation=lambda writer: writer.append("append-write.jsonl", b"after detach\n"),
            expected=lambda root: (
                (root / "append-write.jsonl").read_bytes()
                in {b"before append-write\n", b"before append-write\nafter detach\n"}
            ),
        )
    )
    results.append(
        _scenario(
            image,
            baseline,
            name="atomic_create",
            trigger="atomic_create",
            operation=lambda writer: writer.atomic_write("atomic-create.json", b"new"),
            expected=lambda root: (
                not (root / "atomic-create.json").exists()
                or (root / "atomic-create.json").read_bytes() == b"new"
            ),
        )
    )
    results.append(
        _scenario(
            image,
            baseline,
            name="atomic_rename",
            trigger="atomic_rename",
            operation=lambda writer: writer.atomic_write("atomic-rename.json", b"new"),
            expected=lambda root: (
                (root / "atomic-rename.json").read_bytes() in {b"old atomic value", b"new"}
            ),
        )
    )
    results.append(
        _scenario(
            image,
            baseline,
            name="unlink",
            trigger="unlink",
            operation=lambda writer: writer.unlink("unlink.json"),
            expected=lambda root: (
                not (root / "unlink.json").exists()
                or (root / "unlink.json").read_bytes() == b"remove me"
            ),
        )
    )
    results.append(
        _scenario(
            image,
            baseline,
            name="lock_open",
            trigger="lock_open",
            operation=lambda writer: _take_lock(writer),
            expected=lambda root: (root / ".writer.lock").is_file(),
        )
    )

    method = "getSlot"
    params: list[object] = []
    request_json = (
        canonical_json({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        .decode()
        .strip()
    )
    request_hash = semantic_request_sha256(method, params)
    wire_hash = wire_request_sha256(method, params)
    response_body = b'{"jsonrpc":"2.0","result":123}\n'
    capture = SimpleNamespace(
        response=response_body,
        source_alias="helius-free",
        method=method,
        request_json=request_json,
        request_started_ns=time.time_ns() - 2_000_000,
        received_ns=time.time_ns() - 1_000_000,
    )
    results.append(
        _scenario(
            image,
            baseline,
            name="capture_response_atomic_rename",
            trigger="atomic_rename",
            operation=lambda writer: write_capture(
                writer,
                capture=capture,
                provider="helius-free",
                request_id="detach-test-response",
                method=method,
                params=params,
                request_sha256=request_hash,
                wire_request_sha256=wire_hash,
                decision_ns=time.time_ns(),
                relative_directory="captures/response",
            ),
            expected=lambda root: (
                (
                    not (root / "captures/response/response.json").exists()
                    and not (root / "captures/response/receipt.json").exists()
                )
                or (
                    (root / "captures/response/response.json").read_bytes() == response_body
                    and not (root / "captures/response/receipt.json").exists()
                )
            ),
        )
    )

    receipt_capture = SimpleNamespace(
        response=b'{"jsonrpc":"2.0","result":124}\n',
        source_alias="helius-free",
        method=method,
        request_json=request_json,
        request_started_ns=time.time_ns() - 2_000_000,
        received_ns=time.time_ns() - 1_000_000,
    )
    results.append(
        _scenario(
            image,
            baseline,
            name="capture_receipt_atomic_rename",
            trigger="atomic_rename",
            occurrence=2,
            operation=lambda writer: write_capture(
                writer,
                capture=receipt_capture,
                provider="helius-free",
                request_id="detach-test-receipt",
                method=method,
                params=params,
                request_sha256=request_hash,
                wire_request_sha256=wire_hash,
                decision_ns=time.time_ns(),
                relative_directory="captures/receipt",
            ),
            expected=lambda root: (
                (
                    not (root / "captures/receipt/response.json").exists()
                    and not (root / "captures/receipt/receipt.json").exists()
                )
                or (
                    (root / "captures/receipt/response.json").read_bytes()
                    == receipt_capture.response
                    and (
                        not (root / "captures/receipt/receipt.json").exists()
                        or json.loads((root / "captures/receipt/receipt.json").read_text())[
                            "response_sha256"
                        ]
                        == hashlib.sha256(receipt_capture.response).hexdigest()
                    )
                )
            ),
        )
    )

    image.detach()
    final_underlay_unchanged = _trap_snapshot(image.mountpoint) == baseline
    if not final_underlay_unchanged:
        raise QualificationError("final_underlay_mutated")
    return {
        "schema_version": 1,
        "contract": "research_storage_detach_qualify_v1",
        "status": "passed" if all(row["underlay_unchanged"] for row in results) else "failed",
        "image_filesystem": "case-sensitive APFS",
        "image_uuid": image.volume_uuid,
        "image_owners_enabled_by_os": image.owners_enabled,
        "test_only_noowners_probe_normalization": True,
        "image_directory": "temporary /private/tmp sparse APFS image; removed on exit",
        "underlay_directory": (
            "writable internal temporary mountpoint mirror with sentinel bait files"
        ),
        "configured_kingston_touched": False,
        "storage_guard_precheck": (
            "StorageGuard.revalidate before each injected mutation, with test-only "
            "ownership normalization for the disposable noowners APFS image"
        ),
        "results": results,
        "provider_requests": 0,
        "reservation_replay_gate": replay_result["status"],
        "limitations": [
            (
                "Exercises this host's APFS image detach behavior and descriptor writer "
                "operations; "
                "it does not establish physical cable removal or device-specific behavior."
            ),
            "Does not test a provider call or authorize lease activation.",
            "Does not qualify live Kingston identity or owner-enforcement policy.",
        ],
    }


def _reservation_remount_no_replay(
    image: MountedImage,
    baseline: dict[str, tuple[str, int]],
) -> dict[str, object]:
    """Persist a spent reservation, remount, and prove the provider loader is not called."""
    if not os.path.ismount(image.mountpoint):
        image.attach()
    with writer_guard(image.config, probe=image.probe) as guard:
        _, root_fd = _mounted_tree(image)
        try:
            with DescriptorWriter(root_fd, before_write=guard.revalidate) as writer:
                now_ns = time.time_ns()
                minute_ns = 60_000_000_000
                bootstrap_start = (now_ns // minute_ns + 2) * minute_ns
                frame_start = bootstrap_start + 5 * minute_ns
                plan = orchestrator.build_plan(
                    service_id="svc",
                    root_id=guard.root_id,
                    volume_uuid=image.volume_uuid or "",
                    relative_root=SERVICE_ROOT,
                    predecessor={
                        "campaign_id": "qualification-only",
                        "stop_sha256": "1" * 64,
                        "terminal_receipt_sha256": "2" * 64,
                        "committed_frontier": 1,
                        "gap_intervals": [],
                        "last_inventoried_interval": None,
                    },
                    clock_preflight_ns=bootstrap_start - 2 * minute_ns,
                    bootstrap_start_ns=bootstrap_start,
                    frame_start_ns=frame_start,
                    expires_ns=frame_start + 3 * minute_ns,
                    endpoint_origin="https://mainnet.helius-rpc.com",
                )
                orchestrator.initialize(writer, plan)
                now_ns = time.time_ns()
                bucket = str(plan["schedule"]["bootstrap"]["bucket"])
                base = f"buckets/{bucket}"
                phase = "bootstrap"
                plan_hash = orchestrator.plan_digest(plan)
                reservation = {
                    "schema": external_schema.BUCKET_SCHEMA,
                    "schema_version": 1,
                    "service_id": "svc",
                    "bucket": bucket,
                    "phase": phase,
                    "reservation_id": hashlib.sha256(
                        f"{plan_hash}:{bucket}:{phase}".encode()
                    ).hexdigest()[:32],
                    "plan_sha256": plan_hash,
                    "request_count": 1,
                    "raw_byte_cap": plan["caps"]["bootstrap_raw_bytes"],
                    "stored_byte_cap": plan["caps"]["bootstrap_stored_bytes"],
                    "created_ns": now_ns,
                    "not_before_ns": plan["schedule"]["bootstrap"]["start_ns"],
                    "expires_ns": plan["schedule"]["bootstrap"]["end_ns"],
                    "predecessor_event_sha256": "0" * 64,
                }
                writer.mkdir(base)
                reservation_bytes = canonical_json(reservation)
                writer.atomic_write(base + "/reservation.json", reservation_bytes, create_only=True)
                orchestrator.append_event(
                    writer,
                    kind="reservation",
                    bucket=bucket,
                    at_ns=now_ns,
                    payload={"reservation_sha256": hashlib.sha256(reservation_bytes).hexdigest()},
                )
                reservation_sha256 = hashlib.sha256(reservation_bytes).hexdigest()
        finally:
            os.close(root_fd)

    image.detach()
    _assert_underlay_unchanged(image, baseline, context="reservation_remount")
    image.attach()
    client_loader_calls = 0

    def forbidden_client_loader():
        nonlocal client_loader_calls
        client_loader_calls += 1
        raise AssertionError("provider_loader_called_for_spent_reservation")

    with writer_guard(image.config, probe=image.probe) as guard:
        _, root_fd = _mounted_tree(image)
        try:
            with DescriptorWriter(root_fd, before_write=guard.revalidate) as writer:
                outcome = orchestrator.run_bucket(
                    writer,
                    bucket=bucket,
                    client_loader=forbidden_client_loader,
                    guard=guard,
                    now_ns=now_ns,
                    clock_probe=lambda: (_ for _ in ()).throw(
                        AssertionError("clock_probe_called_for_spent_reservation")
                    ),
                )
        finally:
            os.close(root_fd)
    if (
        outcome.get("status") != "stopped"
        or outcome.get("provider_io") is not False
        or outcome.get("stop", {}).get("reason") != "interrupted_no_replay"
        or client_loader_calls != 0
    ):
        raise QualificationError(
            f"reservation_remount_no_replay_failed:outcome={outcome};"
            f"client_loader_calls={client_loader_calls}"
        )
    image.detach()
    _assert_underlay_unchanged(image, baseline, context="reservation_replay_result")
    return {
        "name": "reservation_remount_no_replay",
        "status": "passed",
        "reservation_sha256": reservation_sha256,
        "outcome": outcome,
        "provider_loader_calls": client_loader_calls,
        "provider_io": False,
        "remounted_before_reconciliation": True,
        "underlay_unchanged": True,
    }


def _take_lock(writer: DescriptorWriter) -> None:
    with writer.lock():
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reservation-replay-only",
        action="store_true",
        help="repeat only the reservation/remount no-replay gate on a disposable APFS image",
    )
    arguments = parser.parse_args()
    started_ns = time.time_ns()
    started_monotonic_ns = time.monotonic_ns()
    try:
        result = (
            run_reservation_replay_qualification()
            if arguments.reservation_replay_only
            else run_qualification()
        )
        source_paths = {
            "qualification_harness": Path(__file__).resolve(),
            "storage_library": Path(__file__).resolve().parents[1]
            / "src/memecoin_research_storage/__init__.py",
            **{
                f"source_binding:{relative}": Path(__file__).resolve().parents[1] / relative
                for relative in orchestrator.source_bindings()
            },
        }
        result["provenance"] = {
            "command": (
                "PYTHONPATH=src python3 tools/research_storage_detach_qualify_v1.py"
                + (" --reservation-replay-only" if arguments.reservation_replay_only else "")
            ),
            "started_utc": datetime.fromtimestamp(started_ns / 1_000_000_000, tz=UTC).isoformat(),
            "completed_utc": datetime.now(UTC).isoformat(),
            "elapsed_monotonic_ns": time.monotonic_ns() - started_monotonic_ns,
            "platform": platform.platform(),
            "macos_version": platform.mac_ver()[0],
            "python": platform.python_version(),
            "source_sha256": {
                label: hashlib.sha256(path.read_bytes()).hexdigest()
                for label, path in source_paths.items()
            },
        }
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except (QualificationError, StorageError, OSError, subprocess.SubprocessError) as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}:{exc}"}
        print(json.dumps(result, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
