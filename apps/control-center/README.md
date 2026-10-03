# Memecoin AI Control Center — стартовая база

**Read-only Observatory, исторические evidence, core baseline6a7e079.**
Adapter — stdlib, без PyTorch/venv. Frontend — `web/`, exact pins в
[toolchain-pins.md](../../docs/control-center/toolchain-pins.md).
Начать с [правил](AGENTS.md) и [onboarding](../../docs/control-center/ONBOARDING.md).

Из корня isolated repository:

```sh
python3 apps/control-center/scripts/check_scope.py
python3 -m unittest discover -s apps/control-center/tests -v
python3 apps/control-center/adapter/export_snapshot.py --check
python3 apps/control-center/adapter/server.py --dump
python3 apps/control-center/adapter/server.py
```

Последняя команда держит только UI adapter foreground на127.0.0.1:8787; Ctrl+C закрывает
его, не collector. Оболочка, отдельным процессом:

```sh
npm --prefix apps/control-center/web ci
npm --prefix apps/control-center/web run dev
```

Dev server только `127.0.0.1:5173`. Проверки: `npm --prefix apps/control-center/web run check`,
`test`, `build`. В другом терминале:

```sh
curl -fsS -H 'X-Control-Center: 1' http://127.0.0.1:8787/api/v1/capabilities
curl -fsS -H 'X-Control-Center: 1' http://127.0.0.1:8787/api/v1/snapshot
```

Открытие URL API в браузере напрямую вернёт403: custom header нужен для API boundary.
Frontend ходит с exact origin `http://127.0.0.1:5173`, не с `localhost`.
HTTP API не раздаёт repository/static files, не имеет PUT/POST/control endpoints.
Обёртка `servedAt` — время ответа UI, **не** свежесть рыночных данных. Каждый source
содержит дату evidence и recorded timestamp, если он известен.

`published/` — маленькая проверенная **производная** проекция реальных committed evidence,
а не demo и не актуальный мониторинг. Models/runs остаются ENGINEERING, synthetic inputs
помечены. Protected windows содержат только ID/planned time/access. `source-pins.json`
задаёт конечный offline allowlist; runtime не читает его sources и не ходит в `work/`.
Не перегенерировать bundle поверх старого и не редактировать числа вручную.

План Grok: [фазы](../../docs/control-center/04-phases.md),
[готовый prompt](../../docs/control-center/GROK-PROMPT.md).


## CC-01 — Coordinator workflow (без нового frontend)

Human progress: [memory/STATUS.md](memory/STATUS.md). Native team, модели, settings и recovery:
[Autonomous workflow](../../docs/control-center/AUTONOMOUS-WORKFLOW.md).
Primary visual contract: [DESIGN-SYSTEM](../../docs/control-center/DESIGN-SYSTEM.md).
Перед coding/commit также `python3 apps/control-center/scripts/workflow.py --check`.
После обновления WORK_STATE: `python3 apps/control-center/scripts/workflow.py --render`.
Эти developer scripts не вызываются runtime API и не управляют core/services.
