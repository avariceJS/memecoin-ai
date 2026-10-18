"""Запуск одного замороженного D40 сегмента; credentials читаются без shell evaluation."""

from __future__ import annotations

import argparse
import json
import threading
from pathlib import Path

from memecoin_ai.forward import collect_forward, verify_forward
from memecoin_ai.hybrid import run_segment, verify_segment
from memecoin_ai.sources import Capture, ReadOnlyRPC, SourceError


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("run", "verify"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--version", type=int, choices=(1, 2), default=1)
    parser.add_argument("--segment", type=int, choices=(0, 1), default=0)
    parser.add_argument("--env-file", type=Path, default=Path(".env.local"))
    args = parser.parse_args()
    segment = args.root / f"segment-{args.segment:02d}"
    control = args.root / f"control-{args.segment:02d}"
    if args.action == "verify":
        print(json.dumps(verify_segment(segment)))
        print(json.dumps(verify_forward(control)))
        return
    plan_path = args.plan or Path(f"configs/alchemy-pilot-v{args.version}.json")
    plan = json.loads(plan_path.read_text())
    values = {}
    for line in args.env_file.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    for key in ("MEMECOIN_ALCHEMY_HTTP_URL", "MEMECOIN_ALCHEMY_WSS_URL"):
        if not values.get(key):
            raise SystemExit(f"Missing local setting: {key}; no network requests sent")
    if segment.exists() or control.exists():
        raise SystemExit("Existing outputs are immutable")
    if args.segment:
        verify_segment(args.root / "segment-00")
        if not json.loads((args.root / "segment-00/report.json").read_text())["complete"]:
            raise SystemExit("Stopped pilot cannot resume")
    stop = threading.Event()

    class Control(ReadOnlyRPC):
        def fetch(self, method, params):
            if stop.is_set():
                raise SourceError("paired experiment stopped")
            return super().fetch(method, params)

    control_result = {}

    def control_emit(row):
        if row.get("status") == "error":
            stop.set()
            print(json.dumps(row), flush=True)

    def collect_control():
        client = Control(
            plan["control_origin"],
            "solana-public-persistent",
            timeout=10,
            max_bytes=plan["max_http_response_bytes"],
            keep_alive=True,
        )
        try:
            refs = [Capture.load(Path(c["reference"])) for c in plan["cohort"]]
            control_result.update(
                collect_forward(
                    client,
                    refs,
                    control,
                    rounds=24,
                    interval_seconds=5,
                    emit=control_emit,
                )
            )
        finally:
            client.close()

    # Same wall window; unmodified existing collector is the public-provider control.
    thread = threading.Thread(target=collect_control)
    thread.start()
    try:
        result = run_segment(
            plan_path,
            segment,
            values["MEMECOIN_ALCHEMY_HTTP_URL"],
            values["MEMECOIN_ALCHEMY_WSS_URL"],
            should_stop=stop.is_set,
        )
        if not result["complete"]:
            stop.set()
    finally:
        thread.join()
    print(json.dumps({k: v for k, v in result.items() if k != "measurements"}))
    print(json.dumps(dict(control=control_result)))


if __name__ == "__main__":
    main()
