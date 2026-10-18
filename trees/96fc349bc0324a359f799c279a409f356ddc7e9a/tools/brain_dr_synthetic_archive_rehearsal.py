#!/usr/bin/env python3
"""Qualify synthetic encrypted archive metadata handling on macOS; never reads project data."""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import pathlib
import secrets
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager


def _run(args: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, check=True, capture_output=True, **kwargs)


def _passphrase_pipe() -> tuple[int, int]:
    return os.pipe()


def _decrypt(
    gpg: str, home: pathlib.Path, ciphertext: pathlib.Path, passphrase: bytes
) -> tuple[int, bytes, bytes]:
    read_fd, write_fd = _passphrase_pipe()
    proc = subprocess.Popen(
        [
            gpg,
            "--homedir",
            str(home),
            "--batch",
            "--pinentry-mode",
            "loopback",
            "--passphrase-fd",
            str(read_fd),
            "--status-fd",
            "2",
            "--decrypt",
            str(ciphertext),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=(read_fd,),
    )
    os.close(read_fd)
    try:
        os.write(write_fd, passphrase + b"\n")
    finally:
        os.close(write_fd)
    stdout, stderr = proc.communicate()
    return proc.returncode, stdout, stderr


def _entries(root: pathlib.Path) -> set[str]:
    return {str(path.relative_to(root)) for path in root.rglob("*")}


def _acl_entries(path: pathlib.Path) -> list[str]:
    result = _run(["/bin/ls", "-le", str(path)]).stdout.decode().splitlines()
    return [line for line in result if line.lstrip().startswith("0:")]


def _has_flag(path: pathlib.Path, flag: str) -> bool:
    output = _run(["/bin/ls", "-lO", str(path)]).stdout.decode()
    return flag in output.split()


@contextmanager
def _synthetic_home(agent: str, gpgconf: str) -> Iterator[pathlib.Path]:
    with tempfile.TemporaryDirectory(prefix="brain-dr-synthetic-") as temp:
        home = pathlib.Path(temp)
        home.chmod(0o700)
        source_flag = home / "source" / "acl-flag.txt"
        restored_flag = home / "restored" / "acl-flag.txt"
        started = False
        try:
            _run([agent, "--homedir", str(home), "--daemon", "--log-file", str(home / "agent.log")])
            started = True
            yield home
        finally:
            for path in (source_flag, restored_flag):
                try:
                    flagged = path.exists() and _has_flag(path, "uchg")
                except (OSError, subprocess.CalledProcessError):
                    flagged = False
                if flagged:
                    subprocess.run(["/usr/bin/chflags", "nouchg", str(path)], check=False)
            if started:
                subprocess.run(
                    [gpgconf, "--homedir", str(home), "--kill", "gpg-agent"],
                    check=False,
                    capture_output=True,
                )


def main() -> int:
    bsdtar = shutil.which("bsdtar")
    gpg = shutil.which("gpg")
    agent = shutil.which("gpg-agent")
    gpgconf = shutil.which("gpgconf")
    if not all((bsdtar, gpg, agent, gpgconf)):
        raise SystemExit("required tools unavailable: bsdtar, gpg, gpg-agent, gpgconf")

    with _synthetic_home(agent, gpgconf) as home:
        source = home / "source"
        restored = home / "restored"
        source.mkdir()
        restored.mkdir()

        payload = source / "payload.bin"
        payload.write_bytes(b"synthetic-portability-fixture\x00" * 8192)
        payload.chmod(0o640)
        hardlink = source / "hardlink.bin"
        hardlink.hardlink_to(payload)
        (source / "symlink.bin").symlink_to("payload.bin")
        (source / "empty.bin").touch()
        sparse = source / "sparse.bin"
        with sparse.open("wb") as stream:
            stream.seek(32 * 1024 * 1024)
            stream.write(b"end")
        _run(["/usr/bin/xattr", "-w", "user.brain-dr-test", "xattr-check", str(payload)])
        resource_fork = subprocess.run(
            ["/usr/bin/xattr", "-w", "com.apple.ResourceFork", "fork-check", str(payload)],
            capture_output=True,
        )
        if resource_fork.returncode:
            raise SystemExit("synthetic resource-fork fixture could not be created")

        flagged = source / "acl-flag.txt"
        flagged.write_text("synthetic-only\n", encoding="utf-8")
        flagged.chmod(0o640)
        _run(["/bin/chmod", "+a", f"user:{getpass.getuser()} allow read", str(flagged)])
        _run(["/usr/bin/chflags", "uchg", str(flagged)])

        passphrase = secrets.token_hex(32).encode("ascii")
        ciphertext = home / "synthetic.tar.gpg"
        read_fd, write_fd = _passphrase_pipe()
        tar = subprocess.Popen(
            [
                bsdtar,
                "--read-sparse",
                "--acls",
                "--fflags",
                "--xattrs",
                "-cf",
                "-",
                "-C",
                str(source),
                ".",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert tar.stdout is not None
        with ciphertext.open("wb") as output:
            encrypt = subprocess.Popen(
                [
                    gpg,
                    "--homedir",
                    str(home),
                    "--batch",
                    "--pinentry-mode",
                    "loopback",
                    "--passphrase-fd",
                    str(read_fd),
                    "--symmetric",
                    "--cipher-algo",
                    "AES256",
                    "--output",
                    "-",
                ],
                stdin=tar.stdout,
                stdout=output,
                stderr=subprocess.PIPE,
                pass_fds=(read_fd,),
            )
            tar.stdout.close()
            os.close(read_fd)
            try:
                os.write(write_fd, passphrase + b"\n")
            finally:
                os.close(write_fd)
            encrypt_stderr = encrypt.communicate()[1]
            tar_stderr = tar.communicate()[1]
        if encrypt.returncode or tar.returncode:
            raise SystemExit(f"synthetic archive command failed: {encrypt_stderr!r} {tar_stderr!r}")

        decrypt_code, tar_bytes, decrypt_status = _decrypt(gpg, home, ciphertext, passphrase)
        if decrypt_code:
            raise SystemExit("synthetic ciphertext integrity/decrypt failed")
        if b"[GNUPG:] GOODMDC" not in decrypt_status:
            raise SystemExit("GPG terminal integrity status GOODMDC missing")
        extract = subprocess.run(
            [bsdtar, "--acls", "--fflags", "--xattrs", "-xf", "-", "-C", str(restored)],
            input=tar_bytes,
            capture_output=True,
        )
        if extract.returncode:
            raise SystemExit("synthetic archive extraction failed")

        restored_payload = restored / "payload.bin"
        restored_sparse = restored / "sparse.bin"
        restored_flagged = restored / "acl-flag.txt"
        checks = {
            "archive_member_set": _entries(source) == _entries(restored),
            "payload_sha256": hashlib.sha256(payload.read_bytes()).digest()
            == hashlib.sha256(restored_payload.read_bytes()).digest(),
            "file_mode": payload.stat().st_mode & 0o777 == restored_payload.stat().st_mode & 0o777,
            "owner_ids": (payload.stat().st_uid, payload.stat().st_gid)
            == (restored_payload.stat().st_uid, restored_payload.stat().st_gid),
            "hardlink": hardlink.stat().st_ino == payload.stat().st_ino
            and (restored / "hardlink.bin").stat().st_ino == restored_payload.stat().st_ino,
            "symlink_target": os.readlink(source / "symlink.bin")
            == os.readlink(restored / "symlink.bin"),
            "logical_size": sparse.stat().st_size == restored_sparse.stat().st_size,
            "sparse_allocation": sparse.stat().st_blocks == restored_sparse.stat().st_blocks,
            "xattr": _run(
                ["/usr/bin/xattr", "-p", "user.brain-dr-test", str(restored_payload)]
            ).stdout.strip()
            == b"xattr-check",
            "resource_fork": _run(
                ["/usr/bin/xattr", "-p", "com.apple.ResourceFork", str(restored_payload)]
            ).stdout.strip()
            == b"fork-check",
            "acl": _acl_entries(flagged) == _acl_entries(restored_flagged)
            and bool(_acl_entries(restored_flagged)),
            "immutable_flag": _has_flag(flagged, "uchg") and _has_flag(restored_flagged, "uchg"),
        }
        wrong_code, _, _ = _decrypt(gpg, home, ciphertext, secrets.token_hex(32).encode("ascii"))
        corrupted = home / "corrupted.gpg"
        altered = bytearray(ciphertext.read_bytes())
        altered[-1] ^= 1
        corrupted.write_bytes(altered)
        corrupted_code, _, _ = _decrypt(gpg, home, corrupted, passphrase)
        checks["wrong_passphrase_rejected"] = wrong_code != 0
        checks["corrupt_ciphertext_rejected"] = corrupted_code != 0
        if not all(checks.values()):
            raise SystemExit(json.dumps({"checks": checks}, sort_keys=True))

        gpg_version = _run([gpg, "--version"]).stdout.decode().splitlines()[0]
        tar_version = _run([bsdtar, "--version"]).stdout.decode().splitlines()[0]
        print(
            json.dumps(
                {
                    "synthetic_only": True,
                    "checks": checks,
                    "gpg": gpg_version,
                    "bsdtar": tar_version,
                    "ciphertext_bytes": ciphertext.stat().st_size,
                    "ciphertext_sha256": hashlib.sha256(ciphertext.read_bytes()).hexdigest(),
                    "limitations": [
                        "no Kingston descriptor-bound write",
                        "no interruption/collision/detach testing",
                        "no group commit-marker publication testing",
                        "sparse allocation is tested only on this synthetic fixture",
                        "no protected payload was read or copied",
                    ],
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
