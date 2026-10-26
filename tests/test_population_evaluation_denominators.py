import json

import pytest

from memecoin_experience.records import contract as legacy_contract
from memecoin_population import catalog
from memecoin_population.evaluation import Cohorts
from test_population import config as population_config
from test_population import definition, member


def setup_cohorts(tmp_path, schema):
    store = tmp_path / "facts"
    entities = [
        member("A", start=0, end=10 * 10**9, known=0, venue="X"),
        member("B", start=0, end=10 * 10**9, known=0, venue="Y"),
        member("EMPTY", start=0, end=10 * 10**9, known=0, venue="Z"),
    ]
    population = catalog.publish(
        store,
        "population",
        entities,
        cutoff_ns=10 * 10**9,
        definition=definition("research_evaluation"),
    )
    config = population_config()
    config.update(axes=[["@entity"], ["venue"]], time_bucket_ns=10 * 10**9)
    config = {
        key: config[key]
        for key in ("entity_prefix", "knowledge", "axes", "time_bucket_ns", "selection")
    }
    binding = dict(population=population, config=config, cutoff_ns=10 * 10**9)
    stage = tmp_path / "scores"
    stage.mkdir()
    return stage, Cohorts(stage, store, binding, 0, 10, schema)


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture
def cohort_factory(tmp_path):
    opened = []

    def create(schema):
        stage, cohorts = setup_cohorts(tmp_path, schema)
        opened.append(cohorts)
        return stage, cohorts

    yield create
    for cohorts in opened:
        cohorts.close()


def test_v2_target_status_eligibility_conserve_through_sidecars(cohort_factory):
    schema = {
        "schema": "brain-experience-v2",
        "targets": [
            {"name": "short", "unit": "ratio", "kind": "synthetic"},
            {"name": "long", "unit": "ratio", "kind": "synthetic"},
        ],
    }
    stage, cohorts = cohort_factory(schema)
    rows = [
        (
            {"pool": "A", "decision_ns": 1 * 10**9, "target_status": ["observed", "pending"]},
            [True, False],
        ),
        (
            {
                "pool": "A",
                "decision_ns": 2 * 10**9,
                "target_status": ["censored", "missing_future"],
            },
            [False, False],
        ),
        (
            {
                "pool": "B",
                "decision_ns": 1 * 10**9,
                "target_status": ["unsupported", "observed"],
            },
            [False, True],
        ),
        (
            {
                "pool": "B",
                "decision_ns": 2 * 10**9,
                "target_status": ["observed", "censored"],
            },
            [False, False],
        ),
    ]
    with pytest.raises(ValueError, match="target_correspondence"):
        cohorts.observe(rows[0][0], [True])
    assert cohorts.db.execute("select sum(rows) from support").fetchone()[0] == 0
    assert cohorts.db.execute("select count(*) from counts where role='test'").fetchone()[0] == 0

    for row, mask in rows:
        cohorts.observe(row, mask)
    result = cohorts.finish(stage)
    support = read_jsonl(stage / "population-support.jsonl")
    denominators = read_jsonl(stage / "cohort-support.jsonl")
    enrollment = read_jsonl(stage / "cohort-enrollment.jsonl")
    assert result["enrollment"] == 3 and result["supported_pools"] == 2
    assert next(item for item in support if item["entity"].endswith("EMPTY")) == {
        "entity": "chain/market/EMPTY",
        "rows": 0,
        "usable": 0,
    }
    entity_counts = {
        (item["value"], item["metric"]): item["n"]
        for item in denominators
        if item["axis"] == '["@entity"]' and item["role"] == "test"
    }
    for entity in ("chain/market/A", "chain/market/B"):
        facet = json.dumps([entity], separators=(",", ":"))
        assert entity_counts[(facet, "rows")] == 2
        assert entity_counts[(facet, "usable")] == 1
        for target in ("short", "long"):
            statuses = {
                metric.rsplit(":", 1)[1]: n
                for (value, metric), n in entity_counts.items()
                if value == facet and metric.startswith("status:" + target + ":")
            }
            assert sum(statuses.values()) == 2
            eligible = entity_counts[(facet, "eligible:" + target)]
            masked = entity_counts[(facet, "masked:" + target)]
            index = ("short", "long").index(target)
            assert eligible + masked == 2
            assert entity_counts[(facet, "target:" + str(index))] == eligible
    a = json.dumps(["chain/market/A"], separators=(",", ":"))
    b = json.dumps(["chain/market/B"], separators=(",", ":"))
    assert entity_counts[(a, "eligible:short")] == 1
    assert entity_counts[(a, "masked:short")] == 1  # observed-but-masked is explicit
    assert entity_counts[(b, "eligible:long")] == 1
    assert entity_counts[(b, "status:short:observed")] == 1
    assert entity_counts[(b, "masked:short")] == 2
    assert any(item["axis"] == '["venue"]' and item["entities"] == 1 for item in enrollment)
    assert any(item["value"] == '["Z"]' and item["entities"] == 1 for item in enrollment)


def test_v1_legacy_target_statuses_are_counted(cohort_factory):
    stage, cohorts = cohort_factory(legacy_contract(2))
    row = {
        "pool": "A",
        "origin_s": 1,
        "target_status": ["observed", "missing_future"],
    }
    cohorts.observe(row, [False, False])
    cohorts.finish(stage)
    rows = read_jsonl(stage / "cohort-support.jsonl")
    target_statuses = {
        item["metric"]: item["n"]
        for item in rows
        if item["axis"] == '["@entity"]'
        and item["value"] == '["chain/market/A"]'
        and item["role"] == "test"
    }
    assert target_statuses["rows"] == 1
    assert target_statuses["status:1:observed"] == 1
    assert target_statuses["status:3:missing_future"] == 1
    assert target_statuses["eligible:1"] == 0
    assert target_statuses["masked:1"] == 1
