# Current Project State

**As of:** 2026-10-14 05:57Z. Portability/DR is **NO-GO for factory reset**. Work is on
`research/active` at `0aa31f36d67c7b75a7a8e6bd30c814e83a50f792`; `main` remains the verified
baseline. Do not erase the Mac.

- Accepted scope is owner-Mac loss with healthy Kingston. Kingston is the only reset-surviving
  physical copy; single-copy and plaintext risk are accepted. Latest recorded internal Data free
  space is about 13.8 GiB at 97% used, with no Data snapshot and no noninteractive admin path. No
  owner T exists or was attempted.
- `research/work/wallets` remains private, opaque, unresolved, and backup-required; no payload was
  read or copied. F01/D43/reserved holdouts remain protected, with no semantic reader used.
- The committed `0aa31f3` v4 descriptor capture supports only disposable synthetic roots. The
  current uncommitted horizon adds synthetic v4 inert reconstruction: included bytes and hardlink
  topology are restored to a fresh quarantine; symlinks and excluded `.env.local` aliases remain
  absent; source metadata is kept as private evidence and not applied. No Kingston, T, owner state,
  services, or real restore authority is involved.
- Synthetic v4 reconstruction is independently accepted by Sol. It proves fixture-only bytes and
  hardlink topology; Sol did not rerun tests or inspect owner/protected data. Focused capture/restore
  suite: 21 passed; 85 combined APFS/v4 tests passed. Ruff, strict mypy for restore, and
  `git diff --check` pass. Next: reassess real-T feasibility against the current free-space/admin
  boundary and improve independent new-host bootstrap/recovery UX without changing runtime.
- Fresh read-only status sees 18 Brain/D43 launchd labels (eight with PIDs) and 72 processes in the
  configured owner/research roots. No services or processes were changed. No provider request,
  collector, production behavior, protected evaluation, or trading state changed.

Canonical DR handoff: `docs/portability-disaster-recovery.md`.
