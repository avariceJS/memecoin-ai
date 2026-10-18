"""Explicit sealed-artifact transport. Publication is transport-only, never admission."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

MAX_FILES = 100000
MAX_BYTES = 64 * 1024**3
ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}\Z")
PIN = re.compile(r"[0-9a-f]{64}\Z")
ROLES = {"report", "dataset-shards", "completed-model", "sealed-snapshot"}


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_name(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or p.as_posix() != name or ".." in p.parts or "\\" in name:
        raise ValueError("unsafe_member")
    if any(ord(c) < 32 for c in name):
        raise ValueError("unsafe_member")
    lowered = name.lower()
    if any(s in lowered for s in ("holdout", "f01", "d43", "credential", "secret", "wallet")):
        raise ValueError("protected_member")
    if any(s.startswith(".env") for s in p.parts) or lowered.endswith(
        (".pem", ".key", "-wal", "-shm")
    ):
        raise ValueError("mutable_or_secret_member")
    return name


def plain(path):
    path = Path(os.path.abspath(path))
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("symlink_path")
    return path


def read_json(path):
    if path.stat().st_size > 16 * 1024**2:
        raise ValueError("metadata_bound")

    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError("duplicate_json_key")
            result[k] = v
        return result

    return json.loads(path.read_text(), object_pairs_hook=unique)


def inventory(root):
    root = plain(root)
    result, total = {}, 0
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode) or path.stat().st_nlink != 1:
            raise ValueError("non_regular_member")
        name = safe_name(path.relative_to(root).as_posix())
        total += path.stat().st_size
        if total > MAX_BYTES or len(result) >= MAX_FILES:
            raise ValueError("artifact_bound")
        result[name] = sha(path)
    return result


def verify(root, pin):
    root = plain(root)
    if not PIN.fullmatch(pin) or sha(root / "manifest.json") != pin:
        raise ValueError("manifest_pin")
    expected = read_json(root / "manifest.json")
    if not isinstance(expected, dict) or not expected or "manifest.json" in expected:
        raise ValueError("manifest_shape")
    for name, digest in expected.items():
        safe_name(name)
        if not isinstance(digest, str) or not PIN.fullmatch(digest):
            raise ValueError("manifest_hash")
    actual = inventory(root)
    if actual != dict(expected, **{"manifest.json": pin}):
        raise ValueError("manifest_membership")
    return actual


def pack(source, archive, artifact_id, role, manifest_pin):
    if not ID.fullmatch(artifact_id) or role not in ROLES:
        raise ValueError("artifact_identity")
    source, archive = plain(source), plain(archive)
    if archive.is_relative_to(source):
        raise ValueError("archive_inside_source")
    members = verify(source, manifest_pin)
    envelope = dict(
        schema=1,
        id=artifact_id,
        role=role,
        scope="exploratory-transfer-only",
        manifest_sha256=manifest_pin,
        members=members,
    )
    with archive.open("xb") as stream, tarfile.open(fileobj=stream, mode="w") as tar:
        import io

        raw = (json.dumps(envelope, sort_keys=True) + "\n").encode()
        info = tarfile.TarInfo("TRANSFER.json")
        info.size = len(raw)
        info.mode = 0o600
        tar.addfile(info, io.BytesIO(raw))
        for name in members:
            tar.add(source / name, arcname="payload/" + name, recursive=False)
    if verify(source, manifest_pin) != members:
        raise ValueError("source_changed")
    # The copied bytes, not only the source, must match before the archive is offered.
    with tempfile.TemporaryDirectory(prefix="artifact-check-", dir=archive.parent) as tmp:
        unpack(archive, Path(tmp))
    return dict(archive_sha256=sha(archive), **envelope)


def unpack(archive, stage):
    seen, total = set(), 0
    with tarfile.open(archive, "r|") as tar:
        for member in tar:
            name = safe_name(member.name)
            if name in seen or not member.isfile() or member.size < 0:
                raise ValueError("archive_member_type_or_duplicate")
            if name != "TRANSFER.json" and not name.startswith("payload/"):
                raise ValueError("archive_membership")
            total += member.size
            if len(seen) > MAX_FILES or total > MAX_BYTES:
                raise ValueError("archive_bound")
            seen.add(name)
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                raise ValueError("archive_member_missing")
            with source, target.open("xb") as out:
                shutil.copyfileobj(source, out, 1024 * 1024)
                out.flush()
                os.fsync(out.fileno())
    envelope = read_json(stage / "TRANSFER.json")
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {"schema", "id", "role", "scope", "manifest_sha256", "members"}
        or envelope["schema"] != 1
        or not isinstance(envelope["id"], str)
        or not ID.fullmatch(envelope["id"])
        or envelope["role"] not in ROLES
        or envelope["scope"] != "exploratory-transfer-only"
    ):
        raise ValueError("transfer_contract")
    members = verify(stage / "payload", envelope["manifest_sha256"])
    if members != envelope["members"] or seen != {
        "TRANSFER.json",
        *("payload/" + n for n in members),
    }:
        raise ValueError("transfer_membership")
    return envelope


def receive(archive, store, expected_sha):
    archive, store = plain(archive), plain(store)
    if not PIN.fullmatch(expected_sha) or sha(archive) != expected_sha:
        raise ValueError("archive_pin")
    store.mkdir(parents=True, exist_ok=True)
    incoming = plain(store / ".incoming")
    incoming.mkdir(exist_ok=True)
    lock = plain(store / ".writer.lock")
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stage = Path(tempfile.mkdtemp(prefix="receive-", dir=incoming))
        # Failed staging is retained as evidence and never becomes a published artifact.
        envelope = unpack(archive, stage)
        if sha(archive) != expected_sha:
            raise ValueError("archive_changed")
        dest = plain(store / envelope["id"])
        if dest.exists():
            if read_json(dest / "TRANSFER.json") != envelope:
                raise ValueError("artifact_conflict")
            verify(dest / "payload", envelope["manifest_sha256"])
            shutil.rmtree(stage)
            return dict(status="already_present", path=str(dest), **envelope)
        for directory in [
            *sorted((p for p in stage.rglob("*") if p.is_dir()), reverse=True),
            stage,
        ]:
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        os.rename(stage, dest)
        fd = os.open(store, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return dict(status="published_transport_only", path=str(dest), **envelope)


def send(
    archive, host, remote_python, remote_tool, remote_store, identity_file=None, known_hosts=None
):
    import shlex

    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*", host):
        raise ValueError("ssh_host")
    archive = plain(archive)
    pin = sha(archive)
    args = [remote_python, remote_tool, "receive-stdin", "--store", remote_store, "--sha256", pin]
    with archive.open("rb") as stream:
        ssh_args = [
            "ssh",
            "-oBatchMode=yes",
            "-oStrictHostKeyChecking=yes",
            "-oConnectTimeout=10",
        ]
        if identity_file is not None:
            ssh_args.extend(("-oIdentitiesOnly=yes", "-i", str(identity_file)))
        if known_hosts is not None:
            ssh_args.extend(
                (
                    f"-oUserKnownHostsFile={known_hosts}",
                    "-oGlobalKnownHostsFile=/dev/null",
                )
            )
        ssh_args.extend((host, shlex.join(args)))
        result = subprocess.run(
            ssh_args,
            stdin=stream,
            capture_output=True,
            timeout=1800,
        )
    if result.returncode:
        raise ValueError("ssh_transfer_failed")
    return json.loads(result.stdout)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    packer = sub.add_parser("pack")
    packer.add_argument("source", type=Path)
    packer.add_argument("archive", type=Path)
    packer.add_argument("--id", required=True)
    packer.add_argument("--role", choices=sorted(ROLES), required=True)
    packer.add_argument("--manifest-sha256", required=True)
    for name in ("receive", "receive-stdin"):
        receiver = sub.add_parser(name)
        if name == "receive":
            receiver.add_argument("archive", type=Path)
        receiver.add_argument("--store", type=Path, required=True)
        receiver.add_argument("--sha256", required=True)
    sender = sub.add_parser("send")
    sender.add_argument("archive", type=Path)
    sender.add_argument("--host", required=True)
    sender.add_argument("--remote-python", required=True)
    sender.add_argument("--remote-tool", required=True)
    sender.add_argument("--remote-store", required=True)
    sender.add_argument("--identity-file", type=Path)
    sender.add_argument("--known-hosts", type=Path)
    a = p.parse_args()
    if a.command == "pack":
        result = pack(a.source, a.archive, a.id, a.role, a.manifest_sha256)
    elif a.command == "receive":
        result = receive(a.archive, a.store, a.sha256)
    elif a.command == "send":
        result = send(
            a.archive,
            a.host,
            a.remote_python,
            a.remote_tool,
            a.remote_store,
            a.identity_file,
            a.known_hosts,
        )
    else:
        import sys

        store = plain(a.store)
        store.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=store, prefix=".transfer-", suffix=".tar") as tmp:
            total = 0
            while chunk := sys.stdin.buffer.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_BYTES + MAX_FILES * 2048:
                    raise ValueError("archive_bound")
                tmp.write(chunk)
            tmp.flush()
            result = receive(Path(tmp.name), store, a.sha256)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
