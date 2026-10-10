# Current Project State

**As of:** 2026-10-10 13:37 UTC. Verify live Git, LaunchAgents, campaign state and disk before acting.

## Boundaries and runtime

- Continue Senses only. Research checkout `/Users/root1/Developer/memecoin-research`, branch
  `research/active`; current HEAD was `dfcee6d` at the 13:28Z check. Production
  `/Users/root1/Desktop/memecoin-ai` remains untouched. No trading,
  signing, paid upgrade, protected holdout access, push, backfill, or ambiguous-request retry.
- c3 is permanently terminal after its reserved 11:00Z `getSlot` transport failure (205 attempts,
  charge uncertain), frontier 455216025. No retry or backfill.
- c4 remains a frozen finite lease: one finalized `getSlot` anchor, then at most 24 future hourly
  attempts, no successor/auto-renewal. Its 12:05Z bootstrap recorded anchor 455249112, frontier
  455249113, gap `[455216025,455249113)`, one request (43 raw/56 stored bytes), no `getBlocks` or
  `getBlock`, zero open reservations; c3 STOP unchanged. Its scheduled 13:05Z frame then completed
  34/34 requests and 16/16 selected block/signature checks over `[455249113,455265709)`, returning
  all 16,596 provider-reported produced slots. It used 58,434,290 raw/13,182,819 stored bytes in
  91.38 s. The c4 LaunchAgent is loaded and idle for its next 14:05Z bucket; one of 24 frame attempts
  is complete, 23 remain, campaign STOP is absent, and failures are zero. Do not manually invoke.
  Receipts:
  `docs/evidence/helius-inventory-v3-c4-bootstrap-2026-10-10.json`.
  `docs/evidence/helius-inventory-v3-c4-first-frame-2026-10-10.json`.
- c4 root: `work/brain-senses-live-helius-inventory-v3-20261010-4/`. Latest observed free space:
  about 73.06 GB. Preserve gaps; reconcile each scheduled epoch and stop on lease failure. The
  first-frame one-hour projection is 816 reservations, 1.40 GB raw and 0.316 GB stored per day;
  credit billing units remain unverified.
- Helius key remains in ignored mode-0600 `.env.local` and the two mode-0600 MCP configs; user
  authorized no-rotation continuation. At 13:28Z exact-value scans found zero in 5,238 Git objects,
  249,759 accessible saved text/runtime/evidence files, process arguments, and 1,285,040,709 bytes
  of eight-hour Unified Log output. Three earlier Cursor-transcript copies were redacted and
  revalidated; the historical process-list disclosure remains in the agent transcript. The c4
  LaunchAgent arguments contain only paths and RPC reads credentials in-process. Receipt:
  `docs/evidence/helius-key-local-exposure-rescan-post-redaction-2026-10-10.json`.
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
- The frozen predicate was applied once to c4's first eligible untouched frame: raw/report candidate
  sets match 171/171 across 16 blocks/17,607 transactions; all 171 structural passes comprise 141
  successful references and 30 failed attempt-only references. Zero settlements qualified. Census:
  `docs/evidence/pumpswap-buy-exact-v1-c4-first-frame-census-2026-10-10.json`; result:
  `docs/evidence/pumpswap-buy-exact-v1-c4-first-frame-pool-v2-prefix-raw-reconciled-2026-10-10.json`.
- Sol's bounded c4 success trace is complete: 141/141 frozen successful references replayed from 16
  receipt-hash-verified full blocks. It records event bytes, ordered target-account touches, token
  operands, and transaction-boundary endpoints/deltas. 140 Token-2022 economics remain prohibited;
  the one dual-legacy ledger gate stayed closed. Receipt:
  `docs/evidence/pumpswap-buy-exact-v1-c4-success-trace-2026-10-10.json.zst` (5,431,224 raw JSON /
  209,052 compressed bytes). Focused tests 4 passed and Ruff passed. `make check` currently fails on
  43 pre-existing Ruff E501 findings in existing qualifier tools; new files were clean.
- The offline state-bracketing feasibility receipt covers all 140 Token-2022-base refs and all 16
  full blocks/17,607 transactions. Counts: 137 exact role-matching descendant events, 3 absent or
  unqualified; 139 rows have missing/partial token endpoints; 140 have outside-subtree target
  touches; 5,421 other-transaction writable-potential occurrences. Ninety-one have no other-transaction
  writable potential on either mint, but zero pass a mint-only bank-state bracket; zero pass the
  full-account bracket. No acquisition control or state RPC. Receipt:
  `docs/evidence/pumpswap-buy-exact-v1-c4-state-bracketing-feasibility-2026-10-10.json`.
- Focused state-bracket tests: `4 passed`; Ruff clean for new tool/test. The manifest records raw
  state/TLV, fee epoch, withheld amount, hook/confidential, native and lifecycle requirements while
  qualifying none of their economic effects.
- Exhaustive focused pytest coverage checks the frozen qualifier's message-header privilege
  partitions through 12 static keys, loaded address boundaries, and fail-closed invalid cases;
  `6 passed`. No Hypothesis dependency was added.
- Later c4 frames are ordinary frozen-predicate replication only; no tuning or independent audit.
  Token-2022 effects, other layouts,
  fills, fee movement and native settlement remain quarantined. Pair64 is sampled same-slot evidence,
  not global completeness. Alchemy PAYG endpoint is absent from research `.env.local`.

## Next decision horizon

1. At/after each c4 scheduled bucket inspect actual execution, request journals, frame integrity,
   bytes, disk, STOP and any traffic after epoch STOP. Do not manually trigger or replay.
2. Keep later c4 use as ordinary replication; do not tune the frozen classifier on these frames.
3. Keep c4 on its loaded calendar schedule; next bucket is 14:05Z. Do not manually invoke, backfill,
   or retry. Preserve c4's finite lease and inspect the actual launchd/journal/capture/STOP state
   after each scheduled frame.
4. Continue with Sol's offline state-source decision after this no-row feasibility result. Keep c4
   running its unchanged finite scheduled lease and inspect every scheduled epoch; no successor or
   auto-renewal until its finite terminal and a separate Sol policy. Do not start Cortex.
