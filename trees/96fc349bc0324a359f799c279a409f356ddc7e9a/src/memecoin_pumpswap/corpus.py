"""Immutable, globally deduplicated historical corpus. One builder, read-only consumers."""

import json
import sqlite3

from memecoin_ml.data import read, sha
from memecoin_registry.contracts import digest, seal, verify
from memecoin_runner.ledger import publish


def merge_events(db, events, source):
    duplicates = 0
    for event in events:
        if event["id"] != digest(dict(signature=event["signature"], path=event["path"])):
            raise ValueError("canonical_event_identity")
        if event["training_eligible"] or event["execution_supported"]:
            raise ValueError("unqualified_admission")
        economic = {
            k: event[k]
            for k in (
                "id",
                "signature",
                "path",
                "slot",
                "event_s",
                "pool",
                "base_mint",
                "quote_mint",
                "side",
                "movement",
            )
        }
        pin = digest(economic)
        previous = db.execute(
            "select economic_sha from events where id=?", (event["id"],)
        ).fetchone()
        if previous and previous[0] != pin:
            raise ValueError("canonical_event_revision")
        duplicates += previous is not None
        if not previous:
            db.execute(
                "insert into events values(?,?,?,?,?,?,?)",
                (
                    event["id"],
                    event["pool"],
                    event["event_s"],
                    event["quote_mint"],
                    pin,
                    json.dumps(economic, sort_keys=True),
                    0,
                ),
            )
        db.execute(
            "insert or ignore into provenance values(?,?,?,?,?,?)",
            (
                event["id"],
                source,
                event["raw_sha"],
                event["received_ns"],
                event["available_ns"],
                json.dumps(event["profiles"]),
            ),
        )
    return duplicates


def schema(db):
    db.executescript("""
      create table events(id text primary key, pool text, event_s integer, quote_mint text,
        economic_sha text, payload text, training_eligible integer check(training_eligible=0));
      create index event_pool_time on events(pool,event_s);
      create table provenance(id text, source text, raw_sha text, received_ns integer,
        available_ns integer, profiles text, primary key(id,source,raw_sha));
      create table births(pool text primary key, event_s integer, payload text);
    """)


def build(birth_catalogue, swap_roots, output):
    verify(birth_catalogue)
    for root in swap_roots:
        verify(root)
    output.mkdir(parents=True, exist_ok=False)
    binding = dict(
        schema="historical-corpus-v1",
        births=dict(
            path=str(birth_catalogue.resolve()), sha=sha(birth_catalogue / "manifest.json")
        ),
        swaps=[dict(path=str(r.resolve()), sha=sha(r / "manifest.json")) for r in swap_roots],
        training_eligible=False,
        historical_online_availability_known=False,
    )
    publish(output / "binding.json", binding)
    try:
        with sqlite3.connect(output / "corpus.sqlite") as db:
            schema(db)
            with sqlite3.connect(
                f"file:{birth_catalogue / 'catalogue.sqlite'}?mode=ro&immutable=1", uri=True
            ) as source:
                for pool, event_s, payload in source.execute(
                    "select pool,event_s,payload from births"
                ):
                    db.execute("insert into births values(?,?,?)", (pool, event_s, payload))
            duplicates = 0
            for root in swap_roots:
                for page in sorted(root.glob("*-events.json")):
                    duplicates += merge_events(db, read(page), sha(page))
                    db.commit()
            report = dict(
                events=db.execute("select count(*) from events").fetchone()[0],
                swap_pools=db.execute("select count(distinct pool) from events").fetchone()[0],
                birth_pools=db.execute("select count(*) from births").fetchone()[0],
                markets_total=db.execute(
                    "select count(*) from (select pool from births union select pool from events)"
                ).fetchone()[0],
                provenance_rows=db.execute("select count(*) from provenance").fetchone()[0],
                duplicate_observations=duplicates,
                qualified_training_decisions=0,
                source_binding_sha=digest(binding),
                unknown_market_outcomes=True,
            )
        publish(output / "report.json", report)
        seal(output)
        return report
    except Exception as exc:
        publish(
            output / "STOP",
            dict(category=str(exc) if isinstance(exc, ValueError) else type(exc).__name__),
        )
        seal(output)
        raise
