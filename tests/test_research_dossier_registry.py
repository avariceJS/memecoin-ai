from __future__ import annotations

import math

import pytest

from memecoin_experience.research_dossier_registry import CURRENT_KINDS, validate_reports


def envelope(kind: str = "availability_profile", digest: str = "a" * 64) -> dict:
    return {
        "schema": "brain-research-diagnostic.v1",
        "kind": kind,
        "input_sha256": digest,
        "summary": {"support": 0, "missing": None, "enabled": False},
        "details": {"rows": [], "note": "explicit empty support"},
        "limitations": ["Synthetic diagnostic only."],
    }


def test_current_kinds_are_explicit_and_exclude_held_producers() -> None:
    assert isinstance(CURRENT_KINDS, frozenset)
    assert len(CURRENT_KINDS) == 21
    assert {"paired_loss_comparison", "paired_loss_missing_bounds", "paired_loss_cluster_influence",
            "paired_loss_signflip", "paired_loss_target_mix"} <= CURRENT_KINDS
    assert "binary_calibration" not in CURRENT_KINDS
    assert "representation_shift" not in CURRENT_KINDS
    assert "split_target_deviation" not in CURRENT_KINDS
    assert "weight_state_delta" not in CURRENT_KINDS


def test_validates_and_sorts_without_losing_declared_values() -> None:
    a = envelope("availability_profile", "f" * 64)
    b = envelope("paired_loss_signflip", "0" * 64)
    b["summary"] = {"huge": 10**5000, "zero": 0, "false": False, "null": None}
    b["details"] = [{"exact": "caller supplied", "unknown": {"n": None}}]
    original = [a, b]

    validated = validate_reports(original)

    assert [(item["kind"], item["input_sha256"]) for item in validated] == [
        ("availability_profile", "f" * 64), ("paired_loss_signflip", "0" * 64),
    ]
    assert validated[1] == b
    assert validated[1] is not b
    assert validated[1]["summary"]["huge"] == 10**5000
    assert original == [a, b]


def test_accepts_empty_reports_and_zero_null_missing_and_empty_details() -> None:
    assert validate_reports([]) == []
    report = envelope()
    report["summary"] = {"zero": 0, "null": None, "false": False}
    report["details"] = []
    assert validate_reports([report]) == [report]


@pytest.mark.parametrize("reports", [None, {}, (), "reports", 1])
def test_rejects_non_list_reports(reports: object) -> None:
    with pytest.raises(ValueError, match="^research_report_reports$"):
        validate_reports(reports)


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda item: item.update(schema="population-diagnostic.v1"), "research_report_schema"),
        (lambda item: item.update(kind="split_stratum_support_v99"), "research_report_kind"),
        (lambda item: item.update(kind="binary_calibration"), "research_report_kind"),
        (lambda item: item.update(kind="representation_shift"), "research_report_kind"),
        (lambda item: item.update(input_sha256="A" * 64), "research_report_input_sha256"),
        (lambda item: item.update(input_sha256=0), "research_report_input_sha256"),
        (lambda item: item.update(summary=[]), "research_report_summary"),
        (lambda item: item.update(details="not structured"), "research_report_details"),
        (lambda item: item.update(limitations=[""]), "research_report_limitations"),
        (lambda item: item.update(extra=True), "research_report_envelope"),
    ],
)
def test_rejects_wrong_envelope_contract(mutate, error: str) -> None:
    item = envelope()
    mutate(item)
    with pytest.raises(ValueError, match=f"^{error}$"):
        validate_reports([item])


def test_rejects_duplicate_kind_hash_identity() -> None:
    with pytest.raises(ValueError, match="^research_report_duplicate_report$"):
        validate_reports([envelope(), envelope()])


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, object(), {1: "non-string key"}, {"set"}])
def test_rejects_non_json_python_values(bad: object) -> None:
    item = envelope()
    item["details"] = {"bad": bad}
    with pytest.raises(ValueError, match="^research_report_non_json_value$"):
        validate_reports([item])


def test_rejects_population_and_split_specific_schema_adapters() -> None:
    for schema in ("brain-population-diagnostic.v1", "brain-split-diagnostic.v1"):
        item = envelope()
        item["schema"] = schema
        with pytest.raises(ValueError, match="^research_report_schema$"):
            validate_reports([item])
