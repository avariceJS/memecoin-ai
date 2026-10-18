# CC-00 — bounded architecture/handoff, 2026-10-03

Core baseline: `6a7e079` (D49). Worktree: `/Users/root1/.codex/worktrees/ec89/memecoin-ai`.
Dedicated branch: `control-center/observatory-handoff`. Это независимый UI workstream,
не D50 и не новая версия ML/data/research/execution architecture.

План: (1) перечитать contracts и audit actual artifacts; (2) выбрать read-only границу;
(3) описать продукт, стек, data map, DTO/API, security, фазы и workflow;
(4) дать маленький runnable adapter scaffold + safety tests, без большого frontend;
(5) подготовить отдельную память/Grok prompt; (6) self-review, commits только этой ветки.

Вариант A: Grok продолжает здесь после handoff. Новый worktree не нужен. Main в другом
worktree и продолжает D50+; не переключать его ветку, не merge/rebase/push main. Не
редактировать core Memory Bank: у UI отдельная память. Core связь отражена только здесь.

Продуктовые коррекции:
- Read-only Observatory показывает датированные evidence, не симулирует live health.
- Source of truth — core artifacts/contracts/receipts; UI projection только производная.
- Даже read-only импорт core Python опасен: Ledger constructor создаёт файлы/lock,
  registry sync пишет, D43 inventory может читать holdout. Никаких таких imports/CLI.
- UI runtime читает только review-approved минимальный published bundle внутри UI app.
  Offline exporter использует конечный список frozen Git evidence с exact hashes.
  `work/**`, protected raw, credentials, checkpoints и operational root не монтировать.
- Development метрики и engineering smoke отделить от F01 research. Нет current model,
  prediction feed, balances, positions, trading controls или emergency-stop contract.
- Core процессы не принадлежат GUI; закрытие UI может остановить только UI adapter.
- Настоящая operational telemetry требует core-owned safe export; formal proposal вместо
  самостоятельного чтения служебных файлов. Полное отсутствие live feed не скрывать.

Исторический пример пользователя Window#1/134mature/executionFAIL — UX сценарий, не
установленный факт. В pinned D43 evidence есть window0:180snapshots/174usable/135mature,
window1 pending на recorded time. Сейчас его фактическое состояние UI не знает.

## CC-01 — visual/team follow-up, 2026-10-03

Поверх принятого CC-00: primary visual contract, native Cursor team, durable execution
state, autonomous/recovery prompts и bounded conformance. Adapter/data map/core не
переделывать; большой frontend остаётся Grok. Scope явно расширен только на шесть
`.cursor/agents/cc-*.md`, перечисленных в UI AGENTS и exact allowlist scope guard.
Основание: пользователь разрешил минимальное project-level расширение для custom agents;
официальный Cursor contract использует `.cursor/agents/`. Это не разрешение другим paths,
permissions.json/hooks/global settings. Core/root memory и источники projection неизменны.
