"""Verified idempotent snapshot replication between local content stores."""

import os
import shutil
import tempfile
from pathlib import Path

from .storage import Snapshot, atomic_json, lock, object_path, safe_path, sync_dir, verify_files


def replicate(source, target, pin):
    snapshot = Snapshot(source, pin)
    target = safe_path(target)
    target.mkdir(parents=True, exist_ok=True)
    copied = reused = 0
    with lock(target / ".publication.lock"):
        for name in ("objects", "snapshots", "staging"):
            safe_path(target / name).mkdir(exist_ok=True)

        def objects():
            for page in snapshot.manifest.get("partition_pages", []):
                yield object_path(source, page["pin"]), page
            for i, entry in enumerate(snapshot.parts):
                origin, _ = snapshot.partition(i)
                yield origin, entry

        for origin, entry in objects():
            destination = object_path(target, entry["pin"])
            if destination.exists():
                verify_files(destination, entry["pin"])
                reused += 1
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            sync_dir(target / "objects")
            stage = Path(tempfile.mkdtemp(prefix="replica-", dir=target / "staging"))
            try:
                shutil.copytree(origin, stage, dirs_exist_ok=True)
                verify_files(stage, entry["pin"])
                for file in stage.iterdir():
                    with file.open("rb") as stream:
                        os.fsync(stream.fileno())
                sync_dir(stage)
                os.rename(stage, destination)
                sync_dir(destination.parent)
                copied += 1
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        atomic_json(target / "snapshots" / (pin + ".json"), snapshot.manifest)
    return dict(snapshot=pin, copied=copied, reused=reused)
