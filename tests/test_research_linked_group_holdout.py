from __future__ import annotations

import json
import subprocess
import sys

import pytest

from memecoin_experience.linked_group_holdout import analyze


def _oracle(payload: dict) -> dict:
    """Independent repeated set-expansion reference for small fixtures."""
    remaining = {row["id"] for row in payload["rows"]}
    by_id = {row["id"]: set(row["groups"]) for row in payload["rows"]}
    nominated = set(payload["holdout_groups"])
    expected_components = []
    while remaining:
        rows = {min(remaining)}
        groups: set[str] = set()
        while True:
            expanded_groups = groups | set().union(*(by_id[item] for item in rows))
            expanded_rows = rows | {
                identity for identity, labels in by_id.items()
                if labels.intersection(expanded_groups)
            }
            if expanded_rows == rows and expanded_groups == groups:
                break
            rows, groups = expanded_rows, expanded_groups
        remaining.difference_update(rows)
        expected_components.append({
            "row_ids": sorted(rows), "groups": sorted(groups),
            "role": "holdout" if groups.intersection(nominated) else "train",
        })
    expected_components.sort(key=lambda component: component["row_ids"][0])
    holdout_ids = sorted(item for component in expected_components if component["role"] == "holdout" for item in component["row_ids"])
    train_ids = sorted(item for component in expected_components if component["role"] == "train" for item in component["row_ids"])
    return {
        "schema": "brain-linked-group-holdout.v1",
        "train_ids": train_ids,
        "holdout_ids": holdout_ids,
        "components": expected_components,
    }


def _fixture() -> dict:
    return {
        "rows": [
            {"id": "a", "groups": ["g1"]},
            {"id": "b", "groups": ["g1", "g2"]},
            {"id": "c", "groups": ["g2", "g3"]},
            {"id": "d", "groups": ["g3"]},
            {"id": "x", "groups": ["other"]},
            {"id": "z", "groups": ["alone"]},
        ],
        "holdout_groups": ["g3"],
    }


def test_transitive_closure_matches_independent_oracle_and_separates_component() -> None:
    payload = _fixture()
    result = analyze(payload)
    assert result == _oracle(payload)
    assert result["holdout_ids"] == ["a", "b", "c", "d"]
    assert result["train_ids"] == ["x", "z"]
    assert result["components"] == [
        {"row_ids": ["a", "b", "c", "d"], "groups": ["g1", "g2", "g3"], "role": "holdout"},
        {"row_ids": ["x"], "groups": ["other"], "role": "train"},
        {"row_ids": ["z"], "groups": ["alone"], "role": "train"},
    ]
    assert set(result["train_ids"]).isdisjoint(result["holdout_ids"])
    assert sorted(result["train_ids"] + result["holdout_ids"]) == sorted(row["id"] for row in payload["rows"])


def test_row_and_group_permutations_do_not_change_result_or_mutate_input() -> None:
    payload = _fixture()
    original = json.loads(json.dumps(payload))
    permuted = {
        "rows": [
            {"id": row["id"], "groups": list(reversed(row["groups"]))}
            for row in reversed(payload["rows"])
        ],
        "holdout_groups": list(reversed(payload["holdout_groups"])),
    }
    assert analyze(payload) == analyze(permuted) == _oracle(payload)
    assert payload == original


@pytest.mark.parametrize("holdout_groups,train,holdout", [
    ([], ["a", "b", "c", "d", "x", "z"], []),
    (["g1", "g2", "g3", "other", "alone"], [], ["a", "b", "c", "d", "x", "z"]),
])
def test_none_or_all_nominations(holdout_groups: list[str], train: list[str], holdout: list[str]) -> None:
    payload = _fixture()
    payload["holdout_groups"] = holdout_groups
    result = analyze(payload)
    assert result == _oracle(payload)
    assert result["train_ids"] == train
    assert result["holdout_ids"] == holdout


def test_empty_rows_and_empty_nominations_are_valid() -> None:
    assert analyze({"rows": [], "holdout_groups": []}) == {
        "schema": "brain-linked-group-holdout.v1",
        "train_ids": [], "holdout_ids": [], "components": [],
    }


@pytest.mark.parametrize("payload", [
    {"rows": [], "holdout_groups": [], "extra": 1},
    {"rows": [], "holdout_groups": [] , "rows2": []},
    {"rows": {}, "holdout_groups": []},
    {"rows": [{"id": "a", "groups": ["g"], "extra": 1}], "holdout_groups": []},
    {"rows": [{"id": "", "groups": ["g"]}], "holdout_groups": []},
    {"rows": [{"id": 3, "groups": ["g"]}], "holdout_groups": []},
    {"rows": [{"id": "a", "groups": []}], "holdout_groups": []},
    {"rows": [{"id": "a", "groups": ["g", "g"]}], "holdout_groups": []},
    {"rows": [{"id": "a", "groups": [""]}], "holdout_groups": []},
    {"rows": [{"id": "a", "groups": ["g"]}, {"id": "a", "groups": ["h"]}], "holdout_groups": []},
    {"rows": [{"id": "a", "groups": ["g"]}], "holdout_groups": ["missing"]},
    {"rows": [{"id": "a", "groups": ["g"]}], "holdout_groups": ["g", "g"]},
    {"rows": [{"id": "a", "groups": ["g"]}], "holdout_groups": [""]},
    {"rows": [{"id": "a", "groups": ["g"]}], "holdout_groups": [True]},
])
def test_api_rejects_invalid_inputs(payload: dict) -> None:
    with pytest.raises(ValueError):
        analyze(payload)


def _cli(input_text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "memecoin_experience.linked_group_holdout"],
        input=input_text, text=True, capture_output=True,
    )


def test_real_cli_reads_one_object_and_emits_one_result() -> None:
    payload = _fixture()
    process = _cli(json.dumps(payload))
    assert process.returncode == 0
    assert process.stderr == ""
    assert process.stdout.count("\n") == 1
    assert json.loads(process.stdout) == analyze(payload)


@pytest.mark.parametrize("payload", [
    {"rows": [{"id": "\ud800", "groups": ["g"]}], "holdout_groups": []},
    {"rows": [{"id": "\udfff", "groups": ["g"]}], "holdout_groups": []},
    {"rows": [{"id": "a", "groups": ["\ud800"]}], "holdout_groups": ["\ud800"]},
    {"rows": [{"id": "a", "groups": ["\udfff"]}], "holdout_groups": ["\udfff"]},
])
def test_real_cli_safely_serializes_escaped_surrogate_ids_and_groups(payload: dict) -> None:
    process = _cli(json.dumps(payload))
    assert process.returncode == 0
    assert process.stderr == ""
    assert process.stdout.count("\n") == 1
    assert json.loads(process.stdout) == analyze(payload)


@pytest.mark.parametrize("input_text", [
    '{"rows":[],"rows":[],"holdout_groups":[]}',
    '{"rows":[],"holdout_groups":[NaN]}',
    '{"rows":[],"holdout_groups":[Infinity]}',
    '{"rows":[{"id":"a","groups":["g"]}],"holdout_groups":["missing"]}',
])
def test_real_cli_rejects_duplicate_keys_nonfinite_and_invalid_payload(input_text: str) -> None:
    process = _cli(input_text)
    assert process.returncode != 0
    assert process.stdout == ""
    assert process.stderr.strip()
