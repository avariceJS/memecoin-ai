# Documentation Index

Read this page first. Current documentation is English and intentionally small. Open one canonical
source for the task; do not scan `docs/` or `docs/evidence/` wholesale.

## Session entry

1. [`memory/now.md`](../memory/now.md): dated volatile state; verify live status before acting.
2. `git status --short` and `git rev-parse HEAD`.
3. Read the relevant owner below. Read [`contracts.md`](contracts.md) when touching shared
   time, provenance, admission, scoring, ledger, or release invariants.
4. Open only a named receipt from [`evidence/README.md`](evidence/README.md) when a claim needs
   verification or reproduction.

`AGENTS.md` defines persistent work/documentation rules. It is always injected in agent context.

## Canonical sources

| Task | Read |
|---|---|
| Brain scope, components, target product and scale direction | [`brain-development.md`](brain-development.md) |
| Solana/price sources, data funnel, admission, coverage and acquisition | [`brain-source-architecture.md`](brain-source-architecture.md) |
| Models, training recipe, experiment conclusions and benchmark design | [`training-evaluation.md`](training-evaluation.md) |
| Control Center, AUTO, virtual paper, Watch Market, two Macs and releases | [`operator-runtime.md`](operator-runtime.md) |
| Cross-cutting engineering and research invariants | [`contracts.md`](contracts.md) |
| Concise experiment timeline and key failures | [`research-history.md`](research-history.md) |
| Evidence filenames, scope, and reproduction pointers | [`evidence/README.md`](evidence/README.md) |

Root `README.md` is developer setup only. `memory/now.md` is the only active project-state
memory. `memory/evaluation.md` is a compatibility pointer for the operator's historical report
renderer; add no second evaluation history there.

## Frozen protocol inputs and compatibility files

The seven preregistration files below are retained byte-for-byte because old experiment tools
embed their SHA256 in receipts. They are **not canonical**, are not current status, and must not be
loaded in ordinary work. Historical reproduction must use the recorded Git commit, not current
code. Their original text is unchanged even though it predates the current English documentation
rule. The remaining listed paths are concise English compatibility pointers kept for existing
code, reports, or frozen documents; they are not independent sources of truth.

- Hash-bound preregistrations: `brain-continuity.md`, `brain-curriculum-comparison.md`,
  `brain-hot-lane.md`, `brain-market-vacuum.md`, `brain-operational-recovery.md`,
  `brain-paper-loop.md`, `brain-training-expansion.md`.
- Compatibility references emitted by old operator reports: `brain-autonomous-operator.md`,
  `brain-operator-evidence.md`, `brain-operator-manual.md`, and `memory/evaluation.md`.
- Other old-link compatibility paths: `brain-experience-review.md`, `brain-b13-acceptance.md`,
  `two-mac-workflow.md`, and `memory/roadmap.md`. Current Brain architecture is in
  `brain-development.md`.

Other retired reports, plans, and narrative memory files were removed from the working tree after
their enduring facts were merged into canonical sources. Git history remains the record of those
documents. Unique JSON receipts and figures remain unchanged.

## Code map

- Data/core replay: `src/memecoin_ai/`, `src/memecoin_brain/`, `src/memecoin_brain_sources/`.
- Source/store/curriculum: `src/memecoin_continuous/`, `src/memecoin_collector/`,
  `src/memecoin_curriculum/`, `src/memecoin_pumpswap/`.
- Models: `src/memecoin_ml/`; registry: `src/memecoin_registry/`.
- Paper/operator: `src/memecoin_paper/`, `src/memecoin_operator/`.
- Profiles: `python3 tools/agent_context.py --task operator|data|ml|architecture|review|docs`;
  add `--query 'terms'` for bounded snippets from that task's canonical sources.

## Scope boundary

No real-money trading, signing, or transaction submission. Frozen F01/D43/reserved holdout remain
protected. A worker checkout is not owner runtime. Current operator state, candidate, champion,
deployment, and repo HEAD are distinct identities.
