# Control Center — STATUS

Generated from WORK_STATE.json; не редактировать вручную.

- Overall: **IN_PROGRESS** — web v1, 0/15 tasks; Tauri вне scope.
- Current phase: **0**
- Current task: **P0.1 — Native model preflight**
- Last completed: CC-01: visual/team preparation; web implementation ещё не начата
- Last verified commit: `131b2ce`; latest фактически: `git log -1 --oneline`.
- Checks: 22 scoped unittest PASS; projection exact; scope/state/reference conformance PASS; Ruff/format PASS (CC-01). Native Cursor execution NOT_RUN.
- Model binding: UNVERIFIED (config ≠ runtime).
- Blockers: нет глобального blocker
- Next action: **Прочитать onboarding; проверить branch/scope; native read-only cc-artifact-analyst preflight и Composer task card, затем P0.2.**

| Task | Role | State |
|---|---|---|
| P0.1 Native model preflight | coordinator | PENDING |
| P0.2 DTO audit и toolchain pins | artifact-analyst | PENDING |
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

- Frontend ещё отсутствует; CC-01 не реализует screens.
- Cursor model/account/native execution ещё не проверены в Cursor chat.
- D49 recorded baseline; actual D50+ status unavailable.
- Core/raw/holdout/credentials/checkpoints недоступны UI agents.
- No collector/research/trading actions; Tauri deferred.

Interface requests: CC-P001 PROPOSED — safe operational metadata export; web v1 не ждёт его

Recovery: docs/control-center/GROK-PROMPT.md → RESUME. State — UI workflow, не core truth.
