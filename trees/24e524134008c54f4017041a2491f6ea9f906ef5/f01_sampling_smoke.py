"""Explicit synthetic transport from old public reference bytes; no network/market data."""

from __future__ import annotations

import argparse
import base64
import json
import os
import struct
import sys
import time
from pathlib import Path

from memecoin_ai.domain import canonical
from memecoin_ai.execution_targets import export_execution_targets
from memecoin_ai.prospective import create_campaign, resume_campaign
from memecoin_ai.sources import Capture, ReadOnlyRPC
from memecoin_ml.data import read, sha, write
from memecoin_registry.contracts import inventory
from memecoin_research.framework import DAY
from memecoin_sampling.contract import NS, freeze
from memecoin_sampling.dossier import export_dossier, reproduce_dossier
from memecoin_sampling.registry import ingest, initialize, reproduce_sources


class FixtureClock:
    def __init__(self, start: int, references: list[Capture]) -> None:
        self.now, self.calls = start, 0
        self.references = {json.loads(c.request_json)["params"][0][0]: c for c in references}

    def clock(self) -> int:
        self.now += 100000
        return self.now

    def mono(self) -> float:
        return self.now / NS

    def sleep(self, seconds: float) -> None:
        self.now += int(seconds * NS)

    def transport(self, endpoint: str, request: bytes, timeout: float, limit: int) -> bytes:
        self.calls += 1
        cap = self.references[json.loads(request)["params"][0][0]]
        payload = json.loads(cap.response)
        slot = payload["result"]["context"]["slot"] + self.calls
        payload["result"]["context"]["slot"] = slot
        clock = bytearray(base64.b64decode(payload["result"]["value"][6]["data"][0]))
        struct.pack_into("<Q", clock, 0, slot)
        struct.pack_into("<q", clock, 32, self.now // NS - 2)
        payload["result"]["value"][6]["data"][0] = base64.b64encode(clock).decode()
        return canonical(payload).encode()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    protected = Path("work/calendar-execution-01").resolve()

    def guard(event, args):
        if event == "socket.connect":
            raise PermissionError("D48_network_forbidden")
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            if Path(os.fsdecode(args[0])).resolve().is_relative_to(protected):
                raise PermissionError("D43_protected")

    sys.addaudithook(guard)
    spec = read(Path("configs/research-f01-v1.json"))
    anchor = ((max(time.time_ns(), spec["inherited_holdout"]["end_ns"]) // DAY) + 2) * DAY
    binding = freeze(
        spec, root / "proposal", anchor_ns=anchor, source_root=root / "synthetic-source"
    )
    initialize(root / "proposal", root / "registry")
    references = [
        Capture.load(Path(f"work/alchemy-pilot-v2b-01/segment-00/captures/http5-{i:04d}"))
        for i in (1, 2, 3)
    ]
    source = root / "synthetic-source/windows/000"
    source.mkdir(parents=True)
    due = binding["windows"][0]["due_ns"]
    t = FixtureClock(due, references)
    write(
        source / "fixture-disclosure.json",
        dict(
            qualification="synthetic_transport_fixture",
            transformations="old reference responses with synthetic Clock/slot/receipt times",
            real_RPC=0,
            not_persistent_transport_benchmark=True,
        ),
    )
    write(
        source / "reservation.json",
        dict(
            index=0,
            due_ns=due,
            started_ns=due,
            reserved_rpc=180,
            plan_sha256=sha(root / "registry/contract/binding.json"),
        ),
    )
    create_campaign(
        source / "campaign",
        references,
        source_alias="calendar-alchemy",
        endpoint_origin=spec["source_semantics"]["campaign"]["endpoint_origin"],
        transport_profile="stdlib_persistent_v1",
        segments=2,
        rounds=30,
        interval_seconds=5,
        clock=t.clock,
    )
    client = ReadOnlyRPC(
        spec["source_semantics"]["campaign"]["endpoint_origin"],
        "calendar-alchemy",
        transport=t.transport,
        timeout=15,
        max_bytes=1000000,
        clock=t.clock,
    )
    # Explicit injection marker is retained above. No PersistentHTTPTransport/network is used.
    client.transport_profile = "stdlib_persistent_v1"
    for _ in range(2):
        state = resume_campaign(
            source / "campaign", client, clock=t.clock, monotonic=t.mono, sleep=t.sleep
        )
        assert state["segments"][-1]["report"]["complete"]
    assert t.calls == 180
    export_execution_targets(source / "campaign", source / "campaign-targets")
    end = t.clock()
    outcome = dict(
        index=0,
        status="completed",
        data_kind="synthetic_fixture",
        finished_ns=end,
        corpus_exit_code=0,
        execution_or_collection_exit_code=0,
        qa_exit_code=0,
    )
    write(source / "seal.json", dict(outcome=outcome, files=inventory(source)))
    ingest(root / "registry", 0, source=source, now_ns=end + 1)
    report = export_dossier(root / "registry", root / "dossier", now_ns=end + 2)
    raw_reproduction = reproduce_sources(root / "registry", root / "raw-reproduction")
    evidence = dict(
        qualification="synthetic_transport_fixture",
        fixture_calls=t.calls,
        real_RPC=0,
        D43_opened=False,
        holdout_opened=False,
        training_runs=0,
        raw_reproduction=raw_reproduction,
        dossier_reproduction=reproduce_dossier(root / "registry", root / "dossier"),
        numerical_design_passed=report["numeric_design_passed"],
        research_launch_authorized=False,
        immutable_source_seal_sha256=sha(source / "seal.json"),
    )
    write(root / "evidence.json", evidence)
    print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    main()
