# Current Project State

**As of:** 2026-10-11 01:17 UTC. Recheck runtime before any prospective service decision.

**Research checkout:** `/Users/root1/Developer/memecoin-research`
**Branch:** `research/active`
**Latest implementation commit:** `572b728ae7afb1664c9d166060aa8ffbbdb8d923`.

## Boundaries and runtime

- Continue Senses in the research checkout only. Production `/Users/root1/Desktop/memecoin-ai` remains
  untouched. No trades, signing, paid upgrades, protected holdouts, push, retry, or backfill.
- User authorized continued use of the current Helius key without rotation. The 00:47Z delta scan
  found no exact matches in all 5,905 local Git objects, 83 files / 322 MB changed since the prior
  broad scan, current process arguments, or 284 MB of recent Unified Log; `.env.local` remains
  owner-only mode 0600. The existing transport is path-only for LaunchAgents and environment-based
  for the pinned MCP process, with no secret in argv. Receipt:
  `docs/evidence/helius-key-post-resume-exposure-scan-2026-10-11T0047Z.json`. Prior process-list
  disclosure remains in session history; provider/remote logs and backups are not inspectable. A
  post-commit exact Git recheck covered 5,913 objects with zero matches.
- c4 is naturally and immutably stopped at 00:05:00Z with
  `minimum_free_disk_floor_plus_frame_envelope`, after 11/24 successful hourly frames. Keep STOP,
  frames, reservations, journals, and captures unchanged. No c4 LaunchAgent or other Senses
  LaunchAgent is loaded; no prospective collector is currently accumulating observations. All 15
  Senses plists in `~/Library/LaunchAgents` now have persistent disabled overrides. The exact c4
  label was disabled after finding its plist still present without an override; it was unloaded
  before and remains unloaded. Receipt: `docs/evidence/helius-c4-launchd-quiescence-2026-10-11.json`.
- c4 frame-only accounting is 374 requests, 769,657,370 raw bytes and 177,576,450 stored bytes.
  Bootstrap-only is 1 request, 43 raw / 56 stored bytes. Campaign all-in is 375 requests,
  769,657,413 raw / 177,576,506 stored bytes. Provider charge units and account headroom remain
  unknown. Receipt: `docs/evidence/helius-inventory-v3-c4-abnormal-stop-accounting-2026-10-11.json`.
- Latest measured free disk is 47,631,126,528 bytes at 01:18:04Z, 6,055,964,672 bytes below the
  50 GiB floor; do not initialize prospective acquisition on this storage state. `work/` occupies
  about 26 GB; no external data volume is mounted. c4 allocated 202,719,232 bytes.
  The prior 00:08Z sizing snapshot had 614,572,928 bytes over the old floor-plus-frame gate; that is
  not current runway. The user cache is not disposable project storage.
- Helius MCP tools are listed, but the last read-only `getHeliusPlanInfo` and `getAccountStatus`
  calls returned `Transport closed`; they were not retried. No account plan/limits are established.

## Frozen audit and terminal qualification

- The frozen c4 PumpSwap buyExact tail2 audit ended incomplete at 1/4 eligible frames. The 23:00Z
  payload remains unopened and has no qualified estimate. Receipt:
  `docs/evidence/pumpswap-buy-exact-tail2-c4-incomplete-outcome-2026-10-11.json`.
- Sol approved a separately versioned abnormal terminal receipt that validates control, accounting,
  provenance and stopped state from metadata only. It must not open/decompress response bodies or
  claim a replayed structural census; it grants no service activation. Implementation and focused
  tests are in commit `572b728`.
- Real offline replay reconciled all 11 frame summaries and bootstrap with a guard that raises on any
  capture-body read: 374 frame requests + one bootstrap, exact frame/all-in raw and stored byte
  denominators, frontier 455430160, no open reservations, and an unknown-ended continuation. Focused
  tests: 15 passed; Ruff, format, and `py_compile` passed. Synthetic test receipt SHA-256:
  `d3d8832a0777985e3c6fbf49bade649d9225abc0a21217c72e8693cd19c369bd` in ignored `work/evidence/`.
- The original 00:08:11Z snapshot remains a process/LaunchAgent/artifact fact only; its wrong-window
  log portion is excluded. At 01:16Z the metadata-only abnormal terminal receipt completed and
  replayed successfully. The corrected local log interval is 00:05:00Z–01:16:26Z with nine
  allowlisted OS events; paired snapshots show no c4 process, Senses writer or loaded Senses label.
  Canonical receipt:
  `docs/evidence/helius-inventory-v3-c4-abnormal-terminal-2026-10-11.json`, SHA-256
  `8b97dd4936ddcd6e8494d35e06cb2a86d01929589e4c4c4c5ff7ba91825cbe65`. This does not qualify
  response bodies, prove provider-side silence, close the unknown-ended gap or authorize a
  successor. Provider RPC after STOP remains zero.
- The frozen c4 buyExact tail2 audit remains incomplete at 1/4; the 23:00Z body stays unopened.
  Helius account/credit status is still unknown after the prior MCP `Transport closed`; do not
  retry it without policy. User authorized this key without rotation but did not authorize paid
  upgrades. Service v1's normal predecessor gate still cannot accept this abnormal 11-frame STOP.
- Obtain a fresh Sol successor decision with the terminal receipt, current 47.63 GB free disk,
  unknown provider headroom, anchor-only bootstrap and explicit unknown-ended gap. No Helius
  successor or WSS session is approved; the 50 GiB floor fails by 5.64 GiB.

## Next decision horizon

1. Ask Sol for a fresh bounded successor/horizon decision using the exact abnormal receipt,
   current disk runway, unavailable account-limit evidence and the no-backfill constraint.
2. Continue offline Senses engineering in parallel. Pair64 comparison is already audited; keep the
   c4 tail2 audit closed. Do not start Cortex or activate acquisition until the fresh Sol and
   resource gates pass.

Canonical technical state: `docs/brain-source-architecture.md`; evidence locator:
`docs/evidence/README.md`.
