from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO))

from tools import brain_senses_gecko_capture_only_v2_terminal_usage_v1 as terminal_v1  # noqa: E402
from tools import brain_senses_gecko_capture_only_v2_terminal_usage_v3 as bridge  # noqa: E402
from tools import brain_senses_gecko_shared_usage_v1 as shared_usage  # noqa: E402
from tools.brain_senses_external_writer_v1 import canonical_json  # noqa: E402

SERVICE_ROOT = "services/gecko-market-v1"
COLLECTIONS = ("v3-pilots", "v4-pilots", "capture-only-v1", "capture-only-v2", "host-health-v1")
DIRECT = ("v3", "v4", "v5")


def _historical_manifest(receipt: dict[str, Any]) -> dict[str, Any]:
    paths = receipt["global_root_census"]["paths"]
    # The frozen a078 census listed all direct roots in root_paths, but only v3
    # existed in the service directory at capture time. Keep directory presence
    # separate from the prospective direct-root census.
    service_entries = sorted({"v3"} | set(COLLECTIONS))
    collections: dict[str, Any] = {}
    roots: list[str] = [f"{SERVICE_ROOT}/{name}" for name in DIRECT]
    for collection in COLLECTIONS:
        prefix = f"{SERVICE_ROOT}/{collection}/"
        children = sorted(path.removeprefix(prefix) for path in paths if path.startswith(prefix))
        collections[collection] = {"present": True, "children": children}
        roots.extend(f"{prefix}{child}" for child in children)
    return {
        "service_root": SERVICE_ROOT,
        "service_entries": service_entries,
        "collections": collections,
        "direct_root_presence": {"v3": True, "v4": False, "v5": False},
        "root_paths": sorted(roots),
    }


def _snapshot_rows(
    roots: list[str], census_hash: str, *, attempted_root: str | None = None
) -> list[dict[str, Any]]:
    rows = []
    for root in roots:
        attempts = []
        if root == attempted_root:
            attempts = [
                {
                    "request_id": "new-sibling-attempt",
                    "decision_ns": 1_791_851_800_000_000_000,
                    "maximum_response_bytes": 32_000_000,
                    "response_bytes": 321,
                    "response_sha256": "a" * 64,
                }
            ]
        rows.append(
            shared_usage.make_verified_root_snapshot(
                root=root,
                status="verified_prefix" if attempts else "verified_none",
                root_inventory_sha256=census_hash,
                source_prefix_sha256=hashlib.sha256(root.encode()).hexdigest(),
                attempts=attempts,
            )
        )
    return rows


def _live(receipt: dict[str, Any], *, add: str | tuple[str, ...] | None = None):
    manifest = _historical_manifest(receipt)
    roots = list(manifest["root_paths"])
    additions = (add,) if isinstance(add, str) else add or ()
    for added_root in additions:
        coll = added_root.removeprefix(f"{SERVICE_ROOT}/").split("/")[0]
        ident = added_root.rsplit("/", 1)[-1]
        if coll in manifest["collections"]:
            manifest["collections"][coll]["children"].append(ident)
            manifest["collections"][coll]["children"].sort()
        roots.append(added_root)
        roots.sort()
    manifest["root_paths"] = roots
    census_hash = hashlib.sha256(canonical_json(manifest)).hexdigest()
    return roots, manifest, census_hash


def _root_metadata(receipt, roots, snapshots):
    pinned = set(receipt["global_root_census"]["paths"])
    snapshot_by_root = {snapshot["root"]: snapshot for snapshot in snapshots}
    metadata: dict[str, dict[str, Any]] = {}
    for root in sorted(set(roots) - pinned):
        capture_match = bridge.ADDED_ROOT_RE.fullmatch(root)
        if capture_match is not None:
            storage = dict(receipt["storage"])
            binding = {
                "root": root,
                "release_sha256": "a" * 64,
                "storage": storage,
            }
            binding["binding_sha256"] = hashlib.sha256(canonical_json(binding)).hexdigest()
            metadata[root] = {"binding": binding}
        health_match = bridge.ADDED_HEALTH_RE.fullmatch(root)
        if health_match is not None:
            release_id = health_match.group(1)
            capture_root = f"{SERVICE_ROOT}/capture-only-v2/{release_id}"
            if capture_root not in metadata:
                continue
            binding = metadata[capture_root]["binding"]
            storage = binding["storage"]
            metadata[root] = {
                "storage": dict(storage),
                "health": {
                    "release_id": release_id,
                    "release_sha256": binding["release_sha256"],
                    "binding_sha256": binding["binding_sha256"],
                    **storage,
                    "intent_count": len(snapshot_by_root[capture_root]["attempts"]),
                },
            }
    return metadata


def _validate(
    receipt: dict[str, Any],
    *,
    add: str | tuple[str, ...] | None = None,
    snapshots=None,
    root_metadata=None,
):
    roots, manifest, census_hash = _live(receipt, add=add)
    current_snapshots = snapshots or _snapshot_rows(roots, census_hash)
    metadata = root_metadata or _root_metadata(receipt, roots, current_snapshots)
    return bridge._validate_live_census(
        receipt=receipt,
        current_roots=roots,
        current_manifest=manifest,
        current_inventory_sha256=census_hash,
        after_roots=roots,
        after_manifest=manifest,
        after_inventory_sha256=census_hash,
        snapshots=current_snapshots,
        root_metadata=metadata,
        storage=receipt["storage"],
    ), (roots, manifest, census_hash, current_snapshots)


@pytest.fixture
def receipt() -> dict[str, Any]:
    return terminal_v1.load_pinned_receipt()


def test_exact_historical_nineteen_root_projection_matches_pin(receipt) -> None:
    paths, manifest, digest = _live(receipt)
    assert len(paths) == 19
    assert bridge._project_historical_manifest(manifest, paths) == manifest
    assert hashlib.sha256(canonical_json(manifest)).hexdigest() == bridge.HISTORICAL_CENSUS_SHA256
    assert _validate(receipt)[0] == (paths, bridge.HISTORICAL_CENSUS_SHA256)


def test_added_plan_only_sibling_is_allowed_and_independently_snapshotted(receipt) -> None:
    sibling = f"{SERVICE_ROOT}/capture-only-v2/0123456789abcdef0123456789abcdef"
    validated, (roots, _manifest, census_hash, snapshots) = _validate(receipt, add=sibling)
    assert sibling in roots
    assert validated[0] == receipt["global_root_census"]["paths"]
    assert len(snapshots) == 20
    assert all(row["root_inventory_sha256"] == census_hash for row in snapshots)


def test_added_attempted_sibling_remains_in_live_assessment(receipt) -> None:
    sibling = f"{SERVICE_ROOT}/capture-only-v2/0123456789abcdef0123456789abcdef"
    roots, _manifest, census_hash = _live(receipt, add=sibling)
    snapshots = _snapshot_rows(roots, census_hash, attempted_root=sibling)
    _validate(receipt, add=sibling, snapshots=snapshots)
    assessment = shared_usage.assess_usage(
        snapshots,
        expected_roots=roots,
        checked_ns=1_791_852_000_000_000_000,
        prospective_request_id="prospective-new-request",
    )
    assert assessment["rolling_30d"]["request_count_including_prospective"] == 2
    assert assessment["rolling_30d"]["charged_response_bytes_including_prospective"] == 321 + 16_384


def test_paired_health_sidecar_is_verified_but_never_double_debited(receipt) -> None:
    release_id = "0123456789abcdef0123456789abcdef"
    capture = f"{SERVICE_ROOT}/capture-only-v2/{release_id}"
    health = f"{SERVICE_ROOT}/host-health-v1/{release_id}"
    roots, _manifest, census_hash = _live(receipt, add=(capture, health))
    snapshots = _snapshot_rows(roots, census_hash, attempted_root=capture)
    _validate(receipt, add=(capture, health), snapshots=snapshots)
    health_snapshot = next(row for row in snapshots if row["root"] == health)
    assert health_snapshot["status"] == "verified_none"
    assert health_snapshot["attempts"] == []
    assessment = shared_usage.assess_usage(
        snapshots,
        expected_roots=roots,
        checked_ns=1_791_852_000_000_000_000,
        prospective_request_id="prospective-new-request",
    )
    assert assessment["rolling_30d"]["request_count_including_prospective"] == 2


def test_added_health_requires_its_exact_capture_sibling(receipt) -> None:
    health = f"{SERVICE_ROOT}/host-health-v1/0123456789abcdef0123456789abcdef"
    with pytest.raises(bridge.CensusBridgeError, match="added_health_root_missing_capture_sibling"):
        _validate(receipt, add=health)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("release_sha256", "c" * 64),
        ("binding_sha256", "d" * 64),
        ("root_id", "e" * 32),
        ("volume_uuid", "00000000-0000-0000-0000-000000000000"),
        ("device", 999),
        ("inode", 999),
    ],
)
def test_added_health_must_match_capture_binding_and_storage(
    receipt, field: str, value: Any
) -> None:
    release_id = "0123456789abcdef0123456789abcdef"
    capture = f"{SERVICE_ROOT}/capture-only-v2/{release_id}"
    health = f"{SERVICE_ROOT}/host-health-v1/{release_id}"
    roots, _manifest, census_hash = _live(receipt, add=(capture, health))
    snapshots = _snapshot_rows(roots, census_hash)
    metadata = _root_metadata(receipt, roots, snapshots)
    metadata[health]["health"][field] = value
    with pytest.raises(bridge.CensusBridgeError, match="added_health_capture_binding_mismatch"):
        _validate(receipt, add=(capture, health), snapshots=snapshots, root_metadata=metadata)


def test_added_health_intent_count_cannot_exceed_replayed_capture_attempts(receipt) -> None:
    release_id = "0123456789abcdef0123456789abcdef"
    capture = f"{SERVICE_ROOT}/capture-only-v2/{release_id}"
    health = f"{SERVICE_ROOT}/host-health-v1/{release_id}"
    roots, _manifest, census_hash = _live(receipt, add=(capture, health))
    snapshots = _snapshot_rows(roots, census_hash)
    metadata = _root_metadata(receipt, roots, snapshots)
    metadata[health]["health"]["intent_count"] = 1
    with pytest.raises(
        bridge.CensusBridgeError,
        match="added_health_intent_count_exceeds_capture_attempts",
    ):
        _validate(receipt, add=(capture, health), snapshots=snapshots, root_metadata=metadata)


def test_added_health_usage_projection_must_be_empty(receipt) -> None:
    release_id = "0123456789abcdef0123456789abcdef"
    capture = f"{SERVICE_ROOT}/capture-only-v2/{release_id}"
    health = f"{SERVICE_ROOT}/host-health-v1/{release_id}"
    roots, _manifest, census_hash = _live(receipt, add=(capture, health))
    snapshots = _snapshot_rows(roots, census_hash, attempted_root=health)
    with pytest.raises(bridge.CensusBridgeError, match="added_health_usage_must_be_empty"):
        _validate(receipt, add=(capture, health), snapshots=snapshots)


def test_health_coordinator_storage_binding_must_match_capture_binding(receipt) -> None:
    release_id = "0123456789abcdef0123456789abcdef"
    capture = f"{SERVICE_ROOT}/capture-only-v2/{release_id}"
    health = f"{SERVICE_ROOT}/host-health-v1/{release_id}"
    roots, _manifest, census_hash = _live(receipt, add=(capture, health))
    snapshots = _snapshot_rows(roots, census_hash)
    metadata = _root_metadata(receipt, roots, snapshots)
    metadata[health]["storage"]["inode"] += 1
    with pytest.raises(bridge.CensusBridgeError, match="added_health_capture_binding_mismatch"):
        _validate(receipt, add=(capture, health), snapshots=snapshots, root_metadata=metadata)


@pytest.mark.parametrize(
    "added",
    [
        f"{SERVICE_ROOT}/host-health-v1/0123456789abcdef0123456789abcdef",
        f"{SERVICE_ROOT}/capture-only-v2/not-a-valid-root-id",
        f"{SERVICE_ROOT}/capture-only-v1/0123456789abcdef0123456789abcdef",
        f"{SERVICE_ROOT}/v5/0123456789abcdef0123456789abcdef",
    ],
)
def test_unknown_or_malformed_added_root_is_rejected(receipt, added: str) -> None:
    with pytest.raises(bridge.CensusBridgeError):
        _validate(receipt, add=added)


def test_changed_old_root_or_health_prefix_is_rejected_by_exact_old_validator(
    monkeypatch: pytest.MonkeyPatch, receipt
) -> None:
    roots, manifest, census_hash = _live(receipt)
    snapshots = _snapshot_rows(roots, census_hash)
    metadata = _root_metadata(receipt, roots, snapshots)
    expected = copy.deepcopy(receipt)
    expected["health_stop"]["health_prefix_sha256"] = "f" * 64
    monkeypatch.setattr(terminal_v1, "build_receipt", lambda *_args, **_kwargs: expected)
    with pytest.raises(bridge.CensusBridgeError, match="a078_receipt_census_mismatch"):
        bridge.reconcile_a078_health_snapshot(
            object(),
            receipt=receipt,
            receipt_sha256=terminal_v1.RECEIPT_SHA256,
            storage=receipt["storage"],
            current_roots=roots,
            current_manifest=manifest,
            current_inventory_sha256=census_hash,
            after_roots=roots,
            after_manifest=manifest,
            after_inventory_sha256=census_hash,
            snapshots=snapshots,
            root_metadata=metadata,
        )


def test_removed_old_path_wrong_historical_hash_and_direct_presence_drift_reject(receipt) -> None:
    roots, manifest, digest = _live(receipt)
    removed = roots[1:]
    removed_manifest = copy.deepcopy(manifest)
    for collection, entry in removed_manifest["collections"].items():
        prefix = f"{SERVICE_ROOT}/{collection}/"
        entry["children"] = [child for child in entry["children"] if f"{prefix}{child}" in removed]
    removed_manifest["root_paths"] = removed
    removed_digest = hashlib.sha256(canonical_json(removed_manifest)).hexdigest()
    removed_snapshots = _snapshot_rows(removed, removed_digest)
    with pytest.raises(bridge.CensusBridgeError, match="historical_root_missing"):
        bridge._validate_live_census(
            receipt=receipt,
            current_roots=removed,
            current_manifest=removed_manifest,
            current_inventory_sha256=removed_digest,
            after_roots=removed,
            after_manifest=removed_manifest,
            after_inventory_sha256=removed_digest,
            snapshots=removed_snapshots,
            root_metadata=_root_metadata(receipt, removed, removed_snapshots),
            storage=receipt["storage"],
        )
    bad_receipt = copy.deepcopy(receipt)
    bad_receipt["global_root_census"]["sha256"] = "0" * 64
    with pytest.raises(bridge.CensusBridgeError, match="receipt_historical_census_hash_invalid"):
        _validate(bad_receipt)
    bad_manifest = copy.deepcopy(manifest)
    bad_manifest["direct_root_presence"]["v3"] = False
    bad_digest = hashlib.sha256(canonical_json(bad_manifest)).hexdigest()
    with pytest.raises(bridge.CensusBridgeError, match="historical_projection_mismatch"):
        bridge._validate_live_census(
            receipt=receipt,
            current_roots=roots,
            current_manifest=bad_manifest,
            current_inventory_sha256=bad_digest,
            after_roots=roots,
            after_manifest=bad_manifest,
            after_inventory_sha256=bad_digest,
            snapshots=_snapshot_rows(roots, bad_digest),
            root_metadata={},
            storage=receipt["storage"],
        )
    assert digest != bad_digest


def test_a078_descendant_and_census_race_reject(receipt) -> None:
    roots, manifest, digest = _live(receipt)
    descendant = f"{terminal_v1.ROOT}/child"
    descendant_roots = sorted([*roots, descendant])
    descendant_manifest = copy.deepcopy(manifest)
    descendant_manifest["root_paths"] = descendant_roots
    descendant_digest = hashlib.sha256(canonical_json(descendant_manifest)).hexdigest()
    with pytest.raises(bridge.CensusBridgeError, match="added_root_not_allowlisted_sibling"):
        bridge._validate_live_census(
            receipt=receipt,
            current_roots=descendant_roots,
            current_manifest=descendant_manifest,
            current_inventory_sha256=descendant_digest,
            after_roots=descendant_roots,
            after_manifest=descendant_manifest,
            after_inventory_sha256=descendant_digest,
            snapshots=_snapshot_rows(descendant_roots, descendant_digest),
            root_metadata={},
            storage=receipt["storage"],
        )
    with pytest.raises(bridge.CensusBridgeError, match="current_census_changed_during_replay"):
        bridge._validate_live_census(
            receipt=receipt,
            current_roots=roots,
            current_manifest=manifest,
            current_inventory_sha256=digest,
            after_roots=roots,
            after_manifest={
                **manifest,
                "service_entries": [*manifest["service_entries"], "changed"],
            },
            after_inventory_sha256=digest,
            snapshots=_snapshot_rows(roots, digest),
            root_metadata={},
            storage=receipt["storage"],
        )


def test_zero_intent_health_recovery_sidecar_has_no_usage_attempts(receipt) -> None:
    roots, manifest, digest = _live(receipt)
    snapshots = _snapshot_rows(roots, digest)
    result = bridge._make_terminal_sidecar(receipt, digest)
    assert result["root"] == terminal_v1.HEALTH_ROOT
    assert result["status"] == "verified_none"
    assert result["attempts"] == []
    assert all(
        snapshot["attempts"] == []
        for snapshot in snapshots
        if snapshot["root"] == terminal_v1.HEALTH_ROOT
    )


def test_reconciliation_validates_frozen_receipt_but_returns_live_census_sidecar(
    monkeypatch: pytest.MonkeyPatch, receipt
) -> None:
    roots, manifest, census_hash = _live(receipt)
    snapshots = _snapshot_rows(roots, census_hash)
    observed: dict[str, Any] = {}

    def build_expected(_writer: object, *, storage: dict[str, Any], root_inventory_sha256: str):
        observed["storage"] = storage
        observed["live_hash"] = root_inventory_sha256
        return copy.deepcopy(receipt)

    monkeypatch.setattr(terminal_v1, "build_receipt", build_expected)
    sidecar = bridge.reconcile_a078_health_snapshot(
        object(),
        receipt=receipt,
        receipt_sha256=terminal_v1.RECEIPT_SHA256,
        storage=receipt["storage"],
        current_roots=roots,
        current_manifest=manifest,
        current_inventory_sha256=census_hash,
        after_roots=roots,
        after_manifest=manifest,
        after_inventory_sha256=census_hash,
        snapshots=snapshots,
        root_metadata={},
    )

    assert observed == {"storage": receipt["storage"], "live_hash": census_hash}
    assert sidecar["root"] == terminal_v1.HEALTH_ROOT
    assert sidecar["root_inventory_sha256"] == census_hash
    assert sidecar["status"] == "verified_none"
    assert sidecar["attempts"] == []
