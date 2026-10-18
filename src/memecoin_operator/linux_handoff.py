"""Prepare and verify inert one-to-one state handoffs between Linux releases."""

from __future__ import annotations

import argparse
import fcntl
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

from memecoin_operator import linux_attachment, linux_release, linux_service
from memecoin_research_storage import StorageError

INSTALLATION_BASE = Path("/var/lib/brain-installations")
HANDOFF_BASE = Path("/var/lib/brain-handoffs")
LOCK_BASE = Path("/run/brain-handoff-locks")
SCHEMA = "brain-linux-handoff-v1"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _validate_id(value: str, label: str) -> str:
    if not ID_RE.fullmatch(value):
        raise StorageError(f"handoff_{label}_invalid")
    return value


def _root_directory(path: Path, mode: int = 0o755) -> None:
    if path.is_symlink():
        raise StorageError("handoff_directory_symlink")
    try:
        path.mkdir(mode=mode, parents=True, exist_ok=True)
        metadata = path.lstat()
    except OSError as exc:
        raise StorageError("handoff_directory_unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        raise StorageError("handoff_directory_unsafe")


def _write_receipt(path: Path, value: dict[str, Any]) -> bytes:
    payload = _json_bytes(value)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(payload)
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)
        os.fchmod(fd, 0o444)
    finally:
        os.close(fd)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return payload


def _read_receipt(path: Path, maximum: int = 1024 * 1024) -> tuple[dict[str, Any], bytes]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise StorageError("handoff_receipt_unavailable") from exc
    try:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o444
            or metadata.st_nlink != 1
            or metadata.st_size > maximum
        ):
            raise StorageError("handoff_receipt_unsafe")
        raw = b""
        while chunk := os.read(fd, min(65536, maximum + 1 - len(raw))):
            raw += chunk
            if len(raw) > maximum:
                raise StorageError("handoff_receipt_oversized")
    finally:
        os.close(fd)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StorageError("handoff_receipt_invalid") from exc
    if not isinstance(value, dict):
        raise StorageError("handoff_receipt_invalid")
    return value, raw


def _logical_state_payload(payload: bytes) -> tuple[str, str]:
    """Hash canonical schema and rows from an isolated copy of exact SQLite bytes."""
    with tempfile.TemporaryDirectory(prefix="brain-linux-handoff-logical-") as temp:
        snapshot = Path(temp) / "operator.sqlite"
        snapshot.write_bytes(payload)
        snapshot.chmod(0o600)
        uri = f"{snapshot.as_uri()}?mode=ro&immutable=1"
        try:
            db = sqlite3.connect(uri, uri=True, timeout=2)
            try:
                if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise StorageError("handoff_sqlite_integrity_failed")
                schema_rows = db.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master "
                    "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
                ).fetchall()
                schema_sha = _sha256(_json_bytes([list(row) for row in schema_rows]))
                tables = [
                    str(row[0])
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                    )
                ]
                rows: dict[str, list[list[Any]]] = {}
                for table in tables:
                    quoted = '"' + table.replace('"', '""') + '"'
                    rows[table] = [
                        list(row) for row in db.execute(f"SELECT * FROM {quoted} ORDER BY rowid")
                    ]
                return schema_sha, _sha256(_json_bytes(rows))
            finally:
                db.close()
        except sqlite3.Error as exc:
            raise StorageError("handoff_sqlite_read_failed") from exc


def _logical_state(database: Path) -> tuple[str, str]:
    owner = pwd.getpwnam("brain")
    payload = linux_attachment._read_file(
        database,
        maximum=linux_attachment.MAX_DATABASE_BYTES,
        uid=owner.pw_uid,
        mode=0o600,
    )
    return _logical_state_payload(payload)


def _release_schema(release_id: str) -> str:
    python = linux_release.RELEASE_BASE / release_id / "venv/bin/python"
    code = (
        "import hashlib,json,sqlite3; from memecoin_operator import control; "
        "db=sqlite3.connect(':memory:'); control._schema(db); "
        'rows=db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master '
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name\").fetchall(); "
        "raw=(json.dumps([list(r) for r in rows],sort_keys=True,separators=(',', ':'),"
        "allow_nan=False)+'\\n').encode(); print(hashlib.sha256(raw).hexdigest())"
    )
    result = subprocess.run(
        [str(python), "-B", "-I", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root"},
    )
    value = result.stdout.strip()
    if result.returncode != 0 or not HASH_RE.fullmatch(value):
        raise StorageError("handoff_target_schema_unavailable")
    return value


def _source_boundary(
    attachment_id: str,
) -> tuple[dict[str, Any], dict[str, Any], Path, bytes, bytes, str, str]:
    manifest, raw_manifest, directory, operator_dir = linux_attachment._read_attachment_manifest(
        attachment_id
    )
    pin = linux_attachment._read_root_pin(linux_attachment._pin_path(attachment_id))
    loaded, pin, operator_dir = linux_attachment._load_pinned_attachment(
        attachment_id,
        str(manifest["release_id"]),
        str(manifest["release_manifest_sha256"]),
        str(manifest["restore_manifest_sha256"]),
    )
    if loaded != manifest or pin.get("manifest_sha256") != _sha256(raw_manifest):
        raise StorageError("handoff_source_pin_mismatch")
    result = linux_attachment._read_activation_record(manifest, pin, "activation-result")
    if result is None:
        raise StorageError("handoff_source_not_finalized")
    intent = linux_attachment._read_activation_record(manifest, pin, "start-intent")
    probe = linux_attachment._read_activation_record(manifest, pin, "idle-probe")
    if (
        intent is None
        or probe is None
        or result.get("intent_sha256") != _sha256(linux_attachment._json_bytes(intent))
        or result.get("idle_probe_sha256") != _sha256(linux_attachment._json_bytes(probe))
        or probe.get("intent_sha256") != _sha256(linux_attachment._json_bytes(intent))
    ):
        raise StorageError("handoff_source_not_finalized")
    if linux_service._unit_status(str(manifest["unit_name"])) != (
        "loaded",
        "inactive",
        "disabled",
        0,
    ):
        raise StorageError("handoff_source_not_stopped")
    hashes, inventory = linux_attachment._verify_stopped_attachment_state(manifest, operator_dir)
    if (
        result.get("config_sha256") != hashes.get("config_sha256")
        or result.get("operator_sqlite_sha256") != hashes.get("operator_sqlite_sha256")
        or result.get("inventory") != inventory
        or result.get("inventory_sha256") != _sha256(linux_attachment._json_bytes(inventory))
    ):
        raise StorageError("handoff_source_finalized_state_mismatch")
    owner = pwd.getpwnam("brain")
    config_bytes = linux_attachment._read_file(
        operator_dir / "config.json",
        maximum=linux_attachment.MAX_CONFIG_BYTES,
        uid=owner.pw_uid,
        mode=0o600,
    )
    database_bytes = linux_attachment._read_file(
        operator_dir / "operator.sqlite",
        maximum=linux_attachment.MAX_DATABASE_BYTES,
        uid=owner.pw_uid,
        mode=0o600,
    )
    schema_sha, logical_sha = _logical_state_payload(database_bytes)
    return manifest, pin, operator_dir, config_bytes, database_bytes, schema_sha, logical_sha


def _handoff_identity(installation_id: str, generation_id: str, release_id: str) -> str:
    return _sha256(
        _json_bytes(
            {
                "schema": SCHEMA,
                "installation_id": installation_id,
                "generation_id": generation_id,
                "release_id": release_id,
                "unit_policy": "disabled-private-network-v1",
            }
        )
    )


def _paths(installation_id: str, generation_id: str, binding_id: str) -> tuple[Path, Path]:
    generation = INSTALLATION_BASE / installation_id / "generations" / generation_id
    receipt = HANDOFF_BASE / binding_id
    return generation, receipt


def _prepare_runtime_directories(operator: Path, uid: int, gid: int) -> None:
    for relative in sorted(
        linux_attachment.RUNTIME_DIRECTORIES, key=lambda item: (item.count("/"), item)
    ):
        current = operator
        for part in relative.split("/"):
            current = current / part
            created = False
            try:
                current.mkdir(mode=0o700)
                created = True
            except FileExistsError:
                pass
            metadata = current.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
                raise StorageError("handoff_runtime_directory_unsafe")
            if created:
                os.chown(current, uid, gid)
            elif metadata.st_uid != uid or metadata.st_gid != gid:
                raise StorageError("handoff_runtime_directory_unsafe")


def prepare(
    installation_id: str, source_attachment_id: str, target_release_id: str
) -> dict[str, object]:
    if os.geteuid() != 0:
        raise StorageError("handoff_prepare_requires_root")
    installation_id = _validate_id(installation_id, "installation_id")
    if not HASH_RE.fullmatch(target_release_id):
        raise StorageError("handoff_target_release_id_invalid")
    (
        source,
        source_pin,
        source_root,
        config_bytes,
        source_database_bytes,
        source_schema,
        source_logical,
    ) = _source_boundary(source_attachment_id)
    if source_schema != linux_attachment._schema_fingerprint():
        raise StorageError("handoff_schema_mismatch")
    target = linux_release.verify(target_release_id)
    if target.get("unit_state") != ("loaded", "inactive", "disabled", 0):
        raise StorageError("handoff_target_not_inert")
    target_schema = _release_schema(target_release_id)
    if target_schema != source_schema:
        raise StorageError("handoff_target_schema_mismatch")
    generation_id = _sha256(
        _json_bytes(
            {
                "schema": SCHEMA,
                "source_attachment_id": source_attachment_id,
                "source_manifest_sha256": source_pin["manifest_sha256"],
                "source_logical_state_sha256": source_logical,
                "target_release_id": target_release_id,
                "target_schema_sha256": target_schema,
            }
        )
    )
    binding_id = _handoff_identity(installation_id, generation_id, target_release_id)
    generation_root, receipt_root = _paths(installation_id, generation_id, binding_id)
    _root_directory(LOCK_BASE, 0o700)
    lock_fd = os.open(
        LOCK_BASE / f"{installation_id}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    pending: Path | None = None
    pending_created = False
    generation_published = False
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        _root_directory(INSTALLATION_BASE)
        _root_directory(INSTALLATION_BASE / installation_id)
        _root_directory(INSTALLATION_BASE / installation_id / "generations")
        _root_directory(HANDOFF_BASE, 0o700)
        if os.path.lexists(generation_root) or os.path.lexists(receipt_root):
            raise StorageError("handoff_generation_collision")
        brain = pwd.getpwnam("brain")
        pending = generation_root.parent / f".pending-{generation_id}-{os.getpid()}"
        pending.mkdir(mode=0o700)
        pending_created = True
        os.chown(pending, 0, 0)
        operator = pending / "operator"
        operator.mkdir(mode=0o700)
        os.chown(operator, brain.pw_uid, brain.pw_gid)
        config_fd = os.open(
            operator / "config.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        try:
            os.write(config_fd, config_bytes)
            os.fsync(config_fd)
        finally:
            os.close(config_fd)
        db_path = operator / "operator.sqlite"
        with tempfile.TemporaryDirectory(prefix="brain-linux-handoff-source-") as temp:
            source_copy = Path(temp) / "source.sqlite"
            source_copy.write_bytes(source_database_bytes)
            source_copy.chmod(0o600)
            source_uri = f"{source_copy.as_uri()}?mode=ro&immutable=1"
            try:
                source_db = sqlite3.connect(source_uri, uri=True, timeout=2)
                dest_db = sqlite3.connect(db_path)
                try:
                    source_db.backup(dest_db, pages=256, sleep=0.01)
                    dest_db.execute("PRAGMA journal_mode=DELETE")
                    dest_db.commit()
                    if dest_db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                        raise StorageError("handoff_snapshot_integrity_failed")
                finally:
                    dest_db.close()
                    source_db.close()
            except sqlite3.Error as exc:
                raise StorageError("handoff_snapshot_failed") from exc
        os.chown(db_path, brain.pw_uid, brain.pw_gid)
        os.chmod(db_path, 0o600)
        _prepare_runtime_directories(operator, brain.pw_uid, brain.pw_gid)
        (
            after_source,
            after_pin,
            after_root,
            after_config_bytes,
            after_database_bytes,
            after_schema,
            after_logical,
        ) = _source_boundary(source_attachment_id)
        if (
            after_source != source
            or after_pin != source_pin
            or after_root != source_root
            or after_config_bytes != config_bytes
            or after_database_bytes != source_database_bytes
            or after_schema != source_schema
            or after_logical != source_logical
        ):
            raise StorageError("handoff_source_changed_during_snapshot")
        linux_attachment._verify_idle_database(db_path.read_bytes(), source_schema)
        output_schema, output_logical = _logical_state(db_path)
        if output_schema != source_schema or output_logical != source_logical:
            raise StorageError("handoff_snapshot_logical_mismatch")
        output_database_sha256 = linux_release._sha256(db_path)
        for name in ("config.json", "operator.sqlite"):
            path = operator / name
            os.chmod(path, 0o600)
            os.chown(path, brain.pw_uid, brain.pw_gid)
        os.chown(operator, brain.pw_uid, brain.pw_gid)
        os.chmod(operator, 0o700)
        os.rename(pending, generation_root)
        generation_published = True
        os.chmod(generation_root, 0o711)
        generation_parent_fd = os.open(
            generation_root.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            os.fsync(generation_parent_fd)
        finally:
            os.close(generation_parent_fd)
        unit_name = f"memecoin-brain-{binding_id}.service"
        code_root = linux_release.RELEASE_BASE / target_release_id
        state_root = generation_root / "operator"
        port = 20000 + int(binding_id[:8], 16) % 40000
        unit_text = linux_service.render_unit(
            code_root / "venv/bin/python",
            state_root,
            port,
            code_root=code_root,
            readwrite_path=state_root,
            state_directory=False,
            state_base=INSTALLATION_BASE,
            home=state_root,
            state_layout="handoff-v1",
        )
        unit_text = unit_text.replace(
            "Description=Disposable Brain operator lab (manual activation only)",
            f"Description=Brain handoff {binding_id} (manual activation only)",
        )
        linux_service.install(
            unit_text,
            unit_path=linux_service.UNIT_PATH.parent / unit_name,
            unit=unit_name,
        )
        target_after = linux_release.verify(target_release_id)
        if target_after.get("unit_state") != ("loaded", "inactive", "disabled", 0):
            raise StorageError("handoff_target_changed_during_prepare")
        generation_record = {
            "schema": SCHEMA,
            "installation_id": installation_id,
            "generation_id": generation_id,
            "source_attachment_id": source_attachment_id,
            "source_release_id": source["release_id"],
            "source_attachment_manifest_sha256": source_pin["manifest_sha256"],
            "source_restore_manifest_sha256": source["restore_manifest_sha256"],
            "source_logical_state_sha256": source_logical,
            "target_release_id": target_release_id,
            "target_release_manifest_sha256": target["manifest_sha256"],
            "sqlite_schema_sha256": target_schema,
            "output_logical_state_sha256": output_logical,
            "config_sha256": _sha256(config_bytes),
            "operator_sqlite_sha256": output_database_sha256,
            "unit_name": unit_name,
            "unit_sha256": _sha256(unit_text.encode()),
            "created_ns": time.time_ns(),
        }
        generation_raw = _json_bytes(generation_record)
        binding_record = {
            "schema": SCHEMA,
            "binding_id": binding_id,
            "installation_id": installation_id,
            "generation_id": generation_id,
            "target_release_id": target_release_id,
            "unit_name": unit_name,
            "generation_sha256": _sha256(generation_raw),
            "state": "prepared-inert",
            "created_ns": time.time_ns(),
        }
        _root_directory(receipt_root, 0o700)
        _write_receipt(receipt_root / "GENERATION.json", generation_record)
        _write_receipt(receipt_root / "BINDING.json", binding_record)
        return {
            "state": "prepared-inert",
            "installation_id": installation_id,
            "generation_id": generation_id,
            "binding_id": binding_id,
            "target_release_id": target_release_id,
            "unit": unit_name,
            "sqlite_schema_sha256": target_schema,
            "logical_state_sha256": output_logical,
        }
    except BaseException:
        if pending_created and not generation_published and pending is not None:
            shutil.rmtree(pending, ignore_errors=True)
        raise
    finally:
        os.close(lock_fd)


def verify(binding_id: str) -> dict[str, object]:
    if os.geteuid() != 0:
        raise StorageError("handoff_verify_requires_root")
    if not HASH_RE.fullmatch(binding_id):
        raise StorageError("handoff_binding_id_invalid")
    receipt_root = HANDOFF_BASE / binding_id
    binding, binding_raw = _read_receipt(receipt_root / "BINDING.json")
    generation, generation_raw = _read_receipt(receipt_root / "GENERATION.json")
    if (
        binding.get("schema") != SCHEMA
        or binding.get("binding_id") != binding_id
        or binding.get("generation_sha256") != _sha256(generation_raw)
        or generation.get("schema") != SCHEMA
        or binding.get("installation_id") != generation.get("installation_id")
        or binding.get("generation_id") != generation.get("generation_id")
    ):
        raise StorageError("handoff_binding_receipt_mismatch")
    installation_id = str(binding["installation_id"])
    generation_id = str(binding["generation_id"])
    release_id = str(binding["target_release_id"])
    expected_binding = _handoff_identity(installation_id, generation_id, release_id)
    if expected_binding != binding_id:
        raise StorageError("handoff_binding_identity_mismatch")
    generation_root, _receipt = _paths(installation_id, generation_id, binding_id)
    for directory in (
        INSTALLATION_BASE,
        INSTALLATION_BASE / installation_id,
        INSTALLATION_BASE / installation_id / "generations",
        generation_root,
    ):
        try:
            metadata = directory.lstat()
        except OSError as exc:
            raise StorageError("handoff_generation_directory_missing") from exc
        expected_mode = 0o711 if directory == generation_root else 0o755
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != expected_mode
        ):
            raise StorageError("handoff_generation_directory_unsafe")
    operator = generation_root / "operator"
    brain = pwd.getpwnam("brain")
    files: dict[str, bytes] = {}
    for name, maximum, key in (
        ("config.json", linux_attachment.MAX_CONFIG_BYTES, "config_sha256"),
        ("operator.sqlite", linux_attachment.MAX_DATABASE_BYTES, "operator_sqlite_sha256"),
    ):
        payload = linux_attachment._read_file(
            operator / name, maximum=maximum, uid=brain.pw_uid, mode=0o600
        )
        if _sha256(payload) != generation.get(key):
            raise StorageError("handoff_output_payload_mismatch")
        files[name] = payload
    if (
        linux_attachment._strict_json(
            files["config.json"], linux_attachment.MAX_CONFIG_BYTES, "handoff_config"
        )
        != linux_attachment.EXPECTED_CONFIG
    ):
        raise StorageError("handoff_config_not_inert")
    linux_attachment._verify_idle_database(
        files["operator.sqlite"], str(generation["sqlite_schema_sha256"])
    )
    schema_sha, logical_sha = _logical_state(operator / "operator.sqlite")
    target = linux_release.verify(release_id)
    unit_path = linux_service.UNIT_PATH.parent / str(binding["unit_name"])
    try:
        unit_bytes = unit_path.read_bytes()
        unit_metadata = unit_path.stat()
    except OSError as exc:
        raise StorageError("handoff_unit_missing") from exc
    if (
        unit_metadata.st_uid != 0
        or stat.S_IMODE(unit_metadata.st_mode) != 0o644
        or _sha256(unit_bytes) != generation.get("unit_sha256")
    ):
        raise StorageError("handoff_unit_pin_mismatch")
    effective = linux_attachment._effective_properties(str(binding["unit_name"]))
    expected_effective = {
        "FragmentPath": str(unit_path),
        "DropInPaths": "",
        "NeedDaemonReload": "no",
        "User": "brain",
        "Group": "brain",
        "PrivateNetwork": "yes",
        "WorkingDirectory": str(linux_release.RELEASE_BASE / release_id),
        "ReadWritePaths": str(operator),
        "StateDirectory": "",
        "ProtectSystem": "strict",
        "ProtectHome": "yes",
        "Restart": "no",
        "EnvironmentFiles": "",
    }
    if any(effective.get(key) != value for key, value in expected_effective.items()):
        raise StorageError("handoff_effective_unit_mismatch")
    match = re.search(r"(?:^| )argv\[\]=(.*?) ; ignore_errors=", effective["ExecStart"])
    try:
        argv = shlex.split(match.group(1)) if match else []
    except ValueError as exc:
        raise StorageError("handoff_effective_execstart_malformed") from exc
    expected_argv = [
        str(linux_release.RELEASE_BASE / release_id / "venv/bin/python"),
        "-B",
        "-I",
        "-m",
        "memecoin_operator.runtime",
        "--root",
        str(operator),
        "--port",
        str(20000 + int(binding_id[:8], 16) % 40000),
    ]
    if (
        schema_sha != generation.get("sqlite_schema_sha256")
        or logical_sha != generation.get("output_logical_state_sha256")
        or target.get("manifest_sha256") != generation.get("target_release_manifest_sha256")
        or argv != expected_argv
        or linux_service._unit_status(str(binding["unit_name"]))
        != ("loaded", "inactive", "disabled", 0)
    ):
        raise StorageError("handoff_inert_verification_failed")
    return {
        "state": "prepared-inert",
        "binding_id": binding_id,
        "installation_id": installation_id,
        "generation_id": generation_id,
        "target_release_id": release_id,
        "unit": binding["unit_name"],
        "sqlite_schema_sha256": schema_sha,
        "logical_state_sha256": logical_sha,
        "generation_receipt_sha256": _sha256(generation_raw),
        "binding_receipt_sha256": _sha256(binding_raw),
    }


def _lifecycle_path(binding_id: str, name: str) -> Path:
    if name not in {"START", "PROBE", "FINAL"}:
        raise StorageError("handoff_lifecycle_record_invalid")
    return HANDOFF_BASE / binding_id / f"{name}.json"


def _read_lifecycle(binding_id: str, name: str) -> tuple[dict[str, Any], bytes] | None:
    path = _lifecycle_path(binding_id, name)
    if not os.path.lexists(path):
        return None
    record, raw = _read_receipt(path)
    binding, _binding_raw = _read_receipt(HANDOFF_BASE / binding_id / "BINDING.json")
    generation, generation_raw = _read_receipt(HANDOFF_BASE / binding_id / "GENERATION.json")
    if (
        record.get("schema") != SCHEMA
        or record.get("binding_id") != binding_id
        or record.get("generation_receipt_sha256") != _sha256(generation_raw)
        or record.get("unit_name") != binding.get("unit_name")
        or record.get("unit_sha256") != generation.get("unit_sha256")
    ):
        raise StorageError("handoff_lifecycle_record_mismatch")
    return record, raw


def activate(binding_id: str) -> dict[str, object]:
    if os.geteuid() != 0:
        raise StorageError("handoff_activate_requires_root")
    verified = verify(binding_id)
    if _read_lifecycle(binding_id, "START") is not None:
        raise StorageError("handoff_activation_already_attempted")
    binding, _binding_raw = _read_receipt(HANDOFF_BASE / binding_id / "BINDING.json")
    generation, generation_raw = _read_receipt(HANDOFF_BASE / binding_id / "GENERATION.json")
    start = {
        "schema": SCHEMA,
        "binding_id": binding_id,
        "generation_receipt_sha256": _sha256(generation_raw),
        "unit_name": binding["unit_name"],
        "unit_sha256": generation["unit_sha256"],
        "start_ns": time.time_ns(),
    }
    _write_receipt(_lifecycle_path(binding_id, "START"), start)
    try:
        subprocess.run(["systemctl", "start", str(binding["unit_name"])], check=True, timeout=30)
        status = linux_attachment._probe_status(
            str(binding["unit_name"]),
            {"port": 20000 + int(binding_id[:8], 16) % 40000},
        )
        unit_state = linux_service._unit_status(str(binding["unit_name"]))
        if unit_state[0] != "loaded" or unit_state[1] != "active" or unit_state[3] <= 0:
            raise StorageError("handoff_unit_not_active_after_probe")
        probe = {
            "schema": SCHEMA,
            "binding_id": binding_id,
            "generation_receipt_sha256": _sha256(generation_raw),
            "unit_name": binding["unit_name"],
            "unit_sha256": generation["unit_sha256"],
            "start_sha256": _sha256(_json_bytes(start)),
            "pid": unit_state[3],
            "status_schema": status["schema"],
            "mode": status["mode"],
            "phase": status["phase"],
            "jobs": status["jobs"],
            "probe_ns": time.time_ns(),
        }
        _write_receipt(_lifecycle_path(binding_id, "PROBE"), probe)
    except BaseException:
        subprocess.run(["systemctl", "stop", str(binding["unit_name"])], check=False, timeout=15)
        raise
    return {
        "state": "active-synthetic-idle",
        "binding_id": binding_id,
        "unit": binding["unit_name"],
        "pid": unit_state[3],
        "mode": status["mode"],
        "phase": status["phase"],
        "jobs": len(status["jobs"]),
        "finalize_required": True,
        "prepared_state_sha256": verified["logical_state_sha256"],
    }


def stop(binding_id: str) -> dict[str, object]:
    if os.geteuid() != 0:
        raise StorageError("handoff_stop_requires_root")
    if not HASH_RE.fullmatch(binding_id):
        raise StorageError("handoff_binding_id_invalid")
    binding, _binding_raw = _read_receipt(HANDOFF_BASE / binding_id / "BINDING.json")
    start_result = _read_lifecycle(binding_id, "START")
    probe_result = _read_lifecycle(binding_id, "PROBE")
    if start_result is None or probe_result is None:
        raise StorageError("handoff_activation_probe_missing")
    start, _start_raw = start_result
    probe, _probe_raw = probe_result
    if probe.get("start_sha256") != _sha256(_json_bytes(start)):
        raise StorageError("handoff_activation_probe_unbound")
    state = linux_service._unit_status(str(binding["unit_name"]))
    if state[0] != "loaded" or state[1] != "active" or state[3] != probe.get("pid"):
        raise StorageError("handoff_active_unit_mismatch")
    subprocess.run(["systemctl", "stop", str(binding["unit_name"])], check=True, timeout=30)
    state = linux_service._unit_status(str(binding["unit_name"]))
    if state != ("loaded", "inactive", "disabled", 0):
        raise StorageError("handoff_unit_not_stopped_disabled")
    return {"state": "stopped-disabled", "binding_id": binding_id, "unit": binding["unit_name"]}


def finalize(binding_id: str) -> dict[str, object]:
    if os.geteuid() != 0:
        raise StorageError("handoff_finalize_requires_root")
    if not HASH_RE.fullmatch(binding_id):
        raise StorageError("handoff_binding_id_invalid")
    binding, _binding_raw = _read_receipt(HANDOFF_BASE / binding_id / "BINDING.json")
    generation, generation_raw = _read_receipt(HANDOFF_BASE / binding_id / "GENERATION.json")
    if (
        binding.get("schema") != SCHEMA
        or generation.get("schema") != SCHEMA
        or binding.get("generation_sha256") != _sha256(generation_raw)
        or _handoff_identity(
            str(binding.get("installation_id")),
            str(binding.get("generation_id")),
            str(binding.get("target_release_id")),
        )
        != binding_id
    ):
        raise StorageError("handoff_binding_receipt_mismatch")
    start_result = _read_lifecycle(binding_id, "START")
    probe_result = _read_lifecycle(binding_id, "PROBE")
    if start_result is None or probe_result is None:
        raise StorageError("handoff_activation_probe_missing")
    start, start_raw = start_result
    probe, probe_raw = probe_result
    if (
        probe.get("start_sha256") != _sha256(start_raw)
        or _read_lifecycle(binding_id, "FINAL") is not None
    ):
        raise StorageError("handoff_activation_receipt_mismatch")
    if linux_service._unit_status(str(binding["unit_name"])) != (
        "loaded",
        "inactive",
        "disabled",
        0,
    ):
        raise StorageError("handoff_unit_not_stopped_disabled")
    generation_root, _receipt_root = _paths(
        str(binding["installation_id"]), str(binding["generation_id"]), binding_id
    )
    target = linux_release.verify(str(binding["target_release_id"]))
    unit_path = linux_service.UNIT_PATH.parent / str(binding["unit_name"])
    try:
        unit_bytes = unit_path.read_bytes()
    except OSError as exc:
        raise StorageError("handoff_unit_missing") from exc
    if target.get("manifest_sha256") != generation.get("target_release_manifest_sha256") or _sha256(
        unit_bytes
    ) != generation.get("unit_sha256"):
        raise StorageError("handoff_unit_pin_mismatch")
    manifest = {
        "files": {"config.json": {"sha256": generation["config_sha256"]}},
    }
    hashes, inventory = linux_attachment._verify_stopped_attachment_state(
        manifest, generation_root / "operator"
    )
    final_schema, final_logical = _logical_state(generation_root / "operator/operator.sqlite")
    if final_schema != generation.get("sqlite_schema_sha256"):
        raise StorageError("handoff_final_schema_mismatch")
    final = {
        "schema": SCHEMA,
        "binding_id": binding_id,
        "generation_receipt_sha256": _sha256(generation_raw),
        "unit_name": binding["unit_name"],
        "unit_sha256": generation["unit_sha256"],
        "start_sha256": _sha256(start_raw),
        "probe_sha256": _sha256(probe_raw),
        **hashes,
        "logical_state_sha256": final_logical,
        "inventory": inventory,
        "inventory_sha256": _sha256(linux_attachment._json_bytes(inventory)),
        "finalized_ns": time.time_ns(),
    }
    _write_receipt(_lifecycle_path(binding_id, "FINAL"), final)
    return {
        "state": "activated-stopped-idle-finalized",
        "binding_id": binding_id,
        "unit": binding["unit_name"],
        "operator_sqlite_sha256": hashes["operator_sqlite_sha256"],
        "logical_state_sha256": final["logical_state_sha256"],
        "inventory_sha256": final["inventory_sha256"],
        "prepared_state_sha256": generation["output_logical_state_sha256"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--installation-id", required=True)
    prepare_parser.add_argument("--source-attachment-id", required=True)
    prepare_parser.add_argument("--target-release-id", required=True)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--binding-id", required=True)
    activate_parser = sub.add_parser(
        "activate", help="explicitly start the verified bound generation"
    )
    activate_parser.add_argument("--binding-id", required=True)
    stop_parser = sub.add_parser("stop", help="stop an explicitly activated handoff unit")
    stop_parser.add_argument("--binding-id", required=True)
    finalize_parser = sub.add_parser("finalize", help="pin a stopped idle activation receipt")
    finalize_parser.add_argument("--binding-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(
                args.installation_id, args.source_attachment_id, args.target_release_id
            )
        elif args.command == "verify":
            result = verify(args.binding_id)
        elif args.command == "activate":
            result = activate(args.binding_id)
        elif args.command == "stop":
            result = stop(args.binding_id)
        else:
            result = finalize(args.binding_id)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (StorageError, OSError, RuntimeError, ValueError) as exc:
        message = str(exc)
        if not re.fullmatch(r"[a-z0-9_:-]+", message):
            message = "handoff_operation_failed"
        print(f"linux_handoff_error: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
