"""Network-free reproduction and externally pinned immutable paper snapshots."""

import shutil
from collections import Counter

from memecoin_ml.data import read, sha
from memecoin_registry.contracts import digest, seal, verify
from memecoin_runner.ledger import Ledger, publish

from .engine import advance, initial


def replay(root, expected_head=None):
    state, records = initial(), []
    with Ledger(root / "ledger", read(root / "ledger/binding.json")) as ledger:
        events = ledger.audit(expected_head)
        for e in events:
            state, added = advance(state, e["payload"]["tick"])
            if digest(dict(state=state, records=added)) != e["payload"]["result_sha"]:
                raise ValueError("paper_replay_mismatch")
            records.extend(added)
        head = ledger.head
    counts = Counter(r["kind"] for r in records)
    return dict(
        state_sha=digest(state),
        ledger_head=head,
        ticks=len(events),
        counts=dict(counts),
        books=len(state["books"]),
        realized={k: b["realized"] for k, b in state["books"].items()},
        open_positions=sum(len(b["positions"]) for b in state["books"].values()),
        pending=sum(len(b["pending"]) for b in state["books"].values()),
        decision_reasons=dict(Counter(r["reason"] for r in records if r["kind"] == "decision")),
        actual_entry_exit_order=all(
            r["decision_ns"] < r["entry_ns"] < r["exit_ns"]
            and r["entry_close_s"] * 10**9 > r["decision_ns"]
            and r["exit_close_s"] >= r["entry_close_s"] + 900
            for r in records
            if r["kind"] == "exit"
        ),
        no_real_execution=True,
        calibrated_execution=False,
        edge_established=False,
    )


def snapshot(source, output):
    output.mkdir(exist_ok=False)
    binding = read(source / "ledger/binding.json")
    with Ledger(source / "ledger", binding) as ledger:
        (output / "ledger").mkdir()
        for path in [
            source / "ledger/binding.json",
            *sorted((source / "ledger").glob("event-*.json")),
        ]:
            shutil.copyfile(path, output / "ledger" / path.name)
        head = ledger.head
        for name in ("config.json", "deployment.json", "activation.json"):
            shutil.copyfile(source / name, output / name)
        if (source / "migration.json").exists():
            shutil.copyfile(source / "migration.json", output / "migration.json")
        publish(output / "external-head.json", dict(ledger_head=head))
    report = replay(output, head)
    publish(output / "result.json", report)
    seal(output)
    verify(output)
    return dict(**report, snapshot_manifest_sha=sha(output / "manifest.json"))
