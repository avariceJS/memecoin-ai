# Current Project State

**As of:** 2026-10-10 12:48 UTC. Verify live Git, LaunchAgents, campaign state and disk before acting.

## Boundaries and runtime

- Continue Senses only. Research checkout `/Users/root1/Developer/memecoin-research`, branch
  `research/active`, HEAD `b1ee9b3`; production `/Users/root1/Desktop/memecoin-ai` remains untouched. No trading,
  signing, paid upgrade, protected holdout access, push, backfill, or ambiguous-request retry.
- c3 is permanently terminal after its reserved 11:00Z `getSlot` transport failure (205 attempts,
  charge uncertain), frontier 455216025. No retry or backfill.
- c4 remains a frozen finite lease: one finalized `getSlot` anchor, then at most 24 future hourly
  attempts, no successor/auto-renewal. Its 12:05Z bootstrap recorded anchor 455249112, frontier
  455249113, gap `[455216025,455249113)`, one request (43 raw/56 stored bytes), no `getBlocks` or
  `getBlock`, zero open reservations; c3 STOP unchanged. LaunchAgent
  `ai.memecoin.brain.senses.helius-inventory-v3-20261010-4` is loaded, one run/exit 0, next scheduled
  bucket 13:05Z. Do not manually invoke. Bootstrap receipt:
  `docs/evidence/helius-inventory-v3-c4-bootstrap-2026-10-10.json`.
- c4 root: `work/brain-senses-live-helius-inventory-v3-20261010-4/`. Latest observed free space:
  71,388,340 KiB. Preserve gaps; reconcile each scheduled epoch and stop on lease failure.
- Helius key stays in ignored mode-0600 `.env.local`; user explicitly authorized continued use without
  rotation. Exact-value scan at 12:30Z found its expected sole private-config occurrence, no matches
  in 5,165 Git objects, 10,308 selected saved text/config/log files, or process command lines. The
  prior process-list disclosure remains in the agent transcript. c4 arguments contain only paths;
  RPC reads credentials in-process. Receipt:
  `docs/evidence/helius-key-local-exposure-rescan-2026-10-10.json`.
- MCP: Alchemy ping and official Solana docs search worked; Helius documentation transport closed.
  No MCP provider RPC was performed.

## Senses evidence

- Frozen sell23 ledger v2 replay on c3's six completed frames: 96 selected blocks, 553 successful
  eligible candidates (68 exact target-account ledgers, 485 censored partial), two failed layout refs
  separately counted, all 3,318 equations passed. No wallet-net price, completeness or admission.
- `buyExact25` uses pinned official PumpSwap SDK 2.1.0. The frozen v2 pool-v2 qualifier's first c3
  frame reconciled raw/report sets 192/192 across 16 blocks/16,668 transactions; 190 structural
  passes and two failed attempts quarantined for global writability. No settlement qualification.
- Frame-2 ordinary development replication reconciled raw/report 258/258 across 16 blocks/16,426
  transactions. Frozen predicate accepted 257 (225 successful references, 32 failed attempt-only)
  and quarantined one successful reference: fee wallet message key 7 is globally writable and is
  referenced by a sibling outer instruction at `[1]` and the PumpSwap CPI at `[1,0]`. Sibling
  semantics remain unknown; no economic settlement qualified. Receipt:
  `docs/evidence/pumpswap-buy-exact-v1-c3-frame2-pool-v2-prefix-raw-reconciled-2026-10-10.json`.
- Run the frozen classifier once only on the first complete c4 frame whose raw bodies were not
  examined for this predicate; otherwise label replication. Token-2022 effects, other layouts,
  fills, fee movement and native settlement remain quarantined. Pair64 is sampled same-slot evidence,
  not global completeness. Alchemy PAYG endpoint is absent from research `.env.local`.

## Next decision horizon

1. At/after 13:05Z check the actual scheduled c4 outcome, request journals, frame integrity, bytes,
   disk, STOP and any traffic after STOP. Do not manually trigger or replay.
2. Qualify the first c4 frame only under the frozen untouched-frame rule.
3. Leave c4 on its finite schedule. After terminal evidence, ask Sol to decide whether a new,
   sustainable prospective lease is justified. Do not install a successor or auto-renewal meanwhile.
4. Continue independent Senses engineering gates during calendar accumulation; do not start Cortex.
