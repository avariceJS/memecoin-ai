# Current Project State

**As of:** 2026-10-10 11:28 UTC. Recheck Git, LaunchAgents and disk before acting.

## Boundaries

Continue Senses; do not start Cortex. Do not change production, trade/sign, use protected holdouts,
push remotes, or make paid upgrades. Keep event, receipt, availability and decision times distinct.
Never turn unknown, missing, censored, failed, stopped or unsupported into zero. See `AGENTS.md`,
`docs/INDEX.md`, and the canonical source/evaluation owners.

## Runtime

- Checkout `/Users/root1/Developer/memecoin-research`, branch `research/active`, HEAD
  `7accfbbc70c91b43a6ad3d5f90358d1e4626fe4b` plus uncommitted activation-state docs/receipt.
  Production `/Users/root1/Desktop/memecoin-ai` remains untouched. Raw captures stay under ignored
  `work/`.
- c3 is permanently terminal after its reserved 11:00Z `getSlot` failed with
  `TransportSourceError`. Count 205 attempts including the uncertain charge; frontier 455216025 did
  not move. Six prior complete frames remain valid only in their provider-reported selected-slot
  scope. Do not retry or backfill. Hash-bound receipt:
  `docs/evidence/helius-inventory-v3-c3-abnormal-stop-2026-10-10.json`.
- Sol authorized one c4 lease: one fresh future finalized `getSlot` bootstrap, then at most 24
  future hourly frame attempts from that new anchor; 817 requests, 4,000,000,000 raw bytes,
  1,000,000,000 stored bytes, 20,000 slots/34 requests per frame, 50-GiB floor plus full-frame
  peak margin. c1/c2/c3 lineage counts are 0/1/205. No successor/renewal. Ambiguous or missed
  bootstrap/frame buckets stop without retry; no backfill.
- c4 LaunchAgent `ai.memecoin.brain.senses.helius-inventory-v3-20261010-4` is loaded idle for its
  12:05Z bootstrap trigger (`20261010T120000Z`); at 11:28Z it had zero runs, reservations, frame
  attempts or bytes. c3, c2, c1, controller, failed schedulers/watchers and completed fixed samples
  are persistently disabled. Activation receipt:
  `docs/evidence/helius-inventory-v3-c4-activation-2026-10-10.json`. Free disk was 73,256,988,672
  bytes; campaign floor plus peak margin requires 54,361,091,200 bytes.
- Existing Helius key is authorized without rotation. `.env.local`, Codex and Cursor configs are
  mode 0600. The latest exact-key scan found zero process-argv matches; c4 plist contains paths only.
  Earlier targeted saved-history/Git/Unified Log scans and Cursor redactions are recorded in the
  source architecture/evidence. Helius MCP account transport was unavailable; local collector RPC
  is independent. No RPC occurred during c4 preparation or activation.
- Alchemy archive entitlement/coverage and account meter remain unestablished; no archive/paid
  request was made. Pair64 evidence supports only sampled identity/signature agreement. No provider
  agreement implies discovery completeness.

## Senses status

- Helius inventory is provider-bounded structural observation, not global market/process coverage.
  Preserve inventory nonreturns, failed blocks, gaps and unknown intervals separately.
- PumpSwap sell23 captured-class transfer ledger is qualified; some native settlement is censored.
  Not wallet-net execution price or admission. `buyExact25` layout-role samples remain structural;
  Token-2022, CPI/fill/fee settlement and unresolved layouts stay quarantined. See
  `docs/brain-source-architecture.md` and named receipts.
- No claims of trading edge, economic completeness or temporal availability from historical
  backfill. The source architecture already records the long-range scale objective and native
  coordinate contracts.

## Next horizon

1. Keep c4 scheduled autonomously. After the 12:05Z bootstrap, verify its one request/result/receipt,
   new anchor, explicit uncovered `[455216025, anchor+1)` gap, and no inventory calls before allowing
   the next hourly frame. Never manually trigger a missed bucket.
2. Continue independent offline source/recovery/anomaly automation work while the calendar history
   accumulates. No RPC outside the c4 lease; no reset or c3 replay.
3. On c4 STOP, reconcile requests/bytes/gaps and runtime teardown, then return the measured evidence
   to Sol for a sustainable prospective lease decision. No auto-successor.
4. Continue qualified `buyExact25` evidence only on fresh disjoint samples; do not infer economics
   from role replication. No Cortex until sensing engineering gates actually move to representation,
   learning, capacity or compute.
5. Use `env -u PYTHONPATH .venv/bin/python` from this checkout; system Python previously resolved a
   production-tree package during a help probe.
