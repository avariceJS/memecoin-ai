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

### Local development and private state — 2026-11-08

Use `~/Developer/memecoin-research` on `develop`. The sole private store is
`~/Developer/memecoin-runtime`: `brain/owner-work` contains the retained operator, champion artifacts
and full SQLite families; `brain/research-work` contains retained research state. The ignored `work`
link keeps existing local tools compatible. Current operator/source config paths were rebound; historical
immutable receipts retain their original paths and hashes.

`tools/brain_dev_operator_preview.py` defaults to the retained owner operator04 under this runtime.
Its snapshot/serve route is read-only, but making another snapshot is unnecessary for cleanup.
The original state is not activated by reading or testing source. No collectors, training, AUTO,
paper ledger, promotion or trading was launched during consolidation.

See the root README for source reconstruction and pinned dependency setup. Optional ML environments
can be rebuilt from their lockfile; saved champion and research artifacts are separate private state.

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

## Operating mode and host boundaries

Current policy is **single-host development/research and owner runtime on Mac 1**. The reset used
Kingston + GitHub only; Mac 2 was not involved. The sole-copy Kingston custody decision applied
during reset. As of 2026-10-19, local development also has the qualified internal read-only replica
described above; owner runtime remains inert and activation requires separate authorization.
Completion and verified pins belong in `memory/now.md`.
Host reset, portability, backup custody, and full recovery readiness are owned by
[`portability-disaster-recovery.md`](portability-disaster-recovery.md); this operating-mode section
does not make the owner Mac disposable.

- Mac 1 research checkout: `/Users/root1/Developer/memecoin-research`, branch `main`.
  Create separate topic branches/worktrees for development and offline research from this baseline.
- Mac 1 owner checkout: `/Users/root1/Desktop/memecoin-ai`. Its source/operator state, deployment
  pointer, runtime bundles, champion and paper ledgers remain separate. Never merge research
  `work/` into owner `work/`, update the owner checkout as part of a research sync, or use production
  services/credentials merely because they are local. New acquisition still needs its own approved
  envelope, quota accounting and fresh owner fence; local transport does not waive these checks.
- Research has its own `.venv`, optional `work/ml-venv`, artifacts and generation ownership. Do not
  reuse a production virtualenv, change LaunchAgents, start another canonical source writer or
  relaunch an expired campaign while preparing the research host.
- The historical Mac 2 worker path was `/Users/sl-aleksandr/Developer/memecoin-worker`; its current
  contents have not been inspected for this plan. It is not an active restore destination. Preserve
  any existing unique work under the Git/custody policy; do not initialize or activate a worker there.
- Transfer immutable captures, shards, datasets, or model bundles through a new incoming path;
  verify file inventories, manifest hashes and provenance before admission. Preserve failed runs,
  STOP/recovery histories and uncommitted user files. Do not delete the sending copy upon cutover.
- Frozen declarations may contain historical absolute paths. Preserve their bytes and identity;
  use explicit new transport locators for portable sources/snapshots and new declarations for new
  runs. Never rewrite sealed JSON or create fake old-host directory symlinks to make it appear local.
- Do not synchronize mutable SQLite/WAL through cloud folders or run a second canonical writer.
  Transfer stopped research state with all companion files; inspect writers before and after.
- Account quotas are provider/account/IP scoped, not multiplied by machine count. Deduplicate
  request ranges across workers and reconcile reservations before resuming after a partition.
- Worker checkout intentionally has no owner `work/`, LaunchAgents, or credentials. Never restore
  live state from copied documents or secrets.

`tools/bootstrap_worker.command` and the compatibility pointer `two-mac-workflow.md` concern a
future, separately authorized worker setup. Do not run worker bootstrap or reactivate Mac 2 for the
current migration. Multi-worker transfer/quota constraints above remain architectural invariants;
they do not assign a worker role to the corporate backup host. After the reset, development resumes
on Mac 1 from GitHub `main`, while the restored owner runtime retains its separately recorded pin.

## Finite external Senses operation

The separately reviewed external-v1 entry point is `tools/brain_senses_external_launch_v1.py`.
Its `init`, `status`, and launchd-only `run` commands take private activation and storage-config
paths. The activation binds an immutable plan, Sol review, qualification receipts, source hashes
and installed plist. Initialization is offline and requires at least 60 seconds before the first
bucket. `run` requires the bound launchd PID, parent and exact argv; manual invocation is rejected.
No secret belongs in command arguments, plist environment or logs.

Calendar starts have no RunAtLoad/KeepAlive behavior. Bootstrap and frame are separate processes;
the latter independently verifies the former's durable prefix before dispatch. A spent reservation
or ambiguous intent is terminal and never retried. Do not kickstart to recover a missed bucket,
edit an active lease, or reuse its service directory. Read-only status verifies the corpus but its
`activation_authorized=false` result intentionally grants no operational authority of its own.
Its current implementation also requires the bound volume and full lease free-space envelope;
under storage failure preserve the root and inspect the separate storage-health status first.

At terminal, verify captures and STOP through the independent descriptor reader, record exact
request/byte/gap accounting and launchd exits, then disable/unload the finite label and archive its
hash-bound plist. The recurring read-only storage-health agent stays enabled. A successor needs a
new immutable predecessor handoff and separately reviewed future lease; this version does not
renew itself. c4 remains terminal. The source architecture owns storage, timing and acquisition
semantics; this procedure never changes the production operator or champion.

## Integrated Observatory UI

`apps/control-center` is a separate React read-only Observatory over its immutable historical
projection (baseline `6a7e079`). It is included in canonical main alongside the current operator
and Brain, but is not deployed and is not a replacement for Control Center on the owner runtime.
No current market, sparse generation, live source or champion feed has been added. Missing
capabilities remain unavailable. Use the app README for opt-in loopback development and tests.
CC02 visual rejection/reconstruction state and pinned evidence remain explicit.

## LLM workers

Strong external coding/research models may review bounded source and evidence. Local Qwen3.5 35B
is a separate preprocessing resource: logs, incident clustering, summaries, and structured
annotations with source references. Its output is untrusted derived data, never ground truth,
promotion authority, or a trading feature without independent experiment. Do not let it compete
with heavy training for memory when measurements show harmful swap/residency pressure. LLMs are
not in the trading runtime.

Eliza Kimi/GLM/Qwen are primarily bounded helpers for large logs/receipts, artifact comparison,
anomaly searches, documentation summaries and edge-case/test inventories. Independent code review
is selective at provenance, recovery, concurrency, temporal and integrity boundaries, not a
mandatory pass after every edit. Treat findings as hypotheses. Architecture, complex implementation,
integration, ML/data semantics and the final quality judgment remain the integrator's responsibility.
Grok remains disabled by the user's latest instruction.

## Git and local state — 2026-11-08

The only source root is `~/Developer/memecoin-research`; remote `avariceJS/memecoin-ai`.
Use `develop` for Brain and organization source. `main` remains the stable baseline and requires
separate release authority. Temporary agent branches/worktrees are removed after accepted publication.
Old branch registries/custody narratives remain in Git history; do not use their paths as live setup.

Private runtime is `~/Developer/memecoin-runtime`. Paperclip owns `state/instances/lab` and its live DB,
secrets, durable receipts and holds. Brain's retained local state is under `brain/`.
Large data is canonical at `/Volumes/Kingston/BrainData`; `brain/owner-work` and `brain/research-work`
are links to its `brain/{owner,research}/work` trees. The physical storage config is
`~/Developer/memecoin-runtime/brain/config/storage-v1.json`. Kingston UUID/root marker and the 50 GiB
free-space guard are unchanged. BrainDevelopment is a distinct internal volume: its ordinary duplicate
was removed; two opaque D43 custody containers remain untouched. Protected holdout is never cleanup input.
See [organization](engineering-organization.md) for the paused native operating model and source install.
