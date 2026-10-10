# Current Project State

**As of:** 2026-10-10 12:38 UTC. Recheck Git, LaunchAgents, UTC bucket and disk before acting.

## Boundaries

Continue Senses only. Production remains `/Users/root1/Desktop/memecoin-ai` and must not be changed.
No real trades/signing, paid upgrades, protected holdout access, pushes, backfills, or retries of
ambiguous requests. Keep event, availability, receipt and decision times distinct; missing,
unknown, censored, failed, stopped and unsupported states are not zero. See `AGENTS.md` and
`docs/INDEX.md`.

## Runtime and credentials

- Research checkout `/Users/root1/Developer/memecoin-research`, branch `research/active`, HEAD
  `007e43a`; the current key rescan receipt and its canonical pointers are the only uncommitted
  scope. Production is untouched.
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
  12:30Z exact-value rescan found one expected private-config occurrence, zero matches in 5,165 Git
  objects, 10,308 selected saved text/config/log files, and current process command lines. The
  earlier process-list disclosure remains in the prior agent transcript. c4 LaunchAgent passes only
  paths and `BRAIN_ZSTD_EXECUTABLE`; RPC reads the endpoint in-process. Receipt:
  `docs/evidence/helius-key-local-exposure-rescan-2026-10-10.json`.
- MCP checks: Alchemy ping and Solana transaction-message docs search responded; Helius MCP
  documentation transport closed. No MCP call made a provider RPC request.

## Senses evidence

- Frozen sell23 ledger v2 replay across c3's six completed frames: 96 selected full blocks, 553
  successful eligible candidates (68 exact target-account ledgers, 485 censored partial), two
  failed layout refs separately counted, all 3,318 equations passed; no RPC. This is not a wallet-net
  price, completeness or admission claim. Receipt:
  `docs/evidence/pumpswap-sell23-c3-six-frame-ledger-replay-2026-10-10.json`.
- `buyExact25` source is official PumpSwap SDK 2.1.0. The v2 raw/report qualifier independently
  normalized 16 c3 full blocks / 16,668 transactions, found 192 matching references and exact
  raw/report identity and field equality (no normalization errors or set differences). The frozen
  pool-v2 predicate passes 190 (134 success references, 56 failed attempt-only); two failed attempts
  with transaction-wide writable accounts remain quarantined. No settlement/economic conclusion.
  Receipt: `docs/evidence/pumpswap-buy-exact-v1-c3-pool-v2-prefix-raw-reconciled-2026-10-10.json`.
- Sol froze the exact role predicate; the v2 population-binding classifier is frozen at HEAD
  `007e43a`. Apply once only to the first complete c4 frame whose raw bodies were not previously
  examined for this predicate; otherwise label as ordinary replication. It emits structural roles
  only and does not admit economic settlement or training.
- Token-2022, other buy layouts, fills, fee movement, native settlement and unknown roles remain
  quarantined. Pair64 remains sampled same-slot evidence, not global completeness.
- Alchemy Free/PAYG account capabilities are not a broad archive/completeness claim; PAYG endpoint
  is absent from research `.env.local`.

## Next horizon

1. Commit the current exact-key rescan receipt/docs; preserve raw captures under ignored `work/`.
2. At/after 13:05Z inspect actual c4 scheduled execution, journal/request identities, structural
   checks, bytes/disk, STOP and no post-STOP traffic. Do not manually invoke or retry.
3. Apply the frozen v2 classifier once only if the first c4 frame is complete and was not previously
   inspected for this predicate. Do not use missed buckets.
4. Keep c4 frozen through its finite run. After its terminal evidence, ask Sol for a separate
   sustainable prospective lease policy; no auto-renewal or controller change is currently
   authorized.
5. Continue engineering Senses gates during autonomous calendar accumulation. Do not start Cortex.
