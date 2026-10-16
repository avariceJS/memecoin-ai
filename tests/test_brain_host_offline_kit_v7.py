from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tools import brain_host_offline_kit_v7 as kit

REPO = Path(__file__).resolve().parents[1]


def _run_git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result.stdout


def _fixture_repo(
    tmp_path: Path,
    *,
    omit: str | None = None,
    injected_import: bool = False,
    injected_dynamic_import: bool = False,
    injected_resource_read: bool = False,
    injected_alias_dynamic_import: bool = False,
    injected_alias_resource_read: bool = False,
    injected_transitive_resource_alias: bool = False,
    injected_assignment_alias_dynamic_import: bool = False,
    injected_builtins_import: bool = False,
    injected_reflective_import: bool = False,
    altered_builder: bool = False,
):
    repo = tmp_path / "fixture-repo"
    repo.mkdir(parents=True)
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "fixture@example.invalid")
    _run_git(repo, "config", "user.name", "Fixture")
    source_commit = _run_git(REPO, "rev-parse", "HEAD").decode().strip()
    for row in kit.SOURCE_ROSTER.values():
        path = row["path"]
        if path == omit:
            continue
        raw = _run_git(REPO, "cat-file", "blob", f"{source_commit}:{path}")
        if injected_import and path == "tools/brain_host_tree_capture_v5_production.py":
            raw += b"\nimport tools.brain_unlisted_helper\n"
        if injected_dynamic_import and path == "tools/brain_host_tree_capture_v5_production.py":
            raw += b"\nimport importlib\nimportlib.import_module('tools.brain_host_dynamic')\n"
        if injected_resource_read and path == "tools/brain_host_tree_capture_v5_production.py":
            raw += b"\nimport importlib.resources as resources\nresources.files('tools')\n"
        if (
            injected_alias_dynamic_import
            and path == "tools/brain_host_tree_capture_v5_production.py"
        ):
            raw += (
                b"\nfrom importlib import import_module as load_local\n"
                b"load_local('tools.brain_unlisted')\n"
            )
        if (
            injected_alias_resource_read
            and path == "tools/brain_host_tree_capture_v5_production.py"
        ):
            raw += b"\nimport importlib as il\nil.resources.files('tools')\n"
        if (
            injected_transitive_resource_alias
            and path == "tools/brain_host_tree_capture_v5_production.py"
        ):
            raw += b"\nimport importlib\nr = importlib.resources\ns = r\ns.files('tools')\n"
        if (
            injected_assignment_alias_dynamic_import
            and path == "tools/brain_host_tree_capture_v5_production.py"
        ):
            raw += (
                b"\nfrom importlib import import_module as load_local\n"
                b"alias = load_local\nalias('tools.brain_unlisted')\n"
            )
        if injected_builtins_import and path == "tools/brain_host_tree_capture_v5_production.py":
            raw += b"\nimport builtins\nbuiltins.__import__('tools.brain_unlisted')\n"
        if injected_reflective_import and path == "tools/brain_host_tree_capture_v5_production.py":
            raw += (
                b"\nimport importlib\ngetattr(importlib, 'import_module')('tools.brain_unlisted')\n"
            )
        destination = repo / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    if injected_import:
        rogue = repo / "tools/brain_unlisted_helper.py"
        rogue.write_text("VALUE = 1\n", encoding="utf-8")
    builder_bytes = Path(kit.__file__).read_bytes()
    if altered_builder:
        builder_bytes += b"\n# altered pinned builder\n"
    builder_path = repo / kit.BUILDER_PATH
    builder_path.parent.mkdir(parents=True, exist_ok=True)
    builder_path.write_bytes(builder_bytes)
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-qm", "fixture source")
    commit = _run_git(repo, "rev-parse", "HEAD").decode().strip()
    return repo, commit


def _isolated_env(source_zip: Path, home: Path) -> dict[str, str]:
    home.mkdir(mode=0o700)
    return {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": f"{source_zip}{os.pathsep}{source_zip}/src",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _archive_process(
    source_zip: Path,
    neutral: Path,
    home: Path,
    args: list[str],
) -> subprocess.CompletedProcess[str]:
    neutral.mkdir(mode=0o700, exist_ok=True)
    return subprocess.run(
        [sys.executable, "-S", *args],
        cwd=neutral,
        env=_isolated_env(source_zip, home),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_source_builder_is_byte_deterministic_and_archive_only_entrypoints_load(
    tmp_path: Path,
) -> None:
    seed, commit = _fixture_repo(tmp_path / "source-seed")
    outputs = []
    clones = []
    for number in (1, 2):
        clone = tmp_path / f"clean-clone-{number}"
        _run_git(seed.parent, "clone", "--quiet", str(seed), str(clone))
        assert not _run_git(clone, "status", "--porcelain").strip()
        clones.append(clone)
        out = tmp_path / f"build-{number}"
        out.mkdir(mode=0o700)
        source_zip = out / "source.zip"
        manifest_path = out / "MANIFEST.json"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.brain_host_offline_kit_v7",
                "build-source",
                "--repo",
                str(clone),
                "--commit",
                commit,
                "--source-zip",
                str(source_zip),
                "--manifest",
                str(manifest_path),
            ],
            cwd=clone,
            env={
                "HOME": str(out),
                "PATH": "/usr/bin:/bin",
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        outputs.append((source_zip.read_bytes(), manifest_path.read_bytes(), manifest))
    first_zip, first_manifest, manifest = outputs[0]
    assert outputs[1][:2] == outputs[0][:2]
    assert manifest["source_commit"] == commit
    assert manifest["source_roster_version"] == kit.ROSTER_VERSION
    assert manifest["builder"]["path"] == kit.BUILDER_PATH
    assert manifest["builder"]["git_blob_oid"]
    assert (
        manifest["builder"]["sha256"] == hashlib.sha256(Path(kit.__file__).read_bytes()).hexdigest()
    )
    assert manifest["entrypoints"] == kit.ENTRYPOINTS
    assert manifest["minimum_macos"] == "26.0"
    assert manifest["runtime_requirement"]["delivery"].endswith(
        "not emitted by this source-only builder"
    )
    assert manifest["source_archive_sha256"] == hashlib.sha256(first_zip).hexdigest()
    assert len(manifest["source_members"]) == len(kit.SOURCE_ROSTER)
    kit.verify_source_archive(first_zip, manifest)

    source_zip = tmp_path / "build-1" / "source.zip"
    manifest_members = manifest["source_members"]
    origins_code = (
        "import importlib,json,sys; names=json.loads(sys.argv[1]); "
        "mods={name:importlib.import_module(name) for name in names}; "
        "print(json.dumps({name:mod.__file__ for name,mod in mods.items()}))"
    )
    origins = _archive_process(
        source_zip,
        tmp_path / "neutral-origins",
        tmp_path / "empty-home-origins",
        ["-c", origins_code, json.dumps([row["module"] for row in manifest_members])],
    )
    assert origins.returncode == 0, origins.stdout + origins.stderr
    loaded = json.loads(origins.stdout)
    assert set(loaded) == {row["module"] for row in manifest_members}
    assert all(value.startswith(f"{source_zip}/") for value in loaded.values())

    help_invocations = [
        ["-m", "tools.brain_host_recovery", "--help"],
        ["-m", "tools.brain_host_recovery_preflight_v1", "--help"],
        ["-m", "tools.brain_host_held_data_t_cli_v1", "--help"],
        ["-m", "tools.brain_host_tree_capture_v5_production", "--help"],
        ["-m", "tools.brain_host_tree_capture_v5_production", "plan-capacity", "--help"],
        ["-m", "tools.brain_host_tree_capture_v5_production", "capture", "--help"],
    ]
    for index, argv in enumerate(help_invocations):
        result = _archive_process(
            source_zip,
            tmp_path / f"neutral-help-{index}",
            tmp_path / f"empty-home-help-{index}",
            argv,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    invalid_source_preflight = _archive_process(
        source_zip,
        tmp_path / "neutral-invalid-recovery-preflight",
        tmp_path / "empty-home-invalid-recovery-preflight",
        [
            "-m",
            "tools.brain_host_recovery_preflight_v1",
            "--expected-data-uuid",
            "invalid-uuid",
            "--expected-volume-group-uuid",
            "11111111-1111-4111-8111-111111111111",
            "--expected-container-uuid",
            "22222222-2222-4222-8222-222222222222",
            "--expected-device-identifier",
            "disk3s5",
        ],
    )
    assert invalid_source_preflight.returncode == 2
    source_preflight = json.loads(invalid_source_preflight.stdout)
    assert source_preflight["state"] == "invalid_request"
    assert source_preflight["reason"] == "expected_data_uuid_required"
    assert source_preflight["no_prior_writable_mount_proven"] is False
    assert source_preflight["safe_to_erase"] is False

    bad_spec = tmp_path / "bad-spec.json"
    bad_spec.write_bytes(b"{}")
    bad_spec.chmod(0o600)
    bad_spec_before = hashlib.sha256(bad_spec.read_bytes()).hexdigest()
    bad_hash = "0" * 64
    plan_sha = "1" * 64
    policy_sha = "2" * 64
    inert_preflights = [
        [
            "-m",
            "tools.brain_host_tree_capture_v5_production",
            "plan-capacity",
            "--spec",
            str(bad_spec),
            "--spec-sha256",
            bad_hash,
            "--plan",
            str(tmp_path / "must-not-open-plan.json"),
            "--plan-sha256",
            plan_sha,
            "--policy-roster-sha256",
            policy_sha,
            "--scratch-parent",
            str(tmp_path / "must-not-open-scratch"),
        ],
        [
            "-m",
            "tools.brain_host_tree_capture_v5_production",
            "capture",
            "--spec",
            str(bad_spec),
            "--spec-sha256",
            bad_hash,
            "--plan",
            str(tmp_path / "must-not-open-plan.json"),
            "--plan-sha256",
            plan_sha,
            "--policy-roster-sha256",
            policy_sha,
        ],
    ]
    for index, argv in enumerate(inert_preflights):
        result = _archive_process(
            source_zip,
            tmp_path / f"neutral-preflight-{index}",
            tmp_path / f"empty-home-preflight-{index}",
            argv,
        )
        assert result.returncode == 2
        response = json.loads(result.stdout)
        assert response["error"] == "capture_spec_sha256_mismatch"
        assert response.get("capture_authorized", False) is False
        assert response["erase_ready"] is False
        assert response.get("activation_permitted", False) is False
    assert hashlib.sha256(bad_spec.read_bytes()).hexdigest() == bad_spec_before
    assert not (tmp_path / "must-not-open-scratch").exists()


def test_builder_rejects_missing_module_and_unlisted_static_import(tmp_path: Path) -> None:
    repo, commit = _fixture_repo(tmp_path / "missing", omit="tools/brain_host_tree_capture_v5.py")
    with pytest.raises(kit.OfflineKitV7Error, match="source_roster_module_missing"):
        kit.build_source_kit(repo, commit)

    import_repo, import_commit = _fixture_repo(tmp_path / "unlisted-import", injected_import=True)
    with pytest.raises(kit.OfflineKitV7Error, match="local_static_import_outside_roster"):
        kit.build_source_kit(import_repo, import_commit)

    dynamic_repo, dynamic_commit = _fixture_repo(
        tmp_path / "dynamic-import", injected_dynamic_import=True
    )
    with pytest.raises(kit.OfflineKitV7Error, match="dynamic_import_without_review_reason"):
        kit.build_source_kit(dynamic_repo, dynamic_commit)

    resource_repo, resource_commit = _fixture_repo(
        tmp_path / "resource-read", injected_resource_read=True
    )
    with pytest.raises(kit.OfflineKitV7Error, match="package_resource_without_review_reason"):
        kit.build_source_kit(resource_repo, resource_commit)

    alias_dynamic_repo, alias_dynamic_commit = _fixture_repo(
        tmp_path / "alias-dynamic-import", injected_alias_dynamic_import=True
    )
    with pytest.raises(kit.OfflineKitV7Error, match="dynamic_import_without_review_reason"):
        kit.build_source_kit(alias_dynamic_repo, alias_dynamic_commit)

    alias_resource_repo, alias_resource_commit = _fixture_repo(
        tmp_path / "alias-resource-read", injected_alias_resource_read=True
    )
    with pytest.raises(kit.OfflineKitV7Error, match="package_resource_without_review_reason"):
        kit.build_source_kit(alias_resource_repo, alias_resource_commit)

    transitive_resource_repo, transitive_resource_commit = _fixture_repo(
        tmp_path / "transitive-resource-alias",
        injected_transitive_resource_alias=True,
    )
    with pytest.raises(kit.OfflineKitV7Error, match="package_resource_without_review_reason"):
        kit.build_source_kit(transitive_resource_repo, transitive_resource_commit)

    assignment_alias_repo, assignment_alias_commit = _fixture_repo(
        tmp_path / "assignment-alias-dynamic-import",
        injected_assignment_alias_dynamic_import=True,
    )
    with pytest.raises(kit.OfflineKitV7Error, match="dynamic_import_without_review_reason"):
        kit.build_source_kit(assignment_alias_repo, assignment_alias_commit)

    builtins_repo, builtins_commit = _fixture_repo(
        tmp_path / "builtins-dynamic-import", injected_builtins_import=True
    )
    with pytest.raises(kit.OfflineKitV7Error, match="dynamic_import_without_review_reason"):
        kit.build_source_kit(builtins_repo, builtins_commit)

    reflective_repo, reflective_commit = _fixture_repo(
        tmp_path / "reflective-dynamic-import", injected_reflective_import=True
    )
    with pytest.raises(kit.OfflineKitV7Error, match="dynamic_import_without_review_reason"):
        kit.build_source_kit(reflective_repo, reflective_commit)

    altered_builder_repo, altered_builder_commit = _fixture_repo(
        tmp_path / "altered-builder", altered_builder=True
    )
    with pytest.raises(kit.OfflineKitV7Error, match="executed_builder_differs_from_pinned_commit"):
        kit.build_source_kit(altered_builder_repo, altered_builder_commit)


def test_builder_rejects_worktree_substitution_and_symlink_sources(tmp_path: Path) -> None:
    repo, commit = _fixture_repo(tmp_path / "dirty")
    member = repo / kit.SOURCE_ROSTER["tools.brain_host_tree_capture_v5"]["path"]
    member.write_bytes(member.read_bytes() + b"\n# unpinned worktree edit\n")
    with pytest.raises(kit.OfflineKitV7Error, match="rostered_worktree_differs"):
        kit.build_source_kit(repo, commit)

    symlink_repo, symlink_commit = _fixture_repo(tmp_path / "symlink")
    path = symlink_repo / kit.SOURCE_ROSTER["tools.brain_host_tree_capture_v5"]["path"]
    path.unlink()
    path.symlink_to("brain_host_tree_capture_v5.py")
    _run_git(symlink_repo, "add", "-A")
    _run_git(symlink_repo, "commit", "-qm", "replace source with symlink")
    symlink_commit = _run_git(symlink_repo, "rev-parse", "HEAD").decode().strip()
    with pytest.raises(kit.OfflineKitV7Error, match="source_roster_member_not_regular_blob"):
        kit.build_source_kit(symlink_repo, symlink_commit)


def test_archive_verifier_rejects_unlisted_member(tmp_path: Path) -> None:
    repo, commit = _fixture_repo(tmp_path / "archive-verifier")
    archive_bytes, manifest, _ = kit.build_source_kit(repo, commit)
    injected = tmp_path / "injected.zip"
    injected.write_bytes(archive_bytes)
    with zipfile.ZipFile(injected, "a", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("tools/unlisted.py", b"VALUE = 1\n")
    with pytest.raises(kit.OfflineKitV7Error, match="source_archive_digest_mismatch"):
        kit.verify_source_archive(injected.read_bytes(), manifest)
