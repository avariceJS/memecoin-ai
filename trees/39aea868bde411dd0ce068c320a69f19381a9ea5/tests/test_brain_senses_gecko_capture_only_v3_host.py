from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

import memecoin_research_storage as storage
from memecoin_research_storage import (
    StorageConfig,
    StorageError,
    VolumeInfo,
    _open_relative_directory,
    create_root,
    writer_guard,
)
from memecoin_research_storage.live_writer import revalidate_descriptor_write
from tools import brain_senses_gecko_canary_watchdog_v1 as watchdog
from tools import brain_senses_gecko_capture_only_v3_host as v3
from tools.brain_senses_external_writer_v1 import DescriptorWriter

UUID = "11111111-2222-3333-4444-555555555555"


class _Probe:
    def __init__(self, mount: Path) -> None:
        self.mount = mount
        self.calls = 0

    def inspect(self, mount_path: Path) -> VolumeInfo:
        assert mount_path == self.mount
        self.calls += 1
        return VolumeInfo(
            mount_path=self.mount,
            volume_uuid=UUID,
            filesystem="Case-sensitive APFS",
            filesystem_type="apfs",
            owners_enabled=True,
            read_only=False,
            available_bytes=1_000_000,
            is_mountpoint=True,
            mount_options=("apfs", "local", "journaled"),
        )


def _prepared_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mount = tmp_path / "volume"
    mount.mkdir()
    config_path = tmp_path / "storage.json"
    config = StorageConfig("a" * 32, UUID, mount, "data/root", 100)
    config_path.write_text(json.dumps(config.to_json()), encoding="utf-8")
    config_path.chmod(0o600)
    probe = _Probe(mount)
    create_root(config_path, probe=probe)
    monkeypatch.setattr(
        storage.os.path,
        "ismount",
        lambda path: Path(path) == mount and mount.exists(),
    )
    return config_path, mount, probe


def _detach_mount(mount: Path, detached: Path) -> None:
    os.rename(mount, detached)
    mount.mkdir(mode=0o700)
    sentinel = mount / "underlay-sentinel"
    sentinel.write_bytes(b"unchanged")
    sentinel.chmod(0o600)


def test_live_writer_guard_uses_fast_identity_validation_only() -> None:
    class FakeGuard:
        def __init__(self) -> None:
            self.identity_calls = 0
            self.full_calls = 0

        def revalidate_identity(self) -> str:
            self.identity_calls += 1
            return "held-root"

        def revalidate(self) -> None:
            self.full_calls += 1
            raise AssertionError("full probe must not run for each descriptor mutation")

    guard = FakeGuard()
    assert revalidate_descriptor_write(guard) == "held-root"  # type: ignore[arg-type]
    assert guard.identity_calls == 1
    assert guard.full_calls == 0


@pytest.mark.parametrize("operation", ["mkdir", "create", "append", "atomic_rename"])
def test_fast_guarded_mutation_after_detach_never_targets_underlay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    config_path, mount, probe = _prepared_guard(tmp_path, monkeypatch)
    detached = tmp_path / "detached-volume"
    with writer_guard(config_path, probe=probe) as guard:
        root_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)
        root = detached / "data" / "root"
        try:
            if operation == "append":
                seed = root_fd
                fd = os.open(
                    "ledger.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=seed
                )
                os.write(fd, b"prior\n")
                os.close(fd)
            if operation == "atomic_rename":
                fd = os.open(
                    "state.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=root_fd
                )
                os.write(fd, b"old")
                os.close(fd)
            detached_once = False

            def fault_hook(name: str) -> None:
                nonlocal detached_once
                trigger = "append_open" if operation == "append" else operation
                if name == trigger and not detached_once:
                    detached_once = True
                    _detach_mount(mount, detached)

            def before_write() -> None:
                revalidate_descriptor_write(guard)

            with DescriptorWriter(
                root_fd,
                before_write=before_write,
                fault_hook=fault_hook,
            ) as writer:
                if operation == "mkdir":
                    writer.mkdir("created")
                elif operation == "create":
                    writer.create("created.json", b"payload")
                elif operation == "append":
                    with pytest.raises(StorageError, match="mounted_directory_replaced"):
                        writer.append("ledger.jsonl", b"next\n")
                else:
                    writer.atomic_write("state.json", b"new")
            assert detached_once
            assert (mount / "underlay-sentinel").read_bytes() == b"unchanged"
            if operation == "mkdir":
                assert (root / "created").is_dir()
            elif operation == "create":
                assert (root / "created.json").read_bytes() == b"payload"
            elif operation == "append":
                assert (root / "ledger.jsonl").read_bytes() == b"prior\n"
            else:
                assert (root / "state.json").read_bytes() == b"new"
            assert list(mount.iterdir()) == [mount / "underlay-sentinel"]
        finally:
            os.close(root_fd)


def test_detach_before_fast_guard_fails_closed_without_underlay_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, mount, probe = _prepared_guard(tmp_path, monkeypatch)
    detached = tmp_path / "detached-volume"
    with writer_guard(config_path, probe=probe) as guard:
        root_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)
        try:
            _detach_mount(mount, detached)

            def before_write() -> None:
                revalidate_descriptor_write(guard)

            with DescriptorWriter(
                root_fd,
                before_write=before_write,
            ) as writer:
                with pytest.raises(StorageError, match="mounted_directory_replaced"):
                    writer.create("forbidden.json", b"payload")
            assert not (mount / "data").exists()
            assert (mount / "underlay-sentinel").read_bytes() == b"unchanged"
        finally:
            os.close(root_fd)


def test_provider_dispatch_runs_full_validation_and_fails_closed_before_fetch() -> None:
    events: list[str] = []

    class Guard:
        def __init__(self, fail: bool = False) -> None:
            self.fail = fail

        def revalidate(self) -> None:
            events.append("full")
            if self.fail:
                raise StorageError("mounted_directory_replaced")

    request: dict[str, Any] = {"request_id": "test"}

    def fetch(_: Any) -> str:
        events.append("provider")
        return "response"

    assert v3._fetch_with_full_revalidation(Guard(), fetch)(request) == "response"
    assert events == ["full", "provider"]
    events.clear()
    with pytest.raises(StorageError, match="mounted_directory_replaced"):
        v3._fetch_with_full_revalidation(Guard(fail=True), fetch)(request)
    assert events == ["full"]


def test_open_window_defers_only_pre_dispatch_health_census(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def publish_health(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(str(kwargs["status"]))
        return {"status": kwargs["status"]}

    monkeypatch.setattr(v3.v2, "publish_health", publish_health)
    release = {"health": {"status": "paused", "request_count": 3}}
    with v3._defer_first_open_health_publish(release):
        preflight = v3.v2.publish_health(status="healthy")
        terminal = v3.v2.publish_health(status="healthy")
    assert preflight == release["health"]
    assert terminal == {"status": "healthy"}
    assert calls == ["healthy"]
    assert v3.v2.publish_health is publish_health


def test_v3_source_scope_is_local_and_pins_both_new_interfaces() -> None:
    original = v3.v2.RELEASE_CODE_PATHS
    with v3._v3_source_scope():
        assert "tools/brain_senses_gecko_capture_only_v3_host.py" in v3.v2.RELEASE_CODE_PATHS
        assert "tools/brain_senses_gecko_canary_watchdog_v1.py" in v3.v2.RELEASE_CODE_PATHS
        assert "src/memecoin_research_storage/live_writer.py" in v3.v2.RELEASE_CODE_PATHS
    assert v3.v2.RELEASE_CODE_PATHS == original


def test_install_refuses_to_overlap_the_active_v2_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "gecko-v3.plist"
    monkeypatch.setattr(v3, "launchd_agent_path", lambda _release_id: path)
    monkeypatch.setattr(
        v3.v2,
        "loaded_launchd_labels",
        lambda: {"ai.memecoin.brain.senses.gecko-capture-only-v2-old"},
    )
    with pytest.raises(v3.ReleaseError, match="competing_gecko_launchd_agent_loaded"):
        v3.install_launchd_agent("a" * 32)
    assert not path.exists()


def test_v3_requires_the_matching_finite_canary_watchdog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release_id = "a" * 32
    close_ns = 1_800_000_000_000_000_000
    label = f"ai.memecoin.brain.senses.gecko-canary-watchdog-v1-{release_id}"
    path = tmp_path / "Library/LaunchAgents" / f"{label}.plist"
    path.parent.mkdir(parents=True)
    path.write_bytes(watchdog.render_launchd_plist(release_id, close_ns))
    path.chmod(0o600)
    monkeypatch.setattr(v3.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(v3.v2, "loaded_launchd_labels", lambda: {label})

    digest = v3.verify_watchdog_loaded(release_id, close_ns)
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(v3.ReleaseError, match="canary_watchdog_plist_binding_changed"):
        v3.verify_watchdog_loaded(release_id, close_ns + 1)
