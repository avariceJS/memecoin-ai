# Current Project State

**As of:** 2026-10-14 04:51Z. Portability/DR remains **NO-GO for factory reset**. Published work
is on `research/active` through `89ff00f`; `main` remains at the verified baseline
`770699d3ef8558518ae750b6d5d6cab41c2e82c8`. Do not erase the owner Mac.

- Accepted recovery case is owner-Mac loss with healthy Kingston. Kingston is the sole
  reset-surviving physical copy; single-copy/plaintext risk is accepted. Its last checked free space
  was about 203 GiB. Internal Data had about 12.6 GiB free, no APFS snapshots or Time Machine
  destination, and no noninteractive admin path. No qualified owner-wide source cutoff exists.
- Three research `work/wallets` directories contain 21 files and three subdirectories by metadata
  count. Their role and contents remain unknown; payloads are outside custody and must not be
  opened, copied, or classified by name. Owner/protected custody closure and complete inert restore
  remain unproven. F01/D43/reserved status stays protected.
- Earlier read-only status observed 18 Brain/D43 labels loaded (nine with PIDs) and 72 cwd-matched
  processes. This is time-bounded; deployed runtime identity and source write frontier remain
  unresolved. DR has not activated or changed services.
- A clean-home Git clone previously stopped at user-controlled authentication. A fresh clone and
  software smoke do not restore ignored state, owner runtime, private configuration, protected
  custody, or Kingston corpus dependencies.
- Synthetic held-source and Kingston set/witness rehearsals are accepted only within their fixture
  scopes. The negative-only restore assessor at `eb2e902` always reports blocked; it does not assess
  live Kingston recovery members or targets. The inert restore conductor at `b80fbf6` only restores
  six pinned fixture roles into a synthetic quarantine, with source closure/T/K/host/erase false.
  Its owner-root exclusions depend on caller-supplied paths. Latest assessor receipt:
  `docs/evidence/portability-dr-restore-assessment-v1-2026-10-14T0411Z.json`.
- The conductor-to-assessor operator flow passed on Kingston with six synthetic roles, isolated
  temporary HOME, a separate-device quarantine, and witness readback. Known run nonce paths were
  removed and no set/witness ID remained in filtered 32-hex ID lists; shared parent effects were not
  classified. Set/witness pin pass was internal to the synthetic run, without external trusted
  recovery authority; pytest-managed local temporary state removal was not asserted. Assessor
  remained blocked with T/K/owner-protected closure unknown. No provider requests, service changes,
  or persistent set/witness ID. Receipt:
  `docs/evidence/portability-dr-kingston-inert-restore-flow-2026-10-14T0450Z.json`.
- Sol independently reviewed the bounded flow and confirms **NO-GO**. No further non-invasive or
  synthetic run materially closes readiness gates. Required next inputs are an admin-assisted
  owner-wide source view, custody/classification decisions, and user Git authentication for a clean
  compatible-host restore. See `docs/portability-disaster-recovery.md`.
- No prospective campaign or provider requests were started for DR. Continue from the canonical
  handoff in `docs/portability-disaster-recovery.md`; it records remaining gates and evidence.

See `docs/INDEX.md` for canonical ownership of Brain, source, evaluation, runtime, and DR status.
