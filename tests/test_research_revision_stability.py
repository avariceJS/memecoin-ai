from __future__ import annotations

import copy
import itertools
import json
import math
import os
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import pytest

from memecoin_experience.revision_stability import analyze


ROOT = Path(__file__).resolve().parents[1]
INVALID = "revision_stability_invalid_input"
LIMITATIONS = [
    "Only caller-supplied numeric revision histories are analyzed; omitted revisions and provenance are unknown.",
    "The final supplied revision is a reference, not truth; a stable supplied suffix does not predict future revisions.",
    "Release ordering and times are caller claims; event, availability, receipt and decision times are not inferred.",
    "Tolerance is absolute in caller-declared value units; histories are not pooled and no accuracy, causal or completeness claim is made.",
]


def row(revision, released_ns, value):
    return {"revision": revision, "released_ns": released_ns, "value": value}


def observation(identity, revisions):
    return {"id": identity, "revisions": revisions}


def payload(observations=(), tolerance=1):
    return {"absolute_tolerance": tolerance, "observations": list(observations)}


def oracle(history, tolerance):
    """Independent enumerating oracle for step signs and suffix starts."""
    rows = sorted(history, key=lambda item: item["revision"])
    values = [Fraction.from_float(float(item["value"])) for item in rows]
    reference = values[-1]
    exact_tolerance = Fraction.from_float(float(tolerance))
    distances = [abs(value - reference) for value in values]
    flags = [distance <= exact_tolerance for distance in distances]
    # Enumerate starts directly rather than using the implementation's last
    # violation formulation.
    suffix_start = next(start for start in range(len(rows))
                        if all(flags[index] for index in range(start, len(rows))))
    steps = [values[index] - values[index - 1] for index in range(1, len(values))]
    signs = [1 if step > 0 else -1 for step in steps if step != 0]
    return {
        "suffix_start": suffix_start,
        "changed": len(signs),
        "reversals": sum(a != b for a, b in zip(signs, signs[1:])),
        "flags": flags,
        "deltas": [None if index == 0 else float(steps[index - 1])
                   for index in range(len(values))],
        "distances": [float(distance) for distance in distances],
    }


def cli(raw: bytes, *args: str):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    return subprocess.run(
        [sys.executable, "-m", "memecoin_experience.revision_stability", *args],
        input=raw, capture_output=True, cwd=ROOT, env=env, check=False,
    )


def assert_invalid(value):
    with pytest.raises(ValueError) as error:
        analyze(value)
    assert str(error.value) == INVALID


def test_hand_checked_histories_and_exact_output_contract():
    source = payload([
        observation("A", [row(0, 10, 2), row(1, 20, 5), row(2, 30, 1),
                           row(3, 40, 3), row(4, 50, 2)]),
        observation("B", [row(1, 0, -1), row(2, 0, -1), row(4, 2, -2)]),
        observation("C", [row(9, 0, 0)]),
    ])
    original = copy.deepcopy(source)
    result = analyze(source)
    assert source == original
    assert list(result) == ["schema", "absolute_tolerance", "summary", "observations", "limitations"]
    assert result["schema"] == "brain-revision-stability.v1"
    assert result["absolute_tolerance"] == 1.0
    assert result["summary"] == {
        "observation_count": 3, "revision_count": 9, "transition_count": 6,
        "changed_transition_count": 5, "direction_reversal_count": 3,
        "single_revision_count": 1,
    }
    assert result["limitations"] == LIMITATIONS
    a, b, c = result["observations"]
    assert [item["id"] for item in result["observations"]] == ["A", "B", "C"]
    assert a["stable_suffix"] == {"from_revision": 2, "from_released_ns": 30, "revision_count": 3}
    assert [item["delta_from_previous"] for item in a["revisions"]] == [None, 3.0, -4.0, 2.0, -1.0]
    assert [item["absolute_distance_to_reference"] for item in a["revisions"]] == [0.0, 3.0, 1.0, 1.0, 0.0]
    assert [item["within_tolerance"] for item in a["revisions"]] == [True, False, True, True, True]
    assert a["reference"] == {"kind": "final_supplied_revision", "revision": 4,
                               "released_ns": 50, "value": 2.0}
    assert b["stable_suffix"] == {"from_revision": 1, "from_released_ns": 0, "revision_count": 3}
    assert [item["delta_from_previous"] for item in b["revisions"]] == [None, 0.0, -1.0]
    assert c["stable_suffix"] == {"from_revision": 9, "from_released_ns": 0, "revision_count": 1}
    assert c["transition_count"] == c["changed_transition_count"] == c["direction_reversal_count"] == 0


def test_empty_singleton_constant_and_zero_tolerance():
    empty = analyze(payload())
    assert empty["observations"] == []
    assert empty["summary"] == {name: 0 for name in (
        "observation_count", "revision_count", "transition_count",
        "changed_transition_count", "direction_reversal_count", "single_revision_count")}
    assert empty["limitations"] == LIMITATIONS
    singleton = analyze(payload([observation("one", [row(3, 2, -0.0)])], 0))
    only = singleton["observations"][0]
    assert only["reference"]["value"] == 0.0
    assert math.copysign(1.0, only["revisions"][0]["value"]) == 1.0
    assert only["stable_suffix"]["revision_count"] == 1
    constant = analyze(payload([observation("flat", [row(0, 0, 4), row(8, 1, 4)])], 0))
    assert constant["observations"][0]["stable_suffix"]["from_revision"] == 0
    assert constant["observations"][0]["direction_reversal_count"] == 0


def test_boundary_nextafter_zero_steps_reversal_and_value_zero_crossing():
    tolerance = 0.5
    source = payload([observation("edges", [
        row(0, 0, math.nextafter(0.5, math.inf)),
        row(1, 1, 0.5), row(2, 2, 0.5), row(3, 3, 0.0),
    ])], tolerance)
    result = analyze(source)["observations"][0]
    assert [item["within_tolerance"] for item in result["revisions"]] == [False, True, True, True]
    assert result["stable_suffix"]["from_revision"] == 1
    assert result["direction_reversal_count"] == 0
    reversal = analyze(payload([observation("turn", [
        row(0, 0, 1), row(1, 1, 0), row(2, 2, 0), row(3, 3, 1),
    ])], 1))["observations"][0]
    # Step signs are -, 0, +, so zero does not reset the nonzero direction.
    assert reversal["direction_reversal_count"] == 1
    no_value_zero_crossing = analyze(payload([observation("positive", [
        row(0, 0, 10), row(1, 1, 12), row(2, 2, 11), row(3, 3, 11),
    ])], 10))["observations"][0]
    assert no_value_zero_crossing["direction_reversal_count"] == 1
    assert no_value_zero_crossing["stable_suffix"]["from_revision"] == 0


def test_suffix_properties_append_invalidation_and_float_exact_classification():
    values = [0, 2, 0, 0]
    base = [row(index, index, value) for index, value in enumerate(values)]
    old = analyze(payload([observation("x", base)], 1))["observations"][0]
    assert old["stable_suffix"]["from_revision"] == 2
    appended = analyze(payload([observation("x", base + [row(4, 4, 2)])], 1))["observations"][0]
    assert appended["stable_suffix"]["from_revision"] == 4
    for history in ([row(0, 0, 0), row(1, 1, 2), row(2, 2, 1)],
                    [row(2, 0, -2), row(7, 0, -1), row(10, 5, 0)]):
        narrow = analyze(payload([observation("x", history)], 0))["observations"][0]["stable_suffix"]["from_revision"]
        wide = analyze(payload([observation("x", history)], 2))["observations"][0]["stable_suffix"]["from_revision"]
        assert wide <= narrow
    adjacent = math.nextafter(0.1, math.inf)
    exact = analyze(payload([observation("x", [row(0, 0, adjacent), row(1, 1, 0.0)])], 0.1))["observations"][0]
    assert exact["revisions"][0]["within_tolerance"] is False
    assert exact["revisions"][0]["absolute_distance_to_reference"] == float(
        abs(Fraction.from_float(adjacent) - Fraction.from_float(0.0)))


def test_permutation_invariance_gaps_tied_release_and_separate_histories():
    left = payload([
        observation("z", [row(5, 5, 3), row(1, 0, 2)]),
        observation("a", [row(1, 0, 2), row(5, 0, 3)]),
    ], 1)
    right = payload(list(reversed([
        observation("z", [row(1, 0, 2), row(5, 5, 3)]),
        observation("a", [row(5, 0, 3), row(1, 0, 2)]),
    ])), 1)
    assert analyze(left) == analyze(right)
    assert [item["id"] for item in analyze(left)["observations"]] == ["a", "z"]


def test_small_exhaustive_histories_match_independent_brute_force_oracle():
    for length in range(1, 6):
        for values in itertools.product((-1, 0, 1), repeat=length):
            history = [row(index * 3, index, value) for index, value in enumerate(values)]
            for tolerance in (0, 1):
                actual = analyze(payload([observation("oracle", history)], tolerance))["observations"][0]
                expected = oracle(history, tolerance)
                assert actual["stable_suffix"]["revision_count"] == length - expected["suffix_start"]
                assert actual["changed_transition_count"] == expected["changed"]
                assert actual["direction_reversal_count"] == expected["reversals"]
                assert [item["within_tolerance"] for item in actual["revisions"]] == expected["flags"]
                assert [item["delta_from_previous"] for item in actual["revisions"]] == expected["deltas"]
                assert [item["absolute_distance_to_reference"] for item in actual["revisions"]] == expected["distances"]


@pytest.mark.parametrize("bad", [
    [], None, {"absolute_tolerance": 0}, {"absolute_tolerance": 0, "observations": [], "x": 1},
    {"absolute_tolerance": True, "observations": []},
    {"absolute_tolerance": -1, "observations": []},
    {"absolute_tolerance": 1e12 + 1, "observations": []},
    {"absolute_tolerance": float("nan"), "observations": []},
    {"absolute_tolerance": 10**400, "observations": []},
    {"absolute_tolerance": 0, "observations": {}},
    {"absolute_tolerance": 0, "observations": [observation("", [row(0, 0, 0)])]},
    {"absolute_tolerance": 0, "observations": [observation("é", [row(0, 0, 0)])]},
    {"absolute_tolerance": 0, "observations": [observation("a" * 65, [row(0, 0, 0)])]},
    {"absolute_tolerance": 0, "observations": [observation("x", [])]},
    {"absolute_tolerance": 0, "observations": [observation("x", [row(0, 0, 0)] * 65)]},
    {"absolute_tolerance": 0, "observations": [observation("x", [row(True, 0, 0)])]},
    {"absolute_tolerance": 0, "observations": [observation("x", [row(-1, 0, 0)])]},
    {"absolute_tolerance": 0, "observations": [observation("x", [row(2**31, 0, 0)])]},
    {"absolute_tolerance": 0, "observations": [observation("x", [row(0, True, 0)])]},
    {"absolute_tolerance": 0, "observations": [observation("x", [row(0, -1, 0)])]},
    {"absolute_tolerance": 0, "observations": [observation("x", [row(0, 2**63, 0)])]},
    {"absolute_tolerance": 0, "observations": [observation("x", [row(0, 0, None)])]},
    {"absolute_tolerance": 0, "observations": [observation("x", [row(0, 0, True)])]},
    {"absolute_tolerance": 0, "observations": [observation("x", [row(0, 0, float("inf"))])]},
    {"absolute_tolerance": 0, "observations": [observation("x", [row(0, 0, 1e12 + 1)])]},
    {"absolute_tolerance": 0, "observations": [observation("x", [dict(row(0, 0, 0), other=1)])]},
    {"absolute_tolerance": 0, "observations": [dict(observation("x", [row(0, 0, 0)]), other=1)]},
    {"absolute_tolerance": 0, "observations": [observation("x", [row(0, 2, 0), row(1, 1, 0)])]},
    {"absolute_tolerance": 0, "observations": [observation("x", [row(0, 0, 0), row(0, 1, 1)])]},
    {"absolute_tolerance": 0, "observations": [observation("x", [row(0, 0, 0)]), observation("x", [row(1, 1, 1)])]},
])
def test_invalid_api_inputs_raise_exact_value_error(bad):
    assert_invalid(bad)


def test_exact_numeric_time_ordinal_id_and_count_maxima_and_one_over():
    max_row = row(2**31 - 1, 2**63 - 1, 10**12)
    maximum = payload([observation("A" * 64, [max_row])], 10**12)
    assert analyze(maximum)["observations"][0]["reference"]["revision"] == 2**31 - 1
    assert_invalid(payload([observation("x", [row(0, 0, 0)])], 10**12 + 1))
    assert_invalid(payload([observation("x", [row(0, 0, 0)])] * 257))
    max_rows = [row(index, index, index) for index in range(64)]
    assert analyze(payload([observation("x", max_rows)]))["summary"]["revision_count"] == 64
    too_many_ids = [observation(f"x{index}", [row(0, 0, 0)]) for index in range(257)]
    assert_invalid(payload(too_many_ids))


def test_cyclic_wrong_shape_is_rejected_as_invalid_input():
    cyclic = {"id": "x", "revisions": []}
    cyclic["revisions"].append(cyclic)
    assert_invalid(payload([cyclic]))


def test_maximum_size_fixture():
    maximum = [observation(f"o{index:03d}", [row(rev, rev, rev) for rev in range(64)])
               for index in range(256)]
    result = analyze(payload(maximum))
    assert result["summary"]["observation_count"] == 256
    assert result["summary"]["revision_count"] == 256 * 64


def test_cli_success_is_compact_and_matches_api():
    source = payload([observation("cafe", [row(1, 0, 1), row(2, 1, 2)])], 1)
    raw = json.dumps(source, ensure_ascii=False).encode()
    completed = cli(raw)
    assert completed.returncode == 0
    assert completed.stderr == b""
    assert completed.stdout.endswith(b"\n") and completed.stdout.count(b"\n") == 1
    assert completed.stdout == (json.dumps(analyze(source), sort_keys=True, separators=(",", ":"),
                                            ensure_ascii=False, allow_nan=False) + "\n").encode()


@pytest.mark.parametrize("raw", [
    b'{"absolute_tolerance":0,"observations":[],"observations":[]}',
    b'{"absolute_tolerance":0,"observations":[{"id":"x","id":"y","revisions":[]}]}',
    b'{"absolute_tolerance":0,"observations":[],"nested":{"x":1,"x":2}}',
    b'{"absolute_tolerance":NaN,"observations":[]}',
    b'{"absolute_tolerance":Infinity,"observations":[]}',
    b'{"absolute_tolerance":-Infinity,"observations":[]}',
    b'{"absolute_tolerance":0,"observations":[]} trailing',
    b'{"absolute_tolerance":0,"observations":[]}{"absolute_tolerance":0,"observations":[]}',
    b'{', b'\xff', b'[' + b'[' * 2000 + b']' * 2000 + b']',
])
def test_cli_invalid_documents_have_exact_failure_contract(raw):
    completed = cli(raw)
    assert completed.returncode == 2
    assert completed.stdout == b""
    assert completed.stderr == (INVALID + "\n").encode()


def test_cli_byte_limit_includes_whitespace_and_unknown_arguments_fail():
    valid = b'{"absolute_tolerance":0,"observations":[]}'
    exactly_at_limit = cli(b" " * (1_048_576 - len(valid)) + valid)
    assert exactly_at_limit.returncode == 0
    assert exactly_at_limit.stderr == b""
    at_limit = cli(b" " * 1_048_576 + b"{}")
    assert at_limit.returncode == 2
    assert at_limit.stdout == b""
    assert at_limit.stderr == (INVALID + "\n").encode()
    too_large = cli(b" " * 1_048_577)
    assert too_large.returncode == 2
    assert too_large.stdout == b""
    assert too_large.stderr == (INVALID + "\n").encode()
    args = cli(b'{"absolute_tolerance":0,"observations":[]}', "unexpected")
    assert args.returncode == 2
    assert args.stdout == b""
    assert args.stderr == (INVALID + "\n").encode()
