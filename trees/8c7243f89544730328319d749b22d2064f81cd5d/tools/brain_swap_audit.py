"""Reproduce historical exact ABI swap reconciliation and unit-separated bar candidates."""

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from memecoin_ml.data import read, sha
from memecoin_pumpswap.historical import decode_historical
from memecoin_registry.contracts import digest, seal, verify
from memecoin_runner.ledger import publish


def main():
    p = argparse.ArgumentParser()
    p.add_argument("campaign", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    verify(a.campaign)
    a.output.mkdir(exist_ok=False)
    counts, pools, profiles, fees = Counter(), Counter(), Counter(), Counter()
    seen, raw_bytes, rows_count, events_count = set(), 0, 0, 0
    per_month, bars = defaultdict(Counter), defaultdict(list)
    import datetime

    for capture in sorted(a.campaign.glob("attempts/*/capture")):
        receipt = read(capture / "receipt.json")
        if receipt["method"] != "getTransactionsForAddress":
            continue
        raw = read(capture / "response.json")["result"]
        rows = raw.get("data", raw.get("transactions", []))
        raw_bytes += (capture / "response.json").stat().st_size
        events = []
        for tx in rows:
            sig = tx["transaction"]["signatures"][0]
            if sig in seen:
                continue
            seen.add(sig)
            rows_count += 1
            month = datetime.datetime.fromtimestamp(tx["blockTime"], datetime.UTC).strftime("%Y-%m")
            result = decode_historical(tx)
            counts[result["status"]] += 1
            per_month[month][result["status"]] += 1
            if result["status"] != "reconciled_candidate":
                continue
            for swap in result["swaps"]:
                roles, m = swap["roles"], swap["movement"]
                identity = dict(signature=sig, path=swap["path"])
                event = dict(
                    id=digest(identity),
                    **identity,
                    slot=tx["slot"],
                    event_s=tx["blockTime"],
                    received_ns=receipt["received_ns"],
                    available_ns=read(capture.parent / "availability.json")["available_ns"],
                    pool=roles["pool"],
                    base_mint=roles["base_mint"],
                    quote_mint=roles["quote_mint"],
                    side=swap["kind"],
                    movement=m,
                    profiles=result["profiles"],
                    raw_sha=sha(capture / "response.json"),
                    training_eligible=False,
                    execution_supported=False,
                )
                events.append(event)
                pools[roles["pool"]] += 1
                profiles.update(result["profiles"])
                fees[roles["protocol_fee_recipient_token_account"]] += 1
                price = (
                    m["quote_vault_amount"]
                    / m["base_amount"]
                    * 10 ** (m["base_decimals"] - m["quote_decimals"])
                )
                bars[(roles["pool"], roles["quote_mint"], tx["blockTime"] // 300 * 300)].append(
                    (
                        tx["slot"],
                        tx["transactionIndex"],
                        swap["path"],
                        price,
                        m["quote_vault_amount"] / 10 ** m["quote_decimals"],
                    )
                )
        publish(a.output / (capture.parent.name + "-events.json"), events)
        events_count += len(events)
    candle_candidates = []
    for (pool, quote, t), values in sorted(bars.items()):
        values.sort()
        prices = [v[3] for v in values]
        candle_candidates.append(
            dict(
                pool=pool,
                quote_mint=quote,
                open_s=t,
                ohlcv=[prices[0], max(prices), min(prices), prices[-1], sum(v[4] for v in values)],
                namespace="pumpswap/quote-vault-flow/5min/incomplete",
                training_eligible=False,
                missing_transactions_unknown=True,
            )
        )
    publish(a.output / "bar-candidates.json", candle_candidates)
    report = dict(
        campaign=str(a.campaign.resolve()),
        campaign_sha=sha(a.campaign / "manifest.json"),
        decoder_sha=sha(
            Path(__file__).resolve().parents[1] / "src/memecoin_pumpswap/historical.py"
        ),
        rows=rows_count,
        statuses=dict(counts),
        events=events_count,
        pools=len(pools),
        per_pool=dict(pools),
        per_month=dict(per_month),
        profiles=dict(profiles),
        shared_fee_accounts=dict(fees),
        raw_bytes=raw_bytes,
        bar_candidates=len(bars),
        qualified_training_bars=0,
        historical_online_availability_known=False,
        protocol_qualified=False,
    )
    publish(a.output / "report.json", report)
    seal(a.output)
    print(
        {
            k: report[k]
            for k in (
                "rows",
                "statuses",
                "events",
                "pools",
                "raw_bytes",
                "bar_candidates",
                "qualified_training_bars",
            )
        }
    )


if __name__ == "__main__":
    main()
