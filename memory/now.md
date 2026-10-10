# Current Project State

**As of:** 2026-10-10 05:28 Europe/Moscow. Verify Git, runtime and disk before acting.

## Boundaries

Build scalable point-in-time Solana market experience; no demonstrated trading edge. Keep
`/Users/root1/Desktop/memecoin-ai`, real money/signing, protected F01/D43 and reserved holdouts,
promotions and remote pushes untouched. Do not start Cortex before Senses is objectively ready.

## Host and prospective runtime

- Research checkout: `/Users/root1/Developer/memecoin-research`, branch `research/active`; offline
  Token-2022 privilege classifier source is committed as `e854c90`. Verify current HEAD and worktree.
  Production untouched.
- About 70 GiB free. The Helius finite controller and campaign-1 LaunchAgents are loaded but idle; the v3 campaign has made no RPC yet.
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
  recorded in lineage; abnormal current-campaign state halts generation. Controller source is committed (`4406fde`) and locally qualified. Controller and campaign-1
  LaunchAgents are loaded; both have zero active processes now. Controller has two successful runs;
  campaign has zero runs and zero requests. Plist hashes and log modes are in
  `docs/evidence/helius-inventory-controller-install-2026-10-10.json`. First v3 attempt is the next
  future local :05 event; do not kickstart or backfill. At 05:17 MSK the first event is 06:05 MSK
  (03:05Z), not yet due.
- A separate v3 scheduler implements Sol's per-campaign contract locally: one-frame reservations,
  34-request/byte/disk preflight, anchor-only gap capture followed by terminal STOP, immutable reservation
  releases, split frontiers, fail-closed recovery and read-only status. Controller and related focused
  suites pass 54 tests; targeted Ruff passes. Differential review is under ignored `work/review/`.
- Owner authorized the current Helius key. It remains in `.env.local` and Codex MCP environment
  configuration, both mode `0600`; collector CLI has no endpoint argument. Credential incident
  status: the exact credential appeared in 20 zsh history files; affected entries were scrubbed
  atomically, and scans found no reachable Git/object or current argv match. Local Codex/SQLite
  history was also redacted. macOS Unified Log query timed out; remote logs and snapshots are outside
  this checkout's control. Never echo or pass a credential in command text or arguments.
- The Helius MCP package resolved locally and from the registry as 2.2.0; its config is now pinned
  to `helius-mcp@2.2.0` with the child environment unchanged and mode `0600`. Existing MCP processes
  were already running the cached 2.2.0 package.
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
- The source-bound offline Token-2022 privilege census reconciled all 1,234 dev refs across 298
  captured full blocks / 384,459 transactions with zero network requests. Same-slot quote-mint
  privilege partitions are 1,146 readonly-or-absent with no unclassified target touch, 67 with an
  unclassified target touch, 11 with writable-potential evidence only, and 10 with both. Writable
  means potential, not actual modification. All 1,234 retain `historical_extension_state=unavailable`
  and `economic_effects=not_assessed`. Receipt:
  `docs/evidence/pumpswap-buy-exact-v1-token2022-privilege-census-2026-10-10.json`.
- A second offline trace replay covered those 1,146 refs across 285 captured blocks / 366,918
  transactions. It retained exact target-touching wire bytes, instruction paths/stacks, and
  same-slot writable-potential counts for each target account. There were 3,552 Token-2022
  `(first byte 12, length 10)` instructions and four `(9, 1)` variants; those four rows stop at an
  unclassified wire shape. The other 1,142 are structural traces only. All 1,146 remain
  economically unqualified with extension state unavailable. Receipt:
  `docs/evidence/pumpswap-buy-exact-v1-token2022-clean-stratum-2026-10-10.json`.
- Offline replay decoded all 3,552 exact Token-2022 `TransferChecked` wire traces as declared
  atomic u64 plus decimals argument; all pass structure and transaction-status gates. IDL role
  attribution is retained only for 3,438 aligned traces; 114 sibling-parent traces remain
  role-unknown. No realized transfer or economic effect is inferred. Receipt:
  `docs/evidence/pumpswap-buy-exact-v1-token2022-transfer-checked-2026-10-10.json`.
- Fresh Alchemy MCP read-only usage summary: 18,680 CU / $0.01 account-wide month-to-date through
  02:27Z; no account limit reported. The PAYG endpoint variable is absent from process env and
  `.env.local`; no archive query or paid request was made.
- Senses remains incomplete: broad process/market discovery, lifecycle, provider-independent
  completeness and prospective continuity are limited. Preserve unknown/gap/censored/failed/stopped/
  unsupported/immature separately; never convert them to zero.

## Next horizon

1. Check the first future campaign-1 scheduled frame (06:05 MSK / 03:05Z), then its controller
   checkpoint; do not kickstart, backfill missed prospective slots, or duplicate requests.
2. Keep the 67 unknown-program refs, 11 writable-potential refs, and 10 with both reasons separately
   quarantined. Within the 1,146 replay, stop four rows with Token-2022 `(9,1)` wire; use only the
   remaining 1,142 for a later structural evidence gate. Preserve all economic/extension states.
3. Alchemy Account Archive remains unexecutable from this checkout: no PAYG endpoint and no account
   limit/entitlement verified. Do not make paid calls; continue other Senses work and prospective
   collection. No Cortex.
