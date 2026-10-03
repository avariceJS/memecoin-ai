# Control Center — STATUS

Generated from WORK_STATE.json; не редактировать вручную.

- Overall: **IN_PROGRESS** — web v1, 14/15 tasks; Tauri вне scope.
- Current phase: **7**
- Current task: **P7.2 — Clean install/E2E, docs, web-v1 acceptance**
- Last completed: P7.1 focus, 12px captions, table overflow, 768 без горизонтального скролла страницы. Visual и boundary PASS.
- Last verified commit: `a0c1c43886e998d2a976979210059ecdce3880ba`; latest фактически: `git log -1 --oneline`.
- Checks: 2026-10-03: web vitest 44 PASS, tsc PASS. Browser 768: scrollWidth=innerWidth, aria-label Торговля сохранён.
- Model binding: UNVERIFIED (config ≠ runtime).
- Blockers: нет глобального blocker
- Next action: **P7.2: clean npm ci, build/test/scope/adapter checks, записать ограничения, не merge main.**

| Task | Role | State |
|---|---|---|
| P0.1 Native model preflight | coordinator | DONE |
| P0.2 DTO audit и toolchain pins | artifact-analyst | DONE |
| P1.1 Vite shell, design tokens, navigation | ui-builder | DONE |
| P1.2 Validated providers и state components | adapter-builder | DONE |
| P2.1 D43 calendar и window details | ui-builder | DONE |
| P2.2 D48 design grid и corpus summary | ui-builder | DONE |
| P3.1 Overview и evidence hierarchy | ui-builder | DONE |
| P3.2 Sources / Pools qualification | ui-builder | DONE |
| P4.1 Research spec, gates и counters | ui-builder | DONE |
| P4.2 Research adversarial state tests | test-debugger | DONE |
| P5.1 Models / runs / benchmarks | ui-builder | DONE |
| P6.1 System и capability placeholders | ui-builder | DONE |
| P6.2 Replay/future proposals и boundary tests | adapter-builder | DONE |
| P7.1 Responsive, a11y, perf и final visual review | test-debugger | DONE |
| P7.2 Clean install/E2E, docs, web-v1 acceptance | coordinator | PENDING |

## Ограничения

- Overview ещё не пересобран в композицию reference: timeline по центру и статус справа.
- Native task card остаётся UNVERIFIED и не блокирует web v1.
- D49 recorded baseline; actual D50+ status unavailable.
- Core/raw/holdout/credentials/checkpoints недоступны UI agents.
- No collector/research/trading actions; Tauri deferred.

Interface requests: CC-P001 PROPOSED — safe operational metadata export; web v1 не ждёт его

Recovery: docs/control-center/GROK-PROMPT.md → RESUME. State — UI workflow, не core truth.
