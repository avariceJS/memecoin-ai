#!/usr/bin/env python3
"""Capture or verify a pinned mixed held-Data candidate set.

This CLI never unlocks or mounts Data and has no restore, activation, or start
operation. Capture writes candidate artifacts to Kingston; verify is read-only.
Neither operation qualifies source cutoff, root closure, T/K, or erase readiness.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memecoin_research_storage import (
    ReadOnlyStorageGuard,
    StorageError,
    StorageGuard,
    read_only_guard,
    writer_guard,
)
from tools.brain_host_held_data_root_set_capture_v2 import (
    HeldDataRootSetV2Error,
    capture_held_data_root_set_v2,
    verify_held_data_root_set_v2,
)
from tools.brain_host_held_data_t_cli_v1 import _load_plan
from tools.brain_host_held_data_t_v1 import HeldDataError, HeldDataT, approved_data_plan_sha256
from tools.brain_senses_external_writer_v1 import DescriptorWriter

SPEC_SCHEMA = "brain-host-held-data-root-set-v2-cli-spec-v1"
MAX_SPEC_BYTES = 256 * 1024
CANONICAL_STORAGE_VOLUME_UUID = "8f8f1081-9be4-4914-9111-75f1b90255bf"
CANONICAL_STORAGE_ROOT_ID = "9428695af6d4f46c08e1a444cdc66de8"
MIN_STORAGE_FREE_BYTES = 50 * 1024**3
SPEC_FIELDS = {
    "schema",
    "plan_path",
    "plan_sha256",
    "source_mountpoint",
    "storage_config_path",
    "storage_volume_uuid",
    "storage_root_id",
    "minimum_free_bytes",
    "role_policy_path",
    "role_policy_sha256",
    "directory_policies",
    "file_policies",
    "scratch_root_relative",
    "scratch_budget_bytes_per_role",
}
POLICY_PIN_FIELDS = {"path", "sha256"}
SHA256_CHARS = frozenset("0123456789abcdef")


class CLIError(ValueError):
    """A bounded CLI input or preflight error."""


class JSONArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise CLIError("arguments_invalid") from None


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CLIError("spec_duplicate_json_field")
        result[key] = value
    return result


def _no_json_constant(_value: str) -> NoReturn:
    raise CLIError("spec_nonstandard_json_constant")


def _read_spec(path: Path, expected_sha256: str) -> dict[str, Any]:
    if not _digest(expected_sha256):
        raise CLIError("spec_sha256_invalid")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise CLIError("spec_open_failed") from exc
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or not 0 < before.st_size <= MAX_SPEC_BYTES
        ):
            raise CLIError("spec_file_unsafe")
        raw = bytearray()
        while chunk := os.read(fd, min(65536, MAX_SPEC_BYTES + 1 - len(raw))):
            raw.extend(chunk)
            if len(raw) > MAX_SPEC_BYTES:
                raise CLIError("spec_size_limit")
        after = os.fstat(fd)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or len(raw) != before.st_size:
            raise CLIError("spec_changed_during_read")
    finally:
        os.close(fd)
    import hashlib

    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise CLIError("spec_sha256_mismatch")
    try:
        spec = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=_no_json_constant,
        )
    except CLIError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise CLIError("spec_json_invalid") from exc
    if not isinstance(spec, dict) or set(spec) != SPEC_FIELDS:
        raise CLIError("spec_schema_fields_invalid")
    canonical = (
        json.dumps(spec, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()
    if bytes(raw) != canonical or spec.get("schema") != SPEC_SCHEMA:
        raise CLIError("spec_canonical_schema_invalid")
    for field in (
        "plan_path",
        "source_mountpoint",
        "storage_config_path",
        "role_policy_path",
        "scratch_root_relative",
    ):
        if not isinstance(spec.get(field), str) or not spec[field]:
            raise CLIError(f"spec_{field}_invalid")
    for field in ("plan_sha256", "role_policy_sha256", "storage_volume_uuid", "storage_root_id"):
        if not isinstance(spec.get(field), str) or not spec[field]:
            raise CLIError(f"spec_{field}_invalid")
    if (
        type(spec.get("minimum_free_bytes")) is not int
        or spec["minimum_free_bytes"] < MIN_STORAGE_FREE_BYTES
        or type(spec.get("scratch_budget_bytes_per_role")) is not int
        or spec["scratch_budget_bytes_per_role"] <= 0
    ):
        raise CLIError("spec_capacity_values_invalid")
    if (
        spec["storage_volume_uuid"].lower() != CANONICAL_STORAGE_VOLUME_UUID
        or spec["storage_root_id"] != CANONICAL_STORAGE_ROOT_ID
    ):
        raise CLIError("spec_storage_binding_not_canonical")
    _validate_policy_pins(spec.get("directory_policies"), "directory")
    _validate_policy_pins(spec.get("file_policies"), "file")
    if not _digest(spec["plan_sha256"]) or not _digest(spec["role_policy_sha256"]):
        raise CLIError("spec_content_hash_invalid")
    return spec


def _digest(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64 and all(char in SHA256_CHARS for char in value)
    )


def _validate_policy_pins(value: object, label: str) -> None:
    if not isinstance(value, dict):
        raise CLIError(f"spec_{label}_policy_roster_invalid")
    for role, row in value.items():
        if (
            not isinstance(role, str)
            or not isinstance(row, dict)
            or set(row) != POLICY_PIN_FIELDS
            or not isinstance(row.get("path"), str)
            or not row["path"]
            or not _digest(row.get("sha256"))
        ):
            raise CLIError(f"spec_{label}_policy_pin_invalid")


def _policy_maps(spec: dict[str, Any], field: str) -> tuple[dict[str, Path], dict[str, str]]:
    rows: dict[str, dict[str, str]] = spec[field]
    return (
        {role: Path(row["path"]) for role, row in rows.items()},
        {role: row["sha256"] for role, row in rows.items()},
    )


def _parser() -> JSONArgumentParser:
    parser = JSONArgumentParser(
        description=(
            "Capture or verify a pinned mixed held-Data candidate set. Capture writes opaque "
            "candidate artifacts to Kingston; verify is read-only. Neither operation restores "
            "or starts Brain, and neither can grant erase readiness."
        )
    )
    parser.add_argument("operation", choices=("capture", "verify"))
    parser.add_argument("--spec", required=True, help="private canonical mode-0600 operation spec")
    parser.add_argument("--spec-sha256", required=True, help="independently recorded spec digest")
    parser.add_argument("--set-id", help="capture only; optional externally chosen 32-digit ID")
    parser.add_argument("--set-index-sha256", help="verify only; independent set-index digest")
    return parser


def _output(
    *, ok: bool, operation: str, error: str | None = None, result: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "brain-host-held-data-root-set-v2-cli-result-v1",
        "ok": ok,
        "operation": operation,
        "T_qualified": False,
        "required_root_closure_complete": False,
        "kingston_K_frontier_qualified": False,
        "brain_restore_verified": False,
        "activation_authorized": False,
        "safe_to_erase": False,
    }
    if error is not None:
        payload["error"] = error
    if result is not None:
        payload["result"] = result
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    operation = "unknown"
    try:
        args = _parser().parse_args(argv)
        operation = args.operation
        if operation == "capture" and args.set_index_sha256:
            raise CLIError("verify_only_index_pin_argument")
        if operation == "verify" and (not args.set_index_sha256 or not args.set_id):
            raise CLIError("verify_requires_set_id_and_index_pin")
        if (
            operation == "capture"
            and args.set_id
            and (len(args.set_id) != 32 or any(c not in SHA256_CHARS for c in args.set_id))
        ):
            raise CLIError("set_id_invalid")
        spec = _read_spec(Path(args.spec), args.spec_sha256)
        plan = _load_plan(Path(spec["plan_path"]))
        if approved_data_plan_sha256(plan) != spec["plan_sha256"]:
            raise CLIError("plan_sha256_mismatch")
        directory_paths, directory_hashes = _policy_maps(spec, "directory_policies")
        file_paths, file_hashes = _policy_maps(spec, "file_policies")
        guard_context = (
            writer_guard(
                Path(spec["storage_config_path"]),
                min_free_bytes=spec["minimum_free_bytes"],
            )
            if operation == "capture"
            else read_only_guard(Path(spec["storage_config_path"]))
        )
        with guard_context as guard:
            if (
                guard.volume_uuid != spec["storage_volume_uuid"]
                or guard.root_id != spec["storage_root_id"]
            ):
                raise CLIError("pinned_storage_identity_mismatch")
            if operation == "capture":
                if not isinstance(guard, StorageGuard):
                    raise CLIError("capture_storage_guard_invalid")
                writer = DescriptorWriter.from_storage_guard(guard)
            else:
                if not isinstance(guard, ReadOnlyStorageGuard):
                    raise CLIError("verify_storage_guard_invalid")
                writer = DescriptorWriter.read_only_from_storage_guard(guard)
            try:
                if operation == "capture":
                    with HeldDataT(
                        plan,
                        Path(spec["source_mountpoint"]),
                        trusted_plan_sha256=spec["plan_sha256"],
                    ) as view:
                        result = capture_held_data_root_set_v2(
                            view,
                            writer,
                            role_policy_path=Path(spec["role_policy_path"]),
                            expected_plan_sha256=spec["plan_sha256"],
                            expected_role_policy_sha256=spec["role_policy_sha256"],
                            directory_policy_paths=directory_paths,
                            expected_directory_policy_sha256_by_role=directory_hashes,
                            file_policy_paths=file_paths,
                            expected_file_policy_sha256_by_role=file_hashes,
                            storage_guard=guard,
                            scratch_root_relative=spec["scratch_root_relative"],
                            scratch_budget_bytes_per_role=spec["scratch_budget_bytes_per_role"],
                            set_id=args.set_id,
                        )
                else:
                    if not _digest(args.set_index_sha256):
                        raise CLIError("set_index_sha256_invalid")
                    assert isinstance(args.set_index_sha256, str)
                    if (
                        not isinstance(args.set_id, str)
                        or len(args.set_id) != 32
                        or any(c not in SHA256_CHARS for c in args.set_id)
                    ):
                        raise CLIError("set_id_invalid")
                    result = verify_held_data_root_set_v2(
                        writer,
                        args.set_id,
                        plan,
                        storage_guard=guard,
                        expected_plan_sha256=spec["plan_sha256"],
                        role_policy_path=Path(spec["role_policy_path"]),
                        expected_role_policy_sha256=spec["role_policy_sha256"],
                        directory_policy_paths=directory_paths,
                        expected_directory_policy_sha256_by_role=directory_hashes,
                        file_policy_paths=file_paths,
                        expected_file_policy_sha256_by_role=file_hashes,
                        expected_set_index_sha256=args.set_index_sha256,
                    )
            finally:
                writer.close()
        if operation == "verify" and result.get("verified") is not True:
            raise CLIError("fresh_verification_not_confirmed")
        output = _output(ok=True, operation=operation, result=result)
        output["binding"] = {
            "spec_sha256": args.spec_sha256,
            "plan_sha256": spec["plan_sha256"],
            "role_policy_sha256": spec["role_policy_sha256"],
            "storage_volume_uuid": guard.volume_uuid,
            "storage_root_id": guard.root_id,
        }
        return _write(output, 0)
    except (CLIError, HeldDataRootSetV2Error, HeldDataError, StorageError, OSError) as exc:
        code = str(exc) if isinstance(exc, CLIError) else _stable_error(exc)
        return _write(_output(ok=False, operation=operation, error=code), 2)
    except Exception as exc:
        return _write(
            _output(ok=False, operation=operation, error=_stable_error(exc)),
            2,
        )


def _stable_error(exc: Exception) -> str:
    text = str(exc)
    if text and all(c.islower() or c.isdigit() or c == "_" for c in text):
        return text
    return "operation_preflight_or_capture_failed"


def _write(payload: dict[str, Any], status: int) -> int:
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
