"""Deterministic causal paper accounting, separate from any calibrated execution model."""

import copy
import math
from decimal import Decimal

D = Decimal
NS = 10**9
FEE, SLIP = D("0.0025"), D("0.005")
NOTIONAL = D("100")


def initial():
    return dict(books={}, seen=[], last_ns=0)


def fresh(o, now):
    return (
        o is not None
        and o["quality"] == "active"
        and 0 <= now - o["close_s"] * NS <= 300 * NS
        and o["available_ns"] <= now
    )


def new_book(model, kind):
    return dict(
        model_sha=model,
        kind=kind,
        cash="1000",
        realized="0",
        positions={},
        pending={},
        trades=0,
        wins=0,
        fees="0",
        closed=[],
        signals=[],
    )


def _require_clock(value):
    if type(value) is not int:
        raise ValueError("invalid_paper_clock")


def advance(previous, tick):
    now = tick["at_ns"]
    activation = tick["activation_ns"]
    _require_clock(now)
    _require_clock(activation)
    for obs in tick["market"]:
        _require_clock(obs["close_s"])
        _require_clock(obs["available_ns"])
        if not obs["close_s"] * NS <= obs["available_ns"] <= now:
            raise ValueError("invalid_paper_observation")
    for pred in tick["predictions"]:
        _require_clock(pred["predicted_ns"])
        _require_clock(pred["input_available_ns"])
        if not activation <= pred["predicted_ns"] <= now or pred["input_available_ns"] > pred["predicted_ns"]:
            raise ValueError("paper_prediction_temporality")

    state = copy.deepcopy(previous)
    records = []
    _require_clock(state["last_ns"])
    if now < state["last_ns"]:
        raise ValueError("paper_clock_regression")
    market = {o["pool"]: o for o in tick["market"]}
    if len(market) != len(tick["market"]):
        raise ValueError("duplicate_market_pool")
    for o in market.values():
        price = D(o["price"])
        if not price.is_finite() or price <= 0:
            raise ValueError("invalid_paper_observation")
    for key, book in state["books"].items():
        cash = D(book["cash"])
        realized = D(book["realized"])
        fees = D(book["fees"])
        for pool, pos in list(book["positions"].items()):
            obs = market.get(pool)
            if not tick["source_ok"] or not fresh(obs, now):
                continue
            if obs["close_s"] < pos["exit_due_s"] or obs["available_ns"] <= pos["entry_ns"]:
                continue
            ref = D(obs["price"])
            gross = D(pos["qty"]) * ref
            sale = gross * (1 - SLIP)
            fee = sale * FEE
            net = sale - fee
            pnl = net - D(pos["cost"])
            cash += net
            realized += pnl
            fees += fee
            trade = dict(
                pool=pool,
                entry_ns=pos["entry_ns"],
                exit_ns=now,
                entry_close_s=pos["entry_close_s"],
                exit_close_s=obs["close_s"],
                entry_ref=pos["entry_ref"],
                exit_ref=str(ref),
                entry_capture=pos["entry_capture"],
                exit_capture=obs["capture_sha"],
                decision_ns=pos["decision_ns"],
                prediction_sha=pos["prediction_sha"],
                qty=pos["qty"],
                entry_cost=pos["cost"],
                exit_proceeds=str(net),
                net_pnl=str(pnl),
                gross_reference_pnl=str(NOTIONAL * (ref / D(pos["entry_ref"]) - 1)),
                stress_net_pnl={
                    str(bps): str(
                        NOTIONAL
                        / (D(pos["entry_ref"]) * (1 + D(bps) / 10000))
                        * ref
                        * (1 - D(bps) / 10000)
                        * (1 - FEE)
                        - NOTIONAL * (1 + FEE)
                    )
                    for bps in (100, 200)
                },
                reason="precommitted_time_exit",
                model_sha=book["model_sha"],
                candidate=book["kind"],
            )
            book["closed"] = (book["closed"] + [trade])[-100:]
            book["trades"] += 1
            book["wins"] += int(pnl > 0)
            records.append(dict(kind="exit", book=key, **trade))
            del book["positions"][pool]
        for pool, order in list(book["pending"].items()):
            obs = market.get(pool)
            if not tick["source_ok"] or now - order["decision_ns"] > 600 * NS:
                records.append(
                    dict(kind="cancel", book=key, pool=pool, reason="source_or_entry_TTL")
                )
                del book["pending"][pool]
                continue
            if (
                not fresh(obs, now)
                or obs["close_s"] * NS <= order["decision_ns"]
                or obs["available_ns"] <= order["decision_ns"]
            ):
                continue
            ref = D(obs["price"])
            cost = NOTIONAL * (1 + FEE)
            if cash < cost:
                raise ValueError("paper_cash_reservation_broken")
            qty = NOTIONAL / (ref * (1 + SLIP))
            cash -= cost
            fees += NOTIONAL * FEE
            pos = dict(
                **order,
                qty=str(qty),
                cost=str(cost),
                entry_ref=str(ref),
                entry_ns=now,
                entry_close_s=obs["close_s"],
                entry_capture=obs["capture_sha"],
                exit_due_s=obs["close_s"] + 900,
            )
            book["positions"][pool] = pos
            del book["pending"][pool]
            records.append(dict(kind="entry", book=key, pool=pool, **pos))
        book.update(cash=str(cash), realized=str(realized), fees=str(fees))
    seen = set(state["seen"])
    for pred in sorted(tick["predictions"], key=lambda p: (p["predicted_ns"], p["id"])):
        if pred["id"] in seen:
            continue
        seen.add(pred["id"])
        for kind, q in pred["q_by_candidate"].items():
            if (
                kind not in ("empirical", "linear", "gru")
                or len(q) != 3
                or q != sorted(q)
                or not all(math.isfinite(v) for v in q)
            ):
                raise ValueError("paper_prediction_schema")
            key = pred["model_sha"] + ":" + kind
            book = state["books"].setdefault(key, new_book(pred["model_sha"], kind))
            pool = pred["pool"]
            obs = market.get(pool)
            reserved = len(book["pending"]) * NOTIONAL * (1 + FEE)
            if not tick["source_ok"]:
                reason = "source_paused"
            elif now - pred["predicted_ns"] > 120 * NS:
                reason = "late_prediction_no_backfill"
            elif not fresh(obs, now):
                reason = "stale_or_quarantined"
            elif pool in book["positions"] or pool in book["pending"]:
                reason = "already_exposed"
            elif len(book["positions"]) + len(book["pending"]) >= 3:
                reason = "position_limit"
            elif D(book["cash"]) - reserved < NOTIONAL * (1 + FEE):
                reason = "cash_limit"
            elif q[0] < -0.25:
                reason = "predicted_tail_risk"
            elif q[1] <= math.log(1.016):
                reason = "median_below_cost_buffer"
            else:
                reason = "enter_next_observed_close"
            signal = dict(
                pool=pool,
                decision_ns=now,
                prediction_sha=pred["id"],
                model_sha=pred["model_sha"],
                q=q,
                reason=reason,
                event_close_s=pred["event_close_s"],
            )
            book["signals"] = (book["signals"] + [signal])[-24:]
            records.append(dict(kind="decision", book=key, **signal))
            if reason == "enter_next_observed_close":
                book["pending"][pool] = dict(decision_ns=now, prediction_sha=pred["id"])
    state["seen"] = sorted(seen)
    state["last_ns"] = now
    for b in state["books"].values():
        if D(b["cash"]) < 0 or len(b["positions"]) + len(b["pending"]) > 3:
            raise ValueError("paper_accounting_invariant")
        invested = sum((D(p["cost"]) for p in b["positions"].values()), D(0))
        if abs(D(b["cash"]) + invested - (D("1000") + D(b["realized"]))) > D("0.000000000001"):
            raise ValueError("paper_cash_cost_basis_identity")
    return state, records


def summary(state, market, now):
    lookup = {o["pool"]: o for o in market}
    out = []
    for key, b in state["books"].items():
        value = D(0)
        stale = False
        positions = []
        for pool, p in b["positions"].items():
            o = lookup.get(pool)
            ref = D(o["price"]) if o else D(p["entry_ref"])
            value += D(p["qty"]) * ref
            stale |= not fresh(o, now)
            positions.append(
                dict(pool=pool, **p, mark_price=str(ref), mark_stale=not fresh(o, now))
            )
        out.append(
            dict(
                key=key,
                model_sha=b["model_sha"],
                candidate=b["kind"],
                cash=b["cash"],
                equity=str(D(b["cash"]) + value),
                realized=b["realized"],
                fees=b["fees"],
                mark_stale=stale,
                trades=b["trades"],
                wins=b["wins"],
                positions=positions,
                pending=[dict(pool=p, **v) for p, v in b["pending"].items()],
                signals=b["signals"],
                closed=b["closed"],
            )
        )
    return out
