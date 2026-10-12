# Current Project State

**As of:** 2026-10-12 14:51Z. Checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, at `b35cf789fca3664278d93f02cc9db4ed0b777666`, pushed to origin before this
worktree update. `main` remains
`3dab3a36cccaa5913fdfa697b054a17181de3dd0`; do not advance until a complete live acquisition
capability and independent recovery/replay are verified. Production, champion, protected data,
trading, and Cortex are untouched. The local worktree has unrelated in-progress Agave runner files
and tests; preserve them.

## Prospective acquisition

- `ai.memecoin.brain.senses.external-rolling-v4` is loaded under `gui/501`, with 60-second ticks,
  `RunAtLoad`, and no calendar expiry. The 13:00Z anchor and its seal replay independently. The
  14:00Z inventory frame recorded 35 intents, 34 verified captures, and one possibly-spent
  `response_invalid`; it did not seal. Its epoch and allocation manifest verify, with no retry.
  The supervisor reconciled it and entered a one-hour provider-error cooldown at 14:06:48Z; the
  service remains loaded and should resume only on a future hourly anchor after cooldown. This is
  structural sampling, not market-price history.
- Kingston `/Volumes/Kingston/BrainData` matches root ID
  `9428695af6d4f46c08e1a444cdc66de8`, UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`; latest
  monitor reported 217,373,667,328 bytes available. The 150-GB lifetime cap, 50-GiB floor, 8-GiB
  reserve, descriptor-bound writes, and no-delete/no-fallback rules remain active.
- No market OHLCV service is live. v4 remains loaded on 60-second ticks but its latest monitor is
  still in provider-error cooldown; it is structural Helius collection only. Kingston currently has
  about 202 GiB free and the configured UUID/root identity still matches. Shared descriptor-bound
  capacity coordinator v2 is now implemented and passes 25 focused crash/recovery, lane-lock,
  capacity-boundary and multiprocess tests, plus Ruff and mypy. Its source and tests are not yet
  committed. Helius v5 integration is active in the worktree; it must bind verified v4 rolling usage
  before dispatch and pass independent recovery/replay. Gecko v2 is an offline forward-only contract;
  no Gecko/Alchemy market collector is live. Meter visibility and qualification perfection are not
  blockers; actual provider charges remain unknown/unverified.

## Senses state and next horizon

The recent separately versioned Agave wrapper and focused tests are pushed. Sol authorized exactly
one offline metadata invocation. Cargo returned zero, but the wrapper rejected colliding redacted
local package IDs; its stdout/graph digest was not persisted. No Cargo retry is authorized. The
read-only receipt is on Kingston at
`qualifications/agave-v3.0.0-token2022-metadata-v1-20261012/metadata-preflight-failure-v1.json`
(SHA-256 `060a604d86edc2a229e7ca24b83967ada72ba44e345c59a74235fe61ad70e0d8`). No fetch, build,
ProgramTest, or production/economic qualification occurred. Scoped Ruff/Mypy and 33 focused tests
pass. Canonical source details are in `docs/brain-source-architecture.md`.

Next: commit the verified shared coordinator; finish Helius v5 integration with metadata-only v4
rolling-usage carry-forward; then qualify a new future-only anchor, Kingston writes, provider traffic,
restart recovery, and independent replay before enabling Gecko's next future bucket. Keep c4 and
`external-v2-20261012T0600Z` terminal. No backfill, ambiguous retry, protected sample, or Cortex.
Continue candidate-position witness, native settlement, and `buyExact25` qualification in
independent work while acquisition integration proceeds.
