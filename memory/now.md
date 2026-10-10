# Current Project State

**As of:** 2026-10-10 05:27 UTC. Recheck Git, LaunchAgents, collector state and disk before acting.

## Boundaries

Continue Senses; do not start Cortex. No production checkout changes, trading/signing, paid upgrades,
protected holdout access, remote pushes or automatic promotion. Keep event, receipt, availability and
decision times distinct. Never convert unknown, missing, censored, failed, stopped or unsupported to
zero. Follow `AGENTS.md` and `docs/INDEX.md`.

## Host and prospective runtime

- Research checkout: `/Users/root1/Developer/memecoin-research`, branch `research/active`; the
  latest functional change is `Add receipt-bound Helius campaign 3 handoff`, followed by state-only
  memory commits. Verify actual HEAD and clean status. Production remains untouched. About 68.5 GiB
  free; raw captures stay under ignored `work/`.
- Campaign 3 is a one-use, receipt-bound continuation. Its first scheduled frame at 05:05Z completed:
  34/34 captured requests, 61,403,814 raw / 13,580,271 stored bytes, interval
  `[455116881,455133322)`, and 16/16 selected-block structural checks. The new finalized anchor is
  455133321. Both inherited gaps remain explicit; no backfill. The LaunchAgent remains loaded for
  the next future trigger. C1 used 0 and c2 used 1/43/56; both count against the original lease.
  C3 cap remains 24 frame attempts / 816 effective requests. Receipt:
  `docs/evidence/helius-inventory-v3-c3-first-frame-2026-10-10.json`.
- C2 and controller STOPs are immutable. Old public/Helius watchers, c1/c2 and the controller are
  unloaded. Helius watch-v3 keeps its explicit STOP; do not resume it.
- Helius credential is authorized without rotation. Private `.env.local`, Codex and Cursor config
  files are mode `0600`; collector reads its RPC endpoint in-process and LaunchAgents pass paths
  only. Codex/Cursor configs pin `helius-mcp@2.2.0`. Five legacy `@latest` npm processes that
  displayed the inherited key in `pgrep` listings were terminated; current process command lines,
  tracked Git, c3 capture, and saved Codex/Cursor files have no matches outside the current private
  configs. A second scan found three exact copies in two old Cursor JSONL transcripts that the first
  targeted history scan had missed; both were redacted, changed to mode `0600`, and their 103 JSONL
  records remain valid. The prior remediation receipt's saved-history zero is superseded by
  `helius-mcp-saved-transcript-redaction-correction-2026-10-10.json`. A 262 MB two-hour Unified Log
  scan found no exact match. One initial credential-bearing process-list result remains in the
  agent-session transcript. The MCP account tool currently returns `Transport closed`; independent
  RPC LaunchAgent is unaffected.
- Alchemy Archive access/limits/coverage were not established. No archive query or paid request was
  made; no separate PAYG endpoint is configured. Pair64 offline audit supports only sampled block
  agreement (64/64 identity/signature pairs); Alchemy inventory failure remains explicit.

## Senses status

- Helius v2 prospective inventory pilot closed at 104 requests / 186,022,930 raw / 41,818,169
  stored bytes, with a preserved oversized-frame gap. It is provider-bounded structural coverage,
  not global discovery or economic completeness.
- PumpSwap legacy sell23 transfer-ledger facts are qualified only in their captured class; censored
  native settlement remains censored, and this is not wallet-net execution price/admission.
- PumpSwap buyExact Token-2022 economics remain quarantined. Captured layouts/traces and endpoint
  deltas do not establish historical extension state, CPI settlement, withheld effects or wallet
  price. Keep unsupported and unknown subgroups separate.
- Source architecture owns acquisition/completeness and Senses receipts. Training findings belong
  in `docs/training-evaluation.md`. Details and scoped denominators are in those canonical docs and
  their named receipts; this file is only live handoff.
- The first c3 frame's frozen sell23 replay found 80/80 eligible candidates: 10 exact diagnostic
  account ledgers and 70 censored native-close settlements; six equations passed on each. No RPCs
  were made. Receipt: `helius-inventory-v3-c3-sell23-census-2026-10-10.json`.
- The buyExact census tool now replays sealed c3 full-block captures while binding epoch plan,
  frame receipt, summary hash, and selected slots; its default v2 path remains unchanged. Focused
  tests passed (4/4), Ruff passed, and a 512-block Pair64 replay exactly matched the frozen
  counters/matrix. First c3 frame: 365 references in 15 of 16 selected blocks; 59 pinned
  25-byte/two-account suffix structural candidates (53 success, six failed). No settlement or
  economics is inferred. Receipt: `pumpswap-buy-exact-v1-c3-frame-census-2026-10-10.json`; its
  mistyped commit ID is corrected by the adjacent correction receipt.
- An independent raw full-block replay rechecked those 59 candidates' transaction paths, signatures,
  wire bytes, status, and five pinned IDL positions. All match across 27 pools; no mint alias or
  per-pool tuple conflict. All 59 ordered two-account suffixes also matched the pinned eight-wallet
  set and ATA derived from each observed quote mint/token program (53 success, 6 failed attempts).
  The 2 distinct base-position keys / 27 quote-position keys remain only an observed sample
  distribution, not economic orientation; actual fee movement/fill remain unproven. Receipt:
  `pumpswap-buy-exact-v1-c3-role-tuple-qualification-2026-10-10.json`. Qualifier commits:
  `09bfe6f5cd606293ac11de53caf18d83ee199e1a`, `02d7feb17f0b4810957b85c2e6f4919f06e0c070`,
  `2045af272134dbe44582435e4eb7f8891d3635b2`, `f89b3df5a02bc1e456ca6e527d55a34dd922a671`.

## Next horizon

1. Read-only inspect each future c3 tick (next expected trigger 06:05Z): unique frame/request
   identities, new finalized anchor, forward interval, full inventory accounting, 16/16 body/signature
   checks, explicit gaps/failures, byte/disk floors, and no repeats. Do not run missed buckets again.
2. Accept normal campaign closure only after all 24 attempts, immutable reconciled journals/captures/
   summaries/STOP and budget totals, no ambiguity, agent bootout, and no later RPC. Otherwise stop and
   create a receipt-specific abnormal recovery decision; never backfill or reset the frontier.
3. After terminal STOP, send measured latency/byte tails, selected-block yield/concentration, gaps,
   account-level quota evidence, and free disk to Sol for a separate prospective-service lease. A
   finite seven-day lease (at most 5,719 all-in requests, measured byte ceilings, 50-GiB floor, no
   auto-renew) is a candidate only if normal closure and resource evidence support it. Continue Senses;
   no Cortex until sensing bottlenecks are actually closed.
