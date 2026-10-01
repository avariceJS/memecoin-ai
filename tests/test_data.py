from dataclasses import replace

import pytest

from memecoin_ai.domain import Availability, Event, Pool, ValidationError
from memecoin_ai.observations import ObservationBook
from memecoin_ai.storage import EventStore, audit, load_bundle, write_bundle


def event(name="e1", t=10, available=20):
    return Event.pool_event(name, t, available, Pool("p", "TOKEN", "SOL", 10000, 10000, 30))


def test_event_roundtrip_and_invalid_timestamps():
    e = event()
    assert Event.from_json(e.to_json()) == e
    with pytest.raises(ValidationError):
        replace(e, available_ns=9)
    with pytest.raises(ValidationError):
        replace(e, received_ns=None)
    with pytest.raises(ValidationError):
        replace(e, availability=Availability.ASSUMED)
    with pytest.raises(ValidationError):
        replace(e, event_ns=True)


def test_store_transactional_idempotency_and_conflicts(tmp_path):
    with EventStore(tmp_path / "store.sqlite") as store:
        assert store.append([event()]) == 1
        assert store.append([event()]) == 0
        with pytest.raises(ValidationError):
            store.append([event("e2"), replace(event(), sequence=2)])
        assert store.events() == [event()]
        sha = store.put_raw(b"raw")
        assert store.raw(sha) == b"raw"
        assert store.append([replace(event("raw"), raw_sha256=sha)]) == 1


def test_bundle_integrity_and_identity(tmp_path):
    a = write_bundle(tmp_path / "a", [event()], "fixture")
    b = write_bundle(tmp_path / "b", [event()], "fixture")
    assert a.identity == b.identity
    assert audit(a)["coverage_verified"] is False
    (a.path / "events.jsonl").write_text("")
    with pytest.raises(ValidationError):
        load_bundle(a.path)
    with pytest.raises(FileExistsError):
        write_bundle(b.path, [event()], "no overwrite")


def test_availability_not_event_time_and_no_rewind():
    book = ObservationBook()
    with pytest.raises(ValidationError):
        book.ingest(event(), 15)
    assert not book.snapshot(15).pools
    book.ingest(event("new", 30, 40), 40)
    book.ingest(event("late", 10, 50), 50)
    assert book.snapshot(50).pools["p"].event_ns == 30
    with pytest.raises(TypeError):
        book.snapshot(50).pools["x"] = None


def test_unknown_and_assumed_are_not_observed():
    book = ObservationBook()
    unknown = replace(event(), available_ns=None, availability=Availability.UNKNOWN)
    with pytest.raises(ValidationError):
        book.ingest(unknown, 100)
    assumed = replace(event(), availability=Availability.ASSUMED, assumption="delay-v1")
    with pytest.raises(ValidationError):
        book.ingest(assumed, 100)
    book.ingest(assumed, 100, allow_assumed=True)


def test_bundle_raw_provenance_is_required_and_verified(tmp_path):
    from memecoin_ai.domain import digest

    raw = b'{"provider":"fixture"}'
    e = replace(event(), raw_sha256=digest(raw))
    with pytest.raises(ValidationError):
        write_bundle(tmp_path / "missing", [e], "fixture")
    bundle = write_bundle(tmp_path / "ok", [e], "fixture", raw_blobs={digest(raw): raw})
    (bundle.path / "raw" / digest(raw)).write_bytes(b"changed")
    with pytest.raises(ValidationError):
        load_bundle(bundle.path)
