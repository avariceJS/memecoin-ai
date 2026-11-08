from __future__ import annotations

import os
from pathlib import Path

import pytest

from memecoin_research_storage import StorageError
from tools import brain_senses_gecko_lane_live_sources_v2 as live_sources
from tools.brain_senses_external_writer_v1 import DescriptorWriter, sha256
from tools.brain_senses_gecko_lane_live_sources_v2 import (
    BINDING_DIR,
    MANIFEST_PATH,
    PLAN_DESTINATION,
    SELECTION_DESTINATION,
    SOURCE_FILES,
    SourcePinError,
    import_source_pins,
    verify_source_binding,
)

ROOT_ID = "1" * 32
VOLUME_UUID = "11111111-2222-3333-4444-555555555555"


def _writer(root: Path, *, fault_hook=None) -> DescriptorWriter:
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        return DescriptorWriter(root_fd, fault_hook=fault_hook)
    finally:
        os.close(root_fd)


def _source_copy(destination: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    synthetic_files = []
    for item, raw in zip(
        SOURCE_FILES, (b'{"synthetic":"plan"}\n', b'{"synthetic":"selection"}\n'), strict=True
    ):
        target = destination / item["source_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        synthetic_files.append({**item, "size_bytes": len(raw), "sha256": sha256(raw)})
    monkeypatch.setattr(live_sources, "SOURCE_FILES", tuple(synthetic_files))
    return destination


@pytest.fixture
def synthetic_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return _source_copy(tmp_path / "synthetic-source", monkeypatch)


def test_import_is_create_only_and_readback_is_descriptor_bound(
    tmp_path: Path, synthetic_sources: Path
) -> None:
    root = tmp_path / "BrainData"
    with _writer(root) as writer:
        manifest = import_source_pins(
            writer, root_id=ROOT_ID, volume_uuid=VOLUME_UUID, source_root=synthetic_sources
        )
        pinned = verify_source_binding(
            writer, expected_root_id=ROOT_ID, expected_volume_uuid=VOLUME_UUID
        )
        assert manifest["source_input_origin_commit"] == "8c0c501c4f2fdcf4ffaed30d6bd5e4a8aae75894"
        assert len(pinned.plan_bytes) == live_sources.SOURCE_FILES[0]["size_bytes"]
        assert len(pinned.selection_bytes) == live_sources.SOURCE_FILES[1]["size_bytes"]
        assert sha256(pinned.plan_bytes) == live_sources.SOURCE_FILES[0]["sha256"]
        assert sha256(pinned.selection_bytes) == live_sources.SOURCE_FILES[1]["sha256"]
        assert pinned.manifest_sha256 == sha256(pinned.manifest_bytes)
        with pytest.raises(SourcePinError, match="source_binding_destination_exists"):
            import_source_pins(
                writer, root_id=ROOT_ID, volume_uuid=VOLUME_UUID, source_root=synthetic_sources
            )


def test_missing_or_changed_imported_file_fails_closed(
    tmp_path: Path, synthetic_sources: Path
) -> None:
    root = tmp_path / "BrainData"
    with _writer(root) as writer:
        import_source_pins(
            writer, root_id=ROOT_ID, volume_uuid=VOLUME_UUID, source_root=synthetic_sources
        )
        with pytest.raises(SourcePinError, match="imported_source_mismatch"):
            writer.atomic_write(PLAN_DESTINATION, b"changed", create_only=False)
            verify_source_binding(
                writer, expected_root_id=ROOT_ID, expected_volume_uuid=VOLUME_UUID
            )


def test_manifest_identity_and_hash_are_rechecked(tmp_path: Path, synthetic_sources: Path) -> None:
    root = tmp_path / "BrainData"
    with _writer(root) as writer:
        import_source_pins(
            writer, root_id=ROOT_ID, volume_uuid=VOLUME_UUID, source_root=synthetic_sources
        )
        with pytest.raises(SourcePinError, match="manifest_digest_mismatch"):
            verify_source_binding(
                writer,
                expected_root_id=ROOT_ID,
                expected_volume_uuid=VOLUME_UUID,
                expected_manifest_sha256="0" * 64,
            )
        with pytest.raises(SourcePinError, match="manifest_binding_mismatch"):
            verify_source_binding(
                writer,
                expected_root_id="2" * 32,
                expected_volume_uuid=VOLUME_UUID,
            )


def test_missing_imported_source_fails_closed(tmp_path: Path, synthetic_sources: Path) -> None:
    root = tmp_path / "BrainData"
    with _writer(root) as writer:
        import_source_pins(
            writer, root_id=ROOT_ID, volume_uuid=VOLUME_UUID, source_root=synthetic_sources
        )
        writer.unlink(PLAN_DESTINATION)
        with pytest.raises(SourcePinError, match="imported_source_unavailable"):
            verify_source_binding(
                writer, expected_root_id=ROOT_ID, expected_volume_uuid=VOLUME_UUID
            )


def test_local_source_mutation_prevents_any_import(tmp_path: Path, monkeypatch) -> None:
    source_root = _source_copy(tmp_path / "source", monkeypatch)
    plan = source_root / SOURCE_FILES[0]["source_path"]
    plan.write_bytes(plan.read_bytes() + b"x")
    root = tmp_path / "BrainData"
    with _writer(root) as writer:
        with pytest.raises(SourcePinError, match="source_pin_mismatch"):
            import_source_pins(
                writer,
                root_id=ROOT_ID,
                volume_uuid=VOLUME_UUID,
                source_root=source_root,
            )
        assert writer.list_names() == ()


def test_detached_descriptor_import_does_not_write_mountpoint_underlay(
    tmp_path: Path, synthetic_sources: Path
) -> None:
    mountpoint = tmp_path / "BrainData"
    mountpoint.mkdir(mode=0o700)
    os.chmod(mountpoint, 0o700)
    root_fd = os.open(mountpoint, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(root_fd)
    os.close(root_fd)
    detached = tmp_path / "detached-volume"
    mountpoint.rename(detached)
    mountpoint.mkdir(mode=0o700)
    os.chmod(mountpoint, 0o700)
    try:
        import_source_pins(
            writer, root_id=ROOT_ID, volume_uuid=VOLUME_UUID, source_root=synthetic_sources
        )
        assert sorted(p.name for p in mountpoint.iterdir()) == []
        assert (detached / PLAN_DESTINATION).is_file()
        assert (detached / SELECTION_DESTINATION).is_file()
        assert (detached / MANIFEST_PATH).is_file()
        assert (detached / BINDING_DIR).is_dir()
    finally:
        writer.close()


def test_prewrite_detach_failure_leaves_underlay_empty(
    tmp_path: Path, synthetic_sources: Path
) -> None:
    mountpoint = tmp_path / "BrainData"
    mountpoint.mkdir(mode=0o700)
    os.chmod(mountpoint, 0o700)
    root_fd = os.open(mountpoint, os.O_RDONLY | os.O_DIRECTORY)

    def detach_on_create(operation: str) -> None:
        if operation == "create":
            mountpoint.rename(tmp_path / "detached-volume")
            mountpoint.mkdir(mode=0o700)
            os.chmod(mountpoint, 0o700)
            raise OSError("synthetic detached mount")

    writer = DescriptorWriter(root_fd, fault_hook=detach_on_create)
    os.close(root_fd)
    try:
        with pytest.raises(OSError, match="synthetic detached mount"):
            import_source_pins(
                writer, root_id=ROOT_ID, volume_uuid=VOLUME_UUID, source_root=synthetic_sources
            )
        assert sorted(p.name for p in mountpoint.iterdir()) == []
    finally:
        writer.close()


@pytest.mark.parametrize(
    ("symlink_kind", "reason"),
    [
        ("root", "source_root_invalid"),
        ("component", "source_component_invalid"),
        ("file", "source_not_regular"),
    ],
)
def test_source_symlinks_fail_before_any_import(tmp_path, synthetic_sources, symlink_kind, reason):
    source_root = synthetic_sources
    if symlink_kind == "root":
        source_root = tmp_path / "source-alias"
        source_root.symlink_to(synthetic_sources, target_is_directory=True)
    elif symlink_kind == "component":
        work = synthetic_sources / "work"
        moved = tmp_path / "moved-work"
        work.rename(moved)
        work.symlink_to(moved, target_is_directory=True)
    else:
        plan = synthetic_sources / SOURCE_FILES[0]["source_path"]
        moved = tmp_path / "moved-plan.json"
        plan.rename(moved)
        plan.symlink_to(moved)
    with _writer(tmp_path / "destination") as writer:
        with pytest.raises(SourcePinError, match=reason):
            import_source_pins(
                writer, root_id=ROOT_ID, volume_uuid=VOLUME_UUID, source_root=source_root
            )
        assert writer.list_names() == ()


@pytest.mark.parametrize("version", [3, 4])
@pytest.mark.parametrize("symlink_brain", [False, True])
def test_host_import_uses_physical_source_root_under_held_storage_guard(
    tmp_path, monkeypatch, version, symlink_brain
):
    import importlib
    from contextlib import contextmanager
    from types import SimpleNamespace

    runtime = importlib.import_module(f"tools.brain_senses_gecko_lane_runtime_v{version}")
    mount = tmp_path / "mount"
    data_root = mount / "ResearchData"
    source_root = data_root / "brain/research"
    source_root.mkdir(parents=True, mode=0o700)
    data_root.chmod(0o700)
    (data_root / "brain").chmod(0o700)
    if symlink_brain:
        brain = data_root / "brain"
        moved = tmp_path / "moved-brain"
        brain.rename(moved)
        brain.symlink_to(moved, target_is_directory=True)
    active = False
    importer_calls = []
    revalidations = []

    def revalidate():
        assert active
        revalidations.append(True)

    @contextmanager
    def storage_guard(config_path):
        nonlocal active
        assert (
            config_path == Path.home() / "Developer/memecoin-runtime/brain/config/storage-v1.json"
        )
        mount_fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
        active = True
        try:
            yield SimpleNamespace(
                mount_fd=mount_fd,
                path=data_root,
                config=SimpleNamespace(data_root="ResearchData"),
                root_id=ROOT_ID,
                volume_uuid=VOLUME_UUID,
                revalidate=revalidate,
            )
        finally:
            active = False
            os.close(mount_fd)

    def imported(_writer, *, root_id, volume_uuid, source_root):
        importer_calls.append(source_root)
        assert active
        assert root_id == ROOT_ID and volume_uuid == VOLUME_UUID
        assert source_root == data_root / "brain/research"
        assert not source_root.is_symlink()
        return {"synthetic": "source-binding"}

    monkeypatch.setattr(runtime, "writer_guard", storage_guard)
    monkeypatch.setattr(runtime.live_sources, "import_source_pins", imported)
    if symlink_brain:
        with pytest.raises(StorageError, match="symlink_root_component|unsafe_root_ancestor"):
            runtime.run_host_mode("import-source-pins")
        assert importer_calls == []
    else:
        result = runtime.run_host_mode("import-source-pins")
        assert result["status"] == "source_pins_imported"
        assert result["provider_io"] is False
        assert importer_calls == [source_root]
        assert len(revalidations) >= 2
    assert active is False
