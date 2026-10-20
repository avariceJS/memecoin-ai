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

### Post-reset local development (2026-10-19)

Open `/Users/root1/Developer/memecoin-research` on `main` in Cursor. Keep the detached historical
owner checkout separate. In a terminal:

```sh
cd /Users/root1/Developer/memecoin-research
source tools/brain_dev_env.sh
.venv/bin/python tools/brain_dev_operator_preview.py serve --latest --port 8793
```

Open `http://127.0.0.1:8793`; Ctrl-C stops this foreground service. No LaunchAgent is needed.
The preview snapshots the preserved owner `work/brain-b13/operator04` into ignored `work/dev-local/`.
To create a new snapshot, stop the server, run the following, then repeat `serve --latest`:

```sh
.venv/bin/python tools/brain_dev_operator_preview.py snapshot --refresh
```

Snapshotting checks source DB/WAL/SHM family stability before using SQLite backup on a scratch
copy; it does not initialize or query the original databases. Source config/registry, health,
history, models and paper projections are copied through an allowlist. Dataset/model payloads,
credentials and deployment authority are not copied. Serving requires the preview marker, binds
loopback, rejects every POST, disables write controls, and starts neither operator nor Watch tickers.
Production server defaults are unchanged. Never run the ordinary mutable operator against owner
state merely to inspect it. Before binding HTTP, preview validation rejects linked files, external
source/paper bindings, operational activation metadata and changed database hashes/nonempty WAL.

All seven existing tabs (Overview, Data, Training, Models, Paper, Watch, System), including the
System journal, loaded real copied state in actual-API jsdom checks; 93,721 matured observations
matched the API. No missing figures were fabricated. Historical timestamps/STOPs remain visible.
The preserved owner has no Watch session. The preview explicitly reports Watch state as omitted;
it never follows the ordinary sibling Watch-state path. Offline Watch provider-double
tests cover resolve/start/tick; live Watch still needs its separate release/provider acceptance.
Native Safari access was unavailable to Computer Use, so native visual/CC02 acceptance is not claimed.

The separate React Observatory is a pinned historical engineering projection, not current operator
telemetry. For its existing development workflow, use two terminals after sourcing the helper:

```sh
.venv/bin/python apps/control-center/adapter/server.py
npm --prefix apps/control-center/web run dev
```

Open `http://127.0.0.1:5173` (adapter `127.0.0.1:8787`); Ctrl-C in each terminal shuts them down.
The local adapter/frontend HTTP flow, 40 Python tests, 44 web tests, typecheck and build passed.
This does not overturn the earlier pending/rejected visual design acceptance.
Preview and Observatory listeners bypass reverse DNS for their explicit loopback bind: the restored
host resolver stalled during the final restart drill. Both then passed startup/restart/shutdown with
no DNS dependency; the ordinary mutable operator server keeps its existing bind behavior.

Read-only historical research commands (choose a **new**, nonexistent output JSON path):

```sh
work/ml-venv/bin/python tools/brain_dev_evaluate.py \
  --model /Users/root1/Desktop/memecoin-ai/work/brain-b13/operator04/operator/jobs/job_5edd5e5ecda88338/artifact/model \
  --dataset /Users/root1/Desktop/memecoin-ai/work/brain-b13/operator04/operator/jobs/job_f34eb5cc5889b45a/artifact/dataset \
  --champion /Users/root1/Desktop/memecoin-ai/work/brain-b01/dev01/models/000000 \
  --output work/dev-local/paired-evaluation-new.json
```

This reproduces the registered paired comparison without fitting or promotion. `make smoke` is
the supported synthetic reference replay; its real-data gate remains closed. Existing B07 historical
virtual replay was also reproduced exactly (204 market joins, 61 forecast joins, zero provider calls).
Persisted paper intents/books remain historical evidence, not proof of real fills or profitable execution.

Senses adapters, offline quality/transport tests and internal storage reads are available, but no
external campaign is active. Before live collection: separately authorize a bounded campaign with
source/request/time budgets and stop/rollback ownership; requalify provider access and current data
admission; bind new derived outputs explicitly to the internal development root UUID and verify
its 50-GiB guard; then pass the existing campaign/release admission and activation checks. Existing
historical STOPs, leases, quarantine and protected F01/D43 boundaries must remain intact. Neither
the read-only replica nor a new Git commit grants writer admission or authorizes credentials work.

Local acceptance receipts (including source hashes, restart and no-Kingston checks) are under
`/Users/root1/Desktop/restore/evidence/development-readiness-20261019/` and its sibling UI/research
evidence directories. Current broad-suite limitations are summarized in `memory/now.md`.

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

## Git integration and recovery registry

### Consolidation and founder audit hold — 2026-10-20

The founder paused organization development and authorized repository consolidation. This registry
supersedes older branch instructions. `main` stays exactly at
`2f8da5f92da521b511e0d4fb20205981a000cab5`; no experimental source was promoted.

| Branch | Role | Status |
|---|---|---|
| `main` | Stable existing baseline | Unchanged; existing repository failures remain documented |
| `brain/development` | Current Brain development entry | Based on main; consolidation documentation only |
| `org/development` | Unified organization source and audit entry | Reviewed metering and validator plus additive reviewed Store; merged candidate is not an independently accepted release |
| `brain/linux` | Isolated Linux portability candidate | Original `65846e8` source retained; clean-host provisioning, lifecycle and inert-service qualification remain open |
| `research/active` | Frozen remote compatibility alias at `c157f79471c1cb330bdab01cc4b4cdd7b9a1a186` | Not a development lane; current `engineering/bootstrap.py` still resolves `origin/research/active`, and historical recovery packages name it |

The compatibility alias cannot be removed without changing and qualifying current bootstrap/recovery
callers. That is a separate controller release, outside this consolidation. Frozen private pilot
repositories also retain their exact historical refs; they are not development checkouts. Linux
changes overlap operator host/control and shared storage behavior; merging this unqualified platform
release into Brain solely to reduce branch count would overstate acceptance. Linux was renamed,
not updated to the current Brain baseline; reconcile that dependency during its future review.

The obsolete development-readiness and organization topic branches are consolidated. UI reconstruction
`de4b42d` and DR v3 prototype `6554a01` are archived, not accepted or silently merged. UI visual
acceptance and physical/owner-scale v3 qualification remain open. Unreviewed supervisor proposal
`1546e4c` and backup/restore `613c5e9` plus its last dirty repair are also custody only.

`archive/consolidation-20261020` is an annotated, never-retargeted custody tag. Its commit has an
archive tree, not a runnable project. `manifest.json` maps every original ref/checkpoint and dirty
organization source file. Its parents preserve all 69 original commit pins. A separate bare clone
from this GitHub tag verified all pins, four dirty organization files, and `git fsck --full` before
branch deletion. Five historical owner documentation edits remain untouched at
`/Users/root1/Desktop/memecoin-ai` and in a verified private dirty-file archive. Private bundles,
ignored worktree artifacts and raw reviewer logs remain outside Git under
`/Users/root1/Developer/brain-org-state/consolidation-20261020/`.

Recover an archived source without merging the custody tree:

```sh
git fetch origin --tags
git show archive/consolidation-20261020:manifest.json
git switch -c recovery/<topic> <original-commit-from-manifest>
```

The archive stores the final uncommitted service repair under
`snapshots/checkout-15/engineering/service.py` and the matching test path. Reconstruct it on
`613c5e9e6dd6fe00d6ebf4b072a31885826ac7a4` only in an isolated recovery checkout, then obtain frozen
acceptance and independent review. Existing `archive/pre-reset-20261018`,
`archive/bootstrap-fixtures-20261018`, `archive/research-active-pre-org-20261019`, DR handoff tags and
`brain-git-consolidated-20261018` remain unchanged. Historical protocol hashes require their exact
source commit, never current archival pointer text.

New ordinary Brain work starts from `brain/development`; organization audit and any later explicitly
resumed Luna work start from `org/development`. The independent director audit of `b2dcd6d` is now
complete; findings and release gates are owned by `engineering-organization.md`. Development remains
paused pending explicit founder resumption. No Goal, team, service, inference, collector, training, AUTO or trading is resumed
by consolidation. Pilot SQLite, its six failures and zero acceptances/integrations, STOP, protected
datasets and historical owner runtime stay unchanged. The current publisher still targets private
`research/active`; no new publication authority or runtime branch rebinding is implied.

See the machine-readable [consolidation receipt](evidence/git-consolidation-2026-10-20.json) for exact
source selection, tests, failures, custody and cleanup. Historical acceptance from the earlier
[2026-10-18 consolidation](evidence/git-consolidation-2026-10-18.json) is not new verification.
