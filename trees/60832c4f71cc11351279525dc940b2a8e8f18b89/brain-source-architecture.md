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

Every fact carries actual availability and evidence pins. Support is `complete`, `unknown`,
`failed` or `unsupported`; complete means coverage of the **named process**, not all economic
activity. Complete intervals cannot overlap other support assertions, end after their availability,
or precede the availability of included events. Missing HTTP/provider bars never certify absence.
Corrections/revised coverage require a new source version, not overlapping contradictory facts.

Facts are CAS JSONL shards grouped by entity/kind/stream/day with paged catalogs. Publication and
replication use existing locks, seals and durable manifest publication. A crash before publication
leaves reusable objects; replay verifies and reuses them. Roles are checked before payload reads.
A disposable SQLite query index provides bounded-cache history, interval and rank-count lookup;
it is not the canonical event store. The current materializer is local and single-worker. Entity
partitioning and immutable source/snapshot identity provide the boundary for later distributed
materializers; there is no cluster scheduler or object-store publication implementation here.

`sparse.materialize` projects one selected stream plus lifecycle facts into existing generic v2
snapshots. It accepts short histories, preserves event spacing, age, padding masks and known gap
coverage, and never forward-fills prices. Multiple source streams are representable today; joined
multistream producers and new objectives are future implementations. Stable support-shard evidence
avoids embedding a changing whole-source hash in every old row; the snapshot binds the full source.
Appending another entity preserves unchanged source objects and materialized partitions.

CLI jobs: `python -m memecoin_experience {publish-observations,observations-from-continuous,
build-sparse,replicate-observations} --job JSON`. Publisher jobs supply sorted `rows_path`, `store`,
`schema`, `cutoff`, `sources`, optional `chunk`; materialization jobs supply `store`, `pin`, `output`,
`plan`, optional `role`/`chunk`. Plans declare stream, context, max_history_ns, lifecycle transitions,
lifecycle_origins, relative_features and named targets with kind/horizon_ns/feature. Replica jobs
supply source/target/pin/role. Adapter jobs supply a sealed continuous reference, store, role and
optional sorted lifecycle_path. The adapter preserves receipt availability and raw evidence,
always emits unknown observation support, and accepts birth facts only from explicit evidence.
