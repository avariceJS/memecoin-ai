"""Execute a frozen independent-population learning qualification; no provider/runtime access."""

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from memecoin_experience import admission, evaluation, generations, representations
from memecoin_experience.records import split_plan
from memecoin_experience.schema import membership
from memecoin_experience.sources import inputs
from memecoin_experience.storage import (
    Snapshot,
    atomic_json,
    build,
    canonical,
    code_identity,
    digest,
    file_hash,
    verify_files,
)
from memecoin_population import corpus, diversity


def load(path):
    return json.loads(path.read_text())


def audit_config():
    return dict(
        entity_prefix="solana/pool/",
        knowledge="at_cutoff",
        axes=[["@entity"], ["birth_cohort"], ["lifecycle"], ["@period"]],
        time_bucket_ns=86400 * 10**9,
        selection=dict(salt="independent-birth-campaign-v1", numerator=1, denominator=1),
        enforcement="report",
        min_train_entities=1,
        max_entity_share=1,
        max_unknown_fraction=1,
        objective=diversity.OBJECTIVE,
    )


def dataset(campaign, root, role, protocol):
    report = corpus.source(campaign, root / (role + "-source"), role)
    ref = dict(
        kind="continuous", path=str(root / (role + "-source/source")), pin=report["source_pin"]
    )
    counts = Counter()
    schema = representations.ohlcv_schema(
        protocol["context"], tuple(protocol["horizons"]), stable=True
    )
    rows = representations.ohlcv_samples(
        inputs([ref], report["cutoff_ns"], counts, role=role), report["cutoff_ns"], schema
    )

    def counted():
        for row in rows:
            counts["context_ready_origins"] += 1
            yield row

    try:
        result = build(
            root / "store",
            counted(),
            sources=[ref],
            cutoff=report["cutoff_ns"],
            schema=schema,
            role=role,
            source_counts=counts,
        )
    except ValueError as exc:
        if str(exc) != "experience_empty_or_truncated_input" or counts["context_ready_origins"]:
            raise
        # No invented zero-row snapshot or successful model: report conversion failure explicitly.
        return None, report
    return result["snapshot"], report


def census(store, pin, split):
    snapshot = Snapshot(store, pin)
    counts = Counter()
    for i in range(len(snapshot.parts)):
        _, rows = snapshot.partition(i)
        for row in rows:
            role, mask = membership(row, split, snapshot.schema)
            counts[role] += 1
            counts[role + "_usable"] += int(any(mask))
    return dict(counts)


def identity_digest(path):
    h = hashlib.sha256()
    with path.open() as stream:
        for line in stream:
            row = json.loads(line)
            h.update(canonical({k: row[k] for k in ("id", "entity", "decision_ns", "target_mask")}))
    return h.hexdigest()


def compare(outputs, cases, *, draws=2000, seed=20261008):
    """Paired whole-base-mint bootstrap on identical evaluation origins, never IID windows."""
    import numpy as np

    entities = {c["pool"]: c["base_mint"] for c in cases}
    records = {}
    identities = {}
    for name, path in outputs.items():
        verify_files(path)
        report = load(path / "report.json")
        identities[name] = (
            report["binding"]["snapshot"],
            identity_digest(path / "predictions.jsonl"),
        )
        grouped = {}
        with (path / "strata.jsonl").open() as stream:
            for line in stream:
                row = json.loads(line)
                if not row["stratum"].startswith("pool:"):
                    continue
                pool = row["stratum"][5:]
                key = (row["target"], entities[pool])
                n, total = grouped.get(key, (0, 0.0))
                grouped[key] = (n + row["n"], total + row["pinball"] * row["n"])
        records[name] = grouped
    if len(set(identities.values())) > 1:
        raise ValueError("population_comparison_unpaired_origins")
    results = []
    for reference in ("narrow_fresh", "narrow_compute_control"):
        if reference not in records or "broad_fresh" not in records:
            continue
        left, right = records[reference], records["broad_fresh"]
        if set(left) != set(right) or any(left[k][0] != right[k][0] for k in left):
            raise ValueError("population_comparison_unpaired_support")
        for target in sorted({k[0] for k in left}):
            keys = sorted(k for k in left if k[0] == target)
            n = np.array([left[k][0] for k in keys], dtype=float)
            # Negative difference means lower broad-model pinball.
            delta = np.array([right[k][1] - left[k][1] for k in keys])
            rng = np.random.default_rng(seed)
            micro, macro = [], []
            for _ in range(draws):
                ix = rng.integers(0, len(keys), len(keys))
                micro.append(float(delta[ix].sum() / n[ix].sum()))
                macro.append(float((delta[ix] / n[ix]).mean()))
            results.append(
                dict(
                    reference=reference,
                    target=target,
                    supported_mints=len(keys),
                    origins=int(n.sum()),
                    micro_difference=float(delta.sum() / n.sum()),
                    equal_mint_difference=float((delta / n).mean()),
                    micro_ci95=np.quantile(micro, [0.025, 0.975]).tolist(),
                    equal_mint_ci95=np.quantile(macro, [0.025, 0.975]).tolist(),
                )
            )
    return dict(
        comparisons=results,
        draws=draws,
        seed=seed,
        uncertainty=(
            "paired observed-mint resampling; conditional on one trainer seed "
            "and fixed historical cohort; no multiple-comparison correction"
        ),
        execution="not_evaluated",
        production_eligible=False,
    )


def learning_protocol(plan, declaration):
    """A revised research recipe is a new declaration; acquisition history is immutable."""
    if declaration is None:
        return plan["protocol"]
    from memecoin_experience.learning import recipe
    from memecoin_experience.storage import PIN

    if (
        set(declaration) != {"version", "acquisition", "protocol", "reason", "evidence"}
        or declaration["version"] != "population-learning-plan-v1"
        or declaration["acquisition"] != digest(plan)
        or not isinstance(declaration["reason"], str)
        or not declaration["reason"].strip()
        or not isinstance(declaration["evidence"], list)
        or not declaration["evidence"]
        or any(not isinstance(p, str) or not PIN.fullmatch(p) for p in declaration["evidence"])
    ):
        raise ValueError("population_learning_declaration")
    protocol = declaration["protocol"]
    if (
        set(protocol) != set(plan["protocol"])
        or protocol["models"] != ["narrow_fresh", "broad_fresh", "narrow_compute_control"]
        or any(protocol[k] != plan["protocol"][k] for k in ("scope", "population_bias"))
        or not protocol["baseline_pools"]
        or len(set(protocol["baseline_pools"])) != len(protocol["baseline_pools"])
        or not set(protocol["baseline_pools"])
        <= {c["pool"] for c in plan["cases"] if c["role"] == "train"}
    ):
        raise ValueError("population_learning_protocol")
    split_plan(protocol["split"])
    representations.ohlcv_schema(protocol["context"], tuple(protocol["horizons"]), stable=True)
    admission.validate_policy(protocol["quality"])
    recipe(dict(protocol["config"], normalization=protocol["normalization"]))
    evaluation_window = protocol["evaluation"]
    if (
        set(evaluation_window) != {"start_s", "end_s"}
        or any(type(t) is not int for t in evaluation_window.values())
        or not protocol["split"]["test_end_s"]
        <= evaluation_window["start_s"]
        < evaluation_window["end_s"]
    ):
        raise ValueError("population_learning_evaluation_window")
    return protocol


def run(campaign, root, *, declaration=None):
    campaign, root = campaign.absolute(), root.absolute()
    root.mkdir(parents=True, exist_ok=True)
    plan = load(campaign / "plan.json")
    corpus.roles(plan)
    protocol = learning_protocol(plan, declaration)
    split = split_plan(protocol["split"])
    binding = dict(
        acquisition=digest(plan), code=code_identity(), harness=file_hash(Path(__file__))
    )
    if declaration is not None:
        binding["learning_plan"] = digest(declaration)
    if (campaign / "evaluation-access.json").exists():
        binding["prior_evaluation_access"] = load(campaign / "evaluation-access.json")
    atomic_json(root / "binding.json", binding)
    if declaration is not None:
        atomic_json(root / "learning-plan.json", declaration)
    populations = {
        role: corpus.population(root / "population", plan, role)
        for role in ("train", "research_evaluation")
    }
    raw, source = dataset(campaign, root, "train", protocol)
    store = root / "store"
    if raw is None:
        result = dict(
            binding=binding,
            populations=populations,
            train_source=source,
            training={
                name: dict(status="infeasible", reason="no_context_ready_experience")
                for name in protocol["models"]
            },
            evaluation="not_opened_no_candidates",
            production_eligible=False,
        )
        atomic_json(root / "result.json", result)
        return result
    broad = admission.create(store, raw, protocol["quality"])
    narrow = admission.create(
        store,
        raw,
        protocol["quality"],
        selection=dict(
            before_ns=split["train_end_s"] * 10**9, entities=sorted(protocol["baseline_pools"])
        ),
    )
    snapshots = dict(
        narrow_fresh=narrow["snapshot"],
        broad_fresh=broad["snapshot"],
        narrow_compute_control=narrow["snapshot"],
    )
    counts = {name: census(store, pin, split) for name, pin in snapshots.items()}
    n, b = (counts[k].get("train_usable", 0) for k in ("narrow_fresh", "broad_fresh"))
    control_epochs = math.ceil(protocol["config"]["epochs"] * b / n) if n else 0
    prepared = dict(
        binding=binding,
        populations=populations,
        train_source=source,
        raw=raw,
        broad=broad,
        narrow=narrow,
        counts=counts,
        compute_control_epochs=control_epochs,
    )
    atomic_json(root / "prepared.json", prepared)
    outcomes, artifacts = {}, {}
    for name in protocol["models"]:
        empty = [
            role
            for role in ("train", "validation", "test")
            if not counts[name].get(role + "_usable", 0)
        ]
        if empty:
            outcomes[name] = dict(
                status="infeasible", reason="experience_empty_role", empty_roles=empty
            )
            continue
        config = dict(protocol["config"], normalization=protocol["normalization"])
        if name == "narrow_compute_control":
            if not 1 <= control_epochs <= 500:
                outcomes[name] = dict(
                    status="infeasible", reason="registered_compute_control_epoch_bound"
                )
                continue
            config["epochs"] = control_epochs
        audit_path = root / (name + "-diversity")
        diversity.audit(
            store,
            snapshots[name],
            root / "population",
            populations["train"],
            audit_path,
            split=split,
            config=audit_config(),
        )
        ref = dict(path=str(audit_path), pin=file_hash(audit_path / "manifest.json"))
        try:
            outcome = generations.run(
                root / "generations",
                name=name,
                owner="independent-birth-campaign",
                store=store,
                snapshot=snapshots[name],
                split=split,
                config=config,
                diversity=ref,
            )
        except ValueError as exc:
            if str(exc) not in {"experience_empty_role", "experience_empty_training_horizon"}:
                raise
            outcomes[name] = dict(status="infeasible", reason=str(exc))
            continue
        outcomes[name] = {k: outcome[k] for k in ("status", "generation", "artifact", "job")}
        _, artifacts[name] = generations.get(root / "generations", outcome["generation"])
    atomic_json(root / "training.json", outcomes)
    if not artifacts:
        result = dict(
            binding=binding,
            training=outcomes,
            train_source=source,
            evaluation="not_opened_no_candidates",
            production_eligible=False,
        )
        atomic_json(root / "result.json", result)
        return result
    # Candidates/quality strata fixed before the evaluation-role source is opened.
    registered_path = root / "registered.json"
    if registered_path.exists():
        registered = load(registered_path)
    else:
        registered = {}
        for name, artifact in artifacts.items():
            registered[name] = {}
            for stratum in ("structural", "input_qualified"):
                result = evaluation.register(
                    root / "protocols",
                    artifact,
                    outcomes[name]["artifact"],
                    pools=[c["pool"] for c in plan["cases"] if c["role"] == "research_evaluation"],
                    **protocol["evaluation"],
                    input_admission=protocol["quality"] if stratum == "input_qualified" else None,
                )
                registered[name][stratum] = result["protocol"]
        atomic_json(registered_path, registered)
    evaluation_raw, evaluation_source = dataset(campaign, root, "research_evaluation", protocol)
    if evaluation_raw is None:
        result = dict(
            binding=binding,
            training=outcomes,
            evaluation_source=evaluation_source,
            evaluation="infeasible_no_context_ready_experience",
            production_eligible=False,
        )
        atomic_json(root / "result.json", result)
        return result
    qualified = admission.create(store, evaluation_raw, protocol["quality"])
    scores = {}
    for stratum, pin in (
        ("structural", evaluation_raw),
        ("input_qualified", qualified["snapshot"]),
    ):
        completed = {}
        scores[stratum] = {}
        for name, artifact in artifacts.items():
            output = root / "evaluation" / (name + "-" + stratum)
            try:
                result = evaluation.score(
                    root / "protocols",
                    registered[name][stratum],
                    store,
                    pin,
                    artifact,
                    output,
                    batch=512,
                )
            except ValueError as exc:
                if str(exc) != "experience_empty_evaluation_horizon":
                    raise
                scores[stratum][name] = dict(status="infeasible", reason=str(exc))
            else:
                scores[stratum][name] = result
                completed[name] = output
        scores[stratum]["paired"] = compare(completed, plan["cases"])
    result = dict(
        binding=binding,
        training=outcomes,
        evaluation_source=evaluation_source,
        evaluation_admission=qualified,
        scores=scores,
        scope=protocol["scope"],
        population_bias=protocol["population_bias"],
        recipe_fixed_before_acquisition=declaration is None,
        production_eligible=False,
    )
    atomic_json(root / "result.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--learning-plan", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.campaign,
                args.root,
                declaration=load(args.learning_plan) if args.learning_plan else None,
            ),
            sort_keys=True,
        )
    )
