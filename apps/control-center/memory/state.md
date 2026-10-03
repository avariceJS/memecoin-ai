# UI workstream status

Обновлено2026-10-03, Europe/Moscow. CC-00 принят; CC-01 visual/team follow-up подготовлен.
Текущая очередь реализации — WORK_STATE.json; краткий обзор — STATUS.md.
Core baseline `6a7e079` (D49), UI plan `f28e5fd`, adapter/guards `06893f3`.
Branch `control-center/observatory-handoff`; тот же managed worktree
`/Users/root1/.codex/worktrees/ec89/memecoin-ai`. Main развивается в другом core-чате.

## Готово

- Правила UI AGENTS + Cursor alwaysApply, curated onboarding, product/architecture/stack,
  data map, typedDTO, read-only API, security/Git/demo/testing workflow, phased plan.
- Отдельный UI Memory Bank, formal operational export proposal CC-P001 (не принят).
- Stdlib Python adapter без core imports/RPC/subprocess/ML. Только2GET endpoints наloopback.
-9pinned committed sources → deterministic reviewed projection33 910bytes. Это real
  historical evidence с engineering/synthetic qualification, не fake realtime и не raw.
- D43calendar6окон (4–5protected metadata only), D45summary174decisions/1392scenarios,
  F01recorded0/114researchfits/135engineeringfits, D48plan336windows/0admitted,
  D44runs15/benchmarks40, D49source6/6qualification иNOT_READY verdict.
-9stdlib tests, scoped Ruff/format, exact projection reproduction и Git scope guard прошли.
  HTTP tests bind only ephemeral loopback; provider calls0, training0, core changes0.
- Python3.13.6, Node24.5.0, npm11.5.1 доступны. Frontend dependencies НЕ установлены,
  npm lock/frontend/typecheck/E2E ещё не созданы/не проведены. Глобальный Ruff был доступен;
  использовался только для UI файлов, core environment не изменялся.

## Не готово / ограничения

React frontend пока отсутствует; это безопасная стартовая база, не большой GUI.
Actual current service health/live market/models/predictions/hardware/logs unavailable.
No global exposure count; recorded release0 не globally unseen proof.
No collector controls/research launch/emergency-stop/trading/wallet API.
D43/F01 operational roots/holdout/credentials не читались и не изменялись. `work/` этого
worktree отсутствует, не добавлять symlink к основному checkout. Public evidence относится
кD49 baseline и не обновляется автоматически main D50+.

## Следующий шаг Grok

Phase 1 shell есть в `apps/control-center/web`: validated snapshot client и navy
навигация на реальной проекции. Model binding остаётся UNVERIFIED, configured policy
`composer-2.5[fast=false]` не менялась. `/research` показывает 0/114 отдельно от 135 engineering. Adversarial fixtures
подтверждают, что numeric pass не снимает LOCKED. Следующий шаг — P5.1
Models / runs / benchmarks, затем System и quality. Core interface deficiency →
proposal, никакого core patch.

Запуск/проверки: ../README.md. План: docs/control-center/04-phases.md.
После каждого крупного phase обновлять эту память и коротко сообщать результат.


## CC-01 — visual contract и Coordinator (2026-10-03)

Primary PNG предоставлен пользователем, открыт, hash pinned в DESIGN-SYSTEM; описание
композиции/типографики/states/charts/a11y и обязательный independent visual gate готовы.
Добавлены 6 native `.cursor/agents/cc-*.md`, explicit Composer2.5 standard binding;
Coordinator — Grok4.7 Extra High picker. Cursor3.22.12 arm64 установлен, native docs и
локальные признаки сверены. Account entitlement/actual model task card ещё UNVERIFIED;
CLI отсутствует, launcher auto-install не запускался. Native smoke — первая задача P0.1.

AGENT-TEAM/AUTONOMOUS-WORKFLOW: bounded briefs, ≤3000char summaries, disjoint leases,
Coordinator-only state/Git/lockfile, no nested/cloud agents, ordinary fixes автономно,
три recovery prompts и loop до web-v1 COMPLETE/внешнего BLOCKED_NEEDS_USER.
WORK_STATE содержит 15 незавершённых web tasks, не притворяется готовым GUI.
Scope расширен явно только на6named definitions, не permissions/hooks/global settings.

22 unittest PASS (9adapter+13workflow/guard); scope/exact projection/Ruff/format PASS.
Independent review нашёл и устранил index/rename bypass в старом guard; проверено fixtures
в disposable Git repo внутри ignored `.local/`, основной Git config не менялся.
Lint first pass E501/F401, затем I001 исправлены; runtime/provider failures отсутствовали.
Новые scripts только development, adapter/runtime/data map/source pins не менялись.
Core/root Memory Bank не изменены; D50 не начат. Web implementation не проведена.

- Final review correction CC-01: canonical/ancestor lease overlap checks и shared-directory
  denial; missing PNG не мешает state recovery, explicit `--visual` fail-closed.
  Два regression tests добавлены; итог22tests PASS. Исторические review receipts не
  доказывают наличие картинки в новом session, каждый visual agent обязан открыть её снова.

CC-01 implementation commit `38765863d54e54115e4beca72a0012fd050a4b3e`; итоговый review/evidence:
`reports/CC-01-review.md`, `docs/control-center/autonomy-evidence.json`.
