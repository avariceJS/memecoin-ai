from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, '/home/brain/lab/clean-d12-source')
import memecoin_research_storage as storage
from memecoin_research_storage import StorageConfig, StorageError, configure, create_root, writer_guard
from memecoin_research_storage import _fd_mount_id, _open_relative_directory
from memecoin_research_storage.live_writer import revalidate_descriptor_write
from tools.brain_senses_external_writer_v1 import DescriptorWriter

MOUNT = Path('/mnt/brain-storage-detach-qual')
IMAGE = Path('/var/tmp/brain-storage-detach-qual.ext4')
CONFIG = Path('/home/brain/.config/brain-storage-detach/config.json')
UNDERLAY = Path('/home/brain/lab/underlay-detach-qual.json')
OUT = Path('/home/brain/lab/linux-ext4-writer-detach-qual.json')


def run_sudo(*args: str) -> None:
    subprocess.run(['sudo', '-n', *args], check=True, timeout=30, capture_output=True, text=True)


def inventory(path: Path) -> list[dict[str, object]]:
    root_stat = path.lstat()
    result: list[dict[str, object]] = [{
        'path': '.',
        'mode': root_stat.st_mode & 0o7777,
        'uid': root_stat.st_uid,
        'gid': root_stat.st_gid,
        'size': root_stat.st_size,
        'kind': 'dir',
    }]
    for base, dirs, files in os.walk(path, topdown=True, followlinks=False):
        dirs.sort()
        files.sort()
        for name in [*dirs, *files]:
            item = Path(base) / name
            st = item.lstat()
            row: dict[str, object] = {
                'path': str(item.relative_to(path)),
                'mode': st.st_mode & 0o7777,
                'uid': st.st_uid,
                'gid': st.st_gid,
                'size': st.st_size,
                'kind': 'dir' if item.is_dir() and not item.is_symlink() else 'file',
            }
            if item.is_file() and not item.is_symlink():
                row['sha256'] = hashlib.sha256(item.read_bytes()).hexdigest()
            result.append(row)
    return result


def require_underlay(expected: list[dict[str, object]], case: str) -> None:
    got = inventory(MOUNT)
    if got != expected:
        raise AssertionError(f'underlay_changed:{case}:{got!r}')


def remount() -> None:
    if not os.path.ismount(MOUNT):
        run_sudo('mount', '-o', 'loop,nodev,nosuid,noexec', str(IMAGE), str(MOUNT))
    if not os.path.ismount(MOUNT):
        raise AssertionError('remount_not_mounted')
    last_error: Exception | None = None
    for _ in range(25):
        try:
            info = storage.LinuxExt4VolumeProbe().inspect(MOUNT)
            print(f'remounted source={info.source} uuid={info.volume_uuid} mount_id={info.mount_id}', flush=True)
            return
        except StorageError as exc:
            last_error = exc
            time.sleep(0.2)
    raise AssertionError(f'remounted_probe_unavailable:{last_error}')


def detach(expected: list[dict[str, object]], case: str) -> None:
    run_sudo('umount', '-l', str(MOUNT))
    if os.path.ismount(MOUNT):
        raise AssertionError(f'lazy_detach_still_mounted:{case}')
    require_underlay(expected, f'{case}:immediate')


def seed(root_fd: int, name: str, value: bytes) -> None:
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root_fd)
    try:
        os.write(fd, value)
        os.fsync(fd)
    finally:
        os.close(fd)


def run() -> dict[str, object]:
    expected_underlay = json.loads(UNDERLAY.read_text())
    if CONFIG.exists():
        bound = StorageConfig.load(CONFIG)
    else:
        bound = configure(CONFIG, MOUNT, 'data/root', min_free_bytes=0, platform='linux')
    if not (MOUNT / 'data/root/.research-root.json').exists():
        create_root(CONFIG)
    report: dict[str, object] = {
        'schema': 'linux-ext4-writer-detach-qualification-v1',
        'platform': 'Ubuntu 24.04 / ext4 loop image',
        'volume_uuid': bound.volume_uuid,
        'root_id': bound.root_id,
        'mount_path': str(MOUNT),
        'underlay_before_sha256': hashlib.sha256(json.dumps(expected_underlay, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        'underlay_before': expected_underlay,
        'precheck_detach': None,
        'between_guard_and_syscall': [],
    }

    with writer_guard(CONFIG) as guard:
        root_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)
        try:
            st = os.fstat(root_fd)
            report['precheck_detach'] = {
                'mount_id': _fd_mount_id(guard.mount_fd),
                'root_device': st.st_dev,
                'root_inode': st.st_ino,
            }
            detach(expected_underlay, 'before_revalidation')
            try:
                revalidate_descriptor_write(guard)
            except StorageError as exc:
                report['precheck_detach']['result'] = str(exc)
            else:
                raise AssertionError('precheck_detach_did_not_fail')
            require_underlay(expected_underlay, 'before_revalidation:postcheck')
            if (MOUNT / 'data/root/forbidden.bin').exists():
                raise AssertionError('precheck_detach_mutated_volume')
        finally:
            os.close(root_fd)
    remount()

    cases = [
        ('mkdir', 'mkdir', lambda writer: writer.mkdir('mkdir-created')),
        ('create', 'create', lambda writer: writer.create('create-created.bin', b'created-after-detach')),
        ('append', 'append_open', lambda writer: writer.append('append.log', b'next\n')),
        ('atomic_rename', 'atomic_rename', lambda writer: writer.atomic_write('state.json', b'new-state')),
    ]
    for case, trigger, operation in cases:
        print(f'start_case={case}', flush=True)
        print(f'probe_before_case={storage.LinuxExt4VolumeProbe().inspect(MOUNT)!r}', flush=True)
        with writer_guard(CONFIG) as guard:
            root_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)
            try:
                if case == 'append':
                    seed(root_fd, 'append.log', b'prior\n')
                if case == 'atomic_rename':
                    seed(root_fd, 'state.json', b'old-state')
                root_stat = os.fstat(root_fd)
                item: dict[str, object] = {
                    'case': case,
                    'mount_id': _fd_mount_id(guard.mount_fd),
                    'root_device': root_stat.st_dev,
                    'root_inode': root_stat.st_ino,
                }
                detached = False

                def hook(name: str) -> None:
                    nonlocal detached
                    if name == trigger and not detached:
                        detach(expected_underlay, case)
                        detached = True

                writer = DescriptorWriter(
                    root_fd,
                    before_write=guard.revalidate_identity,
                    fault_hook=hook,
                    storage_guard=guard,
                )
                try:
                    operation(writer)
                    item['operation_result'] = 'completed'
                except (StorageError, OSError) as exc:
                    item['operation_result'] = f'rejected:{type(exc).__name__}:{str(exc)}'
                finally:
                    writer.close()
                if not detached:
                    raise AssertionError(f'detach_hook_not_reached:{case}')
                require_underlay(expected_underlay, f'{case}:after_operation')
                report['between_guard_and_syscall'].append(item)
            finally:
                os.close(root_fd)
        remount()
        with writer_guard(CONFIG) as guard:
            root_fd = _open_relative_directory(guard.mount_fd, guard.config.data_root)
            relative = {
                'mkdir': 'mkdir-created',
                'create': 'create-created.bin',
                'append': 'append.log',
                'atomic_rename': 'state.json',
            }[case]
            try:
                if case == 'mkdir':
                    fd = os.open(relative, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
                    os.close(fd)
                    persisted = 'directory'
                else:
                    fd = os.open(relative, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
                    try:
                        data = os.read(fd, 4096)
                    finally:
                        os.close(fd)
                    if case == 'create':
                        if data != b'created-after-detach':
                            raise AssertionError('create_not_on_original_volume')
                        persisted = hashlib.sha256(data).hexdigest()
                    elif case == 'append':
                        if data != b'prior\n':
                            raise AssertionError('append_changed_after_detach')
                        persisted = hashlib.sha256(data).hexdigest()
                    else:
                        if data != b'new-state':
                            raise AssertionError('atomic_publish_not_on_original_volume')
                        persisted = hashlib.sha256(data).hexdigest()
            finally:
                os.close(root_fd)
            report['between_guard_and_syscall'][-1]['remount_result'] = persisted

    run_sudo('umount', str(MOUNT))
    require_underlay(expected_underlay, 'final')
    report['underlay_after'] = inventory(MOUNT)
    report['underlay_after_sha256'] = hashlib.sha256(json.dumps(report['underlay_after'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if report['underlay_before'] != report['underlay_after']:
        raise AssertionError('underlay_inventory_changed')
    OUT.write_text(json.dumps(report, sort_keys=True, indent=2) + '\n')
    return report


if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1] == 'capture-underlay':
        UNDERLAY.write_text(json.dumps(inventory(MOUNT), sort_keys=True, indent=2) + '\n')
        print(UNDERLAY.read_text())
    else:
        print(json.dumps(run(), sort_keys=True))
