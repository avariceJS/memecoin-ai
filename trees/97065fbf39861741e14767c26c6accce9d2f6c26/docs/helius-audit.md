# Helius: проверка доступа, 2026-10-02

**Этап закрыт по поручению пользователя 2026-10-02 (D33).** Достигнутой сверки
достаточно для исходной цели второго RPC. Дозагрузка остальных 10 блоков и поиск
идеальных traces не являются дальнейшим планом. Ниже — принятые ограничения;
исторические предложения следующих шагов в конце документа отменены D33.

**Технический результат:** проверенный Helius доступ непригоден для **полной исторической
trace-сверки** двух проблемных транзакций. `getTransaction` (jsonParsed/json) и
`getTransactionsForAddress(full)` возвращают одинаковую обрезку. Это ограничение
проверенных методов и доступа, не утверждение обо всех возможных продуктах Helius.

Enumeration совпал **12/12**. Проверены два full blocks: **1 match / 1 mismatch**,
целиком совпали **2 462/2 464 tx**, ancestry соседних блоков совпала; 10 блоков не
загружены. Full-range agreement **false**. Дополнительная синхронная пара счетов не
состоялась из-за timeout Solana public RPC. Текущий результат и следующий шаг — в
последнем разделе; предыдущие разделы сохраняют историю этапов.

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

## Исправление проверки и следующий шаг на предыдущем этапе

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

## Проверка historical traces и ограниченный следующий шаг

2026-10-02 выполнены отдельные immutable планы, без retries и повторной загрузки
первого full block. Для обеих signatures из indices 433/743:

| Новый метод | Index | Logs | Response time | Bytes |
|---|---:|---:|---:|---:|
| getTransaction, json | 433 | 129, truncated | 0.537 s | 26 949 |
| getTransaction, json | 743 | 127, truncated | 0.398 s | 29 824 |
| getTransactionsForAddress, full/jsonParsed | 433 | 129, truncated | 2.121 s | 44 405 |
| getTransactionsForAddress, full/jsonParsed | 743 | 127, truncated | 0.391 s | 48 587 |

Все HTTP 200, RPC errors нет. Logs во всех четырёх случаях равны Helius getBlock,
не primary. Indexed method запрошен с limit=1, точными slot и signature bounds;
identity проверена. Явный research adapter сохраняет настоящий JSON-RPC envelope
и `result.data`; getTransaction/getBlock captures из него не создаются. Общий
runtime allowlist не расширен ради разового provider-specific опыта.

В документированных параметрах [getTransaction](https://www.helius.dev/docs/api-reference/rpc/http/gettransaction)
нет настройки log limit. [Indexed full history](https://www.helius.dev/docs/rpc/gettransactionsforaddress)
возвращает metadata, но реальный запрос не восстановил отсутствующий tail.
[LaserStream untruncated](https://www.helius.dev/docs/laserstream/log-truncation)
поддерживает полные логи, однако **historical replay для этих endpoints не поддержан**.
Обычный [replay](https://www.helius.dev/docs/laserstream/historical-replay) ограничен
примерно 48 часами; mainnet требует Business/Professional. Тариф не выяснялся,
активация/upgrade не выполнялись. Это не доказательство, что upgrade восстановит
наши traces. MCP troubleshooting не нашёл специальной записи для Log truncated.

Копирование дополнительной `.md` страницы локально завершилось TLS handshake timeout
(20 s, HTTP/RPC code неизвестны). Серия копирования остановлена; HTML был успешно
прочитан web tool. Reading notes помечены derived, не выданы за raw HTTP response.
Сохранённые docs/receipts и MCP result — в `work/helius-trace-research-01`.

**Выбранная альтернатива:** независимое сравнение точных данных выбранного cohort,
с отдельной диагностикой причин full-block mismatch. Cohort не подбирался под
результат: шесть счетов получены `scan_addresses` из frozen journal checkpoint 1
(slot 452408961), включая config и mints. Diagnostic `work/helius-scope-01` сохранил
все 1 310 tx первого блока: 86 readonly references (85 exact), 1 224 без ссылок
(1 223 exact), **0 потенциальных writers**. Tx 433 использует только readonly shared
config, tx 743 не ссылается на cohort. [CPI privileges](https://solana.com/docs/core/cpi)
не допускают writable escalation; вывод о невозможности записи относится только
к этим шести счетам и корректности resolved keys. Нулевое число writers не является
положительной проверкой переходов пула. Full mismatch/replay guards сохранены.

Поэтому до нового запроса выбран первый блок primary-диапазона с writable reference,
**452408963**, index 1068. Он получен один раз: HTTP 200, **8 164 830 bytes / 15.108 s**.
Все **1 154 tx**, весь block result и metadata совпали по schema 3; marker отсутствует.
Ссылка на parent 452408962 / previousBlockhash совпала. Классы: 1 writer, 70 readonly,
1 083 без ссылок; все exact. Это реальная независимая сверка одного активного блока,
не доказательство инфраструктурной независимости или полноты всех execution traces.
План и raw: `work/helius-active-block-01`; данные первых запусков не изменены.

Отдельная current-state проверка `work/helius-state-pairs-01`: максимум три concurrent
пары (6 calls), те же pool/config/vaults/mints плюс Clock, finalized/base64,
minContextSlot=452408973 (только нижняя граница). Первая пара: Helius HTTP 200,
slot **452651827**, **3 707 bytes / 0.318 s**; public RPC — transport timeout **20.069 s**,
HTTP/RPC code неизвестны. Дальнейшая серия остановлена, вторую/третью пары не запускали.
Совпадение bank slot и account bytes **не проверено**. Этот snapshot не представляет
старый диапазон. Глубокая причина public timeout неизвестна.

На этом этапе: 6 успешных Helius requests / 8 318 302 payload bytes, отдельно один
public timeout. Не смешивать эти response times с chain-state age, sustained backfill
throughput или execution latency. Полных архивных traces восстановлено **0/2**.
Общий исторический итог: 12/12 enumeration, 1/2 full blocks matched, 2 462/2 464 tx
matched, ещё 10 blocks не получены; full-range agreement false.

Добавлена только общая offline account-scope диагностика с fail-closed проверкой
resolved keys; CPMM/collection/replay и строгий comparator не ослаблены. 252 tests,
lint/format/strict typing, smoke и build прошли. Manifests проверены, оба новых offline
сравнения воспроизведены. Evidence: [helius-traces-2026-10-02.json](evidence/helius-traces-2026-10-02.json).

Следующий этап: зафиксировать полный набор потенциальных записей cohort на оставшихся
10 блоках и провести ограниченную независимую сверку его истории, сохраняя каждый
full-block mismatch и trace gate. Не выбирать только успешные/короткие traces.
До нового синхронного state pilot отдельно проверить доступность public RPC. Для
полной trace-сверки двух исключений потребуется иной доступный архив с полными logs;
сейчас такой источник не подтверждён, QuickNode не возобновлять. Replay по текущему
состоянию и склейка primary tail не являются независимым восстановлением истории.
ML/RL/trading и qualification не повышены.
