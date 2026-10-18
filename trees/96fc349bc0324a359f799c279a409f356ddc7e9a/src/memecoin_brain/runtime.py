"""Independent immutable Brain deployment, never touching D43 or F01 data/services."""

from __future__ import annotations

import os
import plistlib
import shutil
import sys
import time
from pathlib import Path

from memecoin_ml.data import read, sha, write
from memecoin_operational.contract import no_symlink
from memecoin_registry.contracts import seal, verify


def stage(root: Path, bundle: Path, plist: Path) -> None:
    root = root.resolve()
    no_symlink(root)
    no_symlink(bundle)
    if root.name != "dev01" or root.parent.name != "brain-b01":
        raise ValueError("Brain_root_only")
    if (root / "deployment.json").exists():
        raise ValueError("deployment_exists")
    activation = read(root / "activation.json")
    if (
        activation.get("decision") != "approved"
        or activation.get("scope") != "brain_development_72h"
        or activation.get("paid_budget_usd") != 0
    ):
        raise ValueError("Brain_activation_required")
    bundle.mkdir(parents=True, exist_ok=False)
    src = Path(__file__).parent.parent
    sources = {}
    for path in sorted(src.glob("memecoin_*/*.py")):
        name = str(path.relative_to(src))
        target = bundle / "runtime" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        sources[name] = sha(path)
    c = dict(
        root=str(root),
        config_sha=sha(root / "config.json"),
        universe_sha=sha(root / "universe.json"),
        activation_sha=sha(root / "activation.json"),
        external_debit_sha=sha(root / "external-request-debit.json"),
        sources=sources,
        python=str(Path(sys.executable).resolve()),
        python_sha=sha(Path(sys.executable).resolve()),
        created_ns=time.time_ns(),
        track="brain_development",
        F01=False,
        service_label="ai.memecoin.brain.dev01",
    )
    write(bundle / "deployment.json", c)
    seal(bundle)
    pin = sha(bundle / "manifest.json")
    script = (
        "import sys; sys.path.insert(0,sys.argv.pop(1)); "
        "from memecoin_brain.service import main; main()"
    )
    args = [
        sys.executable,
        "-I",
        "-B",
        "-c",
        script,
        str((bundle / "runtime").resolve()),
        str(root),
        pin,
    ]
    value = dict(
        Label=c["service_label"],
        StartInterval=60,
        RunAtLoad=True,
        WorkingDirectory=str(root),
        ProgramArguments=args,
        ProcessType="Background",
        LowPriorityIO=True,
        Nice=10,
        StandardOutPath=str(root / "service.stdout.log"),
        StandardErrorPath=str(root / "service.stderr.log"),
    )
    with plist.open("xb") as f:
        plistlib.dump(value, f)
    write(
        root / "deployment.json",
        dict(bundle=str(bundle.resolve()), pin=pin, service_plist_sha=sha(plist)),
    )


def verify_runtime(root: Path, pin: str | None = None) -> None:
    ref = read(root / "deployment.json")
    bundle = Path(ref["bundle"])
    no_symlink(bundle)
    if pin is not None and pin != ref["pin"]:
        raise ValueError("deployment_pin")
    if sha(bundle / "manifest.json") != ref["pin"]:
        raise ValueError("runtime_pin_changed")
    verify(bundle)
    c = read(bundle / "deployment.json")
    if (
        c["root"] != str(root.resolve())
        or sha(root / "config.json") != c["config_sha"]
        or sha(root / "universe.json") != c["universe_sha"]
        or sha(root / "activation.json") != c["activation_sha"]
        or sha(root / "external-request-debit.json") != c["external_debit_sha"]
        or sha(Path(sys.executable).resolve()) != c["python_sha"]
    ):
        raise ValueError("Brain_binding_changed")
    src = Path(__file__).parent.parent
    if any(sha(src / name) != expected for name, expected in c["sources"].items()):
        raise ValueError("imported_runtime_changed")
    deadline = os.environ.get("MEMECOIN_BRAIN_DEADLINE_NS")
    if deadline and time.time_ns() >= int(deadline):
        raise ValueError("worker_deadline")
