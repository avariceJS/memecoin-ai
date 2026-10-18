"""Paged immutable partition catalogs; payload and catalog objects share hash identity rules."""

import bisect
import json
import os
import shutil
import tempfile
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

PAGE_SIZE = 1024


class Writer:
    def __init__(self, store):
        self.store = store
        self.buffer = []
        self.pages = []
        self.count = self.decisions = 0

    def append(self, entry):
        if len(self.buffer) == PAGE_SIZE:
            self.flush()
        self.buffer.append(entry)
        self.count += 1
        self.decisions += entry["count"]

    def flush(self):
        from .storage import atomic_json, object_path, seal_files, sync_dir, verify_files

        if not self.buffer:
            return
        staging = Path(tempfile.mkdtemp(prefix="catalog-", dir=self.store / "staging"))
        try:
            atomic_json(staging / "partition-page.json", self.buffer)
            pin = seal_files(staging)
            target = object_path(self.store, pin)
            target.parent.mkdir(parents=True, exist_ok=True)
            sync_dir(self.store / "objects")
            if target.exists():
                verify_files(target, pin)
            else:
                os.rename(staging, target)
                sync_dir(target.parent)
            self.pages.append(
                dict(
                    pin=pin, count=len(self.buffer), decisions=sum(p["count"] for p in self.buffer)
                )
            )
            self.buffer = []
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def finish(self):
        if not self.pages:
            return dict(partitions=self.buffer)
        self.flush()
        return dict(
            partition_pages=self.pages, partition_count=self.count, partition_index="paged-json-v1"
        )


class Parts(Sequence):
    """Only a few bounded catalog pages stay resident; indexes are at page granularity."""

    def __init__(self, store, manifest):
        from .storage import PIN

        self.store = store
        self.pages = manifest["partition_pages"]
        self.ends = []
        count = decisions = 0
        if manifest.get("partition_index") != "paged-json-v1" or not self.pages:
            raise ValueError("experience_catalog_schema")
        for page in self.pages:
            if (
                set(page) != {"pin", "count", "decisions"}
                or not isinstance(page["pin"], str)
                or not PIN.fullmatch(page["pin"])
                or type(page["count"]) is not int
                or not 1 <= page["count"] <= PAGE_SIZE
                or type(page["decisions"]) is not int
                or page["decisions"] < page["count"]
            ):
                raise ValueError("experience_catalog_page")
            count += page["count"]
            decisions += page["decisions"]
            self.ends.append(count)
        if count != manifest["partition_count"] or decisions != manifest["decisions"]:
            raise ValueError("experience_catalog_count")
        # Per-reader cache, so discarded snapshots do not remain globally referenced.
        self.page = lru_cache(maxsize=2)(self._page)

    def _page(self, index):
        from .storage import object_path, verify_files

        entry = self.pages[index]
        path = object_path(self.store, entry["pin"])
        verify_files(path, entry["pin"])
        rows = json.loads((path / "partition-page.json").read_text())
        if len(rows) != entry["count"] or sum(p["count"] for p in rows) != entry["decisions"]:
            raise ValueError("experience_catalog_content")
        return rows

    def __len__(self):
        return self.ends[-1]

    def __getitem__(self, index):
        if isinstance(index, slice):
            raise TypeError("experience_catalog_slice_use_streaming_iteration")
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        page = bisect.bisect_right(self.ends, index)
        offset = self.ends[page - 1] if page else 0
        return self.page(page)[index - offset]
