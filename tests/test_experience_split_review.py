"""Independent request-envelope checks for the composed split review."""

import os
import subprocess
import sys

import pytest

from memecoin_experience import split_review


def request(**updates):
    value = {
        "snapshot": {"snapshot_id": "s1", "snapshot_sha256": "a" * 64, "row_ids": ["r1"]},
        "decisions": [],
        "assignments": [],
        "split_plan": {"plan_id": "p1", "plan_sha256": "b" * 64, "declared_splits": ["train", "test"]},
        "strata": [],
        "row_strata": [],
    }
    value.update(updates)
    return value


def test_rejects_unknown_top_level_keys():
    with pytest.raises(ValueError, match="invalid_request_keys"):
        split_review.analyze(request(extra=True))


@pytest.mark.parametrize("updates", [
    {"max_states": True},
    {"max_states": 50001},
    {"expected_manifest_sha256": "A" * 64},
    {"target_counts": []},
    {"row_strata": {}},
])
def test_rejects_malformed_optional_or_required_values(updates):
    with pytest.raises(ValueError):
        split_review.analyze(request(**updates))


def test_api_is_stdlib_only_until_after_valid_request():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import memecoin_experience.split_review; assert not any(x in sys.modules for x in ('memecoin_experience.storage','memecoin_experience.learning','torch','numpy'))"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(("src", os.environ.get("PYTHONPATH", "")))},
        check=False,
    )
    assert result.returncode == 0, result.stderr


def synthetic_request():
    from copy import deepcopy
    from test_experience_split_review_population import inputs, PLAN

    snapshot, decisions, assignments = inputs()
    return dict(
        snapshot=snapshot, decisions=decisions, assignments=assignments,
        split_plan=deepcopy(PLAN),
        strata=[{"id": "cohort", "supported_splits": ["test", "train"]}],
        row_strata=[{"row_id": row_id, "stratum_id": "cohort"}
                    for row_id in snapshot["row_ids"]],
        target_counts={"train": {"cohort": 2}, "test": {"cohort": 1}},
    )


def test_real_composition_pins_counts_and_hash():
    import hashlib
    import json
    from memecoin_experience.split_review_population import analyze as population
    from memecoin_experience.split_review_allocation import analyze as allocation
    from memecoin_experience.split_review_coverage import analyze as coverage

    payload = synthetic_request()
    result = split_review.analyze(payload)
    pop = population(payload["snapshot"], payload["decisions"], payload["assignments"], payload["split_plan"])
    alloc = allocation(pop, payload["strata"], payload["row_strata"], target_counts=payload["target_counts"])
    cov = coverage(pop, payload["strata"], payload["row_strata"], target_counts=payload["target_counts"])
    assert result["schema"] == "brain-split-review.v1"
    for key in ("inclusion", "lineage"):
        assert result[key] == pop[key]
    for key in ("allocation", "feasibility"):
        assert result[key] == alloc[key]
    for key in ("coverage", "target_deviation"):
        assert result[key] == cov[key]
    assert result["lineage"]["included_row_ids_by_split"] == {"train": ["r1", "r2"], "validation": [], "test": ["r6"]}
    assert result["denominators"]["individually_included_rows"] == 4
    assert result["denominators"]["assignable_rows"] == 3
    assert result["denominators"]["whole_group_excluded_rows"] == 3
    assert result["denominators"]["row_stratum_declarations_filtered_for_known_excluded_rows"] == 3
    assert result["provenance"]["snapshot_sha256"] == "a" * 64
    assert result["provenance"]["plan_sha256"] == "b" * 64
    assert result["feasibility"]["status"] == "feasible"
    body = {k: v for k, v in result.items() if k != "content_sha256"}
    assert result["content_sha256"] == hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    payload["expected_manifest_sha256"] = result["lineage"]["manifest_sha256"]
    assert split_review.analyze(payload)["lineage"] == result["lineage"]


def test_unordered_declarations_normalize_without_mutation_or_lost_duplicates():
    from copy import deepcopy

    payload = synthetic_request()
    payload["row_strata"].append(dict(payload["row_strata"][0]))
    original = deepcopy(payload)
    expected = split_review.analyze(payload)
    assert payload == original
    for key in ("decisions", "assignments", "strata", "row_strata"):
        payload[key].reverse()
    payload["snapshot"]["row_ids"].reverse()
    payload["split_plan"]["declared_splits"].reverse()
    payload["strata"][0]["supported_splits"].reverse()
    assert split_review.analyze(payload) == expected
    assert expected["coverage"]["summary"]["duplicate_stratum_row_count"] == 1
    assert expected["feasibility"]["status"] == "unknown"
    assert expected["target_deviation"]["status"] == "unknown"


@pytest.mark.parametrize("case", ["missing", "unknown_label", "unknown_row", "unsupported", "limit"])
def test_integrated_findings_and_bounded_unknowns(case):
    payload = synthetic_request()
    if case == "missing":
        payload["row_strata"] = [r for r in payload["row_strata"] if r["row_id"] != "r1"]
    elif case == "unknown_label":
        payload["row_strata"][0]["stratum_id"] = "ghost"
    elif case == "unknown_row":
        payload["row_strata"].append({"row_id": "ghost", "stratum_id": "cohort"})
    elif case == "unsupported":
        payload["strata"][0]["supported_splits"] = ["test"]
        payload["target_counts"] = {"train": {"cohort": 0}, "test": {"cohort": 0}}
    else:
        payload["max_states"] = 1
    result = split_review.analyze(payload)
    if case != "limit":
        assert result["coverage"]["summary"]["status"] == "findings"
        assert result["target_deviation"]["status"] == "unknown"
    if case != "unknown_row":
        assert result["feasibility"]["status"] == "unknown"


def test_empty_population_zero_targets_and_no_targets():
    payload = synthetic_request()
    payload["snapshot"]["row_ids"] = []
    payload["decisions"] = payload["assignments"] = payload["row_strata"] = []
    payload["target_counts"] = {"train": {"cohort": 0}, "test": {"cohort": 0}}
    result = split_review.analyze(payload)
    assert result["denominators"]["coverage_availability"] == "empty_population"
    assert result["feasibility"]["status"] == "feasible"
    cells = result["target_deviation"]["audit"]["details"]["cells"]
    assert all(c["observed_count"] == 0 and c["absolute_deviation_over_target"] is None for c in cells)
    del payload["target_counts"]
    result = split_review.analyze(payload)
    assert result["feasibility"]["status"] == result["target_deviation"]["status"] == "not_requested"
    payload["strata"] = []
    payload["target_counts"] = {"train": {}, "test": {}}
    assert split_review.analyze(payload)["target_deviation"] == {"status": "unknown", "reason": "empty_strata"}


@pytest.mark.parametrize("case", ["snapshot_pin", "plan_pin", "manifest", "assignment", "mixed_assignment", "duplicate_assignment"])
def test_integrated_invalid_identity_and_assignment_rejection(case):
    payload = synthetic_request()
    if case == "snapshot_pin":
        payload["snapshot"]["snapshot_sha256"] = "A" * 64
    elif case == "plan_pin":
        payload["split_plan"]["plan_sha256"] = "bad"
    elif case == "manifest":
        payload["expected_manifest_sha256"] = "c" * 64
    elif case == "assignment":
        payload["assignments"] = []
    elif case == "mixed_assignment":
        payload["assignments"].append({"group_id": "mixed", "split": "test"})
    else:
        payload["assignments"].append(dict(payload["assignments"][0]))
    with pytest.raises(ValueError):
        split_review.analyze(payload)
