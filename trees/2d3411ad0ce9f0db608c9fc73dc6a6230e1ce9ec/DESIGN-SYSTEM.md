# Visual contract v1 — Memecoin AI Control Center

Принят пользователем 2026-10-03. PRIMARY VISUAL REFERENCE:
[dashboard-concept-v1.png](references/dashboard-concept-v1.png).
Изображение получено и открыто при CC-01: 1672×941, SHA-256
`dd03716ed059c4bdf85726489460d4c07acb6836a872553ccf2157bb8dd014d6`.
Не заменять/перегенерировать его автоматически. [Reference policy](references/README.md).

Это обязательный визуальный контракт высокой близости, не «просто вдохновение».
Его semantic content не является backend contract. Надписи Trading Intelligence,
Live Mode, Portfolio, Win Rate, Signal Engine, Model Ensemble, Risk/Execution и сами
свечи не доказывают существования таких данных. Не переносить fake значения/online dots.
Текущий Observatory показывает recorded evidence, ENGINEERING и unavailable capabilities.
Архитектура, state semantics и source-of-truth из 01/02/03 остаются прежними.

## Композиция и grid

Сохранить navy/charcoal canvas, тонкую рамку, cyan/teal accents, плотный фиксированный
левый rail, верхнюю status bar, короткий KPI ряд, доминирующую центральную область,
правый стек system/research/model panels и нижние плотные tables/activity widgets.
На reference верхняя полоса примерно 74px, sidebar примерно 212px; это наблюдения,
а следующие значения — стартовые UI tokens, не обещание pixel-perfect измерения.

- Desktop 1672/1920: header 72px; sidebar 212px; main padding 18px; gaps 16px.
  Sidebar фиксирован, собственная вертикальная прокрутка при необходимости; header sticky.
- Main: 12 columns, `minmax(0, 1fr)`; центральный chart/timeline 7–8 cols,
  правая status column 4–5; сверху 4 compact KPI в левой области + summary справа.
  Нижний ряд 3–4 panels с таблицами/списками, не бесконечная grid одинаковых cards.
- KPI высота 108–120px; центральный блок ориентир 380–430px; нижние 240–300px.
  Это диапазоны, не hard clipping текста. На Calendar центральный timeline заменяет свечи;
  справа window detail / source / QA; внизу window table / corpus counts.
- Область chart занимает заметно больше пространства, чем декоративные gauges.
  Overview: corpus timeline, source qualification / F01 gates справа, runs/window table снизу.
  Research: protocol/fit budget в центре, admission справа. Не рисовать sentiment/risk gauge
  вместо отсутствующего контракта; компактное UNAVAILABLE, полезные данные получают площадь.
- 2560px: увеличить рабочую область/число видимых строк, не растягивать KPI до пустых плиток.
  1280px: sidebar 184px или компактный 64px с accessible labels, main 8+4 или 7+5.
  Ниже 1100px: правая колонка под main; на 768px rail 56px, один основной столбец.
  Это usable fallback, не смена продукта на mobile-first. Tables скроллятся внутри panel.
  200% zoom: nav/control content не теряется, horizontal scroll только у сложных data views.

## Tokens и typography

Рекомендуемые исходные tokens (проверить контраст в реальном render):

| Роль | Значение / правило |
|---|---|
| canvas / sidebar | `#080F17` / `#0B131E` |
| panel / raised / hover | `#101C29` / `#152333` / `#1B2D40` |
| border / grid | `#243748` / `#1A2B3B`; border 1px |
| text primary / secondary | `#E7F0FA` / `#A9BED3` |
| cyan / teal / green | `#35DCEA` / `#2DE3C2` / `#54D99B` |
| blue / muted purple | `#51B5FF` / `#AE97F5` |
| warning / failure | `#F2C15C` / `#FF7D8B` |
| radius | cards 8px, small controls/badges 5–6px, icon tiles 12px |
| spacing | 4, 8, 12, 16, 20, 24, 32px; panel padding 16–20px |
| font | local system `-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif`; no remote fonts |
| text sizes | 12px captions minimum, 13px tables, 14px body, 16px panel titles, 20–24px page/KPI |
| weights / numbers | 400/500/600; tabular-nums for counts/time; monospace for hashes/IDs only |

Изображение: restrained separators, icon tiles, чёткая светлая typography на тёмном фоне;
никаких огромных заголовков, пустых cards, Material defaults, casino эффектов.
Tiny source captions не должны быть бледными до нечитаемости. Русские длинные labels
переносятся; truncation допустима для hashes с keyboard-accessible полным текстом/copy.

## Borders, glow, interaction

Subtle shadow максимум `0 4px 20px rgb(0 0 0 / 18%)`; один soft glow на active navigation
или focused primary datum, не на каждом panel. Допустим едва заметный tonal navy gradient
внутри card как в reference; rainbow/animated background запрещён. Active rail cyan line
3px + tinted background; icons 18–22px в строке 44px. Header: product title, recorded date,
snapshot/source status и навигация. Fake universal search и command palette не нужны.
Не добавлять nonfunctional controls. Tooltip supplemental, не единственный носитель смысла.

## Семантические цвета и badges (обязательны рядом с данными)

Цвет всегда дублируется текстом/иконкой/формой. Это независимые оси, не один enum:

| State | Presentation | Недопустимый вывод |
|---|---|---|
| REAL EVIDENCE | cyan outlined badge + source/date | данные live/качественные/прибыльные |
| ENGINEERING | blue outlined badge + purpose | завершено research/есть edge |
| SYNTHETIC | purple badge `SYNTHETIC INPUT` рядом с metric | real market input |
| DEMO | постоянный amber `DEMO — вымышленные данные` banner, badge в каждом panel/export | fallback при API error |
| UNAVAILABLE | muted text + slash icon + причина | zero, empty success, green health |
| PROTECTED / LOCKED | lock icon + muted purple border, явная причина | скрытые labels загружены и просто blurred |
| PASS / healthy | green только для конкретного подтверждённого stage/as-of | весь core online |
| warning / stale | amber + причина/age | историческая запись обязательно испорчена |
| FAIL / error | red/rose + safe code/stage | весь corpus fail из одного execution audit |

RECORDED SNAPSHOT видим глобально. ServedAt API — не дата рыночной доступности.
Overview header не должен говорить System Online / Live Mode для core. UI adapter reachable
может быть зелёным **с подписью UI adapter**, независимо от unknown core health.
Нет pulse/тикера, намекающего на новые market events. Motion hover 100–150ms;
`prefers-reduced-motion` отключает анимацию. Будущие balance/positions/signal cards locked,
не masked `$••••` (это может выглядеть как существующий секретный balance).

## Charts, tables, indicators

Charts: тёмная тихая grid, cyan primary, green comparison, blue secondary, purple distinct;
не больше 4–5 серий без explicit legend/filter. Всегда units, axis labels, timezone и
qualification/source. Missing intervals — разрывы, не interpolation. Null не zero.
D49 шесть probes — точки/столбцы, не invented candles/rolling p95. Protected regions —
hatched LOCKED, ни labels ни metrics ни скрытых tooltips/DOM/cache/accessibility payload.
Crosshair и zoom только для настоящего набора; reduced-motion, keyboard/data table alternative.
Timeline/calendars: UTC/MSK явный toggle; fixed recorded pending не конвертировать по now.

Tables: 32–36px rows, sticky header, числа справа, labels слева, тонкие separators,
мягкий hover, sorting/filter state явно. Dense ≠ 9px font. Row detail не вытесняет источник.
Virtualization только при доказанной нужде; 336 windows не оправдывают heavy chart engine.
Long IDs не раздувают columns. Horizontal overflow в table container, не всей страницы.

Progress: только known numerator/denominator одного смысла (например recorded fits 0/114).
Numeric readiness не permission, не health gauge/«98% ready». Unknown denominator — no bar.
Stage outcomes раздельны: collection/corpus QA/execution audit/seal, не «зелёное среднее».

## Loading / error / stale / empty

Loading: skeleton реальной геометрии + текст «Загрузка проекции», без random metrics.
Error: safe code, retry/manual refresh, source status unknown; не fallback DEMO.
Можно сохранить последний успешно validated snapshot в памяти с `cached, refresh failed`
и его source time; не переписать его freshness временем retry.
No data: отличать valid empty list, null unknown, unavailable capability, protected access.
Stale: договорённая operational TTL только после контракта; старый immutable evidence
показывать dated historical, не измерять ему придуманную live SLA. Locked control —
информационная плашка, не clickable action с fake success.

## Accessibility и visual gate

WCAG AA contrast target: 4.5:1 обычный текст, 3:1 крупный текст и значимые UI границы.
Измерить tokens после composition, не считать hex достаточной проверкой. Focus ring виден,
landmarks/heading order, keyboard nav/dialog escape, targets ≥24px (основные ≥32px).
Screenshots review 1280×800, 1672×941 (reference), 1920×1080; 2560 и 200% zoom smoke.

**Каждый** visual executor/reviewer открывает PNG своим image tool **до реализации/ревью**,
возвращает observed reference SHA и кратко 2–3 увиденных нужных элемента. SHA alone ≠ просмотр.
Coordinator указывает emphasis: shell → sidebar/header; calendar → central timeline/right
column; model table → dense lower widgets/typography. Не передавать только пересказ.

Major screen gate: builder → targeted tests → отдельный visual-reviewer сравнивает PNG и
actual browser screenshots → fixes → один verification pass (дальше только если fail).
Review receipt: task/commit-or-diff, route/viewport, screenshot paths+SHA, reference SHA,
viewed=true, findings по composition/density/spacing/proportions/sidebar/header/chart/
hierarchy/colors/type/states/a11y, severity, accepted или changes_required.
No generic admin drift. Не принимать screenshot, если нет visual tool/image; продолжить
независимые tasks, gate pending. Screenshots локально `.local/reviews/`, компактные receipts
в UI memory/reports; источник данных screenshot не доказывает, boundary review отдельный.


Перед каждой visual implementation/review и перед visual acceptance:
`python3 apps/control-center/scripts/workflow.py --visual` (exact reference check),
затем лично открыть PNG. Команда не заменяет просмотра. Общий `--check/--render`
при missing/changed image предупреждает, но сохраняет возможность независимой работы;
предыдущие review receipts остаются историческими, новый visual PASS запрещён.

## CC-02 — обязательное уточнение после visual rejection (2026-10-04)

CC-01 tokens не равны принятому дизайну. V1 screenshots [current-v1](current-v1/README.md)
показывают, что отдельные navy cards и cyan borders не дают нужной композиции. В случае
конфликта этот addendum и [CC02 acceptance](cc02/VISUAL-ACCEPTANCE.md) важнее старого PASS.
Стартовые размеры выше — guidance, не основание растягивать пустые panels ради min-height.

### Shell и информация на первом экране

Desktop master 1600×1000 CSS px, DPR 1, zoom 100%; дополнительно 1440×900/1920×1080,
1024×768 fallback; target 1672×941 для side-by-side comparison. Sidebar 212–224 px,
header 64–72 px. Main использует доступную ширину, без неожиданного narrow island на широком
мониторе. Grid 12 cols, content-first minmax(0,..); рабочая область 8 cols, side context 4 cols,
При ограниченной ширине columns располагаются друг под другом. На 1600 px aside предпочтительно 320–400 px, но не rigid
по всем страницам: System matrix не должна оказаться в aside. У page один shell title
и одна постоянная дата записи / RECORDED EVIDENCE, без двух дублирующих banner.

Sidebar: небольшой original SVG brand mark, product Memecoin AI / Observatory mode,
Lucide outline icons 20 px, active cyan line + subtle inset surface, groups Evidence / Research /
System / Future. Реальные routes прежние. Group labels тише page content. Collapsed rail
показывает различимые icons и accessible labels/tooltips, не одинаковые dots. Hover/focus
согласованы, keyboard path не теряется, длинный brand не пытаться вместить в 56 px.

Header: navigation title + as-of context, справа честный recorded status и действие
manual refresh только существующего API (если реализовано); timestamp/hash — disclosure.
Никаких fake search, notification counts, Live toggle или command launcher. При неизвестном
core runtime краткое «Состояние core недоступно», а не красная авария на половину экрана.

Overview: сверху 4 compact KPI со строгим выравниванием label/value/context; центральный
D43 evidence timeline примерно 60% рабочей области; справа corpus/research/source states.
Внизу 2–3 плотных полезных widgets, а не одинаковые пустые boxes высотой 240 px. Latency probes — secondary detail.
Все cards имеют одну задачу; объём свободного места должен поддерживать hierarchy, не быть
побочным эффектом fixed min-height / align stretch. Длинная страница допустима, пустота вместо
информационного решения — нет. Shell и Overview принимаются раньше остальных redesign.

### Typography / reason presentation

Inter variable / статические 400/500/600 self-hosted, кириллица/latin, system fallback.
Не загружать remote font в runtime. Font choice помогает consistency, не заменяет дизайн.
Tokens: page-title 22–24/1.2 semibold; section 16/1.35; KPI 26–30/1.15 tabular; body 14/1.5;
table 13/1.4; caption 12/1.4. Metadata 12 px; monospace только id/hash/exact code, не все labels.
Числа выровнены, tabular; labels в sentence case; caps ограничены badges; body copy max 60–72 ch.
Основная операторская лексика — русская; technical labels единообразны в details, без сырого camelCase.

Пример locked card: «Обучение» → LOCKED → «Допуск к исследованию не выдан» →
«Технические детали» (точный underlying code). Не заменять `NO_APPROVED_CORE...` фразой
«Корпус недостаточен», если такой причинной связи нет в source. Qualification/permission/
provenance независимы. DEMO обязателен отдельным state, никогда не SYNTHETIC INPUT.

IDs: one-line ellipsis в summary с copy feedback/tooltip доступным keyboard; полный value
в drawer/detail с wrap-anywhere. Никаких codes за border, `overflow:hidden` на page для сокрытия
дефектов или microscopic font. Technical details сохраняются, но не занимают главное внимание.

### Surface / color / motion

Уровни: canvas → navigation → primary analytical panel → secondary context → inset detail.
Тональный gradient внутри panel почти незаметен; 1 px border, локальная elevation, glows
только active focus / brand / chart selection. Семантический цвет не меняет доступность или время.
Muted purple — protection/engineering distinction, amber — конкретное attention/locked
основание, red — failure/error, **UNAVAILABLE не системная авария**. No rainbow.

Движение — реакция UI на действие, не имитация новых данных: hover 100–160 ms, drawer/page
opacity/transform 160–220 ms, chart reveal 250–400 ms **однократно**. Counter animation по умолчанию
не нужна: значения сразу доступны screenreader; если count-up используется, явно лишь как entry effect,
не тикер и не пересчёт recorded facts. Никаких periodic status pulses / core heartbeat / glowing data flow.
Decorative slow SVG motif — optional и aria-hidden, не часть data encoding. При hidden — pause;
при reduced motion — static. Focus/reduced-motion/idle browser performance обязательны. Не больше одного ambient-элемента.

### Dense visualization / interaction system

ChartFrame: title/question, source/units/state, plot, short legend, table alternative/details.
Data table в scroll container со sticky header; это не единственный visual mode каждой страницы.
Tooltips показывают datum + units + recorded status, no protected details. Drawers поддерживают focus return
и Escape; любое mouse action имеет keyboard path. Copy feedback не меняет текущую page. Active
cell/filter/selected row всегда видимы. Не прятать essential status только на hover.

D48: четыре strata × даты, компактные square/rect cells, цвет + shape/hatch; подписи dates/
week/month вокруг матрицы, одна legend. 336 virtual text cards запрещены. На узких экранах — разбиение на
week/month blocks или явно именованный local scroll, никакого page overflow. Protected cells
передают только public design metadata; не 112 tab stops, а roving focus / legend summary.
D43 markers не рисуют duration без published start. Research diagram не вычисляет новый admission.
Models charts группируются по matched contexts; нельзя выделять скорость несопоставимого batch как преимущество.

### Visual acceptance выше convenience

Для major task недостаточно тестов и декларации «looks good». Обязательны actual browser
screenshot → independent critique → fixes → second actual screenshot → layout audit → acceptance.
Reviewer называет, что плохо, что исправлено и что остаётся слабым. Если посредственно,
NEEDS_FIX даже при semantic PASS. Before/current/target смотрятся рядом, не по памяти Coordinator.
Новые механические receipts проверяются script, но premium quality остаётся human/independent
judgment. Подробная rubric/coverage в CC02 acceptance; user visual rejection важнее старого PASS.
