from __future__ import annotations

import hashlib
import json
import os
import plistlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools import brain_migration_v1 as migration


def _make_roots(tmp_path: Path) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for alias in migration.ROOT_ALIASES:
        root = tmp_path / "sources" / alias
        if alias == "recovery_terminal_transcript":
            root.parent.mkdir(parents=True, exist_ok=True)
            root.write_bytes(b"recovery terminal output\n")
        else:
            root.mkdir(parents=True, exist_ok=True)
            (root / "state.bin").write_bytes(f"{alias}-opaque-state".encode())
        roots[alias] = root
    roots["owner_work"] = tmp_path / "sources/owner_work"
    (roots["owner_work"] / ".env.local").write_text("DO_NOT_READ=secret\n")
    (roots["owner_work"] / ".codex" / "sessions").mkdir(parents=True)
    (roots["owner_work"] / ".codex" / "sessions" / "session.json").write_text("private")
    (roots["owner_work"] / "nested").mkdir()
    (roots["owner_work"] / "nested" / "note.txt").write_text("opaque note")
    (roots["owner_work"] / "calendar-execution-01").mkdir()
    os.symlink("../nested/note.txt", roots["owner_work"] / "nested-link")

    launch = tmp_path / "sources/launch_agents"
    (launch / "allowed").mkdir()
    (launch / "other").mkdir()
    (launch / "allowed" / "ai.memecoin.brain.test.plist").write_bytes(
        plistlib.dumps({"Label": "ai.memecoin.brain.test", "ProgramArguments": ["/private/path"]})
    )
    (launch / "allowed" / "ai.memecoin.d43.test.plist").write_bytes(
        plistlib.dumps({"Label": "ai.memecoin.d43.test", "KeepAlive": False})
    )
    (launch / "other" / "user.plist").write_bytes(
        plistlib.dumps({"Label": "com.example.unrelated"})
    )
    (launch / "allowed" / "credential.plist").write_bytes(
        plistlib.dumps({"Label": "ai.memecoin.brain.secret", "API_TOKEN": "never-copy"})
    )
    roots["launch_agents"] = launch
    return roots


def _bind_synthetic_target(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    uuid = migration.EXPECTED_KINGSTON_UUID
    pin = migration.TargetPin(uuid, str(target), "apfs", "synthetic-target")

    def target_guard(_path: Path, *, expected_uuid: str = migration.EXPECTED_KINGSTON_UUID):
        assert expected_uuid == uuid
        return migration._open_directory(target), pin

    monkeypatch.setattr(migration, "_validate_target", target_guard)
    monkeypatch.setattr(migration, "_target_pin", lambda _path: pin)
    monkeypatch.setattr(migration, "_free_bytes", lambda _fd: 200 * migration.GIB)


def _manifest_records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _capture_test_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, candidate_id: str
) -> tuple[Path, str]:
    roots = _make_roots(tmp_path)
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    _bind_synthetic_target(monkeypatch, target)
    result = migration.capture(
        roots=roots,
        target_root=target,
        candidate_id=candidate_id,
        _test_capability=migration._TEST_CAPTURE_CAPABILITY,
    )
    return target, result["manifest_sha256"]


def test_capture_verify_restore_inert_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    roots = _make_roots(tmp_path)
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    _bind_synthetic_target(monkeypatch, target)

    result = migration.capture(
        roots=roots,
        target_root=target,
        candidate_id="a" * 32,
        _test_capability=migration._TEST_CAPTURE_CAPABILITY,
    )
    assert result["state"] == "verified_inert_candidate"
    assert result["payload_bytes"] > 0
    manifest = (
        target
        / migration.MIGRATION_DIR
        / migration.CANDIDATES_DIR
        / ("a" * 32)
        / migration.MANIFEST
    )
    rows = _manifest_records(manifest)
    member_rows = [row for row in rows if row["record_type"] == "member"]
    member_paths = {(row["alias"], row["path"]) for row in member_rows}
    link_row = next(row for row in member_rows if row["path"] == "nested-link")
    assert link_row["type"] == "symlink"
    assert link_row["link_class"] == "relative_escape"
    assert ("owner_work", ".env.local") not in member_paths
    assert ("owner_work", ".codex") not in member_paths
    assert ("launch_agents", "allowed/ai.memecoin.brain.test.plist") in member_paths
    assert ("launch_agents", "allowed/ai.memecoin.d43.test.plist") not in member_paths
    assert ("launch_agents", "other/user.plist") not in member_paths
    assert ("launch_agents", "allowed/credential.plist") not in member_paths
    assert any(
        row.get("record_type") == "excluded"
        and row.get("path") == ".env.local"
        and row.get("reason") == "provider_credentials_reissue"
        for row in rows
    )
    assert "known_repository_exclusions" not in rows[0]
    assert rows[0]["root_dispositions"]["brain_local_config"] == "private_quarantine_reissue_only"

    verified = migration.verify(
        target_root=target,
        candidate_id="a" * 32,
        expected_manifest_sha256=result["manifest_sha256"],
    )
    assert verified["manifest_sha256"] == result["manifest_sha256"]
    quarantine = tmp_path / "restore-quarantine"
    write = os.write

    def short_write(fd: int, buffer: Any) -> int:
        return write(fd, buffer[:17])

    monkeypatch.setattr(migration.os, "write", short_write)
    restored = migration.restore_inert(
        target_root=target,
        candidate_id="a" * 32,
        expected_manifest_sha256=result["manifest_sha256"],
        quarantine_root=quarantine,
        _test_capability=migration._TEST_CAPTURE_CAPABILITY,
    )
    assert restored["state"] == "inert_quarantine_verified"
    assert restored["start_or_activation"] is False
    assert restored["protected_custody_complete"] is False
    assert restored["protected_custody_disposition"] == "protected_D43_source_present_uncaptured"
    assert (quarantine / "payload/owner_work/nested/note.txt").read_text() == "opaque note"
    assert os.readlink(quarantine / "payload/owner_work/nested-link") == "../nested/note.txt"
    restored_manifest = (quarantine / "MIGRATION_MANIFEST.jsonl").read_bytes()
    assert hashlib.sha256(restored_manifest).hexdigest() == result["manifest_sha256"]
    assert (
        quarantine / "payload/recovery_terminal_transcript"
    ).read_bytes() == b"recovery terminal output\n"
    assert stat_mode(quarantine) == 0o700
    assert not (quarantine / "payload/owner_work/.env.local").exists()


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_capture_preflight_capacity_fails_before_candidate_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = _make_roots(tmp_path)
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    _bind_synthetic_target(monkeypatch, target)
    monkeypatch.setattr(migration, "_free_bytes", lambda _fd: migration.FREE_FLOOR_BYTES)
    with pytest.raises(migration.MigrationError, match="capacity_floor_failed"):
        migration.capture(
            roots=roots,
            target_root=target,
            candidate_id="b" * 32,
            _test_capability=migration._TEST_CAPTURE_CAPABILITY,
        )
    assert not (target / migration.MIGRATION_DIR).exists()


def test_candidate_is_create_only_and_external_manifest_pin_is_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = _make_roots(tmp_path)
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    _bind_synthetic_target(monkeypatch, target)
    candidate = "c" * 32
    result = migration.capture(
        roots=roots,
        target_root=target,
        candidate_id=candidate,
        _test_capability=migration._TEST_CAPTURE_CAPABILITY,
    )
    with pytest.raises(migration.MigrationError, match="candidate_already_exists"):
        migration.capture(
            roots=roots,
            target_root=target,
            candidate_id=candidate,
            _test_capability=migration._TEST_CAPTURE_CAPABILITY,
        )
    with pytest.raises(migration.MigrationError, match="external_manifest_pin_mismatch"):
        migration.verify(
            target_root=target, candidate_id=candidate, expected_manifest_sha256="0" * 64
        )
    assert result["manifest_sha256"] != "0" * 64


def test_verify_detects_payload_and_extra_member_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = _make_roots(tmp_path)
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    _bind_synthetic_target(monkeypatch, target)
    result = migration.capture(
        roots=roots,
        target_root=target,
        candidate_id="d" * 32,
        _test_capability=migration._TEST_CAPTURE_CAPABILITY,
    )
    payload = target / migration.MIGRATION_DIR / migration.CANDIDATES_DIR / ("d" * 32) / "payload"
    (payload / "owner_work/nested/note.txt").write_text("tampered")
    with pytest.raises(migration.MigrationError, match="candidate_member_digest_mismatch"):
        migration.verify(
            target_root=target,
            candidate_id="d" * 32,
            expected_manifest_sha256=result["manifest_sha256"],
        )


def test_special_source_node_rejected_before_target_candidate_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = _make_roots(tmp_path)
    fifo = roots["research_work"] / "unexpected-pipe"
    os.mkfifo(fifo)
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    _bind_synthetic_target(monkeypatch, target)
    with pytest.raises(migration.MigrationError, match="source_special_node_rejected"):
        migration.capture(
            roots=roots,
            target_root=target,
            _test_capability=migration._TEST_CAPTURE_CAPABILITY,
        )
    assert not (target / migration.MIGRATION_DIR).exists()


def test_protected_path_name_candidates_are_copied_as_opaque_custody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = _make_roots(tmp_path)
    protected = roots["research_work"] / "F01-reserved-holdout"
    protected.mkdir()
    payload = protected / "opaque.sqlite"
    payload.write_bytes(b"opaque database bytes; no parser")
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    _bind_synthetic_target(monkeypatch, target)
    result = migration.capture(
        roots=roots,
        target_root=target,
        candidate_id="3" * 32,
        _test_capability=migration._TEST_CAPTURE_CAPABILITY,
    )
    candidate = target / migration.MIGRATION_DIR / migration.CANDIDATES_DIR / ("3" * 32)
    rows = _manifest_records(candidate / migration.MANIFEST)
    protected_rows = [
        row
        for row in rows
        if row.get("record_type") == "member"
        and row.get("alias") == "research_work"
        and row.get("path", "").startswith("F01-reserved-holdout")
    ]
    assert {row["custody_class"] for row in protected_rows} == {
        "protected_path_name_candidate_opaque"
    }
    copied = candidate / "payload/research_work/F01-reserved-holdout/opaque.sqlite"
    assert copied.read_bytes() == payload.read_bytes()
    assert result["state"] == "verified_inert_candidate"


def test_live_d43_source_root_is_excluded_without_descending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = _make_roots(tmp_path)
    d43_root = roots["owner_work"] / "calendar-execution-01"
    os.mkfifo(d43_root / "must-not-stat-or-open")
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    _bind_synthetic_target(monkeypatch, target)
    result = migration.capture(
        roots=roots,
        target_root=target,
        candidate_id="6" * 32,
        _test_capability=migration._TEST_CAPTURE_CAPABILITY,
    )
    candidate = target / migration.MIGRATION_DIR / migration.CANDIDATES_DIR / ("6" * 32)
    rows = _manifest_records(candidate / migration.MANIFEST)
    gap = next(
        row
        for row in rows
        if row.get("record_type") == "excluded"
        and row.get("alias") == "owner_work"
        and row.get("path") == "calendar-execution-01"
    )
    assert gap["reason"] == "protected_D43_source_present_uncaptured"
    assert gap["custody_class"] == "protected_path_name_candidate_opaque"
    assert not (candidate / "payload/owner_work/calendar-execution-01").exists()
    assert rows[0]["protected_custody_complete"] is False
    assert result["protected_custody_complete"] is False
    verified = migration.verify(
        target_root=target,
        candidate_id="6" * 32,
        expected_manifest_sha256=result["manifest_sha256"],
    )
    assert verified["protected_custody_complete"] is False


def test_hardlink_aliases_are_copied_as_independent_verified_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = _make_roots(tmp_path)
    os.link(roots["owner_work"] / "nested" / "note.txt", roots["owner_work"] / "linked-note")
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    _bind_synthetic_target(monkeypatch, target)
    result = migration.capture(
        roots=roots,
        target_root=target,
        candidate_id="9" * 32,
        _test_capability=migration._TEST_CAPTURE_CAPABILITY,
    )
    candidate = target / migration.MIGRATION_DIR / migration.CANDIDATES_DIR / ("9" * 32)
    payload = candidate / "payload/owner_work"
    assert (payload / "nested/note.txt").read_bytes() == (payload / "linked-note").read_bytes()
    assert not os.path.samefile(payload / "nested/note.txt", payload / "linked-note")
    header = _manifest_records(candidate / migration.MANIFEST)[0]
    assert header["hardlink_policy"] == "expand_each_path_to_independent_bytes_topology_unpreserved"
    verified = migration.verify(
        target_root=target,
        candidate_id="9" * 32,
        expected_manifest_sha256=result["manifest_sha256"],
    )
    assert verified["manifest_sha256"] == result["manifest_sha256"]


def test_hardlink_alias_content_must_match() -> None:
    observed: dict[tuple[int, int], tuple[int, str]] = {}
    source_stat = SimpleNamespace(st_nlink=2, st_dev=7, st_ino=11)
    migration._record_hardlink_bytes(observed, source_stat, 12, "a" * 64)
    with pytest.raises(migration.MigrationError, match="hardlink_alias_bytes_disagree"):
        migration._record_hardlink_bytes(observed, source_stat, 12, "b" * 64)


def test_secret_exclusions_apply_inside_protected_name_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = _make_roots(tmp_path)
    protected = roots["owner_work"] / "F01-private"
    protected.mkdir()
    (protected / ".env.local").write_text("synthetic secret must not be copied")
    (protected / "opaque.bin").write_bytes(b"opaque protected bytes")
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    _bind_synthetic_target(monkeypatch, target)
    result = migration.capture(
        roots=roots,
        target_root=target,
        candidate_id="e" * 32,
        _test_capability=migration._TEST_CAPTURE_CAPABILITY,
    )
    candidate = target / migration.MIGRATION_DIR / migration.CANDIDATES_DIR / ("e" * 32)
    payload = candidate / "payload/owner_work/F01-private"
    assert (payload / "opaque.bin").read_bytes() == b"opaque protected bytes"
    assert not (payload / ".env.local").exists()
    assert result["protected_custody_complete"] is False


def test_legacy_or_unlisted_launch_plists_are_never_included(tmp_path: Path) -> None:
    root = tmp_path / "launch"
    root.mkdir()
    (root / "ai.memecoin.brain.capture.plist").write_bytes(
        plistlib.dumps({"Label": "ai.memecoin.brain.capture"})
    )
    (root / "ai.memecoin.d43.capture.plist").write_bytes(
        plistlib.dumps({"Label": "ai.memecoin.d43.capture"})
    )
    (root / "F01-private").mkdir()
    (root / "F01-private" / "user.plist").write_bytes(
        plistlib.dumps({"Label": "com.example.unrelated"})
    )
    (root / "other.plist").write_bytes(plistlib.dumps({"Label": "com.apple.example"}))
    (root / "bad.plist").write_text("not a plist")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        observed = list(migration._iter_source(fd, "launch_agents"))
    finally:
        os.close(fd)
    rows = {row[0]: row[1:] for row in observed}
    assert rows["ai.memecoin.brain.capture.plist"][0] == "file"
    assert rows["ai.memecoin.d43.capture.plist"][0] == "excluded"
    assert rows["F01-private/user.plist"][0] == "excluded"
    assert rows["other.plist"][0] == "excluded"
    assert rows["bad.plist"][0] == "excluded"


def test_git_remote_config_and_named_credential_paths_are_reissued() -> None:
    assert migration._exclude_reason("owner_git", "config") == "git_remote_configuration_recreate"
    assert (
        migration._exclude_reason("research_git", "worktrees/task/config.worktree")
        == "git_remote_configuration_recreate"
    )
    assert (
        migration._exclude_reason("brain_local_config", "providers/helius-auth.json")
        == "provider_credentials_reissue"
    )
    assert migration._exclude_reason("owner_work", ".codex") == "codex_session_store_reissue"
    assert migration._exclude_reason("owner_work", ".cursor") == "cursor_session_store_reissue"
    assert migration._exclude_reason("owner_work", ".env") == "provider_credentials_reissue"
    assert (
        migration._exclude_reason("brain_local_config", "providers/api-token.json")
        == "provider_credentials_reissue"
    )
    assert migration._exclude_reason("research_work", "token2022-source-pins/abi.json") is None


def test_d43_plist_is_excluded_without_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "launch"
    root.mkdir()
    d43 = root / "ai.memecoin.d43.opaque.plist"
    d43.write_text("not parsed")
    monkeypatch.setattr(
        migration,
        "_launch_plist_allowed",
        lambda _raw: pytest.fail("D43 plist content must not be parsed"),
    )
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        rows = list(migration._iter_source(fd, "launch_agents"))
    finally:
        os.close(fd)
    assert next(row for row in rows if row[0] == d43.name)[1] == "excluded"


def test_symlink_classification_is_lexical_only() -> None:
    assert migration._classify_link_target("../outside") == "relative_escape"
    assert migration._classify_link_target("nested/file") == "relative_within_root"
    assert migration._classify_link_target("/private/data") == "absolute"


def test_capture_stops_if_target_identity_changes_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = _make_roots(tmp_path)
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    _bind_synthetic_target(monkeypatch, target)
    expected = migration.TargetPin(
        migration.EXPECTED_KINGSTON_UUID, str(target), "apfs", "synthetic-target"
    )
    observed = 0

    def changing_pin(_path: Path) -> migration.TargetPin:
        nonlocal observed
        observed += 1
        if observed >= 3:
            return migration.TargetPin("deadbeef", str(target), "apfs", "other-device")
        return expected

    monkeypatch.setattr(migration, "_target_pin", changing_pin)
    candidate_id = "e" * 32
    with pytest.raises(migration.MigrationError, match="target_volume_identity_changed"):
        migration.capture(
            roots=roots,
            target_root=target,
            candidate_id=candidate_id,
            _test_capability=migration._TEST_CAPTURE_CAPABILITY,
        )
    candidate = target / migration.MIGRATION_DIR / migration.CANDIDATES_DIR / candidate_id
    assert candidate.exists()
    assert not (candidate / migration.MANIFEST).exists()


def test_capture_rechecks_target_during_single_file_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = _make_roots(tmp_path)
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    _bind_synthetic_target(monkeypatch, target)
    expected = migration.TargetPin(
        migration.EXPECTED_KINGSTON_UUID, str(target), "apfs", "synthetic-target"
    )
    calls = 0

    def changing_pin(_path: Path) -> migration.TargetPin:
        nonlocal calls
        calls += 1
        if calls >= 3:
            return migration.TargetPin("deadbeef", str(target), "apfs", "other-device")
        return expected

    monkeypatch.setattr(migration, "_target_pin", changing_pin)
    monkeypatch.setattr(migration, "TARGET_RECHECK_BYTES", 1)
    with pytest.raises(migration.MigrationError, match="target_volume_identity_changed"):
        migration.capture(
            roots=roots,
            target_root=target,
            candidate_id="5" * 32,
            _test_capability=migration._TEST_CAPTURE_CAPABILITY,
        )


def test_held_target_fd_must_match_fresh_mountpoint_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held = tmp_path / "held"
    fresh = tmp_path / "fresh"
    held.mkdir()
    fresh.mkdir()
    pin = migration.TargetPin("same-uuid", str(held), "apfs", "same-device")
    held_fd = migration._open_directory(held)
    monkeypatch.setattr(migration, "_target_pin", lambda _path: pin)
    monkeypatch.setattr(
        migration, "_open_directory", lambda _path: os.open(fresh, os.O_RDONLY | os.O_DIRECTORY)
    )
    try:
        with pytest.raises(migration.MigrationError, match="target_mountpoint_fd_identity_changed"):
            migration._assert_held_target_identity(held, held_fd, pin)
    finally:
        os.close(held_fd)


def test_quarantine_parent_rejects_nested_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    nested = home / "nested"
    nested.mkdir(parents=True)
    actual_fstat = os.fstat
    nested_inode = nested.stat().st_ino

    def fstat_with_nested_device(fd: int):
        observed = actual_fstat(fd)
        if observed.st_ino == nested_inode:
            return SimpleNamespace(st_dev=observed.st_dev + 1, st_uid=observed.st_uid)
        return observed

    monkeypatch.setattr(migration.os, "fstat", fstat_with_nested_device)
    with pytest.raises(migration.MigrationError, match="quarantine_nested_mount_rejected"):
        migration._open_home_parent(home, nested)


@pytest.mark.parametrize("tamper", ["unknown_record", "bad_root", "bad_excluded", "logical_bytes"])
def test_verifier_rejects_noncanonical_manifest_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    target, _digest = _capture_test_candidate(tmp_path, monkeypatch, "4" * 32)
    candidate = target / migration.MIGRATION_DIR / migration.CANDIDATES_DIR / ("4" * 32)
    manifest = candidate / migration.MANIFEST
    rows = _manifest_records(manifest)
    if tamper == "unknown_record":
        rows.append({"record_type": "future_kind", "field": "value"})
    elif tamper == "bad_root":
        root = next(row for row in rows if row["record_type"] == "root")
        root["alias"] = "unknown_root"
    elif tamper == "bad_excluded":
        excluded = next(row for row in rows if row["record_type"] == "excluded")
        excluded["alias"] = "unknown_root"
    else:
        rows[0]["payload_bytes"] += 1
    manifest.write_bytes(b"".join(migration._canonical(row) for row in rows))
    new_digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    with pytest.raises(migration.MigrationError):
        migration.verify(
            target_root=target,
            candidate_id="4" * 32,
            expected_manifest_sha256=new_digest,
        )


def test_verify_rejects_target_identity_change_after_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, digest = _capture_test_candidate(tmp_path, monkeypatch, "2" * 32)
    expected = migration.TargetPin(
        migration.EXPECTED_KINGSTON_UUID, str(target), "apfs", "synthetic-target"
    )
    calls = 0

    def changing_pin(_path: Path) -> migration.TargetPin:
        nonlocal calls
        calls += 1
        if calls == 2:
            return migration.TargetPin("deadbeef", str(target), "apfs", "other-device")
        return expected

    monkeypatch.setattr(migration, "_target_pin", changing_pin)
    with pytest.raises(migration.MigrationError, match="target_volume_identity_changed"):
        migration.verify(
            target_root=target,
            candidate_id="2" * 32,
            expected_manifest_sha256=digest,
        )


def test_restore_rejects_kingston_alias_and_symlink_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, digest = _capture_test_candidate(tmp_path, monkeypatch, "f" * 32)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    with pytest.raises(migration.MigrationError, match="quarantine_location_forbidden"):
        migration.restore_inert(
            target_root=target,
            candidate_id="f" * 32,
            expected_manifest_sha256=digest,
            quarantine_root=home / "Kingston" / "BrainData" / "restore",
            _test_capability=migration._TEST_CAPTURE_CAPABILITY,
        )
    real_parent = home / "real"
    real_parent.mkdir()
    link_parent = home / "alias"
    link_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(migration.MigrationError, match="quarantine_parent_open_failed"):
        migration.restore_inert(
            target_root=target,
            candidate_id="f" * 32,
            expected_manifest_sha256=digest,
            quarantine_root=link_parent / "restore",
            _test_capability=migration._TEST_CAPTURE_CAPABILITY,
        )


def test_restore_capacity_fails_before_quarantine_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, digest = _capture_test_candidate(tmp_path, monkeypatch, "1" * 32)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(migration, "_free_bytes", lambda _fd: 0)
    quarantine = home / "restore"
    with pytest.raises(migration.MigrationError, match="capacity_floor_failed"):
        migration.restore_inert(
            target_root=target,
            candidate_id="1" * 32,
            expected_manifest_sha256=digest,
            quarantine_root=quarantine,
            _test_capability=migration._TEST_CAPTURE_CAPABILITY,
        )
    assert not quarantine.exists()


def test_manifest_digest_is_streamed_and_canonical(tmp_path: Path) -> None:
    path = tmp_path / migration.MANIFEST
    header = {
        "record_type": "header",
        "schema": migration.SCHEMA,
        "version": 1,
        "candidate_status": "complete",
        "start_or_activation": False,
        "authority": "copy_verify_restore_inert_only",
    }
    with path.open("wb") as stream:
        stream.write(migration._canonical(header))
        for index in range(1000):
            stream.write(migration._canonical({"record_type": "excluded", "path": str(index)}))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    parsed_header, records, observed = migration._read_manifest(path, digest)
    assert parsed_header["candidate_status"] == "complete"
    assert sum(1 for _ in records) == 1000
    assert observed == digest
