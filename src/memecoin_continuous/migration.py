"""Explicit version boundary: copy old evidence exactly, preserve cash and open positions."""

import fcntl
import shutil
import time
from contextlib import ExitStack
from pathlib import Path

from memecoin_ml.data import read, sha
from memecoin_paper.audit import replay
from memecoin_runner.ledger import Ledger, publish


def migrate(old, root, source, pin, champion, *, service_prefix=None):
    with ExitStack() as stack:
        for name in (".paper.lock", ".train.lock"):
            lock = stack.enter_context((old / name).open("a"))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        binding = read(old / "ledger/binding.json")
        with Ledger(old / "ledger", binding) as ledger:
            head = ledger.head
            count = len(ledger.events)
            root.mkdir(exist_ok=False)
            shutil.copytree(
                old / "ledger",
                root / "ledger",
                ignore=shutil.ignore_patterns(".lock", ".pending-*"),
            )
            for name in ("training", "candidate-shadows"):
                shutil.copytree(old / name, root / name)
            prior_source = Path(read(old / "config.json")["source"])
            dest = root / "candidate-shadows" / sha(champion / "manifest.json") / "shadow"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                shutil.copytree(prior_source / "shadow", dest)
            for name in ("soak", "recovery", "candidate-ready"):
                if (old / name).exists():
                    shutil.copytree(old / name, root / name)
            if (old / "soak.sqlite").exists():
                import sqlite3

                with sqlite3.connect(old / "soak.sqlite") as src_db:
                    with sqlite3.connect(root / "soak.sqlite") as dst_db:
                        src_db.backup(dst_db)
                src_db.close()
                dst_db.close()
    previous = replay(old, head)
    copied = replay(root, head)
    if copied != previous:
        raise ValueError("paper_migration_not_exact")
    publish(
        root / "migration.json",
        dict(
            parent_root=str(old.resolve()),
            created_ns=time.time_ns(),
            prefix_head=head,
            prefix_events=count,
            prefix_state_sha=previous["state_sha"],
            ledger_binding=binding,
            carried=previous,
            parent_config_sha=sha(old / "config.json"),
            parent_migration_sha=sha(old / "migration.json")
            if (old / "migration.json").exists()
            else None,
            parent_stop_sha=sha(old / "STOP") if (old / "STOP").exists() else None,
            no_cash_reset=True,
            no_synthetic_exit=True,
        ),
    )
    c = read(old / "config.json")
    c.update(
        source=str(source.resolve()),
        source_backend="continuous",
        source_runtime_pin=pin,
        expires_ns=read(source / "config.json")["expires_ns"],
        migration_sha=sha(root / "migration.json"),
        champion_model_path=str(champion.resolve()),
        training_max_runs=min(
            c.get("training_max_runs", 8), read(source / "config.json").get("training_max_runs", 8)
        ),
        training_bar_cap=28000,
        service_prefix=service_prefix
        or read(source / "config.json").get("service_prefix", "ai.memecoin.brain.b08."),
    )
    publish(root / "config.json", c)
    publish(
        root / "activation.json",
        dict(
            verdict="approved_virtual_migration",
            config_sha=sha(root / "config.json"),
            migration_sha=sha(root / "migration.json"),
            source_runtime_pin=pin,
            F01_D43_holdout_untouched=True,
        ),
    )
    return previous
