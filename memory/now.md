# Current Project State

**As of:** 2026-10-13 02:20Z. Owner checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, HEAD `65f91f6` (also `origin/research/active`); `main` is `19c2aa3`. Preserve
dirty Agave/build-guard, Helius v5r2, account-feasibility, and Gecko-lane scopes. Production,
champion, protected samples, real trading, and Cortex remain untouched.

## Prospective acquisition

- Kingston `/Volumes/Kingston/BrainData` matches root ID
  `9428695af6d4f46c08e1a444cdc66de8`, UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`, device
  `16777242`/inode `400`; latest `df` showed 202 GiB free, above the 50-GiB floor. Descriptor-bound
  storage only.
- Gecko collector release `060bed015daf4918b061d4eea72e2d65` is loaded under a source-bound LaunchAgent
  with 12-second callbacks and no calendar expiry. It targets the first future bucket at 02:30Z; its
  request window is 02:31–02:35Z. No provider request has yet been made by this release. Health
  replay shows root/storage identity valid, `stop_latched=false`, 7 daily / 35 rolling attempts from
  global prior usage, and 0 new intents. Verify natural bucket, provider results, gaps, and recovery;
  never backfill missed windows. Old root `6672937e8ee81c6074e99d7f5f8083c3` stays unloaded and
  unchanged: 3 responses / 2,531 bytes all out-of-window, 3 explicit missed slots, exact 3/3/3
  recovery without retry.
- Host health fix and standalone `src/` bootstrap are committed/pushed at `65f91f6`. Focused suite
  passed 67; Ruff/format/py_compile passed. Global live inventory previously replayed 21 roots,
  32 attempts and 24,441 bytes; actual provider charges remain unknown/unverified.
- Helius v5r2 remains offline-only. Sol's exact 35-attempt cutover/shared rolling-cap design is
  implemented through the 20-test boundary, including descriptor-read activation-cap source checks.
  Multi-epoch continuation/control recovery and host/live gates remain open. No Alchemy RPC has run;
  corroboration follows a verified Helius live frame.

## Senses gates

- c4 raw capability census covers 388 structural refs: 342 native-close rows, 341 source-flow
  diagnostics, one unsupported touch. Close destinations are unattributed; closed-token endpoints,
  owners, historical Rent and instruction-position state are missing. `buyExact25` remains raw wire
  only. Receipt: `docs/evidence/pumpswap-sell23-c4-raw-capability-census-2026-10-13.json`.
- Sol's prospective contract separates `raw_candidate_witness_acquired` from
  `economic_settlement_qualified=false`; ordinary block/slot account RPC is diagnostic only. A
  witness requires exact ordered instruction-position state, Clock/Rent, commit/finality, source
  bindings and distinct event/receipt/availability/decision times. Failed/partial/missing/unsupported/
  fork-ambiguous remain explicit. `sell23` and `buyExact25` require separate class gates.
- Agave Seatbelt confinement v2 is NO-GO after forced stage-path replacement wrote to the underlay.
  Preserve the checkout/cache; do not run Cargo or ProgramTest on this owner Mac.
- Next horizons: verify first natural Gecko bucket and recovery; complete offline Helius v5r2
  multi-epoch replay then qualify activation; operationalize prospective raw-witness evidence without
  claiming economics. Never turn unknown into zero, censored into exact, or disappearance into death.
  Senses is not at the Cortex boundary.
