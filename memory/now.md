# Current Project State

**As of:** 2026-10-09 Europe/Moscow. Start from `docs/INDEX.md`; check linked receipts for evidence.

## Goal and boundaries

Build a scalable, reproducible Solana learning system. No demonstrated executable edge. Do not
promote/deploy, sign transactions, use real money, access frozen F01/D43 or reserved holdouts, or
start Cortex before Senses is objectively complete. Keep production
`/Users/root1/Desktop/memecoin-ai` and credentials untouched. Mac 1 is the research host; Mac 2 is
standby. No Grok.

## Active state

- Research checkout: `/Users/root1/Developer/memecoin-research`, branch `research/active`; verify
  live `git status` and HEAD. Do not push. Production checkout/runtime remain unchanged.
- The sensing priority is prospective, broad real-market capture and economic event qualification.
  Existing archive, source, qualification and evaluation limits are in
  [`brain-source-architecture.md`](../docs/brain-source-architecture.md) and
  [`training-evaluation.md`](../docs/training-evaluation.md). Do not reread the whole archive.
- Current capabilities preserve event time, receipt/availability, exact/unknown/gap/failed/stopped/
  unsupported/pending/censored states and never infer death or zero from provider disappearance.
  Native slot coordinates are not converted into UTC decision time. Details live in the canonical
  source and evaluation documents above.
- No fresh independent model comparison, calibrated execution result, market edge or profit claim.

## Providers and durable evidence

- Alchemy Free/PAYG are one provider family; Helius is independent. Helius is configured only in
  ignored mode-0600 `.env.local`; no key goes in Git, launch arguments or logs. Published Helius
  Free allowance is 1M credits/month and 10 RPC RPS; MCP confirms these plan limits but exposes no
  account usage meter. Alchemy Free and Helius endpoints are configured. PAYG selection is supported
  but its endpoint is absent; no paid request has been made.
- The three-range 50-slot pilot found matching slot/block identities and ordered signatures. Of
  56,615 paired transactions, 120 differed only in Helius logs marked `Log truncated`. Each provider
  returned 7,953 PumpSwap structural instruction references, not economic trades. See
  `docs/evidence/helius-public-block-provider-pilot-2026-10-09.json`.
- A new 32-slot Helius Free/Alchemy Free calibration completed 65 calls/provider (65 expected
  Helius credits), with no failures. All inventories and block identities/signature order matched;
  39,280 transaction trees matched and 38 differences were only explicitly truncated Helius logs.
  After fixing valid empty base58 instruction data, both providers qualified 32/32 structural counts.
  Stored zstd averaged 1.14 MB/provider/slot including inventory and signature checks; see
  `docs/evidence/alchemy-free-helius-same-slot-calibration-2026-10-09.json`.
- A larger precommitted three-day retrieval selected 2,880 slots / 144 twenty-slot windows with
  3,168 Helius calls and a 30 GB raw bound. Helius phase is complete; public phase is still running
  (latest observed 2,091/3,168 public requests: 460 captured, 1,631 failed); root is about 16.3 GB.
  The provider process remains active. Offline replay after the empty-base58
  fix parsed/normalized all 2,880 Helius full responses and matched the stored inventory for all;
  only 144 had the committed full/signatures-only self-check, so only those qualify as exact selected
  counts. No historical availability claim. See
  `docs/evidence/helius-historical-instruction-normalization-audit-2026-10-09.json`.
- The first prospective 20-slot Helius/public window had 20/20 Helius full responses and 11/20
  public full responses. All 11 paired blocks agreed on identity and ordered signatures; public
  HTTP 429s/timeouts remain unknown. The 13,716 paired transactions had 65 log-only differences,
  each explicitly truncated by Helius. Helius showed 4,033 PumpSwap structural references; none
  imply executed swaps. See `docs/evidence/helius-public-prospective-block-epoch-2026-10-09.json`.
- Existing prospective 64-slot Helius/public campaign: its 12:00Z epoch used 258 requests / 129
  Helius reservations, parsed 135 and failed 123 (public transport); failures remain unknown. Its
  13:00Z and 14:00Z scheduled invocations exited `ValueError`, with only the exception class logged
  and no epoch roots created. Do not rely on this lane until its scheduler binding/recovery is
  diagnosed. Its 20,000-credit/32-GiB campaign limits remain local bounds.
- First independent-verification epoch completed under LaunchAgent
  `ai.memecoin.brain.senses.independent-verifier-512`: 512/512 selected Helius blocks passed full vs
  signatures-only self-check and qualified exact structural-reference counts (69,311 total; one
  structural zero, not economic zero). Helius inventory listed 13,399 produced slots in its bounded
  frame. Alchemy Free captured 32 same-slot full/signature pairs and matched block identity/order
  32/32, but its range inventory failed at transport and was not retried; Alchemy population coverage
  remains unknown. Among 36,852 paired transactions, 79 log-only differences each had Helius's
  explicit `Log truncated` marker on raw replay. See
  `docs/evidence/helius-alchemy-stratified-512-first-epoch-2026-10-09.json`.
- The epoch used 1,026 Helius attempts including anchor (account meter unavailable), 66 Alchemy Free
  attempts (65 captured, one failed), and no retries/paid requests. It stored 576,376,420 B from
  2,355,735,101 raw bytes, taking 1,411 seconds through qualification. A same-size day projects
  13.83GB stored. Sol directs a complete 24h Helius 512-slot/hour cycle with Alchemy widened to 64
  slots/hour after this epoch; expected Helius use is 24,624/day. The immutable first root was closed
  after its qualified epoch; the fresh pair64 root began at 15:00 UTC under LaunchAgent
  `ai.memecoin.brain.senses.independent-verifier-512-pair64`. Six-hour review gates: Alchemy
  availability, <45-minute qualification time, 18GiB campaign storage cap, 30GiB free-disk floor.
  Shared cap36k reservations includes prior Helius roots. After each healthy day, renew for two more
  24h waves with an intervening review; 512/h and daily credit caps are wave controls, not ceilings.
  Pair64 epoch `20261009T150000Z` completed around 15:25Z and scheduler exited 0: all 512/512
  selected Helius full/signature slots qualified across 16 strata; 1,154 attempts/completions, 1,025
  Helius reservations, 1,153 parsed and one Alchemy `getBlocks` inventory failure (no retry). 64
  Alchemy same-slot pairs were captured, but this summary does not retain independent pair-agreement
  detail; compare them offline before claiming cross-provider match. Raw response bytes 3.012GB,
  stored 695.2MB, epoch folder ~680MB, 73GiB free. `epoch-summary.json` and schedule result are
  durable in ignored `work/`; status is `provider_window_attempted`, not global completeness.
  LaunchAgent is calendar-scheduled hourly (Minute=0, RunAtLoad=false), now idle after clean exit and
  will resume on the next hour. Keep the existing 24h wave under daily gate; do not renew without
  review.
- Helius address-watch v2 stopped after 1,001 requests: 999 transaction bodies captured, one failed;
  a restart recorded terminal `ValueError` and STOP. Do not resume its cursor. New watch v3 uses
  fresh six-hour UTC roots, parent STOP/last-receipt links, no inherited cursor, and a 1,100-request
  cap per epoch; the hourly block sample is the prospective evidence path, while this watch is a
  sparse address-index sensor, not full population coverage. Its 12:00Z v3 epoch finished cleanly
  at the 1,100-attempt cap: 1,099 captures, one failed, explicit STOP `stop_or_budget_during_page`,
  completeness unknown; it is not resumable. Helius and public decode exception-watcher LaunchAgents
  repeatedly log `ValueError` (class only, no message); inspect their recovery before relying on them.
  Program-watch v2/v3 and old Helius-watch jobs are stopped with last exit 0.
- Latest disk check after pair64 epoch: 73 GiB free; no external research volume is mounted.
  The active 64/h raw block campaign is ~6.6GB/day and Helius watch ~0.09GB/day. At 64 Alchemy
  pairs/hour the next Helius wave projects ~14.7GB/day stored. Helius 1,000-credit and 20,000/day
  values are qualification bounds, not durable ceilings.

## Economic semantics and learning

- PumpSwap decoder pins the official updated IDL, including the eight-byte
  `creator_fee_unclaimed: u64` tail. New remaining-account and Token-2022 shapes still require
  evidence, reconciliation and quarantine before they can be called supported. Existing exception
  counts and paths are in `docs/brain-source-architecture.md`.
- A narrow pinned PumpSwap `sell23` diagnostic reconciles exact event fields, checked CPI-subtree
  transfers, fee destinations, and signed pool balance deltas. Frozen ledger v2 adds full-transaction
  legacy-token account lifecycle and sibling effects. SyncNative cannot balance unexplained residuals;
  WSOL settlement after SyncNative is censored until lamport funding/rent/close effects are proven.
  Fresh disjoint dev/audit both matched 50/50: dev 18 exact full settlements, 31 trade-transfer facts
  with censored native-close settlement, one unsupported instruction; audit 20 exact and 30 censored.
  All six event/transfer/fee equations passed on 49/49 qualified dev transfers and 50/50 audit.
  Diagnostic only: no wallet-net price, training/execution admission or coverage claim. Reports in
  `docs/evidence/helius-pumpswap-sell23-ledger-v2-*-qualification-2026-10-09.json`; prior v1 reports
  remain historical. Frozen v2 then censused all 1,255 eligible sell23s in 512 captured Helius full
  blocks: 410 exact full settlements, 843 censored native settlements, two unsupported instructions;
  equations pass on 1,253 qualified transfers. This is captured-class evidence, not global completeness.
- Positive signature references and bounded block agreement do not prove enrollment, support,
  market coverage, executed swaps or lifecycle. No real source fit or learning gain has been
  established. `partial_count_lower_bound_pinball_v1` is opt-in and excludes zero/unavailable
  bounds; see `docs/training-evaluation.md`.

## Verification and next decision horizon

- Focused Ruff/format/mypy (`--follow-imports=skip`) and PumpSwap checks passed after v2 update
  (36 tests); sample replays and captured-class census used zero RPC. The last full `make check`
  reached 1,352 passed / 25 skipped but failed three `tests/tools/test_agent_context.py` cases on
  the mandatory packet budget; it was not rerun in this checkpoint. `make smoke` is synthetic-only
  and is not evidence of edge.
- Compiled transaction normalization has fail-closed static/lookup-address resolution, valid empty
  base58 decoding and exact SPL `TransferChecked` wire decoding; Token-2022 effects remain
  quarantined. Ledger v2 is frozen at `a5b9b02`; census tool is `d2f39b5`; evidence/state is
  `d36ff9a`. Next: offline audit the 64 captured Alchemy same-slot pairs, inspect recovery of the
  hourly public/Helius scheduler and failing decode watchers, and observe the next pair64 scheduled
  epoch. Then adapt the diagnostic to buyExact25 with fresh disjoint development/audit samples.
  The v2 census covers all captured eligible sell23 candidates only. Keep the current pair64 wave
  under its existing daily and disk gates; no renewal or Cortex start until later review.
  Do not call Senses complete or begin Cortex.
