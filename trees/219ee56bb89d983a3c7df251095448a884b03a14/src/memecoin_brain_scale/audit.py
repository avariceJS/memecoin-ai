"""Immutable capture selection, first-observed merge, coverage and matured paired shadow."""

from __future__ import annotations

import math
import random
import time
from collections import Counter, defaultdict
from pathlib import Path

from memecoin_brain.data import build, save_dataset
from memecoin_brain.shadow import evaluate_prediction
from memecoin_brain.source import BAR, NS, bars
from memecoin_ml.data import read, sha, write
from memecoin_registry.contracts import seal, verify


def merge(captures: list[tuple[Path, str]], as_of_ns: int) -> tuple[list, dict]:
    first, revisions, inventory = {}, [], []
    ordered = sorted(
        captures, key=lambda c: (read(c[0] / "receipt.json")["available_ns"], str(c[0]))
    )
    for path, pool in ordered:
        verify(path)
        receipt, request = read(path / "receipt.json"), read(path / "request.json")
        if (
            not request["started_ns"]
            <= receipt["received_ns"]
            <= receipt["available_ns"]
            <= as_of_ns
        ):
            raise ValueError("capture_after_snapshot_or_bad_clock")
        inventory.append(
            dict(path=str(path.resolve()), pool=pool, manifest_sha=sha(path / "manifest.json"))
        )
        for row in bars(path, pool):
            key = (pool, row["open_s"])
            if key not in first:
                first[key] = row
            elif first[key]["ohlcv"] != row["ohlcv"]:
                revisions.append(
                    dict(
                        pool=pool,
                        open_s=row["open_s"],
                        observed_ns=row["available_ns"],
                        original=first[key]["ohlcv"],
                        revised=row["ohlcv"],
                        original_sha=first[key]["capture_sha"],
                        revised_sha=row["capture_sha"],
                    )
                )
    return sorted(first.values(), key=lambda r: (r["pool"], r["open_s"])), dict(
        as_of_ns=as_of_ns,
        captures=inventory,
        revisions=revisions,
        first_observed_versions=True,
        historical_online_availability_known=False,
    )


def select_captures(roots: list[Path], pools: set[str], as_of_ns: int, quarantine=None) -> list:
    captures = []
    for root in roots:
        for path in sorted((root / "requests").iterdir()):
            if not (path / "manifest.json").exists() or not (path / "receipt.json").exists():
                continue
            request, receipt = read(path / "request.json"), read(path / "receipt.json")
            if request["lane"] == "discovery" or receipt["available_ns"] > as_of_ns:
                continue
            pool = request["url"].split("/pools/")[1].split("/")[0]
            if pool not in pools:
                raise ValueError("unadmitted_pool")
            verify(path)
            try:
                bars(path, pool)
            except ValueError as exc:
                if str(exc) not in (
                    "duplicate_bar_in_response",
                    "bar_schema_or_alignment",
                    "invalid_OHLCV",
                ):
                    raise
                if quarantine is None:
                    raise
                quarantine.append(
                    dict(
                        path=str(path.resolve()),
                        reason=str(exc),
                        manifest_sha=sha(path / "manifest.json"),
                    )
                )
                continue
            captures.append((path, pool))
    return captures


def coverage(rows: list, universe: dict, plan: dict, provenance: dict) -> dict:
    counts, details = Counter(), []
    floors = {t["pool"]: t["floor"] for t in plan["tasks"]}
    for u in universe["pools"]:
        rs = [r for r in rows if r["pool"] == u["pool"]]
        times = sorted(r["open_s"] for r in rs)
        floor, end = floors[u["pool"]], plan["anchor_s"]
        got = sum(floor <= t < end for t in times)
        expected = (end - floor) // BAR
        gaps = [
            (b - a) // BAR - 1 for a, b in zip(times[:-1], times[1:], strict=True) if b - a > BAR
        ]
        details.append(
            dict(
                pool=u["pool"],
                age_bucket=u["age_bucket"],
                dex=u["dex"],
                bars=len(rs),
                first_open_s=min(times) if times else None,
                last_open_s=max(times) if times else None,
                requested_from_s=floor,
                requested_to_s=end,
                calendar_slots=expected,
                observed_slots=got,
                observed_fraction=got / expected if expected else None,
                missing_slots=expected - got,
                internal_gap_count=len(gaps),
                internal_missing_bars=sum(gaps),
                max_gap_bars=max(gaps, default=0),
                unique_event_days=len({t // 86400 for t in times}),
                receipt_days=len({r["available_ns"] // (86400 * NS) for r in rs}),
                zero_volume_bars=sum(r["ohlcv"][4] == 0 for r in rs),
            )
        )
        counts[str(u["age_bucket"])] += len(rs)
    revisions = provenance["revisions"]
    changes = [abs(math.log(r["revised"][3] / r["original"][3])) for r in revisions]
    revised_keys = {(r["pool"], r["open_s"]) for r in revisions}
    return dict(
        bars=len(rows),
        pools=len(details),
        per_pool=details,
        bars_by_admission_age=dict(counts),
        pool_row_hhi=sum((p["bars"] / len(rows)) ** 2 for p in details) if rows else None,
        largest_pool_row_fraction=max((p["bars"] / len(rows) for p in details), default=0),
        event_UTC_hours=dict(Counter(str(r["open_s"] // 3600) for r in rows)),
        receipt_UTC_hours=dict(Counter(str(r["available_ns"] // (3600 * NS)) for r in rows)),
        revision_observations=len(revisions),
        revised_unique_keys=len(revised_keys),
        close_revision_observations=sum(v > 0 for v in changes),
        max_absolute_log_close_revision=max(changes, default=0),
        missing_interval_cause_known=False,
        provider_finality=False,
        sampling_bias=dict(
            current_ranking=True,
            survivorship=True,
            deaths_unseen=True,
            liquidity_filtered=True,
            age_balanced_pools_not_rows=True,
        ),
    )


def paired_summary(records: list[dict], *, draws: int = 200) -> dict:
    def metric(name, weights=None):
        pools = defaultdict(lambda: [0.0, 0])
        for r in records:
            w = 1 if weights is None else weights.get(r["block"], 0)
            pools[r["pool"]][0] += r["losses"][name] * w
            pools[r["pool"]][1] += w
        values = [a / n for a, n in pools.values() if n]
        return sum(values) / len(values) if values else None

    if not records:
        return dict(variants=0, blocks=0, ci=None)
    names = sorted(records[0]["losses"])
    if any(sorted(r["losses"]) != names for r in records):
        raise ValueError("unpaired_candidates")
    blocks = sorted({r["block"] for r in records})
    result = dict(
        variants=len(records),
        pools=len({r["pool"] for r in records}),
        blocks=len(blocks),
        pool_macro={n: metric(n) for n in names},
        pooled={n: sum(r["losses"][n] for r in records) / len(records) for n in names},
        ci=None,
        inference="exploratory; temporal1h clusters shared across all pools",
    )
    if len(blocks) >= 8:
        rng = random.Random(20261004)
        diffs = defaultdict(list)
        for _ in range(draws):
            weights = Counter(rng.choices(blocks, k=len(blocks)))
            base = metric("empirical", weights)
            if base and base > 0:
                for n in names:
                    if n != "empirical":
                        diffs[n].append(1 - metric(n, weights) / base)
        result["ci"] = {
            n: [sorted(v)[int(0.025 * (len(v) - 1))], sorted(v)[int(0.975 * (len(v) - 1))]]
            for n, v in diffs.items()
        }
    return result


def shadow_snapshot(
    primary: Path,
    output: Path,
    rows: list,
    as_of_ns: int,
    *,
    imported_model_roots: tuple[Path, ...] = (),
) -> dict:
    output.mkdir(exist_ok=False)
    records, inventory, pending = [], [], 0
    recovered = 0
    statuses = Counter()
    models = {}
    for path in sorted((primary / "shadow/predictions").glob("*.json")):
        p = read(path)
        if p["predicted_ns"] > as_of_ns:
            continue
        if p["model_sha"] not in models:
            model = Path(p["model_path"])
            allowed = [(primary / "models").resolve()]
            if imported_model_roots:
                activation = read(primary / "activation.json")
                if sha(primary / "import.json") != activation["import_sha"]:
                    raise ValueError("import_binding_changed")
                declared = read(primary / "import.json").get("model_roots", [])
                if [str(p.resolve()) for p in imported_model_roots] != declared:
                    raise ValueError("undeclared_imported_models")
                allowed.extend(imported_model_roots)
            if (primary / "import.json").exists():
                activation = read(primary / "activation.json")
                if sha(primary / "import.json") != activation["import_sha"]:
                    raise ValueError("import_binding_changed")
                allowed.append(Path(read(primary / "import.json")["primary"]) / "models")
            if not any(model.resolve().is_relative_to(p) for p in allowed):
                raise ValueError("model_outside_Brain_registry")
            verify(model)
            if sha(model / "manifest.json") != p["model_sha"]:
                raise ValueError("shadow_model_sha")
            models[p["model_sha"]] = read(model / "model-card.json")
        card = models[p["model_sha"]]
        if not (
            card["training_as_of_ns"] <= card["finished_ns"] <= p["predicted_ns"]
            and p["input_available_ns"] <= p["predicted_ns"]
            and p["predicted_ns"] < (p["event_close_s"] + p["horizon"] * BAR) * NS
        ):
            raise ValueError("shadow_temporal_order")
        item = dict(prediction=p, prediction_sha=sha(path), source=str(path.resolve()))
        op = primary / "shadow/outcomes" / path.name
        o = read(op) if op.exists() else None
        stamp = o.get("scored_ns", o.get("observed_ns")) if o else None
        lane = "published"
        if stamp is None or stamp > as_of_ns:
            pending += 1
            value = evaluate_prediction(p, rows, now_ns=as_of_ns)
            if value is not None and value["status"] == "scored":
                # Newly computed outcome, actual offline scoring time, never backdated publication.
                o = dict(prediction_sha=sha(path), **value)
                stamp = as_of_ns
                lane = "recovery_scoring"
                recovered += 1
                item["recovery_outcome"] = o
        if stamp is not None and stamp <= as_of_ns:
            if o["prediction_sha"] != sha(path):
                raise ValueError("shadow_prediction_sha")
            available = [r for r in rows if r["available_ns"] <= stamp]
            expected = evaluate_prediction(p, available, now_ns=stamp)
            if o != dict(prediction_sha=sha(path), **expected):
                raise ValueError("shadow_raw_outcome_reproduction")
            if lane == "published":
                item.update(outcome=o, outcome_sha=sha(op))
            statuses[o["status"]] += 1
            if o["status"] == "scored":
                if sorted(o["candidate_metrics"]) != ["empirical", "gru", "linear"]:
                    raise ValueError("unpaired_shadow")
                records.append(
                    dict(
                        pool=p["pool"],
                        horizon=p["horizon"],
                        model=p["model_sha"],
                        scoring_lane=lane,
                        block=p["event_close_s"] // 3600,
                        losses={k: v["pinball"] for k, v in o["candidate_metrics"].items()},
                    )
                )
        inventory.append(item)
    report = dict(
        as_of_ns=as_of_ns,
        predictions=len(inventory),
        pending=pending,
        recovered_mature_outcomes=recovered,
        pending_after_offline_recovery=pending - recovered,
        statuses=dict(statuses),
        overall=paired_summary(records),
        published_only=paired_summary([r for r in records if r["scoring_lane"] == "published"]),
        recovery_only=paired_summary(
            [r for r in records if r["scoring_lane"] == "recovery_scoring"]
        ),
        by_horizon={
            str(h): paired_summary([r for r in records if r["horizon"] == h]) for h in (1, 3)
        },
        by_model={m: paired_summary([r for r in records if r["model"] == m]) for m in models},
        by_UTC_hour={
            str(b): paired_summary([r for r in records if r["block"] == b])
            for b in sorted({r["block"] for r in records})
        },
        by_pool={
            p: paired_summary([r for r in records if r["pool"] == p])
            for p in sorted({r["pool"] for r in records})
        },
        future_outcomes_only=True,
        raw_reproduction_exact=True,
        architecture_selected=False,
    )
    write(output / "inventory.json", inventory)
    write(output / "report.json", report)
    seal(output)
    return report


def snapshot(primary: Path, history: Path, output: Path) -> dict:
    output.mkdir(exist_ok=False)
    as_of = time.time_ns()
    universe, plan = read(primary / "universe.json"), read(history / "plan.json")
    if sha(primary / "universe.json") != plan["primary_universe_sha"]:
        raise ValueError("universe_changed")
    pools = {u["pool"] for u in universe["pools"]}
    roots = [primary, history]
    if plan.get("parent"):
        roots.append(Path(plan["parent"]["path"]))
    quarantine = []
    captures = select_captures(roots, pools, as_of, quarantine)
    rows, provenance = merge(captures, as_of)
    baseline_rows, baseline_prov = merge(
        [c for c in captures if c[0].is_relative_to(primary)], as_of
    )
    report = dict(
        before=coverage(baseline_rows, universe, plan, baseline_prov),
        after=coverage(rows, universe, plan, provenance),
    )
    report["quarantine"] = quarantine
    # Keep all raw and merge rows. Common 10-day window for the new curve dataset only.
    filtered = [r for r in rows if r["open_s"] >= plan["anchor_s"] - 10 * 86400]
    provenance.update(
        universe_sha=sha(primary / "universe.json"), window_floor_s=plan["anchor_s"] - 10 * 86400
    )
    bundle = build(filtered, provenance)
    report["dataset"] = dict(
        samples=len(bundle["samples"]),
        bars=len(filtered),
        censored_targets=sum(
            t["log_return"] is None for s in bundle["samples"] for t in s["targets"]
        ),
    )
    write(output / "rows.json", rows)
    write(output / "coverage.json", report)
    write(output / "universe.json", universe)
    save_dataset(output / "dataset", bundle)
    shadow_snapshot(primary, output / "shadow", baseline_rows, as_of)
    seal(output)
    return report


def safe_corpus(root: Path, as_of_ns: int) -> tuple[list, dict]:
    universe = read(root / "universe.json")
    if universe["admitted_ns"] > as_of_ns:
        raise ValueError("future_universe")
    quarantined = []
    captures = select_captures(
        [root], {p["pool"] for p in universe["pools"]}, as_of_ns, quarantined
    )
    rows, provenance = merge(captures, as_of_ns)
    provenance.update(universe_sha=sha(root / "universe.json"), quarantined=quarantined)
    return rows, provenance
