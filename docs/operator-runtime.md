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

## Operating mode and host boundaries

Current policy is **single-host development/research and owner runtime on Mac 1**. Mac 2 is
corporate temporary backup storage only during the Mac 1 reset, subject to its data-custody policy.
It is not a standby worker and receives no Brain environment, services or credentials. Recovery
returns to Mac 1, initially inert. Completion and verified pins belong in `memory/now.md`.
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

As of 2026-10-18, GitHub `avariceJS/memecoin-ai` owns all published code/history. `main` is the
only primary integration baseline. The consolidation advances the former `770699d` baseline through
all 321 research commits ending at `c157f79471c1cb330bdab01cc4b4cdd7b9a1a186`, followed by
test-only contract alignment (`d8fdff7`) and consolidation documentation. This includes Senses/storage work, neural artifact/statistics integrity,
DR tooling, and the practical Migration v1 implementation and operator package. It is code
integration, not model promotion, runtime deployment, backup/restore acceptance, or permission to erase.

| GitHub branch | Purpose and exact checkpoint | Remaining gate |
|---|---|---|
| `main` | Current integration baseline; start here | Use task-specific checks before each integration |
| `research/active` | Frozen compatibility ref at `c157f79471c1cb330bdab01cc4b4cdd7b9a1a186` | Historical Migration v1 package fetches this branch before `6ad944c`; that Mac2 package is now retired, but retain this ref until replacement recovery execution is verified |
| `codex/linux-portability-20261018` | Linux development at `fb36e7e43857e082cbe1c5f1d0ef6eb8f8dd5fe8` | Unmasked clean-host provisioning, inert-service apply, exact-head lifecycle and absence of transient service starts remain unqualified; consult this branch's `memory/now.md` and Linux receipts |
| `codex/v3-scratch-20261017` | Descriptor-bound DR scratch prototype at `4a84d5bd1496bbd6bb8832391edebd9bf51b845a` | Physical APFS/HFS and owner-scale qualification remain open; production capture remains fail-closed |
| `control-center/observatory-handoff` | Recovered UI reconstruction at `ce8505a6da99c5d5e1f118d1e55346f95981c328` | CC02.8 final integrated visual acceptance remains unfinished; nine formerly local commits are published; no new runtime/control permission |

The published topic heads after documentation/custody updates are Linux `65846e8`, v3 `6554a01`,
and UI `de4b42d`; their implementation checkpoints in the table remain unchanged. Root agent
instructions on each topic now identify `main` as the integration entrypoint. UI additionally
preserves 27 previously unpublished local review artifacts under
`docs/control-center/archives/local-pre-reset-20261018/`; five other local images were already
published and are referenced by their original blob IDs. These are historical records, not a new
visual acceptance.

Linux, v3, and UI are intentionally unmerged. Their latest written code does not establish complete
compatibility or acceptance. The UI branch retains historical scoped workflow records; current root
Git policy and this registry govern consolidation and future branch custody. No general work should
resume on frozen `research/active`.

`archive/pre-reset-20261018` is an immutable annotated GitHub tag whose commit is
`b35f8d089b603b3e33c54acbe0db38e7fa1b1178`. It retains all 14 previously unpublished commits,
intermediate blobs/trees, original checkpoint-ref mapping, and four historical migration helper
scripts. It is recovery custody, not a branch to merge or an executable baseline. Read its
`README.txt` and `manifest.json`; `blobs/<original-object-id>` and `trees/<original-object-id>` retain
exact source versions. One negative-test tag is stored as exact raw tag-object bytes for explicit
reproduction, without publishing it under a valid handoff name. Existing `brain-dr-handoff-v1` through
`v5` tags were not retargeted. The supplemental tag `archive/bootstrap-fixtures-20261018`
(`2f28fcc4b37bf8d54fb5a0fef9d2b7b8c3240293`) preserves seven historical negative-test tag objects
found in macOS temporary fixture directories; never use these as operator handoffs. The older v3
files also remain byte-exact in the published
`docs/archives/portability-disaster-recovery-v2/v3-capture-adapter-draft-2026-10-16.tar.gz`.

Recovery of an experimental task requires only a normal clone plus its published branch:

```sh
git fetch origin --prune --tags
git worktree add ../brain-linux --track -b codex/linux-portability-20261018 origin/codex/linux-portability-20261018
```

Use the corresponding registry name for v3 or UI. For new parallel tasks, use
`git worktree add -b codex/<task> ../brain-<task> origin/main`, then
`git push -u origin codex/<task>`. Commit and push meaningful checkpoints, integrate only after
checks/review, and remove the topic branch/worktree after verifying remote custody and ancestry.

The source and object audit, every discovered checkout's disposition, exact retained refs, test
results, and scan limits are in the immutable
[consolidation receipt](evidence/git-consolidation-2026-10-18.json). No Kingston payload, private
configuration, owner runtime, or original Migration v1 handoff/receipt bytes were changed. The owner
checkout stays at its historical source commit; a newer GitHub `main` does not update its files.

Consolidation validation: 62 Migration v1 tests, 591 DR/host tests (two explicit Kingston opt-ins
skipped), 418 experience tests (one CUDA skip), 39 Observatory Python boundary tests, synthetic smoke,
and 80 final Gecko contract tests passed. The broader initial run had 4,878 passes, 56 failures and
six skips after excluding one test module whose legacy local `work/` capture is absent in a clean
clone. Of those failures, 43 reproduce on old `main`; eight pass on isolated rerun after environment
and resource differences are removed; five exposed stale test bindings and are corrected by the
80-test gate. No production implementation was changed to make tests pass. `make check` still stops
at 107 preexisting Ruff findings (old `main`: 110; no new diagnostics). Full-suite green is not claimed.
The five historical `git diff --check 770699d..c157f79` findings are immutable receipt/transcript
whitespace and plain-text `=======` heading underlines, not unresolved merges; their bytes remain
unchanged. New changes pass diff, targeted Ruff and formatting checks.

GitHub Actions [run 39133861313](https://github.com/avariceJS/memecoin-ai/actions/runs/39133861313)
reported failure before jobs started: its account annotation cites failed recent payments or the
spending limit. No CI execution result is inferred from that infrastructure failure, and no billing
or repository settings were changed. The published `brain-git-consolidated-20261018` tag anchors
`a6ee5fcdef32e10475983428dd83302a81dc8773`, including the final object-custody and runtime-status
verification in its annotation. Later documentation clarification does not change its tested code.
