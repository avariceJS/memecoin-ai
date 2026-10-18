from __future__ import annotations

from email.utils import formatdate
from typing import Any

import pytest

from tools import brain_senses_external_clock_v1 as c


def record() -> dict[str, Any]:
    at = 1_800_000_000 * c.SECOND
    samples = [
        {
            "host": host,
            "status": 200,
            "date": formatdate(at // c.SECOND, usegmt=True),
            "age": None,
            "sent_ns": at + i * 1_000_000,
            "received_ns": at + i * 1_000_000 + 100_000,
            "monotonic_sent_ns": at + i * 1_000_000,
            "monotonic_received_ns": at + i * 1_000_000 + 100_000,
        }
        for i, host in enumerate(c.HOSTS)
    ]
    return {
        "schema": c.CLOCK_SCHEMA,
        "schema_version": 1,
        "samples": samples,
        "checked_ns": samples[-1]["received_ns"],
        "monotonic_ns": samples[-1]["monotonic_received_ns"],
    }


def test_accepts_coarse_pair_and_consistent_monotonic_progress() -> None:
    value = record()
    c.validate_clock(value, now_ns=value["checked_ns"])
    c.validate_progress(
        value, wall_ns=value["checked_ns"] + c.SECOND, monotonic_ns=value["monotonic_ns"] + c.SECOND
    )


@pytest.mark.parametrize(
    "mutation",
    ["redirect", "age", "slow", "date", "host", "regression", "stale", "divergence", "noninteger"],
)
def test_fails_closed_on_untrusted_timing(mutation: str) -> None:
    value = record()
    wall = value["checked_ns"] + c.SECOND
    mono = value["monotonic_ns"] + c.SECOND
    if mutation == "redirect":
        value["samples"][0]["status"] = 302
    elif mutation == "age":
        value["samples"][0]["age"] = "60"
    elif mutation == "slow":
        value["samples"][0]["received_ns"] += 3 * c.SECOND
    elif mutation == "date":
        value["samples"][0]["date"] = "Sat, 01 Jan 2000 00:00:00 GMT"
    elif mutation == "host":
        value["samples"][0]["host"] = "untrusted.example"
    elif mutation == "regression":
        wall = value["checked_ns"] - 1
    elif mutation == "stale":
        wall += c.MAX_AGE
        mono += c.MAX_AGE
    elif mutation == "divergence":
        mono += 3 * c.SECOND
    else:
        mono = None
    with pytest.raises(c.ClockError):
        c.validate_progress(value, wall_ns=wall, monotonic_ns=mono)
