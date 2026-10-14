#!/usr/bin/env python3
"""Read-only, negative-only assessment of a Brain restore candidate.

This v1 assessor checks declared pins and existing recovery artifacts. It always
reports readiness as blocked because current v1 custody and source-view formats
cannot establish complete owner closure or a durable T/K frontier. It never
extracts data, loads services, accesses credentials, or sends provider requests.
Verification uses short-lived system temporary SQLite scratch and does not mutate
Kingston artifacts or restore targets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import stat
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import brain_host_recovery as host_recovery
from tools import brain_host_recovery_set as recovery_set
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json

MIN_STORAGE_BYTES = 50 * 1024**3
REQUIRED_TARGETS = {
    "owner_work",
    "research_work",
    "private_config",
    "launchagent_definitions",
    "protected_d43",
    "sqlite_backup",
}


class ReadOnlyRecoveryReader:
    """Read-only facade over the descriptor-bound artifact reader methods."""

    def __init__(self, root_fd: int) -> None:
        self.__reader = DescriptorWriter(root_fd)

    @property
    def root_fd(self) -> int:
        return self.__reader.root_fd

    @property
    def device(self) -> int:
        return self.__reader.device

    def close(self) -> None:
        self.__reader.close()

    def __enter__(self) -> ReadOnlyRecoveryReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def read_bytes(self, relative: str, *, max_bytes: int) -> bytes:
        return self.__reader.read_bytes(relative, max_bytes=max_bytes)

    def list_names(self, relative: str = "") -> tuple[str, ...]:
        return self.__reader.list_names(relative)

    def exists(self, relative: str) -> bool:
        return self.__reader.exists(relative)

    def stat(self, relative: str) -> os.stat_result:
        return self.__reader.stat(relative)

    def _parent_and_name(self, relative: str) -> tuple[str, str]:
        return self.__reader._parent_and_name(relative)

    def _directory(self, relative: str) -> int:
        return self.__reader._directory(relative)


@contextmanager
def open_readonly_storage_reader(
    config_path: Path,
) -> Iterator[tuple[ReadOnlyRecoveryReader, Any]]:
    """Open the configured Kingston root without creating/taking a writer lock."""
    import memecoin_research_storage as storage
    from memecoin_research_storage import StorageConfig

    config_path = config_path.expanduser().absolute()
    config = StorageConfig.load(config_path)
    # This read-only assessment records a low-capacity failure instead of
    # suppressing all other gates at the storage policy threshold.
    resolved = storage.inspect_config(replace(config, min_free_bytes=0), min_free_bytes=0)
    mount_fd = storage._open_directory(config.mount_path)
    root_fd = -1
    reader: ReadOnlyRecoveryReader | None = None
    try:
        mount_stat = os.fstat(mount_fd)
        if (mount_stat.st_dev, mount_stat.st_ino) != (
            resolved.mount_device,
            resolved.mount_inode,
        ):
            raise ValueError("mounted_directory_changed")
        storage._verify_root_at(mount_fd, config)
        root_fd = storage._open_relative_directory(mount_fd, config.data_root)
        root_stat = os.fstat(root_fd)
        current_root_fd = storage._open_relative_directory(mount_fd, config.data_root)
        try:
            current_root_stat = os.fstat(current_root_fd)
            if (root_stat.st_dev, root_stat.st_ino) != (
                current_root_stat.st_dev,
                current_root_stat.st_ino,
            ):
                raise ValueError("research_root_changed")
        finally:
            os.close(current_root_fd)
        storage._verify_root_at(mount_fd, config)
        reader = ReadOnlyRecoveryReader(root_fd)

        def revalidate_bound_root():
            current_config = StorageConfig.load(config_path)
            if current_config != config:
                raise ValueError("storage_config_changed_during_assessment")
            current = storage.inspect_config(
                replace(current_config, min_free_bytes=0), min_free_bytes=0
            )
            held_mount = os.fstat(mount_fd)
            if (held_mount.st_dev, held_mount.st_ino) != (
                current.mount_device,
                current.mount_inode,
            ):
                raise ValueError("mounted_directory_changed_during_assessment")
            storage._verify_root_at(mount_fd, config)
            current_root_fd = storage._open_relative_directory(mount_fd, config.data_root)
            try:
                current_root_stat = os.fstat(current_root_fd)
                held_root_stat = os.fstat(reader.root_fd)
                if (current_root_stat.st_dev, current_root_stat.st_ino) != (
                    held_root_stat.st_dev,
                    held_root_stat.st_ino,
                ):
                    raise ValueError("research_root_changed_during_assessment")
            finally:
                os.close(current_root_fd)
            storage._verify_root_at(mount_fd, config)
            return (
                current.volume_uuid,
                current.root_id,
                current.available_bytes,
                config.min_free_bytes,
            )

        yield reader, revalidate_bound_root
    finally:
        if reader is not None:
            reader.close()
        if root_fd >= 0:
            os.close(root_fd)
        os.close(mount_fd)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _gate(
    name: str,
    state: str,
    reason_code: str,
    evidence_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "state": state,
        "reason_code": reason_code,
        "evidence_sha256": evidence_sha256,
    }


def _pin_state(actual: str | None, expected: str | None, label: str) -> dict[str, Any]:
    if expected is None:
        return _gate(label, "unknown", f"{label}_independent_pin_missing")
    if actual is None:
        return _gate(label, "fail", f"{label}_observation_missing")
    evidence = _digest(canonical_json({"actual": actual, "expected": expected}))
    if actual != expected:
        return _gate(label, "fail", f"{label}_pin_mismatch", evidence)
    return _gate(label, "pass", f"{label}_pin_matches", evidence)


def _host_observation(status: dict[str, Any]) -> dict[str, Any]:
    launchd = status["services"]["launchd"]
    processes = status["processes"]["workspace"]
    payload = {
        "observed_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "os": status["host"]["os"],
        "launchd_state": launchd.get("state"),
        "loaded_count": launchd.get("loaded_count"),
        "running_count": launchd.get("running_count"),
        "process_state": processes.get("state"),
        "process_count": processes.get("count"),
    }
    payload["evidence_sha256"] = _digest(canonical_json(payload))
    return payload


def _readonly_repository(repo: Path, runner: host_recovery.Runner) -> dict[str, Any]:
    requested = repo.expanduser()
    path_state = host_recovery._path_status(requested)
    if path_state.get("state") != "present" or path_state.get("kind") != "directory":
        return {"state": path_state.get("state", "unknown"), "head": None, "dirty": None}
    root = requested.resolve(strict=False)
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        head = runner(
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=/dev/null",
                "--no-optional-locks",
                "-C",
                str(root),
                "rev-parse",
                "HEAD",
            ],
            timeout=15,
            env=env,
        )
        dirty = runner(
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=/dev/null",
                "--no-optional-locks",
                "-C",
                str(root),
                "status",
                "--porcelain",
            ],
            timeout=15,
            env=env,
        )
    except Exception as exc:
        return {"state": "unknown", "head": None, "dirty": None, "reason": type(exc).__name__}
    if head.returncode != 0 or dirty.returncode != 0:
        return {"state": "unknown", "head": None, "dirty": None}
    actual = head.stdout.strip()
    if not host_recovery.COMMIT_RE.fullmatch(actual):
        return {"state": "unknown", "head": None, "dirty": None}
    return {"state": "present", "head": actual, "dirty": bool(dirty.stdout.strip())}


def _storage_values(value: Any) -> tuple[str, str, int | None, int]:
    if isinstance(value, tuple) and len(value) in {2, 3}:
        volume_uuid, root_id = value[:2]
        available = value[2] if len(value) == 3 else None
        configured_minimum = MIN_STORAGE_BYTES
    elif isinstance(value, tuple) and len(value) == 4:
        volume_uuid, root_id, available, configured_minimum = value
    else:
        volume_uuid = getattr(value, "volume_uuid", None)
        root_id = getattr(value, "root_id", None)
        available = getattr(value, "available_bytes", None)
        configured_minimum = getattr(value, "min_free_bytes", MIN_STORAGE_BYTES)
    if not isinstance(volume_uuid, str) or not isinstance(root_id, str):
        raise ValueError("storage_identity_probe_invalid")
    if available is not None and (type(available) is not int or available < 0):
        raise ValueError("storage_capacity_probe_invalid")
    if type(configured_minimum) is not int or configured_minimum < 0:
        raise ValueError("storage_capacity_floor_invalid")
    return volume_uuid, root_id, available, max(MIN_STORAGE_BYTES, configured_minimum)


def assess_restore_v1(
    writer: ReadOnlyRecoveryReader | DescriptorWriter,
    *,
    repo: Path,
    expected_commit: str | None,
    expected_volume_uuid: str | None,
    expected_root_id: str | None,
    expected_index_sha256: str | None,
    expected_witness_sha256: str | None,
    set_id: str,
    witness_id: str | None,
    witness_targets: dict[str, Path] | None,
    revalidate_identity: Any,
    runner: host_recovery.Runner = host_recovery.run_command,
    system: str | None = None,
) -> dict[str, Any]:
    """Check local evidence and fresh host state; never grant restore readiness."""
    gates: list[dict[str, Any]] = []
    target_map_error = False
    if witness_targets is not None and (
        set(witness_targets) != REQUIRED_TARGETS
        or any(not path.is_absolute() for path in witness_targets.values())
    ):
        witness_targets = None
        target_map_error = True
    actual_volume_uuid: str | None = None
    actual_root_id: str | None = None
    available_bytes: int | None = None
    try:
        (
            actual_volume_uuid,
            actual_root_id,
            available_bytes,
            required_free_bytes,
        ) = _storage_values(revalidate_identity())
        identity_evidence = _digest(
            canonical_json(
                {
                    "actual_volume_uuid": actual_volume_uuid,
                    "actual_root_id": actual_root_id,
                    "expected_volume_uuid": expected_volume_uuid,
                    "expected_root_id": expected_root_id,
                }
            )
        )
        identity_state = (
            "pass"
            if expected_volume_uuid is not None
            and expected_root_id is not None
            and actual_volume_uuid == expected_volume_uuid
            and actual_root_id == expected_root_id
            else "unknown"
            if expected_volume_uuid is None or expected_root_id is None
            else "fail"
        )
        identity_reason = {
            "pass": "bound_storage_identity_matches_independent_pins",
            "fail": "bound_storage_identity_mismatch",
            "unknown": "independent_storage_identity_pin_missing",
        }[identity_state]
        gates.append(_gate("kingston_identity", identity_state, identity_reason, identity_evidence))
        if available_bytes is None:
            gates.append(_gate("storage_capacity", "unknown", "capacity_probe_missing"))
        else:
            capacity_evidence = _digest(
                canonical_json(
                    {"available_bytes": available_bytes, "minimum_bytes": required_free_bytes}
                )
            )
            enough = available_bytes >= required_free_bytes
            gates.append(
                _gate(
                    "storage_capacity",
                    "pass" if enough else "fail",
                    "minimum_free_space_met" if enough else "minimum_free_space_not_met",
                    capacity_evidence,
                )
            )
    except Exception as exc:
        gates.append(_gate("kingston_identity", "unknown", f"storage_probe_{type(exc).__name__}"))
        gates.append(_gate("storage_capacity", "unknown", "storage_probe_unavailable"))

    set_result: dict[str, Any] | None = None
    if actual_volume_uuid is not None and actual_root_id is not None:
        try:

            def revalidate_set_identity() -> tuple[str, str]:
                current_uuid, current_root_id, _current_available, _required = _storage_values(
                    revalidate_identity()
                )
                return current_uuid, current_root_id

            set_result = recovery_set.verify_set(
                writer,
                set_id,
                expected_volume_uuid=actual_volume_uuid,
                expected_root_id=actual_root_id,
                revalidate_identity=revalidate_set_identity,
            )
            gates.append(
                _gate(
                    "v1_set_integrity",
                    "pass" if set_result["complete"] else "fail",
                    "v1_set_verified" if set_result["complete"] else "v1_set_incomplete",
                    set_result.get("index_sha256"),
                )
            )
            gates.append(
                _pin_state(
                    set_result.get("index_sha256"), expected_index_sha256, "accepted_set_pin"
                )
            )
            if witness_id is not None and witness_targets is not None and not target_map_error:
                try:
                    witness_path = f"{recovery_set.WITNESS_BASE}/{set_id}/{witness_id}/WITNESS.json"
                    witness_stat_before = writer.stat(witness_path)
                    witness_raw_before = writer.read_bytes(
                        witness_path, max_bytes=recovery_set.MAX_SPEC_BYTES
                    )
                    witness_result = recovery_set.verify_set(
                        writer,
                        set_id,
                        expected_volume_uuid=actual_volume_uuid,
                        expected_root_id=actual_root_id,
                        revalidate_identity=revalidate_set_identity,
                        witness_id=witness_id,
                        witness_targets=witness_targets,
                    )
                    witness_raw_after = writer.read_bytes(
                        witness_path, max_bytes=recovery_set.MAX_SPEC_BYTES
                    )
                    witness_stat_after = writer.stat(witness_path)
                    witness_sig_before = (
                        witness_stat_before.st_dev,
                        witness_stat_before.st_ino,
                        witness_stat_before.st_size,
                        witness_stat_before.st_mtime_ns,
                        witness_stat_before.st_ctime_ns,
                    )
                    witness_sig_after = (
                        witness_stat_after.st_dev,
                        witness_stat_after.st_ino,
                        witness_stat_after.st_size,
                        witness_stat_after.st_mtime_ns,
                        witness_stat_after.st_ctime_ns,
                    )
                    if (
                        witness_raw_before != witness_raw_after
                        or witness_sig_before != witness_sig_after
                    ):
                        raise recovery_set.RecoverySetError(
                            "restore_witness_changed_during_assessment"
                        )
                    witness_sha256 = _digest(witness_raw_after)
                    witness_integrity = witness_result["restore_witness_bound"]
                    gates.append(
                        _gate(
                            "restore_witness",
                            "pass" if witness_integrity else "fail",
                            "witness_matches_targets" if witness_integrity else "witness_mismatch",
                            witness_sha256,
                        )
                    )
                    gates.append(
                        _pin_state(witness_sha256, expected_witness_sha256, "accepted_witness_pin")
                    )
                    gates.append(
                        _gate(
                            "target_isolation",
                            "unknown",
                            "target_paths_are_operator_locators_not_independent_isolation_evidence",
                        )
                    )
                except Exception as exc:
                    reason = (
                        str(exc)
                        if isinstance(exc, recovery_set.RecoverySetError)
                        else type(exc).__name__
                    )
                    gates.append(_gate("restore_witness", "fail", reason))
                    gates.append(
                        _gate("accepted_witness_pin", "unknown", "witness_verification_failed")
                    )
                    gates.append(
                        _gate("target_isolation", "unknown", "witness_verification_failed")
                    )
            elif target_map_error:
                gates.append(
                    _gate("restore_witness", "fail", "restore_witness_target_roles_invalid")
                )
                gates.append(_gate("accepted_witness_pin", "unknown", "invalid_target_map"))
                gates.append(_gate("target_isolation", "unknown", "invalid_target_map"))
            else:
                gates.append(
                    _gate("restore_witness", "unknown", "witness_id_or_target_map_missing")
                )
                gates.append(_gate("accepted_witness_pin", "unknown", "witness_evidence_missing"))
                gates.append(
                    _gate(
                        "target_isolation", "unknown", "target_isolation_not_independently_proven"
                    )
                )
        except Exception as exc:
            reason = (
                str(exc) if isinstance(exc, recovery_set.RecoverySetError) else type(exc).__name__
            )
            gates.append(_gate("v1_set_integrity", "fail", reason))
            gates.append(_gate("accepted_set_pin", "unknown", "set_verification_failed"))
            gates.append(_gate("restore_witness", "unknown", "set_verification_failed"))
            gates.append(_gate("accepted_witness_pin", "unknown", "set_verification_failed"))
            gates.append(_gate("target_isolation", "unknown", "set_verification_failed"))
    else:
        gates.extend(
            [
                _gate("v1_set_integrity", "unknown", "bound_storage_unavailable"),
                _gate("accepted_set_pin", "unknown", "set_verification_unavailable"),
                _gate("restore_witness", "unknown", "set_verification_unavailable"),
                _gate("accepted_witness_pin", "unknown", "set_verification_unavailable"),
                _gate("target_isolation", "unknown", "set_verification_unavailable"),
            ]
        )

    try:
        repo_status = _readonly_repository(repo, runner)
        if expected_commit is None:
            gates.append(_gate("research_commit", "unknown", "independent_commit_pin_missing"))
        elif repo_status.get("state") != "present":
            gates.append(_gate("research_commit", "fail", "research_checkout_unavailable"))
        else:
            actual_commit = repo_status.get("head")
            evidence = _digest(
                canonical_json(
                    {
                        "head": actual_commit,
                        "dirty": repo_status.get("dirty"),
                        "expected": expected_commit,
                    }
                )
            )
            matches = actual_commit == expected_commit and repo_status.get("dirty") is False
            gates.append(
                _gate(
                    "research_commit",
                    "pass" if matches else "fail",
                    "exact_clean_commit" if matches else "commit_or_worktree_mismatch",
                    evidence,
                )
            )
    except Exception as exc:
        gates.append(_gate("research_commit", "unknown", f"repository_probe_{type(exc).__name__}"))

    try:
        detected_system = system or platform.system()
        launchd_status = host_recovery._launchd(runner, detected_system)
        owner_target = (witness_targets or {}).get("owner_work")
        base_processes = host_recovery._workspace_processes(
            runner,
            detected_system,
            research_root=repo,
            owner_root=owner_target,
        )
        research_target = (witness_targets or {}).get("research_work")
        if research_target is not None and owner_target is not None:
            target_processes = host_recovery._workspace_processes(
                runner,
                detected_system,
                research_root=research_target,
                owner_root=owner_target,
            )
            base_rows = base_processes.get("processes", [])
            extra_rows = target_processes.get("processes", [])
            merged = {
                (row.get("pid"), row.get("name"), row.get("cwd")): row
                for row in [*base_rows, *extra_rows]
            }
            process_states = {base_processes.get("state"), target_processes.get("state")}
            if "unknown" in process_states or "not_inspected" in process_states:
                merged_state = "unknown"
            elif merged:
                merged_state = "present"
            else:
                merged_state = "absent"
            workspace_status = {
                "state": merged_state,
                "count": len(merged),
                "processes": list(merged.values()),
            }
        else:
            workspace_status = {
                "state": "unknown",
                "processes": [],
                "reason": "owner_and_research_restore_roots_not_provided",
            }
        host_status = {
            "host": {"os": detected_system},
            "services": {"launchd": launchd_status},
            "processes": {"workspace": workspace_status},
        }
        observed = _host_observation(host_status)
        launchd_state = observed["launchd_state"]
        process_state = observed["process_state"]
        launchd_gate = {
            "absent": ("pass", "zero_filtered_brain_and_d43_labels_observed"),
            "present": ("fail", "brain_or_d43_labels_observed"),
        }.get(launchd_state, ("unknown", "launchd_observation_unavailable"))
        process_gate = {
            "absent": ("pass", "zero_workspace_cwd_processes_observed"),
            "present": ("fail", "workspace_cwd_processes_observed"),
        }.get(process_state, ("unknown", "workspace_process_observation_unavailable"))
        gates.append(
            _gate(
                "zero_loaded_brain_labels",
                launchd_gate[0],
                launchd_gate[1],
                observed["evidence_sha256"],
            )
        )
        gates.append(
            _gate(
                "zero_workspace_cwd_processes",
                process_gate[0],
                process_gate[1],
                observed["evidence_sha256"],
            )
        )
        gates.append(
            _gate(
                "complete_inertness",
                "unknown",
                "host_status_does_not_enumerate_all_services_or_runtime",
                observed["evidence_sha256"],
            )
        )
    except Exception as exc:
        gates.append(
            _gate("zero_loaded_brain_labels", "unknown", f"host_probe_{type(exc).__name__}")
        )
        gates.append(
            _gate("zero_workspace_cwd_processes", "unknown", f"host_probe_{type(exc).__name__}")
        )
        gates.append(_gate("complete_inertness", "unknown", "host_observation_unavailable"))

    gates.extend(
        [
            _gate(
                "complete_owner_and_protected_custody",
                "unknown",
                "full_custody_verifier_not_qualified",
            ),
            _gate("immutable_source_view_T", "unknown", "snapshot_bound_T_verifier_not_qualified"),
            _gate("durable_K_frontier", "unknown", "K_frontier_verifier_not_qualified"),
            _gate(
                "application_restore_qualification", "unknown", "v1_formats_explicitly_unqualified"
            ),
        ]
    )
    try:
        final_storage = _storage_values(revalidate_identity())
        final_matches = final_storage[:2] == (actual_volume_uuid, actual_root_id)
        gates.append(
            _gate(
                "kingston_bound_root_stable",
                "pass" if final_matches else "fail",
                "held_root_matches_live_bound_root" if final_matches else "storage_binding_changed",
            )
        )
    except Exception as exc:
        gates.append(
            _gate("kingston_bound_root_stable", "fail", f"storage_recheck_{type(exc).__name__}")
        )
    return {
        "schema": "brain-host-restore-assessment-v1",
        "status": "blocked",
        "final_readiness": False,
        "activation_authorized": False,
        "assessor_actions": {"provider_requests_sent": 0, "services_changed": False},
        "scratch_scope": (
            "short_lived_system_temporary_verifier_sqlite_files_may_be_created_and_removed"
        ),
        "observed_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "gates": gates,
    }


def render_assessment(payload: dict[str, Any]) -> str:
    lines = [
        "Restore assessment: BLOCKED",
        "v1 is an evidence assessor and cannot grant readiness.",
    ]
    for gate in payload["gates"]:
        evidence = gate.get("evidence_sha256")
        suffix = f"; evidence sha256={evidence}" if evidence else ""
        lines.append(f"{gate['state'].upper():7} {gate['name']}: {gate['reason_code']}{suffix}")
    lines.append(
        "Assessor actions: services changed no; provider requests sent 0; activation authorized no."
    )
    lines.append(
        "Verifier may create and remove short-lived SQLite scratch in the system temp directory."
    )
    return "\n".join(lines)


def _print(payload: dict[str, Any], output_format: str) -> None:
    if output_format == "text":
        print(render_assessment(payload))
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))


HANDOFF_SCHEMA = "brain-host-restore-assessor-handoff-v1"
HANDOFF_FIELDS = {
    "schema",
    "status",
    "set_id",
    "expected_commit",
    "expected_volume_uuid",
    "expected_root_id",
    "expected_index_sha256",
    "witness_id",
    "expected_witness_sha256",
    "witness_targets",
    "repo",
    "activation_authorized",
    "safe_to_erase",
}


def _load_restore_handoff(path: Path) -> dict[str, Any]:
    """Load a conductor result or its handoff bundle with strict shape checks."""

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("handoff_duplicate_key")
            result[key] = value
        return result

    fd = -1
    try:
        fd = os.open(path.expanduser(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("handoff_not_regular_file")
        if before.st_size > 1024 * 1024:
            raise ValueError("handoff_too_large")
        blocks = bytearray()
        while len(blocks) <= 1024 * 1024:
            block = os.read(fd, min(65536, 1024 * 1024 + 1 - len(blocks)))
            if not block:
                break
            blocks.extend(block)
        after = os.fstat(fd)
        before_sig = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_sig = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if before_sig != after_sig or len(blocks) != after.st_size:
            raise ValueError("handoff_changed_during_read")
        if len(blocks) > 1024 * 1024:
            raise ValueError("handoff_too_large")
        raw = bytes(blocks)
        value = json.loads(raw, object_pairs_hook=unique_pairs)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("handoff_invalid_json") from exc
    finally:
        if fd >= 0:
            os.close(fd)
    if not isinstance(value, dict):
        raise ValueError("handoff_invalid_contract")
    if "assessor_handoff" in value:
        if value.get("schema") != "brain-host-inert-restore-v1":
            raise ValueError("handoff_parent_schema_invalid")
        evidence_pin = value.get("evidence_sha256")
        unsigned = dict(value)
        unsigned.pop("evidence_sha256", None)
        if not isinstance(evidence_pin, str) or _digest(canonical_json(unsigned)) != evidence_pin:
            raise ValueError("handoff_parent_integrity_mismatch")
        value = value["assessor_handoff"]
    if not isinstance(value, dict):
        raise ValueError("handoff_invalid_contract")
    if set(value) != HANDOFF_FIELDS or value.get("schema") != HANDOFF_SCHEMA:
        raise ValueError("handoff_invalid_contract")
    if (
        value.get("status") != "blocked"
        or value.get("activation_authorized") is not False
        or value.get("safe_to_erase") is not False
    ):
        raise ValueError("handoff_authority_fields_invalid")
    required_strings = (
        "set_id",
        "expected_commit",
        "expected_volume_uuid",
        "expected_root_id",
        "expected_index_sha256",
        "witness_id",
        "expected_witness_sha256",
        "repo",
    )
    if any(not isinstance(value.get(key), str) or not value[key] for key in required_strings):
        raise ValueError("handoff_pin_invalid")
    repo = Path(value["repo"])
    if not repo.is_absolute() or ".." in repo.parts:
        raise ValueError("handoff_repo_path_invalid")
    normalized_repo = Path(os.path.abspath(repo))
    try:
        if repo.resolve(strict=False) != normalized_repo:
            raise ValueError("handoff_repo_path_symlinked")
    except OSError as exc:
        raise ValueError("handoff_repo_path_invalid") from exc
    targets = value.get("witness_targets")
    if not isinstance(targets, dict) or set(targets) != REQUIRED_TARGETS:
        raise ValueError("handoff_targets_invalid")
    if any(
        not isinstance(target, str) or not Path(target).is_absolute() for target in targets.values()
    ):
        raise ValueError("handoff_targets_invalid")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--handoff", type=Path, help="conductor result JSON or assessor handoff JSON"
    )
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--storage-config", type=Path, required=True)
    parser.add_argument("--set-id")
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-volume-uuid")
    parser.add_argument("--expected-root-id")
    parser.add_argument("--expected-index-sha256")
    parser.add_argument("--expected-witness-sha256")
    parser.add_argument("--witness-id")
    parser.add_argument("--owner-target", type=Path)
    parser.add_argument("--research-target", type=Path)
    parser.add_argument("--private-config-target", type=Path)
    parser.add_argument("--launchagent-target", type=Path)
    parser.add_argument("--protected-target", type=Path)
    parser.add_argument("--sqlite-target", type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    if args.handoff is not None:
        if any(
            value is not None
            for value in (
                args.repo,
                args.set_id,
                args.expected_commit,
                args.expected_volume_uuid,
                args.expected_root_id,
                args.expected_index_sha256,
                args.expected_witness_sha256,
                args.witness_id,
                args.owner_target,
                args.research_target,
                args.private_config_target,
                args.launchagent_target,
                args.protected_target,
                args.sqlite_target,
            )
        ):
            parser.error("--handoff cannot be combined with explicit restore pins or targets")
        try:
            handoff = _load_restore_handoff(args.handoff)
        except ValueError as exc:
            parser.error(str(exc))
        args.repo = Path(handoff["repo"])
        args.set_id = handoff["set_id"]
        args.expected_commit = handoff["expected_commit"]
        args.expected_volume_uuid = handoff["expected_volume_uuid"]
        args.expected_root_id = handoff["expected_root_id"]
        args.expected_index_sha256 = handoff["expected_index_sha256"]
        args.expected_witness_sha256 = handoff["expected_witness_sha256"]
        args.witness_id = handoff["witness_id"]
        handoff_targets = handoff["witness_targets"]
        args.owner_target = Path(handoff_targets["owner_work"])
        args.research_target = Path(handoff_targets["research_work"])
        args.private_config_target = Path(handoff_targets["private_config"])
        args.launchagent_target = Path(handoff_targets["launchagent_definitions"])
        args.protected_target = Path(handoff_targets["protected_d43"])
        args.sqlite_target = Path(handoff_targets["sqlite_backup"])
    elif args.repo is None or args.set_id is None:
        parser.error("--repo and --set-id are required unless --handoff is provided")
    targets_args = {
        "owner_work": args.owner_target,
        "research_work": args.research_target,
        "private_config": args.private_config_target,
        "launchagent_definitions": args.launchagent_target,
        "protected_d43": args.protected_target,
        "sqlite_backup": args.sqlite_target,
    }
    supplied_targets = [value for value in targets_args.values() if value is not None]
    if supplied_targets and len(supplied_targets) != len(REQUIRED_TARGETS):
        parser.error("provide either none or all six *-target paths")
    if any(not value.is_absolute() for value in supplied_targets):
        parser.error("all *-target paths must be absolute")
    if args.witness_id and any(value is None for value in targets_args.values()):
        parser.error("--witness-id requires all six *-target paths")
    targets = (
        {role: value for role, value in targets_args.items() if value is not None}
        if all(value is not None for value in targets_args.values())
        else None
    )
    try:
        storage_config = args.storage_config.expanduser().absolute()
        with open_readonly_storage_reader(storage_config) as (reader, revalidate_identity):
            result = assess_restore_v1(
                reader,
                repo=args.repo,
                expected_commit=args.expected_commit,
                expected_volume_uuid=args.expected_volume_uuid,
                expected_root_id=args.expected_root_id,
                expected_index_sha256=args.expected_index_sha256,
                expected_witness_sha256=args.expected_witness_sha256,
                set_id=args.set_id,
                witness_id=args.witness_id,
                witness_targets=targets,
                revalidate_identity=revalidate_identity,
            )
        _print(result, args.format)
        return 1
    except Exception as exc:
        payload = {
            "schema": "brain-host-restore-assessment-v1",
            "status": "blocked",
            "final_readiness": False,
            "activation_authorized": False,
            "error": type(exc).__name__,
            "assessor_actions": {"provider_requests_sent": 0, "services_changed": False},
            "scratch_scope": (
                "short_lived_system_temporary_verifier_sqlite_files_may_be_created_and_removed"
            ),
        }
        _print(payload, args.format)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
