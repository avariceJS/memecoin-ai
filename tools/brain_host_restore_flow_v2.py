#!/usr/bin/env python3
"""Read-only, negative-only handoff/index inspection for nine mixed Brain roles.

No capture unit payload is opened. Externally declared custody is compared with
index metadata only. This interface has no extraction, restore, start, or bridge
operation and can never issue positive recovery authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn, cast

if __package__ in {None, ""}:
    _REPO = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(_REPO), str(_REPO / "src")]

from memecoin_research_storage import read_only_guard
from tools import brain_host_held_data_root_set_capture_v2 as mixed
from tools.brain_host_held_data_root_set_capture_v1 import CAPTURE_CONSISTENCY_LITERAL
from tools.brain_host_held_data_t_cli_v1 import (
    PLAN_FIELDS,
    PLAN_SCHEMA,
    ROOT_DECLARATION_FIELDS,
    _pairs_no_duplicates,
    _reject_json_constant,
)
from tools.brain_host_held_data_t_v1 import (
    ROOT_KINDS,
    ApprovedDataPlan,
    RootDeclaration,
    approved_data_plan_sha256,
)
from tools.brain_host_restore_flow_v1 import _find_pinned_card, _git_state
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json

SCHEMA = "brain-host-restore-inspection-v2"
HANDOFF_SCHEMA = "brain-host-restore-handoff-v2"
SHA = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"[0-9a-f]{32}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
MAX_BYTES = 256 * 1024
FILE_ROLES = {role for role, kind in ROOT_KINDS.items() if kind == "file"}
AUTHORITY_FLAGS = (
    "required_root_closure_complete",
    "T_qualified",
    "kingston_K_frontier_qualified",
    "brain_restore_verified",
    "activation_authorized",
    "safe_to_erase",
)
FAILURE_ACTIONS = {
    "handoff_plan_policy_inspection_failed": (
        "Inspect private inputs and compare them with independently approved hashes "
        "and role declarations."
    ),
    "checkout_or_source_card_inspection_failed": (
        "Inspect the checkout and source-card pins against the independently approved handoff."
    ),
    "storage_or_card_inspection_failed": (
        "Inspect Kingston identity and its entry-card copy against the independently approved pins."
    ),
    "candidate_index_inspection_failed": (
        "Do not restore; reconcile index and role declarations against independent pins "
        "without changing them."
    ),
    "arguments_invalid": (
        "Run this command with inspect and all required input paths and independent pins."
    ),
}
ROLE_FIELDS = {
    "kind",
    "source_state",
    "absence_reason",
    "relative_locator",
    "custody_state",
    "custody_reason",
    "capture_unit",
    "protected_disposition",
    "opaque_disposition",
    "restore_disposition",
}


class InspectionError(ValueError):
    """A static, path-free rejection of an untrusted inspection input."""


def _require(ok: bool) -> None:
    if not ok:
        raise InspectionError("inspection_input_rejected")


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _matches(pattern: re.Pattern[str], value: Any) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _json(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw)
    _require(type(value) is dict and canonical_json(value) == raw)
    return cast(dict[str, Any], value)


def _read_private_file(path: Path) -> bytes:
    """Read a private file through a no-symlink descriptor walk from the root."""
    raw_path = os.fspath(path)
    _require(
        path.is_absolute()
        and ".." not in path.parts
        and raw_path == os.path.normpath(raw_path)
        and len(path.parts) > 1
    )
    directory_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        fd = os.open(
            path.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd
        )
    finally:
        os.close(directory_fd)
    try:
        before = os.fstat(fd)
        _require(
            stat.S_ISREG(before.st_mode)
            and before.st_uid == os.geteuid()
            and stat.S_IMODE(before.st_mode) == 0o600
            and before.st_nlink == 1
            and 0 < before.st_size <= MAX_BYTES
        )
        chunks, count = [], 0
        while chunk := os.read(fd, min(65536, MAX_BYTES + 1 - count)):
            count += len(chunk)
            _require(count <= MAX_BYTES)
            chunks.append(chunk)
        after = os.fstat(fd)
        _require(
            (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_uid,
                before.st_nlink,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            == (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_uid,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            and count == before.st_size
        )
        return b"".join(chunks)
    finally:
        os.close(fd)


def _private_json(path: Path, expected_sha256: str | None = None) -> dict[str, Any]:
    raw = _read_private_file(path)
    if expected_sha256 is not None:
        _require(_matches(SHA, expected_sha256) and _digest(raw) == expected_sha256)
    return _json(raw)


def _load_plan_bytes(raw: bytes) -> ApprovedDataPlan:
    """Parse a plan from the exact bytes already read through a private descriptor."""
    _require(0 < len(raw) <= 64 * 1024)
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise InspectionError("inspection_input_rejected") from exc
    _require(
        type(document) is dict
        and set(document) == PLAN_FIELDS
        and document["schema"] == PLAN_SCHEMA
        and type(document["root_roster"]) is dict
    )
    roster: dict[str, RootDeclaration] = {}
    for role, row in document["root_roster"].items():
        _require(type(role) is str and type(row) is dict and set(row) == ROOT_DECLARATION_FIELDS)
        _require(type(row["present"]) is bool)
        _require(row["relative_locator"] is None or type(row["relative_locator"]) is str)
        _require(row["kind"] is None or type(row["kind"]) is str)
        _require(row["unresolved_reason"] is None or type(row["unresolved_reason"]) is str)
        roster[role] = RootDeclaration(
            present=row["present"],
            relative_locator=row["relative_locator"],
            kind=row["kind"],
            unresolved_reason=row["unresolved_reason"],
        )
    for field in (
        "approval_id",
        "data_volume_uuid",
        "data_device_identifier",
        "data_container_reference",
        "data_volume_group_id",
    ):
        _require(type(document[field]) is str and bool(document[field]))
    plan = ApprovedDataPlan(
        approval_id=document["approval_id"],
        data_volume_uuid=document["data_volume_uuid"],
        data_device_identifier=document["data_device_identifier"],
        data_container_reference=document["data_container_reference"],
        data_volume_group_id=document["data_volume_group_id"],
        root_roster=roster,
    )
    try:
        approved_data_plan_sha256(plan)
    except (AttributeError, TypeError, ValueError) as exc:
        raise InspectionError("inspection_input_rejected") from exc
    return plan


def validate_handoff(value: dict[str, Any]) -> None:
    _require(
        set(value)
        == {
            "schema",
            "version",
            "handoff_id",
            "execution_commit",
            "entry_card_sha256",
            "kingston",
            "data_plan_sha256",
            "role_policy_roster_sha256",
            "mixed_candidate",
            "authorization_scope",
            "roles",
        }
        and value["schema"] == HANDOFF_SCHEMA
        and type(value["version"]) is int
        and value["version"] == 2
        and value["authorization_scope"] == "inspect_only"
        and _matches(ID, value["handoff_id"])
        and _matches(COMMIT, value["execution_commit"])
        and all(
            _matches(SHA, value[k])
            for k in ("entry_card_sha256", "data_plan_sha256", "role_policy_roster_sha256")
        )
    )
    target = value["kingston"]
    _require(
        type(target) is dict
        and set(target) == {"volume_uuid", "root_id"}
        and _matches(UUID, target["volume_uuid"])
        and _matches(ID, target["root_id"])
    )
    candidate = value["mixed_candidate"]
    _require(
        type(candidate) is dict
        and set(candidate) == {"set_id", "index_sha256"}
        and _matches(ID, candidate["set_id"])
        and _matches(SHA, candidate["index_sha256"])
    )
    roles = value["roles"]
    # Exact roster deliberately rejects legacy owner_work/research_work aliases.
    # No implicit bridge or separately unpinned bridge format is supported here.
    _require(type(roles) is dict and set(roles) == set(ROOT_KINDS))
    unit_ids: set[str] = set()
    for role, row in roles.items():
        _require(type(row) is dict and set(row) == ROLE_FIELDS and row["kind"] == ROOT_KINDS[role])
        _require(
            row["protected_disposition"] in {"non_protected", "protected_opaque"}
            and row["opaque_disposition"]
            == ("private_opaque" if role in FILE_ROLES else "backup_required")
            and row["restore_disposition"]
            == ("quarantine_reissue_only" if role in FILE_ROLES else "inert_quarantine_only")
        )
        if role in FILE_ROLES:
            _require(row["protected_disposition"] == "non_protected")
        if row["source_state"] == "present":
            locator = row["relative_locator"]
            _require(
                type(locator) is str
                and 0 < len(locator) <= 4096
                and "\x00" not in locator
                and not locator.startswith("/")
                and all(p not in {"", ".", ".."} for p in locator.split("/"))
                and row["absence_reason"] is None
            )
        else:
            _require(
                row["source_state"] == "absent"
                and row["relative_locator"] is None
                and type(row["absence_reason"]) is str
                and 0 < len(row["absence_reason"]) <= 512
            )
        if row["custody_state"] == "verified_unit":
            unit = row["capture_unit"]
            _require(
                row["source_state"] == "present"
                and row["custody_reason"] is None
                and type(unit) is dict
                and set(unit) == {"id", "sha256", "type"}
                and unit["type"]
                == ("private_file_manifest_v1" if role in FILE_ROLES else "directory_relation_v3")
                and _matches(ID, unit["id"])
                and _matches(SHA, unit["sha256"])
                and unit["id"] not in unit_ids
            )
            unit_ids.add(unit["id"])
        else:
            _require(
                row["custody_state"] in {"unresolved", "reissue_only"}
                and (row["custody_state"] != "reissue_only" or role in FILE_ROLES)
                and row["capture_unit"] is None
                and type(row["custody_reason"]) is str
                and 0 < len(row["custody_reason"]) <= 512
            )


def assess_index_metadata(
    handoff: dict[str, Any], index: dict[str, Any], policy: dict[str, Any]
) -> dict[str, Any]:
    """Validate type-aware metadata joins only; never open a capture unit."""
    validate_handoff(handoff)
    _require(
        _digest(canonical_json(index)) == handoff["mixed_candidate"]["index_sha256"]
        and _digest(canonical_json(policy)) == handoff["role_policy_roster_sha256"]
        and set(index) == mixed.INDEX_FIELDS
        and index["schema"] == mixed.INDEX_SCHEMA
        and index["set_id"] == handoff["mixed_candidate"]["set_id"]
        and index["source_plan_sha256"] == handoff["data_plan_sha256"]
        and index["role_policy_sha256"] == handoff["role_policy_roster_sha256"]
        and index["role_policy"] == policy
        and set(index["roles"]) == set(ROOT_KINDS)
        and index["index_scope"]
        == "plan_declared_supported_directory_and_private_file_candidates_only"
        and index["application_consistency"] == CAPTURE_CONSISTENCY_LITERAL
        and all(index[key] is False for key in AUTHORITY_FLAGS)
        and type(index["declared_role_count"]) is int
        and index["declared_role_count"] == 9
    )
    counts = {"directory": 0, "file": 0, "unresolved": 0}
    uncaptured_present = 0
    for role, declaration in handoff["roles"].items():
        row = index["roles"][role]
        _require(type(row) is dict)
        if row.get("state") == "absent_unresolved":
            _require(
                set(row) == {"state", "unresolved_reason"}
                and declaration["capture_unit"] is None
                and type(row["unresolved_reason"]) is str
                and 0 < len(row["unresolved_reason"]) <= 512
                and row["unresolved_reason"]
                == declaration[
                    "absence_reason"
                    if declaration["source_state"] == "absent"
                    else "custody_reason"
                ]
            )
            counts["unresolved"] += 1
            uncaptured_present += declaration["source_state"] == "present"
            continue
        is_file = role in FILE_ROLES
        _require(
            set(row) == (mixed.FILE_ROLE_FIELDS if is_file else mixed.DIRECTORY_ROLE_FIELDS)
            and row["state"] == ("file_quarantine_candidate" if is_file else "captured_candidate")
            and row["kind"] == ROOT_KINDS[role]
            and row["relative_locator"] == declaration["relative_locator"]
            and declaration["custody_state"] == "verified_unit"
            and declaration["source_state"] == "present"
            and declaration["protected_disposition"] == "non_protected"
            and row["capture_id"] == declaration["capture_unit"]["id"]
            and row["manifest_sha256"] is not None
            and row["scope"] == ("private_developer_tool_opaque" if is_file else "non_protected")
            and row["policy_sha256"] == policy["roles"][role]["policy_sha256"]
            and row["application_consistency"] == CAPTURE_CONSISTENCY_LITERAL
        )
        _require(
            row["manifest_sha256"]
            and _matches(SHA, row["manifest_sha256"])
            and row["manifest_sha256" if is_file else "relation_sha256"]
            == declaration["capture_unit"]["sha256"]
        )
        if is_file:
            _require(
                row["restore_disposition"] == "quarantine_only"
                and row["metadata_disposition"] == "omitted_unqualified"
                and _matches(SHA, row["payload_sha256"])
                and type(row["payload_bytes"]) is int
                and row["payload_bytes"] >= 0
            )
        else:
            _require(
                type(row["included_entry_count"]) is int
                and row["included_entry_count"] > 0
                and type(row["unresolved_exclusion_count"]) is int
                and row["unresolved_exclusion_count"] == 0
                and type(row["external_custody_required_count"]) is int
                and row["external_custody_required_count"] == 0
            )
        counts[ROOT_KINDS[role]] += 1
    for name, count in (
        ("captured_directory_count", counts["directory"]),
        ("captured_file_count", counts["file"]),
        ("unresolved_role_count", counts["unresolved"]),
    ):
        _require(type(index[name]) is int and index[name] == count)
    blockers = [
        "negative_only_assessor",
        "unit_payloads_not_reverified",
        "source_cutoff_unqualified",
        "kingston_frontier_unqualified",
        "inert_restore_unqualified",
    ]
    if counts["unresolved"]:
        blockers.append("unresolved_role_custody")
    if uncaptured_present:
        blockers.append("present_source_roles_uncaptured")
    return {
        "schema": SCHEMA,
        "status": "blocked",
        "metadata_pins_match": True,
        "declared_role_count": 9,
        "directory_unit_count": counts["directory"],
        "file_unit_count": counts["file"],
        "unresolved_role_count": counts["unresolved"],
        "present_uncaptured_count": uncaptured_present,
        "blockers": blockers,
        "unit_payloads_reverified": False,
        "custody_claims_external": True,
        "protected_payloads_read": False,
        "source_presence_inferred_from_index": False,
        "restore_performed": False,
        "start_performed": False,
        **{key: False for key in AUTHORITY_FLAGS},
    }


def inspect_handoff(
    *,
    handoff_path: Path,
    expected_handoff_sha256: str,
    repository_root: Path,
    storage_config: Path,
    plan_path: Path,
    expected_plan_sha256: str,
    role_policy_path: Path,
    expected_role_policy_sha256: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Inspect exact code/card/storage/index pins without reading source roots or payloads."""
    failure_stage = "handoff_plan_policy_inspection_failed"
    try:
        handoff = _private_json(handoff_path, expected_handoff_sha256)
        validate_handoff(handoff)
        _require(
            handoff["data_plan_sha256"] == expected_plan_sha256
            and handoff["role_policy_roster_sha256"] == expected_role_policy_sha256
        )
        plan_raw = _read_private_file(plan_path)
        plan = _load_plan_bytes(plan_raw)
        _require(approved_data_plan_sha256(plan) == expected_plan_sha256)
        policy = _private_json(role_policy_path, expected_role_policy_sha256)
        mixed._validate_policy(policy, plan, expected_plan_sha256=expected_plan_sha256)
        for role, declaration in plan.root_roster.items():
            row = handoff["roles"][role]
            _require(
                row["source_state"] == ("present" if declaration.present else "absent")
                and row["relative_locator"] == declaration.relative_locator
                and row["absence_reason"] == declaration.unresolved_reason
            )
        failure_stage = "checkout_or_source_card_inspection_failed"
        _require(repository_root.resolve(strict=True) == Path(__file__).resolve().parents[1])
        state = _git_state(repository_root, runner)
        _require(state == {"head": handoff["execution_commit"], "clean": True})
        card_name, _ = _find_pinned_card(repository_root, "recovery", handoff["entry_card_sha256"])
        failure_stage = "storage_or_card_inspection_failed"
        with read_only_guard(storage_config) as guard:
            _require(
                guard.volume_uuid.lower() == handoff["kingston"]["volume_uuid"]
                and guard.root_id == handoff["kingston"]["root_id"]
            )
            with DescriptorWriter.read_only_from_storage_guard(guard) as reader:
                reader.assert_storage_binding(guard)
                matches = [
                    name
                    for name in ("ENTRY.txt", "ENTRY-v2.txt")
                    if reader.exists(f"host-recovery/{name}")
                    and _digest(reader.read_bytes(f"host-recovery/{name}", max_bytes=64 * 1024))
                    == handoff["entry_card_sha256"]
                ]
                _require(matches == [card_name])
                failure_stage = "candidate_index_inspection_failed"
                candidate = handoff["mixed_candidate"]
                path = f"{mixed.SET_BASE}/{candidate['set_id']}.json"
                before = reader.stat(path)
                raw = reader.read_bytes(path, max_bytes=2 * 1024 * 1024)
                _require(_digest(raw) == candidate["index_sha256"])
                index = _json(raw)
                identity = index["source_data_identity"]
                _require(
                    identity["data_volume_uuid"] == plan.data_volume_uuid.lower()
                    and identity["data_device_identifier"] == plan.data_device_identifier
                    and identity["data_container_reference"] == plan.data_container_reference
                    and identity["data_volume_group_id"] == plan.data_volume_group_id.lower()
                    and identity["readonly"] is True
                )
                result = assess_index_metadata(handoff, index, policy)
                after = reader.stat(path)
                _require(
                    (
                        before.st_dev,
                        before.st_ino,
                        before.st_size,
                        before.st_mtime_ns,
                        before.st_ctime_ns,
                    )
                    == (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                        after.st_ctime_ns,
                    )
                    and reader.read_bytes(path, max_bytes=2 * 1024 * 1024) == raw
                )
                reader.assert_storage_binding(guard)
        return {
            **result,
            "handoff_sha256": expected_handoff_sha256,
            "index_sha256": candidate["index_sha256"],
            "execution_commit": state["head"],
        }
    except Exception:
        return {
            "schema": SCHEMA,
            "status": "blocked",
            "metadata_pins_match": False,
            "blockers": [failure_stage],
            "operator_action": FAILURE_ACTIONS[failure_stage],
            "unit_payloads_reverified": False,
            "protected_payloads_read": False,
            "restore_performed": False,
            "start_performed": False,
            **{key: False for key in AUTHORITY_FLAGS},
        }


class _Parser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise InspectionError("arguments_invalid")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    parser.add_argument("command", choices=("inspect",))
    for name in ("handoff", "repository-root", "storage-config", "plan", "role-policy"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    for name in ("handoff-sha256", "plan-sha256", "role-policy-sha256"):
        parser.add_argument(f"--{name}", required=True)
    try:
        args = parser.parse_args(argv)
        result = inspect_handoff(
            handoff_path=args.handoff,
            expected_handoff_sha256=args.handoff_sha256,
            repository_root=args.repository_root,
            storage_config=args.storage_config,
            plan_path=args.plan,
            expected_plan_sha256=args.plan_sha256,
            role_policy_path=args.role_policy,
            expected_role_policy_sha256=args.role_policy_sha256,
        )
    except Exception:
        result = {
            "schema": SCHEMA,
            "status": "blocked",
            "metadata_pins_match": False,
            "blockers": ["arguments_invalid"],
            "operator_action": FAILURE_ACTIONS["arguments_invalid"],
            **{key: False for key in AUTHORITY_FLAGS},
        }
    sys.stdout.buffer.write(canonical_json(result))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
