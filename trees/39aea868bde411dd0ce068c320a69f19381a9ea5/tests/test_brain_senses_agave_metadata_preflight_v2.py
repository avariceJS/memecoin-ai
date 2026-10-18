from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from brain_senses_agave_dependency_fetch_v1 import EXPECTED_REGISTRY_SOURCE  # noqa: E402

from tools import brain_senses_agave_metadata_preflight_v2 as preflight  # noqa: E402
from tools.brain_senses_build_storage_guard import GuardFailure, Snapshot  # noqa: E402


class _Provider:
    def __init__(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot

    def inspect(self) -> Snapshot:
        return self.snapshot


class _Process:
    pid = 987_654_321

    def __init__(self, stdout: bytes, stderr: bytes = b"", status: int = 0) -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.status = status

    def poll(self) -> int:
        return self.status

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.status


def _metadata_bytes() -> bytes:
    package_ids = [f"pkg {index}" for index in range(preflight.EXPECTED_RESOLVED_TUPLE_COUNT)]
    value = {
        "version": 1,
        "packages": [
            {
                "id": package_id,
                "name": f"p{index}",
                "version": "1.0.0",
                "source": (
                    None
                    if index < preflight.EXPECTED_SOURCE_NULL_WORKSPACE_MEMBERS
                    else preflight.EXPECTED_GIT_SOURCE
                    if index == preflight.EXPECTED_SOURCE_NULL_WORKSPACE_MEMBERS
                    else EXPECTED_REGISTRY_SOURCE
                ),
            }
            for index, package_id in enumerate(package_ids)
        ],
        "workspace_members": package_ids[: preflight.EXPECTED_SOURCE_NULL_WORKSPACE_MEMBERS],
        "resolve": {
            "root": package_ids[0],
            "nodes": [
                {
                    "id": package_id,
                    "deps": (
                        [{"pkg": package_ids[1], "dep_kinds": [{"kind": None, "target": None}]}]
                        if index == 0
                        else []
                    ),
                    "features": [],
                }
                for index, package_id in enumerate(package_ids)
            ],
        },
    }
    return json.dumps(value, separators=(",", ":")).encode()


def _lock_bytes() -> bytes:
    rows: list[tuple[str, str, str | None]] = []
    for index in range(preflight.EXPECTED_RESOLVED_TUPLE_COUNT):
        source = (
            None
            if index < preflight.EXPECTED_SOURCE_NULL_WORKSPACE_MEMBERS
            else preflight.EXPECTED_GIT_SOURCE
            if index == preflight.EXPECTED_SOURCE_NULL_WORKSPACE_MEMBERS
            else EXPECTED_REGISTRY_SOURCE
        )
        rows.append((f"p{index}", "1.0.0", source))
    rows.extend(
        (f"lock-only-{index}", "1.0.0", EXPECTED_REGISTRY_SOURCE)
        for index in range(preflight.EXPECTED_LOCK_ONLY_TUPLE_COUNT)
    )
    pieces = ["version = 4", ""]
    for name, version, source in rows:
        pieces.extend(("[[package]]", f'name = "{name}"', f'version = "{version}"'))
        if source is not None:
            pieces.append(f'source = "{source}"')
        pieces.append("")
    return "\n".join(pieces).encode()


def _layout(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path, _Provider]:
    root = tmp_path / "BrainData"
    root.mkdir(mode=0o700)
    source = root / "agave-source"
    source.mkdir(mode=0o700)
    (source / ".git").mkdir(mode=0o700)
    (source / "Cargo.lock").write_bytes(_lock_bytes())
    (root / ".research-root.json").write_text(
        json.dumps(
            {
                "data_root": "BrainData",
                "root_id": "a" * 32,
                "version": 1,
                "volume_uuid": "11111111-2222-3333-4444-555555555555",
            }
        )
    )
    (root / ".research-root.json").chmod(0o600)
    cache_source = root / "qualified-cache"
    cache_source.mkdir(mode=0o700)
    (cache_source / "registry").mkdir(mode=0o700)
    (cache_source / "registry" / "index-marker").write_bytes(b"pinned index")
    cargo_home = root / preflight.METADATA_STAGE_RELATIVE / "cargo-home"
    target_dir = root / preflight.METADATA_STAGE_RELATIVE / "target"
    tmpdir = root / preflight.METADATA_STAGE_RELATIVE / "tmp"
    snapshot = Snapshot(
        root_id="a" * 32,
        volume_uuid="11111111-2222-3333-4444-555555555555",
        mount_path=tmp_path,
        root_path=root,
        available_bytes=100 * 1024**3,
        mount_device=root.stat().st_dev,
        mount_inode=tmp_path.stat().st_ino,
    )
    return source, cache_source, cargo_home, target_dir, tmpdir, _Provider(snapshot)


def _patch_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(preflight, "_check_unprivileged_root1", lambda: None)
    monkeypatch.setattr(shutil, "which", lambda command, path=None: "/mock/bin/cargo")
    monkeypatch.setattr(
        preflight,
        "_source_pins",
        lambda source, cargo_home: ("b" * 40, "c" * 64, "d" * 64, 1075, "e" * 64),
    )


def test_metadata_stage_copies_cache_and_runs_one_default_feature_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, cache_source, cargo_home, target_dir, tmpdir, provider = _layout(tmp_path)
    source_manifest = preflight._locked_cache_manifest(cache_source)
    invocations: list[tuple[list[str], dict[str, str]]] = []
    _patch_host(monkeypatch)

    def fake_popen(command: list[str], **kwargs: Any) -> _Process:
        invocations.append((command, kwargs["env"]))
        unpacked = cargo_home / "registry" / "src" / "index-hash" / "p141-1.0.0"
        unpacked.mkdir(parents=True, mode=0o700)
        (unpacked / "Cargo.toml").write_text("[package]\nname='p0'\nversion='1.0.0'\n")
        return _Process(_metadata_bytes())

    result = preflight.run_metadata_preflight(
        source,
        cache_source,
        cargo_home,
        target_dir,
        tmpdir,
        expected_cache_manifest_sha256=source_manifest,
        snapshot_provider=provider,
        popen=fake_popen,
        sleep=lambda _: None,
    )

    assert result.status == "offline_metadata_resolved_no_build"
    assert result.default_features is True
    assert result.feature_intent == "default_features_only"
    assert result.cache_source_manifest_sha256 == source_manifest
    assert result.cache_manifest_sha256 == source_manifest
    assert result.cache_after_manifest_sha256 != source_manifest
    assert result.cache_extraction_delta_sha256 != "0" * 64
    assert result.cache_copy_entries == 2
    assert result.cache_copy_bytes == len(b"pinned index")
    assert result.lock_tuple_count == 1_075
    assert result.graph_tuple_count == 960
    assert result.lock_only_tuple_count == 115
    assert result.source_null_workspace_member_count == 140
    process_receipt = provider.snapshot.root_path / result.process_receipt_path
    graph_receipt = provider.snapshot.root_path / result.graph_receipt_path
    assert hashlib.sha256(process_receipt.read_bytes()).hexdigest() == result.process_receipt_sha256
    assert hashlib.sha256(graph_receipt.read_bytes()).hexdigest() == result.graph_receipt_sha256
    assert json.loads(graph_receipt.read_bytes()) == result.graph
    assert process_receipt.stat().st_mode & 0o777 == 0o600
    assert graph_receipt.stat().st_mode & 0o777 == 0o600
    assert preflight._locked_cache_manifest(cache_source) == source_manifest
    assert len(invocations) == 1
    command, env = invocations[0]
    assert command[1:8] == [
        "metadata",
        "--offline",
        "--locked",
        "--filter-platform",
        "aarch64-apple-darwin",
        "--format-version",
        "1",
    ]
    assert "--features" not in command and "--all-features" not in command
    assert env["CARGO_HOME"] == str(cargo_home)
    assert env["CARGO_NET_OFFLINE"] == "true"


def test_cache_manifest_allows_only_pinned_crossbeam_pack_hardlinks(tmp_path: Path) -> None:
    cache = tmp_path / "cargo-home"
    db_pack = cache / "git/db/crossbeam-5268678e7223f484/objects/pack/pack-pinned.idx"
    checkout_pack = (
        cache / "git/checkouts/crossbeam-5268678e7223f484/fd279d7/.git/objects/pack/pack-pinned.idx"
    )
    db_pack.parent.mkdir(parents=True)
    checkout_pack.parent.mkdir(parents=True)
    db_pack.write_bytes(b"pinned pack index")
    os.link(db_pack, checkout_pack)

    rows = preflight._tree_manifest_rows(cache)
    assert len([row for row in rows if row["kind"] == "file"]) == 2

    external = cache / "git/db/crossbeam-5268678e7223f484/objects/pack/pack-external.idx"
    external.write_bytes(b"external hardlink")
    os.link(external, tmp_path / "external-hardlink.idx")
    with pytest.raises(preflight.MetadataPreflightError, match="cache_file_identity"):
        preflight._tree_manifest_rows(cache)


def test_local_package_id_redaction_preserves_distinct_identity() -> None:
    value = {
        "version": 1,
        "packages": [
            {"id": "path+file:///private/a#workspace@1.0.0", "name": "a", "version": "1.0.0"},
            {"id": "path+file:///private/b#workspace@1.0.0", "name": "b", "version": "1.0.0"},
        ],
        "workspace_members": ["path+file:///private/a#workspace@1.0.0"],
        "resolve": {
            "root": "path+file:///private/a#workspace@1.0.0",
            "nodes": [
                {"id": "path+file:///private/a#workspace@1.0.0", "deps": [], "features": []},
                {"id": "path+file:///private/b#workspace@1.0.0", "deps": [], "features": []},
            ],
        },
    }

    graph = preflight._metadata_graph(json.dumps(value).encode())
    package_ids = [row["id"] for row in graph["packages"]]
    assert len(package_ids) == len(set(package_ids)) == 2
    assert all("/private/" not in package_id for package_id in package_ids)
    expected = hashlib.sha256(
        preflight._canonical(
            {
                "raw_package_id": "path+file:///private/a#workspace@1.0.0",
                "name": "a",
                "version": "1.0.0",
            }
        )
    ).hexdigest()
    assert f"local+sha256:{expected}" in package_ids
    assert len(expected) == 64


def test_local_package_id_alias_collision_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    value = {
        "version": 1,
        "packages": [
            {"id": "path+file:///private/a#a@1.0.0", "name": "a", "version": "1.0.0"},
            {"id": "path+file:///private/b#b@1.0.0", "name": "b", "version": "1.0.0"},
        ],
        "workspace_members": ["path+file:///private/a#a@1.0.0"],
        "resolve": {
            "root": "path+file:///private/a#a@1.0.0",
            "nodes": [
                {"id": "path+file:///private/a#a@1.0.0", "deps": [], "features": []},
                {"id": "path+file:///private/b#b@1.0.0", "deps": [], "features": []},
            ],
        },
    }
    monkeypatch.setattr(preflight, "_safe_package_id", lambda package_id, package: "same-alias")
    with pytest.raises(
        preflight.MetadataPreflightError, match="metadata_package_identity_collision"
    ):
        preflight._metadata_graph(json.dumps(value).encode())


def test_local_package_id_digest_binds_name_and_version() -> None:
    raw_id = "path+file:///private/project#project@1.0.0"
    first = preflight._safe_package_id(raw_id, {"name": "project", "version": "1.0.0"})
    second = preflight._safe_package_id(raw_id, {"name": "other", "version": "2.0.0"})
    assert first != second
    assert len(first.split(":", 1)[1]) == 64


def test_metadata_graph_preserves_root_members_and_dependency_edges() -> None:
    root_id = "path+file:///Users/user/project#root@1.0.0"
    dependency_id = "path+file:///Users/user/dependency#dep@2.0.0"
    value = {
        "version": 1,
        "packages": [
            {"id": root_id, "name": "root", "version": "1.0.0", "source": None},
            {"id": dependency_id, "name": "dep", "version": "2.0.0", "source": None},
        ],
        "workspace_members": [root_id],
        "resolve": {
            "root": root_id,
            "nodes": [
                {
                    "id": root_id,
                    "deps": [{"pkg": dependency_id, "dep_kinds": [{"kind": None, "target": None}]}],
                    "features": ["default"],
                },
                {"id": dependency_id, "deps": [], "features": []},
            ],
        },
    }
    graph = preflight._metadata_graph(json.dumps(value).encode())
    root_safe = next(row["id"] for row in graph["packages"] if row["name"] == "root")
    dep_safe = next(row["id"] for row in graph["packages"] if row["name"] == "dep")
    assert graph["root"] == root_safe
    assert graph["workspace_members"] == [root_safe]
    root_node = next(row for row in graph["nodes"] if row["id"] == root_safe)
    assert root_node["deps"] == [{"pkg": dep_safe, "dep_kinds": [{"kind": None, "target": None}]}]


def test_resolved_graph_tuple_counts_and_lock_membership(tmp_path: Path) -> None:
    lock_path = tmp_path / "Cargo.lock"
    lock_path.write_bytes(_lock_bytes())
    graph = preflight._metadata_graph(_metadata_bytes())
    counts = preflight._validate_resolved_graph(graph, lock_path)
    assert counts == {
        "lock_tuple_count": 1_075,
        "graph_tuple_count": 960,
        "lock_only_tuple_count": 115,
        "source_null_workspace_member_count": 140,
    }


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("duplicate_tuple", "resolved_graph_identity_collision"),
        ("missing_root", "resolved_graph_root_closure"),
        ("missing_dependency", "resolved_graph_dependency_closure"),
        ("tuple_not_locked", "resolved_graph_not_in_lock"),
    ],
)
def test_resolved_graph_rejects_bad_identity_closure(
    tmp_path: Path, mutation: str, error: str
) -> None:
    lock_path = tmp_path / "Cargo.lock"
    lock_path.write_bytes(_lock_bytes())
    graph = preflight._metadata_graph(_metadata_bytes())
    if mutation == "duplicate_tuple":
        graph["packages"][1]["name"] = graph["packages"][0]["name"]
        graph["packages"][1]["source"] = graph["packages"][0]["source"]
    elif mutation == "missing_root":
        graph["root"] = "absent"
    elif mutation == "missing_dependency":
        graph["nodes"][0]["deps"][0]["pkg"] = "absent"
    else:
        graph["packages"][0]["name"] = "not-in-lock"
    with pytest.raises(preflight.MetadataPreflightError, match=error):
        preflight._validate_resolved_graph(graph, lock_path)


def test_process_receipt_is_published_before_metadata_parse_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, cache_source, cargo_home, target_dir, tmpdir, provider = _layout(tmp_path)
    cache_pin = preflight._locked_cache_manifest(cache_source)
    _patch_host(monkeypatch)
    raw_stdout = b"not json; package path /Users/private/project"
    with pytest.raises(preflight.MetadataPreflightError, match="metadata_stdout_invalid_json"):
        preflight.run_metadata_preflight(
            source,
            cache_source,
            cargo_home,
            target_dir,
            tmpdir,
            expected_cache_manifest_sha256=cache_pin,
            snapshot_provider=provider,
            popen=lambda *args, **kwargs: _Process(raw_stdout, b"password=hunter2"),
            sleep=lambda _: None,
        )
    receipt_path = (
        provider.snapshot.root_path
        / preflight.METADATA_STAGE_RELATIVE
        / "metadata-process-capture.json"
    )
    receipt = json.loads(receipt_path.read_bytes())
    assert receipt["stdout_sha256"] == hashlib.sha256(raw_stdout).hexdigest()
    assert receipt["stdout_bytes"] == len(raw_stdout)
    assert receipt["exit_status"] == 0
    assert receipt["classification"] == "process_completed"
    assert "hunter2" not in receipt["stderr_sanitized"]
    assert b"not json" not in receipt_path.read_bytes()
    assert not (
        provider.snapshot.root_path / preflight.METADATA_STAGE_RELATIVE / "resolved-graph.json"
    ).exists()


def test_graph_artifact_is_create_only_and_descriptor_anchored(tmp_path: Path) -> None:
    root = tmp_path / "BrainData"
    root.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    payload = b'{"schema":"test"}\n'
    relative = Path(
        "qualifications/agave-v3.0.0-token2022-metadata-v2-20261012/resolved-graph.json"
    )
    try:
        digest = preflight._write_create_only_beneath_root(
            root_fd, relative, payload, device=root.stat().st_dev, revalidate=lambda: None
        )
        with pytest.raises(preflight.MetadataPreflightError, match="artifact_already_exists"):
            preflight._write_create_only_beneath_root(
                root_fd, relative, b"different", device=root.stat().st_dev, revalidate=lambda: None
            )
    finally:
        os.close(root_fd)
    output = root / relative
    assert output.read_bytes() == payload
    assert hashlib.sha256(payload).hexdigest() == digest
    assert output.stat().st_mode & 0o777 == 0o600


def test_artifact_write_after_path_replacement_never_touches_underlay(tmp_path: Path) -> None:
    root = tmp_path / "BrainData"
    root.mkdir(mode=0o700)
    held = root.with_name("BrainData.detached")
    device = root.stat().st_dev
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    payload = b"descriptor only"
    relative = Path("fresh-stage/process-capture.json")

    def detach_after_validation() -> None:
        root.rename(held)
        root.mkdir(mode=0o700)
        (root / "underlay-sentinel").write_bytes(b"untouched")

    def revalidate_path() -> None:
        try:
            current = root.stat()
        except FileNotFoundError as exc:
            raise preflight.MetadataPreflightError("artifact_storage_identity_changed") from exc
        if (current.st_dev, current.st_ino) != (device, os.fstat(root_fd).st_ino):
            raise preflight.MetadataPreflightError("artifact_storage_identity_changed")

    try:
        with pytest.raises(
            preflight.MetadataPreflightError, match="artifact_storage_identity_changed"
        ):
            preflight._write_create_only_beneath_root(
                root_fd,
                relative,
                payload,
                device=device,
                revalidate=revalidate_path,
                before_create=detach_after_validation,
            )
    finally:
        os.close(root_fd)
    assert (held / relative).read_bytes() == payload
    assert sorted(path.name for path in root.iterdir()) == ["underlay-sentinel"]
    assert (root / "underlay-sentinel").read_bytes() == b"untouched"


def test_source_cache_mismatch_refuses_before_copy_or_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, cache_source, cargo_home, target_dir, tmpdir, provider = _layout(tmp_path)
    _patch_host(monkeypatch)
    called = False

    def fake_popen(*args: Any, **kwargs: Any) -> _Process:
        nonlocal called
        called = True
        raise AssertionError("process must not run")

    with pytest.raises(preflight.MetadataPreflightError, match="source_cache_manifest_mismatch"):
        preflight.run_metadata_preflight(
            source,
            cache_source,
            cargo_home,
            target_dir,
            tmpdir,
            expected_cache_manifest_sha256="f" * 64,
            snapshot_provider=provider,
            popen=fake_popen,
        )
    assert not called
    assert not cargo_home.exists()


def test_v1_stage_path_is_rejected_before_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, cache_source, cargo_home, target_dir, tmpdir, provider = _layout(tmp_path)
    cargo_home = provider.snapshot.root_path / "metadata-stage" / "cargo-home"
    target_dir = provider.snapshot.root_path / "metadata-stage" / "target"
    tmpdir = provider.snapshot.root_path / "metadata-stage" / "tmp"
    cache_pin = preflight._locked_cache_manifest(cache_source)
    _patch_host(monkeypatch)
    with pytest.raises(preflight.MetadataPreflightError, match="metadata_stage_namespace_mismatch"):
        preflight.run_metadata_preflight(
            source,
            cache_source,
            cargo_home,
            target_dir,
            tmpdir,
            expected_cache_manifest_sha256=cache_pin,
            snapshot_provider=provider,
            popen=lambda *args, **kwargs: pytest.fail("v1 stage must never execute"),
        )
    assert not cargo_home.exists()


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("copy", "unexpected_cargo_cache_mutation"),
        ("source", "source_cache_changed_after_metadata"),
    ],
)
def test_post_metadata_cache_mutations_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    expected: str,
) -> None:
    source, cache_source, cargo_home, target_dir, tmpdir, provider = _layout(tmp_path)
    cache_pin = preflight._locked_cache_manifest(cache_source)
    _patch_host(monkeypatch)

    def fake_popen(command: list[str], **kwargs: Any) -> _Process:
        del command, kwargs
        if mutation == "copy":
            (cargo_home / "unexpected-file").write_text("unexpected")
        else:
            (cache_source / "late-write").write_text("unexpected")
        return _Process(_metadata_bytes())

    with pytest.raises(preflight.MetadataPreflightError, match=expected):
        preflight.run_metadata_preflight(
            source,
            cache_source,
            cargo_home,
            target_dir,
            tmpdir,
            expected_cache_manifest_sha256=cache_pin,
            snapshot_provider=provider,
            popen=fake_popen,
            sleep=lambda _: None,
        )


def test_metadata_stdout_cap_stops_and_reports_full_stream_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, cache_source, cargo_home, target_dir, tmpdir, provider = _layout(tmp_path)
    cache_pin = preflight._locked_cache_manifest(cache_source)
    _patch_host(monkeypatch)
    monkeypatch.setattr(preflight, "MAX_STDOUT_BYTES", 32)

    with pytest.raises(
        preflight.MetadataPreflightError, match="metadata_output_limit_exceeded"
    ) as caught:
        preflight.run_metadata_preflight(
            source,
            cache_source,
            cargo_home,
            target_dir,
            tmpdir,
            expected_cache_manifest_sha256=cache_pin,
            snapshot_provider=provider,
            popen=lambda *args, **kwargs: _Process(_metadata_bytes()),
            sleep=lambda _: None,
        )
    assert caught.value.stdout_bytes > 32
    assert caught.value.stdout_sha256 == hashlib.sha256(_metadata_bytes()).hexdigest()
    receipt_path = (
        provider.snapshot.root_path
        / preflight.METADATA_STAGE_RELATIVE
        / "metadata-process-capture.json"
    )
    receipt = json.loads(receipt_path.read_bytes())
    assert receipt["classification"] == "stdout_truncated"
    assert receipt["stdout_sha256"] == caught.value.stdout_sha256


def test_existing_copy_is_not_reused_or_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, cache_source, cargo_home, target_dir, tmpdir, provider = _layout(tmp_path)
    cargo_home.mkdir(parents=True, mode=0o700)
    (cargo_home / "planted").write_text("do not overwrite")
    _patch_host(monkeypatch)
    with pytest.raises(
        preflight.MetadataPreflightError, match="metadata_stage_namespace_not_fresh"
    ):
        preflight.run_metadata_preflight(
            source,
            cache_source,
            cargo_home,
            target_dir,
            tmpdir,
            expected_cache_manifest_sha256=preflight._locked_cache_manifest(cache_source),
            snapshot_provider=provider,
            popen=lambda *args, **kwargs: pytest.fail("process must not run"),
        )
    assert (cargo_home / "planted").read_text() == "do not overwrite"


def test_cache_copy_rejects_symlink_that_would_escape_cargo_home(tmp_path: Path) -> None:
    root = tmp_path / "root"
    source = root / "cache"
    destination = root / "copy"
    source.mkdir(parents=True, mode=0o700)
    (source / "escape").symlink_to("../../outside")
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        copied, _, _ = preflight._copy_cache_once(
            root_fd,
            root,
            source,
            destination,
            device=root.stat().st_dev,
        )
    finally:
        os.close(root_fd)
    assert copied
    with pytest.raises(GuardFailure, match="symlink_in_non_source_stage"):
        from tools.brain_senses_build_storage_guard import _allocated_bytes

        _allocated_bytes(
            (root / "empty-source", destination, root / "target", root / "tmp"), root.stat().st_dev
        )


def test_copy_allows_only_relative_symlinks_inside_cargo_git_revision(tmp_path: Path) -> None:
    root = tmp_path / "BrainData"
    source_tree = root / "cache-source"
    checkout = source_tree / "git" / "checkouts" / "crossbeam-utils-9f" / "revision-a"
    checkout.mkdir(parents=True, mode=0o700)
    (checkout / "actual.rs").write_text("pub fn safe() {}\n")
    (checkout / "alias.rs").symlink_to("actual.rs")
    destination = root / "metadata" / "cargo-home"
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        copied, _, _ = preflight._copy_cache_once(
            root_fd, root, source_tree, destination, device=root.stat().st_dev
        )
    finally:
        os.close(root_fd)
    assert copied
    from tools.brain_senses_build_storage_guard import _allocated_bytes

    allocation = _allocated_bytes(
        (root / "source", destination, root / "target", root / "tmp"),
        root.stat().st_dev,
        allow_cargo_git_checkout_symlinks=True,
    )
    assert allocation > 0


def test_cache_copy_uses_held_root_fd_after_path_replacement(tmp_path: Path) -> None:
    root = tmp_path / "BrainData"
    source_tree = root / "cache-source"
    source_tree.mkdir(parents=True, mode=0o700)
    (source_tree / "index").write_text("pinned bytes")
    device = root.stat().st_dev
    held_path = root.with_name("BrainData.detached")
    sentinel_bytes = b"underlay sentinel"
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    root.rename(held_path)
    root.mkdir(mode=0o700)
    (root / "sentinel").write_bytes(sentinel_bytes)
    try:
        copied, _, _ = preflight._copy_cache_once(
            root_fd,
            root,
            source_tree,
            root / "metadata" / "cargo-home",
            device=device,
        )
    finally:
        os.close(root_fd)

    assert copied
    assert (held_path / "metadata" / "cargo-home" / "index").read_text() == "pinned bytes"
    assert sorted(path.name for path in root.iterdir()) == ["sentinel"]
    assert (root / "sentinel").read_bytes() == sentinel_bytes


def test_stream_capture_hashes_full_stream_and_marks_truncation() -> None:
    capture = preflight._StreamCapture(io.BytesIO(b"abcdef"), 3)
    capture.drain()
    result = capture.snapshot()
    assert result.retained == b"abc"
    assert result.byte_count == 6
    assert result.raw_sha256 == hashlib.sha256(b"abcdef").hexdigest()
    assert result.truncated


def test_target_probe_rejects_unexpected_or_unpinned_metadata(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    (tmp_path / ".rustc_info.json").write_text(json.dumps({"anything": "goes"}))
    with pytest.raises(preflight.MetadataPreflightError, match="rustc_probe_schema"):
        preflight._target_probe(tmp_path, tmp_path.stat().st_dev)


def test_target_probe_accepts_only_pinned_successful_rustc_probes(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    outputs = {
        "0": {
            "success": True,
            "status": "",
            "code": 0,
            "stdout": "rustc 1.86.0 (05f9846f8 2025-03-31)\n",
            "stderr": "",
        },
        "1": {
            "success": True,
            "status": "",
            "code": 0,
            "stdout": 'target_arch="aarch64"\ntarget_os="macos"\ntarget_vendor="apple"\n',
            "stderr": "",
        },
    }
    marker = tmp_path / ".rustc_info.json"
    marker.write_text(json.dumps({"rustc_fingerprint": 7, "outputs": outputs, "successes": {}}))
    assert preflight._target_probe(tmp_path, tmp_path.stat().st_dev)[0] == ".rustc_info.json"
    outputs["1"]["success"] = False
    marker.write_text(json.dumps({"rustc_fingerprint": 7, "outputs": outputs, "successes": {}}))
    with pytest.raises(preflight.MetadataPreflightError, match="rustc_probe_output"):
        preflight._target_probe(tmp_path, tmp_path.stat().st_dev)


def test_metadata_graph_redacts_user_path_from_package_ids() -> None:
    # The graph parser must not publish absolute home paths from Cargo package IDs.
    package_id = "path+file:///Users/someone/secret/project#name@1.0.0"
    raw = json.dumps(
        {
            "version": 1,
            "packages": [{"id": package_id, "name": "name", "version": "1.0.0", "source": None}],
            "workspace_members": [package_id],
            "resolve": {
                "root": package_id,
                "nodes": [{"id": package_id, "deps": [], "features": []}],
            },
        }
    ).encode()
    graph = preflight._metadata_graph(raw)
    assert "/Users/" not in json.dumps(graph)


def test_metadata_graph_rejects_malformed_source_and_dependencies() -> None:
    malformed = {
        "version": 1,
        "packages": [{"id": "pkg", "name": "name", "version": "1.0.0", "source": []}],
        "workspace_members": ["pkg"],
        "resolve": {"root": "pkg", "nodes": [{"id": "pkg", "deps": [], "features": []}]},
    }
    with pytest.raises(preflight.MetadataPreflightError, match="metadata_package_source"):
        preflight._metadata_graph(json.dumps(malformed).encode())


def test_diagnostic_sanitizer_redacts_secrets_and_stage_paths() -> None:
    raw = (
        b"failed /Volumes/Kingston/BrainData/stage/.cargo/config.toml "
        b"token=supersecret https://user:password@example.test/path"
    )
    sanitized = preflight._sanitize(raw)
    assert "supersecret" not in sanitized
    assert "user:password" not in sanitized
    assert "/Volumes/Kingston" not in sanitized
    assert "<storage-path>" in sanitized
