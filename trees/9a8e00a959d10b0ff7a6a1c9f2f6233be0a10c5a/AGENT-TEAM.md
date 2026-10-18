# Команда и bounded task protocol

CC02: Coordinator — Grok 4.7 Extra High; native Cursor definitions — шесть точных
`.cursor/agents/cc-*.md` из UI AGENTS. Policy/model verification в
[AUTONOMOUS-WORKFLOW.md](AUTONOMOUS-WORKFLOW.md). Definitions — инструкции, не OS sandbox.
Core read-only и data denylist действуют для всех, включая inherited MCP tools.

| Agent | Работа / минимальные дополнительные inputs | Writes / предел |
|---|---|---|
| cc-artifact-analyst | approved projection + types + 02-data-map, найти поля/units/missing semantics | readonly; компактный mapping, не открыть raw producer paths |
| cc-ui-builder | shell/pages/components и charts/timelines/calendar; types + design + PNG + assigned files | только назначенные web files, no API semantics |
| cc-adapter-builder | UI provider/validator/view model, capability discovery, normalization + tests; 03-contracts/types | assigned UI files; producer/schema core не менять, published values не править |
| cc-test-debugger | unit/integration/e2e/build/performance logs; reproduce→fix→verify | assigned UI tests и code; no blanket rerun/core fixes |
| cc-visual-reviewer | actual screens vs primary PNG, density/contrast/states, screenshot review | readonly; не «починить» утверждённые данные ради красоты |
| cc-boundary-reviewer | read-only, protected-before-read, no RPC/control/secret, origins, no second truth | readonly; не открывать запрещённое даже ради аудита |

Visualization объединена с UI builder: общий design/React контекст, отдельный bounded
chart task при необходимости. Docs/state-maintainer — функция Coordinator, не седьмой
writer; краткие doc edits можно поручить builder только с explicit file lease, state/Git
никогда. Сложные UX/architecture/integration решает Coordinator; CC02 ui-builder и visual-reviewer используют configured Grok 4.7 (default High).
Artifact/adapter/test/boundary roles — Composer 2.5 standard. UNVERIFIED binding не блокирует
работу; фиксировать requested/observed отдельно. Routine wiring/fixes поручать Composer,
сложную визуальную композицию и приёмку — Grok High. Не делегировать микрозадачи ради числа агентов.

## Передача задачи

Coordinator сначала читает весь onboarding. Исполнитель читает root AGENTS, UI AGENTS,
этот protocol, свою definition и конкретный brief. Не форкать ему историю чата/весь repo.
Он не обязан читать всю root Memory Bank: curator context и approved fields достаточны.
Не использовать codebase-wide search по raw: только указанные safe paths.

Brief — не более примерно 800 слов; включает:

```
TASK ID / OBJECTIVE / phase acceptance criteria
BASE COMMIT + current uncommitted dependencies (не предполагать clean HEAD)
ROLE / configured model / observed model status
READ: точные файлы/sections и contract versions; минимальный контекст
WRITE LEASE: конечный список files/subdirectory; запрещённые shared files
INPUT: REAL recorded / ENGINEERING / synthetic fixture qualification
REFERENCE: path + SHA; открыть самим; EMPHASIS: нужные элементы композиции
DONE: measurable acceptance + targeted commands/tests + required review
RETURN: compact schema; logs в apps/control-center/.local/tasks/<id>/
NO: core/raw/holdout/credentials/RPC/control/training/Git/nested spawn
```

Coordinator фиксирует assignment в WORK_STATE **до** delegation. Один writer на path;
максимум 2 независимых writing tasks, reviewers могут параллельно только по стабильному
snapshot diff. UI builder и adapter-builder не редактируют общий DTO одновременно.
package.json/lockfile, state/STATUS, agent/rule definitions, scope guard и commits —
Coordinator only. Shared schema меняется последовательно, затем consumers. Не создавать
дополнительные worktrees/cloud copies. `is_background: false` по умолчанию; параллельные
explicit native calls только для непересекающихся leases, без unattended services.

Task boundary: одна операторская цель, обычно 1–5 связанных файлов. Это ориентир
контекста, не лимит времени или необходимых исправлений. Скорость закрытия tasks не KPI.
Timeout не blocker: уменьшить задачу/диагностировать, сохранить попытку, продолжать.
Subagent не пишет COMPLETE всей фазы. Его PASS = кандидат для интеграции/review.

## Возврат (≤ 3000 символов / примерно 400 слов)

```
STATUS: PASS | NEEDS_FIX | BLOCKED_EXTERNAL
TASK: id + цель
FILES CHANGED: список (или none)
WHAT WAS DONE: 2–4 результата
TESTS: команды, pass/fail/count; NOT_RUN с причиной, не скрывать failure
IMPORTANT FINDINGS: поля/контракты; visual tasks: image viewed + SHA + наблюдения
RISKS: реальные ограничения, без общих disclaimers
NEXT / ESCALATION: следующий конкретный шаг; safe log path если нужно
```

Не возвращать npm/browser/test transcripts, JSON, source listings, exploratory reasoning.
Полные временные logs/captures остаются в ignored `.local/`, без secrets.
CC02 accepted PNG, receipt и bounded layout/check evidence обязательно committed в
`docs/control-center/references/cc02-final/`; summary — UI `memory/reports/`. Не сохранять market raw.
Reviewer readonly возвращает summary, Coordinator сохраняет receipt. Все фактические
failed attempts сохраняются в task attempts/changes, retries не превращать в «сразу PASS».
Статус BLOCKED_EXTERNAL одной задачи не останавливает независимую очередь.

## Integration / review

Coordinator проверяет diff/lease, не доверяет тексту PASS без targeted checks. Test-debugger
может чинить ordinary failures внутри explicit lease, затем повторно проверить.
Major visual changes → отдельный visual-reviewer с image + actual screenshots, fix,
verification pass. Data/API/provenance/capabilities → boundary-reviewer; перед web COMPLETE
оба review обязательны. Reviewers не авторы того же patch. Agent reset → не выдавать
прежний PASS за проверку нового diff. Записывать reviewed commit/diff hash в receipt.


Перед каждой visual implementation/review и перед visual acceptance:
`python3 apps/control-center/scripts/workflow.py --visual` (exact reference check),
затем лично открыть PNG. Команда не заменяет просмотра. Общий `--check/--render`
при missing/changed image предупреждает, но сохраняет возможность независимой работы;
предыдущие review receipts остаются историческими, новый visual PASS запрещён.


## CC02 quality override (2026-10-04)

До visual task дополнительно читать cc02/AUDIT.md, PLAN.md, VISUAL-ACCEPTANCE.md;
лично открыть primary и relevant current-v1 PNG. В brief добавить operator question,
expected composition, visual emphasis, source fields, responsive matrix, receipt path.
Оценка «страница существует + tests green» запрещена. Каждый visual return включает:
что плохо относительно target, что изменено, что ещё не достигает качества; full report
Coordinator сохраняет в committed receipt. Неподтверждённый model binding не blocker.

Routine Composer tasks: semantic fixtures, wiring готовых primitives, isolated bug fixes,
logs/measurement. Grok High: shell, Overview, calendar, research/model visualizations,
motion и independent final review. Роль ui-builder использует High; простую Composer
правку назначить test-debugger с явным UI lease, не переключать все роли на Grok.
Reviewer ID отличается от builder; Coordinator не заменяет external review своим self PASS.
Цель оценки качества: сколько операторских задач принято, а не сколько pages сделано за час.


Final visual review разбивается на bounded review **по одному screen**, каждый с 10 concise
axis reasons и findings в пределах compact return. Coordinator сохраняет эти ответы verbatim
в structured receipt, затем объединяет coverage; не пишет critique за reviewer. Shared shell
и final cross-screen consistency — отдельный короткий integration review. Если доказательная
часть не помещается, допускается отдельный structured reviewer report без exploratory logs;
summary остаётся компактным. Передача полного отчёта — исключение для acceptance evidence,
не повод пересылать source listings или browser transcripts.
