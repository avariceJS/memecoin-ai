"""Shared provider budget, distinct live deadlines and opportunistic background work."""

from memecoin_ml.data import read, sha

NS = 10**9


def choose(s, universe, now, c):
    hot = set(c["hot_pools"])
    day = now // (86400 * NS)

    def due(p, q):
        probes = q["probes"] if q["probe_day"] == day else 0
        return q["next_ns"] <= now and (q["status"] == "active" or probes < 2)

    pending = [(q["next_ns"], p) for p, q in s["pools"].items() if p in hot and due(p, q)]
    if pending:
        return dict(
            pool=min(pending)[1],
            lane="prospective",
            before=None,
            limit=100,
            interval_s=c["hot_interval_s"],
            role="hot",
        )
    next_hot = min(
        (q["next_ns"] for p, q in s["pools"].items() if p in hot and q["status"] == "active"),
        default=now + 86400 * NS,
    )
    if (
        next_hot - now < 35 * NS
        or now < s.get("background_next_ns", 0)
        or s.get("background_attempts", 0) >= c["max_background_requests"]
    ):
        return None
    cold = [(q["next_ns"], p) for p, q in s["pools"].items() if p not in hot and due(p, q)]
    history = [
        (s["pools"][u["pool"]]["history_pages"], u["pool"])
        for u in universe
        if s["pools"][u["pool"]]["status"] == "active"
        and not s["pools"][u["pool"]]["history_done"]
        and s["pools"][u["pool"]]["history_pages"] < 30
    ]
    # Alternate roles under ONE provider limiter, never a parallel request budget.
    if cold and (s.get("background_attempts", 0) % 2 == 0 or not history):
        return dict(
            pool=min(cold)[1],
            lane="prospective",
            before=None,
            limit=100,
            interval_s=c["cold_interval_s"],
            role="cold",
            background=True,
        )
    if history:
        p = min(history)[1]
        return dict(
            pool=p,
            lane="historical",
            before=s["pools"][p]["cursor"],
            limit=1000,
            role="history",
            background=True,
        )
    return None


def authorization(root, c, now, attempts):
    """Qualification is a short lease; frozen config alone cannot enable 72h IO."""
    if not c.get("hot_pools"):
        return
    path = root / "activation.json"
    if path.exists():
        a = read(path)
        q = root / "qualification.json"
        if (
            a["config_sha"] != sha(root / "config.json")
            or a["runtime_pin"] != read(root / "deployment.json")["pin"]
            or a["qualification_sha"] != sha(q)
            or not read(q)["approved"]
        ):
            raise ValueError("source_activation_binding")
    elif now >= c["qualification_expires_ns"] or attempts >= c["qualification_max_requests"]:
        raise ValueError("source_qualification_lease_exhausted")


def fresh_sample(root, now):
    from .store import view

    c = read(root / "config.json")
    rows, _ = view(root, now, since=now // NS - 86400)
    latest = {r["pool"]: r for r in rows}
    health = read(root / "health.json") if (root / "health.json").exists() else {"pools": {}}
    fresh = []
    for p in c["hot_pools"]:
        r = latest.get(p)
        if (
            r
            and health["pools"].get(p, {}).get("status") == "active"
            and 0 <= now / NS - r["open_s"] - 300 <= 300
        ):
            fresh.append(p)
    return dict(
        at_ns=now,
        fresh=fresh,
        hot_denominator=len(c["hot_pools"]),
        admitted_denominator=24,
        latest={p: latest[p]["open_s"] for p in c["hot_pools"] if p in latest},
    )
