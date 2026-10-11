from __future__ import annotations

import hashlib
import json
import os
import resource
from pathlib import Path

import pytest

from memecoin_research_storage import migration
from memecoin_research_storage.migration import MigrationError
from tools.research_storage_migrate import _MigrationGuardCheck, _read_preflight


def setup_tree(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    source_parent = tmp_path / "internal" / "work"
    source = source_parent / "pair64"
    source.mkdir(parents=True)
    (source / "nested").mkdir()
    payload = source / "nested" / "receipt.bin"
    payload.write_bytes(b"receipt bytes\x00" * 100)
    payload.chmod(0o640)
    (source / "txf01a9d43b8.capture").write_bytes(b"opaque filename")
    os.link(payload, source / "receipt-hardlink.bin")
    (source / "receipt-link").symlink_to("nested/receipt.bin")
    external = tmp_path / "external" / "BrainData"
    external.mkdir(parents=True)
    dest_parent = external / "archive" / "work"
    dest_parent.mkdir(parents=True)
    receipt = tmp_path / "closed-root-preflight.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "group": source.name,
                "source_root": str(source),
                "closed": True,
                "no_active_writers": True,
                "no_protected_payloads": True,
                "closure_evidence": ["fixture assertion"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    receipt.chmod(0o600)
    return source, external, dest_parent, receipt


def stage(source: Path, external: Path, dest_parent: Path, receipt: Path) -> dict[str, object]:
    return migration.stage_migration(
        source,
        external,
        dest_parent / source.name,
        group=source.name,
        expected_source_root=source,
        closed_preflight=lambda _: None,
        preflight_receipt_path=receipt,
        preflight_receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),
    )


def test_stage_and_verify_preserve_tree_bytes_modes_symlinks_and_hardlinks(tmp_path: Path) -> None:
    source, external, dest_parent, receipt = setup_tree(tmp_path)
    result = stage(source, external, dest_parent, receipt)
    destination = dest_parent / source.name
    manifest_path = destination.with_name(destination.name + ".manifest.json")
    verify = migration.verify_migration(
        source,
        destination,
        manifest_path,
        expected_source_root=source,
        expected_destination_root=destination,
    )
    assert verify["accepted"] is True
    assert verify["manifest_sha256"] == result["manifest_sha256"]
    assert (destination / "nested" / "receipt.bin").read_bytes() == (
        source / "nested" / "receipt.bin"
    ).read_bytes()
    assert (destination / "nested" / "receipt.bin").stat().st_ino == (
        destination / "receipt-hardlink.bin"
    ).stat().st_ino
    assert os.readlink(destination / "receipt-link") == "nested/receipt.bin"
    assert (destination / "nested" / "receipt.bin").stat().st_mode & 0o777 == 0o640
    assert manifest_path.stat().st_mode & 0o777 == 0o444


def test_stage_and_replay_record_persistent_destination_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, external, dest_parent, receipt = setup_tree(tmp_path)
    source_inodes: set[tuple[int, int]] = set()
    for directory, child_dirs, files in os.walk(source, followlinks=False):
        parent = Path(directory)
        source_inodes.add((parent.stat().st_dev, parent.stat().st_ino))
        for name in [*child_dirs, *files]:
            item = parent / name
            info = item.lstat()
            source_inodes.add((info.st_dev, info.st_ino))
    provenance = b"persistent destination provenance"
    provenance_record = {
        "name": "com.apple.provenance",
        "size": len(provenance),
        "sha256": hashlib.sha256(provenance).hexdigest(),
    }

    def fd_xattrs(fd: int) -> list[str]:
        info = os.fstat(fd)
        return [] if (info.st_dev, info.st_ino) in source_inodes else ["com.apple.provenance"]

    def path_xattrs(path: Path) -> list[str]:
        try:
            path.relative_to(source)
        except ValueError:
            return ["com.apple.provenance"]
        return []

    monkeypatch.setattr(migration, "_native_xattrs_fd", fd_xattrs)
    monkeypatch.setattr(migration, "_get_xattr_fd", lambda _fd, _name: provenance)
    # Simulate macOS provenance that remains present even after a removal attempt.
    monkeypatch.setattr(migration, "_remove_xattr_fd", lambda _fd, _name: None)
    monkeypatch.setattr(migration, "_native_xattrs_path", path_xattrs)
    monkeypatch.setattr(migration, "_get_xattr_path", lambda _path, _name: provenance)

    result = stage(source, external, dest_parent, receipt)
    destination = dest_parent / source.name
    manifest_path = destination.with_name(destination.name + ".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["version"] == migration.MANIFEST_VERSION == 3
    assert manifest["destination_added_root_xattrs"] == [provenance_record]
    additions_by_path = {
        item["path"]: item["xattrs"] for item in manifest["destination_added_xattrs"]
    }
    assert additions_by_path["nested/receipt.bin"] == [provenance_record]
    assert additions_by_path["receipt-hardlink.bin"] == [provenance_record]
    assert additions_by_path["receipt-link"] == [provenance_record]

    verified = migration.verify_migration(
        source,
        destination,
        manifest_path,
        expected_source_root=source,
        expected_destination_root=destination,
    )
    assert verified["accepted"] is True
    assert verified["manifest_sha256"] == result["manifest_sha256"]


def test_stage_runs_full_checks_at_each_publication_boundary(tmp_path: Path) -> None:
    source, external, dest_parent, receipt = setup_tree(tmp_path)
    calls = 0

    def full_check() -> None:
        nonlocal calls
        calls += 1

    migration.stage_migration(
        source,
        external,
        dest_parent / source.name,
        group=source.name,
        expected_source_root=source,
        closed_preflight=lambda _: None,
        preflight_receipt_path=receipt,
        preflight_receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),
        before_publish=full_check,
    )
    assert calls == 4


def test_copy_revalidates_at_each_bounded_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, external, dest_parent, receipt = setup_tree(tmp_path)
    payload = source / "large.bin"
    payload.write_bytes(b"x" * (3 * 128 * 1024))
    monkeypatch.setattr(migration, "CHUNK_SIZE", 128 * 1024)
    checks = 0

    def check() -> None:
        nonlocal checks
        checks += 1

    migration.stage_migration(
        source,
        external,
        dest_parent / source.name,
        group=source.name,
        expected_source_root=source,
        closed_preflight=lambda _: None,
        preflight_receipt_path=receipt,
        preflight_receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),
        before_write=check,
    )
    assert checks >= 3


def test_copy_xattrs_removes_destination_only_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "source"
    destination_path = tmp_path / "destination"
    source_path.write_bytes(b"source")
    destination_path.write_bytes(b"destination")
    source_fd = os.open(source_path, os.O_RDONLY)
    destination_fd = os.open(destination_path, os.O_RDONLY)
    attrs = {source_fd: {}, destination_fd: {"com.apple.provenance": b"automatic"}}
    monkeypatch.setattr(migration, "_native_xattrs_fd", lambda fd: sorted(attrs[fd]))
    monkeypatch.setattr(migration, "_get_xattr_fd", lambda fd, name: attrs[fd][name])
    monkeypatch.setattr(
        migration,
        "_remove_xattr_fd",
        lambda fd, name: attrs[fd].pop(name),
    )
    monkeypatch.setattr(
        migration,
        "_set_xattr_fd",
        lambda fd, name, value: attrs[fd].__setitem__(name, value),
    )
    try:
        migration._copy_xattrs_fd(source_fd, destination_fd, [], "capture.bin")
        assert attrs[destination_fd] == {}
    finally:
        os.close(source_fd)
        os.close(destination_fd)


def test_copy_xattrs_reports_source_drift_before_destination_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "source"
    destination_path = tmp_path / "destination"
    source_path.write_bytes(b"source")
    destination_path.write_bytes(b"destination")
    source_fd = os.open(source_path, os.O_RDONLY)
    destination_fd = os.open(destination_path, os.O_RDONLY)
    attrs = {
        source_fd: {"user.note": b"changed"},
        destination_fd: {"com.apple.provenance": b"automatic"},
    }
    monkeypatch.setattr(migration, "_native_xattrs_fd", lambda fd: sorted(attrs[fd]))
    monkeypatch.setattr(migration, "_get_xattr_fd", lambda fd, name: attrs[fd][name])
    removed: list[str] = []
    monkeypatch.setattr(
        migration,
        "_remove_xattr_fd",
        lambda _fd, name: removed.append(name),
    )
    try:
        with pytest.raises(MigrationError, match="source_xattrs_changed_during_copy"):
            migration._copy_xattrs_fd(source_fd, destination_fd, [], "capture.bin")
        assert not removed
        assert attrs[destination_fd] == {"com.apple.provenance": b"automatic"}
    finally:
        os.close(source_fd)
        os.close(destination_fd)


def test_copy_xattrs_records_persisting_destination_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "source"
    destination_path = tmp_path / "destination"
    source_path.write_bytes(b"source")
    destination_path.write_bytes(b"destination")
    source_fd = os.open(source_path, os.O_RDONLY)
    destination_fd = os.open(destination_path, os.O_RDONLY)
    attrs = {source_fd: {}, destination_fd: {"com.apple.provenance": b"automatic"}}
    monkeypatch.setattr(migration, "_native_xattrs_fd", lambda fd: sorted(attrs[fd]))
    monkeypatch.setattr(migration, "_get_xattr_fd", lambda fd, name: attrs[fd][name])
    monkeypatch.setattr(migration, "_remove_xattr_fd", lambda _fd, _name: None)
    try:
        added = migration._copy_xattrs_fd(source_fd, destination_fd, [], "capture.bin")
        assert added == [
            {
                "name": "com.apple.provenance",
                "size": len(b"automatic"),
                "sha256": hashlib.sha256(b"automatic").hexdigest(),
            }
        ]
    finally:
        os.close(source_fd)
        os.close(destination_fd)


def test_v3_verifier_accepts_only_manifested_destination_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / "capture.bin").write_bytes(b"payload")
    original_snapshot = migration._snapshot_full
    actual, metadata = original_snapshot(destination)
    source_records = [{**record, "xattrs": []} for record in actual]
    provenance = {
        "name": "com.apple.provenance",
        "size": 11,
        "sha256": hashlib.sha256(b"provenance").hexdigest(),
    }

    def snapshot_with_provenance(root: Path, **kwargs: object):
        records, root_metadata = original_snapshot(root, **kwargs)
        if root == destination:
            records[0]["xattrs"] = [provenance]
            root_metadata["xattrs"] = [provenance]
        return records, root_metadata

    monkeypatch.setattr(migration, "_snapshot_full", snapshot_with_provenance)
    with pytest.raises(MigrationError, match="tree_manifest_mismatch"):
        migration._verify_records(
            destination,
            source_records,
            root_mode=metadata["mode"],
            root_mtime_ns=metadata["mtime_ns"],
            root_xattrs=[],
        )
    additions = migration._validate_destination_xattr_additions(
        [{"path": "capture.bin", "xattrs": [provenance]}],
        source_records,
        label="destination_added_xattrs",
    )
    migration._verify_records(
        destination,
        source_records,
        root_mode=metadata["mode"],
        root_mtime_ns=metadata["mtime_ns"],
        root_xattrs=[],
        destination_added_xattrs=additions,
        destination_added_root_xattrs=[provenance],
    )

    altered_hash = {**provenance, "sha256": "0" * 64}
    altered_additions = migration._validate_destination_xattr_additions(
        [{"path": "capture.bin", "xattrs": [altered_hash]}],
        source_records,
        label="destination_added_xattrs",
    )
    with pytest.raises(MigrationError, match="tree_manifest_mismatch"):
        migration._verify_records(
            destination,
            source_records,
            root_mode=metadata["mode"],
            root_mtime_ns=metadata["mtime_ns"],
            root_xattrs=[],
            destination_added_xattrs=altered_additions,
            destination_added_root_xattrs=[provenance],
        )


def test_destination_provenance_exception_rejects_other_names_and_source_presence() -> None:
    source_records = [{"path": "capture.bin", "xattrs": []}]
    provenance = {"name": "com.apple.provenance", "size": 1, "sha256": "a" * 64}
    with pytest.raises(MigrationError, match="manifest_destination_added_xattrs_invalid"):
        migration._validate_destination_xattr_additions(
            [{"path": "capture.bin", "xattrs": [{**provenance, "name": "user.extra"}]}],
            source_records,
            label="destination_added_xattrs",
        )
    source_with_provenance = [{"path": "capture.bin", "xattrs": [provenance]}]
    with pytest.raises(MigrationError, match="source_contains_exception"):
        migration._validate_destination_xattr_additions(
            [{"path": "capture.bin", "xattrs": [provenance]}],
            source_with_provenance,
            label="destination_added_xattrs",
        )


def test_migration_guard_check_runs_cheap_each_time_and_full_by_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeGuard:
        identity_checks = 0
        full_checks = 0

        def revalidate_identity(self) -> None:
            self.identity_checks += 1

        def revalidate(self) -> None:
            self.full_checks += 1

    guard = FakeGuard()
    checks = _MigrationGuardCheck(guard)
    times = iter((10.0, 10.1, 12.0, 16.0, 16.1, 16.2))
    monkeypatch.setattr("tools.research_storage_migrate.time.monotonic", lambda: next(times))
    checks()
    checks()
    checks()
    checks.before_publish()
    assert guard.identity_checks == 4
    assert guard.full_checks == 3


def test_stage_rejects_source_change_after_initial_snapshot(tmp_path: Path) -> None:
    source, external, dest_parent, receipt = setup_tree(tmp_path)

    calls = 0

    def mutate(root: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            (root / "nested" / "receipt.bin").write_bytes(b"changed")

    with pytest.raises(MigrationError, match="source_(changed|hash_changed)_during"):
        migration.stage_migration(
            source,
            external,
            dest_parent / source.name,
            group=source.name,
            expected_source_root=source,
            closed_preflight=mutate,
            preflight_receipt_path=receipt,
            preflight_receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),
        )


def test_verifier_rejects_destination_corruption_and_mode_changes(tmp_path: Path) -> None:
    source, external, dest_parent, receipt = setup_tree(tmp_path)
    stage(source, external, dest_parent, receipt)
    destination = dest_parent / source.name
    manifest = destination.with_name(destination.name + ".manifest.json")
    target = destination / "nested" / "receipt.bin"
    target.write_bytes(b"corrupted")
    with pytest.raises(MigrationError, match="tree_manifest_mismatch"):
        migration.verify_migration(
            source,
            destination,
            manifest,
            expected_source_root=source,
            expected_destination_root=destination,
        )
    target.write_bytes((source / "nested" / "receipt.bin").read_bytes())
    target.chmod(0o600)
    with pytest.raises(MigrationError, match="tree_manifest_mismatch"):
        migration.verify_migration(
            source,
            destination,
            manifest,
            expected_source_root=source,
            expected_destination_root=destination,
        )


def test_stage_rejects_copy_corruption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, external, dest_parent, receipt = setup_tree(tmp_path)
    original = migration._copy_tree

    def corrupt_copy(
        *args: object, **kwargs: object
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        result = original(*args, **kwargs)
        external_fd = args[1]
        temp_name = args[2]
        temp_fd = os.open(
            temp_name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0), dir_fd=external_fd
        )
        try:
            nested_fd = os.open(
                "nested", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0), dir_fd=temp_fd
            )
            try:
                target_fd = os.open("receipt.bin", os.O_WRONLY | os.O_TRUNC, dir_fd=nested_fd)
                try:
                    os.write(target_fd, b"corrupted")
                finally:
                    os.close(target_fd)
            finally:
                os.close(nested_fd)
        finally:
            os.close(temp_fd)
        return result

    monkeypatch.setattr(migration, "_copy_tree", corrupt_copy)
    with pytest.raises(MigrationError, match="tree_manifest_mismatch"):
        stage(source, external, dest_parent, receipt)


@pytest.mark.parametrize("unsafe_name", ["f01-protected", "D43", "reserved-holdout"])
def test_protected_names_and_outside_destinations_are_rejected(
    tmp_path: Path, unsafe_name: str
) -> None:
    source = tmp_path / "work" / unsafe_name
    source.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    with pytest.raises(MigrationError, match="protected_source_path"):
        migration._validate_source(source, source, source.name)
    safe = tmp_path / "work" / "pair64"
    safe.mkdir()
    with pytest.raises(MigrationError, match="destination_outside_external_root"):
        migration._validate_destination(external, tmp_path / "outside")


def test_stage_rejects_symlink_escape_and_special_file(tmp_path: Path) -> None:
    source, external, dest_parent, receipt = setup_tree(tmp_path)
    (source / "escape").symlink_to("../../outside")
    with pytest.raises(MigrationError, match="symlink_target_escapes_root"):
        stage(source, external, dest_parent, receipt)
    (source / "escape").unlink()
    if hasattr(os, "mkfifo"):
        os.mkfifo(source / "fifo")
        with pytest.raises(MigrationError, match="unsupported_special_file"):
            stage(source, external, dest_parent, receipt)


def test_protected_name_tokens_do_not_match_opaque_file_names() -> None:
    assert migration._is_protected_name("capture-F01-closed")
    assert migration._is_protected_name("D43")
    assert not migration._is_protected_name("txf01a9d43b8")
    assert not migration._is_protected_name("population-qualification")
    assert not migration._is_protected_name("system-sparse-dev-cohort")


@pytest.mark.parametrize(
    ("entry_name", "kind"), [("000-F01-capture.bin", "file"), ("000-D43-link", "symlink")]
)
def test_protected_payload_names_are_rejected_before_content_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry_name: str,
    kind: str,
) -> None:
    source, external, dest_parent, receipt = setup_tree(tmp_path)
    entry = source / entry_name
    if kind == "file":
        entry.write_bytes(b"protected payload")
    else:
        entry.symlink_to("nested/receipt.bin")
    hash_calls = 0
    original_hash = migration._hash_fd

    def count_hash(fd: int) -> str:
        nonlocal hash_calls
        hash_calls += 1
        return original_hash(fd)

    monkeypatch.setattr(migration, "_hash_fd", count_hash)
    with pytest.raises(MigrationError, match="protected_or_writer_entry"):
        stage(source, external, dest_parent, receipt)
    assert hash_calls == 0


def test_snapshot_uses_bounded_descriptors_for_many_sibling_directories(
    tmp_path: Path,
) -> None:
    source = tmp_path / "work" / "many-captures"
    source.mkdir(parents=True)
    for index in range(300):
        (source / f"capture-{index:04d}").mkdir()

    soft_limit, hard_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
    restricted_limit = 64 if hard_limit == resource.RLIM_INFINITY else min(64, hard_limit)
    if restricted_limit <= 8:
        pytest.skip("descriptor limit is too low for the test runner")
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (restricted_limit, hard_limit))
        records, _metadata = migration._snapshot_full(source)
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft_limit, hard_limit))
    assert len(records) == 300
    assert all(record["type"] == "directory" for record in records)


def test_create_only_manifest_and_locator_collisions(tmp_path: Path) -> None:
    source, external, dest_parent, receipt = setup_tree(tmp_path)
    destination = dest_parent / source.name
    manifest = destination.with_name(destination.name + ".manifest.json")
    manifest.write_text("occupied")
    with pytest.raises(MigrationError, match="destination_or_manifest_already_exists"):
        stage(source, external, dest_parent, receipt)
    manifest.unlink()
    result = stage(source, external, dest_parent, receipt)
    verified = migration.verify_migration(
        source,
        destination,
        manifest,
        expected_source_root=source,
        expected_destination_root=destination,
    )
    replay_path = external / "archive" / "work" / "independent-replay.json"
    replay_path.write_text(json.dumps(verified, sort_keys=True) + "\n", encoding="utf-8")
    replay_path.chmod(0o444)
    replay_hash = hashlib.sha256(replay_path.read_bytes()).hexdigest()
    locator = migration.publish_locator(
        external,
        external / "archive" / "pair64-locator.json",
        group=source.name,
        old_root=source,
        new_root=destination,
        verification_receipt_path=replay_path,
        verification_receipt_sha256=replay_hash,
    )
    assert locator["manifest_sha256"] == result["manifest_sha256"]
    with pytest.raises(MigrationError):
        migration.publish_locator(
            external,
            external / "archive" / "pair64-locator.json",
            group=source.name,
            old_root=source,
            new_root=destination,
            verification_receipt_path=replay_path,
            verification_receipt_sha256=replay_hash,
        )


def test_publish_locator_replays_after_independent_receipt_and_rejects_corruption(
    tmp_path: Path,
) -> None:
    source, external, dest_parent, receipt = setup_tree(tmp_path)
    destination = dest_parent / source.name
    stage(source, external, dest_parent, receipt)
    manifest = destination.with_name(destination.name + ".manifest.json")
    verified = migration.verify_migration(
        source,
        destination,
        manifest,
        expected_source_root=source,
        expected_destination_root=destination,
    )
    replay_path = external / "archive" / "work" / "independent-replay.json"
    replay_path.write_text(json.dumps(verified, sort_keys=True) + "\n", encoding="utf-8")
    replay_path.chmod(0o444)
    replay_hash = hashlib.sha256(replay_path.read_bytes()).hexdigest()
    (destination / "nested" / "receipt.bin").write_bytes(b"corrupt after replay")

    with pytest.raises(MigrationError, match="tree_manifest_mismatch"):
        migration.publish_locator(
            external,
            external / "archive" / "pair64-locator.json",
            group=source.name,
            old_root=source,
            new_root=destination,
            verification_receipt_path=replay_path,
            verification_receipt_sha256=replay_hash,
        )


def test_manifest_preflight_attestation_is_checked_again(tmp_path: Path) -> None:
    source, external, dest_parent, receipt = setup_tree(tmp_path)
    stage(source, external, dest_parent, receipt)
    receipt.write_text("tampered")
    destination = dest_parent / source.name
    with pytest.raises(MigrationError, match="preflight_receipt_hash_mismatch"):
        migration.verify_migration(
            source,
            destination,
            destination.with_name(destination.name + ".manifest.json"),
            expected_source_root=source,
            expected_destination_root=destination,
        )


def test_cli_preflight_receipt_requires_versioned_source_binding(tmp_path: Path) -> None:
    receipt = tmp_path / "preflight.json"
    source = tmp_path / "work" / "pair64"
    valid = {
        "schema_version": 1,
        "group": source.name,
        "source_root": str(source),
        "closed": True,
        "no_active_writers": True,
        "no_protected_payloads": True,
        "closure_evidence": ["reviewed receipt reference"],
    }
    receipt.write_text(json.dumps(valid), encoding="utf-8")
    receipt.chmod(0o600)
    assert _read_preflight(receipt, source, source.name) == valid
    for field, value in (
        ("schema_version", 2),
        ("group", "other"),
        ("source_root", "/other"),
        ("no_protected_payloads", False),
    ):
        invalid = {**valid, field: value}
        receipt.write_text(json.dumps(invalid), encoding="utf-8")
        with pytest.raises(MigrationError):
            _read_preflight(receipt, source, source.name)
