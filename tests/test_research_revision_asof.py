from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from memecoin_experience.revision_asof import analyze


ROOT = Path(__file__).resolve().parents[1]


def observation(key, event, available, revision=0, value=1, withdrawn=False):
    return {"key": key, "event_ns": event, "available_ns": available,
            "revision": revision, "value": value, "withdrawn": withdrawn}


def query(identity, key, decision, age):
    return {"id": identity, "key": key, "decision_ns": decision, "max_age_ns": age}


def oracle(payload):
    """Deliberately straightforward filter/sort reference for small fixtures."""
    results = []
    counts = {name: 0 for name in ("absent", "withdrawn", "stale", "missing_value", "available")}
    for request in payload["queries"]:
        candidates = [row for row in payload["observations"]
                      if row["key"] == request["key"]
                      and row["available_ns"] <= request["decision_ns"]
                      and row["event_ns"] <= request["decision_ns"]]
        candidates.sort(key=lambda row: (row["event_ns"], row["revision"]))
        selected = candidates[-1] if candidates else None
        if selected is None:
            status = "absent"
        elif selected["withdrawn"]:
            status = "withdrawn"
        elif request["decision_ns"] - selected["event_ns"] > request["max_age_ns"]:
            status = "stale"
        elif selected["value"] is None:
            status = "missing_value"
        else:
            status = "available"
        counts[status] += 1
        results.append({"id": request["id"], "status": status, "selected": selected})
    return {"schema": "brain-revision-asof.v1", "results": results, "counts": counts}


def test_reconstructs_knowledge_at_decision_and_matches_oracle_without_mutation():
    payload = {
        "observations": [
            observation("price", 10, 10, 0, 1),
            observation("price", 10, 20, 1, 2),  # not knowable at t=15
            observation("price", 12, 12, 0, 3),
            observation("price", 12, 20, 1, 4),  # old-event revision arrives later
            observation("tie", 5, 8, 0, 5),
            observation("tie", 5, 8, 1, 6),  # equal availability, higher revision wins
            observation("withdraw", 9, 9, 0, 7),
            observation("withdraw", 9, 10, 1, None, True),
            observation("null", 11, 11, 0, 8),
            observation("null", 11, 12, 1, None),
            observation("boundary", 20, 20, 0, 9),
        ],
        "queries": [
            query("before_revision", "price", 15, 10),
            query("after_revision", "price", 20, 10),
            query("old_event_revision", "price", 25, 20),
            query("tie", "tie", 8, 3),
            query("withdrawal", "withdraw", 10, 10),
            query("missing", "null", 12, 10),
            query("age_equal", "boundary", 25, 5),
            query("stale", "boundary", 26, 5),
            query("absent", "other", 26, 5),
        ],
    }
    original = copy.deepcopy(payload)
    result = analyze(payload)
    assert payload == original
    assert result == oracle(payload)
    assert [row["selected"]["revision"] if row["selected"] else None
            for row in result["results"][:4]] == [0, 1, 1, 1]
    assert [row["status"] for row in result["results"]] == [
        "available", "available", "available", "available", "withdrawn",
        "missing_value", "available", "stale", "absent",
    ]
    assert sum(result["counts"].values()) == len(payload["queries"])


def test_empty_lists_return_zero_counts():
    assert analyze({"observations": [], "queries": []}) == {
        "schema": "brain-revision-asof.v1", "results": [],
        "counts": {"absent": 0, "withdrawn": 0, "stale": 0,
                   "missing_value": 0, "available": 0},
    }


@pytest.mark.parametrize("payload", [
    [],
    {"observations": []},
    {"observations": {}, "queries": []},
    {"observations": [{"key": "x"}], "queries": []},
    {"observations": [observation("x", 2, 1)], "queries": []},
    {"observations": [observation("x", True, 1)], "queries": []},
    {"observations": [observation("x", 1, 1, True)], "queries": []},
    {"observations": [observation("x", 1, 1, value=True)], "queries": []},
    {"observations": [observation("x", 1, 1, value=float("nan"))], "queries": []},
    {"observations": [observation("x", 1, 1, value=1e12 + 1)], "queries": []},
    {"observations": [observation("x", 1, 1), observation("x", 1, 2)], "queries": []},
    {"observations": [observation("x", 1, 2, 1), observation("x", 1, 3, 0)], "queries": []},
    {"observations": [], "queries": [query("", "x", 1, 1)]},
    {"observations": [], "queries": [query("q", "x", True, 1)]},
    {"observations": [], "queries": [query("q", "x", 1, 1), query("q", "y", 1, 1)]},
])
def test_invalid_inputs_raise_value_error(payload):
    with pytest.raises(ValueError):
        analyze(payload)


def test_cli_reads_one_json_object_and_writes_one_result():
    payload = {"observations": [observation("x", 2, 3, value=4)],
               "queries": [query("q", "x", 3, 1)]}
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    completed = subprocess.run(
        [sys.executable, "-m", "memecoin_experience.revision_asof"],
        input=json.dumps(payload), text=True, capture_output=True, cwd=ROOT, env=env,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == analyze(payload)
    assert completed.stdout.count("\n") == 1


@pytest.mark.parametrize("raw", [
    '{"observations":[],"observations":[],"queries":[]}',
    '{"observations":[],"queries":[],"x":NaN}',
    '{"observations":[{"key":"x","event_ns":1,"available_ns":1,"revision":0,"value":Infinity,"withdrawn":false}],"queries":[]}',
])
def test_cli_rejects_duplicate_keys_and_nonfinite_constants(raw):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    completed = subprocess.run(
        [sys.executable, "-m", "memecoin_experience.revision_asof"],
        input=raw, text=True, capture_output=True, cwd=ROOT, env=env,
        check=False,
    )
    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr
