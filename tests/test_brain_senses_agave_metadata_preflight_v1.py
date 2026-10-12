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

from tools import brain_senses_agave_metadata_preflight_v1 as preflight  # noqa: E402
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
    package_ids = [f"pkg {index}" for index in range(preflight.WORKSPACE_MEMBER_COUNT)]
    value = {
        "version": 1,
        "packages": [
            {
                "id": package_id,
                "name": f"p{index}",
                "version": "1.0.0",
                "source": (EXPECTED_REGISTRY_SOURCE if index == 0 else None),
            }
            for index, package_id in enumerate(package_ids)
        ],
        "workspace_members": [package_ids[0]],
        "resolve": {
            "root": package_ids[0],
            "nodes": [{"id": package_id, "deps": [], "features": []} for package_id in package_ids],
        },
    }
    return json.dumps(value, separators=(",", ":")).encode()


def _layout(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path, _Provider]:
    root = tmp_path / "BrainData"
    root.mkdir(mode=0o700)
    source = root / "agave-source"
    source.mkdir(mode=0o700)
    (source / ".git").mkdir(mode=0o700)
    cache_source = root / "qualified-cache"
    cache_source.mkdir(mode=0o700)
    (cache_source / "registry").mkdir(mode=0o700)
    (cache_source / "registry" / "index-marker").write_bytes(b"pinned index")
    cargo_home = root / "metadata-stage" / "cargo-home"
    target_dir = root / "metadata-stage" / "target"
    tmpdir = root / "metadata-stage" / "tmp"
    snapshot = Snapshot(
        root_id="a" * 32,
        volume_uuid="11111111-2222-3333-4444-555555555555",
        mount_path=tmp_path,
        root_path=root,
        available_bytes=100 * 1024**3,
        mount_device=root.stat().st_dev,
        mount_inode=root.stat().st_ino,
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
        unpacked = cargo_home / "registry" / "src" / "index-hash" / "p0-1.0.0"
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


def test_existing_copy_is_not_reused_or_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, cache_source, cargo_home, target_dir, tmpdir, provider = _layout(tmp_path)
    cargo_home.mkdir(parents=True, mode=0o700)
    (cargo_home / "planted").write_text("do not overwrite")
    _patch_host(monkeypatch)
    with pytest.raises(
        preflight.MetadataPreflightError, match="copied_cache_destination_already_exists"
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
            root,
            source,
            destination,
            device=root.stat().st_dev,
            root_inode=root.stat().st_ino,
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
    copied, _, _ = preflight._copy_cache_once(
        root,
        source_tree,
        destination,
        device=root.stat().st_dev,
        root_inode=root.stat().st_ino,
    )
    assert copied
    from tools.brain_senses_build_storage_guard import _allocated_bytes

    allocation = _allocated_bytes(
        (root / "source", destination, root / "target", root / "tmp"),
        root.stat().st_dev,
        allow_cargo_git_checkout_symlinks=True,
    )
    assert allocation > 0


def test_cache_copy_uses_held_root_fd_after_path_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "BrainData"
    source_tree = root / "cache-source"
    source_tree.mkdir(parents=True, mode=0o700)
    (source_tree / "index").write_text("pinned bytes")
    device = root.stat().st_dev
    inode = root.stat().st_ino
    held_path = root.with_name("BrainData.detached")
    sentinel_bytes = b"underlay sentinel"
    real_open = os.open
    replaced = False

    def detach_after_root_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal replaced
        fd = real_open(path, flags, *args, **kwargs)
        if not replaced and Path(path) == root and kwargs.get("dir_fd") is None:
            replaced = True
            root.rename(held_path)
            root.mkdir(mode=0o700)
            (root / "sentinel").write_bytes(sentinel_bytes)
        return fd

    monkeypatch.setattr(os, "open", detach_after_root_open)
    copied, _, _ = preflight._copy_cache_once(
        root,
        source_tree,
        root / "metadata" / "cargo-home",
        device=device,
        root_inode=inode,
    )

    assert replaced and copied
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
            "status": 0,
            "code": "-vV",
            "stdout": "rustc 1.86.0 (05f9846f8 2025-03-31)\n",
            "stderr": "",
        },
        "1": {
            "success": True,
            "status": 0,
            "code": "--print=cfg",
            "stdout": 'target_arch="aarch64"\ntarget_os="macos"\ntarget_vendor="apple"\n',
            "stderr": "",
        },
    }
    marker = tmp_path / ".rustc_info.json"
    marker.write_text(json.dumps({"rustc_fingerprint": 7, "outputs": outputs}))
    assert preflight._target_probe(tmp_path, tmp_path.stat().st_dev)[0] == ".rustc_info.json"
    outputs["1"]["success"] = False
    marker.write_text(json.dumps({"rustc_fingerprint": 7, "outputs": outputs}))
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
