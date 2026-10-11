# Current Project State

**As of:** 2026-10-11 04:37 UTC. Recheck host, disk, account meter and LaunchAgents before any acquisition decision.

**Research checkout:** `/Users/root1/Developer/memecoin-research`
**Branch:** `research/active`
**Latest Senses commits:** `3e733b9` (anchor frontier guard), `8d757a9` (finite calendar and handoff).

## Boundaries and runtime

- Continue Senses in this checkout only. Production `/Users/root1/Desktop/memecoin-ai` is untouched.
  No trading/signing, paid upgrade, remote push, protected holdout, c4 tail2 access, backfill, or
  unapproved repeated request. User authorizes continued use of the existing Helius key without
  rotation. `.env.local` is owner-only mode 0600; collector passes a private config path and loads
  credentials in process.
- Exposure delta receipt `docs/evidence/helius-key-exposure-delta-2026-10-11T0435Z.json`: zero
  exact matches in 6,055 local Git objects, current process arguments, 124 changed accessible saved
  files / 403,864,321 bytes since 02:19Z, and 1,203,999,439 bytes of subsequent Unified Log.
  1,194 inaccessible files and remote/provider logs, backups, Time Machine and session transcript
  storage were outside this check; prior disclosure remains in session history.
- Host check at 04:35Z: 27,569,748 KiB free (~26.3 GiB), below the 50 GiB acquisition floor; no
  Senses/Helius/inventory LaunchAgent was loaded, although 15 Senses plists remain installed.
  Helius charge headroom is unknown. No provider request or collector process was started. All
  Senses prospective collectors are stopped and accumulating no new observations.
- c4 `work/brain-senses-live-helius-inventory-v3-20261010-4` remains terminal; its 23:00Z tail2
  body stays frozen/unopened. Missed prospective buckets stay gaps.

## Completed this continuation

- Base `d16b904` adds operation-envelope-v2 / child-v3 offline bridge (34 requests/frame) and
  fake-only validation.
- Commits `3e733b9` and `8d757a9` add a caller-driven finite-calendar-v3 lifecycle and independent
  handoff verifier, plus a strict anchor frontier-regression STOP. Handoffs preserve gaps, unknown
  intervals and conservative request/byte accounting across successor generations; a started child
  is never replayed. Calendar accepts no cadence, credential loader, provider factory, scheduler,
  LaunchAgent or activation path. Sol accepted this offline gate. Receipt:
  `docs/evidence/senses-finite-calendar-v3-offline-validation-2026-10-11.json`.
- Focused validation: 82 tests passed across finite calendar, operation envelope and v3 child; Ruff,
  format, compile and diff checks pass. No RPC, live activation or c4/tail2 access.
- buyExact25 remains structurally/evidentially unresolved: four dual-legacy successes censored,
  Token-2022 quarantined, failed transactions attempt-only; no economic stratum is qualified. See
  existing buyExact feasibility receipts; do not open c4 tail2.

## Next horizon

Sol-selected: build a narrow offline activation/lineage preflight, then produce a reviewable packet
only. Verify the complete predecessor handoff chain, bind one exact successor lease/schedule/source/
code/cap set, and durably prevent duplicate successor claims. Require fresh trusted clock/disk
probes, independently checked Helius charge headroom, conservative full-lease storage envelope over
50 GiB, and an externally authenticated named reviewer decision. Hashes/review digests alone are
not authentication. Missing/stale/forged evidence must yield `eligible=false`. No scheduler, provider,
credential or request path; separate Sol activation review remains required. Current disk/headroom
make live acquisition ineligible. Do not begin Cortex.
