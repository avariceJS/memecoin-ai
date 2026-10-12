# Current Project State

**As of:** 2026-10-12 14:08Z. Checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, at `78fed32c70ad63fb054d0d3f357b3c5d267fc9ef`, pushed to origin. `main` remains
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
- No Alchemy or GeckoTerminal service is live. Alchemy v2 offline same-frame contracts are committed
  and pushed; GeckoTerminal v1 policy and frozen train enrollment are under implementation. The
  shared descriptor-bound capacity coordinator is the real integrity gate before either lane can
  activate. Do not treat meter visibility or quota conservation as a blocker. Actual provider
  charges remain unknown/unverified.

## Senses state and next horizon

The recent separately versioned Agave wrapper and focused tests are pushed. Sol authorized exactly
one offline metadata invocation. Cargo returned zero, but the wrapper rejected colliding redacted
local package IDs; its stdout/graph digest was not persisted. No Cargo retry is authorized. The
read-only receipt is on Kingston at
`qualifications/agave-v3.0.0-token2022-metadata-v1-20261012/metadata-preflight-failure-v1.json`
(SHA-256 `060a604d86edc2a229e7ca24b83967ada72ba44e345c59a74235fe61ad70e0d8`). No fetch, build,
ProgramTest, or production/economic qualification occurred. Scoped Ruff/Mypy and 33 focused tests
pass. Canonical source details are in `docs/brain-source-architecture.md`.

Next: complete review and fault tests for the shared capacity coordinator, integrate its Helius
lock semantics while preserving the terminal v4 history, then run Gecko's first future-only
scheduled bucket and verify provider traffic, Kingston writes, restart recovery, and independent
replay. Keep c4 and `external-v2-20261012T0600Z` terminal. No backfill, ambiguous retry, protected
sample, or Cortex. Continue candidate-position witness, native settlement, and `buyExact25`
qualification in independent work after acquisition integrity is stable.
