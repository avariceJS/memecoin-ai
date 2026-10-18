#!/usr/bin/env python3
"""One-shot descriptor-bound initializer for shared capacity coordinator v2.

This command reads only the configured ``storage-v1.json`` binding. It creates
coordinator state beneath that binding through DescriptorWriter and never
repairs a partial initialization.
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from memecoin_research_storage import (  # noqa: E402
    StorageConfig,
    StorageError,
    _open_relative_directory,
    writer_guard,
)
from tools import brain_senses_capacity_coordinator_v2 as capacity  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402

DEFAULT_CONFIG_PATH = Path.home() / ".config/brain-research/storage-v1.json"

# These paths match the v5 orchestrator's coordinator wiring. Keep this
# initializer independent of importing the runtime (which loads provider code).
SERVICE_ROOTS = {
    "helius": "services/external-rolling-v5",
    "alchemy": "services/alchemy-sentinel-v2",
    "gecko": "services/gecko-market-v1",
}
ALLOCATION_ROOTS = {
    "helius": ("services/external-rolling-v4", "services/external-rolling-v5"),
    "alchemy": ("services/alchemy-sentinel-v2",),
    "gecko": ("services/gecko-market-v1",),
    "coordinator": (capacity.COORDINATOR_ROOT,),
}
SERVICE_LOCK_PATHS = {
    "helius": ".external-rolling-v4.lock",
    "alchemy": ".alchemy-sentinel-v2.lock",
    "gecko": ".gecko-market-v1.lock",
}


class InitializationError(RuntimeError):
    """Fail-closed root, state, or storage-binding error."""


GuardFactory = Callable[..., AbstractContextManager[Any]]


def _private_directory(root_fd: int) -> tuple[int, os.stat_result]:
    st = os.fstat(root_fd)
    if (
        not stat.S_ISDIR(st.st_mode)
        or st.st_uid != os.getuid()
        or stat.S_IMODE(st.st_mode) != 0o700
    ):
        raise InitializationError("unsafe_configured_data_root")
    return st.st_dev, st


def _configured_state_paths() -> tuple[str, ...]:
    # Directories can legitimately predate this initializer (the v4 root and
    # potentially the new lane roots), so partial-state detection is based on
    # the durable coordinator and lock artifacts. The coordinator still makes
    # and inventories the complete configured directory set.
    return (
        capacity.POLICY_PATH,
        capacity.LEDGER_PATH,
        capacity.LOCK_PATH,
        *SERVICE_LOCK_PATHS.values(),
    )


def _path_exists(writer: DescriptorWriter, relative: str) -> bool:
    return bool(writer.exists(relative))


def _verify_helius_lock(writer: DescriptorWriter, *, allow_legacy: bool) -> None:
    helius_path = SERVICE_LOCK_PATHS["helius"]
    helius_payload = writer.read_bytes(helius_path, max_bytes=128)
    allowed_helius = {b"capacity-service-lock-v1\n"}
    if allow_legacy:
        allowed_helius.add(b"")
    if helius_payload not in allowed_helius:
        raise InitializationError("service_lock_content_mismatch")


def _verify_service_locks(writer: DescriptorWriter) -> None:
    _verify_helius_lock(writer, allow_legacy=True)
    for service, path in SERVICE_LOCK_PATHS.items():
        if service == "helius":
            continue
        if writer.read_bytes(path, max_bytes=128) != b"capacity-service-lock-v1\n":
            raise InitializationError("service_lock_content_mismatch")


def _build_coordinator(
    writer: DescriptorWriter,
    guard: Any,
    root_binding: Mapping[str, object],
    root_identity: tuple[int, int],
) -> capacity.CapacityCoordinator:
    def verify_root(_writer: capacity.Writer) -> None:
        guard.revalidate()
        st = os.fstat(writer.root_fd)
        if (st.st_dev, st.st_ino) != root_identity:
            raise InitializationError("configured_root_descriptor_changed")
        if (
            guard.root_id != root_binding["root_id"]
            or guard.volume_uuid.lower() != root_binding["volume_uuid"]
        ):
            raise InitializationError("configured_root_binding_changed")

    source = capacity.DescriptorCapacitySource(
        allocation_roots=ALLOCATION_ROOTS,
        verify_root=verify_root,
    )
    return capacity.CapacityCoordinator(
        writer,
        source=source,
        service_roots=SERVICE_ROOTS,
        service_lock_paths=SERVICE_LOCK_PATHS,
        allocation_roots=ALLOCATION_ROOTS,
        root_binding=dict(root_binding),
        now_ns=time.time_ns,
    )


def initialize_capacity_coordinator(
    config_path: Path = DEFAULT_CONFIG_PATH,
    *,
    guard_factory: GuardFactory = writer_guard,
) -> dict[str, object]:
    """Create coordinator v2 once under the configured Kingston root.

    ``guard_factory`` exists for isolated tests. Production uses writer_guard;
    no mount path is opened directly and no alternate storage root is accepted.
    """
    if not config_path.is_absolute() or config_path.name != "storage-v1.json":
        raise InitializationError("storage_v1_config_required")
    try:
        config = StorageConfig.load(config_path)
        with guard_factory(config_path, min_free_bytes=config.min_free_bytes) as guard:
            if guard.config != config or guard.root_id != config.root_id:
                raise InitializationError("storage_binding_mismatch")
            if guard.volume_uuid.lower() != config.volume_uuid.lower():
                raise InitializationError("storage_volume_mismatch")
            root_fd = _open_relative_directory(guard.mount_fd, config.data_root)
            try:
                device, root_stat = _private_directory(root_fd)
                root_identity = (root_stat.st_dev, root_stat.st_ino)
                binding: dict[str, object] = {
                    "root_id": guard.root_id,
                    "volume_uuid": guard.volume_uuid.lower(),
                    "device": device,
                    "inode": root_stat.st_ino,
                }

                def prewrite() -> None:
                    guard.revalidate()
                    current = os.fstat(root_fd)
                    if (current.st_dev, current.st_ino) != root_identity:
                        raise InitializationError("configured_root_descriptor_changed")

                with DescriptorWriter(root_fd, before_write=prewrite) as writer:
                    guard.revalidate()
                    current = os.fstat(writer.root_fd)
                    if (current.st_dev, current.st_ino) != root_identity:
                        raise InitializationError("configured_root_descriptor_changed")
                    coordinator = _build_coordinator(writer, guard, binding, root_identity)

                    existing = {
                        path: _path_exists(writer, path) for path in _configured_state_paths()
                    }
                    present = {path for path, found in existing.items() if found}
                    helius_lock = SERVICE_LOCK_PATHS["helius"]
                    legacy_only = present == {helius_lock}
                    if legacy_only:
                        _verify_helius_lock(writer, allow_legacy=True)
                    elif present and len(present) != len(existing):
                        raise InitializationError("partial_coordinator_state")
                    if present:
                        if not legacy_only:
                            _verify_service_locks(writer)
                        if (
                            not legacy_only
                            and writer.read_bytes(capacity.LOCK_PATH, max_bytes=1) != b""
                        ):
                            raise InitializationError("coordinator_lock_content_mismatch")
                    coordinator.initialize()
                    # Reopen policy/ledger through the descriptor after init;
                    # initialize() verifies exact policy and the ledger chain.
                    policy_bytes = writer.read_bytes(capacity.POLICY_PATH, max_bytes=1_000_000)
                    ledger_bytes = writer.read_bytes(capacity.LEDGER_PATH, max_bytes=64_000_000)
                    return {
                        "root_id": guard.root_id,
                        "volume_uuid": guard.volume_uuid.lower(),
                        "device": device,
                        "inode": root_stat.st_ino,
                        "policy_sha256": capacity.sha256(policy_bytes),
                        "ledger_sha256": capacity.sha256(ledger_bytes),
                        "initialized": not bool(present - {helius_lock}),
                    }
            finally:
                os.close(root_fd)
    except (StorageError, capacity.CoordinatorError, OSError) as exc:
        raise InitializationError("coordinator_initialization_failed") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    result = initialize_capacity_coordinator(DEFAULT_CONFIG_PATH)
    print(
        "capacity_coordinator_v2_ready "
        f"policy_sha256={result['policy_sha256']} "
        f"ledger_sha256={result['ledger_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
