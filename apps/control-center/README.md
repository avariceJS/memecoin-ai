# Memecoin AI Control Center — web v1 / CC02

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

Текущий план Grok: [CC02](../../docs/control-center/cc02/PLAN.md),
[готовый prompt](../../docs/control-center/GROK-PROMPT.md).


## Web v1

Ветка `control-center/observatory-handoff` содержит read-only оболочку по published
projection. Это не торговая консоль и не merge в main. Live/paper/replay остаются
недоступными. Model binding исполнителей в task card не подтверждён и записан как
UNVERIFIED. На Node 24.5.0 `npm ci` предупреждает, что часть jsdom-зависимостей хочет
Node 24.15 или новее; тесты при этом проходят. Production bundle около 515 kB.

## CC-01 — Coordinator workflow

Human progress: [memory/STATUS.md](memory/STATUS.md). Native team, модели, settings и recovery:
[Autonomous workflow](../../docs/control-center/AUTONOMOUS-WORKFLOW.md).
Primary visual contract: [DESIGN-SYSTEM](../../docs/control-center/DESIGN-SYSTEM.md).
Перед coding/commit также `python3 apps/control-center/scripts/workflow.py --check`.
После обновления WORK_STATE: `python3 apps/control-center/scripts/workflow.py --render`.
Эти developer scripts не вызываются runtime API и не управляют core/services.

## CC02 — visual reconstruction pending

Web v1 функционально сохраняется, визуально отклонён. [Independent audit](../../docs/control-center/cc02/AUDIT.md)
и [screenshot acceptance](../../docs/control-center/cc02/VISUAL-ACCEPTANCE.md) обязательны перед redesign.
Browser harness CC02.0: `@playwright/test` 1.63.0 и `@types/node` 24.19.1 — devDependencies.
Production bundle не вырос (JS 514.84 kB). Harness измеряет дефекты v1 и не принимает их.

```sh
npx --prefix apps/control-center/web playwright install chromium
npm --prefix apps/control-center/web run e2e
npm --prefix apps/control-center/web run e2e:check
```

Сначала shell, затем Overview как эталон; старые visual PASS не переносятся.
Current queue — memory/WORK_STATE.json.
