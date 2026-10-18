"""Full collector→D48 admission from explicit synthetic transport; zero real RPC."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from f01_sampling_smoke import FixtureClock

from memecoin_ai.sources import Capture, ReadOnlyRPC
from memecoin_collector.scheduler import run_due
from memecoin_collector.window import collect_window
from memecoin_ml.data import read, sha, write
from memecoin_research.framework import DAY
from memecoin_sampling.contract import freeze
from memecoin_sampling.dossier import export_dossier, reproduce_dossier
from memecoin_sampling.registry import ingest, initialize, reproduce_sources


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    protected = Path("work/calendar-execution-01").resolve()

    def guard(event, args):
        if event == "socket.connect":
            raise PermissionError("D49_fixture_network_forbidden")
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            if Path(os.fsdecode(args[0])).resolve().is_relative_to(protected):
                raise PermissionError("D43_protected")

    sys.addaudithook(guard)
    spec = read(Path("configs/research-f01-v1.json"))
    anchor = (max(time.time_ns(), spec["inherited_holdout"]["end_ns"]) // DAY + 2) * DAY
    binding = freeze(spec, root / "proposal", anchor_ns=anchor, source_root=root / "source")
    initialize(root / "proposal", root / "registry")
    refs = [
        Capture.load(Path(p))
        for p in read(Path("configs/qualified-http-corpus-v1.json"))["references"]
    ]
    clock = FixtureClock(binding["windows"][0]["due_ns"], refs)
    client = ReadOnlyRPC(
        spec["source_semantics"]["campaign"]["endpoint_origin"],
        "calendar-alchemy",
        transport=clock.transport,
        timeout=15,
        max_bytes=1000000,
        clock=clock.clock,
    )
    client.transport_profile = "stdlib_persistent_v1"

    def runner(window, reservation):
        write(
            window / "fixture-disclosure.json",
            dict(
                data_kind="synthetic_fixture",
                real_rpc=0,
                transformations="old public D40 bytes; synthetic Clock/slot/receipt",
            ),
        )
        return collect_window(
            window,
            refs,
            client,
            data_kind="synthetic_fixture",
            clock=clock.clock,
            monotonic=clock.mono,
            sleep=clock.sleep,
        )

    started = time.monotonic()
    outcomes = run_due(
        root / "source",
        binding,
        sha(root / "proposal/binding.json"),
        now=clock.clock,
        runner=runner,
    )
    assert len(outcomes) == 1 and outcomes[0]["status"] == "completed" and clock.calls == 180
    ingest(root / "registry", 0, source=root / "source/windows/000", now_ns=clock.clock())
    export_dossier(root / "registry", root / "dossier", now_ns=clock.clock())
    raw = reproduce_sources(root / "registry", root / "raw-reproduction")
    evidence = dict(
        qualification="synthetic_fixture_only",
        real_rpc=0,
        fake_calls=clock.calls,
        source_bytes=sum(p.stat().st_size for p in (root / "source").rglob("*") if p.is_file()),
        collector_and_conversion_seconds=time.monotonic() - started,
        raw_reproduction=raw,
        dossier_reproduction=reproduce_dossier(root / "registry", root / "dossier"),
        D43_opened=False,
        holdout_opened=False,
        training=0,
    )
    write(root / "evidence.json", evidence)
    print(json.dumps(evidence))


if __name__ == "__main__":
    main()
