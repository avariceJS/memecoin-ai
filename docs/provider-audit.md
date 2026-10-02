# Второй провайдер, время получения и редкие fee operations

Текущий кандидат второго провайдера — **Helius**. Новые QuickNode запросы прекращены
по поручению пользователя; разделы ниже сохраняют историю. Результат перехода и
расхождение полноты logs при работающем direct RPC: [Helius audit](helius-audit.md).

Результат 2026-10-02: инструменты реализованы, read latency измерена, найдены три
mainnet fund-fee witnesses. **Независимая сверка не состоялась из-за недоступности
второго RPC.** Полнота источника и execution qualification остаются недоказанными.
Числа, hashes и provenance: [evidence](evidence/provider-audit-2026-10-02.json).

## Сверка источников

`crosscheck-captures` сначала сохраняет весь план и исходные captures, затем повторяет
точные finalized-запросы у другого source alias. Поддержаны getBlocks, getBlock
(full/signatures), getTransaction и account reads. Сравниваются значения accounts,
порядок транзакций, block identity и transaction metadata; транспортный JSON не обязан
быть побитово одинаковым. Null/empty block enumeration не считается совпадением.
Accounts разных context slots получают `incomparable`: minContextSlot — нижняя граница,
а не запрос исторического состояния. HTTP 401/403/429 останавливают оставшийся план;
пропущенные запросы не исчезают из знаменателя. Никакого fallback на исходный RPC.

Разные имена/домены не доказывают независимость инфраструктуры. Даже полное совпадение
не превращает небольшой образец в доказательство полноты истории. Для следующего
опыта нужны независимый оператор и фиксированный диапазон finalized slots: сравнить
getBlocks, identity/order каждого блока, затем полные metadata релевантных транзакций.
Сверка нескольких известных tx сама по себе не проверяет отсутствие пропущенных tx.

| Проверенный кандидат | Наблюдение из текущего окружения | Результат |
|---|---|---|
| PublicNode | HTTP 403; formal plan 3, attempted 1 | Ни одного secondary capture |
| OnFinality | HTTP 429 в preflight, повторе и formal plan 2, attempted 1 | Ни одного secondary capture |
| dRPC | HTTP 403 в preflight | Не допущен к сверке |

Это наблюдения доступа, а не установленные причины отказа или оценка качества данных
операторов. Проба неподтверждённого Solana URL 1RPC также дала 403; она **не** считается
проверкой документированного Solana-сервиса. Ankr требует API key; обращения с чужими
ключами и покупка доступа не предпринимались. Полные denied plans сохранены локально
в `work/cross-provider-stage/crosscheck-{publicnode,onfinality}`.

```sh
# MEMECOIN_SECONDARY_RPC_URL задать безопасно в окружении, не в Git.
mcai crosscheck-captures tests/fixtures/fund_collection/fund-tx-2 \
  tests/fixtures/parameter_history/lp-0-tx-1 \
  --source-alias secondary-operator --out work/secondary-audit
mcai verify-provider-audit work/secondary-audit
```

`verify-provider-audit` проверяет registry, hashes и пересчитывает отчёт без сети.
Отказы тоже являются корректными архивами: успешная offline verification не означает
успешную независимую сверку. Source snapshot — версия кода при сборе; поздние проверки
не переписывают этот snapshot. Доказательства ошибки HTTP — локальные записи клиента,
не подписанные сервером ответы. Credential URLs и тексты transport errors не сохраняются.

## Что именно измерено

Один изолированный run, 12 пар concurrent Clock reads confirmed/finalized, 24 запроса,
0 ошибок. После завершения каждой пары пауза 3 секунды. Исторический backfill параллельно
не запускался. Не является нагрузочным тестом, длительным SLA или выбором frequency.

| Величина | confirmed | finalized |
|---|---:|---:|
| HTTP response duration, p50 | 253.726 ms | 249.244 ms |
| HTTP response duration, p95/max | 385.231 ms | 315.638 ms |
| Receipt minus chain Clock, p50 | 1.217 s | 9.718 s |
| Receipt minus chain Clock, max | 1.816 s | 10.482 s |

Response duration измеряется monotonic clock вокруг запроса и включает transport/TLS,
RPC и загрузку ответа. Часы chain и клиента не синхронизированы этим опытом; Unix timestamp
Clock имеет секундную точность. Поэтому receipt-minus-Clock — наблюдаемый возраст с
погрешностью часов, не измерение one-way network latency. У 12 samples nearest-rank p95
совпадает с максимумом; выводы о хвостах нагрузки делать рано.

Первый confirmed slot 452421055 позже увиден в finalized; разница wall-clock receipts
9.851336 s. Это polling-dependent интервал видимости **номера слота**, не доказательство
того же blockhash, не точный момент consensus finality и не задержка включения сделки.
Если finalized достиг target до первого confirmed receipt, delay остаётся null
(left-censored); если не достиг за run — null и finalized_seen=false (right-censored).
Wall-clock corrections могут исказить этот показатель. Execution latency остаётся unknown.
Для коротких горизонтов потребуется отдельное исследование confirmed feed с fork/rollback
семантикой; нельзя просто приписать historical finalized данным возраст 250 ms.

```sh
mcai measure-rpc-latency --source-alias solana-public --rounds 12 --interval 3 \
  --out work/read-latency
mcai verify-provider-audit work/read-latency
```

## Новое mainnet-покрытие редких переходов

Вместо случайного поиска swaps использованы protocol/fund authority из ранее полученного
config и creator известного пула. Это **ретроспективная диагностика**, не PIT universe или
feature. Для каждого адреса сохранена страница 12 signatures; проверены первые 8.
У protocol authority все 8 failed (транзакции не догружались), у fund и creator — по
8 successful captures. Из 16 полученных успешных tx найдены три `collect_fund_fee`:

| Slot | Gross vault debits (atomic units) | Observed deductions |
|---|---|---|
| 452419948 | 3497269462 / 96571796 | 104918084 / 0 |
| 452419907 | 749251459 / 3922 | 7492515 / 0 |
| 452419856 | 1665667007 / 151948 | 16656671 / 1520 |

`audit-collection-witness` проверяет pinned instruction layout, requested limits,
прямые token CPI transfers и точное соответствие pre/post vault и recipient balances.
Лишние потоки через эти accounts, оборванные traces и failed transactions отклоняются.
Token-2022 net credits могут быть меньше gross debits. Наблюдаемые удержания **не**
доказывают исторический tariff без mint anchor. Прежние counters неизвестны: равенство
`min(requested, counter)` и конечный counter не реконструированы. Модель протокола не
потребовала изменения на этих трёх примерах; уровень доказательства расширен аккуратно.

```sh
mcai audit-collection-witness tests/fixtures/fund_collection/fund-tx-2 \
  --out work/fund-witness.json
```

Компактные неизменённые captures в Git; полный поиск и measurement archives вне Git.
Не найдены подтверждения protocol/creator collections, config/setTransferFee mutations,
epoch crossing и LP transitions между независимыми checkpoints. Не выбранные сегменты
не считаются отсутствующими на рынке. E01–E14 не проведены, synthetic replay gate сохранён.

## Первичные источники

- [PublicNode Solana](https://solana.publicnode.com/),
  [OnFinality public networks](https://onfinality.io/en/networks),
  [dRPC Solana API](https://drpc.org/docs/solana-api),
  [Ankr API access](https://www.ankr.com/docs/api-reference/).
- [Solana public RPC limitations](https://solana.com/docs/references/clusters).
- [Pinned Raydium collect_fund_fee](https://github.com/raydium-io/raydium-cp-swap/blob/59fb845a9e5bb569c8b2f3415f13b0c0ebcc6b92/programs/cp-swap/src/instructions/admin/collect_fund_fee.rs).


## Повторная попытка с QuickNode

Пользователь настроил `.env.local`; файл прочитан явно, без `source`/исполнения его
содержимого. CLI автоматически dotenv не загружает. `getSlot(finalized)` прошёл, но
первая сверка `getBlocks(452408962,452408973,{commitment:finalized})` вернула HTTP 413.
План 13 / attempted 1 / agreement false; остальные запросы не отправлялись.
JSON body — 103 bytes. Причина ответа неизвестна: transport сохраняет status, но
не response body HTTPError. Не считать это доказательством размера запроса сверх лимита
или конкретного ограничения тарифа. Пользователь уведомлён, дальнейшая сеть остановлена.
Теперь crosscheck прерывается при любой ошибке запроса, включая JSON-RPC error,
с сохранением планов и доступных raw captures. [Evidence](evidence/secondary-access-2026-10-02.json).


## Части по пять слотов

Пользователь установил ограничение QuickNode Discover: getBlocks ≤5 slots. Размер
части — параметр транспорта, не изменение фиксированного исследовательского диапазона.
Новые команды:

```sh
mcai crosscheck-range work/secondary-stage/range/000-reference \
  --source-alias quicknode-secondary --max-slots 5 --timeout 60 --out work/new-enumeration
mcai verify-provider-audit work/new-enumeration
# После успешной crosscheck-captures всех full blocks исходного диапазона:
mcai audit-provider-range work/new-enumeration work/new-blocks --out work/range-report.json
```

Границы inclusive: 452408962–966, 967–971, 972–973. Оригинальный широкий RPC capture
не переписывается и не нарезается на фиктивные captures. Каждый реальный ответ сохранён
отдельно; spec фиксирует план до сети. Проверяется точное покрытие диапазона, sorted/unique
slots и попадание каждого результата в запрошенные границы. Пустые chunks разрешены;
полностью пустой reference не даёт положительного agreement. Частичный отказ возвращает
missing/extra=null, а не вывод о пропусках у провайдера.

Full-range audit дополнительно требует совпадения всех full blocks у той же пары
источников, правильного порядка и parent/hash ancestry между соседними полученными
блоками. Skipped slots допустимы. За границами диапазона ancestry не доказывается;
не проверяются deployed binary equivalence, market universe и execution admission.

Фактическая новая попытка остановилась на первой части: SourceError без HTTP status
и response. Это не установленный повтор HTTP 413 и не доказанный timeout. Остальные
два enumeration-запроса и full blocks не отправлены. Обновлённый transport различает
категории timeout/dns/tls/connection/other по типам исключений, не раскрывая их тексты;
причина прежнего сбоя остаётся неизвестной. [Evidence](evidence/chunked-range-2026-10-02.json).

## Диагностика доступа QuickNode, 2026-10-02

Официальная [документация getBlocks](https://www.quicknode.com/docs/solana/getBlocks)
указывает предел 5 слотов для free trial и 1005 для paid accounts. Исходный диапазон
из 12 слотов превышает первый предел; прежнее разбиение 5 + 5 + 2 корректно.
Старый HTTP 413 остаётся без сохранённого тела: документация объясняет ограничение,
но не заменяет серверный trace конкретного отказа.

Наличие MCP tools не означает успешный доступ: `list_endpoints` и `call_rpc` сейчас
возвращают HTTP 401 / Console API unavailable for your plan. MCP `call_rpc` использует
Tooling Access, а не пользовательский `.env.local` endpoint. Upgrade не выполнялся.

Прямой запрос первой части в sandbox дал категорию `dns`; тот же запрос после
разрешённого запуска вне sandbox вернул пять ожидаемых слотов. В следующем отдельном
плане с timeout=30 первая часть получила `timeout`; серия остановлена. Одиночная
проба timeout=60 вновь прошла примерно за 0.4 s. Это свидетельство перемежающейся
доступности, а не доказательство, что увеличение timeout устраняет сетевую причину.
Категория старого SourceError по-прежнему неизвестна.

Команды `crosscheck-range`, `crosscheck-captures` и `measure-rpc-latency` теперь
принимают `--timeout` в диапазоне (0,60]; прежний default 10 s сохранён. Timeout
передаётся в urllib/socket и не является гарантированным общим deadline для DNS и
всей загрузки ответа. Никаких автоматических retries: повтор — отдельный явно
запущенный план в новом output. Endpoint передавать через окружение; dotenv CLI
не загружает. При sandbox DNS failure использовать штатное сетевое разрешение.

Новый план timeout=60 полностью получил enumeration: 12/12 slots, missing/extra
пусты. Из 12 full blocks три совпали по identity/order/metadata, четвёртый запрос
получил `timeout`; оставшиеся восемь не отправлены. Bounded-range agreement остаётся
false. Три новых bundles прошли offline verification; старые outputs не переписаны.
[Диагностический отчёт](evidence/quicknode-diagnostic-2026-10-02.json).

## Диагностика заранее заданной группы счетов

`diagnose_block_account_scope(left, right, addresses)` — offline диагностика двух
full jsonParsed captures, отдельно от qualification. Она сохраняет строгий schema 3
результат и классифицирует **все** транзакции по совпавшим resolved accountKeys:
есть writable reference на заданные счета; только readonly reference; ссылок нет.
Failed transactions остаются в потенциальных writers. Сравниваются полные transaction
objects; неизвестные поля и truncated logs не удаляются. Неоднозначные writable flags,
разный порядок транзакций или accountKeys запрещают классификацию.

Список счетов и его происхождение фиксируются в плане до анализа. Совпадение группы
потенциальных writers не доказывает полноту списка зависимостей, trace completeness
всего блока, правильность replay или независимость инфраструктуры. Readonly mismatch
остаётся mismatch. Этот инструмент не меняет gates CPMM replay и full-range audit.
