from __future__ import annotations

import json
import os
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest

from tools import brain_host_tree_custody as custody
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

_real_check_extended_metadata_fd = custody._check_extended_metadata_fd


@pytest.fixture(autouse=True)
def synthetic_sources_have_no_extended_metadata(monkeypatch):
    # The host's Python/xattr APIs vary; archive behavior remains synthetic,
    # and the separate test below exercises fail-closed detection explicitly.
    monkeypatch.setattr(custody, "_check_extended_metadata_fd", lambda _fd: {})
    monkeypatch.setattr(custody, "_check_extended_metadata", lambda *_a, **_k: {})


@pytest.fixture
def writer(tmp_path: Path):
    root = tmp_path / "archive"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with DescriptorWriter(fd) as value:
            yield value, root
    finally:
        os.close(fd)


def make_source(path: Path) -> Path:
    root = path / "sample-root"
    root.mkdir()
    (root / "nested").mkdir()
    db = root / "nested" / "state.sqlite"
    db.write_bytes(b"opaque database bytes\x00\x01")
    (root / "nested" / "state.sqlite-wal").write_bytes(b"opaque wal")
    os.link(db, root / "state-hardlink")
    os.symlink("../../outside-target", root / "outside-link")
    return root.resolve()


def test_capture_verify_extract_preserves_bytes_and_topology(writer, tmp_path):
    archive, _ = writer
    source = make_source(tmp_path)
    result = custody.capture_tree(
        archive,
        custody.TreeRoot("synthetic", "test-authority", "asserted-rp-1", source),
        capture_id="1" * 32,
    )
    assert result["complete"] is True
    verified = custody.verify_tree(archive, "1" * 32)
    assert verified["verified_bytes"] is True
    restored_parent = tmp_path / "restore-parent"
    restored_parent.mkdir()
    restored = custody.extract_tree(
        archive,
        "1" * 32,
        restored_parent / "quarantine",
        target_uid=os.geteuid(),
        target_gid=os.getegid(),
    )
    assert restored["restore_complete"] is True
    tree = restored_parent / "quarantine" / "synthetic"
    assert (tree / "nested" / "state.sqlite").read_bytes() == b"opaque database bytes\x00\x01"
    assert (tree / "nested" / "state.sqlite-wal").read_bytes() == b"opaque wal"
    assert os.readlink(tree / "outside-link") == "../../outside-target"
    assert (
        os.stat(tree / "nested" / "state.sqlite").st_ino == os.stat(tree / "state-hardlink").st_ino
    )
    assert (restored_parent / "quarantine" / "RESTORE_COMPLETE.json").is_file()


def test_rejects_symlink_root_and_unsafe_relative_path(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    root = custody.TreeRoot("synthetic", "auth", "rp", alias)
    with pytest.raises(custody.TreeCustodyError, match="source_root_open_failed"):
        custody.capture_tree(_dummy_writer(tmp_path), root, capture_id="2" * 32)
    for value in ("../escape", "/absolute", "a//b", "a\\b"):
        with pytest.raises(custody.TreeCustodyError):
            custody._relative(value, protected=True)
    for value in (".env", ".ssh", "credentials", "wallet.pem"):
        with pytest.raises(custody.TreeCustodyError):
            custody._component(value, protected=True)


def _dummy_writer(tmp_path: Path) -> DescriptorWriter:
    root = tmp_path / "archive-dummy"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(fd)
    os.close(fd)
    return writer


def test_hardlink_outside_declared_root_is_rejected(writer, tmp_path):
    archive, _ = writer
    outside = tmp_path / "outside"
    outside.write_bytes(b"shared")
    source = tmp_path / "source"
    source.mkdir()
    os.link(outside, source / "inside")
    with pytest.raises(custody.TreeCustodyError, match="hardlink_escapes_declared_root"):
        custody.capture_tree(
            archive,
            custody.TreeRoot("synthetic", "auth", "rp", source.resolve()),
            capture_id="3" * 32,
        )


def test_no_complete_marker_after_prepublication_failure(writer, tmp_path, monkeypatch):
    archive, root = writer
    source = tmp_path / "source"
    source.mkdir()
    (source / "data").write_bytes(b"x")
    monkeypatch.setattr(
        custody,
        "_verify_internal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            custody.TreeCustodyError("injected_verify_failure")
        ),
    )
    with pytest.raises(custody.TreeCustodyError, match="injected_verify_failure"):
        custody.capture_tree(
            archive,
            custody.TreeRoot("synthetic", "auth", "rp", source.resolve()),
            capture_id="4" * 32,
        )
    assert not (root / custody.BASE / ("4" * 32) / "COMPLETE.json").exists()


def test_source_membership_drift_fails_without_complete(writer, tmp_path, monkeypatch):
    archive, root = writer
    source = tmp_path / "source"
    source.mkdir()
    (source / "data").write_bytes(b"x")
    original = custody._emit_entry_pages

    def mutate_after_copy(*args, **kwargs):
        result = original(*args, **kwargs)
        (source / "late").write_bytes(b"new")
        return result

    monkeypatch.setattr(custody, "_emit_entry_pages", mutate_after_copy)
    with pytest.raises(custody.TreeCustodyError, match="source_membership_drift"):
        custody.capture_tree(
            archive,
            custody.TreeRoot("synthetic", "auth", "rp", source.resolve()),
            capture_id="5" * 32,
        )
    assert not (root / custody.BASE / ("5" * 32) / "COMPLETE.json").exists()


def test_chunk_corruption_and_capture_collision_rejected(writer, tmp_path):
    archive, root = writer
    source = tmp_path / "source"
    source.mkdir()
    (source / "data").write_bytes(b"payload")
    custody.capture_tree(
        archive,
        custody.TreeRoot("synthetic", "auth", "rp", source.resolve()),
        capture_id="6" * 32,
    )
    with pytest.raises(custody.TreeCustodyError, match="capture_id_collision"):
        custody.capture_tree(
            archive,
            custody.TreeRoot("synthetic", "auth", "rp", source.resolve()),
            capture_id="6" * 32,
        )
    chunk = next((root / custody.BASE / ("6" * 32) / "chunks").rglob("*.bin"))
    chunk.chmod(0o600)
    chunk.write_bytes(b"changed")
    with pytest.raises(custody.TreeCustodyError, match="chunk_hash_mismatch"):
        custody.verify_tree(archive, "6" * 32)


def test_xattrs_are_rejected_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(custody, "_list_xattrs_fd", lambda *_a, **_k: [b"user.test"])
    with pytest.raises(custody.TreeCustodyError, match="extended_metadata_not_preserved"):
        _real_check_extended_metadata_fd(123)


def test_only_documented_darwin_provenance_xattr_is_ignored(monkeypatch):
    monkeypatch.setattr(custody.sys, "platform", "darwin")
    known = [name.encode() for name in custody.OMITTED_DARWIN_XATTRS]
    monkeypatch.setattr(custody, "_list_xattrs_fd", lambda _fd: known)

    class FakeLib:
        def __getattr__(self, _name):
            class FakeFunction:
                def __call__(self, *_args):
                    return 0

            return FakeFunction()

    monkeypatch.setattr(custody.ctypes, "CDLL", lambda *_a, **_k: FakeLib())
    monkeypatch.setattr(custody.ctypes, "get_errno", lambda: 0)
    counts = _real_check_extended_metadata_fd(123)
    assert counts == {name.decode(): 1 for name in known}
    monkeypatch.setattr(custody, "_list_xattrs_fd", lambda _fd: [b"com.apple.quarantine"])
    with pytest.raises(custody.TreeCustodyError, match="extended_metadata_not_preserved"):
        _real_check_extended_metadata_fd(123)


def test_real_file_and_directory_extended_metadata_probe(tmp_path):
    directory = tmp_path / "metadata-probe"
    directory.mkdir()
    file_path = directory / "empty"
    file_path.write_bytes(b"")
    dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    file_fd = os.open(file_path, os.O_RDONLY)
    try:
        _real_check_extended_metadata_fd(dir_fd)
        _real_check_extended_metadata_fd(file_fd)
    finally:
        os.close(file_fd)
        os.close(dir_fd)


def test_entry_pages_stream_more_than_one_hundred_thousand_entries(writer):
    archive, _ = writer
    with __import__("tempfile").TemporaryDirectory() as temp:
        db = __import__("sqlite3").connect(Path(temp) / "inventory.sqlite")
        db.execute(
            "CREATE TABLE entries(path TEXT PRIMARY KEY, kind TEXT, mode INTEGER, "
            "uid INTEGER, gid INTEGER, size INTEGER, mtime INTEGER, ctime INTEGER, "
            "dev INTEGER, ino INTEGER, nlink INTEGER, sig BLOB, link_b64 TEXT, "
            "group_id TEXT, primary_path TEXT, digest TEXT, chunk_count INTEGER, "
            "chunk_pages TEXT, omitted_xattrs TEXT)"
        )
        db.executemany(
            "INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                (
                    f"d{i:06d}",
                    "directory",
                    0o755,
                    1,
                    2,
                    0,
                    3,
                    4,
                    5,
                    i + 1,
                    1,
                    b"[1,2,0,493,1,2,0,1,3,4,0]",
                    None,
                    None,
                    None,
                    None,
                    0,
                    "[]",
                    "[]",
                )
                for i in range(100_001)
            ),
        )
        refs = custody._emit_entry_pages(
            archive, f"{custody.BASE}/" + "7" * 32, db, protected=False
        )
        assert len(refs) > 195
        assert sum(ref["count"] for ref in refs) == 100_001
        db.close()


def test_entry_reordering_is_rejected_even_if_page_hashes_are_recomputed(writer, tmp_path):
    archive, archive_path = writer
    source = tmp_path / "source"
    source.mkdir()
    (source / "a").write_bytes(b"a")
    (source / "b").write_bytes(b"b")
    capture_id = "8" * 32
    custody.capture_tree(
        archive,
        custody.TreeRoot("synthetic", "auth", "rp", source.resolve()),
        capture_id=capture_id,
    )
    base = archive_path / custody.BASE / capture_id
    manifest_path = base / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_bytes())
    index_path = archive_path / manifest["index_pages"][0]["path"]
    index = json.loads(index_path.read_bytes())
    page_path = archive_path / index["rows"][0]["path"]
    lines = page_path.read_bytes().splitlines()
    reordered = b"\n".join([lines[0], *reversed(lines[1:])]) + b"\n"
    page_path.write_bytes(reordered)
    index["rows"][0]["sha256"] = sha256(reordered)
    index_raw = canonical_json(index)
    index_path.write_bytes(index_raw)
    manifest["index_pages"][0]["sha256"] = sha256(index_raw)
    manifest_path.write_bytes(canonical_json(manifest))
    with pytest.raises(custody.TreeCustodyError, match="entry_order_invalid"):
        custody.verify_tree(archive, capture_id)


def test_file_signature_drift_fails_without_complete(writer, tmp_path, monkeypatch):
    archive, archive_path = writer
    source = tmp_path / "source"
    source.mkdir()
    data_path = source / "data"
    data_path.write_bytes(b"before")
    original = custody._emit_entry_pages

    def mutate_after_copy(*args, **kwargs):
        result = original(*args, **kwargs)
        data_path.write_bytes(b"after!")
        return result

    monkeypatch.setattr(custody, "_emit_entry_pages", mutate_after_copy)
    capture_id = "9" * 32
    with pytest.raises(custody.TreeCustodyError, match="source_member_signature_drift"):
        custody.capture_tree(
            archive,
            custody.TreeRoot("synthetic", "auth", "rp", source.resolve()),
            capture_id=capture_id,
        )
    assert not (archive_path / custody.BASE / capture_id / "COMPLETE.json").exists()


def test_manifest_binds_observed_interval_and_non_atomic_scope(writer, tmp_path):
    archive, archive_path = writer
    source = tmp_path / "interval-source"
    source.mkdir()
    (source / "data").write_bytes(b"opaque")
    capture_id = "b" * 32
    custody.capture_tree(
        archive,
        custody.TreeRoot("synthetic", "auth", "rp", source.resolve()),
        capture_id=capture_id,
    )
    base = archive_path / custody.BASE / capture_id
    manifest = json.loads((base / "MANIFEST.json").read_bytes())
    complete = json.loads((base / "COMPLETE.json").read_bytes())
    assert manifest["capture_start_ns"] <= manifest["capture_end_ns"]
    assert manifest["capture_end_ns"] <= complete["complete_ns"]
    assert complete["capture_start_ns"] == manifest["capture_start_ns"]
    assert complete["capture_end_ns"] == manifest["capture_end_ns"]
    assert manifest["stability_claim"] == custody.STABILITY_CLAIM
    assert manifest["atomicity_scope"] == "no_cross_file_or_application_atomicity"
    assert manifest["root_closure_scope"] == custody.ROOT_CLOSURE_SCOPE
    verified = custody.verify_tree(archive, capture_id)
    assert verified["complete_ns"] == complete["complete_ns"]


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin st_flags are platform-specific")
def test_uf_hidden_is_signature_bound_and_restored(writer, tmp_path, monkeypatch):
    archive, archive_path = writer
    source = tmp_path / "flag-source"
    source.mkdir()
    source_file = source / "hidden"
    source_file.write_bytes(b"flagged bytes")
    os.chflags(source_file, custody.UF_HIDDEN)
    capture_id = "c" * 32
    custody.capture_tree(
        archive,
        custody.TreeRoot("synthetic", "auth", "rp", source.resolve()),
        capture_id=capture_id,
    )
    restore_parent = tmp_path / "flag-restore"
    restore_parent.mkdir()
    custody.extract_tree(
        archive,
        capture_id,
        restore_parent / "quarantine",
        target_uid=os.geteuid(),
        target_gid=os.getegid(),
    )
    restored = restore_parent / "quarantine" / "synthetic" / "hidden"
    assert os.stat(restored, follow_symlinks=False).st_flags & custody.UF_HIDDEN

    drift_archive_path = archive_path
    source2 = tmp_path / "flag-drift-source"
    source2.mkdir()
    file2 = source2 / "data"
    file2.write_bytes(b"before")
    original_emit = custody._emit_entry_pages

    def flip_flag_after_copy(*args, **kwargs):
        emitted = original_emit(*args, **kwargs)
        os.chflags(file2, custody.UF_HIDDEN)
        return emitted

    monkeypatch.setattr(custody, "_emit_entry_pages", flip_flag_after_copy)
    drift_id = "d" * 32
    with pytest.raises(custody.TreeCustodyError, match="source_member_signature_drift"):
        custody.capture_tree(
            archive,
            custody.TreeRoot("synthetic", "auth", "rp", source2.resolve()),
            capture_id=drift_id,
        )
    assert not (drift_archive_path / custody.BASE / drift_id / "COMPLETE.json").exists()
    os.chflags(file2, 0)
    os.chflags(source_file, 0)


def test_protected_oserror_does_not_publish_path_or_message(writer, tmp_path, monkeypatch, capsys):
    archive, _ = writer
    declaration = tmp_path / "protected-root.json"
    declaration.write_text(
        json.dumps(
            {
                "name": "protected-fixture",
                "authority_id": "test-authority",
                "recovery_point_id": "caller-asserted-test-point",
                "path": str(tmp_path / "protected-source"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(custody, "_storage_writer", lambda _path: nullcontext(archive))

    def fail_permission(*_args, **_kwargs):
        raise PermissionError(13, "permission denied", "SECRET_MEMBER_BASENAME")

    monkeypatch.setattr(custody, "capture_tree", fail_permission)
    assert (
        custody.main(
            [
                "capture",
                "--storage-config",
                str(tmp_path / "unused-config"),
                "--root",
                str(declaration),
                "--scope",
                "protected_opaque",
                "--capture-id",
                "e" * 32,
            ]
        )
        == 2
    )
    output = capsys.readouterr().out
    assert "SECRET_MEMBER_BASENAME" not in output
    assert "permission denied" not in output
    assert '"error": "os_error"' in output
    assert '"errno": 13' in output


def test_protected_public_result_is_aggregate_only(writer, tmp_path):
    archive, _ = writer
    source = tmp_path / "synthetic-protected"
    source.mkdir()
    (source / "F01-known-locator").write_bytes(b"fixture")
    result = custody.capture_tree(
        archive,
        custody.TreeRoot("private-root", "authority", "rp", source.resolve(), "protected_opaque"),
        capture_id="a" * 32,
    )
    verified = custody.verify_tree(archive, "a" * 32)
    assert result["scope"] == "protected_opaque"
    assert verified["scope"] == "protected_opaque"
    assert "root_name" not in result and "root_name" not in verified
    assert "F01-known-locator" not in json.dumps(result)
    assert "F01-known-locator" not in json.dumps(verified)


def test_existing_restore_target_is_rejected(writer, tmp_path):
    archive, _ = writer
    source = tmp_path / "source"
    source.mkdir()
    custody.capture_tree(
        archive,
        custody.TreeRoot("synthetic", "auth", "rp", source.resolve()),
        capture_id="b" * 32,
    )
    target = tmp_path / "already-there"
    target.mkdir()
    with pytest.raises(custody.TreeCustodyError, match="restore_target_must_be_absent"):
        custody.extract_tree(archive, "b" * 32, target)


@pytest.mark.parametrize(
    ("mutated_object", "expected_error"),
    [
        ("manifest", "restore_manifest_hash_mismatch"),
        ("index", "restore_index_page_hash_mismatch"),
        ("entry", "restore_entry_page_hash_mismatch"),
        ("chunk_index", "restore_chunk_index_page_hash_mismatch"),
    ],
)
def test_restore_rechecks_pinned_metadata_after_initial_verify(
    writer, tmp_path, monkeypatch, mutated_object, expected_error
):
    archive, archive_path = writer
    source = tmp_path / "source"
    source.mkdir()
    data_file = source / "data"
    data_file.write_bytes(b"restore metadata must stay pinned")
    data_file.chmod(0o640)
    capture_id = "f" * 32
    custody.capture_tree(
        archive,
        custody.TreeRoot("synthetic", "auth", "rp", source.resolve()),
        capture_id=capture_id,
    )
    base = archive_path / custody.BASE / capture_id
    original_verify = custody.verify_tree

    def verify_then_mutate(writer_arg, requested_id):
        result = original_verify(writer_arg, requested_id)
        manifest_path = base / "MANIFEST.json"
        manifest = json.loads(manifest_path.read_bytes())
        if mutated_object == "manifest":
            manifest["root_name"] = "changed-root"
            manifest_path.write_bytes(canonical_json(manifest))
        elif mutated_object == "index":
            index_ref = manifest["index_pages"][0]
            index_path = archive_path / index_ref["path"]
            index = json.loads(index_path.read_bytes())
            index["rows"][0]["count"] += 1
            index_path.write_bytes(canonical_json(index))
        elif mutated_object == "entry":
            index_ref = manifest["index_pages"][0]
            index = json.loads((archive_path / index_ref["path"]).read_bytes())
            entry_path = archive_path / index["rows"][0]["path"]
            raw = entry_path.read_bytes()
            entries = [json.loads(line) for line in raw.splitlines()]
            file_entry = next(entry for entry in entries if entry.get("path") == "data")
            assert file_entry["mode"] == 0o640
            file_entry["mode"] = 0o600
            entry_path.write_bytes(
                b"\n".join(canonical_json(entry).rstrip(b"\n") for entry in entries) + b"\n"
            )
        else:
            index_ref = manifest["index_pages"][0]
            index = json.loads((archive_path / index_ref["path"]).read_bytes())
            entry_ref = index["rows"][0]
            entries = (archive_path / entry_ref["path"]).read_bytes().splitlines()
            file_entry = next(json.loads(line) for line in entries if b'"kind":"file"' in line)
            chunk_ref = file_entry["chunk_index_pages"][0]
            chunk_index_path = archive_path / chunk_ref["path"]
            chunk_index = json.loads(chunk_index_path.read_bytes())
            chunk_index["rows"][0]["sha256"] = "0" * 64
            chunk_index_path.write_bytes(canonical_json(chunk_index))
        return result

    monkeypatch.setattr(custody, "verify_tree", verify_then_mutate)
    destination = tmp_path / "quarantine"
    with pytest.raises(custody.TreeCustodyError, match=expected_error):
        custody.extract_tree(archive, capture_id, destination)
    assert not (destination / "RESTORE_COMPLETE.json").exists()


def test_restore_fsync_failure_leaves_no_completion_marker(writer, tmp_path, monkeypatch):
    archive, _ = writer
    source = tmp_path / "source"
    source.mkdir()
    (source / "data").write_bytes(b"durable payload")
    capture_id = "c" * 32
    custody.capture_tree(
        archive,
        custody.TreeRoot("synthetic", "auth", "rp", source.resolve()),
        capture_id=capture_id,
    )
    real_fsync = os.fsync
    calls = 0

    def fail_file_sync(fd):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("injected file fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(custody.os, "fsync", fail_file_sync)
    destination = tmp_path / "quarantine"
    with pytest.raises(OSError, match="injected file fsync failure"):
        custody.extract_tree(archive, capture_id, destination)
    assert not (destination / "RESTORE_COMPLETE.json").exists()


def test_restore_completion_write_handles_short_writes(monkeypatch):
    read_fd, write_fd = os.pipe()
    real_write = os.write

    def short_write(fd, data):
        return real_write(fd, data[:2])

    monkeypatch.setattr(custody.os, "write", short_write)
    try:
        custody._write_all_fd(write_fd, b"complete marker")
        monkeypatch.setattr(custody.os, "write", real_write)
        assert os.read(read_fd, 64) == b"complete marker"
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_destination_corruption_is_detected_before_restore_marker(writer, tmp_path, monkeypatch):
    archive, _ = writer
    source = tmp_path / "source"
    source.mkdir()
    (source / "data").write_bytes(b"verified input")
    capture_id = "d" * 32
    custody.capture_tree(
        archive,
        custody.TreeRoot("synthetic", "auth", "rp", source.resolve()),
        capture_id=capture_id,
    )
    original = custody._apply_fd_metadata

    def corrupt_after_write(fd, entry, uid, gid):
        if entry["kind"] == "file":
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, b"X")
        original(fd, entry, uid, gid)

    monkeypatch.setattr(custody, "_apply_fd_metadata", corrupt_after_write)
    destination = tmp_path / "quarantine"
    with pytest.raises(custody.TreeCustodyError, match="restore_file_hash_mismatch"):
        custody.extract_tree(archive, capture_id, destination)
    assert not (destination / "RESTORE_COMPLETE.json").exists()
