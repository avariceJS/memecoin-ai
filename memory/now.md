# Current Project State

**As of:** 2026-10-13 17:35Z. Owner checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, scoped code commit `f4462fb` (receipt replay fix); final handoff documentation is
committed and pushed in follow-up `efeb7c2`. `main`/`origin/main` remain `770699d`. Preserve the unrelated dirty
build-guard/Agave/capture-watchdog/provider-evidence scope; do not include it in the Senses release.
Positive-bar projector and discovery evidence remain offline/synthetic; no eligible population or
economic/lifecycle qualification claim.

## Prospective acquisition

- Kingston `/Volumes/Kingston/BrainData`, root ID `9428695af6d4f46c08e1a444cdc66de8`, UUID
  `8f8f1081-9be4-4914-9111-75f1b90255bf`; last descriptor-bound measurement: 202.4 GiB free,
  above the 50-GiB floor.
- Gecko v6's first natural bucket at 13:15Z is independently replayed: four confirmed responses
  (3,042 raw bytes), one permanently spent ambiguous request, one missed slot, cursor out 6. All 12
  parsed bars were out-of-window; zero eligible new-open values. Bootout/bootstrap preserved the
  attempt list and source prefix with no replay. The v6 agent is now unloaded; Sol's accepted path is
  an immutable handoff certificate plus separate forward-only v7 with explicit gaps, cooldown,
  zero retries, and bounded STOP. Receipt:
  `docs/evidence/gecko-v6-first-natural-bucket-2026-10-13T1315Z.json`.
- Kingston descriptor-bound free space: 217,315,987,456 bytes (~202.4 GiB), above the 50-GiB floor.
- V3 capacity migration was published on Kingston at 14:52Z using `StorageGuard` and a held
  `DescriptorWriter` root fd. Pointer-last create-only migration ID
  `95666bfe6c9fed8a99cc29af4e509b1c`; bridge `0df0e66082e3825dce12db54d682451f34d643ac263c1181787db49a7ebe71d5`.
  A second live host replay produced no writes and the same hashes. Provider I/O was false. All 18
  known v2 mutating LaunchAgent labels remain disabled/unloaded; no Senses collector or matching
  process is loaded. Latest `df`: ~202.4 GiB free.
- Sol superseded the discovery day-45 STOP: retain immutable 72-hour epochs and auto-renew unchanged
  policy indefinitely. Day 45 is diagnostic only. Non-resetting physical cap is 101,921,587,200 B;
  next-epoch reservation reconciles allocation/debt and reserves only the uncommitted runway. Source-
  bound v3 discovery owner/runtime, autonomous renewal and provider acceptance are still pending;
  do not claim live prospective collection.
- Added a separate read-only discovery-to-enrollment projector. It replays plan/gap prefixes and raw
  page hashes, keeps first durable availability and conflict provenance, and emits only unassigned
  diagnostic candidates (zero price follow-up; protected exclusion unchecked). Ten focused property,
  replay, and epoch-boundary tests pass. It cannot publish an eligible population or make
  lifecycle/economic claims.
- Coordinator v3 page reservations now bind ordered one-based request ordinals within each epoch
  hold. A recorded schedule gap may be skipped, but a lower ordinal can never be reserved later;
  cross-provider event order is unaffected. The host derives ordinals from the fixed epoch schedule.
  The owner entrypoint's unsupported argument and unresolved-prior-dispatch behavior were fixed.
  The new owner uses 10-second LaunchAgent wakeups, one provider request per tick, read-only idle
  hints, no reservation on early/rate-blocked ticks, and explicit no-dispatch terminalization on a
  rate/priority race after intent. Sol declined a new source-tail ledger field; the verified
  plan/gap/result prefix plus monotonic ordinals is sufficient. Successor standby now has a separately
  versioned v3 schema binding the latest contiguous terminal plan-summary or explicit-gap frontier
  and prefix hash. Rollover compares that immutable prepared frontier to the completed predecessor
  replay, so no-traffic epochs renew without invented provider activity. The combined storage,
  coordinator, discovery, and enrollment focus has 169 passing tests; scoped Ruff, py_compile,
  diff-check, and smoke pass. Repository-wide `make check` still fails on unrelated WSS Ruff findings.
  Source-manifest v3 includes the
  previously omitted `live_writer.py` and external-writer schema, both part of the runtime write
  authority closure. No provider I/O or LaunchAgent activation occurred. The source-pinned owner
  release and unattended service remain unqualified; no Senses collector is loaded.
- Current `launchctl list`/process check confirms no prospective collector; latest `df` shows 202 GiB
  available on Kingston. Sol selected ancestor-plus-exact-bytes source binding: at every callback,
  the release commit must be an ancestor of HEAD and the 12 pinned files must be clean and byte-equal
  across the release commit, HEAD and no-follow checkout. Focused tests pass for descendants, dirty
  unpinned files, pinned drift and divergent history. Five synthetic recovery tests now use the real
  v3 ledger and descriptor writer: reservation/no intent, intent/no dispatch, dispatch ambiguity,
  durable-result settlement once, and detach during page write with no underlay artifact. Sol chose
  a fresh v3 release namespace at `runtime-v3`, new v3 activation path and LaunchAgent label/template;
  shared capacity ledger/root remains common. Preserve old runtime-v2 artifacts byte-for-byte. The
  callback verifier uses no-follow file descriptors and validates captured HEAD stability. Activation
  interruption test retries the pre-existing hold/pointer into one authority. Candidate
  `7b15e8d1f3acdbc6b374e349aaeb1282` was prepared at `runtime-v3` from source `9903412`. Its
  future-only 17:20Z anchor elapsed. Activation exists but bundle verification failed before plist
  install/load or provider I/O. Descriptor-bound `runtime-v3/stop.json` records the failure. The
  verifier fix is in `d86aa6d`; `f4462fb` raises the hold-close receipt read bound to 4 MB and adds a
  regression. Kingston read-only replay confirms hold `d1740af0b53b05f00206fa4ee300bad2` is closed,
  all 2,592 request IDs unconsumed, and receipt/ledger agree. STOP remains. Changed pinned source,
  elapsed anchor, and STOP make this activation unusable. Do not dispatch or reuse it. Old runtime-v2 tree hash remains
  `ef3895bf11079c9bc2686203aab08e9dd00cd7b25c086506d3226dfe8bd31548`; stale candidate
  `3918f088c01caaf414fc37e473ec62b6` untouched. No collector is running; user's next session is
  portability/DR, no new Senses campaign should start here. Current launchd query shows no matching
  Brain Senses label; v3 installed plist is absent and no collector process is running. No provider
  requests were issued. Kingston has 217,316,544,512 bytes free (~202.4 GiB), above the 50-GiB floor.
  Final scoped discovery focus: 38 tests pass with repo `.venv`; Ruff and format checks pass. Do not
  start a new prospective lease/campaign before the dedicated Portability / Disaster Recovery session.
- Full-current replay: 44 roots, 62 historical attempts; all three old v6 roots `verified_none`,
  zero v6 attempts. Runtime binding requires ancestor commit, exact source bytes across release/HEAD/
  checkout, clean pinned paths, and exact plist/live identity. Focused host tests: 43; broader v6
  focus: 53.
- The first-rotation preregistration is pinned in
  `docs/evidence/gecko-v6-population-coverage-preregistration-2026-10-13.json`. Denominator uses
  durable plan slots; explicit gaps remain separate unknown intervals. After one live bucket and
  no-replay restart, implement the read-only coverage adapter for the frozen 170-pool population.
- Sol's next horizon is positive `eligible_new_open` values only. Offline projector
  `tools/brain_senses_gecko_v6_observed_values_v1.py` binds raw hashes, pool/base/quote identity,
  provenance pins and distinct event/decision/request/receipt/durable-availability times. It emits
  no completeness, lifecycle, source, training or economic claim; age is unknown without independent
  birth evidence. Focused tests pass, but code is synthetic-only until live root replay/recovery and
  full preregistered rotation.
- Policy remains six pools per 15 minutes, up to 24 requests/hour, 12-second spacing, forward-only;
  caps include 576 requests/day, 10 MiB/day raw, 80 KiB physical reserve/request, 16-GiB resident
  ceiling and 50-GiB free floor. Qualification/training/economic admission false.

## Senses gates

- Candidate-position witness/native settlement/`buyExact25`, broad lifecycle/population and modality
  qualification remain open. Missing bars never imply zero or death. No Cargo/ProgramTest until an
  isolated worker identity is proved.
- Alchemy through 12:28Z: 18,680 team-wide CU / $0.01, empty hourly series 05:20–12:26Z, no limit or
  workload attribution. Helius account usage unavailable without JWT.
- `make smoke` and focused v6 tests pass. Repo-wide `make check` still has unrelated WSS Ruff
  findings. Preserve unrelated dirty/untracked build-guard, Agave, capture-watchdog and older
  provider evidence; stage only scoped files.
