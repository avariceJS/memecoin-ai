"""Validation boundary for the explicitly supported diagnostic envelopes.

This module deliberately validates envelope structure and JSON values only. It
does not inspect diagnostic meaning or verify declared hashes against inputs.
Only producers with the six-key envelope are supported. In particular,
split_target_deviation adds quota_support and weight_state_delta uses a distinct
states/support/metrics shape; neither is adapted or accepted here.
"""

from __future__ import annotations

import re
from typing import Any

from memecoin_experience.paired_loss_report import _json_copy


CURRENT_KINDS = frozenset({
    "abstention_frontier",
    "attrition_funnel",
    "availability_profile",
    "expected_observation_timeliness",
    "expected_slot_coverage",
    "exposure_shift",
    "group_partition_concordance",
    "group_split_allocation_audit",
    "group_split_feasibility",
    "paired_loss_cluster_influence",
    "paired_loss_comparison",
    "paired_loss_missing_bounds",
    "paired_loss_signflip",
    "paired_loss_target_mix",
    "provider_roster_coverage",
    "quantile_cohort_coverage",
    "research_evidence_gate",
    "source_value_concordance",
    "split_assignment_plan_delta",
    "split_stratum_coverage",
    "weight_concentration",
})

_ENVELOPE_KEYS = frozenset({
    "schema", "kind", "input_sha256", "summary", "details", "limitations",
})
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def validate_reports(reports: object) -> list[dict[str, Any]]:
    """Validate, detach and deterministically order supported envelopes."""
    if not isinstance(reports, list):
        raise ValueError("research_report_reports")
    try:
        copied = _json_copy(reports)
    except ValueError as exc:
        raise ValueError("research_report_non_json_value") from exc

    validated: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for report in copied:
        if not isinstance(report, dict) or set(report) != _ENVELOPE_KEYS:
            raise ValueError("research_report_envelope")
        if report["schema"] != "brain-research-diagnostic.v1":
            raise ValueError("research_report_schema")
        kind, digest = report["kind"], report["input_sha256"]
        if not isinstance(kind, str) or kind not in CURRENT_KINDS:
            raise ValueError("research_report_kind")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError("research_report_input_sha256")
        identity = (kind, digest)
        if identity in seen:
            raise ValueError("research_report_duplicate_report")
        seen.add(identity)
        if not isinstance(report["summary"], dict):
            raise ValueError("research_report_summary")
        if not isinstance(report["details"], (dict, list)):
            raise ValueError("research_report_details")
        limitations = report["limitations"]
        if not isinstance(limitations, list) or any(
            not isinstance(item, str) or not item.strip() for item in limitations
        ):
            raise ValueError("research_report_limitations")
        validated.append(report)
    validated.sort(key=lambda item: (item["kind"], item["input_sha256"]))
    return validated
