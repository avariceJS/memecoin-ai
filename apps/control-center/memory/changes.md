# История UI workstream

## 2026-10-03 — CC-00: architecture/handoff для Grok

- Прочитаны actual Git/Memory/contracts D43–D49. Worktree изначально clean detached6a7e079.
  Создана dedicated branchcontrol-center/observatory-handoff; main не переключался.
- Plancommitf28e5fd; adapter/guardcommit06893f3. Выбран вариант A — Grok работает здесь.
- Подготовлены typed contracts,9pinned source files, reviewed projection,2GET API,
  защищённый bounded reader, scope guard/Cursor rule/UI AGENTS, отдельная память и onboarding.
- Реальные historical metrics доступны; current live status не выдуман. Holdout4/5
  metadata-only; raw не читался. Core files и root Memory Bank не изменены.
-9tests, exact projection reproduction, scoped Ruff/format и scope guard прошли.
  Не проводились frontend build/typecheck/E2E, market/RPC/training/paper/live.
- История разработки: initial HTTP test AttributeError из-за shadowing `http` module
  локальной переменной; test исправлен. RuffE731 в exporter lambda исправлен вdef.
  Это UI fixture/style ошибки, не provider/runtime/core failure; расширения сети не было.
- Frontend оставлен Grok. NextPhase0/1; CC-P001 awaiting core review, не блокирует UIv1.


## 2026-10-03 — CC-01 visual/team follow-up

- Добавлены PRIMARY PNG+DESIGN-SYSTEM, native agent definitions (6exact paths), team/loop/
  model policy/recovery docs, prompts, bounded task mapping в прежних phases0–7.
- UI WORK_STATE/STATUS, validator/render script,13workflow/guard tests; adapter9 tests
  сохранены. No frontend implementation, no core/root Memory changes, no D50.
- Read-only independent Codex review обнаружил guard index/rename bypass; исправлен,
  regression fixtures PASS. Это не native Composer/Cursor run; он ещё P0.1 UNVERIFIED.
- Failure history: первый scoped lint E501/F401, после форматирования I001; исправлены,
  final Ruff/format PASS. Tests22PASS, projection exact и state/scope conformance PASS.

- Final review correction CC-01: canonical/ancestor lease overlap checks и shared-directory
  denial; missing PNG не мешает state recovery, explicit `--visual` fail-closed.
  Два regression tests добавлены; итог22tests PASS. Исторические review receipts не
  доказывают наличие картинки в новом session, каждый visual agent обязан открыть её снова.

CC-01 code `38765863d54e54115e4beca72a0012fd050a4b3e`; final review/evidence сохранены отдельным docs commit.
Native model preflight/web implementation намеренно остаются next tasks Grok.

## 2026-10-03 — Phase 0 audit, модель карточки не закрыта

- HEAD на старте `c6e81aa`, дерево чистое. 22 unittest PASS, exact projection,
  scope/workflow PASS. Primary PNG открыт, SHA совпал.
- Явный Task parameter `composer-2.5[fast=false]` отклонён до spawn. Два native
  вызова без override (analyst ce83fa1b, boundary 267c182b) вернули работу без
  поля model. P0.1 поэтому не DONE. YAML агентов не менялся. Fast/inherit не
  подставлялись.
- Карта DTO сверена с published JSON. Поправка к первому пересказу: `actualStartNs`
  есть и равен null; pending-окна содержат ключи; 122 — `allScenariosObservedDecisions`,
  не all-start 1440.
- Exact pins записаны в toolchain-pins.md. `web/` не создавался, npm install не
  запускался. Core/root memory/D50 не трогались.

## 2026-10-03 — карточка модели не блокирует Phase 1

- Пользователь не смог подтвердить фактическую модель subagent через UI.
- Binding оставлен UNVERIFIED, configured policy `composer-2.5[fast=false]` не сменена.
- P0.1 снят с BLOCKED_EXTERNAL: карта DTO и boundary review уже были PASS.
- Workflow больше не требует VERIFIED, чтобы закрыть P0.1. Ложный VERIFIED без
  observed id по-прежнему отвергается.

## 2026-10-03 — Phase 1 shell

- `web/` на exact pins: React 19.3.0, Router 7.18.4, Vite 8.3.2, Zod 4.6.5.
- Клиент только GET `127.0.0.1:8787`. 11 vitest PASS, tsc PASS, production build PASS.
- Обзор показывает recorded KPI 6 / 0/114 / 135 / 15 и source cards. Trading route
  показывает `NO_APPROVED_TRADING_CONTRACT`.
- Visual review и boundary review PASS. Model binding по-прежнему UNVERIFIED.

## 2026-10-03 — P2.1 календарь D43

- `/calendar`: 6 окон, UTC/МСК, окно 0 показывает 180/174/135 и «старт не опубликован».
- Protected 4–5 в DOM без счётчиков. Pending остаётся «ожидание».
- Vitest 16 PASS. Visual verification PASS после уточнения, что полный кадр сдваивал capture.
- Boundary PASS.

## 2026-10-03 — P2.2 corpus и сетка D48

- На /calendar: revision 000000, 174/1392/362, 122 отдельно от all-start 1440.
- Сетка 336: 224 DESIGN «не активировано», 112 PROTECTED без чисел, admitted 0.
- Collector явно не активирован. Vitest 18 PASS. Шрифт ячеек поднят до 12px.

## 2026-10-03 — P3.1 Overview

- Центр: 6 latency в секундах, подпись «исторический probe, не текущий RPC».
- Справа: NOT_READY, blockers, clock-age 9.95–10.77 с, «p95 не опубликован», admission LOCKED.
- Снизу: 3 pool id с копированием без сети, tests 481/502 как recorded stage.
- Vitest 20 PASS. Высота центра 400px, подписи 12px.

## 2026-10-03 — P3.2 Sources / Pools

- `/sources`: 9 stamps, latency в секундах, NOT_READY, clock-age, 3 pool id.
- Подпись probe строится из accepted/requests и говорит, что это не текущий RPC health.
- Отказ clipboard показывается локально. Vitest 24 PASS.

## 2026-10-03 — P4.1 Research

- `/research`: 0/114 отдельно от 135 engineering, checks 113, blockers 111 без процента.
- Releases 0 подписаны как не global exposures. Admission LOCKED. Ledger UNAVAILABLE и CC-P001.
- Vitest 25 PASS. KPI min-height 112px.

## 2026-10-03 — P4.2 adversarial research fixtures

- Пять synthetic cases поверх published snapshot, файл проекции не менялся.
- blockers 0 при checks 42 остаётся LOCKED. Releases 0 не названы exposures.
- Совпадающие 88/88 не сливаются. Чужой specId без GRU. label/target/holdoutMetric не в DOM.
- Vitest 30 PASS. Boundary PASS.

## 2026-10-03 — P5.1 Models

- `/models`: 15 runs (10 SYNTHETIC, 5 REAL), 40 benchmarks, activeModel «не выбрана».
- Фильтр cpu и 16×8 оставляет 10 строк. Смешанные shape/device — «несопоставимо».
- Null timing — «не измерено». Нет winner/launch. Vitest 36 PASS.

## 2026-10-03 — P6.1 System

- `/system`: «UI adapter ответил» отдельно от pane system UNAVAILABLE.
- servedAt — время ответа API. DESIGN-роли не live-машины.
- predictions/paper/live/shadow/emergencyStop/replay без кнопок. Vitest 38 PASS.

## 2026-10-03 — P6.2 Replay unavailable

- `/replay`: reason из capability, нет file input и кнопки запуска.
- Ложный AVAILABLE остаётся LOCKED. Старый export из PlaceholderPages удалён.
- Vitest 43 PASS. Boundary PASS.

## 2026-10-03 — P7.1 a11y и ширина

- `:focus-visible` 2px cyan на ссылках, кнопках и select.
- Подписи не ниже 12px. Горизонтальный скролл таблиц внутри панелей.
- На 768px страница не шире окна, aria-label «Торговля» сохранён. Vitest 44 PASS.

## 2026-10-03 — P7.2 handoff web v1

- `npm ci`: 153 пакета, 0 vulnerabilities. Повтор Coordinator: vitest 44, tsc, build, scope, projection, 22 adapter tests PASS.
- Lockfile не изменился. Main не объединялся.
- Остаются UNVERIFIED model card, CC-P001, предупреждения Node/jsdom и размера bundle.


## 2026-10-04 — CC02 independent audit / reconstruction handoff

- Web v1 visual acceptance REJECTED; все 10 user PNG и target открыты. Before сохранены
  в исходном `docs/control-center/current-v1/`, SHA/dimensions — cc02/baseline.json.
- Code / visual audit, KEEP/REFACTOR/REPLACE/DELETE, nine-task plan, design addendum,
  screenshot/overflow/motion/a11y acceptance подготовлены. Сам redesign не выполнен.
- Выявлены не только visual defects: DEMO→SYNTHETIC badge mapping и неполный benchmark
  comparability predicate (batch/status). Fixes запланированы внутри frontend, не core.
- Старые WORK_STATE/STATUS/state архивированы; active queue CC02 PENDING. Model policy
  ui-builder + visual-reviewer → Grok High, routine → Composer standard; actual UNVERIFIED.
- Добавлен developer-only receipt conformance и adversarial fixtures. Старые semantic tests
  сохранены; screenshots не заменяются synthetic test PNG. Adapter/projection/web source untouched.
- Baseline Vitest44/build PASS. Wrapper failure: zsh запрещает присваивать `status`; логи npm
  показывают успех. Это orchestration failure, не runtime/provider. Предупреждения build сохранены.
- Финальные checks/commit/provenance — docs/control-center/cc02/audit-evidence.json.
  Independent reviewers — Codex audit agents, не подтверждение Cursor/Grok model runtime.
- Core/main/root Memory/D43/F01 и D50/D51 не изменялись; никакого merge UI.

- Independent guard review нашёл shell/page dependency conflict, слабое final-log binding,
  отсутствие capture binding и принятие truncated PNG. Все исправлены и покрыты fixtures.
  Final: 37 scoped unittest PASS (9 adapter + 13 workflow + 15 visual), Ruff/format PASS.
  Review docs также исправил обещание, будто restart Mac не затрагивает core, невозможное
  требование future captures до первой реализации и размер final reviewer response.

CC02 audit/guard commit `9f47abf9e473ef4f46c6238d805c917490aa9b21`; последующая запись закрепляет только evidence/state.

## 2026-10-04 — CC02.0 browser harness

- Добавлен dev-only Playwright 1.63.0 и `@types/node` 24.19.1. Новых API и production bytes нет:
  JS bundle остался 514.84 kB.
- Harness на 1600×1000 измерил известный v1: календарь 786 побегов текста D48 и 1256 наложений;
  обзор 50px горизонтального overflow. На 1024×768 календарь тоже 786. Тест требует эти пороги.
- Отчёт помечен NOT_A_VISUAL_PASS. Скролл таблиц не считается побегом карточки. Shell counts на этих
  страницах были 0: дефект принадлежит страницам.
- Первый прогон оставил `npm run dev`, потому что завершался npm, а не Vite. Запуск переведён на
  прямой `vite.js`, процесс после теста останавливается.
- Boundary review PASS, agent `f8059a95-ef49-4dbd-9e94-1dd588e4059b`. Vitest 44 PASS. Модель
  reviewer в tool result не указана, binding UNVERIFIED.

## 2026-10-04 — CC02.1 shell

- Рельс 220px: группы Свидетельства / Исследование / Система / Дальше, разные outline-иконки,
  свой знак, одна шапка со статусом записи. Дублирующий баннер RECORDED EVIDENCE снят.
- DEMO больше не рисуется как SYNTHETIC INPUT. Inter и Lucide локальные. Production JS вырос
  с 514.84 до 575.21 kB, CSS до 40.53 kB, плюс файлы Inter.
- Первая критика 4.3: выровнена линия бренда и шапки, сняты лишние рамки статуса, у Replay замок.
  На 768 подписи рельса скрыты без наложения текста. Вторая критика 4.7.
- Мелочи оставлены: повтор имён групп, тихая «Обновить», шапка выше бренда на 768.
- Receipt `docs/control-center/references/cc02-final/CC02.1/review.json`, visual_gate PASS.
  Vitest 48, characterization e2e PASS. Binding UNVERIFIED. Страницы не приняты.
