from __future__ import annotations

import json
import subprocess
import sys

import pytest

from memecoin_experience.research_report import analyze, render_markdown


def report(kind: str, digest: str, summary: dict | None = None) -> dict:
    return {
        "schema": "brain-research-diagnostic.v1",
        "kind": kind,
        "input_sha256": digest,
        "summary": summary or {"support": 0},
        "details": {"cohort": {"n": None, "note": "missing remains explicit"}},
        "limitations": ["Synthetic caller supplied result; diagnostic only."],
    }


def test_orders_reports_deterministically_and_preserves_full_envelopes() -> None:
    first = report("exposure_shift", "b" * 64, {"rate": 0.25})
    second = report("availability_profile", "f" * 64)
    third = report("availability_profile", "a" * 64)
    payload = {"reports": [first, second, third]}
    before = json.loads(json.dumps(payload))

    result = analyze(payload)

    assert result["schema"] == "brain-research-bundle.v1"
    assert result["report_count"] == 3
    assert [(r["kind"], r["input_sha256"]) for r in result["reports"]] == [
        ("availability_profile", "a" * 64),
        ("availability_profile", "f" * 64),
        ("exposure_shift", "b" * 64),
    ]
    assert result["reports"][2] == first
    assert result["reports"][0]["details"] == third["details"]
    assert payload == before
    assert "not verified" in result["hash_semantics"]
    assert "Missing fields" in result["limitations"][2]


def test_empty_bundle_and_empty_details_are_not_filled() -> None:
    assert analyze({"reports": []})["reports"] == []
    envelope = report("abstention_frontier", "0" * 64)
    envelope["details"] = []
    assert analyze({"reports": [envelope]})["reports"][0]["details"] == []


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda r: r.update(schema="wrong"), "research_report_schema"),
        (lambda r: r.update(kind="unknown"), "research_report_kind"),
        (lambda r: r.update(input_sha256="A" * 64), "research_report_input_sha256"),
        (lambda r: r.update(summary=[]), "research_report_summary"),
        (lambda r: r.update(details="not structured"), "research_report_details"),
        (lambda r: r.update(limitations=[""]), "research_report_limitations"),
        (lambda r: r.update(extra=True), "research_report_envelope"),
    ],
)
def test_rejects_malformed_envelopes(mutate, error: str) -> None:
    invalid = report("availability_profile", "a" * 64)
    mutate(invalid)
    with pytest.raises(ValueError, match=f"^{error}$"):
        analyze({"reports": [invalid]})


def test_rejects_duplicate_identity_and_nonfinite_nested_values() -> None:
    one = report("availability_profile", "a" * 64)
    with pytest.raises(ValueError, match="^research_report_duplicate_report$"):
        analyze({"reports": [one, one]})
    one["summary"]["bad"] = float("nan")
    with pytest.raises(ValueError, match="^research_report_non_json_value$"):
        analyze({"reports": [one]})


def test_markdown_uses_fence_longer_than_caller_backticks_and_keeps_html_as_data() -> None:
    envelope = report("binary_calibration", "c" * 64, {"label": "```\n<script>alert(1)</script>"})
    markdown = render_markdown(analyze({"reports": [envelope]}))
    assert markdown.startswith("# Reproducible research dossier")
    assert "````json\n" in markdown
    assert "<script>alert(1)</script>" in markdown
    assert markdown.rstrip().endswith("````")


def test_cli_json_and_markdown_modes() -> None:
    payload = {"reports": [report("representation_shift", "d" * 64)]}
    encoded = json.dumps(payload)
    json_run = subprocess.run(
        [sys.executable, "-m", "memecoin_experience.research_report"],
        input=encoded, text=True, capture_output=True, check=True,
    )
    assert json.loads(json_run.stdout) == analyze(payload)
    assert json_run.stderr == ""

    markdown_run = subprocess.run(
        [sys.executable, "-m", "memecoin_experience.research_report", "--markdown"],
        input=encoded, text=True, capture_output=True, check=True,
    )
    assert markdown_run.stdout == render_markdown(analyze(payload))


def test_cli_rejects_nonfinite_json() -> None:
    run = subprocess.run(
        [sys.executable, "-m", "memecoin_experience.research_report"],
        input='{"reports": [], "extra": NaN}', text=True, capture_output=True,
    )
    assert run.returncode == 2
    assert "research_report_nonfinite_json" in run.stderr
