"""Versioned contract for the offline Senses structural unknown queue."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Any

INPUT_SCHEMA = "brain-senses-structural-unknown-input-v1"
QUEUE_SCHEMA = "brain-senses-structural-unknown-queue-v1"
SCHEMA_VERSION = 1
RECOGNIZED_KINDS = frozenset({"buy", "buy_exact_quote_in", "sell"})
SUPPORTED_QUEUE_STATUSES = frozenset(
    {
        "unsupported_instruction",
        "recognized_but_semantics_unresolved",
        "candidate_parse_failure",
    }
)
SOURCE_FAILURE_CODES = frozenset(
    {
        "selected_block_fetch_failed",
        "capture_persistence_failed",
        "receipt_missing",
        "ambiguous_source_result",
        "candidate_parse_failed",
    }
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_BUCKET = re.compile(r"[0-9]{8}T[0-9]{6}Z\Z")
_SIGNATURE_HASH = re.compile(r"[0-9a-f]{64}\Z")


class QueueInputError(ValueError):
    """A malformed queue input or source-integrity failure."""


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def require_digest(value: Any, code: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise QueueInputError(code)
    return value


def safe_source_path(value: Any) -> str:
    if not isinstance(value, str) or "\x00" in value or "\\" in value:
        raise QueueInputError("source_path_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or str(path) != value:
        raise QueueInputError("source_path_invalid")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise QueueInputError("source_path_invalid")
    lowered = value.casefold()
    if any(token in lowered for token in ("tail2", "f01", "d43", "holdout")):
        raise QueueInputError("protected_or_excluded_source_path")
    return value


def require_bucket(value: Any) -> str:
    if not isinstance(value, str) or not _BUCKET.fullmatch(value):
        raise QueueInputError("bucket_identity_invalid")
    if value == "20261010T230000Z":
        raise QueueInputError("excluded_tail2_bucket")
    return value


def require_signature_hash(value: Any) -> str:
    if not isinstance(value, str) or not _SIGNATURE_HASH.fullmatch(value):
        raise QueueInputError("transaction_identity_invalid")
    return value
