# Current Project State

**As of:** 2026-10-12 23:02Z. Owner checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, HEAD `381c34a` (also pushed to `main` and `origin`). Preserve unrelated dirty
Agave/build-guard and Gecko v1 files; stage only the task-owned v2 runtime, shared coordinator, and
usage-inventory paths. Production, champion, protected samples, real trading, and Cortex remain
untouched.

## Prospective acquisition

- Kingston `/Volumes/Kingston/BrainData` still matches root ID
  `9428695af6d4f46c08e1a444cdc66de8` and UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`; current
  free space is 202 GiB, above the 50-GiB floor. Descriptor-bound storage only; no internal fallback.
- The Gecko capture-only v4 LaunchAgent was loaded at 60-second intervals but repeatedly exited 2
  with its integrity latch set. It has now been booted out. Its immutable root has 18 intents,
  dispatches, durable results, and confirmations. Exact replay failure: one unreferenced clock
  `clocks/1791844200000000000-00.json` (SHA-256
  `e8bd01da09b3b0f999ca745769152cbef4356371b0cc646538a1458e98f6697d`) accompanies four durable
  missed-dispatch skips for that bucket and no intent or dispatch. The root remains untouched.
  Global same-provider census still fails closed until a narrow, hash-pinned usage-only reconciliation
  proves the source closure and independently replays all actual attempts. Do not add a generic clock
  closure bypass.
- Gecko v2 now has a separately versioned descriptor writer: lane lease then nonblocking root lock,
  exact prefix recheck, atomic create-only intent/result/confirmation, and embedded clock evidence.
  Forced detach-at-intent-write left the replacement mount untouched. Synthetic runtime, coordinator,
  verifier, usage-inventory, and assessment tests: 55 passed; scoped Ruff/Mypy and `make smoke` pass.
  No v2 provider runtime or LaunchAgent is active. Sol's next gates are narrow v4 usage reconciliation,
  bounded v2 scheduler/HTTP host, crash/race/recovery/pacing/cap/detach qualification, exact
  release/source/plist binding, then a distinct future-only provider pilot and LaunchAgent recovery.
  Capture may remain unqualified for source, training, and economics. Limits: 576/day, 17,280/rolling
  30d, 10 MiB/day, 300 MiB/rolling 30d, 2 GiB physical envelope, 16 KiB/response, 20s timeout,
  >=12s spacing and <=5/rolling minute; stop on uncertainty, caps, 429 cooldown, or storage/clock drift.
- Provider charge remains unknown/unverified. This does not block bounded capture. v4's prior pacing
  state does not include v2; v4 must stay unloaded through cutover and v2 activation.

## Senses gates

- Native `sell23` settlement sufficiency remains synthetic-only and independent-replay-free; it does
  not qualify Rent, destination settlement, proceeds, wallet net, `buyExact25`, training, or economics.
- Agave Seatbelt confinement v2 remains NO-GO after forced stage-path replacement wrote into the
  underlay. Preserve dirty Agave checkout/cache; do not run Cargo/Agave on this owner Mac.
- Senses stop condition is not met. Continue through consecutive engineering horizons; do not begin
  Cortex. Preserve event, receipt, availability, and decision times and immutable provenance.
