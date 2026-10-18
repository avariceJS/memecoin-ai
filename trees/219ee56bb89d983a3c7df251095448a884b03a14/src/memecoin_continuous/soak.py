"""Observed service coverage, not invented calendar uptime or execution performance."""

import sqlite3

from memecoin_ml.data import read
from memecoin_runner.ledger import publish


def record(root, sample):
    output = root / "soak"
    output.mkdir(exist_ok=True)
    path = output / (str(sample["at_ns"]) + ".json")
    if path.exists():
        if read(path) != sample:
            raise ValueError("soak_timestamp_collision")
    else:
        publish(path, sample)
    # This is a dashboard cache. Immutable samples above remain the audit evidence.
    with sqlite3.connect(root / "soak.sqlite") as db:
        db.execute(
            "create table if not exists samples(at_ns integer primary key, fresh integer, "
            "source_ok integer, attempts integer, bars integer)"
        )
        db.execute(
            "insert or ignore into samples values(?,?,?,?,?)",
            (
                sample["at_ns"],
                sample["fresh_hot"],
                int(sample["source_ok"]),
                sample["source_attempts"],
                sample["corpus_bars"],
            ),
        )
        result = []
        for day, count, first, last, healthy, one, four in db.execute(
            "select at_ns/86400000000000, count(*), min(at_ns), max(at_ns), "
            "sum(source_ok), sum(fresh>=1),sum(fresh>=4) from samples "
            "group by at_ns/86400000000000 order by 1"
        ):
            gaps = [
                b - a
                for (a, b) in db.execute(
                    "select lag(at_ns) over(order by at_ns),at_ns from samples "
                    "where at_ns/86400000000000=?",
                    (day,),
                )
                if a is not None
            ]
            result.append(
                dict(
                    UTC_day=day,
                    samples=count,
                    span_hours=(last - first) / 3.6e12,
                    max_sample_gap_s=max(gaps, default=0) / 1e9,
                    source_ok_fraction=healthy / count,
                    one_fresh_fraction=one / count,
                    four_fresh_fraction=four / count,
                    observed_hours=sum(min(g, 90 * 10**9) for g in gaps) / 3.6e12,
                )
            )
    return result
