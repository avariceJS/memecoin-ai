from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import brain_migration_v1_d43 as d43


def _row(path: str, kind: str, *, body: bytes | None = None, link: str | None = None) -> dict:
    if kind == "symlink":
        body = os.fsencode(link or "")
    return {
        "record_type": "member",
        "alias": "protected_d43",
        "path": path,
        "type": kind,
        "size_bytes": len(body) if body is not None else 0,
        "sha256": hashlib.sha256(body).hexdigest() if body is not None else None,
        "custody_class": "protected_opaque",
        "link_target": link,
    }


def test_verify_payload_counts_directories_and_checks_opaque_bytes(tmp_path: Path) -> None:
    (tmp_path / "calendar-execution-01" / "nested").mkdir(parents=True)
    body = b"opaque protected bytes\x00\xff"
    (tmp_path / "calendar-execution-01" / "nested" / "opaque.bin").write_bytes(body)
    rows = [
        _row("calendar-execution-01", "directory"),
        _row("calendar-execution-01/nested", "directory"),
        _row("calendar-execution-01/nested/opaque.bin", "file", body=body),
    ]
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert d43._verify_payload(fd, rows) == 3
        assert {tuple(member.values()) for member in d43._tree_members_from_fd(fd)} == {
            ("calendar-execution-01", "directory"),
            ("calendar-execution-01/nested", "directory"),
            ("calendar-execution-01/nested/opaque.bin", "file"),
        }
    finally:
        os.close(fd)


def test_verify_payload_detects_byte_change_and_does_not_repair(tmp_path: Path) -> None:
    leaf = tmp_path / "opaque.bin"
    leaf.write_bytes(b"changed")
    row = _row("opaque.bin", "file", body=b"original")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(d43.D43MigrationError, match="readback_member_digest_mismatch"):
            d43._verify_payload(fd, [row])
    finally:
        os.close(fd)
    assert list(tmp_path.iterdir()) == [leaf]


def test_verify_payload_does_not_create_missing_parent(tmp_path: Path) -> None:
    row = _row("absent/opaque.bin", "file", body=b"bytes")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(FileNotFoundError):
            d43._verify_payload(fd, [row])
    finally:
        os.close(fd)
    assert list(tmp_path.iterdir()) == []


def test_verify_payload_checks_symlink_target_without_following(tmp_path: Path) -> None:
    os.symlink("missing-target", tmp_path / "opaque-link")
    row = _row("opaque-link", "symlink", link="missing-target")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert d43._verify_payload(fd, [row]) == 1
    finally:
        os.close(fd)


def test_nested_mount_device_is_rejected() -> None:
    with pytest.raises(d43.D43MigrationError, match="d43_nested_mount_rejected"):
        d43._require_source_device(SimpleNamespace(st_dev=12), 11)


def test_json_metadata_write_is_create_only_and_readback_verified(tmp_path: Path) -> None:
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        d43._write_json_create(fd, "witness.json", {"state": "durable"})
        with pytest.raises(FileExistsError):
            d43._write_json_create(fd, "witness.json", {"state": "replaced"})
    finally:
        os.close(fd)
    assert (tmp_path / "witness.json").read_bytes() == b'{"state":"durable"}\n'


def test_manifest_rejects_duplicate_member_paths(tmp_path: Path) -> None:
    pause = {
        "label": d43.LABEL,
        "source_path": str(d43.SOURCE_ROOT),
        "plist_sha256": d43.PLIST_SHA256_PIN,
        "prior_loaded": True,
        "prior_pid": "-",
        "prior_disabled_override": False,
        "disabled_after_pause": True,
        "loaded_after_pause": True,
        "loaded_after_pid": "-",
        "auto_resume": False,
        "source_mutated_by_tool": False,
        "stable_interval_seconds": d43.STABILITY_SECONDS,
        "post_pause_inventory_signature_sha256": "b" * 64,
        "post_pause_member_count": 0,
        "post_pause_logical_bytes": 0,
        "source_device": 1,
        "source_inode": 2,
        "pause_intent_sha256": "c" * 64,
    }
    header = {
        "record_type": "header",
        "schema": d43.SCHEMA,
        "version": 1,
        "candidate_id": "a" * 32,
        "candidate_status": "complete",
        "role": "D43_protected_opaque_custody_only",
        "protected_semantics_used": False,
        "start_or_activation": False,
        "launch_agent_label": d43.LABEL,
        "launch_agent_plist_sha256": d43.PLIST_SHA256_PIN,
        "source_root_path": str(d43.SOURCE_ROOT),
        "source_root_device": 1,
        "source_root_inode": 2,
        "source_inventory_signature_sha256": "b" * 64,
        "source_inventory_member_count": 0,
        "source_logical_bytes": 0,
        "pause_witness": pause,
    }
    row = _row("calendar-execution-01/opaque.bin", "file", body=b"bytes")
    row.update(
        {
            "source_path": str(d43.SOURCE_ROOT / "opaque.bin"),
            "source_signature": [1, 2, 0o100600, 1, 5, 1, 1, 1, 1, 0, 0],
            "source_device": 1,
            "source_inode": 2,
            "source_link_count": 1,
            "source_mode": 0o600,
        }
    )
    path = tmp_path / "MANIFEST.jsonl"
    path.write_bytes(d43._canonical(header) + d43._canonical(row) + d43._canonical(row))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(d43.D43MigrationError, match="manifest_member_invalid"):
        d43._read_manifest(path, digest)


def test_manifest_recomputes_lineage_counts_and_payload_bytes() -> None:
    root_sig = [1, 2, 0o40700, 1, 0, 1, 1, 501, 20, 0, 0]
    file_sig = [1, 3, 0o100600, 1, 4, 1, 1, 501, 20, 0, 1]
    plist_sig = [1, 4, 0o100644, 1, 20, 1, 1, 501, 20, 0, 1]
    source_rows = [
        {
            "path": "",
            "type": "directory",
            "source_signature": root_sig,
            "source_device": 1,
            "source_inode": 2,
            "source_link_count": 1,
            "source_mode": 0o700,
            "link_target": None,
        },
        {
            "path": "opaque.dat",
            "type": "file",
            "source_signature": file_sig,
            "source_device": 1,
            "source_inode": 3,
            "source_link_count": 1,
            "source_mode": 0o600,
            "link_target": None,
        },
    ]
    source_digest = d43._inventory_signature(source_rows)
    pause = {
        "label": d43.LABEL,
        "source_path": str(d43.SOURCE_ROOT),
        "plist_sha256": d43.PLIST_SHA256_PIN,
        "prior_loaded": True,
        "prior_pid": "-",
        "prior_disabled_override": False,
        "disabled_after_pause": True,
        "loaded_after_pause": True,
        "loaded_after_pid": "-",
        "auto_resume": False,
        "source_mutated_by_tool": False,
        "stable_interval_seconds": d43.STABILITY_SECONDS,
        "post_pause_inventory_signature_sha256": source_digest,
        "post_pause_member_count": 2,
        "post_pause_logical_bytes": 4,
        "source_device": 1,
        "source_inode": 2,
        "pause_intent_sha256": "c" * 64,
    }
    members = [
        _row("calendar-execution-01", "directory"),
        _row("calendar-execution-01/opaque.dat", "file", body=b"data"),
        _row(f"LaunchAgents/{d43.PLIST_PATH.name}", "file", body=b"x" * 20),
    ]
    for row, source, source_path in zip(
        members,
        [
            *source_rows,
            {
                "source_signature": plist_sig,
                "source_device": 1,
                "source_inode": 4,
                "source_link_count": 1,
                "source_mode": 0o644,
                "link_target": None,
            },
        ],
        [str(d43.SOURCE_ROOT), str(d43.SOURCE_ROOT / "opaque.dat"), str(d43.PLIST_PATH)],
        strict=True,
    ):
        row.update(
            {
                "source_path": source_path,
                "source_signature": source.get("source_signature", root_sig),
                "source_device": source.get("source_device", 1),
                "source_inode": source.get("source_inode", 2),
                "source_link_count": source.get("source_link_count", 1),
                "source_mode": source.get("source_mode", 0o600),
            }
        )
    members[-1]["sha256"] = d43.PLIST_SHA256_PIN
    header = {
        "record_type": "header",
        "schema": d43.SCHEMA,
        "version": 1,
        "candidate_id": "a" * 32,
        "candidate_status": "complete",
        "candidate_device": 1,
        "candidate_inode": 5,
        "role": "D43_protected_opaque_custody_only",
        "protected_semantics_used": False,
        "start_or_activation": False,
        "launch_agent_label": d43.LABEL,
        "launch_agent_plist_sha256": d43.PLIST_SHA256_PIN,
        "source_root_path": str(d43.SOURCE_ROOT),
        "source_root_device": 1,
        "source_root_inode": 2,
        "source_inventory_signature_sha256": source_digest,
        "source_inventory_member_count": 2,
        "source_logical_bytes": 4,
        "pause_witness": pause,
        "member_count": 3,
        "payload_bytes": 24,
    }
    raw = b"".join(d43._canonical(row) for row in [header, *members])
    digest = hashlib.sha256(raw).hexdigest()
    parsed_header, parsed_members = d43._parse_manifest(raw, digest)
    assert parsed_header["payload_bytes"] == 24
    assert len(parsed_members) == 3

    header["payload_bytes"] = 23
    bad_raw = b"".join(d43._canonical(row) for row in [header, *members])
    with pytest.raises(d43.D43MigrationError, match="manifest_inventory_join_invalid"):
        d43._parse_manifest(bad_raw, hashlib.sha256(bad_raw).hexdigest())


def test_lsof_read_write_mode_is_treated_as_a_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        d43.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout="p123\nctest-writer\nau\nf17w\n", returncode=0
        ),
    )
    assert d43._lsof_writers(d43.SOURCE_ROOT) == [{"pid": "123", "command": "test-writer"}]


def test_lsof_unknown_access_mode_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        d43.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="p123\nctest\na?\nf17\n", returncode=0),
    )
    with pytest.raises(d43.D43MigrationError, match="lsof_access_mode_unrecognized"):
        d43._lsof_writers(d43.SOURCE_ROOT)


def test_source_parent_rejects_replaced_directory_ancestor(tmp_path: Path) -> None:
    root = tmp_path / "root"
    original = root / "nested"
    original.mkdir(parents=True)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    st = os.stat(original)
    rows = {
        "nested": {
            "type": "directory",
            "source_device": st.st_dev,
            "source_inode": st.st_ino,
        }
    }
    original.rename(root / "old")
    replacement = root / "nested"
    replacement.mkdir()
    with pytest.raises(d43.D43MigrationError, match="source_parent_identity_changed"):
        d43._source_parent_fd(root_fd, ("nested",), rows)
    os.close(root_fd)


def test_pause_waits_without_bootout_or_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_stat = tmp_path.stat()
    tree = [
        {
            "path": "",
            "type": "directory",
            "source_signature": [
                source_stat.st_dev,
                source_stat.st_ino,
                source_stat.st_mode,
                source_stat.st_nlink,
                source_stat.st_size,
                source_stat.st_mtime_ns,
                source_stat.st_ctime_ns,
                source_stat.st_uid,
                source_stat.st_gid,
                0,
                0,
            ],
            "source_device": source_stat.st_dev,
            "source_inode": source_stat.st_ino,
            "source_link_count": source_stat.st_nlink,
            "source_mode": source_stat.st_mode,
            "link_target": None,
        }
    ]
    monkeypatch.setattr(d43, "_launchctl_list", lambda: (True, "-"))
    disabled = False

    def disabled_state() -> bool:
        return disabled

    monkeypatch.setattr(d43, "_disabled_state", disabled_state)
    monkeypatch.setattr(d43, "_root_identity", lambda _path: source_stat)
    monkeypatch.setattr(d43, "_tree_members", lambda _path: tree)
    monkeypatch.setattr(d43, "_inventory_signature", lambda _rows: "a" * 64)
    monkeypatch.setattr(d43, "_lsof_writers", lambda _path: [])
    monkeypatch.setattr(d43, "_plist_identity", lambda: (d43.PLIST_SHA256_PIN, source_stat))
    monkeypatch.setattr(d43.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(d43, "STABILITY_SECONDS", 0)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        nonlocal disabled
        calls.append(command)
        if command[1] == "disable":
            disabled = True
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(d43.subprocess, "run", fake_run)
    candidate_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        witness = d43._pause_exact_label(candidate_fd)
    finally:
        os.close(candidate_fd)
    assert witness["disabled_after_pause"] is True
    assert witness["loaded_after_pause"] is True
    assert witness["loaded_after_pid"] == "-"
    assert [command[1] for command in calls] == ["disable"]
    assert {entry.name for entry in tmp_path.iterdir()} == {
        "PAUSE_INTENT.json",
        "PAUSE_STARTED.json",
    }
    intent = (tmp_path / "PAUSE_INTENT.json").read_bytes()
    assert hashlib.sha256(intent).hexdigest() == witness["pause_intent_sha256"]


def test_d43_capture_verify_restore_end_to_end_is_opaque_and_inert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "nested").mkdir()
    opaque = b"protected opaque bytes\x00\xff"
    (source / "nested" / "opaque.bin").write_bytes(opaque)
    os.symlink("nested/opaque.bin", source / "opaque-link")
    plist = tmp_path / "ai.memecoin.d43.calendar01.plist"
    plist_bytes = b"opaque LaunchAgent definition"
    plist.write_bytes(plist_bytes)
    digest = hashlib.sha256(plist_bytes).hexdigest()

    target = tmp_path / "kingston"
    (target / d43.MIGRATION_DIR).mkdir(parents=True)
    home = tmp_path / "second-home"
    home.mkdir()
    (home / "BrainRestore").mkdir()
    quarantine = home / "BrainRestore" / "d43"
    monkeypatch.setattr(d43, "SOURCE_ROOT", source)
    monkeypatch.setattr(d43, "PLIST_PATH", plist)
    monkeypatch.setattr(d43, "PLIST_SHA256_PIN", digest)
    monkeypatch.setattr(d43.Path, "home", classmethod(lambda _cls: home))
    monkeypatch.setattr(d43, "STABILITY_SECONDS", 0)
    monkeypatch.setattr(d43.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(d43, "_lsof_writers", lambda _path: [])
    monkeypatch.setattr(d43, "_launchctl_list", lambda: (True, "-"))
    disabled = False
    calls: list[list[str]] = []

    def disabled_state() -> bool:
        return disabled

    def fake_launchctl(command: list[str], **kwargs: object) -> SimpleNamespace:
        nonlocal disabled
        calls.append(command)
        if command[1] == "disable":
            disabled = True
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(d43, "_disabled_state", disabled_state)
    monkeypatch.setattr(d43.subprocess, "run", fake_launchctl)
    monkeypatch.setattr(
        d43.base,
        "_validate_target",
        lambda path: (
            os.open(path, os.O_RDONLY | os.O_DIRECTORY),
            d43.base.TargetPin("kingston-test", str(path), "apfs", "disk-test"),
        ),
    )
    monkeypatch.setattr(d43.base, "_assert_held_target_identity", lambda *_args: None)
    monkeypatch.setattr(d43.base, "_free_bytes", lambda _fd: 2**50)
    monkeypatch.setattr(d43, "_require_separate_source_target", lambda *_args: None)
    # This fixture's source, target, and restore paths share one temporary APFS volume.
    # The distinct-volume policy has its own real check; skip only that topology predicate here.
    monkeypatch.setattr(d43, "_validate_restore_devices", lambda *_args: None)

    candidate_id = "c" * 32
    captured = d43.capture_d43(target_root=target, candidate_id=candidate_id)
    assert captured["state"] == "protected_opaque_candidate_verified"
    assert captured["protected_semantics_used"] is False
    assert captured["member_count"] == 6
    assert calls == [["/bin/launchctl", "disable", f"gui/{os.getuid()}/{d43.LABEL}"]]
    assert disabled is True

    restored = d43.restore_d43_inert(
        target_root=target,
        candidate_id=candidate_id,
        expected_manifest_sha256=captured["manifest_sha256"],
        quarantine_root=quarantine,
    )
    restored_root = quarantine / "payload"
    assert (restored_root / "calendar-execution-01/nested/opaque.bin").read_bytes() == opaque
    assert os.readlink(restored_root / "calendar-execution-01/opaque-link") == "nested/opaque.bin"
    assert (restored_root / f"LaunchAgents/{plist.name}").read_bytes() == plist_bytes
    assert restored["state"] == "protected_opaque_inert_quarantine_verified"
    assert restored["start_or_activation"] is False
    assert calls == [["/bin/launchctl", "disable", f"gui/{os.getuid()}/{d43.LABEL}"]]


def test_capture_failure_after_pause_keeps_durable_incomplete_witness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "opaque.bin").write_bytes(b"opaque")
    plist = tmp_path / "ai.memecoin.d43.calendar01.plist"
    plist.write_bytes(b"plist")
    target = tmp_path / "kingston"
    (target / d43.MIGRATION_DIR).mkdir(parents=True)
    monkeypatch.setattr(d43, "SOURCE_ROOT", source)
    monkeypatch.setattr(d43, "PLIST_PATH", plist)
    monkeypatch.setattr(d43, "PLIST_SHA256_PIN", hashlib.sha256(b"plist").hexdigest())
    monkeypatch.setattr(d43, "STABILITY_SECONDS", 0)
    monkeypatch.setattr(d43.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(d43, "_lsof_writers", lambda _path: [])
    monkeypatch.setattr(d43, "_launchctl_list", lambda: (True, "-"))
    disabled = False
    commands: list[list[str]] = []

    def disabled_state() -> bool:
        return disabled

    def fake_launchctl(command: list[str], **_kwargs: object) -> SimpleNamespace:
        nonlocal disabled
        commands.append(command)
        if command[1] == "disable":
            disabled = True
        return SimpleNamespace(stdout="", returncode=0)

    def fail_copy(*_args: object, **_kwargs: object) -> list[dict]:
        raise d43.D43MigrationError("injected_copy_failure")

    monkeypatch.setattr(d43, "_disabled_state", disabled_state)
    monkeypatch.setattr(d43.subprocess, "run", fake_launchctl)
    monkeypatch.setattr(d43, "_copy_members", fail_copy)
    monkeypatch.setattr(
        d43.base,
        "_validate_target",
        lambda path: (
            os.open(path, os.O_RDONLY | os.O_DIRECTORY),
            d43.base.TargetPin("kingston-test", str(path), "apfs", "disk-test"),
        ),
    )
    monkeypatch.setattr(d43.base, "_assert_held_target_identity", lambda *_args: None)
    monkeypatch.setattr(d43.base, "_free_bytes", lambda _fd: 2**50)
    monkeypatch.setattr(d43, "_require_separate_source_target", lambda *_args: None)

    candidate_id = "e" * 32
    with pytest.raises(d43.D43MigrationError, match="injected_copy_failure"):
        d43.capture_d43(target_root=target, candidate_id=candidate_id)
    candidate = target / d43.MIGRATION_DIR / d43.PROTECTED_DIR / candidate_id
    assert (candidate / "PAUSE_INTENT.json").is_file()
    assert (candidate / "PAUSE_STARTED.json").is_file()
    assert (candidate / "PAUSE_WITNESS.json").is_file()
    assert not (candidate / "MANIFEST.jsonl").exists()
    assert not (candidate / "CAPTURE_COMPLETE.json").exists()
    assert disabled is True
    assert [command[1] for command in commands] == ["disable"]
