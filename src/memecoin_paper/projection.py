"""Derived replay checkpoint bound to the immutable ledger result at its prefix."""

from memecoin_ml.data import read
from memecoin_registry.contracts import digest

from .engine import advance, initial
from .runtime import atomic


def restore(root, events):
    state, records, last, count = initial(), [], [], 0
    path = root / "projection.json"
    if path.exists():
        cached = read(path)
        if not isinstance(cached, dict) or set(cached) != {"payload", "checksum"}:
            raise ValueError("paper_projection_shape")
        p = cached["payload"]
        if not isinstance(p, dict) or set(p) != {
            "events", "head", "state", "records", "last_records"
        }:
            raise ValueError("paper_projection_shape")
        if digest(p) != cached["checksum"]:
            raise ValueError("paper_projection_checksum")
        count = p["events"]
        if (
            type(count) is not int
            or count <= 0
            or count > len(events)
            or not isinstance(p["head"], str)
            or not isinstance(p["state"], dict)
            or not isinstance(p["records"], list)
            or not isinstance(p["last_records"], list)
        ):
            raise ValueError("paper_projection_shape")
        if p["last_records"] and p["records"][-len(p["last_records"]):] != p["last_records"]:
            raise ValueError("paper_projection_records_suffix")
        if events[count - 1]["sha256"] != p["head"]:
            raise ValueError("paper_projection_prefix")
        state, records, last = p["state"], p["records"], p["last_records"]
        if digest(dict(state=state, records=last)) != events[count - 1]["payload"]["result_sha"]:
            raise ValueError("paper_projection_state_binding")
    for e in events[count:]:
        state, last = advance(state, e["payload"]["tick"])
        if digest(dict(state=state, records=last)) != e["payload"]["result_sha"]:
            raise ValueError("paper_replay_mismatch")
        records.extend(last)
    return state, records, last


def save(root, events, state, records, last_records):
    if not events:
        return
    p = dict(
        events=len(events),
        head=events[-1]["sha256"],
        state=state,
        records=records,
        last_records=last_records,
    )
    if digest(dict(state=state, records=last_records)) != events[-1]["payload"]["result_sha"]:
        raise ValueError("paper_projection_save_binding")
    atomic(root / "projection.json", dict(payload=p, checksum=digest(p)))
