# Current Project State

**As of:** 2026-10-12 07:59Z. Checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`; pushed baseline `622f1e9449b0da0c66da60bbfd14d050a7c0b6ea`.
`main` remains `2ce3cdaff63f0e0b78dcab8e78886ae405085432`. Current horizon is locally uncommitted.
Production, champion, protected evaluation, trading and Cortex remain untouched.

## Live boundaries

- Kingston `/Volumes/Kingston/BrainData`: root ID
  `9428695af6d4f46c08e1a444cdc66de8`, UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`;
  latest checked health was ready with 219,478,421,504 bytes free. Use only the bound storage
  contract; no internal-SSD fallback.
- c4 and `external-v2-20261012T0600Z` are terminal. Three prospective frames plus bootstrap are
  sealed; nine windows remain expired-unobserved. No backfill, ambiguous replay, unresolved
  reservation, STOP, or successor eligibility. Expiry receipt:
  `docs/evidence/brain-senses-external-v2-20261012T0600Z-final-expiry-status.json`.
- Helius account plan/usage/charge remains unavailable; Alchemy read-only aggregate is not workload
  attribution. Record actual Helius charge as unknown/unverified. User authorizes current Helius key
  without rotation and active use of allowed free quotas; no new material paid campaign is planned.
- The one Sol-authorized Agave Cargo fetch stopped terminally after creating
  `target/.rustc_info.json`. Preserve the staged Cargo home/target; no retry, metadata, build or
  ProgramTest without another Sol review.
- Current read-only host check: Kingston identity still matches the bound root/UUID and health is
  `ready`, with 219,429,179,392 bytes available. The research-storage health LaunchAgent is loaded
  but idle. `launchctl list` showed no Brain/Senses acquisition agents; the 06:00Z external-v2
  receipt retains 103 requests, four seals, frontier 455944206, zero unresolved buckets, STOP=false,
  no replay/successor eligibility, and disabled/unloaded state. The c4 terminal receipt remains the
  canonical prior qualification; do not run the default qualifier against checkout `work/` paths.

## Recent Senses evidence

- Pushed milestone `00e7eee`: fixed-service partial-prefix structural queue v2; three sealed frames
  scanned, nine unobserved rows retain null metrics. Deterministic create-only Kingston publication
  and replay passed. Full-seal v1 is unchanged.
- Current uncommitted horizon adds source-pinned synthetic multi-record attempt→commit→block→frozen
  bank→selected-root verification. Batch and absolute positions stay distinct; failed/FeesOnly
  effects are typed; duplicate/fork/time ambiguities reject. Nineteen focused tests, Ruff, Mypy and
  smoke pass. `make check` still stops at 107 pre-existing Ruff findings outside these new files.
  Receipt: `docs/evidence/agave-commit-to-root-synthetic-join-v1-2026-10-12.json`.
- Current uncommitted horizon adds a descriptor-bound launch-v3 journal snapshot adapter that
  delegates journal semantics to v2, binds the plan/source/predecessor/window, and rejects torn,
  stale, or changed two-read snapshots. Nine focused tests, Ruff and focused Mypy pass. A two-image
  case-sensitive APFS run forced detach before failure-receipt `mkdir` and `create`; both failed
  closed with no receipt on remount and no underlay/fallback artifact. Disposable images used
  OS-noowners and zero-capacity thresholds, so this is detach evidence only. Receipt:
  `docs/evidence/brain-senses-launch-v3-failure-receipt-detach-qualification-v1-2026-10-12.json`.
- This is synthetic consistency only. Runtime law, source qualification, candidate-position
  admission, settlement, buyExact25 and PIT remain false. No real instrumentation or build is
  authorized by this receipt.

## Next horizon

Finish focused launch-v3/receipt regression tests and smoke, record the journal/detach result in the
canonical source and evidence index, then commit/push this complete offline failure-path horizon.
Replan with Sol across independent economic qualification, lifecycle/modalities, unknown/anomaly
handling, and recovery/storage. Any real Agave integration/build requires a fresh Sol decision.
Keep working past individual horizons; stop only at the overall Senses engineering-complete boundary
or a real external/system/user blocker.
