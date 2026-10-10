# Current Project State

**As of:** 2026-10-10 11:44 UTC. Recheck Git, LaunchAgents, time and disk before acting.

## Boundaries

Continue Senses only. Do not change production, trade/sign, touch protected holdouts, push remotes,
make paid upgrades, backfill missed prospective slots, or retry ambiguous requests. Keep event,
receipt, availability and decision times distinct; unknown, missing, censored, failed, stopped and
unsupported states are never zero. See `AGENTS.md`, `docs/INDEX.md` and canonical source/evaluation
owners.

## Runtime

- Research checkout `/Users/root1/Developer/memecoin-research`, branch `research/active`, HEAD
  `3efef49`. Worktree was clean before the current handoff edit. Production
  `/Users/root1/Desktop/memecoin-ai` remains untouched. Raw captures are under ignored `work/`.
- c3 is permanently terminal after its reserved 11:00Z `getSlot` failed; count 205 attempts including
  the uncertain charge. Frontier remains 455216025. Six prior complete frames are valid only in their
  captured provider-reported selected-block scope. Never retry/backfill. Hash-bound STOP is in
  `docs/evidence/helius-inventory-v3-c3-abnormal-stop-2026-10-10.json`.
- Sol authorized c4 as one fresh future finalized `getSlot`, then at most 24 future hourly frames;
  817 total requests, 4 GB raw, 1 GB stored, 20,000 slots/34 requests per frame, 50-GiB floor plus
  peak margin; lineage c1/c2/c3 = 0/1/205. No successor. Missed/ambiguous bootstrap or frame is
  terminal, with no replay. c4 LaunchAgent is loaded idle for its 12:05Z bootstrap; at 11:44Z it had
  zero runs/reservations/frame captures. Never manually trigger it. Check scheduled outcome and gap,
  then let only future buckets continue under the sealed lease. c4 receipt:
  `docs/evidence/helius-inventory-v3-c4-activation-2026-10-10.json`.
- At 11:44Z local disk showed about 73.26 GB free. c4 requires 54.36 GB free including its campaign
  floor and one-frame peak margin. c1–c3 and failed/legacy Senses services remain persistently
  disabled; evidence and STOP records remain.
- Helius credential remains in ignored mode-0600 `.env.local`; no rotation per user authorization.
  Fresh exact-value scan: one expected checkout match, zero in 5,130 Git objects, zero in 8,786
  local saved log/config files, zero process command-line matches. The previous credential-bearing
  process-list output remains in an earlier agent transcript; limits are recorded in
  `docs/evidence/helius-key-local-exposure-scan-2026-10-10.json`. c4 arguments are paths only; its
  environment has zstd configuration only.
- MCP checks: Alchemy `ping` succeeded; official Solana docs catalog responded; Helius MCP account
  transport previously closed/unavailable. Local collector credentials and RPC path are separate.
  No RPC request was made for the MCP checks.

## Senses evidence

- Frozen sell23 ledger v2 replayed offline on c3 frames 05:00–10:00Z: 96 selected full blocks, 553
  successful eligible candidates, 68 exact target-account settlements (diagnostic only), 485
  censored partial settlements, two failed exact-layout refs separately counted. All 3,318 ledger
  equation checks passed; 97 pools represented, top-two candidate concentration 48/553. No RPC.
  Receipt: `docs/evidence/pumpswap-sell23-c3-six-frame-ledger-replay-2026-10-10.json`; replay tool:
  `tools/brain_senses_replay_pumpswap_sell23_c3_six_frames.py`. This does not prove wallet-net price,
  global completeness, execution quality or admission.
- `buyExact25` remains structural only. Token-2022 transfer-fee/hook economics and unresolved
  instruction layouts remain quarantined. See `docs/brain-source-architecture.md` and receipts.
- Alchemy archive entitlement/coverage and account meter remain unestablished. Pair64 is sampled
  same-slot comparison only. No provider agreement proves global discovery completeness.

## Next horizon

1. At 12:05Z verify exactly one c4 fresh finalized anchor, explicit uncovered interval
   `[455216025, anchor+1)`, no stale `getBlocks`/`getBlock`, one unique request identity and unchanged
   c3 STOP/journal. Failure is terminal with no retry. A successful receipt permits only future
   scheduled c4 frames.
2. Keep c4 frozen. Read-only monitoring tracks the 16-block checks, 34-request frame cap, bytes/disk,
   gaps, duplicate identities and post-STOP traffic; no successor from the first frame.
3. After c4 receipts, ask Sol to decide a separate finite sustainable lease. Current Sol candidate is
   a measured seven-day lease with no auto-renewal and at most one future-only recovery after full
   STOP/journal reconciliation; this is not authorization or a change to c4.
4. Continue offline sensing gates during calendar accumulation. Never use c3 failed 11:00Z epoch or
   audit samples for tuning. Do not start Cortex.
5. Run repository Python with `env -u PYTHONPATH .venv/bin/python`; system Python previously resolved
   a production-tree package during a help probe.
