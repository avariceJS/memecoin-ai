from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from brain_senses_agave_dependency_fetch_v1 import EXPECTED_REGISTRY_SOURCE  # noqa: E402

from tools import brain_senses_agave_metadata_adjudicate_v1 as adjudicate  # noqa: E402

preflight = adjudicate.preflight


ROOT_ID = "a" * 32
VOLUME_UUID = "11111111-2222-3333-4444-555555555555"


def _fixture(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    lock_path = tmp_path / "Cargo.lock"
    graph_count = 4
    tuples = [(f"local-{i}", "1.0.0", None) for i in range(graph_count)]
    tuples.extend((f"lock-only-{i}", "1.0.0", EXPECTED_REGISTRY_SOURCE) for i in range(2))
    lock_rows = ["version = 4", ""]
    for name, version, source in tuples:
        lock_rows += ["[[package]]", f'name = "{name}"', f'version = "{version}"']
        if source is not None:
            lock_rows.append(f'source = "{source}"')
        lock_rows.append("")
    lock_path.write_text("\n".join(lock_rows))
    package_ids = [f"local+sha256:{i:064x}" for i in range(graph_count)]
    graph = {
        "schema": "brain-senses-agave-resolved-graph-v1",
        "root": package_ids[0],
        "workspace_members": package_ids,
        "packages": [
            {"id": package_id, "name": f"local-{i}", "version": "1.0.0", "source": None}
            for i, package_id in enumerate(package_ids)
        ],
        "nodes": [{"id": package_id, "deps": [], "features": []} for package_id in package_ids],
    }
    process = {
        "schema": "brain-senses-agave-metadata-process-capture-v2",
        "schema_version": 2,
        "storage_root_id": ROOT_ID,
        "volume_uuid": VOLUME_UUID,
        "stdout_sha256": "1" * 64,
        "stdout_bytes": 1,
        "stdout_truncated": False,
        "stdout_read_error": False,
        "stdout_complete": True,
        "stdout_hash_scope": "full_stream",
        "stderr_sha256": hashlib.sha256(b"").hexdigest(),
        "stderr_bytes": 0,
        "stderr_truncated": False,
        "stderr_read_error": False,
        "stderr_complete": True,
        "stderr_hash_scope": "full_stream",
        "stderr_sanitized": "",
        "exit_status": 0,
        "classification": "process_completed",
    }
    return {"process": process, "graph": graph}, lock_path


def _canonical(value: Any) -> bytes:
    return preflight._canonical(value)


def _small_expected_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(preflight, "WORKSPACE_MEMBER_COUNT", 6)
    monkeypatch.setattr(
        adjudicate,
        "EXPECTED_GRAPH_COUNTS",
        {
            "lock_tuple_count": 6,
            "graph_tuple_count": 4,
            "lock_only_tuple_count": 2,
            "source_null_workspace_member_count": 4,
        },
    )


def test_independent_adjudicator_accepts_closed_graph_in_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _small_expected_counts(monkeypatch)
    artifacts, lock_path = _fixture(tmp_path)
    process, graph, counts = adjudicate._verify_artifacts(
        _canonical(artifacts["process"]),
        _canonical(artifacts["graph"]),
        lock_path,
        root_id=ROOT_ID,
        volume_uuid=VOLUME_UUID,
    )
    assert process["exit_status"] == 0
    assert counts == adjudicate.EXPECTED_GRAPH_COUNTS
    assert len(graph["nodes"]) == 4


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("duplicate_graph_tuple", "resolved_graph_identity_collision"),
        ("missing_root", "resolved_graph_root_closure"),
        ("missing_dependency", "resolved_graph_dependency_closure"),
        ("outside_lock", "resolved_graph_not_in_lock"),
        ("wrong_storage", "process_receipt_invalid"),
        ("failed_process", "process_receipt_invalid"),
        ("noncanonical_graph", "graph_artifact_not_canonical"),
    ],
)
def test_adjudicator_fails_closed_on_graph_or_receipt_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str, reason: str
) -> None:
    _small_expected_counts(monkeypatch)
    artifacts, lock_path = _fixture(tmp_path)
    process = dict(artifacts["process"])
    graph = json.loads(json.dumps(artifacts["graph"]))
    if mutation == "duplicate_graph_tuple":
        graph["packages"][1]["name"] = graph["packages"][0]["name"]
    elif mutation == "missing_root":
        graph["root"] = "missing"
    elif mutation == "missing_dependency":
        graph["nodes"][0]["deps"] = [{"pkg": "missing", "dep_kinds": []}]
    elif mutation == "outside_lock":
        graph["packages"][1]["name"] = "not-in-lock"
    elif mutation == "wrong_storage":
        process["storage_root_id"] = "b" * 32
    elif mutation == "failed_process":
        process["exit_status"] = 1
    with pytest.raises(
        (adjudicate.AdjudicationError, preflight.MetadataPreflightError), match=reason
    ):
        adjudicate._verify_artifacts(
            _canonical(process),
            _canonical(graph) + (b" " if mutation == "noncanonical_graph" else b""),
            lock_path,
            root_id=ROOT_ID,
            volume_uuid=VOLUME_UUID,
        )


def test_adjudication_receipt_is_create_only_under_held_root_fd(tmp_path: Path) -> None:
    root = tmp_path / "BrainData"
    root.mkdir(mode=0o700)
    fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        payload = _canonical(
            {
                "schema": "brain-senses-agave-metadata-adjudication-v1",
                "schema_version": 1,
                "status": "verified_offline_metadata_graph",
            }
        )
        calls = 0

        def revalidate() -> None:
            nonlocal calls
            calls += 1

        expected_sha = hashlib.sha256(payload).hexdigest()
        assert (
            adjudicate.publish_receipt(
                fd, payload, device=os.fstat(fd).st_dev, revalidate=revalidate
            )
            == expected_sha
        )
        assert (root / adjudicate.ADJUDICATION_RELATIVE).read_bytes() == payload
        assert calls >= 3
        with pytest.raises(preflight.MetadataPreflightError, match="artifact_already_exists"):
            adjudicate.publish_receipt(
                fd, payload, device=os.fstat(fd).st_dev, revalidate=revalidate
            )
        assert (root / adjudicate.ADJUDICATION_RELATIVE).read_bytes() == payload
    finally:
        os.close(fd)


def test_cache_audit_accepts_only_exact_opaque_global_cache_change() -> None:
    before = {
        "path": ".global-cache",
        "kind": "file",
        "mode": 0o644,
        "size": 176_128,
        "sha256": "1" * 64,
    }
    after = {**before, "sha256": "2" * 64}
    extraction, bookkeeping = adjudicate._cache_evidence(
        [before], [after], expected_registry_crates=set()
    )
    assert extraction == []
    assert bookkeeping == {
        "path": ".global-cache",
        "classification": "opaque_cargo_bookkeeping",
        "before_sha256": "1" * 64,
        "after_sha256": "2" * 64,
        "mode": 0o644,
        "size": 176_128,
    }


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"mode": 0o600}, "global_cache_bookkeeping_mismatch"),
        ({"size": 176_129}, "global_cache_bookkeeping_mismatch"),
        ({"kind": "symlink"}, "global_cache_bookkeeping_mismatch"),
        ({"sha256": "1" * 64}, "global_cache_bookkeeping_mismatch"),
    ],
)
def test_cache_audit_rejects_changed_global_cache_identity(
    change: dict[str, Any], reason: str
) -> None:
    before = {
        "path": ".global-cache",
        "kind": "file",
        "mode": 0o644,
        "size": 176_128,
        "sha256": "1" * 64,
    }
    after = {**before, "sha256": "2" * 64, **change}
    with pytest.raises(adjudicate.AdjudicationError, match=reason):
        adjudicate._cache_evidence([before], [after], expected_registry_crates=set())


def test_cache_audit_rejects_any_other_delta() -> None:
    before = {
        "path": ".global-cache",
        "kind": "file",
        "mode": 0o644,
        "size": 176_128,
        "sha256": "1" * 64,
    }
    after = {**before, "sha256": "2" * 64}
    unexpected = {
        "path": "unexpected",
        "kind": "file",
        "mode": 0o600,
        "size": 1,
        "sha256": "3" * 64,
    }
    with pytest.raises(preflight.MetadataPreflightError, match="unexpected_cargo_cache_mutation"):
        adjudicate._cache_evidence([before], [after, unexpected], expected_registry_crates=set())
