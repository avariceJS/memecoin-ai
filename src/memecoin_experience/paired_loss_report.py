"""Compose the five paired-loss diagnostic envelopes into an explicit dossier.

The API accepts ``analyze({"reports": [...]})`` and returns
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
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise ValueError("paired_loss_report_non_json_value") from exc


def _reject_constant(_value: str) -> None:
    raise ValueError("paired_loss_report_nonfinite_json")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("paired_loss_report_duplicate_key")
        result[key] = value
    return result


def analyze(payload: dict) -> dict:
    """Validate, detach and deterministically compose paired-loss envelopes."""
    if not isinstance(payload, dict) or set(payload) != {"reports"}:
        raise ValueError("paired_loss_report_payload")
    copied = _json_copy(payload)
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
    return {
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


def render_markdown(bundle: dict) -> str:
    """Render complete bundle JSON in a fence safe for caller-controlled text."""
    detached = _json_copy(bundle)
    if not isinstance(detached, dict) or detached.get("schema") != "brain-paired-loss-bundle.v1":
        raise ValueError("paired_loss_report_bundle")
    encoded = json.dumps(detached, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False)
    longest = max((len(run) for run in re.findall(r"`+", encoded)), default=0)
    fence = "`" * max(3, longest + 1)
    return (
        "# Paired-loss diagnostic dossier\n\n"
        "Complete supplied diagnostic envelopes, ordered by kind and declared input hash. "
        "Hashes are declarations; raw inputs were not provided for verification. "
        "This composition does not determine scientific or trading comparability.\n\n"
        + fence + "json\n" + encoded + "\n" + fence + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", action="store_true", help="write a safely fenced Markdown dossier")
    args = parser.parse_args(argv)
    try:
        payload = json.load(sys.stdin, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        result = analyze(payload)
    except (ValueError, json.JSONDecodeError, RecursionError) as exc:
        print(str(exc) or "paired_loss_report_invalid_input", file=sys.stderr)
        return 2
    if args.markdown:
        sys.stdout.write(render_markdown(result))
    else:
        json.dump(result, sys.stdout, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
