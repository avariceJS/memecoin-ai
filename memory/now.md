# Current Project State

**As of:** 2026-10-11 00:47 UTC. Recheck runtime before any prospective service decision.

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
  disclosure remains in session history; provider/remote logs and backups are not inspectable.
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
- Latest measured free disk is 49,089,638,400 bytes at 00:43:31Z, below c4's 50 GiB floor;
  do not initialize prospective acquisition on this storage state. c4 allocated 202,719,232 bytes.
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
- The 00:08:11Z external snapshot still proves its contemporaneous process, LaunchAgent and artifact
  state. Its saved `log show` query used UTC wall time interpreted as local time and therefore covered
  the wrong interval; preserve it and never relabel it. Sol approved a later-received corrected
  historical Unified Log query with explicit Europe/Moscow arguments, bound to that first snapshot.
- A corrected read-only diagnostic through 00:33Z returned six expected OS records from
  `UserEventAgent`, `launchd`, and `backgroundtaskmanagementd`; none came from the c4 collector. Two
  background-task records followed my read-only `launchctl print`. Unknown processes and any c4
  script remain fail-closed. This diagnostic is not the final log receipt.
- STOP + 70 minutes is 01:15:00.418Z. Use a 71-minute margin: after 01:16:01Z, create one new
  metadata-only process/LaunchAgent snapshot, one corrected log receipt, then qualify and verify.
  No RPC is needed. If either receipt fails, leave c4 terminal and report incomplete.
- Service v1's normal predecessor gate cannot accept this 11-frame disk STOP. Do not initialize or
  activate a successor until a fresh Sol review binds the abnormal receipt, current disk runway,
  account/quota evidence or conservative cap, anchor-only bootstrap and unknown-ended gap. No
  successor is currently approved.

## Next decision horizon

1. After 01:16:01Z, capture and verify the two external operational observations and corrected local
   log receipt. Keep the wrong-window log portion of the first snapshot explicitly excluded.
2. If metadata-only terminal proof passes, ask Sol for a fresh, bounded successor-service decision
   using the exact receipt, current 49.8 GB free disk, unavailable Helius account MCP, and no-provider-
   backfill constraint. Do not activate while disk/account gates fail.
3. Then resume engineering Senses gates; do not start Cortex. Pair64 comparison is already audited;
   buyExact25 development work remains quarantined to its prior evidence and the c4 tail2 sample is
   closed.

Canonical technical state: `docs/brain-source-architecture.md`; evidence locator:
`docs/evidence/README.md`.
