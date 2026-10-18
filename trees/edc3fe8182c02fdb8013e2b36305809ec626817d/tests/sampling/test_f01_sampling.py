from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from conftest import ANCHOR, fixture_window

from memecoin_ml.data import read, sha
from memecoin_registry.contracts import inventory
from memecoin_sampling.contract import NS, freeze, load, make_binding, policy
from memecoin_sampling.dossier import export_dossier, reproduce_dossier
from memecoin_sampling.registry import ingest, verify_registry

SPEC = Path("configs/research-f01-v1.json")


def now(root: Path, index: int) -> int:
    return read(root / "contract/binding.json")["windows"][index]["due_ns"] + 600 * NS


def reseal(path: Path) -> None:
    value = read(path / "seal.json")
    value["files"] = inventory(path, exclude="seal.json")
    (path / "seal.json").write_text(json.dumps(value, sort_keys=True))


def test_fixed_schedule_future_guard_no_activation(tmp_path: Path) -> None:
    spec = read(SPEC)
    binding = make_binding(
        spec, anchor_ns=ANCHOR, created_ns=ANCHOR - 10**15, source_root=tmp_path / "future-source"
    )
    assert len(binding["windows"]) == 336
    assert sum(w["role"] == "development" for w in binding["windows"]) == 224
    assert policy()["total_max_rpc"] == 336 * 180
    assert not binding["collection_authorized"]
    assert not binding["research_launch_authorized"]
    assert all(w["due_ns"] // (6 * 3600 * NS) % 4 == w["stratum"] for w in binding["windows"])
    with pytest.raises(ValueError, match="anchor_overlaps"):
        make_binding(
            spec, anchor_ns=ANCHOR, created_ns=ANCHOR, source_root=tmp_path / "future-source"
        )
    with pytest.raises(ValueError, match="UTC_midnight"):
        make_binding(
            spec,
            anchor_ns=ANCHOR + 1,
            created_ns=ANCHOR - 1,
            source_root=tmp_path / "future-source",
        )
    freeze(
        spec,
        tmp_path / "plan",
        anchor_ns=ANCHOR,
        source_root=tmp_path / "future-source",
        now_ns=ANCHOR - 10**15,
    )
    with pytest.raises(FileExistsError):
        freeze(
            spec,
            tmp_path / "plan",
            anchor_ns=ANCHOR,
            source_root=tmp_path / "future-source",
            now_ns=ANCHOR - 10**15,
        )
    path = tmp_path / "plan/binding.json"
    value = read(path)
    value["policy"]["total_max_rpc"] += 1
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        load(tmp_path / "plan")


def test_append_out_of_order_immutable_and_offline(registry: Path, tmp_path: Path) -> None:
    a = fixture_window(registry, tmp_path / "source-a", 0)
    b = fixture_window(registry, tmp_path / "source-b", 4)
    result = ingest(registry, 4, source=b, now_ns=now(registry, 4))
    assert result["sequence"] == 0
    before = inventory(registry / "revisions/000000")
    ingest(registry, 0, source=a, now_ns=now(registry, 4) + 1)
    assert inventory(registry / "revisions/000000") == before
    assert ingest(registry, 0, source=a, now_ns=now(registry, 4) + 2)["status"] == "no_change"
    assert verify_registry(registry)["sequence"] == 1
    report = export_dossier(registry, tmp_path / "dossier", now_ns=now(registry, 4) + 3)
    assert not report["numeric_design_passed"] and not report["research_launch_authorized"]
    assert report["roles"]["fold0"]["train"]["coverage"]["planned_windows"] == 112
    assert report["roles"]["fold0"]["train"]["coverage"]["completed_fraction"] == 2 / 112
    a.rename(tmp_path / "offline-a")
    b.rename(tmp_path / "offline-b")
    assert reproduce_dossier(registry, tmp_path / "dossier")["exact"]
    # New terminal facts do not change the old revision/dossier.
    ingest(registry, 5, status="missed", now_ns=now(registry, 5) + 1000 * NS)
    assert reproduce_dossier(registry, tmp_path / "dossier")["registry_revision"] == 1


def test_holdout_denied_before_any_path_access(
    registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protected = Path("/never-open-protected-fixture")
    original = Path.open

    def checked(path, *args, **kwargs):
        if path == protected or protected in path.parents:
            pytest.fail("protected path opened")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", checked)
    for index in (224, 335, 336, -1, True):
        with pytest.raises(ValueError, match="protected_or_unknown"):
            ingest(registry, index, source=protected)
    assert not list((registry / "attempts").iterdir())


@pytest.mark.parametrize(
    "mutation",
    ["time", "reservation", "semantics", "budget", "quality", "grid", "future", "duplicate"],
)
def test_resealed_bad_source_rejected(registry: Path, tmp_path: Path, mutation: str) -> None:
    source = fixture_window(registry, tmp_path / "source", 0)
    if mutation in ("time", "reservation"):
        path = source / "reservation.json"
        value = read(path)
        value["started_ns" if mutation == "time" else "reserved_rpc"] += (
            901 * NS if mutation == "time" else 1
        )
    elif mutation in ("semantics", "budget"):
        path = source / "campaign/plan.json"
        value = read(path)
        value["source_alias" if mutation == "semantics" else "rounds"] = "changed"
    elif mutation == "quality":
        path = source / "campaign/segments/000/corpus/features.jsonl"
        path.write_text(path.read_text().splitlines()[0] + "\n")
        reseal(source)
        with pytest.raises(ValueError):
            ingest(registry, 0, source=source, now_ns=now(registry, 0))
        return
    else:
        path = source / "fixture-dataset/dataset.json"
        value = read(path)
        sample = value["samples"][0]
        if mutation == "grid":
            sample["targets"][0]["action"][1] = 0.0002
        elif mutation == "future":
            value["frames"][sample["history"][0]]["available_ns"] = sample["decision_ns"] + 1
        else:
            twin = deepcopy(sample)
            twin["id"] += "duplicate"
            value["samples"].append(twin)
        manifest = read(source / "fixture-dataset/manifest.json")
    path.write_text(json.dumps(value))
    if mutation in ("grid", "future", "duplicate"):
        manifest["dataset.json"] = sha(path)
        (source / "fixture-dataset/manifest.json").write_text(json.dumps(manifest))
    reseal(source)
    with pytest.raises(ValueError):
        ingest(registry, 0, source=source, now_ns=now(registry, 0))
    assert verify_registry(registry)["sequence"] == -1
    assert list((registry / "attempts").glob("*/failure.json"))


def test_failed_missed_denominators_and_terminal_conflict(registry: Path, tmp_path: Path) -> None:
    ingest(registry, 0, status="failed", now_ns=now(registry, 0) + 1000 * NS)
    ingest(registry, 1, status="missed", now_ns=now(registry, 1) + 1000 * NS)
    source = fixture_window(registry, tmp_path / "source", 0)
    with pytest.raises(ValueError, match="conflict"):
        ingest(registry, 0, source=source, now_ns=now(registry, 2))
    report = export_dossier(registry, tmp_path / "dossier", now_ns=now(registry, 2))
    coverage = report["roles"]["fold0"]["train"]["coverage"]
    assert coverage["states"]["failed"] == 1 and coverage["states"]["missed"] == 1
    assert (
        coverage["planned_windows"] == 112
        and coverage["other_source_start_upper_bound"] == 112 * 180
    )
    assert coverage["actual_usable_fraction"] is None
    assert not report["numeric_design_passed"]


def test_clock_lock_and_metadata_deadline(registry: Path) -> None:
    import fcntl

    with pytest.raises(ValueError, match="deadline"):
        ingest(registry, 0, status="missed", now_ns=now(registry, 0))
    ingest(registry, 0, status="missed", now_ns=now(registry, 0) + 1000 * NS)
    with pytest.raises(ValueError, match="regression"):
        ingest(registry, 1, status="failed", now_ns=now(registry, 0))
    with (registry / ".writer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            ingest(registry, 2, status="missed")


def test_partial_publish_recovery_retains_attempt(
    registry: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import memecoin_sampling.registry as module

    source = fixture_window(registry, tmp_path / "source", 0)
    original = module._publish

    def fail_revision(staged, final):
        if final.parent.name == "revisions":
            raise OSError("fixture interruption")
        original(staged, final)

    monkeypatch.setattr(module, "_publish", fail_revision)
    with pytest.raises(OSError):
        ingest(registry, 0, source=source, now_ns=now(registry, 0))
    before = inventory(registry / "attempts")
    monkeypatch.setattr(module, "_publish", original)
    assert ingest(registry, 0, source=source, now_ns=now(registry, 0) + 1)["sequence"] == 0
    after = inventory(registry / "attempts")
    assert all(after[k] == v for k, v in before.items())


def test_raw_tamper_and_output_overlap(registry: Path, tmp_path: Path) -> None:
    source = fixture_window(registry, tmp_path / "source", 0)
    ingest(registry, 0, source=source, now_ns=now(registry, 0))
    with pytest.raises(ValueError, match="disjoint"):
        export_dossier(registry, registry / "bad", now_ns=now(registry, 0))
    raw = next((registry / "objects").glob("*/raw/reservation.json"))
    raw.write_text("{}")
    with pytest.raises(ValueError):
        verify_registry(registry)


def test_mislabelled_path_and_symlink_never_read(
    registry: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protected = tmp_path / "unbound-protected"
    original = Path.open

    def guarded(path, *args, **kwargs):
        if path == protected or protected in path.parents:
            pytest.fail("unbound source must not be opened")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    with pytest.raises(ValueError, match="path_not_bound"):
        ingest(registry, 0, source=protected, now_ns=now(registry, 0))
    bound = Path(read(registry / "contract/binding.json")["source_root"]) / "windows/000"
    bound.parent.mkdir(parents=True)
    protected.mkdir()
    bound.symlink_to(protected, target_is_directory=True)
    with pytest.raises(ValueError, match="source_symlink"):
        ingest(registry, 0, source=bound, now_ns=now(registry, 0))


def test_rehashed_fake_completed_state_rejected(
    registry: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import memecoin_sampling.registry as module

    source = fixture_window(registry, tmp_path / "source", 0)
    monkeypatch.setattr(
        module,
        "verify_campaign",
        lambda path: dict(complete=False, successful_snapshots=179, reserved_requests=180),
    )
    with pytest.raises(ValueError, match="not_complete"):
        ingest(registry, 0, source=source, now_ns=now(registry, 0))


def test_synthetic_rows_never_become_prospective(registry: Path, tmp_path: Path) -> None:
    source = fixture_window(registry, tmp_path / "source", 0)
    ingest(registry, 0, source=source, now_ns=now(registry, 0))
    report = export_dossier(registry, tmp_path / "dossier", now_ns=now(registry, 0))
    assert report["numeric_checks"]["prospective_only"] is False
    assert report["research_launch_authorized"] is False


def test_schedule_denominator_does_not_shrink_before_or_after_missing(
    registry: Path, tmp_path: Path
) -> None:
    early = export_dossier(registry, tmp_path / "early", now_ns=now(registry, 0))
    ingest(registry, 0, status="missed", now_ns=now(registry, 0) + 1000 * NS)
    later = export_dossier(registry, tmp_path / "later", now_ns=now(registry, 0) + 1001 * NS)
    for phase in early["roles"]:
        for role in early["roles"][phase]:
            a = early["roles"][phase][role]["coverage"]
            b = later["roles"][phase][role]["coverage"]
            assert a["planned_windows"] == b["planned_windows"]
            assert a["completed_fraction"] == b["completed_fraction"] == 0


def test_failure_budget_flags_later_collection(registry: Path, tmp_path: Path) -> None:
    for index in range(8):
        ingest(registry, index, status="failed", now_ns=now(registry, index) + 1000 * NS)
    source = fixture_window(registry, tmp_path / "source", 8)
    ingest(registry, 8, source=source, now_ns=now(registry, 8))
    report = export_dossier(registry, tmp_path / "dossier", now_ns=now(registry, 8))
    assert not report["numeric_checks"]["no_collection_after_failure_stop"]
    assert not report["research_launch_authorized"]
