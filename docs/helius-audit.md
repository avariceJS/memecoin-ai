# Helius: проверка доступа, 2026-10-02

**Текущий результат:** direct RPC доступен после локальной настройки endpoint.
Enumeration совпал **12/12**. Первый full block получен; identity и порядок совпали,
но у двух из 1 310 транзакций Helius обрезал логи. Полностью совпало **1 308 tx**;
полных блоков — **0 из 1 проверенного**, ещё 11 не запрашивались. Смена на getTransaction
не восстановила trace. Полное agreement диапазона остаётся false. Подробности нового
прогона — в конце документа; ниже сохранена история первоначального preflight.

## Возможности и документация

Изучены exposed schemas 10 Helius MCP tools. `heliusKnowledge.lookupHeliusDocs`
успешно вернул документацию RPC; ответ самого инструмента имеет `[truncated]`.
Параметры дополнительно сверены с официальными страницами
[getBlocks](https://www.helius.dev/docs/api-reference/rpc/http/getblocks) и
[getBlock](https://www.helius.dev/docs/api-reference/rpc/http/getblock).

- Direct RPC использует HTTPS POST JSON-RPC и API key. Подключение MCP не передаёт
  endpoint или ключ Python-приложению автоматически.
- В exposed `heliusChain` есть `getBlock`, но нет действия `getBlocks` или произвольного
  RPC dispatcher. Обход schema через произвольные `args` не пробовали.
- Обработанные события/переводы и wallet history не заменяют full getBlock.
  MCP summary/expand также не считаются исходным HTTP JSON-RPC capture.
- Документация getBlocks допускает end slot на расстоянии до 500 000 от start.
  Для нашего диапазона выбран один запрос на 12 слотов. Реальный допуск текущего
  доступа ещё не проверен; лимит QuickNode в пять слотов к Helius не перенесён.
- Для getBlock документированы `jsonParsed`, `transactionDetails=full`, finalized и
  `maxSupportedTransactionVersion=1`. Сохраняем точные параметры reference, включая
  `rewards=false`; поддержку на нашем endpoint ещё предстоит подтвердить.

## Первый MCP preflight: безопасный лог отказа

```json
{
  "tool": "heliusChain.getNetworkStatus",
  "params": {"commitment": "finalized", "detail": "full"},
  "internal_method": "getSupply",
  "error": "Request timed out after 30000ms (getSupply)",
  "category": "timeout",
  "http_code": null,
  "rpc_code": null,
  "cause": "unknown"
}
```

Это единственный chain-вызов MCP; после ошибки серия остановлена. Полное wall time
инструмента — 288.369 s. Устройство внутренних ожиданий неизвестно; 30 s в сообщении
относится к getSupply. Эти числа не измеряют HTTP response time нашего ReadOnlyRPC,
возраст chain state, historical throughput или trade execution latency.
Mainnet/finalized slot этим ответом не подтверждены.

На первом этапе в окружении процесса и `.env`/`.env.local` Helius endpoint/key не был найден.
Имеющаяся `MEMECOIN_SECONDARY_RPC_URL` не относится к Helius и не использовалась.
Новые запросы к QuickNode запрещены текущим поручением и не выполнялись.
Для продолжения требовалось локально настроить **`MEMECOIN_HELIUS_RPC_URL`**; пользователь
сделал это перед вторым этапом ниже. Секрет не передавать в чат/Git. CLI не загружает
dotenv автоматически: значение должно попасть в окружение процесса через локальную
настройку. Регистрация, новые ключи, upgrade и оплата не выполнялись.

## Сохранённые доказательства

`work/helius-stage/` содержит план двух MCP-вызовов, неизменённые tool results с временем
начала/окончания, описания инструментов, manifest/checksums, snapshot кода, inventory
reference и отложенный direct RPC plan. Это отдельный формат evidence; RPC captures
из MCP-ответов или derived inventory не создавались.
Компактный отчёт: [helius-preflight-2026-10-02.json](evidence/helius-preflight-2026-10-02.json).

Проверены 13 reference captures: enumeration и 12 блоков 452408962–452408973,
14 809 транзакций; суммарно 112 767 706 bytes JSON responses. Checksums, обязательные
metadata и внутренняя ancestry согласованы. Это offline проверка первого источника.
Frozen journal существует, 115 683 328 bytes, SQLite `integrity_check=ok` при read-only
открытии. Три прежних QuickNode bundles повторно прошли offline verification;
три ранее успешных full blocks совпали и по новому строгому сравнению. Повторных
загрузок не было. Старые captures, отчёты и история отказов сохранены.

## Изменение общего сравнения

Новые `crosscheck-captures` планы фиксируют `comparison_schema=2`. Для getBlock и
getTransaction сравнивается весь `result`, включая неизвестные поля, blockHeight,
rewards, порядок транзакций и все вложенные metadata. JSON key order несущественен;
поля не удаляются и числа не округляются ради совпадения.

Для full blocks отсутствие meta, fee/err, balance arrays, innerInstructions,
logMessages, версии или transaction message/signatures даёт `unavailable`, даже если
оба ответа одинаково неполны. Null trace arrays тоже означают недостаточность образца
для полного аудита, а не доказанную ошибку провайдера. Optional metadata сравниваются
целиком; отсутствие loadedAddresses при parsed encoding не заполняется выдуманным значением.

Schema 1 остаётся для воспроизведения старых bundles без нового маркера. Новые планы
записывают timeout, byte cap и spacing до сети; новая серия останавливается также на
mismatch/unavailable/incomparable. Read-only allowlist расширена `getGenesisHash`
для проверки сети. CPMM/collection/replay не менялись.

## План перед direct RPC запуском (история)

`work/helius-stage/direct-rpc-plan.json`: максимум 16 запросов — getGenesisHash,
getVersion, finalized getSlot, один getBlocks и 12 exact full getBlock. Timeout 30 s,
cap 32 MB/ответ, spacing 3 s, без retries; ошибка/несовпадение останавливает план.
При записи первого отчёта запуск не был проведён; результат его выполнения — ниже.
Исторический интервал фиксирован; seed и train/test
split неприменимы; execution model отсутствует. Сначала подтвердить mainnet genesis
и finalized height; затем использовать существующий CLI с новыми output paths:

```sh
mcai crosscheck-range work/secondary-stage/range/000-reference \
  --endpoint-env MEMECOIN_HELIUS_RPC_URL --source-alias helius-secondary \
  --max-slots 12 --timeout 30 --out work/helius-range-01/enumeration
# Только если enumeration полностью совпал:
mcai crosscheck-captures work/secondary-stage/range/{001..012}-reference \
  --endpoint-env MEMECOIN_HELIUS_RPC_URL --source-alias helius-secondary \
  --timeout 30 --out work/helius-range-01/blocks
mcai audit-provider-range work/helius-range-01/enumeration \
  work/helius-range-01/blocks --out work/helius-range-01/report.json
```

При частичном успехе новый план должен ссылаться на уже сохранённые ответы и запрашивать
только недостающие данные; не повторять всю команду вслепую. Сжатие не проверялось:
нет ответа Helius для измерения. Если понадобится gzip, потребуется сохранение wire
bytes и ограничение сжатого и распакованного объёма.

После полного bounded agreement разумен небольшой синхронный сбор у двух операторов.
Один диапазон не квалифицирует production feed, PIT universe, execution или edge.

## Проверки первого этапа

239 tests, Ruff lint/format, strict mypy, CLI smoke и сборка sdist/wheel прошли.
Сборка сначала остановилась на sandbox DNS при загрузке pinned setuptools;
повтор через штатное сетевое разрешение завершился успешно.

## Direct RPC: результат после настройки endpoint

2026-10-02 пользователь настроил `.env.local:MEMECOIN_HELIUS_RPC_URL` и разрешил
продолжение. Файл прочитан как данные без shell execution; значение не выводилось.
Первый sandbox запрос `getGenesisHash([])` получил `dns`, HTTP/RPC code=null.
Попытка сохранена отдельно в `work/helius-range-01-sandbox`. Штатное разрешение сети
позволило выполнить новый immutable план `work/helius-range-01`.

Подтверждены mainnet genesis `5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d`, версия
RPC 4.3.0 и finalized slot 452643948. Первые 32 символа genesis сверены с официальным
[mainnet CAIP-2 ID](https://solana.com/docs/payments/agentic-payments/x402).
`getBlocks(452408962,452408973,{commitment:finalized})` одним запросом вернул все
12 reference slots. План остановился на первом full block из-за metadata mismatch.

Безопасный запрос: `getBlock(452408962, {commitment:finalized, encoding:jsonParsed,
maxSupportedTransactionVersion:1, rewards:false, transactionDetails:full})`.
HTTP 200, RPC error отсутствует. Blockhash, previousBlockhash, parentSlot, blockTime,
blockHeight, остальные top-level fields и порядок всех 1 310 транзакций совпали.
Единственные отличия внутри блока — две logMessages arrays:

| Tx index (с нуля) | Результат tx | Строк у первого RPC / Helius | UTF-8 bytes логов у первого / Helius |
|---|---|---|---|
| 433 | successful | 139 / 129 | 10 765 / 9 989 |
| 743 | failed | 128 / 127 | 10 173 / 10 008 |

Helius содержит точный marker `Log truncated`; у первого RPC его в этих traces нет.
Все прочие transaction/meta fields совпали, включая инструкции, balances и fees.
Это сохраняется как mismatch. Ни удаление logMessages, ни заполнение из первого RPC
не использовались для объявления независимого agreement. Ancestry всего диапазона
по Helius не проверена: 11 остальных блоков не загружались.

Отдельный план **одного** запроса `work/helius-trace-probe-01` проверил tx 433 через
[getTransaction](https://www.helius.dev/docs/api-reference/rpc/http/gettransaction)
с finalized/jsonParsed/maxSupportedTransactionVersion=1. Signature:
`x5QtkLfgCbcCNusnXAU8BcXUXVKPKWi8mLBgpRdF53ru4bCqX7t3LxRXSv6fkPBVXAaiXHCBymFUquap77H79nB`.
Ответ HTTP 200 подтвердил slot, transaction и version, но logs совпали с обрезанными
Helius block logs. Meta также имеет `rewards=[]` вместо block `null`; различие сохранено
в отчёте без нормализации. Это cross-method диагностика, не фиктивный getTransaction
capture из reference block. Raw getTransaction сохранён как regression fixture в Git.

**Причина/настройка провайдерского ограничения не установлена.** Близость размеров
к 10 KB не доказывает конкретный тариф или доступный параметр увеличения лимита.
После целевой проверки сеть остановлена; иных retries, QuickNode запросов и upgrade нет.

## Измерения direct RPC

| Метод | Monotonic response time | Payload bytes |
|---|---:|---:|
| getGenesisHash | 0.360 s | 80 |
| getVersion | 0.259 s | 83 |
| getSlot(finalized) | 0.233 s | 43 |
| getBlocks(12 slots) | 0.311 s | 155 |
| getBlock(full) | 5.373 s | 10 011 629 |
| getTransaction | 4.235 s | 44 371 |

Итого 6 успешных HTTP запросов, 10 056 361 payload bytes; отдельно один sandbox DNS
отказ. Timing включает TLS/RPC/download, объёмы относятся к response body без HTTP/TLS
overhead. Выборка не является sustained backfill benchmark. Chain-state age здесь
не измерялся; historical receipts не восстанавливают PIT availability. Execution
latency неизвестна. Сжатие не проверялось: обнаруженный блокер — полнота логов.

## Исправление проверки и следующий шаг

Comparison schema **3** обнаруживает точный runtime marker. Одинаково обрезанные
ответы дают unavailable; различающиеся по-прежнему mismatch с indices и JSON-pointer
paths. Подсчёт всех differing sites сопровождается ограниченным списком samples;
диагностика не меняет сравниваемые payloads. Отсутствие marker не доказывает отсутствие
любой возможной обрезки. Schema 1/2 сохранены для offline воспроизведения архивов.

```sh
mcai compare-rpc-captures work/helius-range-01/blocks/000-reference \
  work/helius-range-01/blocks/000-secondary --out work/new-block-diagnostic.json
# Exit 1 при mismatch/unavailable; output создаётся исключительно новым файлом.
```

Следующий gate — подтвердить способ получать у Helius полные исторические traces
на приведённой signature либо явно признать этот доступ непригодным для полной
trace-сверки. До этого не повторять исторические команды выше, не догружать 11 блоков
ради полного range agreement и не начинать синхронный pilot. Полученные ответы
переиспользуются; снятие log-проверки или использование primary tail это gate не закрывает.

Raw plans/captures/measurements, исходники и hashes сохранены; новое offline заключение
отдельно в `work/helius-diagnosis-01`. [Компактный отчёт](evidence/helius-direct-2026-10-02.json).
244 tests, Ruff lint/format, strict mypy, smoke и sdist/wheel build прошли. Schema 3
диагностика воспроизведена из wheel; архивы schema 1/2 проверены. Рыночная data/execution
qualification не повышена; нейросеть/RL/trading не начинались.
