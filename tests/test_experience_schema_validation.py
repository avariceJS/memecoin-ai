import copy

import pytest

from memecoin_experience import coordinate_samples
from memecoin_experience.records import contract
from memecoin_experience.schema import feature_names, target_names, validate


def _v2():
    return {
        "schema": "brain-experience-v2",
        "layout": "dense-sequence-f64-v1",
        "context": 2,
        "features": [
            {"name": "first", "unit": "events", "modality": "process"},
            {"name": "second", "unit": "ratio", "modality": "market"},
        ],
        "targets": [{"name": "outcome", "unit": "ratio", "kind": "synthetic"}],
        "representation": {"name": "synthetic", "version": "1", "code_sha": "c" * 64},
        "availability": "historical_replay",
    }


def _v3():
    return coordinate_samples.schema(
        context=2,
        features=_v2()["features"],
        targets=_v2()["targets"],
        availability="point_in_time",
    )


@pytest.mark.parametrize("schema", [[], None, "schema"])
def test_non_mapping_top_level_uses_schema_error(schema):
    with pytest.raises(ValueError, match="^experience_schema$"):
        validate(schema)


@pytest.mark.parametrize("context", [None, True, False, 0, -1, 257, 1.5, "1"])
def test_legacy_context_must_be_an_integer_in_range(context):
    value = contract(1)
    value["context"] = context
    with pytest.raises(ValueError, match="^experience_schema$"):
        validate(value)


def test_legacy_context_boundaries_preserve_identity_and_exact_contract():
    for context in (1, 256):
        value = contract(context)
        assert validate(value) is value

    missing = contract(1)
    del missing["context"]
    with pytest.raises(ValueError, match="^experience_schema$"):
        validate(missing)


@pytest.mark.parametrize("factory", [_v2, _v3], ids=["v2", "v3"])
@pytest.mark.parametrize(
    "mutation,error",
    [
        (lambda schema: schema.update(availability=[]), "experience_schema"),
        (lambda schema: schema.update(availability="unknown"), "experience_schema"),
        (lambda schema: schema.update(representation=["name", "version", "code_sha"]), "experience_schema"),
        (lambda schema: schema.update(representation={"name": "", "version": "1", "code_sha": "c" * 64}), "experience_schema"),
        (lambda schema: schema.update(features=[None]), "experience_schema_columns"),
        (lambda schema: schema.update(features=[{"name": [], "unit": "u", "modality": "m"}]), "experience_schema_columns"),
        (lambda schema: schema.update(targets=[]), "experience_schema_columns"),
    ],
)
def test_v2_and_v3_reject_malformed_structures(factory, mutation, error):
    value = copy.deepcopy(factory())
    mutation(value)
    with pytest.raises(ValueError, match=f"^{error}$"):
        validate(value)


@pytest.mark.parametrize("factory", [_v2, _v3], ids=["v2", "v3"])
def test_valid_versioned_schema_identity_order_and_context_limit(factory):
    value = factory()
    value["context"] = 65536
    assert validate(value) is value
    assert feature_names(value) == ["first", "second"]
    assert target_names(value) == ["outcome"]


@pytest.mark.parametrize("factory", [_v2, _v3], ids=["v2", "v3"])
def test_duplicate_column_names_keep_column_error(factory):
    value = factory()
    value["features"][1]["name"] = value["features"][0]["name"]
    with pytest.raises(ValueError, match="^experience_schema_columns$"):
        validate(value)


def test_v3_coordinate_domain_guards_remain_active():
    value = _v3()
    value["coordinate_domain_pin"] = "d" * 64
    with pytest.raises(ValueError, match="^experience_coordinate_domain_pin$"):
        validate(value)
