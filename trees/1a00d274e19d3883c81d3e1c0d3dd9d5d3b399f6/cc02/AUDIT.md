# CC-02 — independent architecture / design audit

2026-10-04. Baseline code `b97c7b2`, dedicated branch `control-center/observatory-handoff`.
**Вердикт: функциональный foundation сохранить; visual acceptance web v1 отклонён.**
Это audit/handoff, не выполненная visual reconstruction. Core/D43/F01/root memory вне scope.

Primary [target](../references/dashboard-concept-v1.png) и **все 10** пользовательских PNG
[baseline manifest](baseline.json) открыты лично основным auditor и двумя independent
read-only reviewers. Фактический каталог — `docs/control-center/current-v1/`, не предполагаемый
references/current-v1. Файлы сохраняются на исходных местах и включаются в Git как before.
PNG имеют разные visual scales; CSS viewport/DPR/zoom и точный capture commit пользователя
неизвестны. Не выдаём image pixels за измеренный CSS viewport и не утверждаем, что
screenshots доказывают движение/скорость/keyboard behavior. Код и current screenshots
согласуются в перечисленных дефектах. Старые ignored review screenshots не удаляются.

## Оценка 1–10

| Шкала | Балл | Основание |
|---|---:|---|
| Architecture | 8 | Независимый read-only adapter, validated API, router/query/outlet; transport не нужно менять |
| Code quality | 6 | Небольшие компоненты/TS, но повторный CSS, raw-string presentation и отдельные semantic holes |
| Functionality | 7 | Все основные evidence views полезны, нет fake controls; exploration ограничена |
| UX | 4 | Технические поля/оговорки вместо операторских вопросов, слабая progressive disclosure |
| Visual design | 3 | Однотонные boxes, пустота, нет сильного композиционного центра и качественного rail |
| Reference similarity | 3 | Navy/cyan/rail присутствуют; пропорции, hierarchy, accents/иконки/плотность существенно иные |
| Responsiveness | 3 | Breakpoints есть, но overflow виден уже на больших screenshots; rail с одинаковыми точками |
| Maintainability | 6 | Framework пригоден, страничный CSS и слабые presentation primitives мешают consistency |

Это экспертные оценки результата, не benchmark и не измеренные проценты similarity.
Data boundaries сильнее визуального слоя; «очень хорошо» не значит absence of frontend
semantic bugs: DEMO и batch comparison ниже требуют regression tests.

## TARGET vs CURRENT: страницы

| Страница / before | Главная потеря относительно target | Очевидные defects / решение |
|---|---|---|
| Overview, image.png + image copy.png | Центральная рабочая область занята 6 probe bars вместо обзора evidence; 4 bottom cards почти пусты | KPI values на разных baselines; repeated RECORDED banners; причины capabilities вылезают и накладываются; shell bullets. REPLACE composition: corpus timeline + research/source side panels |
| Calendar, image copy 2/3.png | D43 похож на список с полосками; D48 вообще не calendar matrix | 24 tiny columns × длинные слова; 336 повторов DESIGN/PROTECTED; overlap; крайние axis labels вне panel; левый corpus panel растянут пустым по высоте правой сетки. REPLACE visual geometry |
| Research, image copy 4.png | Основной блок — boolean/field report, справа длинная колонка | Raw missingReviews, duplicated 0/114/135 counters; пустая левая нижняя половина. Gate/dependency diagram с explanatory detail, не «percent ready» |
| Models, image copy 5.png | Две большие таблицы без comparison visualization, ощутимые gutters | Model-state identifiers плохо помещаются; плотность текста без иерархии. Plot matched benchmark groups + architecture/run summary, tables secondary |
| Sources, image copy 6.png | Таблица provenance — главный визуальный объект, а не source/evidence relationship | Blocker IDs выходят за aside; короткие карточки и растянутые пустоты. Static topology + компактные measured probes, provenance drawer |
| System, image copy 7.png | Почти всё состояние в узкой правой полосе; огромный пустой центр | Reason block вылезает; nested future surfaces шире parent; hashes дробятся на много строк. Matrix в основной области; adapter response отдельно |
| Replay, image copy 8.png | Пустая technical error page | Очень крупный reason code и избыточный implementation prose. Красивый compact unavailable state, no import action |
| Trading, image copy 9.png | Пустая page с нерелевантным evidence FactSummary | Нет понятной product hierarchy, повторные counters unrelated to trading. Locked surface, без fake buttons/balance и irrelevant summary |

## Полная visual rubric по страницам (1 плохо → 5 target-quality)

O=Overview, C=Calendar, R=Research, M=Models, S=Sources, Y=System, P=Replay, T=Trading.
В future locked screens плотность оценивается по полезной ясности, не числу fake widgets.

| Критерий | O | C | R | M | S | Y | P | T |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Composition | 2 | 2 | 2 | 2 | 2 | 1 | 2 | 1 |
| Hierarchy | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 1 |
| Useful density | 2 | 1 | 2 | 3 | 3 | 1 | 2 | 1 |
| Typography | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 |
| Card geometry | 2 | 1 | 2 | 3 | 2 | 1 | 2 | 2 |
| Whitespace | 1 | 1 | 1 | 2 | 2 | 1 | 2 | 1 |
| Alignment | 2 | 1 | 3 | 3 | 2 | 1 | 3 | 3 |
| Rhythm | 2 | 1 | 2 | 2 | 2 | 1 | 2 | 2 |
| Overflow containment | 1 | 1 | 2 | 2 | 1 | 1 | 3 | 3 |
| Wrapping / long strings | 1 | 1 | 2 | 2 | 1 | 1 | 2 | 2 |
| Tables/details suitability | 2 | 3 | 2 | 3 | 3 | 2 | 2 | 2 |
| Charts/information encoding | 2 | 1 | 1 | 1 | 2 | 1 | 3 | 3 |
| Navigation | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 |
| Visual identity | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 |
| Consistency | 3 | 2 | 3 | 3 | 3 | 2 | 3 | 2 |
| Perceived quality | 2 | 1 | 2 | 2 | 2 | 1 | 2 | 2 |
| Reference family / professional control room | 2 | 1 | 2 | 2 | 2 | 1 | 2 | 2 |

Главный вывод: недостаток не лечится сменой цвета/radius. Нужно иначе распределить внимание,
разделить operator labels и технические explanations и ввести общую геометрию компонентов.
«Ощущение живой системы» сейчас не подтверждено: source содержит главным образом hover
transitions. Статичные PNG не измеряют motion, но visual flow/связи отсутствуют уже в композиции.

## KEEP / REFACTOR / REPLACE / DELETE

Все paths ниже относительно `apps/control-center/web/src/`; удаление — **после** миграции,
не в этом audit. Provider contracts и published projection не переписывать.

| Класс | Части | Действие / причина |
|---|---|---|
| KEEP | api/client.ts, parse.ts, schema.ts, hooks.ts; main.tsx QueryClient | Loopback GET, runtime validation, strict protected shape, ns strings, no demo-on-error; no polling/focus-refetch |
| KEEP | routes.tsx, outlet contracts, calendar/timeZone.ts, snapshotFacts.ts, overview/selectors.ts | Механика навигации/данных пригодна; не связывать redesign с transport rewrite |
| KEEP | semantic unit tests + Python adapter tests | Pending ≠ missed, unknown ≠ 0, counts ≠ permission, no weights/holdout/actions |
| REFACTOR | shell/AppShell.tsx | Сохранить data loading/outlet; отделить shell presentation и consolidate provenance bar |
| REFACTOR | styles/tokens.css, layout.css, components/status/** | Typography/spacing/surface roles и единый truthful presenter, reason details |
| REFACTOR | page selectors/view models, models/benchmarkFilters.ts | Label/explanation/details; matched comparison по intended axis, no batch/status mixing |
| REFACTOR | page tables, copy/filter interactions | Shared DataTable/TechnicalValue/DetailDrawer/ChartFrame; no long strings escaping |
| REPLACE | shell/Sidebar.tsx, Header.tsx и visual CSS | Icon rail / brand / status hierarchy / viewport-aware shell; outlet остаётся |
| REPLACE | OverviewPage.tsx + overview presentation | Главный story — corpus/research/source evidence, не gigantic latency probe |
| REPLACE | corpus/D48PlanGrid.tsx, CalendarPage visual timeline | Date×strata encoding, event markers вместо произвольных duration полос |
| REPLACE | Research/Models/Sources/System presentation | Переорганизация задач оператора и visualizations; DTO прежний |
| DELETE | unused page exports из PlaceholderPages.tsx | Routes уже используют реальные pages; Trading вынести в locked page |
| DELETE | duplicate/dead panel/header CSS после usage audit | Удалять по references, не blanket purge; общий primitive вместо copy-paste |
| DELETE | 336 повторных visible state labels; raw reason walls; Trading FactSummary | Заменены легендой/details/понятным unavailable state; values/provenance не удалять |

## Причины в коде и severity

- **Critical visual:** `corpus/corpus.module.css .planGrid` = repeat(24, minmax(0, 1fr))
  внутри примерно половины main. При panel около 700 px, 23 gaps × 3 и padding остаётся
  около 25 px на cell, тогда как DESIGN/PROTECTED в 12 px намного шире. `D48PlanGrid.tsx`
  рендерит ещё вторую строку текста. Увеличение font с 8 до 12 в старом review без изменения
  encoding усилило overlap. Отсутствуют row wrappers/consistent gridcell semantics,
  protected cells дают 112 лишних Tab stops. Не пытаться лечить шрифтом 6 px/overflow:hidden.
- **Major:** `.capStatus`, `.blockerList`, `UnavailableState.reason` не имеют единой long-string
  policy; System aside 260–320 px недостаточен для вложенных badges и codes. `min-width:0`
  у внешнего grid не заставляет длинные text runs помещаться внутрь.
- **Major:** Overview min-height 400/240 и fixed 260 px bar track растягивают мало данных;
  `.kpi justify-content:space-between` с 2/3 child nodes создаёт разные baselines.
  `max-width:1600` + центрирование не само по себе bug, но без осмысленного layout даёт
  detached content при широкой/zoomed картинке. Responsive надо проверять browser geometry.
- **Major:** Calendar `width:8%` у markers создаёт видимость длительности; axis ticks
  с translateX(-50%) выходят по краям. Длительность только при published start/end,
  иначе marker/заявленный design interval, не observed duration.
- **Major semantic presentation:** schema допускает DEMO, QualificationBadgeKind — нет;
  Research/Sources map DEMO → SYNTHETIC INPUT, Header всегда RECORDED EVIDENCE. Реальная
  текущая проекция REAL, но future demo fixture будет неверно представлена. Regression first.
- **Major comparison:** timingsComparable проверяет width/context/device, игнорирует
  batchSize/status. Таблица считает +% к minimum смешанных batch. Cross-device charts
  группируют same model/width/context/batch/measurement; cross-model chart фиксирует device/
  width/context/batch и ясно обозначает разные architectures. Не вычислять market winner.

## Почему получилось формально закрыть примерно за час

Git shell commit 22:53:17 → handoff 23:47:29: **54 min 12 s**; Phase 0 first commit 22:22:12
→ handoff: 85 min 17 s. Это расстояния между commits, не полная длительность работы/мыслей.
Backend уже существовал, pages в основном projection→JSX. 48 TSX и 18 CSS файлов не равны
48 продуманным interaction contracts. Icons/chart/e2e libraries в package.json отсутствуют.
44 Vitest/jsdom tests проверяют текст/semantics; jsdom не делает реальный browser layout.
D48 test закрепляет 336 DOM children; плохой rendering спокойно удовлетворяет этому тесту.

Workflow принимал непустые `screenshot`/`reviewed_revision` строки, не bytes/coverage.
P2.1 acceptance опирался на detail crop; P2.2 отметил увеличенный шрифт, но не повторный
full-grid audit; P7.2 повторно использовал **Overview 768** screenshot из P7.1. Это не desktop
матрица 6 major pages. Существующие old files действительно есть; не утверждаем фальсификацию.
Короткое «navy, no fake live» стало заменой visual comparison. Наш CC-01 guard защищал
scope/семантику, но недостаточно строго проверял visual evidence — это тоже дефект handoff.
Не приписываем результат конкретной actual модели: runtime binding остался UNVERIFIED.

## Проверки audit и ограничения

Повторный Vitest: 44 PASS; TypeScript + production build завершён, JS 514.84 kB / gzip 153.15 kB,
CSS 35.99 kB / gzip 6.34 kB. Сохранились PostCSS `from` warning и chunk > 500 kB. Изначальная shell
обёртка после npm ошиблась на readonly `$status`; npm logs содержат завершённые результаты.
Это ошибка audit wrapper, не failure приложения. Логи в `.local/cc02-audit/` (ignored).
Новый browser capture здесь не выполнялся: visual evidence — все предоставленные PNG;
responsive/live browser assertions предстоит реализовать в CC02. Не заявляем E2E PASS.

Следующий шаг — [PLAN.md](PLAN.md), [DESIGN-SYSTEM.md](../DESIGN-SYSTEM.md) addendum и
[VISUAL-ACCEPTANCE.md](VISUAL-ACCEPTANCE.md). Target 80–90% — пользовательская qualitative
цель design family, не fabricated numerical similarity score.


Итоговая provenance/check evidence: [audit-evidence.json](audit-evidence.json).
CC02 orchestration conformance: 37 scoped Python tests PASS. Новый receipt gate проверяет
артефакты, а не объявляет будущий дизайн принятым. Redesign и browser E2E ещё не проведены.
