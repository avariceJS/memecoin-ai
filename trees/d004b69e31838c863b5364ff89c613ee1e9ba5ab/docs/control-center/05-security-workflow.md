# Security, Git, development и testing workflow

## Threat model и реальные границы

Предотвращаем случайный выход frontend agent за scope, утечку holdout/secrets в браузер,
запросы от вредоносной web page/DNS rebinding и ошибочную интерпретацию данных. Нельзя
обещать OS изоляцию: Cursor и оператор имеют тот же user/filesystem. AGENTS, branch,
scope guard и tests не мешают намеренному владельцу переписать код или открыть raw.
Если нужна hard isolation, отдельный OS user/container с read-only копиями approved
exports — новое операционное решение. Здесь не менять права основного collector/main.

## Инварианты

1. HTTP runtime открывает только fixed published bundle+manifest, не core paths.
2. Offline exporter читает только reviewed9pins, не следует paths внутри JSON.
3. Никаких work scans, symlink dirs/files, traversal, FIFO/devices или oversized JSON.
4. Защищённые окна — metadata-only union. Labels отсутствуют в response, не просто скрыты.
5. В adapter нет provider/core imports, RPC или subprocess. Developer Git guard отдельный
   и не вызывается из HTTP/frontend.
6. GET-only, exact Host/origin/custom header. Нет внешнего bind/static repo/log echo.
7. Нет wallet/STOP/launchctl/admission/runner command. Disabled UI не заменяет отсутствие API.
8. REAL/DEMO/SYNTHETIC INPUT/RECORDED/UNKNOWN — явные разные оси.
9. Hash check — byte integrity, не raw replay/qualification или globally unseen certificate.
10. UI не переписывает core artifacts/old outputs. Main развивается независимо.

## Git схема A

Directory: `/Users/root1/.codex/worktrees/ec89/memecoin-ai`.
Branch: `control-center/observatory-handoff`, baseline6a7e079. Main в другом worktree.
Grok наследует эту ветку; новый worktree не нужен. Git objects/refs/config общие,
working files раздельны: не изменять shared hooks/config, не git clean/worktree prune/
remove/reset. Не checkout/push main, не pull/rebase main в UI ветку, не auto-sync D50.
Всю UI branch можно отбросить без удаления/изменения operational services или corpus.

Перед каждым commit:

```sh
git status --short --branch
git diff --stat
git diff --check
python3 apps/control-center/scripts/check_scope.py
python3 -m unittest discover -s apps/control-center/tests -v
python3 apps/control-center/adapter/export_snapshot.py --check
# После появления web/:
# npm --prefix apps/control-center/web ci
# npm --prefix apps/control-center/web run check
# npm --prefix apps/control-center/web run test
# npm --prefix apps/control-center/web run build
```

`git add` — explicit owned paths, не global `git add .`. Логические feature/test/docs
commits. Push допустим только в dedicated ветку, без force:

```sh
git push -u origin HEAD:refs/heads/control-center/observatory-handoff
```

Не merge main. Позже reviewer выберет merge/squash через отдельный UI PR. При integration
нужно пересмотреть alwaysApply Cursor rule и baseline scope guard, чтобы правила этого
форка не блокировали core agent на main. Это задача reviewer, не обход правил Grok.
Если другой агент изменил worktree, не откатывать его работу; выяснить принадлежность.
Scope guard сообщает чужие paths: не удалять guard ради зелёного теста.

## Development process

Phase0 фиксирует UI-only Node/npm/exact package versions/package-lock в `web/`.
Root Python/dev locks не менять; backend tests stdlib. Если нужно окружение — отдельное
`apps/control-center/.venv`. Не выполнять root make setup/training/smoke только ради UI:
core не изменён, часть core commands создаёт artifacts. Проверки root AGENTS остаются
для core changes; здесь запускать собственные UI checks и scoped tests.

Adapter foreground, сам не пишет files. Vite — отдельный процесс127.0.0.1:5173 с strictPort.
Root Vite=web/, envDir UI-only; server.fs.allow не шире web и UI contracts. Нет repo/work
mounts, VITE_* provider keys, auto-upload telemetry. Не --host0.0.0.0, tunnels, public/cloud
preview. Не раздавать сырой evidence или repository через static server.
Launcher может управлять только своими UI processes; никаких killall Python или launchd.

## Tests по уровням

- Foundation stdlib tests: exact projection reproduction; protected DTO keys; null/time
  precision; no raw paths/core imports; traversal/symlink/FIFO/size/digest/JSON guards;
  HTTP method/origin/Host/query denial и safe error. Не весь security audit будущего app.
- Contract: unknown major/additive fields; все availability branches; source date ≠
  servedAt; partial data; future publish race/revision rollback требуют своих tests.
- Component: REAL/DEMO cache separation; engineering/synthetic badges; protected cells
  без metric details; stage error ≠ overall error; null не превращается в0.
- E2E: UI against local recorded API; overview/calendar/research/models; disconnect API;
  response/storage absence protected fields; keyboard navigation/timezones/URL filters.
- Browser network: только loopback API+Vite assets в tests. Никаких Alchemy/QuickNode,
  wallets, external analytics или автоматического Sentry upload.
- Performance: измерить production build на M4,336calendar windows+40benchmarks;
  10krows workload только explicit synthetic fixture. Цели scroll/inputp95<100ms и
  usable1280/1920/2560 layouts — инженерные бюджеты, не уже измеренный результат.
- A11y: focus order/visible focus, contrast, color-independent status, reduced motion,
  200%zoom, table alternative для chart. Axe не заменяет ручную проверку.

## Demo policy

Default DEMO off. Отдельный explicit selection, постоянный banner «DEMO — synthetic UI
scenario» на всех screens/tooltips/exports; отдельные query keys/routes/source IDs.
При потере API нельзя автоматически подменять данные mock. Test fixtures хранятся в
web/src/test-fixtures после Phase1, не содержат скопированные raw credentials/signatures.
В demo trading actions не имеют core effects и не изображают успешную реальную сделку.
Сначала полезный REAL recorded evidence UI; demo не заменяет приёмку реальных providers.

## Tauri отдельно

Не раньше web acceptance. Новый review origin/CSP/window capabilities/sidecar lifecycle/
signing/update policy. Нет shell plugin, arbitrary filesystem, collector autostart или
keychain access по умолчанию. Sidecar, если появится, обслуживает UI reads, не core engine.
Оплата/signing credentials/deployment не входят в этот workstream. Текущий handoff не .app.
