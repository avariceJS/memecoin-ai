# Current Project State

**As of:** 2026-10-10 04:23 UTC. Verify Git, runtime and disk before acting.

## Boundaries

Build scalable point-in-time Solana market experience; no demonstrated trading edge. Keep
`/Users/root1/Desktop/memecoin-ai`, real money/signing, protected F01/D43 and reserved holdouts,
promotions and remote pushes untouched. Do not start Cortex before Senses is objectively ready.

## Host and prospective runtime

- Research checkout: `/Users/root1/Developer/memecoin-research`, branch `research/active`, HEAD
  `277ffca` before this handoff implementation commit. Production untouched. About 68.6 GiB free.
- Campaign 2 sealed its missed-gap STOP at 04:05Z and the controller stopped without a successor.
  Sol approved receipt-bound anchor adoption. C3 is prepared in a separate root and its LaunchAgent
  is loaded but not running; first future trigger is 05:05Z. It starts forward-only at 455116881,
  keeps both gaps, has at most 24 frames / 816 requests, and debits c2's 1/43/56 actual use against
  the original lease. No c3 RPC or backfill has occurred. Receipt:
  `docs/evidence/helius-inventory-v3-c3-handoff-activation-2026-10-10.json`.
- Old public/Helius watchers, c1/c2 and the v3 controller remain stopped. Helius watch-v3 keeps its
  explicit STOP and must not be resumed.
- Helius inventory-first v2 has one complete prospective frame, `[455028552,455037817)`, with
  9,261 provider-returned produced slots, 16/16 selected-block self-checks and 34 captured requests.
  It retained 71,939,096 raw / 15,335,264 zstd bytes; 2,203 PumpSwap structural references are not
  economic swaps. Four numeric positions were absent from returned inventory. Receipt:
  `docs/evidence/helius-inventory-v2-first-frame-2026-10-09.json`.
- The real `20261010T000000Z` invocation at 00:43Z captured one finalized-slot anchor, found a
  23,699-slot range above the 20,000 cap, and sealed `oversized_frame` with no inventory/block
  requests and no retries. The interval is an explicit gap, not zero or backfill. Never replay it.
  Receipt: `docs/evidence/helius-inventory-v2-oversized-gap-2026-10-10.json`.
- The real `20261010T010000Z` bucket completed: 4,496 provider-returned produced slots, 34/34
  journal rows validated offline, and 16/16 selected block/signature self-checks passed. It retained
  53,057,298 raw / 12,349,827 zstd bytes. The prior gap remains uncovered. Receipt:
  `docs/evidence/helius-inventory-v2-frame-2026-10-10T010000Z.json`.
- The real `20261010T020000Z` bucket completed normally: 16,512 provider-returned produced slots,
  34/34 journal rows and 16/16 selected-block self-checks. The frozen pilot closed at 104
  requests / 186,022,930 raw / 41,818,169 stored bytes, with no outstanding reservations. Receipt:
  `docs/evidence/helius-inventory-v2-final-2026-10-10.json`.
- Campaign schedule had a request-cap defect: 817 total requests permits a partial next frame after
  24 complete 34-request frames. Do not edit the frozen v2 manifest. Sol's final bucket ran once under the unchanged manifest,
  then was closed and sealed. A successor needs a
  new version, 24-attempt finite horizon, separate observed/inventoried frontier, and full-frame
  request/byte/disk preflight before anchor.
- Sol authorized a separately finite 72-hour generator lease for at most three unique 24-attempt
  campaigns (2,451 requests / 12 GB raw / 3 GB stored aggregate). The inherited sealed v2 gap is
  recorded in lineage; abnormal current-campaign state halts generation. Campaign 1 failed before
  anchor and RPC at 03:05Z because launchd's default PATH omitted Homebrew zstd. Its immutable STOP
  records zero requests, bytes, reservations and attempts; only one empty `03:00Z` bucket directory
  exists. Sol approved one receipt-bound pre-anchor replacement. Recovery preserved the STOPs and
  bucket, reproduced the failure offline, bound `/opt/homebrew/bin/zstd` by hash, retained the
  original 72-hour deadline and all aggregate ceilings, and started campaign 2 in a fresh root. The
  `03:00Z` slot remains uncovered and will never be replayed. Campaign 2 captured one finalized
  `getSlot` anchor (455116880), 43 raw / 56 stored bytes, zero inventory/block requests, and gap
  `[455082524,455116881)` with `missed_slot_backfill=false`. Its reservation reconciles. The
  unchanged normal-successor verifier rejected the non-24-attempt STOP (`predecessor_campaign_stop_abnormal`);
  controller STOP preserved the original lease. Its aggregate counters remain zero, so c2's actual
  1/43/56 use must be included by any c3 continuation. Receipt:
  `docs/evidence/helius-inventory-v3-campaign2-missed-gap-2026-10-10.json`. Sol's c3 contract adopts
  anchor 455116880 only as observed frontier 455116881, preserves both gaps, and starts from the next
  future frame without bootstrap RPC. C3 is the last ID under the original lease, at most 24 frame
  directories/816 requests; c2 actual use is debited from unchanged 2,451/12GB/3GB aggregate caps.
  Recovery receipt:
  `docs/evidence/helius-inventory-v3-preanchor-recovery-2026-10-10.json` (SHA-256
  `d5da27cdf1909065bdbfc5a5cd676141170f742dd6e11cfefbbe120f30d23c2f`). Both original v3
  LaunchAgents are now stopped; do not revive the controller or c2 or backfill.
- A separate v3 scheduler implements Sol's per-campaign contract locally: one-frame reservations,
  34-request/byte/disk preflight, anchor-only gap capture followed by terminal STOP, immutable reservation
  releases, split frontiers, fail-closed recovery and read-only status. Controller and related focused
  suites pass 54 tests; targeted Ruff passes. Differential review is under ignored `work/review/`.
- Owner authorized the current Helius key without rotation. It remains in `.env.local` and private
  Codex/Cursor MCP configuration, all mode `0600`; LaunchAgents pass only paths and collector code
  reads the endpoint in-process. Exact key scans found no match in working files, 4,891 Git objects,
  current `argv`, recent saved histories, or a 262 MB two-hour Unified Log scan. One obsolete saved
  Cursor config snapshot was redacted. Current MCP configs pin `helius-mcp@2.2.0`; existing process
  argv contains no exact key. Helius account status authenticated, but credit usage/plan limits are
  unavailable without a JWT session.
  One Alchemy Free endpoint did appear in three saved Codex records (one history line, 28 session
  lines, three SQLite rows); those exact strings were redacted, SQLite secure-deleted/checkpointed/
  vacuumed, and affected files tightened to `0600`. Post-scrub file/WAL/process-argument searches
  found no matches. Remote snapshots are outside local control. Never put credentials in arguments
  or saved command text.
- The Helius MCP package resolved locally and from the registry as 2.2.0; Codex and Cursor config
  now pin it to `helius-mcp@2.2.0`, with keys retained only in private env config. The cached package
  version is 2.2.0. Account status authenticated; credit usage and plan limits remain unavailable.
- Alchemy MCP identifies one `memcoin-ai` app with Solana mainnet. MTD usage: 18,680 CU / $0.01
  through 03:44Z; Oct 8 app-method series attributes 15,000 to `getSlot`, 3,520 to
  `getSignaturesForAddress`, and 160 to `getTransaction`. No account limit, Account Archive
  entitlement, archive meter or account-specific coverage was returned. PAYG endpoint variable is
  absent. No archive query was made; the frozen three-read control remains unchanged. Receipt:
  `docs/evidence/alchemy-account-archive-access-gate-mcp-2026-10-10.json`. Frozen plan:
  `docs/evidence/alchemy-account-archive-mint-control-plan-2026-10-10.json`.
  `docs/evidence/alchemy-account-archive-mint-control-plan-2026-10-10.json`.

## Senses facts

- Pair64 offline audit: 512/512 Helius selected-block self-checks; 64/64 Alchemy same-slot block
  identity and ordered-signature matches; 146/80,467 transaction-tree differences were Helius log
  truncation. No independent inventory or global completeness.
- PumpSwap sell23 frozen ledger v2: 1,255 prior captured-class candidates: 410 exact full account
  settlements, 843 censored native settlements and two unsupported token instructions. On the first
  prospective frame, all 67 eligible candidates passed six transfer equations; 13 exact and 54
  censored. No wallet-net price/admission. Receipt:
  `docs/evidence/helius-pumpswap-sell23-ledger-v2-inventory-frame-census-2026-10-10.json`.
- Offline v2 selector/yield census replays all 48 selected full/signature pairs across three
  completed frames from 30,269 provider-returned inventory members. It records 5,743 structural
  PumpSwap references, 2,903 unique referencing transactions, 553 failed referencing transactions,
  and 168 exact legacy sell23 candidates across 28 pools (top two aggregate: 39/168). The first
  frame is concentrated: 39/67 candidates in two pools. Quarter coverage is present in each frame;
  representativeness is not established. Three-frame byte/request totals reconcile exactly to the
  campaign closure. Receipt: `docs/evidence/helius-inventory-v2-selector-yield-census-2026-10-10.json`.
- PumpSwap buyExact v1 remains economically quarantined: pinned official layout is known, but the
  SDK survey found no exact-quote helper or account-suffix mapping. A new offline endpoint-delta
  census replayed one preidentified isolated ref against its exact captured block and derived
  provider-reported account deltas for all 1,234 frozen development refs. All seven endpoint rows
  are present in 52 refs; 1,182 are partial, and 1,233 have sibling touches that censor buy-subtree
  attribution. The one isolated ref is only a delta feasibility candidate. No CPI settlement, fee,
  withheld amount, wallet price, or economic admission is qualified. Receipt:
  `docs/evidence/pumpswap-buy-exact-v1-token2022-endpoint-delta-census-2026-10-10.json.zst`.
- The source-bound offline Token-2022 privilege census reconciled all 1,234 dev refs across 298
  captured full blocks / 384,459 transactions with zero network requests. Same-slot quote-mint
  privilege partitions are 1,146 readonly-or-absent with no unclassified target touch, 67 with an
  unclassified target touch, 11 with writable-potential evidence only, and 10 with both. Writable
  means potential, not actual modification. All 1,234 retain `historical_extension_state=unavailable`
  and `economic_effects=not_assessed`. Receipt:
  `docs/evidence/pumpswap-buy-exact-v1-token2022-privilege-census-2026-10-10.json`.
- A second offline trace replay covered those 1,146 refs across 285 captured blocks / 366,918
  transactions. It retained exact target-touching wire bytes, instruction paths/stacks, and
  same-slot writable-potential counts for each target account. A pinned official Token-2022 source
  replay classified the four exact `(09)` traces as structural `CloseAccount`; they remain
  economically unsupported lifecycle instructions. The other 1,142 rows are structural traces.
  All 1,146 remain economically unqualified with extension state unavailable. Receipt:
  `docs/evidence/pumpswap-buy-exact-v1-token2022-clean-stratum-2026-10-10.json`.
- Offline replay decoded all 3,552 exact Token-2022 `TransferChecked` wire traces as declared
  atomic u64 plus decimals argument; all pass structure and transaction-status gates. IDL role
  attribution is retained only for 3,438 aligned traces; 114 sibling-parent traces remain
  role-unknown. No realized transfer or economic effect is inferred. Receipt:
  `docs/evidence/pumpswap-buy-exact-v1-token2022-transfer-checked-2026-10-10.json`.
- Alchemy MCP access gate: one `memcoin-ai` app, 18,680 CU / $0.01 month-to-date through 03:44Z;
  no account limit or Account Archive entitlement/meter/coverage returned. The research Free endpoint
  is configured, but no separate PAYG endpoint variable exists. No archive query or paid request.
  Receipt: `docs/evidence/alchemy-account-archive-access-gate-mcp-2026-10-10.json`.
- Senses remains incomplete: broad process/market discovery, lifecycle, provider-independent
  completeness and prospective continuity are limited. Preserve unknown/gap/censored/failed/stopped/
  unsupported/immature separately; never convert them to zero.

## Next horizon

1. Verify the 05:05Z c3 prospective frame from captured files; require a new finalized upper anchor,
   start 455116881, full accounting and no retries. A failure seals and stops; do not replay.
2. Let the finite 24-attempt c3 campaign finish autonomously. Track gaps, 16/16 checks, raw/zstd
   bytes, latency, disk headroom and selector concentration; reconcile actual request/byte totals.
3. After c3 STOP, assess a separately versioned finite sustainable collector sized from measured
   throughput and storage. Then continue the remaining Senses gates: buyExact economics stay
   quarantined, Alchemy Archive needs verified access/limits/coverage, and no Cortex yet.
