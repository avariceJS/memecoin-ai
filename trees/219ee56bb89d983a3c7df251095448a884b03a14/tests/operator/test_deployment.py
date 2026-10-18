"""Frozen operator bundle, seven-day clock, and a LaunchAgent plist that is not installed."""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from memecoin_ml.data import read, sha
from memecoin_operator import deployment
from memecoin_registry.contracts import verify
from memecoin_runner.ledger import publish

NS = 10**9
WEEK = 7 * 86400 * NS
ROOT = Path(__file__).resolve().parents[2]


def source_file(path: Path) -> Path:
    path.mkdir(parents=True)
    publish(path / "config.json", dict(expires_ns=1, synthetic=True, paid_budget_usd=0))
    return path


def operator_config(root: Path, source: Path, created_ns: int, expires_ns: int, **extra) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    payload = dict(
        real_execution_enabled=False,
        paid_budget_usd=0,
        created_ns=created_ns,
        expires_ns=expires_ns,
        source_root=str(source.resolve()),
        synthetic=True,
    )
    payload.update(extra)
    target = root / "config.json"
    if target.exists():
        target.unlink()
    publish(target, payload)
    return payload


def installed(tmp_path: Path, *, span: int = WEEK):
    source = source_file(tmp_path / "source")
    root = tmp_path / "op"
    created = 1_700_000_000 * NS
    operator_config(root, source, created, created + span)
    bundle = tmp_path / "bundle"
    pinned = deployment.stage(root, bundle)
    return root, bundle, source, pinned


def test_stage_freezes_the_tree_and_binds_root_config_and_interpreter(tmp_path):
    source = source_file(tmp_path / "source")
    root = tmp_path / "op"
    created = 1_700_000_000 * NS
    operator_config(root, source, created, created + WEEK)
    before = (root / "config.json").read_bytes()
    source_before = (source / "config.json").read_bytes()
    bundle = tmp_path / "bundle"
    pinned = deployment.stage(root, bundle)
    assert (root / "config.json").read_bytes() == before
    assert (source / "config.json").read_bytes() == source_before
    assert pinned["pin"] == sha(bundle / "manifest.json")
    verify(bundle)
    binding = read(bundle / "binding.json")
    assert binding["root"] == str(root.resolve())
    assert binding["config_sha"] == sha(root / "config.json")
    assert binding["python_sha"] == sha(Path(sys.executable).resolve())
    assert binding["source_config_sha"] == sha(source / "config.json")
    assert binding["expires_ns"] == created + WEEK
    copied = bundle / "runtime" / "memecoin_operator" / "deployment.py"
    assert sha(copied) == sha(ROOT / "src" / "memecoin_operator" / "deployment.py")
    assert deployment.guard(root)["bundle"] == str(bundle.resolve())


def test_rollout_bound_accepts_seven_days_and_rejects_the_next_nanosecond(tmp_path):
    source = source_file(tmp_path / "source")
    created = 1_700_000_000 * NS
    root = tmp_path / "exact"
    operator_config(root, source, created, created + WEEK)
    deployment.stage(root, tmp_path / "exact-bundle")
    for name, expires in (
        ("over", created + WEEK + 1),
        ("zero", created),
        ("negative", created - NS),
    ):
        op = tmp_path / name
        operator_config(op, source, created, expires)
        bundle = tmp_path / f"{name}-bundle"
        with pytest.raises(ValueError, match="operator_rollout_bound"):
            deployment.stage(op, bundle)
        assert not bundle.exists()


def test_staged_revision_can_be_verified_without_changing_running_pointer(tmp_path):
    root, old, _source, original = installed(tmp_path)
    before = (root / "deployment.json").read_bytes()
    newer = tmp_path / "next-bundle"
    revision = deployment.stage(root, newer, activate=False)
    assert (root / "deployment.json").read_bytes() == before
    assert deployment.guard(root) == original
    assert deployment.guard_revision(root, revision) == revision
    assert revision["bundle"] != str(old)


def test_stage_rejects_real_execution_and_a_paid_budget(tmp_path):
    source = source_file(tmp_path / "source")
    created = 1_700_000_000 * NS
    cases = (dict(real_execution_enabled=True), dict(paid_budget_usd=1))
    for index, extra in enumerate(cases):
        root = tmp_path / f"op-{index}"
        operator_config(root, source, created, created + NS, **extra)
        bundle = tmp_path / f"bundle-{index}"
        with pytest.raises(ValueError, match="operator_virtual_only"):
            deployment.stage(root, bundle)
        assert not bundle.exists()


def test_acquisition_shaped_config_stages_on_its_seven_day_clock(tmp_path):
    """The live operator clock is init_ns. Staging that config must not crash."""
    source = source_file(tmp_path / "source")
    root = tmp_path / "op"
    now = 1_700_000_000 * NS
    root.mkdir()
    publish(
        root / "config.json",
        dict(
            product="operator",
            mode="operator",
            provider="geckoterminal",
            profile="balanced",
            init_ns=now,
            expires_ns=now + 6 * 86400 * NS,
            real_execution_enabled=False,
            paid_budget_usd=0,
            additional_requests=0,
            max_background_requests=6000,
            source_root=str(source.resolve()),
        ),
    )
    pinned = deployment.stage(root, tmp_path / "bundle")
    binding = read((tmp_path / "bundle") / "binding.json")
    assert pinned["pin"] == sha(tmp_path / "bundle" / "manifest.json")
    assert binding["expires_ns"] == now + 6 * 86400 * NS


def test_guard_rejects_tamper_pin_drift_and_a_moved_root(tmp_path):
    root, bundle, _source, _pinned = installed(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    publish(other / "config.json", read(root / "config.json"))
    publish(other / "deployment.json", read(root / "deployment.json"))
    with pytest.raises(ValueError, match="operator_runtime_binding"):
        deployment.guard(other)
    deployment_path = root / "deployment.json"
    deployment_path.unlink()
    publish(deployment_path, dict(bundle=str(bundle.resolve()), pin="ab" * 32))
    with pytest.raises(ValueError, match="operator_runtime_pin"):
        deployment.guard(root)
    deployment_path.unlink()
    publish(
        deployment_path,
        dict(bundle=str(bundle.resolve()), pin=sha(bundle / "manifest.json")),
    )
    copied = bundle / "runtime" / "memecoin_operator" / "deployment.py"
    copied.write_bytes(copied.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="artifact_inventory_mismatch"):
        deployment.guard(root)


def test_guard_rejects_an_operator_config_rewrite(tmp_path):
    root, bundle, source, _pinned = installed(tmp_path / "case")
    created = 1_700_000_000 * NS
    operator_config(root, source, created, created + WEEK, synthetic=False)
    with pytest.raises(ValueError, match="operator_runtime_binding"):
        deployment.guard(root)
    verify(bundle)


def test_guard_rejects_a_rewritten_source_config(tmp_path):
    root, _bundle, source, _pinned = installed(tmp_path)
    stored = read((tmp_path / "bundle") / "binding.json")["source_config_sha"]
    path = source / "config.json"
    path.unlink()
    publish(path, dict(expires_ns=2, synthetic=True, paid_budget_usd=0))
    assert sha(path) != stored
    with pytest.raises(ValueError, match="operator_runtime_binding"):
        deployment.guard(root)


def test_launchagent_plist_uses_the_pinned_runtime_outside_the_real_home(tmp_path, monkeypatch):
    root, bundle, _source, _pinned = installed(tmp_path)
    real_home = Path.home()
    real_plist = real_home / "Library" / "LaunchAgents" / "ai.memecoin.brain.operator.plist"
    before = real_plist.stat().st_mtime_ns if real_plist.exists() else None
    home_env = os.environ.get("HOME")
    fake = tmp_path / "home"
    (fake / "Library" / "LaunchAgents").mkdir(parents=True)
    monkeypatch.setattr(deployment.Path, "home", lambda: fake)
    arguments = ["--root", str(root.resolve()), "--init"]
    info = deployment.write_launchagent(root, arguments)
    path = Path(info["path"])
    assert path.is_relative_to(fake)
    assert not path.is_relative_to(real_home)
    assert os.environ.get("HOME") == home_env
    assert info["label"] == "ai.memecoin.brain.operator"
    assert info["domain"] == f"gui/{os.getuid()}"
    with pytest.raises(FileExistsError):
        deployment.write_launchagent(root, arguments)
    plist = plistlib.loads(path.read_bytes())
    runtime = bundle / "runtime"
    argv = plist["ProgramArguments"]
    assert argv[:4] == [sys.executable, "-I", "-B", "-c"]
    assert "sys.path.insert(0,sys.argv.pop(1))" in argv[4]
    assert "from memecoin_operator.deployment import guard" in argv[4]
    assert "from memecoin_operator.runtime import main" in argv[4]
    assert argv[5:] == [str(runtime), str(root.resolve()), *arguments]
    assert plist["WorkingDirectory"] == str(root.resolve())
    assert plist["RunAtLoad"] is True
    probe = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            "import sys; sys.path.insert(0, sys.argv.pop(1));"
            "import memecoin_operator.deployment as pinned;"
            "print(pinned.__file__)",
            str(runtime),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert probe.returncode == 0, probe.stderr
    assert Path(probe.stdout.strip()).resolve().is_relative_to(runtime.resolve())
    boot = subprocess.run(
        argv,
        check=False,
        capture_output=True,
        cwd=root,
        timeout=60,
    )
    assert boot.returncode == 0, boot.stderr.decode()
    copied = runtime / "memecoin_operator" / "deployment.py"
    copied.write_bytes(copied.read_bytes() + b"\n")
    rejected = subprocess.run(argv, check=False, capture_output=True, cwd=root, timeout=60)
    assert rejected.returncode != 0
    after = real_plist.stat().st_mtime_ns if real_plist.exists() else None
    assert after == before
