from __future__ import annotations

import plistlib
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO))

from tools import brain_senses_gecko_capture_only_v2_host as v2  # noqa: E402
from tools import brain_senses_gecko_capture_only_v4_host as v4  # noqa: E402
from tools import brain_senses_gecko_usage_inventory_v1 as inventory_v1  # noqa: E402
from tools import brain_senses_gecko_usage_inventory_v2 as inventory_v2  # noqa: E402


def test_v4_dependency_binding_is_scoped_and_restored_on_error() -> None:
    previous_paths = v2.RELEASE_CODE_PATHS
    previous_inventory = v2.usage_inventory
    with pytest.raises(RuntimeError):
        with v4._v4_source_scope():
            assert v2.usage_inventory is inventory_v2
            assert "tools/brain_senses_gecko_usage_inventory_v2.py" in v2.RELEASE_CODE_PATHS
            raise RuntimeError("forced scope exit")
    assert v2.RELEASE_CODE_PATHS is previous_paths
    assert v2.usage_inventory is previous_inventory is inventory_v1


def test_v4_launchd_binding_names_only_new_host_and_watchdog() -> None:
    release_id = "a" * 32
    plist = plistlib.loads(v4.render_launchd_plist(release_id))
    assert plist["Label"] == f"{v4.LABEL_PREFIX}{release_id}"
    assert (
        str(REPO / "tools/brain_senses_gecko_capture_only_v4_host.py") in plist["ProgramArguments"]
    )
    assert v4.LABEL_PREFIX.endswith("4-")
    assert v4.WATCHDOG_LABEL_PREFIX.endswith("v2-")
