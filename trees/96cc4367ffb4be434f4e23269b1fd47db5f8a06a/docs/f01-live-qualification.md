# D51: bounded live qualification и условия эксплуатации

**Итог после owner attestation: BOUNDED LIVE PASS; calendar не активирован.**
Единственная попытка180RPC завершена, raw и independent wheel replay совпали.
[Итоговое evidence](evidence/f01-live-result-2026-10-04.json).
Предыдущий blocked report сохранён отдельно, ниже — история допуска и полный результат.

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

## Состояние до owner attestation (сохранённая история)

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

## Допуск единственной попытки — 2026-10-04, до результата

Владелец подтвердил в чате по Dashboard принадлежность приложения и существующего
локального endpoint своему текущему аккаунту, отсутствие PAYG/платного перерасхода
и карты, usage около28.1K/30M и доступность3600CU. Явно разрешил продолжитьD51.
[Exact owner attestation](evidence/f01-d51-owner-attestation-2026-10-04.json) сохраняет
происхождение; это **не independent provider verification**. Free классифицирован по
сообщённому режиму30M/no-PAYG; numeric provider account ID не известен, review использует
явно обозначенный local alias аккаунта владельца, а не выдаёт app ID за account ID.

Substantive review принят для одной bounded попытки. Local immutable review/receipt:
`work/collector-d51/review-owner-01/`; validity1hour от review, max180RPC/3600CU, paid0,
exact `attempts/live-01`, прежние bundle/proposal/credential pins. Calendar scope не
разрешён. Это конкретное решение по owner evidence, не approval из MCP USD0.

Новый MCP usage-before-live27660CU/USD0 при dataThrough23:59UTC ниже прежнего27940CU:
`account_usage_nonmonotonic`. Причина provider/admin aggregation не установлена;
summary не используется как точный расход/квитанция нашего окна. Budget основан на
owner allocation и локальном upper bound180attempts; shared provider quota атомарно
не резервируется. Current host battery19%/estimated1h33, free134,51GB — достаточно
наблюдаемого headroom для bounded600s, но не подтверждает долгосрочную доступность.
Power/runtime/D43 не менялись. Прежний stop budget и все pass criteria сохраняются.

## Итог единственной live попытки

D51 bounded qualification завершён: **PASS** по preregistered критериям. Реальный Alchemy
HTTP, finalized/profile2, persistent transport, unchanged D50 bundle.180RPC,2segments,
по60checkpoints каждого из3frozen pools; все180 `advanced`. Ошибок/ретраев/новых попыток0.
Reservation2026-10-04T00:02:04.529397Z, завершение00:07:03.719975Z; это03:02–03:07 Europe/Moscow.
Seed отсутствует, sampling rules/CPMM/features/targets не менялись. Весь runtime в пределах
600s; segment durations148,77/149,00s в пределах210s. Supervisor299,17s, внешний launcher299,34s.

| Метрика | Измерено в одном bounded окне |
|---|---|
| Successful coherent snapshots |180/180, по60на pool |
| Features |180starts,174usable;6history_warmup (3pools×2segments) |
| RPC latency p50 / p95 / max |87,3 /136,0 /819,0ms |
| Clock age при receipt p50 / p95 / max |10,229 /11,351 /12,362s |
| Clock age при feature publication max |14,363s, frozen guard45s |
| Receipt age при feature publication max |2,372s, frozen guard25s |
| Интервал availability одного pool p50 / p95 / max |5,000 /5,075 /5,599s |
| Raw response bytes / все attempt artifacts |655440 /4749254bytes |
| Offline checkout / installed wheel |Побайтово одинаковые reports, raw unchanged |

Latency измерена request-start→receipt, publication overhead отдельно: p950,001112s.
Интервалы считаются по фактической availability, включая переход между сегментами;
история/targets через boundary не склеиваются. Это coherent bank state одного HTTP
checkpoint, не atomic cross-pool state и не доказательство наблюдения intervening trades.

Existing execution export:1440variants для180decisions,137mature decisions,70engineering
evaluation decisions;994conditional_roundtrip,24entry_unfilled,26exit_unfilled,396censored.
Варианты и overlapping labels зависимы. Это conditional snapshot reset **до сетевых
издержек**, не фактические fills, не net PnL и не calibration inclusion. Разбиения времени
сохранены в campaign/segment plans; campaign calibration cutoff00:04:34.705813Z.
Actual F01 admission0, research training0; bounded окно находится вне будущего календаря
и не используется для снятия readiness или model selection.

### Воспроизведение и неизменяемость

Raw: `work/collector-d51/attempts/live-01`, seal SHA
`3a4fd46aaa1cc4cb7daf8c91d08a838aa7295cff277305660b9582aeab48d5d9`.
Проверены full inventory и seal до/после анализа. `verify_campaign` воспроизвёл online
features/targets из raw, `verify_execution_targets` — conditional execution export.
`tools/f01_d51_report.py` (18e6c6b) запрещает network и читает только фиксированный D51
attempt; создаёт новый report sibling без изменения source. История времен не исправлялась.

Команды из core (outputs уже существуют; при повторе использовать новые `report-*` имена):

```sh
.venv/bin/python -B tools/f01_d51_report.py work/collector-d51/report-live-01
PYTHONPATH=work/collector-d50/installed-wheel .venv/bin/python -B -S tools/f01_d51_report.py work/collector-d51/report-wheel-01
```

Отчёты byte-identical; wheel defa13258280021b7479b67ddc65148d1d7d915610d7314f71f1565a693a3d3e
из D50, source/runtime pin тот же.506tests passed/2optional skips, Ruff/format/mypy и
make smoke успешны. Source packages и configs не изменены; только offline analysis tool
и docs/evidence. Первоначальный lintB905 в новом report tool исправлен equal-length
zip slices, история сохранена в `report-lint-history.json`; live runtime это не затронуло.

### Budget, ограничения и решение

Owner attestation принята для bounded180RPC, paid budget0. Local reservation полностью
израсходована одной завершённой попыткой; frozen estimate180×20=3600CU. Это **nominal CU**,
не независимо измеренный provider charge. MCP before27660→after19320CU, обаUSD0, freshness
23:59→00:07UTC: повторный `account_usage_nonmonotonic`, причина не установлена. Нельзя
превращать такую дельту в billing reconciliation или доказательство отрицательного расхода.
Account-level usage и другие collectors могут иметь собственную активность; raw сохранены.

После прогона host AC Power29% charging, свободно около134,5GB. Переключение питания
наблюдалось, D51 не менял power settings. Awake/login/network84days не доказаны одним
пятиминутным прогоном. OS sleep/reboot/live failure injection не выполнялись из-за D43;
соответствующие guard/failure semantics остаются D50 fixture evidence. Новый service
не установлен (`launchctl` exit113), actual source root отсутствует. D43/holdout untouched.

**Решение:** принять qualification источника и frozen bounded runtime; D51 завершён.
Calendar activation пока **не разрешена и не выполнена**. Не нужны новый transport tuning,
смена протокола или повтор этого окна. Оставшиеся условия operational: отдельная calendar
allocation/review (ceiling60480RPC/1209600CU), стабильное питание/awake/login и monitoring,
конкретное итоговое решение о запуске. Owner attestation данного шага выделила3600CU
для одной попытки, не является calendar receipt. Staged D50 service остаётся без deployment.

Следующий **D52 — operational calendar activation decision** для unchanged D48 proposal.
Если решение разрешает запуск, сначала exact calendar receipt/review validity и operating
conditions, затем existing staged service; no backfill/сдвигT2026-10-07. D43/F01 rules и
reserved holdout сохраняются, readiness для research оценивается только по будущему corpus.
