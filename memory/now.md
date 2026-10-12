# Current Project State

**As of:** 2026-10-12 05:50Z. Checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, at `9bc9b3bf17cec1c0ce201fb5803cee527a061230`; worktree clean and pushed to
`origin/research/active`. `main`/`origin/main` remain at the previously verified capability baseline
`2ce3cdaff63f0e0b78dcab8e78886ae405085432`; this source-map/evidence checkpoint is not a capability
milestone. Production, champion, protected evaluation, and real trading remain untouched; no signing,
submission, or Cortex work.

## Live state

- Kingston `/Volumes/Kingston/BrainData` matches root ID
  `9428695af6d4f46c08e1a444cdc66de8` and UUID
  `8f8f1081-9be4-4914-9111-75f1b90255bf`; latest free space is 220,789,817,344 bytes and health is
  `ready`, above the 50-GiB floor. Never fall back to internal storage or move frozen roots.
- c4 STOP remains terminal at frontier `455430160`; STOP hash
  `a7ac1fdd02615491fbea494d35f5bbcba8b03d5bf2a4d11f2516616b39f00098`, reservation reconciled,
  zero ambiguous reservations and zero post-STOP RPCs. The sealed-v1 predecessor frontier is
  `455802008`. The 02:30Z/04:30Z siblings are terminal and disabled; no backfill.
- The fixed `external-v2-20261012T0600Z` LaunchAgent is loaded idle for 06:00–07:03Z, path-only,
  calendar scheduled and not manually kicked. Latest read-only status: `verified_prefix`, 0 requests,
  reservations or intents, no STOP, no replay authority. Do not run Cargo while this lease is armed or
  running. After it expires, independently verify terminal completion and unload the label.
- Helius read-only account status confirms an API key but exposes no plan/quota/usage/charge without
  JWT; do not invoke signup/account mutation. Alchemy reports team aggregate 18,680 CU / $0.01 MTD,
  no limit or workload attribution; actual lease charge remains `unknown_unverified`.

## Current Senses horizon

- Fresh isolated Agave `v3.0.0` source closure pins observer v5 + corrected parser v3, 20 changed
  paths and 1,075 packages. Check-only passed with empty Cargo/target/tmp; no dependencies fetched.
- Sol approved exactly one bounded locked Cargo fetch only after the fixed lease is terminal,
  independently verified and unloaded. Immediately before it, rerun check-only and verify unchanged
  source/tool/lock/storage pins and at least 90 GiB free. Keep the 30-minute, 8-GiB cache growth,
  32-GiB stage allocation, 50-GiB floor/reserve, no-retry/process-group guards. A failed fetch is
  terminal; success permits offline metadata only. Build needs another Sol review.
- The read-only Agave persistence/finality seam map is committed in
  `docs/evidence/agave-v3-transaction-persistence-finality-source-map-2026-10-12.json`; its verifier
  passes 12 source files/38 anchors. It identifies missing unique joins across attempt, commit,
  absolute block index, frozen bank/fork and rooted block. After raw runtime qualification, next
  decision is a synthetic commit-to-root witness; ambiguous hash, fork or time remains unknown.
- Do not use c4/tail2 or protected bodies, old partial Cargo caches, dirty historical Agave roots,
  provider RPC, signing or production. No decoder promotion, settlement, buyExact25, PIT or economic
  admission is qualified. Continue Senses through economics, causal population, lifecycle/modalities,
  recovery, storage and unknown qualification; do not stop at this checkpoint.
