from __future__ import annotations

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
from tools import brain_senses_gecko_capture_only_v2_terminal_usage_v4 as bridge  # noqa: E402
from tools import brain_senses_gecko_shared_usage_v1 as shared_usage  # noqa: E402
from tools.brain_senses_external_writer_v1 import canonical_json  # noqa: E402

SERVICE_ROOT = "services/gecko-market-v1"
COLLECTIONS = (
    "v3-pilots",
    "v4-pilots",
    "capture-only-v1",
    "capture-only-v2",
    "host-health-v1",
)


def _current_manifest(receipt: dict[str, Any], *, orphan_watchdog: bool = False):
    paths = receipt["global_root_census"]["paths"]
    release_id = "c" * 32
    watchdog = f"{SERVICE_ROOT}/canary-watchdog-v1/{release_id}"
    collections: dict[str, Any] = {}
    roots = [f"{SERVICE_ROOT}/{name}" for name in ("v3", "v4", "v5")]
    for name in COLLECTIONS:
        prefix = f"{SERVICE_ROOT}/{name}/"
        children = sorted(path.removeprefix(prefix) for path in paths if path.startswith(prefix))
        collections[name] = {"present": True, "children": children}
        roots.extend(f"{prefix}{child}" for child in children)
    collections["canary-watchdog-v1"] = {"present": True, "children": [release_id]}
    roots.append(watchdog)
    if not orphan_watchdog:
        capture = f"{SERVICE_ROOT}/capture-only-v2/{release_id}"
        collections["capture-only-v2"]["children"].append(release_id)
        collections["capture-only-v2"]["children"].sort()
        roots.append(capture)
    roots.sort()
    manifest = {
        "service_root": SERVICE_ROOT,
        "service_entries": sorted({"v3", *COLLECTIONS, "canary-watchdog-v1"}),
        "collections": collections,
        "direct_root_presence": {"v3": True, "v4": False, "v5": False},
        "root_paths": roots,
    }
    return roots, manifest, hashlib.sha256(canonical_json(manifest)).hexdigest(), watchdog


@pytest.fixture
def receipt() -> dict[str, Any]:
    return terminal_v1.load_pinned_receipt()


def test_historical_projection_removes_only_postreceipt_watchdog_collection(receipt) -> None:
    roots, manifest, _digest, _watchdog = _current_manifest(receipt)
    projected = bridge._project_historical_manifest(
        manifest, receipt["global_root_census"]["paths"]
    )
    assert "canary-watchdog-v1" not in projected["service_entries"]
    assert "canary-watchdog-v1" not in projected["collections"]
    assert projected["root_paths"] == receipt["global_root_census"]["paths"]
    assert hashlib.sha256(canonical_json(projected)).hexdigest() == bridge.HISTORICAL_CENSUS_SHA256
    assert len(roots) == 21


def test_orphan_watchdog_root_is_rejected_even_if_it_has_empty_usage(receipt) -> None:
    roots, manifest, digest, watchdog = _current_manifest(receipt, orphan_watchdog=True)
    snapshots = [
        shared_usage.make_verified_root_snapshot(
            root=root,
            status="verified_none",
            root_inventory_sha256=digest,
            source_prefix_sha256=hashlib.sha256(root.encode()).hexdigest(),
            attempts=[],
        )
        for root in roots
    ]
    assert watchdog in roots
    with pytest.raises(
        bridge.CensusBridgeError, match="added_watchdog_root_missing_capture_sibling"
    ):
        bridge._validate_live_census(
            receipt=receipt,
            current_roots=roots,
            current_manifest=manifest,
            current_inventory_sha256=digest,
            after_roots=roots,
            after_manifest=manifest,
            after_inventory_sha256=digest,
            snapshots=snapshots,
            root_metadata={},
            storage=receipt["storage"],
        )
