# Автономный Coordinator: запуск, границы и recovery

Это расширение принятого handoff, не новый engine/adapter. Текущий scope — **CC02 visual reconstruction** из [cc02/PLAN.md](cc02/PLAN.md).
Web v1 functional foundation сохранён; его visual acceptance отклонён пользователем.
Tauri, D50/D51, новые backend capabilities и live/research actions вне scope.
Открой [STATUS](../../apps/control-center/memory/STATUS.md): 30 секунд для текущей задачи,
проверок, blockers и next action. Source task state — WORK_STATE.json рядом; не market truth.

## Что установлено и что ещё не подтверждено

Проверено 2026-10-03 без чтения Cursor private settings, account tokens или chat stores:

| Capability | Evidence / фактический статус | Наш выбор |
|---|---|---|
| Cursor IDE | `/Applications/Cursor.app`, `cursor --version`: 3.22.12, arm64, build `3a92974361033b2051526321308c2740fe5912c0` | используем установленную IDE |
| Project rules | existing `.cursor/rules/control-center.mdc`, alwaysApply | один entrypoint, не дублировать root semantics |
| Custom agents | native `.cursor/agents/` документирован; путь найден в installed app bundle | шесть CC definitions, scoped allowlist |
| Model binding / readonly | поддержаны YAML fields `model`, `readonly`, `is_background`; local Composer 2.5 token присутствует | model explicit, reviewers readonly; actual model runtime UNVERIFIED |
| Grok / Composer | официальные IDs `grok-4.7`, `composer-2.5`; Grok effort `xhigh` | Coordinator Extra High; CC02 visual roles High, routine Composer standard |
| Native commands / skills | documented skills, `/loop`; project skill/command здесь не требуется | не создаём дополнительные .cursor paths |
| CLI / SDK | `cursor --help` содержит agent; отдельный `~/.local/bin/cursor-agent` отсутствует | CLI НЕ запускали: launcher auto-installs/updates; SDK не нужен |
| Foreground/background agents | native documented, actual account availability unknown | direct local subagents; cloud/worktree isolation не используем |
| Goal / continuation | гарантированный persistent goal API не проверен | semantic task loop + repo state; не обещаем daemon |
| Approvals | native Auto-review documented; actual user/team settings не прочитаны | настройки ниже, без bypass/permissions file |
| Session/context recovery | состояние задачи наше, в Git; chat state optional | новый chat восстанавливается из repo; no automatic chat creation |

Основания: [Cursor subagents](https://cursor.com/docs/subagents) (native fields, exact
model syntax `composer-2.5[fast=false]`, model fallback и task-card verification),
[Grok 4.7](https://cursor.com/docs/models/grok-4-7),
[Composer 2.5](https://cursor.com/docs/models/cursor-composer-2-5),
[Rules](https://cursor.com/docs/rules), [Skills](https://cursor.com/docs/skills),
[Run Modes](https://cursor.com/docs/agent/security/run-modes).
Локальные строки bundle — подтверждение наличия механизма, не account entitlement.
Не извлекать credentials/настройки аккаунта для «полного доказательства».

## Model policy CC-02 — quality-sensitive delegation

Coordinator: Grok 4.7 Extra High в picker; фактическое исполнение может оставаться UNVERIFIED.
Именованные cc-ui-builder и cc-visual-reviewer: `model: grok-4.7`, documented default High.
Native параметр effort для этой модели не угадываем: default High подтверждён официальной
[страницей Grok 4.7](https://cursor.com/docs/models/grok-4-7); actual task card отдельно.
Routine artifact/adapter/test/boundary agents остаются `composer-2.5[fast=false]`.
Список IDs/syntax основан на [native subagents docs](https://cursor.com/docs/subagents).

Grok High: shell, design system, Overview, Calendar, Research/Models visualizations,
motion architecture и independent visual acceptance. Composer: fixtures/tests, ordinary
bugfixes, bounded mapping, routine component wiring после принятого design primitive.
Coordinator не раздаёт каждую мелочь дорогой модели; file leases и actual requested model
записываются в task attempts. Docs/state/shared package lock/Git — Coordinator.

Проверять actual model по tool/task card, если сведения доступны. Известно из v1, что
Task parameter с bracketed model string был отклонён; это не доказательство отсутствия
native file configuration. Не пробовать бесконечно variants и не выдумывать model IDs.
При отсутствии evidence оставить UNVERIFIED и **продолжать работу** — прямое указание
пользователя CC-02/CC-010. Ни качество, ни runtime model не доказываются self-report.
Если native override не поддержан — зафиксировать fallback/unknown; сложный design review
Coordinator выполняет дополнительно. Не менять account/settings/оплату за пользователя.

## Что включить человеку в Cursor

1. Открыть **корень** текущего isolated worktree (команда ниже), Agent mode, local checkout,
   Grok 4.7 Extra High. Не cloud, не новый worktree, не main. Убедиться, что CC rule активен.
2. В Settings → Agents → Approvals & Execution выбрать **Auto-review** с доступным sandbox.
   Не Run Everything; не широкие `git`/`python`/`npm` allowlists. Не нужен permissions.json.
   Уже существующие team constraints не обходить. Если действие отклонено — безопасная
   scoped альтернатива; если невозможна, сохранить точную причину external blocker.
3. Custom subagents должны обнаружиться из project files; не требуется global config.
   Requested model проверить по task card, если доступна; UNVERIFIED не блокирует CC02.
   Не запрашивать secret или менять account автоматически.
4. Отключить использование cloud/background deployments для этого workstream. Native local
   foreground делегирование достаточно. Закрытие GUI/IDE не является core control action.
   Сон или restart всей машины могут затронуть и core processes; не выполнять их ради UI workflow.

Официальный [Auto-review](https://cursor.com/docs/agent/security/run-modes) — convenience,
не гарантия исполнения без prompts и не замена read-only boundaries. Не редактируем
глобальные настройки/approval config за пользователя. Не создаём Cursor UI automation.

## Coordinator loop

START → root/UI AGENTS → UI memory → WORK_STATE/STATUS → onboarding architecture/design
→ CC02 audit/plan/acceptance + target/current-v1 images → Git status/log/worktree
→ scope/conformance → следующий unfinished dependency-ready task.

1. Краткий task brief по AGENT-TEAM, назначить role/explicit write lease; зафиксировать в state.
2. Самому решать architecture/integration; остальное native custom subagent. Максимум два
   writers на disjoint paths; no nested spawn. Definitions в [AGENT-TEAM.md](AGENT-TEAM.md).
3. Принять компактный summary, прочитать diff и фактические targeted checks; интегрировать.
4. Ordinary errors: diagnose → fix (executor) → verify. Не ждать разрешения на исправление.
5. CC02 gate: first browser capture → independent critique → fix → second screenshots
   на 4 viewport + fullpage → overflow/layout report → rubric → receipt validator.
   Boundary gate отдельно. Bare PASS, один crop или один Overview на ширине 768 px не являются acceptance.
6. Обновить task checks/reviews/attempts/next action; state/STATUS и нужную UI Memory Bank.
7. Scope guard + conformance + diff review → logical commit (единственный Git writer).
   Commit message содержит task IDs. После commit записать hash в next state update;
   не делать бесконечные commits только для self-referencing hash. Current commit всегда
   `git log -1`; `last_verified_commit` — предыдущий реально проверенный commit, не «latest».
8. Короткий phase report и **сразу NEXT TASK**. Не спрашивать «продолжить?».

`/loop` существует как native повторение prompt/skill, но не требуется: наша очередь конечна,
не периодический сбор. Мы не проверяли persistent `/loop`/background execution после
закрытия IDE и не используем это как guarantee. Ни SDK loop, ни cron/launchctl, ни fake goal.
При tool/turn limit сохранить state и terminal reason; transport interruption — не COMPLETE.
Возобновить существующий chat или новый через RESUME. 70–80% context не trigger нового chat.

## Durable state и recovery

`apps/control-center/memory/WORK_STATE.json` — единственный task state, Coordinator writer.
`STATUS.md` генерируется `scripts/workflow.py --render`; `--check` проверяет consistency,
task dependencies/reviews/leases/model receipt и UI scope. Missing/changed PNG даёт warning
для независимых задач; `--visual` жёстко требует exact image перед новой visual работой.
Workflow script не интегрирован в HTTP adapter.
Reports — `memory/reports/<task>.md` (краткие, versioned), большие логи — ignored `.local/`.
Сохранять источник failure, retries и interface requests. Не редактировать core Memory Bank.

Перед commit обновить JSON, выполнить `--render`, затем `--check`. Перед compaction/session
exit сохранить pending/running/review state и next action; не придумывать complete.
После reset: прочитать rules → UI memory → state → architecture/design → Git → next.
RUNNING после restart не означает действующий subagent: выяснить native session/agent IDs,
проверить scoped dirty diff; если worker потерян, снять lease после фиксации interruption,
вернуть task PENDING/NEEDS_FIX и переделегировать. Не откатывать чужие changes.
Если в state DONE, но commit отсутствует, сверить dirty patch/tests и закончить commit.
Если commit уже есть, а state старый — сверить IDs/diff и recover state, не делать feature снова.
Новый Coordinator не доверяет old checks на изменённом diff. Не запускать одновременно два
Coordinator; один local session владеет очередью, task IDs и leases. Plain JSON не distributed lock.

## Stop policy

COMPLETE: все CC02 tasks DONE, current-build matrix для всех 6 screens и final
visual/layout/semantic/boundary gates passed, docs/memory
и Git review завершены. Future Tauri/core proposals не prerequisite, остаются unavailable. User acceptance
отдельно, не автоматически ACCEPTED из reviewer PASS.
BLOCKED_NEEDS_USER: нет независимой разрешённой задачи и нужен внешний account/settings
action, credential (не присылать в chat), payment, существенное product/core interface
решение или действие, физически недоступное agent. Указать safe cause/action и pending tasks.
Не blocker: TS/lint/CSS/unit/e2e/build errors, dependency conflict в UI scope, refactor,
timeout, reread docs, первая неудача, несовершенный UI design. Missing future interface
→ proposal + locked surface; продолжать. Запрет core не обходить даже по deadline.

## Точный запуск и короткие recovery prompts

```sh
cursor -n /Users/root1/.codex/worktrees/ec89/memecoin-ai
```

В новом local Agent chat выбрать Grok 4.7 Extra High, вставить INITIAL START из
[GROK-PROMPT.md](GROK-PROMPT.md). Там же RESUME и BLOCKER RESOLVED.
Progress: STATUS.md; audit: WORK_STATE/receipts + `git log --oneline -8`.
При новом chat/IDE/Mac restart ничего переустанавливать не нужно; выполнять RESUME,
проверяя фактический branch/dirty state. Память чата optional, repo обязательный.


## CC02 durable evidence и pre-commit

V1 queue сохранена byte-for-byte в UI memory/history/web-v1-WORK_STATE.json;
активный WORK_STATE schema 2 содержит 9 CC02 tasks; old PASS не переносится. STATUS regenerated.
Visual tasks требуют artifacts receipts по cc02/VISUAL-ACCEPTANCE.md; before PNG в current-v1
неизменны. Validator `visual_gate.py --receipt ... --task ...` проверяет current source digest,
`workflow.py --check` сохраняет historical accepted digests по завершённым tasks, а CC02.8
требует current digest. Ни validator, ни hash не заменяет subjective independent review.
Схема receipt и frozen task/route matrix не ослабляются ради green. Major defects → NEEDS_FIX.
Browser harness отсутствовал в v1: implement CC02.0, не обозначать Vitest как E2E.
Recovery: root/UI rules → UI state/history → CC02 docs → Git → next action; потерянный
browser screenshot/review artifact делает review NOT_VERIFIED, не повод создавать fabricated PASS.
