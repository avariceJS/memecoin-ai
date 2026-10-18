import json
import platform

import pytest

np = pytest.importorskip("numpy")
torch = pytest.importorskip("torch")

from test_experience_coordinate_samples import _row, _schema  # noqa: E402

from memecoin_experience import coordinate_samples, evaluation, predict  # noqa: E402
from memecoin_experience.learning import batches  # noqa: E402
from memecoin_experience.records import split_plan  # noqa: E402
from memecoin_experience.storage import (  # noqa: E402
    Snapshot,
    atomic_json,
    build,
    digest,
    file_hash,
)


@pytest.fixture
def coordinate_legacy_transcript_case(tmp_path):
    schema = _schema()

    def role(intervals):
        return {
            "intervals": intervals,
            "dependency_intervals": {},
            "knowledge_cutoff_ns": 1000,
        }

    evaluation_plan = split_plan(
        coordinate_samples.make_split_plan(
            schema["coordinate_domain_pin"],
            {
                "train": role([[0, 4]]),
                "validation": role([[4, 8]]),
                "test": role([[8, 13]]),
            },
        )
    )
    model_plan = split_plan(
        coordinate_samples.make_split_plan(
            schema["coordinate_domain_pin"],
            {
                "train": role([[0, 2]]),
                "validation": role([[2, 4]]),
                "test": role([[4, 8]]),
            },
        )
    )
    store = tmp_path / "store"
    snapshot_pin = build(
        store,
        [_row(coordinate) for coordinate in range(8, 12)],
        sources=[dict(kind="synthetic", pin="e" * 64)],
        cutoff=1000,
        schema=schema,
        role="research_evaluation",
        chunk=2,
    )["snapshot"]
    snapshot = Snapshot(store, snapshot_pin)
    batch_size = 2
    records = []
    for _, _, masks, metadata, _ in batches(
        snapshot, evaluation_plan, "test", batch_size, with_metadata=True
    ):
        for index, (row, mask) in enumerate(zip(metadata, masks, strict=True)):
            records.append(
                dict(
                    id=row["id"],
                    entity=row["pool"],
                    origin=row["origin"],
                    decision_ns=row["decision_ns"],
                    input_available_ns=row["input_available_ns"],
                    target_status=row["target_status"],
                    target_available_ns=row["target_available_ns"],
                    target_mask=[bool(value) for value in mask],
                    prediction=[[float(index), float(index), float(index + 1)]],
                )
            )
    path = tmp_path / "predictions.jsonl"
    write_records(path, records)
    return dict(
        schema=schema,
        snapshot=snapshot,
        evaluation_plan=evaluation_plan,
        model_plan=model_plan,
        batch_size=batch_size,
        records=records,
        path=path,
        store=store,
        snapshot_pin=snapshot_pin,
    )


def write_records(path, records):
    path.write_text("".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records))


def validate(case, path):
    _validate = evaluation._validate_legacy_predictions
    _validate(
        path,
        case["snapshot"],
        case["evaluation_plan"],
        case["batch_size"],
        target_count=1,
        quantiles=[0.1, 0.5, 0.9],
    )


def test_legacy_coordinate_transcript_matches_snapshot_in_order(coordinate_legacy_transcript_case):
    validate(coordinate_legacy_transcript_case, coordinate_legacy_transcript_case["path"])


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_legacy_coordinate_transcript_requires_exact_row_count(
    coordinate_legacy_transcript_case, mutation
):
    case = coordinate_legacy_transcript_case
    changed = list(case["records"])
    if mutation == "missing":
        changed.pop()
    else:
        changed.append(dict(changed[-1]))
    path = case["path"].with_name(f"{mutation}.jsonl")
    write_records(path, changed)
    with pytest.raises(ValueError, match="experience_evaluation_transcript"):
        validate(case, path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "substituted-id"),
        ("entity", "substituted-entity"),
        ("origin", {"coordinate": -1}),
        ("decision_ns", 1),
        ("input_available_ns", 1),
        ("target_status", ["pending"]),
        ("target_available_ns", [1]),
        ("target_mask", [False]),
    ],
)
def test_legacy_coordinate_transcript_rejects_snapshot_metadata_changes(
    coordinate_legacy_transcript_case, field, value
):
    case = coordinate_legacy_transcript_case
    changed = [dict(record) for record in case["records"]]
    changed[0][field] = value
    path = case["path"].with_name(f"changed-{field}.jsonl")
    write_records(path, changed)
    with pytest.raises(ValueError, match="experience_evaluation_transcript"):
        validate(case, path)


@pytest.mark.parametrize("field", ["target_status", "target_available_ns"])
def test_legacy_coordinate_transcript_rejects_partial_metadata_schema(
    coordinate_legacy_transcript_case, field
):
    case = coordinate_legacy_transcript_case
    changed = [dict(record) for record in case["records"]]
    del changed[0][field]
    path = case["path"].with_name(f"missing-{field}.jsonl")
    write_records(path, changed)
    with pytest.raises(ValueError, match="experience_evaluation_transcript"):
        validate(case, path)


def test_legacy_coordinate_transcript_rejects_unknown_fields(coordinate_legacy_transcript_case):
    case = coordinate_legacy_transcript_case
    changed = [dict(record) for record in case["records"]]
    changed[0]["unbound_metadata"] = "unexpected"
    path = case["path"].with_name("unknown-field.jsonl")
    write_records(path, changed)
    with pytest.raises(ValueError, match="experience_evaluation_transcript"):
        validate(case, path)


@pytest.mark.parametrize(
    "prediction",
    [
        [[0.0, float("nan"), 1.0]],
        [[2.0, 1.0, 3.0]],
        [[0.0, 1.0]],
        [],
    ],
)
def test_legacy_coordinate_transcript_rejects_invalid_prediction_structure(
    coordinate_legacy_transcript_case, prediction
):
    case = coordinate_legacy_transcript_case
    changed = [dict(record) for record in case["records"]]
    changed[0]["prediction"] = prediction
    path = case["path"].with_name("invalid-prediction.jsonl")
    write_records(path, changed)
    with pytest.raises(ValueError, match="experience_evaluation_transcript"):
        validate(case, path)


def test_legacy_coordinate_transcript_rejects_duplicate_json_keys(
    coordinate_legacy_transcript_case,
):
    case = coordinate_legacy_transcript_case
    encoded = json.dumps(case["records"][0], separators=(",", ":"))
    line = encoded.replace('"id":', '"id":"shadow","id":', 1)
    path = case["path"].with_name("duplicate-key.jsonl")
    path.write_text(line + "\n" + case["path"].read_text().split("\n", 1)[1])
    with pytest.raises(ValueError, match="experience_evaluation_transcript"):
        validate(case, path)


def test_legacy_coordinate_transcript_bounds_each_line(coordinate_legacy_transcript_case):
    case = coordinate_legacy_transcript_case
    lines = case["path"].read_bytes().splitlines()
    lines[0] += b" " * 4096
    path = case["path"].with_name("oversized-line.jsonl")
    path.write_bytes(b"\n".join(lines) + b"\n")
    with pytest.raises(ValueError, match="experience_evaluation_transcript"):
        validate(case, path)


def test_legacy_coordinate_transcript_fails_closed_when_missing(coordinate_legacy_transcript_case):
    case = coordinate_legacy_transcript_case
    with pytest.raises(ValueError, match="experience_evaluation_transcript"):
        validate(case, case["path"].with_name("missing.jsonl"))


@pytest.mark.parametrize(
    "metrics", [evaluation.COORDINATE_METRICS_V1, evaluation.COORDINATE_METRICS_V2]
)
def test_coordinate_v1_v2_score_reuse_checks_resigned_transcript_without_predicting(
    coordinate_legacy_transcript_case, tmp_path, monkeypatch, metrics
):
    case = coordinate_legacy_transcript_case
    candidate = "a" * 64
    frontier = {
        "version": "brain-native-coordinate-frontier-v1",
        "coordinate_domain_pins": case["model_plan"]["coordinate_domain_pins"],
        "exclusive_ends": {
            case["schema"]["coordinate_domain_pin"]: 8,
        },
    }
    protocol = dict(
        version="brain-native-coordinate-evaluation-v1",
        candidate=candidate,
        schema=case["schema"],
        coordinate_domain_pin=case["schema"]["coordinate_domain_pin"],
        seen_coordinate_frontier=frontier,
        split_plan=case["evaluation_plan"],
        pools=["solana/program/pumpswap"],
        scope="retrospective",
        registered_ns=1,
        metrics=metrics,
        quantiles=[0.1, 0.5, 0.9],
        data_role="research_evaluation",
        production_eligible=False,
    )
    protocol_pin = digest(protocol)
    protocol_root = tmp_path / "protocols"
    protocol_root.mkdir()
    atomic_json(protocol_root / f"{protocol_pin}.json", protocol)

    class NoInferencePredictor:
        def __init__(self, artifact, pin):
            assert pin == candidate
            self.report = {
                "binding": {
                    "plan": case["model_plan"],
                    "parent": None,
                    "initialization": "seeded_fresh",
                },
                "ancestors": [],
                "spec": None,
            }
            self.spec = {"schema": case["schema"], "quantiles": [0.1, 0.5, 0.9]}

        def predict(self, _frames):
            raise AssertionError("score reuse must not rerun prediction")

    monkeypatch.setattr(predict, "Predictor", NoInferencePredictor)
    monkeypatch.setattr(evaluation, "_validate_coordinate_report", lambda *args: None)
    output = tmp_path / "sealed-coordinate-score"
    output.mkdir()
    binding = dict(
        environment=dict(
            torch=str(torch.__version__),
            numpy=np.__version__,
            python=platform.python_version(),
            device="cpu",
        ),
        protocol=protocol_pin,
        snapshot=case["snapshot_pin"],
        candidate=candidate,
        schema=protocol["schema"],
        split_plan=protocol["split_plan"],
        code=evaluation.code_identity(),
        batch=case["batch_size"],
    )
    atomic_json(output / "protocol.json", protocol)
    atomic_json(output / "report.json", {"binding": binding})
    (output / "predictions.jsonl").write_bytes(case["path"].read_bytes())
    (output / "strata.jsonl").write_bytes(b"")
    inventory = {
        path.name: file_hash(path)
        for path in sorted(output.iterdir())
        if path.name != "manifest.json"
    }
    atomic_json(output / "manifest.json", inventory)

    result = evaluation.score(
        protocol_root,
        protocol_pin,
        case["store"],
        case["snapshot_pin"],
        tmp_path / "unused-artifact",
        output,
        batch=case["batch_size"],
    )
    assert result["binding"] == binding

    changed = [dict(record) for record in case["records"]]
    changed[0]["entity"] = "tampered-but-resigned"
    (output / "predictions.jsonl").write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in changed)
    )
    inventory = {
        path.name: file_hash(path)
        for path in sorted(output.iterdir())
        if path.name != "manifest.json"
    }
    atomic_json(output / "manifest.json", inventory, replace=True)
    with pytest.raises(ValueError, match="experience_evaluation_transcript"):
        evaluation.score(
            protocol_root,
            protocol_pin,
            case["store"],
            case["snapshot_pin"],
            tmp_path / "unused-artifact",
            output,
            batch=case["batch_size"],
        )
