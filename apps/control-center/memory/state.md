# UI workstream status

Обновлено2026-10-03, Europe/Moscow. CC-00 architectural handoff завершён.
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

Сначала весь обязательный curated onboarding, затем Git/scope/tests. ВыполнитьPhase0
(toolchain/dependency pins/context audit), Phase1 создать web/React+TS+Vite и первый полезный
read-only shell на настоящем API. Потом Calendar/Corpus, Overview/Sources, Research,
Models/Benchmarks, System/capability boundaries, quality. Не ждать live interface, чтобы
делать доступные screens. Core interface deficiency → proposal, никакого core patch.

Запуск/проверки: ../README.md. План: docs/control-center/04-phases.md.
После каждого крупного phase обновлять эту память и коротко сообщать результат.
