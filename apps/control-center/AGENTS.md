# Правила агента Control Center (Grok/Cursor)

Это отдельный UI workstream. Эти ограничения уточняют корневой AGENTS для данной задачи:
основной core chat продолжает D50+, а ты **не выполняешь D50 и не меняешь core**.
Язык общения, документов и Memory Bank — русский. Работай автономно внутри scope.

## До любых изменений

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
