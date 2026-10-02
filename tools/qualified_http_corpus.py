"""D41: use existing prospective/target pipeline with an explicit bounded source decision."""

import argparse
import json
import subprocess
import time
from pathlib import Path

from memecoin_ai.corpus_quality import export_quality, verify_quality
from memecoin_ai.coverage import export_support, verify_support
from memecoin_ai.execution_targets import export_execution_targets, verify_execution_targets
from memecoin_ai.prospective import create_campaign, read, resume_campaign, verify_campaign, write
from memecoin_ai.sources import Capture, ReadOnlyRPC


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["create", "resume", "report", "verify"])
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root
    if args.action == "create":
        spec = read(Path("configs/qualified-http-corpus-v1.json"))
        refs = [Capture.load(Path(p)) for p in spec["references"]]
        create_campaign(
            root,
            refs,
            source_alias=spec["source_alias"],
            endpoint_origin=spec["endpoint_origin"],
            transport_profile="stdlib_persistent_v1",
            segments=spec["segments"],
            rounds=spec["rounds"],
            interval_seconds=spec["interval_seconds"],
        )
        write(root / "stage-plan.json", spec)
        write(
            root / "stage-provenance.json",
            dict(
                code_commit=subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], text=True
                ).strip(),
                seed=None,
                reason="D40-v2 source/transport decision",
            ),
        )
        return
    if args.action == "resume":
        spec = read(root / "stage-plan.json")
        values = {}
        for line in Path(".env.local").read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
        key = spec["endpoint_env"]
        if not values.get(key):
            raise SystemExit(f"Missing local setting: {key}; no requests sent")
        plan = read(root / "plan.json")
        client = ReadOnlyRPC(
            values[key],
            plan["source_alias"],
            timeout=plan["timeout_seconds"],
            max_bytes=plan["max_response_bytes"],
            keep_alive=True,
        )
        try:
            state = resume_campaign(
                root,
                client,
                emit=lambda row: (
                    print(json.dumps(row), flush=True) if row.get("status") == "error" else None
                ),
            )
        finally:
            client.close()
        if not state["segments"][-1]["report"]["complete"]:
            write(
                root / "closure.json",
                dict(reason="D41 stops at first failure; no retry", time_ns=time.time_ns()),
            )
        print(
            json.dumps(
                dict(
                    complete=state["complete"],
                    snapshots=state["successful_snapshots"],
                    segments=len(state["segments"]),
                )
            )
        )
        return
    outputs = {
        name: root.parent / (root.name + "-" + name) for name in ["targets", "quality", "support"]
    }
    if args.action == "report":
        export_execution_targets(root, outputs["targets"])
        export_quality(root, outputs["targets"], outputs["quality"])
        export_support(root, outputs["targets"], outputs["support"])
    state = verify_campaign(root)
    result = dict(
        campaign=dict(complete=state["complete"], snapshots=state["successful_snapshots"]),
        targets=verify_execution_targets(root, outputs["targets"]),
        quality=verify_quality(root, outputs["targets"], outputs["quality"]),
        support=verify_support(root, outputs["targets"], outputs["support"]),
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
