import hashlib
import json
import subprocess
import sys

import pytest

from memecoin_experience.source_value_concordance import analyze


def payload():
    return {
        "sources": ["alpha", "beta"],
        "absolute_tolerance": 0.1,
        "relative_tolerance": 0.01,
        "observations": [
            {"source": "alpha", "id": "exact", "values": {"x": 2.0, "y": 1}},
            {"source": "beta", "id": "exact", "values": {"x": 2.0, "y": 1}},
            {"source": "alpha", "id": "near", "values": {"x": 10}},
            {"source": "beta", "id": "near", "values": {"x": 10.15}},
            {"source": "alpha", "id": "far", "values": {"x": 1}},
            {"source": "beta", "id": "far", "values": {"x": 2}},
            {"source": "alpha", "id": "a-null", "values": {"x": None}},
            {"source": "alpha", "id": "a-only", "values": {"x": 5}},
            {"source": "beta", "id": "b-only", "values": {"x": 7}},
        ],
    }


def test_pairwise_support_tolerance_deltas_and_null_rates():
    result = analyze(payload())
    assert result["schema"] == "brain-research-diagnostic.v1"
    assert result["kind"] == "source_value_concordance"
    detail = result["details"][0]
    assert (detail["joint_support"], detail["agreement_count"], detail["disagreement_count"]) == (3, 2, 1)
    assert detail["left_only_known_count"] == detail["right_only_known_count"] == 1
    assert detail["both_missing_count"] == 1
    assert detail["left_absent_row_count"] == 1
    assert detail["right_absent_row_count"] == 2
    assert detail["value_deltas"] == [
        {"id": "exact", "delta": 0.0}, {"id": "far", "delta": 1}, {"id": "near", "delta": pytest.approx(0.15000000000000036)}
    ]
    # The feature is present in 5 alpha rows (one null), and 4 beta rows (none null).
    assert result["summary"]["null_rates"]["x"]["alpha"]["null_rate"] == 0.2
    assert result["summary"]["null_rates"]["x"]["beta"]["null_rate"] == 0


def test_zero_joint_support_and_empty_null_rate_denominators():
    result = analyze({
        "sources": ["a", "b"], "absolute_tolerance": 0, "relative_tolerance": 0,
        "observations": [{"source": "a", "id": "x", "values": {"f": None}}],
    })
    assert result["details"][0]["joint_support"] == 0
    assert result["details"][0]["agreement_rate"] is None
    assert result["summary"]["null_rates"]["f"]["b"]["null_rate"] is None


def test_hash_and_result_are_deterministic():
    data = payload()
    expected = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    assert analyze(data)["input_sha256"] == expected
    assert analyze(data) == analyze(data)


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(extra=1),
    lambda p: p.update(sources=["a", "a"]),
    lambda p: p.update(absolute_tolerance=True),
    lambda p: p.update(relative_tolerance=float("inf")),
    lambda p: p.update(absolute_tolerance=-1),
    lambda p: p.update(observations=[{"source": "alpha", "id": "x", "values": {"f": True}}]),
    lambda p: p.update(observations=[
        {"source": "alpha", "id": "x", "values": {}},
        {"source": "alpha", "id": "x", "values": {"f": 1}},
    ]),
])
def test_invalid_inputs_raise_value_error(mutate):
    data = payload()
    mutate(data)
    with pytest.raises(ValueError):
        analyze(data)


def test_cli_reads_json_and_rejects_duplicate_keys_or_nonfinite_constants():
    command = [sys.executable, "-m", "memecoin_experience.source_value_concordance"]
    valid = subprocess.run(command, input=json.dumps(payload()), text=True, capture_output=True, check=False)
    assert valid.returncode == 0 and not valid.stderr
    assert json.loads(valid.stdout) == analyze(payload())
    for raw in (
        '{"sources":["a","b"],"absolute_tolerance":0,"absolute_tolerance":1,"relative_tolerance":0,"observations":[]}',
        '{"sources":["a","b"],"absolute_tolerance":NaN,"relative_tolerance":0,"observations":[]}',
    ):
        invalid = subprocess.run(command, input=raw, text=True, capture_output=True, check=False)
        assert invalid.returncode != 0 and not invalid.stdout
