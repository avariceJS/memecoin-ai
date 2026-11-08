"""Build deterministic Python source and wheel distributions for this checkout."""

from __future__ import annotations

import argparse
import base64
import copy
import csv
import gzip
import hashlib
import os
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
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
                            if member.isfile():
                                normalized.mode = 0o755 if member.mode & 0o111 else 0o644
                            elif member.isdir():
                                normalized.mode = 0o755
                            elif member.issym():
                                normalized.mode = 0o777
                            else:
                                raise ValueError("sdist_unsupported_member_type")
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


def _canonicalize_wheel(path: Path, epoch: int) -> None:
    """Normalize Unix ZIP modes without rewriting compressed wheel payloads."""
    temporary = path.with_name(path.name + ".tmp")
    try:
        raw = path.read_bytes()
        if len(raw) > 64 * 1024 * 1024:
            raise ValueError("wheel_too_large_to_canonicalize")
        with zipfile.ZipFile(path, "r") as source:
            if source.comment:
                raise ValueError("wheel_archive_comment_unsupported")
            members = source.infolist()
            names = [member.filename for member in members]
            if len(names) != len(set(names)):
                raise ValueError("wheel_duplicate_member")
            record_names = [name for name in names if name.endswith(".dist-info/RECORD")]
            if len(record_names) != 1:
                raise ValueError("wheel_record_missing_or_ambiguous")
            _validate_wheel_record(source, record_names[0])
            timestamp = time.gmtime(epoch)
            expected_timestamp = (
                timestamp.tm_year,
                timestamp.tm_mon,
                timestamp.tm_mday,
                timestamp.tm_hour,
                timestamp.tm_min,
                timestamp.tm_sec // 2 * 2,
            )
            regular_names: set[str] = set()
            central_offset = source.start_dir
            patches: list[tuple[int, int]] = []
            for member in members:
                if member.date_time != expected_timestamp:
                    raise ValueError("wheel_timestamp_not_source_date_epoch")
                if member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise ValueError("wheel_compression_unsupported")
                if member.flag_bits & 0x1:
                    raise ValueError("wheel_encryption_unsupported")
                candidate = PurePosixPath(member.filename)
                if (
                    candidate.is_absolute()
                    or ".." in candidate.parts
                    or "\x00" in member.filename
                    or "\\" in member.filename
                ):
                    raise ValueError("wheel_unsafe_member_path")
                if member.filename.lower().endswith((".jws", ".p7s")):
                    raise ValueError("wheel_signature_sidecar_unsupported")
                if (
                    central_offset + 46 > len(raw)
                    or raw[central_offset : central_offset + 4] != b"PK\x01\x02"
                ):
                    raise ValueError("wheel_central_directory_invalid")
                name_length, extra_length, comment_length = struct.unpack_from(
                    "<HHH", raw, central_offset + 28
                )
                name_start = central_offset + 46
                entry_end = name_start + name_length + extra_length + comment_length
                if entry_end > len(raw):
                    raise ValueError("wheel_central_directory_truncated")
                raw_name = raw[name_start : name_start + name_length]
                encoding = "utf-8" if member.flag_bits & 0x800 else "cp437"
                if raw_name.decode(encoding) != member.filename:
                    raise ValueError("wheel_central_directory_name_mismatch")
                mode = member.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                is_directory = member.is_dir()
                if is_directory:
                    if file_type not in {0, stat.S_IFDIR}:
                        raise ValueError("wheel_unsupported_member_type")
                    normalized_mode = stat.S_IFDIR | 0o755
                else:
                    if file_type not in {0, stat.S_IFREG} or mode & 0o7000:
                        raise ValueError("wheel_unsupported_member_type")
                    executable = bool(mode & 0o111)
                    parts = candidate.parts
                    script_payload = any(
                        part.endswith(".data")
                        and index + 1 < len(parts)
                        and parts[index + 1] == "scripts"
                        for index, part in enumerate(parts)
                    )
                    if executable and not script_payload:
                        raise ValueError("wheel_ambiguous_executable_member")
                    normalized_mode = stat.S_IFREG | (0o755 if executable else 0o644)
                    regular_names.add(member.filename)
                external_attr = normalized_mode << 16
                if is_directory:
                    external_attr |= 0x10
                patches.append((central_offset + 38, external_attr))
                central_offset += 46 + name_length + extra_length + comment_length
            if raw[central_offset : central_offset + 4] != b"PK\x05\x06":
                raise ValueError("wheel_zip64_or_trailing_record_unsupported")
            if set(_read_wheel_record(source, record_names[0])) != regular_names:
                raise ValueError("wheel_record_member_set_mismatch")
        normalized = bytearray(raw)
        for attr_offset, external_attr in patches:
            struct.pack_into("<L", normalized, attr_offset, external_attr)
        with temporary.open("xb") as output:
            output.write(normalized)
            output.flush()
            os.fsync(output.fileno())
        with zipfile.ZipFile(temporary, "r") as verified:
            _validate_wheel_record(verified, record_names[0])
            if verified.testzip() is not None:
                raise ValueError("wheel_crc_mismatch_after_canonicalization")
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _read_wheel_record(archive: zipfile.ZipFile, record_name: str) -> dict[str, tuple[str, str]]:
    rows: dict[str, tuple[str, str]] = {}
    try:
        contents = archive.read(record_name).decode("utf-8")
        for row in csv.reader(contents.splitlines()):
            if len(row) != 3 or row[0] in rows:
                raise ValueError("wheel_record_invalid")
            rows[row[0]] = (row[1], row[2])
    except (OSError, UnicodeError, zipfile.BadZipFile) as exc:
        raise ValueError("wheel_record_invalid") from exc
    if rows.get(record_name) != ("", ""):
        raise ValueError("wheel_record_self_hash_invalid")
    return rows


def _validate_wheel_record(archive: zipfile.ZipFile, record_name: str) -> None:
    rows = _read_wheel_record(archive, record_name)
    for name, (digest, size) in rows.items():
        if name == record_name:
            continue
        try:
            contents = archive.read(name)
        except (KeyError, OSError, zipfile.BadZipFile) as exc:
            raise ValueError("wheel_record_member_missing") from exc
        expected = base64.urlsafe_b64encode(hashlib.sha256(contents).digest()).rstrip(b"=").decode()
        if digest != f"sha256={expected}" or size != str(len(contents)):
            raise ValueError("wheel_record_content_mismatch")


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
            elif artifact.suffix == ".whl":
                _canonicalize_wheel(artifact, epoch)
            artifact.chmod(0o644)
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
