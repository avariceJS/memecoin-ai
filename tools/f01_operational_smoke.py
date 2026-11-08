"""Four real child processes, synthetic transport only, full D48 admission/replay."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from memecoin_collector.scheduler import run_due
from memecoin_ml.data import read, sha, write
from memecoin_operational.contract import stage
from memecoin_operational.supervisor import window
from memecoin_research.framework import DAY
from memecoin_sampling.contract import freeze
from memecoin_sampling.dossier import export_dossier, reproduce_dossier
from memecoin_sampling.registry import ingest, initialize, reproduce_sources


def _audit_guard(protected: Path):
    protected = protected.resolve()

    def audit(event, args):
        if event == "socket.connect":
            raise PermissionError("D50_network_forbidden")
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            if Path(os.fsdecode(args[0])).resolve().is_relative_to(protected):
                raise PermissionError("D43_protected")

    return audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    protected = Path.home() / "Developer/memecoin-runtime/brain/owner-work/calendar-execution-01"
    sys.addaudithook(_audit_guard(protected))
    spec = read(Path("configs/research-f01-v1.json"))
    anchor = (max(time.time_ns(), spec["inherited_holdout"]["end_ns"]) // DAY + 2) * DAY
    b = freeze(spec, root / "proposal", anchor_ns=anchor, source_root=root / "source")
    initialize(root / "proposal", root / "registry")
    refs = [Path(p) for p in read(Path("configs/qualified-http-corpus-v1.json"))["references"]]
    stage(root / "proposal", root / "bundle", refs, mode="synthetic_fixture")
    pin = sha(root / "bundle/manifest.json")
    due = b["windows"][0]["due_ns"]
    current = [due]
    started = time.monotonic()

    def runner(path, res):
        result = window(root / "bundle", pin, path, res)
        current[0] = result["finished_ns"]
        return result

    rows = run_due(
        root / "source",
        b,
        sha(root / "proposal/binding.json"),
        now=lambda: current[0],
        runner=runner,
    )
    assert len(rows) == 1 and rows[0]["status"] == "completed", rows
    source = root / "source/windows/000"
    ingest(root / "registry", 0, source=source, now_ns=current[0] + 1)
    export_dossier(root / "registry", root / "dossier", now_ns=current[0] + 2)
    evidence = dict(
        mode="synthetic_fixture",
        real_rpc=0,
        fake_rpc=sum(
            read(source / f"{phase}-result.json")["fake_rpc"]
            for phase in ("init", "segment0", "segment1", "qa")
        ),
        training=0,
        supervision=read(source / "supervision.json"),
        raw_reproduction=reproduce_sources(root / "registry", root / "raw-reproduction"),
        dossier_reproduction=reproduce_dossier(root / "registry", root / "dossier"),
        total_wall_seconds=time.monotonic() - started,
        bundle_sha=pin,
        D43_opened=False,
        holdout_opened=False,
    )
    write(root / "evidence.json", evidence)
    print(json.dumps(evidence))


if __name__ == "__main__":
    main()
