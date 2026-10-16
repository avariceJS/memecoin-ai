from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from tools import brain_host_tree_custody_v3 as custody_v3
from tools.brain_host_held_data_t_v1 import (
    REQUIRED_ROOT_ROLES,
    ApprovedDataPlan,
    RootDeclaration,
    approved_data_plan_sha256,
)
from tools.brain_host_held_data_tree_capture_v3 import (
    HeldDataTreeCaptureError,
    verify_held_data_root_relation_v3,
)
from tools.brain_host_tree_custody import TreeRoot
from tools.brain_host_tree_custody_v3 import (
    POLICY_SCHEMA,
    TreeCustodyV3Error,
    _anchored_scratch,
    _close_db_at,
    _db_open_at,
    _validate_policy,
    capture_tree,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256


def _writer(root: Path) -> DescriptorWriter:
    root.mkdir(mode=0o700)
    return DescriptorWriter(os.open(root, os.O_RDONLY | os.O_DIRECTORY))


def test_scratch_database_fd_survives_scratch_name_replacement_without_path_escape(
    tmp_path: Path,
) -> None:
    storage = tmp_path / "kingston-root"
    writer = _writer(storage)
    data = tmp_path / "data-volume"
    data.mkdir()
    parent_fd = writer.open_directory("host-recovery/scratch", create=True)
    try:
        with pytest.raises(TreeCustodyV3Error, match="scratch_directory_replaced_before_cleanup"):
            with _anchored_scratch(
                writer,
                "host-recovery/scratch",
                "a" * 32,
                source_device=writer.device + 1,
                budget_bytes=1,
            ) as (_parent_fd, scratch_fd, scratch_name):
                db, db_fd = _db_open_at(scratch_fd, "before.sqlite", device=writer.device)
                db.execute("CREATE TABLE probe(value TEXT)")
                db.execute("INSERT INTO probe VALUES ('anchored')")
                db.commit()

                os.rename(
                    scratch_name,
                    "held-scratch",
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
                os.symlink(data, scratch_name, dir_fd=parent_fd)
                db.execute("INSERT INTO probe VALUES ('still-anchored')")
                db.commit()
                assert db.execute("SELECT value FROM probe ORDER BY rowid").fetchall() == [
                    ("anchored",),
                    ("still-anchored",),
                ]
                _close_db_at(db, db_fd, scratch_fd, "before.sqlite")
        assert list(data.iterdir()) == []
        assert (storage / "host-recovery/scratch/held-scratch").is_dir()
    finally:
        os.close(parent_fd)
        writer.close()


def test_anchored_scratch_rejects_read_only_or_under_budget_volume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = tmp_path / "kingston-root"
    writer = _writer(storage)
    original = os.fstatvfs

    class VFS:
        f_bavail = 0
        f_frsize = 4096
        f_flag = 0

    monkeypatch.setattr(os, "fstatvfs", lambda _fd: VFS())
    try:
        with pytest.raises(TreeCustodyV3Error, match="scratch_root_unwritable_or_capacity_low"):
            with _anchored_scratch(
                writer,
                "host-recovery/scratch",
                "b" * 32,
                source_device=writer.device + 1,
                budget_bytes=1,
            ):
                pass
    finally:
        monkeypatch.setattr(os, "fstatvfs", original)
        writer.close()


def test_anchored_scratch_closes_parent_fd_when_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = _writer(tmp_path / "kingston-root")
    opened: list[int] = []
    original_open_directory = writer.open_directory

    def record_open(relative: str, *, create: bool = False) -> int:
        fd = original_open_directory(relative, create=create)
        opened.append(fd)
        return fd

    monkeypatch.setattr(writer, "open_directory", record_open)
    try:
        with pytest.raises(TreeCustodyV3Error, match="scratch_cleanup_entry_unexpected"):
            with _anchored_scratch(
                writer,
                "host-recovery/scratch",
                "e" * 32,
                source_device=writer.device + 1,
                budget_bytes=1,
            ) as (_parent_fd, scratch_fd, _scratch_name):
                unexpected = os.open(
                    "unexpected",
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=scratch_fd,
                )
                os.close(unexpected)
        assert len(opened) == 1
        with pytest.raises(OSError):
            os.fstat(opened[0])
    finally:
        writer.close()


def test_held_capture_closes_source_fd_if_scratch_setup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY)
    policy = _validate_policy(
        {
            "schema": POLICY_SCHEMA,
            "policy_id": "fd-cleanup-test",
            "version": 1,
            "rule_set": "owner-work-sensitive-v1",
            "pem_candidate_dispositions": {},
            "exact_path_exclusions": {},
            "symlink_target_dispositions": {},
            "rebuild_inputs": [],
        }
    )
    policy_raw = canonical_json(policy)
    policy_path = tmp_path / "policy.json"
    policy_path.write_bytes(policy_raw)
    os.chmod(policy_path, 0o600)
    writer = _writer(tmp_path / "archive")
    writer.storage_guard = object()
    writer.assert_storage_binding = lambda _guard: None  # type: ignore[method-assign]
    duplicated: list[int] = []
    original_dup = os.dup

    def track_dup(fd: int) -> int:
        duplicate = original_dup(fd)
        duplicated.append(duplicate)
        return duplicate

    @contextmanager
    def fail_scratch(*_args: object, **_kwargs: object):
        raise TreeCustodyV3Error("scratch_setup_failed")
        yield

    monkeypatch.setattr(os, "dup", track_dup)
    monkeypatch.setattr(custody_v3, "_anchored_scratch", fail_scratch)
    try:
        with pytest.raises(TreeCustodyV3Error, match="scratch_setup_failed"):
            capture_tree(
                writer,
                TreeRoot("fixture", "fixture-plan", "caller_asserted", source),
                policy_path,
                source_root_fd=source_fd,
                source_display_path=source,
                scratch_root_relative="scratch",
                scratch_budget_bytes=1,
                revalidate_source_chunk=lambda: None,
                expected_policy_sha256=sha256(policy_raw),
            )
        assert len(duplicated) == 1
        with pytest.raises(OSError):
            os.fstat(duplicated[0])
        os.fstat(source_fd)
    finally:
        os.close(source_fd)
        writer.close()


def test_descriptor_sqlite_open_is_create_only(tmp_path: Path) -> None:
    root = tmp_path / "kingston-root"
    writer = _writer(root)
    scratch_fd = writer.open_directory("scratch", create=True)
    try:
        db, db_fd = _db_open_at(scratch_fd, "inventory.sqlite", device=writer.device)
        assert db.execute("PRAGMA temp_store").fetchone() == (2,)
        db.close()
        os.close(db_fd)
        with pytest.raises(TreeCustodyV3Error, match="scratch_database_create_failed"):
            _db_open_at(scratch_fd, "inventory.sqlite", device=writer.device)
    finally:
        os.close(scratch_fd)
        writer.close()


def test_close_db_preserves_same_name_replacement(tmp_path: Path) -> None:
    root = tmp_path / "kingston-root"
    writer = _writer(root)
    parent_fd = writer.open_directory("scratch", create=True)
    try:
        with pytest.raises(TreeCustodyV3Error, match="scratch_directory_replaced_before_cleanup"):
            with _anchored_scratch(
                writer,
                "scratch",
                "d" * 32,
                source_device=writer.device + 1,
                budget_bytes=1,
            ) as (_parent_fd, scratch_fd, scratch_name):
                db, db_fd = _db_open_at(scratch_fd, "before.sqlite", device=writer.device)
                db.execute("CREATE TABLE probe(value TEXT)")
                db.commit()
                os.rename(
                    scratch_name,
                    "held-scratch",
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
                os.mkdir(scratch_name, mode=0o700, dir_fd=parent_fd)
                replacement_fd = os.open(
                    scratch_name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent_fd,
                )
                try:
                    replacement_file = os.open(
                        "before.sqlite",
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=replacement_fd,
                    )
                    os.write(replacement_file, b"replacement must survive")
                    os.close(replacement_file)
                finally:
                    os.close(replacement_fd)
                _close_db_at(db, db_fd, scratch_fd, "before.sqlite")
        assert (root / "scratch/held-scratch/before.sqlite").exists() is False
        replacement_path = root / "scratch" / f"tree-v3-{'d' * 32}" / "before.sqlite"
        assert replacement_path.read_bytes() == b"replacement must survive"
    finally:
        os.close(parent_fd)
        writer.close()


def test_v3_complete_without_relation_is_not_accepted_as_held_view_capture(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "payload.bin").write_bytes(b"synthetic inert fixture")
    policy_path = tmp_path / "policy.json"
    policy = _validate_policy(
        {
            "schema": POLICY_SCHEMA,
            "policy_id": "anchored-scratch-test",
            "version": 1,
            "rule_set": "owner-work-sensitive-v1",
            "pem_candidate_dispositions": {},
            "exact_path_exclusions": {},
            "symlink_target_dispositions": {},
            "rebuild_inputs": [],
        }
    )
    policy_bytes = canonical_json(policy)
    policy_path.write_bytes(policy_bytes)
    os.chmod(policy_path, 0o600)
    writer_root = tmp_path / "archive"
    writer = _writer(writer_root)
    storage_guard = object()
    writer.storage_guard = storage_guard
    writer.assert_storage_binding = lambda supplied: None  # type: ignore[method-assign]
    capture_id = "c" * 32
    try:
        capture = capture_tree(
            writer,
            TreeRoot("research_checkout", "synthetic-plan", "caller_asserted", source),
            policy_path,
            capture_id=capture_id,
        )
        assert capture["complete"] is True
        assert (
            writer_root / "host-recovery/tree-custody-v3" / capture_id / "COMPLETE.json"
        ).exists()
        roster = {
            role: RootDeclaration(False, unresolved_reason="fixture_not_selected")
            for role in REQUIRED_ROOT_ROLES
        }
        roster["research_checkout"] = RootDeclaration(True, "roots/research_checkout", "directory")
        plan = ApprovedDataPlan(
            "synthetic-plan",
            "6e652e4f-9c38-4a48-887a-530158ba2767",
            "disk5s1",
            "disk5",
            "6e652e4f-9c38-4a48-887a-530158ba2767",
            roster,
        )
        with pytest.raises(HeldDataTreeCaptureError, match="relation_read_failed"):
            verify_held_data_root_relation_v3(
                writer,
                capture_id,
                plan,
                storage_guard=storage_guard,
                expected_plan_sha256=approved_data_plan_sha256(plan),
                expected_policy_sha256=sha256(policy_bytes),
                expected_relation_sha256="0" * 64,
            )
    finally:
        writer.close()
