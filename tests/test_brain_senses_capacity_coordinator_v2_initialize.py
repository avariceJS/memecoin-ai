from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

import pytest

from memecoin_research_storage import StorageConfig, StorageError
from tools import brain_senses_capacity_coordinator_v2 as capacity
from tools import brain_senses_capacity_coordinator_v2_initialize as initializer
from tools.brain_senses_capacity_coordinator_v2_initialize import (
    ALLOCATION_ROOTS,
    DEFAULT_CONFIG_PATH,
    SERVICE_LOCK_PATHS,
    SERVICE_ROOTS,
    InitializationError,
    initialize_capacity_coordinator,
    main,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter


class _Guard:
    def __init__(self, config: StorageConfig, mount_fd: int, *, detach_after: int | None = None):
        self.config = config
        self.mount_fd = mount_fd
        self.root_id = config.root_id
        self.volume_uuid = config.volume_uuid
        self._detach_after = detach_after
        self._revalidations = 0

    def __enter__(self) -> _Guard:
        return self

    def __exit__(self, *_: object) -> None:
        os.close(self.mount_fd)

    def revalidate(self, **_: object) -> None:
        self._revalidations += 1
        if self._detach_after is not None and self._revalidations >= self._detach_after:
            raise StorageError("simulated detached volume")


class _Fixture:
    def __init__(self, base: Path):
        self.mount = base / "kingston"
        self.mount.mkdir(mode=0o700)
        self.data_root = self.mount / "BrainData"
        self.data_root.mkdir(mode=0o700)
        config_dir = base / "config"
        config_dir.mkdir(mode=0o700)
        self.config_path = config_dir / "storage-v1.json"
        self.config = StorageConfig(
            root_id="a" * 32,
            volume_uuid="12345678-1234-5678-9abc-def012345678",
            mount_path=self.mount,
            data_root="BrainData",
            min_free_bytes=0,
        )
        fd = os.open(
            self.config_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            payload = json.dumps(self.config.to_json(), sort_keys=True, separators=(",", ":"))
            os.write(fd, (payload + "\n").encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def factory(
        self, *, detach_after: int | None = None
    ) -> Callable[..., AbstractContextManager[_Guard]]:
        @contextmanager
        def open_guard(_path: Path, *, min_free_bytes: int | None = None) -> Iterator[_Guard]:
            assert min_free_bytes == self.config.min_free_bytes
            mount_fd = os.open(self.mount, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            with _Guard(self.config, mount_fd, detach_after=detach_after) as guard:
                yield guard

        return open_guard

    def writer(self) -> DescriptorWriter:
        root_fd = os.open(self.data_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        return DescriptorWriter(root_fd)


def test_first_init_is_descriptor_bound_and_matches_v5_paths(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    assert SERVICE_ROOTS == {
        "helius": "services/external-rolling-v5",
        "alchemy": "services/alchemy-sentinel-v2",
        "gecko": "services/gecko-market-v1",
    }
    assert ALLOCATION_ROOTS == {
        "helius": ("services/external-rolling-v4", "services/external-rolling-v5"),
        "alchemy": ("services/alchemy-sentinel-v2",),
        "gecko": ("services/gecko-market-v1",),
        "coordinator": (capacity.COORDINATOR_ROOT,),
    }
    result = initialize_capacity_coordinator(fixture.config_path, guard_factory=fixture.factory())
    assert result["initialized"] is True
    assert result["root_id"] == fixture.config.root_id
    assert result["volume_uuid"] == fixture.config.volume_uuid
    with fixture.writer() as writer:
        assert writer.read_bytes(capacity.POLICY_PATH, max_bytes=1_000_000)
        assert writer.read_bytes(capacity.LEDGER_PATH, max_bytes=64_000_000)
        for path in SERVICE_ROOTS.values():
            assert stat.S_ISDIR(writer.stat(path).st_mode)
        for path in SERVICE_LOCK_PATHS.values():
            assert writer.read_bytes(path, max_bytes=128) == b"capacity-service-lock-v1\n"
        assert writer.list_files() == sorted(
            [
                capacity.POLICY_PATH,
                capacity.LEDGER_PATH,
                capacity.LOCK_PATH,
                *SERVICE_LOCK_PATHS.values(),
            ]
        )


def test_init_is_idempotent_and_does_not_rewrite_policy_or_ledger(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    initialize_capacity_coordinator(fixture.config_path, guard_factory=fixture.factory())
    with fixture.writer() as writer:
        before = {
            path: writer.read_bytes(path, max_bytes=64_000_000)
            for path in (capacity.POLICY_PATH, capacity.LEDGER_PATH)
        }
    result = initialize_capacity_coordinator(fixture.config_path, guard_factory=fixture.factory())
    assert result["initialized"] is False
    with fixture.writer() as writer:
        after = {
            path: writer.read_bytes(path, max_bytes=64_000_000)
            for path in (capacity.POLICY_PATH, capacity.LEDGER_PATH)
        }
    assert after == before


def test_preexisting_helius_lock_is_reused_without_rewrite(
    tmp_path: Path,
) -> None:
    for index, legacy_lock in enumerate((b"", b"capacity-service-lock-v1\n")):
        case = tmp_path / str(index)
        case.mkdir()
        fixture = _Fixture(case)
        with fixture.writer() as writer:
            writer.create(SERVICE_LOCK_PATHS["helius"], legacy_lock)
        result = initialize_capacity_coordinator(
            fixture.config_path, guard_factory=fixture.factory()
        )
        assert result["initialized"] is True
        with fixture.writer() as writer:
            assert writer.read_bytes(SERVICE_LOCK_PATHS["helius"], max_bytes=128) == legacy_lock
            for service in ("alchemy", "gecko"):
                assert (
                    writer.read_bytes(SERVICE_LOCK_PATHS[service], max_bytes=128)
                    == b"capacity-service-lock-v1\n"
                )
        again = initialize_capacity_coordinator(
            fixture.config_path, guard_factory=fixture.factory()
        )
        assert again["initialized"] is False


def test_malformed_preexisting_helius_lock_is_rejected_without_repair(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    with fixture.writer() as writer:
        writer.create(SERVICE_LOCK_PATHS["helius"], b"unexpected\n")
    with pytest.raises(InitializationError, match="service_lock_content_mismatch"):
        initialize_capacity_coordinator(fixture.config_path, guard_factory=fixture.factory())
    with fixture.writer() as writer:
        assert writer.read_bytes(SERVICE_LOCK_PATHS["helius"], max_bytes=128) == b"unexpected\n"
        assert not writer.exists(capacity.POLICY_PATH)


def test_cli_uses_only_the_canonical_storage_config(monkeypatch: pytest.MonkeyPatch) -> None:
    assert DEFAULT_CONFIG_PATH == (
        Path.home() / "Developer/memecoin-runtime/brain/config/storage-v1.json"
    )
    with pytest.raises(SystemExit):
        main(["--config", "/tmp/alternate/storage-v1.json"])
    seen: list[Path] = []

    def fake_initialize(path: Path) -> dict[str, object]:
        seen.append(path)
        return {"policy_sha256": "pinned", "ledger_sha256": "ledger"}

    monkeypatch.setattr(initializer, "initialize_capacity_coordinator", fake_initialize)
    assert main([]) == 0
    assert seen == [DEFAULT_CONFIG_PATH]


def test_partial_policy_state_fails_before_creating_other_layout(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    with fixture.writer() as writer:
        writer.create(capacity.POLICY_PATH, b"partial\n")
    with pytest.raises(InitializationError, match="partial_coordinator_state"):
        initialize_capacity_coordinator(fixture.config_path, guard_factory=fixture.factory())
    with fixture.writer() as writer:
        assert writer.read_bytes(capacity.POLICY_PATH, max_bytes=128) == b"partial\n"
        assert not writer.exists(capacity.LEDGER_PATH)
        assert not writer.exists(SERVICE_LOCK_PATHS["helius"])


def test_existing_policy_binding_mismatch_is_not_rewritten(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    initialize_capacity_coordinator(fixture.config_path, guard_factory=fixture.factory())
    with fixture.writer() as writer:
        original = writer.read_bytes(capacity.POLICY_PATH, max_bytes=1_000_000)
        writer.atomic_write(
            capacity.POLICY_PATH,
            original.replace(b'"inode":', b'"inode":0,"bad":'),
        )
    with pytest.raises(InitializationError, match="coordinator_initialization_failed"):
        initialize_capacity_coordinator(fixture.config_path, guard_factory=fixture.factory())
    with fixture.writer() as writer:
        assert writer.read_bytes(capacity.POLICY_PATH, max_bytes=1_000_000) != original


def test_storage_guard_identity_mismatch_fails_without_writes(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)

    @contextmanager
    def mismatched(_path: Path, **_: Any) -> Iterator[_Guard]:
        mount_fd = os.open(fixture.mount, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        guard = _Guard(fixture.config, mount_fd)
        guard.root_id = "b" * 32
        with guard:
            yield guard

    with pytest.raises(InitializationError, match="storage_binding_mismatch"):
        initialize_capacity_coordinator(fixture.config_path, guard_factory=mismatched)
    assert list(fixture.data_root.iterdir()) == []


def test_detach_during_descriptor_write_fails_closed(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    with pytest.raises(InitializationError, match="coordinator_initialization_failed"):
        initialize_capacity_coordinator(
            fixture.config_path,
            guard_factory=fixture.factory(detach_after=2),
        )
    assert list(fixture.data_root.iterdir()) == []
    # Once the configured root is available again, the untouched initializer
    # can perform its one-shot create.
    result = initialize_capacity_coordinator(fixture.config_path, guard_factory=fixture.factory())
    assert result["initialized"] is True
