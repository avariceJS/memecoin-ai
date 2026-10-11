# Current Project State

**As of:** 2026-10-11 02:46 UTC. Recheck host state before any acquisition decision.

**Research checkout:** `/Users/root1/Developer/memecoin-research`
**Branch:** `research/active`
**Latest implementation commit:** `4090c8afd229e3c7a5bd85677f85cb30f4f1b78e`; c4 replay `37134ec` and c5 runner `4f504cf` are its parents.

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
- No Senses LaunchAgent is loaded. Current free disk was 40,392,872 KiB (~40.4 GB), below both the
  50 GiB floor and c5 composite minimum of 61,834,628,800 bytes. Helius account/charge headroom is
  unknown; the last read-only Helius MCP account calls returned `Transport closed` and were not
  retried. No Helius RPC was made in this continuation.
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

## Next decision horizon

1. Commit the credential exposure receipt/index and current handoff; keep the c4 terminal and tail2 frozen.
2. Route the next buyExact economic-contract decision to Sol with the new captured-class census and
   clearly separated dev/audit populations. Do not open frozen tail2 or tune on it.
3. Before any Helius RPC, obtain a new exact future lease/schedule decision, fresh source-bound
   provider account/charge evidence, disk/churn evidence above the floor, predecessor/quiescence
   proof, and independent reviewer binding. Missed prospective ticks remain gaps; never backfill.
4. Continue offline Senses work while these external gates are absent. Cortex has not started.
