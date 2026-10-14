# Current Project State

**As of:** 2026-10-14 06:10Z. Portability/DR remains **NO-GO for factory reset**. Work continues on
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
- Remaining stateful gates include headroom/admin procedure for an owner source view, complete
  owner/protected custody, a real Kingston durable frontier, and compatible-host inert restore.
  `research/work/wallets` remains private, opaque, unresolved, and backup-required. F01/D43/reserved
  remain protected; no semantic access is authorized.
- Fresh read-only APFS check at 06:10Z: Data has about 12 GiB free (98% used) and no snapshots.
  Do not create a source snapshot at this headroom; admin-assisted space/retention qualification
  remains unresolved.
- No provider requests, collector, service, production behavior, protected evaluation, paper state,
  or trading state changed in this horizon.

Canonical DR handoff: `docs/portability-disaster-recovery.md`.
