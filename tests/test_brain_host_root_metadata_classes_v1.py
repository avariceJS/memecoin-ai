from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools import brain_host_root_metadata_classes_v1 as census


def _role(root: Path, *, exclusions: list[dict[str, str]] | None = None) -> dict[str, object]:
    return {
        "key": "fixture",
        "path": str(root.resolve()),
        "state": "present",
        "expected_kind": "directory",
        "metadata_only_exclusions": exclusions or [],
    }


def test_metadata_class_walk_is_repeatable_and_does_not_follow_symlinks(tmp_path: Path) -> None:
    root = tmp_path.resolve() / "root"
    root.mkdir()
    payload = root / "payload.bin"
    payload.write_bytes(b"opaque payload must not be read")
    (root / "internal-link").symlink_to(payload)
    (root / "external-link").symlink_to("/outside/not-present")

    result = census._walk_root(_role(root), os.stat(root).st_dev)

    assert result["entry_counts"] == {"directory": 1, "regular_file": 1, "symlink": 2}
    assert result["acl_counts"]["no_acl_returned"] == 4
    assert result["regular_file_lstat_bytes"] == len(b"opaque payload must not be read")
    assert result["hardlink_groups_with_unobserved_aliases"] == 0
    assert len(result["scan_sha256"]) == 64


def test_metadata_class_walk_reopens_the_declared_root_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve() / "root"
    root.mkdir()
    (root / "payload.bin").write_bytes(b"opaque")
    role = _role(root)
    original = census._xattr_names_fd
    replaced = False

    def replace_after_open(fd: int) -> list[bytes]:
        nonlocal replaced
        if not replaced and os.fstat(fd).st_ino == os.stat(root).st_ino:
            root.rename(root.with_name("root-moved"))
            root.mkdir()
            replaced = True
        return original(fd)

    monkeypatch.setattr(census, "_xattr_names_fd", replace_after_open)

    with pytest.raises(census.MetadataCensusError, match="root_path_endpoint_changed"):
        census._walk_root(role, os.stat(root).st_dev)


def test_metadata_class_file_root_reopens_its_parent_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path.resolve() / "parent"
    parent.mkdir()
    path = parent / "config.json"
    path.write_bytes(b"opaque")
    role = {
        "key": "fixture_file",
        "path": str(path),
        "state": "present",
        "expected_kind": "file",
        "metadata_only_exclusions": [],
    }
    original = census._xattr_names_fd
    replaced = False

    def replace_parent_after_open(fd: int) -> list[bytes]:
        nonlocal replaced
        if not replaced:
            parent.rename(parent.with_name("parent-moved"))
            parent.mkdir()
            (parent / path.name).write_bytes(b"replacement")
            replaced = True
        return original(fd)

    monkeypatch.setattr(census, "_xattr_names_fd", replace_parent_after_open)

    with pytest.raises(census.MetadataCensusError, match="file_root_parent_endpoint_changed"):
        census._walk_root(role, os.stat(parent).st_dev)


def test_metadata_class_walk_marks_unseen_hardlink_aliases(tmp_path: Path) -> None:
    root = tmp_path.resolve() / "root"
    root.mkdir()
    inside = root / "inside.bin"
    inside.write_bytes(b"opaque")
    os.link(inside, tmp_path.resolve() / "outside-alias.bin")

    result = census._walk_root(_role(root), os.stat(root).st_dev)

    assert result["hardlink_groups_with_unobserved_aliases"] == 1


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin O_SYMLINK xattr contract")
def test_symlink_fd_xattrs_are_for_the_link_not_its_target(tmp_path: Path) -> None:
    root = tmp_path.resolve() / "root"
    root.mkdir()
    target = tmp_path.resolve() / "target"
    target.write_bytes(b"payload")
    link = root / "link"
    link.symlink_to(Path("..") / target.name)
    assert link.resolve() == target
    _darwin_setxattr(target, "com.example.brain-target", b"target", nofollow=False)
    _darwin_setxattr(link, "com.example.brain-link", b"link", nofollow=True)

    result = census._walk_root(_role(root), os.stat(root).st_dev)

    assert result["xattr_name_counts"].get("com.example.brain-link") == 1
    assert "com.example.brain-target" not in result["xattr_name_counts"]


def _darwin_setxattr(path: Path, name: str, value: bytes, *, nofollow: bool) -> None:
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    function = libc.setxattr
    function.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_uint32,
        ctypes.c_int,
    ]
    function.restype = ctypes.c_int
    payload = ctypes.create_string_buffer(value)
    result = function(
        os.fsencode(path),
        name.encode(),
        payload,
        len(value),
        0,
        0x0001 if nofollow else 0,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(path))


def test_metadata_class_walk_skips_opaque_and_credential_entries(tmp_path: Path) -> None:
    root = tmp_path.resolve() / "root"
    root.mkdir()
    wallets = root / "wallets"
    wallets.mkdir()
    (wallets / "opaque.bin").write_bytes(b"not opened")
    (root / ".env.local").write_bytes(b"not opened")
    (root / "included.bin").write_bytes(b"included only as metadata")

    result = census._walk_root(_role(root), os.stat(root).st_dev)

    assert result["entry_counts"] == {"directory": 1, "regular_file": 1}
    assert result["skipped_counts"] == {
        "credential_reissue_lstat_only": 1,
        "opaque_backup_required_lstat_only": 1,
    }


def test_direct_cli_from_neutral_cwd_returns_a_named_input_failure(tmp_path: Path) -> None:
    script = Path(census.__file__).resolve()
    result = subprocess.run(
        [sys.executable, str(script), "--roster", "/dev/null"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stderr == ""
    assert '"error": "roster_file_identity_invalid"' in result.stdout
