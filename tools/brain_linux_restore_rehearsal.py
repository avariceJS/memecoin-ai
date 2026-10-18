#!/usr/bin/env python3
"""Create and restore a small synthetic operator state into inert ext4 quarantine."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memecoin_operator import control, linux_release, linux_service  # noqa: E402
from memecoin_research_storage import (  # noqa: E402
    StorageConfig,
    StorageError,
    read_only_guard,
    writer_guard,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402

SCHEMA = "brain-linux-synthetic-operator-restore-v1"
MAX_CONFIG_BYTES = 64 * 1024
MAX_DATABASE_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_CONFIG = {
    "mode": "operator",
    "real_execution_enabled": False,
    "paid_budget_usd": 0,
    "expires_ns": 0,
}
EXPECTED_TABLES = {"meta", "revisions", "commands", "jobs", "events", "publications"}


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_json(payload: bytes, *, maximum: int, label: str) -> Any:
    if len(payload) > maximum:
        raise StorageError(f"restore_{label}_too_large")

    def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise StorageError("restore_duplicate_json_key")
            result[key] = value
        return result

    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=unique_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StorageError(f"restore_{label}_invalid_json") from exc


def _schema_fingerprint(db: sqlite3.Connection) -> str:
    rows = db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    ).fetchall()
    return _sha256(_json_bytes([list(row) for row in rows]))


def _expected_schema_fingerprint() -> str:
    db = sqlite3.connect(":memory:")
    try:
        control._schema(db)
        return _schema_fingerprint(db)
    finally:
        db.close()


def _verify_idle_database(payload: bytes, *, expected_schema: str) -> None:
    if not payload or len(payload) > MAX_DATABASE_BYTES:
        raise StorageError("restore_database_size_invalid")
    with tempfile.TemporaryDirectory(prefix="brain-linux-restore-check-") as temp:
        path = Path(temp) / "operator.sqlite"
        path.write_bytes(payload)
        path.chmod(0o600)
        uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
        try:
            db = sqlite3.connect(uri, uri=True, timeout=2)
            try:
                if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise StorageError("restore_sqlite_integrity_failed")
                if db.execute("PRAGMA foreign_key_check").fetchall():
                    raise StorageError("restore_sqlite_foreign_key_failed")
                tables = {
                    str(row[0])
                    for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    if not str(row[0]).startswith("sqlite_")
                }
                if tables != EXPECTED_TABLES:
                    raise StorageError("restore_sqlite_schema_tables_mismatch")
                if _schema_fingerprint(db) != expected_schema:
                    raise StorageError("restore_sqlite_schema_fingerprint_mismatch")
                revisions = db.execute(
                    "SELECT revision,mode,phase,collect_profile,selected_candidate_id,"
                    "active_exam_id,"
                    "resume_mode,auto_reason,cycle_json,reason,created_ns FROM revisions "
                    "ORDER BY revision"
                ).fetchall()
                if len(revisions) != 1:
                    raise StorageError("restore_state_revision_count_mismatch")
                revision = revisions[0]
                expected_cycle = {"candidate_id": None, "expect": "none", "snapshot_id": None}
                try:
                    cycle = json.loads(str(revision[8]))
                except json.JSONDecodeError as exc:
                    raise StorageError("restore_state_cycle_invalid") from exc
                if (
                    revision[0] != 1
                    or revision[1] != "idle"
                    or revision[2] != "idle"
                    or revision[3] != "balanced"
                    or revision[4] is not None
                    or revision[5] is not None
                    or revision[6] is not None
                    or revision[7] is not None
                    or cycle != expected_cycle
                    or revision[9] != "initialized"
                    or type(revision[10]) is not int
                    or revision[10] <= 0
                ):
                    raise StorageError("restore_state_not_inert_idle")
                if db.execute("SELECT count(*) FROM meta").fetchone()[0] != 0:
                    raise StorageError("restore_state_has_pending_metadata")
                for table in ("commands", "jobs", "publications"):
                    if db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] != 0:
                        raise StorageError("restore_state_has_activity")
                events = db.execute("SELECT kind,reason FROM events ORDER BY event_id").fetchall()
                if events != [("audit", "initialized")]:
                    raise StorageError("restore_state_event_history_mismatch")
            finally:
                db.close()
        except sqlite3.Error as exc:
            raise StorageError("restore_sqlite_read_failed") from exc


def _release_boundary(release_id: str, unit: str) -> dict[str, object]:
    verified = linux_release.verify(release_id)
    expected_unit = f"memecoin-brain-{release_id}.service"
    if unit != expected_unit:
        raise StorageError("restore_unit_release_mismatch")
    if verified["unit_state"] != ("loaded", "inactive", "disabled", 0):
        raise StorageError("restore_unit_not_inert")
    props = linux_release._unit_properties(unit)
    if props.get("User") != "brain" or props.get("PrivateNetwork") != "yes":
        raise StorageError("restore_unit_boundary_mismatch")
    return verified


def _current_source_commit() -> str:
    root = Path(__file__).resolve().parents[1]
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise StorageError("restore_source_identity_unavailable") from exc
    if dirty:
        raise StorageError("restore_source_checkout_dirty")
    return head


def make_fixture(release_id: str, output: Path) -> dict[str, object]:
    release = _release_boundary(release_id, f"memecoin-brain-{release_id}.service")
    source_commit = _current_source_commit()
    if release.get("source_commit") != source_commit:
        raise StorageError("restore_source_release_commit_mismatch")
    output = output.absolute()
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise StorageError("restore_fixture_output_collision")
    python = linux_release.RELEASE_BASE / release_id / "venv/bin/python"
    with tempfile.TemporaryDirectory(prefix="brain-linux-fixture-") as temp:
        scratch = Path(temp)
        root = scratch / "root"
        seed = scratch / "seed.json"
        seed.write_bytes(_json_bytes(EXPECTED_CONFIG))
        seed.chmod(0o600)
        try:
            subprocess.run(
                [
                    str(python),
                    "-I",
                    "-m",
                    "memecoin_operator.runtime",
                    "--root",
                    str(root),
                    "--init",
                    "--config",
                    str(seed),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
                env={
                    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                    "HOME": str(scratch),
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise StorageError("restore_fixture_runtime_init_failed") from exc
        source_config = (root / "config.json").read_bytes()
        config = _strict_json(source_config, maximum=MAX_CONFIG_BYTES, label="config")
        if config != EXPECTED_CONFIG:
            raise StorageError("restore_fixture_config_mismatch")
        source_db = root / "operator.sqlite"
        if any(
            (root / suffix).exists() for suffix in ("operator.sqlite-wal", "operator.sqlite-shm")
        ):
            raise StorageError("restore_fixture_sqlite_sidecar_present")
        standalone = scratch / "operator.sqlite"
        src = sqlite3.connect(f"{source_db.resolve().as_uri()}?mode=ro", uri=True, timeout=3)
        dst = sqlite3.connect(standalone)
        try:
            src.backup(dst)
            dst.commit()
            if dst.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() != "delete":
                raise StorageError("restore_fixture_sqlite_not_standalone")
        finally:
            dst.close()
            src.close()
        database = standalone.read_bytes()
        schema_fingerprint = _expected_schema_fingerprint()
        _verify_idle_database(database, expected_schema=schema_fingerprint)
        entries = {
            "config.json": source_config,
            "operator.sqlite": database,
        }
        if len(database) > MAX_DATABASE_BYTES:
            raise StorageError("restore_fixture_database_too_large")
        manifest: dict[str, object] = {
            "schema": SCHEMA,
            "source_release_id": release_id,
            "source_commit": source_commit,
            "sqlite_schema_sha256": schema_fingerprint,
            "files": {
                name: {"bytes": len(payload), "mode": "0600", "sha256": _sha256(payload)}
                for name, payload in sorted(entries.items())
            },
        }
        manifest_bytes = _json_bytes(manifest)
        _create_private_bundle(output, {**entries, "manifest.json": manifest_bytes})
        return {
            "manifest_sha256": _sha256(manifest_bytes),
            "source_release_id": release_id,
            "source_commit": source_commit,
            "member_names": sorted([*entries, "manifest.json"]),
            "database_bytes": len(database),
        }


def _create_private_bundle(output: Path, entries: dict[str, bytes]) -> None:
    output.mkdir(mode=0o700)
    output_fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name, payload in sorted(entries.items()):
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=output_fd,
            )
            try:
                with os.fdopen(fd, "wb", closefd=False) as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(fd)
            finally:
                os.close(fd)
        os.fsync(output_fd)
    except BaseException:
        # Preserve an incomplete fixture for diagnosis; callers must use a new output path.
        raise
    finally:
        os.close(output_fd)


def _read_bundle(bundle: Path, expected_manifest_sha256: str, release_id: str) -> dict[str, bytes]:
    if not SHA256_RE.fullmatch(expected_manifest_sha256):
        raise StorageError("restore_manifest_pin_invalid")
    bundle = bundle.absolute()
    cursor = Path(bundle.anchor)
    for component in bundle.parts[1:]:
        cursor = cursor / component
        if cursor.is_symlink():
            raise StorageError("restore_bundle_symlink_path")
    bundle_fd = os.open(bundle, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    metadata = os.fstat(bundle_fd)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.getuid()
    ):
        os.close(bundle_fd)
        raise StorageError("restore_bundle_directory_unsafe")
    payloads: dict[str, bytes] = {}
    caps = {
        "manifest.json": MAX_MANIFEST_BYTES,
        "config.json": MAX_CONFIG_BYTES,
        "operator.sqlite": MAX_DATABASE_BYTES,
    }
    try:
        if sorted(os.listdir(bundle_fd)) != [
            "config.json",
            "manifest.json",
            "operator.sqlite",
        ]:
            raise StorageError("restore_bundle_member_set_mismatch")
        for name, maximum in caps.items():
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=bundle_fd)
            except OSError as exc:
                raise StorageError("restore_bundle_member_unsafe") from exc
            try:
                before = os.fstat(fd)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or stat.S_IMODE(before.st_mode) != 0o600
                    or before.st_uid != os.getuid()
                    or before.st_nlink != 1
                    or before.st_size > maximum
                ):
                    raise StorageError("restore_bundle_member_unsafe")
                chunks: list[bytes] = []
                total = 0
                while chunk := os.read(fd, min(1024 * 1024, maximum + 1 - total)):
                    total += len(chunk)
                    if total > maximum:
                        raise StorageError("restore_bundle_member_unsafe")
                    chunks.append(chunk)
                after = os.fstat(fd)
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    raise StorageError("restore_bundle_member_changed_during_read")
                payloads[name] = b"".join(chunks)
            finally:
                os.close(fd)
    finally:
        os.close(bundle_fd)
    manifest_bytes = payloads["manifest.json"]
    if _sha256(manifest_bytes) != expected_manifest_sha256:
        raise StorageError("restore_manifest_pin_mismatch")
    manifest = _strict_json(manifest_bytes, maximum=MAX_MANIFEST_BYTES, label="manifest")
    if (
        not isinstance(manifest, dict)
        or set(manifest)
        != {"schema", "source_release_id", "source_commit", "sqlite_schema_sha256", "files"}
        or manifest.get("schema") != SCHEMA
        or manifest.get("source_release_id") != release_id
        or manifest.get("source_commit") != _current_source_commit()
        or manifest.get("sqlite_schema_sha256") != _expected_schema_fingerprint()
    ):
        raise StorageError("restore_manifest_schema_mismatch")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != {"config.json", "operator.sqlite"}:
        raise StorageError("restore_manifest_member_set_mismatch")
    for name, maximum in (
        ("config.json", MAX_CONFIG_BYTES),
        ("operator.sqlite", MAX_DATABASE_BYTES),
    ):
        record = files.get(name)
        payload = payloads[name]
        if (
            not isinstance(record, dict)
            or set(record) != {"bytes", "mode", "sha256"}
            or record.get("bytes") != len(payload)
            or record.get("mode") != "0600"
            or record.get("sha256") != _sha256(payload)
            or len(payload) > maximum
        ):
            raise StorageError("restore_manifest_member_mismatch")
    config = _strict_json(payloads["config.json"], maximum=MAX_CONFIG_BYTES, label="config")
    if config != EXPECTED_CONFIG:
        raise StorageError("restore_config_not_inert")
    _verify_idle_database(
        payloads["operator.sqlite"], expected_schema=str(manifest["sqlite_schema_sha256"])
    )
    return payloads


def _target_names(parent: str, run_id: str) -> tuple[str, str]:
    if not RUN_ID_RE.fullmatch(run_id):
        raise StorageError("restore_run_id_invalid")
    if not parent or parent.startswith("/") or "\\" in parent or "\x00" in parent:
        raise StorageError("restore_target_parent_invalid")
    parts = parent.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise StorageError("restore_target_parent_invalid")
    return f"{parent}/.pending-{run_id}", f"{parent}/{run_id}"


def _verify_unit_excludes_target(unit: str, target_path: Path) -> None:
    state = linux_service._unit_status(unit)
    if state != ("loaded", "inactive", "disabled", 0):
        raise StorageError("restore_unit_not_inert")
    props = linux_release._unit_properties(unit)
    for key in ("ExecStart", "ReadWritePaths"):
        if str(target_path) in props.get(key, ""):
            raise StorageError("restore_target_referenced_by_unit")


def _rename_directory_noreplace(parent_fd: int, source: str, destination: str) -> None:
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise StorageError("restore_noreplace_rename_unavailable")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(parent_fd, os.fsencode(source), parent_fd, os.fsencode(destination), 1)
    if result != 0:
        error = ctypes.get_errno()
        if error == 17:
            raise StorageError("restore_final_collision")
        raise StorageError(f"restore_directory_publish_failed_errno_{error}")


def _read_target_payloads(
    reader: DescriptorWriter,
    target: str,
    payloads: dict[str, bytes],
    manifest_sha256: str,
    *,
    include_completion: bool,
) -> None:
    expected_names = {"config.json", "manifest.json", "operator.sqlite"}
    if include_completion:
        expected_names.add("COMPLETE")
    if set(reader.list_names(target)) != expected_names:
        raise StorageError("restore_target_member_set_mismatch")
    manifest = reader.read_bytes(f"{target}/manifest.json", max_bytes=MAX_MANIFEST_BYTES)
    config = reader.read_bytes(f"{target}/config.json", max_bytes=MAX_CONFIG_BYTES)
    database = reader.read_bytes(f"{target}/operator.sqlite", max_bytes=MAX_DATABASE_BYTES)
    if _sha256(manifest) != manifest_sha256 or manifest != payloads["manifest.json"]:
        raise StorageError("restore_target_manifest_mismatch")
    if config != payloads["config.json"] or database != payloads["operator.sqlite"]:
        raise StorageError("restore_target_payload_mismatch")
    _verify_idle_database(database, expected_schema=_expected_schema_fingerprint())


def _read_completion_witness(
    reader: DescriptorWriter,
    target: str,
    manifest_sha256: str,
    release_id: str,
    run_id: str,
) -> None:
    witness = reader.read_bytes(f"{target}/COMPLETE", max_bytes=4096)
    expected_witness = _json_bytes(
        {
            "schema": SCHEMA,
            "run_id": run_id,
            "source_release_id": release_id,
            "manifest_sha256": manifest_sha256,
        }
    )
    if witness != expected_witness:
        raise StorageError("restore_completion_witness_mismatch")


def _read_target(
    reader: DescriptorWriter,
    target: str,
    payloads: dict[str, bytes],
    manifest_sha256: str,
    release_id: str,
    run_id: str,
) -> None:
    _read_target_payloads(reader, target, payloads, manifest_sha256, include_completion=True)
    _read_completion_witness(reader, target, manifest_sha256, release_id, run_id)


def verify_completed(
    release_id: str,
    unit: str,
    storage_config: Path,
    target_parent: str,
    run_id: str,
    bundle: Path,
    expected_manifest_sha256: str,
) -> dict[str, object]:
    _release_boundary(release_id, unit)
    payloads = _read_bundle(bundle, expected_manifest_sha256, release_id)
    config = StorageConfig.load(storage_config)
    if config.platform != "linux" or config.filesystem != "ext4":
        raise StorageError("restore_storage_not_linux_ext4")
    _, final = _target_names(target_parent, run_id)
    root_path = config.mount_path.joinpath(*config.data_root.split("/"))
    target_path = root_path.joinpath(*final.split("/"))
    _verify_unit_excludes_target(unit, target_path)
    with read_only_guard(storage_config) as guard:
        reader = DescriptorWriter.read_only_from_storage_guard(guard)
        try:
            guard.revalidate()
            _read_target(reader, final, payloads, expected_manifest_sha256, release_id, run_id)
            guard.revalidate()
        finally:
            reader.close()
    _verify_unit_excludes_target(unit, target_path)
    return {
        "state": "inert_quarantine_verified",
        "run_id": run_id,
        "manifest_sha256": expected_manifest_sha256,
    }


def restore_inert(
    release_id: str,
    unit: str,
    storage_config: Path,
    target_parent: str,
    run_id: str,
    bundle: Path,
    expected_manifest_sha256: str,
    after_member: Any | None = None,
) -> dict[str, object]:
    _release_boundary(release_id, unit)
    payloads = _read_bundle(bundle, expected_manifest_sha256, release_id)
    config = StorageConfig.load(storage_config)
    if config.platform != "linux" or config.filesystem != "ext4":
        raise StorageError("restore_storage_not_linux_ext4")
    pending, final = _target_names(target_parent, run_id)
    root_path = config.mount_path.joinpath(*config.data_root.split("/"))
    target_path = root_path.joinpath(*final.split("/"))
    _verify_unit_excludes_target(unit, target_path)
    try:
        with read_only_guard(storage_config) as guard:
            reader = DescriptorWriter.read_only_from_storage_guard(guard)
            try:
                existing_fd = reader.open_directory(final)
            except FileNotFoundError:
                pass
            else:
                os.close(existing_fd)
                try:
                    guard.revalidate()
                    _read_target(
                        reader,
                        final,
                        payloads,
                        expected_manifest_sha256,
                        release_id,
                        run_id,
                    )
                    guard.revalidate()
                    return {
                        "state": "inert_quarantine_verified",
                        "run_id": run_id,
                        "manifest_sha256": expected_manifest_sha256,
                        "source_release_id": release_id,
                        "target_relative": final,
                        "unit_unchanged": True,
                        "already_complete": True,
                    }
                finally:
                    reader.close()
            reader.close()
    except StorageError:
        raise
    with writer_guard(storage_config) as guard:
        writer = DescriptorWriter.from_storage_guard(guard)
        try:
            parent_path = "/".join(target_parent.split("/"))
            writer.mkdir(parent_path)
            try:
                writer.open_directory(final)
            except FileNotFoundError:
                pass
            else:
                raise StorageError("restore_final_collision")
            try:
                writer.open_directory(pending)
            except FileNotFoundError:
                pass
            else:
                raise StorageError("restore_pending_collision")
            writer.mkdir(pending)
            manifest = payloads["manifest.json"]
            writer.atomic_write(f"{pending}/config.json", payloads["config.json"], create_only=True)
            if after_member is not None:
                after_member("config.json")
            writer.atomic_write(
                f"{pending}/operator.sqlite", payloads["operator.sqlite"], create_only=True
            )
            if after_member is not None:
                after_member("operator.sqlite")
            writer.atomic_write(f"{pending}/manifest.json", manifest, create_only=True)
            if after_member is not None:
                after_member("manifest.json")
            if set(writer.list_names(pending)) != {
                "config.json",
                "manifest.json",
                "operator.sqlite",
            }:
                raise StorageError("restore_pending_member_set_mismatch")
            if (
                writer.read_bytes(f"{pending}/manifest.json", max_bytes=MAX_MANIFEST_BYTES)
                != manifest
            ):
                raise StorageError("restore_pending_manifest_mismatch")
            if (
                writer.read_bytes(f"{pending}/config.json", max_bytes=MAX_CONFIG_BYTES)
                != payloads["config.json"]
            ):
                raise StorageError("restore_pending_config_mismatch")
            restored_db = writer.read_bytes(
                f"{pending}/operator.sqlite", max_bytes=MAX_DATABASE_BYTES
            )
            if restored_db != payloads["operator.sqlite"]:
                raise StorageError("restore_pending_database_mismatch")
            _verify_idle_database(restored_db, expected_schema=_expected_schema_fingerprint())
            writer.assert_storage_binding(guard)
            parent_fd = writer.open_directory(parent_path)
            try:
                writer._prewrite("restore_publish")
                _rename_directory_noreplace(parent_fd, f".pending-{run_id}", run_id)
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
            writer.assert_storage_binding(guard)
            final_payloads = {**payloads, "manifest.json": manifest}
            _read_target_payloads(
                writer,
                final,
                final_payloads,
                expected_manifest_sha256,
                include_completion=False,
            )
            writer.assert_storage_binding(guard)
            completion_witness = _json_bytes(
                {
                    "schema": SCHEMA,
                    "run_id": run_id,
                    "source_release_id": release_id,
                    "manifest_sha256": expected_manifest_sha256,
                }
            )
            writer.atomic_write(f"{final}/COMPLETE", completion_witness, create_only=True)
            if writer.read_bytes(f"{final}/COMPLETE", max_bytes=4096) != completion_witness:
                raise StorageError("restore_completion_witness_mismatch")
            writer.assert_storage_binding(guard)
        finally:
            writer.close()
    _verify_unit_excludes_target(unit, target_path)
    return {
        "state": "inert_quarantine_verified",
        "run_id": run_id,
        "manifest_sha256": expected_manifest_sha256,
        "source_release_id": release_id,
        "target_relative": final,
        "unit_unchanged": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fixture = sub.add_parser("make-fixture")
    fixture.add_argument("--release-id", required=True)
    fixture.add_argument("--output", type=Path, required=True)
    restore = sub.add_parser("restore-inert")
    restore.add_argument("--release-id", required=True)
    restore.add_argument("--unit", required=True)
    restore.add_argument("--storage-config", type=Path, required=True)
    restore.add_argument("--target-parent", required=True)
    restore.add_argument("--run-id", required=True)
    restore.add_argument("--bundle", type=Path, required=True)
    restore.add_argument("--manifest-sha256", required=True)
    verify = sub.add_parser("verify-inert")
    verify.add_argument("--release-id", required=True)
    verify.add_argument("--unit", required=True)
    verify.add_argument("--storage-config", type=Path, required=True)
    verify.add_argument("--target-parent", required=True)
    verify.add_argument("--run-id", required=True)
    verify.add_argument("--bundle", type=Path, required=True)
    verify.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "make-fixture":
            result = make_fixture(args.release_id, args.output)
        elif args.command == "restore-inert":
            result = restore_inert(
                args.release_id,
                args.unit,
                args.storage_config,
                args.target_parent,
                args.run_id,
                args.bundle,
                args.manifest_sha256,
            )
        else:
            result = verify_completed(
                args.release_id,
                args.unit,
                args.storage_config,
                args.target_parent,
                args.run_id,
                args.bundle,
                args.manifest_sha256,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except StorageError as exc:
        print(f"restore rehearsal failed: {exc}", file=sys.stderr)
        return 2
    except OSError:
        print("restore rehearsal failed: filesystem_operation_failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
