# Current Project State

**As of:** 2026-10-09 Europe/Moscow. Start from `docs/INDEX.md`; verify source claims against the linked receipt.

## Goal and boundaries

Build a scalable, reproducible Solana learning system. No demonstrated executable edge. Do not promote/deploy, sign transactions, use real money, access protected F01/D43 or reserved holdout payloads, or begin the Cortex block before the user returns. Preserve production `/Users/root1/Desktop/memecoin-ai`; Mac 2 remains standby. No Grok. Keep credentials outside Git, docs and ordinary logs.

## Active state

- Research checkout `/Users/root1/Developer/memecoin-research`, branch `research/active`, latest consolidated baseline `d3727c8`. Local commits are allowed; do not push. Current Senses work includes `98c6353` (qualification/Alchemy) and `8e5ff1f` (sensing episode); re-entry fixes are pending final commit.
- Production owner was clean at the handoff fence; no runtime, champion, config, STOP or protected data changed in this work.
- Mac 1 is the active research host; Mac 2 is standby. See `docs/two-mac-workflow.md` before host work.

## Existing Brain capability

- Paged experience, snapshots, training generations, population/diversity accounting and event-time observation contracts are established. Existing real train source `81f7c74b…4b10c3e0` has 170/170 markets with `unknown` support; absence from the provider does not prove zero activity or death.
- Opt-in `partial_count_lower_bound_pinball_v1` consumes exact counts and positive qualified censored lower bounds with a one-sided per-quantile hinge. Zero bounds and unavailable bounds remain excluded. Synthetic fit/resume/evaluation is engineering-only; no learning gain. See `docs/training-evaluation.md` and `docs/evidence/brain-censoring-objective-2026-10-08.json`.
- September cohort is studied, not a fresh holdout. No registered fresh independent comparison has been run.

## Brain Senses — current capability

- `memecoin_experience.qualification` binds versioned process contracts, receipt/scope/population pins, code/decoder identity and exact fixture rows. Only the closed synthetic verifier currently grants `complete`; no real-source completeness verifier exists.
- `memecoin_experience.sensing` seals arrival-independent paged schedules, reconciles a multistream source to receipts on publication and consumption, then streams point-in-time episodes into generic v2 snapshots. Input events/support obey availability; quiet origins remain. Status channels preserve complete, unknown, failed, stopped, unsupported and unasserted gap. No provider disappearance becomes lifecycle/death.
- Counts use `(decision,maturity]`. Complete process support gives exact values including zero; incomplete or enrollment-truncated windows keep a lower bound in metadata and a masked target. Exact price returns require unique exact observations at both endpoints. A synthetic two-process episode now includes positive partial censoring and an entity re-enrolled across a gap. Sparse history/targets do not bridge nonoverlapping enrollment intervals.
- `budget_dry_run` binds requests, account units/entitlement, evidence and ledger state; ambiguous attempts poison the account pending reconciliation. It has no transport or dispatch path.
- One schedule-only profile processed 1,025 rows into three pages in 0.041 s / 278,257 traced bytes on Python 3.13.6 arm64. This does not establish episode, large-source or 100M scale.

## Alchemy and real source boundary (2026-10-09)

- Published Free plan: 30M CU/month, 300 CU/s. Free returned a two-method batch and basic RPC, but `getSignaturesForAddress` had 41 HTTP 429s in 56 calls across bounded low-throughput phases. Free meter was not exposed.
- Existing PAYG: 228/228 bounded calls succeeded; delayed meter reconciled 5,520 CU (180 `getSlot` ×20 and 48 `getSignaturesForAddress` ×40), estimated $0.002898. Peak 33.68 calls/s is a test phase, not saturation. Use Free for healthy development; PAYG for qualified indexed work when Free blocks throughput/capability. Keep budgets per account.
- A separate PAYG pool-address probe returned one 10-row finalized signature page, descending slots 454,538,107→453,121,085. MCP could not expose two large-page results as complete JSON; a later local hashing error lost another result after RPC. No retry was issued. Meter stayed at 5,520 CU but freshness stopped at 2026-10-08 21:36 UTC, so probe CU delta is unresolved. Receipt: `docs/evidence/alchemy-signature-page-probe-2026-10-09.json`.
- Qualified in reality: Alchemy endpoint behavior for bounded Solana RPC, plus existing GeckoTerminal OHLCV/price lane. Not qualified: complete market event process, Alchemy historical retention/pagination, empty-page meaning, or market inactivity/death. RPC signatures are positive references only until raw capture, cursor chain and independent range reconciliation are proven. See `docs/brain-source-architecture.md` and `docs/evidence/README.md`.

## Verification and next wave

- Latest full `make check && make smoke`: 1,184 passed, 24 optional skips; Control Center 39 passed; smoke reproducible and synthetic-only. Ruff, format and mypy (59 source files) passed. ML/torch tests are skipped in this environment. Sensing/sparse focused suite: 26 passed.
- Next: build a direct, raw-sealed, retry-free source capture path with preflighted hashing and account-level CU reconciliation; qualify a named process using complete cursor/boundary accounting plus independent reconciliation; then connect its explicit support receipts to sensing snapshots and verify censored-objective training/evaluation on fresh independent populations. Do not reuse September and do not start Cortex.
