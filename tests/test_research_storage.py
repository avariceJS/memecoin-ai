from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import memecoin_research_storage as research_storage
from memecoin_research_storage import (
    ROOT_MARKER,
    StorageConfig,
    StorageError,
    VolumeInfo,
    bind_existing,
    configure,
    create_root,
    read_only_guard,
    resolve,
    verify_root,
    writer_guard,
)

UUID = "11111111-2222-3333-4444-555555555555"
ROOT_ID = "a" * 32


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


def test_read_only_guard_accepts_readonly_low_space_without_creating_lock(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path, min_free_bytes=100)
    resolved = create_root(config_path, probe=FakeProbe(mount))
    lock_path = mount / research_storage.LOCK_NAME
    lock_path.unlink()
    before = sorted(path.relative_to(mount).as_posix() for path in mount.rglob("*"))
    probe = FakeProbe(
        mount,
        read_only=True,
        available_bytes=1,
        mount_options=("apfs", "local", "ro"),
    )

    with read_only_guard(config_path, probe=probe) as guard:
        assert guard.path == resolved.path
        assert guard.available_bytes == 1
        assert guard.revalidate().path == resolved.path

    assert not lock_path.exists()
    assert sorted(path.relative_to(mount).as_posix() for path in mount.rglob("*")) == before
    assert probe.calls >= 2


def test_read_only_guard_fails_closed_on_volume_and_marker_mismatch(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    resolved = create_root(config_path, probe=FakeProbe(mount))
    with pytest.raises(StorageError, match="volume_uuid_mismatch"):
        with read_only_guard(config_path, probe=FakeProbe(mount, volume_uuid="wrong")):
            pytest.fail("wrong volume was accepted")

    marker = resolved.path / ROOT_MARKER
    marker.write_text("{}", encoding="utf-8")
    marker.chmod(0o600)
    with pytest.raises(StorageError, match="root_marker_binding_mismatch"):
        with read_only_guard(config_path, probe=FakeProbe(mount)):
            pytest.fail("wrong root marker was accepted")


def test_read_only_guard_rejects_replaced_root_descriptor(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    resolved = create_root(config_path, probe=FakeProbe(mount))
    with read_only_guard(config_path, probe=FakeProbe(mount)) as guard:
        displaced = mount / "research" / "old-root"
        resolved.path.rename(displaced)
        resolved.path.mkdir(mode=0o700)
        (resolved.path / ROOT_MARKER).write_bytes((displaced / ROOT_MARKER).read_bytes())
        (resolved.path / ROOT_MARKER).chmod(0o600)
        with pytest.raises(StorageError, match="research_root_replaced"):
            guard.revalidate()


def test_read_only_guard_rejects_mountpoint_substitution(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    create_root(config_path, probe=FakeProbe(mount))
    with read_only_guard(config_path, probe=FakeProbe(mount)) as guard:
        displaced = tmp_path / "displaced-mount"
        mount.rename(displaced)
        mount.mkdir(mode=0o700)
        shutil.copytree(displaced / "research", mount / "research")
        with pytest.raises(StorageError, match="mounted_directory_replaced"):
            guard.revalidate()


def test_read_only_descriptor_reader_rejects_every_mutator_before_changes(tmp_path: Path) -> None:
    from tools.brain_senses_external_writer_v1 import DescriptorWriter

    config_path, mount, _ = make_binding(tmp_path, min_free_bytes=100)
    resolved = create_root(config_path, probe=FakeProbe(mount))
    lock_path = mount / research_storage.LOCK_NAME
    lock_path.unlink()
    probe = FakeProbe(
        mount,
        read_only=True,
        available_bytes=1,
        mount_options=("apfs", "local", "ro"),
    )
    before = sorted(path.relative_to(resolved.path).as_posix() for path in resolved.path.rglob("*"))

    with read_only_guard(config_path, probe=probe) as guard:
        with DescriptorWriter.read_only_from_storage_guard(guard) as reader:
            assert reader.read_bytes(ROOT_MARKER)
            operations = (
                lambda: reader.mkdir("new-dir"),
                lambda: reader.open_directory("new-dir", create=True),
                lambda: reader.create("new-file", b"x"),
                lambda: reader.atomic_write("new-atomic", b"x"),
                lambda: reader.atomic_write_stream("new-stream", [b"x"], max_bytes=1),
                lambda: reader.append("new-append", b"x"),
                lambda: reader.unlink(ROOT_MARKER),
                lambda: reader.unlink("missing", missing_ok=True),
                lambda: reader.lock("new-lock").__enter__(),
            )
            for operation in operations:
                with pytest.raises(StorageError, match="read_only_storage_guard_write_forbidden"):
                    operation()

    assert not lock_path.exists()
    assert (
        sorted(path.relative_to(resolved.path).as_posix() for path in resolved.path.rglob("*"))
        == before
    )


def test_read_only_and_writable_factories_reject_opposite_guard_types(tmp_path: Path) -> None:
    from tools.brain_senses_external_writer_v1 import DescriptorWriter

    config_path, mount, _ = make_binding(tmp_path)
    create_root(config_path, probe=FakeProbe(mount))

    with read_only_guard(config_path, probe=FakeProbe(mount)) as reader_guard:
        with pytest.raises(StorageError, match="bound_writer_requires_initialized_storage_guard"):
            DescriptorWriter.from_storage_guard(reader_guard)
        with pytest.raises(StorageError, match="read_only_guard_requires_reader_factory"):
            DescriptorWriter(reader_guard.root_fd, storage_guard=reader_guard)

    with writer_guard(config_path, probe=FakeProbe(mount)) as write_guard:
        with pytest.raises(StorageError, match="bound_reader_requires_read_only_storage_guard"):
            DescriptorWriter.read_only_from_storage_guard(write_guard)  # type: ignore[arg-type]


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


def test_tagged_linux_config_v2_round_trips_without_changing_macos_v1(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    payload = {
        "version": 2,
        "platform": "linux",
        "filesystem": "ext4",
        "root_id": ROOT_ID,
        "fs_uuid": UUID,
        "mount_path": str(mount),
        "data_root": "research/root",
        "min_free_bytes": 100,
    }
    config_path.write_text(json.dumps(payload))
    config_path.chmod(0o600)
    config = StorageConfig.load(config_path)
    assert config.platform == "linux"
    assert config.filesystem == "ext4"
    assert config.version == 2
    assert config.to_json() == payload


@pytest.mark.parametrize(
    ("field", "value"),
    [("platform", "darwin"), ("filesystem", "xfs")],
)
def test_tagged_linux_config_rejects_unsupported_adapter(field, value, tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    payload = {
        "version": 2,
        "platform": "linux",
        "filesystem": "ext4",
        "root_id": ROOT_ID,
        "fs_uuid": UUID,
        "mount_path": str(mount),
        "data_root": "research/root",
        "min_free_bytes": 100,
    }
    payload[field] = value
    config_path.write_text(json.dumps(payload))
    config_path.chmod(0o600)
    with pytest.raises(StorageError, match="unsupported_storage_platform_or_filesystem"):
        StorageConfig.load(config_path)


def test_linux_mountinfo_parser_decodes_mountpoint_and_keeps_identity_fields() -> None:
    records = research_storage._parse_linux_mountinfo(
        "42 1 7:3 / /mnt/research\\040data rw,nosuid shared:2 - ext4 /dev/loop3 rw\n"
    )
    assert records == [
        {
            "mount_id": "42",
            "major_minor": "7:3",
            "mount_root": "/",
            "mount_path": "/mnt/research data",
            "mount_options": ("rw", "nosuid"),
            "filesystem": "ext4",
            "source": "/dev/loop3",
        }
    ]


@pytest.mark.parametrize(
    ("findmnt_uuid", "linked_uuids", "expected"),
    [
        (UUID, [UUID], UUID),
        (UUID, [], UUID),
        ("", [UUID], UUID),
    ],
)
def test_linux_uuid_sources_accept_single_agreeing_identity(
    findmnt_uuid: str, linked_uuids: list[str], expected: str
) -> None:
    assert research_storage._select_linux_volume_uuid(findmnt_uuid, linked_uuids) == expected


def test_linux_uuid_sources_reject_conflicts_duplicates_and_absence() -> None:
    with pytest.raises(StorageError, match="volume_uuid_source_conflict"):
        research_storage._select_linux_volume_uuid(UUID, ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"])
    with pytest.raises(StorageError, match="ambiguous_volume_uuid_link"):
        research_storage._select_linux_volume_uuid(
            "", [UUID, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"]
        )
    with pytest.raises(StorageError, match="volume_uuid_unavailable"):
        research_storage._select_linux_volume_uuid("", [])


def test_linux_uuid_sources_reject_malformed_findmnt_identity() -> None:
    with pytest.raises(StorageError, match="invalid_volume_uuid"):
        research_storage._canonical_linux_uuid("not-a-uuid")


def test_linux_uuid_link_requires_canonical_name_and_matching_device() -> None:
    assert research_storage._matching_linux_uuid_link(UUID, "7:3", "7:3") == UUID
    assert research_storage._matching_linux_uuid_link(UUID, "7:4", "7:3") is None
    with pytest.raises(StorageError, match="invalid_volume_uuid_link"):
        research_storage._matching_linux_uuid_link("not-a-uuid", "7:3", "7:3")


@pytest.mark.parametrize(
    "directory_stat",
    [
        SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_uid=0),
        SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=501),
        SimpleNamespace(st_mode=stat.S_IFREG | 0o755, st_uid=0),
    ],
)
def test_linux_uuid_directory_must_be_root_owned_and_not_untrusted_writable(
    directory_stat: SimpleNamespace,
) -> None:
    with pytest.raises(StorageError, match="unsafe_volume_uuid_directory"):
        research_storage._validate_linux_uuid_directory(directory_stat)  # type: ignore[arg-type]


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


def _existing_root_fixture(tmp_path: Path, *, marker: bytes | None = None):
    mount = tmp_path / "external"
    root = mount / "research" / "root"
    root.mkdir(parents=True, mode=0o700)
    (mount / "research").chmod(0o700)
    (root / ROOT_MARKER).write_bytes(
        marker
        if marker is not None
        else json.dumps(
            {
                "version": 1,
                "root_id": ROOT_ID,
                "volume_uuid": UUID,
                "data_root": "research/root",
            },
            sort_keys=True,
        ).encode()
    )
    (root / ROOT_MARKER).chmod(0o600)
    host_config_dir = tmp_path / "host-config"
    host_config_dir.mkdir(mode=0o700)
    config_path = host_config_dir / "binding.json"
    return config_path, mount, root


def test_bind_existing_writes_host_binding_without_volume_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, mount, root = _existing_root_fixture(tmp_path)
    probe = FakeProbe(mount)
    monkeypatch.setattr(research_storage, "_ensure_config_parent_on_other_device", lambda *_: None)
    before = sorted(str(path.relative_to(mount)) for path in mount.rglob("*"))
    config = bind_existing(
        config_path,
        mount,
        "research/root",
        expected_volume_uuid=UUID,
        expected_root_id=ROOT_ID,
        min_free_bytes=100,
        probe=probe,
    )
    after = sorted(str(path.relative_to(mount)) for path in mount.rglob("*"))
    assert before == after
    assert config.root_id == ROOT_ID
    assert StorageConfig.load(config_path) == config
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert not (mount / ".research-storage.lock").exists()
    assert probe.calls == 3
    assert root.is_dir()


def test_linux_bind_existing_writes_tagged_config_and_does_not_mutate_volume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, mount, _ = _existing_root_fixture(tmp_path)
    probe = FakeProbe(
        mount,
        filesystem="ext4",
        filesystem_type="ext4",
        mount_options=("rw",),
        mount_id=77,
        source="/dev/loop0",
        mount_root="/",
    )
    monkeypatch.setattr(research_storage, "_ensure_config_parent_on_other_device", lambda *_: None)
    monkeypatch.setattr(research_storage, "_fd_mount_id", lambda _fd: 77)
    monkeypatch.setattr(research_storage.sys, "platform", "linux")
    monkeypatch.setattr(
        research_storage, "_verify_linux_case_sensitive_directory", lambda _fd: None
    )
    before = sorted(str(path.relative_to(mount)) for path in mount.rglob("*"))
    config = bind_existing(
        config_path,
        mount,
        "research/root",
        expected_volume_uuid=UUID,
        expected_root_id=ROOT_ID,
        min_free_bytes=100,
        probe=probe,
        platform="linux",
    )
    after = sorted(str(path.relative_to(mount)) for path in mount.rglob("*"))
    assert before == after
    assert config.version == 2
    assert config.platform == "linux"
    assert config.filesystem == "ext4"
    assert StorageConfig.load(config_path) == config
    assert json.loads(config_path.read_text()) == {
        "version": 2,
        "platform": "linux",
        "filesystem": "ext4",
        "root_id": ROOT_ID,
        "fs_uuid": UUID,
        "mount_path": str(mount),
        "data_root": "research/root",
        "min_free_bytes": 100,
    }


def test_linux_bind_existing_fails_closed_on_mount_id_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, mount, _ = _existing_root_fixture(tmp_path)
    probe = FakeProbe(
        mount,
        filesystem="ext4",
        filesystem_type="ext4",
        mount_options=("rw",),
        mount_id=lambda call: 77 if call == 1 else 78,
        source="/dev/loop0",
        mount_root="/",
    )
    monkeypatch.setattr(research_storage, "_ensure_config_parent_on_other_device", lambda *_: None)
    monkeypatch.setattr(research_storage, "_fd_mount_id", lambda _fd: 77)
    monkeypatch.setattr(research_storage.sys, "platform", "linux")
    monkeypatch.setattr(
        research_storage, "_verify_linux_case_sensitive_directory", lambda _fd: None
    )
    with pytest.raises(StorageError, match="mounted_directory_replaced"):
        bind_existing(
            config_path,
            mount,
            "research/root",
            expected_volume_uuid=UUID,
            expected_root_id=ROOT_ID,
            min_free_bytes=100,
            probe=probe,
            platform="linux",
        )
    assert not config_path.exists()


def test_linux_bind_existing_rejects_casefold_root_before_config_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, mount, _ = _existing_root_fixture(tmp_path)
    probe = FakeProbe(
        mount,
        filesystem="ext4",
        filesystem_type="ext4",
        mount_options=("rw",),
        mount_id=77,
        source="/dev/loop0",
        mount_root="/",
    )
    monkeypatch.setattr(research_storage, "_ensure_config_parent_on_other_device", lambda *_: None)
    monkeypatch.setattr(research_storage, "_fd_mount_id", lambda _fd: 77)
    monkeypatch.setattr(research_storage.sys, "platform", "linux")

    def reject_casefold(_fd: int) -> None:
        raise StorageError("filesystem_casefold_enabled")

    monkeypatch.setattr(research_storage, "_verify_linux_case_sensitive_directory", reject_casefold)
    with pytest.raises(StorageError, match="filesystem_casefold_enabled"):
        bind_existing(
            config_path,
            mount,
            "research/root",
            expected_volume_uuid=UUID,
            expected_root_id=ROOT_ID,
            min_free_bytes=100,
            probe=probe,
            platform="linux",
        )
    assert not config_path.exists()


def test_linux_bind_existing_removes_its_config_if_final_verify_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, mount, _ = _existing_root_fixture(tmp_path)
    probe = FakeProbe(
        mount,
        filesystem="ext4",
        filesystem_type="ext4",
        mount_options=("rw",),
        mount_id=77,
        source="/dev/loop0",
        mount_root="/",
    )
    monkeypatch.setattr(research_storage, "_ensure_config_parent_on_other_device", lambda *_: None)
    monkeypatch.setattr(research_storage, "_fd_mount_id", lambda _fd: 77)
    monkeypatch.setattr(research_storage.sys, "platform", "linux")
    monkeypatch.setattr(
        research_storage, "_verify_linux_case_sensitive_directory", lambda _fd: None
    )

    def fail_verify(*_args, **_kwargs):
        raise StorageError("synthetic_final_verify_failure")

    monkeypatch.setattr(research_storage, "verify_root", fail_verify)
    with pytest.raises(StorageError, match="synthetic_final_verify_failure"):
        bind_existing(
            config_path,
            mount,
            "research/root",
            expected_volume_uuid=UUID,
            expected_root_id=ROOT_ID,
            min_free_bytes=100,
            probe=probe,
            platform="linux",
        )
    assert not config_path.exists()


def test_linux_bind_existing_removes_partial_config_after_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, mount, _ = _existing_root_fixture(tmp_path)
    probe = FakeProbe(
        mount,
        filesystem="ext4",
        filesystem_type="ext4",
        mount_options=("rw",),
        mount_id=77,
        source="/dev/loop0",
        mount_root="/",
    )
    monkeypatch.setattr(research_storage, "_ensure_config_parent_on_other_device", lambda *_: None)
    monkeypatch.setattr(research_storage, "_fd_mount_id", lambda _fd: 77)
    monkeypatch.setattr(research_storage.sys, "platform", "linux")
    monkeypatch.setattr(
        research_storage, "_verify_linux_case_sensitive_directory", lambda _fd: None
    )

    def partial_write_then_fail(fd: int, data: bytes) -> None:
        os.write(fd, data[:8])
        raise OSError("synthetic interrupted config write")

    monkeypatch.setattr(research_storage, "_write_all", partial_write_then_fail)
    with pytest.raises(StorageError, match="existing_root_binding_failed"):
        bind_existing(
            config_path,
            mount,
            "research/root",
            expected_volume_uuid=UUID,
            expected_root_id=ROOT_ID,
            min_free_bytes=100,
            probe=probe,
            platform="linux",
        )
    assert not config_path.exists()


def test_bind_existing_rejects_absent_malformed_or_mismatched_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for index, marker, expected in (
        (1, None, "root_marker_unavailable"),
        (2, b"{broken", "invalid_root_marker"),
        (
            3,
            json.dumps(
                {
                    "version": 1,
                    "root_id": "b" * 32,
                    "volume_uuid": UUID,
                    "data_root": "research/root",
                }
            ).encode(),
            "root_marker_binding_mismatch",
        ),
        (
            4,
            json.dumps(
                {
                    "version": True,
                    "root_id": ROOT_ID,
                    "volume_uuid": UUID,
                    "data_root": "research/root",
                }
            ).encode(),
            "root_marker_binding_mismatch",
        ),
    ):
        case = tmp_path / str(index)
        case.mkdir()
        config_path, mount, _root = _existing_root_fixture(case, marker=marker)
        if marker is None:
            (mount / "research/root" / ROOT_MARKER).unlink()
        monkeypatch.setattr(
            research_storage, "_ensure_config_parent_on_other_device", lambda *_: None
        )
        with pytest.raises(StorageError, match=expected):
            bind_existing(
                config_path,
                mount,
                "research/root",
                expected_volume_uuid=UUID,
                expected_root_id=ROOT_ID,
                min_free_bytes=100,
                probe=FakeProbe(mount),
            )
        assert not config_path.exists()


def test_bind_existing_rejects_volume_identity_drift_and_config_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, mount, _root = _existing_root_fixture(tmp_path)
    monkeypatch.setattr(research_storage, "_ensure_config_parent_on_other_device", lambda *_: None)
    changing_probe = FakeProbe(
        mount,
        volume_uuid=lambda call: UUID if call == 1 else "22222222-3333-4444-5555-666666666666",
    )
    with pytest.raises(StorageError, match="volume_uuid_mismatch"):
        bind_existing(
            config_path,
            mount,
            "research/root",
            expected_volume_uuid=UUID,
            expected_root_id=ROOT_ID,
            min_free_bytes=100,
            probe=changing_probe,
        )
    assert not config_path.exists()
    config_path.write_text("keep", encoding="utf-8")
    config_path.chmod(0o600)
    with pytest.raises(StorageError, match="config_already_exists"):
        bind_existing(
            config_path,
            mount,
            "research/root",
            expected_volume_uuid=UUID,
            expected_root_id=ROOT_ID,
            min_free_bytes=100,
            probe=FakeProbe(mount),
        )
    assert config_path.read_text(encoding="utf-8") == "keep"


def test_bind_existing_requires_trusted_pins_and_bounded_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, mount, _root = _existing_root_fixture(
        tmp_path, marker=b"{" + b" " * research_storage.MAX_ROOT_MARKER_BYTES + b"}"
    )
    monkeypatch.setattr(research_storage, "_ensure_config_parent_on_other_device", lambda *_: None)
    for root_id in ("", "z" * 32, "b" * 31):
        with pytest.raises(StorageError, match="invalid_expected_root_id"):
            bind_existing(
                config_path,
                mount,
                "research/root",
                expected_volume_uuid=UUID,
                expected_root_id=root_id,
                min_free_bytes=100,
                probe=FakeProbe(mount),
            )
    with pytest.raises(StorageError, match="root_marker_too_large"):
        bind_existing(
            config_path,
            mount,
            "research/root",
            expected_volume_uuid=UUID,
            expected_root_id=ROOT_ID,
            min_free_bytes=100,
            probe=FakeProbe(mount),
        )


def test_existing_binding_config_must_be_outside_volume_before_parent_creation(
    tmp_path: Path,
) -> None:
    mount = tmp_path / "external"
    mount.mkdir()
    config_path = mount / "host-config" / "binding.json"
    with pytest.raises(StorageError, match="config_must_be_outside_bound_volume"):
        research_storage._ensure_config_parent_on_other_device(
            config_path.parent, mount.stat().st_dev
        )
    assert not (mount / "host-config").exists()


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


def test_migration_identity_check_rejects_missing_mount_without_volume_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    probe = FakeProbe(mount)
    create_root(config_path, probe=probe)
    monkeypatch.setattr(
        research_storage.os.path,
        "ismount",
        lambda path: Path(path) == mount and mount.exists(),
    )
    with writer_guard(config_path, probe=probe) as guard:
        calls_before = probe.calls
        moved = tmp_path / "unmounted-volume"
        mount.rename(moved)
        with pytest.raises(StorageError, match="mountpoint_directory_fallback"):
            guard.revalidate_identity()
        assert probe.calls == calls_before


def test_migration_identity_check_rejects_changed_config(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    probe = FakeProbe(mount)
    create_root(config_path, probe=probe)
    with writer_guard(config_path, probe=probe) as guard:
        payload = json.loads(config_path.read_text())
        payload["data_root"] = "research/other"
        config_path.write_text(json.dumps(payload))
        config_path.chmod(0o600)
        with pytest.raises(StorageError, match="binding_changed_under_lock"):
            guard.revalidate_identity()


def test_migration_identity_check_enforces_free_space_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, mount, _ = make_binding(tmp_path, min_free_bytes=100)
    probe = FakeProbe(mount)
    create_root(config_path, probe=probe)
    monkeypatch.setattr(research_storage.os.path, "ismount", lambda path: Path(path) == mount)
    monkeypatch.setattr(
        research_storage.os,
        "fstatvfs",
        lambda _fd: type(
            "StatVFS",
            (),
            {"f_flag": 0, "f_bavail": 99, "f_frsize": 1},
        )(),
    )
    with writer_guard(config_path, probe=probe) as guard:
        with pytest.raises(StorageError, match="insufficient_free_space"):
            guard.revalidate_identity()


def test_migration_identity_check_rejects_readonly_statvfs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    probe = FakeProbe(mount)
    create_root(config_path, probe=probe)
    monkeypatch.setattr(research_storage.os.path, "ismount", lambda path: Path(path) == mount)
    monkeypatch.setattr(
        research_storage.os,
        "fstatvfs",
        lambda _fd: type(
            "StatVFS",
            (),
            {"f_flag": os.ST_RDONLY, "f_bavail": 1_000, "f_frsize": 1},
        )(),
    )
    with writer_guard(config_path, probe=probe) as guard:
        with pytest.raises(StorageError, match="volume_not_writable"):
            guard.revalidate_identity()


def test_migration_identity_check_rejects_replaced_lock(tmp_path: Path) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    probe = FakeProbe(mount)
    create_root(config_path, probe=probe)
    with writer_guard(config_path, probe=probe) as guard:
        lock = mount / ".research-storage.lock"
        lock.unlink()
        lock.write_text("replacement")
        lock.chmod(0o600)
        with pytest.raises(StorageError, match="unsafe_writer_lock|writer_lock_replaced"):
            guard.revalidate_identity()


def test_migration_identity_check_rejects_unsafe_root_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, mount, _ = make_binding(tmp_path)
    probe = FakeProbe(mount)
    root = create_root(config_path, probe=probe).path
    monkeypatch.setattr(
        research_storage.os.path,
        "ismount",
        lambda path: Path(path) == mount and mount.exists(),
    )
    with writer_guard(config_path, probe=probe) as guard:
        (root / ROOT_MARKER).chmod(0o644)
        with pytest.raises(StorageError, match="unsafe_root_marker"):
            guard.revalidate_identity()
