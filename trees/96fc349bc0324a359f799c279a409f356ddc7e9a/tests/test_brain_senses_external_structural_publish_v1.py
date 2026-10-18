from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from tools import brain_senses_external_schema_v2 as lease_schema
from tools import brain_senses_external_structural_input_v1 as builder
from tools import brain_senses_external_structural_publish_v1 as publish
from tools.brain_senses_external_structural_input_schema_v1 import (
    BOOTSTRAP_BUCKET,
    StructuralInputError,
    canonical,
)


class FakeGuard:
    def __init__(
        self, mount_fd: int, *, root_id: str = "root-id", volume_uuid: str = "uuid"
    ) -> None:
        self.mount_fd = mount_fd
        self.root_id = root_id
        self.volume_uuid = volume_uuid
        self.config = type("Config", (), {"data_root": "BrainData"})()
        self.revalidations: list[int | None] = []

    def __enter__(self) -> FakeGuard:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def revalidate(self, *, min_free_bytes: int | None = None) -> None:
        self.revalidations.append(min_free_bytes)


class EmptyReader:
    def list_files(self) -> list[str]:
        return []


def _artifact(plan_sha256: str = "a" * 64) -> dict[str, Any]:
    bootstrap_gap = {
        "start": 100,
        "end_exclusive": 100,
        "reason": "uncovered_since_predecessor_no_backfill",
        "bootstrap_bucket": BOOTSTRAP_BUCKET,
    }
    return {
        "schema": "brain-senses-external-structural-input-v1",
        "schema_version": 1,
        "publishable": True,
        "point_in_time_eligible": False,
        "decision_ns": None,
        "scope": {
            "plan_sha256": plan_sha256,
            "event_tail_sha256": "b" * 64,
            "source_code_sha256": {"engine.py": "c" * 64},
            "builder_code_sha256": {"builder.py": "d" * 64},
            "storage": {
                "root_id": "root-id",
                "volume_uuid": "uuid",
                "relative_root": "services/svc",
            },
        },
        "predecessor_certificate_sha256": "e" * 64,
        "predecessor_gap_intervals": [],
        "bootstrap_uncovered_interval": bootstrap_gap,
        "verified_gap_intervals": [bootstrap_gap],
        "maximum_source_availability_ns": 100,
        "frames": [],
        "captures": [],
        "references": [],
        "totals": {},
    }


def _private_file(path: Path, content: bytes) -> None:
    path.write_bytes(content)
    os.chmod(path, 0o600)


def _setup_storage(tmp_path: Path) -> tuple[int, Path, Path, Path, dict[str, Any]]:
    mount = tmp_path / "volume"
    mount.mkdir(mode=0o700)
    brain = mount / "BrainData"
    brain.mkdir(mode=0o700)
    services = brain / "services"
    services.mkdir(mode=0o700)
    source = brain / "services" / "svc"
    source.mkdir(mode=0o700)
    predecessor = brain / "services" / "v1"
    predecessor.mkdir(mode=0o700)
    plan = {
        "schema": "brain-senses-external-v2-plan",
        "schema_version": 2,
        "service_id": "svc",
        "storage": {"root_id": "root-id", "volume_uuid": "uuid", "relative_root": "services/svc"},
        "predecessor": {"relative_root": "services/v1"},
        "code_sha256": {"engine.py": "c" * 64},
        "caps": lease_schema.CAPS,
    }
    _private_file(source / lease_schema.PLAN_FILE, canonical(plan))
    _private_file(source / lease_schema.LOCK_FILE, b"")
    _private_file(predecessor / ".writer.lock", b"")
    return os.open(mount, os.O_RDONLY | os.O_DIRECTORY), mount, source, predecessor, plan


def _plan_sha256(plan: dict[str, Any]) -> str:
    from tools.brain_senses_external_verify_v2 import sha

    return sha(canonical(plan))


def _invoke(
    tmp_path: Path, monkeypatch: Any, *, guard: FakeGuard | None = None
) -> tuple[dict[str, Any], Path, FakeGuard]:
    fd, _mount, source, _pred, plan = _setup_storage(tmp_path)
    fake_guard = guard or FakeGuard(fd)
    plan_sha256 = _plan_sha256(plan)
    artifact = _artifact(plan_sha256)
    monkeypatch.setattr(builder, "compile_from_sealed_v2", lambda *_a, **_k: artifact)
    result = publish.publish_after_seal(
        config_path=tmp_path / "storage.json",
        service_id="svc",
        predecessor_relative_root="services/v1",
        expected_plan_sha256=plan_sha256,
        expected_lease_sources={"engine.py": "c" * 64},
        guard_factory=lambda *_a, **_k: fake_guard,
        host_lock=lambda: __import__("contextlib").nullcontext(),
    )
    os.close(fd)
    return result, source, fake_guard


def test_publication_uses_distinct_descriptor_roots_and_full_revalidation(
    tmp_path: Path, monkeypatch: Any
) -> None:
    result, source, guard = _invoke(tmp_path, monkeypatch)
    assert result["manifest_path"] == "derived/structural-input-v1/svc/manifest.json"
    assert result["source_relative_root"] == "services/svc"
    assert result["output_relative_root"] == "derived/structural-input-v1/svc"
    assert result["confirmed_ns"] >= result["maximum_source_availability_ns"]
    assert result["decision_ns"] is None and result["point_in_time_eligible"] is False
    assert guard.revalidations
    assert set(guard.revalidations) == {publish.required_free_bytes()}
    assert sorted(path.relative_to(source).as_posix() for path in source.rglob("*")) == [
        ".writer-v2.lock",
        "external-plan-v2.json",
    ]
    output = tmp_path / "volume" / "BrainData" / "derived" / "structural-input-v1" / "svc"
    assert {entry.name for entry in output.iterdir()} == {
        ".writer.lock",
        "manifest.json",
        "publication.json",
    }


def test_wrong_guard_identity_fails_before_output_creation(
    tmp_path: Path, monkeypatch: Any
) -> None:
    fd, mount, _source, _pred, plan = _setup_storage(tmp_path)
    guard = FakeGuard(fd, volume_uuid="other")
    plan_sha256 = _plan_sha256(plan)
    artifact = _artifact(plan_sha256)
    monkeypatch.setattr(builder, "compile_from_sealed_v2", lambda *_a, **_k: artifact)
    with pytest.raises(Exception, match="structural_plan_storage_identity"):
        publish.publish_after_seal(
            config_path=tmp_path / "storage.json",
            service_id="svc",
            predecessor_relative_root="services/v1",
            expected_plan_sha256=plan_sha256,
            expected_lease_sources={"engine.py": "c" * 64},
            guard_factory=lambda *_a, **_k: guard,
            host_lock=lambda: __import__("contextlib").nullcontext(),
        )
    assert not (mount / "BrainData" / "derived").exists()
    os.close(fd)


def test_writer_fd_must_be_sibling_output_not_source(tmp_path: Path, monkeypatch: Any) -> None:
    fd, _mount, _source, _pred, plan = _setup_storage(tmp_path)
    guard = FakeGuard(fd)
    plan_sha256 = _plan_sha256(plan)
    artifact = _artifact(plan_sha256)
    monkeypatch.setattr(builder, "compile_from_sealed_v2", lambda *_a, **_k: artifact)

    class WrongWriter:
        def __init__(self, root_fd: int, **_kwargs: Any) -> None:
            self.root_fd = os.dup(_source_fd)

        def close(self) -> None:
            os.close(self.root_fd)

        def __enter__(self) -> WrongWriter:
            return self

        def __exit__(self, *_: object) -> None:
            self.close()

    source_fd = os.open(
        tmp_path / "volume" / "BrainData" / "services" / "svc",
        os.O_RDONLY | os.O_DIRECTORY,
    )
    _source_fd = source_fd
    monkeypatch.setattr(publish, "DescriptorWriter", WrongWriter)
    with pytest.raises(Exception, match="structural_writer_fd_mismatch"):
        publish.publish_after_seal(
            config_path=tmp_path / "storage.json",
            service_id="svc",
            predecessor_relative_root="services/v1",
            expected_plan_sha256=plan_sha256,
            expected_lease_sources={"engine.py": "c" * 64},
            guard_factory=lambda *_a, **_k: guard,
            host_lock=lambda: __import__("contextlib").nullcontext(),
        )
    os.close(source_fd)
    os.close(fd)


def test_source_replay_change_after_manifest_withholds_receipt(
    tmp_path: Path, monkeypatch: Any
) -> None:
    fd, mount, _source, _pred, plan = _setup_storage(tmp_path)
    guard = FakeGuard(fd)
    plan_sha256 = _plan_sha256(plan)
    artifact = _artifact(plan_sha256)
    changed = copy.deepcopy(artifact)
    changed["totals"] = {"unexpected": 1}
    calls = 0

    def compile_source(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return artifact if calls <= 2 else changed

    monkeypatch.setattr(builder, "compile_from_sealed_v2", compile_source)
    with pytest.raises(StructuralInputError, match="artifact_source_replay_mismatch"):
        publish.publish_after_seal(
            config_path=tmp_path / "storage.json",
            service_id="svc",
            predecessor_relative_root="services/v1",
            expected_plan_sha256=plan_sha256,
            expected_lease_sources={"engine.py": "c" * 64},
            guard_factory=lambda *_a, **_k: guard,
            host_lock=lambda: __import__("contextlib").nullcontext(),
        )
    output = mount / "BrainData" / "derived" / "structural-input-v1" / "svc"
    assert (output / "manifest.json").exists()
    assert not (output / "publication.json").exists()
    os.close(fd)


def test_source_file_set_change_after_manifest_withholds_receipt(
    tmp_path: Path, monkeypatch: Any
) -> None:
    fd, mount, source, _pred, plan = _setup_storage(tmp_path)
    guard = FakeGuard(fd)
    plan_sha256 = _plan_sha256(plan)
    artifact = _artifact(plan_sha256)
    calls = 0

    def compile_source(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 2:
            _private_file(source / "unexpected.json", b"{}\n")
        return artifact

    monkeypatch.setattr(builder, "compile_from_sealed_v2", compile_source)
    with pytest.raises(StructuralInputError, match="source_file_set_changed_during_publication"):
        publish.publish_after_seal(
            config_path=tmp_path / "storage.json",
            service_id="svc",
            predecessor_relative_root="services/v1",
            expected_plan_sha256=plan_sha256,
            expected_lease_sources={"engine.py": "c" * 64},
            guard_factory=lambda *_a, **_k: guard,
            host_lock=lambda: __import__("contextlib").nullcontext(),
        )
    output = mount / "BrainData" / "derived" / "structural-input-v1" / "svc"
    assert (output / "manifest.json").exists()
    assert not (output / "publication.json").exists()
    os.close(fd)


@pytest.mark.parametrize(
    "tamper",
    [
        b'{"schema":"x","schema":"y"}\n',
        b'{ "schema": "brain-senses-external-structural-input-publication-v1" }\n',
        None,
    ],
)
def test_existing_receipt_requires_canonical_bytes_exact_keys_and_values(
    tmp_path: Path, monkeypatch: Any, tamper: bytes | None
) -> None:
    root = tmp_path / "output"
    root.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        from tools.brain_senses_external_writer_v1 import DescriptorWriter

        writer = DescriptorWriter(root_fd)
        artifact = _artifact()
        monkeypatch.setattr(builder, "compile_from_sealed_v2", lambda *_a, **_k: artifact)
        common = {
            "source_reader": EmptyReader(),
            "predecessor_reader": EmptyReader(),
            "expected_plan_sha256": "a" * 64,
            "expected_lease_sources": {"engine.py": "c" * 64},
            "relative_directory": "",
            "service_id": "svc",
            "source_relative_root": "services/svc",
            "output_relative_root": "derived/structural-input-v1/svc",
            "identity_check": lambda: {"root_id": "root-id", "volume_uuid": "uuid"},
            "revalidate": lambda: None,
        }
        first = builder.publish_create_only(writer, artifact, **common, confirmed_clock=lambda: 200)
        assert first["confirmed_ns"] == 200
        if tamper is None:
            receipt = json.loads(writer.read_bytes("publication.json"))
            receipt["unreviewed"] = True
            tamper_bytes = canonical(receipt)
        else:
            tamper_bytes = tamper
        fd = os.open("publication.json", os.O_WRONLY | os.O_TRUNC, dir_fd=root_fd)
        try:
            os.write(fd, tamper_bytes)
            os.fsync(fd)
        finally:
            os.close(fd)
        with pytest.raises(StructuralInputError):
            builder.publish_create_only(writer, artifact, **common, confirmed_clock=lambda: 300)
        writer.close()
    finally:
        os.close(root_fd)


def test_manifest_without_receipt_is_replayed_fsynced_then_completed_once(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = tmp_path / "output"
    root.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        from tools.brain_senses_external_writer_v1 import DescriptorWriter

        writer = DescriptorWriter(root_fd)
        artifact = _artifact()
        monkeypatch.setattr(builder, "compile_from_sealed_v2", lambda *_a, **_k: artifact)
        writer.create("manifest.json", canonical(artifact))
        common = {
            "source_reader": EmptyReader(),
            "predecessor_reader": EmptyReader(),
            "expected_plan_sha256": "a" * 64,
            "expected_lease_sources": {"engine.py": "c" * 64},
            "relative_directory": "",
            "service_id": "svc",
            "source_relative_root": "services/svc",
            "output_relative_root": "derived/structural-input-v1/svc",
            "identity_check": lambda: {"root_id": "root-id", "volume_uuid": "uuid"},
            "revalidate": lambda: None,
        }
        first = builder.publish_create_only(writer, artifact, **common, confirmed_clock=lambda: 200)
        second = builder.publish_create_only(
            writer, artifact, **common, confirmed_clock=lambda: 300
        )
        assert first == second
        assert first["confirmed_ns"] == 200
        assert sorted(writer.list_files()) == ["manifest.json", "publication.json"]
        writer.close()
    finally:
        os.close(root_fd)


def test_receipt_without_manifest_is_rejected_without_repair(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = tmp_path / "output"
    root.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        from tools.brain_senses_external_writer_v1 import DescriptorWriter

        writer = DescriptorWriter(root_fd)
        artifact = _artifact()
        monkeypatch.setattr(builder, "compile_from_sealed_v2", lambda *_a, **_k: artifact)
        writer.create("publication.json", b"{}\n")
        common = {
            "source_reader": EmptyReader(),
            "predecessor_reader": EmptyReader(),
            "expected_plan_sha256": "a" * 64,
            "expected_lease_sources": {"engine.py": "c" * 64},
            "relative_directory": "",
            "service_id": "svc",
            "source_relative_root": "services/svc",
            "output_relative_root": "derived/structural-input-v1/svc",
            "identity_check": lambda: {"root_id": "root-id", "volume_uuid": "uuid"},
            "revalidate": lambda: None,
        }
        with pytest.raises(StructuralInputError, match="publication_receipt_without_artifact"):
            builder.publish_create_only(writer, artifact, **common)
        assert sorted(writer.list_files()) == ["publication.json"]
        writer.close()
    finally:
        os.close(root_fd)


def test_confirmation_must_follow_max_source_availability(tmp_path: Path, monkeypatch: Any) -> None:
    root = tmp_path / "output"
    root.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        from tools.brain_senses_external_writer_v1 import DescriptorWriter

        writer = DescriptorWriter(root_fd)
        artifact = _artifact()
        monkeypatch.setattr(builder, "compile_from_sealed_v2", lambda *_a, **_k: artifact)
        common = {
            "source_reader": EmptyReader(),
            "predecessor_reader": EmptyReader(),
            "expected_plan_sha256": "a" * 64,
            "expected_lease_sources": {"engine.py": "c" * 64},
            "relative_directory": "",
            "service_id": "svc",
            "source_relative_root": "services/svc",
            "output_relative_root": "derived/structural-input-v1/svc",
            "identity_check": lambda: {"root_id": "root-id", "volume_uuid": "uuid"},
            "revalidate": lambda: None,
        }
        with pytest.raises(
            StructuralInputError, match="publication_clock_before_source_availability"
        ):
            builder.publish_create_only(writer, artifact, **common, confirmed_clock=lambda: 99)
        assert writer.exists("manifest.json")
        assert not writer.exists("publication.json")
        writer.close()
    finally:
        os.close(root_fd)


def test_guard_detach_failure_leaves_sibling_underlay_unmodified(
    tmp_path: Path, monkeypatch: Any
) -> None:
    fd, mount, _source, _pred, plan = _setup_storage(tmp_path)
    plan_sha256 = _plan_sha256(plan)
    artifact = _artifact(plan_sha256)
    calls = 0

    class DetachingGuard(FakeGuard):
        def revalidate(self, *, min_free_bytes: int | None = None) -> None:
            nonlocal calls
            calls += 1
            super().revalidate(min_free_bytes=min_free_bytes)
            if calls == 3:
                raise OSError("simulated detached mount")

    monkeypatch.setattr(builder, "compile_from_sealed_v2", lambda *_a, **_k: artifact)
    before = sorted(path.relative_to(mount).as_posix() for path in mount.rglob("*"))
    with pytest.raises(OSError, match="simulated detached mount"):
        publish.publish_after_seal(
            config_path=tmp_path / "storage.json",
            service_id="svc",
            predecessor_relative_root="services/v1",
            expected_plan_sha256=plan_sha256,
            expected_lease_sources={"engine.py": "c" * 64},
            guard_factory=lambda *_a, **_k: DetachingGuard(fd),
            host_lock=lambda: __import__("contextlib").nullcontext(),
        )
    after = sorted(path.relative_to(mount).as_posix() for path in mount.rglob("*"))
    assert before == after
    os.close(fd)
