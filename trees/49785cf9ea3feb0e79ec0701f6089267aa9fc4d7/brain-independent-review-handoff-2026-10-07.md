# Brain: Technical Handoff for Independent Review

**Audience:** Claude Code / Claude Opus 5.5, independent systems and ML reviewer.
**Snapshot date:** 2026-10-07, Europe/Moscow.
**Code basis reviewed:** `2b834db345887223f9a1f09b86d869c8ec06293f`.
**Repository:** `https://github.com/avariceJS/memecoin-ai.git`.

This is a dated review packet, not current operational status or a canonical specification. It
consolidates what was built, what the evidence supports, what remains unknown, and where the design
may need replacement. The reviewer should independently decide what to keep, replace, or remove.
The current task map is [`INDEX.md`](INDEX.md); canonical facts are owned by the linked documents
below. Receipts are located through [`evidence/README.md`](evidence/README.md).

## 1. Review Scope and Confidence

The goal is a scalable autonomous Solana research/trading Brain, not a demo forecasting script.
The system should continuously acquire large market datasets, preserve provenance, grow its useful
training corpus by orders of magnitude, train meaningful model generations, and establish
improvement on independent benchmarks. The two Macs are an initial compute stage, not the target
ceiling. The user reports powerful local hardware, two Macs, paid Alchemy with high RPC throughput,
strong coding/research models, and willingness to scale storage/compute. The account plan and its
actual limits were not audited in this snapshot. Historical free-tier caps are not architectural
limits, and the report does not authorize paid upgrades or use of unknown credentials.

Safety, provenance, temporal correctness, immutable artifacts, and honest evaluation remain
requirements at every scale. They must not turn bounded pilot campaigns into the permanent
architecture. V1/V2/V3 should mean meaningful increases in useful data, justified compute,
reproducible recipes, independent evaluation, and measured capability, not merely a new weights
hash after a seconds-long fit.

No production, champion, AUTO, promotion-gate, data, or runtime change was made for this review.
No training, inference, acquisition, or large experiment was run. Protected F01/D43 and reserved
holdout were not opened. Owner status was checked read-only at the timestamp given below.

Use these labels:

- **[E] Evidence:** direct source, immutable receipt/artifact, or checked result.
- **[I] Inference:** engineering interpretation or proposed design.
- **[U] Unknown:** evidence or independent measurement is missing.

Numbers below refer to distinct snapshots and must not be added together. “Causal” refers to a
controlled intervention on a specific sealed dataset/seeds, not a universal explanation of all
future model behavior.

## 2. Executive Diagnosis

- **[E]** A real but small vertical research prototype exists: real provider data, on-chain
  ingestion/decoding, versioned storage, causal feature/label paths, deterministic training,
  immutable candidates, future forecasts, virtual paper accounting, operator jobs, and Control
  Center. The official champion remains empirical; the latest GRU was not promoted.
- **[E]** The strongest property is verifiability. The weakest is turning available markets into a
  broad, continuously refreshed learning and evaluation corpus. The system is currently better at
  explaining why an observation cannot be admitted than at producing a sustained volume of
  qualified observations.
- **[E]** No learned-model superiority, conditional calibration, economic edge, real execution
  quality, week-long unattended operation, or 10x/100x/1000x scaling has been demonstrated.
- **[I]** Do not discard the whole foundation. Preserve temporal/provenance contracts, immutable
  releases, causal replay, accounting, and evaluator discipline. Do not assume the campaign-shaped
  acquisition orchestration, materialize-then-cap trainer, or narrow selector is the scale-ready
  foundation.
- **[I]** The main bottleneck is not proven to be GRU capacity or lack of a GPU. It is the
  combination of incomplete data plane, weak benchmark breadth, and an underspecified optimization
  objective. Scaling compute without fixing them may scale the failure modes too.

## 3. System Built

### Domain and execution foundation

`src/memecoin_ai/` contains domain contracts, causal histories, CPMM replay, portfolio/risk,
execution assumptions, provider checks, reconciliation, prospective collection, and research
tools. Synthetic fixtures and real RPC snapshots/transactions exist, but they have different
validity domains. The bounded CPMM adapter is not a universal CLMM/DLMM/Solana venue adapter. An
on-chain transaction is not automatically a correct counterfactual fill price.

Frozen F01 was a narrow conditional-forecasting study with predefined action/target semantics,
folds, maturity purging, and protected holdout; it did not establish trading edge and did not
complete as a qualified research result. D43 was an engineering pilot, not a successful 48-hour
study: the recorded review found zero completed windows, one execution-hydration failure, two
semantic-checkpoint failures, and other windows not launched. Do not rewrite this as a successful
run. Preserve reusable execution honesty, not the limited F01 cohort/calendar as the universal
future data-system shape. See [`contracts.md`](contracts.md).

### ML foundation and Brain Development

`src/memecoin_ml/` includes empirical, linear, MLP, GRU, TCN, and Transformer options, shared
feature/action interfaces, ordered quantiles, masks, receipts, checkpoint/RNG resume, and CPU/MPS
checks. D44 exercised 15 CPU smoke fits and one MPS GRU fit on an M4 Pro. Those measurements
validate selected kernels/shapes, not the best Brain architecture. The first real-data smoke had
174 usable decisions, too few to choose a backbone.

The exploratory Brain track is separate from frozen F01. Its current task forecasts 5m/15m log
returns from USD OHLCV and maintains virtual long-only paper portfolios. This is not the F01
execution-aware target. Work added bar-source/shadow paths, source qualification, hot/cold
acquisition, continuous storage, paper recovery, historical curriculum, raw vacuum, AUTO operator,
two-host workflow, and a separate Watch Market lane. These components do not yet form one
high-throughput qualified data path, and not every repo feature is installed on the owner host.

Relevant boundaries:

- `memecoin_brain`, `memecoin_brain_sources`: bars, causal features/targets, source probes,
  decoders, and replay.
- `memecoin_continuous`, `memecoin_collector`, `memecoin_curriculum`: canonical bars, source
  scheduling, history shards, admission, training/evaluation, and streaming utilities.
- `memecoin_registry`, `memecoin_ml`: immutable model/data manifests and forecasters/training.
- `memecoin_paper`, `memecoin_operator`: virtual accounting, jobs/commands, release checks,
  Control Center, and Watch Market.

Package boundaries are real code boundaries; they do not prove that the scalable path is wired
into current AUTO. See [`brain-development.md`](brain-development.md).

## 4. Deployment Snapshot and Model Identity

The main data path combines GeckoTerminal OHLCV/snapshots and Alchemy/Solana raw or indexed RPC.
Captured HTTP/RPC data feeds separate bar and event paths; the latter remains a partial
quote-event/history lane, not a broad qualified USD corpus. Admitted bar shards feed a bounded
trainer, then immutable candidates, future forecasts, paper books, and operator views. Watch
Market is separate: user-selected mint/pool -> verified provider -> isolated book, pinned to the
official champion.

Mac 1 is the owner/control host. Mac 2 is a worker checkout; lack of owner `work/`, LaunchAgents,
or credentials on the worker is expected. Shared mutable SQLite is not used across hosts.
Research artifacts can be transferred as sealed immutable inputs. This is not a distributed
training scheduler.

Read-only owner API check at **2026-10-07 17:16:36 UTC / 20:16:36 MSK** reported:

- `mode=paused`, `phase=paused`, revision 33, `domain_stop=false`.
- All 10 jobs returned by the status API were complete; none was queued/running.
- All 3 managed exams were expired. Last expiry: 2026-10-07 12:23:51 UTC.
- Official champion SHA: `9ea4a6bd7977fb66fe4a18eba54ed7eaed0afd65810ec99c75aec3e1443e3920`.
- Deployment-file SHA `db3f12d9e71fb58cfd966953877d6d2c9fcb7036e3e3d60b18bdd09992578fde`
  matched the previously attested runtime08 binding; runtime pin
  `fec78c60d5c93b4b1d9cf4a680843162169f214910b98cc135c194cee8e63c3a`.

This check did not freshly attest the running executable. The expired `active_exam_id` field did
not establish an active exam; top-level `expires_ns=0` was not a substitute for source/exam
receipts. Source STOP after lease expiry was expected and preserved. Repo HEAD, candidate,
champion, and installed runtime are distinct identities. The dated receipt is
[`brain-handoff-status-2026-10-07.json`](evidence/brain-handoff-status-2026-10-07.json); live
status must be re-read before operational action.

### Current learner and candidate

The learned model forecasts 5m/15m returns from an 8-frame context of 12 causal OHLCV-derived
features with value/mask/age channels; total input dependency reaches 15 bars. Latest GRU is width
16/context 8 (2,174 parameters) with one shared linear quantile head. Horizon is concatenated only
at that head, making the 15m-minus-5m median offset history-independent. It is a forecaster, not a
policy, MPC, RL model, or LLM.

Latest completed job `job_5edd5e5ecda88338` selected a GRU ensemble (seeds 7/19), candidate SHA
`69ebb2703f8765527911c32511255a27fa8c7d8a3315c2818bea3a329dc583c6`; it did not promote.
Automatic promotion was disabled. Internal selection among new-run candidates is not the same as
comparison with the frozen official champion.

## 5. Data: What Exists and What It Means

### Raw/indexed on-chain discovery

Historical vacuum used 147 RPC calls and 14,340 nominal local CU (not billing delta). Monthly
migration captured 5,400 transactions (~90.79 MB) and 865 birth pools; pool history captured 4,919
transactions (~88.70 MB) and 1,236 swap candidates across eight pools; fee history captured 1,800
transactions (~38.99 MB) and 243 candidates across 97 pools. After deduplication: 1,579 decoded
events, 167 swap pools, 965 registry markets, and 1,583 provenance observations. These are not
965 trainable markets. Eighteen sampled date ranges touched 17 calendar months but all hit caps;
they are not complete month histories. At that stage the raw lane added zero qualified USD
training decisions.

### Bar corpus and expansion

The expansion control-to-broad comparison had 24 -> 86 training pools (24 old + 62 new),
90,336 -> 98,323 eligible decisions before cap, but retained rows stayed 48,000 -> 48,000.
Time span grew from 93 to 104 UTC days; pool-days from 370 to 465. Among 100 hydrated pools, 38
had no eligible support. New hydration added 17,825 bars and 7,987 decisions, all retained and
displacing old rows. Twenty new pools had fewer than 16 rows; 26 had >=64 and 13 >=256. New
markets were younger than three days; 5,427 origins were under one day and 2,560 were age 1-3d.
97.17% of new origins were adjacent 5-minute bars; 7,987 rows are not 7,987 independent episodes.
The cohort had 568 dependency-disjoint windows. Effective pools by row share rose 6.19 -> 9.55,
not to 86.

The latest AUTO snapshot is a different artifact: raw audit 100,656 bars/5,624 captures; train
48,000/86 pools; validation 264/8 pools; test 249/8 pools. The expansion/ablation test had 246,
not 249. Do not mix denominators.

### Attempted broad development envelope

A frozen roster of 20 mints x two historical age windows requested 40 windows; all HTTP responses
were 200. It yielded 2,020 unique bars, 453 eligible origins, six supported mints, and 29 global
dependency-disjoint windows. 34/40 windows had no support. Age <1d had 405 origins/six mints/27
within-stratum windows/six UTC days; age 1-3d had 48 origins/one mint/three windows/one day; age
7-10d had zero support. One mint supplied 322/453 = 71.08% of origins. The 27+3 within-stratum
windows are not 30 global disjoint windows; global count is 29. Disjoint does not imply statistical
independence.

This is a completed data-only result, not a broad benchmark or prospective collection. Temporal
development/calibration/prospective packets are absent. No complete Solana market coverage,
complete lifecycle history, representative Watch-session stream, or statistically strong old+new
future benchmark exists. Sparse/missing intervals are not zero returns, market death, or executed
trades. Surviving/provider-covered markets carry selection bias; pool age may be a proxy rather
than mint genesis. Current data semantics and receipts are in
[`brain-source-architecture.md`](brain-source-architecture.md) and
[`evidence/README.md`](evidence/README.md).

## 6. Training Results and Calibration

### Matched latest candidate vs champion

The latest job completed four fits and immutable completion checks found no partial/corrupt
outputs or violations of the declared split/maturity/availability checks. This checks implemented
contracts; it does not prove the absence of every possible dataset bias or leakage mode.

On the same validation/test origins, latest GRU vs empirical champion:

| Metric | GRU | Champion | GRU delta |
|---|---:|---:|---:|
| Validation combined pool-macro pinball | 0.010146 | 0.007641 | +32.8% |
| Validation 5m / 15m | 0.008439 / 0.011852 | 0.005040 / 0.010242 | +67.4% / +15.7% |
| Test combined pool-macro pinball | 0.008904 | 0.006272 | +41.95% |
| Test 5m / 15m | 0.008051 / 0.009756 | 0.004867 / 0.007678 | +65.43% / +27.07% |

GRU test q10/q50/q90 coverage was 1.606/77.912/98.996%, far from nominal 10/50/90. Champion
coverage was about 15.9/53.4/93.4%, also imperfect. Test support was 249 overlapping decisions,
eight old pools, one UTC day; no useful population confidence interval was established. This is a
descriptive result on an exposed sample, not stable future degradation or profitability evidence.
Pinball is forecast loss, not trading cost. A prior paired paper slice had eight origins and zero
fills, insufficient for model economics.

### Historical “5%, 9%, 42% degradation” claim

These do not form a generation trend: B12's 5.34% was an improvement of full GRU vs
recent-selected empirical on 6,027 decisions/24 pools/two days (0.0273579 vs 0.0289014); vs recent
GRU the effect was 5.81%. Expansion +8.92% compared broad/control GRUs on 246 decisions. Latest
+41.95% compared a later candidate to the old official empirical champion on a different
249-decision test. Evidence supports several mixed comparisons, not monotonic degradation of
generations. A secondary audit had mislabeled the B12 comparator; the primary report corrected
that. Receipts were not rewritten.

### Sampling intervention

The original sampler allocated equal mass per pool, then per pool-day, then divided within the
day. New pools held 16.64% of retained rows but received 72.09% sampling mass; 20 tiny new pools
received 23.26% mass. Draw-probability ESS fell 1,872.95 -> 1,171.60 (concentration diagnostic,
not independent sample count). New cohort volatility differed; 7,203/7,987 new rows were in the
two highest volatility buckets, while evaluation stayed on old markets.

On one sealed 86-pool snapshot, only new-cohort mass changed from 72.09% to 16.64%; data,
scaler/features, architecture, optimizer, budget, and seeds were held fixed. Controls reproduced
exactly. At 1,000 updates, validation pinball changed -1.15%/-9.83% by seed and -6.42% ensemble;
test changed -5.22%/-16.13% by seed and -11.91% ensemble (0.010601888 -> 0.009339007).
Pooled test coverage moved 2.44/65.45/94.72 -> 6.10/62.40/92.48%; however 15m median coverage
worsened 52.85 -> 71.95%. **A causal sampling effect is confirmed on this exposed snapshot; full
calibration repair and 16.64% as a target population are not.** New-cohort transfer is unknown.

### Budget, head, LR, clipping, normalization

- **Budget:** retained rows were still 48,000, so “larger dataset reduced epochs” did not describe
  this ablation. Old-cohort exposure fell to ~17.9k draws from 64k as sampler shifted toward sparse
  new pools. Exact seeded continuation 1,000 -> 4,000 updates improved selected test loss 8.95%
  (current sampler) and 10.05% (capped); combined change vs current/1,000 was -20.76% test and
  -18.65% validation. Terminal checkpoints improved too, so not only more checkpoint choices.
  Calibration remained poor: current terminal median coverage 83.33%; capped selected pooled
  median 48.78% hid 5m/15m 41.06/56.50%. Four thousand updates is not an optimal recipe.
- **Horizon head:** code makes median15m-median5m constant across histories; replay confirmed
  float32 deviation ~5.96e-8. An isolated 48-parameter zero-initialized horizon x latent head
  intervention preserved initial predictions and other settings. Eight fits completed; current
  sampler terminal ensemble test loss -3.93% with median coverage still 82.72%; capped test loss
  +1.08%, median coverage 38.62/67.89%. The structural restriction is real; it is not proven to
  be the main remaining failure or that this fix repairs calibration.
- **LR:** train-only forks continued four terminal-4k checkpoints at lr 0.001 vs 0.0001 over the
  same next 1,000 draws/updates. No validation/test scoring or model selection occurred. Current
  sampler median-coverage temporal SD fell 8.117 -> 3.677 pp (-54.70%); capped 14.068 -> 5.880
  (-58.20%). Train calibration MAE improved in all four pairs, but loss changed +2.29% current and
  -1.55% capped. Supports conditional optimizer instability, not future-market superiority. LR
  also changes effective AdamW decay.
- **Clipping:** threshold 1 clipped 100% of all eight LR-fork updates. Mean pre-clip norm was
  55.3-95.9 at original LR and 33.9-37.4 at lower LR. Clipping is continuously active, not rare;
  its harmful causal effect is untested. A saturated positive loss scale can cancel algebraically
  in the normalized gradient, but batch-dependent norm still changes directions and Adam moments.
- **Normalization:** historical |z|~1425.58 came from an older lower-wick revision, not latest GRU.
  Latest maxima: train 135.764, validation 6.115, test 4.120; scaler matched its artifact.
  Historical winsorization reduced extremes but worsened validation GRU loss.

Known contributors: cohort/objective mismatch, finite update budget, seed/checkpoint sensitivity,
narrow old-only evaluation, horizon-median coupling. LR supports optimization instability on train.
Clipping, capacity, representation, target mismatch, conditional nonstationarity, and independent
calibration remain unresolved. See [`training-evaluation.md`](training-evaluation.md) for canonical
results and all machine receipts.

## 7. Evaluation and Target Population

Strengths to retain: immutable folds/receipts, maturity purging, train-only transforms, candidate
manifests, future prediction timing, exact paired joins, separate paper accounting, and refusal to
relabel an exposed test as a new holdout. Latest integrity audit found no violations of declared
contracts. Weakness: there is no broad common development benchmark, independent calibration
block, representative old+new prospective benchmark, or adequate uncertainty estimate. Human
decisions based on an exposed test also expose it, even if a selector did not read it. Hash seals
protect bytes, not against research overfitting.

Do not infer target population from row share or pool count. Define distinct estimands:

1. **AUTO:** eligible origins under a frozen source/policy; traffic weighting primary, with
   mint/pool-day macro and age/volatility/venue strata as diagnostics.
2. **Watch:** user-selected market lane. Without session logs, recruitment is only a Watch proxy,
   not representative user traffic. Report per-mint breadth separately from observed session and
   decision frequencies.

Training objective, sampler proposal, and loss weights should be separate explicit objects.
Oversampling for support does not automatically define the intended objective; correction needs
common support and can itself be unstable. Benchmark roles should separate development,
calibration, prospective, and cold-market transfer. Current proposed 7/3/7-day windows and support
floors are heuristics, not power calculations or populated data.

Report per-seed and ensemble 5m/15m pinball, signed q10/q50/q90 coverage residuals, interval width,
tails, supported strata, abstentions/failures, and matured denominators. Use paired mint/time blocks
for uncertainty; overlapping bars are dependent. Include official champion and empirical/linear
baselines on common origins. Forecast loss, calibration, costed paper output, and executable edge
are distinct endpoints. See [`training-evaluation.md`](training-evaluation.md).

## 8. Paper and Watch Market

The virtual paper engine uses $1,000 per model book, $100 entry notional, at most three
pending/active positions, one position per pool, long-only policy, entry on the next fresh closed
bar, 50 bps slippage and 25 bps fee per side. These costs/fills are uncalibrated assumptions, not
real execution. One early GRU slice had two trades and net -$4.780619 with $0.990525 fees; other
slices had many intents and zero fills. Neither proves strategy quality. There is no real signing
or transaction submission.

Forecast target is origin close -> future close, while a paper entry happens later and exit is
measured from entry. This is causal, not leakage, but pinball is not automatically aligned with
the executed policy interval. Test policy-aligned targets, latency/cost sensitivity, and
abstention before claims of trading quality.

Watch Market code was introduced at commit `0e9a2c1`. Axiom supplies an identifier only; provider
resolution is separate. Start must pin the current official champion, use an isolated source/book,
and never trigger AUTO or alter AUTO ledger. Intended UI: snapshot price, closed-bar forecasts,
q10/q50/q90, WAIT/ENTER/HOLD/EXIT, reasons, and virtual portfolio. Polling is >=60s and forecasts
are 5-minute bars, not exchange ticks. Intervals are not qualified uncertainty guarantees.
Unsupported/gapped/stale inputs must block forecasts/trades.

At this snapshot synthetic tests existed, but owner-host provider/browser acceptance had not been
completed. Runtime08 lacked Watch; runtime09 was equivalent to runtime08. A new manifest/release
was required; the handoff neither created nor activated one. Current release status lives in
[`operator-runtime.md`](operator-runtime.md).

## 9. Acquisition: Causes and Mistakes

Technical causes of low qualified support:

1. RPC transaction count is not a training example; identity, decoding, token/quote semantics,
   availability, context, and matured targets are required.
2. Raw chain and price-bar lanes remain partly disconnected. Alchemy supports discovery/history;
   the main qualified USD path still depends on GeckoTerminal. WSOL/USDC is not USD without a
   time-valid conversion.
3. Venue/ABI coverage is limited. CPMM does not cover CLMM/DLMM. PumpSwap payload/account variants
   remain incomplete; diagnostic decode is not admission qualification.
4. Sparse lifecycle and 15 input bars plus mature future labels mean HTTP 200 may yield zero
   examples. Provider-covered survivors are not all Solana markets.
5. Historical pages often stopped at caps, not coverage completion; durable gap debt and resumable
   pagination remain weak.
6. Sleep/network, deadlines, ambiguous process guards, partial campaigns, and legacy supervisors
   complicated collection and health interpretation.

Implementation failures that are not external capacity limits:

- An early indexed Alchemy request omitted `maxSupportedTransactionVersion=0` and failed with
  -32015. That was a request bug; corrected indexed history later worked.
- A raw audit hit a 600s deadline because of repeated scans across 264,529 versions. Adding index
  `(pin, open_s)` reduced an audit batch to 2.235s and full audit to 8.051s. This was an access
  pattern fix, not a GPU need.
- `.DS_Store` caused a paper failure while source remained healthy; recovery and health dimensions
  had to be separated.
- A substring-based consumer guard could misclassify expired legacy supervisors, but the original
  unexpected consumer identity was not preserved; do not retroactively assert a false positive.
  Later exact-role/hash diagnostics passed a finite suffix run, not unattended soak.

The final 31-GET suffix lasted 927.4s at >=30.316s spacing, all HTTP 200, zero unknown consumers.
This qualifies that finite envelope, not the ceiling of Alchemy throughput; much of that campaign
was GeckoTerminal rather than Alchemy. Six hot pools, universe 24, 48k train cap, short windows,
CPU1, 1,000 updates, expiry, and manually approved campaigns were pilot bounds, not hardware or
provider limits. The larger organizational error was evolving acquisition as isolated campaigns
instead of a continuous data product with coverage SLO, durable backlog, shared budget, owner, and
research namespace.

## 10. Scale Readiness: 10x / 100x / 1000x

No load test at these scales exists. Using ~100k eligible decisions as a rough base, 10x/100x/1000x
would be 1m/10m/100m; raw events, markets, and features scale differently.

- **Current / <10x:** B13 materializes the admitted dataset before applying the 48k cap and has a
  500k-example hard bound. Raising caps increases Python/JSON/tensor memory; it does not create a
  streaming trainer. The 500k bound may be reached around 5x this rough example base.
- **10x [I]:** likely feasible without distributed training by wiring partitioned immutable data
  and lazy/mmap loading into the trainer, incremental features/labels, avoiding repeated whole
  scans, and sharing provider budgets. Keep SQLite as control/catalog while measured capacity
  suffices; benchmark Mac CPU/MPS on actual model/batch shapes.
- **100x [I]:** likely pressure points are single-host disk/IO, JSON-per-record overhead, catalog
  scans, full snapshot copies, and sequential hydration. Separate metadata/data plane; use
  partitioned columnar/event storage, idempotent parallel workers, backpressure, checkpointed
  replay, and manifests without full RAM materialization.
- **1000x [I]:** current orchestration is unsuitable. Likely needs immutable object storage,
  distributed task ownership, partition lifecycle/recovery, cost/coverage accounting, scalable
  feature generation, and GPU cluster jobs with reproducibility qualification.

First functional limit is semantic support and benchmark breadth; first obvious code limit is the
fixed cap/full materialization. Later possible bottlenecks include JSON/file overhead, store access,
single-owner scheduling, provider contention, and missing end-to-end backpressure. It is unknown
whether SQLite, network, MPS, or the GRU fails first. A lazy LRU/mmap loader exists, but it was not
the B13 trainer path and no end-to-end throughput qualification exists.

## 11. Proposed Direction for Review (Not an Approved Migration)

### Data plane

1. Continuous raw ingestion with separate live/backfill priority, account-level provider
   coordinator, actual attempt/rate/CU budgets, leases, deadlines, classified retries.
2. Durable coverage ledger by venue/pool/mint/time: wanted, fetched, decoded, qualified,
   feature-ready, label-ready, gaps/reasons. Measure useful episodes as well as bytes/RPC.
3. Immutable raw plus versioned canonical events, partitions by chain/venue/time/schema,
   idempotency, finality/reorg rules, per-partition quarantine.
4. Shared research/runtime feature semantics, explicit availability, multi-resolution bars/events,
   valid USD conversion, dependency manifests. Separate raw retention from training/execution
   admission.
5. Immutable snapshots referencing partitions/transforms instead of nested JSON; streaming
   batches, cached tensors, train-only transforms, explicit objective/draw schedule.

### Learning/evaluation plane

Version recipes by target population, exposure/compute budget, model, optimizer/sampler, checkpoint
rule, seeds, and validation role. Record unique draws, cohort/day exposure, gradients/updates, and
actual compute. Build permanent development/calibration/prospective data roles and exposure log.
Preserve traffic weighting, strata, failure denominators, empirical/linear/champion baselines.
Scale representation/compute only with learning curves; size/FLOPs are axes, not outcomes.

### Control plane

Isolated source, feature, training, evaluation, and paper workers with shared resource leases and
immutable references. Mac1 may remain operator/UI and Mac2 research/data/training worker. Scheduler
must account for RAM/IO/accelerator contention. Process scans are diagnostics, not distributed
locks. Unknown consumers should generate identifiable redacted incidents; retries require
classified transient causes and budgets. Promotion/release remain separate from job completion.

### LLMs

Strong external models can review bounded code/evidence; they are not necessarily MCP services and
must not be given secrets. Local Qwen3.5 35B on 48GB Mac is a free preprocessing worker for
redacted log triage, clustering, summaries, and structured annotations with source references.
Reported ~47 tok/s with substantial swap was not re-benchmarked here. Qwen output is not ground
truth, promotion authority, or a market feature without independent experiment. Scheduler should
yield memory to heavy training; do not use the model just to justify its residency. No LLM belongs
in trading runtime.

## 12. Suggested Next Deliverables

These are reviewer proposals, not authorization or a fixed recipe:

1. Specify capacity/coverage and implement one end-to-end qualified source -> canonical data ->
   features/labels -> snapshot service. Measure actual provider, disk, CPU/RAM, and admission yield;
   set sustained throughput/completeness SLO and recovery behavior.
2. Build broad development/prospective enrollment alongside training corpus. Preserve old/young,
   seen/unseen mint cohorts; do not replace a weak market after outcomes. Derive support from
   desired precision/coverage rather than arbitrary count goals.
3. Integrate streaming trainer and preserve exact small-snapshot regression. On proper development
   data, a fixed-backbone LR x clipping factorial is the most informative next short ML diagnostic
   suggested by current evidence. Then measure data/compute/model-size curves. Do not adopt
   16.64%/4,000/0.0001 as universal recipe or tune against exposed test.
4. Qualify a frozen candidate/calibrator prospectively against champion on old+new cohorts with
   uncertainty, calibration, identical policies/costs, and meaningful product endpoints. If data
   is weak, report insufficient. Real execution remains separate and unauthorized.

Plan for millions and eventually tens/hundreds of millions of qualified decisions as design
targets, not assumed independent examples or a reason to train indiscriminately. Required compute
is unknown.

## 13. Questions for the Independent Reviewer

Please independently answer with **keep / replace / remove / unknown**, evidence, and priorities:

1. Is the main gap data/evaluation, or are target/causal defects more fundamental?
2. Which current contracts are reusable, and which F01/B-series assumptions constrain scale?
3. What coherent two-Mac architecture transfers naturally to a cluster? Where should SQLite/JSON
   remain, and where should partitioned columnar/object storage replace them?
4. Which source/decoder/quote-conversion path first yields broad qualified Solana data with the
   reported Alchemy capacity? How to measure completeness rather than request success?
5. Are AUTO and Watch populations, abstention, and novelty defined correctly? What benchmark roles
   and precision targets support honest generations without self-tuning forever?
6. Is a backbone change justified now, or should objective, optimization, targets, head/calibrator,
   and exposure come first? Which causal results are transferable?
7. Which permanent services/legacy supervisors should be consolidated or removed after migration
   without losing artifact reproducibility?
8. Define V1/V2/V3 in data coverage, compute, quality, and operational reliability.
9. What one engineering milestone maximizes sustained useful data, and what one scientific test
   maximizes information once benchmark support is adequate?

Do not access protected holdouts, change champion/production, or treat this report as permission
to deploy. Do not defend the old architecture because of sunk cost or recommend a rewrite without
specific evidence.

## 14. Reproducibility and Current Navigation

Start from `AGENTS.md`, `memory/now.md`, `docs/INDEX.md`, Git status/HEAD, and read-only owner
runtime when needed. This report is historical and should not be loaded for routine tasks. The
canonical topic documents and `tools/agent_context.py --task architecture|data|ml|operator|review`
are the ordinary entry points. The source repository does not contain all private raw data;
sealed `work/` bundles may be required for reproduction. Their absence does not authorize new
acquisition or replacing real data with synthetic fixtures.

Key identities recorded in the original report:

- Expansion/ablation dataset: `ba9081b124dd066df8641a19724300c81785b885a15ce17a288a55cdc95be430`.
- Latest AUTO dataset: `a1c2f6d101a28ea260c378e05e252bdc0859f8ed964d961bd776aa5cc8971099`.
- Sampling run: `94c86c385c5d944ab34869874cb0922473af6c8fe9676744ad03ace518d15d11`.
- Budget run: `58545e8ef584e2987708169c330100c00d6ebc1693910d7ffded79abd738349a`.
- LR run: `3d81f4eb1239cf7dacfc4ade2650e6c5d752a0de4f97d19be76175258e07415a`.
- Research envelope seal: `9c8f79b0eb76f30981d3427a038b91b92b785ea8ed03ec4d0fe0465ec7d8a756`.
- Support seal: `7d4bf2b4cb2e39f0ca5e9b2f991136bc702b137951e9d400baecb9812d48168f`.

Machine-readable evidence is indexed in [`evidence/README.md`](evidence/README.md). Exact code,
data, protocol, per-seed/per-horizon metrics, and machine-local `work/` paths are in receipts.
Historical protocol files were hash-bound: use their recorded source commit for reproduction;
current English archival pointers must never be treated as the original protocol bytes.

## 15. What I Would Do If Starting Again

Keep the ambition for a large Brain from day one and treat market-data platform,
learning/evaluation, and paper/operator experience as peer products. Start with one complete
supported source-to-example path: durable raw acquisition, versioned decoding, identity,
availability-aware prices, feature/target dependency, coverage ledger, and streaming snapshots.
Design schemas and partitions for millions of examples rather than Python dictionaries in RAM.

Build broad development and future evaluation in parallel with the first continuous corpus. Define
AUTO/Watch estimands and denominators, temporal and mint-disjoint cohorts, and a separate
calibration role before model iteration. Keep empirical/linear as transparent champions; use a
small GRU to prove learning plumbing, not to declare a generation after a seconds-long fit. Then
measure data/compute scaling and increase model representation where broad development shows
benefit; move to GPU when pipeline shape and utilization justify it.

Record sampler objective, unique exposure, gradient/clipping/update diagnostics, per-horizon
calibration, and checkpoint variability from the first recipe. Do not treat equal-pool sampling as
neutral or head flexibility as calibration. Make acquisition a continuously managed service with
account-wide budgets, classified retries, backpressure, partition quarantine, and coverage SLOs;
use bounded qualification to validate it, not as a substitute for it. Keep STOP, receipts, and
financial boundaries.

Bring Watch Market to an accepted end-to-end owner-host scenario early: concrete mint, explicit
supported/unsupported behavior, pinned champion, visible forecasts, isolated book. Carry forward
contracts, provenance, immutable artifacts, causal replay, accounting, useful decoders, and
independent verification. Rewrite where pilot caps, JSON materialization, and manual
campaign-specific guards hardened into architecture; do not rewrite everything for fashion or buy
compute as a substitute for data and benchmark discipline.

The most important change in approach: measure progress by sustained growth in useful incoming
experience and independent evidence that Brain learns better from it. Verifiability should make
that growth reliable, not replace it.
