# Current Project State

**As of:** 2026-10-12 21:49Z. Owner checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, HEAD `4c3078a` (also pushed to `main` and `origin`). Worktree contains user-owned
Agave/build-guard and Gecko v1 dirty files; preserve them and stage only task-owned paths. Production,
champion, protected samples, real trading, and Cortex remain untouched.

## Prospective acquisition

- Kingston `/Volumes/Kingston/BrainData` still matches root ID
  `9428695af6d4f46c08e1a444cdc66de8` and UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`; latest free
  space is about 202.4 GiB. Descriptor-bound storage only, 50-GiB floor, no internal fallback.
- Gecko capture-only v4 LaunchAgent remains loaded at 60-second ticks, no lease expiry. Two natural
  15-minute buckets independently replayed as 8 total HTTP 200 dispatches, 8 durable results and
  confirmations, no retries/unknowns, healthy prefix. Both had 0/4 eligible new-open pools; second
  returned 12 bars, all out-of-window. Receipts: `gecko-capture-only-v4-first-live-bucket-2026-10-12T2100Z.json`
  and `gecko-capture-only-v4-live-bucket-2026-10-12T2115Z.json`. Provider charge remains unknown.
  The second receipt's narrative claim reverses `0/4 eligible` and `12 excluded_out_of_window`; its
  original bytes are preserved and corrected by the indexed erratum. Latest descriptor-bound replay
  verifies an 11-intent timely prefix; one 21:30Z pool is a durable `missed_dispatch` skip, with no
  retry/backfill. Kingston currently has about 202.4 GiB free.
  Descriptor-bound parent inventory: `v3`, three `v3-pilots`, one `v4-pilot`, four `capture-only-v1`
  roots; `v4` and `capture-only-v2` are absent. Existing v4 budget code includes its v3 pilots but
  does not include capture-only roots; v5 dispatch needs a complete replay-verified inventory.
- Sol approved a separate v5 six-pool rotating scout: cycle 170 train pools; up to two incumbent
  retention slots for exactly four selected buckets when the prior bucket is received, durable, and
  eligible new-open; new candidates rank by availability then pinned hash. No retry/backfill or
  response-based replacement. Pure selector and hash-chained plan/cursor model are implemented,
  reviewed and synthetic-tested; gaps preserve the cursor and recovery returns only persisted plans.
  A create-only descriptor-bound journal with synthetic restart and forced atomic-rename failure
  tests is implemented; it has not initialized Kingston or run against a live collector. Next
  horizon: a pure shared-usage assessor now combines explicitly enumerated verified root snapshots,
  charging unresolved intents at maximum response size and checking prospective daily/30-day request
  and byte ceilings. Its Kingston version adapters and cutover reconciliation remain unimplemented;
  do these before any v5 pilot.
- Helius v5 structural collector and health monitor remain loaded. Shared Kingston lock can serialize
  ticks while provider I/O is pending; monitor latency, do not interrupt ambiguous requests.
  Alchemy aggregate charge last verified as $0.01 / 18,680 CU without limit/workload attribution;
  research-lane attribution and current charge remain unknown.

## Senses gates

- Native `sell23` settlement sufficiency adapter is synthetic-only and independent-replay-free; 12
  focused tests, Ruff, formatting, and Mypy pass. It does not qualify Rent, destination settlement,
  proceeds, wallet net, `buyExact25`, training, or economics. Do not open c3 capture bodies.
- Agave Seatbelt confinement v2 is NO-GO after a forced stage-path replacement wrote into the
  underlay. Preserve dirty Agave checkout/cache; do not run Cargo/Agave on this owner Mac.
- Senses stop condition is not met. Continue through consecutive engineering horizons; do not begin
  Cortex. Keep event, receipt, availability, and decision times separate and preserve immutable
  provenance/denominators.
