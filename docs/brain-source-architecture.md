# Data and Acquisition

**Status:** raw Solana history and qualified OHLCV are separate, partially connected lanes.
This document owns source roles, data admission, coverage, and acquisition design.

## Source roles

| Source | Current role | Limitation |
|---|---|---|
| Alchemy indexed Solana RPC | Historical transactions, birth discovery, address-indexed backfill | An indexed transaction is not a decoded market observation; coverage/pagination must be proven |
| GeckoTerminal | Current and historical OHLCV used by Brain's price forecasts | Provider bars are revisions with their own receipt/availability time; coverage can be sparse/stale |
| Public Solana RPC | Structural controls and bounded read-only verification | It is not a complete archive or independent proof of another provider's dataset |
| Helius / secondary RPCs | Diagnostics or explicitly qualified cross-checks | Configured access does not prove plan, completeness, or independent agreement |

The user reports paid Alchemy capacity. Account plan, throughput, concurrent-use scope, and actual
remaining quota are **not measured here**. Plan a provider coordinator around verified account
limits; do not multiply budgets by host count or request overlapping history twice.

## Data funnel

Every layer has a distinct denominator:

```text
requests -> received raw captures -> indexed transactions -> decoded events
         -> identified pools/mints -> qualified price intervals
         -> feature-complete origins -> mature labels -> admitted examples
```

HTTP 200, transaction count, registry market count, bars, eligible origins, and independent
market episodes are not interchangeable measures of training support. Record successes, empty
responses, unsupported layouts, failed requests, gaps, stale intervals, incomplete features,
pending targets, and mature examples separately.

The last historical raw vacuum indexed 5,400 monthly transactions / 865 birth pools and other
pool/fee streams. After dedup it represented 965 registry markets, 1,579 decoded events and
1,583 provenance observations. It added **zero** qualified USD training decisions at that stage.
Sampled date ranges ended at configured caps; this was not complete year coverage.

Later bar expansion produced 86 training pools (24 old + 62 new), 98,323 eligible decisions
before cap and 48,000 retained. Of 100 hydrated markets, 38 had zero eligible support. The new
cohort contributed 7,987 decisions, all retained, displacing old rows. Twenty new pools had fewer
than 16 rows. Nearly all new origins were adjacent 5-minute bars and do not represent independent
episodes.

The latest frozen 20-mint / 40-window historical envelope yielded 2,020 unique bars, 453 eligible
origins, 6 supported mints and 29 globally dependency-disjoint windows. One mint supplied 71.08%
of origins; age 7-10d had no support. This is not a broad or prospective benchmark.

Exact dated receipts and interpretation: [`research-history.md`](research-history.md). Full
machine-readable source/run records: [`evidence/README.md`](evidence/README.md).

## Canonical event and bar rules

- Store raw response bytes, request/receipt metadata, source alias, range, and hashes before decode.
- Decoder version, chain/venue, pool/mint identity, quote units, token program/extensions, and
  transaction status must be explicit. Unknown layouts remain quarantined, not approximated.
- Distinguish event time, received time, and actual availability time. Historical block time is
  not evidence that a model could observe the event then.
- Keep first-seen bar values immutable; later provider revisions are new observations. Do not
  replace an earlier feature with a revised candle that arrived after the historical decision.
- Feature dependencies require 8 context bars and up to 15 input bars including return features;
  both future horizons must mature before an origin enters a fold.
- Missing/stale intervals are not zero returns, market death, or rug labels. Preserve failures in
  the enrollment denominator.
- Keep WSOL/USDC denominated prices separate from USD. A quote conversion is itself time-aware
  data and must be available at the relevant origin.
- Pool birth, graduation, provider support, liquidity rankings, and user selection create different
  populations. Track selection/survivorship and age-at-origin; do not infer a market universe from
  today’s survivors.

## Solana event ingestion

The implemented historical path is indexed RPC -> immutable capture -> reservation/cursor journal
-> versioned decoder -> canonical event shards -> single-writer SQLite catalog -> sealed snapshot.
Raw/provenance support is useful, but the current decoder set does not cover every venue. PumpSwap
historical IDL profiles reconciled a small set of old transactions; newer payload suffixes and
Token-2022 remaining-account semantics remain incomplete. CPMM decoding does not generalize to
CLMM/DLMM/Meteora.

An early indexed query failed because the request omitted `maxSupportedTransactionVersion=0`.
That was a client request bug, not evidence that the provider lacked historical data. Later
corrected indexed RPC worked. `getSignaturesForAddress` gives references, not transaction
completeness; `minContextSlot` is not a historical-state selector. A raw capture can be retained
for future decoding without being admitted into features or trading.

## Capacity and next data-system boundary

The current B13 path materializes all admitted examples before applying the 48k cap and refuses
more than 500k examples. A lazy shard/mmap implementation exists, but is not wired into that
trainer. Historical SQLite audit scans once took 600s before a missing index; the indexed query
reduced the full scan to about 8s. These are implementation facts, not hardware limits.

The next scale design should add a durable coverage ledger by source/venue/pool/time/schema,
separate acquisition from semantic admission, support partitioned immutable raw/columnar data,
and make decoder/feature replay resumable. Keep SQLite for transactional metadata/control while
measured single-writer capacity suffices. No data-center load test has established where a store,
network, CPU, or GPU will fail first.

## Operational acquisition invariants

- Register exact ranges, source identity, decoder/schema, expiry, account-level budget, and
  failure policy before network requests.
- Record a reservation before IO; a timeout/ambiguous response is not a free retry.
- Retry only classified transient failures under a shared budget and deadline. Preserve STOP,
  partial receipts, and gap debt.
- Separate production and research namespaces/leases. A research worker must not become a second
  mutable writer or silently extend a production lease.
- Use per-partition quarantine for unknown data; do not let one unsupported venue erase healthy
  independent captures.
- Measure useful feature-ready and mature-origin throughput in addition to bytes and RPC counts.

## Data path interfaces

### Partitioned research experience (2026-10-07)

`memecoin_experience` introduces an opt-in research data plane. Its admitted-corpus adapter
streams existing sealed `birth-usd-admission-v1` training corpora, checks their namespace and
manifest, and requalifies bounded raw captures before accepting individual rows. Reserved
evaluation roles and protected pools are rejected. It does not call providers or open owner
runtime. Multiple sorted corpora merge by earliest receipt, with explicit duplicate/revision
counts and pinned sources preserving the alternative versions.

The builder retains bounded history and emits every context-ready origin, including separately
masked incomplete horizons. Receipt maxima and capture dependencies cover every raw bar used
by every input frame. Historical availability remains explicitly unknown at event time.
Pool/day partitions contain binary feature/target columns plus provenance JSONL. Content-addressed
objects are shared by snapshot manifests; no 500k assembly ceiling applies. Publication uses
fsync and atomic namespace operations under a local writer lock. Immutable per-partition journal
entries allow retries to replay their input prefix and reuse published objects; this is not a
random-access source cursor. Corrupt objects fail closed. Unreferenced crash leftovers are safe
but currently require manual storage housekeeping.

Multiple admission units merge through a disposable, bounded-cache SQLite spool with a bounded
number of open files; a single unit streams directly. Failed merges restart from pinned sources.
Keep new admission units bounded (for example, per capture/pool); the old `compile_corpus`
implementation itself still materializes its supplied unit. Do not feed it the whole market at once.

Payload memory is bounded by context, raw-capture cache and partition size (maximum 4096 origins).
Snapshot metadata remains O(partitions); source admission metadata remains O(pools/captures).
The initial format is a versioned stdlib binary layout, not Parquet or a distributed database.
The first adapter covers admitted USD history; continuous-index export and raw-vacuum semantic
qualification remain separate work. These engineering changes establish no market edge.

Relevant implementation: `src/memecoin_ai/{sources,storage,collection,observations,datasets}.py`,
`src/memecoin_brain/{source,data}.py`, `src/memecoin_brain_sources/`, `src/memecoin_continuous/`,
`src/memecoin_curriculum/{archive,admission,streaming}.py`, and `src/memecoin_pumpswap/`.

Common implementations and validity boundaries are in [`contracts.md`](contracts.md). Current
benchmark populations and ML conclusions are in [`training-evaluation.md`](training-evaluation.md).
