from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from memecoin_research_storage import StorageError
from tools.brain_senses_sparse_observation_v1 import _open_data_root_descriptor


def _guard(mount_fd: int) -> SimpleNamespace:
    return SimpleNamespace(
        mount_fd=mount_fd,
        config=SimpleNamespace(data_root="BrainData"),
    )


def test_opens_private_data_root_beneath_non_private_mountpoint(tmp_path) -> None:
    mount = tmp_path / "mount"
    root = mount / "BrainData"
    mount.mkdir(mode=0o755)
    root.mkdir(mode=0o700)
    os.chmod(mount, 0o755)
    os.chmod(root, 0o700)
    mount_fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
    try:
        root_fd = _open_data_root_descriptor(_guard(mount_fd))
        try:
            assert os.fstat(root_fd).st_ino == root.stat().st_ino
            assert os.fstat(root_fd).st_mode & 0o777 == 0o700
        finally:
            os.close(root_fd)
    finally:
        os.close(mount_fd)


def test_rejects_symlink_data_root_without_following_it(tmp_path) -> None:
    mount = tmp_path / "mount"
    external = tmp_path / "external"
    mount.mkdir()
    external.mkdir(mode=0o700)
    (mount / "BrainData").symlink_to(external, target_is_directory=True)
    mount_fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(OSError):
            _open_data_root_descriptor(_guard(mount_fd))
        assert list(external.iterdir()) == []
    finally:
        os.close(mount_fd)


def test_rejects_non_private_data_root(tmp_path) -> None:
    mount = tmp_path / "mount"
    root = mount / "BrainData"
    mount.mkdir()
    root.mkdir(mode=0o755)
    os.chmod(root, 0o755)
    mount_fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(StorageError, match="sparse_storage_root_identity"):
            _open_data_root_descriptor(_guard(mount_fd))
    finally:
        os.close(mount_fd)
