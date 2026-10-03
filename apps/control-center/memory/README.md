# Memory Bank Control Center

Самостоятельная память UI workstream. Не является копией/заменой core Memory Bank.
Новая сессия: `../AGENTS.md` → `state.md` → `project.md` → `decisions.md` → `changes.md` →
[onboarding](../../../docs/control-center/ONBOARDING.md). После compaction вернуться сюда.

| Вопрос | Где ответ |
|---|---|
| Цель, границы, следующий шаг | state/project |
| Что принято, что proposal | decisions + docs/control-center/proposals |
| Product/stack/process separation | docs/control-center/01-architecture.md |
| Разрешённые данные и неизвестности | docs/control-center/02-data-map.md |
| DTO/provider/API | docs/control-center/03-contracts.md + ../contracts/types.ts |
| Текущий CC02 plan / acceptance | docs/control-center/cc02/PLAN.md + VISUAL-ACCEPTANCE.md |
| Web v1 historical plan | docs/control-center/04-phases.md |
| Git/security/demo/tests | docs/control-center/05-security-workflow.md |
| Подтверждённая безопасность handoff | docs/control-center/06-review.md |

Обновлять state после большой фазы, decisions при изменениях выбора, changes append-only;
детали features — тематические UI docs. Tests/results должны иметь commit/config/data
reference и actual result. Не заменять failure history; не считать screenshots доказательством
source integrity. Core snapshot baseline указан явно; не объявлять D50+ статус без export.


Durable execution (с CC02 — schema 2, v1 архивирован): [WORK_STATE.json](WORK_STATE.json) — task queue/leases/checks,
[STATUS.md](STATUS.md) — короткая generated сводка. Workflow не второй market source of truth.
Coordinator-only writer; `python3 apps/control-center/scripts/workflow.py --render` обновляет
STATUS из JSON; `--check` проверяет consistency. Compact review receipts — `reports/`.
Recovery/start prompts: docs/control-center/GROK-PROMPT.md; protocol: AUTONOMOUS-WORKFLOW.
