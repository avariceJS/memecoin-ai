# D43: календарный corpus и наблюдаемое исполнение

2026-10-03. [План](../configs/calendar-execution-v1.json) фиксируется до сети.
Transport tuning завершён. Alchemy HTTP persistent TLS, finalized/profile2 для corpus,
WSS off, прежняя fixed cohort. Никаких QuickNode, D33, подписей/торговли/оплаты или ML.

Шесть окон:0/6/12/24/36/48 часов от immutable UTC anchor при создании расписания.
Lateness≤15min; пропущенное окно записывается missed, не переносится в удобное время.
Каждое окно:2×30×5s, максимум180 snapshots requests; отдельно до12 read-only execution
requests. Общий верхний бюджет1152 RPC, максимум2 failed windows, no hidden retries или
смены источника. Первый failure останавливает окно. Interrupted reservation не rerun.
Новый процесс на сегмент; deadline210s/сегмент и210s/execution audit. Сеть разрешена
только в due окне, общий OS lock исключает concurrent runners. Finite local launchd
job проверяет расписание раз в60s; после последнего окна/stop marker запросов нет.
Если Mac спит/выключен/недоступен, missed остаётся missing data. Не удерживать Mac бодрствующим.

Runtime tools и wheel копируются в локальный pinned каталог с проверкой hashes перед
каждым запуском; последующие правки repo не меняют активный эксперимент. Credentials
читаются в runtime из ignored `.env.local`, не входят в plist, args, reports или snapshot.
Raw вне Git, каждое окно и его QA immutable; общий registry — append-only outcomes.
Остановка: создать `STOP` в корне расписания; это не стирает имеющиеся данные.

Execution sample: три getSignaturesForAddress(confirmed, limit8) без pagination;
объединить signatures и выбрать до6 по slot descending/signature, без фильтра по err.
Три getSignatureStatuses в offsets0/5/15s (без history search), затем до6
getTransaction(finalized,json,maxSupportedTransactionVersion0). Всего≤12 RPC.
Null tx/status сохраняются как missing/censored; не повторять до success. Failed
транзакции не удалять. Signature/slot linkage, fee integer, chronology и raw hashes
обязательны. Meta.fee — observed total transaction fee в lamports, не полная стоимость
strategy roundtrip: tips, rent, pool/transfer fees и FX не включать неявно. Compute units
не являются задержкой исполнения; decomposition base/priority здесь не оценивается.

Первое observed ≥confirmed и первое observed finalized — границы наших наблюдений,
не точные chain transition times. Если с первого poll finalized, transition left-censored;
без finalized — right-censored; null не означает failure/drop. BlockTime — оценка chain
времени в секундах; receipt−blockTime не submission→inclusion latency. Чужие on-chain
транзакции представляют включённую выборку, не знаменатель dropped/unsubmitted действий.
Никаких calibrated inclusion/admission, собственных signatures, net PnL labels или
замены existing targets этим audit. Наличие адреса пула не доказывает swap: transactions
классифицируются только как address-related, без расширения protocol adapter.

Official references: [getTransaction](https://solana.com/docs/rpc/http/gettransaction),
[getSignatureStatuses](https://solana.com/docs/rpc/http/getsignaturestatuses),
[getSignaturesForAddress](https://solana.com/docs/rpc/http/getsignaturesforaddress),
[fees](https://solana.com/docs/core/fees). Seed null; source/config/plans/cutoffs/actual
receipt bounds и raw manifests входят в evidence. Настройка стратегии/ML не проводится.

Достаточность corpus: отдельно считать completed/missed/failed windows, реальные UTC
hours/days, usable coverage, full-grid/nonoverlap, отсутствующие regimes/universe и
execution unknowns. Шесть пятиминутных окон — пополнение engineering corpus, не заранее
объявленный достаточный ML dataset. План обучения остаётся закрыт до отдельного решения.

## Фактическое состояние 2026-10-03

[Evidence](evidence/calendar-execution-2026-10-03.json). План `6ec6bad`, код сбора
`ed88a9f`, inventory `29a14ed`. **Расписание активно: выполнено1 окно,5 pending.**
Первое окно: 07:54:16–07:59:14 UTC (10:54–10:59 Москва),180/180 snapshots,174 usable,
135 mature decisions (67 engineering evaluation),5 chronological nonoverlap windows.
1 440 conditional scenarios:988 roundtrip,22 entry_unfilled,20 exit_unfilled,410 censored.
Net PnL по-прежнему unknown; это не реальные сделки стратегии.

Execution audit:12/12 read-only RPC,6/6 выбранных транзакций получены, все on-chain
successful; никаких failed signatures из этой выборки не удалялось. Фактические fees:
15 001 /18 326 /45 300 /80 001 /80 001 /154 999 lamports. Это наблюдённые total transaction
fees, не диапазон стоимости нашей будущей заявки и не полные roundtrip expenses.
Compute units33 976–234 626; ни latency, ни инструкция swap этим не квалифицированы.

Все6 signatures уже finalized при первом status poll: **timing left-censored6/6**.
Observed listing→status receipt62–195ms — локальное время между чтениями, не скорость
finalization/inclusion. Receipt−blockTime60,69–80,26s — возраст полученных транзакций,
не задержка подачи. Обнаружение actual confirmed→finalized transition в первом окне
не произошло. Эта граница сохранена; timings не подставляются в execution simulator.

399 tests, lint/format/mypy, smoke/build прошли. Corpus, targets, quality/support,
fee/status evidence точно воспроизведены dev и frozen installed package. Старый D42
воспроизведён его прежним pinned wheel. Initial synthetic test fixtures ошибочно не
содержали JSON-RPC envelope; исправлены до сети, runtime guards не ослаблялись.
Новых сетевых failures нет. Runtime/source/captures/availability сохранены вне Git.

## Активное расписание и управление

Local LaunchAgent `ai.memecoin.d43.calendar01`, root `work/calendar-execution-01`.
Код и пакет: `runtime/tools`, `runtime/pkg`; hashes проверяются до сети. Агент работает
независимо от чата, без secrets в plist. Наличие Mac awake/logged-in и доступного endpoint
нужно для выполнения; пропуски не скрываются. После exhausted/failed budget новых RPC
нет; idle launchd job можно выгрузить командой ниже.

| Окно | План, Europe/Moscow | Статус на дату evidence |
|---|---|---|
| 0 | 2026-10-03 10:53:59 | completed |
| 1 | 2026-10-03 16:53:59 | pending |
| 2 | 2026-10-03 22:53:59 | pending |
| 3 | 2026-10-04 10:53:59 | pending |
| 4 | 2026-10-04 22:53:59 | pending |
| 5 | 2026-10-05 10:53:59 | pending |

Не запускать новый install/run для already reserved window. Чтобы прочитать свежий
inventory, не меняя active runtime или raw:

```sh
PYTHONPATH=work/calendar-execution-01/runtime/tools:work/calendar-execution-01/runtime/pkg \
  .venv/bin/python work/calendar-execution-01/reporting/calendar_inventory.py work/calendar-execution-01
launchctl print gui/501/ai.memecoin.d43.calendar01
# Остановка новых окон; текущий child ограничен своим deadline:
touch work/calendar-execution-01/STOP
# Выгрузить сервис (не удаляет данные):
launchctl bootout gui/501/ai.memecoin.d43.calendar01
```

Report snapshot и Git Memory не обновляются фоновым job автоматически: актуальные
outcomes/worker logs лежат локально. Следующая сессия должна сначала прочитать inventory,
не объявлять будущие окна проведёнными и не пересоздавать расписание.

## Решение и следующий этап

Продолжать эти frozen окна, а не снова менять транспорт. Первое окно добавило новый
час наблюдений относительно D42; разные дни ещё ожидаются. После оставшихся окон
свести фактические часы/дни, gaps и complete/nonoverlap support; отдельно оценить
нехватку рыночной вселенной и наблюдений execution. Текущая fixed cohort и небольшие
коррелированные окна недостаточны для решения о ML readiness. Для будущего timing
исследования нужна отдельная preregistered выборка, замеченная до finalization;
не объявлять сегодняшние left-censored samples её заменой. Торговля/подписи и ML
не запускались; transport tuning остаётся закрытым.
