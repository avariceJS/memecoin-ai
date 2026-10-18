"""Build deterministic Python source and wheel distributions for this checkout."""

from __future__ import annotations

import argparse
import copy
import gzip
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

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


def _canonicalize_sdist(path: Path, epoch: int) -> None:
    """Normalize source archive metadata while preserving source bytes and modes."""
    temporary = path.with_name(path.name + ".tmp")
    try:
        with tarfile.open(path, "r:gz") as source:
            members = source.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)):
                raise ValueError("sdist_duplicate_member")
            for name in names:
                candidate = PurePosixPath(name)
                if candidate.is_absolute() or ".." in candidate.parts or "\x00" in name:
                    raise ValueError("sdist_unsafe_member_path")
            with temporary.open("wb") as raw:
                with gzip.GzipFile(
                    filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=epoch
                ) as compressed:
                    with tarfile.open(
                        fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT
                    ) as target:
                        for member in sorted(members, key=lambda item: item.name):
                            normalized = copy.copy(member)
                            normalized.mtime = epoch
                            normalized.uid = 0
                            normalized.gid = 0
                            normalized.uname = ""
                            normalized.gname = ""
                            normalized.pax_headers = {}
                            content = source.extractfile(member) if member.isfile() else None
                            target.addfile(normalized, content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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
    with tempfile.TemporaryDirectory(prefix=".build-", dir=outdir) as temporary:
        staging = Path(temporary)
        result = subprocess.run(
            [sys.executable, "-m", "build", "--outdir", str(staging), str(root)],
            cwd=root,
            env=build_env,
            check=False,
        )
        if result.returncode:
            return result.returncode
        artifacts = sorted(
            path
            for path in staging.iterdir()
            if path.suffix == ".whl" or path.name.endswith(".tar.gz")
        )
        if not artifacts:
            raise ValueError("build_produced_no_distributions")
        for artifact in artifacts:
            if artifact.name.endswith(".tar.gz"):
                _canonicalize_sdist(artifact, epoch)
        for artifact in artifacts:
            os.replace(artifact, outdir / artifact.name)
    return 0


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
