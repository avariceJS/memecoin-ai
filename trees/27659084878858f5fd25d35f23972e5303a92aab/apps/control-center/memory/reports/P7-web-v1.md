# P7.2 — handoff web v1

2026-10-03. Ветка `control-center/observatory-handoff`. Core baseline `6a7e079`.
Main не объединялся.

Чистая установка `npm ci` в `web/` выполнена исполнителем: 153 пакета, 0 vulnerabilities.
Coordinator после этого повторно прогнал vitest 44, tsc, vite build, scope guard,
exact projection и 22 adapter-теста. Все PASS. Lockfile не изменился.

Ограничения, которые остаются:
- Native task card модели UNVERIFIED; configured policy `composer-2.5[fast=false]`.
- Нет live/paper/trading/replay. CC-P001 не принят core.
- Node 24.5.0 даёт EBADENGINE для части jsdom-зависимостей, которые просят 24.15+.
- Vite предупреждает, что PostCSS plugin не передал `from`, и что JS chunk больше 500 kB.
- Tauri и D50 вне этой оболочки.

Финальный visual review и boundary review — PASS.
