# D40: bounded Alchemy HTTP / HTTP+WSS

План v1 заморожен до runtime запросов: [конфигурация](../configs/alchemy-pilot-v1.json).
Три прежних pool, finalized, profile2. Два параллельных режима Alchemy и прежний
public HTTP collector как контроль; максимум два 120-секундных сегмента, отдельные
процессы. Проба Alchemy HTTP: genesis + два обхода когорты (7 запросов). Первый
неожиданный отказ останавливает серию, без автоматического retry или fallback.

HTTP-only: 5 s. Hybrid: coalesced dirty-account trigger не чаще 5 s, контрольный
snapshot каждые 15 s. Подписки на pool/config/vaults/mints, root для liveness.
Новый Clock читается только вместе со всеми dependencies через HTTP. При reconnect
новая connection generation, acknowledgements всех subscriptions, затем HTTP anchors.
До anchor уведомления сохраняются; slot выше anchor остаётся dirty. Это handoff
для snapshot-trigger режима, НЕ доказательство lossless transaction stream.
Нет write sequence/cursor: silent loss нельзя полностью обнаружить, heartbeat только
ограничивает stale state. Нельзя объявлять перескок slot пропущенным account update.
Disconnect/overflow/restart оставляют исторический gap; recovery даёт лишь новые
observations с фактической поздней availability и новым warmup.

Бюджеты и критерий выигрыша зафиксированы в JSON. Никакого расширения выборки по
результату. Поддержка snapshot/quote profile2 не расширяет creator collection replay.
Общий CausalBook, decoder и targets используются без смены формул. Нет обучения,
торговли, подписи, оплаты, upgrade, QuickNode или возврата к D33.

Официальные документы, проверены 2026-10-03:

- [accountSubscribe](https://www.alchemy.com/docs/reference/account-subscribe): один
  account, base64, explicit finalized; numeric subscription id, без replay cursor.
- [Solana PubSub](https://www.alchemy.com/docs/reference/solana-subscription-api-endpoints):
  streaming endpoint, account/program/log/signature/root/slot/block методы; bandwidth billing.
- [Общие лимиты](https://www.alchemy.com/docs/reference/subscription-api): заявлены
  100 connections FREE / 2000 прочие, 1000 subscriptions/connection, 200 concurrent
  requests, batch1000. Это документационные ceilings, не проверенная квота данного app.
- [getMultipleAccounts](https://www.alchemy.com/docs/chains/solana/solana-api-endpoints/get-multiple-accounts)
  и [Solana](https://solana.com/docs/rpc/http/getmultipleaccounts): один context,
  до100 accounts; minContextSlot — lower bound, не запрос historical bank.

MCP list_apps/select_app/getGenesisHash подтвердили доступ к единственному существующему
app и mainnet. Runtime credentials отдельно найдены в ignored `.env.local`; они не
выводятся/копируются в evidence. Старое Alchemy raw evidence в work не найдено.

## Результат 2026-10-03: v1 не допущен к замене collector

[Итоговое evidence](evidence/alchemy-pilot-2026-10-03.json). Код сбора `6368caf`,
план `b3e7d89`; source/plan/raw manifests сохранены. Фактические timestamps — UTC ns,
дата этапа — Europe/Moscow. Seeds null: инженерная проверка данных, не ML-эксперимент.
Новых Alchemy RPC 223: 7 preflight +216 snapshots пилота; отдельно MCP genesis.
Public контроль144 snapshots. Все 366 strict account snapshots (6+216+144) приняты.
Оба процесса завершились, по 120 s; между ними coverage не объявляется непрерывным.

| За два сегмента | Alchemy HTTP-only | Alchemy HTTP+WSS | Прежний public HTTP |
|---|---:|---:|---:|
| HTTP snapshots | 144 | 72 | 144 |
| Feature rows / usable | 144 / 138 | 150 / 120 | 144 / 138 |
| Usable fraction, все frames | 95,83% | 80,00% | 95,83% |
| Usable fraction, только scheduled frames | 95,83% | 83,33% | 95,83% |
| Censored observed-price targets | 35 | 95 | 35 |
| Полученные HTTP payload bytes | 524304 | 262152 | 524304 |
| WSS application payload bytes | — | 258666 | — |

Hybrid получил 1158 WSS messages: 68 subscribe acknowledgements, 253 account hints,
837 roots. На соединение 17 subscriptions: 16 уникальных dependencies и root.
Проверены четыре соединения последовательно, максимум одно одновременно. Пересечения
коhort и shared dependencies учтены. Это фактический доступ, не измеренный предел квот.
Остальные документированные методы не запускались и не объявляются квалифицированными.

Первый preflight script после HTTP200/успешного strict decode попытался прочитать
отсутствующее report-поле `clock_slot`: локальный `KeyError`. Оригинал сохранён,
исправление выполнено offline, затем только оставшиеся 5 запросов исходного бюджета 7.
Provider mismatch/сетевая ошибка не обнаружены. В двух основных сегментах 0 неожиданных
failures. Безопасная история отказа и correction — в evidence. Stop budget не расширен.

## Что доказано и где остаётся неопределённость

- В каждом generation все17 acknowledgements предшествовали HTTP anchors.
  Буферизованные raw сохранялись до обработки; уведомления выше snapshot slot
  оставались dirty. Никакие WSS values не попали в pool state/CausalBook.
- Два forced disconnect на 60 s, resubscribe и anchors всей когорты за1,219 /1,468 s
  от gap marker. Каждый оставил masked frame и новый warmup; history не восстановлена.
  Второй120 s segment выполнен новым процессом с новым TLS/WSS, новым CausalBook и
  собственным заранее закреплённым midpoint cutoff. Через restart targets не строятся.
- Все 1158 исходных WSS receipts сверены с bytes/hashes и фактическими временами
  обработанных событий. Raw HTTP captures и сохранённые feature/target outputs
  воспроизведены без сети development package и установленным wheel.
- Synthetic adversarial tests отдельно проверяют duplicate, late, same-slot разные
  значения, out-of-order, сообщения выше anchor, неверный generation, silent omitted
  hint с изменившимся HTTP state, overflow, частичные/неправильные frames и handshake.
  Переполнение сохраняет полученный raw до stop. Прерванный незапечатанный segment
  резервирует бюджет и блокирует следующий запуск; его bytes не переписываются.
  Kill/restart посреди mainnet segment и реальная перегрузка провайдера не проводились.
- Disconnect/timeout/overflow обнаруживаются; периодический HTTP может обнаружить
  состояние, изменившееся без hint. Это conservative uncertainty mask, не доказанная
  причина потери: notification мог просто опоздать. Изменение-и-возврат между anchors
  и полностью silent omission с равными endpoints **необнаружимы** этим API.
  Поэтому полнота промежуточной истории всегда false. HTTP не восстанавливает прошлую
  online availability и не закрывает такой gap. D33 и collection replay не открывались.

Raw сообщения — application text после RFC6455 reassembly, без изменения bytes JSON.
Ping/pong обрабатываются транспортом; payload manifests не являются pcap всей TLS-сессии.
`wire_*` в low-level report — frame/upgrade bytes, без TCP/TLS overhead; их нельзя выдавать
за оплаченный сетевой трафик. Source/manifest hashes фиксируются отдельно от raw outputs.

## Практический выигрыш и причина отказа

Предварительный критерий: ≥20% меньше HTTP при падении usable fraction ≤5 процентных
пунктов либо ≥20% больше distinct economic changes при matched HTTP budget, с integrity
gates. HTTP вызовов на 50% меньше, но падение пригодности15,83 п.п. (12,5 п.п. на scheduled
frames). Покрытие новых экономических изменений не выросло:7/1 наблюдённых transitions
у HTTP против6/1 у hybrid по двум сегментам. Это не число всех swaps и не независимые samples.
Вторая ветка критерия не доказана: request budgets фактически не matched, нет роста coverage.

Общий полученный payload hybrid520818 против HTTP524304: экономия всего 0,66%; первый
сегмент даже увеличил payload примерно на7%. Цена и квота аккаунта не измерялись.
[Таблица Alchemy CU](https://www.alchemy.com/docs/reference/compute-unit-costs) указывает
20CU/getMultipleAccounts и специальный Solana WSS тариф0,0002CU/byte; общий раздел той же
страницы содержит другую ставку для прочих subscriptions. Это документационные ставки,
не bill данного app; итог не объявляется измеренной денежной экономией. Платных действий нет.

Диагноз v1: общий config дал 108 и 62 notifications при одном и том же value внутри
каждого сегмента. Разные slot при одинаковом value сейчас остаются hints. Все72 hybrid
snapshots инициированы dirty state; это не свидетельство полезности каждого сообщения.
Проверка `min_interval` на общей5 s сетке после завершения HTTP приводит примерно к10 s
интервалу между hybrid snapshots. Таким образом, экономия числа calls включает эффект
throttling/cadence; **её нельзя целиком приписать WSS**. Forced fault введён только в hybrid,
поэтому таблица характеризует весь заранее заданный v1 режим вместе со стрессом.
Новый control HTTP-only10 s и отдельное steady/stress сравнение нужны до причинного вывода.
Никакие thresholds или результаты задним числом не переопределялись.

HTTP transport p50:58,5/64,5 ms baseline и58,1/64,0 ms hybrid по сегментам. Отдельное поле
`capture_and_decode_seconds` включает disk/decoder; это не чистый RPC response time.
Возраст chain Clock и receipt приведён в evidence отдельно. Account/root notification
не содержит source wall timestamp, поэтому WSS chain-to-receipt latency неизвестна.
CPU2,26/2,22 s и peak RSS39,6/41,9 MB измерены на Apple M4 Pro для всего paired процесса,
включая public thread; per-arm CPU superiority не измерена. Это не trade execution latency.

## Решение и следующий этап

**D40-v1: не принимать hybrid как основной pipeline.** Alchemy HTTP пригоден для
проверенной bounded области strict finalized checkpoints; WSS технически работает как
дополнительный канал, но v1 не выполнил качество/coverage критерий. D39 collector остаётся
основным. Никаких gates protocol/execution/ML/edge не повышено, creator profile2 остаётся
snapshot/quote-only. Targets здесь — existing observed-price diagnostic schema2, не net PnL.

Следующий шаг — отдельный D40-v2: сначала исправить scheduler и проверить offline
content-aware coalescing, затем заморозить честный matched-cadence HTTP-only контроль,
раздельные steady/stress окна и прежние stop budgets. Новый сбор по этому плану пока
**не проведён**. Длинное coverage/execution QA всё ещё нужно до любого обучения.

## Воспроизведение

Артефакты вне Git: `work/alchemy-pilot-01`, preflight/correction и source archive,
точные пути/хеши — evidence. Нужны исходные raw; Git хранит код/план/компактный отчёт.

```sh
# Только offline; не читает credentials и не отправляет запросов:
.venv/bin/python tools/alchemy_pilot.py verify work/alchemy-pilot-01 --segment 0
.venv/bin/python tools/alchemy_pilot.py verify work/alchemy-pilot-01 --segment 1
.venv/bin/python tools/alchemy_pilot_report.py work/alchemy-pilot-01
# Пinned installed wheel также проверен:
work/alchemy-wheel-01/bin/python tools/alchemy_pilot.py verify work/alchemy-pilot-01 --segment 0
```

Для будущего явно замороженного запуска runner принимает `run ROOT --segment 0/1`;
существующий `ROOT` не перезаписывается, failed/unsealed предшественник блокирует продолжение.
Runtime stdlib, verified TLS, нет redirects/compression/hidden retry. Два процесса здесь
— нормальные restart между завершёнными segments, не production availability гарантия.
