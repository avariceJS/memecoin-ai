"""Project caller-declared paired-loss sensitivities through real producers.

Cluster deletion is descriptive. Sign-flip enumeration is conditional on the
caller's explicit exchangeability assumption and bounded to twenty complete
pairs; this module never samples or imputes missing values.
"""

from __future__ import annotations

from typing import Any

from memecoin_runner import paired_loss_cluster_influence, paired_loss_signflip


_SENSITIVITY_KEYS = {"clusters", "signflip"}
_CLUSTER_DECLARATION_KEYS = {"id", "cluster"}
_SIGNFLIP_ASSUMPTION = "exchangeable_signs_under_null"
_MAX_SIGNFLIP_PAIRS = 20


def _nonempty_string(value: Any, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _validate(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str] | None, bool]:
    if type(payload) is not dict:
        raise ValueError("payload must be an object")
    rows = payload.get("rows")
    if type(rows) is not list:
        raise ValueError("payload rows must be a list")
    sensitivity = payload.get("sensitivity")
    if type(sensitivity) is not dict or set(sensitivity) != _SENSITIVITY_KEYS:
        raise ValueError("sensitivity must contain exactly clusters and signflip")

    seen: set[str] = set()
    for index, row in enumerate(rows):
        if type(row) is not dict:
            raise ValueError(f"rows[{index}] must be an object")
        identity = _nonempty_string(row.get("id"), f"rows[{index}].id")
        if identity in seen:
            raise ValueError("row ids must be unique")
        seen.add(identity)

    clusters_value = sensitivity["clusters"]
    cluster_by_id: dict[str, str] | None
    if clusters_value is None:
        cluster_by_id = None
    else:
        if type(clusters_value) is not list:
            raise ValueError("sensitivity.clusters must be null or a list")
        cluster_by_id = {}
        for index, declaration in enumerate(clusters_value):
            if type(declaration) is not dict or set(declaration) != _CLUSTER_DECLARATION_KEYS:
                raise ValueError(f"sensitivity.clusters[{index}] must contain exactly id, cluster")
            identity = _nonempty_string(declaration["id"], f"sensitivity.clusters[{index}].id")
            cluster = _nonempty_string(declaration["cluster"], f"sensitivity.clusters[{index}].cluster")
            if identity in cluster_by_id:
                raise ValueError("sensitivity cluster ids must be unique")
            if identity not in seen:
                raise ValueError(f"sensitivity cluster id {identity!r} is unknown")
            cluster_by_id[identity] = cluster
        if set(cluster_by_id) != seen:
            raise ValueError("sensitivity.clusters must cover every roster id exactly once")

    signflip_value = sensitivity["signflip"]
    if signflip_value is None:
        signflip_requested = False
    else:
        if type(signflip_value) is not dict or set(signflip_value) != {"assumption"}:
            raise ValueError("sensitivity.signflip must be null or contain exactly assumption")
        if signflip_value["assumption"] != _SIGNFLIP_ASSUMPTION:
            raise ValueError(f"sensitivity.signflip.assumption must be {_SIGNFLIP_ASSUMPTION!r}")
        signflip_requested = True
    return rows, cluster_by_id, signflip_requested


def analyze_sensitivity(payload: dict[str, Any]) -> dict[str, Any]:
    """Return actual optional producer envelopes and projection provenance."""
    rows, cluster_by_id, signflip_requested = _validate(payload)
    complete_rows = [row for row in rows if row.get("loss_a") is not None and row.get("loss_b") is not None]
    roster_count = len(rows)
    pair_count = len(complete_rows)
    excluded = roster_count - pair_count
    common = {key: payload[key] for key in ("model_a", "model_b", "metric")}
    reports: list[dict[str, Any]] = []

    cluster_input_hash: str | None = None
    if cluster_by_id is None:
        cluster_metadata = {
            "status": "not_requested", "reason": None,
            "roster_count": roster_count, "complete_pair_count": pair_count,
            "excluded_incomplete_count": excluded, "input_sha256": None,
            "cluster_count": 0,
        }
    else:
        projected_cluster_rows = [
            {"id": row["id"], "cluster": cluster_by_id[row["id"]],
             "loss_a": row["loss_a"], "loss_b": row["loss_b"]}
            for row in complete_rows
        ]
        report = paired_loss_cluster_influence.analyze({**common, "rows": projected_cluster_rows})
        reports.append(report)
        cluster_input_hash = report["input_sha256"]
        cluster_metadata = {
            "status": "computed", "reason": None,
            "roster_count": roster_count, "complete_pair_count": pair_count,
            "excluded_incomplete_count": excluded, "input_sha256": cluster_input_hash,
            "cluster_count": len(set(cluster_by_id.values())),
        }

    signflip_input_hash: str | None = None
    assumption = _SIGNFLIP_ASSUMPTION if signflip_requested else None
    if not signflip_requested:
        signflip_metadata = {
            "status": "not_requested", "reason": None,
            "roster_count": roster_count, "complete_pair_count": pair_count,
            "excluded_incomplete_count": excluded, "input_sha256": None,
            "assumption": None, "maximum_complete_pairs": _MAX_SIGNFLIP_PAIRS,
        }
    elif pair_count == 0:
        signflip_metadata = {
            "status": "skipped", "reason": "no_complete_pairs",
            "roster_count": roster_count, "complete_pair_count": pair_count,
            "excluded_incomplete_count": excluded, "input_sha256": None,
            "assumption": assumption, "maximum_complete_pairs": _MAX_SIGNFLIP_PAIRS,
        }
    elif pair_count > _MAX_SIGNFLIP_PAIRS:
        signflip_metadata = {
            "status": "skipped", "reason": "complete_pair_limit_exceeded",
            "roster_count": roster_count, "complete_pair_count": pair_count,
            "excluded_incomplete_count": excluded, "input_sha256": None,
            "assumption": assumption, "maximum_complete_pairs": _MAX_SIGNFLIP_PAIRS,
        }
    else:
        projected_pairs = [
            {"id": row["id"], "left_loss": row["loss_a"], "right_loss": row["loss_b"]}
            for row in complete_rows
        ]
        report = paired_loss_signflip.analyze({"pairs": projected_pairs})
        reports.append(report)
        signflip_input_hash = report["input_sha256"]
        signflip_metadata = {
            "status": "computed", "reason": None,
            "roster_count": roster_count, "complete_pair_count": pair_count,
            "excluded_incomplete_count": excluded, "input_sha256": signflip_input_hash,
            "assumption": assumption, "maximum_complete_pairs": _MAX_SIGNFLIP_PAIRS,
        }

    return {
        "reports": reports,
        "metadata": {"cluster_influence": cluster_metadata, "signflip": signflip_metadata},
    }
