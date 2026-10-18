from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def _fetcher(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools"))
    return importlib.import_module("brain_senses_agave_dependency_fetch_v1")


def test_locked_source_gate_accepts_only_reviewed_git_and_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetcher = _fetcher(monkeypatch)
    lock = tmp_path / "Cargo.lock"
    lock.write_text(
        'version = 4\n[[package]]\nname="root"\nversion="0.1.0"\n'
        f'[[package]]\nname="dep"\nversion="1.0.0"\nsource="{fetcher.EXPECTED_GIT_SOURCE}"\n'
        f'[[package]]\nname="dep2"\nversion="1.0.0"\nsource="{fetcher.EXPECTED_REGISTRY_SOURCE}"\n',
        encoding="utf-8",
    )
    sources, count = fetcher._locked_sources(lock)
    assert sources == sorted([fetcher.EXPECTED_GIT_SOURCE, fetcher.EXPECTED_REGISTRY_SOURCE])
    assert count == 3


def test_locked_source_gate_rejects_an_unreviewed_git_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetcher = _fetcher(monkeypatch)
    lock = tmp_path / "Cargo.lock"
    lock.write_text(
        'version = 4\n[[package]]\nname="dep"\nversion="1.0.0"\n'
        'source="git+https://example.invalid/dep#0000000000000000000000000000000000000000"\n'
        f'[[package]]\nname="dep2"\nversion="1.0.0"\nsource="{fetcher.EXPECTED_REGISTRY_SOURCE}"\n',
        encoding="utf-8",
    )
    with pytest.raises(fetcher.GuardFailure, match="cargo_lock_has_unapproved_source"):
        fetcher._locked_sources(lock)


def test_cargo_config_gate_rejects_home_level_source_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetcher = _fetcher(monkeypatch)
    source = tmp_path / "checkout"
    config_dir = source / ".cargo"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        '[target.x86_64-unknown-linux-gnu]\nrustflags = ["-Ctarget-cpu=x86-64-v2"]\n',
        encoding="utf-8",
    )
    git_config = source / ".git" / "config"
    git_config.parent.mkdir()
    git_config.write_text(
        '[remote "origin"]\nurl = https://github.com/anza-xyz/agave.git\n',
        encoding="utf-8",
    )
    cargo_home = tmp_path / "cargo-home"
    cargo_home.mkdir()
    fetcher._source_configuration(source, cargo_home)
    (cargo_home / "config.toml").write_text(
        '[source.crates-io]\nreplace-with="mirror"\n', encoding="utf-8"
    )
    with pytest.raises(fetcher.GuardFailure, match="cargo_home_config_present"):
        fetcher._source_configuration(source, cargo_home)
