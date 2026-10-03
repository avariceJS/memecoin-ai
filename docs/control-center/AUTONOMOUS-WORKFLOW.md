# Автономный Coordinator: запуск, границы и recovery

Это расширение принятого handoff, не новый engine/adapter. Текущий scope — web v1 phases
0–7 из [04-phases.md](04-phases.md). Tauri phase8, D50+, live/research actions вне scope.
Открой [STATUS](../../apps/control-center/memory/STATUS.md): 30 секунд для текущей задачи,
проверок, blockers и next action. Source task state — WORK_STATE.json рядом; не market truth.

## Что установлено и что ещё не подтверждено

Проверено 2026-10-03 без чтения Cursor private settings, account tokens или chat stores:

| Capability | Evidence / фактический статус | Наш выбор |
|---|---|---|
| Cursor IDE | `/Applications/Cursor.app`, `cursor --version`: 3.22.12, arm64, build `3a92974361033b2051526321308c2740fe5912c0` | используем установленный IDE |
| Project rules | existing `.cursor/rules/control-center.mdc`, alwaysApply | один entrypoint, не дублировать root semantics |
| Custom agents | native `.cursor/agents/` документирован; путь найден в installed app bundle | шесть CC definitions, scoped allowlist |
| Model binding / readonly | поддержаны YAML fields `model`, `readonly`, `is_background`; local Composer2.5 token присутствует | model explicit, reviewers readonly; account run ещё не выполнен |
| Grok / Composer | официальные IDs `grok-4.7`, `composer-2.5`; Grok effort `xhigh` | Coordinator picker Extra High, executors standard tier |
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

## Model policy — configuration ≠ observed execution

- Coordinator: выбрать **Grok 4.7 → Extra High** в picker. Prompt не переключает модель.
- Все шесть custom agents: `model: composer-2.5[fast=false]`, стандартный экономичный tier.
  Это документированный ID/parameter, не выдуманный alias и не `inherit`/Auto.
- Первая bounded task P0.1: read-only cc-artifact-analyst на known safe DTO, ≤3000 chars;
  проверить **native task card** actual model/variant, не спрашивать модель о её имени.
  Записать observed model + evidence description + date в WORK_STATE. Если tool result
  не показывает модель, пользователь один раз смотрит task card; скрытые account stores
  читать нельзя. Account access / billing pool / auto-fallback здесь ещё **UNVERIFIED**.
- При mismatch/недоступности: не повторять дорогие spawns и не использовать неявный Grok
  fallback. Исправить definition/config в scope по documented supported syntax, если можно;
  иначе оставить model verification unresolved, выполнять независимый onboarding/review
  Coordinator. Когда дальше нужна delegation — внешний settings blocker с точным действием.

Наблюдение 2026-10-03. Карточка модели в tool result не появилась, и через UI её
подтвердить нельзя. Binding остаётся `UNVERIFIED`: `observed_executor` пустой, статус
`VERIFIED` без exact observed id и evidence по-прежнему запрещён. Configured policy и
YAML остаются `composer-2.5[fast=false]`. Это не блокер web v1: P0.1 можно закрыть по
карте DTO и boundary review, Phase 1 идёт дальше. Fast tier и inherit не подставлять.
- Grok-subagent только explicit escalation сложного architecture/UX/integration review,
  reason/model записать; не менять стандартную policy всех agents. Не покупать quota,
  не включать on-demand/Max автоматически. Настроенный tier не обещает нулевую стоимость.
- Built-in Explore/Bash/Browser не равны нашим pinned agents; не поручать им bulk work
  вместо именованных executors. Native read-only restrictions не фильтруют все inherited
  tools: AGENTS запреты RPC/MCP/core apply независимо от наличия tool.

## Что включить человеку в Cursor

1. Открыть **корень** текущего isolated worktree (команда ниже), Agent mode, local checkout,
   Grok 4.7 Extra High. Не cloud, не новый worktree, не main. Убедиться, что CC rule активен.
2. В Settings → Agents → Approvals & Execution выбрать **Auto-review** с доступным sandbox.
   Не Run Everything; не широкие `git`/`python`/`npm` allowlists. Не нужен permissions.json.
   Уже существующие team constraints не обходить. Если действие отклонено — безопасная
   scoped альтернатива; если невозможна, сохранить точную причину external blocker.
3. Custom subagents должны обнаружиться из project files; не требуется global config.
   На первом запуске проверить Composer standard в task card. Если его нет в account,
   не нужен secret в chat: конкретный выбор доступной модели/настройка выполняется человеком.
4. Отключить использование cloud/background deployments для этого workstream. Native local
   foreground делегирование достаточно. Sleep/restart Mac остановит computation, не core.

Официальный [Auto-review](https://cursor.com/docs/agent/security/run-modes) — convenience,
не гарантия исполнения без prompts и не замена read-only boundaries. Не редактируем
глобальные настройки/approval config за пользователя. Не создаём Cursor UI automation.

## Coordinator loop

START → root/UI AGENTS → UI memory → WORK_STATE/STATUS → onboarding architecture/design
→ Git status/log/worktree → scope/conformance → следующий unfinished dependency-ready task.

1. Краткий task brief по AGENT-TEAM, назначить role/explicit write lease; зафиксировать в state.
2. Самому решать architecture/integration; остальное native custom subagent. Максимум два
   writers на disjoint paths; no nested spawn. Definitions в [AGENT-TEAM.md](AGENT-TEAM.md).
3. Принять компактный summary, прочитать diff и фактические targeted checks; интегрировать.
4. Ordinary errors: diagnose → fix (executor) → verify. Не ждать разрешения на исправление.
5. Visual gate по DESIGN-SYSTEM; boundary gate для data/API и перед общим acceptance.
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
для независимых задач; `--visual` жёстко требует exact image перед новой visual работой. Не интегрирован в HTTP adapter.
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

COMPLETE: все current web-v1 tasks DONE, final tests/build/visual/boundary passed, docs/memory
и Git review завершены. Future Tauri/core proposals не prerequisite, остаются unavailable.
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
