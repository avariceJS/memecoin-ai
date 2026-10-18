# CC-02 — visual/presentation reconstruction поверх web v1

2026-10-04. **Этот fork выполняет audit/handoff. Реализацию ниже выполняет Grok-команда.**
Не новая версия data/research architecture. Framework, API и projection сохраняются.
Цель: все major screens узнаваемо принадлежат design family primary reference, с ощущением
80–90% visual proximity по пользовательскому ожиданию. Это целевой ориентир,
а не измеренный процент сходства.
[Audit](AUDIT.md), [Acceptance](VISUAL-ACCEPTANCE.md), [Design addendum](../DESIGN-SYSTEM.md).

## Scope / неизменяемые ограничения

- Только presentation, frontend view models, UI semantic bug fixes, screenshots/tests/docs.
- Нет Tauri, 3D engine по умолчанию, новых backend capabilities/contracts, RPC, training,
  D50/D51, core source/root Memory edits. No main merge. Existing read-only adapter untouched.
- Не добавлять live prices/positions/P&L/wallet/market predictions/current core health.
- Дата recorded evidence видна постоянно; source age не маскируется анимацией/fetch time.
- Protect before read остаётся. D48 reserved metadata может иметь только уже public design
  дату/stratum/access, без collection metrics, labels, observed outcomes, tooltips с raw payload.
- Не переписывать values/пороги/gates ради диаграммы. Human-readable reason ≠ новая причина:
  явное соответствие существующему code, unknown code → «Подробности недоступны» + technical code.
- Не ждать CC-P001, не создавать новые core proposals ради визуального слоя.

## Архитектура presentation

Существующий API DTO → pure typed **view models** (labels, grouping, units, status axes) →
**design primitives** → **operator pages**. Не новый data store и не новые policy decisions.

Операторские вопросы:

- Overview: что известно из этого snapshot, где corpus, почему research locked?
- Calendar: какие окна записаны, какие лишь запланированы, что защищено?
- Research: что зафиксировано, чего не хватает, какие действия отсутствуют?
- Models: что измерено инженерно и какие случаи действительно сопоставимы?
- Sources: чем подтверждён источник и что qualification не доказывает?
- System: отвечает ли UI adapter, а что о core нам неизвестно?

Набор primitives (создавать по мере повторного использования, не строить framework ради framework):
AppFrame / PageHeader / WorkspaceGrid / Panel(primary, secondary, inset) / Kpi /
StatusBadge / EvidenceStamp / TechnicalValue / ReasonDisclosure / DetailDrawer /
ChartFrame(legend, units, empty, keyboard alternative) / DataTable / CapabilityTile.
CSS Modules + theme tokens; никаких случайных inline fonts/margins. Inline values
допустимы для data geometry/CSS custom properties, не styling exceptions каждой страницы.

## Очередь (bounded tasks, не speed race)

Каждая строка наследует ограничения scope и VISUAL-ACCEPTANCE. Task DONE — результат quality
loop, не факт существования route. CC02.0 может разбиваться на маленькие Composer assignments;
Coordinator один пишет shared lockfile/state/commits. Указанные даты не являются deadline. Не заканчивать
task после одной попытки, если screenshot выглядит посредственно.

| ID / цель | Executor | Scope / sources | Acceptance / tests / DoD |
|---|---|---|---|
| CC02.0 Baseline и test harness | Coordinator + Composer test-debugger/artifact-analyst | Сверить before manifests; browser/E2E harness, фиксированные viewports, code/snapshot hashes; изучить current DTO. Проверить совместимый Node в UI env без global upgrades | Известный current overflow воспроизведён browser check; baseline не «обновлён» в green; model requests записаны UNVERIFIED если нужно; baseline: build и 44 semantic tests. Новые deps только exact scoped lock |
| CC02.1 App shell + design primitives | Grok High ui-builder | **Первое visual изменение:** rail/icons/brand, единый top status, no duplicated banner, content grid, type/surfaces; общие Panel/Kpi/ReasonDisclosure | Screenshots shell на 6 major routes, при ширине 1600 px и responsive-проверках; semantic demo separation fix/test, long strings fixture. Цель — shell anatomy; отдельный visual review исправлений, before/after screenshots. Page content ещё baseline: shell-scoped counts/rubric, global page defects сохраняются с owning task; не заявлять все pages принятыми |
| CC02.2 Overview gold standard | Grok High ui-builder | Existing calendar/corpus/research/models/source DTO; hero timeline, compact KPI row, справа research/source, внизу windows/run summary | Overview — первый эталон composition. Ни одной giant empty fact card; 6 latency samples — secondary detail; все counters и source semantics сохранены; screenshot cycle + boundary. **Остальные redesign pages не начинать до acceptance Overview** |
| CC02.3 Calendar / corpus | Grok High ui-builder | D43 event timeline + detail drawer; D48 derived design: матрица day × 4 UTC strata из approved sampling | 84 days × 4 = 336; 56 development / 28 reserved days — только design; ясные date/stratum labels и legend; no repeated words in cells. UTC/MSK, day boundaries, protected absence, keyboard roving tabindex, recorded statuses 6 окон, null ≠ 0, stress-проверки на ширинах 1440/1600/1920/1024 px; two screenshot iterations |
| CC02.4 Research | Grok High ui-builder | Existing study/spec/counters/gates; visual dependency stages, grouped blockers/details | Frozen spec vs qualified corpus vs admission явно раздельны; source не даёт Runner READY → unknown/engineering, не выдумывать. 0/114 fits отдельно от 135 smoke fits; holdout protected/releases ≠ exposures. Numeric-pass fixture never permission; screenshot cycle |
| CC02.5 Models | Grok High ui-builder + Composer tests | Run summary by architecture, params bars, matched timing comparison, optional detail table | Исправить batch/status comparability. CPU vs MPS same model/width/context/batch/metric; cross-model same device/width/context/batch. Unsupported cases null/error, no winner. SYNTHETIC input / ENGINEERING purpose видны рядом с charts. Focus/filter/copy tests и screenshot cycle |
| CC02.6 Sources | Grok High ui-builder, Composer helpers | Static evidence/source topology, public pool IDs, небольшой chart для 6 probes, provenance/details | Topology не current connectivity; edges не анимируют fake ingestion. No invented latency history/volume; blockers human-readable. No offsite calls. Source evidence drawer + keyboard + screenshots |
| CC02.7 System + locked routes | Grok High ui-builder, Composer routine widgets | Central capability matrix, UI adapter response отдельно от неизвестного состояния core; compact Replay/Trading states | Нет узкой башни panels и пустого canvas; нет fake health/actions. Resize / long code / adverse fetch tests; before/after System plus Replay/Trading sanity; screenshots |
| CC02.8 Integration / deletion / final acceptance | Coordinator + Grok High reviewer + Composer test-debugger | Удалить dead presentation CSS/exports после проверки использования; измеренные bundle size/performance, a11y/motion, all routes screenshots | Full current-build matrix для 6 major screens, no major/critical findings, per-axis visual rubric ≥ 4/5, final score ≥ 4.2/5; semantic/boundary suites; docs/receipts; no main merge. User acceptance отдельно NOT_REQUESTED, не выдумывать |

Разрешённая параллельность после Overview: независимые pages по disjoint leases, максимум
два writing agents. Общие tokens/primitives изменяются последовательно; изменение shell
после принятого Overview требует его повторного review. Политика назначения моделей в AGENT-TEAM.
CC02.3/4/5/6/7 могут идти независимо после CC02.2. Нехватка label не оправдывает source changes.

## Карта визуализаций: data → information design

| View | Главное визуальное кодирование | Secondary detail | Чего не делать |
|---|---|---|---|
| Overview | D43 six event markers / recorded corpus stages; scaled layout, source-linked as-of | date/counts, measured source qualification, readiness blockers | Свечи, smooth fake market line, animated incoming data; 6 latency bars на весь hero |
| Calendar | D43 marker lane; D48 4 rows UTC strata × 84 day columns с month/week grouping; responsive week blocks | selected development window drawer, metadata-only protected tooltip/legend | трактовать design cell как collected window, 336 text labels, inferred missed |
| Research | Evidence/gate map с ясно labelled independent permissions, not computed «readiness %» | translated blockers + exact raw code expandable | все upstream green → automatic training; скрытое holdout report |
| Models | grouped parameter bars + measured latency comparison при matched context | sortable bounded table/failure details | связать params/latency с market quality; смешать batch/runtime benchmark types |
| Sources | статичная схема Source→qualification→projection; annotation с датой observations | bounded probe plot, stamps/hash drawer, copy IDs | realtime network status/animation market pipeline |
| System | capability matrix + own UI adapter response state | API servedAt / source age / build details, original codes | hardware charts, process scan, connection to second Mac |

D48 derived dates разрешены только как арифметические производные существующих anchor/end/UTC offsets/counts;
проверить consistency (84 days, 4 strata, 224 + 112). При mismatch показать ERROR/UNAVAILABLE,
не чинить plan и не открывать raw schedule. Все pixels/design dates производны; это не новая
calendar source of truth. D43 durations не брать из равной ширины div: missing start → marker.

## Dependencies: рекомендация, НЕ установка этим audit

Точные versions/совместимость/bytes измеряются CC02.0; ниже cost — качественная оценка, а не измерение bundle.

| Dependency / решение | WHY / WHERE | COST / bundle impact | Alternative |
|---|---|---|---|
| **lucide-react — добавить** | навигация, statuses, actions; coherent outline icons | выбранные static imports tree-shake; bytes измерить production build, no dynamic icon pack | вручную curated SVG symbols, дороже поддержка |
| **Inter — self-hosted asset** | единая typography c кириллицей, readable dense UI | локальный WOFF2, license, font load / layout shift; не runtime CDN; измерить transfer отдельно от JS | system font stack оставляем fallback; typography не сводится к выбору шрифта |
| **Radix Dialog + Tooltip — только нужные primitives** | details drawers, keyboard/focus management, tooltips | дополнительные JS / portal styles; static imports, no full theme kit | native details для codes; native dialog с проверенным focus |
| **@playwright/test — dev dependency** | actual browser/screenshots/layout/network/keyboard regression | browser install/storage/time, **0 production JS** | native Cursor Browser manual capture с receipts, но automation coverage всё равно нужна |
| CSS/SVG custom charts — основной подход | 6 points, 40 benchmarks, 336 cells требуют небольшой geometry/interaction layer | no chart runtime; свои axis / hit areas/a11y tests | специализированная library, если сложность доказана |
| Motion — defer | только если CSS/SVG не хватает coordinated layout/drawer transitions | дополнительный JS и reduced-motion lifecycle; bundle delta before acceptance | CSS transform/opacity (сейчас предпочтительно) |
| ECharts (Apache ECharts) — defer | rich zoom/large series не нужны нынешним 6/40/336 | renderer / chart imports / bridge / a11y maintenance, не заявлять exact size | SVG/CSS; если понадобится — modular SVG renderer, no second engine |
| lightweight-charts — не добавлять | market candles/live feed отсутствуют | лишняя trading semantic / specialized chart surface | existing evidence visualizations |
| Visx/Nivo — не добавлять сейчас | дублируют небольшую задачу построения SVG | ещё один renderer / tooltips / layout abstractions | custom ChartFrame |
| React Aria — не вместе с Radix | два interaction framework без необходимости | overlapping conventions/bundle | выбранные Radix primitives |
| R3F/Three.js — **не добавлять CC02** | 3D не исправит hierarchy/overflow | WebGL/GPU/idle CPU / renderer accessibility cost | один optional 2D SVG identity motif на Overview |

Один небольшой abstract SVG system identity допустим **после** эталонного Overview,
≤10% hero area, aria-hidden, не data-driven, без legend «AI activity». Не spinner-core в центре
экрана и не status dot pulse для recorded source. Декоративный core orb не нужен для acceptance.

Primary docs (проверены 2026-10-04): [Lucide React](https://lucide.dev/guide/react),
[Inter](https://rsms.me/inter/), [Radix Dialog](https://www.radix-ui.com/primitives/docs/components/dialog),
[Radix Tooltip](https://www.radix-ui.com/primitives/docs/components/tooltip),
[Playwright visual comparison](https://playwright.dev/docs/test-snapshots),
[viewport emulation](https://playwright.dev/docs/emulation).
Остальные инструменты — рассмотренные альтернативы, не утверждение об их измеренной скорости.

## Точный финал

См. mandatory gates VISUAL-ACCEPTANCE. COMPLETE CC02 означает independent engineering/design
acceptance по screenshots+tests. **User acceptance — отдельное поле**, никогда автоматически
ACCEPTED из agent PASS. Если пользователь вновь отклонит дизайн — новый review/fix cycle,
не спор с человеком на основании WORK_STATE. Reviewer должен назвать оставшиеся слабости,
даже при PASS, и обосновать, почему они minor. Final after screenshots сохраняются в
`docs/control-center/references/cc02-final/`, before не удаляются.
