from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memecoin_ai.domain import Availability, Event, Origin, ValidationError, canonical, digest


class EventStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS meta(version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS raw(sha TEXT PRIMARY KEY, body BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS events(
                event_id TEXT PRIMARY KEY, event_ns INTEGER NOT NULL,
                available_ns INTEGER, body TEXT NOT NULL
            );
        """)
        rows = self.connection.execute("SELECT version FROM meta").fetchall()
        if not rows:
            self.connection.execute("INSERT INTO meta VALUES (1)")
            self.connection.commit()
        elif rows != [(1,)]:
            self.close()
            raise ValidationError("unsupported store version")

    def __enter__(self) -> EventStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def put_raw(self, body: bytes) -> str:
        sha = digest(body)
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO raw VALUES (?,?)", (sha, body))
        return sha

    def raw(self, sha: str) -> bytes:
        row = self.connection.execute("SELECT body FROM raw WHERE sha=?", (sha,)).fetchone()
        if row is None or digest(bytes(row[0])) != sha:
            raise ValidationError("missing or corrupt raw blob")
        return bytes(row[0])

    def append(self, events: Iterable[Event]) -> int:
        inserted = 0
        # Entire batch rolls back on a conflicting ID; duplicates are idempotent.
        with self.connection:
            for event in events:
                body = event.to_json()
                row = self.connection.execute(
                    "SELECT body FROM events WHERE event_id=?", (event.event_id,)
                ).fetchone()
                if row is not None:
                    if row[0] != body:
                        raise ValidationError(f"conflicting event ID: {event.event_id}")
                    continue
                if event.raw_sha256 is not None:
                    self.raw(event.raw_sha256)
                self.connection.execute(
                    "INSERT INTO events VALUES (?,?,?,?)",
                    (event.event_id, event.event_ns, event.available_ns, body),
                )
                inserted += 1
        return inserted

    def events(self) -> list[Event]:
        return [
            Event.from_json(row[0])
            for row in self.connection.execute("SELECT body FROM events ORDER BY event_ns,event_id")
        ]


@dataclass(frozen=True)
class Bundle:
    path: Path
    manifest: dict[str, Any]
    events: tuple[Event, ...]

    @property
    def identity(self) -> str:
        return digest(canonical(self.manifest).encode())


def write_bundle(
    path: Path,
    events: Iterable[Event],
    description: str,
    *,
    raw_blobs: dict[str, bytes] | None = None,
) -> Bundle:
    if path.exists():
        raise FileExistsError(path)
    ordered = sorted(events, key=lambda e: (e.event_ns, e.sequence, e.event_id))
    if not ordered or len({e.event_id for e in ordered}) != len(ordered):
        raise ValidationError("bundle requires nonempty events with unique IDs")
    raw_blobs = raw_blobs or {}
    required_raw = {e.raw_sha256 for e in ordered if e.raw_sha256 is not None}
    if set(raw_blobs) != required_raw or any(digest(b) != h for h, b in raw_blobs.items()):
        raise ValidationError("raw provenance blobs missing, unreferenced or corrupt")
    content = ("\n".join(e.to_json() for e in ordered) + "\n").encode()
    manifest = {
        "schema_version": 1,
        "description": description,
        "count": len(ordered),
        "raw_sha256": sorted(raw_blobs),
        "events_sha256": digest(content),
        "origin": sorted({e.origin.value for e in ordered}),
        "availability": sorted({e.availability.value for e in ordered}),
        "sources": sorted({e.source for e in ordered}),
        "event_start_ns": min(e.event_ns for e in ordered),
        "event_end_ns": max(e.event_ns for e in ordered),
        "coverage": "unknown; bounds do not establish completeness",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".bundle-", dir=path.parent))
    try:
        (staging / "raw").mkdir()
        for sha, body in raw_blobs.items():
            (staging / "raw" / sha).write_bytes(body)
        (staging / "events.jsonl").write_bytes(content)
        (staging / "manifest.json").write_text(canonical(manifest) + "\n")
        # Reserve the destination exclusively; a concurrent publisher must not replace it.
        # Manifest is published last, so interrupted output cannot pass load_bundle.
        path.mkdir(exist_ok=False)
        for name in ("raw", "events.jsonl", "manifest.json"):
            os.rename(staging / name, path / name)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return load_bundle(path)


def load_bundle(path: Path) -> Bundle:
    manifest = json.loads((path / "manifest.json").read_text())
    content = (path / "events.jsonl").read_bytes()
    if manifest.get("schema_version") != 1 or digest(content) != manifest["events_sha256"]:
        raise ValidationError("bundle schema or checksum mismatch")
    events = tuple(Event.from_json(line) for line in content.decode().splitlines())
    if not events or len(events) != manifest["count"]:
        raise ValidationError("bundle count mismatch")
    if len({e.event_id for e in events}) != len(events):
        raise ValidationError("duplicate event IDs")
    actual = {
        "origin": sorted({e.origin.value for e in events}),
        "availability": sorted({e.availability.value for e in events}),
        "sources": sorted({e.source for e in events}),
        "event_start_ns": min(e.event_ns for e in events),
        "event_end_ns": max(e.event_ns for e in events),
    }
    if any(manifest.get(k) != v for k, v in actual.items()):
        raise ValidationError("manifest disagrees with events")
    required_raw = sorted({e.raw_sha256 for e in events if e.raw_sha256})
    if manifest.get("raw_sha256") != required_raw:
        raise ValidationError("raw provenance manifest mismatch")
    for sha in required_raw:
        if digest((path / "raw" / sha).read_bytes()) != sha:
            raise ValidationError("raw provenance checksum mismatch")
    return Bundle(path, manifest, events)


def audit(bundle: Bundle) -> dict[str, object]:
    events = bundle.events
    unknown = sum(e.availability == Availability.UNKNOWN for e in events)
    assumed = sum(e.availability == Availability.ASSUMED for e in events)
    return {
        "dataset_id": bundle.identity,
        "count": len(events),
        "unknown_availability": unknown,
        "assumed_availability": assumed,
        "synthetic": all(e.origin == Origin.SYNTHETIC for e in events),
        "declared_gaps": sum(e.kind == "gap" for e in events),
        "observed_delays_ns": sorted(
            e.available_ns - e.event_ns
            for e in events
            if e.available_ns is not None and e.availability == Availability.OBSERVED
        ),
        "coverage_verified": False,
        "real_execution_calibrated": False,
    }
