# Documentation Index

Read this page first. Current documentation is English and intentionally small. Open one canonical
source for the task; do not scan `docs/` or `docs/evidence/` wholesale.

## Consolidated audit entry — 2026-10-20

Current organization branch: `org/development`; Brain: `brain/development`; Linux: `brain/linux`.
The independent director audit of `b2dcd6d` is complete; development remains paused pending
explicit founder resumption. Findings and staged release gates are in `engineering-organization.md`;
the unique audit receipt is `evidence/brain-org-independent-audit-2026-10-20.json`.
Read `engineering/SOL-MANDATE.md` and the current Git registry in `operator-runtime.md`.
The exact consolidation result is `evidence/git-consolidation-2026-10-20.json`.

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
| Control Center, AUTO, virtual paper, Watch Market, two Macs, releases and Git recovery registry | [`operator-runtime.md`](operator-runtime.md) |
| Mac1 reset/restore from Kingston + GitHub, inventory and erase readiness | [`portability-disaster-recovery.md`](portability-disaster-recovery.md) |
| Deferred hardened portability / DR v2 handoff | [`portability-disaster-recovery-v2-roadmap.md`](portability-disaster-recovery-v2-roadmap.md) |
| Engineering organization, executive authority, confinement and integration | [`engineering-organization.md`](engineering-organization.md), [Sol mandate](../engineering/SOL-MANDATE.md), [Russian Founder Desk](../engineering/OWNER-DESK.md) |
| Cross-cutting engineering and research invariants | [`contracts.md`](contracts.md) |
| Concise experiment timeline and key failures | [`research-history.md`](research-history.md) |
| Evidence filenames, scope, and reproduction pointers | [`evidence/README.md`](evidence/README.md) |

Root `README.md` is developer setup only. `memory/now.md` is the only active project-state
memory. `memory/evaluation.md` is a compatibility pointer for the operator's historical report
renderer; add no second evaluation history there.

## Control Center UI ownership

The integrated read-only Observatory is in `apps/control-center/`; start with its English
[`README`](../apps/control-center/README.md) and [`onboarding`](control-center/ONBOARDING.md).
Its adapter is distinct from the production operator UI and displays historical pinned evidence,
not current research/runtime state. Root canonical documents remain authoritative for Brain,
source, evaluation and host policy. Scoped UI agent rules do not constrain unrelated core work.

Imported `docs/control-center/` phase reports, design notes, UI memory history, screenshots and
JSON receipts are preserved historical UI records (some retain their original Russian wording).
Their previous host/branch/model/core-stage instructions are superseded by current onboarding.
CC02 visual reconstruction remains pending; consolidation is not visual acceptance. No new core
source or production control interface is authorized by importing these artifacts.

## Historical Pointers and Review Artifacts

Active canonical Markdown is English; historical UI records are classified above.
The seven legacy protocol paths below are concise
archival pointers, not the hash-bound protocol inputs. Their exact original bytes remain in Git at
`b3bb5e3350d8ae8b713ae034f0556289997fb0ec` and earlier history. Historical reproduction must
checkout the recorded experiment commit and verify its original protocol hash. Do not run the old
campaign scripts from current `HEAD`: they read these pointer files as input and would create a
different protocol identity.

- Legacy hash-bound protocol paths: `brain-continuity.md`, `brain-curriculum-comparison.md`,
  `brain-hot-lane.md`, `brain-market-vacuum.md`, `brain-operational-recovery.md`,
  `brain-paper-loop.md`, `brain-training-expansion.md`.
- Compatibility references emitted by old operator reports: `brain-autonomous-operator.md`,
  `brain-operator-evidence.md`, `brain-operator-manual.md`, and `memory/evaluation.md`.
- Other old-link compatibility paths: `brain-experience-review.md`, `brain-b13-acceptance.md`,
  `two-mac-workflow.md`, and `memory/roadmap.md`. Current Brain architecture is in
  `brain-development.md`.
- [`brain-independent-review-handoff-2026-10-07.md`](brain-independent-review-handoff-2026-10-07.md)
  is a dated external-review packet, not a canonical source or routine context input.
- [`AI-agents/astra.md`](AI-agents/astra.md) is the requested independent forensic verdict at
  `6feba83525c0d21392432c53becd79e8901650ea`, adjudicating the two external reviews against code
  and evidence. It owns the distinct review/adjudication record; canonical topic documents
  continue to own implementation facts and policy, so this is not a replacement architecture spec.

Other retired reports, plans, and narrative memory files were removed from the working tree after
their enduring facts were merged into canonical sources. Git history remains the record of those
documents. Unique JSON receipts and figures remain unchanged.

## Code map

- Data/core replay: `src/memecoin_ai/`, `src/memecoin_brain/`, `src/memecoin_brain_sources/`.
- Source/store/curriculum: `src/memecoin_continuous/`, `src/memecoin_collector/`,
  `src/memecoin_curriculum/`, `src/memecoin_pumpswap/`.
- Models: `src/memecoin_ml/`; registry: `src/memecoin_registry/`.
- Qualified continuous export, versioned experience, research generations and evaluation:
  `src/memecoin_experience/`;
  contracts and usage are owned by `brain-source-architecture.md` and `training-evaluation.md`.
- Population facts, coverage work plans, diversity gates and exposure: `src/memecoin_population/`;
  owned by `brain-source-architecture.md` and `training-evaluation.md`.
- Paper/operator: `src/memecoin_paper/`, `src/memecoin_operator/`.
- Host recovery and metadata-only root qualification: `tools/brain_host_recovery.py`,
  `tools/brain_host_restore_flow_v1.py`, `tools/brain_host_restore_flow_v2.py`, and
  `tools/brain_host_root_metadata_classes_v1.py`; canonical policy is in
  [`portability-disaster-recovery.md`](portability-disaster-recovery.md).
- Profiles: `python3 tools/agent_context.py --task operator|data|ml|architecture|review|docs`;
  add `--query 'terms'` for bounded snippets from that task's canonical sources.

## Scope boundary

No real-money trading, signing, or transaction submission. Frozen F01/D43/reserved holdout remain
protected. A worker checkout is not owner runtime. Current operator state, candidate, champion,
deployment, and repo HEAD are distinct identities.
