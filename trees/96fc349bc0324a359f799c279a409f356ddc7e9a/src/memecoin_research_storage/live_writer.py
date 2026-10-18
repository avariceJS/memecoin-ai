"""Fast identity validation for descriptor-bound live research writers.

This API is safe only when the caller keeps the root descriptor open and all
mutations remain relative to that descriptor. It does not replace a full
volume inspection at callback entry or immediately before a provider request.
"""

from __future__ import annotations

from . import ResolvedRoot, StorageGuard


def revalidate_descriptor_write(guard: StorageGuard) -> ResolvedRoot:
    """Validate the held root binding before one descriptor-relative mutation.

    No identity is cached between calls. A detach after this check can make the
    following descriptor-relative operation fail or land on the detached
    volume, but cannot redirect it to a replacement mountpoint underlay.
    """
    return guard.revalidate_identity()


__all__ = ["revalidate_descriptor_write"]
