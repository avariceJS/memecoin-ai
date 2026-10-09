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
- Existing prospective campaign is separate and unchanged: 64 seeded Helius slots/hour for 72h,
  Helius signature self-check per selected slot, public paired only at UTC 00/06/12/18, and its own
  20,000-credit/32-GiB wave bounds. First window used 129 Helius reservations, captured 64/64
  Helius blocks, while public had 123 failed attempts among 129; failures remain unknown.
- Sol approved a separate 24h follow-up at 512 Helius slots/hour in 16 seeded contiguous 32-slot
  windows and 32 same-manifest Alchemy Free pairs/hour (768 pairs/day). Expected Helius reservations
  are 24,624 (including hourly inventories and anchors); wave cap26,000. Shared daily research cap is
  36,000 locally accounted standard requests across the new wave and active Helius roots; it is not
  a recurring or architectural limit. New wave stored cap18GiB, per-epoch free-space guard preserves
  30GiB. The denser calibration projects ~14.9GB stored for 24h; a 72h run is not safe alongside
  current roots. Request/byte/disk checks run before each epoch; stop this broad wave first.
- Helius address-watch v2 stopped after 1,001 requests: 999 transaction bodies captured, one failed;
  a restart recorded terminal `ValueError` and STOP. Do not resume its cursor. New watch v3 uses
  fresh six-hour UTC roots, parent STOP/last-receipt links, no inherited cursor, and a 1,100-request
  cap per epoch; the hourly block sample is the prospective evidence path, while this watch is a
  sparse address-index sensor, not full population coverage.
- Latest disk check: 76 GiB free; no external research volume is mounted. The historical root may
  add up to its 30-GB cap; the active 64/h raw campaign is ~6.6GB/day and Helius watch is ~0.09GB/day.
  New-wave 1.14MB/provider/slot measurement projects ~14.9GB/day; its one-day cap and floor guard
  account for all active roots. Helius 1,000-credit and 20,000/day values are qualification bounds,
  not durable ceilings.

## Economic semantics and learning

- PumpSwap decoder pins the official updated IDL, including the eight-byte
  `creator_fee_unclaimed: u64` tail. New remaining-account and Token-2022 shapes still require
  evidence, reconciliation and quarantine before they can be called supported. Existing exception
  counts and paths are in `docs/brain-source-architecture.md`.
- Positive signature references and bounded block agreement do not prove enrollment, support,
  market coverage, executed swaps or lifecycle. No real source fit or learning gain has been
  established. `partial_count_lower_bound_pinball_v1` is opt-in and excludes zero/unavailable
  bounds; see `docs/training-evaluation.md`.

## Verification and next decision horizon

- Focused sensing checks passed after this change. The latest full `make check` reached 1,352 passed
  / 25 skipped but failed three `tests/tools/test_agent_context.py` cases because this file plus
  `AGENTS.md` exceeded the context packet's mandatory 23,500-character budget. Keep this state note
  compact, then rerun `make check`. `make smoke` is synthetic-only and is not evidence of edge.
- Compiled transaction normalization has fail-closed static/lookup-address resolution, valid empty
  base58 decoding and exact SPL `TransferChecked` wire decoding; Token-2022 effects remain
  quarantined. Re-audit the historical captures and finish the current two-provider audit without
  replay. Launch the independent 512/32 Helius↔Alchemy Free wave under its own root after tests and
  commit; review actual bytes, provider failures and discrepancy yield at 24h. Do not call Senses
  complete or begin Cortex.
