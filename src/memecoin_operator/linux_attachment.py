"""Stage a verified synthetic restore as a separately activated Linux attachment."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import pwd
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from memecoin_operator import control, linux_release, linux_service
from memecoin_research_storage import StorageConfig, StorageError, read_only_guard

LAB_BASE = Path("/var/lib/brain-lab")
ATTACHMENT_BASE = LAB_BASE / "attachments"
PIN_BASE = Path("/var/lib/brain-linux-attachment-pins")
ACTIVATION_BASE = Path("/var/lib/brain-linux-attachment-activations")
UNIT_BASE = Path("/etc/systemd/system")
ATTACHMENT_SCHEMA = "brain-linux-synthetic-attachment-v1"
RESTORE_SCHEMA = "brain-linux-synthetic-operator-restore-v1"
ACTIVATION_SCHEMA = "brain-linux-synthetic-attachment-activation-v1"
MAX_CONFIG_BYTES = 64 * 1024
MAX_DATABASE_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_WITNESS_BYTES = 4096
RUNTIME_DIRECTORIES = {
    "background",
    "background/cancel",
    "background/history",
    "background/locks",
    "background/logs",
    "background/receipts",
    "background/specs",
    "operator",
    "operator/logs",
}
RUNTIME_FILES = {
    "config.json",
    "operator.sqlite",
    "operator/owner.lock",
    "operator/logs/operator.log",
}
ATTACHMENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
EXPECTED_CONFIG = {
    "mode": "operator",
    "maintenance_policy": "linux-synthetic-idle",
    "real_execution_enabled": False,
    "paid_budget_usd": 0,
    "expires_ns": 0,
}


class _ReadOnlyRootReader:
    """Small no-follow reader anchored to a held read-only storage guard."""

    def __init__(self, guard: Any) -> None:
        if not getattr(guard, "read_only", False):
            raise StorageError("attachment_reader_requires_read_only_guard")
        self.root_fd = os.dup(guard.root_fd)

    @staticmethod
    def _parts(relative: str) -> list[str]:
        if (
            not isinstance(relative, str)
            or not relative
            or relative.startswith("/")
            or "\\" in relative
        ):
            raise StorageError("attachment_source_path_invalid")
        parts = relative.split("/")
        if any(part in {"", ".", ".."} or "\x00" in part for part in parts):
            raise StorageError("attachment_source_path_invalid")
        return parts

    def _open_directory(self, parts: list[str]) -> int:
        current = os.dup(self.root_fd)
        try:
            for part in parts:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current,
                )
                os.close(current)
                current = next_fd
            return current
        except BaseException:
            os.close(current)
            raise

    def list_names(self, relative: str) -> list[str]:
        fd = self._open_directory(self._parts(relative))
        try:
            return sorted(os.listdir(fd))
        finally:
            os.close(fd)

    def read_bytes(self, relative: str, *, max_bytes: int) -> bytes:
        parts = self._parts(relative)
        parent_fd = self._open_directory(parts[:-1])
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
            try:
                before = os.fstat(fd)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_nlink != 1
                    or before.st_size > max_bytes
                ):
                    raise StorageError("attachment_source_file_unsafe")
                chunks: list[bytes] = []
                total = 0
                while chunk := os.read(fd, min(1024 * 1024, max_bytes + 1 - total)):
                    total += len(chunk)
                    if total > max_bytes:
                        raise StorageError("attachment_source_file_oversized")
                    chunks.append(chunk)
                after = os.fstat(fd)
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    raise StorageError("attachment_source_file_changed")
                return b"".join(chunks)
            finally:
                os.close(fd)
        finally:
            os.close(parent_fd)

    def close(self) -> None:
        if self.root_fd >= 0:
            os.close(self.root_fd)
            self.root_fd = -1


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def _strict_json(payload: bytes, maximum: int, label: str) -> Any:
    if len(payload) > maximum:
        raise StorageError(f"attachment_{label}_too_large")

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise StorageError("attachment_duplicate_json_key")
            result[key] = value
        return result

    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StorageError(f"attachment_{label}_invalid_json") from exc


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rename_noreplace(parent: Path, source_name: str, destination_name: str) -> None:
    """Publish two direct children without replacing a concurrently created name."""
    if any(
        name in {"", ".", ".."} or "/" in name or "\\" in name
        for name in (source_name, destination_name)
    ):
        raise StorageError("attachment_publish_name_invalid")
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise StorageError("attachment_noreplace_unsupported")
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        if (
            renameat2(
                parent_fd, os.fsencode(source_name), parent_fd, os.fsencode(destination_name), 1
            )
            != 0
        ):
            error = ctypes.get_errno()
            if error in {17, 18}:
                raise StorageError("attachment_id_already_exists")
            raise StorageError("attachment_publish_failed") from OSError(error, os.strerror(error))
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _schema_fingerprint() -> str:
    db = sqlite3.connect(":memory:")
    try:
        control._schema(db)
        rows = db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
        return _sha256(_json_bytes([list(row) for row in rows]))
    finally:
        db.close()


def _verify_idle_database(
    payload: bytes, expected_schema: str, *, allow_startup_maintenance: bool = False
) -> None:
    if not payload or len(payload) > MAX_DATABASE_BYTES:
        raise StorageError("attachment_database_size_invalid")
    import tempfile

    with tempfile.TemporaryDirectory(prefix="brain-linux-attachment-check-") as temp:
        path = Path(temp) / "operator.sqlite"
        path.write_bytes(payload)
        path.chmod(0o600)
        try:
            db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
            try:
                if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise StorageError("attachment_sqlite_integrity_failed")
                if db.execute("PRAGMA foreign_key_check").fetchall():
                    raise StorageError("attachment_sqlite_foreign_key_failed")
                tables = {
                    str(row[0])
                    for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    if not str(row[0]).startswith("sqlite_")
                }
                if tables != {"meta", "revisions", "commands", "jobs", "events", "publications"}:
                    raise StorageError("attachment_sqlite_schema_tables_mismatch")
                rows = db.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master "
                    "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
                ).fetchall()
                if _sha256(_json_bytes([list(row) for row in rows])) != expected_schema:
                    raise StorageError("attachment_sqlite_schema_mismatch")
                revisions = db.execute(
                    "SELECT revision,mode,phase,collect_profile,selected_candidate_id,"
                    "active_exam_id,resume_mode,auto_reason,cycle_json,reason,created_ns "
                    "FROM revisions ORDER BY revision"
                ).fetchall()
                if len(revisions) != 1:
                    raise StorageError("attachment_revision_count_mismatch")
                row = revisions[0]
                try:
                    cycle = json.loads(row[8])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise StorageError("attachment_cycle_invalid") from exc
                if (
                    row[0] != 1
                    or row[1] != "idle"
                    or row[2] != "idle"
                    or row[3] != "balanced"
                    or any(value is not None for value in row[4:8])
                    or cycle != {"candidate_id": None, "expect": "none", "snapshot_id": None}
                    or row[9] != "initialized"
                    or type(row[10]) is not int
                    or row[10] <= 0
                ):
                    raise StorageError("attachment_state_not_inert_idle")
                metadata = db.execute("SELECT key,value FROM meta ORDER BY key").fetchall()
                if metadata:
                    if (
                        not allow_startup_maintenance
                        or len(metadata) != 1
                        or metadata[0][0] != "last_maintain_ns"
                        or not str(metadata[0][1]).isdecimal()
                        or int(metadata[0][1]) <= 0
                    ):
                        raise StorageError("attachment_state_has_pending_metadata")
                for table in ("commands", "jobs", "publications"):
                    if db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] != 0:
                        raise StorageError("attachment_state_has_activity")
                events = db.execute(
                    "SELECT kind,reason,payload_json,created_ns FROM events ORDER BY event_id"
                ).fetchall()
                allowed_events = [("audit", "initialized", '{"mode": "idle", "phase": "idle"}')]
                if allow_startup_maintenance and len(events) == 2:
                    allowed_events.append(("incident", "adapter_error", "{}"))
                if len(events) != len(allowed_events) or any(
                    tuple(row[:3]) != expected or type(row[3]) is not int or row[3] <= 0
                    for row, expected in zip(events, allowed_events, strict=True)
                ):
                    raise StorageError("attachment_event_history_mismatch")
            finally:
                db.close()
        except sqlite3.Error as exc:
            raise StorageError("attachment_sqlite_read_failed") from exc


def _validate_attachment_id(value: str) -> str:
    if not ATTACHMENT_ID_RE.fullmatch(value):
        raise StorageError("attachment_id_invalid")
    return value


def _unit_identity(manifest: dict[str, Any]) -> str:
    binding = {
        "attachment_id": manifest["attachment_id"],
        "release_id": manifest["release_id"],
        "restore_manifest_sha256": manifest["restore_manifest_sha256"],
    }
    return _sha256(_json_bytes(binding))


def _unit_name(manifest: dict[str, Any]) -> str:
    return f"memecoin-brain-{_unit_identity(manifest)}.service"


def _port(manifest: dict[str, Any]) -> int:
    return 20000 + (int(_unit_identity(manifest)[:8], 16) % 40000)


def _check_private_directory(path: Path, uid: int, gid: int) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise StorageError("attachment_directory_missing") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != uid
        or metadata.st_gid != gid
    ):
        raise StorageError("attachment_directory_unsafe")


def _create_file(dir_fd: int, name: str, payload: bytes) -> None:
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
    try:
        os.fchmod(fd, 0o600)
        offset = 0
        while offset < len(payload):
            offset += os.write(fd, payload[offset:])
        os.fsync(fd)
        metadata = os.fstat(fd)
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise StorageError("attachment_file_permissions_unsafe")
    except BaseException:
        os.close(fd)
        try:
            os.unlink(name, dir_fd=dir_fd)
        except OSError:
            pass
        raise
    else:
        os.close(fd)


def _read_file(path: Path, *, maximum: int, uid: int, mode: int) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise StorageError("attachment_file_unavailable") from exc
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != uid
            or stat.S_IMODE(before.st_mode) != mode
            or before.st_nlink != 1
            or before.st_size > maximum
        ):
            raise StorageError("attachment_file_unsafe")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(fd, min(1024 * 1024, maximum + 1 - total)):
            total += len(chunk)
            if total > maximum:
                raise StorageError("attachment_file_oversized")
            chunks.append(chunk)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise StorageError("attachment_file_changed_during_read")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _read_source(
    release_id: str,
    release_manifest_sha256: str,
    storage_config: Path,
    fs_uuid: str,
    root_id: str,
    target_parent: str,
    run_id: str,
    restore_manifest_sha256: str,
) -> tuple[dict[str, bytes], dict[str, Any], StorageConfig]:
    release = linux_release.verify(release_id)
    if release.get("manifest_sha256") != release_manifest_sha256:
        raise StorageError("attachment_release_manifest_pin_mismatch")
    config = StorageConfig.load(storage_config)
    if (
        config.platform != "linux"
        or config.filesystem != "ext4"
        or config.volume_uuid != fs_uuid.lower()
        or config.root_id != root_id
    ):
        raise StorageError("attachment_storage_identity_mismatch")
    if (
        not run_id
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", run_id)
        or not target_parent
        or target_parent.startswith("/")
        or any(part in {"", ".", ".."} for part in target_parent.split("/"))
    ):
        raise StorageError("attachment_source_target_invalid")
    final = f"{target_parent}/{run_id}"
    with read_only_guard(storage_config) as guard:
        guard.revalidate()
        reader = _ReadOnlyRootReader(guard)
        try:
            if set(reader.list_names(final)) != {
                "COMPLETE",
                "config.json",
                "manifest.json",
                "operator.sqlite",
            }:
                raise StorageError("attachment_source_member_set_mismatch")
            payloads = {
                "config.json": reader.read_bytes(
                    f"{final}/config.json", max_bytes=MAX_CONFIG_BYTES
                ),
                "operator.sqlite": reader.read_bytes(
                    f"{final}/operator.sqlite", max_bytes=MAX_DATABASE_BYTES
                ),
                "manifest.json": reader.read_bytes(
                    f"{final}/manifest.json", max_bytes=MAX_MANIFEST_BYTES
                ),
                "COMPLETE": reader.read_bytes(f"{final}/COMPLETE", max_bytes=MAX_WITNESS_BYTES),
            }
            guard.revalidate()
        finally:
            reader.close()
        guard.revalidate()
    if _sha256(payloads["manifest.json"]) != restore_manifest_sha256:
        raise StorageError("attachment_restore_manifest_pin_mismatch")
    manifest = _strict_json(payloads["manifest.json"], MAX_MANIFEST_BYTES, "restore_manifest")
    expected_schema_db = _schema_fingerprint()
    if (
        not isinstance(manifest, dict)
        or set(manifest)
        != {"schema", "source_release_id", "source_commit", "sqlite_schema_sha256", "files"}
        or manifest.get("schema") != RESTORE_SCHEMA
        or manifest.get("source_release_id") != release_id
        or manifest.get("source_commit") != release.get("source_commit")
        or manifest.get("sqlite_schema_sha256") != expected_schema_db
    ):
        raise StorageError("attachment_restore_manifest_mismatch")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != {"config.json", "operator.sqlite"}:
        raise StorageError("attachment_restore_member_set_mismatch")
    for name, maximum in (
        ("config.json", MAX_CONFIG_BYTES),
        ("operator.sqlite", MAX_DATABASE_BYTES),
    ):
        item = files.get(name)
        data = payloads[name]
        if (
            not isinstance(item, dict)
            or set(item) != {"bytes", "mode", "sha256"}
            or item.get("bytes") != len(data)
            or item.get("mode") != "0600"
            or item.get("sha256") != _sha256(data)
            or len(data) > maximum
        ):
            raise StorageError("attachment_restore_payload_mismatch")
    if _strict_json(payloads["config.json"], MAX_CONFIG_BYTES, "config") != EXPECTED_CONFIG:
        raise StorageError("attachment_config_not_inert")
    _verify_idle_database(payloads["operator.sqlite"], expected_schema_db)
    expected_witness = _json_bytes(
        {
            "schema": RESTORE_SCHEMA,
            "run_id": run_id,
            "source_release_id": release_id,
            "manifest_sha256": restore_manifest_sha256,
        }
    )
    if payloads["COMPLETE"] != expected_witness:
        raise StorageError("attachment_completion_witness_mismatch")
    return payloads, release, config


def prepare_attachment(
    release_id: str,
    release_manifest_sha256: str,
    storage_config: Path,
    fs_uuid: str,
    root_id: str,
    target_parent: str,
    run_id: str,
    restore_manifest_sha256: str,
    attachment_id: str,
) -> dict[str, object]:
    if os.geteuid() != pwd.getpwnam("brain").pw_uid:
        raise StorageError("attachment_prepare_requires_brain_user")
    attachment_id = _validate_attachment_id(attachment_id)
    payloads, release, config = _read_source(
        release_id,
        release_manifest_sha256,
        storage_config,
        fs_uuid,
        root_id,
        target_parent,
        run_id,
        restore_manifest_sha256,
    )
    brain = pwd.getpwnam("brain")
    _check_private_directory(LAB_BASE, brain.pw_uid, brain.pw_gid)
    if ATTACHMENT_BASE.is_symlink():
        raise StorageError("attachment_base_symlink")
    ATTACHMENT_BASE.mkdir(mode=0o700, exist_ok=True)
    _check_private_directory(ATTACHMENT_BASE, brain.pw_uid, brain.pw_gid)
    attachment_dir = ATTACHMENT_BASE / attachment_id
    if os.path.lexists(attachment_dir):
        raise StorageError("attachment_id_already_exists")
    staging_dir = Path(tempfile.mkdtemp(prefix=f".pending-{attachment_id}-", dir=ATTACHMENT_BASE))
    staging_dir.chmod(0o700)
    attachment_dir_published = False
    attachment_dir = staging_dir
    operator_dir = attachment_dir / "operator"
    operator_dir.mkdir(mode=0o700)
    _check_private_directory(attachment_dir, brain.pw_uid, brain.pw_gid)
    _check_private_directory(operator_dir, brain.pw_uid, brain.pw_gid)
    attachment_manifest = {
        "schema": ATTACHMENT_SCHEMA,
        "attachment_id": attachment_id,
        "release_id": release_id,
        "release_manifest_sha256": release_manifest_sha256,
        "restore_manifest_sha256": restore_manifest_sha256,
        "source_fs_uuid": config.volume_uuid,
        "source_root_id": config.root_id,
        "source_run_id": run_id,
        "source_target": f"{target_parent}/{run_id}",
        "unit_name": "",
        "port": 0,
        "files": {
            name: {"bytes": len(payloads[name]), "mode": "0600", "sha256": _sha256(payloads[name])}
            for name in ("config.json", "operator.sqlite")
        },
    }
    attachment_manifest["unit_name"] = _unit_name(attachment_manifest)
    attachment_manifest["port"] = _port(attachment_manifest)
    manifest_bytes = _json_bytes(attachment_manifest)
    attachment_fd = os.open(attachment_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    operator_fd = os.open(operator_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _create_file(operator_fd, "config.json", payloads["config.json"])
        _create_file(operator_fd, "operator.sqlite", payloads["operator.sqlite"])
        _create_file(attachment_fd, "attachment.json", manifest_bytes)
        os.fsync(operator_fd)
        os.fsync(attachment_fd)
        base_fd = os.open(ATTACHMENT_BASE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(base_fd)
        finally:
            os.close(base_fd)
    finally:
        os.close(operator_fd)
        os.close(attachment_fd)
    _verify_local_payloads(attachment_manifest, owner_uid=brain.pw_uid, operator_dir=operator_dir)
    after_copy, _release_again, _config_again = _read_source(
        release_id,
        release_manifest_sha256,
        storage_config,
        fs_uuid,
        root_id,
        target_parent,
        run_id,
        restore_manifest_sha256,
    )
    if after_copy != payloads:
        shutil.rmtree(staging_dir)
        raise StorageError("attachment_source_changed_after_copy")
    final_attachment_dir = ATTACHMENT_BASE / attachment_id
    try:
        _rename_noreplace(
            ATTACHMENT_BASE,
            staging_dir.name,
            final_attachment_dir.name,
        )
        attachment_dir_published = True
    except OSError as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise StorageError("attachment_publish_failed") from exc
    attachment_dir = final_attachment_dir
    operator_dir = attachment_dir / "operator"
    release_python = linux_release.RELEASE_BASE / release_id / "venv/bin/python"
    args = [
        "stage-unit",
        "--attachment-id",
        attachment_id,
        "--release-id",
        release_id,
        "--release-manifest-sha256",
        release_manifest_sha256,
        "--restore-manifest-sha256",
        restore_manifest_sha256,
    ]
    try:
        result = subprocess.run(
            [
                "sudo",
                "-n",
                str(release_python),
                "-B",
                "-I",
                "-m",
                "memecoin_operator.linux_attachment",
                *args,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            match = re.search(r"linux_attachment_error: ([a-z0-9_:-]+)", result.stderr)
            detail = match.group(1) if match else "unknown"
            raise StorageError(f"attachment_unit_stage_failed:{detail}")
    except BaseException:
        if attachment_dir_published:
            shutil.rmtree(attachment_dir, ignore_errors=True)
        raise
    return {
        "state": "attached_inert",
        "attachment_id": attachment_id,
        "unit": attachment_manifest["unit_name"],
        "release_id": release_id,
        "restore_manifest_sha256": restore_manifest_sha256,
        "local_state_sha256": attachment_manifest["files"]["operator.sqlite"]["sha256"],
    }


def _attachment_paths(attachment_id: str) -> tuple[Path, Path, Path]:
    attachment_id = _validate_attachment_id(attachment_id)
    directory = ATTACHMENT_BASE / attachment_id
    return directory, directory / "operator", directory / "attachment.json"


def _read_attachment_manifest(attachment_id: str) -> tuple[dict[str, Any], bytes, Path, Path]:
    directory, operator_dir, manifest_path = _attachment_paths(attachment_id)
    brain = pwd.getpwnam("brain")
    _check_private_directory(LAB_BASE, brain.pw_uid, brain.pw_gid)
    _check_private_directory(ATTACHMENT_BASE, brain.pw_uid, brain.pw_gid)
    _check_private_directory(directory, brain.pw_uid, brain.pw_gid)
    _check_private_directory(operator_dir, brain.pw_uid, brain.pw_gid)
    raw = _read_file(manifest_path, maximum=MAX_MANIFEST_BYTES, uid=brain.pw_uid, mode=0o600)
    manifest = _strict_json(raw, MAX_MANIFEST_BYTES, "attachment_manifest")
    if (
        not isinstance(manifest, dict)
        or set(manifest)
        != {
            "schema",
            "attachment_id",
            "release_id",
            "release_manifest_sha256",
            "restore_manifest_sha256",
            "source_fs_uuid",
            "source_root_id",
            "source_run_id",
            "source_target",
            "unit_name",
            "port",
            "files",
        }
        or manifest.get("schema") != ATTACHMENT_SCHEMA
        or manifest.get("attachment_id") != attachment_id
        or manifest.get("unit_name") != _unit_name(manifest)
        or manifest.get("port") != _port(manifest)
    ):
        raise StorageError("attachment_manifest_mismatch")
    return manifest, raw, directory, operator_dir


def _verify_local_payloads(
    manifest: dict[str, Any], *, owner_uid: int, operator_dir: Path | None = None
) -> dict[str, bytes]:
    if operator_dir is None:
        attachment_id = str(manifest["attachment_id"])
        _, operator_dir, _ = _attachment_paths(attachment_id)
    if set(os.listdir(operator_dir)) != {"config.json", "operator.sqlite"}:
        raise StorageError("attachment_local_member_set_mismatch")
    payloads: dict[str, bytes] = {}
    for name, maximum in (
        ("config.json", MAX_CONFIG_BYTES),
        ("operator.sqlite", MAX_DATABASE_BYTES),
    ):
        data = _read_file(operator_dir / name, maximum=maximum, uid=owner_uid, mode=0o600)
        record = manifest["files"].get(name)
        if (
            not isinstance(record, dict)
            or set(record) != {"bytes", "mode", "sha256"}
            or record.get("bytes") != len(data)
            or record.get("mode") != "0600"
            or record.get("sha256") != _sha256(data)
        ):
            raise StorageError("attachment_local_payload_mismatch")
        payloads[name] = data
    if _strict_json(payloads["config.json"], MAX_CONFIG_BYTES, "local_config") != EXPECTED_CONFIG:
        raise StorageError("attachment_config_not_inert")
    _verify_idle_database(payloads["operator.sqlite"], _schema_fingerprint())
    return payloads


def _pin_path(attachment_id: str) -> Path:
    return PIN_BASE / f"{_validate_attachment_id(attachment_id)}.json"


def _write_root_pin(path: Path, payload: bytes) -> None:
    if PIN_BASE.is_symlink():
        raise StorageError("attachment_pin_directory_symlink")
    PIN_BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    pin_directory = PIN_BASE.lstat()
    if (
        not stat.S_ISDIR(pin_directory.st_mode)
        or pin_directory.st_uid != 0
        or stat.S_IMODE(pin_directory.st_mode) != 0o700
    ):
        raise StorageError("attachment_pin_directory_unsafe")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(fd, payload[offset:])
        os.fsync(fd)
        os.fchmod(fd, 0o444)
    finally:
        os.close(fd)


def _read_root_pin(path: Path) -> dict[str, Any]:
    try:
        pin_directory = PIN_BASE.lstat()
    except OSError as exc:
        raise StorageError("attachment_pin_directory_unavailable") from exc
    if (
        not stat.S_ISDIR(pin_directory.st_mode)
        or pin_directory.st_uid != 0
        or stat.S_IMODE(pin_directory.st_mode) != 0o700
    ):
        raise StorageError("attachment_pin_directory_unsafe")
    raw = _read_file(path, maximum=MAX_MANIFEST_BYTES, uid=0, mode=0o444)
    value = _strict_json(raw, MAX_MANIFEST_BYTES, "root_pin")
    if not isinstance(value, dict):
        raise StorageError("attachment_root_pin_invalid")
    return value


def _activation_path(attachment_id: str, name: str) -> Path:
    if name not in {"start-intent", "idle-probe", "activation-result"}:
        raise StorageError("attachment_activation_record_name_invalid")
    return ACTIVATION_BASE / f"{_validate_attachment_id(attachment_id)}.{name}.json"


def _ensure_activation_directory() -> None:
    if ACTIVATION_BASE.is_symlink():
        raise StorageError("attachment_activation_directory_symlink")
    ACTIVATION_BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = ACTIVATION_BASE.lstat()
    if (
        not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != 0
        or stat.S_IMODE(directory.st_mode) != 0o700
    ):
        raise StorageError("attachment_activation_directory_unsafe")


def _write_activation_record(attachment_id: str, name: str, value: dict[str, Any]) -> None:
    _ensure_activation_directory()
    payload = _json_bytes(value)
    path = _activation_path(attachment_id, name)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(fd, payload[offset:])
        os.fsync(fd)
        os.fchmod(fd, 0o444)
    finally:
        os.close(fd)
    directory_fd = os.open(ACTIVATION_BASE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _read_activation_record(
    manifest: dict[str, Any], pin: dict[str, Any], name: str
) -> dict[str, Any] | None:
    path = _activation_path(str(manifest["attachment_id"]), name)
    if not os.path.lexists(path):
        return None
    _ensure_activation_directory()
    record = _strict_json(
        _read_file(path, maximum=MAX_MANIFEST_BYTES, uid=0, mode=0o444),
        MAX_MANIFEST_BYTES,
        "activation_record",
    )
    if not isinstance(record, dict):
        raise StorageError("attachment_activation_record_invalid")
    common = {
        "schema": ACTIVATION_SCHEMA,
        "attachment_id": manifest["attachment_id"],
        "unit_name": manifest["unit_name"],
        "manifest_sha256": pin["manifest_sha256"],
        "pin_sha256": _sha256(_json_bytes(pin)),
    }
    if any(record.get(key) != value for key, value in common.items()):
        raise StorageError("attachment_activation_record_mismatch")
    if name == "start-intent":
        expected = set(common) | {"config_sha256", "operator_sqlite_sha256", "intent_ns"}
        if (
            set(record) != expected
            or record.get("config_sha256") != manifest["files"]["config.json"]["sha256"]
            or record.get("operator_sqlite_sha256")
            != manifest["files"]["operator.sqlite"]["sha256"]
            or type(record.get("intent_ns")) is not int
            or record["intent_ns"] <= 0
        ):
            raise StorageError("attachment_start_intent_mismatch")
    elif name == "idle-probe":
        expected = set(common) | {
            "intent_sha256",
            "pid",
            "status_schema",
            "mode",
            "phase",
            "jobs",
            "probe_ns",
        }
        if (
            set(record) != expected
            or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("intent_sha256", "")))
            or type(record.get("pid")) is not int
            or record["pid"] <= 0
            or record.get("status_schema") != "operator-status-v1"
            or record.get("mode") != "idle"
            or record.get("phase") != "idle"
            or record.get("jobs") != []
            or type(record.get("probe_ns")) is not int
            or record["probe_ns"] <= 0
        ):
            raise StorageError("attachment_idle_probe_record_mismatch")
    else:
        expected = set(common) | {
            "intent_sha256",
            "idle_probe_sha256",
            "config_sha256",
            "operator_sqlite_sha256",
            "inventory",
            "inventory_sha256",
            "result_ns",
        }
        if (
            set(record) != expected
            or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("intent_sha256", "")))
            or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("idle_probe_sha256", "")))
            or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("config_sha256", "")))
            or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("operator_sqlite_sha256", "")))
            or not isinstance(record.get("inventory"), list)
            or record.get("inventory_sha256") != _sha256(_json_bytes(record["inventory"]))
            or type(record.get("result_ns")) is not int
            or record["result_ns"] <= 0
        ):
            raise StorageError("attachment_result_record_mismatch")
    return record


def _runtime_inventory(operator_dir: Path, owner_uid: int, owner_gid: int) -> list[dict[str, Any]]:
    try:
        root_fd = os.open(operator_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise StorageError("attachment_runtime_root_unavailable") from exc
    entries: list[dict[str, Any]] = []
    total_bytes = 0

    def walk(directory_fd: int, prefix: str) -> None:
        nonlocal total_bytes
        names = sorted(os.listdir(directory_fd))
        if not prefix and set(names) != {
            "background",
            "config.json",
            "operator",
            "operator.sqlite",
        }:
            raise StorageError("attachment_runtime_root_members_mismatch")
        for name in names:
            if name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
                raise StorageError("attachment_runtime_member_name_invalid")
            relative = f"{prefix}/{name}" if prefix else name
            if relative not in RUNTIME_DIRECTORIES | RUNTIME_FILES:
                raise StorageError("attachment_runtime_member_not_allowlisted")
            try:
                before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise StorageError("attachment_runtime_member_unavailable") from exc
            if stat.S_ISDIR(before.st_mode):
                if (
                    before.st_uid != owner_uid
                    or before.st_gid != owner_gid
                    or stat.S_IMODE(before.st_mode) != 0o700
                ):
                    raise StorageError("attachment_runtime_directory_unsafe")
                entries.append({"path": relative, "kind": "directory", "mode": "0700"})
                child_fd = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd
                )
                try:
                    after = os.fstat(child_fd)
                    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                        raise StorageError("attachment_runtime_directory_changed")
                    walk(child_fd, relative)
                finally:
                    os.close(child_fd)
                continue
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != owner_uid
                or before.st_gid != owner_gid
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1
                or before.st_size > MAX_DATABASE_BYTES
            ):
                raise StorageError("attachment_runtime_file_unsafe")
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
            try:
                opened = os.fstat(fd)
                if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                    raise StorageError("attachment_runtime_file_changed")
                digest = hashlib.sha256()
                size = 0
                while block := os.read(fd, 1024 * 1024):
                    size += len(block)
                    total_bytes += len(block)
                    if size > MAX_DATABASE_BYTES or total_bytes > 64 * 1024 * 1024:
                        raise StorageError("attachment_runtime_files_oversized")
                    digest.update(block)
                after = os.fstat(fd)
                if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    raise StorageError("attachment_runtime_file_changed")
                entries.append(
                    {
                        "path": relative,
                        "kind": "file",
                        "mode": "0600",
                        "bytes": size,
                        "sha256": digest.hexdigest(),
                    }
                )
            finally:
                os.close(fd)
            if len(entries) > 2048:
                raise StorageError("attachment_runtime_file_count_exceeded")

    try:
        walk(root_fd, "")
    finally:
        os.close(root_fd)
    return sorted(entries, key=lambda item: str(item["path"]))


def _verify_stopped_attachment_state(
    manifest: dict[str, Any], operator_dir: Path
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    owner = pwd.getpwnam("brain")
    config = _read_file(
        operator_dir / "config.json", maximum=MAX_CONFIG_BYTES, uid=owner.pw_uid, mode=0o600
    )
    if (
        _sha256(config) != manifest["files"]["config.json"]["sha256"]
        or _strict_json(config, MAX_CONFIG_BYTES, "runtime_config") != EXPECTED_CONFIG
    ):
        raise StorageError("attachment_runtime_config_mismatch")
    database = _read_file(
        operator_dir / "operator.sqlite",
        maximum=MAX_DATABASE_BYTES,
        uid=owner.pw_uid,
        mode=0o600,
    )
    _verify_idle_database(database, _schema_fingerprint(), allow_startup_maintenance=True)
    inventory = _runtime_inventory(operator_dir, owner.pw_uid, owner.pw_gid)
    by_path = {entry["path"]: entry for entry in inventory}
    if by_path.get("config.json", {}).get("sha256") != _sha256(config) or by_path.get(
        "operator.sqlite", {}
    ).get("sha256") != _sha256(database):
        raise StorageError("attachment_runtime_inventory_mismatch")
    return {
        "config_sha256": _sha256(config),
        "operator_sqlite_sha256": _sha256(database),
    }, inventory


def _effective_properties(unit: str) -> dict[str, str]:
    names = (
        "FragmentPath",
        "DropInPaths",
        "NeedDaemonReload",
        "ExecStart",
        "User",
        "Group",
        "PrivateNetwork",
        "WorkingDirectory",
        "ReadWritePaths",
        "StateDirectory",
        "ProtectSystem",
        "ProtectHome",
        "Restart",
        "EnvironmentFiles",
    )
    result = subprocess.run(
        ["systemctl", "show", "--no-pager", *[f"--property={name}" for name in names], unit],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise StorageError("attachment_unit_properties_unavailable")
    values: dict[str, str] = {"EnvironmentFiles": ""}
    for line in result.stdout.splitlines():
        name, separator, value = line.partition("=")
        if not separator or name not in names or name in values:
            raise StorageError("attachment_unit_properties_malformed")
        values[name] = value
    if set(values) != set(names):
        raise StorageError("attachment_unit_properties_malformed")
    return values


def _verify_unit(manifest: dict[str, Any], pin: dict[str, Any]) -> None:
    release_id = str(manifest["release_id"])
    unit = str(manifest["unit_name"])
    release_root = linux_release.RELEASE_BASE / release_id
    operator_dir = ATTACHMENT_BASE / str(manifest["attachment_id"]) / "operator"
    unit_path = UNIT_BASE / unit
    try:
        unit_bytes = unit_path.read_bytes()
        unit_stat = unit_path.stat()
    except OSError as exc:
        raise StorageError("attachment_unit_missing") from exc
    if unit_stat.st_uid != 0 or unit_stat.st_mode & 0o022:
        raise StorageError("attachment_unit_permissions_unsafe")
    if (
        _sha256(unit_bytes) != pin.get("unit_sha256")
        or pin.get("unit_name") != unit
        or pin.get("manifest_sha256") != hashlib.sha256(_json_bytes(manifest)).hexdigest()
    ):
        raise StorageError("attachment_root_pin_mismatch")
    fields = _effective_properties(unit)
    expected = {
        "FragmentPath": str(unit_path),
        "DropInPaths": "",
        "NeedDaemonReload": "no",
        "User": "brain",
        "Group": "brain",
        "PrivateNetwork": "yes",
        "WorkingDirectory": str(release_root),
        "ReadWritePaths": str(operator_dir),
        "StateDirectory": "",
        "ProtectSystem": "strict",
        "ProtectHome": "yes",
        "Restart": "no",
        "EnvironmentFiles": "",
    }
    if any(fields[name] != value for name, value in expected.items()):
        raise StorageError("attachment_effective_unit_mismatch")
    try:
        match = re.search(r"(?:^| )argv\[\]=(.*?) ; ignore_errors=", fields["ExecStart"])
        argv = shlex.split(match.group(1)) if match else []
    except ValueError as exc:
        raise StorageError("attachment_effective_execstart_malformed") from exc
    expected_argv = [
        str(release_root / "venv/bin/python"),
        "-B",
        "-I",
        "-m",
        "memecoin_operator.runtime",
        "--root",
        str(operator_dir),
        "--port",
        str(manifest["port"]),
    ]
    if argv != expected_argv:
        raise StorageError("attachment_effective_execstart_mismatch")


def _load_pinned_attachment(
    attachment_id: str,
    expected_release_id: str,
    expected_release_sha256: str,
    expected_restore_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    if os.geteuid() != 0:
        raise StorageError("attachment_verify_requires_root")
    manifest, raw_manifest, _directory, operator_dir = _read_attachment_manifest(attachment_id)
    if (
        manifest.get("release_id") != expected_release_id
        or manifest.get("release_manifest_sha256") != expected_release_sha256
        or manifest.get("restore_manifest_sha256") != expected_restore_sha256
    ):
        raise StorageError("attachment_external_pin_mismatch")
    release = linux_release.verify(expected_release_id)
    if release.get("manifest_sha256") != expected_release_sha256:
        raise StorageError("attachment_release_manifest_pin_mismatch")
    pin_path = _pin_path(attachment_id)
    pin = _read_root_pin(pin_path)
    if (
        pin.get("schema") != ATTACHMENT_SCHEMA
        or pin.get("attachment_id") != attachment_id
        or pin.get("release_id") != expected_release_id
        or pin.get("release_manifest_sha256") != expected_release_sha256
        or pin.get("restore_manifest_sha256") != expected_restore_sha256
        or pin.get("manifest_sha256") != _sha256(raw_manifest)
    ):
        raise StorageError("attachment_root_pin_mismatch")
    _verify_unit(manifest, pin)
    return manifest, pin, operator_dir


def _verify_attachment(
    attachment_id: str,
    expected_release_id: str,
    expected_release_sha256: str,
    expected_restore_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, bytes]]:
    manifest, pin, _operator_dir = _load_pinned_attachment(
        attachment_id,
        expected_release_id,
        expected_release_sha256,
        expected_restore_sha256,
    )
    payloads = _verify_local_payloads(manifest, owner_uid=pwd.getpwnam("brain").pw_uid)
    state = linux_service._unit_status(str(manifest["unit_name"]))
    if state != ("loaded", "inactive", "disabled", 0):
        raise StorageError("attachment_unit_not_inert")
    return manifest, pin, payloads


def stage_unit(
    attachment_id: str,
    release_id: str,
    release_manifest_sha256: str,
    restore_manifest_sha256: str,
) -> dict[str, object]:
    if os.geteuid() != 0:
        raise StorageError("attachment_stage_unit_requires_root")
    manifest, raw_manifest, _directory, operator_dir = _read_attachment_manifest(attachment_id)
    if (
        manifest.get("release_id") != release_id
        or manifest.get("release_manifest_sha256") != release_manifest_sha256
        or manifest.get("restore_manifest_sha256") != restore_manifest_sha256
    ):
        raise StorageError("attachment_external_pin_mismatch")
    release = linux_release.verify(release_id)
    if release.get("manifest_sha256") != release_manifest_sha256:
        raise StorageError("attachment_release_manifest_pin_mismatch")
    _verify_local_payloads(manifest, owner_uid=pwd.getpwnam("brain").pw_uid)
    unit = str(manifest["unit_name"])
    unit_id = unit.removeprefix("memecoin-brain-").removesuffix(".service")
    unit_text = linux_service.render_unit(
        linux_release.RELEASE_BASE / release_id / "venv/bin/python",
        operator_dir,
        int(manifest["port"]),
        code_root=linux_release.RELEASE_BASE / release_id,
        readwrite_path=operator_dir,
        state_directory=False,
    ).replace(
        "Description=Disposable Brain operator lab (manual activation only)",
        f"Description=Brain synthetic attachment {attachment_id} (manual activation only)",
    )
    pin = {
        "schema": ATTACHMENT_SCHEMA,
        "attachment_id": attachment_id,
        "release_id": release_id,
        "release_manifest_sha256": release_manifest_sha256,
        "restore_manifest_sha256": restore_manifest_sha256,
        "manifest_sha256": _sha256(raw_manifest),
        "unit_name": unit,
        "unit_sha256": _sha256(unit_text.encode("utf-8")),
    }
    pin_path = _pin_path(attachment_id)
    unit_path = UNIT_BASE / unit
    if os.path.lexists(unit_path) or linux_service._unit_status(unit)[0] != "not-found":
        raise StorageError("attachment_unit_collision")
    _write_root_pin(pin_path, _json_bytes(pin))
    unit_installed = False
    try:
        linux_service.install(unit_text, unit_path=unit_path, unit=unit)
        unit_installed = True
        state = linux_service._unit_status(unit)
        if state != ("loaded", "inactive", "disabled", 0):
            raise StorageError("attachment_unit_not_inert_after_install")
        _verify_attachment(
            attachment_id,
            release_id,
            release_manifest_sha256,
            restore_manifest_sha256,
        )
    except BaseException:
        if unit_installed:
            unit_path.unlink(missing_ok=True)
            subprocess.run(["systemctl", "daemon-reload"], check=False, timeout=10)
        pin_path.unlink(missing_ok=True)
        raise
    return {
        "state": "attached_inert",
        "attachment_id": attachment_id,
        "unit": unit,
        "unit_id": unit_id,
    }


def _probe_status(unit: str, manifest: dict[str, Any]) -> dict[str, Any]:
    state = linux_service._unit_status(unit)
    if state[0] != "loaded" or state[1] != "active" or state[3] <= 0:
        raise StorageError("attachment_service_not_active")
    probe_code = (
        "import json,sys,urllib.request;"
        "u='http://127.0.0.1:'+sys.argv[1]+'/api/status';"
        "r=urllib.request.urlopen(u,timeout=3);"
        "d=json.loads(r.read(1048577));"
        "print(json.dumps({'schema':d.get('schema'),'mode':d.get('mode'),"
        "'phase':d.get('phase'),'jobs':d.get('jobs')}))"
    )
    last_error = "attachment_status_unavailable"
    for _ in range(30):
        result = subprocess.run(
            [
                "nsenter",
                "--target",
                str(state[3]),
                "--net",
                "/usr/bin/python3",
                "-B",
                "-I",
                "-c",
                probe_code,
                str(manifest["port"]),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError:
                last_error = "attachment_status_malformed"
            else:
                if (
                    data.get("schema") == "operator-status-v1"
                    and data.get("mode") == "idle"
                    and data.get("phase") == "idle"
                    and data.get("jobs") == []
                ):
                    return data
                last_error = "attachment_status_not_idle"
        time.sleep(0.25)
    raise StorageError(last_error)


def activate_attached(
    attachment_id: str,
    release_id: str,
    release_manifest_sha256: str,
    restore_manifest_sha256: str,
) -> dict[str, object]:
    if os.geteuid() != 0:
        raise StorageError("attachment_activate_requires_root")
    manifest, pin, _operator_dir = _load_pinned_attachment(
        attachment_id, release_id, release_manifest_sha256, restore_manifest_sha256
    )
    if _read_activation_record(manifest, pin, "start-intent") is not None:
        raise StorageError("attachment_activation_already_attempted")
    if _read_activation_record(manifest, pin, "activation-result") is not None:
        raise StorageError("attachment_activation_result_already_exists")
    manifest, _pin, _payloads = _verify_attachment(
        attachment_id, release_id, release_manifest_sha256, restore_manifest_sha256
    )
    unit = str(manifest["unit_name"])
    intent = {
        "schema": ACTIVATION_SCHEMA,
        "attachment_id": attachment_id,
        "unit_name": unit,
        "manifest_sha256": pin["manifest_sha256"],
        "pin_sha256": _sha256(_json_bytes(pin)),
        "config_sha256": manifest["files"]["config.json"]["sha256"],
        "operator_sqlite_sha256": manifest["files"]["operator.sqlite"]["sha256"],
        "intent_ns": time.time_ns(),
    }
    _write_activation_record(attachment_id, "start-intent", intent)
    try:
        subprocess.run(["systemctl", "start", unit], check=True, timeout=30)
        status = _probe_status(unit, manifest)
        active_state = linux_service._unit_status(unit)
        if active_state[0] != "loaded" or active_state[1] != "active" or active_state[3] <= 0:
            raise StorageError("attachment_service_not_active_after_probe")
        probe = {
            "schema": ACTIVATION_SCHEMA,
            "attachment_id": attachment_id,
            "unit_name": unit,
            "manifest_sha256": pin["manifest_sha256"],
            "pin_sha256": _sha256(_json_bytes(pin)),
            "intent_sha256": _sha256(_json_bytes(intent)),
            "pid": active_state[3],
            "status_schema": status["schema"],
            "mode": status["mode"],
            "phase": status["phase"],
            "jobs": status["jobs"],
            "probe_ns": time.time_ns(),
        }
        _write_activation_record(attachment_id, "idle-probe", probe)
    except BaseException:
        subprocess.run(["systemctl", "stop", unit], check=False, timeout=15)
        raise
    return {
        "state": "active_synthetic_idle",
        "attachment_id": attachment_id,
        "unit": unit,
        "status_schema": status["schema"],
        "mode": status["mode"],
        "phase": status["phase"],
        "jobs": len(status["jobs"]),
        "finalization_required": True,
    }


def finalize_attached(
    attachment_id: str,
    release_id: str,
    release_manifest_sha256: str,
    restore_manifest_sha256: str,
) -> dict[str, object]:
    if os.geteuid() != 0:
        raise StorageError("attachment_finalize_requires_root")
    manifest, pin, operator_dir = _load_pinned_attachment(
        attachment_id, release_id, release_manifest_sha256, restore_manifest_sha256
    )
    intent = _read_activation_record(manifest, pin, "start-intent")
    if intent is None:
        raise StorageError("attachment_start_intent_missing")
    idle_probe = _read_activation_record(manifest, pin, "idle-probe")
    if idle_probe is None or idle_probe["intent_sha256"] != _sha256(_json_bytes(intent)):
        raise StorageError("attachment_idle_probe_missing_or_unbound")
    if _read_activation_record(manifest, pin, "activation-result") is not None:
        raise StorageError("attachment_activation_result_already_exists")
    unit = str(manifest["unit_name"])
    state = linux_service._unit_status(unit)
    if state != ("loaded", "inactive", "disabled", 0):
        raise StorageError("attachment_unit_not_stopped_disabled")
    hashes, inventory = _verify_stopped_attachment_state(manifest, operator_dir)
    result = {
        "schema": ACTIVATION_SCHEMA,
        "attachment_id": attachment_id,
        "unit_name": unit,
        "manifest_sha256": pin["manifest_sha256"],
        "pin_sha256": _sha256(_json_bytes(pin)),
        "intent_sha256": _sha256(_json_bytes(intent)),
        "idle_probe_sha256": _sha256(_json_bytes(idle_probe)),
        **hashes,
        "inventory": inventory,
        "inventory_sha256": _sha256(_json_bytes(inventory)),
        "result_ns": time.time_ns(),
    }
    _write_activation_record(attachment_id, "activation-result", result)
    return {
        "state": "activated_stopped_idle_verified",
        "attachment_id": attachment_id,
        "unit": unit,
        "inventory_sha256": result["inventory_sha256"],
        "operator_sqlite_sha256": result["operator_sqlite_sha256"],
    }


def verify_attached(
    attachment_id: str,
    release_id: str,
    release_manifest_sha256: str,
    restore_manifest_sha256: str,
) -> dict[str, object]:
    manifest, pin, operator_dir = _load_pinned_attachment(
        attachment_id, release_id, release_manifest_sha256, restore_manifest_sha256
    )
    intent = _read_activation_record(manifest, pin, "start-intent")
    idle_probe = _read_activation_record(manifest, pin, "idle-probe")
    result = _read_activation_record(manifest, pin, "activation-result")
    state = linux_service._unit_status(str(manifest["unit_name"]))
    if intent is None:
        payloads = _verify_local_payloads(manifest, owner_uid=pwd.getpwnam("brain").pw_uid)
        if state != ("loaded", "inactive", "disabled", 0):
            raise StorageError("attachment_unit_not_inert")
        return {
            "state": "attached_inert_verified",
            "attachment_id": attachment_id,
            "unit": manifest["unit_name"],
            "manifest_sha256": pin["manifest_sha256"],
            "config_sha256": _sha256(payloads["config.json"]),
            "operator_sqlite_sha256": _sha256(payloads["operator.sqlite"]),
        }
    if result is None:
        if (
            idle_probe is None
            or intent is None
            or idle_probe["intent_sha256"] != _sha256(_json_bytes(intent))
        ):
            raise StorageError("attachment_idle_probe_missing_or_unbound")
        if state[0] == "loaded" and state[1] == "active" and state[3] > 0:
            if state[3] != idle_probe["pid"]:
                raise StorageError("attachment_idle_probe_pid_mismatch")
            status = _probe_status(str(manifest["unit_name"]), manifest)
            return {
                "state": "activation_started_needs_finalize",
                "attachment_id": attachment_id,
                "unit": manifest["unit_name"],
                "mode": status["mode"],
                "phase": status["phase"],
                "jobs": len(status["jobs"]),
            }
        raise StorageError("attachment_activation_incomplete")
    if state != ("loaded", "inactive", "disabled", 0):
        raise StorageError("attachment_unit_not_stopped_disabled")
    if _sha256(_json_bytes(intent)) != result["intent_sha256"]:
        raise StorageError("attachment_activation_intent_result_mismatch")
    if (
        idle_probe is None
        or idle_probe["intent_sha256"] != _sha256(_json_bytes(intent))
        or _sha256(_json_bytes(idle_probe)) != result["idle_probe_sha256"]
    ):
        raise StorageError("attachment_activation_probe_result_mismatch")
    hashes, inventory = _verify_stopped_attachment_state(manifest, operator_dir)
    inventory_sha = _sha256(_json_bytes(inventory))
    if (
        hashes.get("config_sha256") != result["config_sha256"]
        or hashes.get("operator_sqlite_sha256") != result["operator_sqlite_sha256"]
        or inventory != result["inventory"]
        or inventory_sha != result["inventory_sha256"]
    ):
        raise StorageError("attachment_activation_result_state_mismatch")
    return {
        "state": "activated_stopped_idle_verified",
        "attachment_id": attachment_id,
        "unit": manifest["unit_name"],
        "manifest_sha256": pin["manifest_sha256"],
        "inventory_sha256": inventory_sha,
        "operator_sqlite_sha256": hashes["operator_sqlite_sha256"],
    }


def _root_call(release_id: str, args: list[str]) -> dict[str, Any]:
    python = linux_release.RELEASE_BASE / release_id / "venv/bin/python"
    result = subprocess.run(
        [
            "sudo",
            "-n",
            str(python),
            "-B",
            "-I",
            "-m",
            "memecoin_operator.linux_attachment",
            *args,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise StorageError("attachment_root_action_failed")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise StorageError("attachment_root_result_malformed") from exc
    if not isinstance(data, dict):
        raise StorageError("attachment_root_result_malformed")
    return data


def _add_pins(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--attachment-id", required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--release-manifest-sha256", required=True)
    parser.add_argument("--restore-manifest-sha256", required=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser(
        "prepare", help="copy verified synthetic restore and stage a disabled unit"
    )
    prepare.add_argument("--release-id", required=True)
    prepare.add_argument("--release-manifest-sha256", required=True)
    prepare.add_argument("--storage-config", type=Path, required=True)
    prepare.add_argument("--fs-uuid", required=True)
    prepare.add_argument("--root-id", required=True)
    prepare.add_argument("--target-parent", required=True)
    prepare.add_argument("--run-id", required=True)
    prepare.add_argument("--restore-manifest-sha256", required=True)
    prepare.add_argument("--attachment-id", required=True)
    stage = sub.add_parser("stage-unit", help=argparse.SUPPRESS)
    _add_pins(stage)
    activate_root = sub.add_parser("activate-root", help=argparse.SUPPRESS)
    _add_pins(activate_root)
    finalize_root = sub.add_parser("finalize-root", help=argparse.SUPPRESS)
    _add_pins(finalize_root)
    verify_root = sub.add_parser("verify-root", help=argparse.SUPPRESS)
    _add_pins(verify_root)
    activate = sub.add_parser("activate", help="explicitly start a verified synthetic attachment")
    _add_pins(activate)
    finalize = sub.add_parser("finalize", help="verify a stopped activation and pin its receipt")
    _add_pins(finalize)
    verify = sub.add_parser("verify", help="verify an installed attachment remains inert")
    _add_pins(verify)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_attachment(
                args.release_id,
                args.release_manifest_sha256,
                args.storage_config,
                args.fs_uuid,
                args.root_id,
                args.target_parent,
                args.run_id,
                args.restore_manifest_sha256,
                args.attachment_id,
            )
        elif args.command == "stage-unit":
            result = stage_unit(
                args.attachment_id,
                args.release_id,
                args.release_manifest_sha256,
                args.restore_manifest_sha256,
            )
        elif args.command == "activate":
            result = _root_call(
                args.release_id,
                [
                    "activate-root",
                    "--attachment-id",
                    args.attachment_id,
                    "--release-id",
                    args.release_id,
                    "--release-manifest-sha256",
                    args.release_manifest_sha256,
                    "--restore-manifest-sha256",
                    args.restore_manifest_sha256,
                ],
            )
        elif args.command == "verify":
            result = _root_call(
                args.release_id,
                [
                    "verify-root",
                    "--attachment-id",
                    args.attachment_id,
                    "--release-id",
                    args.release_id,
                    "--release-manifest-sha256",
                    args.release_manifest_sha256,
                    "--restore-manifest-sha256",
                    args.restore_manifest_sha256,
                ],
            )
        elif args.command == "finalize":
            result = _root_call(
                args.release_id,
                [
                    "finalize-root",
                    "--attachment-id",
                    args.attachment_id,
                    "--release-id",
                    args.release_id,
                    "--release-manifest-sha256",
                    args.release_manifest_sha256,
                    "--restore-manifest-sha256",
                    args.restore_manifest_sha256,
                ],
            )
        elif args.command in {"verify-root", "activate-root"}:
            if os.geteuid() != 0:
                raise StorageError("attachment_root_action_requires_root")
            if args.command == "verify-root":
                result = verify_attached(
                    args.attachment_id,
                    args.release_id,
                    args.release_manifest_sha256,
                    args.restore_manifest_sha256,
                )
            else:
                result = activate_attached(
                    args.attachment_id,
                    args.release_id,
                    args.release_manifest_sha256,
                    args.restore_manifest_sha256,
                )
        elif args.command == "finalize-root":
            result = finalize_attached(
                args.attachment_id,
                args.release_id,
                args.release_manifest_sha256,
                args.restore_manifest_sha256,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (StorageError, OSError, RuntimeError, ValueError) as exc:
        message = str(exc)
        if not re.fullmatch(r"[a-z0-9_:-]+", message):
            message = "attachment_operation_failed"
        print(f"linux_attachment_error: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
