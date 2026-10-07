# Research History: Decisions and Evidence

This is a compact historical result index, not a source to load for routine implementation. Current
architecture is in [`brain-development.md`](brain-development.md); current ML conclusions and
benchmark state are in [`training-evaluation.md`](training-evaluation.md). All receipts remain in
[`evidence/README.md`](evidence/README.md).

## Durable lessons

- A successful parser or replay on a small public sample establishes structural behavior only.
  It does not establish complete provider history, market representativeness, execution quality,
  or profitable edge.
- Missing time ranges and unknown venue layouts remain visible. They are not zero outcomes.
- Token-2022 and venue-specific fee/account semantics are not inferred from account shape alone.
- The first selected 965-market on-chain registry yielded no qualified USD examples at that stage.
  Registry size is not training support.
- The initial F01/D43 calendar qualification did not complete; its protected data/holdout is not
  available to Brain experiments.
- Brain bar forecasts and delayed virtual fills measure different targets. The paper cost model is
  an explicit uncalibrated assumption.
- A candidate can finish training, be selected within a new run, and still lose to the official
  champion. Promotion remains separate.
- Broad data volume with a narrow/exposed evaluation split cannot support a generation-quality
  claim. Build benchmark support in parallel with acquisition.

## Milestones

| Period | Result that remains decision-relevant | Receipt family |
|---|---|---|
| 2026-10-01 to 03 | CPMM/account/transaction audits, forward captures, providers, and F01 runner/readiness were engineering and source evidence. No broad qualified market set or F01 release resulted. | `cpmm`, `forward`, `provider-audit`, `history`, `f01-*`, `research-readiness` |
| 2026-10-03 | D44 verified a small CPU/MPS ML implementation; the real bundle had 174 usable decisions and did not select a market backbone. D45/D46 preserved registry/readiness/holdout gates. | `ml-foundation`, `corpus-registry`, `research-readiness` |
| 2026-10-04 | B01-B13 built a real OHLCV forecast/paper/operator path and historical curriculum. Multiple collector, recovery, source, and sampling qualifications found narrow support and reliability gaps; they did not qualify edge or a week-long service. | `brain-development`, `brain-*`, `brain-operator-*` |
| 2026-10-04 B12 | First matched recent/full/balanced curriculum and frozen future paper comparison. | `brain-curriculum-comparison`, `brain-curriculum-exam` |
| 2026-10-06 expansion | Historical birth admission expanded pool IDs; retained training rows stayed capped. | `brain-training-expansion` |
| 2026-10-07 | Candidate audit, causal sampler/budget/head/LR diagnostics, and historical data envelope. None qualified a new champion or broad prospective benchmark. | `brain-post-training-audit`, `brain-sampling-ablation`, `brain-budget-ablation`, `brain-horizon-ablation`, `brain-lr-diagnostic`, `brain-clipping-audit`, `brain-research-envelope` |

Exact ML conclusions, numeric results, and limitations have one canonical owner:
[`training-evaluation.md`](training-evaluation.md). Do not reconstruct a trend from different
baselines or exposed samples.

## Reproducibility rules

Receipts are unique and immutable; 76 JSON artifacts plus 9 SVG/PNG figures are retained. The
short conclusion above is not a replacement for a receipt. Exact source commits, preregistration
hashes, dataset/manifests, seed/draw identities, and detailed per-seed metrics live in the relevant
JSON and sealed machine-local `work/` bundles.

To reproduce an old experiment, use its recorded commit and sealed inputs. Do not run its old
campaign script against current runtime, mutate a source receipt, or overwrite a completed output.
The seven frozen protocol files listed in `INDEX.md` remain byte-identical because historical
tools included their hashes in run receipts.
