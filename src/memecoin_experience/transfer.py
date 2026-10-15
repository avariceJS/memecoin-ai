"""Verified idempotent snapshot replication between local content stores."""

import os
import shutil
import tempfile
from pathlib import Path

from .storage import Snapshot, atomic_json, lock, object_path, safe_path, sync_dir, verify_files


def _censor_lineage(snapshot):
    """Return declared v2 count-censoring ancestors after verifying their membership chain."""
    if (
        snapshot.schema.get("schema") != "brain-experience-v2"
        or not any(
            target["kind"].startswith("observed_process_count@")
            for target in snapshot.schema.get("targets", [])
        )
        or "composition" not in snapshot.manifest
    ):
        return []

    # This validates ancestry, role/schema compatibility, catalog pages, and exact member unions
    # before replication creates or publishes anything in the target.
    snapshot.censor_cutoff_ns()
    found = {}
    pending = [snapshot]
    while pending:
        current = pending.pop()
        for source in current.manifest.get("binding", {}).get("sources", []):
            if source.get("kind") != "snapshot":
                continue
            pin = source["pin"]
            if pin in found:
                continue
            parent = Snapshot(snapshot.store, pin)
            found[pin] = parent
            pending.append(parent)
    return list(found.values())


def replicate(source, target, pin):
    snapshot = Snapshot(source, pin)
    ancestors = _censor_lineage(snapshot)
    target = safe_path(target)
    target.mkdir(parents=True, exist_ok=True)
    copied = reused = 0
    with lock(target / ".publication.lock"):
        for name in ("objects", "snapshots", "staging"):
            safe_path(target / name).mkdir(exist_ok=True)

        def objects():
            emitted = set()
            for page in snapshot.manifest.get("partition_pages", []):
                emitted.add(page["pin"])
                yield object_path(source, page["pin"]), page
            for i, entry in enumerate(snapshot.parts):
                origin, _ = snapshot.partition(i)
                emitted.add(entry["pin"])
                yield origin, entry
                if "admission" in entry:
                    emitted.add(entry["admission"])
                    yield object_path(source, entry["admission"]), dict(pin=entry["admission"])
            for parent in ancestors:
                for page in parent.manifest.get("partition_pages", []):
                    if page["pin"] not in emitted:
                        emitted.add(page["pin"])
                        yield object_path(source, page["pin"]), page

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
        # The descendant is the publication boundary: its parents and catalog pages must already
        # be available when a target reader resolves its censoring lineage.
        for parent in ancestors:
            atomic_json(target / "snapshots" / (parent.pin + ".json"), parent.manifest)
        atomic_json(target / "snapshots" / (pin + ".json"), snapshot.manifest)
        if ancestors:
            restored = Snapshot(target, pin)
            if restored.censor_cutoff_ns() != snapshot.censor_cutoff_ns():
                raise ValueError("experience_replica_lineage")
    return dict(snapshot=pin, copied=copied, reused=reused)
