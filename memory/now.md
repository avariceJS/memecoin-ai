# Current Project State

**As of:** 2026-10-08 Europe/Moscow. Re-read owner state before operational decisions.

## Goal and boundaries

Build a large Solana learning system with growing useful corpus, deliberate compute, reproducible
model generations and independent evaluation. No demonstrated executable trading edge. F01/D43 and
reserved holdout payloads remain protected. No production promotion, signing or real-money trading.
Preserve user-owned untracked `.codex/`. Grok is disabled; Eliza helpers remain authorized for
bounded nonsecret work, with findings checked locally.

## Completed independent-population qualification

- Receipt: `docs/evidence/brain-independent-population-learning-2026-10-08.json`.
  Root: `work/brain-diverse-campaign-20261008/`; raw captures/models stay outside Git.
- All 1,228 frozen requests completed across 307 independently birth-enrolled base mints: 1,227
  received, one sealed timeout (ordinal 925, **never retried**). Six received responses quarantined
  for duplicate bars. All enrollments retained; authority/calendar-page-prefix selection is biased,
  not a census. No acquisition or trainer remains running; capture lock is free.
- User pause/resume preserved the 1,142-request prefix and STOP bytes. `user-resume01.json` binds
  reviewed state and the new SSH transport; `capture/recoveries/` preserves both STOP histories.
  Acquisition is complete: do not restart its expired/old envelope or resend attempted requests.
- Original learning at `73363fe` failed support gates: Aug 8/11/15 boundaries had zero usable
  validation/test. No candidate fitted. Frozen harness nonetheless qualified evaluation source
  and admission; no model scores existed. Keep `learning/result.json` unchanged.
- Explicit `learning-plan02.json` at `24ae9ae` changed only split boundaries: May–June train,
  July validation, August descriptive test, same September evaluation. Decision used train-role
  calendar support only. All other representation/quality/recipe/cohort choices stayed fixed.
  `learning02/` holds three completed 60,681-parameter CPU GRUs and registered evaluation.
- Narrow: 140 usable train rows, two exposed markets, 700 draws / five steps. Broad: 3,990 rows,
  14 markets, 19,950 draws / 40 steps. Repeated narrow control: 20,020 draws / 143 steps, not exact
  FLOP/optimizer matching. Shared validation/test: 1,171 / 3,505 usable rows.
- Broad pinball beats the repeated narrow control by 14.18% / 20.46% / 44.30% on 5/15/30-minute
  September targets, but **loses to its own empirical train quantiles by 35.38% / 19.37% / 13.30%**.
  Paired whole-mint intervals favor broad versus both neural controls, conditional on one seed and
  this historical cohort. Only 30/27/25 of 137 evaluation mints support the respective horizons.
  Nominal 80% intervals overcover. No calibrated or executable edge; no promotion.
- Source funnel: train 38,705 unique bars -> 8,792 contexts across 49/170 markets; evaluation
  14,375 bars -> 6,465 contexts across 30/137. All 307 have bars, only 79 have contexts. One broad
  train market supplies 55.36% of draws; effective Shannon count 4.49. Causal admission removes
  only 20 train-role contexts and none in evaluation. Continuous-bar selection dominates loss.
- Historical receipt availability, initial response-shape inspection and evaluation-source access
  before revised split are disclosed. This benchmark is now studied; do not tune against it again.
- Verified at `24ae9ae`: `make check` 1,130 passed / 23 optional skips; lint/format/mypy and smoke
  passed. Optional ML regressions 38 passed / one CUDA skip (overlapping sets). Independent report
  verified capture/source/context/generation/exposure/paired bindings; all extracted code archives
  match tar contents. No new GPU, cluster or million-row load qualification occurred here.
- Qwen inventoried counter boundaries; GLM audited numerical receipts. Arithmetic was recomputed.
  Incorrect helper claims about positional binding, identical full reports and calibration implying
  empty support were rejected. The flaky operator test matching `b09` inside SHA bytes was replaced
  with an exact semantic-contract assertion; production implementation was unchanged.

## Implementation and next stage

- `24ae9ae` adds separate immutable learning declarations, support preflight before expensive fits,
  no-candidate evaluation gating, and `tools/population_campaign_report.py` reconciliation.
  A new recipe requires a new run root; acquisition roles/history cannot be rewritten.
- Existing foundation: sealed portable qualified sources, generic/paged experience snapshots,
  admission views, replicas/composition, exact training resume, owned generations, portable serving,
  population/coverage work plans, diversity audits and checkpoint-bound optimizer exposure.
- Earlier load evidence remains in `docs/evidence/brain-population-million-capacity-2026-10-08.json`:
  10k synthetic entities, 520k cells, 1M examples, equal audited/plain weights on CPU. It establishes
  engineering plumbing only, not real learning quality or cluster-scale performance.
- Next major bottleneck: convert sparse/short-lived/failed market histories into qualified experience
  without requiring uninterrupted active OHLCV. Develop sparse/event-time and lifecycle producers
  with explicit observation support, gaps and censoring; never invent zero returns or inactivity.
  Then use fresh populations and registered multi-seed tests against empirical baselines before
  enlarging model/compute. Rich on-chain/order-flow sources and GPU qualification remain open.

## Owner

- Mac 1: **172.22.124.203**, user `root1`, same strict pinned SSH key as old `.66` address.
- Final read-only fence at 2026-10-08 10:57:24 UTC passed: operator revision 33 paused, no busy jobs;
  source STOP and pinned source/config journal unchanged. No full executable re-attestation.
- Production deployment/champion were not changed. Last recorded champion:
  `9ea4a6bd7977fb66fe4a18eba54ed7eaed0afd65810ec99c75aec3e1443e3920`, runtime08.
- Paid Alchemy account capacity remains unverified; this campaign used keyless GeckoTerminal only.

Start from `docs/INDEX.md` for canonical architecture, source, training and operator contracts.
