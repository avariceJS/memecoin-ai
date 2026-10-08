"""Opt-in stable OHLCV arithmetic; preserves ordinary finite-ratio results."""

import math


def log_ratio(numerator, denominator):
    ratio = numerator / denominator
    return math.log(ratio) if 0 < ratio < math.inf else math.log(numerator) - math.log(denominator)


def features(history):
    o, high, low, c, v = history[-1]["ohlcv"]
    closes = [r["ohlcv"][3] for r in history]
    changes = [log_ratio(b, a) for a, b in zip(closes[:-1], closes[1:], strict=True)]
    volumes = [r["ohlcv"][4] for r in history[:-1]]
    total = sum(volumes)
    average = (
        total / len(volumes)
        if volumes and math.isfinite(total)
        else math.fsum(x / len(volumes) for x in volumes)
        if volumes
        else None
    )
    return [
        changes[-1] if changes else None,
        log_ratio(c, o),
        log_ratio(high, low),
        math.log1p(v),
        log_ratio(high, max(c, o)),
        log_ratio(min(c, o), low),
        log_ratio(v + 1, average + 1) if volumes else None,
        log_ratio(c, closes[-4]) if len(closes) >= 4 else None,
        log_ratio(c, closes[-7]) if len(closes) >= 7 else None,
        math.sqrt(sum(r * r for r in changes) / len(changes)) if changes else None,
        sum(r > 0 for r in changes) / len(changes) if changes else None,
        float(v == 0),
    ]
