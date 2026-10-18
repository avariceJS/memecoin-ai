from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.brain_host_tree_custody import TreeCustodyError, TreeRoot
from tools.brain_host_tree_custody_v3 import (
    BASE,
    POLICY_SCHEMA,
    TreeCustodyV3Error,
    capture_tree,
    extract_tree,
    verify_tree,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json


def _writer(path: Path) -> DescriptorWriter:
    path.mkdir(mode=0o700)
    return DescriptorWriter(os.open(path, os.O_RDONLY | os.O_DIRECTORY))


def _policy(
    path: Path,
    pem: dict[str, str] | None = None,
    exact: dict[str, dict[str, str]] | None = None,
    rebuild_inputs: list[dict[str, str]] | None = None,
    symlinks: dict[str, dict[str, object]] | None = None,
) -> Path:
    pem_values = pem or {}
    exact_values = exact or {}
    needs_rebuild = "rebuild_disposable" in pem_values.values() or any(
        item["disposition"] == "rebuild_disposable" for item in exact_values.values()
    )
    inputs = rebuild_inputs
    if inputs is None and needs_rebuild:
        inputs = [
            {"role": "bootstrap", "path": "Makefile", "sha256": "a" * 64},
            {"role": "dependency_lock", "path": "requirements-dev.lock", "sha256": "b" * 64},
            {"role": "dependency_lock", "path": "requirements-ml.lock", "sha256": "c" * 64},
        ]
    value = {
        "schema": POLICY_SCHEMA,
        "policy_id": "synthetic-owner-work-policy",
        "version": 1,
        "rule_set": "owner-work-sensitive-v1",
        "pem_candidate_dispositions": pem_values,
        "exact_path_exclusions": exact_values,
        "symlink_target_dispositions": symlinks or {},
        "rebuild_inputs": sorted(inputs or [], key=lambda item: (item["role"], item["path"])),
    }
    path.write_bytes(canonical_json(value))
    return path


def _root(path: Path, scope: str = "non_protected") -> TreeRoot:
    return TreeRoot("sample-root", "synthetic-authority", "asserted-point", path, scope)


def _bind_writer_to_storage_root(
    writer: DescriptorWriter, path: Path, volume_uuid: str, root_id: str
) -> None:
    root_stat = os.fstat(writer.root_fd)
    if (root_stat.st_dev, root_stat.st_ino) != (path.stat().st_dev, path.stat().st_ino):
        raise AssertionError("synthetic writer root does not match fixture path")
    resolved = SimpleNamespace(
        volume_uuid=volume_uuid,
        root_id=root_id,
        path=path,
        mount_device=root_stat.st_dev,
        mount_inode=root_stat.st_ino,
    )
    guard = SimpleNamespace(
        volume_uuid=volume_uuid,
        root_id=root_id,
        revalidate=lambda: resolved,
    )
    writer._custody_mutation_guard = SimpleNamespace(guard=guard)


def test_capture_verify_restore_omits_sensitive_files_and_untraversed_subtrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tools.brain_host_tree_custody as custody_v2

    source = tmp_path / "source"
    source.mkdir()
    (source / "ordinary.txt").write_text("keep this", encoding="utf-8")
    (source / ".env.local").write_text("synthetic secret must never be read", encoding="utf-8")
    (source / "unknown.pem").write_text("private key candidate", encoding="utf-8")
    private = source / ".ssh"
    private.mkdir()
    (private / "id_ed25519").write_text("opaque secret", encoding="utf-8")
    os.symlink(".env.local", source / "env-link")
    policy_path = _policy(tmp_path / "policy.json")
    writer = _writer(tmp_path / "archive")
    original_chunk_file = custody_v2._chunk_file
    read_paths: list[str] = []

    def record_chunk_path(
        writer_arg: object,
        base: str,
        file_id: str,
        root_fd: int,
        db: object,
        row: tuple[object, ...],
    ):
        read_paths.append(str(row[0]))
        return original_chunk_file(writer_arg, base, file_id, root_fd, db, row)

    monkeypatch.setattr(custody_v2, "_chunk_file", record_chunk_path)
    try:
        result = capture_tree(writer, _root(source), policy_path, capture_id="a" * 32)
        assert read_paths == ["ordinary.txt"]
        verified = verify_tree(writer, "a" * 32)
        assert result["complete"] and verified["complete"]
        assert not verified["full_recovery_ready"]
        assert verified["unresolved_exclusion_count"] == 2
        assert "source_root_sha256" not in result
        restored = tmp_path / "restored"
        extract_tree(writer, "a" * 32, restored)
        tree = restored / "sample-root"
        assert (tree / "ordinary.txt").read_text(encoding="utf-8") == "keep this"
        assert not (tree / ".env.local").exists()
        assert not (tree / "unknown.pem").exists()
        assert not (tree / ".ssh").exists()
        assert not (tree / "env-link").exists()
        assert (restored / "RESTORE_COMPLETE.json").exists()
        assert not list((tmp_path / "archive" / BASE / ("a" * 32)).rglob("*secret*"))
    finally:
        writer.close()


def test_hardlink_alias_to_excluded_inode_is_excluded_without_copy(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    secret = source / ".env.prod"
    secret.write_bytes(b"never include")
    os.link(secret, source / "ordinary-looking.bin")
    policy_path = _policy(tmp_path / "policy.json")
    writer = _writer(tmp_path / "archive")
    try:
        result = capture_tree(writer, _root(source), policy_path, capture_id="b" * 32)
        assert result["excluded_entry_count"] == 2
        assert result["included_source_bytes"] == 0
        assert verify_tree(writer, "b" * 32)["full_recovery_ready"]
    finally:
        writer.close()


def test_explicit_public_pem_is_bound_and_policy_pin_is_enforced(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "public.pem").write_text("approved public fixture", encoding="utf-8")
    policy_path = _policy(tmp_path / "policy.json", {"public.pem": "include_nonsecret"})
    writer = _writer(tmp_path / "archive")
    try:
        result = capture_tree(writer, _root(source), policy_path, capture_id="c" * 32)
        assert result["full_recovery_ready"]
        restored = tmp_path / "restored"
        extract_tree(writer, "c" * 32, restored)
        assert (restored / "sample-root" / "public.pem").read_text(
            encoding="utf-8"
        ) == "approved public fixture"
        with pytest.raises(TreeCustodyV3Error, match="policy_pin_mismatch"):
            verify_tree(writer, "c" * 32, expected_policy_sha256="0" * 64)
    finally:
        writer.close()


def test_exclusion_page_corruption_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / ".env.local").write_text("secret", encoding="utf-8")
    policy_path = _policy(tmp_path / "policy.json")
    archive = tmp_path / "archive"
    writer = _writer(archive)
    try:
        capture_tree(writer, _root(source), policy_path, capture_id="d" * 32)
        base = archive / BASE / ("d" * 32)
        page = next((base / "exclusion-pages").glob("*.jsonl"))
        page.chmod(0o600)
        page.write_bytes(page.read_bytes().replace(b"env_filename_prefix", b"tampered_rule_name"))
        with pytest.raises((TreeCustodyV3Error, TreeCustodyError)):
            verify_tree(writer, "d" * 32)
    finally:
        writer.close()


def test_prepublication_source_drift_leaves_no_complete_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tools.brain_host_tree_custody_v3 as custody

    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_text("stable", encoding="utf-8")
    policy_path = _policy(tmp_path / "policy.json")
    writer = _writer(tmp_path / "archive")
    original = custody._inventory
    calls = 0

    def drift(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 2:
            (source / "new-file").write_text("membership drift", encoding="utf-8")
        return original(*args, **kwargs)

    monkeypatch.setattr(custody, "_inventory", drift)
    try:
        with pytest.raises(TreeCustodyV3Error, match="source_membership_drift"):
            capture_tree(writer, _root(source), policy_path, capture_id="e" * 32)
        assert not writer.exists(f"{BASE}/{'e' * 32}/COMPLETE.json")
    finally:
        writer.close()


def test_policy_change_during_capture_leaves_no_complete_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tools.brain_host_tree_custody_v3 as custody

    source = tmp_path / "source"
    source.mkdir()
    (source / "stable.txt").write_text("content", encoding="utf-8")
    policy_path = _policy(tmp_path / "policy.json")
    writer = _writer(tmp_path / "archive")
    original = custody._inventory
    calls = 0

    def mutate_policy(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        if calls == 2:
            changed = {
                "schema": POLICY_SCHEMA,
                "policy_id": "changed-policy",
                "version": 1,
                "rule_set": "owner-work-sensitive-v1",
                "pem_candidate_dispositions": {},
                "exact_path_exclusions": {},
                "symlink_target_dispositions": {},
                "rebuild_inputs": [],
            }
            policy_path.write_bytes(canonical_json(changed))
        return result

    monkeypatch.setattr(custody, "_inventory", mutate_policy)
    try:
        with pytest.raises(TreeCustodyV3Error, match="policy_changed_during_capture"):
            capture_tree(writer, _root(source), policy_path, capture_id="f" * 32)
        assert not writer.exists(f"{BASE}/{'f' * 32}/COMPLETE.json")
    finally:
        writer.close()


def test_self_verification_failure_never_publishes_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tools.brain_host_tree_custody_v3 as custody

    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_text("content", encoding="utf-8")
    policy_path = _policy(tmp_path / "policy.json")
    writer = _writer(tmp_path / "archive")

    def fail_verification(*args: object, **kwargs: object):
        raise TreeCustodyV3Error("injected_self_verification_failure")

    monkeypatch.setattr(custody, "_verify_capture_objects", fail_verification)
    try:
        with pytest.raises(TreeCustodyV3Error, match="injected_self_verification_failure"):
            capture_tree(writer, _root(source), policy_path, capture_id="4" * 32)
        assert not writer.exists(f"{BASE}/{'4' * 32}/COMPLETE.json")
    finally:
        writer.close()


def test_exclusion_inventory_pages_above_page_limit(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for index in range(530):
        (source / f".env.{index:04d}").write_bytes(b"synthetic excluded bytes")
    policy_path = _policy(tmp_path / "policy.json")
    writer = _writer(tmp_path / "archive")
    try:
        result = capture_tree(writer, _root(source), policy_path, capture_id="1" * 32)
        assert result["excluded_entry_count"] == 530
        pages = writer.list_names(f"{BASE}/{'1' * 32}/exclusion-pages")
        assert len(pages) == 2
        verified = verify_tree(writer, "1" * 32)
        assert verified["exclusion_kind_counts"] == {"file": 530}
    finally:
        writer.close()


def test_protected_opaque_capture_has_aggregate_only_result(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "D43").mkdir()
    (source / "D43" / "synthetic.bin").write_bytes(b"opaque fixture")
    policy_path = _policy(tmp_path / "policy.json")
    writer = _writer(tmp_path / "archive")
    try:
        result = capture_tree(
            writer,
            _root(source, "protected_opaque"),
            policy_path,
            capture_id="2" * 32,
        )
        verified = verify_tree(writer, "2" * 32)
        serialized = canonical_json({"capture": result, "verify": verified}).decode()
        assert "D43" not in serialized
        assert "synthetic.bin" not in serialized
        assert result["scope"] == verified["scope"] == "protected_opaque"
    finally:
        writer.close()


def test_exact_policy_subtrees_and_sqlite_family_are_metadata_only(tmp_path: Path) -> None:
    import tools.brain_host_tree_custody as custody_v2

    source = tmp_path / "source"
    source.mkdir()
    protected_tree = source / "protected-runtime"
    protected_tree.mkdir()
    os.mkfifo(protected_tree / "must-not-traverse")
    db_root = source / "workspace-data" / "engine"
    db_root.mkdir(parents=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        (db_root / f"runtime.sqlite{suffix}").write_bytes(b"synthetic opaque database bytes")
    (source / "notes.txt").write_text("included", encoding="utf-8")
    exact = {
        "protected-runtime": {
            "entry_type": "directory",
            "disposition": "separate_protected_custody",
        },
        **{
            f"workspace-data/engine/runtime.sqlite{suffix}": {
                "entry_type": "file",
                "disposition": "substitute_online_backup",
            }
            for suffix in ("", "-wal", "-shm", "-journal")
        },
    }
    policy_path = _policy(tmp_path / "policy.json", exact=exact)
    writer = _writer(tmp_path / "archive")
    original_chunk_file = custody_v2._chunk_file
    read_paths: list[str] = []

    def record_chunk_path(
        writer_arg: object,
        base: str,
        file_id: str,
        root_fd: int,
        db: object,
        row: tuple[object, ...],
    ):
        read_paths.append(str(row[0]))
        return original_chunk_file(writer_arg, base, file_id, root_fd, db, row)

    try:
        custody_v2._chunk_file = record_chunk_path
        result = capture_tree(writer, _root(source), policy_path, capture_id="3" * 32)
        assert read_paths == ["notes.txt"]
        assert result["excluded_entry_count"] == 5
        assert result["included_source_bytes"] == len("included")
        verified = verify_tree(writer, "3" * 32)
        assert not verified["full_recovery_ready"]
        assert verified["external_custody_required_count"] == 5
        assert verified["unknown_descendant_subtree_count"] == 1
        assert "protected-runtime" not in canonical_json(result).decode()
    finally:
        custody_v2._chunk_file = original_chunk_file
        writer.close()


def test_disposable_environment_subtrees_and_python_aliases_are_not_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tools.brain_host_tree_custody as custody_v2

    source = tmp_path / "source"
    source.mkdir()
    venv_parent = source / "runtime" / "venvs"
    venv_parent.mkdir(parents=True)
    aliases = source / "runtime" / "python-links"
    aliases.mkdir()
    exact: dict[str, dict[str, str]] = {}
    for index in range(6):
        venv = venv_parent / f"env-{index}"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("synthetic environment metadata", encoding="utf-8")
        (venv / "lib").mkdir()
        (venv / "lib" / "cacert.pem").write_bytes(b"must not inspect nested candidate")
        (venv / "unique-runtime-state.bin").write_bytes(b"fixture state is intentionally unknown")
        os.mkfifo(venv / "must-not-walk")
        relative_venv = f"runtime/venvs/env-{index}"
        exact[relative_venv] = {
            "entry_type": "directory",
            "disposition": ("rebuild_disposable" if index == 0 else "disposable_not_required"),
        }
    for index in range(18):
        alias_name = aliases / f"python-{index}"
        os.symlink(f"/opt/homebrew/bin/python-{index}", alias_name)
        exact[f"runtime/python-links/python-{index}"] = {
            "entry_type": "symlink",
            "disposition": "disposable_not_required",
        }
    os.link(
        venv_parent / "env-0" / "unique-runtime-state.bin",
        source / "outside-hardlink.bin",
    )
    (source / "requirements-dev.lock").write_text("locked development dependencies\n")
    (source / "requirements-ml.lock").write_text("locked ML dependencies\n")
    (source / "Makefile").write_text("clean-room setup contract\n")
    (source / "ordinary.txt").write_text("preserved", encoding="utf-8")
    dev_lock_sha256 = hashlib.sha256((source / "requirements-dev.lock").read_bytes()).hexdigest()
    ml_lock_sha256 = hashlib.sha256((source / "requirements-ml.lock").read_bytes()).hexdigest()
    policy_path = _policy(
        tmp_path / "policy.json",
        exact=exact,
        rebuild_inputs=[
            {
                "role": "bootstrap",
                "path": "Makefile",
                "sha256": hashlib.sha256((source / "Makefile").read_bytes()).hexdigest(),
            },
            {
                "role": "dependency_lock",
                "path": "requirements-dev.lock",
                "sha256": dev_lock_sha256,
            },
            {
                "role": "dependency_lock",
                "path": "requirements-ml.lock",
                "sha256": ml_lock_sha256,
            },
        ],
    )
    writer = _writer(tmp_path / "archive")
    original_chunk_file = custody_v2._chunk_file
    read_paths: list[str] = []

    def record_chunk_path(
        writer_arg: object,
        base: str,
        file_id: str,
        root_fd: int,
        db: object,
        row: tuple[object, ...],
    ):
        read_paths.append(str(row[0]))
        return original_chunk_file(writer_arg, base, file_id, root_fd, db, row)

    monkeypatch.setattr(custody_v2, "_chunk_file", record_chunk_path)

    def forbidden_readlink(*args: object, **kwargs: object) -> str:
        raise AssertionError("excluded executable symlink target was inspected")

    monkeypatch.setattr(os, "readlink", forbidden_readlink)
    try:
        capture = capture_tree(writer, _root(source), policy_path, capture_id="9" * 32)
        verified = verify_tree(writer, "9" * 32)
        assert read_paths == [
            "Makefile",
            "ordinary.txt",
            "requirements-dev.lock",
            "requirements-ml.lock",
        ]
        assert capture["excluded_entry_count"] == 25
        assert verified["unknown_descendant_subtree_count"] == 6
        assert verified["rebuild_disposable_count"] == 1
        assert verified["rebuild_disposable_subtree_count"] == 1
        assert verified["disposable_not_required_count"] == 23
        assert verified["disposable_not_required_subtree_count"] == 5
        assert verified["rebuild_input_count"] == 3
        assert verified["unresolved_exclusion_count"] == 1
        assert verified["full_recovery_ready"] is False
        private_policy = writer.read_bytes(f"{BASE}/{'9' * 32}/POLICY.json", max_bytes=65536)
        policy_obj = json.loads(private_policy)
        assert {(item["role"], item["path"]) for item in policy_obj["rebuild_inputs"]} == {
            ("bootstrap", "Makefile"),
            ("dependency_lock", "requirements-dev.lock"),
            ("dependency_lock", "requirements-ml.lock"),
        }
        assert len(policy_obj["exact_path_exclusions"]) == 24
        assert (source / "runtime" / "venvs" / "env-0" / "unique-runtime-state.bin").exists()
        assert "runtime/venvs/env-0" not in canonical_json(capture).decode()
        restored = tmp_path / "restored"
        extract_tree(writer, "9" * 32, restored)
        tree = restored / "sample-root"
        for index in range(6):
            assert not (tree / "runtime" / "venvs" / f"env-{index}").exists()
        for index in range(18):
            assert not (tree / "runtime" / "python-links" / f"python-{index}").exists()
        assert not (tree / "outside-hardlink.bin").exists()
        assert (tree / "ordinary.txt").read_text(encoding="utf-8") == "preserved"
        assert (tree / "requirements-dev.lock").exists()
        assert (tree / "Makefile").exists()
        assert (tree / "requirements-ml.lock").exists()
        assert not list((tmp_path / "archive" / BASE / ("9" * 32)).rglob("*cacert.pem*"))
    finally:
        writer.close()


def test_disposable_rebuild_requires_locked_inputs(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "env").mkdir()
    policy_path = _policy(
        tmp_path / "policy.json",
        exact={"env": {"entry_type": "directory", "disposition": "rebuild_disposable"}},
        rebuild_inputs=[],
    )
    writer = _writer(tmp_path / "archive")
    try:
        with pytest.raises(TreeCustodyV3Error, match="policy_rebuild_inputs_incomplete"):
            capture_tree(writer, _root(source), policy_path, capture_id="8" * 32)
        assert not writer.exists(f"{BASE}/{'8' * 32}/COMPLETE.json")
    finally:
        writer.close()


def test_disposable_not_required_is_omitted_without_reconstruction(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for index in range(5):
        root = source / f"cache-{index}"
        root.mkdir()
        (root / "fixture-state.bin").write_bytes(b"not needed for recovery")
    (source / "ordinary.txt").write_text("preserved", encoding="utf-8")
    policy_path = _policy(
        tmp_path / "policy.json",
        exact={
            f"cache-{index}": {
                "entry_type": "directory",
                "disposition": "disposable_not_required",
            }
            for index in range(5)
        },
    )
    writer = _writer(tmp_path / "archive")
    try:
        capture = capture_tree(writer, _root(source), policy_path, capture_id="7" * 32)
        verified = verify_tree(writer, "7" * 32)
        assert capture["full_recovery_ready"] is True
        assert verified["full_recovery_ready"] is True
        assert verified["unknown_descendant_subtree_count"] == 5
        assert verified["disposable_not_required_count"] == 5
        assert verified["disposable_not_required_subtree_count"] == 5
        assert verified["rebuild_disposable_count"] == 0
        assert verified["rebuild_input_count"] == 0
        restored = tmp_path / "restored"
        extract_tree(writer, "7" * 32, restored)
        tree = restored / "sample-root"
        for index in range(5):
            assert not (tree / f"cache-{index}").exists()
        assert (tree / "ordinary.txt").read_text(encoding="utf-8") == "preserved"
    finally:
        writer.close()


def test_external_binding_maps_from_current_root_and_records_original_target(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    original_target = b"/private/var/tmp/rebuild-only-target"
    os.symlink(original_target, source / "temp-link")
    volume_uuid = "4b42e014-2d40-4e2b-9a9b-4f5c08d9a741"
    root_id = "9" * 32
    policy_path = _policy(
        tmp_path / "policy.json",
        symlinks={
            "temp-link": {
                "disposition": "external_root_binding",
                "required": True,
                "target_sha256": hashlib.sha256(original_target).hexdigest(),
                "volume_uuid": volume_uuid,
                "root_id": root_id,
                "relative_locator": "work/runtime",
                "authority_ref": "migration-fixture-1",
            }
        },
    )
    writer = _writer(tmp_path / "archive")
    target = tmp_path / "archive" / "work" / "runtime"
    target.mkdir(parents=True)
    (target / "ready.txt").write_text("synthetic bound dependency", encoding="utf-8")
    _bind_writer_to_storage_root(writer, tmp_path / "archive", volume_uuid, root_id)
    try:
        capture_tree(writer, _root(source), policy_path, capture_id="6" * 32)
        verified = verify_tree(writer, "6" * 32)
        assert verified["unresolved_exclusion_count"] == 1
        assert verified["symlink_disposition_counts"] == {"external_root_binding": 1}
        assert verified["full_recovery_ready"] is False
        destination = tmp_path / "restored"
        with pytest.raises(TreeCustodyV3Error, match="external_root_binding_mismatch"):
            extract_tree(
                writer,
                "6" * 32,
                destination,
                verified_external_root_bindings={
                    "temp-link": {
                        "volume_uuid": "wrong-volume",
                        "root_id": root_id,
                        "relative_locator": "work/runtime",
                        "authority_ref": "migration-fixture-1",
                    }
                },
            )
        assert not destination.exists()
        wrong_root_destination = tmp_path / "wrong-root-restored"
        with pytest.raises(TreeCustodyV3Error, match="external_root_binding_mismatch"):
            extract_tree(
                writer,
                "6" * 32,
                wrong_root_destination,
                verified_external_root_bindings={
                    "temp-link": {
                        "volume_uuid": volume_uuid,
                        "root_id": "8" * 32,
                        "relative_locator": "work/runtime",
                        "authority_ref": "migration-fixture-1",
                    }
                },
            )
        assert not wrong_root_destination.exists()
        restored = extract_tree(
            writer,
            "6" * 32,
            destination,
            verified_external_root_bindings={
                "temp-link": {
                    "volume_uuid": volume_uuid,
                    "root_id": root_id,
                    "relative_locator": "work/runtime",
                    "authority_ref": "migration-fixture-1",
                }
            },
        )
        assert restored["symlink_topology_preserved"] is True
        restored_link = destination / "sample-root" / "temp-link"
        assert restored_link.is_symlink()
        derived_target = os.fsencode(target)
        assert os.readlink(os.fsencode(restored_link)) == derived_target
        receipt = json.loads((destination / "RESTORE_COMPLETE.json").read_text())
        assert receipt["external_root_mappings"] == [
            {
                "path": "temp-link",
                "volume_uuid": volume_uuid,
                "root_id": root_id,
                "relative_locator": "work/runtime",
                "authority_ref": "migration-fixture-1",
                "original_target_b64": base64.b64encode(original_target).decode("ascii"),
                "derived_target_b64": base64.b64encode(derived_target).decode("ascii"),
            }
        ]
        assert restored["external_root_remappings_count"] == 1
        assert original_target != derived_target
        import shutil

        shutil.rmtree(target)
        missing_target_destination = tmp_path / "missing-target-restored"
        with pytest.raises(TreeCustodyV3Error, match="external_root_target_unavailable"):
            extract_tree(
                writer,
                "6" * 32,
                missing_target_destination,
                verified_external_root_bindings={
                    "temp-link": {
                        "volume_uuid": volume_uuid,
                        "root_id": root_id,
                        "relative_locator": "work/runtime",
                        "authority_ref": "migration-fixture-1",
                    }
                },
            )
        assert not missing_target_destination.exists()
    finally:
        writer.close()


def test_optional_dangling_internal_link_is_preserved_without_blocking_readiness(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    original_target = b"docs/not-installed.md"
    os.symlink(original_target, source / "docs-link")
    policy_path = _policy(
        tmp_path / "policy.json",
        symlinks={
            "docs-link": {
                "disposition": "preserve_dangling_internal",
                "required": False,
                "target_sha256": hashlib.sha256(original_target).hexdigest(),
                "relative_locator": "docs/not-installed.md",
            }
        },
    )
    writer = _writer(tmp_path / "archive")
    try:
        capture_tree(writer, _root(source), policy_path, capture_id="5" * 32)
        verified = verify_tree(writer, "5" * 32)
        assert verified["unresolved_exclusion_count"] == 0
        assert verified["full_recovery_ready"] is True
        destination = tmp_path / "restored"
        extract_tree(writer, "5" * 32, destination)
        restored = destination / "sample-root" / "docs-link"
        assert restored.is_symlink()
        assert os.readlink(os.fsencode(restored)) == original_target
    finally:
        writer.close()


def test_required_dangling_internal_link_blocks_readiness(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    original_target = b"required.db"
    os.symlink(original_target, source / "db-link")
    policy_path = _policy(
        tmp_path / "policy.json",
        symlinks={
            "db-link": {
                "disposition": "preserve_dangling_internal",
                "required": True,
                "target_sha256": hashlib.sha256(original_target).hexdigest(),
                "relative_locator": "required.db",
            }
        },
    )
    writer = _writer(tmp_path / "archive")
    try:
        capture_tree(writer, _root(source), policy_path, capture_id="4" * 32)
        verified = verify_tree(writer, "4" * 32)
        assert verified["unresolved_exclusion_count"] == 1
        assert verified["full_recovery_ready"] is False
    finally:
        writer.close()


def test_symlink_policy_rejects_path_escape_and_wrong_external_target(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    os.symlink("../../outside", source / "escape")
    policy_path = _policy(
        tmp_path / "policy.json",
        symlinks={
            "escape": {
                "disposition": "preserve_dangling_internal",
                "required": False,
                "target_sha256": hashlib.sha256(b"../../outside").hexdigest(),
                "relative_locator": "outside",
            }
        },
    )
    writer = _writer(tmp_path / "archive")
    try:
        with pytest.raises(TreeCustodyV3Error):
            capture_tree(writer, _root(source), policy_path, capture_id="3" * 32)
        assert not writer.exists(f"{BASE}/{'3' * 32}/COMPLETE.json")
    finally:
        writer.close()

    source2 = tmp_path / "source2"
    source2.mkdir()
    os.symlink("/mnt/kingston/BrainData/work", source2 / "external")
    bad_policy = _policy(
        tmp_path / "bad-policy.json",
        symlinks={
            "external": {
                "disposition": "external_root_binding",
                "required": True,
                "target_sha256": hashlib.sha256(b"/mnt/kingston/BrainData/work").hexdigest(),
                "volume_uuid": "volume-fixture",
                "root_id": "root-fixture",
                "relative_locator": "work",
                "authority_ref": "capture-fixture",
            }
        },
    )
    wrong_bytes = json.loads(bad_policy.read_bytes())
    wrong_bytes["symlink_target_dispositions"]["external"]["target_sha256"] = "0" * 64
    bad_policy.write_bytes(canonical_json(wrong_bytes))
    writer2 = _writer(tmp_path / "archive2")
    try:
        with pytest.raises(TreeCustodyV3Error, match="policy_symlink_target_mismatch"):
            capture_tree(writer2, _root(source2), bad_policy, capture_id="2" * 32)
        assert not writer2.exists(f"{BASE}/{'2' * 32}/COMPLETE.json")
    finally:
        writer2.close()


def test_external_binding_rejects_escaping_relative_locator(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    original = b"/Volumes/old/BrainData/work"
    os.symlink(original, source / "external")
    policy = _policy(
        tmp_path / "policy.json",
        symlinks={
            "external": {
                "disposition": "external_root_binding",
                "required": True,
                "target_sha256": hashlib.sha256(original).hexdigest(),
                "volume_uuid": "volume-fixture",
                "root_id": "root-fixture",
                "relative_locator": "../escape",
                "authority_ref": "capture-fixture",
            }
        },
    )
    writer = _writer(tmp_path / "archive")
    try:
        with pytest.raises(TreeCustodyV3Error, match="unsafe_path_component"):
            capture_tree(writer, _root(source), policy, capture_id="5" * 32)
        assert not writer.exists(f"{BASE}/{'5' * 32}/COMPLETE.json")
    finally:
        writer.close()
