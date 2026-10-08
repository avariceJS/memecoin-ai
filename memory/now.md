# Current Project State

**As of:** 2026-10-08 Europe/Moscow. Re-read the owner before operational decisions.

## Goal

Build a large Solana learning system with growing useful corpus, deliberate training compute,
reproducible generations and independent evaluation. Two Macs are an initial stage. Preserve
provenance, temporal correctness and recovery; no demonstrated executable trading edge.

## Paused independent-population campaign — explicit user request

- **Do not resume until the user asks.** Safe stop at 2026-10-08 09:28:35 UTC. Acquisition,
  its supervisor and the waiting trainer exited; capture lock is free. No training started.
- Root: `work/brain-diverse-campaign-20261008/`. `pause-state.json` pins the plan, stopped ledger,
  STOP and last sealed capture. 1,142 attempted requests: 1,141 received and one terminal transport
  timeout (ordinal 925, never retried). No unresolved reservations. **86 requests remain.**
- Frozen acquisition at `a994c9e`: 307 birth-enrolled pools (170 May–August train; 137 September
  evaluation), cross-role base mints disjoint, prior/protected cohorts excluded before price IO.
  Plan pin `d872e0d8d8266b2dd22b1c2a8ef7e505a12b6f93ccdf82df9b7b6f206d7e7de8`.
  Original expiry: **2026-10-08 15:58:12 UTC**. No GET retries; never extend/overwrite frozen plans.
- Current `capture/STOP.json` is `user_requested_pause`. Preserve it and pause evidence in history
  when explicitly resuming. Recheck owner fence, ledger/capture hashes and expiry first. If expired,
  use a new explicit continuation envelope for only unrequested jobs, linked to the original plan;
  do not silently reuse/alter the expired envelope or rewrite previous outcomes.
- Mac 1 is `192.168.31.68`, same pinned `.66` SSH host key. The guard required operator revision 33
  paused, source STOP and unchanged source/config hashes before every GET. Transient unreadable
  owner processes stopped before reservation; fresh checks resumed only unrequested work.
- The timeout was reconciled explicitly at `4781ba5`: sealed prefix verified, original STOP archived
  under `capture/recoveries/`, recovery receipt retained, failed request stayed terminal. A later
  tested hardening reconstructs missing STOP after terminal DB commit and verifies prior failure
  reviews. Provider stops and unknown reservations cannot use transport reconciliation.
- `e6ec88b`: immutable causal admission masks, stable OHLCV v3, optional constant-feature scale 1,
  preregistered quality evaluation. `73363fe`: role-separated source qualification, population
  audits, owned generations and paired mint bootstrap. `e7477e0`: undeclared source intervals stay
  unknown in population coverage. No real learning-gain result yet.
- Frozen `learning-code/` at `73363fe` and `learning-source.json` are ready for the completed
  acquisition. Three recipes: narrow/broad fresh training plus rounded row-draw compute control.
  `control/` preserves acquisition/waiting/owner-guard/coverage helper scripts; **do not launch the
  waiting trainer while paused**. `learning-supervisor02.log` records cancellation on STOP.
- `evaluation-access.json` discloses one early raw response-shape/token-metadata/count inspection.
  Recipes/cohort were fixed before all requests. Historical replay, not pristine blind or prospective
  evaluation. No promotion, protected payload access, production changes or claimed learning gains.
- Checks at `4781ba5`: `make check` 1,128 passed / 23 optional skips; smoke passed. Optional ML suite:
  38 passed / one CUDA skip. Subsequent recovery crash regressions: 13 focused tests passed; sets
  overlap. Final hardening is not covered by another full suite yet.
- Eliza GLM/Qwen provided bounded source/admission/recovery/evaluation reviews; Kimi summarized
  partial training inventory. Findings and numbers were checked locally. Recovery crash-window
  findings were fixed; several unsupported helper claims were rejected. Grok remains disabled.

## Current research state

- The opt-in experience foundation connects qualified portable sources, generic representations,
  immutable paged snapshots, replicas/composition, owned generations, exact resume, portable
  prediction and registered evaluation. Production remains separate.
- `memecoin_population` now adds temporal population/coverage fact snapshots, sparse stream work
  plans, generic diversity audits/gates, checkpoint-bound optimizer exposure and registered cohort
  evaluation. Integration commits: `e77ab7b`, `47c72ae`, `4ffde3e`. No balancing or hidden resampling;
  unknown metadata, unsupported enrollment and failed/unknown coverage remain explicit.
- Frozen diversity qualification at `47c72ae`: 10,000 synthetic markets, 520,000 work cells,
  1M generic examples; 616,000 row draws in one CPU epoch. Audited/plain training produced exactly
  equal weights and metrics. 221.67 s total, 359,251,968-byte peak RSS including torch. Journal
  overhead was ~49% of this tiny fit; no 100M, GPU, cluster or learning-gain qualification.
  Receipt: `docs/evidence/brain-population-million-capacity-2026-10-08.json`.
- Existing sealed 24-market corpus re-audited without new fitting or provider requests. Eight
  markets supply 74,680 usable train origins; four supply 95.62%. Entity Shannon effective count
  4.35; 92.37% of descriptive lifecycle exposure is after day seven. All training venue/lifecycle
  metadata is unknown at decision time. Coverage: 95,580/100,253 eligible bar opens, 241 covered
  cells and 120 deficits. Swaps coverage remains unknown, with no invented event denominator.
  Receipt: `docs/evidence/brain-population-real-diversity-2026-10-08.json`.
- Payloads: `work/brain-population-scale-20261008/`, `work/brain-population-real-20261008/`.
  Raw receipts were moved unchanged into canonical evidence. Earlier real CPU qualification
  inputs remain `work/brain-system-qualification-20261008/`; its immutable receipt records the
  three fixed fits, exact SIGKILL recovery and mixed retrospective horizon results. That old
  evaluation window is not a fresh benchmark; do not repeatedly tune on it.
- Final checks at `4ffde3e`: `make check` passed (1,107 tests, 21 optional skips; lint/format/mypy),
  `make smoke` passed; selected optional ML/experience regressions: 86 passed, one CUDA skip.
  Sets overlap. Full load figures precede the later generic axis-gate/status-count additions,
  which have focused regressions. Receipt code/harness hashes and sealed inventories reverified.
- Next: independently enrolled broad populations and historical periods, preserved failures and
  unsupported entities, request-ledger/budget reconciliation, continuous ingestion/backfill, then
  registered tests of whether broader experience improves capability. The same 24 survivors have
  unequal historical lifetimes; filling their remaining gaps cannot solve population diversity.
  Qualified order-flow/on-chain producers and actual GPU qualification are still substantive work.
- Eliza GLM handled bounded inventory and selective critical-boundary review. A qualified-count
  denominator gap was fixed; claims conflicting with explicit enrollment semantics were rejected.
  Grok remains disabled. No protected payloads, provider requests, owner runtime changes or promotion.

## Owner and protected boundary

- Mac 1 is `192.168.31.68`, accessed as `root1` using the previously pinned SSH host key for its
  old `.66` address; strict host-key verification remained enabled.
- Read-only owner check at 2026-10-08 05:22:19 UTC: operator revision 33, paused, expired,
  domain_stop=false; source STOP present. Configs, deployment pointer, universe and source range
  metadata matched preflight. The running executable was not freshly attested.
- Official empirical champion remains
  `9ea4a6bd7977fb66fe4a18eba54ed7eaed0afd65810ec99c75aec3e1443e3920`.
  Deployment still points to runtime08; no Watch release or owner runtime switch occurred.
- Only a new isolated research export directory was created on the owner; no mutable SQLite copy
  or second canonical writer. F01/D43/reserved holdout remain protected. No real trading, signing,
  provider upgrade or secret transfer. Preserve user-owned untracked `.codex/` files.

See [`docs/INDEX.md`](../docs/INDEX.md) for canonical sources. Replace dated state after checking
current evidence; Git and immutable receipts hold history.
