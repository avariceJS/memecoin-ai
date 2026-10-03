# План реализации Grok: Observatory → полноценная web v1

Оценка — 5–8 рабочих дней, а не обещание срока. Фазы идут по зависимости, не по
календарному дедлайну. Каждая заканчивается логическим commit, UI Memory Bank update и
коротким отчётом. Core D50+ может идти параллельно; ожидание core interface не блокирует
доступные UI задачи. Все запреты `apps/control-center/AGENTS.md` действуют в каждой фазе.

## Phase 0 — onboarding и contract audit (первый день)

**Цель:** восстановить контекст и доказать, что работа остаётся в dedicated ветке.
Scope: обязательный reading list, Git/worktree/scope guard, запуск9 adapter tests и exact
projection check; проверить local Node/npm/Python, определить exact compatible UI dependency
versions по official docs/registry, записать lock strategy. Проанализировать published DTO.
Источники: onboarding, UI memory, source-pins, published bundle, core docs только read-only.
Acceptance: branch правильная, core diff пуст, no raw access, чёткая карта available/
unavailable capabilities. Перед coding написать краткий результат проверки контекста.
Запрещено: автоматический main sync, новый worktree, npm install в root, любое чтение work.
Tests: existing9 tests/scope/reproduction; failure исправлять только внутри UI scope.
DoD: актуальный UI state с toolchain/планом и verified starting point. Если нужны изменения
core interface — formal proposal, а не обёртка shell command.

## Phase 1 — app foundation, contracts и design system (день1–2)

**Цель:** запускаемая React SPA, честно использующая настоящий local read API.
Scope: `web/` Vite/TS strict/npm lock, router, TanStack Query, DTO boundary validation,
error/loading/unavailable states, CSS tokens, sidebar/header, status/provenance/time components.
Разделы навигации изarchitecture; global banner RECORDED EVIDENCE + source date/commit.
Sources: `/api/v1/capabilities`, `/api/v1/snapshot`; real published projection по умолчанию.
Acceptance: npm dev/build/check/test работают; closing UI не трогает adapter/core;
network только loopback; malformed contract/error → понятный error, не DEMO fallback.
Запрещено: RPC client, repo static mount, auto-exporter, запуск core, controls.
Tests: DTO validator/types, unknown major, null, ns strings, error boundary, provider cache.
DoD: useful shell с реальными source cards и unavailable capabilities; не только wireframes.

## Phase 2 — Calendar / Corpus, главный операторский экран (день2–3)

**Цель:** человек понимает завершённость окна и ограничения corpus без JSON.
Scope: D43 шесть planned/recorded rows, timeline UTC/MSK; window detail с раздельными
counts/exit codes/first receipt/finish и unavailable actual start. D48 planned336window grid
с design label,112protected cells. Corpus revision/counts/censorship/coverage summary.
Sources: CalendarProvider, CorpusProvider, ResearchProvider.sampling; никаких новых files.
Acceptance: D43window0 показывает180/174/135; source freshness/date видна; pending не
превращается в missed; protected cells не содержат metric payload и не имеют drill-down.
Демонстрацию «corpusPASS/executionFAIL/overallFAILED» делать отдельной mock fixture, не
подменять существующее окно1. D48 design не показывается как работающий collector.
Запрещено: читать source window, вычислять labels/folds, отдавать holdout stats, backfill.
Tests: keyboard navigation, protected response/DOM/cache absence, ns/timezone/rounding,
null semantics, mixed stage outcomes, empty/quarantined fixture states, 336windows perf.
DoD: обзор и детали доступны с клавиатуры и объясняют каждый count/unknown/source.

## Phase 3 — Overview и Sources / Pools (день3)

**Цель:** связать evidence, не создать ложный live dashboard.
Scope: recorded system facts, gates/blockers, last source evidence, historical D49 latency
bar chart и Clock-age range, fixed pool IDs/copy address, links к safe internal details.
Sources: sourceQualification + остальные safe providers. Current operational status unknown.
Acceptance: никакого price/volume/liquidity, если нет export; network health не выводится
из старого6/6probe. Overview не даёт общий GREEN при unknown runtime и closed admission.
Запрещено: token API/logos с внешней загрузкой, explorer auto-fetch, новые источники RPC.
Tests: values/units/ns/seconds, insufficient data p95 label, source provenance, no offsite calls.
DoD: человек отличает successful historical qualification от current service health.

## Phase 4 — Research / F01 read-only console (день3–4)

**Цель:** ясно представить technical readiness и формальные gates.
Scope: F01 spec summary, budget0/114 recorded,135engineering fits отдельно,111numeric
blockers/113checks без percent-ready; required reviews; admission LOCKED/release PROTECTED.
Проектировать generic study route/id; F01-specific детали живут в adapter/view model.
Sources: ResearchProvider; core D46–48 docs для смыслов, не executable imports.
Acceptance: numeric pass в fixture не открывает permission; selection/winner отсутствует;
holdout release count0 не называется global exposures0. Ledger detail показывает unavailable
и ссылку на proposal, если безопасный event export ещё не принят.
Запрещено: runner/trainer invocation, receipt edits, new selection/scoring/bootstrap.
Tests: readiness≠authorization, engineering counter separation, no hidden holdout metrics,
unknown study/schema, all gates/states включая inconclusive synthetic view fixture.
DoD: интерфейс объясняет почему research не запущен; ни один permission не вычислен UI.

## Phase 5 — Models / Training evidence / Benchmarks (день4–5)

**Цель:** полезно исследовать engineering work без заявления о рыночном качестве.
Scope:15recorded CPU runs,40benchmark cases; dataset kind/model kind/seed/epochs/params,
filter shapes/device; plots measured timings и таблица. MPS-only case failures/nullable
metrics не скрывать. Active model unavailable; никаких launch/resume/promotion actions.
Sources: ModelEvidenceProvider; дополнительный MPS run допустим только новой reviewed
projection из уже pinned D44 evidence с отдельным test/field mapping, без weights.
Acceptance: SYNTHETIC INPUT/ENGINEERING badge рядом с metrics; нельзя выбрать «winner»
по этим runs; comparisons same shape либо явное предупреждение о несопоставимости.
Запрещено: loading Torch/checkpoints, inference, retraining, model architecture editor.
Tests: modelKind string не hardcodedGRU; shape filtering, numeric units, missing timings,
keyboard tables, all candidate labels; no dtype/device benchmark generation из UI.
DoD: реально полезная сравнительная таблица измерений с условиями и provenance.

## Phase 6 — System, Replay и future capability boundaries (день5)

**Цель:** честно обозначить границы и оформить недостающие interfaces.
Scope: adapter reachability/version/snapshot hash отдельно от recorded core tests/build;
no live CPU/RAM/MPS/process charts. Статические будущие node roles как DESIGN, не two
connected machines. Replay unavailable до core-approved development-only export.
Trading/Shadow/Paper/Predictions/EmergencyStop — LOCKED/UNAVAILABLE с причиной.
Sources: capabilities, sourceQualification.tests, UI own fetch state. Safe logs отсутствуют.
Acceptance: нет кнопки с имитацией success; ни один route не запускает subprocess/provider.
Если core ответил наCC-P001 — сначала отдельный approved-contract review/новая версия,
не внедрение по бытовому сообщению «файл уже есть».
Запрещено: process scanning/shell executor/remote SSH/launchctl/открытие worker.log.
Tests: unknown capabilities, disabled route actions, HTTP verb denial, offline/error state.
DoD: все future features читаемо объяснены; proposal pending, core не изменён.

## Phase 7 — качество и handoff v1 (день6–8)

**Цель:** рабочая, проверенная оболочка, которую можно review отдельно от core.
Scope: responsive1280/1920/2560, 200%zoom, keyboard/a11y, empty/error/loading states,
visual consistency, unit/E2E security tests, measured production-build performance,
README запуск/stop/UI-only ports, screenshots только своих safe views, final demo policy.
Sources: published real bundle + separate synthetic fault/performance fixtures.
Acceptance: UI работает на actual evidence после clean install; нет внешних requests,
core diff пуст; всеscreens показывают source/qualification/time. No false live/currentmodel.
Запрещено: «починить core» для теста, удалить awkward source records, repin без review.
Tests: полный UI suite/build/type/lint/a11y +9 adapter tests +scope/projection check;
repeat tests только после изменений/новых проблем; записать actual hardware/results.
DoD: логические commits, updated UI memory, review report, known limitations, branch ready
for reviewer. **Не merge main** и не объявлять frontend production trading console.

## Phase 8 — позднее Tauri packaging (за пределами текущего web v1)

Цель: использовать тот же frontend вdesktop shell после отдельного решения.
Scope/data: package approved web build + read-only UI sidecar; review capabilities/origins/
CSP/lifecycle/signing/update policy. Не обращаться к privileged core process management.
Acceptance/tests: close/reopenapp не влияет наcore; FS/shell capabilities absent;
macOSspecificsmoke/permission/error tests. Без утверждённого решения phase остаётся pending.
DoD: отдельный reviewable packaging proposal; сегодняшняя v1 не ждёт .app.
