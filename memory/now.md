# Current Project State

**As of:** 2026-10-08 Europe/Moscow. Re-read the owner before operational decisions.

## Goal

Build a large Solana learning system with growing useful corpus, deliberate training compute,
reproducible generations and independent evaluation. Two Macs are an initial stage. Preserve
provenance, temporal correctness and recovery; no demonstrated executable trading edge.

## Current research state

- The opt-in `memecoin_experience` architecture connects qualified portable continuous sources,
  extensible representations, paged immutable snapshots, composition/replication, owned generations,
  exact resume, parent warmstart, portable prediction and registered research evaluation.
  Migration commits: `2bdef07`, `f5aca3d`, `2f1eff2`, `6ae0ea9`; synthetic million-scale receipts
  and limits are in [`training-evaluation.md`](../docs/training-evaluation.md).
- Real-data CPU system qualification is complete on the fixed existing 24-market development
  universe. Frozen plan/harness: `339cb40`; independent reporter: `d0f6732`.
  Receipt: `docs/evidence/brain-real-system-qualification-2026-10-08.json`.
  Sealed inputs/artifacts: `work/brain-system-qualification-20261008/`, run root `run01`.
  No new market-data requests, recipe search, production promotion or protected payload access.
- 95,801 qualified training-window bars became 82,436 context-32 examples: 12,486 origins lost
  to context after gaps, 667 to initial context, 212 outside the origin interval. Base + disjoint
  increment was 71,548 + 10,888; replicas verified and reused. Cumulative train has 74,680 usable
  origins from eight markets; four still supply 95.62% of exposure. Broad population remains weak.
- Three fixed width-128, 60,681-parameter GRU fits ran five full CPU epochs: qualification V1,
  cumulative V2 warm, cumulative V2 fresh. A separate V1 killed after checkpoint step 20 recovered
  exactly equal weight tensors and an identical report. These are acceptance cases, not production
  model generations or evidence of a large trained Brain.
- October 5–7 retrospective evaluation supported ten of 24 markets, approximately 4.3k origins
  per horizon. Warm versus V1 worsened 5-minute pinball by about 5%, improved 15/30-minute by
  1.8%/5.2%. Coverage remains imperfect. No uniform generation-gain, independent calibration,
  prospective capability or execution/edge claim. Do not tune repeatedly on this same window.
- Final checks: `make check` passed (1,100 tests, 19 optional skips; lint/format/mypy passed),
  `make smoke` passed. Selected broader ML regressions: 98 passed, six hardware skips
  (five MPS, one CUDA). Test sets overlap. Actual CUDA was unavailable; GPU host details were
  requested. No paid compute was provisioned. Per-fit wall time was not instrumented.
- Next: a broader independently enrolled population with contiguous coverage and a real GPU
  worker. Quantify learning conversion, normalization drift, training/I/O throughput and recovery
  before scaling model size. Richer semantic producers, 100M-scale throughput, DDP and remote
  conditional publication remain separate qualifications/extensions.
- Eliza helpers are primarily bounded log/receipt/document/test-inventory assistants. Selective
  critical-boundary review only; outputs are hypotheses. This stage used GLM for test inventory,
  with incorrect suggestions rejected. Grok remains disabled. See `operator-runtime.md`.

## Owner and protected boundary

- Mac 1 is `192.168.31.67`, accessed as `root1` using the previously pinned SSH host key for its
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
