from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest

import tools.brain_host_tree_custody as tree_v2
import tools.brain_host_tree_custody_v3 as tree_v3
from tests import test_brain_host_recovery_set as recovery_set_fixtures
from tools.brain_host_recovery_frontier import BASE as FRONTIER_BASE
from tools.brain_host_recovery_frontier import (
    K_COMPLETE_SCHEMA,
    K_END_SCHEMA,
    K_MANIFEST_SCHEMA,
    RecoveryFrontierError,
    create_frontier_envelope,
    verify_frontier_envelope,
)
from tools.brain_host_recovery_set import (
    SQLITE_BASE,
    bind_restore_witness,
    create_set,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

UUID = recovery_set_fixtures.UUID
ROOT_ID = recovery_set_fixtures.ROOT_ID
SOURCE_UUID = "synthetic-source-volume"
SOURCE_ROOT_ID = "8" * 32
SET_ID = "f" * 32
WITNESS_ID = "8" * 32
ENVELOPE_ID = "9" * 32
K_ID = "a" * 32


def _fixture_source_inventory(source_roots: dict[str, Path]) -> list[dict]:
    """Inventory disposable pytest roots only; never used by recovery tooling."""
    rows = []
    for role, root in sorted(source_roots.items()):
        entries = []
        if root.is_file():
            candidates = [root]
        else:
            candidates = []
            for parent, dirs, files in os.walk(root, followlinks=False):
                dirs.sort()
                files.sort()
                candidates.extend(Path(parent) / name for name in [*dirs, *files])
        for path in candidates:
            info = path.lstat()
            record = {
                "path": "." if path == root else path.relative_to(root).as_posix(),
                "mode": stat.S_IMODE(info.st_mode),
            }
            if stat.S_ISLNK(info.st_mode):
                record.update({"type": "symlink", "target": os.readlink(path)})
            elif stat.S_ISDIR(info.st_mode):
                record["type"] = "directory"
            elif stat.S_ISREG(info.st_mode):
                data = path.read_bytes()
                record.update({"type": "file", "size": len(data), "sha256": sha256(data)})
            else:
                raise AssertionError("unexpected synthetic fixture entry type")
            entries.append(record)
        entries.sort(key=lambda row: row["path"])
        rows.append(
            {
                "role": role,
                "root_locator_sha256": sha256(f"synthetic-root:{role}".encode()),
                "membership_sha256": sha256(canonical_json(entries)),
                "included_count": len(entries),
                "excluded_count": 0,
                "unknown_count": 0,
            }
        )
    return rows


def _fixture(tmp_path: Path, *, extra_owner_files: dict[str, bytes] | None = None):
    writer, set_spec, _, _ = recovery_set_fixtures._setup_set(
        tmp_path, extra_owner_files=extra_owner_files
    )
    source_roots = {
        "owner_work": tmp_path / "owner-work",
        "research_work": tmp_path / "research-work",
        "private_config": tmp_path / "private-config",
        "launchagent_definitions": tmp_path / "launch-defs",
        "protected_d43": tmp_path / "owner-work" / "calendar-execution-01",
        "sqlite_backup": tmp_path / "sqlite-private" / "backup.sqlite",
    }
    archive = tmp_path / "archive"
    targets = {
        "owner_work": tmp_path / "quarantine-owner",
        "research_work": tmp_path / "quarantine-research",
        "private_config": tmp_path / "quarantine-config",
        "launchagent_definitions": tmp_path / "quarantine-launchagents",
        "protected_d43": tmp_path / "quarantine-d43",
        "sqlite_backup": tmp_path / "quarantine-sqlite" / "backup.sqlite",
    }
    create_set(
        writer,
        set_spec,
        expected_volume_uuid=UUID,
        expected_root_id=ROOT_ID,
        revalidate_identity=recovery_set_fixtures._identity,
    )
    by_role = {item["role"]: item for item in set_spec["members"]}
    tree_v3.extract_tree(writer, by_role["owner_work"]["artifact_id"], targets["owner_work"])
    tree_v3.extract_tree(writer, by_role["research_work"]["artifact_id"], targets["research_work"])
    tree_v3.extract_tree(
        writer, by_role["private_config"]["artifact_id"], targets["private_config"]
    )
    tree_v3.extract_tree(
        writer,
        by_role["launchagent_definitions"]["artifact_id"],
        targets["launchagent_definitions"],
    )
    tree_v2.extract_tree(writer, by_role["protected_d43"]["artifact_id"], targets["protected_d43"])
    targets["sqlite_backup"].parent.mkdir()
    sqlite_id = by_role["sqlite_backup"]["artifact_id"]
    writer_path = f"{SQLITE_BASE}/{sqlite_id}/backup.sqlite"
    targets["sqlite_backup"].write_bytes(writer.read_bytes(writer_path))
    bind_restore_witness(
        writer,
        SET_ID,
        WITNESS_ID,
        targets,
        expected_volume_uuid=UUID,
        expected_root_id=ROOT_ID,
        revalidate_identity=recovery_set_fixtures._identity,
    )

    k_base = f"host-recovery/synthetic-durable-frontiers/{K_ID}"
    writer.mkdir("host-recovery/synthetic-durable-frontiers")
    writer.mkdir(k_base)
    stream_id = "synthetic-research-journal"
    durable_end = 1
    marker_path = f"{k_base}/END-{sha256(stream_id.encode())}.json"
    object_path = f"{k_base}/OBJECT-{sha256(stream_id.encode())}.bin"
    stop_path = f"{k_base}/STOP-{sha256(stream_id.encode())}.json"
    object_bytes = b"synthetic committed corpus object 1"
    writer.create(object_path, object_bytes)
    stop_raw = canonical_json(
        {
            "schema": "brain-host-synthetic-stop-state-v1",
            "stream_id": stream_id,
            "state": "stopped",
            "resumable": False,
            "pending_count": 1,
        }
    )
    writer.create(stop_path, stop_raw)
    marker_raw = canonical_json(
        {
            "schema": K_END_SCHEMA,
            "stream_id": stream_id,
            "durable_end": durable_end,
            "object_path": object_path,
            "object_sha256": sha256(object_bytes),
            "object_size": len(object_bytes),
            "stop_path": stop_path,
            "stop_sha256": sha256(stop_raw),
            "state": "committed",
        }
    )
    writer.create(marker_path, marker_raw)
    vector = [
        {
            "stream_id": stream_id,
            "durable_end": durable_end,
            "marker_path": marker_path,
            "marker_sha256": sha256(marker_raw),
        }
    ]
    k_manifest = canonical_json(
        {
            "schema": K_MANIFEST_SCHEMA,
            "scenario": "synthetic",
            "frontier_id": K_ID,
            "volume_uuid": UUID,
            "root_id": ROOT_ID,
            "writer_ends": vector,
        }
    )
    writer.create(f"{k_base}/MANIFEST.json", k_manifest)
    k_complete = canonical_json(
        {
            "schema": K_COMPLETE_SCHEMA,
            "frontier_id": K_ID,
            "manifest_sha256": sha256(k_manifest),
        }
    )
    writer.create(f"{k_base}/COMPLETE.json", k_complete)
    durable_frontier = {
        "schema": "brain-host-durable-frontier-proof-v1",
        "scenario": "synthetic",
        "frontier_id": K_ID,
        "volume_uuid": UUID,
        "root_id": ROOT_ID,
        "manifest_path": f"{k_base}/MANIFEST.json",
        "complete_path": f"{k_base}/COMPLETE.json",
        "manifest_sha256": sha256(k_manifest),
        "complete_sha256": sha256(k_complete),
        "vector": vector,
    }

    set_members = sorted(set_spec["members"], key=lambda row: row["role"])
    inventory = _fixture_source_inventory(source_roots)
    if _fixture_source_inventory(source_roots) != inventory:
        raise RecoveryFrontierError("synthetic_source_view_not_stable_at_freeze")
    source_view_id = "synthetic-view-0001"
    fingerprint_recorded_ns = time.time_ns()
    root_inventory_sha256 = sha256(canonical_json(inventory))
    assertion_sha256 = sha256(
        canonical_json(
            {
                "schema": "brain-host-synthetic-post-capture-fingerprint-v1",
                "synthetic_view_id": source_view_id,
                "fingerprint_recorded_ns": fingerprint_recorded_ns,
                "root_inventory_sha256": root_inventory_sha256,
            }
        )
    )
    source_frontier = {
        "schema": "brain-host-post-capture-fingerprint-v1",
        "scenario": "synthetic",
        "source_view_kind": "synthetic_post_capture_fingerprint",
        "volume_uuid": SOURCE_UUID,
        "root_id": SOURCE_ROOT_ID,
        "synthetic_view_id": source_view_id,
        "fingerprint_recorded_ns": fingerprint_recorded_ns,
        "verification_method": "synthetic_post_capture_fingerprint_assertion",
        "assertion_sha256": assertion_sha256,
        "root_inventory": inventory,
        "root_inventory_sha256": root_inventory_sha256,
        "member_bindings": [
            {
                **item,
                "source_view_id": source_view_id,
            }
            for item in set_members
        ],
        "selection_policy_sha256": sha256(b"synthetic inclusion policy"),
        "exclusion_policy_sha256": sha256(b"synthetic exclusions; credentials absent"),
        "unresolved_candidate_count": 0,
        "wallets_disposition": "synthetic_fixture_absent",
        "metadata_disposition": {
            "scope": "synthetic_bytes_and_topology_only",
            "xattrs": "not_preserved",
            "acls": "not_preserved",
            "flags": "not_preserved",
            "uid_gid": "restored_by_mapping",
        },
    }
    spec = {
        "schema": "brain-host-recovery-frontier-spec-v1",
        "envelope_id": ENVELOPE_ID,
        "scenario": "synthetic",
        "set_id": SET_ID,
        "witness_id": WITNESS_ID,
        "expected_source_volume_uuid": SOURCE_UUID,
        "expected_source_root_id": SOURCE_ROOT_ID,
        "expected_volume_uuid": UUID,
        "expected_root_id": ROOT_ID,
        "source_frontier": source_frontier,
        "durable_frontier": durable_frontier,
    }

    def fresh_reader():
        return DescriptorWriter(os.open(archive, os.O_RDONLY | os.O_DIRECTORY))

    return writer, archive, spec, targets, fresh_reader, source_roots


def _identity():
    return UUID, ROOT_ID


def _create(writer, fresh_reader, spec, targets, **kwargs):
    return create_frontier_envelope(
        writer,
        fresh_reader,
        spec,
        witness_targets=targets,
        expected_storage_identity=(UUID, ROOT_ID),
        expected_source_volume_uuid=SOURCE_UUID,
        expected_source_root_id=SOURCE_ROOT_ID,
        revalidate_identity=_identity,
        **kwargs,
    )


def test_two_frontier_envelope_binds_set_k_vector_and_inert_restore(tmp_path: Path) -> None:
    writer, _archive, spec, targets, fresh_reader, _source_roots = _fixture(tmp_path)
    try:
        result = _create(writer, fresh_reader, spec, targets)
        assert result["complete"] is True
        assert result["cross_volume_atomic"] is False
        assert result["application_recovery_qualified"] is False
        assert result["factory_reset_ready"] is False
        with fresh_reader() as reader:
            verified = verify_frontier_envelope(
                reader,
                ENVELOPE_ID,
                spec,
                witness_targets=targets,
                expected_storage_identity=(UUID, ROOT_ID),
                expected_source_volume_uuid=SOURCE_UUID,
                expected_source_root_id=SOURCE_ROOT_ID,
                revalidate_identity=_identity,
            )
        assert (
            verified["post_capture_fingerprint_recorded_ns"]
            == spec["source_frontier"]["fingerprint_recorded_ns"]
        )
        assert verified["durable_frontier_K_vector_count"] == 1
        assert verified["restore_witness_bound"] is True
        assert verified["metadata_scope"] == "synthetic_bytes_and_topology_only"
        assert verified["application_recovery_qualified"] is False
        assert "COMPLETE.json" in writer.list_names(f"{FRONTIER_BASE}/{ENVELOPE_ID}")
    finally:
        writer.close()


@pytest.mark.parametrize(
    ("field_path", "value", "error"),
    [
        (("source_frontier", "wallets_disposition"), "unresolved", "wallets_unresolved"),
        (("source_frontier", "unresolved_candidate_count"), 1, "unresolved_candidates"),
        (("source_frontier", "root_inventory_sha256"), "0" * 64, "inventory_hash_mismatch"),
        (
            ("source_frontier", "metadata_disposition"),
            {"scope": "full_restore"},
            "metadata_claim_unsupported",
        ),
        (("durable_frontier", "scenario"), "real", "real_durable_frontier_verifier_unavailable"),
    ],
)
def test_ambiguous_or_unproved_frontiers_fail_closed(
    tmp_path: Path, field_path: tuple[str, str], value, error: str
) -> None:
    writer, _archive, spec, targets, fresh_reader, _source_roots = _fixture(tmp_path)
    try:
        spec[field_path[0]][field_path[1]] = value
        with pytest.raises(RecoveryFrontierError, match=error):
            _create(writer, fresh_reader, spec, targets)
        with pytest.raises(FileNotFoundError):
            writer.list_names(f"{FRONTIER_BASE}/{ENVELOPE_ID}")
    finally:
        writer.close()


def test_durable_frontier_requires_every_end_marker_and_exact_membership(tmp_path: Path) -> None:
    writer, _archive, spec, targets, fresh_reader, _source_roots = _fixture(tmp_path)
    try:
        base = f"host-recovery/synthetic-durable-frontiers/{K_ID}"
        writer.create(f"{base}/unexpected.bin", b"unaccounted")
        with pytest.raises(RecoveryFrontierError, match="durable_frontier_membership_invalid"):
            _create(writer, fresh_reader, spec, targets)
        with pytest.raises(FileNotFoundError):
            writer.list_names(f"{FRONTIER_BASE}/{ENVELOPE_ID}")
    finally:
        writer.close()


def test_durable_frontier_rejects_corrupt_corpus_object(tmp_path: Path) -> None:
    writer, _archive, spec, targets, fresh_reader, _source_roots = _fixture(tmp_path)
    try:
        # The stream object name uses SHA-256 of the UTF-8 stream identifier.
        object_hash = sha256(b"synthetic-research-journal")
        object_path = f"host-recovery/synthetic-durable-frontiers/{K_ID}/OBJECT-{object_hash}.bin"
        writer.unlink(object_path)
        writer.create(object_path, b"corrupted corpus bytes")
        with pytest.raises(RecoveryFrontierError, match="durable_frontier_end_marker_mismatch"):
            _create(writer, fresh_reader, spec, targets)
    finally:
        writer.close()


def test_verification_rejects_changed_identity_pins(tmp_path: Path) -> None:
    writer, _archive, spec, targets, fresh_reader, _source_roots = _fixture(tmp_path)
    try:
        _create(writer, fresh_reader, spec, targets)
        spec["expected_root_id"] = "7" * 32
        with pytest.raises(RecoveryFrontierError, match="storage_identity_pin_mismatch"):
            verify_frontier_envelope(
                writer,
                ENVELOPE_ID,
                spec,
                witness_targets=targets,
                expected_storage_identity=(UUID, ROOT_ID),
                expected_source_volume_uuid=SOURCE_UUID,
                expected_source_root_id=SOURCE_ROOT_ID,
                revalidate_identity=_identity,
            )
    finally:
        writer.close()


def test_envelope_interruption_never_publishes_complete(tmp_path: Path) -> None:
    writer, _archive, spec, targets, fresh_reader, _source_roots = _fixture(tmp_path)
    try:

        def interrupt(stage: str) -> None:
            if stage == "after_index":
                raise OSError("synthetic interruption")

        with pytest.raises(OSError, match="synthetic interruption"):
            _create(writer, fresh_reader, spec, targets, fault_hook=interrupt)
        names = set(writer.list_names(f"{FRONTIER_BASE}/{ENVELOPE_ID}"))
        assert names == {"RESERVATION.json", "INDEX.json"}
    finally:
        writer.close()
