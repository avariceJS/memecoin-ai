import copy
import hashlib
import json
import math
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from memecoin_experience.weight_concentration import analyze


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".runtime" / "python"
MODULE = "memecoin_experience.weight_concentration"
LIMITATIONS = [
    "Descriptive weight concentration only; Kish effective sample size does not establish statistical independence, uncertainty, representativeness, or learning quality.",
    "Example, entity and cohort identities and weights are caller claims; no missing population, provenance or omitted examples are inferred.",
    "Entity weights aggregate across cohorts in the total; cohort entity-square sums need not add to the global entity-square sum.",
    "Only finite weights equal to zero or within [1e-100, 1e100] and at most 10000 examples are accepted; results use binary64 arithmetic.",
]


def row(identifier, entity, cohort, weight):
    return {"id": identifier, "entity": entity, "cohort": cohort, "weight": weight}


FIXTURE = {
    "examples": [
        row("1", "a", "x", 2),
        row("2", "a", "y", 1),
        row("3", "b", "y", 1),
        row("4", "c", "z", 0),
    ]
}


def _fraction_metric(rows):
    """Independent real-arithmetic oracle over exact rational input values."""
    weights = [Fraction(item["weight"]) for item in rows]
    total = sum(weights, Fraction())
    groups = {}
    for item, weight in zip(rows, weights):
        groups.setdefault(item["entity"], Fraction())
        groups[item["entity"]] += weight
    qx = sum((weight * weight for weight in weights), Fraction())
    qe = sum((mass * mass for mass in groups.values()), Fraction())
    positive_examples = sum(weight > 0 for weight in weights)
    positive_entities = sum(mass > 0 for mass in groups.values())
    result = {
        "status": "positive_weight" if total else "zero_total_weight",
        "example_count": len(rows),
        "positive_example_count": positive_examples,
        "entity_count": len(groups),
        "positive_entity_count": positive_entities,
        "sum_weights": float(total),
        "sum_squared_example_weights": float(qx),
        "sum_squared_entity_weights": float(qe),
        "example_concentration": float(qx / total**2) if total else None,
        "entity_concentration": float(qe / total**2) if total else None,
        "example_ess": float(total**2 / qx) if total else None,
        "entity_ess": float(total**2 / qe) if total else None,
    }
    return result


def _assert_metric_close(actual, expected, *, tiny=False):
    assert set(expected).issubset(actual)
    for key, value in expected.items():
        observed = actual[key]
        if isinstance(value, float):
            assert math.isclose(observed, value, rel_tol=1e-12, abs_tol=0.0 if tiny else 1e-12), key
        else:
            assert observed == value, key


def _cli(data: bytes):
    return subprocess.run(
        [str(PYTHON), "-m", MODULE],
        input=data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=ROOT,
        check=False,
    )


def test_exact_fixture_and_cross_cohort_entity_identity():
    result = analyze(FIXTURE)
    assert set(result) == {"schema", "kind", "input_sha256", "summary", "details", "limitations"}
    assert result["schema"] == "brain-research-diagnostic.v1"
    assert result["kind"] == "weight_concentration"
    assert result["input_sha256"] == "25a51ac45b86029eb994b969373848b609777a3ee3b70547b17ba628f787e79f"
    assert result["limitations"] == LIMITATIONS
    _assert_metric_close(
        {key: result["summary"][key] for key in _fraction_metric(FIXTURE["examples"])},
        _fraction_metric(FIXTURE["examples"]),
    )
    assert result["summary"]["cohort_count"] == 3
    assert result["summary"]["positive_cohort_count"] == 2
    assert [(item["entity"], item["weight_share"]) for item in result["details"]["entities"]] == [
        ("a", 0.75),
        ("b", 0.25),
        ("c", 0.0),
    ]
    assert [(item["cohort"], item["weight_share"]) for item in result["details"]["cohorts"]] == [
        ("x", 0.5),
        ("y", 0.5),
        ("z", 0.0),
    ]
    cohort_qe = sum(item["metrics"]["sum_squared_entity_weights"] for item in result["details"]["cohorts"])
    assert cohort_qe == 6.0
    assert result["summary"]["sum_squared_entity_weights"] == 10.0
    assert result["details"]["cohorts"][2]["metrics"]["status"] == "zero_total_weight"
    assert result["details"]["cohorts"][2]["metrics"]["entity_ess"] is None


@pytest.mark.parametrize(
    "examples",
    [
        [],
        [row("z", "only", "empty", 0)],
        [row("a", "ea", "ca", 1), row("b", "eb", "cb", 1)],
        [row("a", "same", "c", 1), row("b", "same", "c", 1)],
        [row("a", "e", "c", 4), row("b", "zero", "z", 0)],
        [row("a", "e1", "c1", 0.5), row("b", "e1", "c2", 1.25), row("c", "e2", "c2", 2.0)],
    ],
)
def test_basic_support_patterns_match_independent_fraction_oracle(examples):
    result = analyze({"examples": examples})
    _assert_metric_close(result["summary"], _fraction_metric(examples))
    assert result["summary"]["cohort_count"] == len({item["cohort"] for item in examples})


def test_permutation_hash_nonmutation_scaling_and_conservation():
    payload = {"examples": [row("d", "shared", "two", 1.5), row("a", "shared", "one", 2.0), row("b", "other", "one", 0.5), row("c", "zero", "z", 0)]}
    original = copy.deepcopy(payload)
    result = analyze(payload)
    reverse = analyze({"examples": list(reversed(payload["examples"]))})
    assert payload == original
    assert result["summary"] == reverse["summary"]
    assert result["details"]["examples"] == reverse["details"]["examples"]
    assert result["details"]["entities"] == reverse["details"]["entities"]
    assert result["details"]["cohorts"] == reverse["details"]["cohorts"]
    assert result["input_sha256"] != reverse["input_sha256"]

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    assert result["input_sha256"] == hashlib.sha256(canonical).hexdigest()
    float_encoded = {"examples": [row("a", "e", "c", 2.0)]}
    int_encoded = {"examples": [row("a", "e", "c", 2)]}
    assert analyze(float_encoded)["input_sha256"] != analyze(int_encoded)["input_sha256"]

    for factor in (Fraction(1, 8), Fraction(10**20)):
        scaled = copy.deepcopy(payload)
        for item in scaled["examples"]:
            item["weight"] = float(Fraction(str(item["weight"])) * factor)
        scaled_result = analyze(scaled)
        for key in ("example_concentration", "entity_concentration", "example_ess", "entity_ess"):
            assert math.isclose(scaled_result["summary"][key], result["summary"][key], rel_tol=1e-12, abs_tol=1e-12)
        assert math.isclose(scaled_result["summary"]["sum_weights"], result["summary"]["sum_weights"] * float(factor), rel_tol=1e-12)
        for key in ("sum_squared_example_weights", "sum_squared_entity_weights"):
            summary_key = "sum_squared_" + ("example" if key.endswith("example_weights") else "entity") + "_weights"
            assert math.isclose(
                scaled_result["summary"][summary_key],
                result["summary"][summary_key] * float(factor) ** 2,
                rel_tol=1e-12,
            )

    summary = result["summary"]
    assert sum(item["metrics"]["sum_weights"] for item in result["details"]["entities"]) == summary["sum_weights"]
    assert sum(item["metrics"]["sum_weights"] for item in result["details"]["cohorts"]) == summary["sum_weights"]
    assert math.isclose(sum(item["metrics"]["sum_squared_example_weights"] for item in result["details"]["entities"]), summary["sum_squared_example_weights"])
    assert math.isclose(sum(item["metrics"]["sum_squared_example_weights"] for item in result["details"]["cohorts"]), summary["sum_squared_example_weights"])
    assert math.isclose(sum(item["metrics"]["sum_squared_entity_weights"] for item in result["details"]["entities"]), summary["sum_squared_entity_weights"])
    for axis in ("entities", "cohorts"):
        groups = result["details"][axis]
        assert sum(item["metrics"]["example_count"] for item in groups) == summary["example_count"]
        assert sum(item["metrics"]["positive_example_count"] for item in groups) == summary["positive_example_count"]
        assert math.isclose(sum(item["weight_share"] for item in groups), 1.0)
    assert summary["entity_ess"] <= summary["example_ess"]


def test_bounds_and_10000_rows_have_finite_metrics():
    boundary = analyze({"examples": [row("low", "e1", "c", 1e-100), row("high", "e2", "c", 1e100), row("zero", "e3", "z", 0)]})
    assert boundary["summary"]["sum_weights"] == pytest.approx(1e100)
    assert boundary["details"]["examples"][0]["weight_share"] > 0
    tiny = analyze({"examples": [row("tiny", "e", "c", 1e-100)]})
    assert tiny["summary"]["sum_squared_example_weights"] > 0
    assert tiny["summary"]["sum_squared_entity_weights"] > 0
    _assert_metric_close(
        tiny["summary"],
        _fraction_metric([row("tiny", "e", "c", 1e-100)]),
        tiny=True,
    )

    many = [row(f"{i:05d}", "one-entity", "one-cohort", 1e100) for i in range(10_000)]
    summary = analyze({"examples": many})["summary"]
    assert summary["sum_squared_entity_weights"] == 1e208
    assert summary["example_ess"] == pytest.approx(10_000)
    assert summary["entity_ess"] == pytest.approx(1)
    with pytest.raises(ValueError, match="^weight_concentration_"):
        analyze({"examples": many + [row("extra", "e", "c", 1)]})


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"examples": [], "extra": 0},
        {"examples": {}},
        {"examples": [None]},
        {"examples": [{"id": "a", "entity": "e", "cohort": "c", "weight": 1, "extra": 1}]},
        {"examples": [row("a", "e", "c", True)]},
        {"examples": [row("a", "e", "c", None)]},
        {"examples": [row("a", "e", "c", "1")]},
        {"examples": [row("a", "e", "c", -1)]},
        {"examples": [row("a", "e", "c", 1e-101)]},
        {"examples": [row("a", "e", "c", 1e101)]},
        {"examples": [row("a", "e", "c", float("inf"))]},
        {"examples": [row("a", "e", "c", float("nan"))]},
        {"examples": [row("a", "e", "c", 10**100 + 1)]},
        {"examples": [row("a", "e", "c", 1), row("a", "f", "d", 2)]},
        {"examples": [row("", "e", "c", 1)]},
        {"examples": [row("a" * 129, "e", "c", 1)]},
        {"examples": [row("a b", "e", "c", 1)]},
        {"examples": [row("å", "e", "c", 1)]},
        {"examples": [row("a", "e\n", "c", 1)]},
    ],
)
def test_invalid_payloads_raise_prefixed_value_error(payload):
    with pytest.raises(ValueError, match="^weight_concentration_"):
        analyze(payload)


def test_repeated_entity_and_cohort_are_valid_independent_axes():
    result = analyze({"examples": [row("1", "e", "c1", 1), row("2", "e", "c2", 2), row("3", "f", "c2", 0)]})
    assert result["summary"]["entity_count"] == 2
    assert result["summary"]["cohort_count"] == 2
    assert result["summary"]["sum_squared_entity_weights"] == 9


def test_cli_success_is_compact_deterministic_and_matches_analyze():
    encoded = json.dumps(FIXTURE, separators=(",", ":"), ensure_ascii=False).encode()
    first = _cli(encoded)
    second = _cli(encoded)
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    assert first.stdout.endswith(b"\n") and first.stdout.count(b"\n") == 1
    assert json.loads(first.stdout) == analyze(FIXTURE)
    assert first.stderr == b""


@pytest.mark.parametrize(
    "bad_json",
    [
        b'{"examples":[],"examples":[]}',
        b'{"examples":[{"id":"a","id":"b","entity":"e","cohort":"c","weight":1}]}',
        b'{"examples":[],"nested":{"x":1,"x":2}}',
        b'{"examples":[{"id":"a","entity":"e","cohort":"c","weight":NaN}]}',
        b'{"examples":[{"id":"a","entity":"e","cohort":"c","weight":Infinity}]}',
        b'{"examples":[]} trailing',
        b'{"examples":[]} {"examples":[]}',
        b"\xff",
        b"",
    ],
)
def test_cli_invalid_json_has_exit_two_empty_stdout_and_no_traceback(bad_json):
    result = _cli(bad_json)
    assert result.returncode == 2
    assert result.stdout == b""
    assert result.stderr.startswith(b"weight_concentration_")
    assert b"Traceback" not in result.stderr
    assert result.stderr.endswith(b"\n")


def test_cli_rejects_raw_input_over_eight_mib_before_full_parse():
    result = _cli(b" " * (8 * 1024 * 1024 + 1))
    assert result.returncode == 2
    assert result.stdout == b""
    assert result.stderr.startswith(b"weight_concentration_")
    assert b"Traceback" not in result.stderr
