#!/usr/bin/env python3
"""Exercise post-detach absolute and cwd writes under a disposable APFS mount.

This destructive integration probe intentionally force-unmounts the supplied disposable
image. Never point it at Kingston, any non-disposable volume, or a production mount.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from brain_senses_build_storage_guard import Snapshot, run_guarded_stage

GIB = 1024**3
MIB = 1024**2


def _run(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, capture_output=True, check=True, timeout=10)


def _info(mount: Path) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        plistlib.loads(_run(["/usr/sbin/diskutil", "info", "-plist", str(mount)]).stdout),
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            rows.append({"path": rel, "kind": "symlink"})
        elif path.is_dir():
            st = path.stat()
            rows.append({"path": rel, "kind": "directory", "mode": oct(st.st_mode & 0o777)})
        elif path.is_file():
            st = path.stat()
            rows.append(
                {
                    "path": rel,
                    "kind": "file",
                    "mode": oct(st.st_mode & 0o777),
                    "size": st.st_size,
                    "sha256": _sha(path),
                }
            )
    return rows


def qualify(mount: Path, image: Path) -> dict[str, Any]:
    if mount != Path("/Volumes/BrainBuildGuardTest"):
        raise SystemExit("refusing non-test mount path")
    if not image.is_absolute() or "/private/tmp/" not in str(image):
        raise SystemExit("disposable image must be under /private/tmp")
    info = _info(mount)
    if (
        info.get("FilesystemType") != "apfs"
        or info.get("VolumeUUID", "").lower() == "8f8f1081-9be4-4914-9111-75f1b90255bf"
    ):
        raise SystemExit("refusing non-disposable or non-APFS volume")
    personality = subprocess.run(
        ["/usr/sbin/diskutil", "info", str(mount)], capture_output=True, text=True, check=True
    ).stdout
    if "Case-sensitive APFS" not in personality:
        raise SystemExit("test volume must be case-sensitive APFS")
    owners_enabled = "Owners: Enabled" in personality

    root = mount / "BrainData"
    work = root / "forced-detach-writes"
    source, cargo_home, target, tmpdir = (
        work / name for name in ("source", "cargo-home", "target", "tmp")
    )
    for path in (source, cargo_home, target, tmpdir):
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
    rename_source = source / "rename-source"
    rename_source.write_text("rename-source", encoding="utf-8")
    before_manifest = _manifest(work)
    control = image.parent / "control"
    control.mkdir(mode=0o700, exist_ok=True)
    ready = control / "write-ready"
    results = control / "write-results.json"
    for path in (ready, results):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    created_file = source / "after-detach-file"
    rename_destination = source / "rename-destination"
    tmp_file = tmpdir / "after-detach-tmp"
    operations = {
        "mkdir_absolute": f"os.mkdir({str(source / 'after-detach-dir')!r})",
        "open_create_absolute": (
            f"os.open({str(created_file)!r}, os.O_CREAT | os.O_WRONLY, 0o600)"
        ),
        "tmp_write_absolute": f"pathlib.Path({str(tmp_file)!r}).write_text('x')",
        "rename_absolute": (f"os.rename({str(rename_source)!r}, {str(rename_destination)!r})"),
        "open_create_held_cwd": "os.open('held-cwd-after-detach', os.O_CREAT | os.O_WRONLY, 0o600)",
    }
    child = "\n".join(
        [
            "import errno,json,os,pathlib,sys,time",
            f"control=pathlib.Path({str(control)!r})",
            f"ready=pathlib.Path({str(ready)!r})",
            f"results=pathlib.Path({str(results)!r})",
            "ready.write_text('ready')",
            "sys.stdin.readline()",
            "out={}",
            "def attempt(name, source):",
            " try:",
            "  exec(source, globals())",
            "  out[name]={'result':'succeeded'}",
            " except OSError as exc:",
            "  out[name]={'result':'os_error','errno':exc.errno,'type':type(exc).__name__}",
            " results.write_text(json.dumps(out,sort_keys=True))",
            *[f"attempt({name!r}, {code!r})" for name, code in operations.items()],
            "time.sleep(20)",
        ]
    )

    events: dict[str, Any] = {}
    child_proc: subprocess.Popen[bytes] | None = None
    pids: list[int] = []
    cancel = threading.Event()

    class Provider:
        def inspect(self) -> Snapshot:
            try:
                current = _info(mount)
                st = root.stat()
                space = os.statvfs(root)
                return Snapshot(
                    "d" * 32,
                    current["VolumeUUID"].lower(),
                    mount,
                    root,
                    space.f_bavail * space.f_frsize,
                    st.st_dev,
                    st.st_ino,
                )
            except Exception:
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    try:
                        completed = json.loads(results.read_text())
                    except (FileNotFoundError, json.JSONDecodeError):
                        completed = {}
                    if set(completed) == set(operations):
                        break
                    time.sleep(0.005)
                events["probe_failed_monotonic"] = time.monotonic()
                raise

    def tracked_popen(command: Sequence[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        nonlocal child_proc
        child_proc = subprocess.Popen(command, stdin=subprocess.PIPE, **kwargs)
        pids.append(child_proc.pid)
        return child_proc

    def detach() -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not ready.exists() and not cancel.is_set():
            time.sleep(0.01)
        if cancel.is_set() and not ready.exists():
            events["controller_cancelled"] = True
            return
        if not ready.exists() or child_proc is None or child_proc.stdin is None:
            events["ready_timeout"] = True
            return
        events["writer_ready_monotonic"] = time.monotonic()
        done = subprocess.run(
            ["/usr/sbin/diskutil", "unmount", "force", str(mount)],
            capture_output=True,
            text=True,
        )
        events["detach_done_monotonic"] = time.monotonic()
        events["unmount_returncode"] = done.returncode
        child_proc.stdin.write(b"go\n")
        child_proc.stdin.flush()

    def stage() -> None:
        return_code = run_guarded_stage(
            [sys.executable, "-c", child],
            source=source,
            cargo_home=cargo_home,
            target_dir=target,
            tmpdir=tmpdir,
            initial=True,
            snapshot_provider=Provider(),
            working_directory=source,
            poll_seconds=0.05,
            term_grace_seconds=0.1,
            popen=tracked_popen,
        )
        if return_code != 0:
            cancel.set()
            raise RuntimeError(f"supervised_child_exit_{return_code}")

    controller = threading.Thread(target=detach, name="apfs-detach-controller")
    controller.start()
    import brain_senses_build_storage_guard as guard

    guard.INITIAL_FREE_BYTES = 256 * MIB
    guard.ALLOCATION_CAP_BYTES = 128 * MIB
    guard.MIN_FREE_BYTES = 64 * MIB
    guard.PROBE_TIMEOUT_SECONDS = 2
    started = datetime.now(UTC).isoformat()
    started_monotonic = time.monotonic()
    failure = None
    try:
        stage()
    except Exception as exc:
        failure = {"type": type(exc).__name__, "reason": str(exc)}
    controller.join(timeout=10)
    if controller.is_alive():
        raise SystemExit("detach controller did not stop")
    if mount.exists() or root.exists():
        raise SystemExit("mountpoint underlay appeared or remained mounted")
    result = json.loads(results.read_text()) if results.exists() else {}
    if not result:
        raise SystemExit("no post-detach writes reached their result boundary")
    if any(row.get("result") != "os_error" for row in result.values()):
        raise SystemExit("one or more writes succeeded after detach")
    subprocess.run(
        ["/usr/bin/hdiutil", "attach", "-nobrowse", "-mountpoint", str(mount), str(image)],
        check=True,
        capture_output=True,
    )
    remount = _info(mount)
    remount_details = subprocess.run(
        ["/usr/sbin/diskutil", "info", str(mount)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    remount_owners_enabled = "Owners: Enabled" in remount_details
    if remount.get("VolumeUUID") != info.get("VolumeUUID"):
        raise SystemExit("remount identity mismatch")
    after = _manifest(work)
    ps = subprocess.run(
        ["ps", "-Ao", "pid,pgid,command"], capture_output=True, text=True, check=True
    ).stdout
    survivors = []
    for line in ps.splitlines()[1:]:
        fields = line.strip().split(None, 2)
        if len(fields) >= 2 and any(fields[1] == str(pid) for pid in pids):
            survivors.append(line.strip())
    if survivors:
        raise SystemExit("supervised process group has survivors")
    forbidden = [
        source / "after-detach-dir",
        created_file,
        rename_destination,
        tmp_file,
    ]
    if any(path.exists() for path in forbidden):
        raise SystemExit("delayed or detached write appeared after remount")
    return {
        "contract": "brain_senses_build_storage_guard_apfs_detach_v1",
        "started_utc": started,
        "guard_sha256": _sha(Path(__file__).with_name("brain_senses_build_storage_guard.py")),
        "harness_sha256": _sha(Path(__file__)),
        "events": events,
        "volume_uuid": info["VolumeUUID"],
        "filesystem": "case-sensitive APFS",
        "volume_owners_enabled": owners_enabled,
        "test_only_capacity_override": {
            "initial_free_bytes": 256 * MIB,
            "allocation_cap_bytes": 128 * MIB,
            "min_free_bytes": 64 * MIB,
        },
        "probe_interval_seconds": 0.05,
        "probe_deadline_seconds": 2,
        "failure": failure,
        "elapsed_seconds": time.monotonic() - started_monotonic,
        "supervised_process_group_ids": pids,
        "supervised_group_survivors": survivors,
        "post_detach_write_results": result,
        "underlay_absent_before_remount": True,
        "remount_same_uuid": True,
        "after_manifest_sha256": hashlib.sha256(
            json.dumps(after, sort_keys=True).encode()
        ).hexdigest(),
        "post_remount_forbidden_artifacts": [],
        "before_manifest_sha256": hashlib.sha256(
            json.dumps(before_manifest, sort_keys=True).encode()
        ).hexdigest(),
        "remount_owners_enabled": remount_owners_enabled,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mount", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(qualify(args.mount, args.image), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
