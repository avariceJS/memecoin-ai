# Current Project State

**As of:** 2026-10-10 Europe/Moscow. Start from `docs/INDEX.md`; verify live state before acting.

## Goal and boundaries

Build a scalable, reproducible Solana learning system. No demonstrated executable edge. Do not
promote/deploy, sign transactions, use real money, access frozen F01/D43 or reserved holdouts, or
start Cortex before Senses is objectively complete. Keep production
`/Users/root1/Desktop/memecoin-ai` and credentials untouched. Mac 1 is the research host; Mac 2 is
standby. No Grok.

## Current host state

- Research checkout: `/Users/root1/Developer/memecoin-research`, branch `research/active`; verify
  `git status` and HEAD at entry. Do not push. Production checkout remains untouched.
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
  Its 18:00Z root used 1,100 attempts (1,099 captures, one failure), recorded STOP with pending
  references, and is marked already attempted; no later epoch exists as of 2026-10-09 21:09Z. The
  LaunchAgent is loaded but not running between five-minute scheduler invocations. It must not
  replay or imply continuity. The resulting gap is explicit; this stream is not market/trade
  completeness.

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
- `buy_exact_quote_in` v1 now has a tracked source-bound attempt/suffix census receipt. It is
  structural only: 12,120 candidate transactions / 12,143 refs across 507 slots; 1,377 refs meet
  the pinned 25-byte/two-account documented suffix candidate shape. Failed transactions remain
  attempts. This census itself makes no settlement or admission claim. The 13:00Z archive was
  opened by a frozen buyExact census attempt on 2026-10-10, but report construction failed on an
  optional source-receipt field after processing the 512 blocks. No report/outcomes were emitted or
  accepted. Failure receipt:
  `docs/evidence/pumpswap-buy-exact-v1-audit-failure-2026-10-10.json`. Treat the archive as consumed
  for this audit: do not rerun or tune against it; obtain a new disjoint source.
  `buy_exact_quote_in_v2`, Token-2022 effects and unverified payload/suffix
  layouts remain out of scope/quarantined.
- Frozen development replay currently covers all 62 successful dual-legacy refs in the 15:00Z
  captured class: 62 exact BuyEvent/transfer fact sets and exact full ordered ledgers for all six
  touched token accounts. Unit checks preserved 9/6 decimal pairs (61) and 6/9 (one); values are
  atomic units only. Fee-flow and gross/net equations remain empirical hypotheses. The 13:00Z
  Helius full-block set has a metadata manifest proving disjointness from 15:00Z, but its attempted
  buyExact census failed before emitting results. It is no longer a valid unopened audit source.
- No fresh independent model comparison, real-source fit, calibrated execution result, market edge,
  or profit claim. Sensing still lacks broad market/process completeness; do not call Senses complete.

## Next decision horizon

1. Commit the buyExact census receipt adapter fix. Preserve the 13:00Z failed attempt; bind a fresh
   disjoint full-block source and one-pass acceptance plan before decoding it.
2. Run that sealed audit once with all class denominators and per-ref exact/partial/unsupported
   reasons. Do not tune on audit; if it exposes a code defect, quarantine the result and seek another
   independent source before any requalification.
3. Decide whether the capped v3 address-reference stream should be replaced with a recoverable
   prospective sensor that preserves explicit gaps and charged attempts. The LaunchAgent is loaded
   but idle between scheduler invocations; do not restart the exhausted epoch. No Cortex.
