"""Transport failures must never publish partial, substituted or out-of-scope data."""

import importlib.util
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "worker_artifacts.py"
spec = importlib.util.spec_from_file_location("worker_artifacts", TOOL)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


def artifact(tmp_path, text="synthetic transport probe"):
    root = tmp_path / "source"
    root.mkdir(parents=True)
    (root / "report.txt").write_text(text)
    (root / "manifest.json").write_text(json.dumps({"report.txt": w.sha(root / "report.txt")}))
    archive = tmp_path / "probe.tar"
    result = w.pack(root, archive, "probe01", "report", w.sha(root / "manifest.json"))
    return root, archive, result


def test_round_trip_retry_and_return(tmp_path):
    source, archive, receipt = artifact(tmp_path)
    store = tmp_path / "mac1"
    first = w.receive(archive, store, receipt["archive_sha256"])
    again = w.receive(archive, store, receipt["archive_sha256"])
    assert first["status"] == "published_transport_only"
    assert again["status"] == "already_present"
    back = tmp_path / "back.tar"
    returned = w.pack(
        Path(first["path"]) / "payload", back, "probe01", "report", receipt["manifest_sha256"]
    )
    final = w.receive(back, tmp_path / "mac2", returned["archive_sha256"])
    assert w.inventory(Path(final["path"]) / "payload") == w.inventory(source)
    assert list((store / ".incoming").iterdir()) == []


def test_receive_stdin_uses_same_verification(tmp_path):
    _, archive, receipt = artifact(tmp_path)
    args = [
        sys.executable,
        str(TOOL),
        "receive-stdin",
        "--store",
        str(tmp_path / "remote"),
        "--sha256",
        receipt["archive_sha256"],
    ]
    done = subprocess.run(args, input=archive.read_bytes(), capture_output=True, check=False)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["status"] == "published_transport_only"
    broken = subprocess.run(
        args, input=archive.read_bytes()[:700], capture_output=True, check=False
    )
    assert broken.returncode != 0


def test_send_uses_explicit_identity_and_host_pins(tmp_path, monkeypatch):
    _, archive, receipt = artifact(tmp_path)
    captured = {}

    def fake_run(args, **kwargs):
        captured.update(args=args, stdin_data=kwargs["stdin"].read(), **kwargs)
        return subprocess.CompletedProcess(
            args, 0, stdout=b'{"status":"already_present"}', stderr=b""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = w.send(
        archive,
        "root1@192.168.31.66",
        "/usr/bin/python3",
        "/tmp/worker_artifacts.py",
        "/private/tmp/exchange",
        identity_file=tmp_path / "mac2_ed25519",
        known_hosts=tmp_path / "known_hosts",
    )

    args = captured["args"]
    assert result["status"] == "already_present"
    assert "-oStrictHostKeyChecking=yes" in args
    assert "-oIdentitiesOnly=yes" in args
    assert args[args.index("-i") + 1] == str(tmp_path / "mac2_ed25519")
    assert f"-oUserKnownHostsFile={tmp_path / 'known_hosts'}" in args
    assert "-oGlobalKnownHostsFile=/dev/null" in args
    assert captured["stdin_data"] == archive.read_bytes()


def test_conflict_preserves_original_and_failed_staging(tmp_path):
    source, archive, receipt = artifact(tmp_path / "a")
    store = tmp_path / "store"
    w.receive(archive, store, receipt["archive_sha256"])
    _, other, different = artifact(tmp_path / "b", "different data")
    with pytest.raises(ValueError, match="artifact_conflict"):
        w.receive(other, store, different["archive_sha256"])
    assert w.inventory(store / "probe01" / "payload") == w.inventory(source)
    assert len(list((store / ".incoming").iterdir())) == 1


def test_bad_archive_pin_publishes_nothing(tmp_path):
    _, archive, _ = artifact(tmp_path)
    with pytest.raises(ValueError, match="archive_pin"):
        w.receive(archive, tmp_path / "store", "0" * 64)
    assert not (tmp_path / "store").exists()


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/escape",
        "payload/../escape",
        "payload/x-wal",
        "payload/.env",
        "payload/reserved-holdout.json",
    ],
)
def test_unsafe_archive_members(tmp_path, name):
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo(name)
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ValueError):
        w.receive(archive, tmp_path / "store", w.sha(archive))
    assert not (tmp_path / "store" / "probe01").exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_links_and_devices_rejected(tmp_path, kind):
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("payload/report.txt")
        info.type, info.linkname = kind, "/etc/passwd"
        tar.addfile(info)
    with pytest.raises(ValueError, match="member_type"):
        w.receive(archive, tmp_path / "store", w.sha(archive))


def test_unlisted_source_file_and_tampered_published_copy(tmp_path):
    source, archive, receipt = artifact(tmp_path)
    (source / "extra.txt").write_text("not sealed")
    with pytest.raises(ValueError, match="membership"):
        w.pack(source, tmp_path / "extra.tar", "probe01", "report", receipt["manifest_sha256"])
    store = tmp_path / "store"
    w.receive(archive, store, receipt["archive_sha256"])
    (store / "probe01" / "payload" / "report.txt").write_text("corrupt")
    with pytest.raises(ValueError, match="membership"):
        w.receive(archive, store, receipt["archive_sha256"])


def test_symlink_store_and_payload_rejected(tmp_path):
    source, archive, receipt = artifact(tmp_path)
    store = tmp_path / "store"
    store.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        w.receive(archive, store, receipt["archive_sha256"])
    (source / "report.txt").unlink()
    (source / "report.txt").symlink_to(archive)
    with pytest.raises(ValueError, match="non_regular"):
        w.verify(source, receipt["manifest_sha256"])
