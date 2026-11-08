# Documentation Index

Start with `memory/now.md`, `git status --short`, `git rev-parse HEAD`, then the relevant owner below.
Source lives in `~/Developer/memecoin-research` on `develop`; stable `main` has separate release gates.
Private runtime lives in `~/Developer/memecoin-runtime`. Company stays paused until Founder launch.

| Topic | Canonical owner |
|---|---|
| Architecture, components and product scope | [brain-development.md](brain-development.md) |
| Acquisition, data sources, storage identity, admission and coverage | [brain-source-architecture.md](brain-source-architecture.md) |
| Models, datasets, training, experiments and evaluation limits | [training-evaluation.md](training-evaluation.md) |
| Operator, paper, Watch Market and host paths | [operator-runtime.md](operator-runtime.md) |
| Engineering organization, native publisher and installation | [engineering-organization.md](engineering-organization.md) |
| Shared invariants | [contracts.md](contracts.md) |
| Recovery interfaces and host portability | [portability-disaster-recovery.md](portability-disaster-recovery.md) |
| Historical experiment conclusions | [research-history.md](research-history.md) |
| Reusable engineering lessons | [engineering-lessons.md](engineering-lessons.md) |
| Exact immutable evidence locator | [evidence/README.md](evidence/README.md) |
| Founder status (Russian) | [OWNER-DESK.md](../engineering/OWNER-DESK.md) |

`AGENTS.md` owns working rules and CLEAN-AS-YOU-GO. Update these owners instead of adding reports.
`memory/now.md` is the only current status note; historical states remain in Git and immutable receipts.
Use `python3 tools/agent_context.py --task architecture|data|ml|operator|review|docs --query 'terms'`
for bounded context. Evidence is not authority. Old protocol pointer files are compatibility links;
reproduction must use the receipt-bound historical commit and hashes, never current pointer text.

The read-only Observatory is in `apps/control-center/`; its README and `docs/control-center/ONBOARDING.md`
own UI setup. Pinned screenshots/receipts are historical evidence, not live product state.
No real-money trading, training, live collectors or protected-data access is implied by consolidation.
