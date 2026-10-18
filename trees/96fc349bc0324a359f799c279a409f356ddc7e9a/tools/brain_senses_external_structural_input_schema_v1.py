"""Immutable schema for sampled structural inputs from sealed external-v2 frames."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Any

SCHEMA = "brain-senses-external-structural-input-v1"
PUBLICATION_SCHEMA = "brain-senses-external-structural-input-publication-v1"
FINGERPRINT_SCHEMA = "pump_program_structural_fingerprint_v1"
VERSION = 1
UTC_MINUTE_NS = 60_000_000_000
BOOTSTRAP_BUCKET = "20261012T060000Z"
FRAME_BUCKETS = tuple(
    f"20261012T{hour:02d}{minute:02d}00Z"
    for hour, minute in [(6, minute) for minute in range(5, 60, 5)] + [(7, 0)]
)
PUMP_PROGRAM_ID = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
TX_STATUSES = frozenset({"success", "failed", "unknown"})
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class StructuralInputError(ValueError):
    """Invalid source lineage or structural input."""


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def require_digest(value: Any, code: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise StructuralInputError(code)
    return value


def safe_relative(value: Any) -> str:
    if not isinstance(value, str) or "\x00" in value or "\\" in value:
        raise StructuralInputError("relative_path_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or str(path) != value:
        raise StructuralInputError("relative_path_invalid")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise StructuralInputError("relative_path_invalid")
    return value
