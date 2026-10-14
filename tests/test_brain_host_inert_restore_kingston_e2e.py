from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_brain_host_recovery_set import _setup_set
from tools import brain_host_recovery_set as recovery_set
from tools import brain_host_tree_custody as tree_v2
from tools import brain_host_tree_custody_v3 as tree_v3


def _namespace_ids(reader, relative: str) -> list[str]:
    try:
        return sorted(
            name for name in reader.list_names(relative) if re.fullmatch(r"[0-9a-f]{32}", name)
        )
    except FileNotFoundError:
        return []


def _membership_digest(names: list[str]) -> str:
    return hashlib.sha256("\n".join(names).encode()).hexdigest()


def _metadata_or_none(reader, relative: str) -> dict[str, int] | None:
    try:
        item = reader.stat(relative)
    except FileNotFoundError:
        return None
    return {
        "device": item.st_dev,
        "inode": item.st_ino,
        "mode": stat.S_IMODE(item.st_mode),
        "size_bytes": item.st_size,
        "uid": item.st_uid,
        "gid": item.st_gid,
    }


def _copy_synthetic_tree(
    source, destination, relative: str, files: list[str], dirs: list[str]
) -> None:
    source_stat = source.stat(relative)
    if stat.S_ISDIR(source_stat.st_mode):
        destination.mkdir(relative)
        dirs.append(relative)
        for name in source.list_names(relative):
            _copy_synthetic_tree(source, destination, f"{relative}/{name}", files, dirs)
        return
    if not stat.S_ISREG(source_stat.st_mode):
        raise AssertionError("synthetic artifact contains unsupported entry type")
    payload = source.read_bytes(relative, max_bytes=16 * 1024 * 1024)
    destination.create(relative, payload)
    files.append(relative)


def _rmdir_relative(writer, relative: str) -> None:
    parent, name = writer._parent_and_name(relative)
    parent_fd = writer._directory(parent)
    try:
        child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            if os.listdir(child_fd):
                raise AssertionError("run-owned directory is not empty during cleanup")
        finally:
            os.close(child_fd)
        writer._prewrite("rmdir")
        os.rmdir(name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def test_physical_kingston_inert_restore_cli_flow(tmp_path: Path) -> None:
    if os.environ.get("BRAIN_DR_KINGSTON_REHEARSAL") != "1":
        pytest.skip("requires explicit synthetic Kingston rehearsal opt-in")
    config_text = os.environ.get("BRAIN_DR_KINGSTON_STORAGE_CONFIG")
    expected_uuid = os.environ.get("BRAIN_DR_KINGSTON_EXPECTED_UUID")
    expected_root_id = os.environ.get("BRAIN_DR_KINGSTON_EXPECTED_ROOT_ID")
    if not config_text or not expected_uuid or not expected_root_id:
        pytest.fail("explicit storage config, UUID, and root ID pins are required")

    repo = Path(__file__).resolve().parents[1]
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    if subprocess.check_output(
        ["git", "-C", str(repo), "status", "--porcelain"], text=True
    ).strip():
        pytest.fail("rehearsal must use an exact committed research tree")

    config_path = Path(config_text).expanduser().resolve(strict=True)
    ids = {
        role: secrets.token_hex(16)
        for role in (
            "owner_work",
            "research_work",
            "private_config",
            "launchagent_definitions",
            "protected_d43",
            "sqlite_backup",
            "set",
        )
    }
    target_parent = tmp_path / "restore-target"
    target_parent.mkdir(mode=0o700)
    clean_home = tmp_path / "clean-home"
    clean_home.mkdir(mode=0o700)
    owner_root = tmp_path / "owner-source-root"
    owner_root.mkdir(mode=0o700)
    result_path = tmp_path / "restore-result.json"
    local_archive_root = tmp_path / "synthetic-source-archive"
    local_archive_root.mkdir(mode=0o700)

    from tools.brain_host_restore_assessment import open_readonly_storage_reader

    with open_readonly_storage_reader(config_path) as (reader, revalidate):
        uuid_before, root_before, free_before, _minimum = revalidate()
        assert (uuid_before, root_before) == (expected_uuid, expected_root_id)
        set_names_before = _namespace_ids(reader, recovery_set.BASE)
        witness_set_names_before = _namespace_ids(reader, recovery_set.WITNESS_BASE)
        index_lock_before = _metadata_or_none(reader, f"{recovery_set.BASE}/.index.lock")

    source, spec, _database, _sqlite_base = _setup_set(local_archive_root, synthetic_ids=ids)
    created_files: list[str] = []
    created_dirs: list[str] = []
    kingston_device = None
    artifact_roots = [
        f"{tree_v3.BASE}/{ids[role]}"
        for role in (
            "owner_work",
            "research_work",
            "private_config",
            "launchagent_definitions",
        )
    ] + [
        f"{tree_v2.BASE}/{ids['protected_d43']}",
        f"{recovery_set.SQLITE_BASE}/{ids['sqlite_backup']}",
    ]

    try:
        with tree_v2._storage_writer(config_path) as destination:
            guard = destination._custody_mutation_guard.storage_guard
            identity = (guard.volume_uuid, guard.root_id)
            kingston_device = destination.device
            if identity != (expected_uuid, expected_root_id):
                pytest.fail("descriptor-bound storage identity mismatch")
            if any(destination.exists(path) for path in artifact_roots):
                pytest.fail("random synthetic artifact path collision")
            if destination.exists(f"{recovery_set.BASE}/{ids['set']}"):
                pytest.fail("random synthetic set ID collision")
            for relative in artifact_roots:
                _copy_synthetic_tree(source, destination, relative, created_files, created_dirs)
            spec["research_commit"] = head

            def bound_identity():
                current = guard.revalidate()
                return current.volume_uuid, current.root_id

            set_result = recovery_set.create_set(
                destination,
                spec,
                expected_volume_uuid=expected_uuid,
                expected_root_id=expected_root_id,
                revalidate_identity=bound_identity,
            )
            set_id = spec["set_id"]
            index_sha256 = set_result["index_sha256"]
    finally:
        source.close()

    runtime_env = dict(os.environ)
    runtime_env.pop("PYTHONHOME", None)
    runtime_env.pop("PYTHONPATH", None)
    runtime_env["HOME"] = str(clean_home)
    runtime_env["PYTHONPATH"] = os.pathsep.join((str(repo), str(repo / "src")))
    runtime_env["GIT_CONFIG_NOSYSTEM"] = "1"
    runtime_env["GIT_CONFIG_GLOBAL"] = os.devnull
    runtime_env["GIT_OPTIONAL_LOCKS"] = "0"
    python = sys.executable
    common = ["--storage-config", str(config_path)]
    restore_command = [
        python,
        str(repo / "tools/brain_host_inert_restore_v1.py"),
        *common,
        "--set-id",
        set_id,
        "--expected-index-sha256",
        index_sha256,
        "--target-parent",
        str(target_parent),
        "--repository-root",
        str(repo),
        "--owner-checkout",
        str(owner_root),
    ]
    restore = subprocess.run(
        restore_command,
        cwd=tmp_path,
        env=runtime_env,
        text=True,
        capture_output=True,
        timeout=600,
    )
    if restore.returncode != 0:
        pytest.fail(f"inert restore CLI failed: {restore.stdout} {restore.stderr}")
    restore_payload = json.loads(restore.stdout)
    assert restore_payload["status"] == "complete_inert_quarantine"
    assert restore_payload["safe_to_erase"] is False
    assert restore_payload["activation_authorized"] is False
    assert restore_payload["provider_requests_sent"] == 0
    quarantine = Path(restore_payload["quarantine_root"])
    assert stat.S_IMODE(quarantine.stat().st_mode) == 0o700
    assert quarantine.stat().st_dev != kingston_device
    result_fd = os.open(
        result_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.write(result_fd, restore.stdout.encode())
        os.fsync(result_fd)
    finally:
        os.close(result_fd)

    assessor_command = [
        python,
        str(repo / "tools/brain_host_restore_assessment.py"),
        *common,
        "--handoff",
        str(result_path),
        "--format",
        "json",
    ]
    assessment_run = subprocess.run(
        assessor_command,
        cwd=tmp_path,
        env=runtime_env,
        text=True,
        capture_output=True,
        timeout=600,
    )
    assert assessment_run.returncode == 1
    assessment = json.loads(assessment_run.stdout)
    assert assessment["status"] == "blocked"
    assert assessment["final_readiness"] is False
    assert assessment["activation_authorized"] is False
    assert assessment["assessor_actions"] == {
        "provider_requests_sent": 0,
        "services_changed": False,
    }
    gates = {gate["name"]: gate for gate in assessment["gates"]}
    assert gates["kingston_identity"]["state"] == "pass"
    assert gates["v1_set_integrity"]["state"] == "pass"
    assert gates["accepted_set_pin"]["state"] == "pass"
    assert gates["restore_witness"]["state"] == "pass"
    assert gates["accepted_witness_pin"]["state"] == "pass"
    assert gates["immutable_source_view_T"]["state"] == "unknown"
    assert gates["durable_K_frontier"]["state"] == "unknown"
    assert gates["complete_owner_and_protected_custody"]["state"] == "unknown"

    cleanup_complete = False
    with tree_v2._storage_writer(config_path) as destination:
        guard = destination._custody_mutation_guard.storage_guard
        if (guard.volume_uuid, guard.root_id) != (expected_uuid, expected_root_id):
            pytest.fail("Kingston identity changed before cleanup")
        set_base = f"{recovery_set.BASE}/{set_id}"
        if set(destination.list_names(set_base)) != {
            "RESERVATION.json",
            "INDEX.json",
            "COMPLETE.json",
        }:
            pytest.fail("run-owned set membership changed before cleanup")
        witness_id = restore_payload["witness_id"]
        witness_base = f"{recovery_set.WITNESS_BASE}/{set_id}/{witness_id}"
        if set(destination.list_names(witness_base)) != {"WITNESS.json", "COMPLETE.json"}:
            pytest.fail("run-owned witness membership changed before cleanup")
        for filename in ("RESERVATION.json", "INDEX.json", "COMPLETE.json"):
            destination.unlink(f"{set_base}/{filename}")
        _rmdir_relative(destination, set_base)
        for filename in ("WITNESS.json", "COMPLETE.json"):
            destination.unlink(f"{witness_base}/{filename}")
        _rmdir_relative(destination, witness_base)
        _rmdir_relative(destination, f"{recovery_set.WITNESS_BASE}/{set_id}")
        for file_path in reversed(created_files):
            destination.unlink(file_path)
        for dir_path in sorted(created_dirs, key=lambda item: item.count("/"), reverse=True):
            _rmdir_relative(destination, dir_path)
        with open_readonly_storage_reader(config_path) as (reader, revalidate):
            uuid_after, root_after, free_after, _minimum = revalidate()
            assert (uuid_after, root_after) == (expected_uuid, expected_root_id)
            assert _namespace_ids(reader, recovery_set.BASE) == set_names_before
            assert _namespace_ids(reader, recovery_set.WITNESS_BASE) == witness_set_names_before
            index_lock_after = _metadata_or_none(reader, f"{recovery_set.BASE}/.index.lock")
        cleanup_complete = True

    assert cleanup_complete
    print(
        json.dumps(
            {
                "schema": "brain-host-inert-restore-kingston-e2e-receipt-v1",
                "status": "synthetic_operator_flow_passed",
                "research_commit": head,
                "volume_uuid": expected_uuid,
                "root_id": expected_root_id,
                "available_bytes_before": free_before,
                "available_bytes_after_cleanup": free_after,
                "initial_set_count": len(set_names_before),
                "final_set_count": len(set_names_before),
                "set_membership_sha256_before": _membership_digest(set_names_before),
                "set_membership_sha256_after": _membership_digest(set_names_before),
                "initial_witness_set_count": len(witness_set_names_before),
                "final_witness_set_count": len(witness_set_names_before),
                "witness_membership_sha256_before": _membership_digest(witness_set_names_before),
                "witness_membership_sha256_after": _membership_digest(witness_set_names_before),
                "index_lock_preexisting": index_lock_before is not None,
                "index_lock_present_after": index_lock_after is not None,
                "index_lock_metadata_after": index_lock_after,
                "synthetic_member_count": restore_payload["member_count"],
                "witness_bound": restore_payload["restore_witness_bound"],
                "assessor_status": assessment["status"],
                "assessor_claims": {
                    name: gates[name]["state"]
                    for name in (
                        "kingston_identity",
                        "v1_set_integrity",
                        "accepted_set_pin",
                        "restore_witness",
                        "accepted_witness_pin",
                        "immutable_source_view_T",
                        "durable_K_frontier",
                        "complete_owner_and_protected_custody",
                    )
                },
                "result_file_mode": stat.S_IMODE(result_path.stat().st_mode),
                "target_device_separate_from_kingston": True,
                "run_owned_artifacts_cleaned": cleanup_complete,
                "services_changed": False,
                "provider_requests_sent": 0,
                "single_shared_lock_file_prior_existence": "not_classified",
            },
            sort_keys=True,
        )
    )
