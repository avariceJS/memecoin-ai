"""Opt-in, synthetic-only end-to-end recovery-set exercise on configured Kingston."""

from __future__ import annotations

import contextlib
import os
import secrets
import stat
import time
from pathlib import Path

import pytest

import tools.brain_host_tree_custody as tree_v2
import tools.brain_host_tree_custody_v3 as tree_v3
from tests import test_brain_host_recovery_frontier as frontier_fixtures
from tests import test_brain_host_recovery_set as recovery_set_fixtures
from tools.brain_host_recovery_frontier import (
    BASE as FRONTIER_BASE,
)
from tools.brain_host_recovery_frontier import (
    K_COMPLETE_SCHEMA,
    K_END_SCHEMA,
    K_MANIFEST_SCHEMA,
    create_frontier_envelope,
    verify_frontier_envelope,
)
from tools.brain_host_recovery_set import (
    BASE as SET_BASE,
)
from tools.brain_host_recovery_set import (
    REQUIRED_ROLES,
    SQLITE_BASE,
    WITNESS_BASE,
    bind_restore_witness,
    create_set,
)
from tools.brain_host_tree_custody import _storage_writer
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

EXPECTED = ("8f8f1081-9be4-4914-9111-75f1b90255bf", "9428695af6d4f46c08e1a444cdc66de8")
STORAGE_CONFIG = Path.home() / ".config/brain-research/storage-v1.json"
RUN_KINGSTON = os.environ.get("BRAIN_DR_KINGSTON_REHEARSAL") == "1"


def _copy_tree(source, destination, source_path: str, destination_path: str) -> None:
    parent, name = source._parent_and_name(source_path)
    parent_fd = source._directory(parent)
    try:
        source_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    finally:
        os.close(parent_fd)
    if stat.S_ISREG(source_stat.st_mode):
        destination.create(
            destination_path,
            source.read_bytes(source_path, max_bytes=64 * 1024 * 1024),
        )
        return
    if not stat.S_ISDIR(source_stat.st_mode):
        raise RuntimeError("unsupported_synthetic_artifact_type")
    names = source.list_names(source_path)
    destination.mkdir(destination_path)
    for name in names:
        _copy_tree(
            source,
            destination,
            f"{source_path}/{name}",
            f"{destination_path}/{name}",
        )


def _tree_inventory(writer, root: str) -> dict[str, dict[str, str | int]]:
    inventory: dict[str, dict[str, str | int]] = {}

    def visit(directory: str, prefix: str) -> None:
        for name in writer.list_names(directory):
            relative = f"{prefix}/{name}" if prefix else name
            path = f"{directory}/{name}"
            entry_stat = writer.stat(path)
            if stat.S_ISDIR(entry_stat.st_mode):
                inventory[relative] = {"type": "directory"}
                visit(path, relative)
            elif stat.S_ISREG(entry_stat.st_mode):
                content = writer.read_bytes(path, max_bytes=64 * 1024 * 1024)
                inventory[relative] = {
                    "type": "file",
                    "size_bytes": len(content),
                    "sha256": sha256(content),
                }
            else:
                raise RuntimeError("unexpected_synthetic_artifact_entry")

    visit(root, "")
    return inventory


def _remove_tree(writer, relative: str, revalidate) -> None:
    parent, name = writer._parent_and_name(relative)
    directory_fd = writer._directory(relative)
    try:
        for child_name in os.listdir(directory_fd):
            child_path = f"{relative}/{child_name}"
            child_stat = os.stat(child_name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISDIR(child_stat.st_mode):
                _remove_tree(writer, child_path, revalidate)
            elif stat.S_ISREG(child_stat.st_mode):
                revalidate()
                writer.unlink(child_path)
            else:
                raise RuntimeError("unexpected_fixture_entry_during_cleanup")
    finally:
        os.close(directory_fd)
    parent_fd = writer._directory(parent)
    try:
        revalidate()
        os.rmdir(name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


@contextlib.contextmanager
def _fresh_reader(writer, revalidate):
    """Open an independent read-only root descriptor beneath the active guard."""
    if revalidate() != EXPECTED:
        raise RuntimeError("storage_identity_changed_before_readback")
    fresh_root_fd = os.open(
        ".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=writer.root_fd
    )
    try:
        fresh_stat = os.fstat(fresh_root_fd)
        held_stat = os.fstat(writer.root_fd)
        if (fresh_stat.st_dev, fresh_stat.st_ino) != (held_stat.st_dev, held_stat.st_ino):
            raise RuntimeError("fresh_readback_root_changed")

        def reject_write() -> None:
            raise RuntimeError("readback_descriptor_is_read_only")

        with DescriptorWriter(fresh_root_fd, before_write=reject_write) as reader:
            yield reader
        if revalidate() != EXPECTED:
            raise RuntimeError("storage_identity_changed_after_readback")
    finally:
        os.close(fresh_root_fd)


@pytest.mark.skipif(not RUN_KINGSTON, reason="requires explicit Kingston rehearsal opt-in")
def test_synthetic_recovery_set_frontier_and_inert_restore_on_kingston(tmp_path: Path) -> None:
    run_id = secrets.token_hex(16)
    set_id, witness_id, envelope_id, durable_id = (secrets.token_hex(16) for _ in range(4))
    if len({run_id, set_id, witness_id, envelope_id, durable_id}) != 5:
        pytest.fail("synthetic_rehearsal_id_collision")
    source_uuid = "synthetic-source-volume"
    source_root_id = "8" * 32
    temp_writer, set_spec, _db_path, _sqlite_base = recovery_set_fixtures._setup_set(tmp_path)
    set_spec["set_id"] = set_id
    by_role = {item["role"]: item for item in set_spec["members"]}
    old_ids = {
        "owner_work": by_role["owner_work"]["artifact_id"],
        "research_work": by_role["research_work"]["artifact_id"],
        "private_config": by_role["private_config"]["artifact_id"],
        "launchagent_definitions": by_role["launchagent_definitions"]["artifact_id"],
        "protected_d43": by_role["protected_d43"]["artifact_id"],
        "sqlite_backup": by_role["sqlite_backup"]["artifact_id"],
    }
    # Artifact IDs are embedded in their manifests; preserve them when copying.
    new_ids = old_ids.copy()
    source_bases = {
        "owner_work": tree_v3.BASE,
        "research_work": tree_v3.BASE,
        "private_config": tree_v3.BASE,
        "launchagent_definitions": tree_v3.BASE,
        "protected_d43": tree_v2.BASE,
        "sqlite_backup": SQLITE_BASE,
    }
    targets = {
        "owner_work": tmp_path / "restore-owner",
        "research_work": tmp_path / "restore-research",
        "private_config": tmp_path / "restore-config",
        "launchagent_definitions": tmp_path / "restore-launchagents",
        "protected_d43": tmp_path / "restore-protected",
        "sqlite_backup": tmp_path / "restore-sqlite" / "backup.sqlite",
    }
    archive_paths = [f"{source_bases[role]}/{new_ids[role]}" for role in sorted(REQUIRED_ROLES)]
    durable_base = f"host-recovery/synthetic-durable-frontiers/{durable_id}"
    envelope_path = f"{FRONTIER_BASE}/{envelope_id}"
    witness_parent = f"{WITNESS_BASE}/{set_id}"
    witness_path = f"{witness_parent}/{witness_id}"
    reservation_parent = "host-recovery/kingston-synthetic-rehearsals"
    reservation_path = f"{reservation_parent}/{run_id}"
    reservation_file = f"{reservation_path}/RESERVATION.json"
    coordination_paths = [
        f"{SET_BASE}/.index.lock",
        f"{FRONTIER_BASE}/.publish.lock",
    ]
    cleanup_paths = [
        *archive_paths,
        f"{SET_BASE}/{set_id}",
        witness_path,
        durable_base,
        envelope_path,
    ]
    started = False
    fully_verified = False
    try:
        with _storage_writer(STORAGE_CONFIG) as writer:
            guard = writer._custody_mutation_guard.storage_guard

            def revalidate():
                resolved = guard.revalidate()
                return resolved.volume_uuid, resolved.root_id

            try:
                writer.list_names(reservation_parent)
            except FileNotFoundError:
                reservation_parent_preexisting = False
            else:
                reservation_parent_preexisting = True
            try:
                writer.list_names("host-recovery/synthetic-durable-frontiers")
            except FileNotFoundError:
                durable_parent_preexisting = False
            else:
                durable_parent_preexisting = True

            if revalidate() != EXPECTED:
                pytest.fail("configured_Kingston_identity_mismatch")
            checks = [
                (source_bases[role], artifact_id) for role, artifact_id in new_ids.items()
            ] + [
                (SET_BASE, set_id),
                (WITNESS_BASE, set_id),
                ("host-recovery/synthetic-durable-frontiers", durable_id),
                (FRONTIER_BASE, envelope_id),
                (reservation_parent, run_id),
            ]
            for base, candidate in checks:
                try:
                    existing = set(writer.list_names(base))
                except FileNotFoundError:
                    existing = set()
                if candidate in existing:
                    pytest.fail("synthetic_rehearsal_id_collision")
            reservation = canonical_json(
                {
                    "schema": "brain-host-kingston-synthetic-rehearsal-reservation-v1",
                    "run_id": run_id,
                    "state": "synthetic_only_incomplete_until_cleanup",
                    "volume_uuid": EXPECTED[0],
                    "root_id": EXPECTED[1],
                    "run_owned_cleanup_paths": sorted([*cleanup_paths, reservation_file]),
                    "shared_coordination_paths_retained": sorted(coordination_paths),
                }
            )
            writer.create(reservation_file, reservation)
            started = True
            for role in sorted(REQUIRED_ROLES):
                _copy_tree(
                    temp_writer,
                    writer,
                    f"{source_bases[role]}/{old_ids[role]}",
                    f"{source_bases[role]}/{new_ids[role]}",
                )
            set_spec.update({"volume_uuid": EXPECTED[0], "root_id": EXPECTED[1]})
            create_set(
                writer,
                set_spec,
                expected_volume_uuid=EXPECTED[0],
                expected_root_id=EXPECTED[1],
                revalidate_identity=revalidate,
            )
            for role, member in by_role.items():
                artifact_path = f"{source_bases[role]}/{member['artifact_id']}"
                if role == "owner_work":
                    tree_v3.extract_tree(writer, member["artifact_id"], targets[role])
                elif role in {
                    "research_work",
                    "private_config",
                    "launchagent_definitions",
                }:
                    tree_v3.extract_tree(writer, member["artifact_id"], targets[role])
                elif role == "protected_d43":
                    tree_v2.extract_tree(writer, member["artifact_id"], targets[role])
                else:
                    targets[role].parent.mkdir(mode=0o700)
                    targets[role].write_bytes(writer.read_bytes(f"{artifact_path}/backup.sqlite"))
            bind_restore_witness(
                writer,
                set_id,
                witness_id,
                targets,
                expected_volume_uuid=EXPECTED[0],
                expected_root_id=EXPECTED[1],
                revalidate_identity=revalidate,
            )
            source_roots = {
                "owner_work": tmp_path / "owner-work",
                "research_work": tmp_path / "research-work",
                "private_config": tmp_path / "private-config",
                "launchagent_definitions": tmp_path / "launch-defs",
                "protected_d43": tmp_path / "owner-work" / "calendar-execution-01",
                "sqlite_backup": tmp_path / "sqlite-private" / "backup.sqlite",
            }
            inventory = frontier_fixtures._fixture_source_inventory(source_roots)
            source_view_id = "synthetic-view-" + run_id
            fingerprint_ns = time.time_ns()
            inventory_hash = sha256(canonical_json(inventory))
            assertion_hash = sha256(
                canonical_json(
                    {
                        "schema": "brain-host-synthetic-post-capture-fingerprint-v1",
                        "synthetic_view_id": source_view_id,
                        "fingerprint_recorded_ns": fingerprint_ns,
                        "root_inventory_sha256": inventory_hash,
                    }
                )
            )
            source_frontier = {
                "schema": "brain-host-post-capture-fingerprint-v1",
                "scenario": "synthetic",
                "source_view_kind": "synthetic_post_capture_fingerprint",
                "volume_uuid": source_uuid,
                "root_id": source_root_id,
                "synthetic_view_id": source_view_id,
                "fingerprint_recorded_ns": fingerprint_ns,
                "verification_method": "synthetic_post_capture_fingerprint_assertion",
                "assertion_sha256": assertion_hash,
                "root_inventory": inventory,
                "root_inventory_sha256": inventory_hash,
                "member_bindings": [
                    {**member, "source_view_id": source_view_id}
                    for member in sorted(set_spec["members"], key=lambda row: row["role"])
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
            writer.mkdir(durable_base)
            stream_id = "synthetic-stream-" + run_id
            digest = sha256(stream_id.encode())
            object_path, stop_path, marker_path = (
                f"{durable_base}/OBJECT-{digest}.bin",
                f"{durable_base}/STOP-{digest}.json",
                f"{durable_base}/END-{digest}.json",
            )
            object_bytes = b"synthetic committed corpus object; no project data"
            stop = canonical_json(
                {
                    "schema": "brain-host-synthetic-stop-state-v1",
                    "stream_id": stream_id,
                    "state": "stopped",
                    "resumable": False,
                    "pending_count": 1,
                }
            )
            marker = canonical_json(
                {
                    "schema": K_END_SCHEMA,
                    "stream_id": stream_id,
                    "durable_end": 1,
                    "object_path": object_path,
                    "object_sha256": sha256(object_bytes),
                    "object_size": len(object_bytes),
                    "stop_path": stop_path,
                    "stop_sha256": sha256(stop),
                    "state": "committed",
                }
            )
            vector = [
                {
                    "stream_id": stream_id,
                    "durable_end": 1,
                    "marker_path": marker_path,
                    "marker_sha256": sha256(marker),
                }
            ]
            for path, data in (
                (object_path, object_bytes),
                (stop_path, stop),
                (marker_path, marker),
            ):
                writer.create(path, data)
            manifest = canonical_json(
                {
                    "schema": K_MANIFEST_SCHEMA,
                    "scenario": "synthetic",
                    "frontier_id": durable_id,
                    "volume_uuid": EXPECTED[0],
                    "root_id": EXPECTED[1],
                    "writer_ends": vector,
                }
            )
            complete = canonical_json(
                {
                    "schema": K_COMPLETE_SCHEMA,
                    "frontier_id": durable_id,
                    "manifest_sha256": sha256(manifest),
                }
            )
            manifest_path, complete_path = (
                f"{durable_base}/MANIFEST.json",
                f"{durable_base}/COMPLETE.json",
            )
            writer.create(manifest_path, manifest)
            writer.create(complete_path, complete)
            durable_frontier = {
                "schema": "brain-host-durable-frontier-proof-v1",
                "scenario": "synthetic",
                "frontier_id": durable_id,
                "volume_uuid": EXPECTED[0],
                "root_id": EXPECTED[1],
                "manifest_path": manifest_path,
                "complete_path": complete_path,
                "manifest_sha256": sha256(manifest),
                "complete_sha256": sha256(complete),
                "vector": vector,
            }
            spec = {
                "schema": "brain-host-recovery-frontier-spec-v1",
                "envelope_id": envelope_id,
                "scenario": "synthetic",
                "set_id": set_id,
                "witness_id": witness_id,
                "expected_source_volume_uuid": source_uuid,
                "expected_source_root_id": source_root_id,
                "expected_volume_uuid": EXPECTED[0],
                "expected_root_id": EXPECTED[1],
                "source_frontier": source_frontier,
                "durable_frontier": durable_frontier,
            }
            create_frontier_envelope(
                writer,
                lambda: _fresh_reader(writer, revalidate),
                spec,
                witness_targets=targets,
                expected_storage_identity=EXPECTED,
                expected_source_volume_uuid=source_uuid,
                expected_source_root_id=source_root_id,
                revalidate_identity=revalidate,
            )
            verified = verify_frontier_envelope(
                writer,
                envelope_id,
                spec,
                witness_targets=targets,
                expected_storage_identity=EXPECTED,
                expected_source_volume_uuid=source_uuid,
                expected_source_root_id=source_root_id,
                revalidate_identity=revalidate,
            )
            assert verified["restore_witness_bound"] is True
            assert verified["factory_reset_ready"] is False
            assert verified["durable_frontier_K_vector_count"] == 1
            assert stat.S_ISREG(targets["sqlite_backup"].lstat().st_mode)
            # The API intentionally leaves empty coordination lock files in their namespaces.
            for path in coordination_paths:
                lock_stat = writer.stat(path)
                assert stat.S_ISREG(lock_stat.st_mode) and lock_stat.st_size == 0
            assert set(writer.list_names(f"{SET_BASE}/{set_id}")) == {
                "RESERVATION.json",
                "INDEX.json",
                "COMPLETE.json",
            }
            assert set(writer.list_names(witness_parent)) == {witness_id}
            assert set(writer.list_names(durable_base)) == {
                "OBJECT-" + digest + ".bin",
                "STOP-" + digest + ".json",
                "END-" + digest + ".json",
                "MANIFEST.json",
                "COMPLETE.json",
            }
            assert set(writer.list_names(envelope_path)) == {
                "RESERVATION.json",
                "INDEX.json",
                "COMPLETE.json",
            }
            for role in REQUIRED_ROLES:
                if role == "protected_d43":
                    tree_v2.verify_tree(writer, new_ids[role])
                elif role != "sqlite_backup":
                    tree_v3.verify_tree(writer, new_ids[role])
                source_path = f"{source_bases[role]}/{old_ids[role]}"
                destination_path = f"{source_bases[role]}/{new_ids[role]}"
                assert _tree_inventory(temp_writer, source_path) == _tree_inventory(
                    writer, destination_path
                )
            fully_verified = True
            for relative in cleanup_paths:
                _remove_tree(writer, relative, revalidate)
            if not durable_parent_preexisting and not writer.list_names(
                "host-recovery/synthetic-durable-frontiers"
            ):
                host_recovery_fd = writer._directory("host-recovery")
                try:
                    revalidate()
                    os.rmdir("synthetic-durable-frontiers", dir_fd=host_recovery_fd)
                    os.fsync(host_recovery_fd)
                finally:
                    os.close(host_recovery_fd)
            assert writer.list_names(witness_parent) == ()
            _remove_tree(writer, witness_parent, revalidate)
            writer.unlink(reservation_file)
            reservation_parent_fd = writer._directory(reservation_parent)
            try:
                revalidate()
                os.rmdir(run_id, dir_fd=reservation_parent_fd)
                os.fsync(reservation_parent_fd)
            finally:
                os.close(reservation_parent_fd)
            if not reservation_parent_preexisting and not writer.list_names(reservation_parent):
                host_recovery_fd = writer._directory("host-recovery")
                try:
                    revalidate()
                    os.rmdir("kingston-synthetic-rehearsals", dir_fd=host_recovery_fd)
                    os.fsync(host_recovery_fd)
                finally:
                    os.close(host_recovery_fd)
            assert revalidate() == EXPECTED
            started = False
    finally:
        temp_writer.close()
        if started and not fully_verified:
            print(f"incomplete synthetic run reserved for exact cleanup: {reservation_file}")
