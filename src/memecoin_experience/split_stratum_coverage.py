"""Count explicit row, stratum, and split declarations against supported splits."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from typing import Any

_ERROR = "split_stratum_coverage_invalid_input"
_MAX_INPUT_BYTES = 1_048_576
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", re.ASCII)
_LIMITATIONS = [
    "Rows, strata, supported splits and declarations are caller supplied claims; omitted source rows cannot be inferred.",
    "This reports declaration coverage and label support only; it does not process row data or infer strata or splits.",
    "A clean audit does not establish population completeness, representativeness, independence, temporal validity or model qualification.",
]


def _invalid() -> ValueError:
    return ValueError(_ERROR)


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def analyze(payload: dict) -> dict:
    """Audit explicit declarations without modifying or interpreting row data."""
    if type(payload) is not dict or set(payload) != {"rows", "strata", "row_strata", "row_splits"}:
        raise _invalid()
    rows, strata, row_strata, row_splits = (payload[k] for k in ("rows", "strata", "row_strata", "row_splits"))
    if any(type(value) is not list for value in (rows, strata, row_strata, row_splits)):
        raise _invalid()
    if len(rows) > 10000 or len(strata) > 1000 or len(row_strata) > 20000 or len(row_splits) > 20000:
        raise _invalid()

    row_ids: set[str] = set()
    for row in rows:
        if type(row) is not dict or set(row) != {"id"} or not _valid_id(row["id"]) or row["id"] in row_ids:
            raise _invalid()
        row_ids.add(row["id"])

    supported: dict[str, set[str]] = {}
    for stratum in strata:
        if type(stratum) is not dict or set(stratum) != {"id", "supported_splits"}:
            raise _invalid()
        stratum_id, labels = stratum["id"], stratum["supported_splits"]
        if not _valid_id(stratum_id) or stratum_id in supported or type(labels) is not list:
            raise _invalid()
        if len(labels) > 100 or any(not _valid_id(label) for label in labels) or len(set(labels)) != len(labels):
            raise _invalid()
        supported[stratum_id] = set(labels)

    def parse(records: list, keys: set[str], field: str) -> list[tuple[int, str, str]]:
        parsed = []
        for index, record in enumerate(records):
            if type(record) is not dict or set(record) != keys:
                raise _invalid()
            row_id, value = record["row_id"], record[field]
            if not _valid_id(row_id) or not _valid_id(value):
                raise _invalid()
            parsed.append((index, row_id, value))
        return parsed

    strata_records = parse(row_strata, {"row_id", "stratum_id"}, "stratum_id")
    split_records = parse(row_splits, {"row_id", "split"}, "split")
    try:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise _invalid() from exc

    stratum_by_row: dict[str, list[str]] = {}
    unknown_strata: list[dict[str, Any]] = []
    for index, row_id, stratum_id in strata_records:
        if row_id not in row_ids or stratum_id not in supported:
            unknown_strata.append({"declaration_index": index, "row_id": row_id, "stratum_id": stratum_id})
        else:
            stratum_by_row.setdefault(row_id, []).append(stratum_id)

    split_by_row: dict[str, list[str]] = {}
    unknown_splits: list[dict[str, Any]] = []
    for index, row_id, split in split_records:
        if row_id not in row_ids:
            unknown_splits.append({"declaration_index": index, "row_id": row_id, "split": split})
        else:
            split_by_row.setdefault(row_id, []).append(split)

    missing_strata = sorted(row_ids - set(stratum_by_row))
    missing_splits = sorted(row_ids - set(split_by_row))
    duplicate_strata = [{"row_id": row, "declaration_count": len(vals), "extra_declaration_count": len(vals)-1}
                        for row, vals in sorted(stratum_by_row.items()) if len(vals) > 1]
    duplicate_splits = [{"row_id": row, "declaration_count": len(vals), "extra_declaration_count": len(vals)-1}
                        for row, vals in sorted(split_by_row.items()) if len(vals) > 1]
    unsupported = []
    per_stratum = []
    for sid in sorted(supported):
        members = sorted(row for row, vals in stratum_by_row.items() if vals == [sid])
        unsupported_rows = []
        for row in members:
            for split in split_by_row.get(row, []):
                if split not in supported[sid]:
                    unsupported_rows.append({"row_id": row, "split": split})
                    unsupported.append({"row_id": row, "stratum_id": sid, "split": split})
        per_stratum.append({
            "stratum_id": sid, "supported_splits": sorted(supported[sid]),
            "expected_row_count": len(members), "split_declaration_count": sum(len(split_by_row.get(row, [])) for row in members),
            "supported_declaration_count": sum(1 for row in members for split in split_by_row.get(row, []) if split in supported[sid]),
            "unsupported_declaration_count": len(unsupported_rows), "unsupported_declarations": unsupported_rows,
        })
    # Rows with duplicated or missing stratum declarations cannot be attributed to a single stratum.
    findings = any((missing_strata, missing_splits, duplicate_strata, duplicate_splits, unknown_strata, unknown_splits, unsupported))
    return {
        "schema": "brain-research-diagnostic.v1", "kind": "split_stratum_coverage",
        "input_sha256": hashlib.sha256(canonical).hexdigest(),
        "summary": {
            "status": "findings" if findings else "clean", "expected_row_count": len(row_ids),
            "stratum_count": len(supported), "row_stratum_denominator": len(row_ids),
            "row_stratum_declaration_count": len(row_strata), "unique_rows_with_stratum_count": len(stratum_by_row),
            "missing_stratum_declaration_count": len(missing_strata), "duplicate_stratum_row_count": len(duplicate_strata),
            "unknown_stratum_declaration_count": len(unknown_strata), "row_split_denominator": len(row_ids),
            "row_split_declaration_count": len(row_splits), "unique_rows_with_split_count": len(split_by_row),
            "missing_split_declaration_count": len(missing_splits), "duplicate_split_row_count": len(duplicate_splits),
            "unknown_split_declaration_count": len(unknown_splits), "unsupported_split_declaration_count": len(unsupported),
            "supported_split_declaration_count": sum(x["supported_declaration_count"] for x in per_stratum),
        },
        "details": {"missing_stratum_row_ids": missing_strata, "missing_split_row_ids": missing_splits,
                    "duplicate_stratum_declarations": duplicate_strata, "duplicate_split_declarations": duplicate_splits,
                    "unknown_stratum_declarations": unknown_strata, "unknown_split_declarations": unknown_splits,
                    "unsupported_split_declarations": unsupported, "strata": per_stratum},
        "limitations": list(_LIMITATIONS),
    }


def _reject_constant(_value: str) -> None:
    raise _invalid()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


def main() -> int:
    try:
        if len(sys.argv) != 1:
            raise _invalid()
        raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
        if len(raw) > _MAX_INPUT_BYTES:
            raise _invalid()
        payload = json.loads(raw.decode("utf-8", errors="strict"), parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        result = analyze(payload)
        output = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        sys.stderr.write('{"error":"split_stratum_coverage_invalid_input"}\n')
        return 2
    sys.stdout.write(output + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
