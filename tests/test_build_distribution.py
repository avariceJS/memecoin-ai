from __future__ import annotations

import base64
import csv
import hashlib
import io
import stat
import subprocess
import tarfile
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.build_distribution import _canonicalize_sdist, _canonicalize_wheel, source_date_epoch


def test_source_date_epoch_uses_explicit_environment_value(tmp_path: Path) -> None:
    assert source_date_epoch(tmp_path, {"SOURCE_DATE_EPOCH": "1760745600"}) == 1760745600


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("not-a-number", "source_date_epoch_invalid"),
        ("0", "source_date_epoch_out_of_zip_range"),
        ("4354819200", "source_date_epoch_out_of_zip_range"),
    ],
)
def test_source_date_epoch_rejects_invalid_values(tmp_path: Path, raw: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        source_date_epoch(tmp_path, {"SOURCE_DATE_EPOCH": raw})


def test_source_date_epoch_uses_commit_timestamp_when_unset(tmp_path: Path) -> None:
    result = subprocess.CompletedProcess([], 0, "1760745600\n", "")
    with patch("tools.build_distribution.subprocess.run", return_value=result) as run:
        assert source_date_epoch(tmp_path, {}) == 1760745600
    assert run.call_args.args[0] == [
        "git",
        "-C",
        str(tmp_path),
        "log",
        "-1",
        "--format=%ct",
    ]


def test_source_date_epoch_requires_pin_for_source_archives(tmp_path: Path) -> None:
    with patch(
        "tools.build_distribution.subprocess.run",
        side_effect=FileNotFoundError,
    ):
        with pytest.raises(ValueError, match="source_date_epoch_required_outside_git"):
            source_date_epoch(tmp_path, {})


def test_canonicalize_sdist_normalizes_metadata_and_preserves_content(tmp_path: Path) -> None:
    paths = [tmp_path / "first.tar.gz", tmp_path / "second.tar.gz"]
    for index, path in enumerate(paths):
        with tarfile.open(path, "w:gz") as archive:
            info = tarfile.TarInfo("sample-1.0/source.txt")
            info.size = 6
            info.mtime = 1_700_000_000 + index
            info.uid = index + 1
            info.gid = index + 2
            info.mode = (0o664, 0o644)[index]
            archive.addfile(info, BytesIO(b"source"))
            script = tarfile.TarInfo("sample-1.0/run.sh")
            script.size = 5
            script.mode = (0o775, 0o755)[index]
            archive.addfile(script, BytesIO(b"#!/bin"))
            directory = tarfile.TarInfo("sample-1.0/bin")
            directory.type = tarfile.DIRTYPE
            directory.mode = (0o775, 0o755)[index]
            archive.addfile(directory)
            link = tarfile.TarInfo("sample-1.0/source-link")
            link.type = tarfile.SYMTYPE
            link.linkname = "source.txt"
            link.mode = (0o775, 0o755)[index]
            archive.addfile(link)
    for path in paths:
        _canonicalize_sdist(path, 1760745600)
    assert paths[0].read_bytes() == paths[1].read_bytes()
    with tarfile.open(paths[0], "r:gz") as archive:
        member = archive.getmember("sample-1.0/source.txt")
        assert member.mtime == 1760745600
        assert member.uid == 0
        assert member.gid == 0
        assert member.mode == 0o644
        assert archive.extractfile(member).read() == b"source"
        assert archive.getmember("sample-1.0/run.sh").mode == 0o755
        assert archive.getmember("sample-1.0/bin").mode == 0o755
        assert archive.getmember("sample-1.0/source-link").mode == 0o777
        assert archive.getmember("sample-1.0/source-link").linkname == "source.txt"


def test_canonicalize_sdist_rejects_hardlink_member(tmp_path: Path) -> None:
    path = tmp_path / "hardlink.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("sample-1.0/alias")
        info.type = tarfile.LNKTYPE
        info.linkname = "source.txt"
        archive.addfile(info)
    original = path.read_bytes()
    with pytest.raises(ValueError, match="sdist_unsupported_member_type"):
        _canonicalize_sdist(path, 1760745600)
    assert path.read_bytes() == original


def test_canonicalize_sdist_rejects_path_traversal(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("../outside.txt")
        info.size = 0
        archive.addfile(info)
    original = path.read_bytes()
    with pytest.raises(ValueError, match="sdist_unsafe_member_path"):
        _canonicalize_sdist(path, 1760745600)
    assert path.read_bytes() == original


def test_canonicalize_wheel_normalizes_zip_metadata_and_preserves_contents(tmp_path: Path) -> None:
    paths = [tmp_path / "first.whl", tmp_path / "second.whl"]
    payloads = {
        "sample/__init__.py": b"VALUE = 1\n",
        "sample-1.0.dist-info/METADATA": b"Name: sample\n",
    }
    rows = []
    for name, contents in payloads.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(contents).digest()).rstrip(b"=").decode()
        rows.append((name, f"sha256={digest}", str(len(contents))))
    rows.append(("sample-1.0.dist-info/RECORD", "", ""))
    record = io.StringIO(newline="")
    csv.writer(record, lineterminator="\n").writerows(rows)
    for index, path in enumerate(paths):
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, contents in payloads.items():
                info = zipfile.ZipInfo(name, date_time=(2025, 10, 18, 0, 0, 0))
                info.create_system = 3
                mode = (0o664, 0o644)[index] if name == "sample/__init__.py" else 0o644
                info.external_attr = (stat.S_IFREG | mode) << 16
                archive.writestr(info, contents)
            record_info = zipfile.ZipInfo(
                "sample-1.0.dist-info/RECORD", date_time=(2025, 10, 18, 0, 0, 0)
            )
            record_info.create_system = 3
            record_info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(record_info, record.getvalue().encode())

    for path in paths:
        _canonicalize_wheel(path, 1760745600)

    assert paths[0].read_bytes() == paths[1].read_bytes()
    with zipfile.ZipFile(paths[0]) as archive:
        member = archive.getinfo("sample/__init__.py")
        assert member.date_time == (2025, 10, 18, 0, 0, 0)
        assert member.external_attr >> 16 == stat.S_IFREG | 0o644
        assert archive.read(member) == b"VALUE = 1\n"
        assert archive.read("sample-1.0.dist-info/RECORD") == record.getvalue().encode()


def test_canonicalize_wheel_rejects_unsafe_or_special_members(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.whl"
    record_name = "sample-1.0.dist-info/RECORD"
    unsafe = "../outside.py"
    unsafe_content = b"unsafe"
    unsafe_digest = (
        base64.urlsafe_b64encode(hashlib.sha256(unsafe_content).digest()).rstrip(b"=").decode()
    )
    record = io.StringIO(newline="")
    csv.writer(record, lineterminator="\n").writerows(
        [(unsafe, f"sha256={unsafe_digest}", str(len(unsafe_content))), (record_name, "", "")]
    )
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo(unsafe, date_time=(2025, 10, 18, 0, 0, 0))
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        archive.writestr(info, unsafe_content)
        record_info = zipfile.ZipInfo(record_name, date_time=(2025, 10, 18, 0, 0, 0))
        record_info.create_system = 3
        record_info.external_attr = (stat.S_IFREG | 0o644) << 16
        archive.writestr(record_info, record.getvalue().encode())
    original = path.read_bytes()
    with pytest.raises(ValueError, match="wheel_unsafe_member_path"):
        _canonicalize_wheel(path, 1760745600)
    assert path.read_bytes() == original

    symlink = "sample/link"
    symlink_content = b"target"
    symlink_digest = (
        base64.urlsafe_b64encode(hashlib.sha256(symlink_content).digest()).rstrip(b"=").decode()
    )
    record = io.StringIO(newline="")
    csv.writer(record, lineterminator="\n").writerows(
        [(symlink, f"sha256={symlink_digest}", str(len(symlink_content))), (record_name, "", "")]
    )
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo(symlink, date_time=(2025, 10, 18, 0, 0, 0))
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, symlink_content)
        record_info = zipfile.ZipInfo(record_name, date_time=(2025, 10, 18, 0, 0, 0))
        record_info.create_system = 3
        record_info.external_attr = (stat.S_IFREG | 0o644) << 16
        archive.writestr(record_info, record.getvalue().encode())
    original = path.read_bytes()
    with pytest.raises(ValueError, match="wheel_unsupported_member_type"):
        _canonicalize_wheel(path, 1760745600)
    assert path.read_bytes() == original
