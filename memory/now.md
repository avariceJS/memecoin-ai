# Current Project State

**As of:** 2026-10-10 04:26 UTC. Recheck Git, LaunchAgents, collector state and disk before acting.

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
- Campaign 3 is a one-use, receipt-bound continuation. Its LaunchAgent is loaded and not running;
  first scheduled trigger is 2026-10-10 05:05Z. It adopts c2 finalized anchor 455116880 only as
  observed frontier 455116881, with both gaps inherited and no backfill/bootstrap request. C1 used
  0 requests; c2 used 1 request / 43 raw / 56 stored bytes, all debited from original 72-hour lease.
  C3 cap is 24 frame attempts / 816 effective requests. Receipt:
  `docs/evidence/helius-inventory-v3-c3-handoff-activation-2026-10-10.json`.
- C2 and controller STOPs are immutable. Old public/Helius watchers, c1/c2 and the controller are
  unloaded. Helius watch-v3 keeps its explicit STOP; do not resume it. The first c3 frame has not run.
- Helius credential is authorized without rotation. Private `.env.local`, Codex and Cursor config
  files are mode `0600`; collector reads its RPC endpoint in-process and LaunchAgents pass paths
  only. Current configs pin `helius-mcp@2.2.0`. Exact key scans found no match in working files,
  4,891 Git objects, current `argv`, recent saved histories, or a 262 MB two-hour Unified Log scan.
  One old Cursor config-history snapshot was redacted. Helius account status authenticated, but plan
  and credit usage remain unavailable without JWT session. A prior Alchemy endpoint in saved Codex
  records was redacted; see source-architecture receipt/history.
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

## Next horizon

1. At/after 05:05Z, inspect c3 status and captured evidence offline. Require new finalized upper
   anchor, forward start 455116881, full request/byte reconciliation and 16/16 checks. Never retry
   an ambiguous request; seal and stop on mismatch or gap.
2. Let the finite 24-attempt campaign run independently. Track gaps, request/byte totals, latency,
   disk headroom, selector concentration and terminal STOP; reconcile retained files to journals.
3. After c3 STOP, route the sustainable prospective-service contract to Sol, size it from measured
   c3 throughput/storage, then continue remaining Senses gates. No Cortex until practical sensing
   bottlenecks are actually closed.
