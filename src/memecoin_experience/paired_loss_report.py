"""Compose the five paired-loss diagnostic envelopes into an explicit dossier.

The API accepts ``analyze({"reports": [...]})`` or the full
``brain-paired-loss-workflow.v1`` result, and returns
``brain-paired-loss-bundle.v1``. Each report is a complete
``brain-research-diagnostic.v1`` envelope with exactly ``schema``, ``kind``,
``input_sha256``, ``summary``, ``details`` and ``limitations``. The accepted
kinds are ``paired_loss_comparison``, ``paired_loss_missing_bounds``,
``paired_loss_target_mix``, ``paired_loss_cluster_influence`` and
``paired_loss_signflip``. Reports are stably ordered by kind and declared
input hash; duplicate kind/hash identities are rejected.

Example synthetic composition::

    from memecoin_experience.paired_loss_report import analyze, render_markdown
    envelope = {
        "schema": "brain-research-diagnostic.v1",
        "kind": "paired_loss_signflip", "input_sha256": "0" * 64,
        "summary": {"pairs": 0, "exact_two_sided_p_value": None},
        "details": [], "limitations": ["Synthetic example; no observations."],
    }
    bundle = analyze({"reports": [envelope]})
    markdown = render_markdown(bundle)

For command-line use, send a JSON object containing ``reports`` to stdin;
``python -m memecoin_experience.paired_loss_report`` writes JSON and adding
``--markdown`` writes a safely fenced Markdown dossier. Declared input hashes
are only format checked: raw inputs were not supplied and hashes are not
verified. Equal model labels, metrics or hashes do not establish
comparability; different populations and projection hashes may coexist.
This report preserves diagnostics and their support without computing new
metrics, and makes no scientific, trading or promotion decision.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any


_KINDS = {
    "paired_loss_comparison",
    "paired_loss_missing_bounds",
    "paired_loss_target_mix",
    "paired_loss_cluster_influence",
    "paired_loss_signflip",
}
_ENVELOPE_KEYS = {"schema", "kind", "input_sha256", "summary", "details", "limitations"}
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_WORKFLOW_SCHEMA = "brain-paired-loss-workflow.v1"
_BASE_WORKFLOW_KINDS = [
    "paired_loss_comparison", "paired_loss_missing_bounds", "paired_loss_target_mix",
]
_SUPPORT_KEYS = {
    "roster_count", "roster_denominator", "complete_pair_count", "complete_pair_denominator",
    "paired_count", "paired_denominator", "a_only_count", "a_only_denominator",
    "b_only_count", "b_only_denominator", "both_missing_count", "both_missing_denominator",
    "target_projection_count", "target_projection_denominator",
    "target_projection_excluded_count", "target_projection_excluded_denominator",
    "target_projection_complete_pair_denominator",
}


def _validate_json(value: Any) -> None:
    """Reject non-JSON Python values without coercing object keys."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("paired_loss_report_non_json_value")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("paired_loss_report_non_json_value")
            _validate_json(item)
        return
    raise ValueError("paired_loss_report_non_json_value")


def _json_copy(value: Any) -> Any:
    """Validate JSON-compatible finite values and detach caller-owned data."""
    try:
        _validate_json(value)
        return _detach_json(value)
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise ValueError("paired_loss_report_non_json_value") from exc


def _detach_json(value: Any) -> Any:
    """Copy JSON containers directly, preserving arbitrary-precision integers."""
    if isinstance(value, list):
        return [_detach_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _detach_json(item) for key, item in value.items()}
    return value


def _integer_json(value: int) -> str:
    """Format arbitrary integers in small chunks without changing global limits."""
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    remaining = abs(value)
    chunks = []
    while remaining:
        remaining, chunk = divmod(remaining, 1_000_000_000)
        chunks.append(chunk)
    return sign + str(chunks[-1]) + "".join(f"{chunk:09d}" for chunk in reversed(chunks[:-1]))


def _json_output(value: Any, *, indent: int | None = None, level: int = 0) -> str:
    """Serialize validated JSON data, including integers beyond Python's digit cap."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if type(value) is int:
        return _integer_json(value)
    if type(value) in (float, str):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if isinstance(value, list):
        if not value:
            return "[]"
        if indent is None:
            return "[" + ",".join(_json_output(item) for item in value) + "]"
        pad, child_pad = " " * (indent * level), " " * (indent * (level + 1))
        return "[\n" + child_pad + (",\n" + child_pad).join(
            _json_output(item, indent=indent, level=level + 1) for item in value
        ) + "\n" + pad + "]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        keys = sorted(value)
        if indent is None:
            return "{" + ",".join(
                json.dumps(key, ensure_ascii=False) + ":" + _json_output(value[key]) for key in keys
            ) + "}"
        pad, child_pad = " " * (indent * level), " " * (indent * (level + 1))
        return "{\n" + child_pad + (",\n" + child_pad).join(
            json.dumps(key, ensure_ascii=False) + ": " + _json_output(value[key], indent=indent, level=level + 1)
            for key in keys
        ) + "\n" + pad + "}"
    raise ValueError("paired_loss_report_non_json_value")


def _parse_integer(token: str) -> int:
    """Parse a JSON integer in bounded chunks without relaxing Python's limit."""
    sign = -1 if token.startswith("-") else 1
    digits = token[1:] if sign < 0 else token
    value = 0
    first = len(digits) % 9 or 9
    value = int(digits[:first])
    for offset in range(first, len(digits), 9):
        value = value * 1_000_000_000 + int(digits[offset:offset + 9])
    return sign * value


def _reject_constant(_value: str) -> None:
    raise ValueError("paired_loss_report_nonfinite_json")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("paired_loss_report_duplicate_key")
        result[key] = value
    return result


def _validate_workflow(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) not in (
        {"schema", "input_sha256", "support", "reports", "limitations"},
        {"schema", "input_sha256", "support", "reports", "limitations", "sensitivity"},
    ):
        raise ValueError("paired_loss_report_workflow")
    if value["schema"] != _WORKFLOW_SCHEMA:
        raise ValueError("paired_loss_report_workflow_schema")
    if not isinstance(value["input_sha256"], str) or not _HEX_SHA256.fullmatch(value["input_sha256"]):
        raise ValueError("paired_loss_report_workflow_input_sha256")
    support = value["support"]
    if not isinstance(support, dict) or set(support) != _SUPPORT_KEYS:
        raise ValueError("paired_loss_report_workflow_support")
    if any(type(item) is not int or item < 0 for item in support.values()):
        raise ValueError("paired_loss_report_workflow_support")
    roster = support["roster_count"]
    if any(support[key] != roster for key in (
        "roster_denominator", "complete_pair_denominator", "paired_denominator",
        "a_only_denominator", "b_only_denominator", "both_missing_denominator",
        "target_projection_denominator", "target_projection_excluded_denominator",
    )):
        raise ValueError("paired_loss_report_workflow_support")
    paired = support["complete_pair_count"]
    if (support["paired_count"] != paired or support["target_projection_count"] != paired
        or support["target_projection_excluded_count"] != roster - paired
        or support["target_projection_complete_pair_denominator"] != paired
        or support["a_only_count"] + support["b_only_count"] + support["both_missing_count"] + paired != roster):
        raise ValueError("paired_loss_report_workflow_support")
    limitations = value["limitations"]
    if not isinstance(limitations, list) or any(not isinstance(item, str) or not item.strip() for item in limitations):
        raise ValueError("paired_loss_report_workflow_limitations")
    sensitivity = value.get("sensitivity")
    expected_kinds = list(_BASE_WORKFLOW_KINDS)
    if "sensitivity" in value:
        if not isinstance(sensitivity, dict) or set(sensitivity) != {"cluster_influence", "signflip"}:
            raise ValueError("paired_loss_report_workflow_sensitivity")
        for name in ("cluster_influence", "signflip"):
            meta = sensitivity[name]
            required = {"status", "reason", "roster_count", "complete_pair_count", "excluded_incomplete_count", "input_sha256"}
            required |= {"cluster_count"} if name == "cluster_influence" else {"assumption", "maximum_complete_pairs"}
            if not isinstance(meta, dict) or set(meta) != required:
                raise ValueError("paired_loss_report_workflow_sensitivity")
            if type(meta["status"]) is not str or meta["status"] not in {"computed", "not_requested", "skipped"}:
                raise ValueError("paired_loss_report_workflow_sensitivity")
            if meta["reason"] not in (None, "no_complete_pairs", "complete_pair_limit_exceeded"):
                raise ValueError("paired_loss_report_workflow_sensitivity")
            if any(type(meta[k]) is not int or meta[k] < 0 for k in ("roster_count", "complete_pair_count", "excluded_incomplete_count")):
                raise ValueError("paired_loss_report_workflow_sensitivity")
            if meta["roster_count"] != roster or meta["complete_pair_count"] != paired or meta["excluded_incomplete_count"] != roster - paired:
                raise ValueError("paired_loss_report_workflow_sensitivity")
            digest = meta["input_sha256"]
            if digest is not None and (not isinstance(digest, str) or not _HEX_SHA256.fullmatch(digest)):
                raise ValueError("paired_loss_report_workflow_sensitivity")
            if name == "cluster_influence" and (
                type(meta["cluster_count"]) is not int
                or not 0 <= meta["cluster_count"] <= roster
                or meta["status"] == "skipped"
                or (meta["status"] == "not_requested" and meta["cluster_count"] != 0)
                or (meta["status"] == "computed" and roster > 0 and meta["cluster_count"] == 0)
            ):
                raise ValueError("paired_loss_report_workflow_sensitivity")
            if name == "signflip" and (meta["assumption"] not in (None, "exchangeable_signs_under_null") or type(meta["maximum_complete_pairs"]) is not int or meta["maximum_complete_pairs"] != 20):
                raise ValueError("paired_loss_report_workflow_sensitivity")
            if meta["status"] == "computed" and (meta["reason"] is not None or digest is None):
                raise ValueError("paired_loss_report_workflow_sensitivity")
            if meta["status"] == "not_requested" and (meta["reason"] is not None or digest is not None):
                raise ValueError("paired_loss_report_workflow_sensitivity")
            if meta["status"] == "skipped" and (meta["reason"] is None or digest is not None):
                raise ValueError("paired_loss_report_workflow_sensitivity")
            if name == "signflip":
                if meta["status"] == "not_requested" and meta["assumption"] is not None:
                    raise ValueError("paired_loss_report_workflow_sensitivity")
                if meta["status"] in {"computed", "skipped"} and meta["assumption"] != "exchangeable_signs_under_null":
                    raise ValueError("paired_loss_report_workflow_sensitivity")
                if meta["status"] == "computed" and not 1 <= paired <= 20:
                    raise ValueError("paired_loss_report_workflow_sensitivity")
                if meta["status"] == "skipped" and meta["reason"] != (
                    "no_complete_pairs" if paired == 0 else
                    "complete_pair_limit_exceeded" if paired > 20 else None
                ):
                    raise ValueError("paired_loss_report_workflow_sensitivity")
            if meta["status"] == "computed":
                expected_kinds.append("paired_loss_" + name)
    reports = value["reports"]
    if not isinstance(reports, list) or len(reports) != len(expected_kinds):
        raise ValueError("paired_loss_report_workflow_reports")
    if [item.get("kind") if isinstance(item, dict) else None for item in reports] != expected_kinds:
        raise ValueError("paired_loss_report_workflow_reports")
    if sensitivity is not None:
        for name in ("cluster_influence", "signflip"):
            meta = sensitivity[name]
            if meta["status"] == "computed":
                report = next(item for item in reports if item.get("kind") == "paired_loss_" + name)
                if report.get("input_sha256") != meta["input_sha256"]:
                    raise ValueError("paired_loss_report_workflow_sensitivity")
    return {
        "input_sha256": value["input_sha256"], "support": support,
        "limitations": limitations, **({"sensitivity": sensitivity} if sensitivity is not None else {}),
    }


def analyze(payload: dict) -> dict:
    """Validate and compose reports-only input or a full workflow result."""
    if not isinstance(payload, dict) or set(payload) not in ({"reports"}, {"schema", "input_sha256", "support", "reports", "limitations"}, {"schema", "input_sha256", "support", "reports", "limitations", "sensitivity"}):
        raise ValueError("paired_loss_report_payload")
    copied = _json_copy(payload)
    workflow = None if set(copied) == {"reports"} else _validate_workflow(copied)
    reports = copied["reports"]
    if not isinstance(reports, list):
        raise ValueError("paired_loss_report_reports")

    validated = []
    seen: set[tuple[str, str]] = set()
    for report in reports:
        if not isinstance(report, dict) or set(report) != _ENVELOPE_KEYS:
            raise ValueError("paired_loss_report_envelope")
        if report["schema"] != "brain-research-diagnostic.v1":
            raise ValueError("paired_loss_report_schema")
        kind, digest = report["kind"], report["input_sha256"]
        if not isinstance(kind, str) or kind not in _KINDS:
            raise ValueError("paired_loss_report_kind")
        if not isinstance(digest, str) or not _HEX_SHA256.fullmatch(digest):
            raise ValueError("paired_loss_report_input_sha256")
        identity = (kind, digest)
        if identity in seen:
            raise ValueError("paired_loss_report_duplicate_report")
        seen.add(identity)
        if not isinstance(report["summary"], dict):
            raise ValueError("paired_loss_report_summary")
        if not isinstance(report["details"], (dict, list)):
            raise ValueError("paired_loss_report_details")
        limitations = report["limitations"]
        if not isinstance(limitations, list) or any(
            not isinstance(item, str) or not item.strip() for item in limitations
        ):
            raise ValueError("paired_loss_report_limitations")
        validated.append(report)

    validated.sort(key=lambda item: (item["kind"], item["input_sha256"]))
    result = {
        "schema": "brain-paired-loss-bundle.v1",
        "hash_semantics": (
            "Declared diagnostic input_sha256 values are preserved and format-checked; "
            "raw inputs were not supplied and their hashes are not verified."
        ),
        "report_count": len(validated),
        "reports": validated,
        "limitations": [
            "This bundle preserves caller-supplied diagnostics and does not compute new metrics.",
            "Reports may use different populations, windows, definitions, projections or support; equal model labels, metrics or hashes do not establish comparability or causality.",
            "Missing values, unsupported target means, nulls and empty supports remain as supplied and are not imputed as zero.",
            "No scientific, trading, readiness or promotion decision is produced.",
        ],
    }
    if workflow is not None:
        result["workflow"] = workflow
    return result


def render_markdown(bundle: dict) -> str:
    """Render complete bundle JSON in a fence safe for caller-controlled text."""
    detached = _json_copy(bundle)
    if not isinstance(detached, dict) or detached.get("schema") != "brain-paired-loss-bundle.v1":
        raise ValueError("paired_loss_report_bundle")
    encoded = _json_output(detached, indent=2)
    longest = max((len(run) for run in re.findall(r"`+", encoded)), default=0)
    fence = "`" * max(3, longest + 1)
    return (
        "# Paired-loss diagnostic dossier\n\n"
        "Complete supplied diagnostic envelopes, ordered by kind and declared input hash. "
        "Hashes are declarations; raw inputs were not provided for verification. "
        "This composition does not determine scientific or trading comparability.\n\n"
        + _workflow_markdown(detached.get("workflow"))
        + fence + "json\n" + encoded + "\n" + fence + "\n"
    )


def _workflow_markdown(workflow: Any) -> str:
    if workflow is None:
        return ""
    support = workflow["support"]
    lines = ["## Workflow provenance", "", f"Workflow input SHA-256 (declared): `{workflow['input_sha256']}`.",
             f"Roster: {support['roster_count']} / {support['roster_denominator']}; complete pairs: {support['complete_pair_count']} / {support['complete_pair_denominator']}; target projection excluded incomplete rows: {support['target_projection_excluded_count']} / {support['target_projection_excluded_denominator']}.",
             "Workflow support and limitations are preserved; raw workflow input was not supplied, so its hash is not verified."]
    if "sensitivity" in workflow:
        for name in ("cluster_influence", "signflip"):
            meta = workflow["sensitivity"][name]
            extra = f"; clusters: {meta['cluster_count']}" if name == "cluster_influence" else f"; assumption: {meta['assumption'] or 'none declared'}; maximum complete pairs: {meta['maximum_complete_pairs']}"
            lines.append(f"- {name}: {meta['status']}; complete pairs {meta['complete_pair_count']} / {meta['roster_count']}; excluded incomplete {meta['excluded_incomplete_count']}{extra}; reason: {meta['reason'] or 'none'}.")
        lines.append("Cluster deletion is descriptive, not a confidence interval or recommendation. Sign-flip enumeration is conditional on the explicit zero-null sign-exchangeability assumption and does not correct cluster dependence.")
    return "\n".join(lines) + "\n\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", action="store_true", help="write a safely fenced Markdown dossier")
    args = parser.parse_args(argv)
    try:
        payload = json.load(
            sys.stdin, parse_constant=_reject_constant, parse_int=_parse_integer,
            object_pairs_hook=_unique_object,
        )
        result = analyze(payload)
    except (ValueError, json.JSONDecodeError, RecursionError) as exc:
        print(str(exc) or "paired_loss_report_invalid_input", file=sys.stderr)
        return 2
    if args.markdown:
        sys.stdout.write(render_markdown(result))
    else:
        sys.stdout.write(_json_output(result))
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
