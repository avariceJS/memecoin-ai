from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from memecoin_ml.data import read, save_dataset, sha, synthetic, write
from memecoin_registry import registry as reg
from memecoin_registry.contracts import (
    RegistryError,
    inventory,
    protocol_for_calendar,
    validate_protocol,
)
from memecoin_registry.qualification import memberships

NS = 10**9
ANCHOR = 1767225600 * NS  # Synthetic 2026-01-01 UTC, never market evidence.
OFFSETS = (0, 21600, 43200, 86400, 129600, 172800)


def replace(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, allow_nan=False))


def reseal_window(window: Path) -> None:
    outcome = read(window.parent.parent / "outcomes" / (window.name + ".json"))
    replace(
        window / "seal.json", dict(outcome=outcome, files=inventory(window, exclude="seal.json"))
    )


def add_window(calendar: Path, index: int, *, fixture_offset: int | None = None) -> None:
    window = calendar / "windows" / f"{index:03d}"
    window.mkdir()
    due = ANCHOR + OFFSETS[index] * NS
    offset = due if fixture_offset is None else fixture_offset
    synthetic(window / "fixture-source")
    bundle = read(window / "fixture-source/dataset.json")
    for frame in bundle["frames"].values():
        frame["decision_ns"] += offset
        frame["available_ns"] += offset
        frame["group"] = "0:fixture_pool:0"
    for sample in bundle["samples"]:
        sample["group"] = "0:fixture_pool:0"
        sample["decision_ns"] += offset
        sample["maturity_ns"] += offset
        for target in sample["targets"]:
            target["available_ns"] += offset
    replace(window / "fixture-source/dataset.json", bundle)
    # Synthetic source is not a D44 export; fixture exporter below is the only mocked boundary.
    campaign = {key: "fixture" for key in reg.CAMPAIGN_FIELDS}
    campaign.update(pools=["fixture_pool"], schema_version=2, config_layout_version=2)
    (window / "campaign").mkdir()
    (window / "campaign-targets").mkdir()
    feature_root = window / "campaign/segments/000/corpus"
    feature_root.mkdir(parents=True)
    (feature_root / "features.jsonl").write_text(
        "".join(json.dumps(dict(usable=True, reasons=[])) + "\n" for _ in bundle["samples"])
    )
    write(window / "campaign/plan.json", campaign)
    write(
        window / "campaign-targets/spec.json",
        dict(
            input_indices=[0, 1],
            sizes_ppm=[1000],
            assumed_delays_ns=[0],
            holding_ns=2 * NS,
            execution_qualified=False,
        ),
    )
    write(
        window / "reservation.json",
        dict(index=index, due_ns=due, started_ns=due, plan_sha256=sha(calendar / "plan.json")),
    )
    outcome = dict(
        index=index,
        finished_ns=offset + 125 * NS,
        status="completed",
        corpus_exit_code=0,
        execution_or_collection_exit_code=0,
        qa_exit_code=0,
    )
    write(calendar / "outcomes" / f"{index:03d}.json", outcome)
    write(window / "seal.json", dict(outcome=outcome, files=inventory(window)))


@pytest.fixture
def calendar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "calendar"
    (root / "windows").mkdir(parents=True)
    (root / "outcomes").mkdir()
    write(root / "plan.json", dict(stage="D43", due_ns=[ANCHOR + t * NS for t in OFFSETS]))
    write(root / "plan-sha.json", sha(root / "plan.json"))
    add_window(root, 0)

    def exporter(window: Path, output: Path, **kwargs: Any) -> None:
        bundle = read(window / "fixture-source/dataset.json")
        bundle["source"] = dict(kind="sealed_D43_window", seal_sha256=sha(window / "seal.json"))
        bundle["history_policy"] = kwargs
        save_dataset(output, bundle)

    monkeypatch.setattr(reg, "export_window", exporter)
    return root


def init(tmp_path: Path, calendar: Path, *, now: int = ANCHOR + 200 * NS) -> Path:
    root = tmp_path / "registry"
    reg.initialize(root, calendar, now_ns=now)
    return root


def test_append_idempotent_membership_holdout_and_offline_audit(
    tmp_path: Path, calendar: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = init(tmp_path, calendar)
    first = reg.sync(root, now_ns=ANCHOR + 300 * NS)
    assert first["status"] == "published"
    old = Path(first["revision"])
    old_hashes = inventory(old)
    assert reg.sync(root, now_ns=ANCHOR + 400 * NS)["status"] == "no_change"
    assert len(list((root / "revisions").iterdir())) == 1
    old_folds = read(old / "folds.json")
    for i in (1, 2, 3):
        add_window(calendar, i)
        reg.sync(root, now_ns=ANCHOR + (OFFSETS[i] + 300) * NS)
    current = reg.verify_registry(root)
    assert current is not None and current["report"]["numeric_floors_passed"]
    assert current["report"]["research_training_ready"] is False
    assert current["report"]["development_windows"] == 4
    assert current["report"]["observed_development_span_union_ns"] < 4 * 120 * NS
    assert inventory(old) == old_hashes
    new_folds = read(Path(current["path"]) / "folds.json")
    assert old_folds["fold0"]["partitions"]["train"] == new_folds["fold0"]["partitions"]["train"]
    previous_decisions = current["report"]["development"]["decisions"]
    add_window(calendar, 4)

    def forbidden_export(*args: Any, **kwargs: Any) -> None:
        pytest.fail("holdout labels must not be exported or semantically evaluated")

    monkeypatch.setattr(reg, "export_window", forbidden_export)
    reg.sync(root, now_ns=ANCHOR + (OFFSETS[4] + 300) * NS)
    latest = reg.verify_registry(root)
    assert latest is not None
    assert latest["report"]["holdout"]["quarantined_windows"] == 1
    assert latest["report"]["development"]["decisions"] == previous_decisions
    assert len(list((root / "objects").iterdir())) == 4
    with pytest.raises(RegistryError, match="holdout_release"):
        reg.export_fold(root, tmp_path / "leak", revision=4, fold="fold0", partition="holdout")
    exported = reg.export_fold(
        root, tmp_path / "train", revision=4, fold="fold0", partition="train"
    )
    assert exported["decisions"] == 120
    with pytest.raises(RegistryError, match="outside_registry"):
        reg.export_fold(root, calendar / "bad", revision=4, fold="fold0", partition="train")
    calendar.rename(tmp_path / "offline-source")
    assert (
        reg.verify_registry(root) == latest
    )  # Audit uses pinned objects, no network/source access.


def test_pending_failed_and_missed_are_not_training_data(tmp_path: Path, calendar: Path) -> None:
    root = init(tmp_path, calendar)
    (calendar / "windows/001").mkdir()
    write(calendar / "outcomes/002.json", dict(index=2, status="missed"))
    write(calendar / "outcomes/003.json", dict(index=3, status="failed"))
    reg.sync(root, now_ns=ANCHOR + 100000 * NS)
    latest = reg.verify_registry(root)
    assert latest is not None
    assert latest["report"]["inventory_statuses"] == dict(
        development=1, awaiting_completion=1, excluded_missed=1, excluded_failed=1, pending=2
    )
    assert latest["report"]["development_windows"] == 1
    assert not latest["report"]["numeric_floors_passed"]


def test_crash_before_publish_preserves_revision_and_can_resume(
    tmp_path: Path, calendar: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = init(tmp_path, calendar)
    rename = reg.os.rename

    def crash(source: Path, destination: Path) -> None:
        if source.name == "revision":
            raise OSError("injected publish crash")
        rename(source, destination)

    with monkeypatch.context() as scoped:
        scoped.setattr(reg.os, "rename", crash)
        with pytest.raises(OSError):
            reg.sync(root, now_ns=ANCHOR + 300 * NS)
    assert reg.verify_registry(root) is None
    assert len(list((root / "objects").iterdir())) == 1
    assert len(list((root / "attempts").glob("*/failure.json"))) == 1
    assert reg.sync(root, now_ns=ANCHOR + 400 * NS)["status"] == "published"


@pytest.mark.parametrize("corruption", ["extra_file", "path_escape", "symlink", "changed_seal"])
def test_source_corruption_stops_without_mutating_old_revision(
    tmp_path: Path, calendar: Path, corruption: str
) -> None:
    root = init(tmp_path, calendar)
    reg.sync(root, now_ns=ANCHOR + 300 * NS)
    previous = inventory(root / "revisions")
    window = calendar / "windows/000"
    if corruption == "extra_file":
        (window / "unexpected").write_text("bytes")
    elif corruption == "symlink":
        (window / "link").symlink_to(window / "reservation.json")
    elif corruption == "path_escape":
        value = read(window / "seal.json")
        value["files"]["../outside"] = "bad"
        replace(window / "seal.json", value)
    else:
        (window / "extra").write_text("resealed")
        reseal_window(window)
    with pytest.raises(RegistryError):
        reg.sync(root, now_ns=ANCHOR + 400 * NS)
    assert inventory(root / "revisions") == previous
    assert list((root / "attempts").glob("*/failure.json"))


def test_changed_object_and_revision_are_detected(tmp_path: Path, calendar: Path) -> None:
    root = init(tmp_path, calendar)
    reg.sync(root, now_ns=ANCHOR + 300 * NS)
    obj = next((root / "objects").iterdir())
    with (obj / "dataset/dataset.json").open("a") as stream:
        stream.write(" ")
    with pytest.raises(RegistryError, match="inventory_mismatch"):
        reg.verify_registry(root)


def test_overlap_is_rejected_not_namespaced_away(tmp_path: Path, calendar: Path) -> None:
    import shutil

    shutil.rmtree(calendar / "windows/000")
    (calendar / "outcomes/000.json").unlink()
    add_window(calendar, 0, fixture_offset=ANCHOR + OFFSETS[1] * NS)
    add_window(calendar, 1)
    now = ANCHOR + (OFFSETS[1] + 300) * NS
    root = init(tmp_path, calendar, now=now)
    with pytest.raises(RegistryError, match="overlapping_decision"):
        reg.sync(root, now_ns=now + NS)
    assert reg.verify_registry(root) is None


def test_future_holdout_and_whole_scenario_purge(tmp_path: Path, calendar: Path) -> None:
    plan = read(calendar / "plan.json")
    with pytest.raises(RegistryError, match="holdout_must_be_future"):
        protocol_for_calendar(plan, "fixture", ANCHOR + OFFSETS[4] * NS)
    protocol = protocol_for_calendar(plan, "fixture", ANCHOR + 200 * NS)
    protocol["research_training_ready"] = True
    with pytest.raises(RegistryError, match="research_gate_closed"):
        validate_protocol(protocol)
    protocol["research_training_ready"] = False
    cutoff = protocol["folds"][0]["train_end_ns"]
    base = dict(
        id="siblings",
        decision_ns=cutoff - 5 * NS,
        maturity_ns=cutoff,
        targets=[dict(action=[0, 0.001, 0, 2]), dict(action=[1, 0.001, 0, 2])],
    )
    result = memberships(dict(samples=[base]), protocol)
    assert result["fold0"]["purged"]["siblings"] == "group_maturity_or_horizon_crosses_cutoff"
    late = copy.deepcopy(base)
    late.update(id="embargo", decision_ns=cutoff + 20 * NS, maturity_ns=cutoff + 22 * NS)
    result = memberships(dict(samples=[late]), protocol)
    assert result["fold0"]["purged"]["embargo"] == "embargo"
    # Even an early unfilled status may not bypass conservative nominal horizon purge.
    base["maturity_ns"] = cutoff - 3 * NS
    base["targets"][1]["action"][3] = 10
    assert "siblings" in memberships(dict(samples=[base]), protocol)["fold0"]["purged"]


def test_source_race_does_not_publish(
    tmp_path: Path, calendar: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = init(tmp_path, calendar)
    original = reg.export_window

    def race(window: Path, destination: Path, **kwargs: Any) -> None:
        original(window, destination, **kwargs)
        (window / "concurrent-change").write_text("race")

    monkeypatch.setattr(reg, "export_window", race)
    with pytest.raises(RegistryError, match="inventory_mismatch"):
        reg.sync(root, now_ns=ANCHOR + 300 * NS)
    assert reg.verify_registry(root) is None


def test_future_availability_and_semantic_drift_rejected(tmp_path: Path, calendar: Path) -> None:
    root = init(tmp_path, calendar)
    add_window(calendar, 1)
    spec = calendar / "windows/001/campaign-targets/spec.json"
    value = read(spec)
    value["holding_ns"] += NS
    replace(spec, value)
    reseal_window(calendar / "windows/001")
    with pytest.raises(RegistryError, match="semantics_changed"):
        reg.sync(root, now_ns=ANCHOR + (OFFSETS[1] + 300) * NS)
    assert reg.verify_registry(root) is None


def test_late_arrival_joins_fixed_fold_without_rewriting_past(
    tmp_path: Path, calendar: Path
) -> None:
    root = init(tmp_path, calendar)
    add_window(calendar, 2)
    reg.sync(root, now_ns=ANCHOR + (OFFSETS[2] + 200) * NS)
    first = reg.verify_registry(root)
    assert first is not None
    initial = inventory(Path(first["path"]))
    old_folds = read(Path(first["path"]) / "folds.json")
    add_window(calendar, 1)  # Late registry import, not fabricated late online receipt.
    reg.sync(root, now_ns=ANCHOR + (OFFSETS[2] + 400) * NS)
    final = reg.verify_registry(root)
    assert final is not None
    folds = read(Path(final["path"]) / "folds.json")
    assert old_folds["fold0"]["partitions"]["test"] == folds["fold0"]["partitions"]["test"]
    assert folds["fold0"]["partitions"]["validation"]
    assert inventory(Path(first["path"])) == initial


def test_source_availability_after_finish_is_rejected(tmp_path: Path, calendar: Path) -> None:
    root = init(tmp_path, calendar)
    add_window(calendar, 1)
    bundle_path = calendar / "windows/001/fixture-source/dataset.json"
    bundle = read(bundle_path)
    sample = bundle["samples"][0]
    sample["maturity_ns"] = ANCHOR + (OFFSETS[1] + 126) * NS
    sample["targets"][0]["available_ns"] = sample["maturity_ns"]
    replace(bundle_path, bundle)
    reseal_window(calendar / "windows/001")
    with pytest.raises(RegistryError, match="availability_outside_window"):
        reg.sync(root, now_ns=ANCHOR + (OFFSETS[1] + 300) * NS)
    assert reg.verify_registry(root) is None


def test_second_writer_and_clock_regression_fail_closed(tmp_path: Path, calendar: Path) -> None:
    import fcntl

    root = init(tmp_path, calendar)
    with (root / ".writer.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RegistryError, match="writer_busy"):
            reg.sync(root, now_ns=ANCHOR + 300 * NS)
    reg.sync(root, now_ns=ANCHOR + 400 * NS)
    with pytest.raises(RegistryError, match="clock_regression"):
        reg.sync(root, now_ns=ANCHOR + 300 * NS)


def test_window_crossing_holdout_is_quarantined_whole(
    tmp_path: Path, calendar: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = init(tmp_path, calendar)
    reg.sync(root, now_ns=ANCHOR + 300 * NS)
    add_window(calendar, 3)
    path = calendar / "outcomes/003.json"
    outcome = read(path)
    outcome["finished_ns"] = ANCHOR + OFFSETS[4] * NS
    replace(path, outcome)
    reseal_window(calendar / "windows/003")
    monkeypatch.setattr(
        reg, "export_window", lambda *a, **k: pytest.fail("crossing window exported")
    )
    reg.sync(root, now_ns=outcome["finished_ns"] + NS)
    latest = reg.verify_registry(root)
    assert latest is not None
    assert latest["report"]["development_windows"] == 1
    assert latest["report"]["holdout"]["quarantined_windows"] == 1


def test_revision_tamper_and_overwrite_export_fail(tmp_path: Path, calendar: Path) -> None:
    root = init(tmp_path, calendar)
    result = reg.sync(root, now_ns=ANCHOR + 300 * NS)
    destination = tmp_path / "export"
    reg.export_fold(root, destination, revision=0, fold="fold0", partition="train")
    with pytest.raises(FileExistsError):
        reg.export_fold(root, destination, revision=0, fold="fold0", partition="train")
    (Path(result["revision"]) / "qualification.json").write_text("{}")
    with pytest.raises(RegistryError, match="inventory_mismatch"):
        reg.verify_registry(root)
