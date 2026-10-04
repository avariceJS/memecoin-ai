"""Pinned single-machine operator installation; approval comes from the explicit B13 task."""

import os
import plistlib
import shutil
import sys
from pathlib import Path

from memecoin_ml.data import read, sha
from memecoin_registry.contracts import seal, verify
from memecoin_runner.ledger import publish


def stage(root, bundle):
    root, bundle = Path(root).resolve(), Path(bundle).resolve()
    c = read(root / "config.json")
    if c.get("real_execution_enabled") or c.get("paid_budget_usd", 0) != 0:
        raise ValueError("operator_virtual_only")
    created = c.get("created_ns", c.get("init_ns"))
    if type(created) is not int or not 0 < c["expires_ns"] - created <= 7 * 86400 * 10**9:
        raise ValueError("operator_rollout_bound")
    bundle.mkdir(exist_ok=False)
    src = Path(__file__).parent.parent
    for path in src.glob("memecoin_*/*.py"):
        target = bundle / "runtime" / path.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    publish(
        bundle / "binding.json",
        dict(
            root=str(root),
            config_sha=sha(root / "config.json"),
            python_sha=sha(Path(sys.executable).resolve()),
            expires_ns=c["expires_ns"],
            source_config_sha=sha(Path(c["source_root"]) / "config.json"),
        ),
    )
    seal(bundle)
    publish(root / "deployment.json", dict(bundle=str(bundle), pin=sha(bundle / "manifest.json")))
    return guard(root)


def guard(root):
    d = read(root / "deployment.json")
    bundle = Path(d["bundle"])
    if sha(bundle / "manifest.json") != d["pin"]:
        raise ValueError("operator_runtime_pin")
    verify(bundle)
    b = read(bundle / "binding.json")
    c = read(root / "config.json")
    if (
        b["root"] != str(root.resolve())
        or b["config_sha"] != sha(root / "config.json")
        or b["python_sha"] != sha(Path(sys.executable).resolve())
        or b["source_config_sha"] != sha(Path(c["source_root"]) / "config.json")
        or b["expires_ns"] != c["expires_ns"]
    ):
        raise ValueError("operator_runtime_binding")
    return d


def write_launchagent(root, arguments):
    root = Path(root).resolve()
    d = guard(root)
    label = "ai.memecoin.brain.operator"
    runtime = Path(d["bundle"]) / "runtime"
    script = (
        "import os,sys;os.environ.pop('MEMECOIN_OPERATOR_ADAPTER',None);"
        "os.environ.pop('MEMECOIN_SYNTHETIC_TICK',None);"
        "sys.path.insert(0,sys.argv.pop(1));from pathlib import Path;"
        "from memecoin_operator.deployment import guard;guard(Path(sys.argv.pop(1)));"
        "from memecoin_operator.runtime import main;main()"
    )
    plist = dict(
        Label=label,
        RunAtLoad=True,
        KeepAlive=True,
        ThrottleInterval=15,
        ProcessType="Background",
        Nice=10,
        WorkingDirectory=str(root),
        ProgramArguments=[
            sys.executable,
            "-I",
            "-B",
            "-c",
            script,
            str(runtime),
            str(root),
            *arguments,
        ],
        StandardOutPath=str(root / "operator.stdout.log"),
        StandardErrorPath=str(root / "operator.stderr.log"),
    )
    path = Path.home() / "Library/LaunchAgents" / (label + ".plist")
    with path.open("xb") as stream:
        plistlib.dump(plist, stream)
    return dict(label=label, path=str(path), domain=f"gui/{os.getuid()}")
