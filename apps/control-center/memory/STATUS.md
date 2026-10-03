# Control Center — STATUS

Generated from WORK_STATE.json; не редактировать вручную.

- Overall: **IN_PROGRESS** — web v1, 5/15 tasks; Tauri вне scope.
- Current phase: **2**
- Current task: **P2.2 — D48 design grid и corpus summary**
- Last completed: P2.1 D43 calendar: 180/174/135, protected без метрик, UTC/МСК. Visual и boundary PASS.
- Last verified commit: `3c9017acd9a751a4e63ec9fe04fcfcd182a450e9`; latest фактически: `git log -1 --oneline`.
- Checks: 2026-10-03: web vitest 16 PASS, tsc PASS. Browser /calendar: protected rows без counts, MSK 07:53→10:53, detail panel x=1112 при ширине 1672.
- Model binding: UNVERIFIED (config ≠ runtime).
- Blockers: нет глобального blocker
- Next action: **P2.2: D48 design grid 336/112 protected и corpus summary, без вида работающего collector.**

| Task | Role | State |
|---|---|---|
| P0.1 Native model preflight | coordinator | DONE |
| P0.2 DTO audit и toolchain pins | artifact-analyst | DONE |
| P1.1 Vite shell, design tokens, navigation | ui-builder | DONE |
| P1.2 Validated providers и state components | adapter-builder | DONE |
| P2.1 D43 calendar и window details | ui-builder | DONE |
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

- D48 grid, research и модели ещё не собраны в отдельные экраны.
- Native task card остаётся UNVERIFIED и не блокирует web v1.
- D49 recorded baseline; actual D50+ status unavailable.
- Core/raw/holdout/credentials/checkpoints недоступны UI agents.
- No collector/research/trading actions; Tauri deferred.
- jsdom transitive packages предупреждают engine Node ^24.15 при локальном Node 24.5.0; тесты прошли.

Interface requests: CC-P001 PROPOSED — safe operational metadata export; web v1 не ждёт его

Recovery: docs/control-center/GROK-PROMPT.md → RESUME. State — UI workflow, не core truth.
