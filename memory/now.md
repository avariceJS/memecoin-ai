# Current Project State

**As of:** 2026-10-14 01:12Z. Portability/DR is **NO-GO for factory reset**. Research changes are
published on `research/active`; `main` remains the verified baseline. Do not erase the owner Mac.

- Owner checkout is clean at `a594474`. Fresh read-only status: 18 Brain labels loaded, nine with
  PIDs including `ai.memecoin.brain.b09r2.source`; 55 Brain LaunchAgent files. DR work did not
  start, stop, or change services. Treat the running source service as existing observed state.
- Data currently has 13,239,692 KiB free (~12.6 GiB), no APFS snapshot, and no Time Machine
  destination. Removing our two verified temporary rehearsal clones reclaimed about 569 MiB.
  Noninteractive admin authentication is unavailable; safe snapshot write churn is unmeasured.
  Do not copy live state as a substitute.
- Kingston `/Volumes/Kingston/BrainData` is the one reset-surviving physical copy (~203 GiB free).
  Single-copy failure and plaintext local custody are accepted for this recovery scope.
- Metadata-only path scan located `owner/work/calendar-execution-01`; name matching does not prove
  F01/reserved absence or custody closure. No owner/protected archive, witness, or full inert restore
  exists. Provider requests were zero for DR work. One later broad tracked-source grep included
  `docs/evidence`; its truncated output was not used, and whether a protected receipt line was printed
  is unresolved. Do not rely on that output.
- Next gate: qualified consistent source point with safe headroom, complete state-root/service and
  protected custody classification, then verified backup and inert isolated restore. Obtain a new
  independent Sol review before any erase verdict.

See `docs/portability-disaster-recovery.md` for the canonical recovery procedure and
`docs/evidence/README.md` for the scoped immutable receipts. Brain architecture, source and Senses
status remain owned by the canonical documents listed in `docs/INDEX.md`.
