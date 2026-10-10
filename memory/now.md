# Current Project State

**As of:** 2026-10-10 03:47 Europe/Moscow. Verify Git, runtime and disk before acting.

## Boundaries

Build scalable point-in-time Solana market experience; no demonstrated trading edge. Keep
`/Users/root1/Desktop/memecoin-ai`, real money/signing, protected F01/D43 and reserved holdouts,
promotions and remote pushes untouched. Do not start Cortex before Senses is objectively ready.

## Host and prospective runtime

- Research checkout: `/Users/root1/Developer/memecoin-research`, branch `research/active`; clean at
  handoff. Verify current HEAD before acting. Production untouched.
- About 69 GiB free. No Senses/Helius/Alchemy collector LaunchAgent or collector process is active.
  Previous public/Helius watchers and old schedulers remain stopped; Helius watch-v3 retains its
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
- Campaign schedule has a request-cap defect: 817 total requests permits a partial next frame after
  24 complete 34-request frames. Do not install a LaunchAgent or edit the frozen manifest. Sol's
  decision is to run the next two actual future hourly attempts manually, then stop/review this
  campaign. One (01:00Z) completed; only the future 02:00Z attempt remains. Any successor needs a
  new version, 24-attempt finite horizon, separate observed/inventoried frontier, and full-frame
  request/byte/disk preflight before anchor.
- Owner authorized the current Helius key. It remains in `.env.local` and Codex MCP environment
  configuration, both mode `0600`; collector CLI has no endpoint argument. The exact key was
  redacted from local Codex/shell histories and SQLite thread history. Exact scans found no key in
  reachable Git objects or active process arguments. macOS Unified Log query timed out; remote
  platform logs and filesystem snapshots are outside this checkout's control. Never echo or pass a
  credential in a command argument or shell command text.
- Alchemy research HTTP endpoint/plan limits remain unverified. No paid Account Archive query was
  made. The frozen no-RPC control and its three-read ceiling are in
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
- PumpSwap buyExact v1 remains quarantined: pinned official instruction layout is known, but the SDK
  source survey found no exact-quote builder helper or evidence mapping captured account suffixes to
  builder variants. Token-2022 mint/account state and historical intermediate state remain unknown.
- Senses remains incomplete: broad process/market discovery, lifecycle, provider-independent
  completeness and prospective continuity are limited. Preserve unknown/gap/censored/failed/stopped/
  unsupported/immature separately; never convert them to zero.

## Next horizon

1. Check actual UTC, Git, runtime and disk; run only the next two future buckets. No replay,
   catch-up or retry of `20261010T000000Z`.
2. Review evidence across the prospective campaign, append a terminal campaign STOP, then begin a
   separately versioned successor with whole-frame request preflight, aggregate budget, account
   quota measurement, and a tested LaunchAgent/service recovery path.
3. Continue offline source/coverage and lifecycle qualification and revisit buyExact after this
   provider/recovery horizon. No Cortex.
