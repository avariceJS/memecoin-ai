"""Coarse TLS Date corroboration for finite prospective bucket timing only."""

from __future__ import annotations

import email.utils
import http.client
import ssl
import time
from collections.abc import Callable
from typing import Any

CLOCK_SCHEMA = "brain-senses-external-v1-clock"
HOSTS = ("www.cloudflare.com", "www.google.com")
SECOND = 1_000_000_000
MAX_AGE = 300 * SECOND


class ClockError(ValueError):
    """A clock observation failed its bounded prospective timing policy."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ClockError(reason)


def _int(value: Any) -> bool:
    return type(value) is int and value >= 0


def validate_clock(record: dict[str, Any], *, now_ns: int | None = None) -> None:
    _require(
        set(record) == {"schema", "schema_version", "samples", "checked_ns", "monotonic_ns"},
        "clock_fields",
    )
    _require(
        record["schema"] == CLOCK_SCHEMA
        and type(record["schema_version"]) is int
        and record["schema_version"] == 1,
        "clock_schema",
    )
    samples = record["samples"]
    _require(isinstance(samples, list) and len(samples) == 2, "clock_sample_count")
    previous_wall = previous_mono = 0
    dates = []
    for host, row in zip(HOSTS, samples, strict=True):
        _require(
            isinstance(row, dict)
            and set(row)
            == {
                "host",
                "status",
                "date",
                "age",
                "sent_ns",
                "received_ns",
                "monotonic_sent_ns",
                "monotonic_received_ns",
            },
            "clock_sample_fields",
        )
        _require(
            row["host"] == host
            and type(row["status"]) is int
            and row["status"] == 200
            and row["age"] in (None, "0"),
            "clock_http",
        )
        for name in ("sent_ns", "received_ns", "monotonic_sent_ns", "monotonic_received_ns"):
            _require(_int(row[name]), "clock_sample_time")
        try:
            date = email.utils.parsedate_to_datetime(row["date"])
            offset = date.utcoffset()
            _require(
                offset is not None and offset.total_seconds() == 0,
                "clock_date_zone",
            )
            date_ns = int(date.timestamp()) * SECOND
        except (TypeError, ValueError, OverflowError) as exc:
            raise ClockError("clock_date") from exc
        dates.append(date_ns)
        wall_rtt = row["received_ns"] - row["sent_ns"]
        mono_rtt = row["monotonic_received_ns"] - row["monotonic_sent_ns"]
        _require(
            0 <= wall_rtt <= 2 * SECOND
            and 0 <= mono_rtt <= 2 * SECOND
            and abs(wall_rtt - mono_rtt) <= 2 * SECOND,
            "clock_rtt_or_regression",
        )
        _require(
            date_ns - 2 * SECOND <= row["sent_ns"] <= row["received_ns"] <= date_ns + 3 * SECOND,
            "clock_date_disagreement",
        )
        _require(
            row["sent_ns"] >= previous_wall and row["monotonic_sent_ns"] >= previous_mono,
            "clock_pair_regression",
        )
        previous_wall, previous_mono = row["received_ns"], row["monotonic_received_ns"]
    _require(abs(dates[0] - dates[1]) <= 2 * SECOND, "clock_sources_disagree")
    _require(
        record["checked_ns"] == previous_wall and record["monotonic_ns"] == previous_mono,
        "clock_watermark",
    )
    if now_ns is not None:
        _require(
            _int(now_ns) and 0 <= now_ns - min(row["received_ns"] for row in samples) <= MAX_AGE,
            "clock_stale",
        )


def validate_progress(record: dict[str, Any], *, wall_ns: int, monotonic_ns: int) -> None:
    _require(_int(wall_ns) and _int(monotonic_ns), "clock_progress_type")
    validate_clock(record, now_ns=wall_ns)
    wall = wall_ns - record["checked_ns"]
    mono = monotonic_ns - record["monotonic_ns"]
    _require(
        _int(monotonic_ns) and wall >= 0 and mono >= 0 and abs(wall - mono) <= 2 * SECOND,
        "clock_progress_regression_or_divergence",
    )


def probe_pair(
    *, wall: Callable[[], int] = time.time_ns, monotonic: Callable[[], int] = time.monotonic_ns
) -> dict[str, Any]:
    """Two bounded verified-TLS HEAD calls, without redirect or retry."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_default_certs()
    context.keylog_filename = None  # type: ignore[assignment]  # CPython accepts None to disable.
    samples = []
    for host in HOSTS:
        connection = http.client.HTTPSConnection(host, timeout=2.0, context=context)
        sent_ns, mono_sent = wall(), monotonic()
        try:
            connection.request(
                "HEAD",
                "/",
                headers={"Cache-Control": "no-cache", "User-Agent": "brain-research-clock/1"},
            )
            response = connection.getresponse()
            received_ns, mono_received = wall(), monotonic()
            sample = {
                "host": host,
                "status": response.status,
                "date": response.getheader("Date"),
                "age": response.getheader("Age"),
                "sent_ns": sent_ns,
                "received_ns": received_ns,
                "monotonic_sent_ns": mono_sent,
                "monotonic_received_ns": mono_received,
            }
        except Exception as exc:
            # Do not log exception content from external transport.
            raise ClockError("clock_transport_unavailable") from exc
        finally:
            connection.close()
        samples.append(sample)
    record = {
        "schema": CLOCK_SCHEMA,
        "schema_version": 1,
        "samples": samples,
        "checked_ns": samples[-1]["received_ns"],
        "monotonic_ns": samples[-1]["monotonic_received_ns"],
    }
    validate_clock(record, now_ns=wall())
    return record
