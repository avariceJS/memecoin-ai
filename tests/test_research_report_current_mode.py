"""Interface checks using the real merged registry and rendering helpers."""

from __future__ import annotations

import json
import sys

import pytest

from memecoin_experience import research_report
from memecoin_experience import research_dossier_registry as registry
from memecoin_experience import research_dossier_rendering as rendering


def helpers(monkeypatch, *, seen=None):
    original = registry.validate_reports

    def validate_reports(reports):
        if seen is not None:
            seen.append(reports)
        return original(reports)

    monkeypatch.setattr(registry, "validate_reports", validate_reports)
    return rendering


def sample(kind="availability_profile", number=0):
    return {
        "schema": "brain-research-diagnostic.v1",
        "kind": kind,
        "input_sha256": "a" * 64,
        "summary": {"support": number},
        "details": {"support": number},
        "limitations": ["Synthetic fixture."],
    }


def test_default_remains_legacy_and_current_is_explicit(monkeypatch):
    seen = []
    helpers(monkeypatch, seen=seen)
    payload = {"reports": [sample()]}

    legacy = research_report.analyze(payload)
    current = research_report.analyze(payload, mode="current")

    assert "mode" not in legacy
    assert current["schema"] == legacy["schema"] == "brain-research-bundle.v1"
    assert current["mode"] == "current"
    assert current["reports"] == legacy["reports"]
    assert seen == [payload["reports"]]


def test_unknown_mode_and_payload_shape_use_public_errors(monkeypatch):
    helpers(monkeypatch)
    with pytest.raises(ValueError, match="^research_report_mode$"):
        research_report.analyze({"reports": []}, mode="other")
    with pytest.raises(ValueError, match="^research_report_payload$"):
        research_report.analyze({}, mode="current")


def test_current_markdown_delegates_to_renderer(monkeypatch):
    helpers(monkeypatch)
    bundle = research_report.analyze({"reports": []}, mode="current")
    expected = rendering.render_markdown(bundle)
    seen = []
    original = rendering.render_markdown

    def render(value):
        seen.append(value)
        return original(value)

    monkeypatch.setattr(rendering, "render_markdown", render)
    assert research_report.render_markdown(bundle) == expected
    assert seen == [bundle]


def test_current_cli_uses_helper_integer_parser_and_json_writer(monkeypatch, capsys):
    import io

    rendering = helpers(monkeypatch)
    parsed = []
    original = rendering.parse_integer
    monkeypatch.setattr(rendering, "parse_integer", lambda token: parsed.append(token) or original(token))
    report = sample()
    report["summary"] = {"large": 123456789012345678901234567890}
    input_text = json.dumps({"reports": [report]})
    monkeypatch.setattr(sys, "stdin", io.StringIO(input_text))

    assert research_report.main(["--mode", "current"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    result = json.loads(output.out)
    assert result["mode"] == "current"
    assert result["reports"][0]["summary"]["large"] == 123456789012345678901234567890
    assert "123456789012345678901234567890" in parsed


def test_legacy_cli_duplicate_and_nonfinite_json_still_return_code_two(monkeypatch, capsys):
    import io

    for source, expected in (
        ('{"reports": [], "reports": []}', "research_report_duplicate_key"),
        ('{"reports": [], "x": NaN}', "research_report_nonfinite_json"),
    ):
        monkeypatch.setattr(sys, "stdin", io.StringIO(source))
        assert research_report.main([]) == 2
        assert expected in capsys.readouterr().err
