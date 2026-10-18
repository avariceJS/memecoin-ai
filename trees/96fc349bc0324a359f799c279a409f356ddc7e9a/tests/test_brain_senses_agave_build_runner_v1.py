from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tools import brain_senses_agave_build_runner_v1 as runner
from tools import brain_senses_build_storage_guard as guard


class _Process:
    pid = 999_999

    def __init__(self, stdout: bytes = b"ok\n", stderr: bytes = b"") -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0


def _commands(tmp_path: Path) -> list[dict[str, Any]]:
    cargo = tmp_path / "cargo"
    cargo.write_text("synthetic executable placeholder", encoding="utf-8")
    return runner.command_vectors(
        str(cargo), runner.build_sandbox_profile(tmp_path, tmp_path / "source", tmp_path / "cache")
    )


def test_command_vectors_are_exact_and_network_write_denied(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    source = tmp_path / "kingston" / "source"
    cache = tmp_path / "kingston" / "cargo-home"
    profile = runner.build_sandbox_profile(repo, source, cache)
    assert "(deny network*)" in profile
    assert f'(deny file-write* (subpath "{repo}"))' in profile
    assert f'(deny file-write* (subpath "{source}"))' in profile
    assert f'(deny file-write* (subpath "{cache}"))' in profile
    commands = _commands(tmp_path)
    assert [item["id"] for item in commands] == [
        "candidate_position_lib",
        "cpi_integration",
        "feature_off_check",
    ]
    assert [item["cargo_argv"][1:] for item in commands] == [
        list(args) for _, args in runner.ALLOWED_COMMANDS
    ]
    assert all(
        "--offline" in item["cargo_argv"] and "--locked" in item["cargo_argv"] for item in commands
    )
    assert "--all-features" not in " ".join(commands[-1]["cargo_argv"])


@pytest.mark.parametrize(
    "marker",
    ["setsid", "setpgid(0, 0)", "fork()", "daemon(0, 0)", "process_group", "CommandExt"],
)
def test_process_spawn_closure_rejects_detached_process_markers(
    tmp_path: Path, marker: str
) -> None:
    source = tmp_path / "source"
    cache = tmp_path / "cache"
    source.mkdir(mode=0o700)
    cache.mkdir(mode=0o700)
    package = source / "crate"
    package.mkdir(mode=0o700)
    (package / "Cargo.toml").write_text(
        '[package]\nname="fixture"\nversion="1"\n', encoding="utf-8"
    )
    (package / "build.rs").write_text(f"fn main() {{ {marker}; }}\n", encoding="utf-8")
    with pytest.raises(runner.BuildRunnerError, match="detached_process_marker"):
        runner._process_spawn_closure(source, cache, device=source.stat().st_dev)


def test_process_spawn_closure_binds_active_custom_and_disabled_build_scripts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    cache = tmp_path / "cache"
    source.mkdir(mode=0o700)
    cache.mkdir(mode=0o700)
    active = source / "active"
    active.mkdir(mode=0o700)
    (active / "Cargo.toml").write_text(
        '[package]\nname="active"\nversion="1"\nbuild="build/run.rs"\n',
        encoding="utf-8",
    )
    (active / "build").mkdir(mode=0o700)
    (active / "build" / "run.rs").write_text(
        'fn main() { let _ = std::process::Command::new("cc"); }\n', encoding="utf-8"
    )
    disabled = source / "disabled"
    disabled.mkdir(mode=0o700)
    (disabled / "Cargo.toml").write_text(
        '[package]\nname="disabled"\nversion="1"\nbuild=false\n', encoding="utf-8"
    )
    (disabled / "build.rs").write_text("fn main() {}\n", encoding="utf-8")
    closure = runner._process_spawn_closure(source, cache, device=source.stat().st_dev)
    assert closure["script_count"] == 2
    assert closure["active_declaration_count"] == 1
    assert closure["declaration_count"] == 2
    assert closure["ordinary_process_spawn_matches"] == 1
    assert closure["detached_marker_matches"] == 0
    assert len(closure["sha256"]) == 64
    assert {row["declaration"] for row in closure["scripts"]} == {
        "active",
        "disabled_build_rs",
    }
    active_manifest = next(
        row["manifest"] for row in closure["scripts"] if row["declaration"] == "active"
    )
    assert active_manifest["kind"] == "regular_file"
    assert active_manifest["mode"] == 0o644
    assert active_manifest["size"] > 0


def test_process_spawn_closure_binds_absent_implicit_and_disabled_declarations(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    cache = tmp_path / "cache"
    source.mkdir(mode=0o700)
    cache.mkdir(mode=0o700)
    package = source / "package"
    package.mkdir(mode=0o700)
    manifest = package / "Cargo.toml"
    manifest.write_text('[package]\nname="fixture"\nversion="1"\n', encoding="utf-8")
    closure = runner._process_spawn_closure(source, cache, device=source.stat().st_dev)
    assert closure["script_count"] == 0
    assert closure["declarations"] == [
        {
            "manifest": {
                "path": "source/package/Cargo.toml",
                "kind": "regular_file",
                "mode": 0o644,
                "size": manifest.stat().st_size,
                "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            },
            "package_build": "implicit",
            "script_path": None,
            "script_status": "implicit_script_absent",
        }
    ]


def test_process_spawn_closure_rejects_build_path_escape(tmp_path: Path) -> None:
    source = tmp_path / "source"
    cache = tmp_path / "cache"
    source.mkdir(mode=0o700)
    cache.mkdir(mode=0o700)
    package = source / "crate"
    package.mkdir(mode=0o700)
    (package / "Cargo.toml").write_text(
        '[package]\nname="fixture"\nversion="1"\nbuild="../escape.rs"\n',
        encoding="utf-8",
    )
    with pytest.raises(runner.BuildRunnerError, match="build_manifest_path_invalid"):
        runner._process_spawn_closure(source, cache, device=source.stat().st_dev)


def test_process_spawn_closure_rejects_symlink_intermediate_for_custom_script(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    cache = tmp_path / "cache"
    source.mkdir(mode=0o700)
    cache.mkdir(mode=0o700)
    package = source / "crate"
    package.mkdir(mode=0o700)
    (package / "Cargo.toml").write_text(
        '[package]\nname="fixture"\nversion="1"\nbuild="link/run.rs"\n',
        encoding="utf-8",
    )
    external = tmp_path / "external"
    external.mkdir(mode=0o700)
    (external / "run.rs").write_text("fn main() {}\n", encoding="utf-8")
    (package / "link").symlink_to(external, target_is_directory=True)
    with pytest.raises(runner.BuildRunnerError, match="build_closure_symlink_directory"):
        runner._process_spawn_closure(source, cache, device=source.stat().st_dev)


def test_cache_delta_allows_only_exact_global_cache_content_change() -> None:
    old_hash = "a" * 64
    new_hash = "b" * 64
    before = {
        "sha256": "1" * 64,
        "entry_count": 1,
        "file_bytes": runner.GLOBAL_CACHE_SIZE,
        "rows": [
            {
                "path": runner.GLOBAL_CACHE_PATH,
                "kind": "file",
                "mode": runner.GLOBAL_CACHE_MODE,
                "size": runner.GLOBAL_CACHE_SIZE,
                "sha256": old_hash,
            }
        ],
    }
    after = {**before, "sha256": "2" * 64, "rows": [{**before["rows"][0], "sha256": new_hash}]}
    delta = runner._cache_delta(before, after)
    assert delta["allowed_delta"] == {
        "path": ".global-cache",
        "kind": "regular_file",
        "mode": 0o644,
        "size": 176_128,
        "before_sha256": old_hash,
        "after_sha256": new_hash,
        "classification": "opaque_cargo_bookkeeping_only",
    }
    extra = {**after, "rows": [*after["rows"], {"path": "new-file", "kind": "file"}]}
    with pytest.raises(runner.BuildRunnerError, match="copied_cache_unexpected_delta"):
        runner._cache_delta(before, extra)
    wrong_mode = {**after, "rows": [{**after["rows"][0], "mode": 0o600}]}
    with pytest.raises(runner.BuildRunnerError, match="copied_cache_global_bookkeeping_mismatch"):
        runner._cache_delta(before, wrong_mode)


def test_setup_headroom_includes_free_floor_stage_cap_and_collector_reserves() -> None:
    snapshot = guard.Snapshot(
        runner.ROOT_ID,
        runner.VOLUME_UUID,
        Path("/Volumes/Kingston"),
        Path("/Volumes/Kingston/BrainData"),
        guard.MIN_FREE_BYTES + guard.ALLOCATION_CAP_BYTES - 1,
        1,
        2,
    )
    with pytest.raises(runner.BuildRunnerError, match="free_space_below_reserved_stage_envelope"):
        runner._require_setup_headroom(snapshot)
    runner._require_setup_headroom(
        guard.Snapshot(**{**snapshot.__dict__, "available_bytes": snapshot.available_bytes + 1})
    )


def test_capture_hashes_full_stream_but_only_retains_sanitized_excerpt(tmp_path: Path) -> None:
    secret = b"api_key=do-not-persist"
    process = runner.CapturedProcess(_Process(stdout=b"prefix " + secret), (tmp_path,))
    assert process.poll() == 0
    captured = process.record()
    stdout = captured["stdout"]
    assert stdout["bytes"] == len(b"prefix " + secret)
    assert stdout["sha256"] == hashlib.sha256(b"prefix " + secret).hexdigest()
    assert "do-not-persist" not in stdout["sanitized_excerpt"]
    assert "api_key=<redacted>" in stdout["sanitized_excerpt"]
    assert stdout["complete"] is True


def test_capture_limit_turns_zero_exit_into_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = _Process(stdout=b"x" * (runner.MAX_CAPTURE_BYTES + 1))
    wrapped = runner.CapturedProcess(process, (tmp_path,))
    # The reader thread may still be consuming the synthetic stream on the first poll.
    wrapped.stdout.thread.join(timeout=2)
    assert wrapped.poll() == 125


def test_execute_commands_rejects_over_limit_capture_even_after_zero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "MAX_CAPTURE_BYTES", 8)
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    for path in (
        root / "qualifications",
        root / "qualifications" / runner.BUILD_RELATIVE.name,
        root / runner.TARGET_RELATIVE,
        root / runner.RECEIPTS_RELATIVE,
    ):
        path.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    commands = _commands(tmp_path)
    calls = 0

    def stage(command: list[str], **kwargs: Any) -> int:
        nonlocal calls
        kwargs["popen"](command, env={}, cwd=tmp_path, start_new_session=True)
        calls += 1
        return 0

    try:
        terminal = runner.execute_commands(
            commands=commands,
            source=tmp_path / "source",
            source_cache=tmp_path / "cache",
            cargo_home=tmp_path / "cargo-home",
            target_dir=tmp_path / "target",
            tmpdir=tmp_path / "tmp",
            snapshot_provider=object(),
            root_fd=root_fd,
            device=os.fstat(root_fd).st_dev,
            source_pins={},
            implementation_pins={},
            run_id="c" * 24,
            start_ns=1,
            stage_runner=stage,
            process_factory=lambda *_args, **_kwargs: _Process(stdout=b"x" * 9),
            revalidate=lambda: None,
        )
        assert calls == 1
        assert terminal["failure_reason"] == "capture_output_limit_or_read_failure"
        assert terminal["completed_commands"][0]["capture"]["stdout"]["over_limit"] is True
    finally:
        os.close(root_fd)


def test_execute_commands_stops_at_first_failure_and_publishes_create_only_receipts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    receipts = root / runner.RECEIPTS_RELATIVE
    root.mkdir(mode=0o700)
    for path in (
        root / "qualifications",
        root / "qualifications" / "agave-v3.0.0-token2022-build-v1-20261012",
        root / "qualifications" / "agave-v3.0.0-token2022-build-v1-20261012" / "target",
        receipts,
    ):
        path.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    device = os.fstat(root_fd).st_dev
    commands = _commands(tmp_path)
    calls = 0

    def process_factory(_command: list[str], **kwargs: Any) -> _Process:
        assert kwargs["stdout"] == subprocess.PIPE
        assert kwargs["stderr"] == subprocess.PIPE
        return _Process(stdout=b"phase marker\n", stderr=b"api_key=never-store\n")

    def fake_stage(command: list[str], **kwargs: Any) -> int:
        nonlocal calls
        assert command[0] == commands[calls]["execution_argv"][0]
        popen = kwargs["popen"]
        popen(command, env={"PATH": runner.FIXED_PATH}, cwd=tmp_path, start_new_session=True)
        calls += 1
        return 0 if calls == 1 else 17

    try:
        terminal = runner.execute_commands(
            commands=commands,
            source=tmp_path / "source",
            source_cache=tmp_path / "cache",
            cargo_home=tmp_path / "build-cargo-home",
            target_dir=tmp_path / "target",
            tmpdir=tmp_path / "tmp",
            snapshot_provider=object(),
            root_fd=root_fd,
            device=device,
            source_pins={"source_head": "pinned"},
            implementation_pins={"runner": "a" * 64},
            run_id="r" * 24,
            start_ns=123,
            stage_runner=fake_stage,
            process_factory=process_factory,
            revalidate=lambda: None,
            verify_inputs=lambda: None,
        )
        assert terminal["status"] == "failed_terminal_no_retry"
        assert terminal["failure_reason"] == "command_failed"
        assert len(terminal["completed_commands"]) == 2
        assert calls == 2
        for index in (0, 1):
            path = receipts / f"command-{index:02d}.json"
            data = path.read_bytes()
            record = json.loads(data)
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert (
                record["capture"]["stderr"]["sha256"]
                == hashlib.sha256(b"api_key=never-store\n").hexdigest()
            )
            assert "never-store" not in data.decode()
        terminal_path = receipts / "terminal-v1.json"
        terminal_bytes = terminal_path.read_bytes()
        assert json.loads(terminal_bytes)["status"] == "failed_terminal_no_retry"
        command_zero_bytes = (receipts / "command-00.json").read_bytes()
        with pytest.raises(runner.metadata.MetadataPreflightError, match="artifact_already_exists"):
            runner.execute_commands(
                commands=commands,
                source=tmp_path / "source",
                source_cache=tmp_path / "cache",
                cargo_home=tmp_path / "build-cargo-home",
                target_dir=tmp_path / "target",
                tmpdir=tmp_path / "tmp",
                snapshot_provider=object(),
                root_fd=root_fd,
                device=device,
                source_pins={},
                implementation_pins={},
                run_id="r" * 24,
                start_ns=123,
                stage_runner=fake_stage,
                process_factory=process_factory,
                revalidate=lambda: None,
                verify_inputs=lambda: None,
            )
        assert terminal_path.read_bytes() == terminal_bytes
        assert (receipts / "command-00.json").read_bytes() == command_zero_bytes
    finally:
        os.close(root_fd)


@pytest.mark.parametrize("drift_kind", ["source_cache", "copied_cache"])
def test_execute_commands_stops_on_cache_drift_before_next_command(
    tmp_path: Path, drift_kind: str
) -> None:
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    for path in (
        root / "qualifications",
        root / runner.BUILD_RELATIVE,
        root / runner.TARGET_RELATIVE,
        root / runner.RECEIPTS_RELATIVE,
    ):
        path.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    commands = _commands(tmp_path)
    calls = 0
    source_manifests = iter(
        ["a" * 64, "b" * 64, "b" * 64]
        if drift_kind == "source_cache"
        else ["a" * 64, "a" * 64, "a" * 64]
    )

    before_cache = {
        "rows": [
            {
                "path": "registry/index/fixture",
                "kind": "file",
                "mode": 0o644,
                "size": 1,
                "sha256": "a" * 64,
            }
        ],
        "sha256": "1" * 64,
        "entry_count": 1,
        "file_bytes": 1,
    }
    after_cache = {
        **before_cache,
        "rows": [{**before_cache["rows"][0], "sha256": "b" * 64}],
        "sha256": "2" * 64,
    }
    cache_snapshots = iter(
        [before_cache, after_cache, after_cache]
        if drift_kind == "copied_cache"
        else [before_cache, before_cache, before_cache]
    )

    def stage(_command: list[str], **_kwargs: Any) -> int:
        nonlocal calls
        calls += 1
        return 0

    try:
        terminal = runner.execute_commands(
            commands=commands,
            source=tmp_path / "source",
            source_cache=tmp_path / "source-cache",
            cargo_home=tmp_path / "cargo-home",
            target_dir=tmp_path / "target",
            tmpdir=tmp_path / "tmp",
            snapshot_provider=object(),
            root_fd=root_fd,
            device=os.fstat(root_fd).st_dev,
            source_pins={
                "source_cache_manifest_sha256": "a" * 64,
            },
            implementation_pins={},
            run_id="x" * 24,
            start_ns=1,
            stage_runner=stage,
            revalidate=lambda: None,
            source_cache_manifest=lambda: next(source_manifests),
            cache_snapshot=lambda: next(cache_snapshots),
        )
        assert calls == 1
        assert len(terminal["completed_commands"]) == 1
        assert terminal["status"] == "failed_terminal_no_retry"
        expected = (
            "source_cache_changed_during_command"
            if drift_kind == "source_cache"
            else "copied_cache_unexpected_delta"
        )
        assert terminal["failure_reason"] == expected
        assert terminal["cache_evidence"] is not None
        command = terminal["completed_commands"][0]
        manifests = command["copied_cache_manifests"]
        assert manifests is not None
        before_raw = (root / manifests["before"]["path"]).read_bytes()
        after_raw = (root / manifests["after"]["path"]).read_bytes()
        assert hashlib.sha256(before_raw).hexdigest() == manifests["before"]["artifact_sha256"]
        assert hashlib.sha256(after_raw).hexdigest() == manifests["after"]["artifact_sha256"]
        before = json.loads(before_raw)
        after = json.loads(after_raw)
        assert before["tree_sha256"] == "1" * 64
        assert after["tree_sha256"] == ("2" * 64 if drift_kind == "copied_cache" else "1" * 64)
        terminal_raw = (
            root / terminal["cache_evidence"]["copied_cache_manifest_path"]
        ).read_bytes()
        assert (
            hashlib.sha256(terminal_raw).hexdigest()
            == terminal["cache_evidence"]["copied_cache_manifest_artifact_sha256"]
        )
        terminal_manifest = json.loads(terminal_raw)
        assert terminal_manifest["tree_sha256"] == after["tree_sha256"]
    finally:
        os.close(root_fd)


def test_collector_reserves_are_subtracted_from_each_verified_snapshot() -> None:
    class Storage:
        def __init__(self) -> None:
            self.mount_fd = os.open("/", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))

        def revalidate(self, *, min_free_bytes: int) -> Any:
            assert min_free_bytes == guard.MIN_FREE_BYTES

            class Root:
                root_id = runner.ROOT_ID
                volume_uuid = runner.VOLUME_UUID
                mount_path = Path("/Volumes/Kingston")
                path = Path("/Volumes/Kingston/BrainData")
                available_bytes = 100 * runner.GIB

            return Root()

    storage = Storage()
    try:
        provider = runner.ReservedSnapshotProvider(storage)
        snapshot = provider.inspect()
        assert snapshot.available_bytes == 100 * runner.GIB - runner.EXTRA_RESERVED_BYTES
        assert snapshot.available_bytes < 100 * runner.GIB
    finally:
        os.close(storage.mount_fd)


def test_deadline_prevents_later_command_without_invoking_stage(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    for path in (
        root / "qualifications",
        root / "qualifications" / "agave-v3.0.0-token2022-build-v1-20261012",
        root / "qualifications" / "agave-v3.0.0-token2022-build-v1-20261012" / "target",
        root / runner.RECEIPTS_RELATIVE,
    ):
        path.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    commands = _commands(tmp_path)
    try:
        result = runner.execute_commands(
            commands=commands,
            source=tmp_path / "source",
            source_cache=tmp_path / "cache",
            cargo_home=tmp_path / "cargo-home",
            target_dir=tmp_path / "target",
            tmpdir=tmp_path / "tmp",
            snapshot_provider=object(),
            root_fd=root_fd,
            device=os.fstat(root_fd).st_dev,
            source_pins={},
            implementation_pins={},
            run_id="d" * 24,
            start_ns=1,
            stage_runner=lambda *_args, **_kwargs: pytest.fail("deadline must prevent stage call"),
            clock=lambda: 10.0,
            deadline=9.0,
            revalidate=lambda: None,
        )
        assert result["status"] == "failed_terminal_no_retry"
        assert result["failure_reason"] == "total_build_wall_timeout"
        assert len(result["completed_commands"]) == 1
    finally:
        os.close(root_fd)
