"""Compare explicit observed and target split count tables."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from typing import Any


_ERROR = "split_target_deviation_invalid_input"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z", re.ASCII)
_MAX_CELLS = 256
_MAX_COUNT = 1_000_000
_LIMITATIONS = [
    "The audit compares only the supplied split-by-stratum integer counts; it cannot establish that they describe a complete or representative population.",
    "Quota support means observed cell counts are at least the declared target counts; it does not establish group-level assignability or any other split qualification.",
    "Deviation rates use target counts as denominators and are undefined when a target count is zero; no rows, outcomes, or statistical inference are used.",
]


def _invalid() -> ValueError:
    return ValueError(_ERROR)


def _identifier(value: Any) -> bool:
    return type(value) is str and _ID.fullmatch(value) is not None


def _count(value: Any) -> bool:
    return type(value) is int and 0 <= value <= _MAX_COUNT


def analyze(payload: dict) -> dict:
    """Audit aligned split-by-stratum tables without mutation or inference.

    ``observed_counts`` and ``target_counts`` each map split labels to maps of
    stratum labels to nonnegative integer counts. Signed deviation is observed
    minus target. A cell's absolute-deviation rate uses its target count as the
    denominator, and is ``None`` when that target is zero.
    """
    if type(payload) is not dict or set(payload) != {"observed_counts", "target_counts"}:
        raise _invalid()
    observed, target = payload["observed_counts"], payload["target_counts"]
    if type(observed) is not dict or type(target) is not dict or not observed or set(observed) != set(target):
        raise _invalid()
    if len(observed) > 16:
        raise _invalid()

    splits = sorted(observed)
    if any(not _identifier(split) for split in splits):
        raise _invalid()
    strata: set[str] | None = None
    for table in (observed, target):
        for split in splits:
            cells = table[split]
            if type(cells) is not dict or not cells:
                raise _invalid()
            keys = set(cells)
            if any(not _identifier(key) for key in keys):
                raise _invalid()
            if strata is None:
                strata = keys
            elif keys != strata:
                raise _invalid()
            if any(not _count(value) for value in cells.values()):
                raise _invalid()
    assert strata is not None
    if len(splits) * len(strata) > _MAX_CELLS:
        raise _invalid()

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    input_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    cells_report = []
    by_split = []
    supported_count = exact_match_count = 0
    total_observed = total_target = total_absolute = 0
    for split in splits:
        split_observed = split_target = split_absolute = 0
        for stratum in sorted(strata):
            actual, requested = observed[split][stratum], target[split][stratum]
            signed = actual - requested
            absolute = abs(signed)
            supported = actual >= requested
            exact = actual == requested
            supported_count += int(supported)
            exact_match_count += int(exact)
            split_observed += actual
            split_target += requested
            split_absolute += absolute
            cells_report.append({
                "split": split,
                "stratum": stratum,
                "observed_count": actual,
                "target_count": requested,
                "signed_deviation": signed,
                "absolute_deviation": absolute,
                "absolute_deviation_over_target": absolute / requested if requested else None,
                "denominator_status": "positive_target" if requested else "zero_target",
                "quota_support_status": "supported" if supported else "unsupported",
                "exact_quota_match": exact,
            })
        total_observed += split_observed
        total_target += split_target
        total_absolute += split_absolute
        by_split.append({
            "split": split,
            "observed_total": split_observed,
            "target_total": split_target,
            "signed_deviation": split_observed - split_target,
            "absolute_deviation": split_absolute,
            "absolute_deviation_over_target": split_absolute / split_target if split_target else None,
            "denominator_status": "positive_target" if split_target else "zero_target",
        })

    cell_count = len(cells_report)
    return {
        "schema": "brain-research-diagnostic.v1",
        "kind": "split_target_deviation",
        "input_sha256": input_sha256,
        "summary": {
            "status": "exact_match" if total_absolute == 0 else "deviation",
            "split_count": len(splits),
            "stratum_count": len(strata),
            "cell_count": cell_count,
            "observed_total": total_observed,
            "target_total": total_target,
            "signed_deviation": total_observed - total_target,
            "absolute_deviation": total_absolute,
            "absolute_deviation_over_target": total_absolute / total_target if total_target else None,
            "denominator_status": "positive_target" if total_target else "zero_target",
        },
        "quota_support": {
            "status": "supported" if supported_count == cell_count else "unsupported",
            "supported_cell_count": supported_count,
            "unsupported_cell_count": cell_count - supported_count,
            "exact_match_cell_count": exact_match_count,
            "cell_denominator": cell_count,
        },
        "details": {"cells": cells_report, "split_totals": by_split},
        "limitations": list(_LIMITATIONS),
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise _invalid()


def main() -> int:
    try:
        if len(sys.argv) != 1:
            raise _invalid()
        raw = sys.stdin.buffer.read(1_048_577)
        if len(raw) > 1_048_576:
            raise _invalid()
        payload = json.loads(raw.decode("utf-8", errors="strict"), parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        result = analyze(payload)
        output = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        sys.stderr.write('{"error":"split_target_deviation_invalid_input"}\n')
        return 2
    sys.stdout.write(output + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
