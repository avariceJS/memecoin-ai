from __future__ import annotations

import hashlib
import json
import os
import resource
from pathlib import Path

import pytest

from memecoin_research_storage import migration
from memecoin_research_storage.migration import MigrationError
from tools.research_storage_migrate import _read_preflight


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

    def corrupt_copy(*args: object, **kwargs: object) -> None:
        original(*args, **kwargs)
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
