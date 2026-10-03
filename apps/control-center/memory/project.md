# Продукт и scope Control Center

Memecoin AI Control Center — будущая операторская оболочка независимого engine.
Сегодня: полезная Read-only Observatory по реальным сохранённым evidence. Завтра:
Research Console; затем Shadow/Paper/Trading только по отдельным core contracts.

Engine/source/ML/policy/risk/execution остаются в core. Закрытие GUI не останавливает core
services. UI не содержит model selection/causal target math, не adjudicates permissions,
не второй source of truth. Projection вpublished — детерминированный производный cache.

Рабочие изменения только apps/control-center, docs/control-center, собственный Cursor rule.
Всё core read-only; work/raw/holdout/credentials вообще не открывать. Не выполнять D50+,
не исправлять core ради удобного UI. Не запускать даже engineering training из этой ветки.

Продуктовые приоритеты: Calendar/Corpus → provenance/gates/readiness → engineering model
benchmarks → source qualification. Визуальный стиль dark professional, desktop-first,
readable dense tables, explicit unknowns, keyboard/a11y. Нет crypto-casino и fake live feed.

React/TS/Vite SPA + local stdlib read adapter; позже Tauri. UI models/providers versioned,
model kind/study ID opaque, не hardcoded GRU/F01 как единственно возможная архитектура.
Future core interfaces обсуждаются proposals, не внедряются frontend agent.

Пользователь доверил Grok автономную разработку UI в этой dedicated branch после чтения
правил. Logical commits и scoped branch push разрешены; merge/main push запрещены.


CC02 (2026-10-04): функциональный web v1 сохраняется, visual acceptance REJECTED.
Текущий scope — presentation/UX reconstruction. Порядок: shell → Overview как эталон →
остальные страницы. Data correctness не заменяет visual quality; tests + independent
browser screenshot review обязательны. Current plan: docs/control-center/cc02/PLAN.md.
Существующий write scope также включает шесть exact `.cursor/agents/cc-*.md` по CC-007;
новое расширение permissions в CC02 не требуется.
