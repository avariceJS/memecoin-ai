# Post-training audit — 2026-10-07

Audit scope: latest completed AUTO training `job_5edd5e5ecda88338`; read-only on Mac 1.
No model promotion, AUTO mutation, manual pause, or production switch was performed.
Machine-readable checks and pins: [evidence JSON](evidence/brain-post-training-audit-2026-10-07.json).
Watch Market implementation reviewed and committed as `0e9a2c1` (`Add isolated Watch Market paper lane`).

## Completion and integrity

Pipeline selected the two-seed GRU ensemble (`gru-7`, `gru-19`; 1,000 steps each) from
snapshot `job_f34eb5cc5889b45a`. All four registered fits (GRU and linear, seeds 7 and 19)
have completed ledger events, no incidents, and a verified chain head. Candidate, model,
snapshot, and dataset inventories match their sealed manifests and registry pins. Snapshot
raw audit is exact: 100,656 bars from 5,624 captures. There are no partial/corrupt outputs.

The audited partitions reproduce the immutable snapshot membership: train 48,000 decisions
across 86 pools; validation 264 and test 249 across 8 pools each. Input receipts and target
maturities are at or before the snapshot cutoff; whole 15-minute targets remain inside their
assigned fold. No split, temporal, or availability violation was found.

## Matched forecasts

The candidate was rescored on the same validation/test samples as the currently configured
official champion, whose selected model is empirical, manifest
`9ea4a6bd7977fb66fe4a18eba54ed7eaed0afd65810ec99c75aec3e1443e3920`. Metric is pool-macro
pinball, lower is better:

| Fold | Horizon | New GRU | Official champion | Candidate delta |
|---|---:|---:|---:|---:|
| Validation | combined | 0.010146 | 0.007641 | +32.8% worse |
| Validation | 5m | 0.008439 | 0.005040 | +67.4% worse |
| Validation | 15m | 0.011852 | 0.010242 | +15.7% worse |
| Test | combined | 0.008904 | 0.006272 | +41.9% worse |
| Test | 5m | 0.008051 | 0.004867 | +65.4% worse |
| Test | 15m | 0.009756 | 0.007678 | +27.1% worse |

Candidate test coverage at Q10/median/Q90 is 1.6%/78.0%/99.0%; champion is
15.9%/53.4%/93.4%. The candidate is descriptively worse on both folds and horizons. These
are short, retrospective, serially correlated folds; no confidence interval is registered,
and the training metric applies no execution costs. This is not confirmatory evidence.

## Trading evidence

The latest prospective frozen paper exam is still active. It compares the current champion
and candidate alongside the same source observations under the same B07 policy and cost
assumptions (50 bps slippage and 25 bps fee per side). At the status receipt timestamp, each
selected book had 8 decisions, zero fills/trades, $0 realized PnL/fees, and $1,000 cash/equity.
That is too little evidence to estimate cost-adjusted superiority; historical forecast
scores must not be presented as trading PnL. The exam expires 2026-10-07 12:23:51 UTC.

**Conclusion:** the newly trained candidate is worse on the matched historical forecast
metric. Trading superiority remains statistically unproven because the prospective books
have no trades yet. The candidate was not promoted and the champion remains unchanged.

## Readiness

Mac 1 was `auto/waiting`, `domain_stop=false`, with no queued/running jobs. The active exam
above is the remaining activation blocker, so this is not yet a Pause-only release window.
Active operator PID 64937 runs `operator-runtime08`. Staged `operator-runtime09` has the
identical manifest pin and both manifests omit `memecoin_operator/watch_market.py`.
Read-only status was rechecked at 2026-10-06 21:41:18 UTC (2026-10-07 00:41 Moscow): the same
exam remained active, all listed jobs were completed, and mode/phase remained `auto/waiting`.

After the exam expires and jobs remain quiescent, prepare a new owner-host `operator-runtime10`
from exact Watch Market commit `0e9a2c1`; do not reuse runtime09. The routine Pause is
still required before activation. Pause alone does not activate code: the guarded release
must then switch the deployment pointer/plist, restart the operator and verify health/PID/pin.
Source LaunchAgent and paper ledgers remain untouched. Resume AUTO only after successful
health verification. Production was not switched during this audit.
