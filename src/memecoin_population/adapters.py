"""Explicit adapters for existing research exports. No provider calls or live index access."""

import json
import sqlite3
import tempfile
from pathlib import Path

from memecoin_experience.continuous import NAMESPACE, source, source_plan
from memecoin_experience.storage import digest, file_hash, safe_path, verify_files

from .catalog import MAX_TIME, entity, publish
from .planning import work


def universe(store, path, pin, *, role, cutoff_ns, age_edges_ns=()):
    """Import a pinned legacy enrollment; discovery knowledge is never backdated to birth."""
    path = safe_path(path)
    if file_hash(path) != pin:
        raise ValueError("population_universe_pin")
    value = json.loads(path.read_text())
    value = value.get("universe", value)
    edges = list(age_edges_ns)
    if any(type(t) is not int or not 0 < t < MAX_TIME for t in edges) or edges != sorted(
        set(edges)
    ):
        raise ValueError("population_age_edges")

    def rows():
        for pool in value["pools"]:
            name = entity("solana/pool/" + pool["pool"])
            born = pool.get("pool_created_ns")
            if type(born) is not int or not 0 <= born < MAX_TIME:
                raise ValueError("population_universe_birth")
            known = pool["admitted_ns"]
            attrs = dict(
                venue=pool.get("dex"),
                birth_cohort=str(born // (86400 * 10**9)),
                enrollment="legacy_survivor_selected",
                base_asset=pool["base_mint"],
                quote_asset=pool["quote_mint"],
            )
            bounds = [born, *(min(MAX_TIME, born + t) for t in edges), MAX_TIME]
            for i, (a, b) in enumerate(zip(bounds, bounds[1:], strict=False)):
                if a == b:
                    continue
                yield dict(
                    entity=name,
                    start_ns=a,
                    end_ns=b,
                    known_ns=known,
                    attributes=dict(attrs, lifecycle=f"age_band:{i}" if edges else None),
                    evidence=[pin, pool["capture_sha"]],
                )

    return publish(
        store,
        "population",
        rows(),
        cutoff_ns=cutoff_ns,
        definition=dict(
            role=role,
            producer="legacy_universe_v1",
            age_edges_ns=edges,
            selection="legacy_survivor_selected; not a historical market census",
        ),
    )


def covers_grid(export, pool, start_ns, end_ns):
    """An export covers a work cell only if it declares every eligible bar open in that cell."""
    cadence = 300 * 10**9
    first, end = ((t + cadence - 1) // cadence for t in (start_ns, end_ns))
    spans = export.get("intervals", {}).get(pool, [[export["start_s"], export["end_s"]]])
    covered = sum(max(0, min(end, b // 300) - max(first, a // 300)) for a, b in spans)
    return covered == end - first


def continuous_coverage(store, reference, plan_path, plan_pin):
    """Requalify sealed captures and project actual unique bars onto declared sparse work cells."""
    plan_path = safe_path(plan_path)
    verify_files(plan_path, plan_pin)
    binding = json.loads((plan_path / "plan.json").read_text())
    plan = binding["plan"]
    meta = safe_path(Path(reference["path"]) / "meta")
    verify_files(meta, reference["pin"])
    export = source_plan(json.loads((meta / "source.json").read_text())["plan"])
    if export["role"] != binding["role"] or export["cutoff_ns"] > plan["cutoff_ns"]:
        raise ValueError("population_continuous_role_or_cutoff")
    pools = {"solana/pool/" + p["pool"] for p in export["pools"]}
    compatible = {
        digest(s)
        for s in plan["streams"]
        if s["source"] == NAMESPACE
        and s["modality"] == "ohlcv"
        and s["representation"] == "qualified-ohlcv-v1"
        and s["cadence_ns"] == 300 * 10**9
        and s["unit"] == "unique_bar_opens"
    }
    if not compatible:
        raise ValueError("population_continuous_stream_contract")
    with tempfile.TemporaryDirectory(prefix="population-bars-") as scratch:
        db = sqlite3.connect(Path(scratch) / "bars.sqlite")
        try:
            db.execute("pragma cache_size=-8192")
            db.execute(
                "create table bars(entity text, at integer, primary key(entity,at)) without rowid"
            )
            for row in source(reference, plan["cutoff_ns"], role=binding["role"]):
                db.execute(
                    "insert into bars values(?,?)",
                    ("solana/pool/" + row["pool"], row["open_s"] * 10**9),
                )
            db.commit()

            def rows():
                for cell in work(plan_path, plan_pin, None):
                    if (
                        cell["contract"] not in compatible
                        or cell["entity"] not in pools
                        or not covers_grid(
                            export, cell["entity"].split("/")[-1], cell["start_ns"], cell["end_ns"]
                        )
                    ):
                        continue  # Unknown outside the sealed export's declared scope.
                    observed = db.execute(
                        "select count(*) from bars where entity=? and at>=? and at<?",
                        (cell["entity"], cell["start_ns"], cell["end_ns"]),
                    ).fetchone()[0]
                    if observed > cell["expected"]:
                        raise ValueError("population_continuous_grid")
                    yield dict(
                        **{
                            k: cell[k]
                            for k in (
                                "entity",
                                "stream",
                                "contract",
                                "start_ns",
                                "end_ns",
                                "unit",
                                "expected",
                            )
                        },
                        known_ns=export["cutoff_ns"],
                        stage="qualified",
                        status="complete" if observed == cell["expected"] else "partial",
                        observed=observed,
                        evidence=[reference["pin"], plan_pin],
                    )

            return publish(
                store,
                "coverage",
                rows(),
                cutoff_ns=plan["cutoff_ns"],
                definition=dict(
                    role=binding["role"],
                    producer="sealed_continuous_qualified_support_v1",
                    semantics=(
                        "unique qualified grid support; "
                        "zero is not a successful empty provider response"
                    ),
                ),
            )
        finally:
            db.close()
