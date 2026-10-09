# Current Project State

**As of:** 2026-10-09 Europe/Moscow. Start from `docs/INDEX.md`; verify live state before acting.

## Goal and boundaries

Build a scalable, reproducible Solana learning system. No demonstrated executable edge. Do not
promote/deploy, sign transactions, use real money, access frozen F01/D43 or reserved holdouts, or
start Cortex before Senses is objectively complete. Keep production
`/Users/root1/Desktop/memecoin-ai` and credentials untouched. Mac 1 is the research host; Mac 2 is
standby. No Grok.

## Current host state

- Research checkout: `/Users/root1/Developer/memecoin-research`, branch `research/active`; code
  milestone `abfc531` is committed. Do not push. Pair64 receipt/docs and current handoff are being
  finalized against the committed audit code.
- Disk has 70 GiB free at current review. No external research volume is mounted. Helius Free
  plan info is available, but its account usage meter is not. Alchemy MCP returned an aggregate
  team meter of 18,680 CU / rounded $0.01 MTD; it is not attributable to this epoch or the Free app.
  No paid request was made in this session.
- Pair64 `512/hour` and legacy `64/hour` block campaigns have immutable `CAMPAIGN_STOP` records and
  their LaunchAgents are unloaded. Do not resume either campaign. The 2026-10-09 15:00Z pair64 raw
  root remains in ignored `work/`; latest audit receipt is
  `docs/evidence/helius-alchemy-pair64-offline-audit-2026-10-09.json`.
- The repeated-failure decode watchers, terminal old Helius address watcher, and Public RPC program
  watchers v2/v3 are unloaded; logs, STOP records, and raw captures remain. Helius watch v3 is the
  only remaining scheduled prospective sensor in this set: a capped sparse address-reference stream.
  Its 18:00Z root used 1,100 attempts (1,099 captures, one failure), raw response bodies totalled
  23.446 MB within a 42,216 KiB root, then it recorded STOP with pending references. The next
  six-hour epoch is fresh and cannot bridge that gap; it does not prove market or trade completeness.

## Senses facts

- Helius qualified 512/512 selected pair64 full blocks against signatures-only checks across 16
  strata. Alchemy captured and self-checked 64/64 preselected same-slot pairs; block identity and
  ordered signatures matched 64/64. Of 80,467 paired transactions, 80,321 trees matched; all 146
  differences were only `meta.logMessages` and had Helius's explicit `Log truncated` marker.
  Alchemy's one `getBlocks` inventory request failed and was not retried; its inventory and
  unsampled slots remain unknown. This is bounded provider-response agreement, not global discovery
  or economic agreement.
- Pair64 accounted for 1,154 block requests plus two anchors, 1,025 Helius block reservations, one
  Alchemy inventory failure, 3.012 GB raw response bytes and 695.247 MB zstd-stored block responses.
  It used no paid requests or retries. Local receipt times bind availability to capture; no
  historical availability is asserted. Helius reported 13,390 produced slots in its bounded frame.
- The scheduler failure was a persisted-key mismatch in v2 campaign preflight. The comparison now
  uses the exact written keys; focused tests and a no-RPC dry-run passed. The pair64 result and the
  paused collection decision are recorded in `docs/brain-source-architecture.md`.
- PumpSwap sell23 ledger v2 remains diagnostic-only: captured-class census 1,255 candidates, 410
  exact full target-account settlements, 843 censored native settlements, two unsupported token
  instructions; all six transfer/event/fee equations passed for 1,253 qualified transfers. No
  wallet-net price, training admission, execution quality, or market completeness claim.
- Existing v1 `buy_exact_quote_in` checks are diagnostic-only. Fresh development and sealed audit
  qualification are still required. Unknown layouts, Token-2022 effects, unexplained lamport/rent/
  close effects, and unsupported remaining accounts stay quarantined. Event, receipt, availability,
  and decision times remain distinct; unknown is not zero.
- Preliminary offline v1-discriminator census through the compiled-instruction normalizer: 512/512
  pair64 Helius full blocks, 642,312 transactions, zero normalization failures; 12,120 candidate
  transactions / 12,143 instruction refs across 507 slots (8,422 succeeded, 3,698 failed). Ref data
  lengths were 24: 2,046; 25: 9,605; 26: 492. This is only a reference census; no fills were inferred,
  and `buy_exact_quote_in_v2` was not included. Sol directs development on 15:00Z pair64 and allows
  the unread 13:00Z epoch as sealed audit if disjointness/provenance are bound before opening.
- No fresh independent model comparison, real-source fit, calibrated execution result, market edge,
  or profit claim. Sensing still lacks broad market/process completeness; do not call Senses complete.

## Next decision horizon

1. Finish the provider-audit/scheduler milestone and bind its receipt to committed source.
2. Use the 15:00Z pair64 archive to inspect deterministic development cases by instruction length,
   invocation location, tx result, and multi-reference/sibling patterns; include v2 in a separate
   census. Implement only source-confirmed layouts under Sol's attempt/fill contract.
3. Freeze code, selection rule, checks, and the unread 13:00Z archive hash before a one-time sealed
   audit. Do not tune on that audit; keep all unknowns/censoring explicit. No Cortex.
