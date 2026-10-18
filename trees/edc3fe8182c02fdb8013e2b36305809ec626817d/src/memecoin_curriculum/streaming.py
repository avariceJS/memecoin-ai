"""Bounded-memory bar shards and lazy indexed access. No shared mutable DB writers."""

import bisect
import itertools
from collections import OrderedDict

from memecoin_brain.data import CONTEXT, build, load_dataset, save_dataset
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import seal, verify
from memecoin_runner.ledger import publish


def stream_shards(rows, provenance, chunk=4096):
    if not 1 <= chunk <= 4096:
        raise ValueError("shard_chunk_bound")
    previous = None

    def checked():
        nonlocal previous
        for row in rows:
            key = (row["pool"], row["open_s"])
            if previous is not None and key <= previous:
                raise ValueError("shard_input_order_or_duplicate")
            previous = key
            yield row

    for _pool, iterator in itertools.groupby(checked(), key=lambda r: r["pool"]):
        tail, pending = [], []
        while True:
            pending.extend(itertools.islice(iterator, chunk + 3 - len(pending)))  # noqa: B031
            if not pending:
                break
            origins, future = pending[:chunk], pending[chunk:]
            part = build(tail + origins + future, provenance)
            ids = {f"{r['pool']}:{r['open_s']}" for r in origins}
            part["samples"] = [s for s in part["samples"] if s["id"] in ids]
            keys = {k for s in part["samples"] for k in s["history"]}
            part["frames"] = {k: v for k, v in part["frames"].items() if k in keys}
            if part["samples"]:
                yield part
            tail = (tail + origins)[-2 * (CONTEXT - 1) :]
            pending = future


def write_archive(rows, provenance, output, chunk=4096):
    output.mkdir(parents=True, exist_ok=False)
    index, count = [], 0
    for i, bundle in enumerate(stream_shards(rows, provenance, chunk)):
        name = f"shard-{i:06d}"
        save_dataset(output / name, bundle)
        index.append(
            dict(
                name=name,
                start=count,
                count=len(bundle["samples"]),
                manifest_sha=sha(output / name / "manifest.json"),
            )
        )
        count += len(bundle["samples"])
    if not count:
        raise ValueError("empty_streaming_archive")
    publish(
        output / "catalogue.json",
        dict(
            schema="brain-shards-v1",
            samples=count,
            provenance=provenance,
            shards=index,
            chunk=chunk,
        ),
    )
    seal(output)
    return read(output / "catalogue.json")


class ShardDataset:
    """Bounded LRU; metadata loaded once, payload verified on each cache miss."""

    def __init__(self, root, cache_size=2):
        if type(cache_size) is not int or not 1 <= cache_size <= 16:
            raise ValueError("shard_cache_bound")
        # Validate manifest membership once; subsequent loads verify shard byte pins.
        verify(root)
        self.root = root
        self.catalogue = read(root / "catalogue.json")
        if self.catalogue["schema"] != "brain-shards-v1":
            raise ValueError("shard_schema")
        self.index = self.catalogue["shards"]
        count = 0
        for number, entry in enumerate(self.index):
            if entry["start"] != count or entry["count"] <= 0:
                raise ValueError("shard_index_gap")
            if entry["name"] != f"shard-{number:06d}":
                raise ValueError("shard_index_name")
            count += entry["count"]
        if count != self.catalogue["samples"]:
            raise ValueError("shard_count")
        self.starts = [e["start"] for e in self.index]
        self.cache_size, self.cache = cache_size, OrderedDict()

    def __len__(self):
        return self.catalogue["samples"]

    def get(self, index):
        if type(index) is not int or not 0 <= index < len(self):
            raise IndexError("shard_sample_index")
        shard = bisect.bisect_right(self.starts, index) - 1
        entry = self.index[shard]
        if shard not in self.cache:
            path = self.root / entry["name"]
            if sha(path / "manifest.json") != entry["manifest_sha"]:
                raise ValueError("shard_manifest_changed")
            self.cache[shard] = load_dataset(path)
            while len(self.cache) > self.cache_size:
                self.cache.popitem(last=False)
        self.cache.move_to_end(shard)
        bundle = self.cache[shard]
        return bundle, bundle["samples"][index - entry["start"]]

    def tensor_batch(self, indices, fitted):
        from memecoin_brain.model import tensors

        frames, samples = {}, []
        for index in indices:
            bundle, sample = self.get(int(index))
            samples.append(sample)
            for key in sample["history"]:
                value = bundle["frames"][key]
                if key in frames and frames[key] != value:
                    raise ValueError("shard_frame_revision")
                frames[key] = value
        return tensors(dict(frames=frames), samples, fitted)


def tensor_cache(dataset, fitted, output, batch=256):
    """Materialize bounded batches once; random training draws then avoid JSON shard I/O."""
    import numpy as np

    if type(batch) is not int or not 1 <= batch <= 4096:
        raise ValueError("tensor_cache_batch_bound")
    output.mkdir(parents=True, exist_ok=False)
    shapes = None
    handles = [(output / f"{name}.f32").open("xb") for name in ("x", "actions", "y")]
    try:
        for start in range(0, len(dataset), batch):
            ids = list(range(start, min(start + batch, len(dataset))))
            tensors = dataset.tensor_batch(ids, fitted)
            arrays = [t.numpy().astype(np.float32, copy=False) for t in tensors]
            # Fixed B13 feature contract has two target horizons per decision.
            if any(len(a) != len(ids) * 2 for a in arrays):
                raise ValueError("tensor_cache_horizon_contract")
            current = [[len(dataset), 2, *a.shape[1:]] for a in arrays]
            if shapes is not None and shapes != current:
                raise ValueError("tensor_cache_shape_changed")
            shapes = current
            for handle, array in zip(handles, arrays, strict=True):
                handle.write(array.tobytes())
    finally:
        for handle in handles:
            handle.close()
    publish(
        output / "cache.json",
        dict(
            schema="brain-tensor-cache-v1",
            source_sha=sha(dataset.root / "manifest.json"),
            shapes=shapes,
            fitted=fitted,
            byte_order="little",
            dtype="float32",
            training_membership_not_selected=True,
        ),
    )
    seal(output)


class TensorCache:
    """Read-only CPU memory maps. Caller must select preregistered train membership."""

    def __init__(self, root):
        import numpy as np

        verify(root)
        meta = read(root / "cache.json")
        if meta["schema"] != "brain-tensor-cache-v1" or meta["byte_order"] != "little":
            raise ValueError("tensor_cache_schema")
        self.count = meta["shapes"][0][0]
        self.arrays = [
            np.memmap(root / f"{name}.f32", dtype="<f4", mode="r", shape=tuple(shape))
            for name, shape in zip(("x", "actions", "y"), meta["shapes"], strict=True)
        ]

    def batch(self, indices):
        import numpy as np
        import torch

        if any(type(i) is not int or not 0 <= i < self.count for i in indices):
            raise IndexError("tensor_cache_index")
        if not indices:
            raise ValueError("empty_tensor_batch")
        return tuple(
            torch.from_numpy(np.array(a[indices], copy=True).reshape(-1, *a.shape[2:]))
            for a in self.arrays
        )
