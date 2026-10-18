import shutil

import pytest
from test_experience import create
from test_experience_schema import custom_samples

from memecoin_experience import catalog
from memecoin_experience.composition import compose
from memecoin_experience.storage import Snapshot, build, object_path
from memecoin_experience.transfer import replicate


def test_catalog_pages_are_lazy_bounded_verified_and_portable(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "PAGE_SIZE", 4)
    store = tmp_path / "store"
    result = create(store, n=120, chunk=5)
    assert "partitions" not in result and len(result["partition_pages"]) > 2
    snapshot = Snapshot(store, result["snapshot"])
    assert snapshot.parts.page.cache_info().currsize == 0
    with pytest.raises(TypeError, match="streaming_iteration"):
        snapshot.parts[:]
    assert len(snapshot.parts) == result["partition_count"]
    assert sum(len(snapshot.partition(i)[1]) for i in range(len(snapshot.parts))) == 113
    assert snapshot.parts.page.cache_info().currsize == 2
    assert snapshot.parts[-1] == snapshot.parts[len(snapshot.parts) - 1]
    copied = replicate(store, tmp_path / "replica", result["snapshot"])
    assert copied["copied"] == len(snapshot.parts) + len(result["partition_pages"])
    assert replicate(store, tmp_path / "replica", result["snapshot"])["copied"] == 0
    page = object_path(store, result["partition_pages"][0]["pin"])
    (page / "partition-page.json").write_text("corrupt")
    with pytest.raises(ValueError, match="checksum"):
        Snapshot(store, result["snapshot"]).partition(0)
    assert Snapshot(tmp_path / "replica", result["snapshot"]).partition(0)[1]


def test_paged_composition_preserves_payloads_and_source_accounting(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "PAGE_SIZE", 4)
    schema, rows = custom_samples(60)
    first = build(
        tmp_path,
        rows[:30],
        schema=schema,
        sources=[dict(synthetic=True, range=1)],
        cutoff=10**18,
        chunk=3,
        source_counts=dict(source_rows=30),
    )
    second = build(
        tmp_path,
        rows[30:],
        schema=schema,
        sources=[dict(synthetic=True, range=2)],
        cutoff=10**18,
        chunk=3,
        source_counts=dict(source_rows=30),
    )
    result = compose(tmp_path, [first["snapshot"], second["snapshot"]])
    assert "partition_pages" in result
    assert result["source_counts"] is None
    assert result["source_accounting"]["parents"][first["snapshot"]] == dict(source_rows=30)
    snapshot = Snapshot(tmp_path, result["snapshot"])
    originals = {p["pin"] for s in (first, second) for p in Snapshot(tmp_path, s["snapshot"]).parts}
    assert {p["pin"] for p in snapshot.parts} == originals
    assert sum(len(snapshot.partition(i)[1]) for i in range(len(snapshot.parts))) == 60


def test_legacy_flat_object_location_remains_readable(tmp_path):
    result = create(tmp_path, n=30)
    pin = result["partitions"][0]["pin"]
    modern = object_path(tmp_path, pin)
    assert modern.parent.name == pin[:2]
    legacy = tmp_path / "objects" / pin
    shutil.move(modern, legacy)
    assert object_path(tmp_path, pin) == legacy
    Snapshot(tmp_path, result["snapshot"]).partition(0)


def test_training_uses_paged_metadata_and_exact_resume(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    from test_experience_learning import CONFIG, PLAN

    from memecoin_experience.learning import train

    monkeypatch.setattr(catalog, "PAGE_SIZE", 4)
    result = create(tmp_path / "store", n=310, chunk=10)
    first = train(
        tmp_path / "store", result["snapshot"], tmp_path / "first", plan=PLAN, config=CONFIG
    )
    train(
        tmp_path / "store",
        result["snapshot"],
        tmp_path / "resumed",
        plan=PLAN,
        config=CONFIG,
        stop_after=2,
    )
    assert (
        train(
            tmp_path / "store", result["snapshot"], tmp_path / "resumed", plan=PLAN, config=CONFIG
        )
        == first
    )


def test_role_reader_skips_payloads_outside_authenticated_bounds(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    from test_experience_learning import PLAN

    from memecoin_experience.learning import batches

    monkeypatch.setattr(catalog, "PAGE_SIZE", 4)
    result = create(tmp_path, n=310, chunk=10)
    snapshot = Snapshot(tmp_path, result["snapshot"])
    real = snapshot.partition
    touched = []

    def read(index):
        extent = snapshot.parts[index]["bounds"]
        assert extent["first_ns"] < PLAN["train_end_s"] * 10**9
        touched.append(index)
        return real(index)

    monkeypatch.setattr(snapshot, "partition", read)
    data = list(batches(snapshot, PLAN, "train", 64, seed=42))
    assert len({i for batch in data for i in batch[3]}) == 91
    assert 0 < len(touched) < len(snapshot.parts)
    remainder = list(batches(snapshot, PLAN, "train", 64, seed=42, cursor=data[0][4]))
    assert [i for batch in remainder for i in batch[3]] == [
        i for batch in data[1:] for i in batch[3]
    ]
