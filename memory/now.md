# Current Project State

**As of:** 2026-10-12 12:46Z. Checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, at `cd9285268b7e2f4ec124baebf68488e1b4404db1`. Recent work is committed and
pushed. `main` remains `3dab3a36cccaa5913fdfa697b054a17181de3dd0`; do not advance until live
collection, recovery, and independent replay are verified. Production, champion, protected data,
trading, and Cortex are untouched.

## Prospective acquisition

- `ai.memecoin.brain.senses.external-rolling-v4` is loaded under `gui/501`, with 60-second ticks,
  `RunAtLoad`, and no calendar expiry. Helius-only policy is one fresh finalized anchor followed
  by hourly inventory-first frames; max 34 calls/frame, 16 selected members, 817 calls/4 GB per
  epoch, and rolling 30-day caps. No provider call or capture is yet verified.
- Kingston `/Volumes/Kingston/BrainData` matches root ID
  `9428695af6d4f46c08e1a444cdc66de8`, UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`; latest
  available space was 218,272,071,680 bytes. The 150-GB lifetime cap, 50-GiB floor, 8-GiB reserve,
  descriptor-bound service writes, and no-delete/no-fallback rules remain active.
- The initial future-only plan is terminally abandoned before request intent. 12:00Z is an
  unobserved gap; 13:00Z is the next fresh anchor. The latest monitor says `cooldown`. One scheduled
  tick returned 1 without updating the monitor; the next scheduled tick succeeded (runs=43, last
  exit 0, monitor sequence 44). Verify anchor request, restart recovery, first hourly frame, and
  independent replay. Actual provider charges remain unknown/unverified.

## Senses state and next horizon

The recent separately versioned Agave wrapper and focused tests are pushed. Sol authorized exactly
one offline metadata invocation. Cargo returned zero, but the wrapper rejected colliding redacted
local package IDs; its stdout/graph digest was not persisted. No Cargo retry is authorized. The
read-only receipt is on Kingston at
`qualifications/agave-v3.0.0-token2022-metadata-v1-20261012/metadata-preflight-failure-v1.json`
(SHA-256 `060a604d86edc2a229e7ca24b83967ada72ba44e345c59a74235fe61ad70e0d8`). No fetch, build,
ProgramTest, or production/economic qualification occurred. Scoped Ruff/Mypy and 33 focused tests
pass. Canonical source details are in `docs/brain-source-architecture.md`.

Next: prove prospective collection on Kingston through a scheduled anchor/frame, restart recovery,
and independent replay; then continue candidate-position witness, native settlement, and `buyExact25`
qualification. Keep c4 and `external-v2-20261012T0600Z` terminal. No backfill, ambiguous retry,
protected sample, or Cortex.
