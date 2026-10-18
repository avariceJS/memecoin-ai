"""One-shot importer and descriptor-relative reader for frozen Gecko train sources.

This module copies only the fixed plan and population-selection metadata files from
the repository checkout into the configured research root. It never reads sample
payloads and grants no provider or runtime authority.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

SCHEMA = "brain-senses-gecko-train-source-binding-v2"
SCHEMA_VERSION = 2
SOURCE_HEAD = "8c0c501c4f2fdcf4ffaed30d6bd5e4a8aae75894"
SOURCE_ROOT = Path(__file__).resolve().parents[1]
BINDING_DIR = "brain/source-pins/gecko-train-four-v3"
MANIFEST_PATH = f"{BINDING_DIR}/source-binding-v2.json"
PLAN_DESTINATION = f"{BINDING_DIR}/plan.json"
SELECTION_DESTINATION = f"{BINDING_DIR}/population-selection.json"


class SourceFile(TypedDict):
    name: str
    source_path: str
    destination_path: str
    size_bytes: int
    sha256: str


SOURCE_FILES: tuple[SourceFile, ...] = (
    {
        "name": "plan",
        "source_path": "work/brain-diverse-campaign-20261008/plan.json",
        "destination_path": PLAN_DESTINATION,
        "size_bytes": 332298,
        "sha256": "d872e0d8d8266b2dd22b1c2a8ef7e505a12b6f93ccdf82df9b7b6f206d7e7de8",
    },
    {
        "name": "population_selection",
        "source_path": "work/brain-diverse-campaign-20261008/population-selection.json",
        "destination_path": SELECTION_DESTINATION,
        "size_bytes": 69881,
        "sha256": "4aa13b951e11569bb893672c595783482ee7a987f57b26c93fd601150de39172",
    },
)


class SourcePinError(RuntimeError):
    """A source or descriptor-bound import does not match its immutable pins."""


@dataclass(frozen=True)
class PinnedSources:
    plan_bytes: bytes
    selection_bytes: bytes
    manifest_bytes: bytes
    manifest_sha256: str


def _strict_json(raw: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise SourcePinError("source_manifest_duplicate_key")
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourcePinError("source_manifest_invalid_json") from exc


def _module_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _read_local_source(source_root: Path, entry: SourceFile) -> bytes:
    relative = Path(entry["source_path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise SourcePinError("source_path_invalid")
    path = source_root / relative
    try:
        root_stat = source_root.lstat()
        if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
            raise SourcePinError("source_root_invalid")
        current = source_root
        for component in relative.parts[:-1]:
            current = current / component
            component_stat = current.lstat()
            if not stat.S_ISDIR(component_stat.st_mode) or stat.S_ISLNK(component_stat.st_mode):
                raise SourcePinError("source_component_invalid")
        before = path.lstat()
    except OSError as exc:
        raise SourcePinError("source_missing_or_unreadable") from exc
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise SourcePinError("source_not_regular")
    if before.st_size != entry["size_bytes"]:
        raise SourcePinError("source_pin_mismatch")
    try:
        raw = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise SourcePinError("source_read_failed") from exc
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise SourcePinError("source_changed_during_read")
    if len(raw) != entry["size_bytes"] or sha256(raw) != entry["sha256"]:
        raise SourcePinError("source_pin_mismatch")
    return raw


def import_source_pins(
    writer: DescriptorWriter,
    *,
    root_id: str,
    volume_uuid: str,
    source_root: Path = SOURCE_ROOT,
) -> dict[str, Any]:
    """Import the two fixed metadata files and publish their binding last.

    Existing files always fail closed. A partial import is intentionally not
    repaired in place; an operator must inspect it before using a fresh root.
    """
    if not re.fullmatch(r"[0-9a-f]{32}", root_id):
        raise SourcePinError("root_identity_invalid")
    normalized_uuid = volume_uuid.lower()
    if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", normalized_uuid):
        raise SourcePinError("volume_identity_invalid")
    raw_files = [(entry, _read_local_source(source_root, entry)) for entry in SOURCE_FILES]
    module_digest = _module_sha256()

    try:
        writer.stat(BINDING_DIR)
    except FileNotFoundError:
        pass
    else:
        raise SourcePinError("source_binding_destination_exists")
    writer.mkdir(BINDING_DIR)
    entries: list[dict[str, Any]] = []
    for entry, raw in raw_files:
        writer.create(entry["destination_path"], raw)
        persisted = writer.read_bytes(entry["destination_path"], max_bytes=entry["size_bytes"])
        if len(persisted) != entry["size_bytes"] or sha256(persisted) != entry["sha256"]:
            raise SourcePinError("descriptor_readback_mismatch")
        entries.append(
            {
                "name": entry["name"],
                "source_path": entry["source_path"],
                "destination_path": entry["destination_path"],
                "size_bytes": len(persisted),
                "sha256": sha256(persisted),
            }
        )

    manifest = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "source_input_origin_commit": SOURCE_HEAD,
        "root_id": root_id,
        "volume_uuid": normalized_uuid,
        "importer_sha256": module_digest,
        "files": entries,
    }
    manifest_raw = canonical_json(manifest)
    writer.create(MANIFEST_PATH, manifest_raw)
    readback = writer.read_bytes(MANIFEST_PATH, max_bytes=16_384)
    if readback != manifest_raw:
        raise SourcePinError("manifest_readback_mismatch")
    return manifest


def verify_source_binding(
    writer: DescriptorWriter,
    *,
    expected_root_id: str,
    expected_volume_uuid: str,
    expected_manifest_sha256: str | None = None,
) -> PinnedSources:
    """Read and validate manifest and both data files through the held descriptor."""
    try:
        manifest_raw = writer.read_bytes(MANIFEST_PATH, max_bytes=16_384)
    except Exception as exc:
        raise SourcePinError("manifest_unavailable") from exc
    manifest_digest = sha256(manifest_raw)
    if expected_manifest_sha256 is not None and manifest_digest != expected_manifest_sha256:
        raise SourcePinError("manifest_digest_mismatch")
    manifest = _strict_json(manifest_raw)
    if canonical_json(manifest) != manifest_raw:
        raise SourcePinError("manifest_not_canonical")
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema",
        "schema_version",
        "source_input_origin_commit",
        "root_id",
        "volume_uuid",
        "importer_sha256",
        "files",
    }:
        raise SourcePinError("manifest_shape")
    if (
        manifest["schema"] != SCHEMA
        or manifest["schema_version"] != SCHEMA_VERSION
        or manifest["source_input_origin_commit"] != SOURCE_HEAD
        or manifest["root_id"] != expected_root_id
        or manifest["volume_uuid"] != expected_volume_uuid.lower()
        or manifest["importer_sha256"] != _module_sha256()
        or manifest["files"]
        != [
            {
                "name": item["name"],
                "source_path": item["source_path"],
                "destination_path": item["destination_path"],
                "size_bytes": item["size_bytes"],
                "sha256": item["sha256"],
            }
            for item in SOURCE_FILES
        ]
    ):
        raise SourcePinError("manifest_binding_mismatch")
    payloads: list[bytes] = []
    for entry in SOURCE_FILES:
        try:
            payload = writer.read_bytes(entry["destination_path"], max_bytes=entry["size_bytes"])
        except Exception as exc:
            raise SourcePinError("imported_source_unavailable") from exc
        if len(payload) != entry["size_bytes"] or sha256(payload) != entry["sha256"]:
            raise SourcePinError("imported_source_mismatch")
        payloads.append(payload)
    return PinnedSources(
        plan_bytes=payloads[0],
        selection_bytes=payloads[1],
        manifest_bytes=manifest_raw,
        manifest_sha256=manifest_digest,
    )
