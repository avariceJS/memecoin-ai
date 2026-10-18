from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tools.brain_senses_external_writer_v1 import DescriptorWriter, sha256
from tools.brain_senses_gecko_lane_live_sources_v2 import (
    BINDING_DIR,
    MANIFEST_PATH,
    PLAN_DESTINATION,
    SELECTION_DESTINATION,
    SOURCE_FILES,
    SOURCE_ROOT,
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


def _source_copy(destination: Path) -> Path:
    for item in SOURCE_FILES:
        target = destination / item["source_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE_ROOT / item["source_path"], target)
    return destination


def test_import_is_create_only_and_readback_is_descriptor_bound(tmp_path: Path) -> None:
    root = tmp_path / "BrainData"
    with _writer(root) as writer:
        manifest = import_source_pins(writer, root_id=ROOT_ID, volume_uuid=VOLUME_UUID)
        pinned = verify_source_binding(
            writer, expected_root_id=ROOT_ID, expected_volume_uuid=VOLUME_UUID
        )
        assert manifest["source_input_origin_commit"] == "8c0c501c4f2fdcf4ffaed30d6bd5e4a8aae75894"
        assert len(pinned.plan_bytes) == 332298
        assert len(pinned.selection_bytes) == 69881
        assert sha256(pinned.plan_bytes) == SOURCE_FILES[0]["sha256"]
        assert sha256(pinned.selection_bytes) == SOURCE_FILES[1]["sha256"]
        assert pinned.manifest_sha256 == sha256(pinned.manifest_bytes)
        with pytest.raises(SourcePinError, match="source_binding_destination_exists"):
            import_source_pins(writer, root_id=ROOT_ID, volume_uuid=VOLUME_UUID)


def test_missing_or_changed_imported_file_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "BrainData"
    with _writer(root) as writer:
        import_source_pins(writer, root_id=ROOT_ID, volume_uuid=VOLUME_UUID)
        with pytest.raises(SourcePinError, match="imported_source_mismatch"):
            writer.atomic_write(PLAN_DESTINATION, b"changed", create_only=False)
            verify_source_binding(
                writer, expected_root_id=ROOT_ID, expected_volume_uuid=VOLUME_UUID
            )


def test_manifest_identity_and_hash_are_rechecked(tmp_path: Path) -> None:
    root = tmp_path / "BrainData"
    with _writer(root) as writer:
        import_source_pins(writer, root_id=ROOT_ID, volume_uuid=VOLUME_UUID)
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


def test_missing_imported_source_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "BrainData"
    with _writer(root) as writer:
        import_source_pins(writer, root_id=ROOT_ID, volume_uuid=VOLUME_UUID)
        writer.unlink(PLAN_DESTINATION)
        with pytest.raises(SourcePinError, match="imported_source_unavailable"):
            verify_source_binding(
                writer, expected_root_id=ROOT_ID, expected_volume_uuid=VOLUME_UUID
            )


def test_local_source_mutation_prevents_any_import(tmp_path: Path) -> None:
    source_root = _source_copy(tmp_path / "source")
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


def test_detached_descriptor_import_does_not_write_mountpoint_underlay(tmp_path: Path) -> None:
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
        import_source_pins(writer, root_id=ROOT_ID, volume_uuid=VOLUME_UUID)
        assert sorted(p.name for p in mountpoint.iterdir()) == []
        assert (detached / PLAN_DESTINATION).is_file()
        assert (detached / SELECTION_DESTINATION).is_file()
        assert (detached / MANIFEST_PATH).is_file()
        assert (detached / BINDING_DIR).is_dir()
    finally:
        writer.close()


def test_prewrite_detach_failure_leaves_underlay_empty(tmp_path: Path) -> None:
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
            import_source_pins(writer, root_id=ROOT_ID, volume_uuid=VOLUME_UUID)
        assert sorted(p.name for p in mountpoint.iterdir()) == []
    finally:
        writer.close()
