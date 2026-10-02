# Согласованный prospective источник и transport (D39)

Выбран для следующего bounded corpus официальный public mainnet endpoint
`https://api.mainnet.solana.com` с `stdlib_persistent_v1`: одно проверенное TLS-соединение,
последовательные getMultipleAccounts, explicit application User-Agent. Выбор относится
к измеренной ограниченной задаче; production SLA и универсальная надёжность не доказаны.
[Solana clusters](https://solana.com/docs/references/clusters) указывает этот адрес и
предупреждает об изменяемых public limits. В локальном DNS он и прежний
api.mainnet-beta.solana.com разрешились в один IP: это не второй независимый provider.

## Что установила диагностика

- На одинаковом JSON одного пула urllib timeout15.072s, curl два success0.369/0.388s.
- Контроль curl HTTP/1.1 timeout10.020s: TLS handshake не завершился, HTTP не начался.
  Python с explicit app UA и curl UA получил success, но это не подтвердило UA как fix.
- App-UA preflight всей когорты: три valid responses, затем timeout10.003s. Серия остановлена.
- Instrumented HTTPSConnection к старому host timeout10.006s на connect, до отправки JSON.
- Canonical mainnet host + reusable connection: genesis mainnet подтверждён, затем
  12/12 strict coherent snapshots всей когорты через то же соединение, без ошибок.

Наблюдаемая проблема включает установление TLS, а не только обработку RPC метода.
Не установлены причины всех прежних timeout, различие host/SNI и свойства backend.
UA изменение само по себе не устранило проблему. Связь keepalive и результата требует
длительного наблюдения; preflight не превращается задним числом в causal features.
Helius MCP troubleshooting не объяснил Clock mismatch. Его прежние responses остаются
rejected; [Helius getMultipleAccounts](https://www.helius.dev/docs/api-reference/rpc/http/getmultipleaccounts)
не документирует разрешение подменять context.slot данными Clock.

Неизменяемые планы, реальные requests/raw receipts и измерения:
`work/rpc-transport-probe-01`, `work/rpc-header-probe-01`, `work/public-app-preflight-01`,
`work/public-keepalive-probe-01`, `work/public-canonical-keepalive-probe-01`.
Все четыре diagnostic timeout сохранены с методом/полными публичными params,
HTTP/RPC code null. Повтора внутри варианта после ошибки нет. Статус Helius не удалось
прочитать web tool; это не свидетельство наличия или отсутствия incident.

## Контракт transport

Runtime остаётся Python stdlib. HTTPS certificate/hostname verification включена;
transport связан с одним полным endpoint, секреты не выводятся. HTTP redirects не
исполняются, hidden retry отсутствует. Каждый вызов — отдельный JSON-RPC request и
отдельный Capture; connection reuse не означает повторное использование данных.
Ошибочное/неполное соединение закрывается. Сервер может закрыть idle connection;
следующая invocation снова может столкнуться с TLS timeout, это не скрывается.

Response size ограничен до выделения большого тела и при чтении; unexpected compression
отклоняется. Body deadline общий, не сбрасывается на каждый chunk. DNS/connect остаётся
в пределах возможностей blocking stdlib socket timeout, не объявляется hard real-time
гарантией. Phase timings/reuse сохраняются в attempt рядом с raw provenance; это
read-side measurements, не скорость trade execution. Default urllib доступен отдельно.

Новый campaign plan закрепляет transport_profile вместе с endpoint_origin, source,
cohort и бюджетом. Resume отвергает другую комбинацию до сети. Старые plans подразумевают
urllib; старые artifacts воспроизводятся своими pinned wheels. Account decoder,
Clock equality, freshness/masks, per-segment warmup, cutoff и censoring не изменены.

```sh
mcai create-prospective-campaign --checkpoint CAPTURE --source-alias SOURCE \
  --endpoint-origin https://api.mainnet.solana.com \
  --transport-profile stdlib_persistent_v1 --out CAMPAIGN
mcai resume-prospective-campaign CAMPAIGN --endpoint-env VARIABLE
```

## Следующий offline этап: сколько данных действительно есть

`audit-target-support CAMPAIGN TARGETS --out REPORT` и `verify-target-support` используют
только verified raw→features→targets и independent temporal/accounting quality checks.
Для complete decision нужны все восемь planned scenarios, зрелые в одной partition;
partial/censored/mixed/purged решения отдельно учитываются. Полная scenario grid проверяется.
Затем chronological thinning считает неперекрывающиеся окна до максимального observed
target_available среди вариантов — по каждому pool и совместно. Это QA покрытия, не
оценка статистически независимого effective sample size. Signs/values PnL не участвуют
в выборе; будущие метки не становятся online features или фильтром торговых решений.
Flat observed price не доказывает отсутствия intervening trades. Training gate остаётся false.

## Реальное продолжение и ограничение длинной сессии

`work/prospective-persistent-01`: immutable4×30 rounds×5s, прежние три pools,
max360 requests. Три сегмента 90/90 завершены; четвёртый 32 valid, затем connection error
при ожидании headers на request303 (3.856s, HTTP/RPC code отсутствуют). Сегмент остановлен,
кампания закрыта по исчерпанному бюджету. Это не полная успешная кампания:302 accepted,
303 feature rows,290 usable,2424 conditional scenarios. Скрытого reconnect/retry нет.

Сбор впервые дошёл до engineering evaluation на первоначальном frozen cutoff:
211 distinct mature decisions, из них136 calibration и75 evaluation. Все восемь
сценариев зрелые в одной partition у185 решений (122/63). Совместный chronological
nonoverlap оставляет6 calibration и4 evaluation окна; это не независимый sample size.
Причинный split не превращает короткий engineering sample в final holdout.

Вторая кампания `work/prospective-persistent-02` проверяет реальный restart между
сегментами (2×30×5s, max180 requests): одна invocation/процесс/новое TLS-соединение на
каждый сегмент. Это предпочтительная эксплуатационная форма следующего bounded сбора:
connection reuse внутри сегмента, закрытие на его границе; не бесконечно живая сессия.
Причина оборванного соединения первой кампании неизвестна, server lifetime limit не
объявляется установленным. Финальные результаты второй кампании — в evidence D39.


Финальный результат restart campaign 02: 180/180 strict snapshots, 2 сегмента и 2 новых
TLS-соединения, 0 ошибок. Итого по двум кампаниям 482 accepted  / 483 requests,
464 usable features, 345 distinct mature decisions, 141 evaluation.
Полный отчёт:[evidence](evidence/persistent-source-2026-10-02.json). 346 tests, smoke/build,
offline dev/wheel reproduction прошли. Совместный итог 15 chronological nonoverlap
окон не даёт основания для ML; следующий этап — более длинное календарное покрытие
и uncertainty исполнения. Source production readiness/edge не объявлены.
