#!/usr/bin/env python3
"""External-storage adapter for the finite Senses service-v1 runner.

This adapter requires an initialized UUID-bound research root. It does not
activate a LaunchAgent or synthesize service authorization evidence.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import hashlib
import json
import os
import stat
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memecoin_ai.sources import ReadOnlyRPC  # noqa: E402
from memecoin_research_storage import (  # noqa: E402
    ResolvedRoot,
    StorageConfig,
    StorageError,
    StorageGuard,
    resolve,
    verify_root,
    writer_guard,
)
from tools import brain_senses_block_epoch_v3_schedule as schedule_v3  # noqa: E402
from tools import brain_senses_service_v1 as service  # noqa: E402
from tools import brain_senses_service_v1_runner as runner  # noqa: E402

ADAPTER_BINDING = "external-adapter-binding-v1.json"
BINDING_VERSION = 1


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_regular_file(path: Path) -> bytes:
    if not path.is_absolute() or "\x00" in str(path):
        raise StorageError("invalid_binding_source_path")
    parent_fd = _open_absolute_directory(path.parent)
    try:
        fd = os.open(
            path.name,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    finally:
        os.close(parent_fd)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise StorageError("binding_source_not_regular_file")
        chunks: list[bytes] = []
        while chunk := os.read(fd, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(fd)
        if _stat_identity(before) != _stat_identity(after):
            raise StorageError("binding_source_changed_during_read")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _stat_identity(st: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        st.st_dev,
        st.st_ino,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
        st.st_mode,
        st.st_uid,
    )


def _open_absolute_directory(path: Path) -> int:
    if not path.is_absolute() or "\x00" in str(path) or ".." in path.parts:
        raise StorageError("unsafe_binding_source_path")
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except OSError:
        os.close(fd)
        raise


def _binding(
    config_path: Path,
    *,
    relative_root: str,
    service_id: str,
    plist_path: Path,
) -> dict[str, Any]:
    config = StorageConfig.load(config_path)
    source_paths = {
        "adapter_sha256": Path(__file__),
        "storage_module_sha256": Path(str(sys.modules["memecoin_research_storage"].__file__)),
        "runner_sha256": Path(runner.__file__),
        "service_sha256": Path(service.__file__),
        "scheduler_sha256": Path(schedule_v3.__file__),
        "private_config_reader_sha256": REPO_ROOT / "src/memecoin_collector/private_config.py",
        "plist_sha256": plist_path,
    }
    hashes = {name: _sha(_read_regular_file(path)) for name, path in source_paths.items()}
    hashes["config_sha256"] = _sha(_read_regular_file(config_path))
    # Read the private config again after hashing so a concurrent change cannot
    # bind one config version while the storage guard opens another.
    if StorageConfig.load(config_path) != config:
        raise StorageError("storage_config_changed_during_binding")
    return {
        "version": BINDING_VERSION,
        "root_id": config.root_id,
        "volume_uuid": config.volume_uuid,
        "data_root": config.data_root,
        "relative_root": relative_root,
        "service_id": service_id,
        **hashes,
    }


def _canonical_json(value: object) -> bytes:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return (encoded + "\n").encode()


def _open_service_root_fd(guard: Any, relative_root: str) -> int:
    relative_root = validate_relative_root(relative_root)
    guard.revalidate()
    fd = os.dup(guard.mount_fd)
    try:
        for part in (*guard.config.data_root.split("/"), *relative_root.split("/")):
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=fd,
            )
            child_stat = os.fstat(child)
            if child_stat.st_uid != os.getuid() or stat.S_IMODE(child_stat.st_mode) != 0o700:
                os.close(child)
                raise StorageError("unsafe_service_root_owner_or_mode")
            os.close(fd)
            fd = child
        return fd
    except FileNotFoundError as exc:
        os.close(fd)
        raise StorageError("service_root_missing") from exc
    except OSError as exc:
        os.close(fd)
        if exc.errno == errno.ELOOP:
            raise StorageError("service_root_symlink_rejected") from exc
        raise StorageError("service_root_unavailable") from exc
    except StorageError:
        os.close(fd)
        raise


def validate_relative_root(value: str) -> str:
    if not isinstance(value, str) or "\x00" in value or "\\" in value:
        raise StorageError("invalid_relative_root")
    parts = value.split("/")
    if (
        len(parts) != 2
        or parts[0] != "services"
        or not parts[1]
        or parts[1] in {".", ".."}
        or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for char in parts[1]
        )
    ):
        raise StorageError("invalid_relative_root")
    return value


def _read_adapter_binding(root_fd: int) -> dict[str, Any]:
    fd = os.open(
        ADAPTER_BINDING,
        os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
        dir_fd=root_fd,
    )
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
        ):
            raise StorageError("unsafe_adapter_binding_file")
        chunks: list[bytes] = []
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
        after = os.fstat(fd)
        if _stat_identity(before) != _stat_identity(after):
            raise StorageError("adapter_binding_changed_during_read")
        value = json.loads(b"".join(chunks).decode("utf-8"), object_pairs_hook=_unique_pairs)
        if not isinstance(value, dict):
            raise StorageError("invalid_adapter_binding")
        return value
    except StorageError:
        raise
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StorageError(f"invalid_adapter_binding: {exc}") from exc
    finally:
        os.close(fd)


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise StorageError("duplicate_adapter_binding_key")
        result[key] = value
    return result


def _write_adapter_binding(root_fd: int, payload: Mapping[str, Any]) -> None:
    fd = os.open(
        ADAPTER_BINDING,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=root_fd,
    )
    try:
        os.fchmod(fd, 0o600)
        data = _canonical_json(dict(payload))
        view = memoryview(data)
        while view:
            size = os.write(fd, view)
            if size <= 0:
                raise OSError("short_adapter_binding_write")
            view = view[size:]
        os.fsync(fd)
        metadata = os.fstat(fd)
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise StorageError("unsafe_adapter_binding_file")
    finally:
        os.close(fd)
    os.fsync(root_fd)


def _check_adapter_binding(root_fd: int, expected: Mapping[str, Any]) -> bool:
    try:
        return _read_adapter_binding(root_fd) == dict(expected)
    except StorageError:
        return False


class _GuardedServiceAPI:
    def __init__(self, api: Any, guard: StorageGuard) -> None:
        self._api = api
        self._guard = guard

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._api, name)
        if not callable(value):
            return value

        def guarded(*args: Any, **kwargs: Any) -> Any:
            self._guard.revalidate()
            return value(*args, **kwargs)

        return guarded


class _GuardedClient:
    def __init__(self, client: Any, guard: StorageGuard) -> None:
        self._client = client
        self._guard = guard

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._client, name)
        if name != "fetch" or not callable(value):
            return value

        def guarded_fetch(*args: Any, **kwargs: Any) -> Any:
            self._guard.revalidate()
            if not isinstance(self._client, ReadOnlyRPC):
                return value(*args, **kwargs)
            original_transport = self._client.transport

            def guarded_transport(*transport_args: Any, **transport_kwargs: Any) -> Any:
                self._guard.revalidate()
                return original_transport(*transport_args, **transport_kwargs)

            self._client.transport = guarded_transport
            try:
                return value(*args, **kwargs)
            finally:
                if self._client.transport is guarded_transport:
                    self._client.transport = original_transport

        return guarded_fetch


def _guard_clients(clients: Mapping[str, Any] | None, guard: StorageGuard) -> dict[str, Any] | None:
    if clients is None:
        return None
    return {
        key: _GuardedClient(value, guard) if callable(getattr(value, "fetch", None)) else value
        for key, value in clients.items()
    }


@contextlib.contextmanager
def _runner_service_proxy(runner_module: Any, proxy: _GuardedServiceAPI) -> Iterator[None]:
    if not hasattr(runner_module, "service"):
        yield
        return
    previous = runner_module.service
    runner_module.service = proxy
    try:
        yield
    finally:
        runner_module.service = previous


def storage_check(config_path: Path, relative_root: str) -> dict[str, Any]:
    relative_root = validate_relative_root(relative_root)
    volume = resolve(config_path)
    root = verify_root(config_path)
    read_guard = _ReadOnlyGuard(config_path, root)
    service_root = root.path.joinpath(*relative_root.split("/"))
    initialized = False
    try:
        root_fd = _open_service_root_fd(read_guard, relative_root)
        os.close(root_fd)
        initialized = True
    except StorageError as exc:
        if str(exc) not in {"service_root_missing"}:
            raise
    finally:
        read_guard.close()
    return {
        "status": "volume_preconditions_passed",
        "provider_io": False,
        "root_verified": True,
        "root_id": volume.root_id,
        "volume_uuid": volume.volume_uuid,
        "service_root": str(service_root),
        "service_root_initialized": initialized,
    }


def _validate_runner_binding(service_root: Path, plist_path: Path) -> bool:
    try:
        return runner._verify_binding(service_root, plist_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def status_only(
    config_path: Path,
    relative_root: str,
    *,
    service_id: str,
    plist_path: Path,
) -> dict[str, Any]:
    relative_root = validate_relative_root(relative_root)
    if relative_root != f"services/{service_id}":
        raise StorageError("service_id_relative_root_mismatch")
    resolved = verify_root(config_path)
    guard = _ReadOnlyGuard(config_path, resolved)
    root_fd = -1
    try:
        root_fd = _open_service_root_fd(guard, relative_root)
        root_path = resolved.path.joinpath(*relative_root.split("/"))
        expected = _binding(
            config_path,
            relative_root=relative_root,
            service_id=service_id,
            plist_path=plist_path,
        )
        if not _check_adapter_binding(root_fd, expected):
            return {"status": "no_rpc", "reason": "adapter_binding_missing_or_changed"}
        if not _validate_runner_binding(root_path, plist_path):
            return {"status": "no_rpc", "reason": "runner_binding_missing_or_changed"}
        guard.revalidate()
        state = service.read_status(root_path)
        guard.revalidate()
        return {"status": "read_only_status", "provider_io": False, "service": state}
    finally:
        if root_fd >= 0:
            os.close(root_fd)
        guard.close()


class _ReadOnlyGuard:
    def __init__(self, config_path: Path, root: ResolvedRoot) -> None:
        self.config_path = config_path
        self.root = root
        self.config = StorageConfig.load(config_path)
        self.mount_fd = _open_absolute_directory(root.mount_path)

    def revalidate(self) -> ResolvedRoot:
        current = verify_root(self.config_path)
        if _resolved_identity(current) != _resolved_identity(self.root) or (
            StorageConfig.load(self.config_path) != self.config
        ):
            raise StorageError("storage_binding_changed")
        mount_stat = os.fstat(self.mount_fd)
        if (mount_stat.st_dev, mount_stat.st_ino) != (
            current.mount_device,
            current.mount_inode,
        ):
            raise StorageError("mounted_directory_replaced")
        return current

    def close(self) -> None:
        os.close(self.mount_fd)


def _resolved_identity(root: ResolvedRoot) -> tuple[str, str, Path, Path, int, int]:
    return (
        root.root_id,
        root.volume_uuid,
        root.mount_path,
        root.path,
        root.mount_device,
        root.mount_inode,
    )


def initialize_offline(
    config_path: Path,
    relative_root: str,
    *,
    service_id: str,
    plist_path: Path,
    authorized_at_ns: int,
    expires_at_ns: int,
    predecessor_stop_path: Path,
    terminal_receipt_path: Path,
    runner_module: Any = runner,
    service_api: Any = service,
) -> dict[str, Any]:
    relative_root = validate_relative_root(relative_root)
    if relative_root != f"services/{service_id}":
        raise StorageError("service_id_relative_root_mismatch")
    with writer_guard(config_path) as guard:
        guard.revalidate()
        service_root = guard.create_directory(relative_root)
        root_fd = _open_service_root_fd(guard, relative_root)
        try:
            try:
                os.stat(ADAPTER_BINDING, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise StorageError("adapter_binding_already_exists")
            expected = _binding(
                config_path,
                relative_root=relative_root,
                service_id=service_id,
                plist_path=plist_path,
            )
            proxy = _GuardedServiceAPI(service_api, guard)
            guard.revalidate()
            with _runner_service_proxy(runner_module, proxy):
                result = runner_module.initialize_service(
                    service_root,
                    plist_path=plist_path,
                    service_id=service_id,
                    authorized_at_ns=authorized_at_ns,
                    expires_at_ns=expires_at_ns,
                    predecessor_stop_path=predecessor_stop_path,
                    terminal_receipt_path=terminal_receipt_path,
                )
            guard.revalidate()
            _write_adapter_binding(root_fd, expected)
            return {**result, "external_adapter_binding": expected}
        finally:
            os.close(root_fd)


def scheduled_tick(
    config_path: Path,
    relative_root: str,
    *,
    service_id: str,
    plist_path: Path,
    gate_path: Path,
    predecessor_stop_path: Path,
    terminal_receipt_path: Path,
    runner_module: Any = runner,
    client_loader: Callable[[], Mapping[str, Any] | None] | None = None,
    service_api: Any = service,
    scheduler: Callable[..., dict[str, Any]] = schedule_v3.run_bucket,
    **runner_options: Any,
) -> dict[str, Any]:
    relative_root = validate_relative_root(relative_root)
    if relative_root != f"services/{service_id}":
        raise StorageError("service_id_relative_root_mismatch")
    try:
        with writer_guard(config_path) as guard:
            guard.revalidate()
            root_fd = _open_service_root_fd(guard, relative_root)
            service_root = guard.path.joinpath(*relative_root.split("/"))
            try:
                expected = _binding(
                    config_path,
                    relative_root=relative_root,
                    service_id=service_id,
                    plist_path=plist_path,
                )
                if not _check_adapter_binding(root_fd, expected):
                    return {"status": "no_rpc", "reason": "adapter_binding_missing_or_changed"}
                if not _validate_runner_binding(service_root, plist_path):
                    return {"status": "no_rpc", "reason": "runner_binding_missing_or_changed"}
                proxy = _GuardedServiceAPI(service_api, guard)
                source_client_loader = client_loader or runner_module._default_client_loader

                def load_guarded_clients() -> dict[str, Any] | None:
                    guard.revalidate()
                    clients = source_client_loader()
                    return _guard_clients(clients, guard)

                def run_guarded_scheduler(*args: Any, **kwargs: Any) -> dict[str, Any]:
                    guard.revalidate()
                    return scheduler(*args, **kwargs)

                guard.revalidate()
                with _runner_service_proxy(runner_module, proxy):
                    result = runner_module.run_tick(
                        service_root,
                        plist_path=plist_path,
                        gate_path=gate_path,
                        predecessor_stop_path=predecessor_stop_path,
                        terminal_receipt_path=terminal_receipt_path,
                        client_loader=load_guarded_clients,
                        service_api=proxy,
                        scheduler=run_guarded_scheduler,
                        **runner_options,
                    )
                if not isinstance(result, dict):
                    raise StorageError("runner_returned_invalid_result")
                return result
            finally:
                os.close(root_fd)
    except StorageError as exc:
        return {"status": "no_rpc", "reason": str(exc)}


def _parse_ns(value: str) -> int:
    return int(runner._parse_ns(value))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--relative-root", required=True, help="services/<service-id>")
    parser.add_argument("--service-id")
    parser.add_argument("--plist", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--storage-check", action="store_true")
    mode.add_argument("--status-only", action="store_true")
    mode.add_argument("--initialize", action="store_true")
    mode.add_argument("--scheduled-tick", action="store_true")
    parser.add_argument("--authorized-at", type=_parse_ns)
    parser.add_argument("--expires-at", type=_parse_ns)
    parser.add_argument("--predecessor-stop", type=Path)
    parser.add_argument("--terminal-receipt", type=Path)
    parser.add_argument("--gate-receipt", type=Path)
    args = parser.parse_args(argv)
    try:
        relative_root = validate_relative_root(args.relative_root)
        derived_id = relative_root.split("/", 1)[1]
        service_id = args.service_id or derived_id
        if service_id != derived_id:
            parser.error("--service-id must match --relative-root services/<service-id>")
        if args.storage_check:
            result = storage_check(args.config, relative_root)
        else:
            if args.plist is None:
                parser.error("service modes require --plist")
            if args.status_only:
                result = status_only(
                    args.config,
                    relative_root,
                    service_id=service_id,
                    plist_path=args.plist,
                )
            elif args.initialize:
                required = (
                    args.authorized_at,
                    args.expires_at,
                    args.predecessor_stop,
                    args.terminal_receipt,
                )
                if any(value is None for value in required):
                    parser.error(
                        "--initialize requires explicit authorization bounds and predecessor paths"
                    )
                result = initialize_offline(
                    args.config,
                    relative_root,
                    service_id=service_id,
                    plist_path=args.plist,
                    authorized_at_ns=args.authorized_at,
                    expires_at_ns=args.expires_at,
                    predecessor_stop_path=args.predecessor_stop,
                    terminal_receipt_path=args.terminal_receipt,
                )
            else:
                required_tick = (
                    args.gate_receipt,
                    args.predecessor_stop,
                    args.terminal_receipt,
                )
                if any(value is None for value in required_tick):
                    parser.error("--scheduled-tick requires gate receipt and predecessor paths")
                result = scheduled_tick(
                    args.config,
                    relative_root,
                    service_id=service_id,
                    plist_path=args.plist,
                    gate_path=args.gate_receipt,
                    predecessor_stop_path=args.predecessor_stop,
                    terminal_receipt_path=args.terminal_receipt,
                )
        from memecoin_ai.domain import canonical

        print(canonical(result), flush=True)
        return 0
    except StorageError as exc:
        print(json.dumps({"status": "no_rpc", "reason": str(exc)}, sort_keys=True), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
