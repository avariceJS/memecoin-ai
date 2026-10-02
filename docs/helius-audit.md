# Helius: проверка доступа, 2026-10-02

**Результат:** документация доступна, chain data через Helius не получены.
Совпавших блоков Helius: **0/12**, транзакций: **0**. Enumeration, identity,
порядок, metadata и ancestry между Helius и Solana public RPC пока не проверены.
Это блокер доступа, а не свидетельство расхождения данных.

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

## Безопасный лог отказа

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

В окружении процесса и `.env`/`.env.local` проекта Helius endpoint/key не найден.
Имеющаяся `MEMECOIN_SECONDARY_RPC_URL` не относится к Helius и не использовалась.
Новые запросы к QuickNode запрещены текущим поручением и не выполнялись.
Для продолжения требуется локально настроить **`MEMECOIN_HELIUS_RPC_URL`** с существующим
авторизованным mainnet HTTPS endpoint. Секрет не передавать в чат/Git. CLI не загружает
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

## Следующий ограниченный запуск

`work/helius-stage/direct-rpc-plan.json`: максимум 16 запросов — getGenesisHash,
getVersion, finalized getSlot, один getBlocks и 12 exact full getBlock. Timeout 30 s,
cap 32 MB/ответ, spacing 3 s, без retries; ошибка/несовпадение останавливает план.
Это план, **запуск не проведён**. Исторический интервал фиксирован; seed и train/test
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

## Проверки

239 tests, Ruff lint/format, strict mypy, CLI smoke и сборка sdist/wheel прошли.
Сборка сначала остановилась на sandbox DNS при загрузке pinned setuptools;
повтор через штатное сетевое разрешение завершился успешно.
