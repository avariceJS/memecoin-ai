"""Resolve controller-owned verifier manifests against immutable task bindings.

This module validates manifest custody, harness identity, interpreter file and running-code
identity pins, and the required isolated CPython command prefix. It does not qualify Python's full
import/loader closure, the sandbox, result execution, or authorize use.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from .mac_code_identity import MacCodeIdentityError, static_code_directory_hash
from .task_execution import VERIFIER_COMMAND_PREFIX


class VerifierManifestError(ValueError):
    """A verifier manifest or its trusted harness artifact is invalid."""


_HASH = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_FIELDS = {
    "schema",
    "controller_release_sha256",
    "integration_policy_sha256",
    "interpreter_path",
    "interpreter_sha256",
    "interpreter_runtime_path",
    "interpreter_runtime_cdhash",
    "verifiers",
}
_VERIFIER_FIELDS = {
    "acceptance_id",
    "acceptance_sha256",
    "policy_id",
    "commands",
    "env",
    "harness_path",
    "harness_sha256",
    "result_schema",
    "case_field",
    "passed_field",
}


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise VerifierManifestError("invalid_verifier_manifest_json") from None


def _hash(value: Any) -> bool:
    return type(value) is str and _HASH.fullmatch(value) is not None


def _interpreter_identity(
    path_value: Any, expected_sha256: Any, *, configured_interpreter: str | Path
) -> Path:
    if type(path_value) is not str or not path_value or not Path(path_value).is_absolute():
        raise VerifierManifestError("verifier_interpreter_path_invalid")
    if not _hash(expected_sha256):
        raise VerifierManifestError("verifier_interpreter_hash_invalid")
    path = Path(path_value)
    if str(path) != path_value:
        raise VerifierManifestError("verifier_interpreter_path_not_canonical")
    try:
        resolved = path.resolve(strict=True)
        if resolved != path:
            raise VerifierManifestError("verifier_interpreter_path_not_canonical")
        configured = Path(configured_interpreter).resolve(strict=True)
        if configured != path:
            raise VerifierManifestError("verifier_interpreter_config_mismatch")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except VerifierManifestError:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        code = (
            "verifier_interpreter_symlink"
            if isinstance(exc, OSError) and exc.errno == errno.ELOOP
            else "verifier_interpreter_unavailable"
        )
        raise VerifierManifestError(code) from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
            raise VerifierManifestError("verifier_interpreter_not_executable")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, 1_048_576)
            if not chunk:
                break
            total += len(chunk)
            if total > 512_000_000:
                raise VerifierManifestError("verifier_interpreter_too_large")
            digest.update(chunk)
        after = os.fstat(fd)
        before_identity = (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if total != info.st_size or before_identity != after_identity:
            raise VerifierManifestError("verifier_interpreter_changed_while_reading")
        if digest.hexdigest() != expected_sha256:
            raise VerifierManifestError("verifier_interpreter_hash_mismatch")
        return path
    except OSError:
        raise VerifierManifestError("verifier_interpreter_unavailable") from None
    finally:
        os.close(fd)


def _read_regular(
    path: Path,
    *,
    symlink_code: str,
    unavailable_code: str,
    regular_code: str,
    too_large_code: str,
    max_bytes: int,
) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        code = symlink_code if exc.errno == errno.ELOOP else unavailable_code
        raise VerifierManifestError(code) from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise VerifierManifestError(regular_code)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise VerifierManifestError(too_large_code)
        return raw
    except OSError:
        raise VerifierManifestError(unavailable_code) from None
    finally:
        os.close(fd)


def _trusted_harness(root: Path, relative: Any) -> Path:
    if type(relative) is not str or not relative or "\\" in relative or "\x00" in relative:
        raise VerifierManifestError("verifier_harness_path_invalid")
    path = PurePosixPath(relative)
    if (
        path.is_absolute()
        or str(path) != relative
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise VerifierManifestError("verifier_harness_path_invalid")
    candidate = root.joinpath(*path.parts)
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise VerifierManifestError("verifier_harness_symlink")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        raise VerifierManifestError("verifier_harness_unavailable") from None
    if not resolved.is_file():
        raise VerifierManifestError("verifier_harness_not_regular")
    return resolved


def load_verifier_manifest(path: str | Path, *, expected_sha256: str) -> dict:
    """Load exact canonical JSON from a regular, non-symlinked controller file."""
    if not _hash(expected_sha256):
        raise VerifierManifestError("invalid_verifier_manifest_hash")
    candidate = Path(path)
    raw = _read_regular(
        candidate,
        symlink_code="verifier_manifest_symlink",
        unavailable_code="verifier_manifest_unavailable",
        regular_code="verifier_manifest_not_regular",
        too_large_code="verifier_manifest_too_large",
        max_bytes=1_000_000,
    )
    if _digest(raw) != expected_sha256:
        raise VerifierManifestError("verifier_manifest_hash_mismatch")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise VerifierManifestError("invalid_verifier_manifest_json") from None
    if type(value) is not dict or _canonical(value) != raw:
        raise VerifierManifestError("verifier_manifest_not_canonical")
    return value


def resolve_verifier_manifest(
    binding: Any,
    manifest: Any,
    *,
    trusted_root: str | Path,
    configured_interpreter: str | Path,
) -> tuple[dict, ...]:
    """Require exact verifier metadata and harness bytes for each immutable binding entry."""
    if type(binding) is not dict or type(manifest) is not dict:
        raise VerifierManifestError("verifier_manifest_shape")
    binding_hash = binding.get("binding_sha256")
    unsigned_binding = {key: value for key, value in binding.items() if key != "binding_sha256"}
    if (
        binding.get("schema") != "brain-task-execution-binding-v1"
        or not _hash(binding_hash)
        or _digest(_canonical(unsigned_binding)) != binding_hash
    ):
        raise VerifierManifestError("verifier_manifest_binding_invalid")
    if not _hash(binding.get("controller_release_sha256")) or not _hash(
        binding.get("integration_policy_sha256")
    ):
        raise VerifierManifestError("verifier_manifest_binding_invalid")
    if (
        set(manifest) != _MANIFEST_FIELDS
        or manifest.get("schema") != "brain-verifier-manifest-v4"
        or manifest.get("controller_release_sha256") != binding.get("controller_release_sha256")
        or manifest.get("integration_policy_sha256") != binding.get("integration_policy_sha256")
    ):
        raise VerifierManifestError("verifier_manifest_binding_mismatch")
    bound = binding.get("verifiers")
    records = manifest.get("verifiers")
    if (
        type(bound) is not list
        or not bound
        or type(records) is not list
        or len(records) != len(bound)
    ):
        raise VerifierManifestError("verifier_manifest_records_mismatch")
    root = Path(trusted_root)
    if root.is_symlink():
        raise VerifierManifestError("verifier_trusted_root_symlink")
    try:
        root = root.resolve(strict=True)
    except OSError:
        raise VerifierManifestError("verifier_trusted_root_unavailable") from None
    if not root.is_dir():
        raise VerifierManifestError("verifier_trusted_root_unavailable")
    interpreter_path = _interpreter_identity(
        manifest.get("interpreter_path"),
        manifest.get("interpreter_sha256"),
        configured_interpreter=configured_interpreter,
    )
    runtime_cdhash = manifest.get("interpreter_runtime_cdhash")
    runtime_path_value = manifest.get("interpreter_runtime_path")
    if type(runtime_cdhash) is not str or re.fullmatch(r"[0-9a-f]{40}", runtime_cdhash) is None:
        raise VerifierManifestError("verifier_interpreter_runtime_cdhash_invalid")
    if (
        type(runtime_path_value) is not str
        or not runtime_path_value
        or not Path(runtime_path_value).is_absolute()
        or str(Path(runtime_path_value)) != runtime_path_value
    ):
        raise VerifierManifestError("verifier_interpreter_runtime_path_invalid")
    try:
        runtime_path = Path(runtime_path_value)
        if runtime_path.resolve(strict=True) != runtime_path:
            raise VerifierManifestError("verifier_interpreter_runtime_path_invalid")
        actual_runtime_cdhash = static_code_directory_hash(runtime_path)
    except VerifierManifestError:
        raise
    except (MacCodeIdentityError, OSError, ValueError, RuntimeError):
        raise VerifierManifestError("verifier_interpreter_runtime_identity_invalid") from None
    if actual_runtime_cdhash != runtime_cdhash:
        raise VerifierManifestError("verifier_interpreter_runtime_cdhash_mismatch")

    resolved = []
    for expected, record in zip(bound, records, strict=True):
        if (
            type(expected) is not dict
            or type(record) is not dict
            or set(record) != _VERIFIER_FIELDS
        ):
            raise VerifierManifestError("verifier_manifest_record_invalid")
        projection = {
            key: expected.get(key)
            for key in (
                "acceptance_id",
                "acceptance_sha256",
                "policy_id",
                "commands",
                "env",
                "harness_sha256",
                "result_schema",
                "case_field",
                "passed_field",
            )
        }
        actual = {key: record.get(key) for key in projection}
        if _canonical(actual) != _canonical(projection):
            raise VerifierManifestError("verifier_manifest_record_mismatch")
        commands = record.get("commands")
        if (
            type(commands) is not list
            or not commands
            or any(type(command) is not list for command in commands)
            or len(commands[0]) < 5
            or commands[0][0] != "{interpreter}"
            or commands[0][: len(VERIFIER_COMMAND_PREFIX)] != list(VERIFIER_COMMAND_PREFIX)
            or sum(arg == "{harness}" for command in commands for arg in command) != 1
            or sum(arg == "{interpreter}" for command in commands for arg in command) != 1
        ):
            raise VerifierManifestError("verifier_harness_command_unbound")
        if not _hash(record.get("harness_sha256")):
            raise VerifierManifestError("verifier_harness_hash_invalid")
        harness = _trusted_harness(root, record.get("harness_path"))
        content = _read_regular(
            harness,
            symlink_code="verifier_harness_symlink",
            unavailable_code="verifier_harness_unavailable",
            regular_code="verifier_harness_not_regular",
            too_large_code="verifier_harness_too_large",
            max_bytes=16_000_000,
        )
        if _digest(content) != record["harness_sha256"]:
            raise VerifierManifestError("verifier_harness_hash_mismatch")
        resolved.append(
            {
                **json.loads(_canonical(actual)),
                "harness_path": record["harness_path"],
                "interpreter_path": str(interpreter_path),
                "interpreter_sha256": manifest["interpreter_sha256"],
                "interpreter_runtime_path": str(runtime_path),
                "interpreter_runtime_cdhash": runtime_cdhash,
            }
        )
    return tuple(resolved)
