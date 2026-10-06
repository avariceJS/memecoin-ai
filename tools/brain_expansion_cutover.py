"""Exact idle operator runtime revision; preserve source, limits, journals and paper books."""

import argparse
import fcntl
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from memecoin_ml.data import read, sha
from memecoin_operator.deployment import guard, guard_revision, stage
from memecoin_paper.runtime import atomic
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import publish


def status(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5) as r:
        return json.load(r)


def prefixes(root):
    # Paper payloads remain with their existing exam. Verify every pre-cutover event/STOP.
    paths = [*root.glob("paper/*/ledger/event-*.json"), *root.glob("paper/*/STOP")]
    return {str(p.relative_to(root)): sha(p) for p in paths}


def install(root, corpus, bundle):
    original = guard(root)
    c = read(root / "config.json")
    state = status(c["port"])
    if state["mode"] != "auto" or state["phase"] != "waiting" or state["gates"]["busy"]:
        raise ValueError("cutover_requires_idle_auto")
    if state["gates"]["auto"]["eligible"] or not state["source"]["healthy"]:
        raise ValueError("cutover_not_safe_now")
    ref = dict(path=str(corpus), sha=sha(corpus / "manifest.json"))
    revision = stage(root, bundle, historical_training_corpora=[ref], activate=False)
    record = root / "revisions" / bundle.name
    record.mkdir(exist_ok=False)
    publish(record / "proposed-deployment.json", revision)
    publish(record / "previous-deployment.json", original)
    publish(record / "status-before.json", state)
    plist = Path.home() / "Library/LaunchAgents/ai.memecoin.brain.operator.plist"
    shutil.copyfile(plist, record / "previous-launchagent.plist")
    value = plistlib.loads(plist.read_bytes())
    argv = value["ProgramArguments"]
    old_runtime = str(Path(original["bundle"]) / "runtime")
    if argv.count(old_runtime) != 1 or Path(argv[0]).resolve() != Path(sys.executable).resolve():
        raise ValueError("cutover_launchagent_binding")
    value["ProgramArguments"] = [str(bundle / "runtime") if a == old_runtime else a for a in argv]
    locks = []
    domain = f"gui/{os.getuid()}"
    label = "ai.memecoin.brain.operator"
    removed = False
    try:
        # Drain between ticks. Hold the same locks used by the existing background manager,
        # so stopping its parent cannot interrupt an in-flight paper transaction.
        deadline = time.monotonic() + 45
        for path in sorted((root / "background/locks").glob("*.lock")):
            fd = os.open(path, os.O_RDWR)
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locks.append(fd)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        os.close(fd)
                        raise ValueError("cutover_drain_timeout") from None
                    time.sleep(0.2)
        before = prefixes(root)
        publish(record / "preserved-prefixes.json", before)
        latest = status(c["port"])
        if latest["gates"]["busy"] or latest["gates"]["auto"]["eligible"]:
            raise ValueError("cutover_operator_became_busy")
        result = subprocess.run(
            ["launchctl", "bootout", f"{domain}/{label}"], capture_output=True, timeout=20
        )
        if result.returncode:
            raise ValueError("cutover_bootout_failed")
        removed = True
        guard_revision(root, revision)
        atomic(root / "deployment.json", revision)
        temporary = record / "proposed-launchagent.plist"
        temporary.write_bytes(plistlib.dumps(value))
        # Keep both plists as evidence. Atomic replacement prevents partial launch config.
        pending = plist.with_name(plist.name + ".pending")
        pending.write_bytes(temporary.read_bytes())
        os.replace(pending, plist)
        result = subprocess.run(
            ["launchctl", "bootstrap", domain, str(plist)], capture_output=True, timeout=20
        )
        if result.returncode:
            raise ValueError("cutover_bootstrap_failed")
        ready = None
        for _ in range(25):
            try:
                ready = status(c["port"])
                break
            except OSError:
                time.sleep(0.2)
        if (
            not ready
            or ready["mode"] != state["mode"]
            or ready["gates"]["runs_used"] != state["gates"]["runs_used"]
        ):
            raise ValueError("cutover_state_changed")
        if any(sha(root / p) != pin for p, pin in before.items()):
            raise ValueError("cutover_prefix_changed")
        publish(
            record / "activation.json",
            dict(
                at_ns=time.time_ns(),
                previous=original,
                deployment=revision,
                config_sha=sha(root / "config.json"),
                source_config_sha=sha(Path(c["source_root"]) / "config.json"),
                training_history=ref,
                status=ready,
                preserved_events=len(before),
                no_quota_reset=True,
            ),
        )
        seal(record)
    except Exception as exc:
        if removed:
            subprocess.run(
                ["launchctl", "bootout", f"{domain}/{label}"], capture_output=True, timeout=20
            )
            atomic(root / "deployment.json", original)
            plist.write_bytes((record / "previous-launchagent.plist").read_bytes())
            restored = subprocess.run(
                ["launchctl", "bootstrap", domain, str(plist)], capture_output=True, timeout=20
            )
            publish(
                record / "rollback.json", dict(at_ns=time.time_ns(), returncode=restored.returncode)
            )
        publish(
            record / "STOP",
            dict(at_ns=time.time_ns(), category=type(exc).__name__, automatic_retry=False),
        )
        if not (record / "manifest.json").exists():
            seal(record)
        raise
    finally:
        for fd in locks:
            os.close(fd)
    print(json.dumps(dict(revision=revision, record=str(record), refs=[ref])))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("corpus", type=Path)
    p.add_argument("bundle", type=Path)
    a = p.parse_args()
    install(a.root.resolve(), a.corpus.resolve(), a.bundle.resolve())


if __name__ == "__main__":
    main()
