from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
for _path in (REPO / "src", REPO / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from tools import brain_senses_gecko_capture_only_v6 as capture_v6  # noqa: E402
from tools import brain_senses_gecko_capture_only_v6_bootstrap as bootstrap  # noqa: E402
from tools import brain_senses_gecko_capture_only_v6_host as host_v6  # noqa: E402
from tools import brain_senses_gecko_lane_policy_v3 as frozen_v3  # noqa: E402
from tools import brain_senses_gecko_lane_policy_v6 as policy_v6  # noqa: E402
from tools import brain_senses_gecko_rotating_scout_v1 as scout  # noqa: E402
from tools import brain_senses_gecko_shared_usage_v1 as shared_usage  # noqa: E402
from tools import brain_senses_gecko_usage_inventory_v3 as inventory_v3  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402


def _synthetic_policy(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    rows = [
        {
            "pool_id": f"pool-{index:03d}",
            "base_mint": f"base-{index:03d}",
            "quote_mint": "wsol",
            "rank_sha256": hashlib.sha256(
                f"{policy_v6.SOURCE_PIN}\0pool-{index:03d}".encode()
            ).hexdigest(),
        }
        for index in range(170)
    ]
    rows = scout.pool_order(rows, policy_v6.SOURCE_PIN)
    population_hash = scout.population_digest(rows)
    monkeypatch.setattr(policy_v6, "POPULATION_SHA256", population_hash)
    core = {
        "schema": policy_v6.SCHEMA,
        "schema_version": policy_v6.SCHEMA_VERSION,
        "policy_id": policy_v6.POLICY_ID,
        "source": {
            "role": "train",
            "source_pin": policy_v6.SOURCE_PIN,
            "source_sha256": {
                "train_enrollment": frozen_v3.TRAIN_ENROLLMENT_SHA256,
                "train_role_manifest": frozen_v3.TRAIN_MANIFEST_SHA256,
                "train_plan": frozen_v3.TRAIN_PLAN_SHA256,
                "population_selection": frozen_v3.TRAIN_SELECTION_SHA256,
            },
            "enrolled_train_count": 170,
            "population_sha256": population_hash,
            "ranking": "sha256(utf8(source_pin + NUL + pool_id)),ascending",
            "protected_screen_fields": ["pool_id", "base_mint"],
            "quote_mint_rule": "preserve_and_match_pinned_pool_metadata_only",
            "quote_token_features": False,
        },
        "population": rows,
        "sampling_epoch": policy_v6.sampling_epoch_contract(),
        "protocol": policy_v6._protocol(),
        "capacity": {
            "scope": "v6_local_gecko_only;no_global_provider_census_claim",
            "maximum_intents_per_utc_day": policy_v6.MAX_INTENTS_PER_UTC_DAY,
            "maximum_intents_per_rolling_30d": policy_v6.MAX_INTENTS_PER_ROLLING_30D,
            "maximum_raw_bytes_per_utc_day": policy_v6.MAX_RAW_BYTES_PER_UTC_DAY,
            "maximum_raw_bytes_per_rolling_30d": policy_v6.MAX_RAW_BYTES_PER_ROLLING_30D,
            "maximum_physical_reserve_per_rolling_30d": (
                policy_v6.MAX_PHYSICAL_RESERVE_PER_ROLLING_30D
            ),
            "physical_reserve_per_intent": policy_v6.PHYSICAL_RESERVE_PER_INTENT,
            "resident_allocation_ceiling_bytes": policy_v6.MAX_RESIDENT_BYTES,
            "minimum_free_bytes": policy_v6.MIN_FREE_BYTES,
            "conservative_prior_gecko_usage_offset_required": True,
            "prior_usage_claim": "known_gecko_attempts_only;not_global_account_usage",
        },
        "activation_authorized": False,
        "provider_io": False,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
    }
    policy = {**core, "policy_sha256": bootstrap.sha256(policy_v6.canonical(core))}
    policy_v6.verify_policy_hash(policy)
    return policy


def _receipt() -> tuple[bytes, dict[str, Any]]:
    raw = (host_v6.REPO_ROOT / host_v6.PRIOR_USAGE_CENSUS_PATH).read_bytes()
    return raw, json.loads(raw.decode())


def _inventory_from_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    by_root: dict[str, list[dict[str, Any]]] = {
        root: [] for root in receipt["root_inventory"]["root_paths"]
    }
    for row in receipt["attempts"]:
        attempt = {key: value for key, value in row.items() if key != "root"}
        by_root[row["root"]].append(attempt)
    snapshots = [
        {
            "root": root,
            "snapshot_sha256": digest,
            "attempts": by_root[root],
        }
        for root, digest in receipt["snapshot_sha256_by_root"].items()
    ]
    return {
        "schema": "brain-senses-gecko-shared-usage-inventory-v1",
        "root_inventory": receipt["root_inventory"],
        "root_inventory_sha256": receipt["root_inventory_sha256"],
        "snapshots": snapshots,
        "provider_io": False,
    }


def _full_v3_inventory_from_receipt(
    receipt: dict[str, Any], *, candidate_release_id: str, candidate_present: bool
) -> dict[str, Any]:
    attempts_by_root = {root: [] for root in receipt["root_inventory"]["root_paths"]}
    for row in receipt["attempts"]:
        attempts_by_root[row["root"]].append(
            {key: value for key, value in row.items() if key != "root"}
        )
    v6_ids = [inventory_v3.LEGACY_V6_RELEASE_ID]
    if candidate_present:
        v6_ids.append(candidate_release_id)
    v6_ids.sort()
    roots = sorted(
        [
            *receipt["root_inventory"]["root_paths"],
            *(f"{inventory_v3.CAPTURE_V6_ROOT}/{release_id}" for release_id in v6_ids),
        ]
    )
    manifest = {
        "schema": "brain-senses-gecko-full-current-inventory-v3",
        "schema_version": 3,
        "service_root": inventory_v3.SERVICE_ROOT,
        "service_entries": sorted(
            set(receipt["root_inventory"]["service_entries"]) | {inventory_v3.CAPTURE_V6_ENTRY}
        ),
        "historical_v2_root_inventory_sha256": receipt["root_inventory_sha256"],
        "capture_only_v6_release_ids": v6_ids,
        "root_paths": roots,
    }
    full_hash = shared_usage.sha256(shared_usage.canonical_json(manifest))
    snapshots = []
    for root in roots:
        if root.startswith(f"{inventory_v3.CAPTURE_V6_ROOT}/"):
            rows = []
            status = "verified_none"
            prefix = shared_usage.sha256(root.encode())
        else:
            rows = attempts_by_root[root]
            status = "verified_prefix" if rows else "verified_none"
            prefix = shared_usage.sha256(root.encode())
        snapshots.append(
            shared_usage.make_verified_root_snapshot(
                root=root,
                status=status,
                root_inventory_sha256=full_hash,
                source_prefix_sha256=prefix,
                attempts=rows,
            )
        )
    return {
        "schema": "brain-senses-gecko-shared-usage-inventory-v3",
        "root_inventory": manifest,
        "root_inventory_sha256": full_hash,
        "snapshots": snapshots,
        "provider_io": False,
        "candidate_release_present": candidate_present,
        "historical_attempt_count": receipt["attempt_count"],
    }


class _Guard:
    def __init__(self, mount_fd: int, data_path: Path, root_id: str = "a" * 32) -> None:
        self.mount_fd = mount_fd
        self.config = SimpleNamespace(data_root="BrainData")
        self.root_id = root_id
        self.volume_uuid = "12345678-1234-1234-1234-123456789abc"
        self.data_path = data_path
        self.expected = os.stat(data_path)
        self.available = 100 * 1024**3
        self.calls = 0
        self.detach_at: int | None = None

    def revalidate(self, *, min_free_bytes: int) -> Any:
        self.calls += 1
        if self.detach_at == self.calls:
            old = self.data_path.with_name("BrainData-held")
            os.rename(self.data_path, old)
            self.data_path.mkdir(mode=0o700)
            self.detach_at = None
        current = os.stat(self.data_path)
        if (current.st_dev, current.st_ino) != (self.expected.st_dev, self.expected.st_ino):
            raise RuntimeError("detached")
        if self.available < min_free_bytes:
            raise RuntimeError("capacity")
        return SimpleNamespace(available_bytes=self.available)


def _install_test_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    policy: dict[str, Any],
    receipt_raw: bytes,
    receipt: dict[str, Any],
) -> None:
    source_map = {path: "b" * 64 for path in host_v6.SOURCE_PATHS}
    source_map[host_v6.PRIOR_USAGE_CENSUS_PATH] = bootstrap.sha256(receipt_raw)
    source_map["tools/brain_senses_gecko_rotating_scout_v1.py"] = "c" * 64
    source_map[host_v6.PLIST_PATH] = "d" * 64
    core = {
        "schema": host_v6.SOURCE_MANIFEST_SCHEMA,
        "schema_version": 6,
        "source_commit": "e" * 40,
        "source_paths": list(host_v6.SOURCE_PATHS),
        "source_map": source_map,
    }
    manifest = {**core, "source_manifest_sha256": bootstrap.sha256(host_v6.canonical(core))}
    monkeypatch.setattr(host_v6, "compile_source_manifest", lambda _root: manifest)
    monkeypatch.setattr(host_v6, "verify_source_manifest", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(host_v6, "load_policy_from_writer", lambda _writer: policy)
    rows = [
        {
            "intent_ns": item["decision_ns"],
            "raw_bytes_upper_bound": item["maximum_response_bytes"],
            "physical_bytes_upper_bound": policy_v6.PHYSICAL_RESERVE_PER_INTENT,
        }
        for item in receipt["attempts"]
    ]
    monkeypatch.setattr(host_v6, "verify_prior_usage_census", lambda *_a, **_kw: rows)
    monkeypatch.setattr(
        host_v6,
        "_read_repo_file",
        lambda _root, path, **_kw: (
            receipt_raw if path == host_v6.PRIOR_USAGE_CENSUS_PATH else b"test-source"
        ),
    )
    monkeypatch.setattr(
        host_v6,
        "SOURCE_INPUTS",
        {
            f"input-{index}": (
                f"test-inputs/{index}.bin",
                bootstrap.sha256(f"input-{index}".encode()),
            )
            for index in range(4)
        },
    )
    monkeypatch.setattr(
        host_v6,
        "verify_activation_record",
        host_v6.verify_activation_record,
    )
    monkeypatch.setattr(
        bootstrap.usage_inventory_v3,
        "build_usage_inventory_v3",
        lambda _writer, *, candidate_release_id, require_candidate_absent=False, **_kwargs: (
            _full_v3_inventory_from_receipt(
                receipt,
                candidate_release_id=candidate_release_id,
                candidate_present=not require_candidate_absent,
            )
        ),
    )


def _prepare_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, fault_hook: Any = None
) -> tuple[dict[str, Any], Path, _Guard, DescriptorWriter, DescriptorWriter]:
    mount = tmp_path / "mount"
    data_path = mount / "BrainData"
    data_path.mkdir(parents=True, mode=0o700)
    mount_fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
    data_fd = os.open(data_path, os.O_RDONLY | os.O_DIRECTORY)
    guard = _Guard(mount_fd, data_path)
    data_writer = DescriptorWriter(data_fd)
    lane = capture_v6.CrossReleaseLaneLeaseV6(data_writer)
    receipt_raw, receipt = _receipt()
    policy = _synthetic_policy(monkeypatch)
    _install_test_dependencies(
        monkeypatch,
        policy=policy,
        receipt_raw=receipt_raw,
        receipt=receipt,
    )
    for index in range(4):
        data_writer.create(f"test-inputs/{index}.bin", f"input-{index}".encode())
    return (
        {
            "guard": guard,
            "lane": lane,
            "wall": receipt["observed_ns"] + 1_000_000_000,
            "receipt": receipt,
        },
        data_path,
        guard,
        data_writer,
        DescriptorWriter(data_fd),
    )


def _run(
    args: dict[str, Any],
    writer: DescriptorWriter,
    *,
    repo_root: Path,
    fault_hook: Any = None,
) -> dict[str, Any]:
    return bootstrap.prepare_v6_release(
        writer,
        guard=args["guard"],
        lane_lease=args["lane"],
        active_gecko_labels=[],
        wall_ns=lambda: args["wall"],
        repo_root=repo_root,
        fault_hook=fault_hook,
    )


def test_release_bootstrap_replays_62_attempt_census_and_separates_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, data_path, guard, lane_writer, writer = _prepare_fixture(tmp_path, monkeypatch)
    try:
        result = _run(args, writer, repo_root=tmp_path)
        service = data_path / result["service_root"]
        epoch = json.loads((service / capture_v6.EPOCH_PATH).read_bytes())
        activation = json.loads((data_path / host_v6.ACTIVATION_PATH).read_bytes())
        assert result["attempt_count"] == 62
        assert result["raw_bytes_upper_bound"] == 1_015_808
        assert result["physical_bytes_upper_bound"] == 5_079_040
        assert result["anchor_close_ns"] > args["wall"]
        assert epoch["storage"]["data_root"]["inode"] != epoch["storage"]["service_root"]["inode"]
        assert epoch["storage"]["data_root"]["device"] == epoch["storage"]["service_root"]["device"]
        assert activation["activation_id"] == result["activation_id"]
        assert activation["capture_authorized"] is True
        assert activation["market_source_qualified"] is False
        assert "activation.json" not in {p.name for p in service.rglob("*")}
    finally:
        lane_writer.close()
        writer.close()
        os.close(guard.mount_fd)


def test_crash_before_activation_leaves_inert_identical_prefix_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, data_path, guard, lane_writer, writer = _prepare_fixture(tmp_path, monkeypatch)
    try:
        seen: list[str] = []

        def crash(stage: str) -> None:
            seen.append(stage)
            if stage == "before_activation":
                assert not writer.exists(host_v6.ACTIVATION_PATH)
                raise RuntimeError("synthetic crash")

        with pytest.raises(RuntimeError, match="synthetic crash"):
            _run(args, writer, repo_root=tmp_path, fault_hook=crash)
        assert "before_activation" in seen
        assert not writer.exists(host_v6.ACTIVATION_PATH)
        result = _run(args, writer, repo_root=tmp_path)
        assert writer.exists(host_v6.ACTIVATION_PATH)
        assert result["provider_io"] is False
    finally:
        lane_writer.close()
        writer.close()
        os.close(guard.mount_fd)


def test_activation_is_last_durable_artifact_and_create_only_conflict_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, _data_path, guard, lane_writer, writer = _prepare_fixture(tmp_path, monkeypatch)
    try:
        stages: list[str] = []

        def inspect(stage: str) -> None:
            stages.append(stage)
            if stage == "before_activation":
                assert not writer.exists(host_v6.ACTIVATION_PATH)
                assert writer.exists(host_v6.SOURCE_MANIFEST_PATH)

        _run(args, writer, repo_root=tmp_path, fault_hook=inspect)
        assert stages[-2:] == ["before_activation", "after_activation"]
        prior = writer.read_bytes(host_v6.ACTIVATION_PATH)
        writer.atomic_write(host_v6.ACTIVATION_PATH, b"{}\n")
        with pytest.raises(bootstrap.BootstrapError, match="activation_conflict"):
            _run(args, writer, repo_root=tmp_path)
        assert writer.read_bytes(host_v6.ACTIVATION_PATH) == b"{}\n"
        assert prior != b"{}\n"
    finally:
        lane_writer.close()
        writer.close()
        os.close(guard.mount_fd)


def test_active_competing_label_and_low_space_fail_before_service_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, data_path, guard, lane_writer, writer = _prepare_fixture(tmp_path, monkeypatch)
    try:
        with pytest.raises(bootstrap.BootstrapError, match="competing_gecko_label"):
            bootstrap.prepare_v6_release(
                writer,
                guard=guard,
                lane_lease=args["lane"],
                active_gecko_labels=["ai.memecoin.brain.senses.gecko-old"],
                wall_ns=lambda: args["wall"],
                repo_root=tmp_path,
            )
        guard.available = policy_v6.MIN_FREE_BYTES - 1
        with pytest.raises(bootstrap.BootstrapError, match="storage_preflight|free_space_floor"):
            _run(args, writer, repo_root=tmp_path)
        assert not (data_path / capture_v6.ROOT_PREFIX).exists()
        assert not writer.exists(host_v6.ACTIVATION_PATH)
    finally:
        lane_writer.close()
        writer.close()
        os.close(guard.mount_fd)


def test_detach_during_create_has_no_mountpoint_underlay_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, data_path, guard, lane_writer, writer = _prepare_fixture(tmp_path, monkeypatch)
    try:
        guard.detach_at = guard.calls + 15
        with pytest.raises(bootstrap.BootstrapError):
            _run(args, writer, repo_root=tmp_path)
        underlay = data_path
        assert underlay.is_dir()
        assert list(underlay.iterdir()) == []
        assert not (underlay / host_v6.ACTIVATION_PATH).exists()
        # The held descriptor may contain an inert partial prefix; it never redirects
        # to the replacement path and cannot confer authority without activation.
        held = data_path.with_name("BrainData-held")
        assert held.exists()
        assert not (held / host_v6.ACTIVATION_PATH).exists()
    finally:
        lane_writer.close()
        writer.close()
        os.close(guard.mount_fd)


def test_inventory_attempt_or_snapshot_mutation_is_rejected() -> None:
    _raw, receipt = _receipt()
    inventory = _inventory_from_receipt(receipt)
    inventory_v3._historical_projection_matches(inventory, receipt)
    bad = json.loads(json.dumps(inventory))
    bad["snapshots"][0]["attempts"].append({"request_id": "x"})
    with pytest.raises(inventory_v3.InventoryV3Error, match="historical_attempt_mismatch"):
        inventory_v3._historical_projection_matches(bad, receipt)
    bad = json.loads(json.dumps(inventory))
    bad["snapshots"][0]["snapshot_sha256"] = "0" * 64
    with pytest.raises(inventory_v3.InventoryV3Error, match="historical_snapshot_hash_mismatch"):
        inventory_v3._historical_projection_matches(bad, receipt)
