# CC-02 screenshot-driven acceptance — mandatory

Старый web-v1 COMPLETE — историческая инженерная декларация. Пользователь её визуально
отклонил. CC02 не наследует visual PASS. [Before manifest](baseline.json) неизменяем;
новые изображения в `docs/control-center/references/cc02-final/<task-id>/`.

## Обязательная последовательность для major task

1. Builder лично открывает target + соответствующие before PNG. Brief называет emphasis.
2. Implementation и targeted semantic tests. Реальный browser, реальная pinned projection.
3. First full viewport screenshot 1600×1000, DPR 1, zoom 100%, fonts ready, stable route;
   capture не generated mock и не DOM dump. Сохранить source/build digest и snapshot hash.
4. Независимый reviewer в отдельном context лично открывает target, before и first capture.
   Называет конкретно плохие места, почему это хуже target и что изменить. Bare PASS запрещён.
5. Builder выполняет fix/refinement pass. При отсутствии defects — явно зафиксировать review
   без выдуманных bugs; повторная верификация всё равно нужна. Tests по изменённой области.
6. Second screenshots + actual browser overflow/layout audit. First не перезаписывать.
7. Reviewer сравнивает before/first/second/target, описывает, что исправлено и что остаётся.
   Если mediocre — NEEDS_FIX независимо от test PASS. Повторять cycle до качества, не гонка.
8. Coordinator проверяет receipt bytes/hash/matrix, targeted tests, scope, boundary; state/commit.

Для каждого major screen: Overview `/`, Calendar `/calendar`, Research `/research`, Models
`/models`, Sources `/sources`, System `/system`. Shell task снимает все 6 routes, оценивает
shell anatomy отдельно (не принимает старые page contents). Финальная task принимает всё.
Receipt `acceptance_scope`: `shell` только CC02.1, `page` для CC02.2–7, `integrated`
для CC02.8; `screen.rubric_scope` равен этому scope. Shell scores оценивают shell, не страницу.
Это убирает циклическое требование принять ещё не переработанный Calendar до Calendar task.
Replay/Trading — дополнительные screenshots/smoke locked states, не новый product scope.

## Матрица второго capture

На каждом major screen: viewport screenshots **1440×900, 1600×1000, 1920×1080, 1024×768**,
DPR 1, zoom 100%. Для каждого — metadata + layout measurement. Дополнительно full-page
1600×1000 (image height может быть больше 1000); нельзя заменять viewport screenshot на crop/full-page capture.
Target 1672×941 полезен дополнительным reference comparison, не обязательная новая матрица.
Финальный zoom 200% browser sanity (реальный browser zoom, не CSS zoom) и reduced-motion
проход: Overview + Calendar минимум, keyboard Tab/Arrow/Escape/focus return на всех interactions.
Если zoom automation недоступна — записать manual browser evidence; не ставить fake PASS.

У каждого capture: route, viewport CSS px, DPR, zoom, capture_kind, actual PNG dimensions,
UTC timestamp, browser/version/OS, source-code digest, build command, snapshot SHA;
loaded fonts и state (default/filter/selected/long string fixture). Не путать screenshot
pixel dimensions со viewport при нестандартном DPR/zoom. Основная mandatory matrix: DPR 1.
Crops — только supplementary evidence, не замена whole screen.

## Hard layout gate (actual DOM + visual inspection)

- document scrollWidth − clientWidth ≤ 1 px; body/root overflow:hidden не способ спрятать bug.
- Нет visible text overlaps, card content escaping bounds и unintentionally clipped text.
- Range.getClientRects для long codes/axis ticks/cell text; сравнить с panel bounds и соседями.
  Legit tooltip/portal overlay проверяется по собственному overlay container, не false positive.
- Long identifiers: intentional ellipsis + keyboard-readable full value или controlled wrap.
  Не принять break-all всего пользовательского текста как хорошую typography.
- Tables/heatmap scroll только в named local scroll region (`data-cc-scroll`), с focus/label.
  Badge/label inside flex/grid `min-width:0`, border-box; font/zoom не выбивает content.
- D48 stress: 336 cells, 4 strata / 84 days, month/day boundaries, protected navigation без metrics,
 1024 px width, 200% zoom, long localized labels. Colorblind-accessible legend + shape/focus.
- Tooltips/drawers/copy/filter tested реальной keyboard traversal; `.focus()` в jsdom недостаточно.
- Browser request log: frontend/static local assets + existing loopback API only. Нет CDN fonts,
  RPC, background telemetry. Screenshot fixture помечен SYNTHETIC/DEMO, не замена основного real view.

Playwright harness пишет машинный layout report на каждый after viewport; реализует Grok
CC02.0. Validator проверяет report, а не сам измеряет DOM. Test snapshots регрессий обновляют
только после независимого visual acceptance; `--update-snapshots` не лечит плохой дизайн.

### Shell gate до реконструкции страниц

CC02.1 обязан измерять и сохранять настоящие global/page counts, даже если они плохие.
Дополнительно `shell_counts` содержит overflow_px (≤1), text_overlap_count,
card_escape_count, unintended_clip_count (все 0) внутри shell regions. Обязательные
`inspected_regions`: sidebar, header, main-grid, shell-containment. Положительные глобальные
counts требуют `page_findings`: route, metric (имя global metric), severity, reason,
owner_task — соответствующая CC02.2–7 задача. Page defects не получают PASS и не маскируются
нулём; shell containment не должен сам создавать page overflow. Code mapping owner→route
фиксирован. Shell rubric оценивает только эти regions и их композицию.
Page/final tasks используют полные global counts и `PAGE_REGIONS[route]` из validator:
только zero-defects (overflow ≤1), пустой page_findings; final не принимает отложенные findings.
PAGE_REGIONS — логические области проверки, не требование сохранить прежние DOM/classes/tables.
Models: model-summary и benchmark-comparison; harness сопоставляет текущие elements этим
областям и проверяет видимые interactive states. Не цементировать старую DOM structure.

## Quality rubric (independent judgment, не pixel-diff score)

Для каждого screen reviewer выставляет оценки 1–5:
composition, hierarchy, density, typography, geometry, navigation, identity, reference_family,
state_clarity, interaction. **Все ≥ 4; среднее ≥ 4.2**. Эти оценки — качественная rubric, не
утверждение «измерено 90% similarity». Narrative для каждой axis обязателен; одна общая строка
«navy and no live» не проходит. Reviewer перечисляет remaining weaknesses даже при accept.
Zero unresolved critical/major findings; minor с объяснением влияния и owner. Model assignment
не заменяет эту работу. Цель — заметная design family, не literal fake-candle clone.

## Receipt v1 и проверка

Developer-only validator `scripts/visual_gate.py`; `workflow.py` вызывает его для CC02 DONE.
Все accepted receipts/screenshots/layout reports — committed, не только ignored `.local`.
Большие временные transcripts сохраняются ignored; before пользовательские PNG неизменны.

Минимальная форма (не PASS fixture):

```json
{
  "schema_version": 1,
  "task_id": "CC02.2",
  "acceptance_scope": "page",
  "web_sha256": "<scripts/visual_gate.py --digest>",
  "snapshot_sha256": "<sha256 published/observatory-v1.json>",
  "reference_sha256": "dd03716ed059c4bdf85726489460d4c07acb6836a872553ccf2157bb8dd014d6",
  "builder_id": "<native agent/session id>",
  "reviewer_id": "<different native agent/session id>",
  "model_binding": "UNVERIFIED",
  "first_review": "<what looked bad against target>",
  "fix_summary": "<what changed after independent review>",
  "remaining_findings": [],
  "screens": [{
    "route": "/",
    "rubric_scope": "page",
    "before": {"path":"<PNG>","sha256":"<SHA>","route":"/","capture_id":"<unique capture id>","viewport":[1600,1000],"capture":"viewport","dpr":1,"zoom":1,"web_sha256":"<first code hash>","captured_at":"<UTC>","browser":"<actual version>"},
    "after": ["<same-shaped capture objects for 4 viewports + full-page; each has layout_report path + sha256>"],
    "rubric": {"composition":{"score":4,"reason":"<concrete observation>"}},
    "remaining_weaknesses":"<honest screen-specific limitations>"
  }]
}
```

Полный набор axes/required routes/viewports — constants validator (не удалить из state).
`layout_report` — `{path,sha256}` к JSON с source=`browser-dom`, route, viewport, web_sha256,
page_overflow_px, text_overlap_count, card_escape_count, unintended_clip_count,
page_overflow_hidden=false, allowed_scroll_containers (selectors), inspected_regions,
acceptance_scope, capture_id, screenshot_sha256, page_findings. Capture ID / route / PNG SHA
должны совпадать с конкретным capture; cannot attach another page screenshot by mistake.
Для fullpage layout viewport остаётся 1600×1000. Counts/report выдаёт реальный browser harness,
не handwritten zeros. Validator не может доказать honesty происхождения PNG/DOM, поэтому
independent review остаётся обязательным. Signed/remote service здесь не нужен.

До принятия task:

```sh
python3 apps/control-center/scripts/visual_gate.py --digest
python3 apps/control-center/scripts/visual_gate.py --receipt docs/control-center/references/cc02-final/CC02.2/review.json --task CC02.2
python3 apps/control-center/scripts/workflow.py --check
```

Receipt проверяет exact current web digest (src/public/config/package/lock), snapshot/reference
hashes, image existence/SHA/dimensions, bounded PNG chunks/CRC/decompressed scanlines, complete matrix,
reviewer≠builder, findings/rubric и
layout measurements. Workflow сохраняет `accepted_web_sha256` у DONE task; старые задачи
могут иметь исторический digest после следующих page changes. **CC02.8/final COMPLETE
требует current digest всех 6 screens**, поэтому old page PASS не заменит final integration review.
Новый shared shell/token change → переснять затронутые страницы; full matrix в конце обязательна.
CC02.8 дополнительно требует `final_checks`: zoom200, keyboard, reduced_motion,
network_boundary, semantic_tests, build, boundary_review. Каждый — `{result:"PASS",
evidence:{path,sha256}}` с существующим bounded log/report в final artifact directory.
Evidence здесь — structured JSON envelope, не любой непустой log или PNG:

```json
{
  "schema_version": 1,
  "kind": "zoom200",
  "result": "PASS",
  "web_sha256": "<current digest>",
  "snapshot_sha256": "<approved snapshot SHA>",
  "executed_at": "<UTC timestamp>",
  "command": "<actual command or manual browser procedure>",
  "raw_evidence": {"path":"<bounded raw report/log>","sha256":"<SHA>"},
  "routes": ["/", "/calendar"],
  "browser": "<actual version>",
  "zoom": 2,
  "zoom_method": "browser"
}
```

Каждый envelope привязан к current build и snapshot, его kind совпадает с именем проверки.
Browser checks содержат browser/version. Дополнительные условия:

- zoom200: `/` + `/calendar`, zoom=2, zoom_method=browser.
- reduced_motion: те же routes, prefers_reduced_motion=reduce.
- keyboard: все 6 routes, interactions содержат Tab/Arrow/Escape/focus-return.
- network_boundary: все 6 routes, disallowed_request_count=0.
- semantic_tests и build: exit_code=0.
- boundary_review: все 6 routes, reviewer_id.

Только фактические browser/test результаты, не handwritten PASS. Raw evidence также SHA-bound;
структура защищает от случайного reuse чужого/старого log, не доказывает честность автора.
Полные executable examples — tests/test_visual_gate.py, **synthetic fixtures, не actual evidence**.

## Definition of Done CC02

Все CC02 tasks DONE; six major pages coherent; Overview premium; target family ≥ rubric;
нет overflow/overlap/escaping text; professional typography/rail/header; major datasets
визуализированы, таблицы secondary; truthful beautiful locked/protected/unavailable; motion
не fake live, reduced motion работает корректно; all viewport/layout/keyboard/zoom checks; semantic/boundary
suite PASS; final independent review accepts текущий build; PNG/receipts сохранены; docs,
Memory Bank, scope/Git чистые. User acceptance хранится отдельно и не самоназначается.
Работа над Tauri/core contract/engine/trading не нужна для DONE.
