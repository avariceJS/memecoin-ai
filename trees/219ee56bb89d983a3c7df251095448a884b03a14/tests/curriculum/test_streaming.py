import pytest

from memecoin_brain.data import build
from memecoin_curriculum.streaming import ShardDataset, stream_shards, write_archive


def rows():
    return [
        dict(
            pool=p,
            open_s=t * 300,
            available_ns=(t + 1) * 300 * 10**9,
            ohlcv=[1, 1.1, 0.9, 1 + t / 1000, 100 + t],
            capture_sha="a" * 64,
        )
        for p in ["a", "b"]
        for t in range(60)
        if t not in [20, 21]
    ]


def test_streaming_exact_history_and_targets_across_shards_and_gaps(tmp_path):
    data = rows()
    prov = dict(as_of_ns=100 * 300 * 10**9)
    expected = build(data, prov)
    parts = list(stream_shards(iter(data), prov, chunk=9))
    assert [s for p in parts for s in p["samples"]] == expected["samples"]
    for p in parts:
        for k, v in p["frames"].items():
            assert v == expected["frames"][k]
    write_archive(iter(data), prov, tmp_path / "archive", chunk=9)
    lazy = ShardDataset(tmp_path / "archive", cache_size=1)
    for index in reversed(range(len(lazy))):
        _, s = lazy.get(index)
        assert s == expected["samples"][index]
        assert len(lazy.cache) <= 1
    with pytest.raises(IndexError):
        lazy.get(-1)


def test_unsorted_or_duplicate_input_is_rejected():
    data = rows()
    data.insert(1, data[0])
    with pytest.raises(ValueError, match="order_or_duplicate"):
        list(stream_shards(iter(data), dict(as_of_ns=10**18), chunk=8))


def test_corrupt_shard_after_reader_creation_fails(tmp_path):
    write_archive(iter(rows()), dict(as_of_ns=10**18), tmp_path / "a", chunk=8)
    lazy = ShardDataset(tmp_path / "a")
    (tmp_path / "a/shard-000000/dataset.json").write_text("{}")
    with pytest.raises(ValueError):
        lazy.get(0)
