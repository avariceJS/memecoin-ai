# Training and Evaluation

**Status:** latest official champion is empirical; latest GRU candidate was not promoted. The
broad prospective benchmark is not populated. This document owns model/training semantics,
benchmark policy, current experiment conclusions, and ML evidence pointers.

## Current learner

The production Brain target is 5m/15m log return from the origin's 5m close. Inputs are 12 causal
OHLCV-derived features with value/mask channels and age across an 8-frame context. Full input
dependency spans up to 15 bars. The current GRU is width 16/context 8 (2,174 parameters), followed
by one shared linear output head for median and ordered q10/q50/q90. Horizon is concatenated only
at that shared linear head. Therefore `median(15m) - median(5m)` is a history-independent constant.
The model is a forecaster, not a trading policy.

Latest B13 recipe: train cap 48,000, eval cap 6,000, 3h validation + 3h test, GRU/linear x seeds
7/19, 1,000 updates, batch 64, AdamW lr 0.001 / weight decay 0.0001, global norm clip 1, CPU one
thread. Pinball loss is scaled by train-label IQR. Checkpoints are selected by validation
pool-macro pinball with a simplicity tie rule. Automatic promotion is disabled.

The training function currently materializes the admitted dataset, tensors, and train/eval sets;
then caps retained examples. It draws replacement samples with seeded multinomial weights. The
available sharded/LRU/mmap loader is not the B13 training path. Current row/update bounds are an
experimental recipe/capacity limit, not the intended scale ceiling.

## Current official comparison

Latest completed AUTO job `job_5edd5e5ecda88338` selected a GRU ensemble but did not promote it.
Against the official empirical champion on identical origins:

| Split / horizon | GRU pinball | Champion pinball | GRU delta |
|---|---:|---:|---:|
| Validation combined | 0.010146 | 0.007641 | +32.8% |
| Validation 5m / 15m | 0.008439 / 0.011852 | 0.005040 / 0.010242 | +67.4% / +15.7% |
| Test combined | 0.008904 | 0.006272 | +41.95% |
| Test 5m / 15m | 0.008051 / 0.009756 | 0.004867 / 0.007678 | +65.43% / +27.07% |

GRU test q10/q50/q90 coverage was 1.606/77.912/98.996%, far from nominal 10/50/90%.
The champion's test coverage was about 15.9/53.4/93.4%, not perfectly calibrated either.
The test had 249 overlapping decisions, 8 old pools, and one UTC day. It is exposed and has no
useful population-level confidence interval. This result establishes a descriptive loss on that
sample, not a stable 41.95% future degradation estimate or profitability verdict.

The earlier “5%, 9%, 42% degradation trend” is false as a generation series. B12's known 5.34%
gain was full GRU versus recent-selected empirical (and 5.81% versus recent GRU); B13's +8.92%
compared broad/control GRUs on a 246-decision, 8-pool test; the latest +41.95% compared a later
GRU with the official empirical champion on a different 249-decision test. These are not a
common baseline or evaluation set.

## Controlled diagnostics on the exposed 86-pool snapshot

These are narrow causal diagnostics, not new recipes. The common 48k train set, feature/scaler
bytes, old-only 8-pool validation/test, and test day were reused. Do not treat these results as an
independent benchmark or tune another model against that test.

### Cohort sampling mass

The current equal-pool/day proposal gave the new cohort 72.09% sampling mass despite 16.64% of
retained rows; probability-weight ESS fell 1,872.95 -> 1,171.60. This ESS is a concentration
diagnostic, not independent sample count. New pools were sparse and much more volatile; evaluation
was on old pools.

Changing only new-cohort mass to 16.64%, while keeping relative within-cohort weights, improved
ensemble pool-macro pinball at 1,000 updates:

| Split | Current mass | Capped mass | Delta |
|---|---:|---:|---:|
| Validation | 0.014873905 | 0.013919116 | -6.42% |
| Test | 0.010601888 | 0.009339007 | -11.91% |

Both seeds improved test pinball (-5.22%, -16.13%). Calibration did not fully improve: test
15m median coverage worsened 52.85% -> 71.95%, while tails moved closer to nominal. **Sampling's
causal effect on this exposed snapshot is confirmed; 16.64% is not an established target mixture.**
New pools were absent from evaluation, so transfer to young markets is unknown.

### Training budget

Extending exact seeded continuations from 1,000 to 4,000 updates improved selected test loss by
8.95% current sampler and 10.05% capped sampler (10.24% is current-sampler validation,
not test; terminal test checkpoints: 3.53% and 9.52%). At
4,000 updates, capped mass also improved selected test loss 12.98% over current mass. Combined
sampling + budget change improved test loss 20.76% versus current/1,000.

Retained rows were 48,000 in both original cohorts. The naive claim “the larger dataset reduced
epochs” is therefore not what this ablation tested. Changed sampling reduced old-market optimizer
exposure and oversampled sparse new pools. Longer optimization improved loss but not conditional
calibration: selected capped/4k combined median coverage 48.78% hides 5m/15m coverage
41.06/56.50%; current/4k terminal median coverage was 83.33%. Neither 4,000 nor 16.64% is an
optimal recipe.

### Horizon interaction head

The shared head's constant median horizon offset is directly implied by code. A 48-parameter,
zero-initialized horizon x latent interaction removed that restriction while preserving initial
predictions. Eight fits completed; controls reproduced exactly. Terminal ensemble test loss changed
-3.93% with current sampling and +1.08% with capped sampling. Median calibration remained poor
(82.72% current; capped 5m/15m 38.62/67.89%). **The structural restriction is real; its role as
the main remaining failure and the benefit of this fix are unproven.**

### Learning rate and gradient clipping

Eight train-only continuations from identical 4k checkpoints compared lr 0.001 vs 0.0001 for
1,000 matched draws/updates. Lower LR reduced median-coverage temporal SD 54.70% (current) and
58.20% (capped); weighted train calibration MAE improved in all four seed/sampler pairs. Current
sampler train loss worsened 2.29%; capped improved 1.55%. No validation/test predictions were
scored. This supports conditional optimization instability, not future quality or a new default LR.

Gradient clipping triggered on 100% of those updates; mean pre-clip norm was 55.3-95.9 at current
LR and 33.9-37.4 at lower LR, with threshold 1. This confirms clipping is continuously active.
Its harmful causal effect is **not established**. No clip-on/clip-off factorial ablation has run.

### Normalization and remaining calibration questions

Historical |z| ~1425.58 came from an older lower-wick revision, not the latest GRU test. Latest
max normalized magnitudes were train 135.764, validation 6.115, test 4.120; scaler verification
matched the artifact. Historical winsorization reduced extremes but worsened validation GRU loss.

Known contributors are objective/cohort mismatch, finite update budget, seed/checkpoint sensitivity,
narrow old-only evaluation, and horizon-median coupling. LR continuation supports optimization
instability on train. The contributions of clipping, capacity, representation, target mismatch,
and conditional nonstationarity remain unresolved. Pooled pinball selection does not guarantee
per-horizon/per-market calibration; seed-ensemble quantiles need their own calibration tests.

## Benchmark contract

Do not reuse the exposed one-day tests for recipe selection. The benchmark foundation separates:

1. **Development:** frozen enrollment rules, broad old/young and seen/unseen cohorts; scores may
   be used for tuning and every exposure is recorded.
2. **Calibration:** a later, separate period after recipe freeze; only a registered calibration
   transform may be fitted.
3. **Prospective:** origins published before labels mature, after model/calibrator freeze. Fixed
   windows end when registered; no extension chosen by the score.
4. **Cold-market transfer:** mint-disjoint from all train/scaler markets, separately reported from
   temporal forecasting on known markets.

AUTO and Watch are distinct estimands. AUTO's primary score should weight eligible origins under a
frozen source policy, with mint/day and market strata reported. Watch breadth should be reported
per mint/age/seen strata; actual session/decision weighting is a separate statistic when sessions
exist. Do not use row share, admission cohort share, or equal pool counts as a proxy for deployment
traffic. Report failed/unsupported/abstained and matured denominators, not just scored examples.

For each model report per seed and ensemble, 5m/15m pinball, signed q10/q50/q90 coverage residuals,
interval width, tails, and supported strata. Use paired mint/time blocks for uncertainty; overlapping
bars are dependent. Include the current official champion and empirical/linear baselines on common
origins. Forecast loss, calibration, and costed paper outcomes are different endpoints.

The offline readiness checker validates metadata packets only; raw receipt/provenance/admission
review is still required. Current support floors and 7/3/7-day windows are initial design
heuristics, not statistical power analysis. Actual development/calibration/prospective packets
are absent. No promotion gate is changed by this document.

## Partitioned research jobs (2026-10-07)

The separate `memecoin_experience` implementation removes B13's assembly, retention and fixed
update ceilings for opt-in research jobs. It is not wired into AUTO or promotion. Its first
representation remains the twelve OHLCV features; context (1–256) and GRU width (4–4096) are
configurable. The default width is 64. Two independent ordered quantile heads allow conditional
horizon differences. This is an engineering interface, not a new validated Brain generation.

Every epoch visits each usable train origin once, using seeded partition order and bounded
within-partition permutations. Large paged snapshots first permute catalog pages, then partitions
inside each page, then rows; the recorded sampler is hierarchical, not a globally uniform row
permutation. This keeps page reads local and metadata memory bounded. Batches span partition boundaries; only the final batch of an
epoch may be short. Partition size therefore does not cap the requested compute batch or give
every small pool/day its own full optimizer update. There is no pool/day oversampling
or 48k retention. Chronological split boundaries are explicit absolute seconds; the full raw input
dependency must lie inside its role, and each target horizon must mature strictly before the
role ends. Partial targets contribute only where observed. Missing labels are not zero returns.
Role counts, purged/outside counts, per-horizon availability and usable denominators are retained.

Train-only Welford statistics normalize decision-frame occurrences (not unique raw frames).
Missing features get a separate mask; standardized values clip at ±8. Exact empirical train
quantiles use a disposable SQLite disk sort. The neural loss is the macro mean of available
horizon pinball means within each batch, in raw log-return units; AdamW uses its default weight
decay 0.01 and gradient norm clip 1. Validation mean horizon pinball selects an epoch. The final
temporal test is descriptive, scored after selection and accompanied by the train-only empirical
baseline, counts and interval coverage. Repeated jobs on this test cannot establish independent
generalization. No trading/execution or uncertainty claim follows from these metrics.

CPU and single CUDA-device recipes share the reader/model path. CUDA is implemented but has
not been exercised on this Mac. Distributed training and throughput tuning are not implemented.
CPU `train()` scopes and restores the CPU RNG, thread count, deterministic-algorithm setting, and
backend flags after success, interruption or exception; CPU seeding does not reseed device RNGs.
These Torch settings are process-global, so this does not make concurrent training calls thread-safe
or isolate CUDA jobs.
An immutable `recipe.json` establishes job ownership before preprocessing. The final `binding.json`
also pins the computed statistics before the first optimizer step. Interrupted, uncommitted
statistics are recomputed; committed statistics with a changed digest fail closed. Checkpoints
use only safe built-in metadata types plus tensors, including validation history.
Checkpoint state binds snapshot, code, recipe, environment, statistics, optimizer, model, RNG,
best validation weights and the next partition/batch cursor. Atomic checkpoints default to every
100 updates and each epoch; a crash replays only updates since the last complete checkpoint.
Resume validates progress types/ranges, epoch/history/best-score consistency, and replays the seeded
training batch stream to reconcile the saved cursor, step count and rows seen. Before restoring
state, it also requires AdamW parameter groups/options and parameter order to match a fresh optimizer
from the bound recipe, and validates per-parameter step counters and finite moment tensors against
model shapes/dtypes. Checkpoint model and best weights must match the model state shape/dtype and
contain only finite floating tensors. Empty optimizer state and a null best state remain valid before
the first validation epoch. These checks detect inconsistent progress and optimizer state; they do
not authenticate checkpoint bytes or prove that claimed optimizer updates were executed.
CPU synthetic tests establish bitwise resumed-weight equality, including a failure after an
optimizer mutation, and reject synthetic optimizer-state mutations before optimizer restore
or artifact publication. Synthetic non-finite model/best weights are rejected before state restore.
They do not establish cross-device bitwise reproducibility. Resume requires the same binding; a
different dataset/configuration needs a new job directory. Cross-generation warm starts and replay
policies remain future work.

When a completed training job is reused, `train()` validates its sealed artifact through the same
standalone `Predictor` contract before returning it. This rechecks completion and production
ineligibility, model/statistics bindings, and finite model state, then matches the report and
statistics to the current job binding. Synthetic re-pinned status, eligibility, model, statistics,
and weight corruptions fail before return or generation publication. This is internal artifact
consistency, not external authenticity or a model-quality check.
`Predictor` holds a no-follow artifact-directory descriptor, checks the exact manifest roster, and
hashes every regular member in bounded chunks. It copies consumed model, statistics, report,
optional diversity, and weight members into spooled streams, then parses or loads those same captured
bytes. Each stream keeps up to 8 MiB in memory before spilling to temporary disk; disk use is bounded
by the captured members' sizes and is removed after load. Replacing a pathname after verification
cannot substitute what inference loads. Symlinks, non-regular files, inventory drift, and hash
mismatches fail closed. This establishes byte coherence with the caller-supplied manifest pin; it
does not authenticate that pin or prove that a file was not modified during a single stream read.
At artifact load and before resume, persisted statistics are checked against the schema: feature
means are finite and correctly sized, scales are finite and positive, baselines are null or three
finite nondecreasing quantiles, and target transforms use supported identifiers. Missing legacy
transform lists default to identity; null baselines and older minimal statistics remain valid. This
does not prove that statistics were computed from the bound snapshot.
New training bindings also pin the exact `target_transforms.py` source digest used for target
encoding and prediction inversion. Standalone `Predictor` checks this digest before model
construction. Sealed legacy artifacts without the field remain readable and expose
`inference_transform_bound=False`; their inverse-transform implementation is therefore explicitly
unbound. This guards transform-code drift, not numerical identity across library versions.
New training bindings also pin the source bytes of `predict.py` as `inference_entry_sha`.
Standalone `Predictor` checks this before model construction; legacy artifacts without the field
remain loadable and expose `inference_entry_bound=False`. This covers inference-entry source drift,
and completed-job reuse rejects a changed entry digest before returning an artifact. This covers
source drift, not cross-version Torch/NumPy numerical equivalence or external authenticity.
The opt-in `audit_statistics_derivation()` utility accepts the expected snapshot and split, verifies
them against a sealed research artifact, and recomputes train-only statistics in automatically
removed scratch before comparing their canonical digest. It accepts complete current recipes for
seeded-fresh statistics and one-hop parent-normalizer overlays. Warm-start audits require an
explicit sealed parent artifact path/pin, and match its model spec and target transforms to the
child before overlaying only the frozen mean/scale and parent-normalizer lineage fields. It also
requires the child model schema to equal the pinned snapshot schema. Legacy/incomplete recipe
bindings return an explicit unsupported error. Results report `matched`, `matched_parent_overlay`,
or `mismatch`, with artifact, parent, snapshot, split, statistics and current derivation-code pins.
A match does not attest historical weight initialization or optimizer execution, recursively derive
the parent's statistics, or externally authenticate a coherently resealed artifact. The default
system temp parent is resolved and checked; a supplied scratch parent must already exist and cannot
be inside the child artifact, parent artifact, or source store. It does not fit, score, alter either
input, or establish model quality. Equality is under current code and numerical environment;
synthetic tests cover coherent reseals, mismatched pins/schema, parent overlay/mutations and scratch
containment/cleanup. `audit_statistics_lineage()` accepts an explicit child-to-root list of one to
three descriptors (artifact/path pin, source store, snapshot pin, and split plan), checks every
descriptor and parent-pin edge before creating scratch, then applies the one-hop audit to every
artifact. The terminal node must be seeded-fresh; omitted ancestors are rejected and ancestry is
never discovered from storage. A chain matches only when all node audits match. This checks
statistics provenance across the supplied bounded lineage, but still does not attest model-weight
or optimizer lineage.
The opt-in audit_partition_columns(snapshot, index) checks v2/v3 binary partition cells against
their row-status metadata in row chunks capped at 4096 rows and 16 MiB of binary cell data; it
rejects a schema whose single row exceeds that byte cap. Input cells may be finite or NaN but
cannot be infinite; observed/exact targets must be finite and every other supported target status
must have a NaN cell. It reports cell/status denominators and mismatches, and requires an explicit
train snapshot role before reading a partition. This is separate from ordinary partition loading
and training. The check assumes a quiescent snapshot: partition verification occurs before the
audit reopens the binary columns, so it is not resistant to concurrent mutation. It cannot detect
replacement of one finite exact target with another or prove source truth.
Before a completed report is published, reused, or read through the generation registry, its epoch,
step and row counters must reconcile with the recipe and train denominator. Validation history must
have one ordered finite selection score per epoch. This detects inconsistent report progress and
prevents generations from recording a self-consistent but impossible row count; it does not replay
the optimizer or recompute validation scores. Legacy masked-objective statistics with
`train_usable` remain readable when the newer `train_objective_usable` count is absent.

### Running an isolated job

Use bounded, already qualified training admission units. A build-plan JSON has exactly these keys:

```json
{
  "sources": [{"kind": "admitted", "path": "/absolute/path/to/admitted-corpus", "pin": "SHA256_OF_MANIFEST_JSON"}],
  "cutoff_ns": 1791388800000000000,
  "context": 32,
  "chunk": 4096
}
```

Use a receipt cutoff and split dates appropriate for the pinned data. The train-plan JSON has
`split` (integer `train_start_s`, `train_end_s`, `validation_end_s`, `test_end_s`, strictly ordered)
and `config` (optional `width`, `batch`, `epochs`, `seed`, `device`, `lr`, `checkpoint_every`,
`cpu_threads`, `sampler`; default CPU thread count is one and sampler is `content_pin_v1`). Set
sampler to `role_isolated_partition_v1` to derive seeded order from stable partition ordinals
instead of content pins. This remains opt-in pending broader research qualification.
Every role and both evaluation horizons must retain usable labels, otherwise the job fails.

```sh
work/ml-venv/bin/python -m memecoin_experience build --plan work/build-plan.json --store work/experience
work/ml-venv/bin/python -m memecoin_experience inspect --store work/experience --snapshot SNAPSHOT_SHA
work/ml-venv/bin/python -m memecoin_experience train --store work/experience --snapshot SNAPSHOT_SHA --plan work/train-plan.json --output work/research-generation-v1
```

The last command resumes an interrupted identical job or verifies and returns its completed result.
`artifact/` contains sealed weights, statistics and a report; it is explicitly production-ineligible.
Copy the immutable store objects and snapshot manifest to another training host, install the pinned
ML environment, and use a new output directory. No owner credentials or runtime are required.
Local publication locking must not be treated as distributed coordination.

Engineering checks: `work/ml-venv/bin/python -m pytest -q tests/test_experience*.py`;
capacity: `work/ml-venv/bin/python tools/experience_capacity.py --rows 1000000 --output NEW_RECEIPT.json`.
The capacity tool uses temporary synthetic payloads and deletes them after measurement. The initial
[520k-bar receipt](evidence/brain-experience-capacity-2026-10-07.json) records 519,993 persisted
origins and 415,991 train-reader origins at approximately 210 MiB peak RSS, including torch import.
It measured a development revision before final provenance/checkpoint hardening; its exact code
hashes are in the receipt. It did not perform a 520k-origin optimizer fit or assess market utility.

The [million-bar receipt](evidence/brain-experience-million-capacity-2026-10-07.json),
reproducible from `8683305`, records 999,993 persisted origins, 799,991 training-reader origins,
3,473 partitions, approximately 212 MiB peak RSS and 55.5 seconds on this worker. Payloads occupied
2.96 GB before cleanup. This checks the actual partition writer, integrity reader and learning
batch iterator above the former ceilings; the million-origin optimizer workload and distributed
execution were not benchmarked. The representation still repeats window features/provenance,
so compaction and columnar ecosystem integration remain future storage improvements.

The [post-review batch-capacity receipt](evidence/brain-experience-batch-capacity-2026-10-08.json)
is bound to `035406a`: 1,000,000 synthetic bars, 999,993 persisted origins, 799,991 training-reader
origins, 782 batches at requested size 1,024 (781 full batches and a final 247), approximately
214 MiB peak RSS and 62 seconds. A physical partition holds at most 288 origins in this fixture;
the measurement confirms that it no longer caps the learning batch. These are data-path checks,
not a million-origin optimizer fit, GPU throughput measurement or evidence of market edge.

Migration acceptance includes focused tests in the pinned ML environment, including CLI
training at context 32/width 64, exact recovery, source corruption, partial targets, role exclusion,
source merging and immutable reuse. Final focused migration tests: 32 passed, one CUDA check
skipped. Combined migration and existing optional ML regression tests: 64 passed, six device
checks skipped (five Apple MPS, one CUDA). `make smoke` passed; final `make check` completed
with 1,079 passed and 13 optional-dependency skips, plus lint and type checks. Acceptance also
covers symlink rejection, completed-artifact recipe binding and post-validation checkpoint recovery.
The October 7 capacity measurement's code basis remains `8683305`, before later batch-packing
and recovery hardening; it is not silently relabeled as evidence for later source bytes.

### External code review and adjudication (2026-10-08)

After the user explicitly authorized source export, Kimi K3 and GLM 5.3 reviewed selected actual
source/test files. Completed responses, rather than `finish_reason=length` fragments, informed
the review. Kimi's default `max` reasoning required a larger budget; finalization with the API's
supported `low` setting returned complete answers. No secrets or protected market data were sent.
Grok 4.7 High previously returned useful generic design reviews, but four actual-code review
attempts timed out (two large packets, two single-file packets); those are not counted as reviews.
Two later `grok-4.7-high-fast` file-tool attempts were cancelled when the user discontinued Grok;
neither produced a completed review. Further external verification uses Eliza only.
Qwen's partial answer is likewise not counted as completed code acceptance. No helper edited code.
A final GLM 5.3 review of the current `learning.py`, `storage.py` and recovery tests completed
with `finish_reason=stop` and found no blockers. Its batch/cursor and publication arguments were
checked against the code and local regression results; source review is not CUDA acceptance.

Integrator-verified findings and fixes: Kimi identified unsafe NumPy scalar metadata in post-epoch
checkpoints and statistics not authenticated before the first checkpoint. Both were reproduced
and repaired without unsafe pickle loading. GLM identified a stale admission-role read: the exact
metadata bytes driving the role gate now must match the pinned manifest before payload access.
The integrator also reproduced partition-boundary batch truncation, nonfinite values masquerading
as missing features, and incomplete-epoch publication after cursor corruption; all have focused
regression tests. Target overflow/underflow and malformed learning-rate configuration fail with
explicit contract errors. Missing data continues to have a distinct representation.

Rejected or scoped findings: GLM's claim that legacy `load_corpus` accepts a symlinked root is
false (`verify` already raises `symlink_forbidden`, locally reproduced). Legacy admission's
context-8 disjoint count is not consumed as a variable-context experience metric. Equal-receipt
tie policy differs deliberately: the new path uses capture hash, legacy keeps reference order;
production merge behavior was not changed. Kimi's later claim that a conflicting recipe overwrites
`binding.json` ignores `atomic_json`'s no-replace semantics; tests verify rejection and unchanged
bytes. `stop_after` is an absolute, one-shot test interrupt, absent from the CLI.

Kimi's assertion of inevitable failure on every CUDA host was not accepted as established:
it was not reproduced here, and inspecting the current
[PyTorch cuDNN RNN source](https://github.com/pytorch/pytorch/blob/main/aten/src/ATen/native/cudnn/RNN.cpp)
did not establish
the claimed universal rejection. CUDA remains unqualified; an actual-device train/resume test
now exists and skips on this Mac. Checkpoint validation-history size grows with epochs and full
consumed partition hashes are reread each pass; these are recorded performance limits, not hidden claims
of distributed or unlimited-throughput training.

## Integrated research pipeline and generations (2026-10-08)

The concrete command path is now:

```text
read-only continuous WAL snapshot -> portable qualified source + coverage/quarantine
  -> versioned representation -> immutable experience snapshot (or disjoint composition)
  -> owned generation job -> sealed model spec/weights/statistics/report
  -> standalone predictor -> registered candidate/population/window -> evaluation receipt
```

The v2 dense contract removes trainer assumptions about twelve features or two targets. The
shipped producer still derives OHLCV features; richer producer semantics are not invented by the
contract. Feature names/units/modalities, target names/units/kinds, context and representation code
are pinned. Both `gru-quantiles-v1` and `temporal-mlp-quantiles-v1` implement the current dense
quantile trainer; `config.model_kind` selects one. This is a real model factory, not a promise of
transformer/DDP qualification. A different task/loss or physical layout needs another explicit
trainer/reader capability, retaining the immutable data/job/evaluation boundaries.

`artifact/model.json` pins the complete model/representation/encoding specification and model
implementation SHA. `Predictor(artifact, manifest_pin)` verifies the artifact and statistics,
strictly loads weights, rejects nonfinite model state and returned predictions, and validates input
dimensions. Missing input values remain encoded through the shared training/inference tensor encoder.
It needs neither the training store nor operator runtime. A model implementation change is not
silently treated as compatible serving code. Older snapshots remain readable; old checkpoint resume
still requires its original code/environment binding, not an automatic migration.

### Generation ownership and recovery

`generations.run(root, name=..., owner=..., store=..., snapshot=..., split=..., config=..., parent=...)`
reserves a name against a content-derived declaration. The owner is a research namespace identity,
not OS authentication. Local locks prevent a second job writer. Attempts/outcomes survive failure;
only completed sealed artifacts get immutable generation records. Repeating a completed generation
verifies/returns it without entering the optimizer. A changed dataset, recipe, code or parent needs
a new generation name; rollback selects an older immutable artifact, never rewrites a champion.
Lookup checks that generation metadata remains consistent with the pinned artifact report, including
the production-ineligible flag and optional native-coordinate frontier. This checks those fields against
the artifact; it does not authenticate the whole local generation record.

`parent` is optional `{ "path": ".../artifact", "pin": "manifest SHA" }`. Warm start requires the
same model and representation spec, loads weights strictly, freezes parent mean/scale, computes
current-corpus baseline/denominators and creates a fresh optimizer. It is distinct from exact-job
resume. Ancestor artifact pins and versioned per-domain coordinate frontiers propagate to children
from the verified pinned parent report;
the compatibility scalar is the primary domain endpoint. Registration and scoring require every
evaluation primary/dependency test interval to start at or after the corresponding frontier. Legacy
standalone v3 artifacts derive frontiers from their pinned split plan; legacy warm-start lineage
without a complete ancestor frontier fails closed. Parent frontiers are read from pinned artifact
reports; the report checks its own plan extent but does not cryptographically attest recursive lineage.
No tensor surgery, silent normalization drift or cross-generation optimizer reuse occurs. Frozen
normalization can become statistically unsuitable as markets drift;
qualification must compare a fresh fit as well as continuation. Corpus/replay policy is the pinned
snapshot membership, not an implicit latest-data-only policy.

### Separate evaluation contract

`evaluation.register` fixes candidate artifact, representation, enrolled entities, interval,
metrics, scope and local registration time before evaluation payload is read. `score` requires a
`research_evaluation` snapshot, the same representation, a closed registered interval and no
candidate/ancestor-seen interval. Training refuses that snapshot role. Superset populations are
rejected rather than silently changing enrollment. Legacy population reports keep missing enrolled
pools in their declared population denominator; record counts, usable records, label statuses,
purged/outside counts and per-pool/day scores are reported separately. Overlapping rows are not
independent episodes; uncertainty is explicitly unestimated. The empirical baseline comes from
training, and no execution/trading result is inferred.
Legacy protocol readback also requires the registered metric identifier, `production_eligible=false`
and a plain nonnegative integer receipt time before opening the evaluation snapshot. These checks
bind protocol fields, not an external timestamp or proof that the protocol was originally emitted by
`register`. Newly registered native-coordinate protocols use
`native_coordinate_macro_pinball_coverage_train_baseline_pool_coverage_v3`: the pinned artifact's
train-frozen empirical quantiles are scored on the same exact-eligible target cells as the model,
with matching per-target `n` when that comparator exists. If training had no exact labels for a target, baseline
`n` is zero, pinball and coverage stay null, and the report says `no_exact_train_baseline`;
evaluation labels never fill or replace that baseline. Existing
v1 and v2 metric protocols remain scoreable and retain their original report shapes; v2 is
baseline-only. V3 reports also include `pool_coverage`: registered, snapshot-present, test-assigned
and exact-scored counts; missing
registered pool IDs; and snapshot-present pools without exact-scored origins (including pools whose
test rows are pending or purged). Missing or unusable pools add no invented exact cells to model or
baseline metrics. The coverage list is bounded by the preregistered pool set because unregistered
snapshot pools are rejected. The metric identifier must be considered when comparing old and new
immutable score receipts.
V3 score bindings include Python, NumPy and PyTorch versions plus the explicit CPU device before
existing-output reuse. Reuse also checks that the sealed protocol copy is the registered protocol,
the report remains completed and production-ineligible, and metric denominators and V3 pool-coverage
counts/lists agree with the pinned protocol. This validates internal consistency; it does not
recompute metric values or authenticate storage against an external authority. Pre-change v3 score
directories lack the environment field and remain immutable legacy outputs; rescore to a new
destination. The binding records the numerical environment, not bitwise equivalence across machines
or library builds. V3 reuse also streams the saved prediction transcript against the deterministic
eligible test-row sequence, checking identity and availability metadata, target masks, exact row
count, prediction dimensions, finite values and ordered quantiles. Reads have a per-row byte cap
derived from expected metadata and prediction dimensions. This detects transcript-to-snapshot
inconsistencies without rerunning the model or metrics; it does not authenticate the transcript
against an external authority.

Native-coordinate V1/V2 score reuse uses the same bounded test-row readback contract with the
current coordinate writer's exact transcript schema: ID, entity, origin, decision time, input
availability, target status/availability, mask, and prediction. It rejects missing, extra,
reordered, duplicate-key, oversized, malformed, nonfinite, or unordered rows without invoking the
model or recomputing metrics. The first coordinate writer already emitted a prediction transcript
(`fd748de`); current reuse fails closed if it is absent. The evaluation source is part of
`code_identity`, so an output bound to an older writer already fails the existing binding check;
this change does not broaden that compatibility. Readback verifies snapshot pairing and prediction
structure, not that altered finite ordered values equal the candidate's predictions.

`retrospective` is a fixed-candidate historical research comparison. The separate
`preregistered_forward_replay` scope requires future registration and a `point_in_time` producer;
its test-role knowledge cutoff must be later than registration, and every test-assigned origin
decision must be strictly after registration, including pending or purged rows. It is not live
forecast-publication or trade-fill evidence. The current OHLCV orchestration uses only retrospective
scope. Registration time is a local receipt, not an external timestamp attestation.
Registration uses the observed local clock; callers cannot supply an earlier registration time.
This is a reproducible local research record, not an externally attested timestamp service.
All evaluation artifacts are production-ineligible. Protected holdouts and existing promotion gates
are not part of this path.

### CLI and one-run orchestration

The existing `build`, `inspect`, `train` commands remain. New commands are
`export-continuous`, `compose`, `replicate`, `generation`, `predict`, `register-evaluation`,
`evaluate` and `pipeline`; use `python -m memecoin_experience COMMAND --help` for file flags.
Build plans can specify `representation={"name":"ohlcv-sequence-v2","horizons":[1,3,6]}`,
`role`, and a v2 `origin_interval_s=[start,end]`. That interval filters output decisions after
loading adequate raw lookback/label overlap. It is included in build identity. Composition never
silently replaces an overlapping old origin with a revision.

A pipeline plan has exactly `name`, `owner`, `index`, `training_source`, `evaluation_source`,
`representation`, `split` and `config`. Source-plan fields are specified in
[`brain-source-architecture.md`](brain-source-architecture.md); their roles must be `train` and
`research_evaluation`. `representation` is `{ "context": 32, "horizons": [1,3,6] }`.
`split` is the existing four absolute-second boundaries; `config` is the trainer recipe.
The evaluation source must start at or after the descriptive training test ends. Enroll populations
explicitly, including unsupported/empty markets; do not auto-select winners from observed support.

```sh
work/ml-venv/bin/python -m memecoin_experience pipeline --root work/research-system-run --plan work/pipeline-plan.json
work/ml-venv/bin/python -m memecoin_experience replicate --source work/research-system-run/experience --target work/worker-replica --snapshot SNAPSHOT_SHA
work/ml-venv/bin/python -m memecoin_experience compose --store work/experience --snapshots INCREMENT_A INCREMENT_B
```

The pipeline's declaration, sealed exports, stage pins, generation, registered protocol and final
receipt make reruns recover the same inputs. It never starts collection or changes owner state.
Its final receipt connects qualified bars, persisted examples, optimizer exposure, artifact and
evaluation pins; a failed/empty stage does not become a completed candidate. Source exports can
be moved; new snapshot identity excludes physical source paths and keeps locator hints separately.

### Qualification scope and remaining implementation limits

Synthetic regression now covers read-only WAL consistency with a concurrent writer, portable raw
requalification, quarantine evidence corruption, empty enrollment, variable feature/target counts,
two model kinds, replica integrity, disjoint composition/overlap rejection, parent warmstart and
exact recovery, standalone inference, separate evaluation roles and end-to-end stage retry.
Full-pipeline capacity tooling is `tools/experience_pipeline_capacity.py`; the original
`tools/experience_capacity.py` also supports `--fit-epochs` for an actual synthetic optimizer run.
Unique load receipts below, rather than a neural market candidate, establish the measured scope.

CUDA remains unqualified. Its workspace setting is validated and recorded, not silently inherited
as an unbound recipe. There is no DDP, cluster scheduler or remote conditional object publication.
The explicit local job/artifact boundaries permit those extensions; they do not certify performance.
Payload memory is bounded; new large snapshots page partition metadata and keep root/permutation
metadata O(catalog pages). Legacy inline snapshots remain O(partitions), and enrollment metadata
is O(pools). Preparation verifies all partitions and spills exact-quantile sorting to disk. Role
readers use authenticated decision bounds to skip unrelated payloads and verify every consumed
partition. Normalization and per-epoch training/evaluation remain real data passes. Continuous
exports still requalify indexed capture history and hold a WAL read view while exporting.
Profile these costs before moving to 100M+ examples. None is disguised as
constant-cost ingestion or unlimited GPU scaling.

Kimi/GLM designs and code reviews were used as fallible inputs. Suggestions to adopt partitions by
boundary IDs, loosen warmstart weight binding, exclude missing markets from enrollment, or
materialize the whole continuous view were rejected. The integrator verified v1 input availability,
atomic export and completed-training reuse against actual code/tests instead of accepting contrary
review claims. Point-in-time label receipt, retained quarantine verification, explicit source
accounting and completed-generation idempotence received focused regression coverage.
Qwen completed an integration review of the pre-paging implementation at `f5aca3d` with no
remaining blockers; its initially incomplete conclusion was obtained in a follow-up. GLM separately
reviewed catalog publication, integrity and iteration. Its concerns about catalog slices and
progress-directory durability were resolved with streaming-only catalog iteration and parent
directory fsync. These are static assistant reviews, independently checked against code and
regressions; they do not replace executed tests or qualify market/GPU behavior. Grok was not used.

The first integrated [100k-bar receipt](evidence/brain-integrated-pipeline-capacity-2026-10-08.json)
is bound to `f5aca3d`: eight synthetic pools, 80,000 qualified training-source bars, 79,944 persisted
examples and 119,856 optimizer row visits across two CPU epochs, followed by separate registered
evaluation. It completed in 41.6 seconds at approximately 337 MiB process peak RSS. A separate
[million-bar writer/reader receipt](evidence/brain-integrated-million-capacity-2026-10-08.json)
measured 72.9 seconds and approximately 217 MiB RSS on the same revision. Both precede catalog
paging and are not silently relabeled as later-code evidence. Timings include concurrent worker
activity and are engineering observations, not an isolated hardware or GPU benchmark.

The [million-bar full-pipeline receipt](evidence/brain-paged-million-pipeline-capacity-2026-10-08.json)
is bound to `2f1eff2`: eight synthetic pools, 800,000 qualified training-source bars, 799,944
persisted examples, 1,199,856 optimizer row visits over two CPU epochs, and separate evaluation
on 200,000 source bars. It completed in 271.1 seconds at approximately 348 MiB process peak RSS.
Despite the filename, this run exercises the paging-capable revision with small inline catalogs:
its eight long histories do not exceed the 1,024-partition paging threshold. Paging correctness is
covered separately; do not interpret the filename as evidence that the large-catalog branch ran.
The reference model is a width-16 GRU with three targets. Synthetic forecast scores demonstrate
the evaluation plumbing, not trading usefulness or adequate model capacity for a large Brain.

The [wide paged-pipeline receipt](evidence/brain-wide-paged-pipeline-capacity-2026-10-08.json)
is bound to `6ae0ea9` and explicitly records catalog shape. Across 2,048 synthetic pools and
1,024,000 raw bars, it qualified 819,200 training-source bars, persisted 804,864 examples in
4,096 partitions/four catalog pages, consumed 1,191,936 optimizer row visits across two epochs,
then evaluated a separate 204,800-bar source in 2,048 partitions/two pages. It completed in
360.0 seconds with approximately 355 MiB process peak RSS and 5.94 GB temporary payloads.
This exercises paged reading and hierarchical training sampling at the real page-size threshold.
It does not establish 100M-scale performance. Temporary synthetic payloads were deleted after
each load run; immutable receipts retain code/tool/identity/count bindings for reproduction.

Final regression checks for this migration: `make check` passed with 1,099 tests and 18 optional
skips, including lint/format and the configured mypy targets; `make smoke` passed. Separately,
the selected broader ML suite in `work/ml-venv` passed 96 tests with five MPS and one CUDA
hardware skips. These test sets overlap; do not sum their counts. GPU hardware and optional
browser acceptance were not established. Both million-scale receipts' code/tool hashes were
checked against their recorded Git revisions.

### Real-data system qualification (2026-10-08)

The fixed [plan](../configs/brain-system-qualification-20261008.json) and
[receipt](evidence/brain-real-system-qualification-2026-10-08.json) qualify the integrated CPU
path on existing development history. The harness/test/plan were committed at `339cb40`; the
independent funnel reporter at `d0f6732`. Raw bundles and artifacts remain under
`work/brain-system-qualification-20261008/` (run root `run01`), outside Git. Reproduce from those
revisions and sealed sources, not by rereading a later live index. No market-data request, production
promotion, protected payload read or owner runtime switch was performed.

All 24 members of the pre-existing continuous universe were enrolled before source export/fits;
none was replaced based on support or scores. Pool and base-mint metadata were disjoint from the
reserved cohorts. This is a survivor-selected, previously studied development population, not an
untouched cold-market benchmark. The new artifacts never saw the October 5–7 evaluation interval
in fitting/validation/descriptive test, but this temporal separation does not erase earlier human
research on the same market history. Historical replay remains explicitly retrospective.

The read-only WAL exporter processed 6,644 indexed captures: 6,625 qualified, 19 quarantined for
duplicate bars within a response, 282,525 duplicate observations and 9,839 revised observations.
All 95,801 indexed firsts in the declared training event window had qualified support; the larger
102,168 owner count covered a later interval and is not a qualification-loss denominator.
Independent accounting reconciled all 95,801 exported bars: 212 outside the origin interval,
667 initial-context losses, 12,486 context losses after gaps, and 82,436 persisted examples.
Expected-slot gaps include pre-birth periods; they are not a provider failure rate.

The base snapshot has 71,548 examples and the disjoint increment 10,888; composition preserves
their objects and sums to 82,436. Both base and cumulative snapshots were replicated, reread and
reused idempotently. Base train has 56,435 usable origins from four markets; cumulative train has
74,680 from eight. The top four still supply 95.62% of cumulative train exposure. The cumulative
split purges 1,050 origins for transitive input dependencies and separately retains target masks.
Removing row caps has not supplied broad market support or solved missing contiguous context.

Three fixed qualification cases used context 32, horizons 5/15/30 minutes, GRU width 128,
60,681 parameters, five full CPU epochs and no recipe search: base `V1`, cumulative `V2-warm`
with frozen parent normalization/fresh optimizer, and cumulative `V2-fresh`. They consumed
282,175 / 373,400 / 373,400 optimizer row visits respectively. The names denote this acceptance
run, not production generations or a large trained Brain. A fourth V1 run was killed with SIGKILL
after a durable checkpoint at step 20 / 10,240 rows. Recovery produced exactly equal weight tensors
and an identical report to uninterrupted V1. All three evaluation protocols were registered before
the separate evaluation export and scoring. Model artifacts were not selected from those scores.

The October 5–7 window yielded 4,310 / 4,290 / 4,260 scored origins for 5/15/30 minutes, supported
by ten of 24 enrolled markets. Pinball values were:

- V1: 0.009613 / 0.018959 / 0.026967.
- V2 fresh: 0.009552 / 0.019262 / 0.026535.
- V2 warm: 0.010094 / 0.018616 / 0.025564.

All beat their own train-only empirical baseline on this window. Warm continuation versus V1
worsened 5-minute loss by about 5.0% and improved 15/30-minute loss by 1.8%/5.2%; improvement is
not uniform. Warm q10–q90 coverage was 79.7% / 75.0% / 75.7%, and fresh 30-minute coverage only
66.4%. These dependent historical origins establish neither independent uncertainty, calibration,
stable generation improvement nor executable edge. Per-market/day strata and missing-market
denominators remain in sealed evaluation artifacts. No repeated tuning on this window is justified.

One simultaneous generation admission encountered the shared registry's nonblocking lock before
creating a training attempt. Resubmitting that unstarted case after lock release succeeded;
training ownership was preserved. Callers must handle this busy signal; the local registry is not
a distributed scheduler. Per-fit wall time was not instrumented, so this receipt does not estimate
GPU cost or training throughput. CUDA hardware was unavailable; its acceptance remains open.

`tools/experience_qualify.py` provides `prepare`, `fit --case`, `recovery`, `register`, and `score`
phases with `--root`/`--plan`; source phases additionally require `--source`. It binds the fixed plan,
source code and harness hash, and refuses changed-run reuse. `tools/experience_qualification_report.py`
reconciles the source/snapshot/report pins and independent context-loss accounting. The new
synthetic regression exercises composition, three fits, repeated registration/scoring, empty-market
enrollment and immutable plan/source rejection. A bounded GLM test inventory helped plan the
acceptance; its mistaken historical-availability and test-coverage claims were not adopted.

Final checks for this qualification tooling: `make check` passed (1,100 tests, 19 optional skips,
lint/format/mypy), `make smoke` passed, and the selected broader ML suite passed 98 tests with
six hardware skips. Test sets overlap. The receipt's source/harness/reporter hashes were checked
against their recorded commits. Owner metadata, configs, deployment and champion matched preflight;
operator stayed paused and source STOP stayed present. This did not re-attest the running binary.

### Next qualification boundary

The next acceptance unit is a broader, independently enrolled non-protected population and a real
compute worker. Use sealed read-only exports, keep unsupported pools in coverage denominators, measure
raw-to-qualified-to-persisted-to-optimizer conversion, and attribute each loss to its stage.
Repeat cumulative snapshots and parent-bound next generations at that broader scale, including interrupted-job
recovery and replica verification, before judging improvements against the train-only baseline.
Register candidate/population/window before scoring. Historical backfills remain retrospective;
future-availability claims require a qualified point-in-time producer and actual advance registration.

On a real CUDA worker, measure preparation, I/O, optimizer throughput, memory and checkpoint
recovery separately using the same snapshot/artifact contracts. DDP and remote publication need
their own acceptance when implemented. Broader modalities require semantic source qualification,
not just adding feature names. Forecast improvement and executable trading usefulness remain
separate questions: costs, liquidity, execution and independent temporal/population coverage are
not established by these engineering fixtures. No promotion follows automatically from any run.

## Historical reproduction map

Receipts live in `docs/evidence/` and sealed `work/` roots on the research machines. The receipt
index is [`evidence/README.md`](evidence/README.md); open only the relevant artifact.

| Result | Machine evidence |
|---|---|
| ML foundation / device smoke | `ml-foundation-2026-10-03.json` |
| B12 curriculum comparison and exam | `brain-curriculum-comparison-2026-10-04.json`, `brain-curriculum-exam-2026-10-04.json` |
| 86-pool expansion and latest candidate audit | `brain-training-expansion-2026-10-06.json`, `brain-post-training-audit-2026-10-07.json` |
| Sampling causal ablation | `brain-sampling-ablation-2026-10-07.json` |
| Sampling x budget continuation | `brain-budget-ablation-2026-10-07.json` |
| Horizon-head ablation | `brain-horizon-ablation-2026-10-07.json` |
| Train-only LR diagnostic / clipping audit | `brain-lr-diagnostic-2026-10-07.json`, `brain-clipping-audit-2026-10-07.json` |
| Benchmark readiness / cold acquisition | `brain-benchmark-readiness-2026-10-07.json`, `brain-research-envelope-2026-10-07.json` |

Source modules: `src/memecoin_operator/learning.py`, `src/memecoin_curriculum/{design,training,benchmark}.py`,
`src/memecoin_ml/{data,models,training}.py`. Exact code/data/protocol hashes are inside receipts.
For reproduction, checkout the recorded source commit and use its immutable protocol bytes;
do not rerun old experiments from current code or overwrite sealed output roots.

## Diversity and committed optimizer exposure

`memecoin_population.diversity.audit` pins population, snapshot, split, representation, optional
coverage plan and a declared policy. Generic marginal/cross axes separate enrollment entity-time,
available rows, usable origins and per-target support. Unknown metadata remains explicit.
`at_decision` uses only knowledge available at the origin; `at_cutoff` is descriptive retrospective
stratification, never a model input. Entropy/Simpson effective counts measure concentration, not
independent samples or learned capability. Explicit report/require gates do not resample rows.

Training/generation plans accept optional `diversity: {path, pin}`. A required failing audit blocks
training before optimizer work. The artifact binds a portable audit report and its original manifest.
The full-pass objective is unchanged: a batch averages masked quantile loss per present horizon,
then averages present horizons. This is not a globally uniform per-label objective. Per-step exposure
records both actual row/target draws and the mass this batch objective assigns to each declared
stratum. Journal heads are checkpoint-bound; identical replay is idempotent. Large group aggregates
live in disk-backed sidecars, not checkpoints. Exposure means committed optimizer inputs, not
physical retried compute, gradient magnitude or causal influence on the selected best checkpoint.

Evaluation registration optionally accepts `cohorts: {store, population, config}` instead of an
inline pool list. The population must have `research_evaluation` role and cutoff no later than
registration. Config pins `entity_prefix`, `knowledge`, `axes`, `time_bucket_ns`, `selection`.
Scoring takes `population_store` as a transport locator and revalidates the pinned contract.
Enrollment, zero-support groups and per-target support stream to sealed JSONL sidecars; generic
strata receive the same unreweighted pinball/baseline/coverage metrics. No claim of independence,
calibration or edge follows from these counts. The evaluation snapshot must contain only enrolled
entity IDs, including its non-test records; test origins must also lie inside valid enrollment.

The existing source-driven `memecoin_experience.pipeline` accepts optional `diversity` audit
arguments and `cohorts` registration arguments. It binds the audit before generation ownership,
propagates exposure into the receipt and registers cohorts before reading evaluation payload.
Generic representations use the same `audit`, `generations.run` and `evaluation.register/score`
interfaces directly; the OHLCV convenience pipeline does not define the data model boundary.

Policies may add `axis_limits` entries with `axis` (one of the declared axes),
`min_known_values`, `max_share`, `max_unknown_fraction`. These gate usable training support,
not merely enrolled groups; unknown groups do not satisfy `min_known_values`. Per-target status
counts preserve missing/maturity/purge denominators alongside usable masks. The declared objective
remains independent of these distribution requirements; changing it requires a distinct trainer
contract and matching exposure accounting.

### Diversity qualification, 2026-10-08

Frozen harness/source revision `47c72ae` produced two immutable receipts (later generic axis gates
and status-denominator reporting at `4ffde3e` have focused regressions; the load figures below belong
to the frozen revision):

- [`brain-population-million-capacity-2026-10-08.json`](evidence/brain-population-million-capacity-2026-10-08.json):
  10,000 declared synthetic markets, 20,000 temporal metadata facts, 520,000 sparse work cells,
  1,000,000 generic flow/depth examples. Intentionally unsupported 2,000 markets stayed enrolled;
  8,000 supplied usable training inputs. Planning took 11.18 s, building 28.62 s, audit 49.20 s.
  One CPU epoch used 616,000 rows in 602 steps. Audited/plain fits took 78.99/52.97 s and produced
  exactly equal tensors and test metrics. Peak process RSS was 359,251,968 bytes, including torch;
  total run 221.67 s. The 115-parameter synthetic model measures plumbing, not useful learning.
  Row-draw effective Shannon count was 8,000; objective-mass count was about 7,998.23 because the
  final short batch has the same loss normalization as full batches. These are concentration
  diagnostics, not effective independent observations. Journaling overhead was about 49% of the
  tiny baseline fit; larger training throughput, GPUs and 100M-scale storage remain unqualified.
- [`brain-population-real-diversity-2026-10-08.json`](evidence/brain-population-real-diversity-2026-10-08.json):
  read-only reuse of the prior sealed 24-market development inputs. 74,680 usable train origins
  still come from eight markets; four supply 95.6227%. Entity effective Shannon/Simpson counts are
  4.35/3.89. Descriptive lifecycle classification places 92.37% of training origins after seven
  days of age. All venue/lifecycle labels are unknown under `at_decision`, because these legacy
  enrollments were learned after the training interval. No label was backdated or fed to a model.
  Within the declared population intervals there are 95,580 qualified bar opens out of 100,253
  expected slots, across 361 cells: 241 covered, 120 with deficits. The separate swaps stream has
  361 unobserved cells and no invented expected event count. Neither a success response nor a
  checked grid slot count proves complete market experience or independent population selection.

The real-data diagnosis is therefore broader than filling gaps: these recent survivor-selected
markets have highly unequal available lifetimes inside the training window. Backfilling the same
24 cannot produce a broad historical census. The next campaign should enroll populations and
periods independently of download success/outcomes, retain unsupported/failed/disappeared entities,
reconcile provider request/budget ownership, and qualify new experience producers. Keep original
coverage, usable corpus and optimizer objective mass separate when testing whether diversity/data
improve future capability. No new fit or score search on the already studied real evaluation window
was performed in this stage.

Reproduce from `47c72ae` with `tools/population_qualify.py scale --entities 10000 --per-entity 125
--fit --root <new-directory>` in the optional ML environment, or `real --inputs
work/brain-system-qualification-20261008 --root <new-directory>` in the core environment. Original
sealed payloads remain in `work/brain-population-scale-20261008/` and
`work/brain-population-real-20261008/`; receipts were moved unchanged to the canonical evidence
paths above. The real-mode harness pins and requalifies the previous qualification's allowed
sources and uses its snapshot; it never opens protected payloads or calls a provider.

Final regression state at `4ffde3e`: `make check` passed (1,107 tests, 21 optional skips,
lint/format/mypy), `make smoke` passed; selected ML/experience regressions passed 86 tests with
one CUDA skip. Sets overlap. New cases cover temporal metadata ambiguity/unknowns, paged append,
compaction/replication/corruption, sparse worker partitions, stream identity, count semantics,
required diversity gates, zero-support cohorts, pipeline resume, checkpoint-journal corruption,
uncheckpointed step replay, partial-horizon objective mass and portable artifact binding.

### Input-only admission views

`python -m memecoin_experience admit --store STORE --snapshot PIN --plan JSON` creates an
immutable eligibility view. The JSON contains `policy` and optional `selection`. Current policy:
`{"name":"causal_activity_v1","min_positive_volume_fraction":0.25,"min_context_volume_usd":100}`.
It reads only OHLCV input frames with explicit USD/indicator semantics; it cannot inspect targets,
future support, survival or current liquidity. These thresholds define a research objective, not
proof that rejected markets are worthless or that accepted prices are executable.

Views retain every physical row and target status. One byte per row records eligibility/reason in
a separately hashed object, bound to the original partition and policy implementation. Feature and
target columns are shared; training, normalization, diversity and exposure consume the same mask.
Optional fixed entity selection applies before a declared decision timestamp, allowing narrower
training with identical later validation/test populations. Compose raw snapshots before admission;
composing or stacking admitted views fails explicitly. Replication includes mask objects.

The opt-in censored-count objective applies the v2 admission mask to positive lower bounds as well
as exact labels. Rejected rows remain in custody and denominators but contribute neither optimizer
mass nor validation selection mass; admitted bounds remain separate from exact labels, and rejected
v2 censored-count rows receive a distinct `input_admission_rejected` denominator bucket. A synthetic
regression covers train/validation/test batching and `prepare` denominators. This closes an input
eligibility bypass and makes no model-quality claim.

Evaluation of an admitted view requires the same input policy in its preregistration; entity
selection is prohibited for evaluation views. Always report structural and input-qualified support
separately. This is admission, not resampling or equal market weighting.

OHLCV producer version 3 (`ohlcv_schema(..., stable=True)`) uses stable log ratios for extreme
positive finite prices and an overflow-safe past volume average. Version 2 remains the default.
The opt-in training recipe `normalization="constant_feature_scale_one_v1"` gives exactly constant
features scale 1 instead of amplifying later unseen variation by 1e6; other scales retain the 1e-6
floor. These choices are included in representation/recipe identities. Legacy defaults are retained.

`tools/population_learning.py --campaign CAPTURE_ROOT --root RUN_ROOT` binds a frozen acquisition
plan, source code and harness before conversion. It builds separate train/evaluation source bundles,
raw and admitted snapshots, population audits and owned model generations. The narrow model uses a
preselected entity subset only during training; later validation/test origins are shared with the
broad model. The additional narrow control repeats epochs to approximately match broad row draws
(rounded whole epochs, bounded at 500); this is not an exact FLOP match. Report actual steps, draws,
training mass and support alongside losses.

Candidates and structural/input-qualified evaluation protocols are registered before opening the
new evaluation-role source. Comparisons require identical snapshot and origin/mask identities.
Both legacy v1/v2 and native-coordinate v3 scorers census the verified snapshot before scoring, then
reconcile the ordered exact-usable origin/mask stream and per-target cell counts against emitted
predictions before publishing a report. This catches pass-to-pass omissions or substitutions with
constant-memory digests; it does not authenticate mutable storage during a run.
Paired uncertainty resamples whole observed base mints, combining pools for the same mint, and reports
micro and equal-mint descriptive differences. It is conditional on one trainer seed and the fixed
historical cohort; it does not establish prospective performance, causal value of diversity alone,
execution quality or statistical independence across correlated mints. Empty roles/horizons and
infeasible controls are recorded as failures, never successful models or zero losses.

A later recipe uses `--learning-plan JSON` and a new run root. The
`population-learning-plan-v1` declaration binds the original acquisition pin, complete protocol,
reason and evidence pins. It is saved before conversion/fitting, cannot relabel acquisition roles
or silently replace an existing run, and explicitly loses the pre-acquisition recipe claim.
Check support across whole calendar cohorts before reserving expensive compute: the harness now
reports empty train/validation/test roles before fitting and never opens evaluation payloads when
no candidates completed. An already accessed evaluation cohort remains retrospective on reuse.

`tools/population_campaign_report.py --campaign ROOT --root RUN --output JSON` independently
reconciles the terminal ledger and sealed captures, qualified source/bar/context counts including
zero-support entities, per-pool persisted contexts, admission/split counts, committed row draws,
model artifacts and paired evaluation summaries. It performs no requests, fitting or selection.
Infeasible runs remain explicit reports; an empty comparison list is not evidence of improvement.

### Independent birth-population learning qualification, 2026-10-08

Receipt: [`brain-independent-population-learning-2026-10-08.json`](evidence/brain-independent-population-learning-2026-10-08.json).
Payload root: `work/brain-diverse-campaign-20261008/`. Acquisition at `a994c9e` completed
1,228 distinct requests for 307 independently birth-enrolled base mints (170 May–August training
source; 137 September evaluation). There were 1,227 received responses and one sealed timeout,
explicitly reconciled without retry. The user pause/resume preserved the ledger prefix and STOP.
Six received responses were quarantined for duplicate bars; all markets remain enrolled.

The original fixed learning protocol at `73363fe` was **infeasible**: Aug 8/11/15 split boundaries
left zero usable validation/test rows. No candidate fitted. Its frozen harness nevertheless
qualified the evaluation source and built its admission view; no model scores existed. This is
preserved evidence, not a successful experiment. After inspecting only train-role calendar support,
a new declaration at `24ae9ae` used May–June train, July validation and August descriptive test,
with unchanged representation, quality policy, preselected narrow population, model recipes and
September evaluation bounds. `learning-plan02.json` binds the failed result/support evidence;
`learning02/` is a separate run. This revision and the earlier one-response metadata/shape inspection
mean the evaluation is retrospective, not untouched. Do not tune more candidates on it.

The three 60,681-parameter CPU GRUs used 32 frames, 12 features and 5/15/30-minute return targets:

- Narrow fresh: 140 usable training rows from two markets, five epochs, 700 row draws / five steps.
- Broad fresh: 3,990 rows from 14 markets, five epochs, 19,950 draws / 40 steps.
- Narrow compute control: 143 epochs, 20,020 draws / 143 steps. Approximate row-draw matching is
  not equal FLOPs or equal optimizer opportunity. All models shared 1,171 usable July validation
  and 3,505 August test rows; no score-based recipe or admission-policy search was performed.

On September observed support, broad pinball was 0.025848 / 0.039153 / 0.046405 for 5/15/30 minutes.
It improved over narrow fresh by 49.27% / 50.35% / 48.45%, and over the repeated narrow control by
14.18% / 20.46% / 44.30%. Paired whole-mint bootstrap micro and equal-mint intervals exclude zero
for these six comparisons, conditional on one trainer seed and this fixed historical cohort,
without multiplicity correction. Support is 6,428 / 6,356 / 6,261 origins from 30 / 27 / 25 mints,
out of 137 enrolled. Input admission removes no evaluation rows; structural and qualified metrics
and paired comparisons match, while the latter reports additional admission counters.

**The broad neural model still loses to its own train-only empirical quantile baseline by 35.38% /
19.37% / 13.30%.** Its nominal 80% intervals cover 98.48% / 97.03% / 95.27%. This is evidence that
broader usable data helped these neural controls on this replay; it is not evidence of sufficient
conditional forecasting skill, calibrated intervals or trading usefulness. More market names did
not produce broad learning exposure: one market supplies 55.36% of broad draws, effective Shannon
entity count is 4.49, and May supplies 96.07% of train rows. Admission was causal, but observability
and contiguous-context selection still condition the learned population.

Checks at `24ae9ae`: `make check` 1,130 passed / 23 optional skips, lint/format/mypy passed;
`make smoke` passed; optional ML regressions 38 passed / one actual-CUDA skip (sets overlap).
Independent reconciliation rechecked all raw capture seals, source/context counts, generations,
actual draws and paired summaries. All three extracted source archives match their archived files.
No new million-row/GPU/cluster test occurred in this campaign. Owner read-only fence remained paused;
no champion, production runtime or protected payload was changed.

Next priority is qualified sparse/event-time and lifecycle experience that can retain learning
signal outside uninterrupted active-bar sequences, with explicit gaps and observation-process
semantics. Assess feature-conditioned skill against empirical baselines on fresh populations;
do not scale this GRU or repeatedly extend the same survivor-conditioned bar corpus and call it
Brain growth. Representation changes must not invent zero activity/returns where capture support
is unknown. Richer sources, independent fresh evaluation, multiple seeds and GPU qualification
remain unproven.

### Sparse/event-time engineering qualification, 2026-10-08

Receipt: [`brain-sparse-experience-2026-10-08.json`](evidence/brain-sparse-experience-2026-10-08.json).
Frozen implementation/harness: `f9a0c1f`; root `work/brain-sparse-qualification-20261008/`.
`tools/sparse_qualify.py` declares the job before publication and fitting. Subsequent harness
recovery at `86c33da` preserves first-run measurements and rejects semantic checkpoint changes;
the qualification's frozen code and completed artifacts are not rewritten.

The producer emits at each qualified event and optionally each evidenced lifecycle transition,
using at most a declared number/age of past observations, with explicit short-history padding,
event age, gaps and observation-support features. It supports exact endpoint log returns and
complete-process event counts. Input availability, target availability and maturity remain separate.
Arrival-time mode uses availability as the decision clock; real backfill remains historical replay.
The existing generic training, normalization, generation, prediction, evaluation and exposure
contracts consume these samples without a new source-specific trainer.

Only the previous campaign's **train-role source** was converted; no September evaluation payload
was opened or rescored. Its 38,705 qualified observations plus 170 evidenced births now yield
38,875 origins across all 170 enrolled markets, versus 8,792 contiguous-context origins across
49 previously. The same May–June / July / August role boundaries admit 16,616 / 4,348 / 10,504
origins with at least one observed endpoint, from 46 / 26 / 93 markets. Five markets have no usable
endpoint target but remain enrolled and represented. Birth-only origins lack price targets.

Across all three 5/15/30-minute targets, 74,804 cells are observed, 41,282 lack an exact endpoint,
510 have no origin price, and 29 are administratively censored. Missing future prices remain
unknown, not zero. The adapter asserts no complete observation windows and infers no death from
silence. Thus real lifecycle-only histories currently retain evidence but supply no price loss;
an appropriately qualified event/count or survival objective is still required for that signal.

One 5,577-parameter CPU engineering generation completed one epoch: 16,616 actual row draws,
33 optimizer steps, 46 exposed markets; largest draw share 20.20%, Shannon effective count 13.40.
The previous admitted bar run had 3,990 origins, 14 markets and 55.36% largest share, but this is
**not a controlled learning comparison**: representation, context eligibility, targets, recipe and
admission differ. This run uses qualified bar validity, not the old contiguous-context volume
admission rule. It establishes conversion/training plumbing, not forecast improvement or edge.

Synthetic load retained 2,000 entities and 117,664 facts, emitting 113,664 origins through paged
source and experience catalogs. Of the count labels, 90,969 are exact and 22,695 remain censored.
Publication plus projection/census took 55.78 seconds, peak process RSS 66,584,576 bytes (about
63.5 MiB). The real conversion and CPU fit took 85.10 seconds, peak RSS 404,750,336 bytes including
ML libraries. These are local measurements, not million-row sparse, GPU or cluster qualification.
Dense context snapshots duplicate history by design; immutable sparse facts remain the canonical
input for future packed/ragged or multistream representations.

Focused tests cover delayed arrivals, future-fact isolation, explicit zero-event supervision,
unknown/failed/stopped/unsupported censoring, role-before-payload rejection, integrity, support
conflicts, idempotent publication/replication, stable historical partitions, full dependency
purging, exact training resume, optimizer exposure, portable prediction and synthetic registered
evaluation.

### Identified lower-bound count objective (2026-10-08)

`partial_count_lower_bound_pinball_v1` is an opt-in research objective for `observed_process_count`
targets. Existing `masked_pinball_v1`, v1/v2 representations, five-item batch API and exact-label
mask are unchanged by default. A count with complete named-process support, including zero, receives
ordinary pinball loss. An incomplete interval keeps `target=None`; a positive count of qualified
observed events supplies only the inequality `Y >= L`. A zero bound, pending target, missing bound
availability or unavailable point-in-time bound supplies no loss. Positive v2 bound availability
and target maturity must be at or before the pinned snapshot cutoff; point-in-time data also
requires bound availability before the role cutoff. Historical v2 snapshots remain readable, but
training excludes bounds whose receipt or target maturity exceeds their snapshot cutoff. Input
purging remains enforced. Composed snapshots use the minimum cutoff over hash-verified transitive
parent manifests for censored-bound eligibility. At each composition node the resolver checks that
child partition pins exactly equal the deduplicated union of declared parent catalogs; conflicting
duplicate-pin metadata, missing members or extra members fail closed. A disk-backed SQLite index
bounds memory, while temporary disk use scales with catalog size. This proves manifest/catalog
lineage, not provider truth or mutable backing-store integrity; an externally re-pinned root that
erases every composition indicator cannot be distinguished without external provenance. Bounds
absent from all legacy parent catalogs are checked against payload only when that partition is read.
The minimum can conservatively omit valid later-parent bounds; exact per-partition cutoff lineage
remains a future contract. `prepare` resolves lineage for v2 count schemas even with the default
masked objective; censored batching resolves it before iteration. The resolved cutoff is cached for
the lifetime of a `Snapshot`, assuming its backing store is immutable during use.

Replicating a composed v2 count snapshot validates this lineage before publishing its top-level
manifest, copies each ancestor manifest and paged catalog object, and copies the descendant's
verified partition payload union once. A restored replica therefore resolves the same conservative
cutoff and censored batch membership. Missing or corrupt source ancestry fails before that snapshot
is published at the destination. Before successful return, replication reopens the destination and
checks that it resolves the same cutoff. Publication is not a transaction across all files: a
destination-side readback failure after the top manifest is published can leave that manifest in
place. Synthetic direct and nested paged-composition checks cover idempotence and batch identity.
This preserves declared local lineage; it does not authenticate external source truth.

For quantile `q`, the censored-cell term is `q * max(L - prediction, 0)`. This is a per-quantile
lower-envelope surrogate for compatible completions, not a proper score, survival likelihood,
consistent estimator of full count quantiles, or upper bound on forecast error. Predictions above a
bound are unresolved; arbitrarily high predictions can achieve zero loss on all-censored labels.
Training and validation reject non-finite model outputs before applying this surrogate or inverse
target transforms, then check the converted raw-count values for overflow. Otherwise positive
infinity could appear as zero bound violation, or a finite but extreme log-count output could
overflow its inverse transform.
The count head uses `log1p` while fitting and maps ordered predictions back to nonnegative event-count
units for reports/inference. Exact empirical baselines and observed-subset evaluation remain separate;
when a target has no exact train labels its baseline is `null` only if that same target has eligible
positive training bounds; an unsupported head fails before fitting. Exact pinball is reported with
`n=0`. Before fitting, every target also needs validation support from exact labels, or from eligible
positive bounds for the opt-in count objective; otherwise checkpoint selection would silently omit
that head. The statistics record the per-target exact, bound, and selected validation support.
Test-set targets with `n=0` remain descriptive and are not a selection gate. Validation checkpoint
selection uses the same quantile-weighted bound term as training and
reports `partial_identification_objective_space_v2_quantile_weighted`; prior v1 selection scores
used unweighted bound violations and are not comparable. Raw `partial_count_bounds` diagnostics stay
in original count space with unweighted quantile violations. Existing artifacts and their selected
checkpoints are immutable and are not rewritten under the corrected metric. The `objective_usable`
population count follows the selected recipe in train, validation and test; `partial_count_usable`
remains a separate descriptive count of eligible lower-bound rows.

Synthetic engineering qualification shows censored-only count targets can pass through immutable
snapshot, objective fit, stop/resume, evaluation and standalone inference without manufacturing an
exact label. It is plumbing evidence, not a learning-gain comparison. The existing real train source
has 170/170 support intervals unknown, so its observed counts can contribute positive lower bounds
but cannot teach observed inactivity or establish complete event-process coverage. Event-triggered
origins still sample observation intensity; no source qualification, fresh independent comparison,
or learning gain is established. See [`brain-censoring-objective-2026-10-08.json`](evidence/brain-censoring-objective-2026-10-08.json)
for source counts, exact checks and limitations. This stage passed `make check` (1,147 passed, 24 optional skips; Control Center Python 39 passed), `make smoke`, `make ml-check` (17 passed), `make runner-check` (18 passed), and the focused sparse/learning suite (42 passed, one actual-CUDA skip).

Checks: `make check` 1,146 passed / 24 optional skips, lint/format/mypy and smoke passed; optional
ML regression set 60 passed / one actual-CUDA skip. The final protected-namespace guard at `b975471`
has focused coverage after the full-suite start. Independent reconciliation verifies frozen code,
source/snapshot counts and hashes, the real generation and exposure chain, source/snapshot replicas
and finite standalone predictions. Qwen audited receipt arithmetic; GLM reviewed selected temporal
boundaries. Neither helper output substitutes for these checks. Initial real execution failed closed
on a transcribed incorrect source pin; a separate corrected declaration/root preserves that failure.

Event-triggered origins still sample observation intensity, not uniform wall-clock market exposure.
Enrollment, source coverage, origin eligibility, target observability and optimizer weights remain
separate denominators. Any change to sampling or loss needs its own registered objective; retaining
more sparse origins does not automatically remove observation-process selection bias.

Next: verify account-level quota before registering any acquisition; qualify a real event source with
independent coverage reconciliation and explicit stopped/failed/unsupported intervals; then run
fresh preregistered populations and multi-seed comparisons against empirical baselines. More retained
observations or bound-compatible optimization do not alone establish useful conditional learning.

### Brain Senses qualification boundary

The opt-in `memecoin_experience.qualification` module binds a versioned process descriptor and
immutable receipt to source/decoder/qualifier/input pins and a sealed paged `scope_pin` of explicit
entity/interval members for one pinned population and role. Each member keeps its own availability,
status, accounting, capabilities and evidence; aggregate partition counts never imply complete
support for all members. The process pin resolves payload-schema fields/units/denominations plus
versioned identity, event-time, interval and revision rules; timestamp precision may be unknown, and
provider location is excluded. Counts use `candidate_process_records`; projection revalidates exact
membership against the receipt. An opaque hash or self-declared all-zero receipt cannot grant
complete support or a partial count. The registered synthetic fixture verifier re-executes against
the fixture and process/member pins for complete and partial fixtures, with its implementation hash
bound into the proof and required as the receipt qualification-code pin. Verifier reads repeat
publisher checks so hand-constructed fixture pins cannot bypass interval, schema or finite-value
validation. Synthetic source identity derives from process and scope pins; the decoder identity is
registered. Fixture publication checks finite schema-compatible values and process
rules use a fixed supported set. Other statuses remain
declarations with no projected counts. Observation projection binds the destination process, stream,
payload schema, fields, units and modality. Full receipt/scope validation runs on publication and
first use/import and is cached by immutable dependency identity, including file identities for every
fixture dependency. Cache-hit invalidation currently stats each verifier fixture, so lookup cost is
linear in verified fixtures; large-scope performance is not established. No real-source completeness verifier is available. A
complete qualified zero requires zero rejected and unresolved inputs and the member's
`complete_support` capability. Partial maps to `unknown` in the strict v1 observation-support schema, while population
coverage retains `partial` and its observed bound. Event-count coverage keeps expected null even
under complete support. Missing, unknown, failed, stopped and unsupported cases do not become exact
zero targets; lifecycle evidence remains separate. The validator checks identity, accounting and
scope consistency, not provider truth or domain semantics. Synthetic fixture coverage includes a
page-boundary case just over one page; it is not a capacity claim. No real market source was newly
qualified and no market-data acquisition was performed. No learning or execution claim follows from
this interface work.

### Brain Senses scheduled episode prototype (2026-10-09)

The opt-in `memecoin_experience.sensing` module adds `publish_schedule`, `publish_source`,
`materialize_episode` and `budget_dry_run` without changing `sparse.materialize`. A schedule is
independent of events and support and is sealed as sorted content-addressed pages. The sensing
wrapper binds the observation source, population/role, process-contract pins, receipt pins and
fixture-source pin. Publication and each materialization re-run the exact observation-event/support
reconciliation against receipt-verified synthetic fixtures; the wrapper or a matching hash alone
does not qualify facts.

The point-in-time materializer streams origins into `storage.build`, with `available_ns <=
decision_ns` for input history. It reserves the final context frame for the schedule origin, with
per-stream event values, missing-value masks, age/presence and six support-state channels. Support
intervals are dependencies from their valid-time start. Count targets use `(decision_ns,
maturity_ns]`; complete support yields an exact count including zero, while incomplete or
enrollment-truncated windows retain a censored lower bound in metadata. Endpoint price targets
require one qualified observation exactly at both the scheduled origin and future maturity, each
available at its allowed time; stale-origin substitution is rejected. Target maturity remains the
full requested horizon. A schedule page is explicitly treated as available at its decision time.

A 2026-10-15 regression review found the last retained history event could be placed in the
reserved final frame. The corrected offset places all at-most `context - 1` history events before
the schedule-origin frame; the regression test checks that an available history event remains in
the preceding frames while event/presence channels in the final frame stay empty.

The synthetic end-to-end test binds two process streams to separate receipts, retains quiet origins,
checks a zero count, a positive partial lower bound that remains censored with a masked target,
event-window boundaries, and replay identity after source replication. A repeated-enrollment entity
proves the snapshot does not bridge the interval gap. This checks the snapshot contract that the
existing opt-in censor objective consumes; no model fit on this sensing snapshot was performed. The
dry-run budget function uses request identities, a reservation ledger,
unit-matched cost evidence and verified account entitlement; usage without an entitlement blocks.
Ambiguous or unpriced outstanding attempts block the shared account pending reconciliation. It
records decisions only and has no dispatch path.

Measured synthetic schedule profile: 1,025 sorted origins, 512 rows/page, three sealed pages;
0.041 s and 278,257 bytes peak traced Python allocation in one local run (Python 3.13.6, arm64).
This measures only schedule publication at this fixture size; it is not a throughput or capacity
claim. Observation indexes support multiple nonoverlapping enrollment intervals and reject events
or support that cross an interval boundary; a focused sparse test proves target history does not
cross a re-enrollment gap. No real market-event source was qualified by this synthetic episode,
and no learning or execution result was measured.

### Frozen controlled-signal fit (2026-10-15)

One CPU GRU fit used a preregistered deterministic binary feature → count mapping, 88 exact train
labels plus eight positive censored lower bounds, 32 validation and 32 temporal-test labels, and a
separate registered evaluation cohort. The fit completed and produced finite predictions. Registered
evaluation enrolled 64 synthetic entities but only 63 exact labels were eligible: the first input
dependency began one second before the registered window and was correctly purged. The exact-64
acceptance gate therefore failed. On the 63 eligible rows, raw-count macro pinball was 0.00272
versus 0.47196 for the train-only unconditional empirical quantiles and 0 for the known deterministic
oracle; these descriptive values do not override the failed denominator gate. The frozen fixture was
not repaired or rerun after exposure. This does not qualify conditional learnability and says nothing
about market prediction. See the [preregistration](evidence/brain-controlled-signal-learnability-prereg-2026-10-15.json)
and [result receipt](evidence/brain-controlled-signal-learnability-result-2026-10-15.json). The
default test suite now audits these pinned receipts read-only and does not repeat the exposed fit.

### Training role-isolation sampler audit (2026-10-15)

The legacy research sampler seeds each partition's row permutation from its content pin. A frozen
synthetic paired audit changed only test values and labels inside four mixed-role partitions while
keeping train/validation rows, IDs, partition geometry, recipe and train-only statistics fixed. The
training ID order and final CPU weights changed. This confirms that payload reserved for descriptive
test can influence training order when storage partitions mix roles. It does not demonstrate a
market-data effect or model-quality change. The opt-in `role_isolated_partition_v1` sampler now derives seeded
partition and row permutations from stable ordinals instead of mixed-role content pins. On the same
paired fixture it kept train order, train-only statistics and final weights identical, and exact
CPU interruption/resume reproduced the uninterrupted weights. This guarantee requires unchanged
train-role membership, row ordering/IDs and partition geometry; validation data remains allowed to
select the best checkpoint. The default remains `content_pin_v1` for compatibility. The package-wide
code identity changed, so unfinished checkpoints created before this sampler change fail their
code-binding check; published artifacts were not modified. See the
[legacy audit](evidence/brain-training-role-isolation-legacy-result-2026-10-15.json),
[opt-in sampler result](evidence/brain-training-role-isolation-result-2026-10-15.json), and
[preregistration](evidence/brain-training-role-isolation-prereg-2026-10-15.json).

### Positive values qualification (2026-10-09)

`qualification-observed-values-v2` is an independent capability for immutable positive facts. Its
registered Solana signature verifier replays sealed provider response bodies, checks the exact
normalized source, and accounts for captured rows, event-time-resolved rows and rows lacking
supported event time. Its only grant is `observed_values`; it does not create membership, support,
population coverage, complete support or an exact-zero target. Point-in-time availability remains
the local response receipt time; historical `blockTime` is the event time and is never used to
backdate availability. A value-only source cannot be indexed into a market experience without
independent enrollment. The 1,000-row Free fixture exercises verifier scale at one maximum-size page;
it does not qualify archive retention, market transaction decoding or complete event counts.

The real Alchemy signature captures therefore remain positive address-reference observations under
unknown support. They do not create objective-eligible count labels or a real-source fit. A bounded
16-transaction sample exposed an 8-byte event-layout drift; the pinned official Pump IDL update
resolved it, and all 33 candidate events now parse exactly. The transactions remain quarantined on
remaining-account and Token-2022 semantics, with one additional transfer mismatch, so they do not
provide market-event supervision. The synthetic lower-bound objective remains usable when a
producer supplies a valid positive bound and its availability/role gates pass; no source completeness
or learning gain follows from these receipts. See [`brain-source-architecture.md`](brain-source-architecture.md)
and the linked Alchemy receipts.

PumpSwap v1 `buy_exact_quote_in` Token-2022 references also remain outside learning admission. The
offline development census found 1,234 quote-side refs, but historical mint/account extensions and
bank-time fee configuration are unavailable for every row, and 1,233 have outside-subtree touches to
target token accounts. No amount decoder, exact label or objective change follows from this census.
See the [captured-class contract and receipt](brain-source-architecture.md).

A frozen PumpSwap sell23 ledger v2 now scopes full-transaction legacy-token account effects. On fresh
disjoint 50-row development/audit samples, 18/20 had exact full touched-account settlement; 31/30
had exact event/CPI trade-transfer facts but censored native-account settlement after SyncNative;
one development item remained unsupported. All six transaction equations passed on every item with
qualified transfer facts (49/49 development, 50/50 audit). These rows remain diagnostic only: no
wallet-net price, training label, or execution support is published. SyncNative residuals do not
become exact without an independent lamport/rent/lifecycle ledger. Receipts:
[`v2 development`](evidence/helius-pumpswap-sell23-ledger-v2-development-qualification-2026-10-09.json),
[`v2 audit`](evidence/helius-pumpswap-sell23-ledger-v2-audit-qualification-2026-10-09.json).
The frozen contract was then replayed over all 1,255 eligible sell23 candidates in the 512 captured
Helius blocks: 410 exact full target-account settlements, 843 trade-transfer records with censored
native settlement, and two unsupported instructions; the six equations passed on all 1,253 records
with qualified transfer facts. This captured-class census does not supply market-complete supervision
or expand training eligibility. Receipt:
[`v2 captured-class census`](evidence/helius-pumpswap-sell-ledger-v2-capture-census-2026-10-09.json).

The bounded finalized block-inventory path adds full-block observations with request, response,
receipt-time and replica provenance. A single Free slot capture plus adjacent witnesses did not
close lineage because the left witness failed, and remains unresolved. A later two-slot bilateral
replay yields one exact target for the narrow structural instruction-reference count below; this
does not supply a qualified economic market-event count. No fresh independent baseline comparison
was registered or run. The previously studied September cohort remains ineligible as a fresh
holdout.

### Native-coordinate sensing and v3 objective integration (2026-10-09)

`brain-experience-v3` adds typed native-coordinate origins, dependency ranges, target windows and
maturity while retaining UTC availability as a separate knowledge clock. Split plans specify role
extents per declared domain and a monotone UTC cutoff. No implicit coordinate conversion or
cross-domain alignment is permitted. Actual point-in-time schemas require an actual `decision_ns`;
inputs must be available by that decision, while future exact/lower-bound target receipts may arrive
later if they are within the snapshot cutoff and role knowledge cutoff. Historical replay may keep
`decision_ns` null and is not an online forecast. v1/v2 schemas, exact-label masks,
dense f64 partition storage and existing training contracts remain compatible. Coordinate-aware
compose, replication, batching, and evaluation paths are available for v3. V3 partition readback
rechecks decision, input/dependency and exact/lower-bound receipt clocks against the pinned snapshot
cutoff before rows reach training or scoring; re-pinning a late-receipt partition does not bypass
this check. On every v1-v3 partition read, row IDs and target-status counts are reconciled with the
sealed partition extent and catalog summary. Composition reads each member partition before using
its catalog status counts, so a re-pinned false summary cannot change aggregate denominators. This
establishes consistency inside the content-addressed snapshot, not source authenticity. A composed
snapshot uses its declared cutoff, currently the maximum parent cutoff; this
does not prove that each reused partition was available by its original parent's cutoff. Composition
hashes and reads each unique source partition once; v3 repeats that check when the same pin appears
under a different parent cutoff. Reuse requires matching catalog metadata and resolved bounds. The
integrity pass scales with unique payload size and is not metadata-only on large corpora. This does
not change the composed cutoff semantics. Training
reports a versioned conservative exclusive-end frontier for every native domain from all train, validation,
test and dependency extents, merged with a proven parent frontier. The primary-domain value remains
available as `seen_end_coordinate`. Registered native-coordinate evaluation requires every test
primary/dependency interval to start at or after its domain frontier; empty-domain history is unknown
and blocks intervals in that domain. Legacy standalone v3 artifacts derive a frontier from the
pinned plan, while legacy warm-start lineage without a complete parent frontier fails closed.
Evaluation verifies enrollment across every snapshot row before scoring, including pending and
purged origins. Reports expose assigned, outside-test, purged, pending, lower-bound, exact-usable and
scored-origin counts plus per-target status/eligibility denominators; legacy `population.test`
continues to mean scored origins. The scorer reconciles emitted identities and exact cell counts to
the census without changing pinball or coverage.

The existing opt-in `partial_count_lower_bound_pinball_v1` objective now admits validated positive
bounds from v3 under the same coordinate purging and UTC availability gates. Exact labels remain in
the exact mask; a bound is retained separately and zero/ineligible bounds add no objective mass.
Unknown, failed, stopped, unsupported, pending and evidenced skipped-slot cases remain separate in
the source/target records. This does not turn a provider's absent response into an exact zero, and it
does not establish a count distribution from censored-only labels.

An offline real replay tied to the two-slot dual-provider finalized-block receipt published and
replicated a two-row v3 snapshot. It contains one exact H=1 target of 87 recognized PumpSwap
instruction references and one pending target; the shared native-coordinate batch path admits one
exact row. The reference-count process is structural: transaction status is retained, and these
references are not qualified economic swaps. The episode is too small for a temporal split, model
fit, independent evaluation or baseline comparison. `decision_ns` stays null. Receipt:
[`brain-native-slot-experience-2026-10-09.json`](evidence/brain-native-slot-experience-2026-10-09.json).

Verification after integration: `make check` passed (1,319 passed, 25 optional skips; Control
Center 39 passed; mypy clean), `make smoke` passed synthetic-only, and the focused native-coordinate,
learning, sparse, generation and target suite passed (87 passed, one CUDA-only skip). Tests include
fractional-count rejection and separation of pending maturity from incomplete support. No model fit
or learning gain was claimed. Fresh independent comparisons remain infeasible until a prospective
or otherwise valid independent source supplies enough mature, eligible examples; historical event
times are not backdated into decision times.

### Scheduled sensing v2 neural path (2026-10-15)

Scheduled sensing episodes now use representation v2. Its representation identity binds the
sensing implementation, plan, availability mode, ordered stream feature schemas and process
contract pins. Population, role, receipt and schedule pins remain in source/snapshot provenance,
but do not make compatible train and separate evaluation cohorts appear to have different model
inputs. Stream order is canonical by stream name. The evaluator still requires exact schema
equality. New materialization requires point-in-time availability; existing v1 snapshot artifacts
are not rewritten or reinterpreted.

Sensing history and support queries are clamped to the active enrollment interval. A regression
fixture proves that a re-entered entity cannot inherit prior-interval event values or support
dependencies, an event at the new interval start can enter after its receipt time, and a target
window crossing the enrollment end remains censored.

One fixed CPU synthetic run exercised exact-zero labels and positive partial count bounds through
fit, interrupted checkpoint recovery, fresh-fit weight equality, standalone inference and registered
evaluation on a distinct research-evaluation role/cohort with identical representation v2. The
evaluation retained all 13 scheduled rows, counted 10 exact usable labels, and kept three censored
labels; the lower-bound training path admitted one positive bound. These synthetic scores establish
engineering flow only: they do not show market predictive quality or learning gain. No provider,
protected or production data was used. Reproduce with
[`test_scheduled_sensing_fits_resumes_and_scores_separate_synthetic_cohort`](../tests/test_experience_sensing.py)
and inspect the unique [qualification receipt](evidence/brain-scheduled-sensing-neural-qualification-2026-10-15.json).

A separate frozen lagged-context retrieval assay fit one width-16 CPU GRU for 640 steps on 256
balanced synthetic four-bit histories. Its one retrospective registered evaluation used 128
disjoint synthetic entities with exact one-second count labels and passed both frozen predictive
thresholds: raw-count summed q10/q50/q90 pinball was 0.0097413 (0.0032471 per quantile, matching
the sealed scorer), or 0.00696 of each train-only empirical baseline. All 128 evaluation rows had
inputs available by decision time, context inside the declared 40-second bound, and matured exact
targets. The result is arithmetic over the already exposed, sealed prediction transcript; it is
not a second evaluation. Overall assay qualification remains incomplete because peak scratch use
was not measured. This establishes only retrieval of the frozen synthetic lag rule on this cohort;
it does not establish market skill, general temporal learning, transfer, or a model improvement for
Brain. Two read-only result-controller attempts failed before publishing metrics and are preserved;
the final reconciliation reused the sole sealed score without refitting, invoking prediction again,
registering another protocol, or rebuilding the cohort. See the [assay preregistration and result
receipts](evidence/README.md#brain-and-operator).
