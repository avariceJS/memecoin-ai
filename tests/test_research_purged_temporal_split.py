from __future__ import annotations

import copy
import itertools
import json
import os
import subprocess
import sys

import pytest

from memecoin_experience.purged_temporal_split import analyze


def row(identifier: str, role: str, start: int, end: int) -> dict:
    return {"id": identifier, "role": role, "start": start, "end": end}


def expected(payload: dict) -> dict:
    tests = [r for r in payload["rows"] if r["role"] == "test"]
    reasons = []
    purged, retained = [], []
    for train in (r for r in payload["rows"] if r["role"] == "train"):
        matches = []
        for test in tests:
            if train["start"] < test["end"] and test["start"] < train["end"]:
                reason = "overlap"
            elif (payload["embargo"] > 0 and train["start"] < test["end"] + payload["embargo"]
                  and test["end"] < train["end"]):
                reason = "embargo"
            else:
                continue
            matches.append({"test_id": test["id"], "reason": reason})
        matches.sort(key=lambda item: item["test_id"])
        reasons.append({"id": train["id"], "matches": matches})
        (purged if matches else retained).append(train["id"])
    return {
        "schema": "brain-purged-temporal-split.v1", "embargo": payload["embargo"],
        "retained_train_ids": sorted(retained), "purged_train_ids": sorted(purged),
        "test_ids": sorted(r["id"] for r in tests),
        "reasons": sorted(reasons, key=lambda item: item["id"]),
    }


def cli(data: bytes) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH="src")
    return subprocess.run([sys.executable, "-m", "memecoin_experience.purged_temporal_split"],
                          input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          cwd=os.getcwd(), env=env, check=False)


def test_frozen_examples_and_every_matching_test() -> None:
    rows = [row("h", "test", 10, 20)]
    for n, (start, end) in enumerate([(0, 10), (9, 11), (10, 20), (11, 12), (0, 30), (20, 25)]):
        rows.append(row(f"z{n}", "train", start, end))
    result = analyze({"rows": rows, "embargo": 0})
    assert result["purged_train_ids"] == ["z1", "z2", "z3", "z4"]
    rows.extend([row("j", "test", 22, 24), row("a", "test", 22, 24),
                 row("multi", "train", 21, 23), row("edge", "train", 20, 21)])
    result = analyze({"rows": rows, "embargo": 3})
    by_id = {item["id"]: item["matches"] for item in result["reasons"]}
    assert by_id["multi"] == [
        {"test_id": "a", "reason": "overlap"},
        {"test_id": "h", "reason": "embargo"},
        {"test_id": "j", "reason": "overlap"},
    ]
    assert by_id["edge"] == [{"test_id": "h", "reason": "embargo"}]
    assert result["test_ids"] == ["a", "h", "j"]


def test_empty_single_role_extrema_partition_and_nonmutation() -> None:
    empty = {"rows": [], "embargo": 0}
    assert analyze(empty) == {"schema": "brain-purged-temporal-split.v1", "embargo": 0,
                              "retained_train_ids": [], "purged_train_ids": [],
                              "test_ids": [], "reasons": []}
    payload = {"rows": [row("T", "train", 0, 1_000_000_000_000),
                        row("H", "test", 999_999_999_999, 1_000_000_000_000)],
               "embargo": 1_000_000_000_000}
    before = copy.deepcopy(payload)
    result = analyze(payload)
    assert payload == before
    assert result["purged_train_ids"] == ["T"]
    assert analyze({"rows": [row("only", "train", 0, 1)], "embargo": 2})["retained_train_ids"] == ["only"]
    assert analyze({"rows": [row("only", "test", 0, 1)], "embargo": 2})["test_ids"] == ["only"]


def test_small_exhaustive_pair_oracle() -> None:
    intervals = [(a, b) for a in range(5) for b in range(a + 1, 5)]
    for h, t, embargo in itertools.product(intervals, intervals, (0, 1, 2)):
        payload = {"rows": [row("H", "test", *h), row("T", "train", *t)], "embargo": embargo}
        assert analyze(payload) == expected(payload)


@pytest.mark.parametrize("payload", [
    None, [], {}, {"rows": [], "embargo": 0, "extra": 1},
    {"rows": None, "embargo": 0}, {"rows": [], "embargo": True},
    {"rows": [], "embargo": 1.0}, {"rows": [], "embargo": -1},
    {"rows": [], "embargo": 1_000_000_000_001},
    {"rows": [{}], "embargo": 0},
    {"rows": [{"id": "bad id", "role": "train", "start": 0, "end": 1}], "embargo": 0},
    {"rows": [{"id": "é", "role": "train", "start": 0, "end": 1}], "embargo": 0},
    {"rows": [{"id": "x", "role": "Train", "start": 0, "end": 1}], "embargo": 0},
    {"rows": [{"id": "x", "role": "train", "start": True, "end": 1}], "embargo": 0},
    {"rows": [{"id": "x", "role": "train", "start": 0.0, "end": 1}], "embargo": 0},
    {"rows": [{"id": "x", "role": "train", "start": -1, "end": 1}], "embargo": 0},
    {"rows": [{"id": "x", "role": "train", "start": 0, "end": 1_000_000_000_001}], "embargo": 0},
    {"rows": [{"id": "x", "role": "train", "start": 1, "end": 1}], "embargo": 0},
    {"rows": [row("x", "train", 0, 1), row("x", "test", 1, 2)], "embargo": 0},
    {"rows": [row("x", "train", 0, 1), row("y", "test", 0, 1), row("z", "test", 0, 1)]
             + [row(f"n{i}", "train", 0, 1) for i in range(255)], "embargo": 0},
])
def test_invalid_payloads(payload: object) -> None:
    with pytest.raises(ValueError, match=r"^purged_temporal_split_.+"):
        analyze(payload)  # type: ignore[arg-type]


def test_permutation_key_order_and_exact_output_shape() -> None:
    original = {"rows": [row("z", "train", 20, 23), row("T", "test", 10, 20),
                         row("a", "train", 10, 12)], "embargo": 3}
    permuted = {"embargo": 3, "rows": [dict(reversed(list(r.items()))) for r in reversed(original["rows"])]}
    result = analyze(original)
    assert analyze(permuted) == result
    assert set(result) == {"schema", "embargo", "retained_train_ids", "purged_train_ids", "test_ids", "reasons"}
    assert all(set(item) == {"id", "matches"} for item in result["reasons"])
    assert all(set(match) == {"test_id", "reason"} for item in result["reasons"] for match in item["matches"])


def test_cli_canonical_output_and_size_boundary() -> None:
    payload = b'{"rows":[],"embargo":0}'
    valid = cli(payload)
    assert valid.returncode == 0 and valid.stderr == b""
    assert valid.stdout == (json.dumps(analyze({"rows": [], "embargo": 0}), sort_keys=True,
                                        separators=(",", ":"), ensure_ascii=True) + "\n").encode()
    max_size = payload + b" " * (1_048_576 - len(payload))
    assert len(max_size) == 1_048_576
    assert cli(max_size).returncode == 0
    too_big = cli(max_size + b" ")
    assert too_big.returncode == 2 and too_big.stdout == b""
    assert too_big.stderr.startswith(b"purged_temporal_split_") and too_big.stderr.endswith(b"\n")


@pytest.mark.parametrize("bad", [
    b"", b"{", b'{"rows":[],"embargo":0} {}',
    b'{"rows":[],"rows":[],"embargo":0}',
    b'{"rows":[{"id":"x","id":"y","role":"train","start":0,"end":1}],"embargo":0}',
    b'{"rows":[],"embargo":NaN}', b'{"rows":[],"embargo":Infinity}',
    b'{"rows":[],"embargo":-Infinity}', b"\xff",
    (b"[" * 2000) + b"0" + (b"]" * 2000),
])
def test_cli_rejects_invalid_documents(bad: bytes) -> None:
    result = cli(bad)
    assert result.returncode == 2 and result.stdout == b""
    assert result.stderr.startswith(b"purged_temporal_split_") and result.stderr.endswith(b"\n")
