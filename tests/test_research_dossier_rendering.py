from __future__ import annotations

import copy
import sys

import pytest

from memecoin_experience.research_dossier_rendering import (
    copy_json,
    dumps_json,
    parse_integer,
    render_markdown,
)


def _bundle() -> dict:
    return {
        "schema": "brain-research-bundle.v1",
        "mode": "current",
        "hash_semantics": "Declared hashes; raw inputs were not supplied.",
        "report_count": 1,
        "reports": [{
            "schema": "brain-research-diagnostic.v1",
            "kind": "availability_profile",
            "input_sha256": "a" * 64,
            "summary": {"zero": 0, "unknown": None, "hostile": "</table>\n| cell |\n```\n\x01"},
            "details": [{"supported": False, "label": "<script>alert(1)</script>"}],
            "limitations": ["Only supplied values are shown."],
        }],
        "limitations": ["No comparisons, denominators, or winners are inferred."],
    }


def test_json_adapter_detaches_and_preserves_exact_large_integers() -> None:
    cap = sys.get_int_max_str_digits() if hasattr(sys, "get_int_max_str_digits") else None
    huge = 10**5000
    original = {"nested": [huge, None, 0]}
    copied = copy_json(original)
    assert copied == original and copied is not original and copied["nested"] is not original["nested"]
    encoded = dumps_json(original, indent=2)
    assert "1" + "0" * 5000 in encoded
    assert parse_integer("-" + "9" * 5000) == -(10**5000 - 1)
    if cap is not None:
        assert sys.get_int_max_str_digits() == cap


@pytest.mark.parametrize("token", ["", "+1", "01", "-01", "1.0", "1e2", "--1"])
def test_parse_integer_accepts_only_json_integer_tokens(token: str) -> None:
    with pytest.raises(ValueError, match="^research_report_integer$"):
        parse_integer(token)


def test_markdown_is_readable_and_caller_values_stay_fenced_as_data() -> None:
    bundle = _bundle()
    original = copy.deepcopy(bundle)
    markdown = render_markdown(bundle)

    assert "## Report 1" in markdown
    assert "Kind:" in markdown and "availability_profile" in markdown
    assert "Declared input SHA-256 (full value):" in markdown
    assert "a" * 64 in markdown
    assert "Supplied summary:" in markdown and "Supplied support / details:" in markdown
    assert "Bundle limitations" in markdown and "Complete exact bundle" in markdown
    assert "````json\n" in markdown
    assert "<script>alert(1)</script>" in markdown
    assert "</table>" in markdown
    assert "\\u0001" in markdown
    assert markdown.endswith("\n")
    assert bundle == original
    assert markdown.count('"input_sha256"') == 1


def test_render_rejects_malformed_bundle_with_stable_safe_error() -> None:
    malformed = _bundle()
    malformed["reports"] = [{"kind": "untrusted\n# forged"}]
    malformed["report_count"] = 1
    with pytest.raises(ValueError, match="^research_report_bundle$"):
        render_markdown(malformed)


def test_copy_and_dumps_reject_non_json_values_with_research_report_error() -> None:
    with pytest.raises(ValueError, match="^research_report_non_json_value$"):
        copy_json({"bad": float("nan")})
    with pytest.raises(ValueError, match="^research_report_non_json_value$"):
        dumps_json({1: "non-string key"})
    with pytest.raises(ValueError, match="^research_report_indent$"):
        dumps_json({}, indent=-1)
