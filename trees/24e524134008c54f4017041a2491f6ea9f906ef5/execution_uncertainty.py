"""Offline conditional cost/delay sensitivity, never a calibrated PnL or online filter."""

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from memecoin_ai.domain import ValidationError, digest
from memecoin_ai.execution_targets import verify_execution_targets
from memecoin_ai.prospective import hashes, read, write

COST_BPS = [0, 5, 10, 25, 50, 100]
PARTITIONS = ("calibration", "engineering_evaluation")


def summary(values):
    return dict(
        count=len(values),
        minimum=min(values) if values else None,
        median=statistics.median(values) if values else None,
        maximum=max(values) if values else None,
    )


def positive(row, cost):
    # Integer comparison, including equality=not positive; no cross-asset sums.
    return row["pnl_before_network_costs"] * 10000 > row["gross_input"] * cost


def analyze(rows):
    groups = defaultdict(list)
    pairs = defaultdict(dict)
    eligible = [
        r for r in rows if r["status"] == "conditional_roundtrip" and r["partition"] in PARTITIONS
    ]
    for row in eligible:
        if row["gross_input"] <= 0 or row["net_pnl"] is not None:
            raise ValidationError("uncertainty audit requires positive input and unknown net PnL")
        key = (row["pool_id"], row["input_index"], row["size_ppm"], row["partition"])
        groups[key].append(row)
        pair = pairs[(row["sample_id"], *key)]
        delay = row["assumed_delay_ns"]
        if delay in pair:
            raise ValidationError("duplicate scenario in uncertainty audit")
        pair[delay] = row
    group_reports = []
    for key, subset in sorted(groups.items()):
        group_reports.append(
            dict(
                pool_id=key[0],
                input_index=key[1],
                size_ppm=key[2],
                partition=key[3],
                conditional_rows=len(subset),
                distinct_decisions=len({r["sample_id"] for r in subset}),
                break_even_extra_cost_bps=summary(
                    [r["pnl_before_network_costs"] / r["gross_input"] * 10000 for r in subset]
                ),
                cost_sensitivity=[
                    dict(
                        assumed_extra_roundtrip_cost_bps=c,
                        positive_conditional_rows=sum(positive(r, c) for r in subset),
                    )
                    for c in COST_BPS
                ],
            )
        )
    paired = [(p[0], p[5_000_000_000]) for p in pairs.values() if set(p) == {0, 5_000_000_000}]
    pair_reports = []
    for partition in PARTITIONS:
        subset = [(a, b) for a, b in paired if a["partition"] == partition]
        pair_reports.append(
            dict(
                partition=partition,
                paired_scenarios=len(subset),
                distinct_decisions=len({a["sample_id"] for a, b in subset}),
                delayed_minus_zero_return_bps=summary(
                    [
                        (
                            b["pnl_before_network_costs"] / b["gross_input"]
                            - a["pnl_before_network_costs"] / a["gross_input"]
                        )
                        * 10000
                        for a, b in subset
                    ]
                ),
                sign_flips=sum(positive(a, 0) != positive(b, 0) for a, b in subset),
                cost_sensitivity=[
                    dict(
                        assumed_extra_roundtrip_cost_bps=c,
                        zero_delay_positive=sum(positive(a, c) for a, b in subset),
                        delayed_positive=sum(positive(b, c) for a, b in subset),
                        both_positive=sum(positive(a, c) and positive(b, c) for a, b in subset),
                    )
                    for c in COST_BPS
                ],
            )
        )
    return dict(
        kind="offline_conditional_execution_uncertainty",
        scenario_rows=len(rows),
        statuses=dict(sorted(Counter(r["status"] for r in rows).items())),
        partitions=dict(sorted(Counter(r["partition"] for r in rows).items())),
        eligible_conditional_rows=len(eligible),
        groups=group_reports,
        paired_delay=pair_reports,
        unpaired_eligible_scenario_rows=len(eligible) - 2 * len(paired),
        assumptions=dict(
            extra_roundtrip_cost_bps=COST_BPS,
            calibrated=False,
            inclusion_probability=None,
            network_costs=None,
            rent=None,
            fx=None,
            persistent_own_impact=False,
        ),
        limitations=[
            "conditional fills only; not realized PnL or an edge test",
            "censored/unfilled excluded from numeric sensitivity; never zero-filled",
            "delay pairing requires identical decision/pool/direction/size/partition",
            "overlapping decisions and variants are not independent",
            "input-asset bps are hypothetical costs, not native fee conversion",
            "offline audit only, never feature selection or online admissibility",
        ],
        net_pnl=None,
        training_ready=False,
        execution_qualified=False,
        seed=None,
    )


def reproduce(parent, targets):
    verify_execution_targets(parent, targets)
    rows = [json.loads(line) for line in (targets / "targets.jsonl").read_text().splitlines()]
    return analyze(rows)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["export", "verify"])
    p.add_argument("parent", type=Path)
    p.add_argument("targets", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    result = reproduce(a.parent, a.targets)
    binding = dict(
        target_manifest_sha256=digest((a.targets / "manifest.json").read_bytes()),
        tool_sha256=digest(Path(__file__).read_bytes()),
    )
    if a.action == "export":
        a.output.mkdir(parents=True, exist_ok=False)
        write(a.output / "report.json", result)
        write(a.output / "provenance.json", binding)
        (a.output / "audit_source.py").write_bytes(Path(__file__).read_bytes())
        write(a.output / "manifest.json", hashes(a.output))
    else:
        actual = hashes(a.output)
        actual.pop("manifest.json")
        if (
            actual != read(a.output / "manifest.json")
            or binding != read(a.output / "provenance.json")
            or result != read(a.output / "report.json")
        ):
            raise ValidationError("uncertainty artifact reproduction/checksum differs")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
