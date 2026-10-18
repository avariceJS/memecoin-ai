# ruff: noqa: E402
import pytest

torch = pytest.importorskip("torch")

from memecoin_brain.data import build, scaler
from memecoin_brain.model import tensors
from memecoin_curriculum.streaming import ShardDataset, write_archive


def test_lazy_batches_match_eager_tensors_with_repeated_cross_shard_draws(tmp_path):
    rows = [
        dict(
            pool=p,
            open_s=t * 300,
            available_ns=(t + 1) * 300 * 10**9,
            ohlcv=[1, 1.1, 0.9, 1 + t / 1000, 100 + t],
            capture_sha="a" * 64,
        )
        for p in ("a", "b")
        for t in range(40)
    ]
    prov = dict(as_of_ns=10**18)
    bundle = build(rows, prov)
    fitted = scaler(bundle, bundle["samples"])
    write_archive(iter(rows), prov, tmp_path / "archive", chunk=9)
    lazy = ShardDataset(tmp_path / "archive", cache_size=1)
    ids = [0, 35, 1, 34, 0, len(lazy) - 1]
    expected = tensors(bundle, [bundle["samples"][i] for i in ids], fitted)
    actual = lazy.tensor_batch(ids, fitted)
    for a, b in zip(actual, expected, strict=True):
        assert torch.allclose(a, b, equal_nan=True, rtol=0, atol=0)


def test_memmap_random_draws_equal_lazy_and_immutable(tmp_path):
    from memecoin_curriculum.streaming import TensorCache, tensor_cache

    rows = [
        dict(
            pool="a",
            open_s=t * 300,
            available_ns=(t + 1) * 300 * 10**9,
            ohlcv=[1, 1.1, 0.9, 1 + t / 1000, 100 + t],
            capture_sha="a" * 64,
        )
        for t in range(40)
    ]
    prov = dict(as_of_ns=10**18)
    bundle = build(rows, prov)
    fitted = scaler(bundle, bundle["samples"])
    write_archive(iter(rows), prov, tmp_path / "archive", chunk=8)
    lazy = ShardDataset(tmp_path / "archive", cache_size=1)
    tensor_cache(lazy, fitted, tmp_path / "cache", batch=7)
    cache = TensorCache(tmp_path / "cache")
    indices = [0, 20, 0, 10, len(lazy) - 1]
    for a, b in zip(cache.batch(indices), lazy.tensor_batch(indices, fitted), strict=True):
        assert torch.allclose(a, b, equal_nan=True, rtol=0, atol=0)
    with pytest.raises(IndexError):
        cache.batch([-1])
