import copy

import pytest

from memecoin_experience import coordinate_frontier, coordinate_samples
from memecoin_experience.composition import compose
from memecoin_experience.records import split_plan
from memecoin_experience.schema import checked_samples, membership
from memecoin_experience.storage import Snapshot, build
from memecoin_experience.transfer import replicate


def _schema(availability="historical_replay"):
    return coordinate_samples.schema(
        context=1,
        features=[dict(name="prior_count", unit="events", modality="process")],
        targets=[
            dict(
                name="next_slot_count",
                unit="events",
                kind="observed_process_count@1slots",
            )
        ],
        availability=availability,
    )


def _row(coordinate, *, status="exact", value=0, availability=100, decision=None):
    detail = {}
    if status == "lower_bound":
        detail = {"lower_bound": value, "lower_bound_available_ns": availability}
        value = None
        availability = None
    elif status in {"masked", "pending"}:
        value = None
        availability = None
    window_status = (
        "complete" if status == "exact" else "unknown" if status == "pending" else "incomplete"
    )
    available = 90 if decision is None else min(90, decision)
    return dict(
        id=f"program:{coordinate}",
        pool="solana/program/pumpswap",
        origin=dict(
            domain_pin=coordinate_samples.digest(coordinate_samples.SLOT_DOMAIN),
            coordinate=coordinate,
            position=[],
        ),
        decision_ns=decision,
        input_dependencies=[
            dict(
                artifact_sha="a" * 64,
                domain_pin=coordinate_samples.digest(coordinate_samples.SLOT_DOMAIN),
                start_coordinate=coordinate,
                end_coordinate_exclusive=coordinate + 1,
                available_ns=available,
            )
        ],
        input_available_ns=available,
        values=[[float(coordinate)]],
        targets=[value],
        target_status=[status],
        target_windows=[
            dict(
                domain_pin=coordinate_samples.digest(coordinate_samples.SLOT_DOMAIN),
                start_coordinate=coordinate + 1,
                end_coordinate_exclusive=coordinate + 2,
                maturity_coordinate_exclusive=coordinate + 2,
                completion_status=window_status,
                completion_evidence=["b" * 64] if status == "exact" else [],
                support_reasons=[] if status == "exact" else ["partial_support"],
            )
        ],
        target_available_ns=[availability],
        target_details=[detail],
        provenance=["c" * 64],
    )


def _plan(*, cutoff=1000):
    def role(intervals):
        return {
            "intervals": intervals,
            "dependency_intervals": {},
            "knowledge_cutoff_ns": cutoff,
        }

    return split_plan(
        coordinate_samples.make_split_plan(
            coordinate_samples.digest(coordinate_samples.SLOT_DOMAIN),
            {
                "train": role([[0, 4]]),
                "validation": role([[4, 8]]),
                "test": role([[8, 12]]),
            },
        )
    )


def test_native_samples_keep_slot_coordinates_separate_from_utc_clocks():
    schema = _schema()
    row = _row(1)
    assert list(checked_samples([row], schema, 1000)) == [row]
    assert row["decision_ns"] is None
    assert row["origin"]["coordinate"] == 1
    assert "origin_ns" not in row and "target_maturity_ns" not in row
    assert membership(row, _plan(), schema) == ("train", [True])


def test_domain_role_extent_and_independent_utc_cutoff_are_gated():
    schema, plan = _schema(), _plan(cutoff=95)
    row = _row(4, availability=100)
    assert coordinate_samples.eligibility(row, schema, plan, "validation")["exact_mask"] == [False]
    assert membership(row, plan, schema)[0] == "validation"
    crossing = _row(4)
    crossing["input_dependencies"][0]["start_coordinate"] = 3
    assert (
        coordinate_samples.eligibility(crossing, schema, _plan(), "validation")["eligible"] is False
    )
    assert membership(crossing, _plan(), schema) == ("purged", [False])


def test_pit_requires_actual_decision_and_rejects_future_input():
    schema = _schema("point_in_time")
    missing_decision = _row(1, decision=None)
    with pytest.raises(ValueError, match="pit_decision"):
        list(checked_samples([missing_decision], schema, 1000))
    future = _row(1, decision=80)
    future["input_available_ns"] = 90
    future["input_dependencies"][0]["available_ns"] = 90
    with pytest.raises(ValueError, match="future_input"):
        list(checked_samples([future], schema, 1000))


def test_pit_future_target_receipts_use_snapshot_and_role_cutoffs():
    schema = _schema("point_in_time")
    exact = _row(1, decision=100, availability=150)
    assert list(checked_samples([exact], schema, 200)) == [exact]
    assert coordinate_samples.eligibility(exact, schema, _plan(cutoff=200), "train")[
        "exact_mask"
    ] == [True]
    assert coordinate_samples.eligibility(exact, schema, _plan(cutoff=140), "train")[
        "exact_mask"
    ] == [False]
    with pytest.raises(ValueError, match="target_window"):
        list(checked_samples([exact], schema, 140))

    lower_bound = _row(1, status="lower_bound", value=2, availability=150, decision=100)
    assert list(checked_samples([lower_bound], schema, 200)) == [lower_bound]
    assert coordinate_samples.eligibility(lower_bound, schema, _plan(cutoff=200), "train")[
        "lower_bound_mask"
    ] == [True]
    assert coordinate_samples.eligibility(lower_bound, schema, _plan(cutoff=140), "train")[
        "lower_bound_mask"
    ] == [False]
    with pytest.raises(ValueError, match="lower_bound"):
        list(checked_samples([lower_bound], schema, 140))


def test_unknown_domain_and_invalid_zero_lower_bound_fail_closed():
    schema, row = _schema(), _row(1, status="lower_bound", value=0)
    bad_schema = copy.deepcopy(schema)
    bad_schema["coordinate_domain"] = dict(coordinate_samples.SLOT_DOMAIN, unit="nanoseconds")
    with pytest.raises(ValueError, match="coordinate_domain"):
        from memecoin_experience.schema import validate

        validate(bad_schema)
    with pytest.raises(ValueError, match="lower_bound"):
        list(checked_samples([row], schema, 1000))


def test_composed_v3_learning_does_not_require_parent_manifests(tmp_path):
    from memecoin_experience.learning import batches

    schema = _schema()
    store = tmp_path / "composed-coordinate-store"
    first = build(
        store,
        [_row(1, status="lower_bound", value=2)],
        sources=[dict(kind="synthetic", pin="a" * 64)],
        cutoff=1000,
        schema=schema,
    )
    second = build(
        store,
        [_row(2)],
        sources=[dict(kind="synthetic", pin="b" * 64)],
        cutoff=1000,
        schema=schema,
    )
    composed = compose(store, [first["snapshot"], second["snapshot"]])
    (store / "snapshots" / f"{first['snapshot']}.json").unlink()
    (store / "snapshots" / f"{second['snapshot']}.json").unlink()

    snapshot = Snapshot(store, composed["snapshot"])
    rows = [
        row_id
        for batch in batches(snapshot, _plan(), "train", 4, with_censoring=True)
        for row_id in batch[5]
    ]
    assert rows == ["program:1", "program:2"]


def test_count_targets_and_bounds_reject_fractional_counts():
    schema = _schema()
    exact = _row(1, value=1.5)
    with pytest.raises(ValueError, match="exact_target"):
        list(checked_samples([exact], schema, 1000))
    bound = _row(1, status="lower_bound", value=1)
    bound["target_details"][0]["lower_bound"] = 1.5
    with pytest.raises(ValueError, match="lower_bound"):
        list(checked_samples([bound], schema, 1000))


def test_pending_window_is_not_collapsed_into_incomplete_support():
    schema = _schema()
    pending = _row(1, status="pending")
    assert list(checked_samples([pending], schema, 1000)) == [pending]
    pending["target_windows"][0]["completion_status"] = "incomplete"
    with pytest.raises(ValueError, match="masked_target"):
        list(checked_samples([pending], schema, 1000))


def test_v3_partition_readback_rechecks_receipt_cutoffs(tmp_path):
    import json
    import shutil

    from memecoin_experience.storage import atomic_json, canonical, digest, object_path, seal_files

    schema = _schema()
    for status in ("exact", "lower_bound", "pending"):
        store = tmp_path / f"valid-{status}"
        row = _row(1, status=status, value=2 if status == "lower_bound" else 0, availability=100)
        result = build(
            store,
            [row],
            sources=[dict(kind="synthetic", pin="d" * 64)],
            cutoff=100,
            schema=schema,
        )
        snapshot = Snapshot(store, result["snapshot"])
        assert snapshot.partition(0)[1][0]["target_status"] == [status]

        staged = tmp_path / f"re-pinned-{status}"
        shutil.copytree(object_path(store, snapshot.parts[0]["pin"]), staged)
        metadata_path = staged / "rows.jsonl"
        metadata = json.loads(metadata_path.read_text())
        if status == "exact":
            metadata["target_available_ns"][0] = 101
            error = "target_window_0"
        elif status == "lower_bound":
            metadata["target_details"][0]["lower_bound_available_ns"] = 101
            error = "lower_bound"
        else:
            metadata["target_available_ns"][0] = 100
            error = "masked_target"
        metadata_path.write_bytes(canonical(metadata) + b"\n")
        (staged / "manifest.json").unlink()
        forged_partition_pin = seal_files(staged)
        forged_partition = object_path(store, forged_partition_pin)
        forged_partition.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(staged, forged_partition)

        forged_manifest = json.loads(
            (store / "snapshots" / f"{result['snapshot']}.json").read_text()
        )
        forged_manifest["partitions"][0]["pin"] = forged_partition_pin
        forged_snapshot_pin = digest(forged_manifest)
        atomic_json(store / "snapshots" / f"{forged_snapshot_pin}.json", forged_manifest)
        with pytest.raises(ValueError, match=error):
            Snapshot(store, forged_snapshot_pin).partition(0)


def test_exact_zero_is_eligible_but_incomplete_zero_has_no_target():
    schema = _schema()
    exact_zero = _row(1, status="exact", value=0)
    assert coordinate_samples.eligibility(exact_zero, schema, _plan(), "train")["exact_mask"] == [
        True
    ]
    incomplete_zero = _row(1, status="masked")
    assert coordinate_samples.eligibility(incomplete_zero, schema, _plan(), "train")[
        "exact_mask"
    ] == [False]
    assert incomplete_zero["targets"] == [None]


def test_v3_snapshot_replication_and_coordinate_composition_boundary(tmp_path):
    schema = _schema()
    rows = [_row(coordinate) for coordinate in range(1, 10)]
    source = tmp_path / "source"
    result = build(
        source,
        rows,
        sources=[dict(kind="synthetic", pin="d" * 64)],
        cutoff=1000,
        schema=schema,
        chunk=2,
    )
    snapshot = Snapshot(source, result["snapshot"])
    assert snapshot.schema == schema
    assert len(snapshot.parts) > 1
    assert snapshot.partition(0)[1][0]["decision_ns"] is None
    copied = tmp_path / "replica"
    assert replicate(source, copied, result["snapshot"])["copied"] > 0
    replica = Snapshot(copied, result["snapshot"])
    assert replica.schema == schema
    assert [
        row["origin"]["coordinate"]
        for i in range(len(replica.parts))
        for row in replica.partition(i)[1]
    ] == list(range(1, 10))
    combined = compose(source, [result["snapshot"]])
    assert combined["composition_counts"]["unique_partitions"] == len(snapshot.parts)


def test_native_coordinate_composition_is_disjoint_and_domain_aware(tmp_path):
    schema = _schema()
    source = tmp_path / "source"
    first = build(
        source,
        [_row(c) for c in range(1, 5)],
        sources=[dict(kind="synthetic", pin="d" * 64)],
        cutoff=1000,
        schema=schema,
        chunk=2,
    )
    second = build(
        source,
        [_row(c) for c in range(5, 9)],
        sources=[dict(kind="synthetic", pin="e" * 64)],
        cutoff=1000,
        schema=schema,
        chunk=2,
    )
    joined = compose(source, [first["snapshot"], second["snapshot"]])
    assert (
        joined["composition"]
        == "disjoint_entity_native_coordinate_intervals; identical_objects_deduplicated"
    )
    assert joined["composition_counts"]["unique_partitions"] == 4
    joined_snapshot = Snapshot(source, joined["snapshot"])
    assert sum(len(joined_snapshot.partition(i)[1]) for i in range(len(joined_snapshot.parts))) == 8

    overlapping = build(
        source,
        [_row(c) for c in range(4, 7)],
        sources=[dict(kind="synthetic", pin="f" * 64)],
        cutoff=1000,
        schema=schema,
        chunk=2,
    )
    with pytest.raises(ValueError, match="composition_overlap"):
        compose(source, [first["snapshot"], overlapping["snapshot"]])

    other = dict(coordinate_samples.SLOT_DOMAIN, name="other_slot_axis")
    other_schema = coordinate_samples.schema(
        context=1,
        features=[dict(name="prior_count", unit="events", modality="process")],
        targets=[dict(name="next_slot_count", unit="events", kind="observed_process_count@1slots")],
        availability="historical_replay",
        coordinate_domain=other,
    )
    other_snapshot = build(
        source,
        [
            _row(c)
            | {
                "origin": {
                    "domain_pin": coordinate_samples.digest(other),
                    "coordinate": c,
                    "position": [],
                },
                "input_dependencies": [
                    _row(c)["input_dependencies"][0]
                    | {"domain_pin": coordinate_samples.digest(other)}
                ],
                "target_windows": [
                    _row(c)["target_windows"][0] | {"domain_pin": coordinate_samples.digest(other)}
                ],
            }
            for c in range(1, 3)
        ],
        sources=[dict(kind="synthetic", pin="9" * 64)],
        cutoff=1000,
        schema=other_schema,
        chunk=2,
    )
    with pytest.raises(ValueError, match="composition_contract"):
        compose(source, [first["snapshot"], other_snapshot["snapshot"]])


def test_composition_rechecks_shared_v3_partition_at_each_parent_cutoff(tmp_path, monkeypatch):
    import json

    from memecoin_experience.storage import atomic_json, digest

    schema = _schema()
    built = build(
        tmp_path,
        [_row(1, availability=150)],
        sources=[dict(kind="synthetic", pin="e" * 64)],
        cutoff=200,
        schema=schema,
    )
    original = json.loads((tmp_path / "snapshots" / f"{built['snapshot']}.json").read_text())

    candidates = {}
    for cutoff in (200, 100):
        candidates[cutoff] = []
        for marker in range(1000):
            manifest = copy.deepcopy(original)
            manifest["binding"]["cutoff_ns"] = cutoff
            manifest["compose_test_order"] = marker
            candidates[cutoff].append((digest(manifest), manifest))
    high_pin, high_manifest = next(
        candidate
        for candidate in candidates[200]
        if any(candidate[0] < pin for pin, _ in candidates[100])
    )
    low_pin, low_manifest = next(
        candidate for candidate in candidates[100] if high_pin < candidate[0]
    )
    atomic_json(tmp_path / "snapshots" / f"{high_pin}.json", high_manifest)
    atomic_json(tmp_path / "snapshots" / f"{low_pin}.json", low_manifest)

    original_partition = Snapshot.partition
    reads = []

    def tracked_partition(snapshot, index):
        reads.append((snapshot.parts[index]["pin"], snapshot.manifest["binding"]["cutoff_ns"]))
        return original_partition(snapshot, index)

    monkeypatch.setattr(Snapshot, "partition", tracked_partition)
    with pytest.raises(ValueError, match="target_window_0"):
        compose(tmp_path, [high_pin, low_pin])
    shared_pin = Snapshot(tmp_path, high_pin).parts[0]["pin"]
    assert shared_pin == Snapshot(tmp_path, low_pin).parts[0]["pin"]
    assert reads == [(shared_pin, 200), (shared_pin, 100)]


def test_registry_accepts_multiple_domains_without_implicit_alignment():
    slot = coordinate_samples.SLOT_DOMAIN
    block_height = {
        "version": "typed-coordinate-domain-v1",
        "name": "solana_block_height",
        "network": "solana-mainnet",
        "kind": "block_height",
        "unit": "block",
    }
    schema = coordinate_samples.schema(
        context=1,
        features=[dict(name="prior_count", unit="events", modality="process")],
        targets=[dict(name="next_slot_count", unit="events", kind="observed_process_count@1slots")],
        availability="historical_replay",
        coordinate_domains=[slot, block_height],
    )
    slot_pin = coordinate_samples.digest(slot)
    block_pin = coordinate_samples.digest(block_height)
    row = _row(1)
    row["input_dependencies"].append(
        dict(
            artifact_sha="e" * 64,
            domain_pin=block_pin,
            start_coordinate=100,
            end_coordinate_exclusive=101,
            available_ns=90,
        )
    )
    plan = coordinate_samples.make_split_plan(
        slot_pin,
        {
            "train": {
                "intervals": [[0, 4]],
                "dependency_intervals": {block_pin: [[100, 200]]},
                "knowledge_cutoff_ns": 100,
            },
            "validation": {
                "intervals": [[4, 8]],
                "dependency_intervals": {block_pin: [[200, 300]]},
                "knowledge_cutoff_ns": 100,
            },
            "test": {
                "intervals": [[8, 12]],
                "dependency_intervals": {block_pin: [[300, 400]]},
                "knowledge_cutoff_ns": 100,
            },
        },
        coordinate_domain_pins=[slot_pin, block_pin],
    )
    assert list(checked_samples([row], schema, 1000)) == [row]
    assert coordinate_samples.eligibility(row, schema, plan, "train")["exact_mask"] == [True]
    row["input_dependencies"][1]["end_coordinate_exclusive"] = 201
    assert (
        coordinate_samples.eligibility(row, schema, plan, "train")["reason"]
        == "input_extent_crosses_role_boundary"
    )
    row["input_dependencies"][1]["domain_pin"] = "f" * 64
    with pytest.raises(ValueError, match="dependency"):
        list(checked_samples([row], schema, 1000))


def test_registry_requires_monotonic_cutoffs_and_missing_domain_extents_fail():
    slot = coordinate_samples.SLOT_DOMAIN
    extra = dict(slot, name="other_clock", kind="counter", unit="tick")
    primary_pin, extra_pin = coordinate_samples.digest(slot), coordinate_samples.digest(extra)
    plan = {
        "version": coordinate_samples.SPLIT_VERSION,
        "coordinate_domain_pin": primary_pin,
        "coordinate_domain_pins": sorted([primary_pin, extra_pin]),
        "eligibility_policy": coordinate_samples.ELIGIBILITY_POLICY,
        "roles": {
            "train": {
                "intervals": [[0, 4]],
                "dependency_intervals": {extra_pin: [[0, 4]]},
                "knowledge_cutoff_ns": 20,
            },
            "validation": {
                "intervals": [[4, 8]],
                "dependency_intervals": {extra_pin: [[4, 8]]},
                "knowledge_cutoff_ns": 10,
            },
            "test": {
                "intervals": [[8, 12]],
                "dependency_intervals": {extra_pin: [[8, 12]]},
                "knowledge_cutoff_ns": 30,
            },
        },
    }
    with pytest.raises(ValueError, match="knowledge_cutoff_order"):
        coordinate_samples.validate_split_plan(plan)
    plan["roles"]["train"]["dependency_intervals"] = {}
    plan["roles"]["validation"]["knowledge_cutoff_ns"] = 20
    with pytest.raises(ValueError, match="split_role"):
        coordinate_samples.validate_split_plan(plan)


def test_coordinate_frontier_covers_all_roles_domains_and_parent_lineage():
    schema = _schema()
    primary = schema["coordinate_domain_pin"]
    out_of_order = coordinate_samples.make_split_plan(
        primary,
        {
            "train": {
                "intervals": [[50, 60]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 10,
            },
            "validation": {
                "intervals": [[10, 20]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 20,
            },
            "test": {
                "intervals": [[30, 40]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 30,
            },
        },
    )
    frontier = coordinate_frontier.from_plan(out_of_order)
    assert frontier["exclusive_ends"][primary] == 60
    valid_forward = coordinate_samples.make_split_plan(
        primary,
        {
            "train": {
                "intervals": [[0, 10]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 10,
            },
            "validation": {
                "intervals": [[10, 20]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 20,
            },
            "test": {
                "intervals": [[60, 70]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 30,
            },
        },
    )
    coordinate_frontier.require_test_after(frontier, valid_forward)
    too_early = copy.deepcopy(valid_forward)
    too_early["roles"]["test"]["intervals"] = [[59, 70]]
    with pytest.raises(ValueError, match="experience_coordinate_evaluation_seen_interval"):
        coordinate_frontier.require_test_after(frontier, too_early)

    block_height = dict(
        version="typed-coordinate-domain-v1",
        name="solana_block_height",
        network="solana-mainnet",
        kind="block_height",
        unit="block",
    )
    secondary = coordinate_samples.digest(block_height)
    multi = coordinate_samples.make_split_plan(
        primary,
        {
            "train": {
                "intervals": [[0, 4]],
                "dependency_intervals": {secondary: [[100, 200]]},
                "knowledge_cutoff_ns": 10,
            },
            "validation": {
                "intervals": [[4, 8]],
                "dependency_intervals": {secondary: [[200, 300]]},
                "knowledge_cutoff_ns": 20,
            },
            "test": {
                "intervals": [[8, 12]],
                "dependency_intervals": {secondary: [[300, 400]]},
                "knowledge_cutoff_ns": 30,
            },
        },
        coordinate_domain_pins=[primary, secondary],
    )
    multi_schema = coordinate_samples.schema(
        context=1,
        features=[dict(name="prior_count", unit="events", modality="process")],
        targets=[dict(name="next_count", unit="events", kind="observed_process_count@1slots")],
        availability="historical_replay",
        coordinate_domains=[coordinate_samples.SLOT_DOMAIN, block_height],
    )
    multi_frontier = coordinate_frontier.from_plan(multi)
    assert multi_frontier["exclusive_ends"][secondary] == 400
    overlap = coordinate_samples.make_split_plan(
        primary,
        {
            "train": {
                "intervals": [[0, 4]],
                "dependency_intervals": {secondary: [[0, 10]]},
                "knowledge_cutoff_ns": 10,
            },
            "validation": {
                "intervals": [[4, 8]],
                "dependency_intervals": {secondary: [[10, 20]]},
                "knowledge_cutoff_ns": 20,
            },
            "test": {
                "intervals": [[12, 16]],
                "dependency_intervals": {secondary: [[399, 450]]},
                "knowledge_cutoff_ns": 30,
            },
        },
        coordinate_domain_pins=[primary, secondary],
    )
    coordinate_frontier.validate(
        multi_frontier, [row["pin"] for row in multi_schema["coordinate_domains"]]
    )
    with pytest.raises(ValueError, match="experience_coordinate_evaluation_seen_interval"):
        coordinate_frontier.require_test_after(multi_frontier, overlap)

    inherited = coordinate_frontier.merge(frontier, coordinate_frontier.from_plan(valid_forward))
    assert inherited["exclusive_ends"][primary] == 70
    legacy_report = dict(
        binding=dict(parent=None, initialization="seeded_fresh", plan=out_of_order),
        ancestors=[],
        seen_end_coordinate=40,
    )
    assert coordinate_frontier.from_report(legacy_report, schema) == frontier
    underreported = copy.deepcopy(legacy_report)
    underreported["seen_coordinate_frontier"] = copy.deepcopy(frontier)
    underreported["seen_coordinate_frontier"]["exclusive_ends"][primary] = 40
    underreported["seen_end_coordinate"] = 40
    with pytest.raises(ValueError, match="experience_coordinate_frontier_understated"):
        coordinate_frontier.from_report(underreported, schema)
    missing_ancestors = copy.deepcopy(legacy_report)
    del missing_ancestors["ancestors"]
    with pytest.raises(ValueError, match="experience_coordinate_frontier_unproven_legacy_lineage"):
        coordinate_frontier.from_report(missing_ancestors, schema)
    missing_parent = copy.deepcopy(legacy_report)
    del missing_parent["binding"]["parent"]
    with pytest.raises(ValueError, match="experience_coordinate_frontier_unproven_legacy_lineage"):
        coordinate_frontier.from_report(missing_parent, schema)

    underreported_secondary = copy.deepcopy(multi_frontier)
    underreported_secondary["exclusive_ends"][secondary] = 399
    secondary_report = dict(
        binding=dict(parent=None, initialization="seeded_fresh", plan=multi),
        ancestors=[],
        seen_coordinate_frontier=underreported_secondary,
        seen_end_coordinate=12,
    )
    with pytest.raises(ValueError, match="experience_coordinate_frontier_understated"):
        coordinate_frontier.from_report(secondary_report, multi_schema)
    legacy_child = dict(
        binding=dict(
            parent="f" * 64,
            initialization="parent_weights_frozen_normalizer_fresh_optimizer",
            plan=out_of_order,
        ),
        seen_end_coordinate=40,
    )
    with pytest.raises(ValueError, match="experience_coordinate_frontier_unproven_legacy_lineage"):
        coordinate_frontier.from_report(legacy_child, schema)
    incomplete = copy.deepcopy(frontier)
    incomplete["exclusive_ends"] = {}
    incomplete_report = dict(
        binding=dict(parent=None, initialization="seeded_fresh", plan=out_of_order),
        ancestors=[],
        seen_coordinate_frontier=incomplete,
        seen_end_coordinate=60,
    )
    with pytest.raises(ValueError, match="experience_coordinate_frontier"):
        coordinate_frontier.from_report(incomplete_report, schema)

    empty_secondary = coordinate_samples.make_split_plan(
        primary,
        {
            "train": {
                "intervals": [[0, 4]],
                "dependency_intervals": {secondary: []},
                "knowledge_cutoff_ns": 10,
            },
            "validation": {
                "intervals": [[4, 8]],
                "dependency_intervals": {secondary: []},
                "knowledge_cutoff_ns": 20,
            },
            "test": {
                "intervals": [[8, 12]],
                "dependency_intervals": {secondary: []},
                "knowledge_cutoff_ns": 30,
            },
        },
        coordinate_domain_pins=[primary, secondary],
    )
    empty_frontier = coordinate_frontier.from_plan(empty_secondary)
    assert empty_frontier["exclusive_ends"][secondary] is None
    unknown_domain_test = coordinate_samples.make_split_plan(
        primary,
        {
            "train": {
                "intervals": [[0, 4]],
                "dependency_intervals": {secondary: []},
                "knowledge_cutoff_ns": 10,
            },
            "validation": {
                "intervals": [[4, 8]],
                "dependency_intervals": {secondary: []},
                "knowledge_cutoff_ns": 20,
            },
            "test": {
                "intervals": [[12, 16]],
                "dependency_intervals": {secondary: [[0, 10]]},
                "knowledge_cutoff_ns": 30,
            },
        },
        coordinate_domain_pins=[primary, secondary],
    )
    with pytest.raises(ValueError, match="experience_coordinate_evaluation_seen_interval"):
        coordinate_frontier.require_test_after(empty_frontier, unknown_domain_test)


def test_v3_replication_batch_fit_resume_and_registered_evaluation(tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    pytest.importorskip("torch")

    from memecoin_experience import evaluation, learning
    from memecoin_experience.learning import batches, train
    from memecoin_experience.storage import file_hash

    schema = _schema()
    plan = coordinate_samples.make_split_plan(
        schema["coordinate_domain_pin"],
        {
            "train": {
                "intervals": [[0, 10]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 1000,
            },
            "validation": {
                "intervals": [[10, 20]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 1000,
            },
            "test": {
                "intervals": [[20, 30]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 1000,
            },
        },
    )
    rows = [_row(c, status="lower_bound", value=2) if c == 5 else _row(c) for c in range(1, 29)]
    source = tmp_path / "source"
    train_snapshot = build(
        source,
        rows,
        sources=[dict(kind="synthetic", pin="d" * 64)],
        cutoff=1000,
        schema=schema,
        chunk=4,
    )["snapshot"]
    replica = tmp_path / "replica"
    replicate(source, replica, train_snapshot)
    original = Snapshot(source, train_snapshot)
    copied = Snapshot(replica, train_snapshot)
    original_batches = list(batches(original, plan, "train", 4, with_censoring=True))
    replica_batches = list(batches(copied, plan, "train", 4, with_censoring=True))
    assert [item[5] for item in original_batches] == [item[5] for item in replica_batches]
    assert any(any(item[4].ravel()) for item in original_batches)

    config = dict(
        width=4,
        batch=4,
        epochs=1,
        seed=7,
        device="cpu",
        objective="partial_count_lower_bound_pinball_v1",
    )
    complete = train(replica, train_snapshot, tmp_path / "fit-full", plan=plan, config=config)
    pause_path = tmp_path / "fit-resume"
    paused = train(replica, train_snapshot, pause_path, plan=plan, config=config, stop_after=1)
    assert paused["status"] == "interrupted"
    resumed = train(replica, train_snapshot, pause_path, plan=plan, config=config)
    assert resumed == complete
    assert complete["statistics"]["population"]["train_partial_count_usable"] == 1

    eval_plan = coordinate_samples.make_split_plan(
        schema["coordinate_domain_pin"],
        {
            "train": {
                "intervals": [[0, 10]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 1000,
            },
            "validation": {
                "intervals": [[10, 20]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 1000,
            },
            "test": {
                "intervals": [[30, 40]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 1000,
            },
        },
    )
    eval_store = tmp_path / "eval-store"
    eval_snapshot = build(
        eval_store,
        [_row(c) for c in range(31, 39)],
        sources=[dict(kind="synthetic", pin="e" * 64)],
        cutoff=1000,
        schema=schema,
        role="research_evaluation",
        chunk=3,
    )["snapshot"]
    artifact = tmp_path / "fit-full/artifact"
    artifact_pin = file_hash(artifact / "manifest.json")
    child_plan = coordinate_samples.make_split_plan(
        schema["coordinate_domain_pin"],
        {
            "train": {
                "intervals": [[0, 9]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 1000,
            },
            "validation": {
                "intervals": [[10, 19]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 1000,
            },
            "test": {
                "intervals": [[20, 29]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 1000,
            },
        },
    )
    child_report = train(
        replica,
        train_snapshot,
        tmp_path / "fit-child-frontier",
        plan=child_plan,
        config=config,
        parent={"path": str(artifact), "pin": artifact_pin},
    )
    assert child_report["seen_end_coordinate"] == 30
    assert (
        child_report["seen_coordinate_frontier"]["exclusive_ends"][schema["coordinate_domain_pin"]]
        == 30
    )

    unsafe_eval_plan = copy.deepcopy(eval_plan)
    unsafe_eval_plan["roles"]["test"]["intervals"] = [[29, 40]]
    with pytest.raises(ValueError, match="experience_coordinate_evaluation_seen_interval"):
        evaluation.register_coordinate(
            tmp_path / "protocols",
            artifact,
            artifact_pin,
            split_plan=unsafe_eval_plan,
            pools=["solana/program/pumpswap"],
        )
    registration = evaluation.register_coordinate(
        tmp_path / "protocols",
        artifact,
        artifact_pin,
        split_plan=eval_plan,
        pools=["solana/program/pumpswap"],
    )
    scored = evaluation.score(
        tmp_path / "protocols",
        registration["protocol"],
        eval_store,
        eval_snapshot,
        artifact,
        tmp_path / "score",
        batch=4,
    )
    assert scored["status"] == "completed"
    assert scored["metrics"][0]["n"] == 8
    assert scored["availability"] == "coordinate_replay; no slot_to_utc_conversion"
    assert scored["population"]["test"] == 8
    assert scored["population"]["test_exact_usable_origins"] == 8
    assert scored["population"]["test_scored_origins"] == 8

    import json

    from memecoin_experience.storage import atomic_json, digest

    protocol_root = tmp_path / "protocols-score-frontier"
    protocol_root.mkdir()
    protocol = json.loads((tmp_path / "protocols" / f"{registration['protocol']}.json").read_text())
    protocol["split_plan"]["roles"]["test"]["intervals"] = [[29, 40]]
    protocol_pin = digest(protocol)
    atomic_json(protocol_root / f"{protocol_pin}.json", protocol)
    rejected_output = tmp_path / "score-frontier-rejected"
    with pytest.raises(ValueError, match="experience_coordinate_evaluation_seen_interval"):
        evaluation.score(
            protocol_root,
            protocol_pin,
            eval_store,
            eval_snapshot,
            artifact,
            rejected_output,
            batch=4,
        )
    assert not rejected_output.exists()

    purged = _row(30)
    purged["input_dependencies"][0]["start_coordinate"] = 29
    enriched_rows = [_row(c) for c in range(31, 39)]
    enriched_rows[1]["id"] = enriched_rows[0]["id"]
    enriched_snapshot = build(
        eval_store,
        [purged, *enriched_rows, _row(39, status="pending")],
        sources=[dict(kind="synthetic", pin="f" * 64)],
        cutoff=1000,
        schema=schema,
        role="research_evaluation",
        chunk=3,
    )["snapshot"]
    enriched = evaluation.score(
        tmp_path / "protocols",
        registration["protocol"],
        eval_store,
        enriched_snapshot,
        artifact,
        tmp_path / "score-enriched",
        batch=4,
    )
    assert enriched["metrics"] == scored["metrics"]
    assert enriched["population"]["test_assigned_origins"] == 10
    assert enriched["population"]["test_exact_usable_origins"] == 8
    assert enriched["population"]["test_exact_usable_cells"] == 8
    assert enriched["population"]["test_pending_target_cells"] == 1
    assert enriched["population"]["test_purged_origins"] == 1
    assert enriched["population"]["test_scored_origins"] == 8
    assert enriched["denominators"]["test/next_slot_count/pending"] == 1
    assert enriched["denominators"]["test/next_slot_count/exact_usable"] == 8
    assert enriched["denominators"]["test/next_slot_count/scored_exact_usable"] == 8

    rogue = _row(39, status="pending")
    rogue["pool"] = "solana/program/rogue"
    rogue_snapshot = build(
        eval_store,
        [*[_row(c) for c in range(31, 39)], rogue],
        sources=[dict(kind="synthetic", pin="9" * 64)],
        cutoff=1000,
        schema=schema,
        role="research_evaluation",
        chunk=3,
    )["snapshot"]
    rogue_output = tmp_path / "score-rogue"
    with pytest.raises(ValueError, match="experience_coordinate_evaluation_enrollment"):
        evaluation.score(
            tmp_path / "protocols",
            registration["protocol"],
            eval_store,
            rogue_snapshot,
            artifact,
            rogue_output,
            batch=4,
        )
    assert not rogue_output.exists()

    original_batches = learning.batches

    def substituted_batches(*args, **kwargs):
        for x, y, mask, metadata, censoring in original_batches(*args, **kwargs):
            changed_metadata = [dict(row) for row in metadata]
            changed_metadata[0]["origin"] = dict(changed_metadata[0]["origin"])
            changed_metadata[0]["origin"]["coordinate"] += 1
            yield x, y, mask, changed_metadata, censoring

    monkeypatch.setattr(learning, "batches", substituted_batches)
    substituted_output = tmp_path / "score-substituted"
    with pytest.raises(ValueError, match="experience_coordinate_evaluation_census"):
        evaluation.score(
            tmp_path / "protocols",
            registration["protocol"],
            eval_store,
            enriched_snapshot,
            artifact,
            substituted_output,
            batch=4,
        )
    assert not substituted_output.exists()


def test_v3_preregistered_replay_requires_future_cutoff_and_decisions(tmp_path, monkeypatch):
    import json

    pytest.importorskip("numpy")
    pytest.importorskip("torch")

    from memecoin_experience import coordinate_frontier, evaluation, predict

    schema = _schema("point_in_time")
    schema["targets"].append(
        dict(name="next_slot_count/detail", unit="events", kind="observed_process_count@1slots")
    )
    primary = schema["coordinate_domain_pin"]

    def append_pending_target(row):
        coordinate = row["origin"]["coordinate"]
        row["targets"].append(None)
        row["target_status"].append("pending")
        row["target_windows"].append(
            dict(
                domain_pin=primary,
                start_coordinate=coordinate + 1,
                end_coordinate_exclusive=coordinate + 2,
                maturity_coordinate_exclusive=coordinate + 2,
                completion_status="unknown",
                completion_evidence=[],
                support_reasons=["synthetic_pending"],
            )
        )
        row["target_available_ns"].append(None)
        row["target_details"].append({})
        return row

    def make_plan(test_cutoff, test_intervals=((30, 40),)):
        return coordinate_samples.make_split_plan(
            primary,
            {
                "train": {
                    "intervals": [[0, 10]],
                    "dependency_intervals": {},
                    "knowledge_cutoff_ns": 1000,
                },
                "validation": {
                    "intervals": [[10, 20]],
                    "dependency_intervals": {},
                    "knowledge_cutoff_ns": 1400,
                },
                "test": {
                    "intervals": [list(interval) for interval in test_intervals],
                    "dependency_intervals": {},
                    "knowledge_cutoff_ns": test_cutoff,
                },
            },
        )

    train_plan = make_plan(1400, test_intervals=((20, 30),))
    frontier = coordinate_frontier.from_plan(train_plan)

    class SyntheticPredictor:
        def __init__(self, artifact, pin):
            self.spec = dict(schema=schema, quantiles=[0.1, 0.5, 0.9])
            self.stats = {"baseline": [[1.0, 2.0, 3.0] if pin == "a" * 64 else None, None]}
            self.report = dict(
                binding=dict(plan=train_plan),
                seen_coordinate_frontier=frontier,
                seen_end_coordinate=frontier["exclusive_ends"][primary],
            )

        def predict(self, frames):
            import numpy as np

            return np.zeros((len(frames), len(schema["targets"]), 3))

    monkeypatch.setattr(predict, "Predictor", SyntheticPredictor)
    artifact = tmp_path / "synthetic-artifact"
    artifact_pin = "a" * 64

    eval_plan = make_plan(2000)
    eval_store = tmp_path / "eval-store"
    purged = _row(30, decision=1630, availability=1730, value=10)
    purged["input_dependencies"][0]["start_coordinate"] = 29
    eval_rows = [purged]
    pending_pool_purged = _row(30, decision=1630, availability=1730, value=10)
    pending_pool_purged["pool"] = "solana/program/pending-only"
    pending_pool_purged["input_dependencies"][0]["start_coordinate"] = 29
    eval_rows.append(pending_pool_purged)
    eval_rows.extend(
        _row(c, decision=1600 + c, availability=1700 + c, value=c - 30) for c in range(31, 39)
    )
    eval_rows.append(_row(39, status="pending", decision=1639, availability=1739))
    pending_pool_row = _row(39, status="pending", decision=1639, availability=1739)
    pending_pool_row["pool"] = "solana/program/pending-only"
    eval_rows.append(pending_pool_row)
    eval_rows = [append_pending_target(row) for row in eval_rows]
    eval_rows.sort(
        key=lambda row: (
            row["pool"],
            row["origin"]["coordinate"],
            tuple(row["origin"]["position"]),
            row["id"],
        )
    )
    eval_snapshot = build(
        eval_store,
        eval_rows,
        sources=[dict(kind="synthetic", pin="2" * 64)],
        cutoff=2000,
        chunk=1,
        schema=schema,
        role="research_evaluation",
    )["snapshot"]
    monkeypatch.setattr(evaluation.time, "time_ns", lambda: 1500)
    protocol_root = tmp_path / "protocols"
    for cutoff in (1499, 1500):
        with pytest.raises(ValueError, match="experience_coordinate_evaluation_not_prospective"):
            evaluation.register_coordinate(
                protocol_root,
                artifact,
                artifact_pin,
                split_plan=make_plan(cutoff),
                pools=["solana/program/pumpswap"],
                scope="preregistered_forward_replay",
            )
    registration = evaluation.register_coordinate(
        protocol_root,
        artifact,
        artifact_pin,
        split_plan=eval_plan,
        pools=[
            "solana/program/pumpswap",
            "solana/program/pending-only",
            "solana/program/missing",
        ],
        scope="preregistered_forward_replay",
    )
    assert registration["registered_ns"] == 1500
    assert registration["metrics"] == evaluation.COORDINATE_METRICS_V3
    score_output = tmp_path / "future-score"
    scored = evaluation.score(
        protocol_root,
        registration["protocol"],
        eval_store,
        eval_snapshot,
        artifact,
        score_output,
        batch=4,
    )
    assert scored["status"] == "completed"
    assert scored["population"]["test_scored_origins"] == 8
    assert scored["population"]["test_purged_origins"] == 2
    assert scored["population"]["test_pending_target_cells"] == 14
    assert scored["pool_coverage"] == dict(
        registered=3,
        present_in_snapshot=2,
        test_assigned=2,
        exact_scored=1,
        missing_registered=1,
        missing_pool_ids=["solana/program/missing"],
        present_without_exact_scored=["solana/program/pending-only"],
    )
    metric = scored["metrics"][0]
    assert metric["n"] == 8 and metric["pinball"] > 0 and metric["coverage"] == 0
    import numpy as np

    quantiles = np.asarray([0.1, 0.5, 0.9])
    truths = np.arange(1.0, 9.0)
    baseline_values = np.asarray([1.0, 2.0, 3.0])
    errors = truths[:, None] - baseline_values
    expected_pinball = np.maximum(quantiles * errors, (quantiles - 1) * errors).mean(axis=1).mean()
    assert metric["train_baseline"] == dict(
        n=8,
        pinball=pytest.approx(expected_pinball),
        coverage=pytest.approx(3 / 8),
        unavailable_reason=None,
    )
    sparse_metric = scored["metrics"][1]
    assert sparse_metric == dict(
        target="next_slot_count/detail",
        n=0,
        pinball=None,
        coverage=None,
        train_baseline=dict(
            n=0,
            pinball=None,
            coverage=None,
            unavailable_reason="no_exact_train_baseline",
        ),
    )

    from memecoin_experience.storage import atomic_json, digest, file_hash

    v1_protocol = dict(registration)
    v1_protocol.pop("protocol")
    v1_protocol["metrics"] = evaluation.COORDINATE_METRICS_V1
    v1_pin = digest(v1_protocol)
    v1_root = tmp_path / "v1-protocols"
    v1_root.mkdir()
    atomic_json(v1_root / f"{v1_pin}.json", v1_protocol)
    v1_scored = evaluation.score(
        v1_root,
        v1_pin,
        eval_store,
        eval_snapshot,
        artifact,
        tmp_path / "v1-score",
        batch=4,
    )
    assert v1_scored["metrics"][0] == {
        key: metric[key] for key in ("target", "n", "pinball", "coverage")
    }
    assert "pool_coverage" not in v1_scored
    assert v1_scored == evaluation.score(
        v1_root,
        v1_pin,
        eval_store,
        eval_snapshot,
        artifact,
        tmp_path / "v1-score",
        batch=4,
    )

    v2_protocol = dict(registration)
    v2_protocol.pop("protocol")
    v2_protocol["metrics"] = evaluation.COORDINATE_METRICS_V2
    v2_pin = digest(v2_protocol)
    v2_root = tmp_path / "v2-protocols"
    v2_root.mkdir()
    atomic_json(v2_root / f"{v2_pin}.json", v2_protocol)
    v2_scored = evaluation.score(
        v2_root,
        v2_pin,
        eval_store,
        eval_snapshot,
        artifact,
        tmp_path / "v2-score",
        batch=4,
    )
    assert v2_scored["metrics"][0] == metric
    assert "pool_coverage" not in v2_scored
    assert v2_scored == evaluation.score(
        v2_root,
        v2_pin,
        eval_store,
        eval_snapshot,
        artifact,
        tmp_path / "v2-score",
        batch=4,
    )

    no_baseline = evaluation.register_coordinate(
        protocol_root,
        artifact,
        "b" * 64,
        split_plan=eval_plan,
        pools=[
            "solana/program/pumpswap",
            "solana/program/pending-only",
            "solana/program/missing",
        ],
        scope="preregistered_forward_replay",
    )
    no_baseline_scored = evaluation.score(
        protocol_root,
        no_baseline["protocol"],
        eval_store,
        eval_snapshot,
        artifact,
        tmp_path / "no-baseline-score",
        batch=4,
    )
    assert no_baseline_scored["metrics"][0]["train_baseline"] == dict(
        n=0,
        pinball=None,
        coverage=None,
        unavailable_reason="no_exact_train_baseline",
    )
    environment = scored["binding"]["environment"]
    assert set(environment) == {"python", "numpy", "torch", "device"}
    assert environment["device"] == "cpu"
    assert (
        evaluation.score(
            protocol_root,
            registration["protocol"],
            eval_store,
            eval_snapshot,
            artifact,
            score_output,
            batch=4,
        )
        == scored
    )
    report_before = (score_output / "report.json").read_bytes()
    protocol_copy_before = (score_output / "protocol.json").read_bytes()
    manifest_before = (score_output / "manifest.json").read_bytes()
    import platform

    with monkeypatch.context() as patch:
        patch.setattr(platform, "python_version", lambda: "simulated-version-change")
        with pytest.raises(ValueError, match="experience_coordinate_evaluation_binding"):
            evaluation.score(
                protocol_root,
                registration["protocol"],
                eval_store,
                eval_snapshot,
                artifact,
                score_output,
                batch=4,
            )
    assert (score_output / "report.json").read_bytes() == report_before
    assert (score_output / "manifest.json").read_bytes() == manifest_before

    def set_production_eligible(report_copy, protocol_copy):
        report_copy["production_eligible"] = True

    def change_protocol_copy(report_copy, protocol_copy):
        protocol_copy["candidate"] = "f" * 64

    def change_metric_n(report_copy, protocol_copy):
        report_copy["metrics"][0]["n"] += 1

    def erase_missing_pool_id(report_copy, protocol_copy):
        report_copy["pool_coverage"]["missing_pool_ids"] = []

    def change_missing_count(report_copy, protocol_copy):
        report_copy["pool_coverage"]["missing_registered"] += 1

    for corrupt in (
        set_production_eligible,
        change_protocol_copy,
        change_metric_n,
        erase_missing_pool_id,
        change_missing_count,
    ):
        report_copy = json.loads(report_before)
        protocol_copy = json.loads(protocol_copy_before)
        corrupt(report_copy, protocol_copy)
        atomic_json(score_output / "report.json", report_copy, replace=True)
        atomic_json(score_output / "protocol.json", protocol_copy, replace=True)
        inventory = {
            path.name: file_hash(path)
            for path in sorted(score_output.iterdir())
            if path.name != "manifest.json"
        }
        atomic_json(score_output / "manifest.json", inventory, replace=True)
        rejected_bytes = {
            name: (score_output / name).read_bytes()
            for name in ("report.json", "protocol.json", "manifest.json")
        }
        with pytest.raises(ValueError, match="experience_coordinate_evaluation_output"):
            evaluation.score(
                protocol_root,
                registration["protocol"],
                eval_store,
                eval_snapshot,
                artifact,
                score_output,
                batch=4,
            )
        assert rejected_bytes == {
            name: (score_output / name).read_bytes()
            for name in ("report.json", "protocol.json", "manifest.json")
        }
        (score_output / "report.json").write_bytes(report_before)
        (score_output / "protocol.json").write_bytes(protocol_copy_before)
        (score_output / "manifest.json").write_bytes(manifest_before)

    clean_rows = []
    for coordinate in range(31, 39):
        row = _row(coordinate, decision=1600 + coordinate, availability=1700 + coordinate)
        clean_rows.append(append_pending_target(row))
    clean_store = tmp_path / "clean-eval-store"
    clean_snapshot = build(
        clean_store,
        clean_rows,
        sources=[dict(kind="synthetic-clean", pin="3" * 64)],
        cutoff=2000,
        schema=schema,
        role="research_evaluation",
    )["snapshot"]
    clean_registration = evaluation.register_coordinate(
        protocol_root,
        artifact,
        artifact_pin,
        split_plan=eval_plan,
        pools=["solana/program/pumpswap"],
        scope="preregistered_forward_replay",
    )
    clean_score_output = tmp_path / "clean-score"
    clean_scored = evaluation.score(
        protocol_root,
        clean_registration["protocol"],
        clean_store,
        clean_snapshot,
        artifact,
        clean_score_output,
        batch=4,
    )
    assert "test_purged_origins" not in clean_scored["population"]
    assert clean_scored["metrics"][1]["n"] == 0
    assert clean_scored == evaluation.score(
        protocol_root,
        clean_registration["protocol"],
        clean_store,
        clean_snapshot,
        artifact,
        clean_score_output,
        batch=4,
    )

    protocol = dict(registration)
    protocol.pop("protocol")
    protocol["registered_ns"] = 1700
    late_pin = digest(protocol)
    late_root = tmp_path / "late-protocol"
    late_root.mkdir()
    atomic_json(late_root / f"{late_pin}.json", protocol)
    late_output = tmp_path / "late-registration-score"
    with pytest.raises(ValueError, match="experience_coordinate_evaluation_not_future"):
        evaluation.score(
            late_root,
            late_pin,
            eval_store,
            eval_snapshot,
            artifact,
            late_output,
            batch=4,
        )
    assert not late_output.exists()

    for kind in ("pending", "purged"):
        row = (
            _row(31, status="pending", decision=1499)
            if kind == "pending"
            else _row(31, decision=1499)
        )
        if kind == "purged":
            row["input_dependencies"][0]["start_coordinate"] = 29
        row = append_pending_target(row)
        bad_snapshot = build(
            eval_store,
            [row],
            sources=[dict(kind="synthetic", pin=("3" if kind == "pending" else "4") * 64)],
            cutoff=2000,
            schema=schema,
            role="research_evaluation",
        )["snapshot"]
        bad_output = tmp_path / f"pre-registration-{kind}"
        with pytest.raises(ValueError, match="experience_coordinate_evaluation_not_future"):
            evaluation.score(
                protocol_root,
                registration["protocol"],
                eval_store,
                bad_snapshot,
                artifact,
                bad_output,
                batch=4,
            )
        assert not bad_output.exists()


@pytest.mark.parametrize(
    "tamper",
    ["epoch_jump", "forged_rows_seen", "forged_cursor", "missing_history", "missing_best"],
)
def test_v3_resume_rejects_inconsistent_checkpoint_progress(tmp_path, tamper):
    torch = pytest.importorskip("torch")

    from memecoin_experience.learning import train

    schema = _schema()
    plan = coordinate_samples.make_split_plan(
        schema["coordinate_domain_pin"],
        {
            "train": {
                "intervals": [[0, 10]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 1000,
            },
            "validation": {
                "intervals": [[10, 20]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 1000,
            },
            "test": {
                "intervals": [[20, 30]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 1000,
            },
        },
    )
    store = tmp_path / "store"
    snapshot = build(
        store,
        [_row(c) for c in range(1, 29)],
        sources=[dict(kind="synthetic", pin="a" * 64)],
        cutoff=1000,
        schema=schema,
        chunk=4,
    )["snapshot"]
    config = dict(width=4, batch=4, epochs=2, seed=7, device="cpu", checkpoint_every=1)
    base = tmp_path / "base"
    if tamper in {"missing_history", "missing_best"}:
        train(store, snapshot, base, plan=plan, config=config)
        import shutil

        shutil.rmtree(base / "artifact")
    else:
        train(store, snapshot, base, plan=plan, config=config, stop_after=1)

    import shutil

    job = tmp_path / "tampered"
    shutil.copytree(base, job)
    checkpoint = job / "checkpoint.pt"
    saved = torch.load(checkpoint, map_location="cpu", weights_only=True)
    if tamper == "epoch_jump":
        saved["epoch"] = config["epochs"]
    elif tamper == "forged_rows_seen":
        saved["rows_seen"] += 1
    elif tamper == "forged_cursor":
        saved["cursor"] = (len(Snapshot(store, snapshot).parts) + 10, 0)
    elif tamper == "missing_history":
        saved["history"].pop()
    elif tamper == "missing_best":
        saved["best"] = None
    torch.save(saved, checkpoint)

    with pytest.raises(ValueError, match="experience_checkpoint_progress"):
        train(store, snapshot, job, plan=plan, config=config)


@pytest.mark.parametrize("boundary", ["last_batch", "epoch_checkpoint"])
def test_v3_resume_accepts_training_boundary_checkpoints(tmp_path, monkeypatch, boundary):
    torch = pytest.importorskip("torch")

    from memecoin_experience import learning
    from memecoin_experience.learning import train

    schema = _schema()
    plan = coordinate_samples.make_split_plan(
        schema["coordinate_domain_pin"],
        {
            "train": {
                "intervals": [[0, 10]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 1000,
            },
            "validation": {
                "intervals": [[10, 20]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 1000,
            },
            "test": {
                "intervals": [[20, 30]],
                "dependency_intervals": {},
                "knowledge_cutoff_ns": 1000,
            },
        },
    )
    store = tmp_path / "store"
    snapshot = build(
        store,
        [_row(c) for c in range(1, 29)],
        sources=[dict(kind="synthetic", pin="b" * 64)],
        cutoff=1000,
        schema=schema,
        chunk=4,
    )["snapshot"]
    config = dict(width=4, batch=4, epochs=2, seed=7, device="cpu", checkpoint_every=1)
    interrupted_path = tmp_path / "interrupted"
    if boundary == "last_batch":
        result = train(
            store,
            snapshot,
            interrupted_path,
            plan=plan,
            config=config,
            stop_after=3,
        )
        assert result["status"] == "interrupted"
    else:
        save = learning.save_checkpoint

        def fail_after_epoch_checkpoint(path, state):
            save(path, state)
            if state["epoch"] == 1 and state["cursor"] == (0, 0):
                raise RuntimeError("simulated_crash_after_epoch_checkpoint")

        monkeypatch.setattr(learning, "save_checkpoint", fail_after_epoch_checkpoint)
        with pytest.raises(RuntimeError, match="simulated_crash_after_epoch_checkpoint"):
            train(store, snapshot, interrupted_path, plan=plan, config=config)
        monkeypatch.setattr(learning, "save_checkpoint", save)

    resumed = train(store, snapshot, interrupted_path, plan=plan, config=config)
    fresh = train(store, snapshot, tmp_path / "fresh", plan=plan, config=config)
    assert resumed == fresh
    resumed_weights = torch.load(interrupted_path / "artifact/weights.pt", weights_only=True)
    fresh_weights = torch.load(tmp_path / "fresh/artifact/weights.pt", weights_only=True)
    assert all(torch.equal(resumed_weights[name], fresh_weights[name]) for name in fresh_weights)
