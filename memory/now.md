# Current Project State

**As of:** 2026-10-12 03:44Z. Checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`; HEAD and published active are `ec15335` before the current atomic changes. `main` remains
`148ca165e1487a99c3ce6532dbc276c4922c31b6`. Worktree state is checked before handoff.
Production, champion, protected evaluation and real trading remain untouched. No signing/submission;
Cortex is out of scope.

## Live state

- Kingston `/Volumes/Kingston/BrainData` matches UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`
  and root ID `9428695af6d4f46c08e1a444cdc66de8`; owners enabled, health `ready`, 220,973,776,896
  bytes available (~206 GiB), above the 50-GiB floor. The 300-second health LaunchAgent is loaded,
  last exit 0. c4 remains terminal STOP `minimum_free_disk_floor_plus_frame_envelope`, frontier
  `455430160`, last frame reconciled. No backfill.
- The external-v2 lease is terminal `missed_bootstrap`, zero requests/reservations/intents/seals.
  The 03:20Z no-provider calendar callback ran once but exited 1 with `ModuleNotFoundError` before
  status; no provider I/O or status result. Its label is disabled/unloaded and its immutable failure
  receipt is `docs/evidence/brain-senses-external-timing-rehearsal-2026-10-12T0320Z.json`.
- Fix `78a7db6` adds trusted repository-root import binding and an isolated subprocess regression
  test (10 focused passes); `make smoke` passes. The distinct no-provider rehearsal at 03:40Z passed
  one scheduled callback, status-only preflight and +30s single-run gate with exit 0; no provider
  activity occurred and the service tree was unchanged. Receipt:
  `docs/evidence/brain-senses-external-timing-rehearsal-2026-10-12T0340Z.json`.
- Helius status/plan still have no JWT, so plan/usage/charge are unknown. Alchemy team aggregate is
  18,680 CU / $0.01 through 03:42Z, no limit or workload attribution. Fresh dual-HTTPS clock pair
  passed at 03:43Z. Receipt: `docs/evidence/senses-provider-account-readonly-2026-10-12T0342Z.json`.
- Structural unknown queue changes are under independent review. Synthetic tests pass (22); the
  c4 source remains readiness-only because it lacks transaction partitions, reference membership,
  and post-fsync availability. Current prototype also needs truthful post-fsync queue availability,
  representative signature/capture references, and consistent denominator units before publication.
- The 03:40Z timing rehearsal is disabled and unloaded after its successful single callback; the
  external-v2 missed-bootstrap lease remains disabled/terminal. No new provider lease is active.
- Agave fetch v2 remains terminal exit 101; partial cache preserved, do not reuse/clean/retry. No
  candidate-position raw decoder or economic qualification is established.

## Next horizons

After the scheduled status-only callback, refresh host clock/storage/source/account facts and ask
Sol for a separate prospective Helius lease review. Provider activation is not implied by rehearsal.
Then continue economic qualification, broad causal population/lifecycle/modalities, recovery/retention,
and unknown/anomaly automation; do not expand unconfirmed decoders. Commit/push completed work to
`origin/research/active`; sync `main` only at a verified capability milestone. Update canonical
English owners, preserve unknown/censored/gap distinctions, and never inspect protected samples.

Capture-root caution: never use wildcard reads over capture roots; one prior broad JSON match printed
raw response payloads. Use exact metadata paths only.
