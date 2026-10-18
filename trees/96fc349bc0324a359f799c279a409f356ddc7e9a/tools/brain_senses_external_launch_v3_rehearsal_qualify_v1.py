#!/usr/bin/env python3
"""Qualify the inert launch-v3 entry with one disposable APFS LaunchAgent run.

The harness creates a private case-sensitive APFS image under /private/tmp,
uses a descriptor-bound storage config, installs one nonce-labeled temporary
LaunchAgent, kicks it once, then disables and boots it out. It does not load
credentials, contact providers, or alter any production or v2 runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from memecoin_research_storage import configure, create_root  # noqa: E402
from tools import brain_senses_external_launch_v3_rehearsal as entry  # noqa: E402
from tools import brain_senses_launch_v3_synthetic_fixture_v1 as fixture  # noqa: E402

TEMPLATE = REPO / "ops/launchd/ai.memecoin.brain.senses.launch-v3-rehearsal.plist"
ENTRY = REPO / "tools/brain_senses_external_launch_v3_rehearsal.py"
PINNED_PATHS = (
    "tools/brain_senses_external_launch_v3_rehearsal.py",
    "tools/brain_senses_launch_v3_synthetic_fixture_v1.py",
    "tools/brain_senses_external_launch_v3_rehearsal_qualify_v1.py",
    "tools/brain_senses_failure_receipt_v1.py",
    "tools/brain_senses_external_launch_v3.py",
    "tools/brain_senses_external_verify_v2.py",
    "tools/brain_senses_external_writer_v1.py",
    "src/memecoin_research_storage/__init__.py",
    "ops/launchd/ai.memecoin.brain.senses.launch-v3-rehearsal.plist",
    "tests/test_brain_senses_external_launch_v3_rehearsal.py",
    "tests/test_brain_senses_external_launch_v3.py",
)
DATA_ROOT = "ResearchData"
KINGSTON_UUID = "8f8f1081-9be4-4914-9111-75f1b90255bf"


class QualificationError(RuntimeError):
    """Sanitized qualification failure."""


class MountedImage:
    def __init__(self, root: Path, mountpoint: Path, image: Path, config: Path) -> None:
        self.root = root
        self.mountpoint = mountpoint
        self.image = image
        self.config = config
        self.device: str | None = None
        self.volume_uuid: str | None = None
        self.probe: entry.DisposableImageProbe | None = None

    def attach(self) -> None:
        result = subprocess.run(
            [
                "/usr/bin/hdiutil",
                "attach",
                "-nobrowse",
                "-noautoopen",
                "-mountpoint",
                str(self.mountpoint),
                str(self.image),
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if result.returncode != 0 or not os.path.ismount(self.mountpoint):
            raise QualificationError("disposable_image_attach_failed")
        info = _disk_info(self.mountpoint)
        self.device = str(info.get("DeviceIdentifier", ""))
        self.volume_uuid = str(info.get("VolumeUUID", "")).lower()
        if not self.device.startswith("disk") or not self.volume_uuid:
            raise QualificationError("disposable_image_identity_missing")
        if str(info.get("FilesystemName", "")).lower().strip() != "case-sensitive apfs":
            raise QualificationError("disposable_filesystem_invalid")
        self.probe = entry.DisposableImageProbe(self.volume_uuid)

    def detach(self) -> None:
        if not os.path.ismount(self.mountpoint):
            return
        if not self.device:
            raise QualificationError("disposable_device_identity_missing")
        result = subprocess.run(
            ["/usr/bin/hdiutil", "detach", "-force", f"/dev/{self.device}"],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if result.returncode != 0 or os.path.ismount(self.mountpoint):
            raise QualificationError("disposable_image_detach_failed")


def _disk_info(mountpoint: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["/usr/sbin/diskutil", "info", "-plist", str(mountpoint)],
        capture_output=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        raise QualificationError("diskutil_info_failed")
    value = plistlib.loads(result.stdout)
    if not isinstance(value, dict):
        raise QualificationError("diskutil_info_invalid")
    return value


def _run(argv: list[str], timeout: int) -> None:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise QualificationError(f"command_failed:{Path(argv[0]).name}")


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def _hash_file(path: Path) -> str:
    return _sha(path.read_bytes())


def _source_hashes() -> dict[str, str]:
    return {relative: _hash_file(REPO / relative) for relative in PINNED_PATHS}


def _underlay_snapshot(mountpoint: Path) -> dict[str, tuple[str, int]]:
    out: dict[str, tuple[str, int]] = {}
    for path in sorted(mountpoint.rglob("*")):
        relative = str(path.relative_to(mountpoint))
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            out[relative] = (f"symlink:{os.readlink(path)}", mode)
        elif path.is_dir():
            out[relative] = ("directory", mode)
        elif path.is_file():
            out[relative] = (_hash_file(path), mode)
        else:
            out[relative] = ("other", mode)
    return out


def _write_private(path: Path, body: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(body)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise QualificationError("short_write")
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _launchctl(argv: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/launchctl", *argv], capture_output=True, text=True, timeout=timeout, check=False
    )


def _assert_absent(label: str) -> None:
    target = f"gui/{os.getuid()}/{label}"
    result = _launchctl(["print", target])
    if result.returncode == 0:
        raise QualificationError("launchd_label_still_loaded")


def _override_state(label: str) -> str:
    target = f"gui/{os.getuid()}"
    result = _launchctl(["print-disabled", target])
    if result.returncode != 0:
        raise QualificationError("launchctl_disabled_state_unavailable")
    return _parse_override_state(label, result.stdout)


def _parse_override_state(label: str, output: str) -> str:
    matching = [line for line in output.splitlines() if f'"{label}"' in line]
    if not matching:
        return "default"
    if len(matching) != 1 or "=>" not in matching[0]:
        raise QualificationError("launchctl_disabled_state_ambiguous")
    value = matching[0].split("=>", 1)[1].strip()
    if value not in {"disabled", "enabled"}:
        raise QualificationError("launchctl_disabled_state_invalid")
    return value


def _assert_not_disabled(label: str) -> str:
    state = _override_state(label)
    if state == "disabled":
        raise QualificationError("launchd_disabled_override_remains")
    return state


def qualify(output_path: Path) -> dict[str, Any]:
    if sys.platform != "darwin" or not output_path.is_absolute():
        raise QualificationError("expected_macos_and_absolute_output")
    with tempfile.TemporaryDirectory(
        prefix="senses-launch-v3-rehearsal-", dir="/private/tmp"
    ) as raw:
        root = Path(raw)
        mountpoint = root / "mount"
        mountpoint.mkdir(mode=0o700)
        config_dir = root / "config"
        config_dir.mkdir(mode=0o700)
        state_dir = root / "state"
        state_dir.mkdir(mode=0o700)
        result_path = state_dir / "result.json"
        plist_path = root / "launch-v3-rehearsal.plist"
        image = MountedImage(
            root,
            mountpoint,
            root / "launch-v3-rehearsal.sparseimage",
            config_dir / "storage.json",
        )

        # Seed a writable directory hidden by the mounted image; it must remain
        # byte- and mode-identical when the image is detached.
        underlay = mountpoint / ".underlay-sentinel"
        underlay.mkdir(mode=0o700)
        _write_private(underlay / "sentinel.json", b"underlay must remain unchanged\n")
        baseline = _underlay_snapshot(mountpoint)
        _run(
            [
                "/usr/bin/hdiutil",
                "create",
                "-size",
                "256m",
                "-fs",
                "Case-sensitive APFS",
                "-volname",
                "BrainLaunchV3Rehearsal",
                "-type",
                "SPARSE",
                str(image.image),
            ],
            timeout=90,
        )
        image.attach()
        if image.volume_uuid is None or image.volume_uuid == KINGSTON_UUID or image.probe is None:
            raise QualificationError("disposable_volume_identity_invalid")
        cfg = configure(
            image.config,
            mountpoint,
            DATA_ROOT,
            min_free_bytes=0,
            volume_uuid=image.volume_uuid,
            probe=image.probe,
        )
        if cfg.volume_uuid != image.volume_uuid:
            raise QualificationError("storage_config_uuid_mismatch")
        create_root(image.config, probe=image.probe)
        journal_sha = entry.seed_fixture(image.config, image.probe)
        if journal_sha != fixture.JOURNAL_SHA256:
            raise QualificationError("fixture_journal_hash_mismatch")

        nonce = secrets.token_hex(8)
        label = f"ai.memecoin.brain.senses.launch-v3-rehearsal-{nonce}"
        template = plistlib.loads(TEMPLATE.read_bytes())
        if not isinstance(template, dict):
            raise QualificationError("plist_template_invalid")
        template["Label"] = label
        template["ProgramArguments"] = [
            sys.executable,
            str(ENTRY),
            "--config",
            str(image.config),
            "--state-dir",
            str(state_dir),
            "--result",
            str(result_path),
            "--label",
            label,
        ]
        template["StandardOutPath"] = str(state_dir / "stdout.log")
        template["StandardErrorPath"] = str(state_dir / "stderr.log")
        forbidden = {
            "RunAtLoad",
            "KeepAlive",
            "StartCalendarInterval",
            "StartInterval",
            "EnvironmentVariables",
        }
        if forbidden.intersection(template):
            raise QualificationError("plist_has_schedule_or_environment")
        plist_bytes = plistlib.dumps(template, fmt=plistlib.FMT_XML, sort_keys=True)
        plistlib.loads(plist_bytes)
        _write_private(plist_path, plist_bytes)
        plist_sha = _sha(plist_bytes)
        config_sha = _hash_file(image.config)
        target = f"gui/{os.getuid()}/{label}"
        bootstrapped = False
        disabled_override = False
        teardown: dict[str, int] = {}
        try:
            boot = _launchctl(["bootstrap", f"gui/{os.getuid()}", str(plist_path)])
            if boot.returncode != 0:
                raise QualificationError("launchctl_bootstrap_failed")
            bootstrapped = True
            kick = _launchctl(["kickstart", target])
            if kick.returncode != 0:
                raise QualificationError("launchctl_kickstart_failed")
            deadline = time.monotonic() + 30
            while not result_path.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            if not result_path.is_file():
                error_log = state_dir / "stderr.log"
                detail = error_log.read_text(errors="replace") if error_log.exists() else ""
                raise QualificationError(f"launchd_result_timeout:{detail[-300:]}")
            result_bytes = result_path.read_bytes()
            result = json.loads(result_bytes)
            if (
                result.get("schema") != entry.RESULT_SCHEMA
                or result.get("synthetic") is not True
                or result.get("label") != label
                or result.get("status") != "pre_intent_failure_receipted"
                or result.get("intent_state") != "verified_none"
                or result.get("dispatch_attempts") != 1
                or result.get("restart_dispatch_attempts") != 0
                or result.get("journal_sha256_before") != fixture.JOURNAL_SHA256
                or result.get("journal_sha256_after") != fixture.JOURNAL_SHA256
                or result.get("provider_calls") != 0
                or result.get("credential_reads") != 0
                or result.get("blocked_import_attempts") != []
                or result.get("loaded_forbidden_imports") != []
            ):
                raise QualificationError("launchd_result_invalid")

            # Simulate a process restart with the same path-only arguments. The
            # create-only marker must make this an inert exit without dispatch.
            restart = subprocess.run(
                template["ProgramArguments"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if restart.returncode != 0 or _hash_file(result_path) != _sha(result_bytes):
                raise QualificationError("restart_replayed_or_changed_result")

            disable = _launchctl(["disable", target])
            teardown["disable"] = disable.returncode
            disabled_override = disable.returncode == 0
            bootout = _launchctl(["bootout", target])
            teardown["bootout"] = bootout.returncode
            if disable.returncode != 0 or bootout.returncode != 0:
                raise QualificationError("launchctl_teardown_failed")
            bootstrapped = False
            enable = _launchctl(["enable", target])
            teardown["enable"] = enable.returncode
            if enable.returncode != 0:
                raise QualificationError("launchctl_enable_failed")
            disabled_override = False
            _assert_absent(label)
            override_state = _assert_not_disabled(label)
            if override_state == "disabled" or teardown["enable"] != 0:
                raise QualificationError("disabled_override_clear_failed")
            pid = result.get("pid")
            if type(pid) is not int:
                raise QualificationError("result_pid_invalid")
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                process_absent = True
            else:
                process_absent = False
            if not process_absent:
                raise QualificationError("rehearsal_process_still_present")

            receipt = entry._read_receipt(
                image.config, image.probe, fixture.SERVICE_ID, fixture.BUCKET
            )
            receipt_hash = _sha(receipt)
            if result.get("receipt_sha256") != receipt_hash:
                raise QualificationError("receipt_hash_mismatch")
            from tools.brain_senses_failure_receipt_v1 import validate_record

            receipt_value = json.loads(receipt)
            validate_record(receipt_value)
            if receipt_value.get("intent_state") != "verified_none":
                raise QualificationError("receipt_not_verified_none")
        finally:
            if bootstrapped:
                _launchctl(["disable", target])
                _launchctl(["bootout", target])
                disabled_override = True
            if disabled_override:
                _launchctl(["enable", target])
            if plist_path.exists():
                plist_path.unlink()
            if os.path.ismount(mountpoint):
                image.detach()

        if _underlay_snapshot(mountpoint) != baseline:
            raise QualificationError("underlay_changed_after_detach")
        if plist_path.exists() or os.path.ismount(mountpoint):
            raise QualificationError("temporary_artifact_cleanup_failed")
        return {
            "schema": "brain-senses-launch-v3-disposable-launchd-rehearsal-v1",
            "schema_version": 1,
            "status": "passed",
            "synthetic_only": True,
            "production_activation": False,
            "provider_io": False,
            "provider_calls": 0,
            "credential_reads": 0,
            "blocked_import_attempts": result["blocked_import_attempts"],
            "loaded_forbidden_imports": result["loaded_forbidden_imports"],
            "transaction_signing_or_submission": False,
            "launchd_label": label,
            "launchd_bootstrap_exit": boot.returncode,
            "launchd_kickstart_exit": kick.returncode,
            "launchd_teardown_exit": teardown,
            "launchd_label_absent": True,
            "launchd_disabled_override_absent": True,
            "launchd_override_state_after_enable": override_state,
            "process_absent": True,
            "launch_count": 1,
            "restart_exit": restart.returncode,
            "restart_dispatch_attempts": 0,
            "callback_start_utc_ns": result["callback_start_utc_ns"],
            "receipt_utc_ns": result["receipt_utc_ns"],
            "callback_exit": 0,
            "intent_state": receipt_value["intent_state"],
            "journal_sha256_before": fixture.JOURNAL_SHA256,
            "journal_sha256_after": fixture.JOURNAL_SHA256,
            "receipt_sha256": receipt_hash,
            "result_sha256": _sha(result_bytes),
            "storage_config_sha256": config_sha,
            "plist_sha256": plist_sha,
            "volume_uuid": image.volume_uuid,
            "filesystem": "case-sensitive APFS",
            "capacity_qualified": False,
            "underlay_unchanged": True,
            "temporary_plist_removed": True,
            "temporary_image_removed": True,
            "source_sha256": _source_hashes(),
            "observed_at_utc_ns": time.time_ns(),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = qualify(args.output)
        args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write_private(args.output, _canonical(result))
    except Exception as exc:
        print(f"launch-v3 rehearsal qualification failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
