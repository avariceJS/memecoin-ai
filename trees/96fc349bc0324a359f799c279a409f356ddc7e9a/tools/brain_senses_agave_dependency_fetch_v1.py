#!/usr/bin/env python3
"""Fetch one pinned Agave dependency closure into the bound Kingston Cargo home.

This command cannot build or test. It permits only Cargo's locked fetch operation,
with a private Cargo home, a sanitized environment, and fixed time/storage caps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

from brain_senses_build_storage_guard import (
    MIN_FREE_BYTES,
    RUST_VERSION,
    BoundStorageSnapshot,
    GuardFailure,
    _allocated_bytes,
    _bounded_inspect_stage,
    _build_environment,
    _check_rust_toolchain,
    _check_stage_paths,
    _check_unprivileged_root1,
    _secure_mkdirs,
    _terminate_group,
)

EXPECTED_GIT_SOURCE = (
    "git+https://github.com/anza-xyz/crossbeam?"
    "rev=fd279d707025f0e60951e429bf778b4813d1b6bf"
    "#fd279d707025f0e60951e429bf778b4813d1b6bf"
)
EXPECTED_REGISTRY_SOURCE = "registry+https://github.com/rust-lang/crates.io-index"
EXPECTED_AGAVE_HEAD = "b6c96e84b10396b92912d4574dae7d03f606da26"
EXPECTED_POST_V5_LOCK = "fc3fdf681e0c0a89399c07376747e13cdd5a31d61f6b209df5437a9594fc76a5"
EXPECTED_COMPOSITE_DIFF = "b35b3dba78a4dc90aee3f91827cf1d6467f728d7218d8e3d7c14a861dcc6aa78"
MAX_CACHE_GROWTH_BYTES = 8 * 1024**3
MAX_WALL_SECONDS = 30 * 60
POLL_SECONDS = 0.2
EXPECTED_CHANGED_PATHS = frozenset(
    {
        "Cargo.lock",
        "banks-interface/Cargo.toml",
        "banks-interface/src/lib.rs",
        "banks-server/Cargo.toml",
        "banks-server/src/banks_server.rs",
        "program-runtime/Cargo.toml",
        "program-runtime/src/invoke_context.rs",
        "program-test/Cargo.toml",
        "program-test/src/candidate_position_raw_account.rs",
        "program-test/src/candidate_position_witness_mapping.rs",
        "program-test/src/lib.rs",
        "program-test/tests/cpi.rs",
        "runtime-transaction/src/runtime_transaction.rs",
        "runtime/src/bank.rs",
        "svm-transaction/src/svm_transaction.rs",
        "svm-transaction/src/svm_transaction/sanitized_transaction.rs",
        "svm/Cargo.toml",
        "svm/src/transaction_commit_result.rs",
        "svm/src/transaction_execution_result.rs",
        "svm/src/transaction_processor.rs",
    }
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _locked_sources(lock_path: Path) -> tuple[list[str], int]:
    data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    packages = data.get("package")
    if not isinstance(packages, list):
        raise GuardFailure("cargo_lock_package_table")
    sources = sorted({p["source"] for p in packages if isinstance(p, dict) and "source" in p})
    allowed = {EXPECTED_GIT_SOURCE, EXPECTED_REGISTRY_SOURCE}
    if set(sources) != allowed:
        raise GuardFailure("cargo_lock_has_unapproved_source")
    return sources, len(packages)


def _source_configuration(source: Path, cargo_home: Path) -> None:
    config = source / ".cargo" / "config.toml"
    for path in (source / ".cargo" / "config", config):
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        if path == config:
            if path.is_symlink() or not path.is_file():
                raise GuardFailure("source_cargo_config_not_plain_file")
        else:
            raise GuardFailure("legacy_source_cargo_config_present")
    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    if set(parsed) != {"target"}:
        raise GuardFailure("source_cargo_config_has_unapproved_keys")
    if parsed["target"] != {"x86_64-unknown-linux-gnu": {"rustflags": ["-Ctarget-cpu=x86-64-v2"]}}:
        raise GuardFailure("source_cargo_config_mismatch")
    for name in ("config", "config.toml"):
        try:
            (cargo_home / name).lstat()
        except FileNotFoundError:
            continue
        raise GuardFailure("cargo_home_config_present")
    for parent in source.parents:
        candidates = (parent / ".cargo" / "config", parent / ".cargo" / "config.toml")
        if any(_lexists(candidate) for candidate in candidates):
            raise GuardFailure("ancestor_cargo_config_present")
    git_config = source / ".git" / "config"
    if not git_config.is_file() or git_config.is_symlink():
        raise GuardFailure("source_git_config_not_plain_file")
    local = (
        subprocess.run(
            ["git", "config", "--file", str(git_config), "--get", "remote.origin.url"],
            check=True,
            capture_output=True,
            timeout=10,
            env=_safe_git_env({"PATH": "/usr/bin:/bin", "HOME": str(cargo_home)}),
        )
        .stdout.decode("utf-8")
        .strip()
    )
    if local != "https://github.com/anza-xyz/agave.git":
        raise GuardFailure("source_git_origin_mismatch")


def _lexists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _safe_git_env(env: dict[str, str]) -> dict[str, str]:
    return {
        **env,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }


def _git_snapshot(source: Path, env: dict[str, str]) -> tuple[str, str, dict[str, str]]:
    safe_env = _safe_git_env(env)
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=source,
        check=True,
        capture_output=True,
        timeout=10,
        env=safe_env,
    ).stdout
    if any(line.startswith(b"??") for line in status.splitlines()):
        raise GuardFailure("untracked_source_path")
    changed = (
        subprocess.run(
            ["git", "--no-pager", "diff", "--name-status", "--no-renames", "-z", "HEAD"],
            cwd=source,
            check=True,
            capture_output=True,
            timeout=10,
            env=safe_env,
        )
        .stdout.decode("utf-8")
        .split("\0")
    )
    changed = [item for item in changed if item]
    changed_paths = {changed[i + 1] for i in range(0, len(changed), 2)}
    if len(changed) % 2 or changed_paths != EXPECTED_CHANGED_PATHS:
        raise GuardFailure("source_changed_path_set_mismatch")
    diff = subprocess.run(
        ["git", "--no-pager", "diff", "--no-ext-diff", "--binary", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        timeout=10,
        env=safe_env,
    ).stdout
    file_hashes = {rel: _sha256((source / rel).read_bytes()) for rel in sorted(changed_paths)}
    return _sha256(status), _sha256(diff), file_hashes


def _source_revision(source: Path, env: dict[str, str]) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        timeout=10,
        env=_safe_git_env(env),
    )
    return result.stdout.decode("ascii").strip()


def _host_triple(env: dict[str, str]) -> str:
    result = subprocess.run(
        ["rustc", "-vV"], check=True, capture_output=True, text=True, timeout=10, env=env
    )
    for line in result.stdout.splitlines():
        if line.startswith("host: "):
            return line.removeprefix("host: ")
    raise GuardFailure("rustc_host_missing")


def _git_checkout_revision(cargo_home: Path, expected: str, env: dict[str, str]) -> bool:
    checkouts = cargo_home / "git" / "checkouts"
    if not checkouts.is_dir():
        return False
    for repository in checkouts.iterdir():
        if not repository.is_dir() or repository.is_symlink():
            continue
        for checkout in repository.iterdir():
            if not checkout.is_dir() or checkout.is_symlink():
                continue
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=checkout,
                    check=True,
                    capture_output=True,
                    timeout=10,
                    env=_safe_git_env(env),
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if result.stdout.decode("ascii").strip() == expected:
                return True
    return False


def run_fetch(
    source: Path, cargo_home: Path, target_dir: Path, tmpdir: Path, *, check_only: bool = False
) -> dict[str, Any]:
    _check_unprivileged_root1()
    source = source.resolve(strict=True)
    cargo_home = cargo_home.resolve(strict=False)
    target_dir = target_dir.resolve(strict=False)
    tmpdir = tmpdir.resolve(strict=False)
    provider = BoundStorageSnapshot()
    first = provider.inspect()
    paths = _check_stage_paths((source, cargo_home, target_dir, tmpdir), first)
    if first.available_bytes < 90 * 1024**3:
        raise GuardFailure("initial_free_below_90_gib")
    _secure_mkdirs(paths[1:], first)
    sources, package_count = _locked_sources(source / "Cargo.lock")
    _source_configuration(source, cargo_home)
    if target_dir.exists() and any(target_dir.iterdir()):
        raise GuardFailure("target_dir_not_empty")

    env = _build_environment(paths)
    env["PATH"] = "/Users/root1/.cargo/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    env.update(
        {
            "HOME": str(cargo_home),
            "CARGO_HOME": str(cargo_home),
            "CARGO_TARGET_DIR": str(target_dir),
            "TMPDIR": str(tmpdir),
            "TMP": str(tmpdir),
            "TEMP": str(tmpdir),
            "CARGO_NET_OFFLINE": "false",
            "CARGO_NET_RETRY": "0",
            "CARGO_NET_GIT_FETCH_WITH_CLI": "false",
            "CARGO_REGISTRIES_CRATES_IO_PROTOCOL": "sparse",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "RUSTUP_TOOLCHAIN": RUST_VERSION,
        }
    )
    _check_rust_toolchain(env)
    host = _host_triple(env)
    if _source_revision(source, env) != EXPECTED_AGAVE_HEAD:
        raise GuardFailure("agave_head_changed_before_fetch")
    source_status = _git_snapshot(source, env)
    if source_status[1] != EXPECTED_COMPOSITE_DIFF:
        raise GuardFailure("composite_source_diff_mismatch")
    lock_hash = _sha256((source / "Cargo.lock").read_bytes())
    if lock_hash != EXPECTED_POST_V5_LOCK:
        raise GuardFailure("post_v5_cargo_lock_mismatch")
    initial_allocated = _allocated_bytes(
        paths, first.mount_device, allow_cargo_git_checkout_symlinks=True
    )
    argv = [
        shutil.which("cargo", path=env["PATH"]) or "cargo",
        "fetch",
        "--locked",
        "--manifest-path",
        str(source / "program-test" / "Cargo.toml"),
        "--target",
        host,
    ]
    _bounded_inspect_stage(
        provider,
        paths,
        first,
        timeout_seconds=2.0,
        allow_cargo_git_checkout_symlinks=True,
    )
    if check_only:
        return {
            "status": "fetch_preflight_passed_no_network",
            "argv": argv,
            "host_triple": host,
            "lock_sha256": lock_hash,
            "lock_sources": sources,
            "locked_package_count": package_count,
            "source_status_sha256": source_status[0],
            "source_diff_sha256": source_status[1],
            "changed_paths_sha256": source_status[2],
            "storage_root_id": first.root_id,
            "volume_uuid": first.volume_uuid,
            "available_bytes": first.available_bytes,
        }
    process = subprocess.Popen(
        argv,
        cwd=source,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    started = time.monotonic()
    try:
        while True:
            if time.monotonic() - started > MAX_WALL_SECONDS:
                raise GuardFailure("dependency_fetch_timeout")
            current, allocated = _bounded_inspect_stage(
                provider,
                paths,
                first,
                timeout_seconds=2.0,
                allow_cargo_git_checkout_symlinks=True,
            )
            if allocated - initial_allocated > MAX_CACHE_GROWTH_BYTES:
                raise GuardFailure("dependency_cache_growth_over_8_gib")
            if current.available_bytes < MIN_FREE_BYTES:
                raise GuardFailure("dependency_fetch_free_floor")
            status = process.poll()
            if status is not None:
                _terminate_group(process, grace_seconds=1.0)
                if status != 0:
                    raise GuardFailure(f"cargo_fetch_exit_{status}")
                break
            time.sleep(POLL_SECONDS)
    except BaseException:
        _terminate_group(process, grace_seconds=1.0)
        raise

    final, final_allocated = _bounded_inspect_stage(
        provider,
        paths,
        first,
        timeout_seconds=2.0,
        allow_cargo_git_checkout_symlinks=True,
    )
    if final.available_bytes < MIN_FREE_BYTES:
        raise GuardFailure("dependency_fetch_final_free_floor")
    if final_allocated - initial_allocated > MAX_CACHE_GROWTH_BYTES:
        raise GuardFailure("dependency_cache_growth_over_8_gib")
    if _git_snapshot(source, env) != source_status:
        raise GuardFailure("cargo_fetch_modified_source")
    if _sha256((source / "Cargo.lock").read_bytes()) != lock_hash:
        raise GuardFailure("cargo_fetch_modified_lock")
    if not _git_checkout_revision(cargo_home, "fd279d707025f0e60951e429bf778b4813d1b6bf", env):
        raise GuardFailure("pinned_crossbeam_revision_not_checked_out")
    if any(target_dir.iterdir()):
        raise GuardFailure("cargo_fetch_created_build_output")
    return {
        "status": "locked_dependencies_fetched_no_build",
        "argv": argv,
        "host_triple": host,
        "rustc": subprocess.run(
            ["rustc", "--version"], check=True, capture_output=True, text=True, env=env
        ).stdout.strip(),
        "cargo": subprocess.run(
            ["cargo", "--version"], check=True, capture_output=True, text=True, env=env
        ).stdout.strip(),
        "lock_sha256": lock_hash,
        "lock_sources": sources,
        "locked_package_count": package_count,
        "source_status_sha256": source_status[0],
        "source_diff_sha256": source_status[1],
        "changed_paths_sha256": source_status[2],
        "cache_growth_bytes": max(0, final_allocated - initial_allocated),
        "elapsed_seconds": time.monotonic() - started,
        "storage_root_id": final.root_id,
        "volume_uuid": final.volume_uuid,
        "remaining_free_bytes": final.available_bytes,
        "build_output_count": 0,
        "source_or_lock_modified": False,
        "retry_count": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--cargo-home", type=Path, required=True)
    parser.add_argument("--target-dir", type=Path, required=True)
    parser.add_argument("--tmpdir", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                run_fetch(
                    args.source,
                    args.cargo_home,
                    args.target_dir,
                    args.tmpdir,
                    check_only=args.check_only,
                )
            )
        )
        return 0
    except (GuardFailure, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"dependency fetch stopped: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
