"""Compare two caller-pinned population inclusion decisions.

This is a bounded, pure summary of supplied decisions. Row identity is the
only pairing key; the module does not load populations or infer decisions.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


_INVALID = "population_inclusion_delta_invalid_input"
_ROOT_KEYS = {"left", "right"}
_DECISION_KEYS = {"decision_id", "decision_sha256", "rows"}
_ROW_KEYS = {"row_id", "group_id", "disposition", "reason"}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_MAX_JSON_BYTES = 1_048_576
_MAX_ROWS_PER_DECISION = 10_000
_MAX_STRING_LENGTH = 512


def _invalid() -> ValueError:
    return ValueError(_INVALID)


def _string(value: Any) -> str:
    if type(value) is not str or not value or len(value) > _MAX_STRING_LENGTH:
        raise _invalid()
    return value


def _decision(value: Any) -> tuple[dict, dict[str, dict]]:
    if type(value) is not dict or set(value) != _DECISION_KEYS:
        raise _invalid()
    decision_id = _string(value["decision_id"])
    pin = value["decision_sha256"]
    if type(pin) is not str or _SHA256.fullmatch(pin) is None:
        raise _invalid()
    rows = value["rows"]
    if type(rows) is not list or len(rows) > _MAX_ROWS_PER_DECISION:
        raise _invalid()
    checked: dict[str, dict] = {}
    for row in rows:
        if type(row) is not dict or set(row) != _ROW_KEYS:
            raise _invalid()
        row_id = _string(row["row_id"])
        group_id = _string(row["group_id"])
        disposition = row["disposition"]
        if type(disposition) is not str or disposition not in ("included", "excluded"):
            raise _invalid()
        reason = row["reason"]
        if disposition == "excluded":
            reason = _string(reason)
        elif reason is not None:
            reason = _string(reason)
        if row_id in checked:
            raise _invalid()
        checked[row_id] = {"row_id": row_id, "group_id": group_id,
                           "disposition": disposition, "reason": reason}
    return {"decision_id": decision_id, "decision_sha256": pin}, checked


def analyze(payload: dict) -> dict:
    """Compare exactly two pinned decisions supplied under ``left``/``right``.

    The pin is a caller-declared reference and is reported verbatim; its
    relationship to upstream source material is not independently verified.
    """
    try:
        if type(payload) is not dict or set(payload) != _ROOT_KEYS:
            raise _invalid()
        left_meta, left = _decision(payload["left"])
        right_meta, right = _decision(payload["right"])
        if left_meta["decision_id"] == right_meta["decision_id"]:
            raise _invalid()

        left_ids, right_ids = set(left), set(right)
        left_only = sorted(left_ids - right_ids)
        right_only = sorted(right_ids - left_ids)
        matched_ids = sorted(left_ids & right_ids)
        matched = []
        included_to_excluded = []
        excluded_to_included = []
        disposition_unchanged = []
        reason_changes = []
        group_changes = []
        for row_id in matched_ids:
            before, after = left[row_id], right[row_id]
            change = {
                "row_id": row_id,
                "left": {"disposition": before["disposition"], "reason": before["reason"], "group_id": before["group_id"]},
                "right": {"disposition": after["disposition"], "reason": after["reason"], "group_id": after["group_id"]},
            }
            matched.append(change)
            if before["disposition"] == "included" and after["disposition"] == "excluded":
                included_to_excluded.append(row_id)
            elif before["disposition"] == "excluded" and after["disposition"] == "included":
                excluded_to_included.append(row_id)
            else:
                disposition_unchanged.append(row_id)
            if before["reason"] != after["reason"]:
                reason_changes.append({"row_id": row_id, "left": before["reason"], "right": after["reason"]})
            if before["group_id"] != after["group_id"]:
                group_changes.append({"row_id": row_id, "left": before["group_id"], "right": after["group_id"]})

        return {
            "schema": "brain-population-inclusion-delta.v1",
            "decisions": {"left": left_meta, "right": right_meta},
            "matched": matched,
            "transitions": {
                "included_to_excluded": included_to_excluded,
                "excluded_to_included": excluded_to_included,
                "disposition_unchanged": disposition_unchanged,
            },
            "reason_changes": reason_changes,
            "group_changes": group_changes,
            "identity_denominators": {
                "left": len(left_ids), "right": len(right_ids),
                "matched": len(matched_ids), "left_only": len(left_only), "right_only": len(right_only),
            },
            "left_only_row_ids": left_only,
            "right_only_row_ids": right_only,
            "limitations": [
                "Only the two caller-supplied decisions are compared; pin provenance and decision correctness are not verified.",
                "Rows pair only by exact row_id; left-only and right-only identities have no inferred counterpart.",
                "Group and reason changes describe supplied labels and do not imply a cause or population-level effect.",
            ],
        }
    except RecursionError as exc:
        raise _invalid() from exc


def analyze_json(document: str | bytes) -> dict:
    """Parse and analyze one bounded JSON document with duplicate-key rejection."""
    try:
        if type(document) is str:
            encoded = document.encode("utf-8")
        elif type(document) is bytes:
            encoded = document
        else:
            raise _invalid()
        if len(encoded) > _MAX_JSON_BYTES:
            raise _invalid()

        def unique_object(pairs: list[tuple[str, Any]]) -> dict:
            result = {}
            for key, value in pairs:
                if key in result:
                    raise _invalid()
                result[key] = value
            return result

        def reject_constant(_value: str) -> None:
            raise _invalid()

        payload = json.loads(encoded, object_pairs_hook=unique_object, parse_constant=reject_constant)
        return analyze(payload)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise _invalid() from exc


def canonical_decision_sha256(decision_id: str, rows: list[dict]) -> str:
    """Return the deterministic pin for one decision's canonical JSON content.

    This helper is for synthetic/caller-side pin construction; ``analyze``
    intentionally treats the provided digest as an opaque declared pin.
    """
    try:
        decision_id = _string(decision_id)
        _meta, checked = _decision({"decision_id": decision_id,
                                    "decision_sha256": "0" * 64, "rows": rows})
        canonical = {"decision_id": decision_id, "rows": [checked[key] for key in sorted(checked)]}
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    except (TypeError, UnicodeError, RecursionError) as exc:
        raise _invalid() from exc
