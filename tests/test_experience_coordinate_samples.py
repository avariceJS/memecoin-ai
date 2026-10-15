import copy

import pytest

from memecoin_experience import coordinate_samples
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


def test_pit_requires_actual_decision_and_rejects_future_input_or_support():
    schema = _schema("point_in_time")
    missing_decision = _row(1, decision=None)
    with pytest.raises(ValueError, match="pit_decision"):
        list(checked_samples([missing_decision], schema, 1000))
    future = _row(1, decision=80)
    future["input_available_ns"] = 90
    future["input_dependencies"][0]["available_ns"] = 90
    with pytest.raises(ValueError, match="future_input"):
        list(checked_samples([future], schema, 1000))
    future = _row(1, decision=100)
    future["target_available_ns"] = [101]
    with pytest.raises(ValueError, match="future_target"):
        list(checked_samples([future], schema, 1000))


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
