# Current Project State

**As of:** 2026-10-14 05:29Z. Portability/DR remains **NO-GO for factory reset**. Work is on
`research/active` at `e346e99919cc3acd9d34ec46c59db2701b874805`; `main` remains the verified
baseline. The Mac must not be erased.

- Accepted recovery case is owner-Mac loss with healthy Kingston. Kingston is the sole
  reset-surviving physical copy; single-copy and plaintext risk remain accepted. Last verified
  Kingston capacity was recorded at 04:56Z. Internal Data is currently about 14 GiB free at 97%
  used, has no APFS snapshots, and this session has no interactive admin path. No snapshot attempt
  has been made; safe retention/headroom and a real source frontier remain unqualified.
- Research `work/wallets` remains semantically unknown. User-authorized disposition is private,
  opaque, unresolved, backup-required custody only. No wallet payload has been read or copied.
  F01/D43/reserved status remains protected; no protected payload reader was used.
- Uncommitted DR work adds the v4 metadata-only opaque custody planner, synthetic-only temporary
  artifact exercise, and `brain_host_apfs_snapshot_attestor_v1.py`. The attestor consumes only an
  already mounted read-only Data snapshot; it never creates or mounts one. It requires a separately
  pinned host plan with selected UUID/XID, checks exact listed snapshot source name and held
  mount/root identities, and exposes borrowed root descriptors with pre/post validation. Its
  injected backend is synthetic-only. External approval authority is not cryptographically proven.
- Darwin API-shape check used the current System snapshot and live Data only: System UUID/XID and
  read-only mount matched; Data snapshot count was zero. This is not owner T. APFS path-based
  `diskutil` checks are revalidated around held descriptors but are not atomic against local root.
- The 63 focused attestor/v4 tests pass, as do scoped Ruff/format, strict mypy for the attestor, and
  `git diff --check`. Hypothesis was installed only under `/tmp` to run the pinned property tests.
  Sol accepted the bounded identity contract; current work is uncommitted and needs its logical
  commit/push. Plan authority still depends on a separate human-approved pin.
- No providers, prospective collectors, production behavior, services, champion, protected
  evaluation, or trading state were changed. Next independent horizon is source/restore closure and
  an operator-facing recovery flow after the real T procedure is qualified. See
  `docs/portability-disaster-recovery.md`.

See `docs/INDEX.md` for canonical documentation ownership.
