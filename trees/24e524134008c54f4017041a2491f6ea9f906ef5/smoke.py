from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    def cli(*arguments: object) -> dict[str, object]:
        output = subprocess.check_output(
            [sys.executable, "-m", "memecoin_ai", *map(str, arguments)], cwd=ROOT, text=True
        )
        return dict(json.loads(output))

    with tempfile.TemporaryDirectory(prefix="memecoin-smoke-") as directory:
        root = Path(directory)
        bundle = root / "bundle"
        cli("fixture", "--out", bundle)
        cli("audit", bundle)
        store = root / "events.sqlite"
        cli("ingest", bundle / "events.jsonl", "--store", store)
        assert cli("ingest", bundle / "events.jsonl", "--store", store)["inserted"] == 0
        exported = root / "exported"
        cli("export", "--store", store, "--out", exported)
        for name in ("first", "second"):
            metrics = cli(
                "run", exported, "--config", ROOT / "configs/synthetic.toml", "--out", root / name
            )
            assert metrics["edge_established"] is False
            cli("verify-run", root / name)
        assert (root / "first/result.json").read_bytes() == (
            root / "second/result.json"
        ).read_bytes()
        print(json.dumps({"smoke": "passed", "reproducible": True, "metrics": metrics}, indent=2))


if __name__ == "__main__":
    main()
