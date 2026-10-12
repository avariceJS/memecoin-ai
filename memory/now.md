# Current Project State

**As of:** 2026-10-12 10:34Z. Checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, last pushed HEAD `8b39d0657a689bcf0ccce971e426d74518d256a8`. Current
worktree adds v4 schema/control/epoch verification and policy corrections; not yet committed.
Production, champion, protected evaluation, trading, and Cortex remain untouched.

## Live runtime and storage

- No prospective Senses market collector is loaded. The storage-health monitor is loaded and
  reports Kingston ready. Other Senses collectors are retired/disabled.
- c4 and `external-v2-20261012T0600Z` are terminal. Do not replay or backfill; its nine missed
  windows remain unobserved. The old service is not eligible as a successor.
- Kingston `/Volumes/Kingston/BrainData` identity matches configured root ID
  `9428695af6d4f46c08e1a444cdc66de8` and UUID
  `8f8f1081-9be4-4914-9111-75f1b90255bf`; latest `df` had 214,242,320 KiB free (about 214 GiB),
  or 41.4 days above the 50-GiB floor at the 4-GB/day physical cap. This is a maximum-rate runway
  scenario, not a lease. Never fall back to internal storage.
- Read-only Alchemy usage through 10:15Z remains 18,680 CU / $0.01 for MTD, 7d, and 30d; Helius
  confirms a configured key but its usage meter is unavailable without a JWT. Meter uncertainty is
  not an acquisition blocker. Helius public Free limits currently list 1M credits and 10 RPS; use
  conservative local caps and preserve actual charge as unknown/unverified.
- One diagnostic Helius finalized `getSlot` at 10:20Z succeeded (slot 456,011,716; 43-byte
  response). It was not saved as market data; this confirms endpoint/key/network readiness only.

## Acquisition horizon

Sol selected a new independent forward-only Helius service: one fresh finalized anchor, then 24
hourly inventory-first frames per epoch; caps are 817 requests, 4 GB response bytes, and 4 GB
physical allocation per epoch; rolling 30-day caps are 25,000 requests, 250,000 modeled credits,
120 GB response bytes, and 120 GB physical allocation. Thirty days is not a service expiry. Misses
stay gaps; ambiguous requests are never replayed. Policy math passes six focused property tests.
Sol ruled that a new v4 plan/orchestrator/verifier/launcher is required; do not reuse the v2 engine
or synthetic v3 launcher. Offline policy, canonical plan, hash-chain, control transitions and
epoch no-replay projections now pass 26 focused/property tests plus Ruff, scoped Mypy and smoke. No
live runner or market dispatch exists yet. Remaining gates: capture/file-closure verifier, actual
descriptor-bound orchestrator and physical-allocation ledger, crash/duplicate-poll/cap/provider-error
tests, disposable and Kingston no-provider LaunchAgent rehearsal, then one future live anchor/frame
with independent replay. Read policy in `docs/brain-source-architecture.md`.

## Other active Senses evidence

- Positive-reference readiness v1 and the sample-local unknown queue are independently replayed,
  create-only Kingston artifacts. They cover only the sealed external-v2 sample; unobserved windows
  remain null and no completeness, population, PIT, training, economic, or admission claim follows.
- Candidate-position/runtime semantics remain synthetic or source-feasibility only: no unique
  Bank-to-root transaction witness, native settlement, or `buyExact25` qualification. Preserve
  existing registry/module hashes pending a reviewed semantic contract.
- The authorized Agave Cargo fetch ended terminally after creating target metadata. Preserve its
  staged Cargo home/target; no retry, metadata, build, or ProgramTest without a separate Sol review.

Use `docs/INDEX.md` and canonical owner documents for durable scope. Keep this file short; raw
receipts remain immutable under `docs/evidence/` and research artifacts under Kingston.
