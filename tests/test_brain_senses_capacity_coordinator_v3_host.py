from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools import brain_senses_capacity_coordinator_v2 as v2
from tools import brain_senses_capacity_coordinator_v3 as v3
from tools import brain_senses_capacity_coordinator_v3_host as host
from tools.brain_senses_capacity_coordinator_v3_host import (
    QUIESCENCE_SCHEMA,
    V2_MUTATING_CLIENT_LABELS,
    V2_MUTATING_ENTRYPOINTS,
    CutoverHostError,
    produce_owner_quiescence_proof,
    publish_cutover_v3,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter


def _free_bytes(_writer):
    return 250_000_000_000


def _quiescence():
    entrypoints = sorted(V2_MUTATING_ENTRYPOINTS)
    proof = {
        "schema": QUIESCENCE_SCHEMA,
        "schema_version": 1,
        "service_lock_paths": LOCKS,
        "registered_clients": {
            label: {
                "entrypoint": entrypoints[index % len(entrypoints)],
                "plist_sha256": f"{index + 1:064x}",
                "installed": True,
            }
            for index, label in enumerate(V2_MUTATING_CLIENT_LABELS)
        },
        "client_labels": {
            label: {"loaded": False, "disabled": True, "pid": None}
            for label in V2_MUTATING_CLIENT_LABELS
        },
        "active_v2_pids": [],
        "active_provider_children": [],
        "observed_at_ns": 1_900_000_000_000_000_000,
    }
    proof["proof_sha256"] = hashlib.sha256(v3.canonical(proof)).hexdigest()
    return proof


UUID = "12345678-1234-1234-1234-123456789abc"
LOCKS = {
    "helius": ".helius-v4.lock",
    "alchemy": ".alchemy-v2.lock",
    "gecko": ".gecko-v1.lock",
}
ROOTS = {
    "helius": ("services/helius-v4",),
    "alchemy": ("services/alchemy-v2",),
    "gecko": ("services/gecko-v1",),
    "coordinator": (v2.COORDINATOR_ROOT,),
}


class Guard:
    def __init__(self, root: Path, root_id: str, uuid: str):
        self.root = root
        self.root_id = root_id
        self.volume_uuid = uuid
        self.expected = (root.stat().st_dev, root.stat().st_ino)

    def revalidate(self, *, min_free_bytes: int | None = None):
        del min_free_bytes
        if (
            not self.root.exists()
            or (self.root.stat().st_dev, self.root.stat().st_ino) != self.expected
        ):
            raise RuntimeError("detached")


class FailAfterCreate:
    def __init__(self, writer: DescriptorWriter, fail_at: int):
        self.writer = writer
        self.fail_at = fail_at
        self.calls = 0

    def __getattr__(self, name):
        return getattr(self.writer, name)

    def create(self, relative: str, payload: bytes, *, mode: int = 0o600):
        self.writer.create(relative, payload, mode=mode)
        self.calls += 1
        if self.calls == self.fail_at:
            raise OSError("simulated post-fsync process crash")


def _fixture(root: Path, *, fault_hook=None):
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    st = root.stat()
    binding = {
        "root_id": "a" * 32,
        "volume_uuid": UUID,
        "device": st.st_dev,
        "inode": st.st_ino,
    }
    for values in ROOTS.values():
        for rel in values:
            path = root / rel
            path.mkdir(parents=True, mode=0o700)
            for parent in (path, *path.parents):
                if parent == root.parent:
                    break
                os.chmod(parent, 0o700)
    for lock_path in [*LOCKS.values(), v2.LOCK_PATH]:
        path = root / lock_path
        path.write_bytes(b"")
        os.chmod(path, 0o600)
    policy_bytes = v2.canonical(v2.policy_document(binding, LOCKS, ROOTS))
    genesis = v2._event(
        seq=0,
        kind="genesis",
        at_ns=1_900_000_000_000_000_000,
        previous=None,
        fields={"policy_sha256": v2.sha256(policy_bytes)},
    )
    ledger_bytes = v2.canonical(genesis)
    (root / v2.POLICY_PATH).write_bytes(policy_bytes)
    (root / v2.LEDGER_PATH).write_bytes(ledger_bytes)
    for path in (root / v2.POLICY_PATH, root / v2.LEDGER_PATH):
        os.chmod(path, 0o600)
    guard = Guard(root, binding["root_id"], UUID)
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        writer = DescriptorWriter(root_fd, fault_hook=fault_hook)
    finally:
        os.close(root_fd)
    return writer, guard, binding


def test_host_cutover_is_pointer_last_and_retry_is_idempotent(tmp_path):
    writer, guard, _binding = _fixture(tmp_path / "data")
    try:
        result = publish_cutover_v3(
            writer=writer,
            guard=guard,
            cutover_ns=1_900_000_000_000_000_001,
            free_bytes_probe=_free_bytes,
            quiescence_verifier=_quiescence,
        )
        assert result["activation_ready"] is True
        assert result["provider_io"] is False
        assert result["writes_created"] == [v3.POLICY_PATH, v3.LEDGER_PATH, v3.POINTER_PATH]
        retry = publish_cutover_v3(
            writer=writer,
            guard=guard,
            cutover_ns=1_900_000_000_000_000_001,
            free_bytes_probe=_free_bytes,
            quiescence_verifier=_quiescence,
        )
        assert retry["writes_created"] == []
        assert retry["pointer_sha256"] == result["pointer_sha256"]
        bridge = v3.build_v2_bridge(
            v2_policy_bytes=writer.read_bytes(v2.POLICY_PATH),
            v2_ledger_bytes=writer.read_bytes(v2.LEDGER_PATH),
            v2_root_inventory={
                root: v2.physical_inventory_bytes(writer, root)
                for roots in ROOTS.values()
                for root in roots
            },
            artifact_sha256_by_path={},
        )
        events = v3.verify_pointer_and_ledger(
            pointer_bytes=writer.read_bytes(v3.POINTER_PATH),
            ledger_bytes=writer.read_bytes(v3.LEDGER_PATH),
            policy_bytes=writer.read_bytes(v3.POLICY_PATH),
            bridge=bridge,
        )
        assert len(events) == 1 and events[0]["kind"] == "genesis"
    finally:
        writer.close()


def test_host_partial_policy_prefix_recovers_with_exact_genesis_time(tmp_path):
    writer, guard, _binding = _fixture(tmp_path / "data")
    try:
        writer.mkdir(v3.DISCOVERY_ROOT)
        writer.mkdir(v3.COORDINATOR_ROOT)
        policy = writer.read_bytes(v2.POLICY_PATH)
        ledger = writer.read_bytes(v2.LEDGER_PATH)
        inventory = {
            root: v2.physical_inventory_bytes(writer, root)
            for roots in ROOTS.values()
            for root in roots
        }
        bridge = v3.build_v2_bridge(
            v2_policy_bytes=policy,
            v2_ledger_bytes=ledger,
            v2_root_inventory=inventory,
            artifact_sha256_by_path={},
        )
        docs = v3.migration_documents(
            v2_policy_bytes=policy,
            v2_ledger_bytes=ledger,
            v2_root_inventory=inventory,
            artifact_sha256_by_path={},
            bridge=bridge,
            root_binding=_binding,
            cutover_ns=1_900_000_000_000_000_001,
            migration_id="f" * 32,
        )
        # Host migration IDs are content-derived, so reproduce the host's plan.
        writer.create(v3.POLICY_PATH, docs["policy"])
        result = publish_cutover_v3(
            writer=writer,
            guard=guard,
            cutover_ns=1_900_000_000_000_000_001,
            free_bytes_probe=_free_bytes,
            quiescence_verifier=_quiescence,
        )
        assert result["activation_ready"] is True
        assert result["writes_created"] == [v3.LEDGER_PATH, v3.POINTER_PATH]
    finally:
        writer.close()


def test_host_recovers_crash_after_genesis_before_pointer(tmp_path):
    writer, guard, _binding = _fixture(tmp_path / "data")
    crash_after_ledger = FailAfterCreate(writer, 2)
    try:
        with pytest.raises(CutoverHostError, match="failed_closed"):
            publish_cutover_v3(
                writer=crash_after_ledger,
                guard=guard,
                cutover_ns=1_900_000_000_000_000_001,
                free_bytes_probe=_free_bytes,
                quiescence_verifier=_quiescence,
            )
        assert writer.read_bytes(v3.POLICY_PATH)
        assert writer.read_bytes(v3.LEDGER_PATH)
        assert not writer.exists(v3.POINTER_PATH)
        result = publish_cutover_v3(
            writer=writer,
            guard=guard,
            cutover_ns=1_900_000_000_000_000_001,
            free_bytes_probe=_free_bytes,
            quiescence_verifier=_quiescence,
        )
        assert result["writes_created"] == [v3.POINTER_PATH]
        assert result["activation_ready"] is True
    finally:
        writer.close()


def test_host_fails_closed_on_detach_without_underlay_artifacts(tmp_path):
    mount_path = tmp_path / "data"
    moved_path = tmp_path / "held-data"
    underlay = mount_path
    calls = 0

    def hook(operation: str) -> None:
        nonlocal calls
        if operation == "create":
            calls += 1
            if calls == 2:
                os.rename(mount_path, moved_path)
                mount_path.mkdir(mode=0o700)

    writer, guard, _binding = _fixture(mount_path, fault_hook=hook)
    try:
        with pytest.raises(CutoverHostError, match="failed_closed|revalidation"):
            publish_cutover_v3(
                writer=writer,
                guard=guard,
                cutover_ns=1_900_000_000_000_000_001,
                free_bytes_probe=_free_bytes,
                quiescence_verifier=_quiescence,
            )
        assert not [path for path in underlay.rglob("*") if path.is_file()]
        assert (moved_path / v3.POLICY_PATH).exists()
        assert not (underlay / v3.POLICY_PATH).exists()
    finally:
        writer.close()


def test_host_rejects_wrong_writer_descriptor_identity(tmp_path):
    writer, guard, _binding = _fixture(tmp_path / "data")
    other = tmp_path / "other"
    other.mkdir(mode=0o700)
    wrong = DescriptorWriter(os.open(other, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)))
    try:
        with pytest.raises(CutoverHostError):
            publish_cutover_v3(
                writer=wrong,
                guard=guard,
                cutover_ns=1_900_000_000_000_000_001,
                free_bytes_probe=_free_bytes,
                quiescence_verifier=_quiescence,
            )
        assert not (other / v3.POLICY_PATH).exists()
    finally:
        writer.close()
        wrong.close()


def test_host_quiescence_requires_exact_mutating_client_label_set(tmp_path):
    writer, guard, _binding = _fixture(tmp_path / "data")
    try:
        proof = _quiescence()
        proof["client_labels"]["ai.memecoin.brain.senses.inert-operator-job"] = {
            "loaded": True,
            "disabled": False,
            "pid": 123,
        }
        proof.pop("proof_sha256")
        proof["proof_sha256"] = hashlib.sha256(v3.canonical(proof)).hexdigest()
        with pytest.raises(CutoverHostError, match="cutover_v2_not_quiescent"):
            publish_cutover_v3(
                writer=writer,
                guard=guard,
                cutover_ns=1_900_000_000_000_000_001,
                free_bytes_probe=_free_bytes,
                quiescence_verifier=lambda: proof,
            )
        assert not writer.exists(v3.POINTER_PATH)
    finally:
        writer.close()


def test_host_cutover_refuses_while_any_v2_service_lane_is_held(tmp_path):
    writer, guard, _binding = _fixture(tmp_path / "data")
    try:
        with writer.lock(LOCKS["gecko"], blocking=False):
            with pytest.raises(CutoverHostError, match="failed_closed"):
                publish_cutover_v3(
                    writer=writer,
                    guard=guard,
                    cutover_ns=1_900_000_000_000_000_001,
                    free_bytes_probe=_free_bytes,
                    quiescence_verifier=_quiescence,
                )
        assert not writer.exists(v3.POINTER_PATH)
    finally:
        writer.close()


def test_shared_v3_lock_excludes_a_competing_process(tmp_path):
    root = tmp_path / "lock-root"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    writer = DescriptorWriter(root_fd)
    child_script = """\
import os, sys
from tools.brain_senses_capacity_coordinator_v3 import SHARED_LOCK_PATH
from tools.brain_senses_external_writer_v1 import DescriptorWriter, StorageError
root_fd = os.open(sys.argv[1], os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
writer = DescriptorWriter(root_fd)
try:
    try:
        with writer.lock(SHARED_LOCK_PATH, blocking=False):
            print('acquired')
    except StorageError:
        print('busy')
finally:
    writer.close()
"""
    try:
        with writer.lock(v3.SHARED_LOCK_PATH, blocking=False):
            blocked = subprocess.run(
                [sys.executable, "-c", child_script, str(root)],
                cwd=Path(__file__).resolve().parents[1],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert blocked.stdout.strip() == "busy"
        acquired = subprocess.run(
            [sys.executable, "-c", child_script, str(root)],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert acquired.stdout.strip() == "acquired"
    finally:
        writer.close()


def test_owner_quiescence_producer_is_exact_and_sanitizes_process_output(monkeypatch):
    registered = _quiescence()["registered_clients"]
    monkeypatch.setattr(host, "_registered_v2_clients", lambda _uid: registered)
    disabled = (
        "disabled services = {\n"
        + "".join(f'    "{label}" => disabled\n' for label in V2_MUTATING_CLIENT_LABELS)
        + "}\n"
    )
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        if argv[1] == "list":
            stdout = "PID Status Label\n- 0 unrelated.operator.job\n"
        elif argv[1] == "print-disabled":
            stdout = disabled
        else:
            stdout = "501 501 /usr/bin/python3 unrelated.py secret-looking-argv\n"
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    proof = produce_owner_quiescence_proof(
        service_lock_paths=LOCKS,
        uid=501,
        run=run,
        observed_at_ns=1_900_000_000_000_000_000,
    )
    assert set(proof["client_labels"]) == set(V2_MUTATING_CLIENT_LABELS)
    assert proof["active_v2_pids"] == []
    assert proof["active_provider_children"] == []
    assert "secret-looking-argv" not in repr(proof)
    assert calls == [
        ["/bin/launchctl", "list"],
        ["/bin/launchctl", "print-disabled", "gui/501"],
        ["/bin/ps", "-axo", "pid=,uid=,args="],
    ]


def test_owner_quiescence_producer_rejects_enabled_or_loaded_client(monkeypatch):
    registered = _quiescence()["registered_clients"]
    monkeypatch.setattr(host, "_registered_v2_clients", lambda _uid: registered)
    states = {label: "disabled" for label in V2_MUTATING_CLIENT_LABELS}
    states["ai.memecoin.brain.senses.external-rolling-v5"] = "enabled"
    enabled = (
        "disabled services = {\n"
        + "".join(f'    "{label}" => {state}\n' for label, state in states.items())
        + "}\n"
    )

    def run(argv, **_kwargs):
        stdout = (
            "PID Status Label\n123 0 ai.memecoin.brain.senses.external-rolling-v5\n"
            if argv[1] == "list"
            else enabled
        )
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    with pytest.raises(CutoverHostError, match="cutover_v2_client_active_or_enabled"):
        produce_owner_quiescence_proof(
            service_lock_paths=LOCKS,
            uid=501,
            run=run,
            observed_at_ns=1_900_000_000_000_000_000,
        )

    def unexpected_runner(argv, **_kwargs):
        stdout = (
            "PID Status Label\n321 0 ai.memecoin.brain.senses.gecko-capture-only-v4-"
            "ffffffffffffffffffffffffffffffff\n"
            if argv[1] == "list"
            else "disabled services = {}\n"
        )
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    with pytest.raises(CutoverHostError, match="cutover_unregistered_v2_client_loaded"):
        produce_owner_quiescence_proof(
            service_lock_paths=LOCKS,
            uid=501,
            run=unexpected_runner,
            observed_at_ns=1_900_000_000_000_000_000,
        )


def test_host_uses_owner_quiescence_producer_when_no_test_verifier_is_injected(
    tmp_path, monkeypatch
):
    writer, guard, _binding = _fixture(tmp_path / "data")
    registered = _quiescence()["registered_clients"]
    monkeypatch.setattr(host, "_registered_v2_clients", lambda _uid: registered)
    disabled = (
        "disabled services = {\n"
        + "".join(f'    "{label}" => disabled\n' for label in V2_MUTATING_CLIENT_LABELS)
        + "}\n"
    )

    def run(argv, **_kwargs):
        if argv[1] == "list":
            stdout = "PID Status Label\n"
        elif argv[1] == "print-disabled":
            stdout = disabled
        else:
            stdout = ""
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    try:
        result = publish_cutover_v3(
            writer=writer,
            guard=guard,
            cutover_ns=1_900_000_000_000_000_001,
            free_bytes_probe=_free_bytes,
            quiescence_runner=run,
        )
        assert result["activation_ready"] is True
        assert writer.exists(v3.POINTER_PATH)
    finally:
        writer.close()
