from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tools import brain_senses_build_storage_guard as guard


class FakeProvider:
    def __init__(self, root: Path, *, available: int = 100 * guard.GIB) -> None:
        self.root = root
        self.available = available
        self.calls = 0

    def inspect(self) -> guard.Snapshot:
        self.calls += 1
        return guard.Snapshot(
            "r" * 32,
            "1" * 8 + "-111-4111-8111-111111111111",
            self.root.parent,
            self.root,
            self.available,
            self.root.stat().st_dev,
            self.root.stat().st_ino,
        )


def _paths(root: Path) -> tuple[Path, Path, Path, Path]:
    return (
        root / "agave",
        root / ".cargo-home",
        root / ".cargo-target",
        root / ".build-tmp",
    )


def _run(monkeypatch: pytest.MonkeyPatch, provider: FakeProvider, command: list[str]) -> int:
    monkeypatch.setattr(guard, "_check_unprivileged_root1", lambda: None)
    monkeypatch.setattr(guard, "_check_rust_toolchain", lambda _env: None)
    source, cargo_home, target, tmpdir = _paths(provider.root)
    return guard.run_guarded_stage(
        command,
        source=source,
        cargo_home=cargo_home,
        target_dir=target,
        tmpdir=tmpdir,
        initial=True,
        snapshot_provider=provider,
        poll_seconds=0.005,
        term_grace_seconds=0.02,
    )


def test_initial_free_gate_and_allocation_gate_are_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "BrainData"
    root.mkdir()
    with pytest.raises(guard.GuardFailure, match="initial_free_below_90_gib"):
        _run(monkeypatch, FakeProvider(root, available=89 * guard.GIB), ["true"])

    provider = FakeProvider(root)
    monkeypatch.setattr(guard, "_allocated_bytes", lambda _paths, _device: 33 * guard.GIB)
    with pytest.raises(guard.GuardFailure, match="allocation_over_32_gib"):
        _run(monkeypatch, provider, ["true"])


def test_remaining_allocation_reservation_requires_peak_margin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "BrainData"
    root.mkdir()
    monkeypatch.setattr(guard, "_check_unprivileged_root1", lambda: None)
    monkeypatch.setattr(guard, "_check_rust_toolchain", lambda _env: None)
    source, cargo_home, target, tmpdir = _paths(root)
    with pytest.raises(guard.GuardFailure, match="free_space_below_stage_envelope"):
        guard.run_guarded_stage(
            ["true"],
            source=source,
            cargo_home=cargo_home,
            target_dir=target,
            tmpdir=tmpdir,
            initial=False,
            snapshot_provider=FakeProvider(root, available=89 * guard.GIB),
        )


def test_stage_paths_must_be_bound_and_nonoverlapping(tmp_path: Path) -> None:
    root = tmp_path / "BrainData"
    root.mkdir()
    snapshot = FakeProvider(root).inspect()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(guard.GuardFailure, match="outside_bound_root"):
        guard._check_stage_paths((outside, root / "a", root / "b", root / "c"), snapshot)
    with pytest.raises(guard.GuardFailure, match="stage_paths_overlap"):
        guard._check_stage_paths(
            (root / "a", root / "a" / "nested", root / "b", root / "c"), snapshot
        )


def test_allocated_bytes_accepts_only_symlinks_resolving_inside_source(
    tmp_path: Path,
) -> None:
    root = tmp_path / "BrainData"
    root.mkdir()
    stage = root / "stage"
    stage.mkdir()
    target = stage / "target.txt"
    target.write_text("inside", encoding="utf-8")
    (stage / "internal-link").symlink_to(target)
    (stage / "dangling-internal-link").symlink_to(stage / "removed-page.md")
    assert guard._allocated_bytes((stage,), root.stat().st_dev) >= target.stat().st_blocks * 512

    outside = tmp_path / "outside"
    outside.mkdir()
    (stage / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(guard.GuardFailure, match="unsafe_source_symlink"):
        guard._allocated_bytes((stage,), root.stat().st_dev)


def test_allocated_bytes_rejects_symlinks_in_cargo_target_or_tmp(
    tmp_path: Path,
) -> None:
    root = tmp_path / "BrainData"
    root.mkdir()
    source = root / "source"
    source.mkdir()
    cargo_home = root / "cargo-home"
    cargo_home.mkdir()
    target = root / "target"
    target.mkdir()
    tmpdir = root / "tmp"
    tmpdir.mkdir()
    destination = root / "external"
    destination.mkdir()
    (cargo_home / "escape").symlink_to(destination, target_is_directory=True)
    with pytest.raises(guard.GuardFailure, match="symlink_in_non_source_stage"):
        guard._allocated_bytes((source, cargo_home, target, tmpdir), root.stat().st_dev)


def test_allocated_bytes_allows_only_internal_pinned_cargo_git_checkout_links(
    tmp_path: Path,
) -> None:
    root = tmp_path / "BrainData"
    root.mkdir()
    source = root / "source"
    source.mkdir()
    cargo_home = root / "cargo-home"
    checkout = cargo_home / "git" / "checkouts" / "crossbeam" / "fd279d7"
    (checkout / "crossbeam-epoch").mkdir(parents=True)
    (checkout / "no_atomic.rs").write_text("// pinned", encoding="utf-8")
    link = checkout / "crossbeam-epoch" / "no_atomic.rs"
    link.symlink_to("../no_atomic.rs")
    target = root / "target"
    target.mkdir()
    tmpdir = root / "tmp"
    tmpdir.mkdir()
    paths = (source, cargo_home, target, tmpdir)
    with pytest.raises(guard.GuardFailure, match="symlink_in_non_source_stage"):
        guard._allocated_bytes(paths, root.stat().st_dev)
    assert (
        guard._allocated_bytes(paths, root.stat().st_dev, allow_cargo_git_checkout_symlinks=True)
        > 0
    )

    other_revision = cargo_home / "git" / "checkouts" / "crossbeam" / "other-revision"
    other_revision.mkdir()
    (other_revision / "foreign.rs").write_text("// foreign", encoding="utf-8")
    link.unlink()
    link.symlink_to("../../other-revision/foreign.rs")
    with pytest.raises(guard.GuardFailure, match="unsafe_cargo_checkout_symlink"):
        guard._allocated_bytes(paths, root.stat().st_dev, allow_cargo_git_checkout_symlinks=True)

    other_repository = cargo_home / "git" / "checkouts" / "other-repository" / "revision"
    other_repository.mkdir(parents=True)
    (other_repository / "foreign.rs").write_text("// foreign", encoding="utf-8")
    link.unlink()
    link.symlink_to("../../../other-repository/revision/foreign.rs")
    with pytest.raises(guard.GuardFailure, match="unsafe_cargo_checkout_symlink"):
        guard._allocated_bytes(paths, root.stat().st_dev, allow_cargo_git_checkout_symlinks=True)

    link.unlink()
    link.symlink_to((checkout / "no_atomic.rs").resolve())
    with pytest.raises(guard.GuardFailure, match="absolute_cargo_checkout_symlink"):
        guard._allocated_bytes(paths, root.stat().st_dev, allow_cargo_git_checkout_symlinks=True)


@pytest.mark.parametrize("structural_level", ["repository", "revision"])
def test_allocated_bytes_rejects_symlinked_cargo_checkout_structure(
    tmp_path: Path, structural_level: str
) -> None:
    root = tmp_path / "BrainData"
    root.mkdir()
    source = root / "source"
    source.mkdir()
    cargo_home = root / "cargo-home"
    checkout_base = cargo_home / "git" / "checkouts"
    target = root / "target"
    target.mkdir()
    tmpdir = root / "tmp"
    tmpdir.mkdir()
    real = root / "real"
    (real / "revision").mkdir(parents=True)
    (real / "revision" / "file.rs").write_text("// pinned", encoding="utf-8")
    checkout_base.mkdir(parents=True)
    if structural_level == "repository":
        (checkout_base / "repository").symlink_to(real, target_is_directory=True)
    else:
        repository = checkout_base / "repository"
        repository.mkdir()
        (repository / "revision").symlink_to(real / "revision", target_is_directory=True)
    paths = (source, cargo_home, target, tmpdir)
    with pytest.raises(guard.GuardFailure):
        guard._allocated_bytes(paths, root.stat().st_dev, allow_cargo_git_checkout_symlinks=True)


def test_identity_loss_kills_process_group_and_child_cannot_write_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "BrainData"
    root.mkdir()
    provider = FakeProvider(root)
    original_inspect = provider.inspect

    def detach_after_start() -> guard.Snapshot:
        if provider.calls >= 2:
            raise OSError("simulated diskutil identity loss")
        return original_inspect()

    provider.inspect = detach_after_start  # type: ignore[method-assign]
    monkeypatch.setattr(guard, "_check_unprivileged_root1", lambda: None)
    monkeypatch.setattr(guard, "_check_rust_toolchain", lambda _env: None)
    source, cargo_home, target, tmpdir = _paths(root)
    sentinel = tmp_path / "late-write"
    child_code = (
        "import pathlib,time; time.sleep(0.3); "
        f"pathlib.Path({str(sentinel)!r}).write_text('underlay')"
    )
    parent_code = (
        "import subprocess,sys; "
        f"subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
        "import time; time.sleep(20)"
    )
    started = time.monotonic()
    with pytest.raises(guard.GuardFailure, match="storage_probe_or_path_failed"):
        guard.run_guarded_stage(
            [sys.executable, "-c", parent_code],
            source=source,
            cargo_home=cargo_home,
            target_dir=target,
            tmpdir=tmpdir,
            initial=True,
            snapshot_provider=provider,
            poll_seconds=0.01,
            term_grace_seconds=0.05,
        )
    assert time.monotonic() - started < 2
    time.sleep(0.35)
    assert not sentinel.exists()


def test_probe_timeout_kills_child_without_waiting_for_filesystem_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "BrainData"
    root.mkdir()
    provider = FakeProvider(root)
    original_inspect = provider.inspect
    started_probe = threading.Event()

    def stuck_probe() -> guard.Snapshot:
        if provider.calls >= 2:
            started_probe.set()
            time.sleep(10)
        return original_inspect()

    provider.inspect = stuck_probe  # type: ignore[method-assign]
    monkeypatch.setattr(guard, "_check_unprivileged_root1", lambda: None)
    monkeypatch.setattr(guard, "_check_rust_toolchain", lambda _env: None)
    monkeypatch.setattr(guard, "PROBE_TIMEOUT_SECONDS", 0.05)
    source, cargo_home, target, tmpdir = _paths(root)
    child: subprocess.Popen[bytes] | None = None

    class StartedProcess:
        @property
        def pid(self) -> int:
            assert child is not None
            return child.pid

        def poll(self) -> int | None:
            assert child is not None
            return child.poll()

        def wait(self, timeout: float | None = None) -> int:
            assert child is not None
            return child.wait(timeout=timeout)

    def launch(*_args: object, **_kwargs: object) -> StartedProcess:
        nonlocal child
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(20)"],
            start_new_session=True,
        )
        return StartedProcess()

    with pytest.raises(guard.GuardFailure, match="storage_probe_timeout"):
        guard.run_guarded_stage(
            [sys.executable, "-c", "pass"],
            source=source,
            cargo_home=cargo_home,
            target_dir=target,
            tmpdir=tmpdir,
            initial=True,
            snapshot_provider=provider,
            popen=launch,  # type: ignore[arg-type]
        )
    assert started_probe.is_set()
    assert child is not None
    assert child.poll() is not None


def test_stage_success_uses_offline_pinned_environment_and_no_fd_inheritance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "BrainData"
    root.mkdir()
    provider = FakeProvider(root)
    monkeypatch.setattr(guard, "_check_unprivileged_root1", lambda: None)
    monkeypatch.setattr(guard, "_check_rust_toolchain", lambda _env: None)
    captured: dict[str, object] = {}

    class DoneProcess:
        pid = 54321

        def poll(self) -> int:
            return 0

    def fake_popen(*args: object, **kwargs: object) -> DoneProcess:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return DoneProcess()

    source, cargo_home, target, tmpdir = _paths(root)
    result = guard.run_guarded_stage(
        ["cargo", "test", "--locked"],
        source=source,
        cargo_home=cargo_home,
        target_dir=target,
        tmpdir=tmpdir,
        initial=True,
        snapshot_provider=provider,
        popen=fake_popen,  # type: ignore[arg-type]
    )
    assert result == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["start_new_session"] is True
    assert kwargs["close_fds"] is True
    assert kwargs["pass_fds"] == ()
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert env["CARGO_NET_OFFLINE"] == "true"
    assert env["RUSTUP_TOOLCHAIN"] == "1.86.0"
    assert env["RUSTUP_HOME"] == str(Path.home() / ".rustup")
    assert env["CARGO_HOME"] == str(cargo_home)
    assert env["CARGO_TARGET_DIR"] == str(target)
    assert env["TMPDIR"] == str(tmpdir)


@pytest.mark.skipif(os.name != "posix", reason="process-group supervision requires POSIX")
def test_terminate_group_escalates_to_kill_when_leader_exits_first(
    tmp_path: Path,
) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(20)"],
        start_new_session=True,
        close_fds=True,
    )
    try:
        guard._terminate_group(process, grace_seconds=0.05)
        assert process.poll() is not None
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)


def test_successful_leader_exit_terminates_delayed_writer_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "BrainData"
    root.mkdir()
    provider = FakeProvider(root)
    monkeypatch.setattr(guard, "_check_unprivileged_root1", lambda: None)
    monkeypatch.setattr(guard, "_check_rust_toolchain", lambda _env: None)
    source, cargo_home, target, tmpdir = _paths(root)
    sentinel = tmp_path / "late-after-success"
    child_code = (
        "import pathlib,time; time.sleep(0.3); "
        f"pathlib.Path({str(sentinel)!r}).write_text('survived')"
    )
    parent_code = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child_code!r}])"
    assert (
        guard.run_guarded_stage(
            [sys.executable, "-c", parent_code],
            source=source,
            cargo_home=cargo_home,
            target_dir=target,
            tmpdir=tmpdir,
            initial=True,
            snapshot_provider=provider,
            poll_seconds=0.005,
            term_grace_seconds=0.02,
        )
        == 0
    )
    time.sleep(0.35)
    assert not sentinel.exists()
