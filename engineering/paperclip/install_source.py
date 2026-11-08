"""Reconstruct the exact admitted Paperclip source from upstream and its custom delta.

This installs source only. It never reads runtime state or starts Paperclip.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path

from engineering.paperclip_lab_server import LabPaths, _validate_source


def install_source(destination: Path, *, upstream: str | None = None) -> str:
    package = Path(__file__).resolve().parent
    pin = json.loads((package / "source-pin.json").read_text(encoding="utf-8"))
    source = pin["reproducible_source"]
    delta = (package / source["file"]).read_bytes()
    if hashlib.sha256(delta).hexdigest() != source["sha256"]:
        raise ValueError("paperclip_source_delta_hash_mismatch")
    payload = gzip.decompress(delta)
    destination = destination.expanduser().absolute()
    # Refuse to overwrite or reuse another checkout, including a symlink.
    destination.mkdir(parents=True, exist_ok=False)

    def git(*args: str, data: bytes | None = None) -> str:
        result = subprocess.run(
            ["git", "-C", str(destination), *args], input=data,
            capture_output=True, check=True,
        )
        return result.stdout.decode().strip()

    git("init", "--quiet")
    git("remote", "add", "origin", upstream or pin["upstream_repository"])
    git("fetch", "--quiet", "--depth=1", "--no-tags", "origin", source["upstream_commit"])
    git("fast-import", "--quiet", data=payload)
    git("checkout", "--quiet", source["branch"])
    if git("rev-parse", "HEAD^{tree}") != source["tree"]:
        raise ValueError("paperclip_source_tree_mismatch")
    custom_count = git("rev-list", "--count", source["upstream_commit"] + "..HEAD")
    if custom_count != str(source["custom_commit_count"]):
        raise ValueError("paperclip_source_commit_count_mismatch")
    # Reuse the launcher's unchanged exact HEAD/parent-chain and clean-tree admission.
    admitted = _validate_source(replace(LabPaths.resolve(destination.parent), source=destination))
    git("fsck", "--full", "--no-reflogs")
    return admitted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination", type=Path,
        default=Path.home() / "Developer/memecoin-runtime/paperclip",
    )
    parser.add_argument(
        "--upstream", help="Optional mirror; upstream and custom commits remain pinned",
    )
    args = parser.parse_args()
    print(install_source(args.destination, upstream=args.upstream))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
