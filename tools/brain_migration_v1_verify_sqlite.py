#!/usr/bin/env python3
"""Read-only structural verification of ordinary Migration v1 SQLite state."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import stat
import sys
import time
from pathlib import Path, PurePosixPath
from urllib.parse import quote


class SQLiteVerificationError(RuntimeError):
    """Stable, operator-facing fail-closed verification error."""


DB_SUFFIXES = (".sqlite", ".sqlite3", ".db")
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
PROTECTED_PARTS = ("f01", "d43", "holdout", "reserved")


def _manifest(path: Path, expected_sha256: str) -> tuple[dict, list[dict]]:
    digest = hashlib.sha256()
    rows: list[dict] = []
    header: dict | None = None
    try:
        with path.open("rb") as stream:
            for line in stream:
                digest.update(line)
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise SQLiteVerificationError("manifest_record_invalid")
                if value.get("record_type") == "header":
                    if header is not None:
                        raise SQLiteVerificationError("manifest_header_duplicate")
                    header = value
                elif value.get("record_type") == "member":
                    rows.append(value)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SQLiteVerificationError("manifest_read_failed") from exc
    if digest.hexdigest() != expected_sha256:
        raise SQLiteVerificationError("manifest_sha256_mismatch")
    if header is None or header.get("schema") != "brain-practical-migration-v1":
        raise SQLiteVerificationError("manifest_schema_invalid")
    if header.get("candidate_status") != "complete":
        raise SQLiteVerificationError("candidate_not_complete")
    return header, rows


def _safe_member(payload: Path, alias: str, relative: str) -> Path:
    rel = PurePosixPath(relative)
    if not rel.parts or rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise SQLiteVerificationError("manifest_path_invalid")
    components = (alias, *rel.parts)
    path = payload
    for index, component in enumerate(components):
        path = path / component
        try:
            st = path.lstat()
        except OSError as exc:
            raise SQLiteVerificationError("database_member_missing") from exc
        if stat.S_ISLNK(st.st_mode):
            raise SQLiteVerificationError("database_path_symlink_rejected")
        if index < len(components) - 1 and not stat.S_ISDIR(st.st_mode):
            raise SQLiteVerificationError("database_parent_not_directory")
    if not stat.S_ISREG(st.st_mode):
        raise SQLiteVerificationError("database_member_not_regular")
    return path


def _candidate_database_rows(rows: list[dict]) -> tuple[list[dict], set[tuple[str, str]]]:
    files: set[tuple[str, str]] = set()
    databases: list[dict] = []
    for row in rows:
        if row.get("type") != "file":
            continue
        alias, relative = row.get("alias"), row.get("path")
        if not isinstance(alias, str) or not isinstance(relative, str):
            raise SQLiteVerificationError("manifest_member_invalid")
        key = (alias, relative)
        parts = PurePosixPath(relative).parts
        is_protected = row.get("custody_class") != "ordinary_opaque" or any(
            part_token in part.casefold() for part in parts for part_token in PROTECTED_PARTS
        )
        if is_protected:
            continue
        files.add(key)
        if relative.casefold().endswith(DB_SUFFIXES):
            databases.append(row)

    database_keys = {(row["alias"], row["path"]) for row in databases}
    for alias, relative in files:
        for suffix in SIDECAR_SUFFIXES:
            if relative.casefold().endswith(suffix):
                base = relative[: -len(suffix)]
                if (alias, base) not in database_keys:
                    raise SQLiteVerificationError("sqlite_sidecar_without_verified_main")
    if not databases:
        raise SQLiteVerificationError("no_ordinary_sqlite_databases_found")
    return databases, files


def _hash_member(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
                size += len(block)
    except OSError as exc:
        raise SQLiteVerificationError("database_family_read_failed") from exc
    return size, digest.hexdigest()


def verify_sqlite(
    *, payload_root: Path, manifest_path: Path, expected_manifest_sha256: str
) -> dict:
    if not payload_root.is_absolute() or not payload_root.is_dir():
        raise SQLiteVerificationError("payload_root_invalid")
    header, rows = _manifest(manifest_path, expected_manifest_sha256)
    if header.get("candidate_id") is None:
        raise SQLiteVerificationError("candidate_identity_missing")
    databases, files = _candidate_database_rows(rows)
    ordinary_files = {
        (row["alias"], row["path"]): row
        for row in rows
        if row.get("record_type") == "member"
        and row.get("type") == "file"
        and row.get("custody_class") == "ordinary_opaque"
        and not any(
            token in part.casefold()
            for part in PurePosixPath(row["path"]).parts
            for token in PROTECTED_PARTS
        )
    }
    families: list[list[dict]] = []
    for row in databases:
        family = [row]
        for suffix in SIDECAR_SUFFIXES:
            sidecar = ordinary_files.get((row["alias"], row["path"] + suffix))
            if sidecar is not None:
                family.append(sidecar)
        families.append(family)
    started = time.monotonic()
    checked = 0
    for row, family in zip(databases, families, strict=True):
        alias, relative = row["alias"], row["path"]
        path = _safe_member(payload_root, alias, relative)
        family_paths = [
            _safe_member(payload_root, member["alias"], member["path"]) for member in family
        ]
        before = [_hash_member(member_path) for member_path in family_paths]
        expected = [(member["size_bytes"], member["sha256"]) for member in family]
        if before != expected:
            raise SQLiteVerificationError("restored_sqlite_family_digest_mismatch")
        uri = "file:" + quote(str(path), safe="/") + "?mode=ro&immutable=1"
        try:
            connection = sqlite3.connect(uri, uri=True)
            try:
                result = [str(value[0]) for value in connection.execute("PRAGMA integrity_check")]
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise SQLiteVerificationError(
                f"sqlite_open_or_integrity_check_failed:{alias}/{relative}"
            ) from exc
        if result != ["ok"]:
            raise SQLiteVerificationError(f"sqlite_integrity_check_failed:{alias}/{relative}")
        after = [_hash_member(member_path) for member_path in family_paths]
        if after != before:
            raise SQLiteVerificationError("sqlite_readonly_check_mutated_family")
        checked += 1
    return {
        "state": "ordinary_sqlite_integrity_verified",
        "candidate_id": header["candidate_id"],
        "manifest_sha256": expected_manifest_sha256,
        "ordinary_database_count": checked,
        "protected_database_semantics_checked": False,
        "restore_payload_mutated": False,
        "sqlite_family_hashes_unchanged": True,
        "sqlite_version": sqlite3.sqlite_version,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    try:
        result = verify_sqlite(
            payload_root=args.payload_root,
            manifest_path=args.manifest,
            expected_manifest_sha256=args.manifest_sha256,
        )
    except SQLiteVerificationError as exc:
        print(json.dumps({"state": "failed_closed", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
