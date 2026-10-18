from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from memecoin_research_storage import (
    StorageError,
    VolumeInfo,
    configure,
    create_root,
)
from tools.research_storage_health import check_storage_health

UUID = "11111111-2222-3333-4444-555555555555"


class FakeProbe:
    def __init__(self, mount: Path, **changes: object) -> None:
        self.mount = mount
        self.changes = changes
        self.calls = 0

    def inspect(self, mount_path: Path) -> VolumeInfo:
        assert mount_path == self.mount
        self.calls += 1
        fields: dict[str, object] = {
            "mount_path": self.mount,
            "volume_uuid": UUID,
            "filesystem": "Case-sensitive APFS",
            "filesystem_type": "apfs",
            "owners_enabled": True,
            "read_only": False,
            "available_bytes": 1_000_000,
            "is_mountpoint": True,
            "mount_options": ("apfs", "local"),
        }
        for key, value in self.changes.items():
            fields[key] = value(self.calls) if callable(value) else value
        return VolumeInfo(**fields)  # type: ignore[arg-type]


def setup(tmp_path: Path) -> tuple[Path, Path, Path, FakeProbe]:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    state = home / "Library" / "StorageHealth"
    state.mkdir(mode=0o700, parents=True)
    state.chmod(0o700)
    mount = tmp_path / "external"
    mount.mkdir()
    config_path = home / "private" / "storage.json"
    probe = FakeProbe(mount)
    configure(
        config_path,
        mount,
        "BrainData",
        min_free_bytes=1,
        volume_uuid=UUID,
        probe=probe,
    )
    create_root(config_path, probe=probe)
    return config_path, state, home, probe


def read_status(state: Path) -> dict[str, object]:
    return json.loads((state / "status.json").read_text(encoding="utf-8"))


def test_health_writes_ready_status_without_touching_research_volume(tmp_path: Path) -> None:
    config_path, state, home, probe = setup(tmp_path)
    mount_contents_before = set((tmp_path / "external").iterdir())
    result = check_storage_health(config_path, state, probe=probe, home_dir=home, checked_at_ns=123)
    assert result["status"] == "ready"
    assert result["root_id"]
    assert result["volume_uuid"] == UUID
    assert result["available_bytes"] == 1_000_000
    assert result["checked_at_ns"] == 123
    assert read_status(state) == result
    assert set((tmp_path / "external").iterdir()) == mount_contents_before
    assert stat.S_IMODE((state / "status.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((state / ".lock").stat().st_mode) == 0o600
    assert set(p.name for p in state.iterdir()) == {"status.json", ".lock"}
    assert probe.calls >= 2


def test_unavailable_then_ready_records_bounded_transition(tmp_path: Path) -> None:
    config_path, state, home, _ = setup(tmp_path)
    unavailable_probe = FakeProbe(tmp_path / "external", is_mountpoint=False)
    unavailable = check_storage_health(
        config_path, state, probe=unavailable_probe, home_dir=home, checked_at_ns=1
    )
    assert unavailable["status"] == "storage_unavailable"
    assert unavailable["error"] == "mountpoint_directory_fallback"
    assert "mount_path" not in unavailable
    ready = check_storage_health(
        config_path,
        state,
        probe=FakeProbe(tmp_path / "external"),
        home_dir=home,
        checked_at_ns=2,
    )
    assert ready["status"] == "ready"
    assert ready["previous_status"] == "storage_unavailable"
    assert ready["transition"] == "storage_unavailable_to_ready"
    assert set(p.name for p in state.iterdir()) == {"status.json", ".lock"}


def test_unexpected_error_text_is_not_written_to_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, state, home, _ = setup(tmp_path)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("secret-token /private/path")

    monkeypatch.setattr("tools.research_storage_health.verify_root", fail)
    result = check_storage_health(
        config_path, state, probe=FakeProbe(tmp_path / "external"), home_dir=home
    )
    assert result["error"] == "storage_check_failed"
    serialized = (state / "status.json").read_text(encoding="utf-8")
    assert "secret-token" not in serialized
    assert "/private/path" not in serialized


def test_rejects_symlink_unsafe_and_external_state_directories(tmp_path: Path) -> None:
    config_path, state, home, probe = setup(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    with pytest.raises(StorageError, match="state_directory_must_be_inside_home"):
        check_storage_health(config_path, outside, probe=probe, home_dir=home)
    linked = home / "linked"
    linked.symlink_to(state, target_is_directory=True)
    with pytest.raises(StorageError, match="symlink_state_dir_path"):
        check_storage_health(config_path, linked, probe=probe, home_dir=home)
    state.chmod(0o750)
    with pytest.raises(StorageError, match="unsafe_internal_state_directory"):
        check_storage_health(config_path, state, probe=probe, home_dir=home)


def test_rejects_state_directory_inside_configured_external_volume(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    mount = home / "external"
    mount.mkdir(mode=0o700)
    state = mount / "health"
    state.mkdir(mode=0o700)
    config_path = home / "private" / "storage.json"
    probe = FakeProbe(mount)
    configure(config_path, mount, "BrainData", min_free_bytes=1, volume_uuid=UUID, probe=probe)
    with pytest.raises(StorageError, match="state_directory_inside_research_volume"):
        check_storage_health(config_path, state, probe=probe, home_dir=home)
    assert list(state.iterdir()) == []


def test_rejects_unknown_state_files_and_oversized_previous_status(tmp_path: Path) -> None:
    config_path, state, home, probe = setup(tmp_path)
    (state / "unbounded.log").write_text("x")
    with pytest.raises(StorageError, match="unexpected_state_directory_entry"):
        check_storage_health(config_path, state, probe=probe, home_dir=home)
    (state / "unbounded.log").unlink()
    (state / "status.json").write_text("x" * 5000)
    (state / "status.json").chmod(0o600)
    with pytest.raises(StorageError, match="unsafe_status_file"):
        check_storage_health(config_path, state, probe=probe, home_dir=home)
