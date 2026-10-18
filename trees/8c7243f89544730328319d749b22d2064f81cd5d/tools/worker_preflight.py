#!/usr/bin/env python3
"""Read-only worker inventory; no model inference, provider calls or service changes.

Standalone stdlib script: it can run before this project is cloned onto Mac 2.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def command(args: list[str], cwd: Path | None = None) -> str | None:
    try:
        result = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("local_metadata_redirect_rejected")


def ollama_metadata(endpoint: str) -> dict:
    if endpoint not in ("tags", "ps"):
        raise ValueError("metadata_only")
    # Never inherit HTTP proxies or follow redirects to another machine/provider.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(f"http://127.0.0.1:11434/api/{endpoint}", timeout=2) as response:
            payload = response.read(1_048_577)
        if len(payload) > 1_048_576:
            raise ValueError("metadata_too_large")
        rows = json.loads(payload)["models"]
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("metadata_shape")
        models = []
        for row in rows:
            details = row.get("details", {})
            models.append(
                {
                    key: row.get(key)
                    for key in ("name", "digest", "size", "size_vram", "context_length")
                }
                | {
                    "parameter_size": details.get("parameter_size"),
                    "quantization_level": details.get("quantization_level"),
                }
            )
        return {"available": True, "models": models}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        # Do not copy arbitrary server/error response text into receipts.
        return {"available": False, "error_class": type(exc).__name__, "models": []}


def inventory(repo: Path, node_id: str) -> dict:
    repo = repo.resolve()
    head = command(["git", "rev-parse", "HEAD"], repo)
    dirty = command(["git", "status", "--porcelain"], repo) if head else None
    memory = command(["sysctl", "-n", "hw.memsize"])
    cursor = shutil.which("agent") or shutil.which("cursor-agent")
    if cursor is None:
        candidate = Path.home() / ".local/bin/agent"
        if candidate.is_file():
            cursor = str(candidate)
    usage = shutil.disk_usage(repo if repo.exists() else Path.cwd())
    return {
        "schema": "memecoin-worker-preflight-v1",
        "observed_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 (standalone Python 3.9)
        "node_id": node_id,
        "host": {
            "os": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
            "cpu": command(["sysctl", "-n", "machdep.cpu.brand_string"]),
            "memory_bytes": int(memory) if memory and memory.isdigit() else None,
            "swap_observation": command(["sysctl", "-n", "vm.swapusage"]),
            "disk_free_bytes": usage.free,
            "python_version": platform.python_version(),
        },
        "repo": {"head": head, "clean": dirty == "" if dirty is not None else None},
        "cursor": {
            "installed": cursor is not None,
            "version": command([cursor, "--version"]) if cursor else None,
            "model_catalog_verified": False,
        },
        "ollama_catalog": ollama_metadata("tags"),
        "ollama_loaded": ollama_metadata("ps"),
        "provider_credentials_inspected": False,
        "provider_requests": 0,
        "inference_requests": 0,
        "ready_for_live_or_training": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--node-id", choices=("mac1", "mac2", "unassigned"), default="unassigned")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    text = json.dumps(inventory(args.repo, args.node_id), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(text)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
