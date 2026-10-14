# Current Project State

**As of:** 2026-10-14 06:57Z. Portability/DR remains **NO-GO for factory reset**. Work continues on
`research/active`; do not erase the Mac. The owner-Mac-loss scope assumes Kingston remains healthy.
Kingston is the sole reset-surviving physical copy; its single-copy and plaintext risks are accepted.

- A plain-text first-host code entry card is published on Kingston at
  `BrainData/host-recovery/ENTRY.txt`. Its pinned source revision is
  `edf2898ba4fb877ed8bee159fc9673754f7e12e4`; card SHA-256 is
  `ac8859ee4fc499ffe55dc1824be11a55fd4475fee2dbe0cae749a7f6cbbb5b76`. Verify that hash before
  following any card instruction and keep both pins in an
  independent handoff. Pins are in Git and this chat; a separate persistent external handoff has
  not been created. The card checks Kingston identity read-only, fetches and compares the exact
  source, then invokes the existing inert bootstrap helper.
- Clean temporary-HOME rehearsal with a local bare Git fixture reached the exact commit in both
  checkouts, rebuilt locked dependencies, and passed smoke. Wrong commit, wrong Kingston UUID pin,
  missing Git authentication, and preexisting target were blocked. This is code bootstrap only;
  physical clean-host restore and authenticated GitHub bootstrap remain unproven. Receipt:
  `docs/evidence/portability-dr-first-host-entry-rehearsal-2026-10-14T0610Z.json`.
- The synthetic v4 inert tree restore remains Sol-accepted for disposable fixture bytes/topology
  only. It does not cover owner state, real T, Kingston custody, or erase readiness.
- A third pinned execution-kit candidate on Kingston has no unresolved symlinks or Mach-O dependency
  paths in its static audit. From the exact read-only image, synthetic capture, inert topology
  restore, and interruption cleanup passed with `/opt/homebrew` denied. This remains current-host-only;
  Recovery and compatible-host restore are unproven. Sol accepted this narrow result. Receipts:
  `docs/evidence/portability-dr-offline-execution-kit-2026-10-14T0630Z.json`.
  `docs/evidence/portability-dr-offline-execution-kit-closure-2026-10-14T0637Z.json`.
  `docs/evidence/portability-dr-offline-execution-kit-capture-restore-2026-10-14T0640Z.json`.
- Read-only Git metadata found a clean owner Codex worktree branch with nine local commits beyond
  its cached local upstream ref. Opaque history is preserved in a verified Kingston bundle; commit
  payloads were not semantically inspected and may include protected-like material. The ignored
  `apps/control-center/.local` subtree is 2.9 MB. A prior Kingston tar exists and was hash-rechecked,
  but remains an uncoordinated provisional copy; current live state lacks a qualified immutable-T
  capture. Its role remains private/opaque/backup-required. Receipt:
  `docs/evidence/portability-dr-ignored-root-and-branch-custody-2026-10-14T0645Z.json`.
  Corrections and archive recheck:
  `docs/evidence/portability-dr-ignored-root-claim-correction-2026-10-14T0651Z.json`,
  `docs/evidence/portability-dr-codex-local-archive-recheck-2026-10-14T0650Z.json`.
- Named owner/research/config/service/Codex roots and Kingston `BrainData` are confirmed as directory
  entries on their current pinned APFS volumes. This does not close descendants or the offline T.
  Recovery compatibility/mount-history work now depends on a user-assisted session; metadata root
  closure can proceed independently. Receipt:
  `docs/evidence/portability-dr-root-entry-identity-2026-10-14T0647Z.json`.
- The current Data volume reports FileVault enabled. A candidate Recovery first-action gate would
  require the pinned UUID, encrypted/Locked state, and no mountpoint before any unlock/mount command,
  then use interactive `-nomount` and explicit read-only mount. Sol says this is necessary but not
  enough to prove history; record shutdown/authentication, inspect boot mount logs/gaps, enumerate
  all aliases before unlock, then verify still-unmounted after `-nomount`. If history is unavailable,
  retain it as an assumption. This is not rehearsed or accepted. Receipt:
  `docs/evidence/portability-dr-recovery-locked-preflight-2026-10-14T0653Z.json`.
- Fresh read-only owner runtime query: 18 Brain/D43 launchd labels loaded, eight with PIDs; no
  service changes. Data is FileVault-enabled, unlocked/mounted under the live OS, 13,371,820 KiB
  free (97% used), with no APFS snapshots. Kingston is mounted read/write at its pinned UUID with
  211,803,256 KiB free (~202 GiB). Do not create a source snapshot at current Data headroom.
- Current Git: `research/active` and `origin/research/active` at
  `d15c1e825d088d3c2fe75b7e297e13de8dae160b`; the two documentation/evidence commits for this
  horizon are pushed and the worktree is clean. `main` and cached `origin/main` remain at
  `770699d3ef8558518ae750b6d5d6cab41c2e82c8`; main has not moved.
- Remaining stateful gates include a qualified source view/history boundary, complete owner/protected
  custody, a real Kingston durable frontier, and compatible-host inert restore.
  `research/work/wallets` remains private, opaque, unresolved, and backup-required. F01/D43/reserved
  remain protected; no semantic access is authorized.
- Fresh read-only APFS check at 06:15Z: Data has about 12.8 GiB free (97% used) and no snapshots.
  Do not create a source snapshot at this headroom; admin-assisted space/retention qualification
  remains unresolved.
- No provider requests, collector, service, production behavior, protected evaluation, paper state,
  or trading state changed in this horizon.

Canonical DR handoff: `docs/portability-disaster-recovery.md`.
