import copy

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("torch")

from test_experience import create  # noqa: E402
from test_experience_schema import custom_samples  # noqa: E402

from memecoin_experience.coordinate_samples import schema as coordinate_schema  # noqa: E402
from memecoin_experience.learning import batches, prepare, validate_statistics  # noqa: E402
from memecoin_experience.storage import Snapshot, build  # noqa: E402

PLAN = dict(train_start_s=0, train_end_s=30000, validation_end_s=60000, test_end_s=90000)


def test_legacy_preparation_coverage_reconciles_rows_cells_and_masks(tmp_path):
    result = create(tmp_path / "store", n=310, chunk=43)
    snapshot = Snapshot(tmp_path / "store", result["snapshot"])
    # Extend the final role through this fixture's late missing-future rows so
    # their status reasons are tested in-role; v2/v3 cases cover outside rows.
    legacy_plan = dict(PLAN, test_end_s=100000)
    stats = prepare(snapshot, legacy_plan, tmp_path, 17)
    validate_statistics(stats, snapshot.schema)

    coverage = stats["preparation_coverage"]
    train_batches = list(batches(snapshot, legacy_plan, "train", 17))
    selected_ids = {item for batch in train_batches for item in batch[3]}
    assert coverage["rows"]["train/retained_objective"] == len(selected_ids)
    assert coverage["rows"]["input"] == coverage["input_rows"]
    assert sum(
        coverage["rows"].get(f"{role}/input", 0)
        for role in ("train", "validation", "test", "purged", "outside")
    ) == coverage["input_rows"]

    for h, target in enumerate(snapshot.schema["horizons"]):
        key = f"train/{target}"
        emitted = sum(int(batch[2][:, h].sum()) for batch in train_batches)
        assert coverage["targets"][f"{key}/retained_objective"] == emitted
        assert coverage["targets"][f"{key}/exact_eligible"] == emitted
        assert coverage["targets"][f"{key}/input"] == (
            coverage["targets"][f"{key}/retained_objective"]
            + coverage["targets"][f"{key}/dropped_objective"]
        )
    missing_status_count = sum(
        value
        for key, value in coverage["targets"].items()
        if key.endswith("/status/missing_future")
    )
    assert missing_status_count > 0
    missing_reason_count = sum(
        value
        for key, value in coverage["target_reasons"].items()
        if key.startswith("test/") and "/target_status_missing_future" in key
    )
    assert missing_reason_count == missing_status_count
    assert coverage["targets"]["train/1/retained_objective"] != coverage["targets"][
        "train/3/retained_objective"
    ]
    assert coverage["availability"]["mode"] == "legacy_historical_only"
    assert coverage["availability"]["point_in_time_claim"] is False
    assert coverage["availability"]["historical_late_availability_offline_only"] is False

    legacy = copy.deepcopy(stats)
    legacy.pop("preparation_coverage")
    validate_statistics(legacy, snapshot.schema)

    corruptions = []
    bad = copy.deepcopy(stats)
    reason_key = next(
        key for key in bad["preparation_coverage"]["row_reasons"] if key.startswith("train/")
    )
    bad["preparation_coverage"]["row_reasons"][reason_key] -= 1
    corruptions.append(bad)
    bad = copy.deepcopy(stats)
    bad["preparation_coverage"]["targets"]["train/1/input"] -= 1
    corruptions.append(bad)
    bad = copy.deepcopy(stats)
    bad["preparation_coverage"]["targets"]["train/1/exact_eligible"] -= 1
    corruptions.append(bad)
    bad = copy.deepcopy(stats)
    bad["preparation_coverage"]["targets"]["train/1/input"] += 1
    bad["preparation_coverage"]["targets"]["train/1/dropped_objective"] += 1
    bad["preparation_coverage"]["target_reasons"]["train/1/false_reason"] = 1
    corruptions.append(bad)
    for changed in corruptions:
        with pytest.raises(ValueError, match="experience_statistics_contract"):
            validate_statistics(changed, snapshot.schema)


def test_v2_historical_late_receipt_is_offline_eligible_and_pit_is_not(tmp_path):
    schema, source_rows = custom_samples(310)
    row = copy.deepcopy(source_rows[97])
    # Its target matures before the train boundary, while the receipt arrives after it.
    row["target_available_ns"][0] = 40000 * 10**9
    row["target_captures"][0] = ["d" * 64]
    rows = [copy.deepcopy(candidate) for candidate in source_rows]
    rows[97] = row
    # Preserve valid explicit target statuses and ambiguous endpoint evidence.
    for index, status in ((6, "missing_future"), (7, "unsupported")):
        rows[index]["targets"] = [None]
        rows[index]["target_status"] = [status]
        rows[index]["target_available_ns"] = [None]
        rows[index]["target_captures"] = [[]]
    rows[6]["target_details"] = [{"reason": "no_exact_endpoint_unknown_return"}]
    rows[7]["target_details"] = [{"reason": "ambiguous_endpoint"}]
    # This train target is not mature by the role boundary even though its receipt exists.
    rows[98]["target_maturity_ns"] = [30000 * 10**9]
    rows[98]["target_available_ns"] = [30000 * 10**9]
    schema = dict(schema, availability="historical_replay")
    store = tmp_path / "historical"
    built = build(store, rows, sources=[dict(synthetic=True)], cutoff=10**18, schema=schema)
    snapshot = Snapshot(store, built["snapshot"])
    stats = prepare(snapshot, PLAN, tmp_path, 32)

    assert stats["preparation_coverage"]["targets"]["train/change/exact_eligible"] > 0
    assert stats["preparation_coverage"]["availability"]["mode"] == "historical_replay"
    assert stats["preparation_coverage"]["availability"]["point_in_time_claim"] is False
    assert stats["preparation_coverage"]["availability"][
        "historical_late_availability_offline_only"
    ] is True
    assert stats["preparation_coverage"]["target_reasons"][
        "train/change/exact_eligible_historical_late_availability_offline_only"
    ] == 1
    historical_targets = stats["preparation_coverage"]["targets"]
    assert historical_targets["train/change/status/missing_future"] == 1
    assert historical_targets["train/change/status/unsupported"] == 1
    assert stats["preparation_coverage"]["target_reasons"][
        "train/change/target_status_missing_future:no_exact_endpoint_unknown_return"
    ] == 1
    assert stats["preparation_coverage"]["target_reasons"][
        "train/change/target_status_unsupported:ambiguous_endpoint"
    ] == 1
    ambiguous_row = next(
        row
        for index in range(len(snapshot.parts))
        for row in snapshot.partition(index)[1]
        if row["id"] == rows[7]["id"]
    )
    assert ambiguous_row["target_details"][0]["reason"] == "ambiguous_endpoint"
    # Rows beyond the split are accounted as outside before any role-boundary
    # maturity or knowledge-cutoff check is attempted.
    assert stats["preparation_coverage"]["rows"]["outside/input"] == 11
    assert stats["preparation_coverage"]["target_reasons"][
        "outside/change/origin_outside_roles"
    ] == 11
    assert stats["preparation_coverage"]["row_reasons"][
        "purged/input_extent_crosses_role_boundary"
    ] >= 1
    assert stats["preparation_coverage"]["target_reasons"][
        "train/change/target_maturity_or_role_boundary"
    ] >= 1

    pit_schema = dict(schema, availability="point_in_time")
    pit_store = tmp_path / "pit"
    pit_built = build(
        pit_store,
        rows,
        sources=[dict(synthetic=True)],
        cutoff=10**18,
        schema=pit_schema,
    )
    pit_snapshot = Snapshot(pit_store, pit_built["snapshot"])
    pit_stats = prepare(pit_snapshot, PLAN, tmp_path, 32)
    pit_coverage = pit_stats["preparation_coverage"]
    assert pit_coverage["availability"]["point_in_time_claim"] is True
    assert pit_coverage["target_reasons"][
        "train/change/knowledge_cutoff"
    ] >= 1


def test_v2_positive_lower_bounds_reconcile_with_real_batches(tmp_path):
    schema, source_rows = custom_samples(310)
    schema = dict(
        schema,
        availability="historical_replay",
        targets=[dict(name="count", unit="events", kind="observed_process_count@1slots")],
    )
    rows = [copy.deepcopy(row) for row in source_rows]
    for index, row in enumerate(rows):
        row["targets"] = [index % 4]
        if row["decision_ns"] < 30000 * 10**9:
            maturity = row["decision_ns"] + 100 * 10**9
            row["targets"] = [None]
            row["target_status"] = ["censored"]
            row["target_available_ns"] = [None]
            row["target_maturity_ns"] = [maturity]
            row["target_captures"] = [[]]
            row["target_details"] = [
                {"lower_bound": 3, "lower_bound_available_ns": maturity + 10 * 10**9}
            ]
    store = tmp_path / "positive-bounds"
    built = build(store, rows, sources=[dict(synthetic=True)], cutoff=10**18, schema=schema)
    snapshot = Snapshot(store, built["snapshot"])
    stats = prepare(
        snapshot,
        PLAN,
        tmp_path,
        16,
        objective="partial_count_lower_bound_pinball_v1",
    )
    coverage = stats["preparation_coverage"]
    emitted = list(batches(snapshot, PLAN, "train", 16, with_censoring=True))
    emitted_rows = sum(len(batch[5]) for batch in emitted)
    emitted_bounds = sum(int(batch[4][:, 0].sum()) for batch in emitted)
    assert emitted_rows > 0
    assert emitted_bounds == emitted_rows
    assert coverage["rows"]["train/retained_objective"] == emitted_rows
    assert coverage["targets"]["train/count/informative_lower_bound_eligible"] == emitted_bounds
    assert coverage["targets"]["train/count/retained_objective"] == emitted_bounds
    assert coverage["targets"]["outside/count/input"] == 11
    assert coverage["target_reasons"]["outside/count/origin_outside_roles"] == 11
    validate_statistics(stats, snapshot.schema)


def test_v2_active_censored_count_requires_target_details(tmp_path):
    schema, source_rows = custom_samples(310)
    schema = dict(
        schema,
        availability="historical_replay",
        targets=[dict(name="count", unit="events", kind="observed_process_count@1slots")],
    )
    rows = [copy.deepcopy(row) for row in source_rows]
    for index, sample in enumerate(rows):
        sample["targets"] = [index % 4]
    row = rows[0]
    row["targets"] = [None]
    row["target_status"] = ["censored"]
    row["target_available_ns"] = [None]
    row["target_maturity_ns"] = [row["decision_ns"] + 100 * 10**9]
    row["target_captures"] = [[]]
    row.pop("target_details", None)
    store = tmp_path / "missing-active-count-details"
    built = build(store, rows, sources=[dict(synthetic=True)], cutoff=10**18, schema=schema)
    snapshot = Snapshot(store, built["snapshot"])
    with pytest.raises(ValueError, match="experience_censor_detail"):
        prepare(
            snapshot,
            PLAN,
            tmp_path,
            16,
            objective="partial_count_lower_bound_pinball_v1",
        )


def test_v3_target_cells_separate_exact_and_informative_lower_bound(tmp_path):
    from test_experience_coordinate_samples import _plan, _row

    schema = coordinate_schema(
        context=1,
        features=[dict(name="prior_count", unit="events", modality="process")],
        targets=[dict(name="next_count", unit="events", kind="observed_process_count@1slots")],
        availability="historical_replay",
    )
    # Keep the native typed domain and split plan used by the real eligibility gate.
    rows = []
    for coordinate in range(12):
        rows.append(_row(coordinate, status="exact", value=coordinate + 1))
    rows[1] = _row(1, status="lower_bound", value=2)
    # A dependency crossing the role boundary is purged by native v3 eligibility.
    rows[4]["input_dependencies"][0]["start_coordinate"] = 3
    # This exact receipt is beyond its role's verified UTC knowledge cutoff.
    rows[5]["target_available_ns"][0] = 150
    rows.append(_row(12, status="exact", value=13))
    plan = _plan(cutoff=120)
    store = tmp_path / "coordinate"
    built = build(
        store,
        rows,
        sources=[dict(synthetic=True)],
        cutoff=1000,
        schema=schema,
    )
    snapshot = Snapshot(store, built["snapshot"])
    stats = prepare(
        snapshot,
        plan,
        tmp_path,
        4,
        objective="partial_count_lower_bound_pinball_v1",
    )
    coverage = stats["preparation_coverage"]
    assert coverage["targets"]["train/next_count/exact_eligible"] > 0
    assert coverage["targets"]["train/next_count/informative_lower_bound_eligible"] == 1
    assert coverage["row_reasons"]["purged/input_extent_crosses_role_boundary"] == 1
    assert coverage["row_reasons"]["outside/origin_outside_roles"] == 1
    assert coverage["target_reasons"][
        "train/next_count/target_maturity_or_role_boundary"
    ] >= 1
    assert coverage["target_reasons"]["validation/next_count/knowledge_cutoff"] == 1
    assert coverage["target_reasons"]["outside/next_count/origin_outside_roles"] == 1
    packed = list(batches(snapshot, plan, "train", 4, with_censoring=True))
    exact = sum(int(batch[2][:, 0].sum()) for batch in packed)
    lower = sum(int(batch[4][:, 0].sum()) for batch in packed)
    assert coverage["targets"]["train/next_count/exact_eligible"] == exact
    assert coverage["targets"]["train/next_count/informative_lower_bound_eligible"] == lower
