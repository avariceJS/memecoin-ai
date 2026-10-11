# Current Project State

**As of:** 2026-10-11 03:02 UTC. Recheck host state before any acquisition decision.

**Research checkout:** `/Users/root1/Developer/memecoin-research`
**Branch:** `research/active`
**Latest implementation/evidence commit:** `8ba6eeef3d8acfe41d9962003c9240241006e965` (c4 buyExact pool-v2 development replay); offline controller `4090c8a` is an ancestor.

## Boundaries and runtime

- Continue Senses in this checkout only; production `/Users/root1/Desktop/memecoin-ai` is untouched.
  No trade/signing, paid upgrade, remote push, protected holdout, c4 tail2 opening, backfill, or
  unapproved repeat request.
- User authorized the existing Helius key without rotation. Post-resume exact scan receipt
  `docs/evidence/helius-key-post-resume-exposure-scan-2026-10-11T0218Z.json`: zero matches in 5,961
  local Git objects, 75,127 accessible recent files / 1.26 GB, process arguments and 1.26 GB of
  Unified Log; `.env.local` is owner-only mode 0600. Provider/remote storage, inaccessible backups
  and one unreadable file were outside scan scope; prior disclosure remains in session history.
  Senses runner passes config path only and reads the endpoint in-process.
- All 15 Senses LaunchAgents remain disabled/unloaded. Current free disk is 38,904,756 KiB (~39.8 GB), below both the
  50 GiB floor and c5 composite minimum of 61,834,628,800 bytes. Helius account/charge headroom is
  unknown; the last read-only Helius MCP account calls returned `Transport closed` and were not
  retried. No Helius RPC was made in this continuation; do not retry without a new reviewed lease.
- c4 campaign `work/brain-senses-live-helius-inventory-v3-20261010-4` remains terminally stopped;
  its abnormal STOP and 23:00Z frozen tail2 body stay unchanged/unopened. All prospective Senses
  collectors are stopped; none is accumulating observations.

## Completed this continuation

- c5 finite forward runner/template is commit `4f504cf`; it has source/predecessor/time/disk/process
  gates, path-only credential loading, no ambiguous replay, and fails closed until production account,
  disk/churn and independent-review evidence are source-bound. Template is uninstalled. Fake-only
  focused tests pass; no runtime root, LaunchAgent, endpoint read or provider call occurred.
- Frozen c4 captured-class replay is commit `37134ec`. Receipt
  `docs/evidence/pumpswap-c4-captured-class-replay-2026-10-11.json` independently pins the freeze
  digest, manifest bytes, old repository HEAD and campaign root; the 23:00Z body is excluded. It
  covers 10 frames / 160 selected full blocks, 173,899 transactions, 591,869 resolved instruction
  refs, 22,319 PumpSwap refs, 3,555 buyExact candidates / 3,556 refs. Published 13:00Z and 18:00Z
  census matrices/counts reproduce exactly. This is structural selected-block exposure only, not
  discovery/economic completeness, exact economic zero, settlement qualification or admission.
- Offline prospective controller is committed in `4090c8a`: finite lease with declared source
  hashes, append-only hashed event chain, daily/lease request and byte caps, physical floor and width
  caps, distinct times, one reservation per scheduled bucket, explicit gaps, anchor-only reset,
  no-replay recovery, first-reason STOP, and terminal frontier/gap transfer to a new lease carrying
  a fresh review digest. It has no live adapter, credential loader, scheduler, LaunchAgent or
  activation parser. Focused tests: 20 passed.
- c4 buyExact pool-v2 development replay is commit `8ba6eee`. Receipt
  `docs/evidence/pumpswap-buy-exact-v1-c4-pool-v2-development-replay-2026-10-11.json` binds ten
  dev frames / 160 blocks and all source/capture/toolchain hashes. It partitions 3,556 references as
  2,333 exact 25-byte/valid-bool/3-tail candidates plus 1,223 other refs; the 2,333 comprise 2,317
  SDK structural matches and 16 mismatches. Of the 2,317: 767 are failed attempt-only, 1,550 are
  successful transaction refs; 1,546 remain Token-2022 quarantined and four legacy refs are
  censored (two endpoint missing, two endpoint residuals). Zero pass the existing narrow diagnostic;
  that is not an economic zero. 23:00Z remains unopened.

## Next decision horizon

1. Continue the Sol-approved offline feasibility taxonomy on the same ten c4 dev frames. Separate
   candidate-descendant event/transfer wire facts from whole-transaction endpoint/touch attribution
   and bank-time Token-2022 state availability; trace the four censored legacy refs and three
   successful structural mismatches. No decoder promotion or audit tuning.
2. Before any Helius RPC, obtain a new exact future lease/schedule decision, fresh source-bound
   provider account/charge evidence, disk/churn evidence above the floor, predecessor/quiescence
   proof, and independent reviewer binding. Missed prospective ticks remain gaps; never backfill.
3. Keep c4 terminal and tail2 frozen; continue offline Senses work while acquisition gates are
   absent. Cortex has not started.
