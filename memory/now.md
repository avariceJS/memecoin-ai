# Current Project State

**As of:** 2026-10-11 03:29 UTC. Recheck host state before any acquisition decision.

**Research checkout:** `/Users/root1/Developer/memecoin-research`
**Branch:** `research/active`
**Latest implementation/evidence commit:** `14ff00843be688626ccf942dfb3a5b8f98be1830` (independent controller-v1 lease verifier).

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
- Read-only host check at 03:26Z found all 15 Senses LaunchAgents disabled, no collector process, and
  34,877,620 KiB free (~35.7 GB), below both the 50 GiB floor and c5 composite minimum of
  61,834,628,800 bytes. Helius account/charge headroom is
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
- The Sol-approved c4 evidence-feasibility taxonomy is committed as `50efc1a`. It
  source-binds all 2,333 candidate traces across the same 160 full blocks. Transaction-tree ancestry
  identifies 1,621 exact BuyEvent wire descendants (1,553 successful and 68 failed attempts), with
  roles and four instruction arguments matching; this remains wire/event evidence only. It records
  6,392 legacy and 1,710 Token-2022 descendant TransferChecked operands plus outside-subtree transfer
  wires. The four successful dual-legacy rows reproduce two missing user-quote endpoints and two
  explicit user-base/user-quote boundary residuals. Bank-time mint/account/Token-2022 extension state
  is absent. Receipt: `docs/evidence/pumpswap-buy-exact-v1-c4-evidence-feasibility-2026-10-11.json`;
  full row output is compressed under ignored `work/` and hash-bound by the receipt.
- Sol decided no buyExact25 economic stratum is qualified. Keep all four rows censored, the 68
  failed transactions attempt-only, and Token-2022 quarantined. A future four-frame event/wire audit
  could test only that narrower claim under a separately approved lease; do not freeze an economic
  audit until transaction-position effects and outside touches reconcile.
- Independent read-only prospective controller-v1 verifier is commit `14ff008`. Receipt
  `docs/evidence/senses-prospective-verifier-v1-2026-10-11.json` binds verifier, controller and test
  hashes; 18 verifier + 20 controller cases pass. It verifies local canonical/hash-chain evidence,
  crash prefixes, capture identity, accounting, gaps, STOP and terminal transfer, but does not
  authenticate source/reviewer/provider/disk truth or authorize activation. v1 is one request/capture
  per bucket and is explicitly unsuitable for the existing 34-request v3 frame.

## Next decision horizon

1. Sol-selected next gate: build an offline versioned operation-envelope/child-bundle bridge between
   durable lease ownership and the existing v3 frame journal. A frame has 34 child attempts: one
   anchor, one inventory request, 16 full blocks, and 16 signature checks. Bind all child plans,
   request identities, receipts, captures, selection, summary and recomputed raw/stored totals before
   parent seal. Fault-inject dispatch/fsync/seal crashes; no started child request may replay. One
   anchor and one complete fake frame must reconcile. Mutate/drop each child record and reject. Keep
   v1 bytes unchanged; new persisted semantics need a new contract identity. Sol review is required
   after implementation; no Astra question is indicated.
2. This is offline fake-client work only: no runtime, RPC, c4/tail2 access, activation, backfill, or
   new lease. Any later Helius call still needs an exact schedule/lease, fresh source-bound account
   and charge evidence, disk/churn evidence above floor, predecessor/quiescence proof and independent
   review. Missed prospective ticks remain gaps; never backfill.
3. Keep c4 terminal and tail2 frozen; continue offline Senses work while acquisition gates are
   absent. Cortex has not started.
