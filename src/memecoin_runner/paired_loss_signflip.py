"""Exact sign-flip uncertainty for explicit paired losses.


Each pair contributes right_loss - left_loss. The diagnostic enumerates
all sign assignments to these caller-paired differences and counts assignments
whose absolute mean is at least the observed absolute mean. This two-sided
randomization tail is interpretable under exchangeability of the paired
differences under a zero-centered null; it does not make an acceptance or
trading decision. Optional less/greater alternatives compare signed sums to the
observed signed sum using exact integer sums of the represented binary
floating-point differences. Input is bounded to 20 pairs (at most 1,048,576
assignments). Input conversion and loss subtraction retain their normal
floating-point representation limits.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from typing import Any


_TOP_LEVEL_KEYS = {"pairs"}
_OPTIONAL_TOP_LEVEL_KEYS = {"alternative"}
_PAIR_KEYS = {"id", "left_loss", "right_loss"}
_ALTERNATIVES = {"two-sided", "less", "greater"}
_MAX_PAIRS = 20


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("payload must contain finite JSON values") from exc


def _loss(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a nonnegative finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        number = math.inf
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} must be a nonnegative finite number")
    return number


def _sign_flip_tail_count(differences: list[float], observed_sum: float) -> int:
    """Count two-sided tail assignments, including floating-point ties."""
    support = len(differences)
    observed_abs = abs(observed_sum)
    tail_threshold = observed_abs - 8 * math.ulp(observed_abs)
    count = 0
    for assignment in range(1 << support):
        null_sum = math.fsum(
            difference if assignment & (1 << index) else -difference
            for index, difference in enumerate(differences)
        )
        if abs(null_sum) >= tail_threshold:
            count += 1
    return count


def _directional_tail_count(
    differences: list[float], alternative: str
) -> int:
    """Count inclusive directional tails using exact sums of represented floats."""
    ratios = [difference.as_integer_ratio() for difference in differences]
    denominator = max((denominator for _, denominator in ratios), default=1)
    exact_differences = [numerator * (denominator // divisor) for numerator, divisor in ratios]
    observed_sum = sum(exact_differences)
    count = 0
    for assignment in range(1 << len(exact_differences)):
        null_sum = sum(
            difference if assignment & (1 << index) else -difference
            for index, difference in enumerate(exact_differences)
        )
        if (alternative == "less" and null_sum <= observed_sum) or (
            alternative == "greater" and null_sum >= observed_sum
        ):
            count += 1
    return count


def analyze(payload: dict) -> dict:
    """Validate strict paired-loss input and return an exact diagnostic envelope."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    if not _TOP_LEVEL_KEYS.issubset(payload) or set(payload) - _TOP_LEVEL_KEYS - _OPTIONAL_TOP_LEVEL_KEYS:
        raise ValueError("payload must contain exactly pairs")
    explicit_alternative = "alternative" in payload
    alternative = payload.get("alternative", "two-sided")
    if not isinstance(alternative, str) or alternative not in _ALTERNATIVES:
        raise ValueError("alternative must be one of: two-sided, less, greater")
    pairs = payload["pairs"]
    if not isinstance(pairs, list):
        raise ValueError("pairs must be a list")
    if len(pairs) > _MAX_PAIRS:
        raise ValueError("pairs must contain at most 20 items")

    canonical = _canonical_json(payload)
    seen_ids: set[str] = set()
    differences: list[float] = []
    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict) or set(pair) != _PAIR_KEYS:
            raise ValueError(f"pairs[{index}] must contain exactly id, left_loss, right_loss")
        identity = pair["id"]
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError(f"pairs[{index}].id must be a nonempty string")
        if identity in seen_ids:
            raise ValueError("pair ids must be unique")
        seen_ids.add(identity)
        left_loss = _loss(pair["left_loss"], f"pairs[{index}].left_loss")
        right_loss = _loss(pair["right_loss"], f"pairs[{index}].right_loss")
        differences.append(right_loss - left_loss)

    support = len(differences)
    assignments = 1 << support
    if support:
        # Scaling prevents overflow in the observed sum and every null sum.
        scale = max(abs(value) for value in differences)
        scaled = [value / scale for value in differences] if scale else differences
        observed_scaled_sum = math.fsum(scaled)
        observed_mean = (observed_scaled_sum / support) * scale if scale else 0.0
        if alternative == "two-sided":
            tail_count = _sign_flip_tail_count(scaled, observed_scaled_sum)
        else:
            tail_count = _directional_tail_count(differences, alternative)
        p_value = tail_count / assignments
    else:
        observed_mean = None
        tail_count = 0
        p_value = None

    summary = {
        "pairs": support,
        "observed_mean_signed_difference": observed_mean,
        "sign_flip_assignments": assignments,
    }
    if alternative == "two-sided":
        summary["exact_two_sided_tail_count"] = tail_count
        summary["exact_two_sided_p_value"] = p_value
        if explicit_alternative:
            summary["alternative"] = alternative
    else:
        summary["alternative"] = alternative
        summary[f"exact_{alternative}_tail_count"] = tail_count
        summary[f"exact_{alternative}_p_value"] = p_value

    limitations = [
        "The exact sign-flip tail is inferential under exchangeability of caller-paired differences under a zero-centered null.",
        "All 2^n sign assignments are enumerated; at most 20 explicit pairs are accepted.",
        "This diagnostic does not establish model quality or make an acceptance or trading decision.",
        "Pairs and losses are supplied by the caller; interpretation assumes a common comparable evaluation protocol.",
    ]
    if explicit_alternative:
        direction = {
            "two-sided": "The declared two-sided alternative tests for a nonzero signed mean difference.",
            "less": "The declared less alternative tests whether right_loss - left_loss is lower than zero; a negative signed mean favors lower right losses.",
            "greater": "The declared greater alternative tests whether right_loss - left_loss is higher than zero; a positive signed mean favors higher right losses.",
        }[alternative]
        limitations.extend([
            direction,
            "The alternative must be selected before inspecting these paired outcomes for confirmatory interpretation; choosing a direction after observing results is not accounted for by this exact tail.",
            "Directional tails compare exact sums of the binary floating-point values produced by each right_loss - left_loss subtraction; input conversion and subtraction retain normal floating-point representation limits.",
        ])

    return {
        "schema": "brain-research-diagnostic.v1",
        "kind": "paired_loss_signflip",
        "input_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "summary": summary,
        "details": [],
        "limitations": limitations,
    }


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def main() -> int:
    try:
        payload = json.load(sys.stdin, parse_constant=_reject_constant)
        result = analyze(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"paired_loss_signflip: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
