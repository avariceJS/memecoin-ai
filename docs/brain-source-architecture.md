# Data and Acquisition

**Status:** raw Solana history and qualified OHLCV are separate, partially connected lanes.
This document owns source roles, data admission, coverage, and acquisition design.

**Scale target:** the Brain is intended to grow across very large market populations, long-lived
prospective history, multiple venues/modalities, and future worker/GPU scale-out. Today's OHLCV,
small slot samples, narrow venue paths, small models, and one-host limits are evidence fixtures,
not target boundaries; data contracts, provenance, recovery, and representations must extend without
a full redesign.

## Source roles

| Source | Current role | Limitation |
|---|---|---|
| Alchemy indexed Solana RPC | Historical transactions, birth discovery, address-indexed backfill | An indexed transaction is not a decoded market observation; coverage/pagination must be proven |
| GeckoTerminal | Current and historical OHLCV used by Brain's price forecasts | Provider bars are revisions with their own receipt/availability time; coverage can be sparse/stale |
| Public Solana RPC | Structural controls and bounded read-only verification | It is not a complete archive or independent proof of another provider's dataset |
| Helius | Independent indexed RPC and standard WSS cross-checks | A configured endpoint does not prove account plan, completeness, or independent agreement |

Alchemy Free and PAYG share one provider family. Read-only block verification can select the PAYG
endpoint through `MEMECOIN_ALCHEMY_PAYG_HTTP_URL`; the current research `.env.local` has no PAYG
endpoint, so no paid acquisition is currently configured. Helius remains a separate provider family.

The user-provided Helius RPC credential is configured only in ignored, mode-0600 `.env.local` under
`MEMECOIN_HELIUS_RPC_URL`; LaunchAgent arguments, Git and logs contain no key. Read-only Helius MCP
plan info and current pricing confirm Free at 1M credits/month and 10 standard RPC requests/s;
standard `getBlock`, `getBlocks`, `getSignaturesForAddress`, and `getTransaction` each cost one
credit. Account-level usage is not exposed. Standard WSS is available; enhanced
`transactionSubscribe` and mainnet LaserStream gRPC are not in Free. The old address-signature
watch stopped after 4,350 reserved/captured requests, with three transport failures and a growing
backlog; it is preserved as a stopped source run, not resumed as a per-signature completeness path.
The 20,000/day setting was only that run's bound, not a Brain quota or architecture ceiling. Use
Helius Free broadly where independent comparisons improve evidence, with per-wave credit, request,
raw-byte, and recovery accounting. See [Helius plan pricing](https://www.helius.dev/pricing),
[RPC rates](https://www.helius.dev/solana-rpc-nodes), and [Helius WebSocket capabilities](https://www.helius.dev/blog/laserstream-websockets).

A 2026-10-09 bounded PumpSwap program-address request returned zero signatures from Alchemy Free,
while public RPC returned five finalized references for the same address/limit/commitment shortly
afterward. This is a provider/index discrepancy, not evidence of zero activity. The six-call public
capture path preserved full `jsonParsed` transaction bodies and receipt times. It was a canary, not
a qualified market process: one transaction failed, the samples do not establish a pool census or
provider exhaustiveness, and the short canary source revision was not committed. See
[`brain-senses-program-watch-2026-10-09.json`](evidence/brain-senses-program-watch-2026-10-09.json).
The committed collector runs from a separate research LaunchAgent root. It journals each request
before I/O, seals responses, paces calls, records explicit provider/error/cursor state, and stops on
ambiguous reservations. Its observed population is transactions returned through the PumpSwap
program-address index, not a pool census. At the first decoder census boundary (279 transactions),
41 were failed on-chain transactions, 63 had no supported PumpSwap swap, and 175 remained
quarantined. Coverage and completeness are unknown. Do not admit these rows as exact market activity
or zero until independently reconciled.
An unrecognized PumpSwap instruction discriminator is explicitly surfaced as
`unsupported_instruction`, never summarized as `no_supported_swap`; recognized rows with unresolved
semantics remain quarantined.

The commit-bound read-only exception watcher records decoder/schema pins, transaction-shape classes,
representative signature hashes, raw-capture links, receipt/event times and new-class alerts. It
never changes raw captures or admits facts. In the first census, 53 apparent `event_transfer_mismatch`
cases exposed a v1 `buy_exact_quote_in` gross/net comparator defect. A bounded diagnostic now checks
separate user gross debit, pool quote credit, event net pricing amount, and fee-transfer equations for
that specific instruction. Successful arithmetic remains `economic_fee_semantics_diagnostic_only`;
remaining-account and Token-2022 blockers stay independent. V2 has a separate account and fee
retention layout and is not assigned the v1 equations. The official Pump [V2 trade contract](https://github.com/pump-fun/pump-public-docs/blob/main/docs/instructions/PUMP_SWAP_TRADE_V2.md)
describes that layout and its fee behavior. No economic process is qualified yet.

Alchemy endpoint qualification (2026-10-09) measured bounded read-only behavior on two
independent project accounts. The Free Solana account is published at 30M CU/month and 300 CU/s.
Earlier direct probes verified `getSlot` and a two-method JSON-RPC batch, but
`getSignaturesForAddress` returned HTTP 429 in 41/56 calls across two low-throughput phases. A later
isolated sequential raw capture returned HTTP 200 for both pages, with 571/412 ms end-to-end
latencies and no 429. This shows Free can support bounded raw development capture, but the earlier
429 pattern remains a measured bulk-ingestion risk. Its account-level usage meter was not available
to this workspace. PAYG returned 228/228 calls without provider errors (up to 33.68 calls/s in the
tested phase); the delayed usage meter moved by 5,520 CU: 180 `getSlot` calls at 20 CU each and 48
`getSignaturesForAddress` calls at 40 CU each. At the published $0.525/M CU rate, the measured
increment estimates $0.002898. PAYG 10,000 CU/s is a published base throughput, not a locally
saturated limit. PAYG's MCP transport did not expose JSON-RPC batch testing. A later two-call PAYG
MCP cursor probe was not reflected in the refreshed 5,760-CU account meter through 22:14 UTC; its
increment is unreconciled, so no zero-cost or request-count-based CU claim is made for that probe.
Use Free for bounded development when healthy; choose PAYG for larger indexed acquisition when its
observed reliability or throughput advantage is needed. Meter and budget each account independently.

Receipts: [`alchemy-endpoint-qualification-2026-10-09.json`](evidence/alchemy-endpoint-qualification-2026-10-09.json)
and [`alchemy-free-raw-cursor-qualification-2026-10-09.json`](evidence/alchemy-free-raw-cursor-qualification-2026-10-09.json).
Published plan/method references: [pricing plans](https://www.alchemy.com/docs/reference/pricing-plans),
[compute-unit costs](https://www.alchemy.com/docs/reference/compute-unit-costs),
[throughput](https://www.alchemy.com/docs/reference/throughput),
[Free tier](https://support.alchemy.com/articles/5827006872-what-is-included-in-the-free-tier), and
[`getSignaturesForAddress`](https://www.alchemy.com/docs/chains/solana/solana-api-endpoints/get-signatures-for-address).
Measurements are specific to this bounded test date and do not establish sustained throughput.

A separate 2026-10-09 PAYG probe returned 10 finalized signatures in descending slot order for one
known pool (slots 454,538,107 through 453,121,085). This qualifies the small response shape only.
Two 1,000-row MCP results could not be parsed as complete arrays; of two small-page attempts, one
was parsed and one summary was lost at the local tool boundary. No retry was issued. The later account
meter moved from 5,520 to 5,680 CU, exactly matching four requests at the published 40 CU method
cost (estimated $0.000084). The
[`probe receipt`](evidence/alchemy-signature-page-probe-2026-10-09.json) records the result and
limitations. Use a direct capture transport with atomic raw sealing and preflighted hashing for
future cursor qualification; the current MCP result surface is not a raw capture interface.

A follow-up bounded test stored the exact MCP result text for two sequential 10-row pages in ignored
`work/`, set mode 0600 and hashed both files. The result arrays contain 20 unique finalized
signatures with descending slots and no duplicate signature; the slot at the page boundary was
shared because distinct transactions can occupy one slot. The saved receipt omits the exact `before`
signature values, so request-to-page cursor lineage remains unverified. This qualifies local result
retention and observed response shape, not a verifiable cursor chain, HTTP wire capture or archive
completeness. The usage summary later reconciled the meter from 5,680 to 5,760 CU, exactly +80 CU
for two calls; estimated cost at the published method rate is $0.000042. Receipt:
[`alchemy-signature-cursor-qualification-2026-10-09.json`](evidence/alchemy-signature-cursor-qualification-2026-10-09.json).

The saved result was replayed through `signature_capture.publish` into the generic observation store.
It yielded 20 real provider-returned finalized signature events. The modality is transaction-reference
activity for one address, with slot and an RPC-reported transaction-success indicator; it is not
decoded swaps, volume, price, or a market-wide process. `blockTime` supplies chain event time; local
receipt time supplies point-in-time availability. The importer deliberately adds no enrollment or
support interval because neither an event-time membership declaration nor a queried event-time scope
was supplied. A first attempt to derive enrollment from the last receipt time produced 10 sparse rows;
that snapshot was rejected as semantically invalid. There are therefore no real experience examples,
objective targets, or real-source fit/evaluation from this capture. The machine receipt binds the
observation source and records that cursor lineage was not verified because exact `before` values
were not sealed. This proves the bounded positive-reference adapter only; it does not prove cursor
completeness, full retention, independent reconciliation, or that no other event occurred.

A bounded follow-up used the Free endpoint directly with a no-retry HTTP capture. Both five-row
pages returned HTTP 200 (571/412 ms); the raw response bodies were sealed at mode 0600 before JSON
decoding, and the second request cursor hash matched the first page's last signature. The direct
capture imported 10 finalized address references with raw-response provenance. It still emitted no
enrollment/support facts and cannot yield a valid historical snapshot by itself. Its 30M-CU account
meter is not available through the current account-summary tool. See
[`alchemy-free-raw-cursor-qualification-2026-10-09.json`](evidence/alchemy-free-raw-cursor-qualification-2026-10-09.json).

The same Free capture path subsequently accepted one maximum-limit request: 1,000 finalized refs,
HTTP 200 in 819 ms, with a 224 KB response body sealed and replayed into 1,000 observation facts.
This is one response-size check, not sustained rate qualification or an archive guarantee. The
importer still withheld enrollment and support because the result contains no independent membership
or event-scope evidence. See
[`alchemy-free-max-page-qualification-2026-10-09.json`](evidence/alchemy-free-max-page-qualification-2026-10-09.json).

A separate no-retry Free capture requested one finalized slot plus explicit adjacent boundary
witnesses. The in-range block response was complete (7.08 MB), while the left witness failed at the
transport layer and the right witness succeeded. Offline publication and replica replay verified
the single captured block and preserved the boundary failure. The range has unresolved lineage;
provider exhaustiveness, interval completeness and market-process coverage are not established.
The Free account usage delta was unavailable, so no CU cost was inferred. Receipt:
[`alchemy-free-block-range-2026-10-09.json`](evidence/alchemy-free-block-range-2026-10-09.json).

A selected PAYG app completed a bounded 300-request series: 270 `getSlot` requests at concurrency
4/8 and 30 sequential 10-row `getSignaturesForAddress` cursor pages. All returned successfully;
the 300 page rows were unique, slot-ordered, and cursor-linked in the live probe. MCP call latency
for the 30 pages was 290 ms median / 401 ms p95; the two `getSlot` phases measured 345-346 ms median
and 860-906 ms p95. These are MCP-end-to-end timings, not raw HTTP or provider-only latency. The
PAYG usage meter moved 5,840 -> 18,520 CU (+12,680) after its delayed update. The method
time-series attributed 11,400 CU to 570 `getSlot` calls and 1,280 CU to 32
`getSignaturesForAddress` calls, reconciling those method totals. The separate `getTransaction`
MCP requests did not appear in the `SUPERNODE_CU` method breakdown, so their CU cost remains
unreconciled; no zero-cost inference is made. At the published $0.525 / million CU rate, the
measured CU increment is approximately $0.006657; the account summary rounded MTD USD to $0.01.
No HTTP 429 or JSON-RPC error appeared in this bounded series. MCP has no raw JSON-RPC batch surface,
so PAYG batch behavior remains unmeasured. The Free direct HTTP page test processed 1,000 rows in
819 ms, while earlier Free phases had 41 HTTP 429s / 56 calls; PAYG is therefore the resource for
indexed work when Free throttling becomes an observed bottleneck, not a universal throughput claim.
See [`alchemy-payg-read-only-performance-2026-10-09.json`](evidence/alchemy-payg-read-only-performance-2026-10-09.json).

Free transaction retrieval was exercised on two complete bodies and then on a further 16
success-status finalized signatures evenly spread over one already sealed 1,000-row page. The first
probes showed that the endpoint requires `maxSupportedTransactionVersion=1`; one bounded request
timed out without retry. The later 16 JSONParsed responses all returned HTTP 200, complete, in
528 ms median / 1,046 ms p95. Four PAYG MCP transaction probes returned truncated text and are not
treated as captures. The decoder now accepts version 1 only when account keys resolve from the
transaction itself and no address lookup tables are present.

The expanded sample found a concrete IDL drift. All 33 candidate Buy/Sell events had exactly eight
bytes beyond the older pinned event layout. The official Pump IDL update at commit
[`8cda1fa`](https://github.com/pump-fun/pump-public-docs/commit/8cda1fa30ea658b20909d8aedf002047119388d2)
adds the trailing `creator_fee_unclaimed: u64` field. `memecoin_pumpswap.schema` now pins that file
hash and the decoder parses all 33 event payloads exactly. The 16 transactions remain quarantined:
all candidates have unreviewed remaining accounts and unqualified Token-2022 paths, with one
additional event/transfer mismatch. They have no training or execution eligibility. This is a
bounded schema-update qualification, not complete market-event support. Receipt:
[`alchemy-free-transaction-variant-qualification-2026-10-09.json`](evidence/alchemy-free-transaction-variant-qualification-2026-10-09.json)
and [`alchemy-free-transaction-qualification-2026-10-09.json`](evidence/alchemy-free-transaction-qualification-2026-10-09.json).

A narrow PumpSwap `sell` diagnostic now covers the pinned 24-byte instruction and 21-account prefix
with the evidenced fee-recipient/quote-ATA suffix, legacy SPL Token transfers, exact `SellEvent`,
and the strict CPI descendant subtree. It reconciles checked transfer amounts, exact pool signed
balance deltas, fee-recipient destinations, and empirical fee equations. Those fee equations remain
diagnostic hypotheses; the result has no training or execution eligibility. All touched token
accounts must have exact pre/post settlement for `reconciled_diagnostic_only`; otherwise exact
instruction/event transfer facts remain alongside an explicit quarantined, partial-settlement state.
It does not estimate wallet-net price or claim market/range completeness. The preselected 50-row
development replay matched 50/50: 12 had exact all-account settlement and 38 were quarantined for
unknown other account effects. After freezing the implementation at `c41f30b`, the one-time disjoint
50-row audit matched 50/50: all six event/transfer/fee-equation checks passed, 10 had exact
all-account settlement, and 40 were quarantined for unknown other account effects. This qualifies
the strict per-transaction gate and quarantine behavior on this sample only, not the 1,255-row class
or any market/range process. Fee equations remain empirical hypotheses. Receipts:
[`development replay`](evidence/helius-pumpswap-sell23-development-qualification-2026-10-09.json),
[`sealed audit`](evidence/helius-pumpswap-sell23-audit-qualification-2026-10-09.json).

One finalized slot was then fetched with full `getBlock` transactions from Alchemy Free and an
independent public Solana RPC. Both HTTP responses were complete and agreed on block identity,
parent linkage, 1,132 transaction entries, and the ordered flattened sequence of 1,171 signatures.
The full response hashes differ, as provider JSON representations may differ. This is single-slot
cross-provider consistency evidence, not proof that either RPC's list is exhaustive, not a bracketed
slot range, and not market-process support. The Free CU meter was unavailable. Receipt:
[`alchemy-public-block-cross-provider-2026-10-09.json`](evidence/alchemy-public-block-cross-provider-2026-10-09.json).

`signature_capture.publish_verified_values` now emits a versioned qualification receipt whose sole
capability is `observed_values`. The registered verifier replays sealed raw response pages and
reconciles every normalized signature reference, process/schema/decoder pins and conserved
resolved-vs-unresolved-time accounting. It checks role and protected entity identity before page
payload access. It never grants population coverage, support or completeness, and a values-only
source still cannot be indexed as market experience without independent enrollment. This allows
positive, available facts to be retained without turning omitted history or an empty capture into a
zero label. No real transaction objective target or
source-backed training example has resulted from the signature-reference captures.

Qualification has a read-only replay verifier that compares regenerated positive facts against the
existing immutable observation source using disk-backed scratch storage. It checks the source's
recorded chunk and refuses missing schema/rule dependencies instead of recreating them. The
`replicate_verified_values` path copies the source and verified-source shards/catalog pages, import
manifest, qualification receipt, schema/rules and only the raw capture files declared by the
receipt. The destination is fully revalidated; an integration test removes the original store and
capture directory before replaying from the replica. This establishes a portable single-capture
dependency closure, not distributed object storage or high-throughput worker capacity. Raw capture
files remain local inputs to verification, and implementation pins intentionally reject code drift.

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
- The legacy eight-frame OHLCV representation needs up to 15 transitive input bars. New contracts
  declare context and target definitions; each observed target is masked/purged independently.
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

The versioned compiled-transaction normalizer resolves static keys followed by loaded writable and
loaded readonly addresses, preserves instruction bytes/index paths and key provenance, and fails
closed when lookup addresses, account indexes, message versions, or CPI stack evidence are missing
or inconsistent. It decodes only the exact SPL `TransferChecked` wire form (tag 12, u64 amount,
decimals); Token-2022 transfer-fee/hook economic effects stay quarantined. This is a resolved
instruction layer, not yet an economically reconciled swap. The layout follows [Solana RPC JSON
structures](https://solana.com/docs/rpc/json-structures) and the [official SPL token instruction
implementation](https://github.com/solana-program/token/blob/main/interface/src/instruction.rs).

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
manifest, and requalifies bounded raw captures before accepting individual rows. The exact metadata
bytes used for the role gate must match the pinned manifest before market payloads are accessed. Reserved
evaluation roles and protected pools are rejected. It does not call providers or open owner
runtime. Multiple sorted corpora merge by earliest receipt, with explicit duplicate/revision
counts and pinned sources preserving the alternative versions. Equal receipt times use capture
hash as a deterministic tie-break. Legacy admission eligibility/disjoint counts retain their
context-8 meaning; new variable-context jobs derive their own membership and denominators.

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
Legacy inline metadata is O(partitions); new large snapshots use the paged catalog below.
Source admission metadata remains O(pools/captures).
The initial format is a versioned stdlib binary layout, not Parquet or a distributed database.
The admitted-history adapter is retained; the continuous export described below connects a
second source to the same reader. Raw-vacuum semantic qualification remains separate work. These engineering changes establish no market edge.

### Continuous export and representation boundary (2026-10-08)

`continuous.export(index, output, plan)` reads the existing SQLite index using `mode=ro` and one
explicit WAL read transaction. It does not invoke the writer's schema initializer or change the
owner runtime. Its plan has exactly `role`, `cutoff_ns`, `start_s`, `end_s`, `selection` and `pools`;
each enrolled pool supplies `pool`, `base_mint`, `quote_mint`. Roles are `train` or the new
`research_evaluation`, never protected/reserved holdout. Validate roles/protected IDs before
opening index/payload. Missing enrolled pools remain in expected 300-second-slot denominators.

Export replays bounded captures, verifies their index/version/checksum agreement and explicit
HTTPS GeckoTerminal USD/base/5-minute namespace and mint identity. Unsupported namespace/mints
and malformed bar schemas are quarantined with pinned evidence. Integrity failures abort
publication. Selection is the earliest **qualified** receipt with capture-SHA tie-break, not the
legacy `firsts` table's insertion-order tie policy. Revisions/ties retain counters and all capture
versions. The export does not infer a request-attempt denominator: unindexed transport failures
are explicitly unknown. Selection history is the declared enrollment, not a reconstructed universe.

The portable bundle contains `meta/{source.json,captures.jsonl,rows.jsonl,manifest.json}` and
`captures/<capture-pin>/`. Export copies/seals into staging and atomically renames the result;
a failure cannot publish a partial final directory. The source reader verifies quarantine evidence
as well as raw dependencies of admitted rows, including after bundle relocation. It requires a
consumer cutoff at least as late as the export cutoff. No receipt is rewritten as event-time
availability. Re-export still scans/requalifies indexed captures; caching that pure pinned
qualification function and reducing WAL retention are future throughput optimizations.

`brain-experience-v2` declares a materialized layout (`dense-sequence-f64-v1`), context, named
feature units/modalities, named target units/kinds, representation name/version/code SHA and an
availability policy. The currently shipped OHLCV producer supports explicit horizon sets; generic
producers can emit a different feature/target count through `storage.build(..., schema=...)`.
Records carry nanosecond decisions, full input dependencies, availability maxima, separate target
maturity/availability, masks and capture pins. Structural validation cannot qualify a new venue's
semantics: that remains the producer's responsibility and needs independent evidence/review.
Dense views are not a replacement for immutable raw multi-modal evidence; new physical layouts
need a new layout reader while retaining snapshot/object and role contracts.

`historical_replay` means event-time splits on data known by the research cutoff, without a claim
that it existed at historical decisions. `point_in_time` additionally enforces input availability
by decision and label receipt within the role interval. The two validity domains are explicit.
Unknown/missing/censored targets have masks, never invented zero values.

`compose` combines snapshots with identical representation/role and disjoint entity/decision
intervals; identical objects deduplicate, overlapping distinct partitions fail closed. This is an
increment interface, not silent row-level revision replacement. Supply context/label overlap when
exporting an increment and use build-plan `origin_interval_s` with v2 to choose disjoint output
origins. Source accounting stays attached to parent snapshot pins and is not summed when parents
share inputs. Location hints live in build journals; relocating a pinned source does not change
the new dataset identity. `replicate` publishes a destination snapshot only after its objects are
copied, fsynced and verified.

Real-data CPU qualification on the existing 24-market development universe exported 95,801
qualified training-window bars and persisted 82,436 context-32 examples. Independent accounting
identified 12,486 losses after temporal gaps, 667 initial-context losses and 212 outside the chosen
origin interval. Only eight markets supplied usable cumulative training origins; four supplied
95.62% of that exposure. These are source/representation/population constraints, not retained-row
caps. Source coverage and model findings have their receipt and interpretation in
[`training-evaluation.md`](training-evaluation.md#real-data-system-qualification-2026-10-08).

New snapshots with more than 1,024 partitions use immutable catalog pages of at most 1,024 entries.
The root pins page identities/counts; each page pins feature objects. Readers cache at most two
catalog pages. Small and historical inline manifests remain supported. New object paths use a
SHA-prefix directory, with a fallback for old flat locations; build journals group ordinal records
into directories as well. Writers/composition flush page metadata incrementally. Root metadata
and the large-snapshot sampler are O(catalog pages), with O(page size) partition ordering, rather
than materializing all partition dictionaries or a full partition permutation. Metadata is still
not constant-size, and explicitly converting an iterator to a list is the caller's responsibility.

Relevant implementation: `src/memecoin_ai/{sources,storage,collection,observations,datasets}.py`,
`src/memecoin_brain/{source,data}.py`, `src/memecoin_brain_sources/`, `src/memecoin_continuous/`,
`src/memecoin_curriculum/{archive,admission,streaming}.py`, and `src/memecoin_pumpswap/`.

Common implementations and validity boundaries are in [`contracts.md`](contracts.md). Current
benchmark populations and ML conclusions are in [`training-evaluation.md`](training-evaluation.md).

## Population and coverage facts

`memecoin_population` is an opt-in research data plane independent of provider, cadence and model.
Population enrollment precedes download support: namespaced entities carry valid intervals,
knowledge timestamps, evidence pins and extensible categorical attributes. Immutable paged fact
snapshots append parents; compaction retains all historical facts. SQLite indexes are disposable
views. Overlapping metadata with equal knowledge timestamps is ambiguous and fails closed.
Enrollment is a union of declared intervals, not inferred survival; correction/retraction requires
an explicit new population definition. Protected entities and research/train roles remain gated.

Coverage facts distinguish capture from qualification, bind a full stream contract and use explicit
units, expected counts (nullable for event streams), observed counts and failure/unknown states.
Sparse plans join declared population intervals with arbitrary stream tiles. Hash selection is
stable as population grows and independent of downloaded support. Worker partitions are disjoint;
they are work descriptions, not a distributed executor or authority to retry charged requests.
Changing a stream/grid contract requires reprojecting existing captures into new cells. A missing
cell never proves that a provider request has not previously been charged.

Use `python -m memecoin_population ACTION --job arguments.json`: `publish` streams `rows_path`
JSONL; `compact` and `replicate` operate on fact pins; `plan` seals a sparse SQLite worklist;
`work` streams one `partition` (or all cells with null); `audit` joins a pinned experience snapshot.
The corresponding function signatures in `catalog.py`, `planning.py`, `diversity.py` define job keys.
The `universe` adapter imports a hash-pinned legacy universe with its real admission timestamp;
optional `age_edges_ns` produces time-varying lifecycle bands. It explicitly retains survivor
selection bias. `continuous-coverage` requalifies a sealed continuous export, then projects unique
bar support onto compatible cells; other modalities and out-of-export intervals stay unknown.
Only this adapter knows the existing 300-second OHLCV convention. New producers publish the same
population/coverage facts without changing their storage model.

Knowledge policy governs attribute lookup, not retrospective enrollment: the declared population
is the union as of its pinned cutoff, including periods whose attributes were then unknown.
This is essential for diagnosing selection bias and is not a claim that the population was known
historically. A population removal is a new selection contract; shorter metadata intervals never
silently withdraw prior enrollment. Before dispatch, reconcile work cells with the acquisition
request ledger and provider budget; planning alone cannot prove a request has never been charged.

Population attributes are small categorical stratification metadata, not market feature vectors.
Each fact supplies a complete attribute map for its valid interval; later knowledge replaces that
map for lookup, not field-wise merging across unrelated producers. Explicit `unknown` request
outcomes route to reconciliation, not another acquisition suggestion. Capture completeness and
qualified grid support remain distinct: qualified counts cannot exceed a known denominator.

### Independent birth population acquisition

`tools/population_campaign.py` freezes public keyless acquisition from previously captured birth
metadata, excludes prior/protected cohorts and their known base mints, and separates train/evaluation
mints before price requests. The population is an authority/calendar-page prefix, with graduation
and decoder-support bias; it is not a complete market census. Unsupported, empty and failed requests
remain enrolled. No replacement based on returned prices, survival or current liquidity.

Acquisition reserves each request durably before network IO, seals raw responses, enforces pacing,
expiry/storage/request limits, and requires a caller-owned live owner fence. Sealed requests recover
without another GET; ambiguous reservations and provider failures stop without automatic retry.
A transient failed owner check occurs before reservation: later readiness checks may resume only
unrequested work. Continuous export additionally supports optional per-entity disjoint `intervals`;
coverage denominators use these intervals, avoiding invented missing history before market birth.
HTTP failures, malformed payloads, namespace/mint failures and invalid bars stay quarantined in
source accounting. Integrity failures still abort publication.

`memecoin_population.corpus` bridges the acquisition ledger to portable qualified source bundles.
Every request in a role must be resolved before its source is published; the other role's payloads
are not opened. The bridge checks frozen-plan, request-window, ordinal, outcome, receipt and raw
capture bindings. Its sealed accounting lists every planned request and qualification reason.
Population publication retains the complete enrolled role with birth/lifecycle metadata known only
at plan creation, not retroactively at event time. OHLCV intervals denote grid-aligned opens in the
planned half-open request windows; full-bar receipt causality is enforced independently.
The population coverage adapter publishes a cell only when the source declares every eligible bar
open in it. Cells crossing undeclared per-entity gaps remain unknown; they are not relabeled as
observed zero/partial coverage merely because they lie within the export's global calendar bounds.

A sealed transport failure can be reconciled explicitly with
`python -m memecoin_population reconcile-transport-stop --job JSON` (`root`, exact `stop_pin`,
nonempty review `reason`). This is never invoked automatically. Under the capture lock it verifies
the entire attempted prefix against the frozen plan, ledger, outcomes, receipts and sealed captures;
reserved/unsealed requests and provider stops fail closed. It writes a recovery receipt and moves
the original STOP bytes into immutable history. Original budgets/expiry remain in force. The failed
request remains terminal and counted; a later guarded capture invocation sends only previously
unrequested jobs. Crash recovery of the reconciliation itself is idempotent.
Restart also reconstructs an unreviewed STOP from terminal ledger failures, covering a crash between
terminal commit and STOP publication. Earlier transport failures require their own matching review
receipt; interrupted archival with both directory entries is completed only after verifying the
original STOP hash. User-requested pause is a distinct STOP reason and is never auto-reconciled.

The 2026-10-08 independent-population campaign completed 307 birth-enrolled markets without
response-based replacement. Its source funnel is recorded in
[`brain-independent-population-learning-2026-10-08.json`](evidence/brain-independent-population-learning-2026-10-08.json):
38,705 unique train-role bars became 8,792 context-ready rows across 49 of 170 markets;
14,375 evaluation bars became 6,465 rows across 30 of 137. All 307 markets have some qualified bars.
The 32-consecutive-bar producer loses 29,913 train bars to initial/gap context warm-up. This is an
experience-conversion bottleneck; a larger population alone cannot resolve it.

The provider returned nonempty qualified payloads for 1,221 requests, yet 381 contained no bars
inside their own requested window (216 train, 165 evaluation). Older returned history and repeated
bars are not new interval coverage. Source exports deduplicate first qualified receipts across all
captures and apply the declared per-entity interval union; an overlapping response can recover
support absent from its own interval's request. The terminal timeout and six duplicate-bar
quarantines remain explicit. Requested intervals contain 587,520 train and 473,472 evaluation grid
slots; missing slots can mean inactivity, unsupported history or missing observations and are not
a provider-error rate. Wider lifetime work plans also include unrequested cells; cells partly
outside source intervals remain unknown, so their denominators differ intentionally.

Causal volume admission rejected only 20 train-role contexts and no evaluation contexts. It does
not fix the larger selection imposed by requiring uninterrupted active bars. Future sparse/event
producers should preserve event spacing, known inactivity versus unknown observation support,
lifecycle and censoring, and use objectives that can learn from short/failed market histories.
Do not infer inactivity from a missing provider bar or filter enrollment by future survival.

### Sparse observations and lifecycle facts

`memecoin_experience.observations` publishes `market-observations-v1`: a sorted immutable stream
of namespaced entity enrollments, typed events, observation-support intervals and evidenced
lifecycle transitions. Enrollment is an administrative interval, not a claim about market lifetime.
Empty, failed and unsupported entities remain enrolled. No terminal transition is inferred from
silence. Streams declare feature names/units/modalities and the exact observed process.

Every fact carries actual availability and evidence pins. Support is `complete`, `unknown`, `failed`,
`stopped` or `unsupported`; complete means coverage of the **named process**, not all economic
activity. `stopped` records an evidenced source stop; neither it nor `failed` implies market death.
Complete intervals cannot overlap other support assertions, end after their availability, or
precede the availability of included events. Missing HTTP/provider bars never certify absence.
Corrections/revised coverage require a new source version, not overlapping contradictory facts.

`memecoin_experience.qualification` adds `process-contract-v1` and immutable
`qualification-receipt-v1` records over the existing stores. A receipt binds its process descriptor,
input/source/decoder/qualification-code pins, population pin and role, actual availability, explicit
captured/decoded/rejected/qualified/unresolved partition counts, and the exact projection capabilities
it supports; counts use `accounting_unit=candidate_process_records`. `scope_pin` names a sealed paged manifest of explicit
entity/half-open-interval members with each member's own status, availability, counts, capabilities
and evidence. Accounting requires captured = decoded + rejected and decoded = qualified +
unresolved, both per member and for the partition. A `SupportAssertion` is accepted only for an
exact scope member; partition totals never prove that every member is complete. Projection
revalidates membership and capability immediately before writing the existing observation support
fact or population coverage row. Complete support additionally requires the member's
`complete_support` capability and zero rejected/unresolved records. Only then can that member's
qualified count of zero mean an exact observed zero. A self-declared status, opaque hash, or all-zero
receipt is insufficient: the statically registered synthetic fixture verifier currently supports
complete and partial fixtures after binding and re-executing against the explicit fixture, process
contract, exact member and verifier implementation hash. The receipt's qualification-code pin must
equal that registered verifier hash. Verifier reads repeat publisher checks for fixture intervals,
schema shape and finite values, so a content pin constructed outside the publisher does not bypass
validation. Synthetic source identity is derived from the process and scope pins, and synthetic receipts require the registered decoder identity. Fixture
publication checks finite values and payload shape; only registered rule definitions are accepted.
Other statuses remain declarations and project no counts. Full-use cache keys include file identity
for every fixture dependency, while full verification hashes each dependency. This grants no
real-source completeness path. `qualified_process_events` has no independent
expected-event denominator, so its projected coverage keeps `expected=null`, even for complete
support. The v1 observation schema has no `partial` support state: that projection is conservatively
`unknown`; population coverage preserves verifier-backed `partial` and its observed lower-bound
count. Observation projection checks the destination process, stream, schema pin, fields, units and
modality. Receipt/scope pages are fully checked on publication and first use/import, then cached by
immutable pins and dependency file signatures; member-page reads are bounded. Cache-hit invalidation
still stats each verifier fixture, so cost is linear in verified fixtures; large-scope throughput
is not established. The receipt itself does not infer a lifecycle transition.

The process descriptor binds `entity_namespace`, modality, a hash-pinned payload schema (value names,
units and denominations), versioned event-identity/time-basis/interval/revision rules, and nullable
timestamp precision. These rule/schema references must resolve to matching immutable artifacts.
Provider location is not process identity. Changing event, time, payload or revision semantics
changes the process pin. Distinct producers may share this semantic pin while retaining separate
source and qualification receipts.

These machine checks establish hash identity, accounting, role/scope and capability consistency;
they do not establish provider truth or replace human/domain qualification. Capture/reconciliation
records remain acquisition evidence; these receipts are not a replacement transport ledger or a
distributed qualification service. `stopped` is an explicit population
coverage status and routes to `review_stop`; it is never converted to empty, complete or observed=0.
Incomplete support remains explicit in the assertion and coverage fact, and cannot prove
inactivity.

Facts are CAS JSONL shards grouped by entity/kind/stream/day with paged catalogs. Publication and
replication use existing locks, seals and durable manifest publication. A crash before publication
leaves reusable objects; replay verifies and reuses them. Roles are checked before payload reads.
A disposable SQLite query index provides bounded-cache history, interval and rank-count lookup;
it is not the canonical event store. The current materializer is local and single-worker. Entity
partitioning and immutable source/snapshot identity provide the boundary for later distributed
materializers; there is no cluster scheduler or object-store publication implementation here.

`memecoin_experience.sensing` extends this local architecture without changing `sparse.materialize`.
It seals an arrival-independent schedule in content-addressed pages, then reconciles wrapper event
and support facts to the exact receipt-verified synthetic fixture pins on publication and use.
`materialize_episode` streams scheduled origins through `storage.build`; input history is as-of,
quiet origins remain, and the final context frame carries per-stream support state and masks. Count
targets use `(decision,maturity]` and preserve the full horizon; exact price returns require unique
qualified observations at both the scheduled origin and endpoint. The dry-run budget interface
returns evidence-pinned decisions only and cannot dispatch. One local schedule measurement processed
1,025 origins into three 512-row pages in 0.041 s with 278,257 bytes traced peak (Python 3.13.6,
arm64); this is not a throughput claim. The observation index and sparse materializer now preserve
multiple nonoverlapping enrollment intervals per entity; events, support and lifecycle facts must
belong to exactly one interval, and history/targets cannot cross the gap. This is property-tested,
not a large-scale throughput claim.

`sparse.materialize` projects one selected stream plus lifecycle facts into existing generic v2
snapshots. It accepts short histories, preserves event spacing, age, padding masks and known gap
coverage, and never forward-fills prices. Multiple source streams are representable today; joined
multistream producers remain future work. Count targets retain an exact value only under complete
coverage of the named process. Otherwise they retain a lower bound from qualified observed events,
the latest receipt time supporting that bound, and support reasons (`unknown_support`, `source_failed`,
`source_stopped`, `unsupported_process`, or `unasserted_gap`). An administrative enrollment boundary
is separately marked; lifecycle transitions still require explicit evidence. A zero lower bound on
an incomplete interval is not an inactivity label. Stable support-shard evidence avoids embedding a
changing whole-source hash in every old row; the snapshot binds the full source. Appending another
entity preserves unchanged source objects and materialized partitions.

CLI jobs: `python -m memecoin_experience {publish-observations,observations-from-continuous,
build-sparse,replicate-observations} --job JSON`. Publisher jobs supply sorted `rows_path`, `store`,
`schema`, `cutoff`, `sources`, optional `chunk`; materialization jobs supply `store`, `pin`, `output`,
`plan`, optional `role`/`chunk`. Plans declare stream, context, max_history_ns, lifecycle transitions,
lifecycle_origins, relative_features and named targets with kind/horizon_ns/feature. Replica jobs
supply source/target/pin/role. Adapter jobs supply a sealed continuous reference, store, role and
optional sorted lifecycle_path. The adapter preserves receipt availability and raw evidence,
always emits unknown observation support, and accepts birth facts only from explicit evidence. It
does not qualify a source's event-process completeness. Qualification needs a named process and
interval, complete request/cursor accounting, explicit failure/unsupported ranges, immutable receipt
provenance, and an independently reconciled coverage boundary. Provider disappearance is not a
lifecycle event. The existing real sparse source `81f7c74b…4b10c3e0` has 170/170 support facts
marked `unknown`; its 38,705 retained events prove neither complete event counts nor inactivity.
The 2026-10-09 Alchemy work qualifies bounded RPC behavior, positive signature references,
transaction response capture, one-slot block response consistency across two providers, and a
replicable block-inventory receipt. A later two-slot finalized interval matches provider-reported
block identities and transaction projections and produces one exact next-slot count for a narrowly
defined structural PumpSwap instruction-reference process. It is not a qualified economic trade or
market-activity process: references in failed transactions remain references, not executed swaps.
The first left-witness attempt failed and remains recorded; the successful bounded replay does not
erase it. The range inventory producer accepts only a validated, expiring request plan with explicit
witnesses and request/byte/time budgets; it records reservations before dispatch, never retries, and
exposes no automatic witness search or range expansion. The generic inventory verifier is offline
and can label a slot skipped only from successful enumeration plus a qualified parent/hash chain; an
absent provider record alone is unknown. These are finalized block inventory facts, not universal
provider guarantees or lifecycle evidence.

The 2026-10-09 Helius/public same-slot block pilot compared three separated finalized ranges (50
slots). Both providers returned identical slot sets in all three ranges; block identity and ordered
transaction signatures matched for all 50 slots, and each provider's full/signatures-only lists
matched for all 100 provider-slot pairs. All 56,615 paired transactions were structurally comparable:
56,495 exact JSON-tree matches and 120 differences, all confined to Helius `meta.logMessages` with
an explicit `Log truncated` marker. The 103 Helius calls are expected to cost 103 standard credits;
the account meter was unavailable. Both providers returned 7,953 PumpSwap structural instruction
references (7,761 in successful and 192 in failed transactions), not economic swaps. The 50 slots
were a feasibility selection, not a probability sample or broad-completeness result. Block capture
recovered about 159 structural references per full-block request and averaged 4.3 MB per full block
per provider, substantially improving request efficiency over one `getTransaction` per signature.
Receipt: [`helius-public-block-provider-pilot-2026-10-09.json`](evidence/helius-public-block-provider-pilot-2026-10-09.json).
The next wave is precommitted in
[`helius-public-block-sample-precommit-2026-10-09.json`](evidence/helius-public-block-sample-precommit-2026-10-09.json):
2,880 selected slots across three days, 24 equal slot-number strata/day, 144 random 20-slot windows,
and one seeded signatures-only cross-check per window. UTC-hour alignment is an approximation from
a short measured slot-rate interval and will be audited from captured blockTime. It expects 3,168
calls/credits per provider on current published pricing and about 24.8 GB paired from the pilot's
full-block mean alone. This wave stops at 30 GB paired raw responses, 75,000 expected Helius credit
reservations total, 25,000/day, or 4 Helius RPS; public calls share the 1 RPS gate. These are bounds
for this wave, not durable Brain caps. Preserve the prospective public signature watch independently
and report only sampled-block agreement; scale subsequent waves from measured discrepancy yield,
uncertainty, actual billing evidence, durable raw bytes, and available storage.

## Prospective provider epochs (2026-10-09)

The prior public and Helius program-address watches stopped on failed requests and remain immutable.
Neither cursor is resumed. Helius watch v2 made a fresh head query and attempted 1,000 signature
references plus 1,000 transaction bodies; 999 bodies were captured, one request failed, and a later
runtime restart wrote a terminal `ValueError` STOP. The account-bound source state is not resumed.
LaunchAgent `ai.memecoin.brain.senses.helius-watch-v3` starts a new current-head epoch each six-hour
UTC bucket in `work/brain-senses-live-helius-watch-v3/`. Each root links to the previous STOP and
last receipt, carries no cursor across roots, reserves each request before I/O, and stops after 1,100
request reservations, an error, or its bucket boundary. A failed epoch is not retried; the next
scheduled bucket starts a fresh query. At most 4,400 watch requests/day are planned, a campaign
bound rather than an architecture limit. This is a sparse provider address-index stream with
unknown completeness, not full program history or a block-range truth source. A fresh current-head
query is a separate observation, not a retry or a bridge across the old gap.

The paired finalized-block sensor runs a seeded 64-slot epoch each UTC hour through
`ai.memecoin.brain.senses.block-epochs`, with raw reservations and receipts under
`work/brain-senses-live-helius-hourly-20261009/`. Every selected Helius block is checked against its
signatures-only response and the range's `getBlocks` inventory. Four times per day (UTC hours 0, 6,
12, 18), the same selected range also requests public RPC; all other hourly epochs use Helius alone.
This yields up to 1,536 Helius sampled slots/day (about 0.48% of the measured slot rate), roughly
3,120 standard credits/day, and up to 256 paired public slots/day. At the first scheduled capture's
4.8 MB per full block, Helius raw growth is provisionally about 7.4 GB/day / 22 GB per 72-hour
campaign; this is a campaign design estimate to reconcile against actual bytes and failures. The
1M monthly Free allowance and 10 RPC RPS are source-plan limits, not a Brain coverage goal. The
72-hour root has a finite campaign stop, 4,608 Helius slots, 768 public paired slot attempts,
20,000 Helius credit reservations, and 32 GiB aggregate raw-response cap; these are this wave's
bounds and do not constrain future sampling. Buckets are idempotent: an attempted bucket is never
replayed. Missed intervals remain unknown. Public RPC
requests use the shared 1 request/s gate. The first six-hour scheduled range
`[454865291,454865311)` captured all 20 Helius full blocks
and all 20 Helius full-block results parsed. Public returned 11/20 full blocks; eight full requests
received HTTP 429 and one timed out. Its sampled signatures-only request received HTTP 429 and its
`getBlocks` enumeration failed at transport. All 11 paired blocks had the same identity and ordered
signatures. Of 13,716 paired transactions, 13,651 matched exactly; 65 differences were only
`meta.logMessages`, each with Helius's explicit `Log truncated` marker. Helius returned 4,033
PumpSwap structural instruction references (3,510 in successful and 523 in failed transactions);
the public sample returned 2,304 references from its 11 full blocks. These counts are not economic
trades. Public failures remain unknown. This is source/range-bounded observation, not global market
coverage or a lifecycle claim. Receipt:
[`helius-public-prospective-block-epoch-2026-10-09.json`](evidence/helius-public-prospective-block-epoch-2026-10-09.json).

The 64-slot/hour wave and the 1,000-credit verifier are bounded experiments, not coverage ceilings.
The first public-RPC epoch received HTTP 429 on 120/128 full-block attempts, so public stays a sparse
availability sentinel. A separate 32-slot same-slot Helius Free/Alchemy Free calibration qualified
all 32 blocks on both sources after the empty-base58 decoder correction. Inventories and block
identity/signature order agreed. Of 39,318 paired transactions, 38 differed only in
`meta.logMessages`, each with Helius's explicit `Log truncated` marker. Receipt:
[`alchemy-free-helius-same-slot-calibration-2026-10-09.json`](evidence/alchemy-free-helius-same-slot-calibration-2026-10-09.json).

The first broad independent-verification epoch is complete. Its immutable sample contains 16 seeded
contiguous 32-slot windows, one in each equal slot stratum of the preceding-hour slot frame. Helius
captured `getBlocks` inventory for 13,399 produced slots and all 512 selected block bodies; all 512
full/signatures-only ordered signature lists matched, qualifying 512 exact **structural-reference**
counts. Those counts total 69,311, including one exact zero structural-reference slot; the economic
trade count remains unknown. This does not establish completeness beyond the Helius-reported bounded
inventory.

Alchemy Free captured 32 selected full blocks and signatures-only self-checks. All 32 same-slot block
identities and ordered signatures matched Helius. Its `getBlocks` inventory request failed once at
transport and was not retried. The 32 bodies still support bounded same-slot comparison, but Alchemy
inventory completeness and Alchemy exact structural zeros remain unknown. Across 36,852 paired
transactions, 36,773 trees matched and 79 differed only in `meta.logMessages`; offline replay of the
raw captures confirmed Helius's explicit `Log truncated` marker in all 79. Receipt:
[`helius-alchemy-stratified-512-first-epoch-2026-10-09.json`](evidence/helius-alchemy-stratified-512-first-epoch-2026-10-09.json).

This first epoch used 1,026 Helius attempts including the anchor (1,025 reservation rows in the
block capture plus the anchor's expected reservation) and 66 Alchemy Free attempts (65 captured,
one failed inventory). No request was retried or paid. It stored 576,376,420 bytes from
2,355,735,101 raw response bytes with zstd, taking 1,410.8 seconds from anchors through qualification
summary. Repeating this exact epoch 24 times projects 13.83 GB stored/day. The denser prior
calibration projected 14.9 GB/day, so retain 18 GiB/day storage capacity and 30 GiB free-space floor
until more epochs replace the estimate. Current disk had 75 GiB free after this epoch.

Sol's current wave keeps Helius at 512 selected slots/hour for a full diurnal cycle and pairs Alchemy
Free on four slots in each of the 16 Helius windows (64/hour). The first epoch took about 23.5
minutes including qualification. Its expected Helius use is 24,624 for the day, before other
sensors; the 26,000 campaign cap and 36,000 shared local reservation envelope are per-wave guards,
not recurring quota or Brain ceilings. At the daily evidence boundary, review completeness,
provider discrepancies, failures, CPU time, bytes, and disk headroom; if guards remain healthy,
renew the same 24-hour wave twice, reviewing between days. Do not autoextend without those reviews.
Helius coverage is intentionally wide because its independent comparison can expose gaps hidden by
Alchemy-family agreement. Every epoch records inclusion probabilities, request outcomes, immutable
raw/stored hashes, codec, and receipt times. Fresh inventories are new observations, not retries.
The 512/hour sample is itself a current collection rate, not a permanent coverage ceiling; raise it
when measured discrepancies or uncertainty show material benefit and storage/recovery remain sound.
Preserve the separate 64-slot prospective collector and Helius watch if a broad wave reaches its
campaign cap or disk floor.

The first paired window wrote 152.6 MB to disk (96.8 MB Helius, 55.8 MB public) including only 11
public full blocks. Helius watch v2 had captured 1,001 request results / 17.7 MB at its terminal
checkpoint; v3 is capped at 4,400 requests/day, which projects to 0.09 GB/day at 20.3 KB/request.
At the measured first-epoch rate, 512 Helius slots/hour plus 32 Alchemy pairs/hour is about 13.83
GB/day stored. Sol's planned 64 Alchemy pairs/hour adds about 36 MB/hour, bringing the estimate to
about 14.7 GB/day. This one-day wave is within the observed 75 GiB free while maintaining the 30 GiB
floor, but this rate cannot be retained indefinitely without more capacity. Keep the prospective
collectors running while their per-epoch disk guards pass; add archival storage only if measured free
capacity makes it necessary.

The 2026-10-09 `brain-experience-v3` contract carries typed native coordinates alongside independent
UTC availability. Origins, dependency extents, target windows, maturity and role intervals name a
pinned coordinate domain. Historical slot replay may leave `decision_ns` null; it cannot qualify as
point-in-time forecasting. There is no slot-to-UTC conversion or implicit alignment across domains.
Snapshots remain dense f64 partitions, with coordinate-aware publication, replication, composition,
batches, objective eligibility and registered evaluation. Legacy v1/v2 contracts are unchanged.
Provider errors/stops, unresolved support, explicit skipped-slot proofs, unsupported instruction
traces, pending windows and exact zeros retain separate states; only a qualified complete support
window or a verifiable skipped-slot absence can certify an exact zero for the declared reference
process.

The real v3 replay is bounded to two adjacent finalized slots: 2,066 transaction comparisons,
147 recognized instruction-reference facts (98 in successful transactions, 49 in failed ones), one
matured H=1 target with count 87, and one pending target. Shared training batches admit the one
matured exact row. The two-row episode cannot form independent train/validation/test sets; no fit or
learning-gain comparison was run. Cross-provider agreement and ancestry prove consistency within
these captured responses, not that either provider returned every transaction in the global ledger.
No current real source qualifies complete market trades, market inactivity, lifecycle death, or
historical availability. Expanding to another provider or modality requires its own semantic and
coverage evidence; the architecture does not infer cross-domain joins.
