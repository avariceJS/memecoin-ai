# Operator and Runtime

**Status snapshot:** see [`memory/now.md`](../memory/now.md); runtime state changes and must be
re-read before any operational decision. This document owns operator behavior, paper/Watch lanes,
two-host roles, and release boundaries.

## Control plane

The local Control Center exposes durable operator commands, current status, jobs, source health,
model candidates, exams, and paper books. Main implementation is `src/memecoin_operator/`; paper
accounting is `src/memecoin_paper/`. Data/model lineage is documented in
[`brain-source-architecture.md`](brain-source-architecture.md) and
[`training-evaluation.md`](training-evaluation.md).

The pipeline is collect -> immutable snapshot -> bounded fit -> immutable candidate -> separate
future exam. Job completion is not promotion. Automatic champion promotion is disabled; current
official champion remains pinned until the existing release/review policy admits a change.

Commands are durable/idempotent by request ID. Reservations are accounted before side effects and
failed attempts remain visible/charged. Stop intent and confirmed stop are distinct states. Pause
prevents new operator jobs; it is not a universal stop for unrelated legacy services. Stop All has
an explicit confirmation/acknowledgement boundary. Integrity, unknown consumer, deadline, and
source STOP conditions must not be cleared by retrying or by changing a UI mode. Existing paper
cash, positions, ledgers, and source STOPs are not reset during a release.

Training loads isolated ML dependencies; the ordinary runtime path does not require PyTorch.
Offline/read-only reports use a consistent SQLite snapshot. Do not use SQLite `immutable=1` on a
live WAL database; do not copy a live database independently of its WAL/SHM files.

## Paper semantics

The paper engine is virtual and long-only. Current B07 assumptions: $1,000 per model book, $100
entry notional, up to three pending/active positions, one position per pool, 50 bps slippage and
25 bps fee per side. Entry waits for a new fresh closed bar after decision time; entry intent is
not a fill. Exit is time-based, not a calibrated intrabar risk model. These cost/fill assumptions
are not qualified against real execution.

Persisted fills and matured outcomes are evidence; WAIT/ENTER intent is not. Zero trades provides
no estimate of profitability. Forecast pinball, virtual PnL, and executable net edge are separate
claims. The reference `memecoin_ai` replay is synthetic-only and is not unlocked by a real-data
paper run.

## Watch Market

Watch Market is a separate single-mint/pool virtual lane. The user may copy an identifier from
Axiom; the UI/provider path, not Axiom, resolves market data. Start must pin the current official
champion manifest, create an isolated source journal and virtual book, and never invoke AUTO
training or write the AUTO paper ledger. No transaction signing or submission exists.

The worker polls a supported provider at >=60s cadence for current snapshot and 5-minute OHLCV.
Show snapshot price separately from the latest first-seen closed bar. Forecasts use current Brain
5m/15m q10/q50/q90; virtual decisions/books use the registered paper policy. Unsupported pools,
stale data, gaps, and insufficient context must block a forecast/trade rather than inventing a
value. Provider polling is not exchange tick data. Quantile intervals are not qualified
probabilistic guarantees. Watch output is not automatically admitted to training.

Code is present in repository commit `0e9a2c1`, with synthetic tests. The owner runtime check on
2026-10-07 identified runtime08; the Watch release and public-provider/browser owner acceptance
were not complete. Runtime09 was equivalent to runtime08 and did not contain Watch. A future
release requires a new pinned manifest plus the ordinary owner-host readiness, Pause, switch,
health/PID/pin, and rollback checks. Do not infer deployment from a code commit.

## Two-host boundary

- Mac 1 owns production source/operator state and the deployment pointer.
- Mac 2 is a worker checkout for isolated code/research and sealed artifact processing.
- Transfer immutable captures, shards, datasets, or model bundles through a new incoming path;
  verify manifest hashes and provenance before admission.
- Do not synchronize mutable SQLite/WAL through cloud folders or run a second canonical writer.
- Account quotas are provider/account/IP scoped, not multiplied by machine count. Deduplicate
  request ranges across workers and reconcile reservations before resuming after a partition.
- Worker checkout intentionally has no owner `work/`, LaunchAgents, or credentials. Never restore
  live state from copied documents or secrets.

Bootstrap entrypoint: `tools/bootstrap_worker.command`. The full workflow formerly at
`two-mac-workflow.md` is retained as a short compatibility pointer; current boundaries are here.

## LLM workers

Strong external coding/research models may review bounded source and evidence. Local Qwen3.5 35B
is a separate preprocessing resource: logs, incident clustering, summaries, and structured
annotations with source references. Its output is untrusted derived data, never ground truth,
promotion authority, or a trading feature without independent experiment. Do not let it compete
with heavy training for memory when measurements show harmful swap/residency pressure. LLMs are
not in the trading runtime.
