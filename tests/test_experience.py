import json
from collections import Counter

import pytest

from memecoin_brain.source import EXCLUDED, NS
from memecoin_experience.records import experiences, membership, split_plan
from memecoin_experience.sources import admitted
from memecoin_experience.storage import Snapshot, build, file_hash, lock


def bars(n=100, *, pool="synthetic", start=0):
    for i in range(n):
        t = start + i * 300
        close = 10 + (i % 19) / 10
        yield dict(
            pool=pool,
            open_s=t,
            available_ns=(t + 301) * NS,
            ohlcv=[close, close + 1, close - 1, close, 100 + i],
            capture_sha="a" * 64,
            lane="historical",
        )


def create(tmp_path, n=100, **kwargs):
    return build(
        tmp_path, bars(n), sources=[dict(synthetic=True)], cutoff=(n * 300 + 10000) * NS, **kwargs
    )


def test_transitive_and_partial():
    rows = list(bars(25))
    rows[2]["available_ns"] = 9000 * NS
    result = list(experiences(rows, 10000 * NS))
    full = next(r for r in result if r["origin_s"] == 4500)
    assert len(full["input_dependencies"]) == 15
    assert full["input_available_ns"] == 9000 * NS
    assert full["input_start_s"] == 0
    assert result[-2]["target_status"] == ["observed", "missing_future"]
    assert result[-1]["targets"] == [None, None]
    pending = list(experiences(bars(25), 7501 * NS))
    assert pending[-1]["target_status"] == ["pending", "pending"]


def test_gaps_duplicates_protected_cutoff():
    rows = list(bars(25))
    result = list(experiences([r for r in rows if r["open_s"] != 3000], 10000 * NS))
    before = next(r for r in result if r["origin_s"] == 3000)
    assert before["target_status"] == ["missing_future"] * 2
    assert not any(3000 < r["origin_s"] < 5700 for r in result)
    for data, cutoff in [
        (rows + rows, 10000 * NS),
        (bars(pool=next(iter(EXCLUDED))), 100000 * NS),
        (bars(), 1000 * NS),
    ]:
        with pytest.raises(ValueError):
            list(experiences(data, cutoff))


def test_purge_full_dependencies_and_each_horizon():
    plan = split_plan(
        dict(train_start_s=0, train_end_s=6000, validation_end_s=12000, test_end_s=18000)
    )
    rows = list(experiences(bars(65), 30000 * NS))
    assert membership(next(r for r in rows if r["origin_s"] == 5400), plan) == (
        "train",
        [True, False],
    )
    assert membership(next(r for r in rows if r["origin_s"] == 9000), plan)[0] == "purged"
    assert membership(next(r for r in rows if r["origin_s"] == 10800), plan)[0] == "validation"


def test_store_replay_and_corruption(tmp_path):
    result = create(tmp_path, chunk=17)
    assert result == create(tmp_path, chunk=17)
    snapshot = Snapshot(tmp_path, result["snapshot"])
    assert sum(len(snapshot.partition(i)[1]) for i in range(len(snapshot.parts))) == 93
    path, _ = snapshot.partition(0)
    (path / "x.f64").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        snapshot.partition(0)
    with pytest.raises(ValueError, match="checksum"):
        create(tmp_path, chunk=17)


def test_interrupted_build_reuses_prefix(tmp_path):
    def fail():
        yield from bars(70)
        raise RuntimeError("crash")

    with pytest.raises(RuntimeError):
        build(tmp_path, fail(), sources=[dict(synthetic=True)], cutoff=40000 * NS, chunk=17)
    objects = {p.name for p in (tmp_path / "objects").iterdir()}
    assert objects and not list((tmp_path / "snapshots").iterdir())
    done = build(tmp_path, bars(100), sources=[dict(synthetic=True)], cutoff=40000 * NS, chunk=17)
    assert objects <= {p.name for p in (tmp_path / "objects").iterdir()}
    assert done["decisions"] == 93


def test_lock_and_reserved_source_before_payload(tmp_path):
    with lock(tmp_path / "lock"), pytest.raises(BlockingIOError), lock(tmp_path / "lock"):
        pass
    (tmp_path / "admission.json").write_text(json.dumps(dict(role="descriptive_unseen_test")))
    with pytest.raises(ValueError, match="reserved"):
        list(admitted(dict(path=str(tmp_path), pin="a" * 64), 10000 * NS))


def test_synthetic_snapshot_not_market_evidence(tmp_path):
    result = create(tmp_path)
    assert result["production_eligible"] is False
    assert result["binding"]["contract"]["historical_online_availability_known"] is False
    statuses = Counter()
    snapshot = Snapshot(tmp_path, result["snapshot"])
    for i in range(len(snapshot.parts)):
        path, rows = snapshot.partition(i)
        assert file_hash(path / "manifest.json") == snapshot.parts[i]["pin"]
        for row in rows:
            statuses.update(row["target_status"])
    assert dict(statuses) == result["target_statuses"]


def test_append_shares_complete_days_and_rejects_symlink(tmp_path):
    first = create(tmp_path / "store", n=600)
    second = create(tmp_path / "store", n=900)
    # Last few labels in the last day may change; earlier completed day stays identical.
    assert first["partitions"][0] == second["partitions"][0]
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "store", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        Snapshot(alias, first["snapshot"])


def test_manifest_symlink_is_rejected(tmp_path):
    result = create(tmp_path / "store")
    snapshot = Snapshot(tmp_path / "store", result["snapshot"])
    path, _ = snapshot.partition(0)
    manifest = path / "manifest.json"
    moved = tmp_path / "moved.json"
    manifest.rename(moved)
    manifest.symlink_to(moved)
    with pytest.raises(ValueError, match="symlink"):
        snapshot.partition(0)
