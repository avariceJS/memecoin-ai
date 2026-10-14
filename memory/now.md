# Current Project State

**As of:** 2026-10-14 14:53Z. Portability/DR remains **NO-GO for factory reset**. The user is
home with Kingston attached; no Recovery reboot/reset occurred. No production, champion, protected
evaluation, paper/trading state, or prospective collector changed.

- `research/active` is the working branch. The bounded root-census implementation and aggregate
  receipt are committed as `614d8cb` (`Add bounded host root boundary census`); this state note is
  the final handoff update. Read `git rev-parse HEAD` for its commit. `main` and `origin/main` remain
  `770699d3ef8558518ae750b6d5d6cab41c2e82c8`; do not move `main`. Push the completed work to
  `origin/research/active` before handoff.
- The 14:51Z sequential metadata-only census published and reread-verified a 299,700,736-byte
  private manifest plus completion marker on Kingston. Nine declared roots and four Git bindings
  were observed, with no external Git metadata roots. The census is incomplete because one owner
  checkout directory changed metadata during traversal. Do not rerun against the live host or
  infer a common source point, root closure, custody, or erase readiness. Private manifest locator
  and hashes are recorded in
  `docs/evidence/portability-dr-root-boundary-census-2026-10-14T1451Z.json`; exact drift path stays
  in the private manifest. Sol accepted the scanner for this bounded census only.
- Data is UUID `430740ca-42e7-4e6c-a07a-e4e7370152cc`, device `disk3s5`, mounted at
  `/System/Volumes/Data`; latest `df` showed 13,252,360 KiB free (97% used). Kingston is mounted at
  `/Volumes/Kingston`, UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`, root ID
  `9428695af6d4f46c08e1a444cdc66de8`; latest verified free space is 216,588,333,056 bytes
  (about 201.7 GiB). Kingston is the only reset-surviving physical copy.
- Read-only runtime at 14:53Z: 18 Brain/D43 launchd labels loaded, eight with PIDs. This is not
  inactivity or write-frontier evidence. Recovery mount history / real Data T, complete owner and
  protected custody, Kingston K lineage, and authenticated clean-host inert restore remain open.
- **Next decision horizon:** user-assisted macOS Recovery mount-history and Locked-state rehearsal,
  then Sol-reviewed real read-only Data-view/T design. Do not repeat the live root census; resolve
  its owner-checkout metadata drift from a qualified source view. The user may need to perform the
  Recovery reboot in the next session. Keep services inert and retain NO-GO.
