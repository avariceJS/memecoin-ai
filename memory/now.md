# Current Project State

**As of:** 2026-10-08 Europe/Moscow. Re-read owner state before operational decisions.

## Goal and boundaries

Build a large Solana learning system with growing useful corpus and reproducible model generations.
No demonstrated executable edge. No promotion, signing, real-money trading, protected F01/D43 or
reserved holdout payload access. Preserve user-owned untracked `.codex/`. Grok is disabled; use
Eliza for bounded nonsecret assistance and verify findings. Runtime dependencies remain stdlib.

## Operating-mode migration in progress

User selected single-host active development/research on Mac 1; Mac 2 is becoming standby.
Destination: `/Users/root1/Developer/memecoin-research`, branch `research/mac1-active`.
Do not treat the transfer as complete until the verified handoff receipt is recorded here.
Mac 1 is currently reachable at **192.168.31.66**; the previous `172.22.124.203` timed out.
Owner `/Users/root1/Desktop/memecoin-ai` is clean at `a594474`, operator revision 33 paused,
runtime08 and champion unchanged. Research files will not be merged into owner state.

## Completed sparse/event-time stage

- Code: `f9a0c1f` observation foundation; `86c33da` qualification checkpoint recovery;
  `b975471` namespaced protected-entity evaluation guard before payload access.
- Receipt: `docs/evidence/brain-sparse-experience-2026-10-08.json`, SHA256
  `b85d22b84c3aa5e5b5759cf92ab1a44d3f7fb84812a066ac8aa946314617e8ea`.
  Root: `work/brain-sparse-qualification-20261008/`; frozen code at `f9a0c1f`.
- Immutable typed observation facts separate enrollment, events, process support and evidenced
  lifecycle. CAS shards/paged catalogs, bounded-cache disposable query index, idempotent replication.
  Sparse projection uses short histories, explicit age/gaps/masks and exact endpoint/count targets.
  Historical replay and arrival-time decisions are distinct; unknown support never means inactivity.
- Existing **train-role source only**: 38,705 events + 170 births -> 38,875 origins across all 170
  markets (previous contiguous producer: 8,792 origins, 49 markets). Same May-June/July/August roles:
  16,616 / 4,348 / 10,504 usable origins, 46 / 26 / 93 markets. Five markets have no usable endpoint
  label but remain represented. All birth-only price targets are unsupported, not invented zeros.
- One CPU engineering generation: 5,577 parameters, one epoch, 16,616 draws, 33 steps; largest draw
  share 20.20%, Shannon effective count 13.40. Changed representation/admission/recipe means this
  is not a controlled learning-gain comparison. No September evaluation payload read or rescoring.
- Synthetic load: 2,000 entities, 117,664 facts, 113,664 origins; 90,969 exact count targets and
  22,695 censored. Includes 118 empty entities, 94 with complete support. 55.78 seconds / 63.5 MiB
  peak RSS without ML libraries. Real conversion plus fit: 85.10 seconds / 386 MiB including ML.
- Verified source/snapshot/code archive, real generation and committed exposure chain; real source
  and snapshot replicas preserve identity and repeat idempotently; standalone prediction finite.
- `make check` 1,146 passed / 24 optional skips, lint/format/mypy and smoke passed. Final optional
  ML regression set: 60 passed / one actual-CUDA skip, including exact resume, harness interruption,
  synthetic evaluation and final namespace guard. Full suite started before the final guard change;
  the guard has focused coverage. No jobs remain running at completion.
- Qwen inventoried interfaces and audited receipt arithmetic; GLM checked temporal/provenance
  boundaries. Incorrect helper hypotheses were rejected. Helper outputs are under the run root.
- Initial real job had a transcribed wrong source pin and failed before payload verification.
  `pin-correction.json` binds the failure and corrected `real-job02.json`; `real02/` is the successful
  run. Never rewrite frozen declarations or receipts to make a failed run look successful.

## Next major stage

Qualify real event-process completeness and lifecycle coverage, retaining sparse/failed entities.
Current quantile loss excludes censored cells; bounds are metadata, not a survival likelihood.
Real missing bars still cannot prove inactivity/death. Next objective work must consume honest
coverage/censoring, then use fresh registered populations and multiple seeds against empirical
baselines. No further tuning on the studied September cohort. GPU, distributed materialization,
object storage, rich qualified on-chain/order-flow and packed multistream views remain unqualified.

## Prior campaign and owner

- Prior receipt: `docs/evidence/brain-independent-population-learning-2026-10-08.json`.
  Root `work/brain-diverse-campaign-20261008/`: all 1,228 frozen requests attempted, one sealed
  timeout never retried, six duplicate-bar responses quarantined; all 307 enrollments retained.
  Do not restart its expired acquisition envelope. Original infeasible `learning/` stays unchanged;
  `learning02/` completed three candidates. Broader neural data helped neural controls but lost to
  empirical quantile baselines. September is studied, not fresh evaluation.
- Mac 1: **172.22.124.203**, user `root1`, same strict pinned SSH key as old `.66` address.
  Last read-only fence: 2026-10-08 10:57:24 UTC, revision 33 paused/no busy jobs. No owner access or
  runtime mutation in the sparse stage. Recheck owner before any later operational action.
- Last recorded champion: `9ea4a6bd7977fb66fe4a18eba54ed7eaed0afd65810ec99c75aec3e1443e3920`, runtime08.
  Paid Alchemy account capacity remains unverified; no provider request in this stage.

Start from `docs/INDEX.md` for canonical architecture, source, training and operator contracts.
