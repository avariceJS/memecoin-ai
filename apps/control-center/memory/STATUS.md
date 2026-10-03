# Control Center — STATUS

Generated from WORK_STATE.json; не редактировать вручную.

- Overall: **BLOCKED_NEEDS_USER** — web v1, 1/15 tasks; Tauri вне scope.
- Current phase: **0**
- Current task: **P0.1 — Native model preflight**
- Last completed: P0.2 toolchain pins записаны; карта DTO и boundary review приняты. P0.1 не закрыт: model card не наблюдалась.
- Last verified commit: `c6e81aa937d2c5f497a64ab57bd710de0b5dd734`; latest фактически: `git log -1 --oneline`.
- Checks: 2026-10-03 перед docs commit: 22 unittest PASS; projection exact; scope PASS. Model card UNVERIFIED.
- Model binding: UNVERIFIED (config ≠ runtime).
- Blockers: Прочитать native task card субагента ce83fa1b-0fff-49ba-8c51-9c2089a46e5f и сообщить показанную модель/variant. Токены не присылать. Пока observed executor не composer-2.5[fast=false], P0.1 не DONE и P1 не стартует.
- Next action: **Пользователь один раз сообщает модель с task card ce83fa1b. Если это Composer 2.5 standard, Coordinator записывает VERIFIED и начинает P1.1. Иначе не подменять fast/inherit.**

| Task | Role | State |
|---|---|---|
| P0.1 Native model preflight | coordinator | BLOCKED_EXTERNAL |
| P0.2 DTO audit и toolchain pins | artifact-analyst | DONE |
| P1.1 Vite shell, design tokens, navigation | ui-builder | PENDING |
| P1.2 Validated providers и state components | adapter-builder | PENDING |
| P2.1 D43 calendar и window details | ui-builder | PENDING |
| P2.2 D48 design grid и corpus summary | ui-builder | PENDING |
| P3.1 Overview и evidence hierarchy | ui-builder | PENDING |
| P3.2 Sources / Pools qualification | ui-builder | PENDING |
| P4.1 Research spec, gates и counters | ui-builder | PENDING |
| P4.2 Research adversarial state tests | test-debugger | PENDING |
| P5.1 Models / runs / benchmarks | ui-builder | PENDING |
| P6.1 System и capability placeholders | ui-builder | PENDING |
| P6.2 Replay/future proposals и boundary tests | adapter-builder | PENDING |
| P7.1 Responsive, a11y, perf и final visual review | test-debugger | PENDING |
| P7.2 Clean install/E2E, docs, web-v1 acceptance | coordinator | PENDING |

## Ограничения

- Frontend и web/ ещё отсутствуют; pins записаны, lockfile не создан.
- Native task card model не видна в tool result; P0.1 BLOCKED_EXTERNAL.
- D49 recorded baseline; actual D50+ status unavailable.
- Core/raw/holdout/credentials/checkpoints недоступны UI agents.
- No collector/research/trading actions; Tauri deferred.

Interface requests: CC-P001 PROPOSED — safe operational metadata export; web v1 не ждёт его

Recovery: docs/control-center/GROK-PROMPT.md → RESUME. State — UI workflow, не core truth.
