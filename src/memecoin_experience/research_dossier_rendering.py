"""Safe JSON adapters and readable Markdown rendering for research dossiers."""

from __future__ import annotations

import re
from typing import Any

from memecoin_experience.paired_loss_report import (
    _json_copy,
    _json_output,
    _parse_integer,
)


_INTEGER_TOKEN = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
_REPORT_KEYS = {"schema", "kind", "input_sha256", "summary", "details", "limitations"}


def copy_json(value: Any) -> Any:
    """Validate and detach JSON data, retaining integers of any size."""
    try:
        return _json_copy(value)
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise ValueError("research_report_non_json_value") from exc


def dumps_json(value: Any, *, indent: int | None = None) -> str:
    """Serialize JSON deterministically without Python's integer digit cap."""
    if indent is not None and (type(indent) is not int or indent < 0):
        raise ValueError("research_report_indent")
    detached = copy_json(value)
    try:
        return _json_output(detached, indent=indent)
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise ValueError("research_report_non_json_value") from exc


def parse_integer(token: str) -> int:
    """Parse a JSON integer token in chunks, without changing process limits."""
    if not isinstance(token, str) or not _INTEGER_TOKEN.fullmatch(token):
        raise ValueError("research_report_integer")
    try:
        return _parse_integer(token)
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise ValueError("research_report_integer") from exc


def _json_fence(value: Any) -> str:
    encoded = dumps_json(value, indent=2)
    longest = max((len(run) for run in re.findall(r"`+", encoded)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}json\n{encoded}\n{fence}"


def _validate_bundle(bundle: Any) -> dict:
    detached = copy_json(bundle)
    if not isinstance(detached, dict) or detached.get("schema") != "brain-research-bundle.v1":
        raise ValueError("research_report_bundle")
    reports = detached.get("reports")
    count = detached.get("report_count")
    if not isinstance(reports, list) or type(count) is not int or count != len(reports):
        raise ValueError("research_report_bundle")
    if not isinstance(detached.get("hash_semantics"), str):
        raise ValueError("research_report_bundle")
    if not isinstance(detached.get("limitations"), list) or any(
        not isinstance(item, str) for item in detached["limitations"]
    ):
        raise ValueError("research_report_bundle")
    if "mode" in detached and detached["mode"] != "current":
        raise ValueError("research_report_bundle")
    for report in reports:
        if not isinstance(report, dict) or set(report) != _REPORT_KEYS:
            raise ValueError("research_report_bundle")
        if report.get("schema") != "brain-research-diagnostic.v1":
            raise ValueError("research_report_bundle")
        if not isinstance(report.get("kind"), str) or not isinstance(report.get("input_sha256"), str):
            raise ValueError("research_report_bundle")
        if not isinstance(report.get("summary"), dict):
            raise ValueError("research_report_bundle")
        if not isinstance(report.get("details"), (dict, list)):
            raise ValueError("research_report_bundle")
        if not isinstance(report.get("limitations"), list) or any(
            not isinstance(item, str) for item in report["limitations"]
        ):
            raise ValueError("research_report_bundle")
    return detached


def render_markdown(bundle: dict) -> str:
    """Render supplied declarations legibly and retain the complete exact bundle."""
    try:
        detached = _validate_bundle(bundle)
    except ValueError as exc:
        if str(exc).startswith("research_report_"):
            raise
        raise ValueError("research_report_bundle") from exc

    sections = [
        "# Reproducible research dossier",
        "",
        "This overview repeats supplied report declarations for reading. Values are preserved as data; no cross-report aggregates, denominators, comparability, quality ranking, or winners are inferred.",
        "",
        f"Report count: {detached['report_count']}",
        "",
        "Hash semantics:",
        _json_fence(detached["hash_semantics"]),
        "",
    ]
    for index, report in enumerate(detached["reports"], start=1):
        sections.extend([
            f"## Report {index}",
            "",
            "Kind:",
            _json_fence(report["kind"]),
            "",
            "Declared input SHA-256 (full value):",
            _json_fence(report["input_sha256"]),
            "",
            "Supplied summary:",
            _json_fence(report["summary"]),
            "",
            "Supplied support / details:",
            _json_fence(report["details"]),
            "",
            "Supplied limitations:",
            _json_fence(report["limitations"]),
            "",
        ])
    sections.extend([
        "## Bundle limitations",
        "",
        _json_fence(detached["limitations"]),
        "",
        "## Complete exact bundle",
        "",
        _json_fence(detached),
        "",
    ])
    return "\n".join(sections)
