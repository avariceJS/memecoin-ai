"""Explicitly stage B07 virtual rollout after tests; no source job mutation or real trades."""

import socket
import time
from pathlib import Path

from memecoin_brain_quality.runtime import verify_runtime
from memecoin_ml.data import read, sha
from memecoin_paper.runtime import stage
from memecoin_registry.contracts import verify
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE / "work/brain-b07/paper02"
SOURCE = BASE / "work/brain-b04/prospective01"
PIN = "f248c0305d81f684db7954d1c914c335c01c2ab0f9312d10ecc593ab6d2d183e"


def main():
    verify_runtime(SOURCE, PIN)
    model = BASE / "work/brain-b01/dev01/models/000000"
    verify(model)
    expires = read(SOURCE / "config.json")["expires_ns"]
    if (SOURCE / "STOP").exists() or time.time_ns() >= expires:
        raise ValueError("source_not_active")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 8790))
    ROOT.mkdir(exist_ok=False)
    for name in ("training", "candidate-shadows"):
        (ROOT / name).mkdir()
    publish(
        ROOT / "config.json",
        dict(
            source=str(SOURCE),
            source_runtime_pin=PIN,
            activation_ns=time.time_ns(),
            expires_ns=expires,
            champion_model_sha=sha(model / "manifest.json"),
            port=8790,
            policy="B07-price-proxy-1",
            real_execution_enabled=False,
            training_max_runs=3,
            paper_worker_timeout_s=120,
            training_worker_timeout_s=600,
            plan_sha=sha(BASE / "docs/brain-paper-loop.md"),
        ),
    )
    publish(
        ROOT / "activation.json",
        dict(
            verdict="activate_virtual_only_bounded_rollout",
            owner_authorization="direct_B07_strategy_paper_shadow_only",
            config_sha=sha(ROOT / "config.json"),
            qualification="tests/paper: causal accounting, restart, boundaries, read-only HTTP",
            source_untouched=True,
            F01_D43_holdout_untouched=True,
            costs="uncalibrated 50bps slippage + 25bps fee per side",
        ),
    )
    print(stage(ROOT, BASE / "work/brain-b07/runtime02"))


if __name__ == "__main__":
    main()
