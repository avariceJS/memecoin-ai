from __future__ import annotations

import contextlib
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from memecoin_ai.sources import ReadOnlyRPC
from memecoin_research_storage import StorageError
from tools import brain_senses_external_service_v1 as adapter


class FakeGuard:
    def __init__(self, mount: Path, *, fail_at: int | None = None) -> None:
        self.mount_fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
        self.config = SimpleNamespace(data_root="data")
        self.path = mount / "data"
        self.calls = 0
        self.fail_at = fail_at

    def revalidate(self) -> None:
        self.calls += 1
        if self.calls == self.fail_at:
            raise StorageError("volume_unplugged")

    def close(self) -> None:
        os.close(self.mount_fd)


class FakeRunner:
    def __init__(self, action: str) -> None:
        self.action = action
        self.calls = 0
        self.reserve_calls = 0

    def _default_client_loader(self) -> dict[str, Any]:
        return {}

    def run_tick(self, _root: Path, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        if self.action == "reserve":
            kwargs["service_api"].reserve_bucket("service-root")
        elif self.action == "fetch":
            clients = kwargs["client_loader"]()
            next(iter(clients.values())).fetch("getSlot", [])
        return {"status": "tick"}


class FakeService:
    def __init__(self) -> None:
        self.reserve_calls = 0

    def reserve_bucket(self, _root: str) -> None:
        self.reserve_calls += 1


class FakeClient:
    def __init__(self) -> None:
        self.fetch_calls = 0

    def fetch(self, _request: str) -> dict[str, str]:
        self.fetch_calls += 1
        return {"ok": "yes"}


def _service_tree(tmp_path: Path) -> tuple[Path, Path]:
    mount = tmp_path / "external"
    mount.mkdir(mode=0o700)
    data = mount / "data"
    services = data / "services"
    service_root = services / "svc1"
    service_root.mkdir(parents=True, mode=0o700)
    for path in (data, services, service_root):
        path.chmod(0o700)
    return mount, service_root


def _patch_adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    fail_at: int | None = None,
) -> tuple[FakeGuard, Path]:
    mount, service_root = _service_tree(tmp_path)
    guard = FakeGuard(mount, fail_at=fail_at)

    @contextlib.contextmanager
    def fake_writer_guard(_path: Path):
        try:
            yield guard
        finally:
            guard.close()

    monkeypatch.setattr(adapter, "writer_guard", fake_writer_guard)
    # Synthetic tests opt into the adapter path; live execution stays blocked by
    # the module-level release gate until detach safety is independently proven.
    monkeypatch.setattr(adapter, "EXTERNAL_WRITER_DETACH_QUALIFIED", True)
    monkeypatch.setattr(adapter, "_binding", lambda *args, **kwargs: {"binding": "fixed"})
    monkeypatch.setattr(adapter, "_check_adapter_binding", lambda *_args: True)
    monkeypatch.setattr(adapter, "_validate_runner_binding", lambda *_args: True)
    return guard, service_root


def _tick(monkeypatch: pytest.MonkeyPatch, runner: Any, **kwargs: Any) -> dict[str, Any]:
    return adapter.scheduled_tick(
        Path("/unused/config.json"),
        "services/svc1",
        service_id="svc1",
        plist_path=Path("/unused/adapter.plist"),
        gate_path=Path("/unused/gate.json"),
        predecessor_stop_path=Path("/unused/stop.json"),
        terminal_receipt_path=Path("/unused/receipt.json"),
        runner_module=runner,
        service_api=kwargs.pop("service_api"),
        client_loader=kwargs.pop("client_loader", None),
    )


@pytest.mark.parametrize(
    "relative_root", ["../services/svc1", "services/../svc1", "/services/svc1", "services/svc1/"]
)
def test_relative_root_rejects_noncanonical_paths(relative_root: str) -> None:
    with pytest.raises(StorageError, match="invalid_relative_root"):
        adapter.validate_relative_root(relative_root)


def test_initialize_is_blocked_before_external_mutation_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapter, "EXTERNAL_WRITER_DETACH_QUALIFIED", False)
    monkeypatch.setattr(
        adapter,
        "writer_guard",
        lambda *_args, **_kwargs: pytest.fail("writer guard reached while release gate is closed"),
    )

    with pytest.raises(StorageError, match="external_writer_detach_not_qualified"):
        adapter.initialize_offline(
            tmp_path / "binding.json",
            "services/svc1",
            service_id="svc1",
            plist_path=tmp_path / "service.plist",
            authorized_at_ns=1,
            expires_at_ns=2,
            predecessor_stop_path=tmp_path / "stop.json",
            terminal_receipt_path=tmp_path / "terminal.json",
        )

    assert list(tmp_path.iterdir()) == []


def test_scheduled_tick_is_blocked_before_guard_and_client_loading_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapter, "EXTERNAL_WRITER_DETACH_QUALIFIED", False)
    monkeypatch.setattr(
        adapter,
        "writer_guard",
        lambda *_args, **_kwargs: pytest.fail("writer guard reached while release gate is closed"),
    )
    client_loads = 0

    def load_clients() -> dict[str, Any]:
        nonlocal client_loads
        client_loads += 1
        return {"helius-free": FakeClient()}

    result = adapter.scheduled_tick(
        tmp_path / "binding.json",
        "services/svc1",
        service_id="svc1",
        plist_path=tmp_path / "service.plist",
        gate_path=tmp_path / "gate.json",
        predecessor_stop_path=tmp_path / "stop.json",
        terminal_receipt_path=tmp_path / "terminal.json",
        runner_module=FakeRunner("reserve"),
        client_loader=load_clients,
        service_api=FakeService(),
    )

    assert result == {"status": "no_rpc", "reason": "external_writer_detach_not_qualified"}
    assert client_loads == 0
    assert list(tmp_path.iterdir()) == []


def test_unplug_before_reservation_prevents_service_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_adapter(monkeypatch, tmp_path, fail_at=4)
    runner = FakeRunner("reserve")
    service_api = FakeService()

    result = _tick(monkeypatch, runner, service_api=service_api)

    assert result == {"status": "no_rpc", "reason": "volume_unplugged"}
    assert service_api.reserve_calls == 0


def test_unplug_before_fetch_prevents_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    guard, _ = _patch_adapter(monkeypatch, tmp_path, fail_at=5)
    runner = FakeRunner("fetch")
    client = FakeClient()

    def load_clients() -> dict[str, FakeClient]:
        return {"provider": client}

    result = _tick(
        monkeypatch,
        runner,
        service_api=FakeService(),
        client_loader=load_clients,
    )

    assert result == {"status": "no_rpc", "reason": "volume_unplugged"}
    assert guard.calls == 5
    assert client.fetch_calls == 0


def test_unplug_after_shared_start_gate_prevents_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    guard, _ = _patch_adapter(monkeypatch, tmp_path)
    runner = FakeRunner("fetch")
    transport_calls = 0

    def transport(*_args: Any) -> bytes:
        nonlocal transport_calls
        transport_calls += 1
        return b'{"jsonrpc":"2.0","id":1,"result":1}'

    client = ReadOnlyRPC(
        "https://rpc.example.test",
        "helius-free",
        transport=transport,
        shared_start_gate=Path("/unused/start-gate.json"),
    )
    gate_calls = 0

    def gate_then_unplug(_path: Path) -> None:
        nonlocal gate_calls
        gate_calls += 1
        guard.fail_at = guard.calls + 1

    monkeypatch.setattr("memecoin_ai.sources._reserve_shared_request_start", gate_then_unplug)

    result = _tick(
        monkeypatch,
        runner,
        service_api=FakeService(),
        client_loader=lambda: {"helius-free": client},
    )

    assert result == {"status": "no_rpc", "reason": "volume_unplugged"}
    assert gate_calls == 1
    assert transport_calls == 0
    assert client.transport is transport


def test_changed_adapter_binding_stops_before_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(adapter, "_check_adapter_binding", lambda *_args: False)
    runner = FakeRunner("reserve")
    service_api = FakeService()

    result = _tick(monkeypatch, runner, service_api=service_api)

    assert result == {"status": "no_rpc", "reason": "adapter_binding_missing_or_changed"}
    assert runner.calls == 0
    assert service_api.reserve_calls == 0


def test_symlinked_service_directory_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _guard, service_root = _patch_adapter(monkeypatch, tmp_path)
    services = service_root.parent
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    service_root.rmdir()
    services.rmdir()
    services.symlink_to(outside, target_is_directory=True)
    runner = FakeRunner("reserve")
    service_api = FakeService()

    result = _tick(monkeypatch, runner, service_api=service_api)

    assert result["status"] == "no_rpc"
    assert runner.calls == 0
    assert service_api.reserve_calls == 0


def test_storage_check_is_read_only_and_reports_root_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mount, _ = _service_tree(tmp_path)
    root_path = mount / "data"
    mount_stat = mount.stat()
    resolved = adapter.ResolvedRoot(
        root_id="a" * 32,
        volume_uuid="12345678-1234-1234-1234-123456789abc",
        mount_path=mount,
        path=root_path,
        available_bytes=1024,
        mount_device=mount_stat.st_dev,
        mount_inode=mount_stat.st_ino,
    )
    config = adapter.StorageConfig(
        root_id=resolved.root_id,
        volume_uuid=resolved.volume_uuid,
        mount_path=mount,
        data_root="data",
        min_free_bytes=0,
    )
    monkeypatch.setattr(adapter, "resolve", lambda *_args, **_kwargs: resolved)
    monkeypatch.setattr(adapter, "verify_root", lambda *_args, **_kwargs: resolved)
    monkeypatch.setattr(
        adapter.StorageConfig,
        "load",
        classmethod(lambda cls, _path: config),
    )
    before = sorted(str(path.relative_to(mount)) for path in mount.rglob("*"))

    result = adapter.storage_check(Path("/unused/config.json"), "services/svc1")

    after = sorted(str(path.relative_to(mount)) for path in mount.rglob("*"))
    assert result["status"] == "volume_preconditions_passed"
    assert result["provider_io"] is False
    assert result["service_root_initialized"] is True
    assert after == before


def test_readonly_guard_ignores_free_space_changes_but_honors_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mount, _ = _service_tree(tmp_path)
    mount_stat = mount.stat()
    config = adapter.StorageConfig(
        root_id="a" * 32,
        volume_uuid="12345678-1234-1234-1234-123456789abc",
        mount_path=mount,
        data_root="data",
        min_free_bytes=500,
    )
    initial = adapter.ResolvedRoot(
        root_id=config.root_id,
        volume_uuid=config.volume_uuid,
        mount_path=mount,
        path=mount / config.data_root,
        available_bytes=1_000,
        mount_device=mount_stat.st_dev,
        mount_inode=mount_stat.st_ino,
    )
    current = initial

    def verify(_path: Path) -> adapter.ResolvedRoot:
        if current.available_bytes < config.min_free_bytes:
            raise StorageError("insufficient_free_space")
        return current

    monkeypatch.setattr(adapter, "verify_root", verify)
    monkeypatch.setattr(
        adapter.StorageConfig,
        "load",
        classmethod(lambda cls, _path: config),
    )
    guard = adapter._ReadOnlyGuard(Path("/unused/config.json"), initial)
    try:
        current = adapter.ResolvedRoot(**{**initial.__dict__, "available_bytes": 800})
        assert guard.revalidate().available_bytes == 800

        current = adapter.ResolvedRoot(**{**initial.__dict__, "available_bytes": 400})
        with pytest.raises(StorageError, match="insufficient_free_space"):
            guard.revalidate()
    finally:
        guard.close()
