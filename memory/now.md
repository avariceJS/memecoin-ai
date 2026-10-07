# Current Project State

**As of:** 2026-10-07 Europe/Moscow. Re-read the owner host before operational decisions.

## Goal

Build a scalable Solana Brain/data system whose generations represent substantially more useful
data, deliberate training compute, independent evaluation, and measured improvements. The two
Macs are an initial stage, not the infrastructure ceiling. Preserve temporal correctness,
provenance, immutable artifacts, and safety while removing historical pilot limits that have no
technical justification. The user reports paid Alchemy capacity and willingness to scale; actual
plan limits have not been audited in this repository.

## Repository and research

- Opt-in `memecoin_experience` migration now provides immutable partitioned experience from
  qualified admission units and resumable full-epoch CPU/CUDA research jobs. Usage and limitations
  are in `training-evaluation.md`; source contract is in `brain-source-architecture.md`.
  Synthetic checks cover provenance, partial targets, full consumption and exact CPU recovery.
  AUTO/champion, owner runtime, market datasets and protected holdouts were not changed.

- The status snapshot was read from code base `b3bb5e3350d8ae8b713ae034f0556289997fb0ec`; the evidence receipt is `docs/evidence/brain-handoff-status-2026-10-07.json`.
- Owner Mac 1 read-only status at 2026-10-07 17:16:36 UTC: revision 33, paused/paused,
  domain_stop=false, 10 API-returned jobs completed, 3 managed exams expired.
- Official champion is the empirical model,
  `9ea4a6bd7977fb66fe4a18eba54ed7eaed0afd65810ec99c75aec3e1443e3920`.
- Active deployment file matched the previously attested runtime08 binding. That check did not
  freshly attest the running executable. Runtime08 does not contain Watch Market; repo feature
  commit `0e9a2c1` has not passed owner-host live acceptance.
- Last broad corpus: 86 training pools, 48,000 retained decisions. Last research envelope:
  2,020 bars, 453 origins, 6 supported mints, 29 globally disjoint windows; old/young support is
  imbalanced. It is not a broad or prospective benchmark.
- Latest GRU was not promoted. Current comparisons and causal ML evidence are in
  [`training-evaluation.md`](../docs/training-evaluation.md).
- Current priority remains sustained semantic qualification and broad prospective evaluation.
  Migration validation has trained synthetic fixtures only, not a new market candidate.

## Working boundary

F01/D43 and reserved holdout remain protected. No real trades, signing, submission, provider
upgrade, or secret transfer. User-owned `.codex/` files are outside this repository cleanup.

See [`docs/INDEX.md`](../docs/INDEX.md) for canonical sources. This file is volatile status only;
replace dated facts after checking current evidence, do not append an event history.
