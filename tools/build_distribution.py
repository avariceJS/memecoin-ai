"""Build deterministic Python source and wheel distributions for this checkout."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_MIN_ZIP_EPOCH = 315532800  # 1980-01-01; the earliest timestamp supported by ZIP.
_MAX_ZIP_EPOCH = 4354819199  # 2107-12-31; the latest timestamp supported by ZIP.


def _parse_epoch(raw: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("source_date_epoch_invalid") from exc
    if not _MIN_ZIP_EPOCH <= value <= _MAX_ZIP_EPOCH:
        raise ValueError("source_date_epoch_out_of_zip_range")
    return value


def source_date_epoch(root: Path, env: dict[str, str] | None = None) -> int:
    """Use an explicit epoch or the checked-out commit time; archives must pin one."""
    env = os.environ if env is None else env
    if "SOURCE_DATE_EPOCH" in env:
        return _parse_epoch(env["SOURCE_DATE_EPOCH"])
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%ct"],
            capture_output=True,
            check=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("source_date_epoch_required_outside_git") from exc
    return _parse_epoch(result.stdout.strip())


def build(root: Path, outdir: Path, env: dict[str, str] | None = None) -> int:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("source_root_not_directory")
    epoch = source_date_epoch(root, env)
    build_env = dict(os.environ if env is None else env)
    build_env["SOURCE_DATE_EPOCH"] = str(epoch)
    outdir = outdir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"source_date_epoch={epoch}")
    return subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(outdir), str(root)],
        cwd=root,
        env=build_env,
        check=False,
    ).returncode


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", type=Path, default=root / "dist")
    args = parser.parse_args(argv)
    try:
        return build(root, args.outdir)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
