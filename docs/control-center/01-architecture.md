# Control Center: продукт, архитектура и стек

Статус: выбран для UI workstream; core baseline `6a7e079`, не решение D50+.
[Scope](00-scope.md), [data map](02-data-map.md), [contracts](03-contracts.md).

## Цель первой версии

Работающее локальное приложение превращает **существующие датированные artifacts** в
понятную оператору картину: что было собрано, что проверено, что неизвестно и почему
research/trading закрыты. Успех — оператор понимает состояние evidence без чтения JSON,
а приложение не получает новых полномочий. Дизайн не должен обещать существующий бот.

Путь продукта: Observatory → read-only Research Console → отдельно разрешённая Research
control plane → Shadow/Paper → Trading Control Center. Наличие экрана не создаёт backend.
Текущий OBSERVATORY — режим UI, **не** core OBSERVE engine. SHADOW/PAPER/LIVE пока идеи.
F01 — study ID, GRU — engineering candidate, ни одно не встроено в базовые provider types.

## Разделение процессов и доверия

```text
RPC → frozen collector → causal data/features → [future model/policy/risk/execution]
               core-owned services / contracts / immutable artifacts
                                     │
                   reviewed, minimal, non-protected exports
                                     ▼
             UI artifact adapter → versioned DTO → React application
```

Сегодня допустим только путь:

```text
9 pinned committed JSON files (offline development export)
 → whitelist field projection + protected metadata-only rows
 → published/observatory-v1.json + manifest
 → stdlib loopback read-only API
 → будущий React frontend
```

Runtime не знает корневого пути collector/registry. Он не импортирует core Python и не
выполняет core tools. Не запускать Ledger()/sync()/verifier «ради статуса»: некоторые
конструкторы пишут locks/dirs, а verifier может читать raw/holdout. В v1 нет direct
artifact mounts, daemon management, database, IPC control bus, RPC client или ML runtime.
Projection детерминированно воспроизводится; source pins/date/revision видны в интерфейсе.
Она не редактируется как доменная база и не выдаёт admission. Не разрешать users editing
facts, outcomes, metrics, model selection, classifications или target semantics.

Core процессы принадлежат своему supervisor, не UI. Закрытие браузера/adapter/Tauri
никогда не вызывает STOP/kill/unload core. UI adapter можно запустить отдельно от frontend;
их общий launcher позднее может владеть только своими дочерними UI процессами.

## Product IA и корректировки

| Экран | Полезный v1 | Что не притворяется существующим |
|---|---|---|
| Overview | Дата/commit evidence, recorded D43/F01/D49, blockers, источник знаний | Live green health, online mode, активная модель |
| Calendar / Corpus | D43 timeline6окон; D48 planned336; development stats/revision | Новые D43 outcomes, скрытые holdout metrics, автоматически inferred missed |
| Sources / Pools | Fixed3pool IDs, D49 historical latency/freshness/qualification | Token names/logos, USD price/liquidity, volume, trade feed |
| Research | Protocol, numeric checks отдельно от admission, recorded fits/budget, holdout lock | Запуск training, «0 exposures глобально», actual winner/edge |
| Models / Benchmarks | D44 engineering runs, CPU/MPS measured cases/config shapes | Champion/current model, model activation, inference stream |
| System | UI adapter availability отдельно от recorded build/tests | Current core process health/CPU/RAM/MPS/remote machine telemetry |
| Replay | Пока unavailable с причиной | Новый simulator, candles/trades, replay holdout |
| Trading | Небольшая capability page LOCKED | Fake balances, PnL, controls, emergency stop |

Навигацию группировать по доступным задачам, не девять одинаково пустых dashboards.
Overview → Calendar/Corpus → Research → Models → Sources → System; Trading/replay как
явно недоступные capabilities. Скрытая кнопка не security boundary: write API отсутствует.
Market назвать «Sources / Pools · recorded evidence», пока нет безопасного market export.
Пустые predictions/capital allocation не иллюстрировать случайными числовыми рядами.

## Состояния и UX

Показать одновременно независимые оси:
1. **Происхождение:** REAL EVIDENCE / DEMO. Реальные измерения на synthetic tensors —
   REAL engineering evidence + отдельный badge SYNTHETIC INPUT, не реальные market results.
2. **Доступ:** AVAILABLE / UNAVAILABLE / PROTECTED; LOCKED — control/admission state.
3. **Время:** RECORDED SNAPSHOT с source date/ns; STALE при ошибке обновления уже показанного
   snapshot. UI servedAt ≠ observation/availability time; offline artifact не live heartbeat.
4. **Целостность:** PINNED BYTES = совпадение export bytes; не raw semantic verification,
   не подпись провайдера, не доказательство unopened holdout.
5. **Назначение:** ENGINEERING / FROZEN DESIGN / будущий ADMITTED RESEARCH только с contract.

Null → «неизвестно / не опубликовано», не0 и не success. No selection≠cash decision.
Pending в historical report остаётся «pending на дату evidence», не становится missed
при прохождении локальных часов. Failed collection, QA и execution audit независимы;
overall берётся из authoritative outcome, не вычисляется логическим AND неизвестных.
Показывать first receipt отдельно от actual start; в pinned D43 actual start не опубликован.

Dark desktop-first, спокойные нейтральные поверхности, tabular numerals, чёткие заголовки,
компактные таблицы и осторожные status colors. Красный=failure, amber=unknown/stale,
синий=information, серый=unavailable, protected=icon+текст. Цвет никогда не единственный
код. Никаких profit fireworks, казино-градиентов, мигающего fake ticker.
Сайдбар сворачивается на1280px, таблицы доступны клавиатурой; charts имеют текстовую/табличную
альтернативу. Timezone UTC/Europe/Moscow переключаема, с явным zone label; dates из source.

## Стек

Выбран React + TypeScript strict + Vite SPA в `apps/control-center/web/`, npm lockfile
внутри web. Без SSR/Next, Electron и SwiftUI-only. Это соответствует web-first интерфейсу,
не связывает core с JS и позволяет сохранить frontend при будущем Tauri packaging.

| Область | Решение | Когда добавлять |
|---|---|---|
| Runtime | Node22.12+ совместимый с Vite; exact local Node/package pins вPhase0 | Только UI environment, не core env |
| UI | React19, TS strict, Vite stable | Phase1; проверить registry/official docs, pin точные версии |
| Router | React Router7 declarative | Phase1, без server actions |
| Remote state | TanStack Query5 | Snapshot cache/retry=false/stale errors, не Redux |
| Local state | React state/context + URL filters | Не хранить domain facts в localStorage |
| Tables | TanStack Table8 | Phase2/3; small views native tables до необходимости |
| Charts | Recharts3 для bounded report charts; calendar CSS/SVG | По мере экранов; gaps не интерполировать |
| Large lists | TanStack Virtual3 только после измеренного bottleneck | 336окон обычно не требуют virtualization |
| Styling | CSS custom properties/modules; small owned component kit | Phase1, без тяжёлого design framework |
| A11y primitives | Radix Dialog/Tooltip по необходимости | Точное назначение/lock, не весь набор заранее |
| Validation | Zod4 boundary validation в web, matching DTO v1 | Phase1; неизвестная major → error |
| Tests | Vitest + Testing Library + Playwright + axe | Unit/interaction, HTTP contract, E2E/a11y |
| Backend | stdlib Python3.11+ adapter scaffold | Отдельный от memecoin packages; без FastAPI пока GET достаточно |
| Desktop later | Tauri2 least-capability shell | После web acceptance и отдельного security review |

Это UI tooling choices, не запрет обновлять minor dependencies с review. Не ставить всё
сразу. Exact npm versions/lock ещё **не созданы**: Grok фиксирует проверенную совместимую
матрицу вPhase0. Не `npm install` в repo root, не менять core locks/Makefile/CI.

Проверены официальные документы2026-10-03:
[Vite](https://vite.dev/guide/) описывает React/TS templates и Node20.19+/22.12+;
[TanStack Query](https://tanstack.com/query/latest/docs/framework/react/overview) даёт
server-state cache, которую используем как производную;
[Tauri capabilities](https://v2.tauri.app/security/capabilities/) позволяет ограничивать
IPC. Не переносить current localhost origin policy в Tauri без отдельного review.
[Recharts accessibility](https://github.com/recharts/recharts/wiki/Recharts-and-accessibility)
— базовые keyboard/a11y возможности не заменяют тесты приложения. Никаких обещаний
performance без измерения наших shapes.

## Refresh и будущая эволюция

V1 immutable bundle: GET на входе + ручное refresh; cache по snapshotId. Автоповтор ошибок
выключен, hidden tab не polling. Realtime badge запрещён. При будущем core-owned atomic
operational export достаточно poll5–15s только видимой вкладки с backoff и last-known-state
маркировкой; initial error не показывать как empty healthy system. Watcher/SSE только
после published event/sequence/retention/gap/reconnect contract; WS сейчас не нужен.

Два Mac: предусмотреть opaque nodeId/provider metadata в следующей API версии, не создавать
remote agent, SSH executor, discovery, общую БД или distributed training. Future read API
и **отдельная** authenticated command API не смешиваются. Prediction payload обязан нести
model/procedure/source/availableAt/horizon/uncertainty; торговые команды/limits/stop semantics
определяет core decision. Появление нового adapter расширяет оболочку, не переписывает
её навигацию/состояния, но сегодняшняя оболочка не реализует будущую торговую семантику.
