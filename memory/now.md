# Current Project State

**As of:** 2026-10-08 Europe/Moscow. Re-read the owner host before operational decisions.

## Goal

Build a scalable Solana Brain/data system whose generations represent substantially more useful
data, deliberate training compute, independent evaluation, and measured improvements. The two
Macs are an initial stage, not the infrastructure ceiling. Preserve temporal correctness,
provenance, immutable artifacts, and safety while removing historical pilot limits that have no
technical justification. The user reports paid Alchemy capacity and willingness to scale; actual
plan limits have not been audited in this repository.

## Repository and research

- `memecoin_experience` connects read-only continuous export, qualified portable sources,
  versioned snapshots, disjoint composition/replication, owned model generations, warmstart/resume,
  standalone prediction and registered research evaluation. Its `pipeline` CLI runs and recovers
  the historical research path end to end; usage and qualification scope are in
  [`training-evaluation.md`](../docs/training-evaluation.md). Source paths are outside snapshot
  identity. Large catalogs page metadata; hierarchical sampling preserves full-epoch exposure.
  Existing v1 snapshots remain readable. No market candidate was trained during this migration.
- Implemented in `2bdef07`, `f5aca3d`, `2f1eff2` and `6ae0ea9`. Final code checks: `make check`
  passed (1,099 tests, 18 optional skips; lint/format and mypy passed), `make smoke` passed.
  Selected broader ML regressions passed in the torch environment: 96 tests, six hardware skips
  (five MPS, one CUDA). Counts overlap and must not be summed.
- The million-bar full pipeline at `2f1eff2` completed two CPU epochs, 1,199,856 optimizer row
  visits and separate evaluation in 271.1 seconds at about 348 MiB RSS. This is synthetic
  engineering evidence with a width-16 GRU, not market/GPU acceptance. Its eight-pool catalogs
  remain inline; the receipt filename does not establish that paging was exercised.
- A second full-pipeline run at `6ae0ea9` covered 1,024,000 bars across 2,048 synthetic pools:
  804,864 persisted training examples, four training catalog pages, two evaluation pages,
  1,191,936 optimizer row visits and separate evaluation. It completed in 360 seconds at about
  355 MiB RSS. The unique receipt is
  `docs/evidence/brain-wide-paged-pipeline-capacity-2026-10-08.json`. Both million-scale receipts
  were verified against their recorded source/tool hashes; temporary payloads were removed.
- Kimi/GLM designs and reviews, Qwen's completed integration review, and GLM's catalog review
  were independently adjudicated. Some assistant bug claims were disproved by code and tests;
  canonical training docs summarize material decisions. Grok remains disabled; Eliza only.
  AUTO/champion, owner runtime, market datasets and protected holdouts were not changed.
- Next acceptance is integrated qualification on an explicitly enrolled, non-protected research
  population plus real CUDA worker qualification. Measure raw-to-learning conversion, incremental
  generations, recovery, throughput and independent forecasts. Richer source semantics, executable
  trading value, 100M-scale throughput, DDP and remote conditional publication remain unqualified
  or unimplemented; they are not implied by local replica or synthetic results.
- User supplied owner Mac 1 address `192.168.31.67` on 2026-10-08. It was not contacted during
  this migration; the owner status below remains dated, not a fresh runtime attestation.

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
