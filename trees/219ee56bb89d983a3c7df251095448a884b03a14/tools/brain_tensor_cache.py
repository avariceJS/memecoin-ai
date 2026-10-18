"""Create and measure a pinned scaler's read-only tensor cache; no fits or membership changes."""

import argparse
import resource
import time
from pathlib import Path

import torch

from memecoin_curriculum.streaming import ShardDataset, TensorCache, tensor_cache
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import verify
from memecoin_runner.ledger import publish

p = argparse.ArgumentParser()
p.add_argument("shards", type=Path)
p.add_argument("fit", type=Path)
p.add_argument("output", type=Path)
a = p.parse_args()
verify(a.fit.parent)
fitted = read(a.fit)["scaler"]
start = time.monotonic()
lazy = ShardDataset(a.shards)
tensor_cache(lazy, fitted, a.output)
created = time.monotonic() - start
cache = TensorCache(a.output)
generator = torch.Generator().manual_seed(7)
draws = torch.randint(cache.count, (1000, 64), generator=generator).tolist()
start = time.monotonic()
for ids in draws:
    cache.batch(ids)
publish(
    a.output.parent / (a.output.name + "-measurement.json"),
    dict(
        create_s=created,
        batches=1000,
        batch_decisions=64,
        load_s=time.monotonic() - start,
        decisions=cache.count,
        peak_rss_bytes_macos=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        scaler_source=str(a.fit.resolve()),
        scaler_source_sha=sha(a.fit),
        cache_sha=sha(a.output / "manifest.json"),
        fits=0,
        training_membership_selected=False,
        device="CPU M4 local",
    ),
)
print(a.output)
