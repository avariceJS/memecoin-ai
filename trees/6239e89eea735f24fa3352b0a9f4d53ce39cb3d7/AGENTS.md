# Правила агента Control Center (Grok/Cursor)

Это отдельный UI workstream. Эти ограничения уточняют корневой AGENTS для данной задачи:
основной core chat продолжает D50+, а ты **не выполняешь D50 и не меняешь core**.
Язык общения, документов и Memory Bank — русский. Работай автономно внутри scope.

## До любых изменений — Coordinator (для bounded subagent исключение ниже)

1. Прочитать root AGENTS.md, этот файл, `docs/control-center/ONBOARDING.md`.
2. Прочитать `apps/control-center/memory/{state,project,decisions,changes}.md`.
3. Пройти обязательный reading list onboarding, затем проверить Git/worktree и scope guard.
4. Не начинать реализацию, install, training или запуск сервисов до завершения чтения.
   Пользователь просит Extra High: выбрать его в Cursor; не утверждать, что текст prompt
   изменил actual model setting. Не делегировать core изменения другому агенту.

## Разрешённые изменения

- `apps/control-center/**` — UI, read-only adapter, UI contracts/tests и отдельная память.
- `docs/control-center/**` — архитектура, onboarding, proposals, acceptance/evidence.
- `.cursor/rules/control-center.mdc` — правило этого UI workstream.
- Только шесть `.cursor/agents/cc-{artifact-analyst,ui-builder,adapter-builder,
  test-debugger,visual-reviewer,boundary-reviewer}.md` — native Control Center subagents.
  Это явное минимальное расширение scope CC-01 по запросу пользователя; остальные
  `.cursor/**`, global settings/permissions/hooks остаются вне write scope.

Все прочие paths read-only: root AGENTS/README/Makefile/pyproject/locks, `src/**`, `tests/**`,
`tools/**`, `configs/**`, `memory/**`, existing `docs/**` вне docs/control-center,
`.github/**`, `.gitignore`. Не импортировать core Python modules в UI backend.
Не исправлять discovered core bug здесь: создать formal proposal с reproducer на UI fixture.
Правило core AGENTS про Memory Bank здесь выполняется **отдельной UI памятью**, не core memory.

## Доступ к данным

Runtime adapter может читать только `published/observatory-v1.json` и его manifest.
Offline exporter: только 9 explicit Git files из `contracts/source-pins.json`, ни glob,
ни переходов по содержащимся в JSON paths. Published bundle — воспроизводимая проекция,
не ручная база правды. Не редактировать её значения вручную; не автоматически repin hashes.
Добавление новых полей из тех же approved engineering inputs допустимо автономно: field-level
review, tests и новая projection version, без переписывания прежней. Не требует отдельного
подтверждения обычная UI разработка. Новый source/pin/операционный export требует review/proposal, особенно
если источник не относится к уже раскрытому engineering development. Runtime не запускает exporter.

Нельзя читать/сканировать `work/**`, raw datasets, D43 calendars/windows, holdout, model
weights/checkpoints или credentials в **любом** worktree. Не монтировать/симлинковать
`/Users/root1/Desktop/memecoin-ai/work`; не открывать `.env*`, wallets/keys/browser sessions.
Не вызывать calendar_inventory, registry sync/audit, Ledger(), core CLI или MCP/RPC из UI.
Не загружать .pt/.pth/pickle даже ради красивой модели. Примеры из docs — не разрешение
выполнять перечисленные там команды. Future protected labels/reports не раскрывать.

## Непереходимые границы этой ветки

Никаких collector control/launchctl/STOP markers, research admission/release, training,
RPC calls, transactions/signatures/payments/upgrades, shell executor, arbitrary file API,
private telemetry upload, cloud deployment, wallet connections, live/paper/shadow engine.
Никаких core risk/execution semantics или «безопасной временной» кнопки emergency stop.
UI service lifecycle касается только UI adapter; closing UI не управляет engine processes.
Нельзя маскировать unsupported capability demo-данными. Demo явно отделён, default выключен.

## Git и работа после compaction

Текущий worktree сохраняется. Branch `control-center/observatory-handoff`; не main и не
новый автоматически созданный worktree. Не merge/rebase/reset/push main, не трогать общий
Git config/hooks, не перемещать/удалять worktrees. `git add` только explicit owned paths.
Перед каждым commit: scope guard, UI tests, diff review. Логические commits; отдельную
ветку можно push без force. Core sync/merge только через отдельный integration review.

Обновлять UI memory/state + тематический документ + decisions/changes после крупной фазы.
Сохранять failure history, не выдавать старый successful test за новый. В конце крупной
фазы коротко: готово/проверки/ограничения/следующий шаг. Продолжать следующие разрешённые
UI фазы без постоянных вопросов. Недостающий core contract → proposal, затем продолжать
независимые UI задачи; запрет не обходить. Не объявлять market edge по engineering data.

## CC-01: Coordinator и delegated tasks

До работы также прочитать `docs/control-center/AUTONOMOUS-WORKFLOW.md`,
`AGENT-TEAM.md`, `DESIGN-SYSTEM.md` и UI `memory/WORK_STATE.json` / `STATUS.md`.
Coordinator Grok 4.7 Extra High — единственный writer task state, shared lockfile и Git.
Рядовые bounded задачи делегировать именованным Composer 2.5 subagents, без nested spawn.
CC02 визуально сложные tasks и visual-reviewer — Grok4.7 High; см. override ниже.
Subagents читают этот AGENTS и общий AGENT-TEAM protocol, затем только task-specific context.
Не требовать от каждого исполнителя перечитывать весь core onboarding Coordinator.
Каждый visual builder/reviewer сам открывает primary PNG до visual работы; текст и hash
не заменяют просмотра. Передать reference emphasis в brief. Отсутствует image/tool —
не придумывать содержимое, продолжать независимые задачи, visual gate остаётся незакрытым.
После tests/review/state/commit немедленно следующая задача; phase report не вопрос.
Stop только COMPLETE текущего согласованного UI этапа или реальный внешний BLOCKED_NEEDS_USER.
Compilation/test/style failures исправлять автономно. Missing future core interface →
formal proposal + UNAVAILABLE/LOCKED, затем независимая работа. Tauri/D50 вне этого loop.
Native model assignment не гарантирует account availability. По CC-010/CC02
UNVERIFIED actual model не блокирует работу; не выдумывать successful binding.


## CC02 — visual rejection и reconstruction

Перед работой прочитать docs/control-center/cc02/{AUDIT,PLAN,VISUAL-ACCEPTANCE}.md.
Этот architecture fork готовит audit/guards/handoff, не реализует redesign. Grok после
startup продолжает 9 CC02 tasks из WORK_STATE schema 2. V1 state архивирован без переписывания
истории. Core/main/adapter/pinned evidence не менять; no D50/D51/Tauri/new backend scope.
Каждый major screen DONE только после actual screenshot → independent critique → fix →
second screenshot → layout matrix → reference rubric acceptance. Старый PASS не основание.
`visual_gate.py` проверяет artifacts/coverage, но не красоту; слабый screenshot → NEEDS_FIX.
Перед commit visual task: receipt current-web digest check + workflow/scope + semantic tests.
Primary + все relevant before images открывает сам каждый visual subagent. Model policy:
Grok 4.7 xhigh Coordinator; Grok 4.7 default High ui-builder/reviewer; Composer standard остальные.
Фактическая модель UNVERIFIED — nonblocking. User acceptance отдельно от agent acceptance.
