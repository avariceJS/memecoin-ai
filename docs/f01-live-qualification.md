# D51: bounded live qualification и условия эксплуатации

## План до прогона — 2026-10-04

D50 принят пользователем. Работа в core `main`; UI worktree, D43, reserved holdout,
F01 specification и D48 calendar не менять. D51 не разрешает calendar activation.

1. Один read-only проход доступных account tools: usage, список приложений, metadata
   приложения. Не вызывать market RPC для проверки credentials. Browser discovery один
   раз; при отсутствии браузера — не повторять transport/access tuning.
2. Для единственного live окна нужны account-specific Free/no-paid-overage и exact
   mapping существующего runtime credential к этому аккаунту. Допустимо несекретное
   подтверждение владельца доступа с честно указанным происхождением. MCP USD0,
   masked key suffix и public pricing сами по себе недостаточны. Paid budget0.
3. После substantive review: отдельный pinned receipt `bounded_qualification`,180RPC,
   allocation3600CU, validity покрывает600s, единственный attempt root вне actual F01.
   Runtime — unchanged D50 bundle manifest SHA
   `a06f1d69ff8fb1c0f8647fc5622c3e2d8c1811e697d40eb8be15deb1663da880`.
   Никаких automatic approvals. До proof — RPC0 и никакой reservation.
4. Thin launcher вызывает existing supervisor, сохраняет exclusive durable reservation
   до запуска; crash/ошибка/существующий каталог исключают повтор. Два segment×90RPC,
  210s/segment,600s от reservation, первые ошибки останавливают работу. Никаких retries,
   смены профиля/пула, WSS, execution RPC или продления budget после результата.
5. Live pass:4completed phases,180successful coherent finalized/profile2 snapshots,
  180advanced feature starts, no guard failures, offline raw/feature/target reproduction.
   Неполное окно — fail, даже если часть данных полезна для engineering. Сохранить
   freshness/latency/gaps/statuses/storage, реальные availability неизменны. Clock age и
   freshness квалифицируются по existing frozen guards, без нового подбора порогов.
   Ни bounded окно, ни fixtures не импортируются в actual F01 registry.
6. Read-only host assessment: power/sleep/login/free disk, отсутствие нового launchd job,
   current runtime/credential binding. Не менять power settings и D43. Описать условия
   awake/login/monitoring; metadata не выдавать за измеренный uptime/reboot qualification.
7. Tests сначала offline, затем live только после пункта2. Immutable evidence, Memory
   Bank, логические commits/push. При недостающем доступе D51 остаётся OPEN/BLOCKED,
   а live — `не проведён`; не объявлять очередной этап завершённым вместо снятия blocker.

Calendar activation требует отдельного итогового решения после evidence. T2026-10-07
не сдвигать; missed окна не backfill. ML/research training, holdout release, торговля,
подпись/платежи/upgrades запрещены. Этот план не меняет научные критерии F01.

## Фактический статус

**D51 OPEN/BLOCKED; proposal NOT READY FOR ACTIVATION.** План97c2d26,
launcher4ed893b. [Evidence](evidence/f01-live-qualification-2026-10-04.json).
Инженерная подготовка выполнена; live qualification и её offline reproduction
**не проведены**. Новых рыночных RPC0, reservations0, receipts0, research training0.
Никакой части этого результата нельзя приписывать качество рыночной модели.

Один проход Alchemy MCP: usage27940CU/USD0,00, dataThrough2026-10-03T22:00UTC;
приложение `memcoin-ai`, app ID `nsvflslm1kqtntvz`, Solana mainnet включён. App ID
не является billing account ID. Masked credential не сохранялся в curated evidence.
Разницу usage с D50 нельзя приписать D51: D51 не делал market RPC. MCP не вернул
тариф, запрет automatic paid overage или exact billing mapping. Browser discovery `[]`:
категория `browser_runtime_unavailable`; дальнейших попыток обхода/настройки не было.

Пользователю задан один запрос недостающих несекретных фактов. На момент отчёта ответа
нет. Нужны идентичность billing account и приложения, подтверждение принадлежности
существующего локального endpoint этому приложению/аккаунту, Free без платного
перерасхода и доступная allocation3600CU. Сохранить источник и время подтверждения;
owner attestation не называть независимой provider verification. Сам endpoint/ключ
в чат или evidence не нужен. До substantive review нельзя выпускать approved receipt.

### Что готово

`tools/f01_live_qualification.py` — тонкий launcher поверх unchanged D50 supervisor;
runtime snapshot не пересобран и pin не изменён. Проверяет scoped receipt/credential,
exact attempt root, отсутствие пересечения с protected paths, validity на всё600s,
STOP и disk headroom **до reservation**. Exclusive directory claim предотвращает
одновременный запуск; существующий каталог, включая crash до reservation, не повторяется.
Durable reservation180RPC пишется до supervisor. Исключение после reservation сохраняет
safe failure без provider payload, полного URL/ключа. Нет receipt generator, выбора
альтернативного транспорта, scheduler install или actual registry ingestion.

`--check` выполняет только preflight, не резервирует бюджет и не запускает RPC.
Проверка на реальном D50 bundle без receipt дала ожидаемый safe log:

```json
{"status":"closed","category":"separate_decision_required","error_type":"GateError"}
```

Каталог попытки и actual F01 source root отсутствуют. Это отказ admission до попытки,
не failed calendar window. `completed` от launcher означает завершение supervisor;
финальный PASS квалификации требует отдельного анализа критериев плана и replay raw.

10новых offline tests: отказ до claim, preflight без reservation, durable-before-worker,
no-retry после crash/partial claim, concurrent claim, protected path/symlink rejection,
short validity/STOP/disk rejection, исключение secret-bearing exception payload из лога.
Reviews в тестах — явные mock fixtures, не настоящие approvals. `make check`:506passed,
2skipped (optional ML),Ruff/format/mypy; `make smoke` успешен. Build/ML training не
повторялись: package sources не менялись. Source diff относительно3887f97 пуст.
UI worktree отдельно, observed HEADb97c7b2/ahead15; D51 его не менял.

### Host: наблюдения и эксплуатационные условия

Read-only snapshot:134767243264bytes свободно, console UID501 совпадает с process UID501,
host на Battery Power40%, pmset sleep2100/standby1/hibernatemode3. Uptime47days не
доказывает непрерывную доступность: он не исключает сон. Новый service отсутствует
(`launchctl print` exit113). Power settings, D43 job и reserved raw не менялись/не читались.

Перед calendar activation нужны устойчивое питание и сеть, awake Mac на каждом due
окне и живой GUI login для staged LaunchAgent. Закрытие крышки, logout, reboot или
потеря питания могут привести к missed/failed без backfill; никакой launchd SLA из
plist не следует. Не включать auto-login и не менять power settings неявно. Если host
условия не обеспечены, не объявлять84day coverage достижимым. По текущему battery
snapshot этих гарантий нет; это отдельное ограничение даже после billing proof.

Read-only monitoring после отдельной activation: metadata журнала, reservation totals,
safe exit categories, disk headroom, питание/session/service presence и review expiry.
Не читать holdout features/targets для мониторинга. STOP срабатывает до следующего RPC;
8cumulative failed закрывают календарь, expired review закрывает новые RPC. Disk floor
2GiB+512MiB — прежний stop guard, не зарезервированное место для84days. При пропуске
не переноситьT и не запускать catch-up. OS sleep/reboot эксперимент не делали из-за D43.

### Точный следующий шаг внутри D51

После получения недостающих фактов сохранить их отдельными immutable evidence files,
выполнить substantive review и выпустить один pinned `bounded_qualification` receipt.
Exact attempt root: `/Users/root1/Desktop/memecoin-ai/work/collector-d51/attempts/live-01`;
локальный STOP: `work/collector-d51/STOP`. Receipt references review/bundle/proposal/
credential fingerprints; max180RPC/3600CU, validity не меньше600s на старте. Источник
авторизации bounded проверки — поручение продолжитьD51 и этот preregistered план;
оно не является разрешением на calendar activation или платные расходы.

Запуск из core с existing `.venv/bin/python -B tools/f01_live_qualification.py`, bundle
`work/collector-d50/runtime-candidate-final`, externally pinned `--bundle-sha`, reviewed
`--receipt`/`--receipt-sha`, exact `--output` выше. Сначала `--check`, затем единственный
вызов без него. Все existing guards действуют; не повторять неудачную попытку.

Offline: `verify_campaign` воспроизводит каждый segment через `verify_forward`,
`verify_execution_targets` — targets из raw. Сохранить вывод в новом sibling report,
сверить inventory/seal до и после, проверить180advanced starts и всю frozen cohort.
Отдельно сообщить реальные времена, gaps/freshness/latency и размер; не выдавать
conditional pre-network targets за net execution или ML edge. Actual F01 registry
остаётся пустым. Только после этого — итоговый verdict и отдельное activation decision.
**D51 не закрыт и не переименован в D52.**
