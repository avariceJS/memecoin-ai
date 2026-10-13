"""Source-bound Gecko v4 host with versioned watchdog-diagnostic census."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import plistlib
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
for _entry in (REPO, REPO / "src", REPO / "tools"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from memecoin_research_storage import (  # noqa: E402
    _open_relative_directory,
    writer_guard,
)
from memecoin_research_storage.live_writer import (  # noqa: E402
    revalidate_descriptor_write,
)
from tools import brain_senses_capacity_coordinator_v2 as capacity  # noqa: E402
from tools import brain_senses_external_clock_v1 as external_clock  # noqa: E402
from tools import brain_senses_gecko_capture_only_v2_host as v2  # noqa: E402
from tools import brain_senses_gecko_capture_only_v2_service as service  # noqa: E402
from tools import brain_senses_gecko_rotating_scout_v1 as scout  # noqa: E402
from tools import brain_senses_gecko_usage_inventory_v2 as usage_inventory_v2  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402

CONFIG_PATH = Path.home() / ".config/brain-research/storage-v1.json"
RELEASE_CODE_PATHS = (
    "tools/brain_senses_gecko_capture_only_v4_host.py",
    "tools/brain_senses_gecko_canary_watchdog_v2.py",
    "tools/brain_senses_gecko_usage_inventory_v2.py",
    "tools/brain_senses_gecko_capture_only_v2_terminal_usage_v4.py",
    "src/memecoin_research_storage/live_writer.py",
)
LAUNCHD_PREFIX = "ai.memecoin.brain.senses.gecko-capture-only-v"
LABEL_PREFIX = f"{LAUNCHD_PREFIX}4-"
WATCHDOG_LABEL_PREFIX = "ai.memecoin.brain.senses.gecko-canary-watchdog-v2-"


class ReleaseError(ValueError):
    """Invalid v4 release, launchd identity, or storage precondition."""


@contextlib.contextmanager
def _v4_source_scope() -> Iterator[None]:
    """Bind the v4 diagnostic census only within this synchronous v4 call."""
    previous = tuple(v2.RELEASE_CODE_PATHS)
    previous_inventory = v2.usage_inventory
    vars(v2)["RELEASE_CODE_PATHS"] = tuple(dict.fromkeys((*previous, *RELEASE_CODE_PATHS)))
    vars(v2)["usage_inventory"] = usage_inventory_v2
    try:
        yield
    finally:
        vars(v2)["RELEASE_CODE_PATHS"] = previous
        vars(v2)["usage_inventory"] = previous_inventory


def compile_release_material(writer: DescriptorWriter, guard: Any) -> dict[str, Any]:
    with _v4_source_scope():
        return v2.compile_release_material(writer, guard)


def first_bucket_close(release: Mapping[str, Any]) -> int:
    records = release["journal"].records()
    first = records[0].get("first_bucket_close_ns") if records else None
    if type(first) is not int or first <= 0:
        raise ReleaseError("first_bucket_close_invalid")
    return first


def verify_prepared_release(
    writer: DescriptorWriter, guard: Any, release_id: str
) -> dict[str, Any]:
    with _v4_source_scope():
        return v2.verify_prepared_release(writer, guard, release_id)


def render_launchd_plist(release_id: str) -> bytes:
    if len(release_id) != 32 or any(ch not in "0123456789abcdef" for ch in release_id):
        raise ReleaseError("release_id_invalid")
    return plistlib.dumps(
        {
            "Label": f"{LABEL_PREFIX}{release_id}",
            "ProgramArguments": [
                str(REPO / ".venv/bin/python"),
                "-B",
                str(Path(__file__).resolve()),
                "--scheduled-tick",
                "--release-id",
                release_id,
            ],
            "WorkingDirectory": str(REPO),
            "StartInterval": 12,
            "RunAtLoad": True,
            "KeepAlive": False,
            "ProcessType": "Background",
            "StandardOutPath": "/dev/null",
            "StandardErrorPath": "/dev/null",
        },
        fmt=plistlib.FMT_XML,
        sort_keys=True,
    )


def launchd_label(release_id: str) -> str:
    if len(release_id) != 32 or any(ch not in "0123456789abcdef" for ch in release_id):
        raise ReleaseError("release_id_invalid")
    return f"{LABEL_PREFIX}{release_id}"


def launchd_agent_path(release_id: str) -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{launchd_label(release_id)}.plist"


def verify_launchd_loaded(release_id: str) -> str:
    label = launchd_label(release_id)
    labels = v2.loaded_launchd_labels()
    active = {item for item in labels if item.startswith(LAUNCHD_PREFIX)}
    if active != {label}:
        raise ReleaseError("launchd_label_state_invalid")
    path = launchd_agent_path(release_id)
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise ReleaseError("launchd_plist_file_unsafe")
    raw = path.read_bytes()
    if raw != render_launchd_plist(release_id):
        raise ReleaseError("launchd_plist_binding_changed")
    return hashlib.sha256(raw).hexdigest()


def verify_watchdog_loaded(release_id: str, bucket_close_ns: int) -> str:
    """Require the independent one-bucket watchdog before every scheduled callback."""
    label = f"{WATCHDOG_LABEL_PREFIX}{release_id}"
    if label not in v2.loaded_launchd_labels():
        raise ReleaseError("canary_watchdog_not_loaded")
    path = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise ReleaseError("canary_watchdog_plist_unsafe")
    raw = path.read_bytes()
    try:
        data = plistlib.loads(raw)
    except plistlib.InvalidFileException as exc:
        raise ReleaseError("canary_watchdog_plist_invalid") from exc
    args = data.get("ProgramArguments") if isinstance(data, Mapping) else None
    if (
        not isinstance(data, Mapping)
        or data.get("Label") != label
        or data.get("StartInterval") != 15
        or data.get("RunAtLoad") is not True
        or type(data.get("KeepAlive")) is not dict
        or not isinstance(data.get("EnvironmentVariables"), Mapping)
        or data["EnvironmentVariables"].get("SENSES_CANARY_DEADLINE_NS")
        != str(bucket_close_ns + 300_000_000_000)
        or not isinstance(args, list)
        or str(REPO / "tools/brain_senses_gecko_canary_watchdog_v2.py") not in args
        or "--release-id" not in args
        or args.index("--release-id") + 1 >= len(args)
        or args[args.index("--release-id") + 1] != release_id
        or "--bucket-close-ns" not in args
        or args.index("--bucket-close-ns") + 1 >= len(args)
        or args[args.index("--bucket-close-ns") + 1] != str(bucket_close_ns)
    ):
        raise ReleaseError("canary_watchdog_plist_binding_changed")
    return hashlib.sha256(raw).hexdigest()


def install_launchd_agent(release_id: str) -> dict[str, Any]:
    label = launchd_label(release_id)
    competing = {
        item
        for item in v2.loaded_launchd_labels()
        if item.startswith(LAUNCHD_PREFIX) and item != label
    }
    if competing:
        raise ReleaseError("competing_gecko_launchd_agent_loaded")
    path = launchd_agent_path(release_id)
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    parent = path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid():
        raise ReleaseError("launchd_directory_unsafe")
    raw = render_launchd_plist(release_id)
    if path.exists():
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or path.read_bytes() != raw
        ):
            raise ReleaseError("launchd_plist_file_unsafe")
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            view = memoryview(raw)
            while view:
                count = os.write(fd, view)
                if count <= 0:
                    raise ReleaseError("launchd_plist_write_failed")
                view = view[count:]
            os.fsync(fd)
        finally:
            os.close(fd)
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0 and label_not_loaded(release_id):
        raise ReleaseError("launchd_bootstrap_failed")
    return {
        "label": launchd_label(release_id),
        "plist_path": str(path),
        "plist_sha256": verify_launchd_loaded(release_id),
        "start_interval_seconds": 12,
        "provider_io": False,
    }


def label_not_loaded(release_id: str) -> bool:
    return launchd_label(release_id) not in v2.loaded_launchd_labels()


def prepare_from_owner_host() -> dict[str, Any]:
    with writer_guard(CONFIG_PATH) as guard:
        guard.revalidate()
        data_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)

        def before_write() -> None:
            revalidate_descriptor_write(guard)

        try:
            with DescriptorWriter(data_fd, before_write=before_write) as writer:
                with _v4_source_scope():
                    material = compile_release_material(writer, guard)
                    from tools import brain_senses_gecko_lane_runtime_v4 as lane_runtime

                    capacity_coordinator = lane_runtime.make_capacity_coordinator(
                        writer, guard, now_ns=time.time_ns
                    )
                    observed = time.time_ns()
                    first_close = (observed // scout.BUCKET_NS + 1) * scout.BUCKET_NS
                    receipt = v2.initialize_release(
                        writer,
                        policy=material["policy"],
                        release_id=material["release_id"],
                        release_sha256=material["release_sha256"],
                        source_sha256=material["source_sha256"],
                        first_bucket_close_ns=first_close,
                        coordinator=capacity_coordinator,
                        now_ns=time.time_ns,
                        monotonic_ns=time.monotonic_ns,
                    )
                guard.revalidate()
                return {
                    **receipt,
                    "source_bindings": material["source_bindings"],
                    "provider_io": False,
                    "activation_authorized": False,
                }
        finally:
            os.close(data_fd)


def _fetch_with_full_revalidation(
    guard: Any,
    provider_fetch: Callable[[Mapping[str, Any]], service.FetchResult] = service.fetch_once,
) -> Callable[[Mapping[str, Any]], service.FetchResult]:
    def fetch_once(request: Mapping[str, Any]) -> service.FetchResult:
        guard.revalidate()
        return provider_fetch(request)

    return fetch_once


@contextlib.contextmanager
def _defer_first_open_health_publish(release: Mapping[str, Any]) -> Iterator[None]:
    """Skip the pre-dispatch full census; admission and terminal health still replay fresh."""
    original = v2.publish_health
    deferred = False

    def publish_health(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal deferred
        if not deferred and kwargs.get("status") == "healthy":
            deferred = True
            snapshot = release.get("health")
            if not isinstance(snapshot, Mapping):
                raise ReleaseError("pre_dispatch_health_snapshot_missing")
            return dict(snapshot)
        return original(*args, **kwargs)

    vars(v2)["publish_health"] = publish_health
    try:
        yield
    finally:
        vars(v2)["publish_health"] = original


def run_callback(
    writer: DescriptorWriter,
    *,
    guard: Any,
    release: Mapping[str, Any],
    coordinator: capacity.CapacityCoordinator,
    now_ns: Callable[[], int] = time.time_ns,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    clock_probe: Callable[[], dict[str, Any]] = external_clock.probe_pair,
) -> dict[str, Any]:
    guard.revalidate()
    with _v4_source_scope():
        with _defer_first_open_health_publish(release):
            result = v2.run_callback(
                writer,
                release=release,
                coordinator=coordinator,
                now_ns=now_ns,
                monotonic_ns=monotonic_ns,
                clock_probe=clock_probe,
                fetch_once=_fetch_with_full_revalidation(guard),
            )
    guard.revalidate()
    return result


def run_from_owner_host(release_id: str) -> dict[str, Any]:
    from tools import brain_senses_gecko_lane_runtime_v4 as lane_runtime

    with writer_guard(CONFIG_PATH) as guard:
        guard.revalidate()
        data_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)

        def before_write() -> None:
            revalidate_descriptor_write(guard)

        try:
            with DescriptorWriter(data_fd, before_write=before_write) as writer:
                coordinator = lane_runtime.make_capacity_coordinator(
                    writer, guard, now_ns=time.time_ns
                )
                release = verify_prepared_release(writer, guard, release_id)
                verify_launchd_loaded(release_id)
                verify_watchdog_loaded(release_id, first_bucket_close(release))
                return run_callback(
                    writer,
                    guard=guard,
                    release=release,
                    coordinator=coordinator,
                )
        finally:
            os.close(data_fd)


def install_from_owner_host(release_id: str) -> dict[str, Any]:
    from tools import brain_senses_gecko_lane_runtime_v4 as lane_runtime

    with writer_guard(CONFIG_PATH) as guard:
        guard.revalidate()
        data_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)

        def before_write() -> None:
            revalidate_descriptor_write(guard)

        try:
            with DescriptorWriter(data_fd, before_write=before_write) as writer:
                lane_runtime.make_capacity_coordinator(writer, guard, now_ns=time.time_ns)
                release = verify_prepared_release(writer, guard, release_id)
                guard.revalidate()
                verify_watchdog_loaded(release_id, first_bucket_close(release))
                return install_launchd_agent(release_id)
        finally:
            os.close(data_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare-release", action="store_true")
    modes.add_argument("--scheduled-tick", action="store_true")
    modes.add_argument("--install-launchd", action="store_true")
    parser.add_argument("--release-id")
    args = parser.parse_args(argv)
    if args.prepare_release == (args.release_id is not None):
        parser.error("--release-id is required only for scheduled/install modes")
    try:
        result = (
            prepare_from_owner_host()
            if args.prepare_release
            else run_from_owner_host(args.release_id)
            if args.scheduled_tick
            else install_from_owner_host(args.release_id)
        )
    except Exception as exc:
        reason = exc.args[0] if exc.args and isinstance(exc.args[0], str) else type(exc).__name__
        print(f'{{"status":"failed_closed","reason":"{reason}"}}')
        return 2
    import json

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
