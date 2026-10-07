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
10.24% current sampler and 10.05% capped sampler (terminal checkpoints: 3.53% and 9.52%). At
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

## Reproduction map

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
