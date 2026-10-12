# Current Project State

**As of:** 2026-10-12 15:15Z. Checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, at `2934618b5dda10d7236aa9e4720070b49416bb7f`, pushed to origin. `main` remains
`3dab3a36cccaa5913fdfa697b054a17181de3dd0`; do not advance until a live market acquisition
capability and independent recovery/replay are verified. Production, champion, protected data,
trading, and Cortex remain untouched. Preserve the dirty Agave build-guard files and drafts.

## Prospective acquisition

- Kingston `/Volumes/Kingston/BrainData` still matches root ID
  `9428695af6d4f46c08e1a444cdc66de8`, UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`; free space is
  212,276,452 KiB (about 202 GiB). The 50-GiB floor, descriptor-bound writes, and no-fallback rules
  remain active.
- `ai.memecoin.brain.senses.external-rolling-v4` is loaded under `gui/501`, scheduled every 60s,
  with no calendar expiry. The 14:00Z frame has 34 verified captures and one possibly-spent
  `response_invalid`; it did not seal and was not retried. The supervisor is still reporting
  `integrity_stop/response_invalid` (monitor seq 161). A future 16:00Z epoch currently has only
  `epoch_started`, no request intent. It must be terminally reconciled before any v5 cutover.
- Helius v5 adapter/verifier, pinned c1-c4 plus rolling-v4 usage builder, LaunchAgent definition,
  and forced detach-at-intent tests are committed at `2934618`; 66 focused v5/coordinator tests,
  Ruff/format, `py_compile`, targeted mypy, and plist lint pass. v5 is not activated and has made
  no provider request. Sol approved a separate no-network STOP/reconcile helper; implement and test
  it before touching v4 live state. Then build/publish the Kingston usage receipt, initialize the
  shared coordinator under the bound volume, and only then activate v5 at a fresh anchor.
- There is no prospective market OHLCV collector yet. Gecko v2 is offline; Alchemy same-frame
  corroboration is offline. Billing/charges remain unknown/unverified and are not blockers.

## Senses and next horizon

Shared capacity coordinator v2 and Helius v5 integration are committed and pushed. Next: complete
the one-shot v4 STOP/reconcile gate and v5 live forward-only anchor, then implement and qualify the
separately versioned Gecko runtime/LaunchAgent for four pinned train pools at 15-minute cadence.
Verify Kingston writes, actual provider traffic, recovery/replay, no fallback, finite rolling and
resident caps, and no calendar expiry before claiming autonomous market history. Do not backfill or
retry ambiguous requests. Continue candidate-position witness, native settlement, and `buyExact25`
qualification after collection activation; keep all outputs source/economic/PIT-ineligible until
their independent gates pass. No protected samples, production changes, or Cortex.

The separately versioned Agave metadata wrapper remains a failed offline preflight only; no Cargo
fetch/build or production/economic qualification occurred. Its read-only receipt and limitation are
recorded in `docs/brain-source-architecture.md`.
