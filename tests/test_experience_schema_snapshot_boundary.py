"""Snapshot entry paths must reject malformed representation contracts early."""

import copy
import json

import pytest

from memecoin_experience.storage import Snapshot, atomic_json, build, digest
from test_experience_schema import custom_samples


def malformed_contracts(schema):
    cases = []

    def add(name, change):
        candidate = copy.deepcopy(schema)
        change(candidate)
        cases.append(pytest.param(candidate, id=name))

    add("root-null", lambda value: value.update(context=None))
    add("root-list", lambda value: value.update(features=None))
    add("representation-null", lambda value: value.update(representation=None))
    add("representation-list", lambda value: value.update(representation=[]))
    add("feature-null", lambda value: value["features"].__setitem__(0, None))
    add("feature-list", lambda value: value["features"].__setitem__(0, []))
    add("target-null", lambda value: value["targets"].__setitem__(0, None))
    add("target-list", lambda value: value["targets"].__setitem__(0, []))
    return cases


@pytest.mark.parametrize("bad_schema", malformed_contracts(custom_samples(1)[0]))
def test_build_rejects_malformed_supplied_contract_before_store_or_rows(tmp_path, bad_schema):
    consumed = []

    def rows():
        consumed.append(True)
        yield from custom_samples(1)[1]

    store = tmp_path / "store"
    with pytest.raises((ValueError,)) as error:
        build(
            store,
            rows(),
            sources=[dict(kind="synthetic", pin="a" * 64)],
            cutoff=10**18,
            schema=bad_schema,
        )

    assert "experience_schema" in str(error.value) or "experience_schema_columns" in str(error.value)
    assert consumed == []
    assert not store.exists()


@pytest.fixture
def stored_snapshot(tmp_path):
    schema, rows = custom_samples(4)
    result = build(
        tmp_path,
        rows,
        sources=[dict(kind="synthetic", pin="a" * 64)],
        cutoff=10**18,
        schema=schema,
    )
    return tmp_path, result["snapshot"], schema


@pytest.mark.parametrize("bad_schema", malformed_contracts(custom_samples(1)[0]))
def test_snapshot_rejects_malformed_persisted_binding_before_catalog_work(
    stored_snapshot, bad_schema, monkeypatch
):
    store, original_pin, schema = stored_snapshot
    path = store / "snapshots" / f"{original_pin}.json"
    manifest = json.loads(path.read_text())
    manifest["binding"]["contract"] = bad_schema
    forged_pin = digest(manifest)
    atomic_json(store / "snapshots" / f"{forged_pin}.json", manifest)

    def catalog_must_not_be_read(*args, **kwargs):
        raise AssertionError("catalog work started before contract validation")

    monkeypatch.setattr("memecoin_experience.catalog.Parts", catalog_must_not_be_read)
    with pytest.raises((ValueError,)) as error:
        Snapshot(store, forged_pin)

    assert "experience_schema" in str(error.value) or "experience_schema_columns" in str(error.value)
    assert Snapshot(store, original_pin).schema == schema


def test_expected_schema_valid_exact_and_different_contract_families(stored_snapshot):
    store, pin, schema = stored_snapshot

    assert Snapshot(store, pin, expected_schema=copy.deepcopy(schema)).schema == schema

    different_but_valid = copy.deepcopy(schema)
    different_but_valid["availability"] = "historical_replay"
    with pytest.raises(ValueError, match="experience_snapshot_contract"):
        Snapshot(store, pin, expected_schema=different_but_valid)

    malformed_expected = copy.deepcopy(schema)
    malformed_expected["features"] = None
    with pytest.raises(ValueError, match="experience_schema"):
        Snapshot(store, pin, expected_schema=malformed_expected)
