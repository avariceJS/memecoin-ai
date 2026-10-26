import math
import struct

import pytest

from memecoin_experience.schema import checked_samples
from memecoin_experience.storage import Snapshot, build
from test_experience_schema import custom_samples


def count_samples(values=(0, 3), *, kind="observed_process_count@synthetic"):
    schema, rows = custom_samples(len(values))
    schema["targets"] = [dict(name="events", unit="count", kind=kind)]
    for row, value in zip(rows, values, strict=True):
        row["targets"] = [value]
    return schema, rows


@pytest.mark.parametrize(
    "value",
    [-1, -0.5, 1.5, 1.0, True, False, math.nan, math.inf, -math.inf],
)
def test_observed_count_requires_nonnegative_python_integer(value):
    schema, rows = count_samples([value])
    with pytest.raises(ValueError, match="experience_(count_target|target_contract)"):
        list(checked_samples(rows, schema, 10**18))


@pytest.mark.parametrize("value", [0, 4])
def test_count_snapshot_builds_and_reads_back_exact_integer(value, tmp_path):
    schema, rows = count_samples([value])
    checked = list(checked_samples(rows, schema, 10**18))
    assert checked == rows

    result = build(
        tmp_path, rows, sources=[dict(synthetic=True)], cutoff=10**18, schema=schema
    )
    snapshot = Snapshot(tmp_path, result["snapshot"])
    partition, partition_rows = snapshot.partition(0)
    assert [row["target_status"] for row in partition_rows] == [["observed"]]
    assert struct.unpack("<d", (partition / "y.f64").read_bytes()) == (float(value),)
    assert (tmp_path / "snapshots" / f"{result['snapshot']}.json").is_file()


def test_build_rejects_bad_count_before_completed_snapshot_manifest(tmp_path):
    schema, rows = count_samples([2, 1.0])
    with pytest.raises(ValueError, match="experience_count_target"):
        build(tmp_path, rows, sources=[dict(synthetic=True)], cutoff=10**18, schema=schema)

    assert list((tmp_path / "snapshots").glob("*.json")) == []
    assert not list((tmp_path / "builds").glob("*/complete.json"))


@pytest.mark.parametrize("value", [-1.25, 1.5])
def test_generic_regression_keeps_finite_negative_and_fractional_values(value):
    schema, rows = custom_samples(1)
    rows[0]["targets"] = [value]
    assert list(checked_samples(rows, schema, 10**18)) == rows


@pytest.mark.parametrize("status", ["pending", "missing_future", "unsupported"])
def test_nonobserved_count_status_keeps_null_payload(status):
    schema, rows = count_samples([None])
    row = rows[0]
    row["targets"] = [None]
    row["target_status"] = [status]
    row["target_available_ns"] = [None]
    row["target_captures"] = [[]]
    assert list(checked_samples(rows, schema, 10**18)) == rows


def test_censored_count_lower_bound_contract_is_unchanged():
    schema, rows = count_samples([None])
    row = rows[0]
    row["targets"] = [None]
    row["target_status"] = ["censored"]
    row["target_available_ns"] = [None]
    row["target_captures"] = [[]]
    row["target_details"] = [dict(lower_bound=2, lower_bound_available_ns=row["decision_ns"] + 1)]
    assert list(checked_samples(rows, schema, 10**18)) == rows


def test_boolean_remains_rejected_as_an_observed_numeric_value():
    schema, rows = count_samples([True])
    with pytest.raises(ValueError, match="experience_target_contract"):
        list(checked_samples(rows, schema, 10**18))
