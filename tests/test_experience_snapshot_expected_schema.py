import copy

import pytest

from memecoin_experience.storage import Snapshot, build
from test_experience_schema import custom_samples


@pytest.fixture
def persisted_snapshot(tmp_path):
    schema, rows = custom_samples(4)
    result = build(
        tmp_path,
        rows,
        sources=[dict(kind="synthetic", pin="a" * 64)],
        cutoff=10**18,
        schema=schema,
    )
    return tmp_path, result["snapshot"], schema


def test_expected_schema_accepts_exact_persisted_contract_and_reads_payload(persisted_snapshot):
    store, pin, schema = persisted_snapshot

    snapshot = Snapshot(store, pin, expected_schema=copy.deepcopy(schema))

    assert snapshot.schema == schema
    _, rows = snapshot.partition(0)
    assert len(rows) == 4


@pytest.mark.parametrize(
    "change",
    [
        lambda schema: schema["features"].reverse(),
        lambda schema: schema["features"][0].update(name="other-feature"),
        lambda schema: schema["features"][0].update(unit="other-unit"),
        lambda schema: schema["features"][0].update(modality="other-modality"),
        lambda schema: schema.update(context=3),
        lambda schema: schema["targets"][0].update(name="other-target"),
        lambda schema: schema["targets"][0].update(unit="other-target-unit"),
        lambda schema: schema["targets"][0].update(kind="other-target-kind"),
        lambda schema: schema["representation"].update(name="other-representation"),
        lambda schema: schema["representation"].update(version="2"),
        lambda schema: schema["representation"].update(code_sha="d" * 64),
        lambda schema: schema.update(availability="historical_replay"),
    ],
    ids=[
        "feature-order",
        "feature-name",
        "feature-unit",
        "feature-modality",
        "context",
        "target-name",
        "target-unit",
        "target-kind",
        "representation-name",
        "representation-version",
        "representation-code",
        "availability",
    ],
)
def test_expected_schema_rejects_valid_but_different_contract(persisted_snapshot, change):
    store, pin, schema = persisted_snapshot
    expected = copy.deepcopy(schema)
    change(expected)

    with pytest.raises(ValueError, match="experience_snapshot_contract"):
        Snapshot(store, pin, expected_schema=expected)


def test_expected_schema_rejects_invalid_layout_as_invalid_schema(persisted_snapshot):
    store, pin, schema = persisted_snapshot
    expected = copy.deepcopy(schema)
    expected["layout"] = "other-layout"

    with pytest.raises(ValueError, match="experience_schema"):
        Snapshot(store, pin, expected_schema=expected)


@pytest.mark.parametrize("expected", [None, {}, "not-a-schema"])
def test_expected_schema_is_optional_and_malformed_schema_is_rejected(persisted_snapshot, expected):
    store, pin, schema = persisted_snapshot

    if expected is None:
        assert Snapshot(store, pin).schema == schema
    else:
        with pytest.raises(ValueError, match="experience_schema"):
            Snapshot(store, pin, expected_schema=expected)
