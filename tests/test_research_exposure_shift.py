from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys

import pytest

from memecoin_population.exposure_shift import analyze


def test_attributes_concentrated_exposure_with_unknown_and_zero_cases() -> None:
    payload = {
        "entities": [
            {"entity": "alpha", "corpus": 5, "exposure": 8},
            {"entity": "beta", "corpus": 3, "exposure": 1},
            {"entity": "gamma", "corpus": 2, "exposure": 0},
            {"entity": "delta", "corpus": 0, "exposure": 1},
            {"entity": "corpus-only", "corpus": 4, "exposure": None},
            {"entity": "exposure-only", "corpus": None, "exposure": 3},
            {"entity": "unknown", "corpus": None, "exposure": None},
        ]
    }

    result = analyze(payload)
    assert result["schema"] == "brain-research-diagnostic.v1"
    assert result["kind"] == "exposure_shift"
    assert result["input_sha256"] == hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()
    summary = result["summary"]
    assert summary["common_known_count"] == 4
    assert summary["corpus_missing_count"] == 2
    assert summary["exposure_missing_count"] == 2
    assert summary["common_corpus_total"] == 10
    assert summary["common_exposure_total"] == 10
    assert summary["corpus_positive_mass_outside_common_support"] == 4
    assert summary["exposure_positive_mass_outside_common_support"] == 3
    assert summary["total_variation"] == pytest.approx(0.4)

    by_name = {row["entity"]: row for row in result["details"]["entities"]}
    assert by_name["alpha"]["share_delta"] == pytest.approx(0.3)
    assert by_name["alpha"]["share_ratio"] == pytest.approx(1.6)
    assert by_name["delta"]["share_ratio"] is None
    assert by_name["delta"]["explicit_zero_mismatch"] is True
    assert by_name["corpus-only"]["common_support"] is False
    assert by_name["corpus-only"]["unmatched_positive_corpus_mass"] == 4
    assert by_name["exposure-only"]["unmatched_positive_exposure_mass"] == 3
    ranked = [row["entity"] for row in result["details"]["ranked_contributors"]]
    assert ranked[0] == "alpha"
    assert set(ranked[1:]) == {"beta", "gamma", "delta"}


def test_zero_common_total_has_no_shares_or_total_variation() -> None:
    result = analyze({"entities": [{"entity": "a", "corpus": 0, "exposure": 1}]})
    assert result["summary"]["total_variation"] is None
    assert result["details"]["entities"][0]["share_delta"] is None
    assert result["details"]["entities"][0]["corpus_share"] is None


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "payload_shape"),
        ({"entities": {}}, "entities_shape"),
        ({"entities": [{"entity": "a", "corpus": True, "exposure": 1}]}, "entity_weight"),
        ({"entities": [{"entity": "a", "corpus": math.inf, "exposure": 1}]}, "entity_weight"),
        (
            {"entities": [{"entity": "a", "corpus": 1, "exposure": 1}, {"entity": "a", "corpus": 2, "exposure": 2}]},
            "entity_duplicate",
        ),
        ({"entities": [{"entity": " ", "corpus": 1, "exposure": 1}]}, "entity_identity"),
    ],
)
def test_rejects_malformed_nonfinite_boolean_and_duplicate_input(
    payload: dict, message: str
) -> None:
    with pytest.raises(ValueError, match=f"^{message}$"):
        analyze(payload)


def test_module_cli_reads_json_and_emits_envelope() -> None:
    payload = {"entities": [{"entity": "a", "corpus": 1, "exposure": 2}]}
    completed = subprocess.run(
        [sys.executable, "-m", "memecoin_population.exposure_shift"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    output = json.loads(completed.stdout)
    assert output["kind"] == "exposure_shift"
    assert output["summary"]["total_variation"] == 0
    assert completed.stderr == ""
