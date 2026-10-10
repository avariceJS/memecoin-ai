# Current Project State

**As of:** 2026-10-10 16:10 UTC. Recheck live runtime and disk before any campaign decision.

## Boundaries and runtime

- Continue Senses only. Research checkout `/Users/root1/Developer/memecoin-research`, branch
  `research/active`; HEAD at 16:10Z after runner review is `e082e3f`. Production `/Users/root1/Desktop/memecoin-ai`
  remains untouched. No trading, signing, paid upgrade, protected holdout access, push, backfill,
  or ambiguous-request retry.
- c3 is permanently terminal after its reserved 11:00Z `getSlot` transport failure (205 attempts,
  charge uncertain), frontier 455216025. No retry or backfill.
- c4 remains a frozen finite lease: one finalized `getSlot` anchor, then at most 24 future hourly
  attempts, no successor/auto-renewal. Its 12:05Z bootstrap recorded anchor 455249112, frontier
  455249113, gap `[455216025,455249113)`, one request (43 raw/56 stored bytes), no `getBlocks` or
  `getBlock`, zero open reservations; c3 STOP unchanged. The 13:00Z, 14:00Z, and 15:00Z frames
  completed 34/34 requests and 16/16 selected block/signature checks each. Across those frames,
  responses total 216,017,528 raw / 45,211,652 stored bytes; the 15:00Z inventory interval was
  `[455282201,455298550)` with 16,345 provider-reported produced slots. Four future buckets had
  scheduled ticks through 16:05Z (five total process runs including bootstrap); the latest exited
  0. At 16:09Z the LaunchAgent was loaded idle, c4 root STOP was absent, free disk was 71 GiB, and
  the root used 70,680 KiB. Next trigger is 17:05Z. The 16:00Z frame evidence remains unopened
  while the four-case frozen audit set is incomplete. Do not manually invoke. Receipts:
  `docs/evidence/helius-inventory-v3-c4-bootstrap-2026-10-10.json`,
  `docs/evidence/helius-inventory-v3-c4-first-frame-2026-10-10.json`.
- c4 root: `work/brain-senses-live-helius-inventory-v3-20261010-4/`. Preserve gaps; reconcile each
  scheduled epoch and stop on lease failure. The first-frame one-hour projection is 816
  reservations, 1.40 GB raw and 0.316 GB stored per day;
  credit billing units remain unverified.
- 2026-10-10 15:38Z: offline service controller v1 uses a separate v2 external anchor epoch before
  initializing an unchanged v3 campaign. Adjacent UTC-day rollover now reconciles the last sealed
  epoch, closes the prior campaign with canonical v3 administrative STOP under both locks, and binds
  closure/root hashes plus anchor/frontier/gaps into new g01 lineage before frame reservation. Missed
  midnight remains anchor-only. Initialization and reservation crash prefixes repair only when
  provably unstarted; bootstrap source dependencies are pinned. Focused synthetic suite: 29 passed,
  including partial/full-day rollover, recovery generation, missed midnight, crash points, duplicate
  tick, lock contention, source drift, init/reserve recovery, campaign-hash scope and budget
  monotonicity. v2/scheduler/c4 recovery suites: 53 passed; Ruff and diff checks pass. A separate
  status timestamp fix now projects per-request start/receipt/availability/decision nanoseconds and
  nullable block time; ambiguous transport receipt/availability remain unknown. Service suite: 32
  passed. Offline path-only runner and LaunchAgent template committed in `7cc22ea`; runner/service
  focused suite: 50 passed, Ruff/format/diff passed. The template is not installed or loaded.
  Activation remains gated on c4 normal terminal receipt, measured tails, account limit review and
  independent review; no service activation has occurred.
- Helius key remains in ignored mode-0600 `.env.local` and two mode-0600 MCP configs; user
  authorized no-rotation continuation. The latest exact-value scan (13:55Z) found zero in 5,253
  Git objects, 253,570 accessible saved text/runtime/evidence files, current process arguments and
  1,313,859,327 bytes of eight-hour Unified Log output. c4 LaunchAgent arguments contain only fixed
  paths/subcommands; collector reads the endpoint in-process. Historical process-list disclosure
  remains in agent transcript. Provider-side logs and backups were not inspected. Receipt:
  `docs/evidence/helius-key-pre-resume-exposure-rescan-2026-10-10.json`.
- Post-resume exact rescan at 14:30Z found zero matches in 5,318 Git objects, 681 process command
  lines, 4,995 files modified since the prior full scan, and 90,043,541 bytes of new Unified Log
  output. Only the three expected private configs contain the key. MCP uses pinned
  `helius-mcp@2.2.0` with environment configuration; c4 argv is path-only. Receipt:
  `docs/evidence/helius-key-post-resume-exposure-rescan-2026-10-10.json`.
- Pre-work continuation rescan at 15:38Z found zero matches in 5,334 Git objects / 121,698,114
  blob bytes, active process arguments, and 198,986,586 Unified Log bytes. The complete checkout
  and full accessible saved-root scan found no credential components outside `.env.local` and the
  two expected mode-0600 MCP configs. c4 still uses fixed argv. Receipt:
  `docs/evidence/helius-key-resume-exposure-rescan-2026-10-10.json`.
- Post-first-tick scan at 16:07Z compared exact endpoint/key components in 237 recently modified
  accessible files (283,138,089 bytes) and active process arguments: zero argument matches; the
  only file match was expected private mode-0600 `~/.codex/config.toml`; no recent saved-log or
  other-path match. `.env.local` and both MCP configs remain mode 0600. This supplements the
  15:38Z full Git/object and accessible-root scan; provider-side logs and backups were not inspected.
  The earlier process-list disclosure remains in the session transcript and is accepted by user.
- MCP: Alchemy ping and official Solana docs search worked; Helius documentation transport closed.
  No MCP provider RPC was performed.

## Senses evidence

- Frozen sell23 ledger v2 replay on c3's six completed frames: 96 selected blocks, 553 successful
  eligible candidates (68 exact target-account ledgers, 485 censored partial), two failed layout refs
  separately counted, all 3,318 equations passed. No wallet-net price, completeness or admission.
- `buyExact25` uses pinned official PumpSwap SDK 2.1.0. The frozen v2 pool-v2 qualifier's first c3
  frame reconciled raw/report sets 192/192 across 16 blocks/16,668 transactions; 190 structural
  passes and two failed attempts quarantined for global writability. No settlement qualification.
- Frame-2 ordinary development replication reconciled raw/report 258/258 across 16 blocks/16,426
  transactions. Frozen predicate accepted 257 (225 successful references, 32 failed attempt-only)
  and quarantined one successful reference: fee wallet message key 7 is globally writable and is
  referenced by a sibling outer instruction at `[1]` and the PumpSwap CPI at `[1,0]`. Sibling
  semantics remain unknown; no economic settlement qualified. Receipt:
  `docs/evidence/pumpswap-buy-exact-v1-c3-frame2-pool-v2-prefix-raw-reconciled-2026-10-10.json`.
- The frozen predicate was applied once to c4's first eligible untouched frame: raw/report candidate
  sets match 171/171 across 16 blocks/17,607 transactions; all 171 structural passes comprise 141
  successful references and 30 failed attempt-only references. Zero settlements qualified. Census:
  `docs/evidence/pumpswap-buy-exact-v1-c4-first-frame-census-2026-10-10.json`; result:
  `docs/evidence/pumpswap-buy-exact-v1-c4-first-frame-pool-v2-prefix-raw-reconciled-2026-10-10.json`.
- Sol's bounded c4 success trace is complete: 141/141 frozen successful references replayed from 16
  receipt-hash-verified full blocks. It records event bytes, ordered target-account touches, token
  operands, and transaction-boundary endpoints/deltas. 140 Token-2022 economics remain prohibited;
  the one dual-legacy ledger gate stayed closed. Receipt:
  `docs/evidence/pumpswap-buy-exact-v1-c4-success-trace-2026-10-10.json.zst` (5,431,224 raw JSON /
  209,052 compressed bytes). Focused tests 4 passed and Ruff passed. `make check` currently fails on
  43 pre-existing Ruff E501 findings in existing qualifier tools; new files were clean.
- The offline state-bracketing feasibility receipt covers all 140 Token-2022-base refs and all 16
  full blocks/17,607 transactions. Counts: 137 exact role-matching descendant events, 3 absent or
  unqualified; 139 rows have missing/partial token endpoints; 140 have outside-subtree target
  touches; 5,421 other-transaction writable-potential occurrences. Ninety-one have no other-transaction
  writable potential on either mint, but zero pass a mint-only bank-state bracket; zero pass the
  full-account bracket. No acquisition control or state RPC. Receipt:
  `docs/evidence/pumpswap-buy-exact-v1-c4-state-bracketing-feasibility-2026-10-10.json`.
- Focused state-bracket tests: `4 passed`; Ruff clean for new tool/test. The manifest records raw
  state/TLV, fee epoch, withheld amount, hook/confidential, native and lifecycle requirements while
  qualifying none of their economic effects.
- Exhaustive focused pytest coverage checks the frozen qualifier's message-header privilege
  partitions through 12 static keys, loaded address boundaries, and fail-closed invalid cases;
  `6 passed`. No Hypothesis dependency was added.
- Later c4 frames are ordinary frozen-predicate replication only; no tuning or independent audit.
  Token-2022 effects, other layouts,
  fills, fee movement and native settlement remain quarantined. Pair64 is sampled same-slot evidence,
  not global completeness. Alchemy PAYG endpoint is absent from research `.env.local`.
- The sell23 c4 audit selector and offline auditor are frozen in `537c929` and timing hardening in
  `3d84e6c`. It selects the first four
  completed frames whose scheduled trigger, plan creation, first durable reservation, request start,
  and seal are all strictly after `5c5eed3`'s freeze time. Thus `13:00Z` is excluded; `14:00Z` is
  the first eligible completed frame. At 14:23Z only that one eligible frame exists; transaction
  cases remain unopened. The offline preflight returns inconclusive without reading cases. Focused
  tests: 14 passed; Ruff clean. Audit tool:
  `tools/brain_senses_sell23_c4_native_flow_audit.py`.
- Offline service checks also pass: c4 recovery fixtures `7 passed`, scheduler/epoch suites `35
  passed`, finite controller fixtures `11 passed`. These were local synthetic tests; no RPC or live
  LaunchAgent invocation occurred.
- 2026-10-10: committed the offline PumpSwap structural novelty tool/tests (`574db0f`); final
  receipt `docs/evidence/pumpswap-structural-novelty-v1-2026-10-10.json` binds that commit and tool
  hash. Pair64: 512 blocks, 642,312 tx, 37,427 referencing tx, 72,034 refs. c3: six 16-block frames,
  98,993 tx, 6,858 referencing tx, 13,422 refs. Census denominators reconcile where frozen receipts
  exist; structural-only, no global discovery/economic/admission claim. Focused tests: 7 passed;
  no RPC or c4 audit access.

## Next decision horizon

1. Keep c4 on its unchanged loaded schedule; inspect each scheduled frame read-only and never
   manually invoke, retry, or backfill. First four completed scheduled frames after the frozen sell23
   gate define its audit. At 14:23Z, `13:00Z` is pre-freeze and excluded; only `14:00Z` is eligible
   and complete. Audit cases remain unopened. Preserve intervening failed buckets as gaps and use
   later frames only under the frozen first-four-eligible-completed rule. Audit selector/examples
   were committed before case inspection.
2. Once four frames are sealed, independently enumerate raw eligible sell23 native-close rows,
   reconcile all denominators and request/frame hashes, and apply the frozen gate once. No tuning.
3. c4's finite terminal and the audit are separate evidence boundaries. Only then revisit the
   diagnostic-ledger decision and a finite successor policy with Sol; no auto-renewal. Do not start
   Cortex.
4. Do not reuse `tools/brain_senses_campaign_controller.py` unchanged as the long-term successor:
   it is a 72-hour/3-campaign controller with a hard-coded v2 predecessor contract. After c4 STOP,
   reconcile actual c4 tails and get Sol's bounded successor contract before implementing or loading
   any replacement controller.
