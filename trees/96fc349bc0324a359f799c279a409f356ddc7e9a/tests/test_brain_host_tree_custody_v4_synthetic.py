from __future__ import annotations

import pytest

from tests.test_brain_host_tree_custody_v4 import _entry, _plan, _roots
from tools.brain_host_tree_custody_v4_synthetic import (
    SyntheticCaptureError,
    rehearse_synthetic_capture,
)


def _empty_metadata_values(plan):
    rows = [(f"root:{row['root_key']}", row) for row in plan["root_roster"]]
    rows.extend(
        (f"entry:{row['root_key']}:{row['path']}", row)
        for row in plan["entries"]
        if row["disposition"] == "included_opaque"
    )
    return {key: {"xattrs": {}, "acl_bytes": None, "flags_value": None} for key, _row in rows}


def _fixture_plan(*, roots=None):
    entry = _entry("root-alpha", "payload.bin", inode=100)
    entry["size"] = 10
    return _plan([entry], roots=roots)


def test_synthetic_capture_writes_reads_back_and_marks_fixture_only() -> None:
    plan = _fixture_plan()
    file_group = plan["entries"][0]["hardlink_group_id"]
    receipt = rehearse_synthetic_capture(
        plan,
        file_payloads={file_group: b"0123456789"},
        metadata_values=_empty_metadata_values(plan),
    )
    assert receipt["status"] == "synthetic_artifact_regular_files_verified"
    assert receipt["object_count"] == 1
    assert receipt["metadata_member_count"] == 4
    assert receipt["source_view_qualified"] is False
    assert receipt["t_cutoff_qualified"] is False
    assert receipt["real_complete_authority"] is False
    assert receipt["activation_authorized"] is False
    assert receipt["restore_qualified"] is False
    assert receipt["factory_reset_ready"] is False


def test_synthetic_capture_binds_exact_xattr_acl_and_flag_values() -> None:
    roots = _roots()
    roots[0]["xattr_names_b64"] = ["dXNlci5leGFtcGxl"]
    roots[0]["acl_state"] = "value_required"
    roots[0]["flags_state"] = "value_required"
    plan = _fixture_plan(roots=roots)
    metadata_values = _empty_metadata_values(plan)
    metadata_values["root:root-alpha"] = {
        "xattrs": {"dXNlci5leGFtcGxl": b"opaque-xattr"},
        "acl_bytes": b"opaque-acl",
        "flags_value": 0,
    }
    file_group = plan["entries"][0]["hardlink_group_id"]
    receipt = rehearse_synthetic_capture(
        plan,
        file_payloads={file_group: b"0123456789"},
        metadata_values=metadata_values,
    )
    assert receipt["status"] == "synthetic_artifact_regular_files_verified"
    assert receipt["metadata_member_count"] == 4


@pytest.mark.parametrize("payloads", [{}, {"unexpected": b"x"}])
def test_synthetic_capture_rejects_missing_or_extra_file_objects(payloads) -> None:
    plan = _fixture_plan()
    with pytest.raises(SyntheticCaptureError, match="file_payload_roster_mismatch"):
        rehearse_synthetic_capture(
            plan,
            file_payloads=payloads,
            metadata_values=_empty_metadata_values(plan),
        )


def test_synthetic_capture_rejects_size_mismatch() -> None:
    plan = _fixture_plan()
    group_id = plan["entries"][0]["hardlink_group_id"]
    with pytest.raises(SyntheticCaptureError, match="fixture_file_size_mismatch"):
        rehearse_synthetic_capture(
            plan,
            file_payloads={group_id: b"short"},
            metadata_values=_empty_metadata_values(plan),
        )


def test_synthetic_capture_rejects_unresolved_metadata() -> None:
    roots = _roots()
    roots[0]["acl_state"] = "unknown"
    plan = _fixture_plan(roots=roots)
    group_id = plan["entries"][0]["hardlink_group_id"]
    with pytest.raises(SyntheticCaptureError, match="unsupported_metadata_present"):
        rehearse_synthetic_capture(
            plan,
            file_payloads={group_id: b"0123456789"},
            metadata_values=_empty_metadata_values(plan),
        )


def test_synthetic_capture_rejects_incomplete_metadata_member_roster() -> None:
    plan = _fixture_plan()
    metadata = _empty_metadata_values(plan)
    metadata.pop("root:root-alpha")
    group_id = plan["entries"][0]["hardlink_group_id"]
    with pytest.raises(SyntheticCaptureError, match="metadata_member_roster_mismatch"):
        rehearse_synthetic_capture(
            plan,
            file_payloads={group_id: b"0123456789"},
            metadata_values=metadata,
        )


def test_synthetic_capture_rejects_tampered_plan_before_publication() -> None:
    plan = _fixture_plan()
    plan["entries"][0]["disposition"] = "reissue_excluded"
    group_id = plan["entries"][0]["hardlink_group_id"]
    with pytest.raises(SyntheticCaptureError, match="metadata_plan_internal_consistency_failed"):
        rehearse_synthetic_capture(
            plan,
            file_payloads={group_id: b"0123456789"},
            metadata_values=_empty_metadata_values(plan),
        )
