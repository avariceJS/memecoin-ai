"""Immutable binary columns, content-addressed objects and atomic snapshot publication."""

import fcntl
import hashlib
import itertools
import json
import os
import re
import shutil
import sqlite3
import struct
import tempfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from .records import contract, experiences
from .schema import checked_samples, feature_names, target_names, validate

PIN = re.compile(r"[0-9a-f]{64}\Z")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_path(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("experience_symlink")
    return path


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value, *, replace=False):
    path = safe_path(path)
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != canonical(value):
                    raise ValueError("experience_immutable_conflict") from None
        sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def lock(path):
    path = safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def seal_files(root):
    inventory = {
        p.name: file_hash(safe_path(p)) for p in sorted(root.iterdir()) if p.name != "manifest.json"
    }
    atomic_json(root / "manifest.json", inventory)
    return file_hash(root / "manifest.json")


def read_manifest(root, pin=None):
    root = safe_path(root)
    manifest = safe_path(root / "manifest.json")
    raw = manifest.read_bytes()
    if pin is not None and hashlib.sha256(raw).hexdigest() != pin:
        raise ValueError("experience_manifest_pin")
    return json.loads(raw)


def verify_files(root, pin=None):
    root = safe_path(root)
    expected = read_manifest(root, pin)
    if set(expected) != {p.name for p in root.iterdir() if p.name != "manifest.json"}:
        raise ValueError("experience_inventory")
    for name, checksum in expected.items():
        if Path(name).name != name or file_hash(safe_path(root / name)) != checksum:
            raise ValueError("experience_checksum")
    return expected


def object_path(store, pin):
    if not isinstance(pin, str) or not PIN.fullmatch(pin):
        raise ValueError("experience_object_pin")
    legacy = safe_path(Path(store) / "objects" / pin)
    if legacy.exists():
        return legacy
    return safe_path(Path(store) / "objects" / pin[:2] / pin)


def write_partition(store, rows, schema):
    staging = Path(tempfile.mkdtemp(prefix="partition-", dir=store / "staging"))
    try:
        counts = Counter()
        with (
            (staging / "x.f64").open("wb") as x,
            (staging / "y.f64").open("wb") as y,
            (staging / "rows.jsonl").open("wb") as meta,
        ):
            for row in rows:
                values = [float("nan") if v is None else v for f in row["values"] for v in f]
                labels = [float("nan") if v is None else v for v in row["targets"]]
                x.write(struct.pack(f"<{len(values)}d", *values))
                y.write(struct.pack(f"<{len(labels)}d", *labels))
                meta.write(
                    canonical({k: v for k, v in row.items() if k not in {"values", "targets"}})
                )
                meta.write(b"\n")
                counts.update(row["target_status"])
            for stream in (x, y, meta):
                stream.flush()
                os.fsync(stream.fileno())
        atomic_json(
            staging / "partition.json",
            dict(contract=schema, count=len(rows), first=rows[0]["id"], last=rows[-1]["id"]),
        )
        pin = seal_files(staging)
        target = object_path(store, pin)
        target.parent.mkdir(parents=True, exist_ok=True)
        sync_dir(store / "objects")
        # Caller holds the store publication lock. A directory rename publishes all columns.
        if target.exists():
            verify_files(target, pin)
        else:
            os.rename(staging, target)
            sync_dir(target.parent)
        from .composition import bounds

        return dict(pin=pin, count=len(rows), statuses=dict(counts), bounds=bounds(rows, schema))
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def code_identity():
    from memecoin_brain import data, source
    from memecoin_curriculum import admission
    from memecoin_ml import data as ml_data
    from memecoin_registry import contracts

    files = [
        *Path(__file__).parent.glob("*.py"),
        *(Path(__file__).parent.parent / "memecoin_population").glob("*.py"),
        *(Path(m.__file__) for m in (data, source, admission, contracts, ml_data)),
    ]
    return {f"{p.parent.name}/{p.name}": file_hash(p) for p in sorted(files)}


def progress_part(progress, index):
    group = safe_path(progress / "parts" / f"{index // 1024:09d}")
    if not group.exists():
        group.mkdir(parents=True, exist_ok=True)
        sync_dir(group.parent)
        sync_dir(progress)
    return group / f"{index:09d}.json"


def logical_sources(references):
    """Locations resolve immutable inputs; moving bytes must not change dataset identity."""
    result = []
    for reference in references:
        if "path" in reference and (
            not isinstance(reference.get("pin"), str) or not PIN.fullmatch(reference["pin"])
        ):
            raise ValueError("experience_unpinned_locator")
        result.append({k: v for k, v in reference.items() if k != "path"})
    return result


def build(
    store,
    rows,
    *,
    sources,
    cutoff,
    context=8,
    chunk=4096,
    source_counts=None,
    schema=None,
    role="train",
    selection=None,
):
    """Retry replays the deterministic input prefix and reuses sealed objects; no row cap."""
    if role not in {"train", "research_evaluation"}:
        raise ValueError("experience_reserved_source")
    store = safe_path(store)
    if type(chunk) is not int or not 1 <= chunk <= 4096 or not sources:
        raise ValueError("experience_build_plan")
    schema = validate(schema) if schema is not None else contract(context)
    context = schema["context"]
    binding = dict(
        contract=schema,
        sources=logical_sources(sources),
        cutoff_ns=cutoff,
        chunk=chunk,
        code=code_identity(),
        role=role,
        selection=selection or {},
    )
    build_id = digest(binding)
    store.mkdir(parents=True, exist_ok=True)
    with lock(store / ".publication.lock"):
        for name in ("objects", "snapshots", "builds", "staging"):
            safe_path(store / name).mkdir(exist_ok=True)
        progress = store / "builds" / build_id
        progress.mkdir(exist_ok=True)
        sync_dir(store / "builds")
        sync_dir(store)
        atomic_json(progress / "binding.json", binding)
        atomic_json(progress / ("locators-" + digest(sources) + ".json"), sources)
        from .catalog import Writer

        parts, counts = Writer(store), Counter()
        consumed = 0

        def counted():
            nonlocal consumed
            for row in rows:
                consumed += 1
                yield row

        records = (
            experiences(counted(), cutoff, context)
            if schema["schema"] == "brain-experience-v1"
            else checked_samples(counted(), schema, cutoff)
        )
        # Stable pool/day boundaries let later snapshots share completed historical partitions.
        group_key = (
            (lambda r: r["pool"])
            if schema["schema"] == "brain-experience-v3"
            else lambda r: (
                r["pool"],
                (r["origin_s"] if "origin_s" in r else r["decision_ns"] // 10**9) // 86400,
            )
        )
        for _, group in itertools.groupby(records, key=group_key):
            while batch := list(itertools.islice(group, chunk)):
                part = write_partition(store, batch, schema)
                i = parts.count
                atomic_json(progress_part(progress, i), part)
                parts.append(part)
                counts.update(part["statuses"])
        if progress_part(progress, parts.count).exists() or not parts.count:
            raise ValueError("experience_empty_or_truncated_input")
        manifest = dict(
            binding=binding,
            **parts.finish(),
            decisions=parts.decisions,
            target_statuses=dict(counts),
            input_records=consumed,
            unique_bars=consumed if schema["schema"] == "brain-experience-v1" else None,
            context_unready_bars=(consumed - parts.decisions)
            if schema["schema"] == "brain-experience-v1"
            else None,
            source_counts=dict(source_counts or {}),
            production_eligible=False,
        )
        pin = digest(manifest)
        atomic_json(store / "snapshots" / (pin + ".json"), manifest)
        atomic_json(progress / "complete.json", dict(snapshot=pin))
        return dict(snapshot=pin, **manifest)


class Snapshot:
    """Metadata at snapshot granularity, payload IO bounded by one immutable partition."""

    def __init__(self, store, pin):
        if not isinstance(pin, str) or not PIN.fullmatch(pin):
            raise ValueError("experience_snapshot_pin")
        self.store, self.pin = safe_path(store), pin
        self._censor_cutoff_cache = None
        self.manifest = json.loads(
            safe_path(self.store / "snapshots" / (pin + ".json")).read_text()
        )
        if digest(self.manifest) != pin:
            raise ValueError("experience_snapshot_checksum")
        self.schema = self.manifest["binding"]["contract"]
        validate(self.schema)
        if "partitions" in self.manifest:
            if "partition_pages" in self.manifest:
                raise ValueError("experience_catalog_schema")
            self.parts = self.manifest["partitions"]
            if sum(p["count"] for p in self.parts) != self.manifest["decisions"]:
                raise ValueError("experience_snapshot_count")
        else:
            from .catalog import Parts

            self.parts = Parts(self.store, self.manifest)

    def censor_cutoff_ns(self):
        """Conservative cutoff inherited through immutable composition ancestry."""
        if self._censor_cutoff_cache is not None:
            return self._censor_cutoff_cache

        def catalog_metadata(entry):
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("pin"), str)
                or not PIN.fullmatch(entry["pin"])
                or type(entry.get("count")) is not int
                or entry["count"] < 1
            ):
                raise ValueError("experience_composition_lineage")
            return canonical({key: value for key, value in entry.items() if key != "bounds"})

        def index_membership(db, child, sources):
            db.execute("delete from expected_partitions")
            db.execute("delete from child_partitions")
            for source in sources:
                parent = Snapshot(child.store, source["pin"])
                for entry in parent.parts:
                    pin = entry.get("pin") if isinstance(entry, dict) else None
                    core = catalog_metadata(entry)
                    bounds_json = canonical(entry["bounds"]) if "bounds" in entry else None
                    existing = db.execute(
                        "select metadata,bounds from expected_partitions where pin=?", (pin,)
                    ).fetchone()
                    if existing is None:
                        db.execute(
                            "insert into expected_partitions values(?,?,?)",
                            (pin, core, bounds_json),
                        )
                    elif existing[0] != core or (
                        existing[1] is not None
                        and bounds_json is not None
                        and existing[1] != bounds_json
                    ):
                        raise ValueError("experience_composition_lineage")
                    elif existing[1] is None and bounds_json is not None:
                        db.execute(
                            "update expected_partitions set bounds=? where pin=?",
                            (bounds_json, pin),
                        )
            for entry in child.parts:
                pin = entry.get("pin") if isinstance(entry, dict) else None
                core = catalog_metadata(entry)
                bounds_json = canonical(entry["bounds"]) if "bounds" in entry else None
                if db.execute("select 1 from child_partitions where pin=?", (pin,)).fetchone():
                    raise ValueError("experience_composition_lineage")
                db.execute("insert into child_partitions values(?,?,?)", (pin, core, bounds_json))
            if (
                db.execute(
                    "select 1 from expected_partitions e left join child_partitions c using(pin) "
                    "where c.pin is null or c.metadata != e.metadata "
                    "or (e.bounds is not null and c.bounds is not e.bounds) limit 1"
                ).fetchone()
                or db.execute(
                    "select 1 from child_partitions c left join expected_partitions e using(pin) "
                    "where e.pin is null limit 1"
                ).fetchone()
            ):
                raise ValueError("experience_composition_lineage")

        def resolve(snapshot, active, memo, db):
            if snapshot.pin in active:
                raise ValueError("experience_composition_lineage")
            if snapshot.pin in memo:
                return memo[snapshot.pin]
            active.add(snapshot.pin)
            binding = snapshot.manifest.get("binding", {})
            cutoff = binding.get("cutoff_ns")
            if type(cutoff) is not int or cutoff < 0:
                raise ValueError("experience_composition_lineage")
            cutoffs = [cutoff]
            sources = binding.get("sources")
            declares_snapshot_sources = isinstance(sources, list) and any(
                isinstance(source, dict) and source.get("kind") == "snapshot" for source in sources
            )
            has_composition_marker = "composition" in snapshot.manifest
            if not has_composition_marker and (
                declares_snapshot_sources or "composition_counts" in snapshot.manifest
            ):
                raise ValueError("experience_composition_lineage")
            if has_composition_marker:
                if (
                    not isinstance(sources, list)
                    or not sources
                    or any(
                        not isinstance(source, dict)
                        or set(source) != {"kind", "pin"}
                        or source["kind"] != "snapshot"
                        or not isinstance(source["pin"], str)
                        or not PIN.fullmatch(source["pin"])
                        for source in sources
                    )
                    or [source["pin"] for source in sources]
                    != sorted({source["pin"] for source in sources})
                ):
                    raise ValueError("experience_composition_lineage")
                role = binding.get("role", "train")
                for source in sources:
                    try:
                        parent = Snapshot(snapshot.store, source["pin"])
                    except (OSError, TypeError, ValueError, KeyError) as exc:
                        raise ValueError("experience_composition_lineage") from exc
                    if (
                        parent.schema != snapshot.schema
                        or parent.manifest["binding"].get("role", "train") != role
                    ):
                        raise ValueError("experience_composition_lineage")
                    cutoffs.append(resolve(parent, active, memo, db))
                index_membership(db, snapshot, sources)
            active.remove(snapshot.pin)
            memo[snapshot.pin] = min(cutoffs)
            return memo[snapshot.pin]

        with tempfile.TemporaryDirectory(prefix="experience-lineage-") as temporary:
            db = sqlite3.connect(Path(temporary) / "membership.sqlite")
            try:
                db.execute("pragma cache_size=-8192")
                db.execute(
                    "create table expected_partitions(pin text primary key, metadata blob, "
                    "bounds blob)"
                )
                db.execute(
                    "create table child_partitions(pin text primary key, metadata blob, "
                    "bounds blob)"
                )
                self._censor_cutoff_cache = resolve(self, set(), {}, db)
            finally:
                db.close()
        return self._censor_cutoff_cache

    def partition(self, index):
        entry = self.parts[index]
        path = object_path(self.store, entry["pin"])
        verify_files(path, entry["pin"])
        description = json.loads((path / "partition.json").read_text())
        if (
            not isinstance(description, dict)
            or set(description) != {"contract", "count", "first", "last"}
            or description["contract"] != self.schema
            or type(description["count"]) is not int
            or type(entry.get("count")) is not int
            or description["count"] != entry["count"]
            or not isinstance(description["first"], str)
            or not isinstance(description["last"], str)
        ):
            raise ValueError("experience_partition_contract")
        with (path / "rows.jsonl").open() as stream:
            rows = [json.loads(line) for line in stream]
        n = len(rows)
        if (
            n != entry["count"]
            or not 1 <= n <= self.manifest["binding"]["chunk"]
            or (path / "x.f64").stat().st_size
            != n * self.schema["context"] * len(feature_names(self.schema)) * 8
            or (path / "y.f64").stat().st_size != n * len(target_names(self.schema)) * 8
        ):
            raise ValueError("experience_column_size")
        if description["first"] != rows[0].get("id") or description["last"] != rows[-1].get("id"):
            raise ValueError("experience_partition_extent")
        statuses = entry.get("statuses")
        if not isinstance(statuses, dict) or any(
            not isinstance(name, str) or type(count) is not int or count < 1
            for name, count in statuses.items()
        ):
            raise ValueError("experience_partition_statuses")
        observed_statuses = Counter()
        for row in rows:
            row_statuses = row.get("target_status")
            if not isinstance(row_statuses, list) or any(
                not isinstance(status, str) or not status for status in row_statuses
            ):
                raise ValueError("experience_partition_statuses")
            observed_statuses.update(row_statuses)
        if dict(observed_statuses) != statuses:
            raise ValueError("experience_partition_statuses")
        if self.schema["schema"] == "brain-experience-v3":
            from .coordinate_samples import validate_temporal_metadata

            cutoff_ns = self.manifest.get("binding", {}).get("cutoff_ns")
            for row in rows:
                validate_temporal_metadata(row, self.schema, cutoff_ns)
        if "bounds" in entry:
            from .composition import bounds

            if entry["bounds"] != bounds(rows, self.schema):
                raise ValueError("experience_partition_bounds")
        from .admission import apply

        apply(self, entry, rows)
        return path, rows


def audit_partition_columns(snapshot, index, *, row_chunk=256):
    """Check v2/v3 binary cells against row-status metadata without training or scoring."""
    import numpy as np

    if not isinstance(snapshot, Snapshot):
        raise ValueError("experience_column_audit_snapshot")
    if type(row_chunk) is not int or not 1 <= row_chunk <= 4096:
        raise ValueError("experience_column_audit_chunk")
    if snapshot.schema.get("schema") not in {"brain-experience-v2", "brain-experience-v3"}:
        raise ValueError("experience_column_audit_schema")
    if snapshot.manifest.get("binding", {}).get("role") != "train":
        raise ValueError("experience_column_audit_reserved_snapshot")

    from .schema import feature_names, target_names

    context = snapshot.schema["context"]
    feature_count = len(feature_names(snapshot.schema))
    target_count = len(target_names(snapshot.schema))
    bytes_per_row = 8 * (context * feature_count + target_count)
    if bytes_per_row > 16 * 1024 * 1024:
        raise ValueError("experience_column_audit_row_size")
    bounded_chunk = min(row_chunk, (16 * 1024 * 1024) // bytes_per_row)
    path, rows = snapshot.partition(index)
    row_count = len(rows)
    exact_status = "observed" if snapshot.schema["schema"] == "brain-experience-v2" else "exact"
    statuses = (
        {"observed", "pending", "missing_future", "unsupported", "censored"}
        if snapshot.schema["schema"] == "brain-experience-v2"
        else {"exact", "lower_bound", "masked", "pending"}
    )
    status_counts = Counter()
    exact_cells = 0
    masked_cells = 0
    input_infinities = 0
    exact_nonfinite = 0
    masked_non_nan = 0

    for row in rows:
        row_statuses = row.get("target_status")
        if not isinstance(row_statuses, list) or len(row_statuses) != target_count:
            raise ValueError("experience_column_audit_status_shape")
        if any(status not in statuses for status in row_statuses):
            raise ValueError("experience_column_audit_unknown_status")
        status_counts.update(row_statuses)

    with (path / "x.f64").open("rb") as x_stream, (path / "y.f64").open("rb") as y_stream:
        for start in range(0, row_count, bounded_chunk):
            stop = min(start + bounded_chunk, row_count)
            count = stop - start
            x = np.fromfile(x_stream, dtype="<f8", count=count * context * feature_count)
            y = np.fromfile(y_stream, dtype="<f8", count=count * target_count)
            if x.size != count * context * feature_count or y.size != count * target_count:
                raise ValueError("experience_column_audit_truncated")
            x = x.reshape(count, context, feature_count)
            y = y.reshape(count, target_count)
            exact = np.asarray(
                [
                    [status == exact_status for status in rows[row_index]["target_status"]]
                    for row_index in range(start, stop)
                ],
                dtype=bool,
            )
            input_infinities += int(np.isinf(x).sum())
            exact_cells += int(exact.sum())
            masked_cells += int((~exact).sum())
            exact_nonfinite += int((~np.isfinite(y[exact])).sum())
            masked_non_nan += int((~np.isnan(y[~exact])).sum())

    failures = dict(
        input_infinities=input_infinities,
        exact_target_nonfinite=exact_nonfinite,
        masked_target_non_nan=masked_non_nan,
    )
    matches = not any(failures.values())
    return dict(
        status="matched" if matches else "mismatch",
        matches=matches,
        snapshot_pin=snapshot.pin,
        partition_index=index,
        partition_pin=snapshot.parts[index]["pin"],
        rows=row_count,
        input_cells=row_count * context * feature_count,
        target_cells=row_count * target_count,
        exact_target_cells=exact_cells,
        masked_target_cells=masked_cells,
        target_status_counts=dict(sorted(status_counts.items())),
        failures=failures,
    )


def audit_snapshot_columns(snapshot, *, row_chunk=256):
    """Audit every train partition with bounded aggregate memory and reconcile coverage."""
    if not isinstance(snapshot, Snapshot):
        raise ValueError("experience_column_audit_snapshot")
    if snapshot.schema.get("schema") not in {"brain-experience-v2", "brain-experience-v3"}:
        raise ValueError("experience_column_audit_schema")
    if snapshot.manifest.get("binding", {}).get("role") != "train":
        raise ValueError("experience_column_audit_reserved_snapshot")
    if type(row_chunk) is not int or not 1 <= row_chunk <= 4096:
        raise ValueError("experience_column_audit_chunk")

    expected_partitions = len(snapshot.parts)
    expected_rows = snapshot.manifest.get("decisions")
    rows = input_cells = target_cells = exact_cells = masked_cells = 0
    status_counts = Counter()
    failures = Counter()
    audited_partitions = failed_partitions = 0
    first_failing_index = None

    for index in range(expected_partitions):
        result = audit_partition_columns(snapshot, index, row_chunk=row_chunk)
        audited_partitions += 1
        rows += result["rows"]
        input_cells += result["input_cells"]
        target_cells += result["target_cells"]
        exact_cells += result["exact_target_cells"]
        masked_cells += result["masked_target_cells"]
        status_counts.update(result["target_status_counts"])
        failures.update(result["failures"])
        if not result["matches"]:
            failed_partitions += 1
            if first_failing_index is None:
                first_failing_index = index

    coverage_matches = audited_partitions == expected_partitions
    row_count_matches = type(expected_rows) is int and expected_rows > 0 and rows == expected_rows
    matches = coverage_matches and row_count_matches and failed_partitions == 0
    return dict(
        status="matched" if matches else "mismatch",
        matches=matches,
        snapshot_pin=snapshot.pin,
        expected_partitions=expected_partitions,
        audited_partitions=audited_partitions,
        expected_rows=expected_rows,
        rows=rows,
        row_count_matches=row_count_matches,
        input_cells=input_cells,
        target_cells=target_cells,
        exact_target_cells=exact_cells,
        masked_target_cells=masked_cells,
        target_status_counts=dict(sorted(status_counts.items())),
        failed_partitions=failed_partitions,
        first_failing_index=first_failing_index,
        failures=dict(
            input_infinities=failures["input_infinities"],
            exact_target_nonfinite=failures["exact_target_nonfinite"],
            masked_target_non_nan=failures["masked_target_non_nan"],
        ),
    )
