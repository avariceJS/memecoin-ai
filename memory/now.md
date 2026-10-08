# Current Project State

**As of:** 2026-10-09 Europe/Moscow. Re-read owner state before operational decisions.

## Goal and boundaries

Build a large Solana learning system with growing useful corpus and reproducible model generations.
No demonstrated executable edge. No promotion, signing, real-money trading, protected F01/D43 or
reserved holdout payload access. Preserve user-owned untracked `.codex/`. Grok is disabled; use
Eliza for bounded nonsecret assistance and verify findings. Runtime dependencies remain stdlib.

## Active operating mode

- **Mac 1 active:** `192.168.31.66`, `/Users/root1/Developer/memecoin-research`, branch
  `research/active` (from canonical integrated `main`). Open this checkout in a Mac 1 Codex session; the old Mac 2 session does
  not change execution host automatically. Mac 2 is **standby/inactive**; no new research or
  acquisition there without an explicit worker assignment.
- Handoff receipt: `docs/evidence/brain-single-host-handoff-2026-10-08.json`. Full Git history and
  178,401 research files (4,745,125,891 bytes) transferred and hash-verified; stopped SQLite/WAL
  companion files preserved. All Mac 2 originals, virtualenvs and user `.codex/config.toml` remain.
- New isolated `.venv` and `work/ml-venv` installed on Mac 1 from pins. At `99980a2`, Mac 1 passed
  1,146 core tests / 24 skips, 60 ML tests / one CUDA skip, lint/format/mypy/smoke and both pip checks.
  Migrated real source/snapshot/generation verified and standalone prediction works locally.
- Owner `/Users/root1/Desktop/memecoin-ai` remains clean at `a594474`; before/after fences match:
  revision 33 paused, no busy jobs, runtime08, champion, source STOP/config/universe unchanged.
  No production switch, credential transfer or provider requests. Git consolidation is recorded below.
- Recovery package/manifests/logs: Mac 1 `/Users/root1/Developer/.memecoin-migration-20261008`,
  Mac 2 `work/host-migration-20261008`. Frozen old-host paths remain evidence; use explicit local
  transport locators/new declarations, never rewrite sealed files or restart expired campaigns.
  Reactivation procedure and stable ownership rules: `docs/operator-runtime.md`.

## Git consolidation

- Canonical `main` combines the prior main, all 32 subsequent research commits and 22 unique
  Observatory commits. Integration code: `fb04468`; receipt:
  `docs/evidence/brain-git-consolidation-2026-10-08.json`.
- Active development uses `research/active` on Mac 1. Both old branch refs are retained and fully
  absorbed. Mac 2 remains standby; it provides authenticated GitHub transport for this publication.
  Mac 1 origin/upstream is configured; noninteractive GitHub authentication there is not verified.
- No textual merge conflicts. Scoped UI rules/guard/helper policy were reconciled; core research
  and two-host tools are unchanged. UI adapter, web code, pins and published bytes are unchanged.
  Observatory is historical evidence, not current Brain telemetry; CC02 visual acceptance is pending.
- Mac 1 checks: core 1,146 passed / 24 optional skips; full ML environment 1,217 passed / two skips
  (Playwright browser acceptance and actual CUDA). UI Python 39, frontend 44; lint/type checks,
  smoke, Python/frontend builds, workflow and portable real-generation prediction passed.
  Node 24.5 dependency-engine/PostCSS/chunk warnings remain; no visual acceptance is claimed.
- Production fence still matches the verified handoff: owner HEAD/config/champion/STOP unchanged.
  Logs are in `work/git-consolidation-20261008` on both hosts. No deployment or provider requests.

## Completed sparse/event-time stage

- Code: `f9a0c1f` observation foundation; `86c33da` qualification checkpoint recovery;
  `b975471` namespaced protected-entity evaluation guard before payload access.
- Receipt: `docs/evidence/brain-sparse-experience-2026-10-08.json`, SHA256
  `b85d22b84c3aa5e5b5759cf92ab1a44d3f7fb84812a066ac8aa946314617e8ea`.
  Root: `work/brain-sparse-qualification-20261008/`; frozen code at `f9a0c1f`.
- Immutable typed observation facts separate enrollment, events, process support and evidenced
  lifecycle. CAS shards/paged catalogs, bounded-cache disposable query index, idempotent replication.
  Sparse projection uses short histories, explicit age/gaps/masks and exact endpoint/count targets.
  Historical replay and arrival-time decisions are distinct; unknown support never means inactivity.
- Existing **train-role source only**: 38,705 events + 170 births -> 38,875 origins across all 170
  markets (previous contiguous producer: 8,792 origins, 49 markets). Same May-June/July/August roles:
  16,616 / 4,348 / 10,504 usable origins, 46 / 26 / 93 markets. Five markets have no usable endpoint
  label but remain represented. All birth-only price targets are unsupported, not invented zeros.
- One CPU engineering generation: 5,577 parameters, one epoch, 16,616 draws, 33 steps; largest draw
  share 20.20%, Shannon effective count 13.40. Changed representation/admission/recipe means this
  is not a controlled learning-gain comparison. No September evaluation payload read or rescoring.
- Synthetic load: 2,000 entities, 117,664 facts, 113,664 origins; 90,969 exact count targets and
  22,695 censored. Includes 118 empty entities, 94 with complete support. 55.78 seconds / 63.5 MiB
  peak RSS without ML libraries. Real conversion plus fit: 85.10 seconds / 386 MiB including ML.
- Verified source/snapshot/code archive, real generation and committed exposure chain; real source
  and snapshot replicas preserve identity and repeat idempotently; standalone prediction finite.
- `make check` 1,146 passed / 24 optional skips, lint/format/mypy and smoke passed. Final optional
  ML regression set: 60 passed / one actual-CUDA skip, including exact resume, harness interruption,
  synthetic evaluation and final namespace guard. Full suite started before the final guard change;
  the guard has focused coverage. No jobs remain running at completion.
- Qwen inventoried interfaces and audited receipt arithmetic; GLM checked temporal/provenance
  boundaries. Incorrect helper hypotheses were rejected. Helper outputs are under the run root.
- Initial real job had a transcribed wrong source pin and failed before payload verification.
  `pin-correction.json` binds the failure and corrected `real-job02.json`; `real02/` is the successful
  run. Never rewrite frozen declarations or receipts to make a failed run look successful.

## Completed censor-aware objective step

- Opt-in `partial_count_lower_bound_pinball_v1` trains on exact counts plus qualified positive
  censored lower bounds; exact-label masks and default objectives remain unchanged. Zero bounds,
  unknown labels, and missing point-in-time bound availability never become exact zeros. Count heads
  fit in `log1p` units and predictions return to nonnegative event-count units.
- Support details preserve unknown, failed, stopped, unsupported, unasserted-gap, and administrative
  window-end reasons. Provider disappearance still creates no lifecycle transition.
- Re-audited the existing real train source `81f7c74b…4b10c3e0`: 170 enrollments, 38,705 events,
  170 births, 170 support facts, all 170 `unknown`. This source cannot establish complete counts or
  inactivity. At that 2026-10-08 checkpoint no provider request had been made and Alchemy limits
  were unverified; bounded endpoint behavior was measured on 2026-10-09 below.
- Synthetic fit/resume/evaluation/inference reproduced in `work/brain-censoring-objective-2026-10-08/`.
  Receipt: [`brain-censoring-objective-2026-10-08.json`](../docs/evidence/brain-censoring-objective-2026-10-08.json).
  The run is engineering-only and did not establish learning gain.
- `make check`: 1,147 passed / 24 optional skips; smoke passed; `make ml-check` 17 passed and
  `make runner-check` 18 passed. Focused sparse/learning suite: 42 passed / one actual-CUDA skip.
- Sol architecture review was incorporated. Eliza Kimi/GLM/Qwen connectors were unavailable in this
  session; no secrets or protected payloads were sent externally.
- Implementation committed locally on `research/active` as `5c8ea27`; working tree was clean
  after commit. Branch remains two commits ahead of `origin/research/active`; no push or deployment.

## Next Brain Senses horizon

Continue with an offline causal sensing episode: verified synthetic price and irregular-flow
processes, decision origins from a pinned clock schedule (including quiet intervals), stream-specific
as-of histories, point-in-time dense snapshots, objective-compatible targets, deterministic replay,
and a dry-run account-budget adapter. This is a semantics/integration exercise, not source evidence.
After reassessing it, qualify real named event coverage using complete request/cursor accounting and
independent reconciliation; PAYG is available for bounded work, with per-campaign measured budget.
Do not infer complete activity, inactivity, lifecycle or death from endpoint availability. Fresh
independent population comparisons remain after sourcing and must not reuse September.

## Brain Senses Wave 1

`memecoin_experience.qualification` adds a versioned process descriptor, immutable receipt and paged
scope manifest with per-entity/interval status, availability, denominators, capabilities and evidence.
Projection revalidates exact member scope before using existing observation/population facts; hashes
check identity and consistency, not provider truth. `candidate_process_records` is the accounting unit;
only the registered closed synthetic-fixture verifier grants complete support, after re-executing the
bound input and process/member pins; the proof binds the verifier code hash. Synthetic source identity
is derived from process/scope pins and decoder identity is registered. No real-source completeness
verifier or qualification was added.
Synthetic tests only; this verifier grants no real-source completeness. Market-process acquisition
was not performed in this wave. Authenticated Alchemy usage was initially 0 CU, but the account
meter was refreshed after the bounded read-only qualification below.

## Prior campaign and owner

- Prior receipt: `docs/evidence/brain-independent-population-learning-2026-10-08.json`.
  Root `work/brain-diverse-campaign-20261008/`: all 1,228 frozen requests attempted, one sealed
  timeout never retried, six duplicate-bar responses quarantined; all 307 enrollments retained.
  Do not restart its expired acquisition envelope. Original infeasible `learning/` stays unchanged;
  `learning02/` completed three candidates. Broader neural data helped neural controls but lost to
  empirical quantile baselines. September is studied, not fresh evaluation.
- Mac 1: **192.168.31.66**, user `root1`, strict existing pinned SSH key. The prior
  `172.22.124.203` address timed out. Migration before/after read-only fences agree; recheck owner
  before any later operational action. No runtime mutation in the sparse or migration stage.
- Last recorded champion: `9ea4a6bd7977fb66fe4a18eba54ed7eaed0afd65810ec99c75aec3e1443e3920`, runtime08.
  No production state was inspected or changed in this sensing work.

Start from `docs/INDEX.md` for canonical architecture, source, training and operator contracts.

## Brain Senses Wave 1 — endpoint qualification update (2026-10-09)

- Sanitized receipt: `docs/evidence/alchemy-endpoint-qualification-2026-10-09.json`. Free Solana
  verified basic RPC and a two-method HTTP batch; signature lookup produced 41 HTTP 429 responses
  in 56 calls across two bounded phases, with no retries. PAYG had 228/228 successful calls and a
  measured usage delta of 5,520 CU: `getSlot` 20 CU/call and `getSignaturesForAddress` 40 CU/call,
  matching the delayed account meter. Estimated increment at the published rate: $0.002898.
- Choose Free for healthy basic development calls and PAYG for bounded indexed-signature
  qualification under the observed Free 429 pattern. The test did not saturate PAYG, test its HTTP
  batch behavior, inspect Free account usage, establish historical retention, or qualify market
  completeness. The measured 33.68 PAYG calls/s is a phase result, not a plan limit.
- The Free key is local in ignored `.env.local` (mode 0600); no endpoint/key is in Git, docs or
  receipts. `MEMECOIN_ALCHEMY_FREE_HTTP_URL` in `.env.example` is blank.
- Qualification implementation remains synthetic-only and must not project real zero counts. Sol
  reviewed the next horizon: scheduled offline multistream sensing with point-in-time availability,
  followed by budget-admission dry-run. Cache invalidation still stats all fixture dependencies;
  large-scope throughput remains unqualified.

## Brain Senses Wave 2 — offline episode prototype (2026-10-09)

- Added uncommitted `memecoin_experience.sensing`: paged arrival-independent schedules, wrapper
  reconciliation to the same receipt-verified synthetic source fixtures, streaming multistream
  episode snapshots, and simulation-only budget decisions. No provider, credential, or protected
  payload access; no real-source completeness path.
- A synthetic two-process integration case covers quiet origins, as-of arrival, complete zero,
  `(decision,maturity]` event boundaries, replay, and snapshot identity after observation replication.
  Focused suites passed 53 tests. A 1,025-origin schedule used three 512-row pages and measured
  0.041 s / 278,257 bytes traced peak on Python 3.13.6 arm64 in one local run; no scale inference.
- Current index boundary rejects repeated population enrollment intervals. Full `make check` and
  `make smoke` remain for root integration review. No commit made.
