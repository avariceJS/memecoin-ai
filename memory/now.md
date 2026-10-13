# Current Project State

**As of:** 2026-10-13 01:55Z. Owner checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, HEAD `14cc5b9` (also `origin/research/active`). `main` is behind. Preserve dirty
Agave/build-guard and Gecko-lane files; do not stage them with unrelated work. Production, champion,
protected samples, real trading, and Cortex remain untouched.

## Prospective acquisition

- Kingston `/Volumes/Kingston/BrainData` matches root ID
  `9428695af6d4f46c08e1a444cdc66de8`, UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`, device
  `16777242`/inode `400`; 202 GiB free, above the 50-GiB floor. Use descriptor-bound storage only.
- No market collector is currently loaded. Helius v5 remains unloaded after two `response_invalid`
  epochs (one possibly-spent request each) and an invalid open 23:00Z root with missed windows.
  Sol approved a distinct v5r2 cutover; its implementation/qualification is pending.
- Gecko release `00263d9787393fbc4d0fa3db8a276d7c` remains immutable, plan-only, and unloaded after
  its missed window and `a078_receipt_census_mismatch`; no backfill. The versioned bridge now pins
  the exact old receipt, independently replays current roots and paired health sidecars, and checks
  the full census again after reconciliation. Focused tests pass. Read-only descriptor-bound live
  inventory passes: 21 roots, 32 verified attempts, 24,441 response bytes, `provider_io=false`.
  Next: commit/push the bridge, prepare a distinct future-only release, qualify its LaunchAgent and
  first natural bucket, then verify durable data and restart recovery. No market collector is loaded.
- The old a078 root's immutable terminal receipt replays four durable requests/3,041 bytes and leaves
  two no-intent/no-skip slots unknown. Read-only Helius account evidence says the key is configured,
  but usage/plan are unavailable without JWT. Alchemy usage is 18,680 CU/$0.01 month-to-date through
  2026-10-13 01:37Z, unchanged from the previous reading; its usage summary returned no limit.

## Senses gates

- The c4 raw capability census closes 388 structural references: 342 native-close rows, 341 exact
  source-flow diagnostics and one unsupported touch. Native settlement/economics remain unqualified;
  closed token endpoints, program owners, historical Rent, and attributable destinations are missing.
  `buyExact25` remains observed but undecoded for economics. Receipt:
  `docs/evidence/pumpswap-sell23-c4-raw-capability-census-2026-10-13.json`.
- Candidate-position qualification still requires attributable acquisition/disposition, residual
  balance, separate cost/proceeds, fee/rent treatment, and decision-time separation.
- Agave Seatbelt confinement v2 is NO-GO after forced stage-path replacement wrote into the
  underlay. Preserve the checkout/cache; do not run Cargo or ProgramTest on this owner Mac.
- Helius v5r2 has an offline-only preflight implementation and tests, including all 35 old attempts
  (33 captured, 2 possibly spent) and a 2026-10-13 19:00Z earliest fresh anchor. It has no active
  runtime/receipt persistence or LaunchAgent qualification. Candidate-position feasibility contract
  and tests are also offline only; no account reads were made.
- Next horizons: restore forward-only market collection through the verified Gecko bridge; finish
  Helius v5r2 runtime qualification; advance candidate-position/native settlement evidence. Do not
  promote unknowns to zero or begin Cortex before the sensing bottlenecks move.
