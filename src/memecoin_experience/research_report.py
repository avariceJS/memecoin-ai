"""Compose explicit diagnostic envelopes into a deterministic research dossier.

Accepts ``{"reports": [...]}`` containing the seven frozen
``brain-research-diagnostic.v1`` kinds. Each envelope must contain exactly
``schema``, ``kind``, ``input_sha256``, ``summary``, ``details`` and
``limitations``. Hashes are validated as declared 64-character lowercase
SHA-256 values; the underlying diagnostic inputs are not supplied and are not
verified here. Reports are ordered by kind then declared hash, and duplicate
(kind, hash) pairs are rejected. Empty report lists and empty details are
valid and remain empty rather than implying zero observations.

This is a descriptive composition only: it preserves supplied report content,
does not recompute diagnostic metrics, infer missing values, establish
comparability or causality, and makes no acceptance or promotion decision.
Markdown puts the complete JSON bundle in a dynamically sized fenced code
block, so caller strings (including HTML and backticks) are rendered as data.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any


_KINDS = {
    "availability_profile",
    "exposure_shift",
    "attrition_funnel",
    "binary_calibration",
    "paired_loss_comparison",
    "representation_shift",
    "abstention_frontier",
}
_ENVELOPE_KEYS = {"schema", "kind", "input_sha256", "summary", "details", "limitations"}
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _reject_constant(_value: str) -> None:
    raise ValueError("research_report_nonfinite_json")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("research_report_duplicate_key")
        result[key] = value
    return result


def _json_copy(value: Any) -> Any:
    """Validate JSON-compatible finite values and detach caller-owned data."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("research_report_non_json_value") from exc


def analyze(payload: dict) -> dict:
    """Validate and preserve explicit envelopes in stable report order."""
    if not isinstance(payload, dict) or set(payload) != {"reports"}:
        raise ValueError("research_report_payload")
    copied = _json_copy(payload)
    reports = copied["reports"]
    if not isinstance(reports, list):
        raise ValueError("research_report_reports")

    validated = []
    seen: set[tuple[str, str]] = set()
    for report in reports:
        if not isinstance(report, dict) or set(report) != _ENVELOPE_KEYS:
            raise ValueError("research_report_envelope")
        if report["schema"] != "brain-research-diagnostic.v1":
            raise ValueError("research_report_schema")
        kind, digest = report["kind"], report["input_sha256"]
        if not isinstance(kind, str) or kind not in _KINDS:
            raise ValueError("research_report_kind")
        if not isinstance(digest, str) or not _HEX_SHA256.fullmatch(digest):
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
    return {
        "schema": "brain-research-bundle.v1",
        "hash_semantics": "Declared diagnostic input_sha256 values are preserved and format-checked; raw inputs were not supplied and their hashes are not verified.",
        "report_count": len(validated),
        "reports": validated,
        "limitations": [
            "This bundle preserves caller-supplied diagnostics and does not recompute their metrics.",
            "Reports may use different populations, windows, definitions, or support; this bundle does not establish comparability or causality.",
            "Missing fields and empty supports remain as supplied and are not imputed as zero.",
            "No acceptance, readiness, or promotion decision is produced.",
        ],
    }


def render_markdown(bundle: dict) -> str:
    """Render a bundle as Markdown with caller content safely fenced as JSON."""
    detached = _json_copy(bundle)
    if not isinstance(detached, dict) or detached.get("schema") != "brain-research-bundle.v1":
        raise ValueError("research_report_bundle")
    encoded = json.dumps(detached, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False)
    longest = max((len(run) for run in re.findall(r"`+", encoded)), default=0)
    fence = "`" * max(3, longest + 1)
    return "# Reproducible research dossier\n\nSupplied diagnostic envelopes, ordered by kind and declared input hash. Hashes are declarations; underlying raw inputs were not provided for verification.\n\n" + fence + "json\n" + encoded + "\n" + fence + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", action="store_true", help="write a safely fenced Markdown dossier")
    args = parser.parse_args(argv)
    try:
        payload = json.load(sys.stdin, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        result = analyze(payload)
    except (ValueError, json.JSONDecodeError, RecursionError) as exc:
        print(str(exc) or "research_report_invalid_input", file=sys.stderr)
        return 2
    if args.markdown:
        sys.stdout.write(render_markdown(result))
    else:
        json.dump(result, sys.stdout, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
