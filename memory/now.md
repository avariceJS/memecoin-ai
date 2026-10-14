# Current Project State

**As of:** 2026-10-14 01:24Z. Portability/DR is **NO-GO for factory reset**. Earlier DR work is
published on `research/active`; latest read-only handoff refresh is pending review/commit. `main`
remains the verified baseline. Do not erase the owner Mac.

- Owner checkout was last verified clean at `a594474` on `main`; its cached `origin/main` is 15
  ahead, while live remote main `770699` is 507 commits ahead (verified in research object DB).
  Ancestry is clear; deployed runtime source remains unverified.
  Fresh read-only status: 18 Brain labels
  loaded, eight with PIDs; `ai.memecoin.brain.b09r2.source` was loaded without a PID. There were 72
  cwd-matched processes under owner/research roots. No arguments or environments were read. This
  does not establish the source write frontier or earlier workload.
- LaunchAgent metadata: 55 Brain + one D43 plist; all have `RunAtLoad`; four have five environment
  entries with unclassified values. Their arguments refer to owner/research/config roots through
  three host symlink mappings; keep services inert and do not copy definitions blindly.
- Current read-only check reports about 12.6 GiB free on Data and 203 GiB on Kingston; no APFS
  snapshot or Time Machine destination exists. Recheck headroom before any copy.
  Noninteractive admin authentication is unavailable; safe snapshot write churn is unmeasured.
  Do not copy live state as a substitute.
- Kingston `/Volumes/Kingston/BrainData` is the one reset-surviving physical copy (~203 GiB free).
  Single-copy failure and plaintext local custody are accepted for this recovery scope.
- Metadata-only scans found `owner/work/calendar-execution-01` and basename-prefix candidates under
  owner work (37 D43, 30 F01), research work (15 D43, 81 F01, three reserved), and Kingston (four
  D43, eight reserved). Names do not prove protected role or custody closure. No owner/protected
  archive, witness, or full inert restore exists. Provider requests were zero for DR work. One later broad tracked-source grep included
  `docs/evidence`; its truncated output was not used, and whether a protected receipt line was printed
  is unresolved. Do not rely on that output.
- Next gate: qualified consistent source point with safe headroom, complete state-root/service and
  protected custody classification, then verified backup and inert isolated restore. Obtain a new
  independent Sol review before any erase verdict.

See `docs/portability-disaster-recovery.md` for the canonical recovery procedure and
`docs/evidence/README.md` for the scoped immutable receipts. Brain architecture, source and Senses
status remain owned by the canonical documents listed in `docs/INDEX.md`.
