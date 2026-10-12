# Current Project State

**As of:** 2026-10-12 06:31Z. Checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, last commit `abbdcf7` pushed to `origin/research/active`; current receipt and
diagnostic capability updates are in progress. `main` remains at capability baseline
`2ce3cdaff63f0e0b78dcab8e78886ae405085432`.
Production, champion, protected evaluation and trading remain untouched; no signing, submission or
Cortex work.

## Live state

- Kingston `/Volumes/Kingston/BrainData` remains bound to root ID
  `9428695af6d4f46c08e1a444cdc66de8`, UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`; latest
  free-space reading was 220,789,817,344 bytes and health `ready`. c4 stays terminal; no backfill.
- The fixed `external-v2-20261012T0600Z` LaunchAgent was disabled and unloaded at 06:21:54Z after
  its scheduled 06:20Z callback exited 1 with no durable intent/reservation and no recoverable
  exception. No kickstart/replay occurred. Four buckets remain sealed (103 requests); the unchanged
  journal tail ends at frontier `455944206`. At 06:23:23Z status classified 06:20Z as
  `expired_unobserved`; at 06:28Z it also classified the cancelled 06:25Z window that way. The four
  sealed buckets account for 144,392,483 measured raw/stored bytes against the 3,104,000,000-byte
  lease cap. Later buckets remain cancelled/unstarted. This is incomplete, not a cleanly completed
  lease, STOP, provider error, or zero observation. Its outer schedule expires at 07:03Z;
  recheck terminal status then. Receipt: `docs/evidence/brain-senses-external-v2-20261012T0600Z-callback-failure-unload.json`.
- Helius still exposes only “API key configured,” not plan/quota/usage/charge without JWT. Alchemy's
  read-only team aggregate is 18,680 CU / $0.01 across MTD/7-day/30-day through 06:30Z; no account
  limit or workload attribution, and hourly series for Oct 12 is empty. Lease charge remains
  `unknown_unverified`; signup/account mutation was not invoked.

## Current Senses horizon

- Source closure and read-only Agave transaction-persistence/finality map are committed; they make no
  runtime/finality/economic claim. The next dependent horizon is synthetic commit-to-root candidate-
  position witness after raw runtime qualification.
- Sol authorized exactly one locked Cargo fetch in the fresh
  `/Volumes/Kingston/BrainData/qualifications/agave-v3.0.0-token2022-runtime-20261012/{source,cargo-home,target,tmp}`
  stage. Do not start it until the fixed lease expires, completion is independently checked, and the
  label is disabled/unloaded. Immediately before fetch repeat check-only and exact source/tool/lock/
  storage pins, require empty Cargo home/target/tmp and at least 90 GiB free. Keep the 30-minute,
  8-GiB cache growth, 32-GiB stage allocation, 50-GiB floor/reserve and no-retry guards. Failure is
  terminal; success permits offline metadata only. A build needs another Sol review.
- Continue Senses through independent economic qualification, causal population, lifecycle/modalities,
  recovery, storage and unknown/anomaly qualification. No decoder promotion, settlement, buyExact25,
  PIT or economic admission is qualified.
