# Evidence Index

Evidence is immutable and task-scoped. Do not read this directory wholesale. Historical receipts
and figures retain their original bytes. Reports/receipts can
contain large exact payloads, so open only the named artifact needed for a specific claim.

## Brain and operator

- Partitioned experience engineering capacity (synthetic only):
  `brain-experience-capacity-2026-10-07.json` (initial 520k-bar development measurement),
  `brain-experience-million-capacity-2026-10-07.json` (final million-bar path at `8683305`).
- Post-review experience batching: `brain-experience-batch-capacity-2026-10-08.json`
  (million synthetic bars, cross-partition batches of 1,024, code `035406a`).

- Development, source architecture, historical vacuum, and acquisition: `brain-development-2026-10-04.json`,
  `brain-source-architecture-2026-10-04.json`, `brain-vacuum-2026-10-06.json`,
  `brain-development-acquisition-2026-10-07.json`, `brain-research-envelope-2026-10-07.json`.
- B02/B03/B04 source and quality: `brain-data-scale-2026-10-04.json`, `brain-pool-quality-2026-10-04.json`,
  `brain-availability-normalization-2026-10-04.json`; plots: `brain-b02-coverage.svg`,
  `brain-b02-learning-curves.svg`, `brain-b03-coverage.svg`, `brain-b03-learning-curves.svg`,
  `brain-b04-normalization.svg`.
- B06/B07/B08/B09/B10/B11 and host acceptance: `brain-pumpswap-events-2026-10-04.json`,
  `brain-paper-loop-2026-10-04.json`, `brain-paper-loop-verification-2026-10-04.json`,
  `brain-b07-first-activation-incident.json`, `brain-continuity-2026-10-04.json`,
  `brain-hot-lane-2026-10-04.json`, `brain-hot-lane-verification-2026-10-04.json`,
  `brain-operational-recovery-2026-10-04.json`, `brain-operational-recovery-verification-2026-10-04.json`,
  `brain-archive-curriculum-2026-10-04.json`, `brain-archive-qualification-2026-10-04.json`,
  `brain-archive-reproduction-2026-10-04.json`, `brain-operator-preflight-2026-10-04.json`,
  `brain-operator-acceptance-2026-10-05.json`, `brain-operator-delegation-2026-10-05.json`,
  `brain-operator-delegation-final-2026-10-05.json`, `brain-operator-final-checks-2026-10-05.json`,
  `brain-operator-history-2026-10-05.json`, `brain-suspend-recovery-2026-10-06.json`.
- Latest expansion/training/evaluation: `brain-training-expansion-2026-10-06.json`,
  `brain-curriculum-comparison-2026-10-04.json`, `brain-curriculum-exam-2026-10-04.json`,
  `brain-experience-review-2026-10-06.json`, `brain-post-training-audit-2026-10-07.json`,
  `ml-review-audit-2026-10-07.json`, `brain-benchmark-readiness-2026-10-07.json`.
- Causal diagnostics: `brain-sampling-ablation-2026-10-07.json`,
  `brain-budget-ablation-2026-10-07.json`, `brain-horizon-ablation-2026-10-07.json`,
  `brain-lr-diagnostic-2026-10-07.json`, `brain-clipping-audit-2026-10-07.json`.
- Owner/worker handoff status: `brain-handoff-status-2026-10-07.json`,
  `two-mac-bootstrap-2026-10-06.json`, `two-mac-worker-2026-10-06.json`.
- UI images: `brain-continuity-paused-ui-2026-10-04.png`, `brain-hot-lane-control-center-2026-10-04.png`,
  `brain-operational-recovery-control-center-2026-10-04.png`, `brain-paper-control-center-2026-10-04.png`.

## Earlier source, CPMM, and F01 evidence

- Alchemy transport: `alchemy-pilot-2026-10-03.json`, `alchemy-pilot-v2-2026-10-03.json`,
  `qualified-http-corpus-2026-10-03.json`, `cold-connection-corpus-2026-10-03.json`.
- CPMM/protocol/fees/token history: `cpmm-2026-10-01.json`, `protocol-2026-10-01.json`,
  `creator-fee-share-2026-10-02.json`, `parameter-history` is included in `history-2026-10-02.json`,
  `token2022-2026-10-02.json`, `economic-corpus-2026-10-02.json`, `checkpoint-coherence-2026-10-02.json`.
- Forward/prospective: `forward-2026-10-01.json`, `causal-forward-2026-10-02.json`,
  `prospective-execution-2026-10-02.json`, `calendar-execution-2026-10-03.json`.
- Provider access: `provider-audit-2026-10-02.json`, `helius-preflight-2026-10-02.json`,
  `helius-direct-2026-10-02.json`, `helius-traces-2026-10-02.json`, `secondary-access-2026-10-02.json`,
  `quicknode-diagnostic-2026-10-02.json`, `chunked-range-2026-10-02.json`,
  `persistent-source-2026-10-02.json`.
- Research registry and F01: `corpus-registry-2026-10-03.json`,
  `research-readiness-2026-10-03.json`, `f01-runner-2026-10-03.json`,
  `f01-runner-integrity-2026-10-03.json`, `f01-sampling-2026-10-03.json`,
  `f01-collector-qualification-2026-10-03.json`, `f01-operational-runtime-2026-10-03.json`,
  `f01-live-qualification-2026-10-04.json`, `f01-live-result-2026-10-04.json`,
  `f01-d51-owner-attestation-2026-10-04.json`.
- ML foundation: `ml-foundation-2026-10-03.json`.

For an exact filename lookup, use `rg --files docs/evidence`. Receipt names are deliberately kept
stable because reports and experiment artifacts refer to them. `work/` datasets and model bundles
are not in Git; consult the receipt's pinned local root and manifest when authorized.

- `brain-integrated-pipeline-capacity-2026-10-08.json`: 100k synthetic bars through continuous export, v2 experience, two CPU training epochs and registered evaluation at `f5aca3d`.
- `brain-integrated-million-capacity-2026-10-08.json`: million-bar writer/reader check at `f5aca3d`, before paged-catalog implementation.
- `brain-paged-million-pipeline-capacity-2026-10-08.json`: million-bar, two-epoch full pipeline at `2f1eff2`; eight pools use inline catalogs despite the paging-capable revision and filename.
- `brain-wide-paged-pipeline-capacity-2026-10-08.json`: 1.024M synthetic bars across 2,048 pools at `6ae0ea9`; actual paged train/evaluation catalogs, two CPU epochs and separate evaluation.
- `brain-real-system-qualification-2026-10-08.json`: fixed 24-market development population, cumulative/replica snapshots, three CPU fits, exact SIGKILL recovery and retrospective evaluation; harness `339cb40`, reporter `d0f6732`.
