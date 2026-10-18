#!/usr/bin/env python3
"""Read-only operator CLI for HeldDataT identity and root-locator checks.

This command observes an already-mounted APFS Data volume. It never mounts,
unlocks, repairs, captures, starts services, or writes to the supplied mount.
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

# Direct script invocation starts with tools/ on sys.path. Resolve the checkout
# root from this file so the command also works from a clean clone and cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.brain_host_held_data_t_v1 import (
    SCHEMA,
    ApprovedDataPlan,
    HeldDataError,
    HeldDataT,
    RootDeclaration,
    approved_data_plan_sha256,
)

PLAN_SCHEMA = "brain-host-approved-data-plan-v1"
MAX_PLAN_BYTES = 64 * 1024
PLAN_FIELDS = {
    "schema",
    "approval_id",
    "data_volume_uuid",
    "data_device_identifier",
    "data_container_reference",
    "data_volume_group_id",
    "root_roster",
}
ROOT_DECLARATION_FIELDS = {
    "present",
    "relative_locator",
    "kind",
    "unresolved_reason",
}


class CLIInputError(ValueError):
    """A bounded machine-readable CLI input error."""


class JSONArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise CLIInputError("arguments_invalid") from None


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CLIInputError("plan_duplicate_json_field")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> NoReturn:
    raise CLIInputError("plan_nonstandard_json_constant")


def _read_bounded_plan(path: Path) -> bytes:
    try:
        fd = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
    except OSError as exc:
        raise CLIInputError("plan_read_failed") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise CLIInputError("plan_must_be_regular_file")
        if metadata.st_size <= 0 or metadata.st_size > MAX_PLAN_BYTES:
            raise CLIInputError("plan_size_invalid")
        chunks: list[bytes] = []
        remaining = MAX_PLAN_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 8192))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > MAX_PLAN_BYTES or not payload:
            raise CLIInputError("plan_size_invalid")
        return payload
    except OSError as exc:
        raise CLIInputError("plan_read_failed") from exc
    finally:
        os.close(fd)


def _string_field(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CLIInputError(f"plan_{field}_invalid")
    return value


def _load_plan(path: Path) -> ApprovedDataPlan:
    payload = _read_bounded_plan(path)
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=_reject_json_constant,
        )
    except CLIInputError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise CLIInputError("plan_json_invalid") from exc
    if not isinstance(document, dict) or set(document) != PLAN_FIELDS:
        raise CLIInputError("plan_schema_fields_invalid")
    if document["schema"] != PLAN_SCHEMA:
        raise CLIInputError("plan_schema_unsupported")
    roster_value = document["root_roster"]
    if not isinstance(roster_value, dict):
        raise CLIInputError("plan_root_roster_invalid")
    roster: dict[str, RootDeclaration] = {}
    for role, value in roster_value.items():
        if not isinstance(role, str) or not isinstance(value, dict):
            raise CLIInputError("plan_root_declaration_invalid")
        if set(value) != ROOT_DECLARATION_FIELDS:
            raise CLIInputError("plan_root_declaration_fields_invalid")
        present = value["present"]
        if type(present) is not bool:
            raise CLIInputError("plan_root_presence_invalid")
        relative_locator = value["relative_locator"]
        kind = value["kind"]
        unresolved_reason = value["unresolved_reason"]
        if relative_locator is not None and not isinstance(relative_locator, str):
            raise CLIInputError("plan_root_locator_invalid")
        if kind is not None and not isinstance(kind, str):
            raise CLIInputError("plan_root_kind_invalid")
        if unresolved_reason is not None and not isinstance(unresolved_reason, str):
            raise CLIInputError("plan_root_disposition_invalid")
        roster[role] = RootDeclaration(
            present=present,
            relative_locator=relative_locator,
            kind=kind,
            unresolved_reason=unresolved_reason,
        )
    plan = ApprovedDataPlan(
        approval_id=_string_field(document["approval_id"], "approval_id"),
        data_volume_uuid=_string_field(document["data_volume_uuid"], "data_volume_uuid"),
        data_device_identifier=_string_field(
            document["data_device_identifier"], "data_device_identifier"
        ),
        data_container_reference=_string_field(
            document["data_container_reference"], "data_container_reference"
        ),
        data_volume_group_id=_string_field(
            document["data_volume_group_id"], "data_volume_group_id"
        ),
        root_roster=roster,
    )
    try:
        approved_data_plan_sha256(plan)
    except (AttributeError, TypeError, ValueError) as exc:
        raise CLIInputError("plan_values_invalid") from exc
    return plan


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    # Keep ordinary stdout free of volume IDs, host paths, and root locators.
    return {
        "schema": SCHEMA,
        "ok": True,
        "plan_sha256": report["plan_sha256"],
        "classification": report["classification"],
        "trusted_plan_pin_matches": report["trusted_plan_pin_matches"],
        "approval_authority": "external_plan_sha256_pin_not_approval_authentication",
        "root_role_roster_schema_validated": report[
            "root_role_roster_schema_validated"
        ],
        "held_root_count": report["held_root_count"],
        "required_role_closure": report["required_role_closure"],
        "unresolved_required_roles": sorted(report["unresolved_required_roles"]),
        "clean_shutdown_verified": False,
        "prior_writable_mount_history_verified": False,
        "source_cutoff_verified": False,
        "T_qualified": False,
        "capture_complete": False,
        "kingston_durable": False,
        "restore_verified": False,
        "erase_ready": False,
        "mock_backend": False,
    }


def _failure(error: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "ok": False,
        "error": error,
        "clean_shutdown_verified": False,
        "prior_writable_mount_history_verified": False,
        "source_cutoff_verified": False,
        "T_qualified": False,
        "capture_complete": False,
        "kingston_durable": False,
        "restore_verified": False,
        "erase_ready": False,
        "mock_backend": False,
    }


def _parser() -> JSONArgumentParser:
    parser = JSONArgumentParser(
        description=(
            "Verify the identity and held read-only state of an already-mounted APFS "
            "Data volume and its explicitly declared roots. This does not restore or "
            "start Brain and never qualifies capture or erase readiness."
        ),
        epilog=(
            "Prerequisites: macOS with diskutil, a Data volume already mounted read-only, "
            "an externally approved bounded plan JSON, and its independently recorded "
            "SHA-256 pin. This command will not unlock or mount volumes. Writable, "
            "unresolved, replaced, or mismatched identity stops with a JSON error. "
            "Protected data is not opened or read."
        ),
    )
    parser.add_argument("--plan", required=True, help="explicit approved plan JSON file")
    parser.add_argument(
        "--plan-sha256", required=True, help="independently recorded 64-digit plan SHA-256"
    )
    parser.add_argument("--mountpoint", required=True, help="already-mounted Data path")
    parser.add_argument(
        "--private-json-stdout",
        action="store_true",
        help="print the full identity and locator report to stdout; treat output as private",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if len(args.plan_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in args.plan_sha256
        ):
            raise CLIInputError("plan_sha256_invalid")
        plan = _load_plan(Path(args.plan))
        with HeldDataT(
            plan,
            Path(args.mountpoint),
            trusted_plan_sha256=args.plan_sha256,
        ) as held:
            report = held.result()
        if report.get("mock_backend") is not False:
            raise CLIInputError("mock_backend_forbidden_in_cli")
        if report.get("trusted_plan_pin_matches") is not True:
            raise CLIInputError("plan_pin_match_not_verified")
        report = {
            **report,
            "plan_sha256": args.plan_sha256,
            "approval_authority": "external_plan_sha256_pin_not_approval_authentication",
        }
        output = (
            report
            if args.private_json_stdout
            else _summary(report)
        )
        sys.stdout.write(
            json.dumps(output, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n"
        )
        return 0
    except (CLIInputError, HeldDataError) as exc:
        error = str(exc) if isinstance(exc, CLIInputError) else str(exc)
        sys.stdout.write(
            json.dumps(
                _failure(error),
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        return 2
    except OSError:
        sys.stdout.write(
            json.dumps(
                _failure("host_readback_failed"),
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
