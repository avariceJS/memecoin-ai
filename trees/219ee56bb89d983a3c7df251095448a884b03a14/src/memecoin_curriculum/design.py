"""Label-independent nested curriculum membership and shared chronological evaluation."""

import math
from collections import Counter, defaultdict

from memecoin_registry.contracts import digest

ARMS = ("recent", "full", "balanced")
FRACTIONS = (25, 50, 100)


def partition(bundle, bounds):
    parts = dict(train=[], recent=[], validation=[], test=[], purged=[])
    seen = set()
    for sample in bundle["samples"]:
        if sample["id"] in seen:
            raise ValueError("curriculum_duplicate")
        seen.add(sample["id"])
        cutoff = bundle["provenance"]["as_of_ns"]
        if sample["actual_available_ns"] > cutoff:
            raise ValueError("curriculum_future_input")
        if any(bundle["frames"][k]["available_ns"] > cutoff for k in sample["history"]):
            raise ValueError("curriculum_future_frame")
        complete = all(t["log_return"] is not None for t in sample["targets"])
        if complete and any(t["available_ns"] > cutoff for t in sample["targets"]):
            raise ValueError("curriculum_future_target")
        t, maturity = sample["event_close_s"], sample["maturity_s"]
        if not t < maturity:
            raise ValueError("curriculum_maturity")
        role = "purged"
        if complete:
            for name, start, end in (
                ("train", 0, bounds["train_end_s"]),
                ("validation", bounds["train_end_s"], bounds["validation_end_s"]),
                ("test", bounds["validation_end_s"], bounds["test_end_s"]),
            ):
                if start <= t < maturity < end:
                    role = name
        parts[role].append(sample)
        if role == "train" and t >= bounds["train_start_s"]:
            parts["recent"].append(sample)
    if any(len(parts[k]) < 16 for k in ("train", "recent", "validation", "test")):
        raise ValueError("curriculum_support")
    return parts


def nested(samples, fraction):
    if fraction not in FRACTIONS:
        raise ValueError("curriculum_fraction")
    buckets = defaultdict(list)
    for sample in samples:
        buckets[(sample["pool"], sample["event_close_s"] // 86400)].append(sample)
    chosen = set()
    for (pool, day), group in buckets.items():
        ordered = sorted(group, key=lambda s: digest([7, pool, day, s["id"]]))
        chosen.update(s["id"] for s in ordered[: math.ceil(len(group) * fraction / 100)])
    return [s for s in samples if s["id"] in chosen]


def weights(samples, balanced):
    if not samples:
        raise ValueError("curriculum_empty")
    if not balanced:
        return [1 / len(samples)] * len(samples)
    counts = Counter((s["pool"], s["event_close_s"] // 86400) for s in samples)
    days = Counter(pool for pool, _ in counts)
    return [
        1 / (len(days) * days[s["pool"]] * counts[s["pool"], s["event_close_s"] // 86400])
        for s in samples
    ]


def weighted_quantile(values, probabilities, level):
    if (
        len(values) != len(probabilities)
        or not values
        or not 0 <= level <= 1
        or any(not math.isfinite(v) for v in values)
        or any(not math.isfinite(w) or w <= 0 for w in probabilities)
    ):
        raise ValueError("curriculum_quantile")
    ordered = sorted(zip(values, probabilities, strict=True))
    threshold, mass = level * math.fsum(probabilities), 0.0
    for value, weight in ordered:
        mass += weight
        if mass >= threshold:
            return value
    return ordered[-1][0]


def support(samples, probabilities):
    pools, mass = Counter(), Counter()
    for s, w in zip(samples, probabilities, strict=True):
        pools[s["pool"]] += 1
        mass[s["pool"]] += w
    return dict(
        decisions=len(samples),
        pools=len(pools),
        days=len({s["event_close_s"] // 86400 for s in samples}),
        pool_days=len({(s["pool"], s["event_close_s"] // 86400) for s in samples}),
        per_pool=dict(pools),
        pool_probability=dict(mass),
        effective_pools=1 / sum(w * w for w in mass.values()),
        effective_decisions=1 / sum(w * w for w in probabilities),
        first_s=min(s["event_close_s"] for s in samples),
        last_s=max(s["event_close_s"] for s in samples),
        membership=digest([s["id"] for s in samples]),
        weight_sha=digest(probabilities),
    )


def experiments(bundle, bounds):
    parts = partition(bundle, bounds)
    for arm in ARMS:
        for fraction in FRACTIONS:
            train = nested(parts["recent"] if arm == "recent" else parts["train"], fraction)
            probability = weights(train, arm == "balanced")
            yield dict(
                name=f"{arm}-{fraction}",
                arm=arm,
                fraction=fraction,
                train=train,
                probability=probability,
                support=support(train, probability),
                validation=parts["validation"],
                test=parts["test"],
            )
