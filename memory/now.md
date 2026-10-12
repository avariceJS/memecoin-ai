# Current Project State

**As of:** 2026-10-12 07:27Z. Checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, synchronized with `origin/research/active`; `main` remains at
`2ce3cdaff63f0e0b78dcab8e78886ae405085432`. Work is continuing across Senses horizons. Production,
champion, protected evaluation and trading remain untouched; no signing, submission or Cortex work.

## Live state

- Kingston `/Volumes/Kingston/BrainData` matches root ID
  `9428695af6d4f46c08e1a444cdc66de8`, UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`; latest
  checked health is `ready` with 219,478,421,504 bytes free. c4 is terminal; no backfill.
- `external-v2-20261012T0600Z` is terminal, disabled and unloaded. Post-expiry status remains
  `verified_prefix`: bootstrap plus three frames sealed, 103 requests, tail
  `0ed5e952c199bb3f420eba1be72da813d7e876910ec10545a273b782e0058787`, frontier `455944206`,
  no unresolved reservations, no STOP and no successor eligibility. Nine frame windows are
  `expired_unobserved`; no replay/backfill. Receipts:
  `docs/evidence/brain-senses-external-v2-20261012T0600Z-callback-failure-unload.json` and
  `docs/evidence/brain-senses-external-v2-20261012T0600Z-final-expiry-status.json`.
- Helius still exposes only configured-key status, not plan/quota/usage/charge without JWT. Alchemy
  read-only aggregate is 18,680 CU / $0.01 through 06:30Z with no account limit or workload
  attribution. Charge for the fixed lease remains `unknown_unverified`.

## Active horizons

- Added inactive `brain-senses-external-launch-failure-receipt-v1` descriptor-bound diagnostic
  writer and a separately versioned inactive launch-v3 callback prototype. The callback dispatches
  once, unwinds locks before rereads/sink, binds the scheduled bucket to exact plan bytes and the
  captured UTC window, and accepts `verified_none` only for two matching snapshots with pinned
  source/predecessor identity and unchanged empty reservation/intent sets. The combined focused
  suite passes 19 tests. It has injected verifier/lock dependencies only; forced detach remains
  synthetic, not APFS qualification. No activation or provider path exists. Frozen v2 code and
  journal remain unchanged.
- Source closure and read-only Agave persistence/finality map are committed. The one Sol-authorized
  guarded locked Cargo fetch in the fresh
  `/Volumes/Kingston/BrainData/qualifications/agave-v3.0.0-token2022-runtime-20261012/{source,cargo-home,target,tmp}`
  stage stopped terminally with wrapper reason `cargo_fetch_created_build_output` after creation of
  `target/.rustc_info.json`. Source diff and lock pins match preflight; preserve the approximately
  546-MiB Cargo home and target marker. No retry, offline metadata, build or ProgramTest. Another
  fetch needs new Sol review. Receipt:
  `docs/evidence/agave-token2022-runtime-fetch-attempt-terminal-2026-10-12.json`.
- Sol-approved `brain-senses-external-partial-structural-queue-v2` is compiled and create-only
  published at Kingston
  `derived/partial-structural-queue-v2/external-v2-20261012T0600Z/partial-queue.json` (SHA-256
  `fd8a2ffb750d53eb877e1326c02419358c24870aeb7338ea444a121712f83b81`), with post-fsync sibling
  publication receipt. It keeps 12 frame rows: 3 sealed (48 blocks), 9 unobserved with null metrics;
  46,222 transactions, 4,865 structural references, 4,595 unresolved classes. Deterministic replay
  and repeated publication returned identical hashes. Full-seal v1 compilers are unchanged.
- Next horizon: qualify the inactive launch-v3 failure-recovery writer on disposable case-sensitive
  APFS with forced detach-at-write points, or advance another independent Senses engineering gate;
  never activate it without the source/journal/lease gates. In parallel, continue source-only
  qualification of candidate-position/finality, native settlement, lifecycle/modalities, causal
  population and unknown/anomaly handling. No decoder promotion, settlement, `buyExact25`,
  point-in-time admission or completeness is qualified. Preserve Agave Cargo artifacts; no retry.
