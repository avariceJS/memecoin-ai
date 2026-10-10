"""Bounded reader for owner-only local configuration files."""

from __future__ import annotations

import os
import stat
from pathlib import Path

MAX_PRIVATE_CONFIG_BYTES = 16 * 1024


class PrivateConfigError(ValueError):
    """Sanitized private-file failure; never includes path or file contents."""


def read_private_config(path: Path, *, max_bytes: int = MAX_PRIVATE_CONFIG_BYTES) -> bytes:
    """Read a stable, owner-only regular file using one no-follow descriptor."""
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_PRIVATE_CONFIG_BYTES:
        raise PrivateConfigError("private_config_limit_invalid")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise PrivateConfigError("private_config_no_follow_unavailable")
    flags = os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise PrivateConfigError("private_config_unavailable") from None
    try:
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_uid != os.getuid()
                or before.st_nlink != 1
            ):
                raise PrivateConfigError("private_config_permissions_invalid")
            if before.st_size > max_bytes:
                raise PrivateConfigError("private_config_too_large")
            chunks = bytearray()
            while len(chunks) <= max_bytes:
                part = os.read(fd, min(4096, max_bytes + 1 - len(chunks)))
                if not part:
                    break
                chunks.extend(part)
            if len(chunks) > max_bytes:
                raise PrivateConfigError("private_config_too_large")
            after = os.fstat(fd)
            identity_before = (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_uid,
                before.st_nlink,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            identity_after = (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_uid,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            if identity_before != identity_after or len(chunks) != after.st_size:
                raise PrivateConfigError("private_config_changed_during_read")
            return bytes(chunks)
        except PrivateConfigError:
            raise
        except OSError:
            raise PrivateConfigError("private_config_io_failed") from None
    finally:
        try:
            os.close(fd)
        except OSError:
            raise PrivateConfigError("private_config_close_failed") from None
