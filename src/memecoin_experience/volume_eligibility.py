"""Read-only prior-volume eligibility summary over authenticated candle observations."""

from collections import Counter

from . import observations, sparse

_STREAM = "qualified_price_bar"
_PROFILE = "candle-volume-context-v1"


def _count(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f"volume_eligibility_{name}")
    return int(value)


def _fraction(eligible, observed):
    return eligible / observed if observed else None


def _counts(counter):
    observed = counter["eligible"] + counter["insufficient_prior"] + counter["gapped_history"]
    return {
        "observed": observed,
        "eligible": counter["eligible"],
        "insufficient_prior": counter["insufficient_prior"],
        "gapped_history": counter["gapped_history"],
        "eligibility_fraction": _fraction(counter["eligible"], observed),
    }


def _validate_row(row, indices):
    values = row["values"]
    prior = _count(values[indices["prior_volume_count"]], "prior_count")
    context = _count(values[indices["context_count"]], "context_count")
    contiguous = values[indices["context_contiguous"]]
    max_gap = values[indices["context_max_gap_s"]]
    span = values[indices["context_span_s"]]
    delta = values[indices["volume_log1p_delta"]]

    if not 1 <= context <= 12 or prior != context - 1:
        raise ValueError("volume_eligibility_count_consistency")
    if context == 1:
        if type(span) is not int or span != 0 or max_gap is not None or contiguous is not None:
            raise ValueError("volume_eligibility_singleton_context")
    else:
        if (
            type(span) is not int
            or span <= 0
            or span > 3600
            or type(max_gap) is not int
            or max_gap <= 0
            or max_gap < 300
            or span < 300 * (context - 1)
            or span % 300 != 0
            or max_gap % 300 != 0
            or max_gap > span
            or span < max_gap + 300 * (context - 2)
            or span > max_gap * (context - 1)
            or type(contiguous) not in (int, float)
            or contiguous not in (0, 1)
        ):
            raise ValueError("volume_eligibility_gap_consistency")
        if contiguous == 1 and (max_gap != 300 or span != 300 * (context - 1)):
            raise ValueError("volume_eligibility_contiguity_consistency")
        if contiguous == 0 and max_gap == 300:
            raise ValueError("volume_eligibility_contiguity_consistency")

    eligible = prior >= 2 and contiguous == 1
    expected_delta = eligible
    if (delta is None) == expected_delta:
        raise ValueError("volume_eligibility_delta_missingness")
    return "eligible" if eligible else "insufficient_prior" if prior < 2 else "gapped_history"


def validate_source_rows(source):
    """Reject contradictory volume states anywhere in an authenticated source."""
    stream = source.schema["streams"][_STREAM]
    names = [feature["name"] for feature in stream["features"]]
    indices = {name: names.index(name) for name in (
        "context_count", "context_span_s", "context_max_gap_s", "context_contiguous",
        "prior_volume_count", "volume_log1p_delta",
    )}
    for row in source.rows():
        if row["kind"] == "event" and row["stream"] == _STREAM:
            _validate_row(row, indices)


def inspect(store, pin, *, role="train"):
    """Summarize prior-volume eligibility from a sealed, authenticated Source."""
    source = observations.Source(store, pin, role=role)
    descriptor = sparse._observation_profile(
        source, _PROFILE, {"stream": _STREAM, "relative_features": []}
    )
    names = [feature["name"] for feature in descriptor["features"]]
    indices = {name: names.index(name) for name in (
        "context_count", "context_span_s", "context_max_gap_s", "context_contiguous",
        "prior_volume_count", "volume_log1p_delta",
    )}
    total = Counter()
    entities = {}
    for row in source.rows():
        if row["kind"] != "event" or row["stream"] != _STREAM:
            continue
        category = _validate_row(row, indices)
        total[category] += 1
        entities.setdefault(row["entity"], Counter())[category] += 1
    return {
        "schema": "candle-volume-eligibility-v1",
        "source_pin": descriptor["source_pin"],
        "continuous_source_pin": descriptor["continuous_source_pin"],
        "profile": {"name": _PROFILE, "version": 1},
        "limitation": "historical_replay",
        "denominator": _STREAM,
        "global": _counts(total),
        "entities": {entity: _counts(entities[entity]) for entity in sorted(entities)},
    }
