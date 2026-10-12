# Current Project State

**As of:** 2026-10-12 06:43Z. Checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, synchronized with `origin/research/active`; `main` remains at
`2ce3cdaff63f0e0b78dcab8e78886ae405085432`. Work is continuing across Senses horizons. Production,
champion, protected evaluation and trading remain untouched; no signing, submission or Cortex work.

## Live state

- Kingston `/Volumes/Kingston/BrainData` matches root ID
  `9428695af6d4f46c08e1a444cdc66de8`, UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`; latest
  checked health is `ready` with 220,613,636,096 bytes free. c4 is terminal; no backfill.
- `external-v2-20261012T0600Z` remains disabled and unloaded. Its 06:20Z callback exited 1 before
  any new durable intent/reservation. Four buckets remain sealed (103 requests); event tail and
  frontier `455944206` are unchanged, with no unresolved reservations. Status is
  `verified_prefix`, not STOP or successful lease completion. Windows from 06:20Z onward are
  unobserved/cancelled; no replay or dispatch. The outer schedule expires 07:03Z. Recheck after
  expiry before any other action. Receipt:
  `docs/evidence/brain-senses-external-v2-20261012T0600Z-callback-failure-unload.json`.
- Helius still exposes only configured-key status, not plan/quota/usage/charge without JWT. Alchemy
  read-only aggregate is 18,680 CU / $0.01 through 06:30Z with no account limit or workload
  attribution. Charge for the fixed lease remains `unknown_unverified`.

## Active horizons

- Added inactive `brain-senses-external-launch-failure-receipt-v1` descriptor-bound diagnostic
  writer with eight focused synthetic tests. It never serializes exception text, reads no journal,
  and performs no provider I/O. Forced detach proof is synthetic, not APFS qualification; a future
  launcher must supply journal certainty only after its own independent locked rereads. Frozen v2
  launch code and journal remain unchanged.
- Source closure and read-only Agave persistence/finality map are committed. Sol authorized exactly
  one guarded locked Cargo fetch in the fresh
  `/Volumes/Kingston/BrainData/qualifications/agave-v3.0.0-token2022-runtime-20261012/{source,cargo-home,target,tmp}`
  stage. Wait until 07:03Z expiry and independently check terminal status, no active process,
  disabled/unloaded label, source/tool/lock pins and stage emptiness. Immediately before fetch repeat
  `--check-only`; require at least 90 GiB free. Keep 30-minute, 8-GiB cache growth, 32-GiB stage,
  50-GiB floor and no-retry guards. Failure is terminal; after success only offline Cargo metadata
  is authorized. A build requires another Sol review.
- The next scientific dependency is a synthetic commit-to-root candidate-position witness. Continue
  independent economic qualification, lifecycle/modalities, causal population, recovery/storage,
  and unknown/anomaly qualification in rolling horizons. No decoder promotion, settlement,
  buyExact25, point-in-time admission or completeness is qualified.
