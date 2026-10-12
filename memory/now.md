# Current Project State

**As of:** 2026-10-12 18:08Z. Checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, HEAD `a7e95eadacb8715c1e2712192ad1dd881eeb061e`, pushed to origin. `main` remains
`3dab3a36cccaa5913fdfa697b054a17181de3dd0`. Worktree includes in-progress Gecko v4 release-gate,
candidate-position envelope v2, and user-owned Agave/build-guard work; stage only the intended Senses
files. Production, champion, protected samples, trading, and Cortex remain untouched.

## Prospective acquisition

- Kingston `/Volumes/Kingston/BrainData` matches root ID `9428695af6d4f46c08e1a444cdc66de8` and
  UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`; about 202 GiB is free. Keep the 50-GiB floor,
  descriptor-bound writes, and no-fallback rule.
- `ai.memecoin.brain.senses.external-rolling-v5` is loaded in `gui/501`, on a 60-second cadence,
  `RunAtLoad`, without calendar expiry. Its latest verified anchor contains one finalized
  `getSlot` capture and coordinator linkage; it proves structural acquisition only, not OHLCV.
- Gecko's 17:15Z pilot and later 17:45Z attempt are terminal. The latter was sealed
  diagnostic-only before provider I/O because its pinned plist lacked the required pilot ID.
  Runtime/plist/release-builder changes now implement a v4 two-stage preflight → status-only
  LaunchAgent callback → activation gate. Focused writer-detach, recovery, runtime, release, and
  candidate-position tests pass (89 total). Gecko LaunchAgent is not loaded. Freeze/commit the
  implementation before compiling a fresh pilot under static ID
  `9c90e69cf8104df4acd532a3f2a70a31`; only use a new prospective bucket, then replay, qualify
  preflight, load for callback, verify callback, activate, and verify persistent collection.
- No Alchemy research PAYG endpoint is configured. Account-level Helius billing remains
  unknown/unverified and is not a blocker. Stop on provider quota/error, ambiguous request, storage,
  clock, provenance, or recovery-integrity faults.

## Senses and next work

Candidate-position witness-envelope v2 supports only a Sol-approved
`synthetic_unverified` crosswalk hypothesis; source/runtime/economic/point-in-time claims remain
false. Continue independent economic qualification, native settlement and `buyExact25`, broad
causal population/lifecycle/modality coverage, and automated unknown/anomaly qualification. Keep
event, availability, receipt and decision times distinct. No protected samples, production changes,
real trading, transaction signing/submission, backfill, or Cortex.
