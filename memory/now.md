# Current Project State

**As of:** 2026-10-12 10:19Z. Checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, last pushed HEAD `3dab3a36cccaa5913fdfa697b054a17181de3dd0`. Current
worktree adds an offline rolling-acquisition policy module and tests; not yet committed. Production,
champion, protected evaluation, trading, and Cortex remain untouched.

## Live runtime and storage

- No prospective Senses market collector is loaded. The storage-health monitor is loaded and
  reports Kingston ready. Other Senses collectors are retired/disabled.
- c4 and `external-v2-20261012T0600Z` are terminal. Do not replay or backfill; its nine missed
  windows remain unobserved. The old service is not eligible as a successor.
- Kingston `/Volumes/Kingston/BrainData` identity matches configured root ID
  `9428695af6d4f46c08e1a444cdc66de8` and UUID
  `8f8f1081-9be4-4914-9111-75f1b90255bf`; latest `df` had about 214 GiB free. Enforce the 50-GiB
  floor and never fall back to internal storage.
- Read-only Alchemy usage through 10:15Z remains 18,680 CU / $0.01 for MTD, 7d, and 30d; Helius
  confirms a configured key but its usage meter is unavailable without a JWT. Meter uncertainty is
  not an acquisition blocker. Helius public Free limits currently list 1M credits and 10 RPS; use
  conservative local caps and preserve actual charge as unknown/unverified.

## Acquisition horizon

Sol selected a new independent forward-only Helius service: one fresh finalized anchor, then 24
hourly inventory-first frames per epoch; caps are 817 requests and 4 GB per epoch, and 25,000
requests / 250,000 conservatively modeled credits / 120 GB in a rolling 30-day accounting window.
Thirty days is not a service expiry. Misses stay gaps; ambiguous requests are never replayed. The
new `tools/brain_senses_external_rolling_policy_v1.py` only encodes pure schedule/cap math; six
focused property tests pass. No live plan, journal, rolling supervisor, LaunchAgent qualification,
or provider dispatch has been implemented or activated. The next acquisition gate is that separate
runtime plus independent recovery verification, exact-source LaunchAgent rehearsal, and bounded
Kingston/provider validation. Read current policy and gate details in `docs/brain-source-architecture.md`.

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
