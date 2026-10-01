# Implemented contracts and validity domain

## Time and observations

All normalized clocks are integer UTC nanoseconds. This is an encoding, not a claim of nanosecond source accuracy. `event_ns` orders market changes; `available_ns` gates observations; `received_ns` is receipt evidence. `observed`, `assumed` with a named assumption, and `unknown` are distinct. Historical block time must never be substituted for receipt time.

Events are immutable canonical JSON with schema version 1. IDs deduplicate identical observations; a conflicting ID aborts the ingest transaction. Corrections need new events with explicit effective ordering, not silent overwrite. A late older pool update cannot rewind the observation book. Observation snapshots are immutable, and never expose the truth book. Gap ingestion disables trading until reconstruction from a verified checkpoint. General chain fork/reorg reconciliation is not implemented.

## Storage

SQLite is a local transactional reference journal, not a full-chain ingestion database. Raw blobs are content addressed. Dataset bundles contain canonical JSONL, required raw provenance blobs and a checksummed manifest. Export does not establish coverage or source honesty. The bundle identity hashes the manifest; record it in run provenance. The reference implementation loads bundles into memory; large dataset streaming/Parquet is a future measured extension.

## Synthetic execution only

`ReferenceCPMM` is a mathematical constant-product example, NOT a Raydium or other Solana adapter. Pool reserves, balances, inputs and fees are integer asset atoms. All pools in one reference replay use one explicit quote asset; decimal conversion belongs at validated ingest/display boundaries. Quote currency also pays the synthetic network fee. A SOL-quoted reference does not implement USDC-denominated gas conversion.

Only synthetic events can enter `ReferenceReplay`. No live transaction signing/submission exists. Multi-route execution, token extensions, venue-specific fee splits/rounding and calibration are absent. Unsupported mechanisms fail explicitly.

At a tied timestamp: market updates (sequence order), observation arrivals, due executions, then decisions. Decisions always have positive execution latency. The policy receives only `Observation` and immutable `Portfolio`; execution receives current truth. End-of-run orders that cannot resolve are rejected before submission.

Reference pool fee = ceil(input * fee_bps / 10000), retained in input reserves. Output rounds down. Included failures charge the configured network fee; drops and pretrade rejections do not. Outcomes are seeded synthetic scenarios, not empirical estimates. An out-of-domain participation violation invalidates the replay, rather than inventing a real execution failure.

Historical snapshots overwrite pool state, including previous own impact. This is explicitly limited by both per-order and cumulative input/reserve participation budgets; the latter never resets. This restriction does not make market counterfactuals accurate. A venue simulator must revisit this assumption.

## Portfolio and risk

Ledger reservations prevent double spending. Settlement is atomic, bound to the original intent and asset identities, and cannot exceed reserved debits. The reference engine serializes pending orders globally. That is a conservative limitation, not the eventual portfolio architecture.

Risk checks run on observed information: freshness, quote currency, order/position/total exposure and input participation. Exposure checks use observed reserve ratios as conservative sizing references, not as liquidation valuations. Market changes after submission can move exposure beyond a pretrade limit; limits are not a guarantee against loss. Portfolio target-to-intent conversion is separate from policy and risk. Integer swap rounding may overshoot requested base units slightly; risk checks use the resulting quote. HOLD means current units, not constant portfolio weight.

Valuation uses hypothetical executable exit proceeds minus exit network fee. It never adds those proceeds to actual balances. Without gas, a supported pool or a positive exit quote, the asset is reported as unpriced and valued at zero in the conservative mark. Final inventories remain explicit; there is no automatic profitable terminal liquidation. The mark is not evidence that an exit would succeed.
