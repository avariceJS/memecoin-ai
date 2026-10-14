# Current Project State

**As of:** 2026-10-14 01:55Z. Portability/DR is **NO-GO for factory reset**. All DR work through
the LaunchAgent and ignored-state witnesses is published on `research/active`; the host census and
private candidate-path inventory are the current uncommitted scope. `main` remains the verified
baseline. Do not erase the owner Mac.

- Owner checkout was last verified clean at `a594474` on `main`; its cached `origin/main` is 15
  ahead, while live remote main `770699` is 507 commits ahead (verified in research object DB).
  Ancestry is clear; deployed runtime source remains unverified.
  Fresh read-only status: 18 Brain labels
  loaded, eight with PIDs; `ai.memecoin.brain.b09r2.source` was loaded without a PID. There were 72
  cwd-matched processes under owner/research roots. No arguments or environments were read. This
  does not establish the source write frontier or earlier workload.
- LaunchAgent metadata: 55 Brain + one D43 plist; all have `RunAtLoad`; four have five environment
  entries with unclassified values. Their arguments refer to owner/research/config roots through
  three host symlink mappings; keep quarantined definitions inert and do not install definitions
  blindly. Source service state was not changed.
  A later inventory found 60 current plist files total; all 60 are in an opaque Kingston quarantine
  and were hash-verified after isolated inert restore. Four files remain unclassified; no service
  definition was loaded by this DR work. Existing source service state was not changed.
- Private Brain config has 13 directories/75 files (~184 KiB), all files mode 0600; it includes
  versioned activation/config sets, one retired plist set (15 mode-0600 copies), storage health,
  and handoffs. All bytes are now in a provisional Kingston quarantine; they were hashed but not
  parsed or classified. Do not install or activate as live config without private review.
- Current read-only check reports about 12.6 GiB free on Data and 203 GiB on Kingston; no APFS
  snapshot or Time Machine destination exists. Recheck headroom before any copy.
  Noninteractive admin authentication is unavailable; safe snapshot write churn is unmeasured.
  Do not copy live state as a substitute.
- Kingston `/Volumes/Kingston/BrainData` is the one reset-surviving physical copy (~203 GiB free).
  Single-copy failure and plaintext local custody are accepted for this recovery scope.
- A local Codex worktree had 131 reachable commits, including nine absent from cached origin refs.
  A 19.4 MB bundle, 32-file `.local` archive, and second tar with all 10,661 other ignored-status
  entries were written create-only to Kingston and hash-verified after reread. The `.local` copy
  remains provisional because three logs may have changed; ignored-state roles/rebuildability,
  empty dirs, xattrs, and ACLs remain unresolved. All 11 ignored symlinks resolve to regular files
  included and hash-verified in the isolated restore. All three archives were restored
  into temporary quarantine roots and hash-verified; no content was executed or parsed. See the
  custody/restore receipts and canonical DR handoff.
- Metadata-only scans found `owner/work/calendar-execution-01` and basename-prefix candidates under
  owner work (37 D43, 30 F01), research work (15 D43, 81 F01, three reserved), and Kingston (four
  D43, eight reserved). Names do not prove protected role or custody closure. No qualified owner-wide
  recovery set, classified protected custody set, complete Brain restore witness, or full inert
  restore exists; the local opaque-group witness does not qualify the whole Brain. The provisional
  Codex bundle may contain protected-like objects of unclassified role. Provider
  requests were zero for DR work. One later broad tracked-source grep included
  `docs/evidence`; its truncated output was not used, and whether a protected receipt line was printed
  is unresolved. Do not rely on that output.
- Metadata-only census found about 28.6 GB across 615,850 regular files and 126 symlinks under the
  owner/research `work/` trees. Owner-tree apparent bytes fell 594,048 across consecutive full scans;
  a short repeat changed metadata on three owner entries, including a growing log and a SQLite
  file. This proves churn, not a consistent source point. `.env.local` is 0600/470 bytes, unopened
  and unhashed, designated for archive exclusion and credential reissue. See the canonical DR handoff
  and census receipt.
- A private metadata-only inventory of 178 F01/D43/reserved-prefix candidates is now on Kingston and
  was reread/hash-verified through a fresh descriptor-bound verifier. Candidate paths remain private;
  roles and payload custody are unproven. See the canonical handoff and public aggregate receipt.
- Next gate: classify private config, owner/research state, service roots, Codex ignored roles and
  external runtime dependencies; then establish a qualified consistent source point and protected
  opaque custody before a full inert isolated restore. Obtain independent Sol review before any
  erase verdict.

See `docs/portability-disaster-recovery.md` for the canonical recovery procedure and
`docs/evidence/README.md` for the scoped immutable receipts. Brain architecture, source and Senses
status remain owned by the canonical documents listed in `docs/INDEX.md`.
