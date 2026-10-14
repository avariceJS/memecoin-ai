# Current Project State

**As of:** 2026-10-14 18:43Z. Portability/DR remains **NO-GO for factory reset**. The user is
home with Kingston attached and may perform the Recovery rehearsal later; no reboot/reset occurred.
Production, champion, protected evaluation, trading, owner services, provider traffic, and prospective
collectors were not changed in this session.

- `research/active` is the working branch. Code and rehearsal evidence are pushed through
  `b6b7234b22d83c7915c0b09537deba3abc851b78`; this note is the final handoff update. Read
  `git rev-parse HEAD` for the note commit. `main` and `origin/main` remain at
  `770699d3ef8558518ae750b6d5d6cab41c2e82c8`; do not move `main`.
- Exact-commit bootstrap now isolates Python venv/install/check/smoke from host pip config/cache and
  Python user-site state. Same-Mac local-bare rehearsal at `8544c4d82ebc7dbf277cab5e68715e279f2b267b`
  passed clean checkout, exact HEAD, unchanged lock SHA, `pip check`, smoke, and poisoned-host-Python
  checks; unknown commit and existing destination failed closed. Git clone/fetch and the initial
  Python-version probe intentionally use caller environment for authentication/tool selection.
  Receipt: `docs/evidence/portability-dr-isolated-bootstrap-rehearsal-2026-10-14T1836Z.json`.
  This is code-bootstrap evidence only, not Brain-state restore, clean-Mac proof, or erase readiness.
- Latest read-only host status at 18:41Z: macOS 15.5 / arm64, Python 3.13.6, Git 2.39.5; 18
  launchd labels loaded, eight with PIDs, and 74 processes matched configured workspace roots.
  Only labels and process metadata were read (no arguments/environments); this is a point-in-time
  observation, not inactivity or write-frontier evidence.
- Kingston root is currently verified at `/Volumes/Kingston/BrainData`, volume UUID
  `8f8f1081-9be4-4914-9111-75f1b90255bf`, root ID `9428695af6d4f46c08e1a444cdc66de8`, with
  216,590,835,712 bytes available (~201.7 GiB). It remains the only reset-surviving physical copy.
- The 14:51Z host root census remains incomplete (`scan_complete=false`) due owner-checkout metadata
  drift; do not infer source consistency or custody closure from it. Recovery mount history / real
  Data T, complete owner and protected custody, K lineage, and authenticated clean-host inert state
  restore remain open. Preserve NO-GO and leave all current services untouched.
- **Next decision horizon:** user-led macOS Recovery first-action Locked/no-mount check and mount-history
  rehearsal; stop if Data is unlocked or already mounted. Then obtain Sol-reviewed real Data-view/T
  design and continue only from a demonstrable durable boundary. No factory reset until separately
  qualified restore readiness is established.
