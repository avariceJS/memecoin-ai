"""Pure deterministic lineage audit for split experiment inputs."""

import hashlib
import json
import re


_HEX = re.compile(r"[0-9a-f]{64}\Z")
_SPLITS = ("train", "validation", "test")


def _fail(code):
    raise ValueError(code)


def _pin(value, keys, code):
    if not isinstance(value, dict) or set(value) != set(keys):
        _fail(code)
    return value


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _count(value, code):
    if type(value) is not int or value < 0:
        _fail(code)
    return value


def analyze(snapshot_pin, inclusion, assignments, split_plan):
    """Return a content-addressed input manifest without loading population data."""
    snapshot_pin = _pin(snapshot_pin, ("snapshot_id", "snapshot_sha256"), "split_lineage_snapshot_pin")
    if not isinstance(snapshot_pin["snapshot_id"], str) or not snapshot_pin["snapshot_id"] or not isinstance(snapshot_pin["snapshot_sha256"], str) or not _HEX.fullmatch(snapshot_pin["snapshot_sha256"]):
        _fail("split_lineage_snapshot_pin")
    required_inclusion = {
        "snapshot_pin", "input_sha256", "rows", "groups", "row_denominators", "group_denominators"
    }
    if not isinstance(inclusion, dict) or not required_inclusion <= set(inclusion):
        _fail("split_lineage_inclusion")
    if inclusion["snapshot_pin"] != snapshot_pin:
        _fail("split_lineage_pin_mismatch")
    if not isinstance(inclusion["input_sha256"], str) or not _HEX.fullmatch(inclusion["input_sha256"]):
        _fail("split_lineage_inclusion")
    if "canonical_input_sha256" in inclusion and inclusion["canonical_input_sha256"] != inclusion["input_sha256"]:
        _fail("split_lineage_inclusion")
    plan = _pin(split_plan, ("plan_id", "plan_sha256", "declared_splits"), "split_lineage_plan")
    if not isinstance(plan["plan_id"], str) or not plan["plan_id"] or not isinstance(plan["plan_sha256"], str) or not _HEX.fullmatch(plan["plan_sha256"]):
        _fail("split_lineage_plan")
    declared = plan["declared_splits"]
    if not isinstance(declared, list) or not declared or any(not isinstance(s, str) or s not in _SPLITS for s in declared) or len(set(declared)) != len(declared) or not {"train", "test"} <= set(declared):
        _fail("split_lineage_plan")

    rows, groups = inclusion["rows"], inclusion["groups"]
    if not isinstance(rows, list) or not isinstance(groups, list):
        _fail("split_lineage_inclusion")
    group_rows = {}
    row_ids = set()
    excluded_rows = 0
    for row in rows:
        if not isinstance(row, dict) or not {"row_id", "group_id", "disposition", "reason"} <= set(row) or not isinstance(row["row_id"], str) or not row["row_id"] or not isinstance(row["group_id"], str) or not row["group_id"] or row["disposition"] not in ("included", "excluded") or (row["reason"] is not None and not isinstance(row["reason"], str)) or (row["disposition"] == "excluded" and (not isinstance(row["reason"], str) or not row["reason"])):
            _fail("split_lineage_inclusion")
        if row["row_id"] in row_ids:
            _fail("split_lineage_duplicate_row")
        row_ids.add(row["row_id"])
        bucket = group_rows.setdefault(row["group_id"], {"included": 0, "excluded": 0, "ids": []})
        bucket[row["disposition"]] += 1
        if row["disposition"] == "included":
            bucket["ids"].append(row["row_id"])
        else:
            excluded_rows += 1

    group_contract = {}
    for group in groups:
        if not isinstance(group, dict) or not {"group_id", "row_count", "disposition"} <= set(group) or not isinstance(group["group_id"], str) or not group["group_id"] or group["disposition"] not in ("included", "excluded"):
            _fail("split_lineage_inclusion")
        gid = group["group_id"]
        if gid in group_contract:
            _fail("split_lineage_duplicate_group")
        included_key = "included_row_count" if "included_row_count" in group else "included_count"
        excluded_key = "excluded_row_count" if "excluded_row_count" in group else "excluded_count"
        if included_key not in group or excluded_key not in group:
            _fail("split_lineage_inclusion")
        counts = tuple(_count(group[k], "split_lineage_inclusion") for k in ("row_count", included_key, excluded_key))
        actual = group_rows.get(gid, {"included": 0, "excluded": 0})
        if counts != (actual["included"] + actual["excluded"], actual["included"], actual["excluded"]) or group["disposition"] != ("included" if actual["included"] else "excluded"):
            _fail("split_lineage_inclusion")
        group_contract[gid] = group["disposition"]
    if set(group_contract) != set(group_rows):
        _fail("split_lineage_inclusion")

    def denom(key, report_key):
        value = inclusion[key]
        if not isinstance(value, dict) or set(value) != {"before", "after"}:
            _fail("split_lineage_inclusion")
        before, after = _count(value["before"], "split_lineage_inclusion"), _count(value["after"], "split_lineage_inclusion")
        expected = (
            (len(rows), sum(row["disposition"] == "included" for row in rows))
            if key == "row_denominators"
            else (len(groups), sum(group_contract[g] == "included" for g in group_contract))
        )
        if (before, after) != expected:
            _fail("split_lineage_inclusion")
        if "denominators" in inclusion:
            nested = inclusion["denominators"].get(report_key) if isinstance(inclusion["denominators"], dict) else None
            if not isinstance(nested, dict) or (nested.get("before"), nested.get("after")) != (before, after):
                _fail("split_lineage_inclusion")
        return {"before": before, "after": after, "change": after - before}

    row_den = denom("row_denominators", "rows")
    group_den = denom("group_denominators", "groups")
    if not isinstance(assignments, list):
        _fail("split_lineage_assignments")
    assigned = {}
    for record in assignments:
        if not isinstance(record, dict) or set(record) != {"group_id", "split"} or not isinstance(record["group_id"], str) or not record["group_id"] or not isinstance(record["split"], str) or record["split"] not in _SPLITS:
            _fail("split_lineage_assignment")
        gid, split = record["group_id"], record["split"]
        if gid not in group_contract:
            _fail("split_lineage_unknown_assignment")
        if gid in assigned:
            _fail("split_lineage_duplicate_assignment")
        if group_contract[gid] == "excluded":
            _fail("split_lineage_excluded_assignment")
        if split not in declared:
            _fail("split_lineage_undeclared_split")
        assigned[gid] = split
    included_groups = {g for g, disposition in group_contract.items() if disposition == "included"}
    if assigned.keys() != included_groups:
        _fail("split_lineage_missing_assignment")

    split_rows, split_groups = {}, {}
    split_denominators = {}
    for split in _SPLITS:
        gids = sorted(g for g, value in assigned.items() if value == split)
        ids = sorted(row_id for gid in gids for row_id in group_rows[gid]["ids"])
        split_groups[split] = len(gids)
        split_rows[split] = ids
        split_denominators[split] = {
            "rows": {"before_inclusion": row_den["before"], "after_inclusion": len(ids), "change": len(ids) - row_den["before"]},
            "groups": {"before_inclusion": group_den["before"], "after_inclusion": len(gids), "change": len(gids) - group_den["before"]},
        }
    manifest = {
        "snapshot_pin": dict(snapshot_pin),
        "inclusion_sha256": inclusion["input_sha256"],
        "split_plan": {"plan_id": plan["plan_id"], "plan_sha256": plan["plan_sha256"]},
        "declared_splits": sorted(declared, key=_SPLITS.index),
        "included_row_ids_by_split": split_rows,
        "included_group_counts_by_split": split_groups,
        "excluded_row_count": excluded_rows,
        "excluded_group_count": sum(v == "excluded" for v in group_contract.values()),
        "row_denominators": row_den,
        "group_denominators": group_den,
        "split_denominator_changes": split_denominators,
    }
    return {**manifest, "manifest_sha256": _digest(manifest)}
