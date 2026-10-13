# Current Project State

**As of:** 2026-10-13 12:25Z. Owner checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, HEAD `25b19ea` before the current uncommitted source-binding fix. `main` and
`origin/main` are `770699d`; `origin/research/active` was `25b19ea` at last check. Verify again
before committing. Preserve unrelated dirty build-guard/Agave work.

## Prospective acquisition

- Kingston `/Volumes/Kingston/BrainData`, root ID `9428695af6d4f46c08e1a444cdc66de8`, UUID
  `8f8f1081-9be4-4914-9111-75f1b90255bf`; last measured 202 GiB free, 50-GiB floor.
- No Gecko v6 LaunchAgent is loaded and no collector process/provider I/O is active. Read-only
  full-current census: 44 roots, 62 historical attempts; all three v6 roots verified `verified_none`
  with zero v6 attempts. Old root/control records stay immutable.
- Exact-HEAD runtime pin broke on a docs-only descendant. Sol approved clean release commit plus
  ancestor check and exact pinned-byte equality across release commit, current HEAD, and no-follow
  checkout, with clean pinned paths and exact plist/launchd identity. Focused host tests pass (43).
  New release ID in the current fix is `94da5c80bdd82308ebbad0c8f2e158b3`; create-only release,
  future anchor, first live bucket, and no-replay restart proof remain outstanding.
- Capture policy: six pools per 15-minute bucket, 12-second spacing, no retry/backfill; bounded
  request/byte/storage caps, 16-GiB resident ceiling, 50-GiB free floor. It is transport capture
  only, not market/economic qualification.

## Senses gates

- Candidate-position witness/native settlement/`buyExact25`, broad lifecycle/population and
  modalities remain open. Prioritize actual forward capture, then economic witness and broad source
  coverage; downshift further local writer polish after a real restart proof. Do not run Cargo or
  ProgramTest in the dirty Agave checkout; its dedicated unprivileged build gate is unresolved.
- Focused v6 tests: 53 pass. `make smoke` previously passed; repo-wide `make check` still has 107
  unrelated WSS Ruff findings. Worktree has unrelated dirty/untracked build-guard, Agave, and prior
  capture-watchdog work; stage only the current scoped files.
