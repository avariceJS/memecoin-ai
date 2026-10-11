from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

import memecoin_research_storage as research_storage
from memecoin_research_storage import (
    ROOT_MARKER,
    StorageConfig,
    StorageError,
    VolumeInfo,
    configure,
    create_root,
    resolve,
    verify_root,
    writer_guard,
)

UUID = "11111111-2222-3333-4444-555555555555"


class FakeProbe:
    def __init__(self, mount: Path, **changes: object) -> None:
        self.mount = mount
        self.changes = changes
        self.calls = 0

    def inspect(self, mount_path: Path) -> VolumeInfo:
        assert mount_path == self.mount
        self.calls += 1
        values: dict[str, object] = {
            "mount_path": self.mount,
            "volume_uuid": UUID,
            "filesystem": "Case-sensitive APFS",
            "filesystem_type": "apfs",
            "owners_enabled": True,
            "read_only": False,
            "available_bytes": 10_000,
            "is_mountpoint": True,
            "mount_options": ("apfs", "local", "journaled"),
        }
        for key, value in self.changes.items():
            values[key] = value(self.calls) if callable(value) else value
        return VolumeInfo(**values)  # type: ignore[arg-type]


def make_binding(
    tmp_path: Path, *, data_root: str = "research/root", min_free_bytes: int = 100
) -> tuple[Path, Path, StorageConfig]:
    mount = tmp_path / "external"
    mount.mkdir()
    config_path = tmp_path / "host-config.json"
    config = StorageConfig("a" * 32, UUID, mount, data_root, min_free_bytes)
    config_path.write_text(json.dumps(config.to_json()), encoding="utf-8")
    config_path.chmod(0o600)
    return config_path, mount, config


def test_resolve_uses_logical_identity_and_relative_data_root(tmp_path: Path) -> None:
    config_path, mount, config = make_binding(tmp_path)
    resolved = resolve(config_path, probe=FakeProbe(mount))
    assert resolved.root_id == config.root_id
    assert resolved.volume_uuid == UUID
    assert resolved.path == mount / "research/root"
    assert resolved.available_bytes == 10_000


def test_read_only_resolve_does_not_create_a_probe_file(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    before = set(mount.iterdir())
    resolve(config_path, probe=FakeProbe(mount))
    assert set(mount.iterdir()) == before


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("is_mountpoint", False, "mountpoint_directory_fallback"),
        ("mount_path", Path("/elsewhere"), "mountpoint_directory_fallback"),
        ("volume_uuid", "wrong", "volume_uuid_mismatch"),
        ("owners_enabled", False, "volume_owners_disabled"),
        ("read_only", True, "volume_not_writable"),
        ("filesystem", "APFS", "filesystem_not_case_sensitive_apfs"),
        ("filesystem_type", "exfat", "filesystem_type_not_apfs"),
        ("mount_options", ("apfs", "noowners"), "unsafe_mount_options"),
        ("available_bytes", 99, "insufficient_free_space"),
    ],
)
def test_volume_preconditions_fail_closed(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    with pytest.raises(StorageError, match=message):
        resolve(config_path, probe=FakeProbe(mount, **{field: value}))


def test_missing_mount_and_symlink_mount_are_rejected(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    mount.rmdir()
    with pytest.raises(StorageError, match="mount_missing"):
        resolve(config_path, probe=FakeProbe(mount))
    mount.mkdir()
    symlink = tmp_path / "alias"
    symlink.symlink_to(mount)
    payload = json.loads(config_path.read_text())
    payload["mount_path"] = str(symlink)
    config_path.write_text(json.dumps(payload))
    with pytest.raises(StorageError, match="symlink_path_rejected"):
        resolve(config_path, probe=FakeProbe(symlink))


def test_config_owner_mode_and_relative_path_validation(tmp_path: Path) -> None:
    config_path, mount, config = make_binding(tmp_path)
    config_path.chmod(0o640)
    with pytest.raises(StorageError, match="unsafe_config_permissions"):
        StorageConfig.load(config_path)
    config_path.chmod(0o600)
    for unsafe in ("/absolute", "../escape", "a/../b", "a//b", "a\\b"):
        payload = config.to_json()
        payload["data_root"] = unsafe
        config_path.write_text(json.dumps(payload))
        config_path.chmod(0o600)
        with pytest.raises(StorageError):
            resolve(config_path, probe=FakeProbe(mount))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("volume_uuid", "not-a-uuid", "invalid_volume_uuid"),
        ("mount_path", "relative/path", "invalid_mount_path"),
        ("min_free_bytes", -1, "invalid_min_free_bytes"),
    ],
)
def test_config_inputs_are_strictly_validated(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    config_path, mount, config = make_binding(tmp_path)
    payload = config.to_json()
    payload[field] = value
    config_path.write_text(json.dumps(payload))
    config_path.chmod(0o600)
    with pytest.raises(StorageError, match=message):
        StorageConfig.load(config_path)


def test_configure_qualifies_volume_outside_read_only_check(tmp_path: Path) -> None:
    mount = tmp_path / "external"
    mount.mkdir()
    config_path = tmp_path / "private" / "binding.json"
    probe = FakeProbe(mount)
    config = configure(
        config_path,
        mount,
        "research/root",
        min_free_bytes=100,
        volume_uuid=UUID,
        probe=probe,
    )
    assert config_path.is_file()
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(config_path.parent.stat().st_mode) == 0o700
    assert list(mount.iterdir()) == []
    assert probe.calls == 3
    assert config.volume_uuid == UUID


def test_configure_rejects_negative_headroom_before_disk_probe(tmp_path: Path) -> None:
    mount = tmp_path / "external"
    mount.mkdir()
    with pytest.raises(StorageError, match="invalid_min_free_bytes"):
        configure(
            tmp_path / "private" / "binding.json",
            mount,
            "research/root",
            min_free_bytes=-1,
            volume_uuid=UUID,
            probe=FakeProbe(mount),
        )


def test_symlink_in_data_root_path_is_rejected(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    (mount / "research").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(StorageError, match="symlink_root_component"):
        resolve(config_path, probe=FakeProbe(mount))


def test_config_symlink_is_rejected(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    alias = tmp_path / "config-link"
    alias.symlink_to(config_path)
    with pytest.raises(StorageError, match="symlink_path_rejected"):
        resolve(alias, probe=FakeProbe(mount))


def test_create_root_writes_private_marker_and_refuses_existing_root(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    probe = FakeProbe(mount)
    resolved = create_root(config_path, probe=probe)
    marker = resolved.path / ROOT_MARKER
    assert marker.is_file()
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600
    assert json.loads(marker.read_text()) == {
        "version": 1,
        "root_id": "a" * 32,
        "volume_uuid": UUID,
        "data_root": "research/root",
    }
    assert verify_root(config_path, probe=probe) == resolved
    with pytest.raises(StorageError, match="research_root_already_exists"):
        create_root(config_path, probe=probe)


def test_root_marker_binding_is_verified(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    probe = FakeProbe(mount)
    resolved = create_root(config_path, probe=probe)
    marker = resolved.path / ROOT_MARKER
    marker.write_text(json.dumps({"version": 1, "root_id": "b" * 32, "volume_uuid": UUID}))
    marker.chmod(0o600)
    with pytest.raises(StorageError, match="root_marker_binding_mismatch"):
        verify_root(config_path, probe=probe)
    marker.write_text(json.dumps({"version": 1, "root_id": "a" * 32, "volume_uuid": UUID}))
    marker.chmod(0o644)
    with pytest.raises(StorageError, match="unsafe_root_marker"):
        verify_root(config_path, probe=probe)


def test_marker_is_bound_to_relative_data_root_and_root_mode(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    probe = FakeProbe(mount)
    resolved = create_root(config_path, probe=probe)
    other = mount / "research/other"
    other.mkdir(mode=0o700)
    (other / ROOT_MARKER).write_bytes((resolved.path / ROOT_MARKER).read_bytes())
    (other / ROOT_MARKER).chmod(0o600)
    payload = json.loads(config_path.read_text())
    payload["data_root"] = "research/other"
    config_path.write_text(json.dumps(payload))
    config_path.chmod(0o600)
    with pytest.raises(StorageError, match="root_marker_binding_mismatch"):
        verify_root(config_path, probe=probe)
    payload["data_root"] = "research/root"
    config_path.write_text(json.dumps(payload))
    config_path.chmod(0o600)
    resolved.path.chmod(0o755)
    with pytest.raises(StorageError, match="unsafe_root_owner_or_mode"):
        verify_root(config_path, probe=probe)


def test_existing_root_ancestors_must_be_private(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    probe = FakeProbe(mount)
    resolved = create_root(config_path, probe=probe)
    (mount / "research").chmod(0o755)
    with pytest.raises(StorageError, match="unsafe_root_owner_or_mode"):
        verify_root(config_path, probe=probe)
    (mount / "research").chmod(0o700)
    assert verify_root(config_path, probe=probe) == resolved


def test_marker_read_rejects_changed_file_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, mount, config = make_binding(tmp_path)
    resolved = create_root(config_path, probe=FakeProbe(mount))
    marker = resolved.path / ROOT_MARKER
    marker_fd = os.open(marker, os.O_RDONLY)
    original_read = os.read
    changed = False

    def mutate_then_read(fd: int, size: int) -> bytes:
        nonlocal changed
        if fd == marker_fd and not changed:
            changed = True
            st = marker.stat()
            os.utime(marker, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        return original_read(fd, size)

    monkeypatch.setattr(research_storage.os, "read", mutate_then_read)
    try:
        with pytest.raises(StorageError, match="root_marker_changed_during_read"):
            research_storage._read_marker_fd(marker_fd, config)
    finally:
        os.close(marker_fd)


def test_writer_guard_rechecks_volume_under_lock(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    create_root(config_path, probe=FakeProbe(mount))
    probe = FakeProbe(mount, volume_uuid=lambda n: UUID if n == 1 else "replaced")
    with pytest.raises(StorageError, match="volume_uuid_mismatch"):
        with writer_guard(config_path, probe=probe):
            pytest.fail("guard yielded after volume identity changed")
    assert probe.calls == 2


def test_normal_writer_guard_requires_root_before_creating_lock(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    with pytest.raises(StorageError, match="research_root_missing"):
        with writer_guard(config_path, probe=FakeProbe(mount)):
            pytest.fail("uninitialized root was accepted")
    assert not (mount / ".research-storage.lock").exists()


def test_readonly_mount_replacement_between_initial_check_and_lock_fails(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    create_root(config_path, probe=FakeProbe(mount))
    probe = FakeProbe(mount, read_only=lambda n: n > 1)
    with pytest.raises(StorageError, match="volume_not_writable"):
        with writer_guard(config_path, probe=probe):
            pytest.fail("guard yielded after mount became read-only")


def test_lock_with_unsafe_mode_is_rejected_without_chmod(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    create_root(config_path, probe=FakeProbe(mount))
    lock = mount / ".research-storage.lock"
    lock.chmod(0o644)
    with pytest.raises(StorageError, match="unsafe_writer_lock"):
        with writer_guard(config_path, probe=FakeProbe(mount)):
            pytest.fail("unsafe lock was accepted")
    assert stat.S_IMODE(lock.stat().st_mode) == 0o644


def test_guard_rejects_same_uuid_mount_replacement_and_config_change(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    create_root(config_path, probe=FakeProbe(mount))
    moved = tmp_path / "old-volume"
    with writer_guard(config_path, probe=FakeProbe(mount)) as guard:
        mount.rename(moved)
        mount.mkdir()
        with pytest.raises(StorageError, match="mounted_directory_replaced"):
            guard.revalidate()
    mount.rmdir()
    moved.rename(mount)
    with writer_guard(config_path, probe=FakeProbe(mount)) as guard:
        payload = json.loads(config_path.read_text())
        payload["data_root"] = "research/other"
        config_path.write_text(json.dumps(payload))
        config_path.chmod(0o600)
        with pytest.raises(StorageError, match="binding_changed_under_lock"):
            guard.revalidate()


def test_guard_creates_only_safe_relative_private_directories(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    create_root(config_path, probe=FakeProbe(mount))
    with writer_guard(config_path, probe=FakeProbe(mount)) as guard:
        path = guard.create_directory("captures/day-1")
        assert path.is_dir()
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
        with pytest.raises(StorageError):
            guard.create_directory("captures/day-1/../escape")


def test_guard_rejects_replaced_lock_inode(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    create_root(config_path, probe=FakeProbe(mount))
    with writer_guard(config_path, probe=FakeProbe(mount)) as guard:
        lock = mount / ".research-storage.lock"
        lock.unlink()
        lock.write_text("replacement")
        lock.chmod(0o600)
        with pytest.raises(StorageError, match="writer_lock_replaced"):
            guard.revalidate()
