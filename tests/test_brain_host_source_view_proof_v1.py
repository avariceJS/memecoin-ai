from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests import test_brain_host_recovery_frontier as frontier_fixtures
from tests import test_brain_host_recovery_set as set_fixtures
from tools.brain_host_recovery_set import verify_set
from tools.brain_host_source_view_proof_v1 import (
    SCHEMA,
    SourceViewProofError,
    _verify_time_order,
    capture_closure_counts,
    capture_inventory_sha256,
    synthetic_root_locator_sha256,
    verify_synthetic_source_view_proof,
    verify_synthetic_sqlite_source_family_binding,
    verify_synthetic_tree_source_binding,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter, sha256


def _proof(tmp_path: Path):
    writer, _archive, spec, _targets, _fresh_reader, source_roots = frontier_fixtures._fixture(
        tmp_path
    )
    verified = verify_set(
        writer,
        spec["set_id"],
        expected_volume_uuid=set_fixtures.UUID,
        expected_root_id=set_fixtures.ROOT_ID,
        revalidate_identity=set_fixtures._identity,
    )
    index = json.loads(
        writer.read_bytes(
            f"host-recovery/recovery-sets-v1/{verified['set_id']}/INDEX.json",
            max_bytes=4 * 1024 * 1024,
        )
    )
    verified_members = index["members"]
    view_id = "synthetic-held-view-0001"
    ledger = []
    intervals = [member["interval_ns"] for member in verified_members]
    capture_started = min(interval[0] for interval in intervals)
    capture_completed = max(interval[1] for interval in intervals)
    source_locator_hashes = {}
    for member in sorted(verified_members, key=lambda row: row["role"]):
        role = member["role"]
        counts = capture_closure_counts(writer, member)
        if member["format"] == "tree-custody-v3":
            source_binding = verify_synthetic_tree_source_binding(
                writer, member, source_roots[role]
            )
            source_locator_hashes[role] = synthetic_root_locator_sha256(
                role,
                "synthetic-source-volume",
                "8" * 32,
                source_binding["root_identity"],
            )
        else:
            source_locator_hashes[role] = sha256(f"synthetic-root:{role}".encode())
        ledger.append(
            {
                "role": role,
                "root_locator_sha256": source_locator_hashes[role],
                "inventory_sha256": capture_inventory_sha256(writer, member),
                **counts,
                "disposition": "opaque_protected" if role == "protected_d43" else "included",
            }
        )
    by_role = {row["role"]: row for row in ledger}
    bindings = [
        {
            **{
                key: item[key]
                for key in (
                    "role",
                    "format",
                    "artifact_id",
                    "manifest_sha256",
                    "complete_sha256",
                    "scope",
                )
            },
            "source_view_id": view_id,
            "root_locator_sha256": by_role[item["role"]]["root_locator_sha256"],
            "inventory_sha256": by_role[item["role"]]["inventory_sha256"],
        }
        for item in sorted(verified_members, key=lambda row: row["role"])
    ]
    proof = {
        "schema": SCHEMA,
        "scenario": "synthetic",
        "set_id": verified["set_id"],
        "set_index_sha256": verified["index_sha256"],
        "source_view": {
            "kind": "synthetic_readonly_view_fixture",
            "volume_uuid": "synthetic-source-volume",
            "root_id": "8" * 32,
            "view_id": view_id,
            "binding_method": "synthetic_held_descriptor_fixture",
        },
        "times_ns": {
            "snapshot_event": capture_started - 40,
            "view_opened": capture_started - 30,
            "capture_started": capture_started,
            "capture_completed": capture_completed,
            "artifact_verification_completed": capture_completed + 10,
            "proof_sealed": capture_completed + 20,
            "view_released": capture_completed + 30,
        },
        "adapter_sha256": sha256(b"synthetic adapter"),
        "capture_tool_sha256": sha256(b"synthetic capture tool"),
        "policy_sha256": {
            "selection": sha256(b"selection policy"),
            "exclusion": sha256(b"exclusion policy"),
            "metadata": sha256(b"metadata policy"),
        },
        "closure_ledger": ledger,
        "member_bindings": bindings,
        "wallets_disposition": "synthetic_fixture_absent",
        "metadata_disposition": {
            "scope": "synthetic_bytes_and_topology_only",
            "xattrs": "not_preserved",
            "acls": "not_preserved",
            "flags": "not_preserved",
            "uid_gid": "restored_by_mapping",
        },
    }
    return writer, proof, source_roots, verified_members


def _verify(writer: DescriptorWriter, proof: dict, source_roots: dict[str, Path]):
    tree_roots = {
        role: source_roots[role]
        for role in ("owner_work", "research_work", "private_config", "launchagent_definitions")
    }
    return verify_synthetic_source_view_proof(
        writer,
        proof,
        synthetic_source_roots=tree_roots,
        expected_volume_uuid=set_fixtures.UUID,
        expected_root_id=set_fixtures.ROOT_ID,
        expected_source_volume_uuid="synthetic-source-volume",
        expected_source_root_id="8" * 32,
        revalidate_identity=set_fixtures._identity,
    )


def test_source_view_proof_binds_all_verified_members_but_never_qualifies_restore(
    tmp_path: Path,
) -> None:
    writer, proof, source_roots, verified_members = _proof(tmp_path)
    try:
        result = _verify(writer, proof, source_roots)
        assert result["member_count"] == 6
        assert result["declared_unknown_count"] == 1
        assert result["source_root_closure_verified"] is False
        assert result["application_recovery_qualified"] is False
        assert result["factory_reset_ready"] is False
        assert result["synthetic_tree_source_bindings_verified"] == 4
        assert result["synthetic_sqlite_source_family_verified"] is True
    finally:
        writer.close()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("index", "set_index_hash_mismatch"),
        ("member", "source_member_binding_artifact_mismatch"),
        ("inventory", "closure_ledger_capture_inventory_mismatch"),
        ("source_identity", "source_view_identity_invalid"),
        ("root_locator", "synthetic_source_root_locator_mismatch"),
        ("counts", "closure_ledger_capture_counts_mismatch"),
        ("intervals", "capture_time_does_not_cover_member_intervals"),
        ("unknown", "closure_ledger_capture_counts_mismatch"),
        ("wallets", "source_view_wallets_unresolved"),
        ("real", "real_source_view_verifier_unavailable"),
        ("order", "source_view_time_order_invalid"),
    ],
)
def test_source_view_proof_rejects_unbound_or_unresolved_claims(
    tmp_path: Path, mutation: str, message: str
) -> None:
    writer, proof, source_roots, _verified_members = _proof(tmp_path)
    try:
        bad = copy.deepcopy(proof)
        if mutation == "index":
            bad["set_index_sha256"] = "0" * 64
        elif mutation == "member":
            bad["member_bindings"][0]["manifest_sha256"] = "0" * 64
        elif mutation == "unknown":
            row = bad["closure_ledger"][0]
            row["unknown_count"] += 1
        elif mutation == "inventory":
            bad["closure_ledger"][0]["inventory_sha256"] = "0" * 64
        elif mutation == "source_identity":
            bad["source_view"]["volume_uuid"] = "unbound-volume"
        elif mutation == "root_locator":
            row = next(row for row in bad["closure_ledger"] if row["role"] == "research_work")
            row["root_locator_sha256"] = "0" * 64
        elif mutation == "counts":
            bad["closure_ledger"][0]["included_count"] += 1
        elif mutation == "intervals":
            bad["times_ns"]["capture_started"] += 1
        elif mutation == "wallets":
            bad["wallets_disposition"] = "unknown"
        elif mutation == "real":
            bad["scenario"] = "real"
        elif mutation == "order":
            bad["times_ns"]["view_released"] = 55
        with pytest.raises(SourceViewProofError, match=message):
            _verify(writer, bad, source_roots)
    finally:
        writer.close()


def test_synthetic_descriptor_source_reader_rejects_changed_bytes(tmp_path: Path) -> None:
    writer, _proof_value, source_roots, verified_members = _proof(tmp_path)
    try:
        member = next(item for item in verified_members if item["role"] == "research_work")
        source_file = source_roots["research_work"] / "research.txt"
        source_file.write_text("changed synthetic bytes", encoding="utf-8")
        with pytest.raises(
            SourceViewProofError,
            match="synthetic_source_(entry_metadata|file_hash)_mismatch",
        ):
            verify_synthetic_tree_source_binding(writer, member, source_roots["research_work"])
    finally:
        writer.close()


def test_synthetic_sqlite_family_reader_rejects_source_metadata_drift(tmp_path: Path) -> None:
    writer, _proof_value, source_roots, verified_members = _proof(tmp_path)
    try:
        member = next(item for item in verified_members if item["role"] == "sqlite_backup")
        source_db = source_roots["owner_work"] / "brain-b13/operator04/operator.sqlite"
        os.utime(source_db, None)
        with pytest.raises(SourceViewProofError, match="synthetic_sqlite_family_mismatch"):
            verify_synthetic_sqlite_source_family_binding(
                writer, member, source_roots["owner_work"]
            )
    finally:
        writer.close()


def test_synthetic_sqlite_family_reader_rejects_replaced_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer, _proof_value, source_roots, verified_members = _proof(tmp_path)
    try:
        member = next(item for item in verified_members if item["role"] == "sqlite_backup")
        operator_dir = source_roots["owner_work"] / "brain-b13/operator04"
        detached_dir = operator_dir.with_name("operator04-detached")
        original_stat = os.stat
        replaced = False

        def replace_parent_after_source_stat(path: object, *args: object, **kwargs: object):
            nonlocal replaced
            result = original_stat(path, *args, **kwargs)
            if (
                not replaced
                and path == "operator.sqlite"
                and kwargs.get("dir_fd") is not None
                and kwargs.get("follow_symlinks") is False
            ):
                operator_dir.rename(detached_dir)
                operator_dir.mkdir()
                replaced = True
            return result

        monkeypatch.setattr(os, "stat", replace_parent_after_source_stat)
        with pytest.raises(SourceViewProofError, match="synthetic_sqlite_parent_chain_changed"):
            verify_synthetic_sqlite_source_family_binding(
                writer, member, source_roots["owner_work"]
            )
        assert replaced
    finally:
        writer.close()


@given(st.permutations((10, 20, 30, 40, 50, 60, 70)))
def test_source_view_timeline_accepts_only_strictly_increasing_event_order(
    times: tuple[int, ...],
) -> None:
    keys = (
        "snapshot_event",
        "view_opened",
        "capture_started",
        "capture_completed",
        "artifact_verification_completed",
        "proof_sealed",
        "view_released",
    )
    value = dict(zip(keys, times, strict=True))
    if tuple(times) == tuple(sorted(times)):
        assert _verify_time_order(value) == list(times)
    else:
        with pytest.raises(SourceViewProofError, match="source_view_time_order_invalid"):
            _verify_time_order(value)
