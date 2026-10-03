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
