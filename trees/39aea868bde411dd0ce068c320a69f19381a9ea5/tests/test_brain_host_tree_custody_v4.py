from __future__ import annotations

import base64
import copy
import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tools.brain_host_tree_custody_v4 import (
    TreeCustodyV4Error,
    plan_group_inventory,
    verify_plan,
)


def _roots():
    return [
        {
            "root_key": f"root-{name}",
            "source_locator": f"trees/{name}",
            "device": 7,
            "inode": index,
            "nlink": 2,
            "mode": 0o700,
            "uid": 501,
            "gid": 20,
            "size": 64,
            "mtime_ns": 1,
            "ctime_ns": 2,
            "xattr_names_b64": [],
            "xattr_inventory_state": "observed",
            "acl_state": "absent",
            "flags_state": "absent",
        }
        for index, name in enumerate(("alpha", "beta", "gamma"), start=10)
    ]


def _entry(root_key, path, *, inode=None, nlink=1, kind="file", link_text=None):
    if inode is None:
        inode = (
            100
            if kind == "file"
            else 10_000 + int.from_bytes(hashlib.sha256(f"{root_key}/{path}".encode()).digest()[:4])
        )
    return {
        "root_key": root_key,
        "path": path,
        "kind": kind,
        "device": 7,
        "inode": inode,
        "nlink": nlink,
        "mode": 0o600 if kind == "file" else 0o700,
        "uid": 501,
        "gid": 20,
        "size": 10 if kind == "file" else 0,
        "mtime_ns": 1,
        "ctime_ns": 2,
        "xattr_names_b64": [],
        "xattr_inventory_state": "observed",
        "acl_state": "absent",
        "flags_state": "absent",
        "link_text_b64": base64.b64encode(link_text).decode() if link_text is not None else None,
    }


def _plan(entries, *, roots=None):
    return plan_group_inventory(
        source_view_ref="synthetic-held-view",
        volume_uuid="synthetic-volume",
        roots=_roots() if roots is None else roots,
        entries=entries,
    )


def _directory(root_key, path, inode):
    return _entry(root_key, path, inode=inode, nlink=2, kind="directory")


@given(
    alias_count=st.integers(min_value=1, max_value=6),
    outside_aliases=st.integers(min_value=0, max_value=5),
    env_alias_index=st.integers(min_value=-1, max_value=5),
)
def test_hardlink_group_reconciliation_and_env_exclusion(
    alias_count: int, outside_aliases: int, env_alias_index: int
) -> None:
    env_alias_index %= alias_count + 1
    entries = [
        _directory(f"root-{name}", "group", 1000 + index)
        for index, name in enumerate(("alpha", "beta", "gamma"))
    ]
    if env_alias_index < alias_count:
        env_root = ("root-alpha", "root-beta", "root-gamma")[env_alias_index % 3]
        entries.append(_directory(env_root, "nested", 1010 + env_alias_index))
    for index in range(alias_count):
        root = ("root-alpha", "root-beta", "root-gamma")[index % 3]
        path = f"group/{index}.bin"
        if index == env_alias_index:
            path = "nested/.env.local"
        entries.append(_entry(root, path, inode=100, nlink=alias_count + outside_aliases))

    plan = _plan(entries)
    rows = plan["entries"]
    file_rows = [row for row in rows if row["kind"] == "file"]
    excluded = [row for row in file_rows if row["disposition"] == "reissue_excluded"]
    included = [row for row in file_rows if row["disposition"] == "included_opaque"]
    assert len(excluded) == (1 if env_alias_index < alias_count else 0) * alias_count
    assert len(included) == alias_count - len(excluded)
    assert plan["unaccounted_hardlink_group_count"] == int(outside_aliases > 0)
    for row in file_rows:
        assert row["source_nlink"] == alias_count + outside_aliases
        assert row["in_group_alias_count"] == alias_count
        assert row["unaccounted_alias_count"] == outside_aliases
        assert row["unaccounted_alias_location"] == (
            "unresolved" if outside_aliases > 0 else "none_reported"
        )
    assert plan["complete_marker_publishable"] is False
    assert plan["status"] == "metadata_plan_only_not_captured"
    assert plan["semantic_lineage"] == "unproven"
    assert plan["activation_authorized"] is False


def test_cross_root_alias_to_env_local_excludes_entire_group() -> None:
    plan = _plan(
        [
            _directory("root-alpha", "nested", 1001),
            _directory("root-beta", "nested", 1002),
            _entry("root-alpha", "nested/.env.local", inode=100, nlink=3),
            _entry("root-beta", "wallet.db", inode=100, nlink=3),
            _entry("root-gamma", "copy.bin", inode=100, nlink=3),
        ]
    )
    file_rows = [row for row in plan["entries"] if row["kind"] == "file"]
    assert {row["disposition"] for row in file_rows} == {"reissue_excluded"}
    assert {row["exclusion_reason"] for row in file_rows} == {
        "exact_env_local_credential_policy",
        "hardlink_alias_to_exact_env_local",
    }


def test_symlink_to_excluded_credential_path_is_not_materialized() -> None:
    plan = _plan(
        [
            _directory("root-alpha", "nested", 1001),
            _directory("root-beta", "nested", 1002),
            _entry("root-alpha", "nested/.env.local", inode=100, nlink=2),
            _entry("root-beta", "wallet.db", inode=100, nlink=2),
            _entry("root-alpha", "nested/link", kind="symlink", link_text=b".env.local"),
        ]
    )
    row = next(row for row in plan["entries"] if row["path"] == "nested/link")
    assert row["symlink_lexical_target_hint"] == "in_root_target_excluded_unresolved"
    assert row["symlink_materialize"] is False


def test_exact_env_local_symlink_is_excluded_without_target_text() -> None:
    entry = _entry("root-alpha", ".env.local", kind="symlink")
    plan = _plan([entry])
    row = next(row for row in plan["entries"] if row["path"] == ".env.local")
    assert row["link_text_b64"] is None
    assert row["disposition"] == "reissue_excluded"
    assert row["symlink_lexical_target_hint"] == "excluded_target_not_supplied"
    assert row["symlink_materialize"] is False


def test_exact_env_local_symlink_rejects_supplied_target_text() -> None:
    entry = _entry("root-alpha", ".env.local", kind="symlink", link_text=b"private/path")
    with pytest.raises(TreeCustodyV4Error, match="env_local_symlink_text_forbidden"):
        _plan([entry])


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (b"../outside", "outside_root_target_unresolved"),
        (b"/absolute", "absolute_target_unresolved"),
        (b"missing", "in_root_target_unresolved"),
        (b"data.bin", "in_root_target_present"),
        (b"child-link", "in_root_symlink_chain_unresolved"),
    ],
)
def test_symlink_targets_are_classified_without_following(target, expected) -> None:
    entries = [_entry("root-alpha", "link", kind="symlink", link_text=target)]
    if target == b"data.bin":
        entries.append(_entry("root-alpha", "data.bin", inode=101))
    if target == b"child-link":
        entries.append(_entry("root-alpha", "child-link", kind="symlink", link_text=b"missing"))
    row = next(row for row in _plan(entries)["entries"] if row["path"] == "link")
    assert row["symlink_lexical_target_hint"] == expected
    assert row["symlink_materialize"] is False


def test_intermediate_symlink_before_dotdot_stays_unresolved() -> None:
    entries = [
        _directory("root-alpha", "a", 1001),
        _entry("root-alpha", "a/escape", kind="symlink", link_text=b"../../outside"),
        _entry("root-alpha", "a/link", kind="symlink", link_text=b"escape/../data.bin"),
        _entry("root-alpha", "data.bin", inode=101),
    ]
    row = next(row for row in _plan(entries)["entries"] if row["path"] == "a/link")
    assert row["symlink_lexical_target_hint"] == "in_root_symlink_component_unresolved"
    assert row["symlink_materialize"] is False


@pytest.mark.parametrize(
    ("prefix_kind", "expected"),
    [
        ("missing", "in_root_missing_intermediate_unresolved"),
        ("file", "in_root_non_directory_intermediate_unresolved"),
    ],
)
def test_missing_or_nondirectory_symlink_intermediate_is_not_present(prefix_kind, expected) -> None:
    entries = [
        _entry(
            "root-alpha", "link", kind="symlink", link_text=f"{prefix_kind}/../data.bin".encode()
        )
    ]
    if prefix_kind == "file":
        entries.append(_entry("root-alpha", "file", inode=102))
    entries.append(_entry("root-alpha", "data.bin", inode=101))
    row = next(row for row in _plan(entries)["entries"] if row["path"] == "link")
    assert row["symlink_lexical_target_hint"] == expected
    assert row["symlink_materialize"] is False


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("special", "special_file_rejected"),
        ("cross_device", "entry_cross_device"),
        ("invalid_path", "path_invalid"),
        ("duplicate", "entry_path_duplicate"),
        ("nlink", "hardlink_alias_count_exceeds_source"),
        ("metadata", "hardlink_metadata_conflict"),
        ("root_overlap", "root_locator_overlap"),
        ("root_alias", "root_alias_overlap"),
        ("root_entry_alias", "entry_aliases_root"),
        ("missing_parent", "entry_parent_directory_unproven"),
    ],
)
def test_invalid_metadata_plan_fails_closed(mutation, error) -> None:
    roots = _roots()
    entries = [_entry("root-alpha", "data.bin")]
    if mutation == "special":
        entries[0]["kind"] = "fifo"
    elif mutation == "cross_device":
        entries[0]["device"] = 8
    elif mutation == "invalid_path":
        entries[0]["path"] = "../outside"
    elif mutation == "duplicate":
        entries.append(copy.deepcopy(entries[0]))
    elif mutation == "nlink":
        entries.extend(
            [
                _entry("root-beta", "alias", inode=100, nlink=1),
            ]
        )
    elif mutation == "metadata":
        entries.append(_entry("root-beta", "alias", inode=100, nlink=2))
        entries[0]["size"] = 11
    elif mutation == "root_overlap":
        roots[1]["source_locator"] = "trees/alpha/child"
    elif mutation == "root_alias":
        roots[1]["inode"] = roots[0]["inode"]
    elif mutation == "root_entry_alias":
        entries[0]["inode"] = roots[0]["inode"]
    elif mutation == "missing_parent":
        entries[0]["path"] = "nested/data.bin"
    with pytest.raises(TreeCustodyV4Error, match=error):
        plan_group_inventory(
            source_view_ref="synthetic",
            volume_uuid="synthetic-volume",
            roots=roots,
            entries=entries,
        )


def test_included_metadata_unknown_is_counted_and_never_complete() -> None:
    entry = _entry("root-alpha", "data.bin")
    entry["acl_state"] = "unknown"
    plan = _plan([entry])
    assert plan["unsupported_metadata_member_count"] == 1
    assert plan["complete_marker_publishable"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("xattr_names_b64", ["dXNlci5leGFtcGxl"]),
        ("acl_state", "value_required"),
        ("flags_state", "value_required"),
    ],
)
def test_present_extended_metadata_creates_a_value_capture_obligation(field, value) -> None:
    entry = _entry("root-alpha", "data.bin")
    entry[field] = value
    plan = _plan([entry])
    assert plan["metadata_value_obligation_member_count"] == 1
    assert plan["complete_marker_publishable"] is False


def test_root_directory_metadata_is_first_class_and_obligates_value_capture() -> None:
    roots = _roots()
    roots[0]["xattr_names_b64"] = ["dXNlci5leGFtcGxl"]
    plan = plan_group_inventory(
        source_view_ref="synthetic-held-view",
        volume_uuid="synthetic-volume",
        roots=roots,
        entries=[],
    )
    assert plan["root_count"] == 3
    assert plan["root_roster"][0]["disposition"] == "included_opaque"
    assert plan["root_roster"][0]["metadata_provenance"] == "caller_metadata_unqualified"
    assert plan["metadata_value_obligation_member_count"] == 1
    assert plan["complete_marker_publishable"] is False


def test_root_roster_rejects_legacy_identity_only_records() -> None:
    roots = _roots()
    del roots[0]["mode"]
    with pytest.raises(TreeCustodyV4Error, match="root_record_invalid"):
        plan_group_inventory(
            source_view_ref="synthetic-held-view",
            volume_uuid="synthetic-volume",
            roots=roots,
            entries=[],
        )


def test_unknown_xattr_inventory_cannot_be_counted_as_empty() -> None:
    entry = _entry("root-alpha", "data.bin")
    entry["xattr_inventory_state"] = "unknown"
    plan = _plan([entry])
    assert plan["unsupported_metadata_member_count"] == 1
    assert plan["metadata_value_obligation_member_count"] == 0


def test_plan_verifier_rederives_all_dispositions_and_counts() -> None:
    plan = _plan([_entry("root-alpha", "data.bin")])
    assert verify_plan(plan) == plan


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("entry_count", "plan_derived_fields_mismatch"),
        ("drop_entry", "plan_derived_fields_mismatch"),
        ("hardlink_id", "plan_derived_fields_mismatch"),
        ("forged_exclusion", "plan_derived_fields_mismatch"),
        ("classification", "plan_derived_fields_mismatch"),
        ("duplicate_root", "root_identity_invalid"),
    ],
)
def test_plan_verifier_rejects_tampered_plan(mutation, error) -> None:
    plan = _plan([_entry("root-alpha", "data.bin")])
    if mutation == "entry_count":
        plan["entry_count"] += 1
    elif mutation == "drop_entry":
        plan["entries"].clear()
    elif mutation == "hardlink_id":
        plan["entries"][0]["hardlink_group_id"] = "0" * 64
    elif mutation == "forged_exclusion":
        plan["entries"][0]["disposition"] = "reissue_excluded"
        plan["entries"][0]["exclusion_reason"] = "exact_env_local_credential_policy"
    elif mutation == "classification":
        plan["classification"] = "public_research"
    elif mutation == "duplicate_root":
        plan["root_roster"][1]["root_key"] = plan["root_roster"][0]["root_key"]
    with pytest.raises(TreeCustodyV4Error, match=error):
        verify_plan(plan)
