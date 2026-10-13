# Current Project State

**As of:** 2026-10-13 12:34Z. Owner checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, HEAD and `origin/research/active` `08f8c6a`. `main` and `origin/main` remain
`770699d`. Verify live state before handoff. Preserve unrelated dirty build-guard/Agave work.

## Prospective acquisition

- Kingston `/Volumes/Kingston/BrainData`, root ID `9428695af6d4f46c08e1a444cdc66de8`, UUID
  `8f8f1081-9be4-4914-9111-75f1b90255bf`; last measured 202 GiB free, 50-GiB floor.
- Gecko v6 LaunchAgent `ai.memecoin.brain.senses.gecko-capture-only-v6` is loaded in `gui/501`,
  exact pinned plist/argv, 60-second interval. Three natural callbacks exited 0, including after the
  docs-only descendant `08f8c6a`; no provider request yet. The new Kingston root
  `94da5c80bdd82308ebbad0c8f2e158b3` has immutable 2026-10-13 13:15Z first close. Keep it forward
  only; no backfill/retry. Old v6 roots remain immutable zero-attempt roots.
- Full-current replay: 44 roots, 62 historical attempts; all three old v6 roots `verified_none`,
  zero v6 attempts. Source runtime now accepts ancestor commits only with exact pinned bytes across
  release blob, current-HEAD blob and no-follow checkout; pinned paths stay clean and plist/live
  identity exact. Sol approved this contract. Focused host tests: 43; broader v6 focus: 53.
- New first-rotation preregistration is pinned in
  `docs/evidence/gecko-v6-population-coverage-preregistration-2026-10-13.json`: durable planned
  slots define the pool denominator, while gap events remain separate unknown intervals. Next
  decision horizon after a natural bucket + no-replay restart is the read-only adapter/audit for the
  frozen 170-pool population, preserving all missingness and event/receipt/availability times.
- Policy remains six pools per 15 minutes, up to 24 requests/hour, one forward-only request per
  slot, 12-second spacing, bounded caps, 16-GiB resident ceiling and 50-GiB floor. Admission to
  source qualification, training or economics remains false.

## Senses gates

- Candidate-position witness/native settlement/`buyExact25`, broad lifecycle/population and modality
  qualification remain open. Do not infer activity/death from missing bars. Agave Cargo/ProgramTest
  remains no-go until isolated worker identity is proved.
- Alchemy read-only summary at 12:28Z remains 18,680 team-wide CU / $0.01; hourly series 05:20–12:26Z
  empty, with no limit or workload attribution. Helius account usage remains unavailable without JWT.
- `make smoke` and focused v6 tests pass. Repository-wide `make check` still has unrelated WSS Ruff
  findings. Worktree carries unrelated dirty/untracked build-guard, Agave, capture-watchdog and
  earlier provider evidence; stage only scoped changes.
