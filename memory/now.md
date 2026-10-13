# Current Project State

**As of:** 2026-10-13 02:16Z. Owner checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, HEAD `6a10fab` (also `origin/research/active`); `main` is `19c2aa3`. Preserve
unrelated dirty Agave/build-guard, Helius v5r2, account-feasibility, and Gecko-lane work. Production,
champion, protected samples, real trading, and Cortex remain untouched.

## Prospective acquisition

- Kingston `/Volumes/Kingston/BrainData` matches root ID
  `9428695af6d4f46c08e1a444cdc66de8`, UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`, device
  `16777242`/inode `400`; 202 GiB free, above the 50-GiB floor. Use descriptor-bound storage only.
- No market collector is loaded. Gecko root `6672937e8ee81c6074e99d7f5f8083c3` recorded 3 HTTP-200
  results / 2,531 bytes, all out-of-window, plus 3 explicit missed slots. Bootout/bootstrap recovery
  preserved 3 intents/results/confirmations and issued no retry. Its old source-bound LaunchAgent is
  unloaded and must not be reused after the health-counter correction.
- Uncommitted Gecko host fix recomputes exact usage under the lane lock and preserves the old health
  snapshot on census failure; standalone entrypoint now adds repo `src/`. Focused suite: 67 passed;
  Ruff/format/py_compile passed. Next: commit only Gecko host/test/docs/memory changes, prepare a new
  release after 02:18Z for a future bucket, install and qualify it. The 02:15Z slot is missed; no
  backfill. Recheck actual requests, no fallback, health counters, and restart recovery.
- Helius v5r2 implementation/verification remains offline and not activated. Sol's approved design
  binds the exact 35-attempt cutover and enforces shared rolling caps; Alchemy corroboration follows
  only after a verified Helius live frame. No Alchemy RPC has been made in the current lane.
- Gecko census bridge v3 is committed/pushed at `6a10fab`. Prior read-only replay: 21 roots, 32
  attempts, 24,441 response bytes, `provider_io=false`. Actual provider charges remain
  unknown/unverified.

## Senses gates

- c4 raw capability census covers 388 structural references: 342 native-close rows, 341 exact
  source-flow diagnostics, one unsupported. Native settlement/economics remain unqualified; closed
  token endpoints, program owners, historical Rent, and attributable destinations are missing.
  `buyExact25` is observed but undecoded for economics. Receipt:
  `docs/evidence/pumpswap-sell23-c4-raw-capability-census-2026-10-13.json`.
- Candidate-position qualification still needs attributable acquisition/disposition, residual
  balance, separate cost/proceeds, fee/rent treatment, and decision-time separation. The feasibility
  tool is offline-only; no account reads were made.
- Agave Seatbelt confinement v2 is NO-GO after forced stage-path replacement wrote to the underlay.
  Preserve the checkout/cache; do not run Cargo or ProgramTest on this owner Mac.
- Next horizons: restore corrected forward-only Gecko collection; finish Helius v5r2 runtime and
  activation gates; advance candidate-position/native settlement evidence. Do not turn unknown into
  zero, censored into exact, or disappearance into death. Senses is not yet at the Cortex boundary.
