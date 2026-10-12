# Current Project State

**As of:** 2026-10-12 12:01Z. Checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, clean pushed HEAD `a73f3ec008c8faeb643a4900eeaa49bb032fa9ec`. `main` remains
at `3dab3a36cccaa5913fdfa697b054a17181de3dd0`; do not advance it until the live collection
capability is verified. Production, champion, protected evaluation, trading, and Cortex are untouched.

## Prospective acquisition

- The separate `ai.memecoin.brain.senses.external-rolling-v4` LaunchAgent is loaded with a 60-second
  schedule and no calendar expiry. Kingston `/Volumes/Kingston/BrainData` matches root ID
  `9428695af6d4f46c08e1a444cdc66de8`, UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`, and has about
  219.38 GB free; the 50-GiB floor and 8-GiB operating reserve remain enforced.
- First activation pinned a 150-GB cumulative service allocation cap, with 4-GB whole-epoch and
  rolling 30-day request/credit/response/allocation caps. The descriptor-relative census counts all
  service-root files/directories, rejects unexplained entries, and terminal receipts bind epoch
  allocation manifests. Raw captures are not deleted; lifetime-cap pause does not auto-resume.
- The initial future-only plan was superseded because its source hash changed before its first
  request intent. It is terminal `abandoned`, manifest-verified, and has no provider requests or
  captures. The one-hour recovery cooldown moves the next fresh anchor to 13:00Z; the 12:00Z window
  remains unobserved and is not replayed. One clock probe timed out; subsequent probes passed.
- Latest Kingston monitor reports `cooldown`, sequence 7, about 229 KiB service allocation, and
  about 219.38 GB volume free. LaunchAgent loaded and last scheduled exit was 0. Next checks: fresh
  13:00Z anchor capture/restart replay, then the next hourly inventory/sample frame and independent
  raw-capture replay. Actual provider charge remains unknown/unverified.

## Senses state and next horizon

Sol approved Helius-only hourly finalized anchor/inventory/sample collection: max 34 calls/frame,
16 deterministic hash-ranked inventory members, 817 calls/4 GB per epoch, 25,000 requests/250,000
modeled credits/120 GB rolling physical allocation per 30 days, and no backfill or ambiguous retry.
New v4 code, forced-detach and crash recovery tests pass. Scoped Ruff/Mypy, 53 focused/property/
recovery tests, and `make smoke` pass. `make check` still reports 107 existing Ruff findings in
unmodified files.

Economic qualification remains open: candidate-position/runtime witness, native settlement, and
`buyExact25` semantics are not qualified. The terminal Agave fetch attempt, dirty Cargo home/target,
and their gates are preserved; no retry/build/ProgramTest until a new Sol decision. Sol is deciding
the next useful bounded horizon. Keep c4 and `external-v2-20261012T0600Z` terminal; no replay,
backfill, protected data, or Cortex.

Use canonical owner docs and only task-specific receipts. Keep this file short; raw evidence stays
immutable under Kingston and `docs/evidence/`.
