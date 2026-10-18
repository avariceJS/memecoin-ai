from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from memecoin_research_storage import StorageError
from tests.test_brain_senses_external_v2_plan import _plan
from tools import brain_senses_external_launch_v2 as launch
from tools import brain_senses_external_orchestrator_v2 as engine
from tools import brain_senses_external_verify_v2 as verify


def _save(path: Path, value: Any, *, private: bool = True) -> bytes:
    raw = engine.canonical(value)
    path.write_bytes(raw)
    path.chmod(0o600 if private else 0o644)
    return raw


def _activation_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, dict]:
    plan = _plan()
    monkeypatch.setattr(engine, "source_bindings", lambda: plan["code_sha256"])
    kingston_path = launch.REPO / launch._V1_WRITER_RECEIPTS["kingston_writer_v1"][0]
    kingston = json.loads(kingston_path.read_bytes())
    identity = kingston["observed"]["root_binding_after"]
    plan["storage"]["root_id"] = identity["root_id"]
    plan["storage"]["volume_uuid"] = identity["volume_uuid"]
    plan["predecessor"]["certificate"]["storage"]["root_id"] = identity["root_id"]
    plan["predecessor"]["certificate"]["storage"]["volume_uuid"] = identity["volume_uuid"]
    plan["predecessor"]["certificate_sha256"] = engine.sha(
        engine.canonical(plan["predecessor"]["certificate"])
    )
    plan_path = tmp_path / "plan.json"
    config_path = tmp_path / "storage.json"
    review_path = tmp_path / "review.json"
    qualification_path = tmp_path / "qualification-manifest.json"
    activation_path = tmp_path / "activation.json"
    plist_path = tmp_path / "launch.plist"
    plan_raw = _save(plan_path, plan)
    config_raw = _save(config_path, {"private": True})
    predecessor = plan["predecessor"]
    writer_receipts = {}
    for role, (receipt_rel, receipt_schema) in launch._V1_WRITER_RECEIPTS.items():
        receipt_raw = (launch.REPO / receipt_rel).read_bytes()
        writer_receipts[role] = {
            "path": receipt_rel,
            "sha256": engine.sha(receipt_raw),
            "schema": receipt_schema,
            "status": "passed",
        }
    v2_path = tmp_path / launch.V2_DETACH_RECEIPT_PATH
    v2_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(launch, "V2_DETACH_RECEIPT_ROOT", tmp_path)
    v2_detach = {
        "schema": "external-v2-descriptor-detach-integration-qualification",
        "schema_version": 2,
        "status": "passed",
        "provider_requests": 0,
        "source_sha256": plan["code_sha256"],
        "writer_source_sha256": plan["code_sha256"],
        "mount_identity": {
            "root_id": identity["root_id"],
            "volume_uuid": identity["volume_uuid"],
        },
        "descriptor_path": plan["storage"]["relative_root"],
        "storage": plan["storage"],
        "tested_disposable_identity": {
            "volume_uuid": "temporary-test-uuid",
            "mount_path": "/private/tmp/qualifier/mount",
            "fs_type": "case-sensitive APFS",
            "case_sensitive": True,
            "owners_enabled": False,
            "root_id": "temporary-root-id",
            "relative_root": "services/v2-detach-fixture",
        },
        "test_only_ownership_normalization": True,
        "tested_disposable_source": launch.V2_DETACH_HARNESS_PATH,
        "tested_disposable_source_sha256": engine.sha(
            (launch.REPO / launch.V2_DETACH_HARNESS_PATH).read_bytes()
        ),
        "capacity_override_test_only": True,
        "capacity_qualified": False,
        "actual_disposable_free_bytes": 36_000_000_000,
        "requested_live_min_free_bytes": (
            plan["caps"]["disk_floor_bytes"]
            + plan["caps"]["stored_bytes_total"]
            + plan["caps"]["peak_write_margin_bytes"]
            + plan["caps"]["metadata_margin_bytes"]
        ),
        "applied_min_free_bytes": 0,
        "capacity_override_call_count": 16,
        "real_capacity_precheck_rejected": True,
        "configured_kingston_touched": False,
        "forced_detach_points": [
            {
                "name": "storage_guard_mkdir_after_revalidation",
                "operation": "mkdir",
                "underlay_unchanged": True,
                "no_retry_or_replay": True,
                "safe_syscall_failure": True,
                "provider_loader_calls": 0,
                "fallback_artifacts": [],
                "tested_disposable_volume_uuid": "temporary-test-uuid",
                "tested_disposable_mount_path": "/private/tmp/qualifier/mount",
            },
            {
                "name": "atomic_rename_2",
                "operation": "atomic_rename",
                "underlay_unchanged": True,
                "no_retry_or_replay": True,
                "safe_syscall_failure": True,
                "provider_loader_calls": 0,
                "fallback_artifacts": [],
                "replay_classification": "reservation_spent",
                "tested_disposable_volume_uuid": "temporary-test-uuid",
                "tested_disposable_mount_path": "/private/tmp/qualifier/mount",
            },
        ],
        "underlay_manifest_sha256": "d" * 64,
        "fallback_artifacts": [],
        "underlay_manifest_unchanged": True,
        "cleanup_verified": True,
        "temporary_root_removed": True,
    }
    v2_raw = _save(v2_path, v2_detach)
    writer_receipts["descriptor_detach_v2"] = {
        "path": launch.V2_DETACH_RECEIPT_PATH,
        "sha256": engine.sha(v2_raw),
        "schema": v2_detach["schema"],
        "status": "passed",
    }
    writer = {
        "schema": "brain-senses-external-v2-writer-qualification-set",
        "schema_version": 3,
        "status": "passed",
        "scope": (
            "v1 writer mechanics and Kingston scratch are reused; "
            "v2 semantic runtime is separately qualified"
        ),
        "mount_identity": {
            "root_id": identity["root_id"],
            "volume_uuid": identity["volume_uuid"],
        },
        "receipts": writer_receipts,
    }
    role_refs = {}
    role_receipts = {}
    for role, schema_name in (
        ("clock", launch._ROLE_SCHEMAS["clock"]),
        ("launch", launch._ROLE_SCHEMAS["launch"]),
    ):
        receipt_path = tmp_path / f"{role}-qualification.json"
        receipt = {
            "schema": schema_name,
            "schema_version": 2,
            "status": "passed",
            "plan_sha256": engine.sha(plan_raw),
            "source_sha256": plan["code_sha256"],
            "predecessor_certificate_sha256": predecessor["certificate_sha256"],
        }
        receipt_raw = _save(receipt_path, receipt)
        ref = {
            "path": str(receipt_path),
            "sha256": engine.sha(receipt_raw),
            "schema": schema_name,
            "status": "passed",
        }
        role_refs[role] = ref
        role_receipts[role] = receipt
    sol_path = tmp_path / "sol-qualification.json"
    sol = {
        "schema": launch._ROLE_SCHEMAS["sol_decision"],
        "schema_version": 2,
        "status": "approved",
        "plan_sha256": engine.sha(plan_raw),
        "source_sha256": plan["code_sha256"],
        "predecessor_certificate_sha256": predecessor["certificate_sha256"],
        "candidate_sha256": predecessor["candidate_sha256"],
        "validation_sha256": predecessor["validation_sha256"],
        "completion_sha256": predecessor["completion_sha256"],
        "expires_ns": plan["schedule"]["expires_ns"],
        "qualification_refs": {
            "writer_v1_and_v2": engine.sha(verify.canonical(writer)),
            **{
                role: {"path": role_refs[role]["path"], "sha256": role_refs[role]["sha256"]}
                for role in ("clock", "launch")
            },
        },
    }
    sol_raw = _save(sol_path, sol)
    role_refs["sol_decision"] = {
        "path": str(sol_path),
        "sha256": engine.sha(sol_raw),
        "schema": sol["schema"],
        "status": "approved",
    }
    manifest = {
        "schema": "brain-senses-external-v2-qualification-manifest",
        "schema_version": 2,
        "status": "passed",
        "bindings": {
            "plan_sha256": engine.sha(plan_raw),
            "source_sha256": plan["code_sha256"],
            "predecessor_certificate_sha256": predecessor["certificate_sha256"],
            "candidate_sha256": predecessor["candidate_sha256"],
            "validation_sha256": predecessor["validation_sha256"],
            "completion_sha256": predecessor["completion_sha256"],
            "expires_ns": plan["schedule"]["expires_ns"],
        },
        "roles": {"writer": writer, **role_refs},
    }
    manifest_raw = _save(qualification_path, manifest)
    review = {
        "schema": "brain-senses-external-v2-review",
        "schema_version": 2,
        "decision": "approved_for_finite_activation",
        "plan_sha256": engine.sha(plan_raw),
        "source_sha256": plan["code_sha256"],
        "predecessor_certificate_sha256": predecessor["certificate_sha256"],
        "candidate_sha256": predecessor["candidate_sha256"],
        "validation_sha256": predecessor["validation_sha256"],
        "completion_sha256": predecessor["completion_sha256"],
        "expires_ns": plan["schedule"]["expires_ns"],
        "qualification_manifest_path": str(qualification_path),
        "qualification_manifest_sha256": engine.sha(manifest_raw),
    }
    review_raw = _save(review_path, review)
    label = f"ai.memecoin.brain.senses.{plan['service_id']}"
    plist = {
        "Label": label,
        "ProgramArguments": [
            str(launch.REPO / ".venv/bin/python"),
            str(Path(launch.__file__).resolve()),
            "run",
            "--activation",
            str(activation_path),
            "--storage-config",
            str(config_path),
        ],
        "WorkingDirectory": str(launch.REPO),
        "StartCalendarInterval": launch._calendar(plan),
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
        "RunAtLoad": False,
        "KeepAlive": False,
    }
    plist_raw = plistlib.dumps(plist)
    plist_path.write_bytes(plist_raw)
    plist_path.chmod(0o600)
    activation = {
        "schema": "brain-senses-external-launch-v2",
        "schema_version": 2,
        "plan_path": str(plan_path),
        "plan_sha256": engine.sha(plan_raw),
        "storage_config_sha256": engine.sha(config_raw),
        "review_receipt_path": str(review_path),
        "review_receipt_sha256": engine.sha(review_raw),
        "plist_path": str(plist_path),
        "plist_sha256": engine.sha(plist_raw),
        "label": label,
    }
    _save(activation_path, activation)
    return activation_path, config_path, plan


def _refresh_detach_qualification_refs(activation_path: Path) -> None:
    activation = verify.decode(activation_path.read_bytes())
    review_path = Path(activation["review_receipt_path"])
    review = verify.decode(review_path.read_bytes())
    manifest_path = Path(review["qualification_manifest_path"])
    manifest = verify.decode(manifest_path.read_bytes())
    roles = manifest["roles"]
    writer = roles["writer"]
    v2_ref = writer["receipts"]["descriptor_detach_v2"]
    v2_raw = (activation_path.parent / v2_ref["path"]).read_bytes()
    v2_ref["sha256"] = engine.sha(v2_raw)
    sol_ref = roles["sol_decision"]
    sol_path = Path(sol_ref["path"])
    sol = verify.decode(sol_path.read_bytes())
    sol["qualification_refs"]["writer_v1_and_v2"] = engine.sha(verify.canonical(writer))
    sol_raw = _save(sol_path, sol)
    sol_ref["sha256"] = engine.sha(sol_raw)
    manifest_raw = _save(manifest_path, manifest)
    review["qualification_manifest_sha256"] = engine.sha(manifest_raw)
    review_raw = _save(review_path, review)
    activation["review_receipt_sha256"] = engine.sha(review_raw)
    _save(activation_path, activation)


def _repin_qualification(
    activation_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Path, Path]:
    activation = verify.decode(activation_path.read_bytes())
    review_path = Path(activation["review_receipt_path"])
    review = verify.decode(review_path.read_bytes())
    manifest_path = Path(review["qualification_manifest_path"])
    manifest = verify.decode(manifest_path.read_bytes())
    return activation, review, manifest, review_path, manifest_path


def _save_repin_qualification(
    activation_path: Path,
    activation: dict[str, Any],
    review: dict[str, Any],
    manifest: dict[str, Any],
    review_path: Path,
    manifest_path: Path,
) -> None:
    manifest_raw = _save(manifest_path, manifest)
    review["qualification_manifest_sha256"] = engine.sha(manifest_raw)
    review_raw = _save(review_path, review)
    activation["review_receipt_sha256"] = engine.sha(review_raw)
    _save(activation_path, activation)


def test_activation_binds_plan_sources_review_and_thirteen_path_only_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    activation, config, plan = _activation_files(tmp_path, monkeypatch)
    monkeypatch.setattr(launch, "runtime_gate", lambda *args, **kwargs: None)
    loaded_activation, loaded_plan = launch.activation_gate(activation, config, running=False)
    assert loaded_activation["schema"] == "brain-senses-external-launch-v2"
    assert loaded_plan == plan
    assert len(launch._calendar(plan)) == 13


@pytest.mark.parametrize(
    "mutation", ["unknown_activation_key", "review_source", "review_schema", "plist_hash"]
)
def test_activation_rejects_unpinned_or_unreviewed_launch_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    activation_path, config, plan = _activation_files(tmp_path, monkeypatch)
    monkeypatch.setattr(launch, "runtime_gate", lambda *args, **kwargs: None)
    activation = verify.decode(activation_path.read_bytes())
    if mutation == "unknown_activation_key":
        activation["extra"] = True
    elif mutation == "review_source":
        review_path = Path(activation["review_receipt_path"])
        review = verify.decode(review_path.read_bytes())
        review["source_sha256"] = {"forged.py": "5" * 64}
        review_raw = _save(review_path, review)
        activation["review_receipt_sha256"] = engine.sha(review_raw)
    elif mutation == "review_schema":
        review_path = Path(activation["review_receipt_path"])
        review = verify.decode(review_path.read_bytes())
        review["schema"] = "unrelated-review"
        review_raw = _save(review_path, review)
        activation["review_receipt_sha256"] = engine.sha(review_raw)
    else:
        plist_path = Path(activation["plist_path"])
        plist_path.write_bytes(plist_path.read_bytes() + b" ")
    _save(activation_path, activation)
    with pytest.raises(StorageError):
        launch.activation_gate(activation_path, config, running=False)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("disposable_source_hash", "v2_detach_disposable_identity_and_capacity"),
        ("capacity_claim", "v2_detach_disposable_identity_and_capacity"),
        ("detach_point", "v2_detach_point_bindings"),
        ("missing_mkdir_point", "v2_detach_required_scenarios"),
    ],
)
def test_detach_gate_rejects_forged_disposable_qualification_before_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    expected: str,
) -> None:
    activation_path, config, _ = _activation_files(tmp_path, monkeypatch)
    monkeypatch.setattr(launch, "runtime_gate", lambda *args, **kwargs: None)
    activation = verify.decode(activation_path.read_bytes())
    manifest_path = Path(
        verify.decode(Path(activation["review_receipt_path"]).read_bytes())[
            "qualification_manifest_path"
        ]
    )
    manifest = verify.decode(manifest_path.read_bytes())
    v2_ref = manifest["roles"]["writer"]["receipts"]["descriptor_detach_v2"]
    v2_path = tmp_path / v2_ref["path"]
    receipt = verify.decode(v2_path.read_bytes())
    if mutation == "disposable_source_hash":
        receipt["tested_disposable_source_sha256"] = "f" * 64
    elif mutation == "capacity_claim":
        receipt["capacity_qualified"] = True
    elif mutation == "detach_point":
        receipt["forced_detach_points"][1]["tested_disposable_volume_uuid"] = "lease-uuid"
    else:
        receipt["forced_detach_points"] = [receipt["forced_detach_points"][1]]
    _save(v2_path, receipt)
    _refresh_detach_qualification_refs(activation_path)

    loader_calls = 0
    guard_calls = 0

    def loader() -> None:
        nonlocal loader_calls
        loader_calls += 1

    def guard_factory(*args: Any, **kwargs: Any) -> None:
        nonlocal guard_calls
        guard_calls += 1
        raise AssertionError("storage must not open after failed detach qualification")

    monkeypatch.setattr(launch, "_load_private_endpoint", loader)
    with pytest.raises(StorageError, match=expected):
        launch.execute(
            "run",
            activation_path=activation_path,
            config_path=config,
            guard_factory=guard_factory,
        )
    assert loader_calls == 0
    assert guard_calls == 0


@pytest.mark.parametrize("mutation", ["path_traversal", "stale_v1_receipt", "writer_set_v2"])
def test_writer_set_v3_requires_allowlisted_versioned_receipt_before_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    activation_path, config, _ = _activation_files(tmp_path, monkeypatch)
    monkeypatch.setattr(launch, "runtime_gate", lambda *args, **kwargs: None)
    activation, review, manifest, review_path, manifest_path = _repin_qualification(activation_path)
    writer = manifest["roles"]["writer"]
    if mutation == "path_traversal":
        writer["receipts"]["descriptor_detach_v2"]["path"] = (
            "docs/evidence/../../private/stale.json"
        )
    elif mutation == "stale_v1_receipt":
        writer["receipts"]["descriptor_detach_v2"]["path"] = (
            "docs/evidence/external-v2-descriptor-detach-integration-qualification-2026-10-12.json"
        )
    else:
        writer["schema_version"] = 2
    _save_repin_qualification(
        activation_path, activation, review, manifest, review_path, manifest_path
    )

    loader_calls = 0
    guard_calls = 0

    def loader() -> None:
        nonlocal loader_calls
        loader_calls += 1

    def guard_factory(*args: Any, **kwargs: Any) -> None:
        nonlocal guard_calls
        guard_calls += 1
        raise AssertionError("storage must not open after rejected writer qualification")

    monkeypatch.setattr(launch, "_load_private_endpoint", loader)
    with pytest.raises(StorageError):
        launch.execute(
            "run",
            activation_path=activation_path,
            config_path=config,
            guard_factory=guard_factory,
        )
    assert loader_calls == 0
    assert guard_calls == 0


@pytest.mark.parametrize("mutation", ["symlink", "wrong_digest", "wrong_source"])
def test_writer_set_v3_reopens_exact_receipt_without_following_or_rebinding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    activation_path, config, _ = _activation_files(tmp_path, monkeypatch)
    monkeypatch.setattr(launch, "runtime_gate", lambda *args, **kwargs: None)
    activation, review, manifest, review_path, manifest_path = _repin_qualification(activation_path)
    ref = manifest["roles"]["writer"]["receipts"]["descriptor_detach_v2"]
    receipt_path = tmp_path / launch.V2_DETACH_RECEIPT_PATH
    if mutation == "symlink":
        replacement = tmp_path / "replacement.json"
        replacement.write_bytes(receipt_path.read_bytes())
        receipt_path.unlink()
        receipt_path.symlink_to(replacement)
    elif mutation == "wrong_digest":
        ref["sha256"] = "f" * 64
    else:
        receipt = verify.decode(receipt_path.read_bytes())
        receipt["source_sha256"] = {"unbound.py": "a" * 64}
        receipt_raw = _save(receipt_path, receipt)
        ref["sha256"] = engine.sha(receipt_raw)
    _save_repin_qualification(
        activation_path, activation, review, manifest, review_path, manifest_path
    )

    loader_calls = 0
    guard_calls = 0

    def loader() -> None:
        nonlocal loader_calls
        loader_calls += 1

    def guard_factory(*args: Any, **kwargs: Any) -> None:
        nonlocal guard_calls
        guard_calls += 1
        raise AssertionError("storage must not open after rejected detach receipt")

    monkeypatch.setattr(launch, "_load_private_endpoint", loader)
    with pytest.raises((StorageError, OSError)):
        launch.execute(
            "run",
            activation_path=activation_path,
            config_path=config,
            guard_factory=guard_factory,
        )
    assert loader_calls == 0
    assert guard_calls == 0


def test_decoy_qualification_role_is_rejected_before_loader_or_storage_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    activation_path, config, _ = _activation_files(tmp_path, monkeypatch)
    monkeypatch.setattr(launch, "runtime_gate", lambda *args, **kwargs: None)
    activation = verify.decode(activation_path.read_bytes())
    review_path = Path(activation["review_receipt_path"])
    review = verify.decode(review_path.read_bytes())
    manifest_path = Path(review["qualification_manifest_path"])
    manifest = verify.decode(manifest_path.read_bytes())
    roles = manifest["roles"]
    decoy_path = tmp_path / "decoy.json"
    decoy_raw = _save(
        decoy_path,
        {
            "schema": "brain-senses-external-v2-clock-qualification",
            "schema_version": 2,
            "status": "passed",
            "plan_sha256": "0" * 64,
            "source_sha256": {},
            "predecessor_certificate_sha256": "0" * 64,
        },
    )
    roles["clock"].update(path=str(decoy_path), sha256=engine.sha(decoy_raw))
    sol_ref = roles["sol_decision"]
    sol_path = Path(sol_ref["path"])
    sol = verify.decode(sol_path.read_bytes())
    sol["qualification_refs"]["clock"] = {
        "path": str(decoy_path),
        "sha256": engine.sha(decoy_raw),
    }
    sol_raw = _save(sol_path, sol)
    sol_ref["sha256"] = engine.sha(sol_raw)
    manifest_raw = _save(manifest_path, manifest)
    review["qualification_manifest_sha256"] = engine.sha(manifest_raw)
    review_raw = _save(review_path, review)
    activation["review_receipt_sha256"] = engine.sha(review_raw)
    _save(activation_path, activation)

    loader_calls = 0
    guard_calls = 0

    def loader() -> None:
        nonlocal loader_calls
        loader_calls += 1

    def guard_factory(*args: Any, **kwargs: Any) -> None:
        nonlocal guard_calls
        guard_calls += 1
        raise AssertionError("storage must not open after failed qualification")

    monkeypatch.setattr(launch, "_load_private_endpoint", loader)
    with pytest.raises(StorageError, match="qualification_role_scope"):
        launch.execute(
            "run",
            activation_path=activation_path,
            config_path=config,
            guard_factory=guard_factory,
        )
    assert loader_calls == 0
    assert guard_calls == 0


def test_open_service_traverses_held_mount_descriptor_without_path_fallback(
    tmp_path: Path,
) -> None:
    mount = tmp_path / "mount"
    (mount / "BrainData" / "services" / "v2-service").mkdir(parents=True, mode=0o700)
    for directory in (
        mount,
        mount / "BrainData",
        mount / "BrainData/services",
        mount / "BrainData/services/v2-service",
    ):
        directory.chmod(0o700)
    mount_fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
    calls = 0

    def revalidate() -> None:
        nonlocal calls
        calls += 1

    guard = SimpleNamespace(
        mount_fd=mount_fd,
        config=SimpleNamespace(data_root="BrainData"),
        revalidate=revalidate,
    )
    try:
        service_fd = launch.open_service(guard, "services/v2-service")
        try:
            assert os.fstat(service_fd).st_dev == os.fstat(mount_fd).st_dev
            assert calls == 1
        finally:
            os.close(service_fd)
        with pytest.raises(StorageError, match="invalid_service_path"):
            launch.open_service(guard, "services/../outside")
    finally:
        os.close(mount_fd)


def test_predecessor_read_lock_uses_existing_inode_without_manifest_mutation(
    tmp_path: Path,
) -> None:
    service = tmp_path / "predecessor"
    service.mkdir(mode=0o700)
    lock_path = service / ".writer.lock"
    lock_path.write_bytes(b"")
    lock_path.chmod(0o600)
    service_fd = os.open(service, os.O_RDONLY | os.O_DIRECTORY)
    before = sorted(
        (path.name, path.stat().st_ino, path.stat().st_size) for path in service.iterdir()
    )
    try:
        with launch.predecessor_read_lock(service_fd) as lock_fd:
            assert os.fstat(lock_fd).st_ino == lock_path.stat().st_ino
        after = sorted(
            (path.name, path.stat().st_ino, path.stat().st_size) for path in service.iterdir()
        )
        assert after == before
    finally:
        os.close(service_fd)


def test_predecessor_read_lock_refuses_separate_exclusive_writer_process(
    tmp_path: Path,
) -> None:
    service = tmp_path / "predecessor"
    service.mkdir(mode=0o700)
    lock_path = service / ".writer.lock"
    lock_path.write_bytes(b"")
    lock_path.chmod(0o600)
    data_path = service / "events.jsonl"
    data_path.write_bytes(b"immutable\n")
    before = sorted(
        (path.name, path.stat().st_ino, path.stat().st_size) for path in service.iterdir()
    )
    service_fd = os.open(service, os.O_RDONLY | os.O_DIRECTORY)
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import fcntl,os,sys; fd=os.open(sys.argv[1],os.O_RDWR); "
            "fcntl.flock(fd,fcntl.LOCK_EX); print('locked',flush=True); sys.stdin.read()",
            str(lock_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(StorageError, match="predecessor_writer_active"):
            with launch.predecessor_read_lock(service_fd):
                pytest.fail("shared predecessor lock must exclude writer")
        assert (
            sorted(
                (path.name, path.stat().st_ino, path.stat().st_size) for path in service.iterdir()
            )
            == before
        )
    finally:
        if child.stdin is not None:
            child.stdin.close()
        child.wait(timeout=5)
        with launch.predecessor_read_lock(service_fd):
            assert os.fstat(service_fd).st_ino == service.stat().st_ino
        os.close(service_fd)
    assert (
        sorted((path.name, path.stat().st_ino, path.stat().st_size) for path in service.iterdir())
        == before
    )


def test_due_bucket_returns_only_current_window_and_never_catches_up() -> None:
    plan = _plan()
    bootstrap = plan["schedule"]["bootstrap"]
    frame = plan["schedule"]["frames"][0]
    assert launch.scheduled_bucket(plan, bootstrap["start_ns"]) == bootstrap["bucket"]
    assert (
        launch.scheduled_bucket(plan, bootstrap["start_ns"] + 1_000_000_000) == bootstrap["bucket"]
    )
    assert launch.due_bucket(plan, bootstrap["start_ns"]) is None
    assert launch.due_bucket(plan, bootstrap["start_ns"] + 1_000_000_000) is None
    assert launch.due_bucket(plan, bootstrap["start_ns"] + 10_000_000_000) == bootstrap["bucket"]
    assert launch.due_bucket(plan, bootstrap["start_ns"] + 10_000_000_000) == bootstrap["bucket"]
    assert launch.due_bucket(plan, frame["start_ns"] + 10_000_000_000) == frame["bucket"]
    assert launch.due_bucket(plan, bootstrap["end_ns"] + 1) is None


def test_all_expired_unlaunched_windows_are_unobserved() -> None:
    plan = _plan()
    state = {"sealed": {}, "missed": set(), "reservations": {}}
    completion = launch.completion_view(plan, state, plan["schedule"]["expires_ns"])
    expected = [
        plan["schedule"]["bootstrap"]["bucket"],
        *(row["bucket"] for row in plan["schedule"]["frames"]),
    ]
    assert completion["expired_unobserved"] == expected
    assert completion["scheduled_windows"] == 13
    assert completion["all_windows_clean_complete"] is False
    assert completion["successor_eligible"] is False


def test_only_final_unobserved_window_is_reported() -> None:
    plan = _plan()
    windows = [plan["schedule"]["bootstrap"], *plan["schedule"]["frames"]]
    final_bucket = windows[-1]["bucket"]
    state = {
        "sealed": {row["bucket"]: True for row in windows[:-1]},
        "missed": set(),
        "reservations": {},
    }
    completion = launch.completion_view(plan, state, plan["schedule"]["expires_ns"])
    assert completion["expired_unobserved"] == [final_bucket]
    assert completion["successor_eligible"] is False


def test_status_cli_serializes_verifier_sets_as_sorted_arrays(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "brain_senses_external_launch_v2.py",
            "status",
            "--activation",
            "/tmp/activation.json",
            "--storage-config",
            "/tmp/storage.json",
        ],
    )
    monkeypatch.setattr(
        launch,
        "execute",
        lambda *args, **kwargs: {
            "status": "verified_prefix",
            "missed": {"bucket-b", "bucket-a"},
            "buckets": {"bucket-a": {"unresolved": {"request-2", "request-1"}}},
        },
    )
    assert launch.main() == 0
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "status": "verified_prefix",
        "missed": ["bucket-a", "bucket-b"],
        "buckets": {"bucket-a": {"unresolved": ["request-1", "request-2"]}},
    }
    assert output == json.dumps(json.loads(output), sort_keys=True) + "\n"
