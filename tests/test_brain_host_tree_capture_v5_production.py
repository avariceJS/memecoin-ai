from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import memecoin_research_storage as storage
from memecoin_research_storage import StorageGuard
from tools import brain_host_tree_capture_v5_production as production_module
from tools.brain_host_held_data_t_v1 import DarwinDataOS, HeldDataT
from tools.brain_host_tree_capture_v5 import (
    REQUIRED_ROOT_ROLES,
    RoleCapturePolicy,
    TreeCaptureV5Error,
    verify_candidate_v5,
)
from tools.brain_host_tree_capture_v5_production import (
    CAPTURE_SPEC_SCHEMA,
    PinnedScratchV5,
    ProductionCaptureV5CLIError,
    ProductionCaptureV5Error,
    _assert_writer_source_separation,
    _capture_failure,
    _capture_summary,
    _load_capture_policies,
    _preflight_policy_roster,
    _read_private_capture_spec,
    _require_real_held_view,
    _required_boundary_callback,
    _validate_pins,
    _validate_scratch_devices,
    production_capture_v5,
    verify_production_candidate_v5,
)
from tools.brain_host_tree_capture_v5_production import (
    main as production_cli_main,
)
from tools.brain_host_tree_custody_v3 import POLICY_SCHEMA
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json


def test_required_boundary_callback_forwards_reserve_to_full_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[object, ...]] = []

    def record_boundary(*args: object) -> None:
        observed.append(args)

    monkeypatch.setattr(
        "tools.brain_host_tree_capture_v5_production._full_boundary", record_boundary
    )
    view, guard, writer = object(), object(), object()
    callback = _required_boundary_callback(
        view,
        guard,
        writer,
        "11111111-2222-3333-4444-555555555555",
        "a" * 32,
        17,
    )
    callback(1234)
    assert observed == [
        (view, guard, writer, "11111111-2222-3333-4444-555555555555", "a" * 32, 17, 1234)
    ]
    with pytest.raises(ProductionCaptureV5Error, match="production_reserve_value_invalid"):
        callback(-1)


def test_production_factory_rejects_synthetic_view_before_storage_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    view = object.__new__(HeldDataT)
    view.synthetic = True
    view.os_api = object()

    def forbidden_storage_open(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("storage must not be opened for synthetic source")

    monkeypatch.setattr(
        "tools.brain_host_tree_capture_v5_production.storage.writer_guard", forbidden_storage_open
    )
    with pytest.raises(
        ProductionCaptureV5Error, match="production_source_view_not_real_or_unpinned"
    ):
        with production_capture_v5(
            view,
            tmp_path / "does-not-exist.json",
            expected_plan_sha256="0" * 64,
            expected_policy_roster_sha256="0" * 64,
            expected_target_volume_uuid="11111111-2222-3333-4444-555555555555",
            expected_target_root_id="a" * 32,
            role_policies={},
            scratch_reserve_bytes=1,
        ):
            pytest.fail("synthetic source cannot enter the production context")


@pytest.mark.parametrize(
    ("volume_uuid", "root_id"),
    [
        ("not-a-uuid", "a" * 32),
        ("11111111-2222-3333-4444-555555555555", "short"),
        ("11111111-2222-3333-4444-555555555555", "A" * 32),
    ],
)
def test_production_storage_pins_require_canonical_values(volume_uuid: str, root_id: str) -> None:
    with pytest.raises(ProductionCaptureV5Error, match="target_storage_pin_invalid"):
        _validate_pins(volume_uuid, root_id)


def test_real_view_requires_independent_plan_pin_before_revalidation() -> None:
    view = object.__new__(HeldDataT)
    view.synthetic = False
    view.os_api = object.__new__(DarwinDataOS)
    view.trusted_plan_sha256 = "a" * 64
    with pytest.raises(
        ProductionCaptureV5Error, match="production_source_view_not_real_or_unpinned"
    ):
        _require_real_held_view(view, "b" * 64)


def test_policy_roster_digest_binds_all_role_scopes_hashes_and_absences(
    tmp_path: Path,
) -> None:
    plan_sha = "d" * 64
    roster = {}
    for role in REQUIRED_ROOT_ROLES:
        roster[role] = SimpleNamespace(
            present=role == "owner_checkout",
            unresolved_reason=(None if role == "owner_checkout" else f"absent:{role}"),
        )
    plan = SimpleNamespace(plan=SimpleNamespace(root_roster=roster))
    policy_value = {
        "schema": POLICY_SCHEMA,
        "policy_id": "synthetic-roster-pin",
        "version": 1,
        "rule_set": "owner-work-sensitive-v1",
        "pem_candidate_dispositions": {},
        "exact_path_exclusions": {},
        "symlink_target_dispositions": {},
        "rebuild_inputs": [],
    }
    raw = canonical_json(policy_value)
    policy_path = tmp_path / "owner.policy.json"
    policy_path.write_bytes(raw)
    policies = {"owner_checkout": RoleCapturePolicy(policy_path, hashlib.sha256(raw).hexdigest())}
    expected_rows = []
    for role in sorted(REQUIRED_ROOT_ROLES):
        if role == "owner_checkout":
            expected_rows.append(
                {
                    "role": role,
                    "present": True,
                    "scope": "non_protected",
                    "policy_sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        else:
            expected_rows.append(
                {
                    "role": role,
                    "present": False,
                    "disposition": "absent_unresolved",
                    "unresolved_reason_sha256": hashlib.sha256(
                        f"absent:{role}".encode()
                    ).hexdigest(),
                    "scope": None,
                    "policy_sha256": None,
                }
            )
    from tools.brain_host_tree_capture_v5 import policy_roster_sha256_v5

    digest = policy_roster_sha256_v5(plan_sha, expected_rows)
    assert (
        _preflight_policy_roster(
            plan,
            policies,
            plan_sha256=plan_sha,
            expected_policy_roster_sha256=digest,
        )
        == digest
    )
    with pytest.raises(ProductionCaptureV5Error, match="production_policy_roster_pin_mismatch"):
        _preflight_policy_roster(
            plan,
            policies,
            plan_sha256=plan_sha,
            expected_policy_roster_sha256="0" * 64,
        )
    policy_path.write_bytes(raw + b"\n")
    with pytest.raises(ProductionCaptureV5Error, match="production_policy_file_pin_invalid"):
        _preflight_policy_roster(
            plan,
            policies,
            plan_sha256=plan_sha,
            expected_policy_roster_sha256=digest,
        )


def test_scratch_capability_pins_no_follow_path_and_rejects_source_alias(
    tmp_path: Path,
) -> None:
    scratch_path = tmp_path / "scratch"
    scratch_path.mkdir(mode=0o700)
    scratch_path.chmod(0o700)
    capability = PinnedScratchV5.open(scratch_path)
    device = os.fstat(capability.fd).st_dev
    try:
        _validate_scratch_devices(device + 1, device + 2, device + 3)
        with pytest.raises(ProductionCaptureV5Error, match="scratch_device_separation_invalid"):
            _validate_scratch_devices(device, device, device + 1)
        capability.validate(
            source_device=device + 1,
            target_device=device + 2,
            required_free_bytes=0,
        )
        with pytest.raises(ProductionCaptureV5Error, match="scratch_device_separation_invalid"):
            capability.validate(
                source_device=device,
                target_device=device + 1,
                required_free_bytes=0,
            )
        moved = tmp_path / "moved-scratch"
        scratch_path.rename(moved)
        scratch_path.mkdir(mode=0o700)
        scratch_path.chmod(0o700)
        with pytest.raises(ProductionCaptureV5Error, match="scratch_identity_or_capacity_invalid"):
            capability.validate(
                source_device=device + 1,
                target_device=device + 2,
                required_free_bytes=0,
            )
    finally:
        capability.close()


def test_scratch_path_rejects_symlink_directory(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    real.chmod(0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ProductionCaptureV5Error, match="scratch_path_open_failed"):
        PinnedScratchV5.open(alias)


def test_source_storage_separation_rejects_equal_volume_uuid_and_device(tmp_path: Path) -> None:
    directory = tmp_path / "root"
    directory.mkdir()
    root_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        source_device = os.fstat(root_fd).st_dev
        view = SimpleNamespace(
            plan=SimpleNamespace(data_volume_uuid="11111111-2222-3333-4444-555555555555"),
            root_fds={"owner_checkout": root_fd},
        )
        writer = SimpleNamespace(
            storage_volume_uuid="11111111-2222-3333-4444-555555555555",
            device=source_device + 1,
        )
        with pytest.raises(ProductionCaptureV5Error, match="source_storage_not_distinct"):
            _assert_writer_source_separation(view, writer, source_device)
        writer.storage_volume_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        writer.device = source_device
        with pytest.raises(ProductionCaptureV5Error, match="source_storage_not_distinct"):
            _assert_writer_source_separation(view, writer, source_device)
    finally:
        os.close(root_fd)


def _write_private_spec(path: Path, payload: bytes) -> str:
    path.write_bytes(payload)
    path.chmod(0o600)
    return hashlib.sha256(payload).hexdigest()


def test_capture_spec_requires_external_hash_private_mode_and_unique_json_keys(
    tmp_path: Path,
) -> None:
    spec = tmp_path / "operation.json"
    raw = json.dumps({"schema": CAPTURE_SPEC_SCHEMA}, separators=(",", ":")).encode()
    digest = _write_private_spec(spec, raw)
    assert _read_private_capture_spec(spec, digest) == {"schema": CAPTURE_SPEC_SCHEMA}
    with pytest.raises(ProductionCaptureV5CLIError, match="capture_spec_sha256_mismatch"):
        _read_private_capture_spec(spec, "0" * 64)
    spec.chmod(0o644)
    with pytest.raises(ProductionCaptureV5CLIError, match="capture_spec_private_file_invalid"):
        _read_private_capture_spec(spec, digest)
    duplicate = b'{"schema":"one","schema":"two"}'
    duplicate_digest = _write_private_spec(spec, duplicate)
    with pytest.raises(ProductionCaptureV5CLIError, match="capture_json_duplicate_key"):
        _read_private_capture_spec(spec, duplicate_digest)
    alias = tmp_path / "operation-alias.json"
    alias.symlink_to(spec)
    with pytest.raises(ProductionCaptureV5CLIError, match="capture_spec_path_invalid"):
        _read_private_capture_spec(alias, duplicate_digest)


def test_capture_spec_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "operation.fifo"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(ProductionCaptureV5CLIError, match="capture_spec_private_file_invalid"):
        _read_private_capture_spec(fifo, "a" * 64)


def test_capture_policy_roster_rejects_absence_downgrade_and_scope_mutation(
    tmp_path: Path,
) -> None:
    from tools.brain_host_held_data_t_v1 import RootDeclaration
    from tools.brain_host_tree_capture_v5 import policy_roster_sha256_v5

    plan_sha = "d" * 64
    declarations = {
        role: RootDeclaration(
            present=role == "owner_checkout",
            relative_locator="root" if role == "owner_checkout" else None,
            kind="directory" if role == "owner_checkout" else None,
            unresolved_reason=None if role == "owner_checkout" else f"absent:{role}",
        )
        for role in REQUIRED_ROOT_ROLES
    }
    policy_value = {
        "schema": POLICY_SCHEMA,
        "policy_id": "cli-policy",
        "version": 1,
        "rule_set": "owner-work-sensitive-v1",
        "pem_candidate_dispositions": {},
        "exact_path_exclusions": {},
        "symlink_target_dispositions": {},
        "rebuild_inputs": [],
    }
    policy_path = tmp_path / "owner.policy.json"
    raw = canonical_json(policy_value)
    policy_path.write_bytes(raw)
    policy_sha = hashlib.sha256(raw).hexdigest()
    roster: dict[str, object] = {}
    digest_rows = []
    for role in sorted(REQUIRED_ROOT_ROLES):
        if role == "owner_checkout":
            roster[role] = {
                "disposition": "present",
                "policy_path": str(policy_path),
                "policy_sha256": policy_sha,
                "scope": "non_protected",
            }
            digest_rows.append(
                {
                    "role": role,
                    "present": True,
                    "scope": "non_protected",
                    "policy_sha256": policy_sha,
                }
            )
        else:
            roster[role] = {
                "disposition": "absent_unresolved",
                "unresolved_reason": f"absent:{role}",
            }
            digest_rows.append(
                {
                    "role": role,
                    "present": False,
                    "disposition": "absent_unresolved",
                    "unresolved_reason_sha256": hashlib.sha256(
                        f"absent:{role}".encode()
                    ).hexdigest(),
                    "scope": None,
                    "policy_sha256": None,
                }
            )
    spec = {"role_roster": roster}
    policies, digest = _load_capture_policies(
        spec, SimpleNamespace(root_roster=declarations), plan_sha
    )
    assert set(policies) == {"owner_checkout"}
    assert digest == policy_roster_sha256_v5(plan_sha, digest_rows)

    roster["owner_codex_worktree"] = {
        "disposition": "absent_unresolved",
        "unresolved_reason": "silently-downgraded",
    }
    with pytest.raises(ProductionCaptureV5CLIError, match="capture_absent_role_reason_mismatch"):
        _load_capture_policies(spec, SimpleNamespace(root_roster=declarations), plan_sha)

    roster["owner_codex_worktree"] = {
        "disposition": "absent_unresolved",
        "unresolved_reason": "absent:owner_codex_worktree",
    }
    roster["owner_checkout"]["scope"] = "protected_opaque"
    _policies, changed_digest = _load_capture_policies(
        spec, SimpleNamespace(root_roster=declarations), plan_sha
    )
    assert changed_digest != digest


def test_capture_cli_pin_failure_is_path_free_and_has_false_authority(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = tmp_path / "private-operation.json"
    digest = _write_private_spec(spec, b'{"schema":"wrong"}')
    code = production_cli_main(
        [
            "capture",
            "--spec",
            str(spec),
            "--spec-sha256",
            digest,
            "--plan",
            str(tmp_path / "plan.json"),
            "--plan-sha256",
            "0" * 64,
            "--policy-roster-sha256",
            "1" * 64,
        ]
    )
    output = capsys.readouterr().out
    assert code == 2
    assert str(tmp_path) not in output
    assert '"capture_complete":false' in output
    assert '"erase_ready":false' in output
    assert '"activation_permitted":false' in output
    assert '"unaccepted_partial_candidate_may_remain":false' in output
    assert '"root_closure_verified":false' in output


def test_capture_result_is_provenance_bound_and_never_authorizes_activation() -> None:
    result = _capture_summary(
        {
            "candidate_id": "a" * 32,
            "index_sha256": "b" * 64,
            "entry_count": 7,
            "payload_bytes": 12,
            "readback_verified": True,
            "synthetic_fixture_only": False,
        },
        operation_spec_sha256="c" * 64,
        plan_sha256="d" * 64,
        policy_roster_sha256="e" * 64,
    )
    assert result["operation_spec_sha256"] == "c" * 64
    assert result["plan_sha256"] == "d" * 64
    assert result["policy_roster_sha256"] == "e" * 64
    assert result["capture_complete"] is False
    assert result["erase_ready"] is False
    assert result["activation_permitted"] is False
    failure = _capture_failure("candidate_write_failed", partial_candidate_possible=True)
    assert failure["unaccepted_partial_candidate_may_remain"] is True
    assert failure["activation_permitted"] is False
    secret_failure = _capture_failure("secrettoken123", partial_candidate_possible=True)
    assert secret_failure["error"] == "capture_failed"
    assert secret_failure["unaccepted_partial_candidate_may_remain"] is True


def test_capture_cli_argument_errors_are_path_free(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = production_cli_main(["capture", "--spec", "/private/secret/path.json"])
    output = capsys.readouterr().out
    assert code == 2
    assert "/private/secret" not in output
    assert '"error":"arguments_invalid"' in output


def test_verify_cli_does_not_echo_arbitrary_exception_text(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "storage.json"
    config.write_text("{}", encoding="utf-8")

    def fail_guard(*_args: object, **_kwargs: object) -> None:
        raise ValueError("secrettoken123")

    monkeypatch.setattr(storage, "read_only_guard", fail_guard)
    code = production_cli_main(
        [
            "verify",
            "a" * 32,
            "--index-sha256",
            "b" * 64,
            "--storage-config",
            str(config),
            "--target-volume-uuid",
            "11111111-2222-3333-4444-555555555555",
            "--target-root-id",
            "c" * 32,
            "--plan-sha256",
            "d" * 64,
            "--scratch-parent",
            str(tmp_path),
        ]
    )
    error = capsys.readouterr().err
    assert code == 2
    assert "verify_failed" in error
    assert "secrettoken123" not in error
    assert str(tmp_path) not in error


def test_capture_cli_reports_partial_candidate_as_unaccepted_and_path_free(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.brain_host_held_data_t_v1 import (
        ApprovedDataPlan,
        RootDeclaration,
        approved_data_plan_sha256,
    )
    from tools.brain_host_tree_capture_v5 import policy_roster_sha256_v5

    source_uuid = "11111111-2222-3333-4444-555555555555"
    target_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    declarations = {
        role: (
            RootDeclaration(True, "root", "directory", None)
            if role == "owner_checkout"
            else RootDeclaration(False, None, None, f"absent_{role}")
        )
        for role in REQUIRED_ROOT_ROLES
    }
    plan = ApprovedDataPlan(
        "synthetic-cli-partial-failure",
        source_uuid,
        "disk99s1",
        "disk99",
        source_uuid,
        declarations,
    )
    plan_document = {
        "schema": "brain-host-approved-data-plan-v1",
        "approval_id": plan.approval_id,
        "data_volume_uuid": plan.data_volume_uuid,
        "data_device_identifier": plan.data_device_identifier,
        "data_container_reference": plan.data_container_reference,
        "data_volume_group_id": plan.data_volume_group_id,
        "root_roster": {
            role: {
                "present": declaration.present,
                "relative_locator": declaration.relative_locator,
                "kind": declaration.kind,
                "unresolved_reason": declaration.unresolved_reason,
            }
            for role, declaration in declarations.items()
        },
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan_document), encoding="utf-8")
    plan_sha = approved_data_plan_sha256(plan)

    policy_path = tmp_path / "policy.json"
    policy_raw = canonical_json(
        {
            "schema": POLICY_SCHEMA,
            "policy_id": "injected-partial-failure",
            "version": 1,
            "rule_set": "owner-work-sensitive-v1",
            "pem_candidate_dispositions": {},
            "exact_path_exclusions": {},
            "symlink_target_dispositions": {},
            "rebuild_inputs": [],
        }
    )
    policy_path.write_bytes(policy_raw)
    policy_sha = hashlib.sha256(policy_raw).hexdigest()
    role_roster = {}
    digest_rows = []
    for role in sorted(REQUIRED_ROOT_ROLES):
        declaration = declarations[role]
        if declaration.present:
            role_roster[role] = {
                "disposition": "present",
                "policy_path": str(policy_path),
                "policy_sha256": policy_sha,
                "scope": "non_protected",
            }
            digest_rows.append(
                {
                    "role": role,
                    "present": True,
                    "scope": "non_protected",
                    "policy_sha256": policy_sha,
                }
            )
        else:
            role_roster[role] = {
                "disposition": "absent_unresolved",
                "unresolved_reason": declaration.unresolved_reason,
            }
            digest_rows.append(
                {
                    "role": role,
                    "present": False,
                    "disposition": "absent_unresolved",
                    "unresolved_reason_sha256": hashlib.sha256(
                        declaration.unresolved_reason.encode()
                    ).hexdigest(),
                    "scope": None,
                    "policy_sha256": None,
                }
            )
    policy_roster_sha = policy_roster_sha256_v5(plan_sha, digest_rows)
    config_path = tmp_path / "storage.json"
    config_path.write_text("{}", encoding="utf-8")
    spec_document = {
        "schema": CAPTURE_SPEC_SCHEMA,
        "source_mountpoint": str(tmp_path),
        "storage_config_path": str(config_path),
        "target_volume_uuid": target_uuid,
        "target_root_id": "b" * 32,
        "scratch_reserve_bytes": 1,
        "metadata_reserve_bytes": 0,
        "payload_reserve_bytes": 0,
        "role_roster": role_roster,
    }
    spec_path = tmp_path / "operation.json"
    spec_raw = json.dumps(spec_document, sort_keys=True, separators=(",", ":")).encode()
    spec_sha = _write_private_spec(spec_path, spec_raw)

    class FakeHeldView:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> FakeHeldView:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class FailingSession:
        def capture_candidate(self) -> dict[str, object]:
            raise ValueError("secrettoken123")

        def __enter__(self) -> FailingSession:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    @contextlib.contextmanager
    def fake_factory(*_args: object, **_kwargs: object):
        yield FailingSession()

    monkeypatch.setattr(production_module, "HeldDataT", FakeHeldView)
    monkeypatch.setattr(production_module, "production_capture_v5", fake_factory)
    code = production_cli_main(
        [
            "capture",
            "--spec",
            str(spec_path),
            "--spec-sha256",
            spec_sha,
            "--plan",
            str(plan_path),
            "--plan-sha256",
            plan_sha,
            "--policy-roster-sha256",
            policy_roster_sha,
        ]
    )
    output = capsys.readouterr().out
    assert code == 2
    assert str(tmp_path) not in output
    assert '"error":"capture_failed"' in output
    assert "secrettoken123" not in output
    assert '"unaccepted_partial_candidate_may_remain":true' in output
    assert '"capture_complete":false' in output
    assert '"activation_permitted":false' in output

    class SuccessfulSession:
        def capture_candidate(self) -> dict[str, object]:
            return {
                "candidate_id": "a" * 32,
                "index_sha256": "b" * 64,
                "entry_count": 1,
                "payload_bytes": 1,
                "readback_verified": True,
                "synthetic_fixture_only": False,
            }

        def __enter__(self) -> SuccessfulSession:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    @contextlib.contextmanager
    def exit_failing_factory(*_args: object, **_kwargs: object):
        yield SuccessfulSession()
        raise ValueError("private teardown detail")

    monkeypatch.setattr(production_module, "production_capture_v5", exit_failing_factory)
    code = production_cli_main(
        [
            "capture",
            "--spec",
            str(spec_path),
            "--spec-sha256",
            spec_sha,
            "--plan",
            str(plan_path),
            "--plan-sha256",
            plan_sha,
            "--policy-roster-sha256",
            policy_roster_sha,
        ]
    )
    output = capsys.readouterr().out
    assert code == 2
    assert str(tmp_path) not in output
    assert '"error":"capture_failed"' in output
    assert "private teardown detail" not in output
    assert '"unaccepted_partial_candidate_may_remain":true' in output


def test_production_verifier_rejects_unbound_writer_before_scratch_use(tmp_path: Path) -> None:
    root = tmp_path / "candidate-root"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(root_fd)
    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o700)
    scratch_fd = os.open(scratch, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(
            ProductionCaptureV5Error,
            match="production_verify_read_only_candidate_guard_required",
        ):
            verify_production_candidate_v5(
                writer,
                "c" * 32,
                expected_target_volume_uuid="11111111-2222-3333-4444-555555555555",
                expected_target_root_id="a" * 32,
                scratch=None,
                expected_index_sha256="0" * 64,
                expected_plan_sha256="d" * 64,
            )
        assert list(scratch.iterdir()) == []
    finally:
        os.close(scratch_fd)
        writer.close()


def test_bound_candidate_verifier_requires_explicit_scratch_fd(tmp_path: Path) -> None:
    root = tmp_path / "candidate-root"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    guard = object.__new__(StorageGuard)
    guard.root = SimpleNamespace(
        volume_uuid="11111111-2222-3333-4444-555555555555",
        root_id="a" * 32,
    )
    writer = DescriptorWriter(root_fd, storage_guard=guard)
    try:
        with pytest.raises(
            TreeCaptureV5Error, match="candidate_verify_scratch_capability_required"
        ):
            verify_candidate_v5(writer, "c" * 32)
    finally:
        writer.close()
        os.close(root_fd)
