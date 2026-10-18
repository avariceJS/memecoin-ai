from __future__ import annotations

import builtins
import contextlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import brain_host_root_boundary_census_v1 as census


def _fake_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        census,
        "_volume_identity",
        lambda path, *, held_fd, is_directory: {
            "volume_uuid": "11111111-1111-4111-8111-111111111111",
            "mount_point": "/synthetic",
            "filesystem_name": "Synthetic",
            "filesystem_type": "synthetic",
        },
    )


def test_census_is_metadata_only_and_does_not_follow_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_volume(monkeypatch)
    root = tmp_path / "tree"
    root.mkdir()
    (root / "nested").mkdir()
    payload = root / "nested" / "payload.bin"
    payload.write_bytes(b"must not be opened")
    (root / ".env.local").write_text("credential must not be opened")
    (root / "external-link").symlink_to(payload)

    def forbidden_readlink(*_args, **_kwargs):
        raise AssertionError("symlink target must not be read")

    def forbidden_open(*_args, **_kwargs):
        raise AssertionError("payload must not be opened")

    monkeypatch.setattr(os, "readlink", forbidden_readlink)
    monkeypatch.setattr(builtins, "open", forbidden_open)
    result = census.census_root(census.RootSpec("fixture", root))

    by_path = {row["path"]: row for row in result["entries"]}
    assert by_path["external-link"]["kind"] == "symlink"
    assert by_path["external-link"]["disposition"] == "link_target_not_read"
    assert "link_text" not in by_path["external-link"]
    assert by_path[".env.local"]["disposition"] == "excluded_reauth_reissue"
    assert "nested/payload.bin" in by_path
    assert result["counts"]["credential_name_excluded"] == 1
    assert result["counts"]["symlink_targets_unresolved"] == 1
    assert result["root_stable_at_endpoints"] is True
    assert result["source_volume_start"]["volume_uuid"] == "11111111-1111-4111-8111-111111111111"
    assert result["source_volume_start"] == result["source_volume_end"]


def test_census_streams_entry_records_without_retaining_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_volume(monkeypatch)
    root = tmp_path / "tree"
    root.mkdir()
    (root / "one").write_text("one")
    (root / "two").write_text("two")
    emitted: list[dict[str, object]] = []
    result = census.census_root(census.RootSpec("fixture", root), record_sink=emitted.append)
    assert result["entries"] == []
    assert result["entry_disposition_counts"] == {"metadata_only": 2}
    assert len(emitted) == 2


def test_unexpected_env_local_directory_is_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_volume(monkeypatch)
    root = tmp_path / "tree"
    (root / ".env.local").mkdir(parents=True)

    result = census.census_root(census.RootSpec("fixture", root))

    entry = result["entries"][0]
    assert entry["kind"] == "directory"
    assert entry["disposition"] == "excluded_name_unexpected_kind_unresolved"
    assert result["complete_traversal"] is False
    assert result["counts"]["unresolved"] == 1
    assert result["errors"] == [
        {"path": ".env.local", "error": "credential_exclusion_kind_invalid"}
    ]


def test_cross_device_child_makes_traversal_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_volume(monkeypatch)
    root = tmp_path / "tree"
    root.mkdir()
    (root / "mounted.bin").write_text("metadata only")
    original_stat = os.stat

    def stat_with_mount(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == "mounted.bin" and kwargs.get("dir_fd") is not None:
            fields = (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_nlink",
                "st_uid",
                "st_gid",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
            values = {field: getattr(result, field) for field in fields}
            values["st_dev"] += 1
            return SimpleNamespace(**values)
        return result

    monkeypatch.setattr(census.os, "stat", stat_with_mount)
    result = census.census_root(census.RootSpec("fixture", root))

    assert result["entries"][0]["kind"] == "regular_file"
    assert result["entries"][0]["disposition"] == "cross_device_not_descended"
    assert result["counts"]["cross_device"] == 1
    assert result["complete_traversal"] is False
    assert result["all_descendants_examined"] is False


def test_inode_cleanup_leaves_name_owned_by_another_writer(tmp_path: Path) -> None:
    pending = tmp_path / "run.pending"
    final = tmp_path / "run.jsonl"
    pending.write_bytes(b"this run")
    final.write_bytes(b"another run")
    own = pending.stat()

    parent_fd = os.open(tmp_path, census.NOFOLLOW_DIRECTORY)
    try:
        assert census._unlink_if_owned_inode(
            parent_fd,
            "run.pending",
            device=own.st_dev,
            inode=own.st_ino,
        ) is True
        assert census._unlink_if_owned_inode(
            parent_fd,
            "run.jsonl",
            device=own.st_dev,
            inode=own.st_ino,
        ) is False
    finally:
        os.close(parent_fd)
    assert final.read_bytes() == b"another run"


def test_post_reservation_revalidation_failure_removes_owned_pending_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mount = tmp_path / "kingston"
    data_root = mount / "BrainData"
    data_root.mkdir(parents=True)
    data_root.chmod(0o700)
    mount_fd = os.open(mount, census.NOFOLLOW_DIRECTORY)

    class Guard:
        config = SimpleNamespace(data_root="BrainData")
        volume_uuid = census.EXPECTED_KINGSTON_VOLUME_UUID
        root_id = census.EXPECTED_KINGSTON_ROOT_ID

        def __init__(self) -> None:
            self.mount_fd = mount_fd
            self.revalidations = 0

        def revalidate(self) -> None:
            self.revalidations += 1
            if self.revalidations == 2:
                raise census.CensusError("synthetic_post_reservation_failure")

    guard = Guard()

    @contextlib.contextmanager
    def fake_guard(_config: Path):
        yield guard

    monkeypatch.setattr(census, "writer_guard", fake_guard)
    monkeypatch.setattr(census, "_verify_marker_in_root_fd", lambda *_args: None)
    monkeypatch.setattr(census._UnlockedStorageBinding, "revalidate", lambda self: None)
    roots = [census.RootSpec(f"root-{index}", tmp_path / f"root-{index}") for index in range(9)]

    try:
        with pytest.raises(census.CensusError, match="synthetic_post_reservation_failure"):
            census.run_census(roots, storage_config=tmp_path / "storage.json")
        pending_dir = data_root / "host-recovery/private-root-boundary-census-v1"
        pending = list(pending_dir.glob("*.pending"))
        assert pending == []
    finally:
        os.close(mount_fd)


def test_opaque_subtree_is_recorded_and_not_descended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_volume(monkeypatch)
    root = tmp_path / "research"
    (root / "work" / "wallets" / "private").mkdir(parents=True)
    (root / "work" / "wallets" / "private" / "opaque.bin").write_bytes(b"opaque")
    (root / "work" / "ordinary").mkdir()
    (root / "work" / "ordinary" / "seen.bin").write_bytes(b"visible metadata")

    result = census.census_root(
        census.RootSpec("research", root, (("@basename:wallets", "opaque_wallets"),))
    )
    by_path = {row["path"]: row for row in result["entries"]}
    wallet_rows = [path for path in by_path if "wallets" in path]
    assert wallet_rows == ["work/wallets"]
    assert by_path["work/wallets"]["disposition"] == (
        "skipped_opaque_wallets_unresolved_backup_required"
    )
    assert "work/ordinary/seen.bin" in by_path
    assert result["counts"]["unresolved_backup_required"] == 1


def test_single_file_root_lstats_symlink_and_records_ancestors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_volume(monkeypatch)
    parent = tmp_path / "config"
    parent.mkdir()
    target = parent / "target"
    target.write_text("not read")
    link = parent / "mcp.json"
    link.symlink_to(target)

    def forbidden_readlink(*_args, **_kwargs):
        raise AssertionError("symlink target must not be read")

    monkeypatch.setattr(os, "readlink", forbidden_readlink)
    result = census.census_root(census.RootSpec("mcp", link, expected_kind="file"))
    assert result["root_kind"] == "single_declared_file"
    assert result["entries"][0]["kind"] == "symlink"
    assert result["entries"][0]["disposition"] == "link_target_not_read"
    assert result["ancestor_identities_start"] == result["ancestor_identities_end"]
    assert result["complete_traversal"] is True


def test_missing_declared_file_is_explicitly_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_volume(monkeypatch)
    parent = tmp_path / "config"
    parent.mkdir()
    result = census.census_root(
        census.RootSpec("missing_config", parent / "mcp.json", expected_kind="file")
    )
    assert result["complete_traversal"] is False
    assert result["counts"]["missing_declared_file"] == 1
    assert result["errors"] == [{"path": "mcp.json", "error": "declared_file_missing"}]


def test_expected_directory_rejects_symlink_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_volume(monkeypatch)
    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "declared"
    root.symlink_to(target, target_is_directory=True)
    with pytest.raises(census.CensusError, match="declared_directory_kind_mismatch"):
        census.census_root(census.RootSpec("declared", root))


def test_declared_single_file_does_not_recurse_if_replaced_by_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_volume(monkeypatch)
    parent = tmp_path / "config"
    parent.mkdir()
    target = parent / "mcp.json"
    target.mkdir()
    (target / "private-token-name").write_text("not read")
    with pytest.raises(census.CensusError, match="declared_file_kind_mismatch"):
        census.census_root(census.RootSpec("mcp", target, expected_kind="file"))


def test_directory_path_replacement_is_not_reported_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    _fake_volume(monkeypatch)
    original = census._volume_identity
    calls = 0

    def replace_on_end(path: Path, *, held_fd: int, is_directory: bool):
        nonlocal calls
        calls += 1
        if calls == 2:
            moved = tmp_path / "held-tree"
            path.rename(moved)
            path.mkdir()
        return original(path, held_fd=held_fd, is_directory=is_directory)

    monkeypatch.setattr(census, "_volume_identity", replace_on_end)
    result = census.census_root(census.RootSpec("tree", root))
    assert result["root_stable_at_endpoints"] is False
    assert result["counts"]["unresolved"] >= 1


def test_file_replacement_during_final_volume_probe_is_not_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "config"
    parent.mkdir()
    item = parent / "mcp.json"
    item.write_text("first")
    _fake_volume(monkeypatch)
    original = census._volume_identity
    calls = 0

    def replace_on_end(path: Path, *, held_fd: int, is_directory: bool):
        nonlocal calls
        calls += 1
        result = original(path, held_fd=held_fd, is_directory=is_directory)
        if calls == 2:
            replacement = parent / "replacement"
            replacement.write_text("second")
            replacement.replace(item)
        return result

    monkeypatch.setattr(census, "_volume_identity", replace_on_end)
    result = census.census_root(census.RootSpec("mcp", item, expected_kind="file"))
    assert result["root_stable_at_endpoints"] is False


def test_worktree_discovery_is_shallow_and_does_not_follow_links(tmp_path: Path) -> None:
    base = tmp_path / "worktrees"
    checkout = base / "linked"
    checkout.mkdir(parents=True)
    (checkout / ".git").write_text("gitdir: /private/git/linked\n")
    nested = checkout / ".local/nested"
    nested.mkdir(parents=True)
    (nested / ".git").mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / ".git").mkdir()
    (base / "linked-outside").symlink_to(external, target_is_directory=True)
    assert census._discover_linked_worktrees(base) == [checkout]


def test_git_metadata_uses_only_pointer_and_commondir_control_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    admin = tmp_path / "admin" / "worktrees" / "one"
    common = tmp_path / "admin"
    checkout.mkdir()
    admin.mkdir(parents=True)
    (checkout / ".git").write_text(f"gitdir: {admin}\n")
    (admin / "commondir").write_text("../..\n")
    (admin / "config").write_text("credential.helper=private-value\n")

    def forbidden_git(*_args, **_kwargs):
        raise AssertionError("Git subprocess/config must not be consulted")

    monkeypatch.setattr(census.subprocess, "run", forbidden_git)
    result = census._git_metadata_paths(checkout)
    assert result["git-dir"] == admin
    assert result["git-common-dir"] == common


def test_opaque_count_mismatch_is_not_complete() -> None:
    assert census._opaque_counts_match("research_checkout", {}) is False
    assert census._opaque_counts_match("research_checkout", {
        "skipped_opaque_wallets_unresolved_backup_required": 3
    }) is True


def test_git_admin_metadata_inside_excluded_subtree_is_not_covered(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    (root / "apps/control-center/.local/git-admin").mkdir(parents=True)
    spec = census.RootSpec(
        "owner_codex_worktree",
        root,
        (("apps/control-center/.local", "opaque_codex_local"),),
    )
    assert census._is_scanned_by_root(spec, root / "apps/control-center/.local/git-admin") is False


def test_final_pair_verification_rejects_changed_completion_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = census._canonical(
        {
            "schema": f"{census.SCHEMA}-complete",
            "status": "private_manifest_published_and_reread_verified",
            "manifest_relative_path": "manifest.jsonl",
            "manifest_sha256": "a" * 64,
            "manifest_bytes": 1,
            "safe_to_erase": False,
        }
    )
    changed = expected.replace(b'"safe_to_erase":false', b'"safe_to_erase":true')
    manifest_digest = "a" * 64
    monkeypatch.setattr(
        census,
        "_hash_private_file",
        lambda _guard, path: (
            (manifest_digest, 1)
            if path == "manifest.jsonl"
            else (census.hashlib.sha256(changed).hexdigest(), len(changed))
        ),
    )
    monkeypatch.setattr(census, "_read_small_private_bytes", lambda *_args: changed)

    class Guard:
        def revalidate(self) -> None:
            return None

    with pytest.raises(census.CensusError, match="published_pair_verification_failed"):
        census._verify_published_pair(
            Guard(), "manifest.jsonl", "marker.json", manifest_digest, 1, expected
        )


def test_streamed_census_publishes_private_jsonl_and_exact_completion_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mount = tmp_path / "kingston"
    data_root = mount / "BrainData"
    data_root.mkdir(parents=True)
    data_root.chmod(0o700)
    mount_fd = os.open(mount, census.NOFOLLOW_DIRECTORY)

    class Guard:
        config = SimpleNamespace(data_root="BrainData")
        volume_uuid = census.EXPECTED_KINGSTON_VOLUME_UUID
        root_id = census.EXPECTED_KINGSTON_ROOT_ID

        def __init__(self) -> None:
            self.mount_fd = mount_fd

        def revalidate(self) -> None:
            return None

    guard = Guard()

    @contextlib.contextmanager
    def fake_guard(_config: Path):
        yield guard

    monkeypatch.setattr(census, "writer_guard", fake_guard)
    monkeypatch.setattr(census, "_verify_marker_in_root_fd", lambda *_args: None)
    monkeypatch.setattr(
        census,
        "_volume_identity",
        lambda path, *, held_fd, is_directory: {
            "volume_uuid": census.EXPECTED_DATA_VOLUME_UUID,
            "mount_point": census.EXPECTED_DATA_MOUNT_POINT,
            "filesystem_name": "Synthetic",
            "filesystem_type": "synthetic",
        },
    )
    monkeypatch.setattr(census._UnlockedStorageBinding, "revalidate", lambda self: None)

    roots: list[census.RootSpec] = []
    research = tmp_path / "research"
    for index in range(3):
        (research / "work" / f"tree-{index}" / "wallets").mkdir(parents=True)
    roots.append(
        census.RootSpec(
            "research_checkout",
            research,
            (("@basename:wallets", "opaque_wallets"),),
        )
    )
    codex = tmp_path / "codex"
    (codex / "apps/control-center/.local").mkdir(parents=True)
    roots.append(
        census.RootSpec(
            "owner_codex_worktree",
            codex,
            (("apps/control-center/.local", "opaque_codex_local"),),
        )
    )
    for index in range(7):
        root = tmp_path / f"root-{index}"
        root.mkdir()
        (root / "payload.txt").write_text("metadata only")
        roots.append(census.RootSpec(f"other-{index}", root))

    try:
        summary = census.run_census(
            roots,
            storage_config=tmp_path / "storage.json",
            now=census.dt.datetime(2026, 10, 14, tzinfo=census.dt.UTC),
        )
        assert summary["publication_complete"] is True
        assert summary["scan_complete"] is True
        assert summary["safe_to_erase"] is False
        manifest = data_root / summary["private_manifest_locator"]
        lines = [json.loads(line) for line in manifest.read_text().splitlines()]
        assert lines[0]["record_type"] == "header"
        assert sum(line.get("record_type") == "entry" for line in lines) >= 10
        assert any(line.get("record_type") == "root_summary" for line in lines)
        marker = data_root / summary["private_manifest_complete_marker_locator"]
        completion = json.loads(marker.read_text())
        assert completion["manifest_sha256"] == summary["private_manifest_sha256"]
        assert completion["scan_complete"] is True
    finally:
        os.close(mount_fd)


def test_publisher_is_create_only_and_readback_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mount = tmp_path / "mount"
    root = mount / "BrainData"
    root.mkdir(parents=True)
    root.chmod(0o700)
    monkeypatch.setattr(census, "_verify_marker_in_root_fd", lambda _fd, _config: None)
    mount_fd = os.open(mount, census.NOFOLLOW_DIRECTORY)

    class Guard:
        config = SimpleNamespace(data_root="BrainData")
        volume_uuid = "8f8f1081-9be4-4914-9111-75f1b90255bf"
        root_id = "9428695af6d4f46c08e1a444cdc66de8"

        def __init__(self) -> None:
            self.mount_fd = mount_fd
            self.revalidations = 0

        def revalidate(self) -> None:
            self.revalidations += 1

    guard = Guard()
    raw = b'{"private":true}\n'
    relative = "host-recovery/private-root-boundary-census-v1/test.json"
    try:
        digest = census._publish_create_only(guard, relative, raw)
        assert digest == census.hashlib.sha256(raw).hexdigest()
        assert guard.revalidations >= 2
        assert (root / relative).read_bytes() == raw
        with pytest.raises(census.CensusError, match="private_output_already_exists"):
            census._publish_create_only(guard, relative, raw)
    finally:
        os.close(mount_fd)


def test_aggregate_never_claims_source_or_erase_readiness() -> None:
    summary = census._aggregate(
        [
            {
                "root_key": "fixture",
                "complete_traversal": True,
                "root_stable_at_endpoints": True,
                "source_volume_start": {"volume_uuid": "11111111-1111-4111-8111-111111111111"},
                "source_volume_end": {"volume_uuid": "11111111-1111-4111-8111-111111111111"},
                "counts": {"entries": 0},
                "limitations": [],
            }
        ]
    )
    assert summary["source_consistency_proven"] is False
    assert summary["root_closure_proven"] is False
    assert summary["safe_to_erase"] is False
    assert summary["protected_payloads_read"] is False
