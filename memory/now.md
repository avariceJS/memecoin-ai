# Current Project State

**As of:** 2026-10-11 07:30 UTC. Research work is on `research/active` in
`/Users/root1/Developer/memecoin-research`; production `/Users/root1/Desktop/memecoin-ai` is
untouched. Check Git and runtime again before any acquisition.

## Runtime and boundaries

- The user authorizes the existing Helius key without rotation and accepts the prior process-list
  exposure. Do not print it. The 05:26Z broad scan found no exact credential matches in 6,146 Git
  objects, 65 launchd plists, current process args, 14,449 recent accessible files / 390.8 MB,
  `~/.npm` / 4.66 GB, shell files or 874 MB Unified Log. `.env.local` is mode 0600; the collector
  resolves the URL in-process and launchd arguments carry only a config path. Remote/provider data,
  backups, inaccessible files and session storage remain outside scan visibility. Receipt:
  `docs/evidence/helius-key-broad-exposure-rescan-2026-10-11T0526Z.json`.
- Helius MCP account-status retry returned `Transport closed` at 07:27Z. Account plan, usage, reset,
  prepaid balance, overage and autoscaling settings are unknown. Public billing docs conflict:
  reserve 10 credits per archival `getBlock`/`getBlocks` and 1 per `getSlot` for offline capacity
  scenarios only; this is neither actual billing nor a ceiling. Do no provider schedule planning
  until an official account-specific usage/settings export or written support clarification and
  fresh disk/full-runway evidence exist. Alchemy MCP returned an account-wide 18,680 CU / $0.01
  usage summary for MTD, 7d and 30d through 07:27Z, but omitted its limit and does not attribute
  usage to this research. It does not establish headroom. Canonical detail and source links are in
  `docs/brain-source-architecture.md`.
- At 07:27Z internal APFS had 41,669,092 KiB free (~39.74 GiB), still below the reviewed 50
  GiB floor. External Kingston ExFAT has 164.6 GB free (~153.3 GiB) but is not eligible under the
  durable collector contract: permissions, hard-link seals, fsync, flock, mount identity and
  unplug/recovery are unqualified. Sol requires separately authorized disposable volume tests and
  owner intent; no contents were inspected and no writes were made. No Senses/Helius
  collector LaunchAgent is loaded. Helius MCP processes are present (pinned `2.2.0` and `@latest`);
  the latter are tool-host processes, not the Senses collector. Current process arguments had zero
  exact matches against the private endpoint. c4 is terminal; no successor, RPC, backfill or retry was
  started. Its frozen 23:00Z tail2 body remains unopened; missed prospective intervals remain gaps.
  Do not touch protected holdouts, trade, sign, push, or begin Cortex.

## Qualified sensing evidence

- Offline calendar, operation-envelope-v2/child-v3, initial-entry, supervisor and signed
  activation-preflight dependencies were requalified at current source pins: 239 tests; activation
  remains false. No service consumes the offline candidate. Aggregate receipt:
  `docs/evidence/senses-dependent-gates-requalification-2026-10-11.json`.
- Pair64 offline audit: 64/64 sampled Helius/Alchemy pairs match block identity and ordered
  signatures. Of 80,467 paired transactions, 146 differ only in Helius logs with explicit truncation
  markers. Alchemy inventory failed once; unsampled slots and global discovery remain unknown.
  Receipt: `helius-alchemy-pair64-offline-audit-2026-10-09.json`.
- Added c4 structural novelty replay over the ten included frames: 160 full blocks, 173,899
  transactions and 22,319 PumpSwap instruction refs reconcile to frozen per-slot counts and
  buyExact censuses; 1,336 wire-family and 19,751 exact fingerprint classes are detector keys, not
  layout/economic-process counts. The compressed detail binds capture receipts, event/receipt times,
  and class fingerprints; tail2 is excluded. No completeness/admission claim. Receipt:
  `pumpswap-c4-structural-novelty-v1-2026-10-11.json`.
- PumpSwap `sell23` captured-class ledger census remains narrow evidence. `buyExact25` is not
  economically qualified: failed attempts, Token-2022 and four censored legacy settlements remain
  quarantined/censored. Frozen c4 tail2 is not an audit sample.
- c4 metadata-only STOP and storage packets preserve terminal/gap accounting. Latest host receipt:
  `senses-storage-readiness-2026-10-11T071800Z.json`; ~388 MB/day is only a 10-frame mean, not a
  forward storage guarantee. No cleanup or external-volume writes were done.

## Next horizon

Keep runtime inactive while account and storage gates are closed. Continue useful offline Senses
engineering and evidence reconciliation; do not repeat captured provider requests or reopen c4.
After account-specific billing controls and fresh disk/runway evidence, seek separate Sol review of
a new forward-only lease before activation. Keep `memory/now.md` short and record detailed evidence
in the canonical source document/immutable receipts.
