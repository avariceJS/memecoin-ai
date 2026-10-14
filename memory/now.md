# Current Project State

**As of:** 2026-10-14 03:15Z. Portability/DR is **NO-GO for factory reset**. Published work remains
on `research/active` through `c8957de`; `main` remains the verified baseline. Do not erase the
owner Mac. A Sol-reviewed synthetic frontier scaffold is being prepared on top; it is not a real
source cutoff, Kingston corpus proof, or full restore.

- Owner checkout was last verified clean at `a594474` on `main`; its cached `origin/main` is 15
  ahead, while live remote main `770699` is 507 commits ahead (verified in research object DB).
  Ancestry is clear; deployed runtime source remains unverified.
  At 02:43Z, read-only status found 18 combined Brain/D43 labels loaded, nine with PIDs, and 72
  cwd-matched processes under owner/research roots. Three owner Python paper/operator processes were
  among them. No arguments or environments were read. This is a time-bounded observation and does not
  establish the source write frontier or earlier workload.
- LaunchAgent metadata: 55 Brain + one D43 plist; all have `RunAtLoad` (54 true, two false), with
  five environment-key names across four definitions; values were not inspected or emitted. Four
  additional Steam, Zoom, and Homebrew PostgreSQL definitions are classified as host-software
  services. All 60 are
  quarantined on Kingston and were isolated-restored; none was installed or loaded by DR. Source
  service state was not changed.
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
- A private Kingston capture of the Helius wrapper and Codex/Cursor MCP configuration was hash-verified
  and restored in isolated quarantine. The wrapper hardcodes the research checkout `.env.local`;
  owner and research `.env.local` files were excluded from this capture and credentials must be
  reissued. MCP auth/session state was not opened. Configuration values remain private/potentially
  sensitive; captured tooling remains inert pending review, host-path rebinding, and separate use
  authorization.
- An empty-environment clean-home bootstrap stopped before checkout because the private Git remote
  requires account authentication. No credential or auth store was copied; the temporary home was
  removed. User login is an explicit clean-host restore step; a fully authenticated bootstrap remains
  unproven. See the canonical DR procedure and auth-boundary receipt.
- A private Kingston root/dependency map now records 657 selected entries and value-filtered paths
  for all 60 LaunchAgents. The four extra definitions are Steam/Zoom/Homebrew host services, not
  Brain services. Extended attributes are present on many recorded entries and remain unclassified;
  recursive work path topology is now mapped, but semantic role/custody and a consistent source
  boundary are still missing. See the canonical DR handoff and root/dependency receipt.
- A private recursive metadata-only inventory now covers 320,989 owner and 411,217 research `work/`
  entries under sensitive-subtree pruning and reconciles all 166 owner/research protected-name
  candidates against the earlier path list. It reads no payload bytes and does not establish
  consistency or custody. Three sensitive-name research directories were pruned; the earlier
  census deltas of 21 files and three directories are not directly comparable or evidence of
  deletion. Only private inventory revision `a3` is eligible; `a1` and `a2` were transparently
  invalidated. See the canonical DR handoff and recursive inventory receipt.
- Selected LaunchAgent path fields now reconcile to that private map: all four private-config refs
  match; 203 of 215 owner/work refs match and 12 absent log destinations are confirmed; no paths
  point into research/work. This still does not establish which runtime source revision executed.
- Sol independently reviewed the published `beac499` handoff and confirms NO-GO under the accepted
  Kingston/plaintext scope. The 02:43Z status is time-bounded, not a source frontier; next material
  gate is admin-assisted qualified source view, measured snapshot headroom, complete custody, and
  isolated inert restore.
- An allowlisted pass read the current owner operator `deployment.json` and its bundle `manifest.json`
  only. The documented `{bundle,pin}` pin matches the manifest hash and both files match the private
  recursive inventory; the running process image and seven other running Brain code identities remain
  unresolved. At 02:50Z, 17 Brain labels were loaded/eight running and one D43 label was loaded with
  no PID. See the canonical DR handoff and pointer receipt.
- A metadata-only pass over the eight then-running Brain labels matched five declared nonprotected
  plist working directories to the private recursive inventory; three sensitive/protected-candidate
  entries were excluded without retaining paths. Sol reviewed both this pass and the prior operator
  deployment-pointer scope; neither proves process/code identity or changes NO-GO. At 02:55Z, 17 Brain
  labels were loaded/eight with PIDs and one D43 label was loaded with no PID. See the canonical DR
  handoff and active service-root receipt.
- A metadata-only count of three research directories named `wallets` found 21 files and three
  subdirectories, matching the earlier census delta. Child names were enumerated transiently but not
  retained or emitted; payloads were not read. Contents and role remain unknown; this sensitive
  state is not in recovery custody and blocks erase pending user classification or an authorized
  custody design.
- Current engineering slice: synthetic envelope scaffold pins the existing recovery set/witness,
  verifies a synthetic K object and STOP state, and keeps all fixture-root reads in pytest temp
  space. Sol accepts only this narrow scope. The immutable receipt and canonical DR handoff record
  its limitations. Next priority remains an admin-assisted source view with safe snapshot/headroom,
  followed by resolved wallets, complete opaque custody and full inert restore.

See `docs/portability-disaster-recovery.md` for the canonical recovery procedure and
`docs/evidence/README.md` for the scoped immutable receipts. Brain architecture, source and Senses
status remain owned by the canonical documents listed in `docs/INDEX.md`.
