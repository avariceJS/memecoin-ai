"""Bounded, no-follow local reads. Never accept paths from HTTP clients."""

import hashlib
import json
import os
import stat
from pathlib import Path

LIMIT = 512 * 1024


class Refused(ValueError):
    pass


def read_bytes(root: Path, relative: str, expected_sha: str | None = None) -> bytes:
    parts = relative.split("/")
    if not parts or any(p in ("", ".", "..") for p in parts):
        raise Refused("unsafe_path")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in parts[:-1]:
            new_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new_fd
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            before = os.fstat(leaf)
            if not stat.S_ISREG(before.st_mode) or before.st_size > LIMIT:
                raise Refused("unsupported_or_oversized_file")
            with os.fdopen(leaf, "rb", closefd=False) as stream:
                data = stream.read(LIMIT + 1)
            after = os.fstat(leaf)
            if len(data) > LIMIT or (before.st_size, before.st_mtime_ns) != (
                after.st_size,
                after.st_mtime_ns,
            ):
                raise Refused("unstable_or_oversized_file")
        finally:
            os.close(leaf)
    except OSError as exc:
        raise Refused("file_unavailable") from exc
    finally:
        os.close(fd)
    if expected_sha is not None and hashlib.sha256(data).hexdigest() != expected_sha:
        raise Refused("digest_mismatch")
    return data


def json_bytes(data: bytes):
    def reject_constant(value):
        raise Refused("nonfinite_json")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise Refused("duplicate_json_key")
            result[key] = value
        return result

    return json.loads(data, parse_constant=reject_constant, object_pairs_hook=unique)
