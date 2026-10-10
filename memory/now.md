# Current Project State

**As of:** 2026-10-10 05:11 UTC. Recheck Git, LaunchAgents, collector state and disk before acting.

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

## Next horizon

1. At/after the next scheduled frame (expected LaunchAgent trigger 06:05Z), inspect c3 status and
   evidence offline. Require a new finalized
   anchor, monotonic forward interval, full accounting and 16/16 checks. Never retry an ambiguous
   request; seal and stop on mismatch or gap.
2. Let the finite campaign reach its 24-attempt cap independently. Track gaps, request/byte totals, latency,
   disk headroom, selector concentration and terminal STOP; reconcile retained files to journals.
3. After c3 STOP, route the sustainable prospective-service contract to Sol, size it from measured
   c3 throughput/storage, then continue remaining Senses gates. No Cortex until practical sensing
   bottlenecks are actually closed.
