# Current Project State

**As of:** 2026-10-10 12:30 UTC. Recheck Git, LaunchAgents, UTC bucket and disk before acting.

## Boundaries

Continue Senses only. Production remains `/Users/root1/Desktop/memecoin-ai` and must not be changed.
No real trades/signing, paid upgrades, protected holdout access, pushes, backfills, or retries of
ambiguous requests. Keep event, availability, receipt and decision times distinct; missing,
unknown, censored, failed, stopped and unsupported states are not zero. See `AGENTS.md` and
`docs/INDEX.md`.

## Runtime and credentials

- Research checkout `/Users/root1/Developer/memecoin-research`, branch `research/active`, HEAD
  `3290956`; c4 bootstrap docs/receipt are the only uncommitted scope. Production is untouched.
- c3 is permanently terminal after the reserved 11:00Z `getSlot` failure; all 205 attempts count,
  including the uncertain charge. Trusted frontier is 455216025. No retry/backfill.
- c4 lease remains frozen: one anchor plus at most 24 future hourly frames, no successor. The
  scheduled 12:05Z bootstrap ran once: finalized anchor 455249112, frontier 455249113, explicit
  uncovered interval `[455216025,455249113)`, one request / 43 raw / 56 stored bytes, zero open
  reservations, no `getBlocks` or `getBlock`, c3 STOP unchanged. c4 LaunchAgent had one run, exit 0;
  next full frame is only the scheduled 13:05Z bucket. Do not manually trigger. Receipt:
  `docs/evidence/helius-inventory-v3-c4-bootstrap-2026-10-10.json`.
- c4 root: `work/brain-senses-live-helius-inventory-v3-20261010-4/`; about 73.10 GB free after
  bootstrap. Verify scheduled frame and its explicit gap/failure outcome, then leave later forward
  buckets autonomous under the frozen lease. No backfill or successor.
- Helius key remains in ignored mode-0600 `.env.local`, with no rotation as explicitly authorized.
  Exact-value scan found one expected checkout occurrence, zero in 5,130 Git objects, zero in
  8,786 selected local text/config/log files, and zero current process arguments. The earlier
  process-list disclosure remains in a prior agent transcript and is recorded in the scan receipt.
  c4 LaunchAgent carries paths only and has no credential environment entry; runtime loads the URL
  in-process. Do not echo the key. Receipt:
  `docs/evidence/helius-key-local-exposure-scan-2026-10-10.json`.
- MCP checks: Alchemy ping and official Solana docs catalog responded; Helius MCP account transport
  was unavailable in the prior check. No MCP check made an RPC request.

## Senses evidence

- Frozen sell23 ledger v2 replay across c3's six completed frames: 96 selected full blocks, 553
  successful eligible candidates (68 exact target-account ledgers, 485 censored partial), two
  failed layout refs separately counted, all 3,318 equations passed; no RPC. This is not a wallet-net
  price, completeness or admission claim. Receipt:
  `docs/evidence/pumpswap-sell23-c3-six-frame-ledger-replay-2026-10-10.json`.
- `buyExact25` latest source is official PumpSwap SDK 2.1.0. On the 192 exact 25-byte/three-tail
  refs in c3's first frame, SDK pool-v2 derivation matched 192/192 and cashback ATA matched 0/192.
  Combined prefix and fee-pair structure is supported for 190 refs (134 successful tx refs, 56
  failed attempt-only); two failed refs with transaction-wide writable prefix/fee account are
  quarantined. No settlement/economic conclusion. Receipt:
  `docs/evidence/pumpswap-buy-exact-v1-c3-pool-v2-prefix-qualification-2026-10-10.json`.
- Sol froze the exact role predicate and the offline classifier is frozen at HEAD `3290956`. Apply
  once only to the first complete c4 frame whose raw bodies were not previously examined for this
  predicate; otherwise label the result ordinary replication. It emits structural roles only and
  does not admit economic settlement or training.
- Token-2022, other buy layouts, fills, fee movement, native settlement and unknown roles remain
  quarantined. Pair64 remains sampled same-slot evidence, not global completeness.
- Alchemy Free/PAYG account capabilities are not a broad archive/completeness claim; PAYG endpoint
  is absent from research `.env.local`.

## Next horizon

1. Commit the reviewed c4 bootstrap receipt/docs separately from the committed c3 SDK-prefix
   qualification; preserve all immutable raw captures under ignored `work/`.
2. At/after 13:05Z inspect actual c4 scheduled execution, journal/request identities, structural
   checks, bytes/disk, STOP and no post-STOP traffic. Do not manually invoke or retry.
3. Apply the frozen classifier once only if the first c4 frame is complete and was not previously
   inspected for this predicate. Do not use missed buckets.
4. Keep c4 frozen through its finite run. After its terminal evidence, ask Sol for a separate
   sustainable prospective lease policy; no auto-renewal or controller change is currently
   authorized.
5. Continue engineering Senses gates during autonomous calendar accumulation. Do not start Cortex.
