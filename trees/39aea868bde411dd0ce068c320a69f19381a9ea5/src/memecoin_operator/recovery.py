"""Explicit audited revision for a stopped paper exam; never removes its STOP."""

import fcntl
import shutil
import sqlite3
import time
from contextlib import ExitStack
from pathlib import Path

from memecoin_ml.data import read, sha
from memecoin_paper.audit import replay
from memecoin_runner.ledger import Ledger, publish


def recover_exam(old, root, prefix, port):
    old, root = Path(old), Path(root)
    if not (old / "STOP").exists():
        raise ValueError("recovery_requires_stopped_exam")
    config = read(old / "config.json")
    if not config.get("frozen_exam_models") or config["training_max_runs"] != 0:
        raise ValueError("recovery_only_frozen_exam")
    if time.time_ns() >= config["expires_ns"]:
        raise ValueError("recovery_exam_expired")
    with ExitStack() as stack:
        for name in (".paper.lock", ".train.lock"):
            lock = stack.enter_context((old / name).open("a"))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        binding = read(old / "ledger/binding.json")
        with Ledger(old / "ledger", binding) as ledger:
            count, head = len(ledger.events), ledger.head
            root.mkdir(exist_ok=False)
            for name in (
                "ledger",
                "training",
                "candidate-shadows",
                "candidate-ready",
                "recovery",
                "soak",
            ):
                if (old / name).exists():
                    shutil.copytree(
                        old / name,
                        root / name,
                    )
            if (old / "soak.sqlite").exists():
                with sqlite3.connect(old / "soak.sqlite") as src:
                    with sqlite3.connect(root / "soak.sqlite") as dst:
                        src.backup(dst)
                src.close()
                dst.close()
    before, after = replay(old, head), replay(root, head)
    if before != after:
        raise ValueError("recovery_prefix_mismatch")
    publish(
        root / "migration.json",
        dict(
            parent_root=str(old.resolve()),
            created_ns=time.time_ns(),
            prefix_head=head,
            prefix_events=count,
            prefix_state_sha=before["state_sha"],
            ledger_binding=binding,
            carried=before,
            parent_config_sha=sha(old / "config.json"),
            parent_stop_sha=sha(old / "STOP"),
            parent_migration_sha=sha(old / "migration.json")
            if (old / "migration.json").exists()
            else None,
            no_cash_reset=True,
            no_synthetic_exit=True,
            reason="consistent_SQLite_reader_revision",
        ),
    )
    config.update(migration_sha=sha(root / "migration.json"), service_prefix=prefix, port=port)
    publish(root / "config.json", config)
    publish(
        root / "activation.json",
        dict(
            at_ns=time.time_ns(),
            verdict="audited_stopped_exam_revision",
            parent_stop_sha=sha(old / "STOP"),
            expires_ns=config["expires_ns"],
            models_and_exam_start_unchanged=True,
            prefix_state_sha=before["state_sha"],
        ),
    )
    return before
