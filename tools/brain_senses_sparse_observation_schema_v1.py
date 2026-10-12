"""Canonical schema helpers for historical sparse observation references."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

ROLE_SCHEMA = "brain-senses-sparse-observation-role-v1"
CAMPAIGN_SCHEMA = "brain-senses-sparse-observation-campaign-v1"
SCHEMA_VERSION = 1
BAR_SECONDS = 300
NS_PER_SECOND = 1_000_000_000
MAX_HISTORY = 32
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
ROLES = ("train", "research_evaluation")


class SparseReferenceError(ValueError):
    """Malformed, mismatched, or unsafe sparse-reference source."""


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def require_sha256(value: Any, code: str = "invalid_sha256") -> str:
    if not isinstance(value, str) or not HEX_SHA256.fullmatch(value):
        raise SparseReferenceError(code)
    return value
