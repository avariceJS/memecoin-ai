from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tools.brain_host_recovery_set import (
    BASE,
    SPEC_SCHEMA,
    SQLITE_BASE,
    RecoverySetError,
    _sqlite_member,
    _validate_sqlite_source_family,
    bind_restore_witness,
    create_set,
    verify_quarantine_restore,
    verify_set,
)
from tools.brain_host_tree_custody import TreeRoot
from tools.brain_host_tree_custody_v3 import POLICY_SCHEMA, capture_tree
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

UUID = "4b42e014-2d40-4e2b-9a9b-4f5c08d9a741"
ROOT_ID = "9" * 32
RESEARCH_COMMIT = "1" * 40
OWNER_COMMIT = "2" * 40


def _writer(path: Path) -> DescriptorWriter:
    path.mkdir(mode=0o700)
    return DescriptorWriter(os.open(path, os.O_RDONLY | os.O_DIRECTORY))


def _policy(path: Path, exact: dict[str, dict[str, str]] | None = None) -> Path:
    value = {
        "schema": POLICY_SCHEMA,
        "policy_id": "synthetic-set-policy",
        "version": 1,
        "rule_set": "owner-work-sensitive-v1",
        "pem_candidate_dispositions": {},
        "exact_path_exclusions": exact or {},
        "symlink_target_dispositions": {},
        "rebuild_inputs": [],
    }
    path.write_bytes(canonical_json(value))
    return path


def _capture(writer: DescriptorWriter, path: Path, capture_id: str, role: str, exact=None):
    policy_path = _policy(path.parent / f"{role}-{capture_id}.policy.json", exact)
    result = capture_tree(
        writer,
        TreeRoot(role, f"{role}-authority", f"point-{capture_id}", path, "non_protected"),
        policy_path,
        capture_id=capture_id,
    )
    base = f"host-recovery/tree-custody-v3/{capture_id}"
    manifest = writer.read_bytes(f"{base}/MANIFEST.json", max_bytes=16 * 1024 * 1024)
    complete = writer.read_bytes(f"{base}/COMPLETE.json", max_bytes=65536)
    return {
        "role": role,
        "format": "tree-custody-v3",
        "artifact_id": capture_id,
        "manifest_sha256": sha256(manifest),
        "complete_sha256": sha256(complete),
        "scope": "non_protected",
    }, result


def _setup_set(tmp_path: Path, *, sqlite_disposition: str = "substitute_online_backup"):
    import tools.brain_host_sqlite_backup as backup_tool
    import tools.brain_host_tree_custody as tree_v2

    archive_path = tmp_path / "archive"
    writer = _writer(archive_path)
    owner_root = (tmp_path / "owner-work").resolve()
    owner_root.mkdir()
    d43 = owner_root / "calendar-execution-01"
    d43.mkdir()
    (d43 / "opaque.bin").write_bytes(b"synthetic protected bytes")
    db_path = owner_root / "brain-b13" / "operator04" / "operator.sqlite"
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE sample(value TEXT)")
        conn.execute("INSERT INTO sample VALUES ('synthetic')")
        conn.commit()
    finally:
        conn.close()

    exact_exclusions = {
        "calendar-execution-01": {
            "entry_type": "directory",
            "disposition": "separate_protected_custody",
        },
        "brain-b13/operator04/operator.sqlite": {
            "entry_type": "file",
            "disposition": sqlite_disposition,
        },
    }
    owner_member, _ = _capture(
        writer,
        owner_root,
        "a" * 32,
        "owner_work",
        exact_exclusions,
    )
    research_root = (tmp_path / "research-work").resolve()
    research_root.mkdir()
    (research_root / "research.txt").write_text("synthetic", encoding="utf-8")
    os.link(research_root / "research.txt", research_root / "research-alias.txt")
    research_member, _ = _capture(writer, research_root, "b" * 32, "research_work")

    private_root = (tmp_path / "private-config").resolve()
    private_root.mkdir()
    (private_root / "config.json").write_text("{}", encoding="utf-8")
    config_member, _ = _capture(writer, private_root, "c" * 32, "private_config")

    launch_root = (tmp_path / "launch-defs").resolve()
    launch_root.mkdir()
    (launch_root / "service.plist").write_bytes(b"synthetic definition")
    launch_member, _ = _capture(writer, launch_root, "d" * 32, "launchagent_definitions")

    tree_v2.capture_tree(
        writer,
        TreeRoot(
            "calendar-execution-01",
            "d43-authority",
            "d43-point",
            d43,
            "protected_opaque",
        ),
        capture_id="e" * 32,
    )
    protected_base = f"{tree_v2.BASE}/{'e' * 32}"
    protected_manifest = writer.read_bytes(
        f"{protected_base}/MANIFEST.json", max_bytes=tree_v2.MAX_MANIFEST_BYTES
    )
    protected_complete = writer.read_bytes(f"{protected_base}/COMPLETE.json", max_bytes=65536)
    protected_member = {
        "role": "protected_d43",
        "format": "tree-custody-v2",
        "artifact_id": "e" * 32,
        "manifest_sha256": sha256(protected_manifest),
        "complete_sha256": sha256(protected_complete),
        "scope": "protected_opaque",
    }

    local_backup_dir = tmp_path / "sqlite-private"
    local_backup_dir.mkdir(mode=0o700)
    local_backup = local_backup_dir / "backup.sqlite"
    receipt = backup_tool.backup_read_only(
        db_path,
        local_backup,
        allowed_root=owner_root,
        protected_roots=[d43],
        logical_id="operator04-synthetic",
        max_output_bytes=1024 * 1024,
    )
    sqlite_id = "f" * 32
    sqlite_base = f"{SQLITE_BASE}/{sqlite_id}"
    writer.mkdir(sqlite_base)
    backup_bytes = local_backup.read_bytes()
    writer.create(f"{sqlite_base}/backup.sqlite", backup_bytes)
    receipt_raw = canonical_json(receipt)
    writer.create(f"{sqlite_base}/RECEIPT.json", receipt_raw)
    sqlite_complete = canonical_json(
        {
            "schema": "brain-host-sqlite-online-artifact-complete-v1",
            "artifact_id": sqlite_id,
            "backup_sha256": sha256(backup_bytes),
            "backup_size_bytes": len(backup_bytes),
            "receipt_sha256": sha256(receipt_raw),
        }
    )
    writer.create(f"{sqlite_base}/COMPLETE.json", sqlite_complete)
    sqlite_member = {
        "role": "sqlite_backup",
        "format": "sqlite-online-backup-v1",
        "artifact_id": sqlite_id,
        "manifest_sha256": sha256(receipt_raw),
        "complete_sha256": sha256(sqlite_complete),
        "scope": "non_protected_sqlite_backup",
    }
    spec = {
        "schema": SPEC_SCHEMA,
        "set_id": "f" * 32,
        "volume_uuid": UUID,
        "root_id": ROOT_ID,
        "research_commit": RESEARCH_COMMIT,
        "owner_commit": OWNER_COMMIT,
        "members": [
            owner_member,
            research_member,
            config_member,
            launch_member,
            sqlite_member,
            protected_member,
        ],
    }
    return writer, spec, db_path, sqlite_base


def _identity():
    return UUID, ROOT_ID


def test_create_and_verify_set_binds_members_and_reports_limited_claims(tmp_path: Path) -> None:
    writer, spec, _, _ = _setup_set(tmp_path)
    try:
        result = create_set(
            writer,
            spec,
            expected_volume_uuid=UUID,
            expected_root_id=ROOT_ID,
            revalidate_identity=_identity,
        )
        assert result["members_verified"] is True
        assert result["coverage_resolved"] is True
        assert result["restore_witness_bound"] is False
        assert result["application_recovery_qualified"] is False
        assert result["source_root_closure_verified"] is False
        assert result["factory_reset_ready"] is False
        assert "calendar-execution-01" not in canonical_json(result).decode()
        verified = verify_set(
            writer,
            spec["set_id"],
            expected_volume_uuid=UUID,
            expected_root_id=ROOT_ID,
            revalidate_identity=_identity,
        )
        assert verified["members_verified"] is True
        assert verified["d43_exclusion_bound_to_opaque_capture"] is True
        assert verified["sqlite_substitution_bound_to_backup"] is True
        assert verified["rebuild_inputs_hash_bound"] is True
        assert verified["application_recovery_qualified"] is False
        assert verified["source_root_closure_verified"] is False
        assert verified["factory_reset_ready"] is False
    finally:
        writer.close()


def test_wrong_volume_member_scope_and_collision_fail_closed(tmp_path: Path) -> None:
    writer, spec, _, _ = _setup_set(tmp_path)
    try:
        with pytest.raises(RecoverySetError, match="set_identity_pin_mismatch"):
            create_set(
                writer,
                spec,
                expected_volume_uuid="wrong-volume",
                expected_root_id=ROOT_ID,
                revalidate_identity=_identity,
            )
        wrong_scope = json.loads(canonical_json(spec))
        next(item for item in wrong_scope["members"] if item["role"] == "protected_d43")[
            "scope"
        ] = "non_protected"
        with pytest.raises(RecoverySetError, match="spec_member_scope_invalid"):
            create_set(
                writer,
                wrong_scope,
                expected_volume_uuid=UUID,
                expected_root_id=ROOT_ID,
                revalidate_identity=_identity,
            )
        wrong_pin = json.loads(canonical_json(spec))
        next(item for item in wrong_pin["members"] if item["role"] == "protected_d43")[
            "manifest_sha256"
        ] = "0" * 64
        with pytest.raises(RecoverySetError, match="member_pin_mismatch"):
            create_set(
                writer,
                wrong_pin,
                expected_volume_uuid=UUID,
                expected_root_id=ROOT_ID,
                revalidate_identity=_identity,
            )
        create_set(
            writer,
            spec,
            expected_volume_uuid=UUID,
            expected_root_id=ROOT_ID,
            revalidate_identity=_identity,
        )
        with pytest.raises(RecoverySetError, match="set_id_collision"):
            create_set(
                writer,
                spec,
                expected_volume_uuid=UUID,
                expected_root_id=ROOT_ID,
                revalidate_identity=_identity,
            )
    finally:
        writer.close()


def test_wrong_sqlite_substitution_disposition_does_not_resolve_coverage(
    tmp_path: Path,
) -> None:
    writer, spec, _, _ = _setup_set(tmp_path, sqlite_disposition="disposable_not_required")
    try:
        result = create_set(
            writer,
            spec,
            expected_volume_uuid=UUID,
            expected_root_id=ROOT_ID,
            revalidate_identity=_identity,
        )
        assert result["members_verified"] is True
        assert result["sqlite_substitution_bound_to_backup"] is False
        assert result["coverage_resolved"] is False
    finally:
        writer.close()


def test_interrupted_publication_and_identity_change_leave_no_complete(tmp_path: Path) -> None:
    writer, spec, _, _ = _setup_set(tmp_path)
    try:

        def interrupt(stage: str) -> None:
            if stage == "after_index":
                raise RuntimeError("simulated interruption")

        with pytest.raises(RuntimeError, match="simulated interruption"):
            create_set(
                writer,
                spec,
                expected_volume_uuid=UUID,
                expected_root_id=ROOT_ID,
                revalidate_identity=_identity,
                fault_hook=interrupt,
            )
        assert not writer.exists(f"{BASE}/{spec['set_id']}/COMPLETE.json")

        changed = False

        def detach_after_index():
            nonlocal changed
            if changed:
                return "wrong-volume", ROOT_ID
            changed = True
            return UUID, ROOT_ID

        other_id = "0" * 32
        spec2 = dict(spec, set_id=other_id)
        with pytest.raises(RecoverySetError, match="set_identity_changed"):
            create_set(
                writer,
                spec2,
                expected_volume_uuid=UUID,
                expected_root_id=ROOT_ID,
                revalidate_identity=detach_after_index,
            )
        assert not writer.exists(f"{BASE}/{other_id}/COMPLETE.json")
    finally:
        writer.close()


def test_sqlite_verifier_allows_transient_shm_but_rejects_durable_wal_drift(
    tmp_path: Path,
) -> None:
    writer, _, _, base = _setup_set(tmp_path)
    try:
        original = json.loads(writer.read_bytes(f"{base}/RECEIPT.json", max_bytes=262144))
        changed_shm = json.loads(canonical_json(original))
        changed_shm["source_family_before"]["-shm"] = None
        changed_shm["source_family_after"]["-shm"] = {
            "device": 1,
            "inode": 2,
            "mode": 0o600,
            "uid": os.getuid(),
            "gid": os.getgid(),
            "size_bytes": 64,
            "mtime_ns": 10,
            "ctime_ns": 11,
        }
        changed_shm["source_shm_signature_changed"] = True
        _validate_sqlite_source_family(changed_shm)

        durable_drift = json.loads(canonical_json(changed_shm))
        durable_drift["source_family_before"]["-wal"] = None
        durable_drift["source_family_after"]["-wal"] = {
            "device": 1,
            "inode": 3,
            "mode": 0o600,
            "uid": os.getuid(),
            "gid": os.getgid(),
            "size_bytes": 1,
            "mtime_ns": 1,
            "ctime_ns": 1,
        }
        with pytest.raises(RecoverySetError, match="sqlite_source_durable_family_drift"):
            _validate_sqlite_source_family(durable_drift)
    finally:
        writer.close()


def test_sqlite_artifact_sidecars_are_rejected(tmp_path: Path) -> None:
    writer, spec, _, base = _setup_set(tmp_path)
    try:
        writer.create(f"{base}/backup.sqlite-wal", b"unexpected")
        member = next(item for item in spec["members"] if item["role"] == "sqlite_backup")
        with pytest.raises(RecoverySetError, match="sqlite_artifact_membership_invalid"):
            _sqlite_member(writer, member)
    finally:
        writer.close()


def test_restore_witness_binds_quarantine_tree_bytes_and_topology(tmp_path: Path) -> None:
    import tools.brain_host_tree_custody as tree_v2
    import tools.brain_host_tree_custody_v3 as tree_v3

    writer, spec, _, _ = _setup_set(tmp_path)
    try:
        create_set(
            writer,
            spec,
            expected_volume_uuid=UUID,
            expected_root_id=ROOT_ID,
            revalidate_identity=_identity,
        )
        by_role = {item["role"]: item for item in spec["members"]}
        targets = {
            "owner_work": tmp_path / "quarantine-owner",
            "research_work": tmp_path / "quarantine-research",
            "private_config": tmp_path / "quarantine-config",
            "launchagent_definitions": tmp_path / "quarantine-launchagents",
            "protected_d43": tmp_path / "quarantine-d43",
            "sqlite_backup": tmp_path / "quarantine-sqlite" / "backup.sqlite",
        }
        tree_v3.extract_tree(writer, by_role["owner_work"]["artifact_id"], targets["owner_work"])
        tree_v3.extract_tree(
            writer, by_role["research_work"]["artifact_id"], targets["research_work"]
        )
        tree_v3.extract_tree(
            writer, by_role["private_config"]["artifact_id"], targets["private_config"]
        )
        tree_v3.extract_tree(
            writer,
            by_role["launchagent_definitions"]["artifact_id"],
            targets["launchagent_definitions"],
        )
        tree_v2.extract_tree(
            writer, by_role["protected_d43"]["artifact_id"], targets["protected_d43"]
        )
        targets["sqlite_backup"].parent.mkdir()
        sqlite_base = f"{SQLITE_BASE}/{by_role['sqlite_backup']['artifact_id']}"
        targets["sqlite_backup"].write_bytes(writer.read_bytes(f"{sqlite_base}/backup.sqlite"))
        witness = bind_restore_witness(
            writer,
            spec["set_id"],
            "8" * 32,
            targets,
            expected_volume_uuid=UUID,
            expected_root_id=ROOT_ID,
            revalidate_identity=_identity,
        )
        assert witness["restore_witness_bound"] is True
        assert witness["application_recovery_qualified"] is False
        assert witness["source_root_closure_verified"] is False
        assert witness["factory_reset_ready"] is False
        verified = verify_set(
            writer,
            spec["set_id"],
            expected_volume_uuid=UUID,
            expected_root_id=ROOT_ID,
            revalidate_identity=_identity,
            witness_id="8" * 32,
            witness_targets=targets,
        )
        assert verified["restore_witness_bound"] is True
        assert verified["restore_mapping_obligations_resolved"] is True
        assert verified["factory_reset_ready"] is False
        owner_marker = targets["owner_work"] / "RESTORE_COMPLETE.json"
        marker_value = json.loads(owner_marker.read_bytes())
        marker_value["schema"] = "brain-host-tree-custody-restore-v2"
        owner_marker.write_bytes(canonical_json(marker_value))
        with pytest.raises(RecoverySetError, match="restore_witness_marker_mismatch"):
            verify_set(
                writer,
                spec["set_id"],
                expected_volume_uuid=UUID,
                expected_root_id=ROOT_ID,
                revalidate_identity=_identity,
                witness_id="8" * 32,
                witness_targets=targets,
            )
        marker_value["schema"] = "brain-host-tree-custody-v3-restore"
        owner_marker.write_bytes(canonical_json(marker_value))
        sqlite_target = targets["sqlite_backup"]
        sqlite_bytes = sqlite_target.read_bytes()
        sqlite_target.write_bytes(b"x" * len(sqlite_bytes))
        with pytest.raises(RecoverySetError, match="restore_witness_sqlite_hash_mismatch"):
            verify_set(
                writer,
                spec["set_id"],
                expected_volume_uuid=UUID,
                expected_root_id=ROOT_ID,
                revalidate_identity=_identity,
                witness_id="8" * 32,
                witness_targets=targets,
            )
        sqlite_target.write_bytes(sqlite_bytes)
        restored_research = targets["research_work"] / "research_work" / "research.txt"
        restored_research.write_text("changed", encoding="utf-8")
        with pytest.raises(RecoverySetError, match="restore_witness_metadata_mismatch"):
            verify_set(
                writer,
                spec["set_id"],
                expected_volume_uuid=UUID,
                expected_root_id=ROOT_ID,
                revalidate_identity=_identity,
                witness_id="8" * 32,
                witness_targets=targets,
            )
    finally:
        writer.close()


def test_static_v1_restore_is_witnessed_by_file_hash_and_membership(tmp_path: Path) -> None:
    import tools.brain_host_recovery_capture as static_v1

    archive = tmp_path / "archive"
    writer = _writer(archive)
    source = tmp_path / "static-source"
    source.mkdir()
    (source / "definition.plist").write_bytes(b"synthetic plist bytes")
    capture_id = "7" * 32
    try:
        static_v1.capture_static_roots(
            writer,
            [static_v1.StaticRoot("private-config", "authority", "point", source)],
            capture_id=capture_id,
        )
        base = f"{static_v1.CAPTURES_DIR}/{capture_id}"
        manifest = writer.read_bytes(
            f"{base}/manifest.json", max_bytes=static_v1.MAX_METADATA_BYTES
        )
        complete = writer.read_bytes(f"{base}/COMPLETE", max_bytes=16384)
        member = {
            "role": "private_config",
            "format": "static-recovery-v1",
            "artifact_id": capture_id,
            "manifest_sha256": sha256(manifest),
            "complete_sha256": sha256(complete),
            "scope": "non_protected_static",
        }
        destination = tmp_path / "static-quarantine"
        static_v1.extract_capture(writer, capture_id, destination)
        observed = verify_quarantine_restore(writer, member, destination)
        assert observed["entry_count"] == 2
        (destination / "private-config" / "extra.txt").write_text("unexpected", encoding="utf-8")
        with pytest.raises(
            RecoverySetError,
            match="restore_witness_(metadata|membership)_mismatch",
        ):
            verify_quarantine_restore(writer, member, destination)
    finally:
        writer.close()


def test_recovery_set_cli_supports_module_invocation_from_repo_root() -> None:
    repo = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((".", "src", env.get("PYTHONPATH", "")))
    result = subprocess.run(
        [sys.executable, "-m", "tools.brain_host_recovery_set", "--help"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "bind-witness" in result.stdout
