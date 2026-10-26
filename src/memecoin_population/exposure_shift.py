"""Attribute exposure shifts against an explicitly supplied usable corpus.

Input is ``{"entities": [{"entity": str, "corpus": weight|null,
"exposure": weight|null}, ...]}``. Null is unknown and is never imputed as
zero. Common-known support contains rows with both weights known, including
explicit zeros. Shares and total variation are computed only on that support:
``share = weight / support_total`` and ``TV = 0.5 * sum(abs(exposure_share -
corpus_share))``. TV is null unless both common-support totals are positive.
The per-entity share delta is exposure share minus corpus share; its ratio is
exposure share / corpus share and is null for zero corpus share. Positive known
weight outside common support is reported as unmatched positive mass; unknown
counts are reported separately. Contributors rank by absolute share delta,
then entity name. These are descriptive contrasts, not causal attribution,
sampling corrections, or optimizer-quality claims.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from typing import Any

KIND = "exposure_shift"
LIMITATIONS = [
    "Descriptive comparison only; exposure differences do not establish causation or optimizer quality.",
    "Shares and total variation use common-known support only; positive mass outside it is reported separately.",
    "Null weights remain unknown and are excluded from support totals; no population beyond the supplied rows is inferred.",
]


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _weight(value: Any) -> bool:
    return value is None or (
        type(value) in (int, float) and math.isfinite(value) and value >= 0
    )


def _finite_sum(values: Any) -> float:
    try:
        result = math.fsum(values)
    except OverflowError as exc:
        raise ValueError("weights_total") from exc
    if not math.isfinite(result):
        raise ValueError("weights_total")
    return result


def analyze(payload: dict) -> dict:
    """Return a deterministic diagnostic envelope for explicit entity weights."""
    if not isinstance(payload, dict) or set(payload) != {"entities"}:
        raise ValueError("payload_shape")
    rows = payload["entities"]
    if not isinstance(rows, list):
        raise ValueError("entities_shape")

    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"entity", "corpus", "exposure"}:
            raise ValueError("entity_shape")
        entity = row["entity"]
        if not isinstance(entity, str) or not entity.strip():
            raise ValueError("entity_identity")
        if entity in seen:
            raise ValueError("entity_duplicate")
        seen.add(entity)
        if not _weight(row["corpus"]) or not _weight(row["exposure"]):
            raise ValueError("entity_weight")

    common = [row for row in rows if row["corpus"] is not None and row["exposure"] is not None]
    corpus_total = _finite_sum(row["corpus"] for row in common)
    exposure_total = _finite_sum(row["exposure"] for row in common)
    corpus_common_shares = {
        row["entity"]: row["corpus"] / corpus_total if corpus_total > 0 else None
        for row in common
    }
    exposure_common_shares = {
        row["entity"]: row["exposure"] / exposure_total if exposure_total > 0 else None
        for row in common
    }

    details = []
    for row in rows:
        entity = row["entity"]
        in_common = entity in corpus_common_shares
        corpus_share = corpus_common_shares.get(entity)
        exposure_share = exposure_common_shares.get(entity)
        delta = exposure_share - corpus_share if in_common and corpus_total > 0 and exposure_total > 0 else None
        ratio = exposure_share / corpus_share if delta is not None and corpus_share > 0 else None
        details.append(
            {
                "entity": entity,
                "common_support": in_common,
                "corpus_known": row["corpus"] is not None,
                "exposure_known": row["exposure"] is not None,
                "corpus_share": corpus_share if corpus_total > 0 else None,
                "exposure_share": exposure_share if exposure_total > 0 else None,
                "share_delta": delta,
                "share_ratio": ratio,
                "unmatched_positive_corpus_mass": (
                    row["corpus"] if row["corpus"] is not None and not in_common and row["corpus"] > 0 else 0
                ),
                "unmatched_positive_exposure_mass": (
                    row["exposure"] if row["exposure"] is not None and not in_common and row["exposure"] > 0 else 0
                ),
                "explicit_zero_mismatch": (
                    in_common
                    and ((row["corpus"] == 0 and row["exposure"] > 0)
                         or (row["exposure"] == 0 and row["corpus"] > 0))
                ),
            }
        )

    ranked = sorted(
        (item for item in details if item["share_delta"] is not None),
        key=lambda item: (-abs(item["share_delta"]), item["entity"]),
    )
    tv = (
        0.5 * math.fsum(abs(item["share_delta"]) for item in ranked)
        if corpus_total > 0 and exposure_total > 0
        else None
    )
    summary = {
        "entity_count": len(rows),
        "corpus_known_count": sum(row["corpus"] is not None for row in rows),
        "exposure_known_count": sum(row["exposure"] is not None for row in rows),
        "common_known_count": len(common),
        "corpus_missing_count": sum(row["corpus"] is None for row in rows),
        "exposure_missing_count": sum(row["exposure"] is None for row in rows),
        "common_corpus_total": corpus_total,
        "common_exposure_total": exposure_total,
        "corpus_positive_mass_outside_common_support": _finite_sum(
            row["corpus"] for row in rows if row["corpus"] is not None and row["exposure"] is None
        ),
        "exposure_positive_mass_outside_common_support": _finite_sum(
            row["exposure"] for row in rows if row["exposure"] is not None and row["corpus"] is None
        ),
        "total_variation": tv,
    }
    return {
        "schema": "brain-research-diagnostic.v1",
        "kind": KIND,
        "input_sha256": hashlib.sha256(_canonical(payload)).hexdigest(),
        "summary": summary,
        "details": {"entities": details, "ranked_contributors": ranked},
        "limitations": LIMITATIONS.copy(),
    }


def _reject_constant(_value: str) -> None:
    raise ValueError("json_nonfinite")


def main() -> None:
    try:
        payload = json.load(sys.stdin, parse_constant=_reject_constant)
        result = analyze(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2) from exc
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
