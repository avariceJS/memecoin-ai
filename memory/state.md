# Текущий статус

Обновлено: **2026-10-02**, Europe/Moscow.

**Текущий результат D39:** найден и проверен bounded вариант согласованного источника:
canonical public mainnet `https://api.mainnet.solana.com`, explicit verified TLS keepalive
внутри сегмента, новый процесс/соединение на его границе. Account/Clock/temporal checks
не ослаблены. Некоторые прежние timeout локализованы до HTTP на connect/TLS; полная
причина всех отказов неизвестна. User-Agent сам по себе проблему не устранил.

**482 новых accepted snapshots  / 483 corpus requests**, 483 feature rows, 464 usable,
781.187 s суммарного segment coverage. Первая кампания 302 valid остановилась на
connection error четвёртого сегмента; вторая 180/180 завершена с реальным process restart.
Все failed/censored artifacts сохранены. Helius Clock mismatch unresolved; QuickNode
не использован, D33 whole-chain historical audit остаётся закрытым.

Execution-conditioned targets: 3864 scenarios, 345 distinct mature decisions,
141 engineering evaluation. Все восемь scenarios созрели в одной partition у 307
решений; chronological nonoverlap оставляет 15 окон по двум кампаниям. Это не
независимые samples и не final holdout. Net PnL неизвестен, ML/RL/paper/live не запускались.

346 tests, lint/format/mypy, smoke/build и dev/installed-wheel verification прошли.
Memory и previous raw/source snapshots сохранены. Контракт: [persistent source](../docs/persistent-source.md);
[evidence](../docs/evidence/persistent-source-2026-10-02.json). Следующий этап —
разные календарные окна bounded prospective сбора и execution uncertainty QA, затем
решение о dataset sufficiency. Public RPC не объявлен production source.

## Стадия и авторизация

Memory Bank принят как source of truth. Пользователь разрешил автономный архитектурный проход: реализацию, тесты, локальные инструменты, коммиты и push в существующий `origin/main` при доступной аутентификации. Выполнены утверждённые bounded forward collection/recovery pilot и этапы протокольной реконструкции CPMM и ограниченной поддержки Token-2022. Реальные деньги, торговые транзакции и оплата внешних сервисов не разрешены.

Локальный инженерный фундамент реализован. Это не прохождение рыночных gates R2–R8: реальный источник/сегмент ещё не квалифицирован, edge не установлен. Git и факт отправки проверять непосредственно через историю и remote, а не по вручную дублируемым hash.

## Что реально существует

- Python 3.11+ package и CLI; runtime на stdlib. Dev tools закреплены в lock-файле.
- Неизменяемые нормализованные события, observed/assumed/unknown availability, raw provenance.
- SQLite append/dedup с rollback конфликтов, content-verified bundles и аудит ограничений.
- Availability-gated observations, causal reference features и отдельные targets/censoring.
- Synthetic dual-clock replay, integer CPMM reference, задержки, slippage/failure/drop, reservations, fees, risk и target-to-intent conversion.
- Liquidation marks с сохранением непроданных позиций и явными unpriced assets.
- Purged walk-forward, mature-label guard, train-only standardizer, empirical distribution baseline, pinball и block bootstrap.
- Run config/provenance/source snapshot, completed/failed lifecycle, checksums и evidence blockers для promotion.
- Read-only RPC capture/probe и bounded CPMM qualification: реальные v0/v1 транзакции, pinned account decoder, structural Token-2022 inventory, offline reproduction и сверка vault flows.
- Bounded forward collector фиксированной когорты: SQLite/WAL journal, persisted cursors, recovery, block ordering, vault continuity audit и переносимые snapshots.
- Allowlisted Token-2022 checkpoints с Clock, transfer-fee gross/net math и отдельный withheld ledger с harvest-to-mint.
- Make tooling, тесты и CI definition для Python 3.11/3.13. Состояние удалённого CI не следует путать с локальной проверкой.

Команды и карта модулей: [README](../README.md). Точные ограничения: [contracts](../docs/contracts.md). Проверка источника: [source audit](../docs/source-audit.md).

## Что проверено

Локально на Python 3.13: 346 автоматических тестов, Ruff lint/format, strict mypy, полный CLI smoke с повторным ingest/export и двумя побитно одинаковыми replay results, сборка sdist/wheel. Synthetic fixture имеет отрицательный net-outcome, cash сохраняет капитал. Эти тесты проверяют бухгалтерию и временную семантику, не рыночную прибыльность.

## Чего нет и чего нельзя утверждать

Нет qualified historical/live market dataset, полной исторической account-state reconstruction, production collector, empirically calibrated execution, neural training, MPC/RL, paper/live bot и доказанного OOS edge. Ни один рыночный E01–E14 не проведён. Эмпирический forecast baseline тестировался только как reference implementation.

ReferenceReplay намеренно synthetic-only, one-quote, one-pool-per-base и serial-pending. Snapshot replay имеет контрфактуальное ограничение own impact; per-order/cumulative budgets не устраняют его. Данные пока обрабатываются in-memory; полного Solana throughput benchmark нет. RPC receipt сегодня не является временем доступности исторического события.

## Открытые решения

Backbone, размер, temporal format, scanner/K, cross-token/graph, targets/horizons/frequency, sizing, MPC/RL, источники, капитал, primary currency и реальные risk/promotion thresholds остаются открытыми. Числа `configs/synthetic.toml` — инженерный fixture, не утверждённые финансовые параметры. Актуальный реестр D01–D39: [decisions](decisions.md).

## Результат bounded data-аудита

На mainnet полностью получен финальный образец 20/20 транзакций: 4 pool inventories,
2 strict reserve checkpoints, 2 совпавшие vault-flow сверки одной транзакции по двум пулам.
Ещё 2 пула отклонены строгим decoder из-за Token-2022. Контроль SOL/USDC: 5/5 транзакций,
1 strict checkpoint, 2 совпавшие сверки двух транзакций. Контрольный pool не мемкоин;
классификация токенов основной выборки не подтверждена. Это не полная историческая
реконструкция, не независимая проверка провайдера и не swap-price calibration.

Публичный Raydium каталог ответил 403; read-only mainnet RPC пригоден для bounded audit.
Выявленная ошибка RPC версии транзакций исправлена после проверки актуальных docs.
Решение: CPMM — первая семья data adapter; legacy-only ограничение не распространять
на всю наблюдаемую вселенную. История отказов сохранена. Подробности и точные evidence
в [source audit](../docs/source-audit.md); компактный реальный regression fixture в Git.

## Результат forward pilot

Сбор на фиксированной когорте двух ранее известных пулов: 10 finalized checkpoints за
211.299 секунды receipt span, 8 интервалов (2 непустых), 189 транзакций в 15 блоках.
Из них 46 успешных, 143 failed; v0/v1 сохранены. После restart/backfill все 24 address
scans завершены, pending jobs нет, промежуточные balances и конечные residuals совпали
во всех интервалах. Перед дозагрузкой gap-open audit не проходил; endpoints не менялись.

56 записей об ошибках/остановках сохранены, включая один измеренный HTTP 429. Начальные
маленькие страницы и исправленная ошибка CLI привели к лишним запросам: это не throughput
benchmark. Журнал и ~8.7 MB архив вне Git; summary/hashes в docs/evidence/forward-2026-10-01.json.
Архив повторно проверен без сети из development package и установленного wheel. Повторное
recovery завершённого журнала не отправило запросов. Подробности — [forward pilot](../docs/forward-pilot.md).

На момент завершения forward pilot реконструировались только classic SPL vault amounts.
Следующий протокольный этап расширил эту проверку — см. ниже. Полнота индекса
условна относительно одного провайдера; birth/death universe и классификация memecoins
не доказаны. Synthetic execution gate и market_dataset_qualified=false сохранены.

## Результат протокольного этапа

Реализованы `cpmm.py` (integer transitions) и `protocol.py`/`audit-protocol` (CPI/event/
transfer/endpoint verification). На прежнем immutable журнале: 44 swap — 42 exact-input,
2 exact-output; 143 failed без изменения состояния; 2 успешных no-op маршрута. Все суммы
swap совпали до atomic unit; экономическое состояние совпало во всех 8 интервалах.
Новая версия не меняет старый checkpoint schema и не подгоняет состояния по endpoints.

126 тестов, lint/format/strict typing, synthetic smoke и package build прошли. Повторный
аудит, включая установленный wheel, побитово идентичен; прежний frozen pilot также
проверен повторно. Компактные реальные regression captures сохранены в Git; полный
журнал, source captures и отчёты вне Git, hashes/summary — docs/evidence.

На момент завершения classic этапа deposit/withdraw/collections и creator fee были
подтверждены только инженерными тестами; creator fee проверена в следующем этапе ниже. Дополнительная диагностическая выборка 24 mainnet tx
нашла creator-fee swaps; все четыре проверенных кандидата используют Token-2022 и не
допущены strict classic adapter. Полная source completeness, config-history, binary
equivalence, oracle/Clock, latency/inclusion/network costs остаются неизвестными.
Полная execution qualification, market dataset и edge не объявлены.
Детали, команды и provenance: [protocol audit](../docs/protocol-audit.md).

## Результат Token-2022 этапа

Whitelist mint/vault extensions реализован отдельно от inventory. Schema 2 явно
выбирается при создании pilot и включает Clock того же bank. В новом mainnet pilot:
9 snapshots трёх пулов за 52.948 секунды, 382 транзакции в 88 блоках, 6 интервалов.
132 exact-input swap с creator fee совпали до atomic unit; 58 входящих и 65 исходящих
transfer fees проверены. 8 harvest-to-mint восстановлены отдельно от spendable reserves.
Все конечные economic/withheld состояния совпали, pending jobs нет.

Реальные harvest показали неполноту первоначальной модели «только начислять withheld»;
переход добавлен по pinned source и реальным traces. Публичный RPC дал 19 HTTP 429 и
один transport SourceError: история сохранена, recovery завершён после замедления.
Новый collector добавляет cooldown и увеличивает spacing после 429 без скрытого retry.

160 тестов, lint/format, strict typing, smoke, build, old archive regression и одинаковые
dev/wheel protocol reports прошли. ~22.6 MB frozen journal вне Git; компактные реальные
fixtures и summary/hashes сохранены. Подробности: [Token-2022](../docs/token2022.md).

Mainnet-подтверждения exact-output с Token-2022, LP changes, pool fee collection и
прямого withdraw withheld ещё нет. Epoch-boundary и mint/config изменения блокируют
интервал; полная mint-wide сумма withheld не реконструируется. У одного из трёх пулов
не было успешных swaps: его snapshots не расширяют доказанное покрытие quote math.
Qualified dataset, полное execution и edge не заявлены; synthetic gate сохранён.

## Результат истории параметров

Реализованы opt-in config/mint dependency history, epoch schedule с Clock anchors,
ordered config/transfer fee/supply mutations и отдельные event-conditioned LP witnesses.
Источник показал дорогую пагинацию общего config: добавлены parsed full blocks и
альтернативное bounded-range coverage, без подмены raw captures или старых cursors.

На mainnet два новых интервала по 12 слотов: 455 + 863 связанных tx, economic/withheld/
config/mint endpoints совпали. У самих выбранных pools успешных операций не было;
это не новые quote calibration observations. 13 отдельно найденных LP witnesses
совпали по arithmetic/transfers, но их prestate взят из events. Один реальный внешний
withdraw-withheld сохранён как regression без притязания на полный mint ledger.

Смена config/tariff и граница эпох проверены инженерно, но не наблюдались в новых
mainnet интервалах. LP между anchors, pool fee collections, прямые withdrawals в/из
tracked vault, независимая полнота и deployed binary equivalence остаются gates.
192 tests, lint/typing, smoke/build, offline frozen verification и побитово равные
dev/wheel range reports прошли; старые classic/Token-2022 audits также прошли.
E01–E14 не проведены.
Документ и evidence: [parameter history](../docs/parameter-history.md).

## Результат проверки второго провайдера и времени получения

Реализованы exact-request cross-provider comparison, bounded paired Clock sampler,
sealed raw/measurement bundles с offline reproduction и collection-transfer witnesses.
Независимая сверка **не состоялась**: PublicNode вернул 403, OnFinality — 429;
formal plans сохранены полностью, остановлены после первого отказа. dRPC также дал
403 в preflight. Смена alias не считается доказательством независимой инфраструктуры.

Изолированный реальный run: 12 пар confirmed/finalized, 24 запроса, 0 ошибок.
Response p50 253.726 / 249.244 ms; receipt minus chain Clock p50 1.217 / 9.718 s.
Это read-side measurements с ограничением часов/выборки, не inclusion/fill latency.
Нельзя присвоить historical finalized данным доступность через 250 ms.

Целевой поиск fee authorities дал 3 collect_fund_fee: CPI/metadata/limits совпали,
наблюдены Token-2022 deductions. Старые fee counters и historical tariffs неизвестны;
полная checkpoint-anchored collection реконструкция не объявлена. Protocol/creator
collections, config/tariff updates и epoch boundary по-прежнему не подтверждены.
209 tests, lint/format/typing, smoke/build и dev/wheel offline reproduction прошли.
Подробности и ограничения: [provider audit](../docs/provider-audit.md).

## Повторный запуск с настроенным пользователем RPC

2026-10-02 пользователь сообщил о настройке `MEMECOIN_SECONDARY_RPC_URL`.
В текущем процессе Codex переменная отсутствует; `.env`/`.env.local` в репозитории
не найдены, `.env.example` содержит пустое значение. CLI preflight в 09:33:53 UTC
завершился с exit 2: `RPC endpoint environment variable is not set`.
Сетевых запросов к новому RPC не было; его доступность не проверена. Запрошено место
настройки без передачи секрета в чат. CLI не загружает `.env` автоматически.
Локальный журнал: `work/secondary-env-preflight-20261002T093353Z.json`.

## QuickNode: доступ восстановлен, range-запрос отклонён

После уточнения пользователя `.env.local` прочитан явно без выполнения shell-кода;
секрет не выведен и не сохранён в Git. `getSlot(finalized)` у QuickNode прошёл.
Первый exact-request `getBlocks(452408962,452408973,finalized)` получил HTTP 413.
План: 13 запросов; отправлен 1, остальные 12 остановлены. JSON request — 103 bytes,
поэтому причина 413 не объявляется установленной по одному HTTP status. Тело HTTP
ошибки текущий transport не сохраняет. Пользователь уведомлён сразу; дальнейшие RPC
запросы остановлены по его условию. Независимого agreement пока нет.

Crosscheck теперь останавливает серию при любой SourceError/ValidationError, а не
только HTTP 401/403/429. 212 tests и lint/format/typing прошли. Raw references и failed
plan сохраняются, offline verification проходит. Evidence:
[secondary access](../docs/evidence/secondary-access-2026-10-02.json).

## Разбиение по лимиту QuickNode Discover

Пользователь уточнил причину прежнего HTTP 413: предел getBlocks — 5 слотов;
разрешил разбить прежний диапазон без upgrade. Реализованы inclusive partition,
проверка объединения raw ответов против исходного capture и отдельная проверка
покрытия full blocks с ancestry. Никаких синтетических RPC captures при объединении.

Новый фиксированный план: 452408962–966 / 967–971 / 972–973. Первый запрос завершился
SourceError без HTTP status и без полученного response; оставшиеся части и getBlock
не запрашивались. Причина транспорта неизвестна (старый клиент её не сохранял).
Пользователь уведомлён сразу; новый failed bundle не заменяет прежний HTTP 413.
Добавлены безопасные категории transport errors для будущих запусков; задним числом
этому сбою timeout/DNS/TLS не приписаны. Mainnet agreement по-прежнему не получено.

227 tests, lint/format/typing, smoke/build и offline verification установленным wheel
прошли. Evidence: [chunked range](../docs/evidence/chunked-range-2026-10-02.json).

## Диагностика QuickNode, 2026-10-02

Sandbox дал DNS failure, разрешённая сеть вернула первую часть. MCP инструменты
доступны, но Console API/Tooling Access получили 401 из-за тарифа. Прямой RPC работает
перемежающе: новый 30s plan остановился на timeout; 60s plan получил все 12 slots
и три совпавших full blocks, четвёртый getBlock получил timeout. Остальные запросы
остановлены. Причина старого SourceError неизвестна; увеличение timeout не доказано
как исправление нестабильности. CLI получил явный `--timeout` (0,60], default 10s.
227 tests, lint/typing и smoke прошли; три новых bundles проверены offline.
[Evidence](../docs/evidence/quicknode-diagnostic-2026-10-02.json).

## Первый preflight Helius (до настройки endpoint)

По новому поручению пользователь выбрал **Helius вместо QuickNode** для следующей
сверки. Новых QuickNode запросов не делать; его evidence и общие RPC-интерфейсы сохраняются.

Helius MCP документация ответила, но `getNetworkStatus(finalized)` вернул timeout
внутреннего getSupply (30000 ms); HTTP/RPC code неизвестны. Wall time MCP 288.369 s
не является HTTP latency прямого RPC. Серия остановлена. В schema есть getBlock,
но нет getBlocks dispatcher; успешные chain reads не подтверждены. Helius runtime
endpoint/key в проекте/окружении не найден. Сверено через Helius **0/12 блоков**.

Следующий шаг — локальная настройка `MEMECOIN_HELIUS_RPC_URL` через авторизованный
доступ, затем mainnet genesis/finalized preflight и сохранённый bounded план максимум
16 запросов, getBlocks одним диапазоном из 12 slots. CLI dotenv не загружает.
Secrets в чат/Git не передавать; upgrade не разрешён.

Offline проверены 12 reference blocks / 14 809 tx, checksums/metadata/ancestry;
SQLite frozen journal цел. Новые сравнения schema 2 сохраняют все поля block/tx
result и отклоняют неполные metadata; legacy bundles воспроизводятся schema 1.
Три прежних QuickNode blocks совпали также по schema 2 без новых запросов.
239 tests, lint/format/typing, smoke и build прошли. Исторические ML E01–E14 не
проведены, data/execution qualification не повышена. Детали, безопасный лог и план:
[Helius audit](../docs/helius-audit.md), [evidence](../docs/evidence/helius-preflight-2026-10-02.json).

## Direct Helius: доступ подтверждён, обнаружена неполнота traces

2026-10-02 после настройки пользователем MEMECOIN_HELIUS_RPC_URL выполнен
новый immutable read-only plan. Sandbox getGenesisHash дал dns failure; вне sandbox
mainnet genesis/version/finalized slot подтверждены. Enumeration 452408962–452408973
совпал 12/12. Первый full block получен за 5.373 s, 10 011 629 bytes, 1 310 tx.
Identity/order и все поля кроме двух logMessages arrays совпали; 1 308 tx совпали
целиком. У Helius tx indices 433/743 имеют Log truncated. Полных block matches 0/1,
11 остальных блоков не запрошены, range agreement false.

Один отдельный getTransaction для tx 433 вернул тот же обрезанный trace; его
meta.rewards=[] отличается от block null и также сохранён. HTTP 200 у всех шести
запросов вне sandbox; всего 10 056 361 payload bytes. Это ограниченные response-time
наблюдения, не sustained throughput/chain age/execution latency. После выявленного
metadata blocker сеть остановлена; причинный механизм/настройка Helius неизвестны.

Comparison schema 3 обнаруживает marker, сохраняет mismatch и выдаёт bounded
JSON-pointer diagnostics; одинаковая обрезка даёт unavailable. Старые schema 1/2
воспроизводятся. Добавлены offline compare-rpc-captures и неизменённый реальный
getTransaction regression. 244 tests, lint/format/strict typing, smoke и sdist/wheel build прошли.
Отчёт и raw hashes: [Helius audit](../docs/helius-audit.md),
[evidence](../docs/evidence/helius-direct-2026-10-02.json).

## Следующий ограниченный шаг на предыдущем этапе (заменён D32 ниже)

Установить доступный способ получать у Helius полные исторические logs на уже
известной signature; до этого не повторять успешно полученные данные и не начинать
синхронный сбор. Если такого пути нет, этот доступ не подходит для полной trace
qualification. Не удалять logs из сравнения и не подставлять primary tail, чтобы
получить agreement. Mainnet доступ подтверждён, но market dataset, execution,
инфраструктурная независимость и edge не квалифицированы. QuickNode для новых
запросов не используется; прежние archives остаются. ML/RL/trading не начинались.

## Актуальный результат: historical trace gate и D32

2026-10-02: дополнительные getTransaction(json) и getTransactionsForAddress(full)
для **обеих** проблемных signatures получили HTTP 200, но те же обрезанные logs.
Полных traces восстановлено 0/2. Helius untruncated LaserStream документирован,
но historical replay там не поддержан. Проверенный доступ **непригоден для полной
исторической trace-сверки**; это не запрет использовать его для иных проверок данных.

По новому поручению выполнен следующий ограниченный шаг: scope diagnostic на заранее
заданном cohort и один новый full block 452408963 — первый с potential write в шесть
счетов frozen cohort. Все 1 154 tx/metadata совпали; parent ancestry подтверждена.
Общий результат: **12/12 slots**, **1/2 full blocks match**, **2 462/2 464 tx exact**;
10 blocks ещё не получены, full-range agreement false. В первом блоке cohort writers
вообще нет; readonly config mismatch не удалён и не назван pass.

Синхронная finalized пара семи account reads остановлена: Helius slot 452651827
ответил (0.318 s), public RPC timeout 20.069 s, HTTP/RPC code неизвестны. Сравнения
счетов одного bank не было. Retries/upgrade/QuickNode requests не выполнялись.

Код: общая offline diagnose_block_account_scope, 8 новых tests; strict comparison
и replay trace gates сохранены. **252 tests**, lint/format/typing, smoke/build прошли;
raw manifests проверены, новые comparisons воспроизведены offline. Отчёт:
[Helius audit](../docs/helius-audit.md), [evidence](../docs/evidence/helius-traces-2026-10-02.json).

**Далее:** bounded независимая сверка полной истории записей frozen cohort на оставшихся
10 блоках (включая failed/unsupported), с отдельным строгим full-block результатом.
Не отбирать только удобные traces. Перед новой синхронной парой проверить доступность
public RPC; текущий snapshot не восстанавливает исторический bank. Для полного tail
двух исключений нужен другой подтверждённый архив. Dataset/execution/infrastructure
independence и edge всё ещё не квалифицированы; ML/RL/trading не начинались.


## Результат D34: economic dataset построен

Вместо дальнейшей RPC-сверки реализованы offline `export-economic-dataset` и
`verify-economic-dataset`: SQLite snapshot + source/config/hashes + states/quotes/report.
Состояния экспортируются непосредственно existing protocol replay; fees, counters,
LP supply, withheld, epoch и порядок tx сохраняются. Failed/no-op не удаляются.
Любой blocker карантинирует интервал целиком, без endpoint repair. Quote probes
с Token-2022 используют isolated hypothetical state и проверяют immediate round trip;
ReferenceReplay по-прежнему synthetic-only, ML/RL/paper/live не запускались.

Четыре immutable исходных journals (classic, token2022, dependency-index/range)
обработаны **без сети**. 16/16 интервалов прошли, 5 pools, 1 889 уникальных tx,
1 921 state rows, 11 430 conditional quotes; 96 unavailable на classic anchors без
chain timestamp. 176 наблюдавшихся swap checks и 8 harvest сохраняются из прежних
аудитов, это не новые market observations. В corpus 127 tx slots, sparse envelope
452384039–452408973 не означает непрерывное покрытие. Нулевой positive roundtrip
на данной сетке — проверка модели, не свидетельство PnL или edge.

Все четыре bundles пересчитаны из собственной journal-копии, Token-2022 — также из
установленного wheel. Оригиналы остались побитно прежними. **264 tests**, lint/format/
strict typing, smoke и build прошли. Raw+bundles: work/economic-corpus-01; контракт:
[dataset](../docs/economic-dataset.md); [evidence](../docs/evidence/economic-corpus-2026-10-02.json).

**Ограничения:** historical_available_ns=null для всех строк; receipt и поздняя
endpoint verification не становятся исторической доступностью features. Training,
market dataset и execution qualification false. Короткий fixed cohort не покрывает
births/deaths, разнообразие режимов или calibrated inclusion/costs. Quotes одной
истории/размеров не являются независимыми сделками или логами поведения агента.

**Следующий крупный шаг:** causal observation/feature pipeline с отдельным контрактом
доступности и gap masks, затем bounded forward corpus с настоящими receipts и
разделением calibration/evaluation. Не интерполировать будущие snapshots/параметры.
Provider stage закрыт D33; оставшиеся 10 blocks не являются текущим заданием.


## Текущий результат D35: causal features и forward corpus

CausalBook обрабатывает только реальные validated finalized snapshots семи счетов
одного bank с Clock. Receipt/decode/as-of/feature-publication timestamps разделены;
future append/mutation не меняет прошлые features. 12 признаков, nullable values и
явные masks; duplicate bank не обновляет freshness, gap/regression/conflict/invalid
history требует нового прогрева. Старый economic tape не объявляется causal dataset.

Реальный frozen plan: все три прежних Token-2022 pool, 12 rounds × 10 s, max 36 calls,
10 s timeout / 1 MB, spacing 1 s, max ages 25/45 s, max gap 25 s. Выполнены 32 вызова,
31 HTTP success / 112 971 bytes; index 31 к pool 4GWh…QYfm получил transport timeout
10.006 s, HTTP/RPC code неизвестны. Серия остановлена, retries нет, оставшиеся четыре
calls не выполнялись. Успешные response median 318.753 ms — не execution latency.

11 frames / 33 rows, 29 usable, 3 warmup + 1 timeout mask; ~109.038 s span.
В ходе QA усилен endpoint contract: fresh targets schema 2 требуют snapshot available
не раньше horizon end и slot новее start. Это исключает превращение carry-forward
feature frame в новое будущее наблюдение. Результат: **8 calibration / 4 engineering
 evaluation / 7 purged / 14 censored**. Horizon 20 s, tolerance 15 s; actual endpoints
20.129–32.554 s, без интерполяции. Schema 1 artifact сохранён неизменным как история.

Пути: work/causal-forward-session-01/corpus и work/causal-forward-targets-02.
Оба artifacts воспроизведены offline и из wheel; все raw/source/manifest checks прошли.
**280 tests**, lint/format/typing, smoke/build. Подробный контракт:
[causal forward](../docs/causal-forward.md), [evidence](../docs/evidence/causal-forward-2026-10-02.json).

Original collector code a666c5b; target-v2 code 7cc4f91. Для оригинала на новой версии
`verify-forward-corpus --compatible-version` разрешает отличие source только при
точном воспроизведении всех values; ожидаемый exit 1 означает incomplete collection.
`verify-forward-targets PARENT DERIVED` пересчитывает оба слоя. Secrets не сохранялись.

**Граница:** temporal contract проверен, но training_ready/market_dataset/execution/edge
false. Fixed cohort, короткая история, unknown intervening trades, неисполняемые price
labels и незавершённый сбор не заменяют квалифицированную выборку. Evaluation partition
использовался для инженерной проверки контракта, не final holdout. Следующий этап —
возобновляемый segmented prospective collector и более длинное покрытие с честными
пропусками; затем execution-conditioned labels до ML. RL/paper/live не начинались.


## Результат D36: bounded segments и условное исполнение

Кампания фиксирует cohort/budget/expiry/source и единый calibration cutoff до сети.
Resume создаёт новый immutable segment с warmup, hash-linked seal и OS lock; crash bytes
сохраняются, весь planned segment budget резервируется, пропуски не backfill. HTTP/RPC/
validation ошибки останавливают кампанию; timeout/connection могут продолжиться новой
invocation после cooldown, максимум два failed сегмента подряд. Resume/budget/crash/
concurrency проверены synthetic tests; реальное продолжение той же кампании в этом
запуске не выполнялось из-за non-transient validation guards.

Реальные artifacts: `work/prospective-campaign-01` (48 valid / 49 requests),
`work/prospective-campaign-02` (67 / 68), isolated `work/prospective-clock-check-01`
(1 valid, не входит в features). Всего 118 requests, все HTTP 200; два несовместимых
checkpoint в quarantine. Context/Clock пары: 452681508/452681509 и 452682339/452682340.
Root cause upstream не установлен. Raw fixtures добавлены, исходные responses неизменны.
Plan-01 360 maximum, остановлен; после probe plan-02 ограничен 270, также остановлен.
Новые запросы прекращены; 360 не было фактически израсходовано.

`work/conditional-execution-targets-01/02`: два направления, sizing 100/1000 ppm
по decision reserve, delay scenarios 0/5 s, holding 20 s, tolerance 15 s, minOut с
100 bps slippage. Quotes используют existing integer CPMM/transfer fee math. Entry,
exit-decision и exit-fill требуют новых snapshots, causal availability, no gaps.
Exit failure сохраняет acquired position; unknown network costs/admission/persistent
impact не подменяются нулями. `net_pnl=null` для всех labels; результат roundtrip только
после pool/transfer fees до network expenses, не executable/realized PnL.

444 mature scenario outcomes относятся к 67 различным decisions, все calibration;
engineering evaluation 0. Восемь вариантов и перекрывающиеся окна не независимы.
Полный долгий сбор и execution qualification остаются открытыми, training_ready=false.
Исходный D35 corpus и все прошлые journals не изменялись. 297 tests, lint/format/mypy,
smoke/build, обе пары campaign/target воспроизведены offline и из installed wheel
`work/prospective-wheel-01`. Seeds отсутствуют (детерминированная data QA), code/plan/
source hashes, точные times/cutoffs/errors и measurement metrics — в evidence D36.


## Результат D37: coherence отдельно от поддержки protocol version

Agave getMultipleAccounts выбирает один bank для accounts/context; Clock.slot в Bank
равен slot. Это поддерживает прежний strict equality guard, но не устанавливает
upstream причину двух Helius ответов. Helius MCP troubleshooting не дал объяснения;
документированный Gatekeeper с existing key получил timeout первого запроса 10.061 s.

`work/coherence-source-probe-01`: Solana public 6/6 full strict snapshots прежних трёх
пулов, Gatekeeper 0/1 timeout. `work/prospective-public-coherent-01`: новый fixed plan
3×20 rounds×10 s, 180 max requests, прежняя cohort, public origin закреплён. Первый
getMultipleAccounts дал context=Clock=452687445, но config CRRS… word@116=50000;
collection остановлен, raw сохранён. Последний preflight в slot 452686853 имел нули
в этой области. Точная write transition и смысл изменения не доказаны.

`work/coherence-classic-probe-01`: заранее выбраны оба classic pools прежнего pilot
(включая SOL/USDC control), другой общий config D4FPE…. Первый ответ в slot 452688467
тоже coherent, но word@116=50000; второй запрос не отправлялся. Пул по доходности не
выбирался, успешно прошедшая замещающая cohort не найдена и не объявлена. Разумный
следующий этап — source/deployed layout qualification, а не дальнейший перебор.

Текущий публичный Raydium source всё ещё описывает bytes 116+ как padding, update_config
не объясняет это значение. Копии/хеши в work/checkpoint-coherence-research. Program read
`work/cpmm-program-version-01` установил ProgramData адрес DMaw…tHhU, но чтение его
первых 45 bytes завершилось timeout. Upgrade slot/authority и binary equivalence
неизвестны. Нельзя делать вывод, что подтверждён upgrade или новая комиссия.

`checkpoint_diagnostics.py` даёт отдельный diagnostic-only kind, не observation;
разделяет Clock mismatch и unsupported config words. `corpus_quality.py` проверяет
coverage/maturity/boundaries/inventory и отдельно измеряет HTTP, chain age и условные
observation delays. Новые raw fixtures сохраняют оба distinct-config отказа. Guard
AmmConfig padding и все cpmm/tokens decoders не менялись.

Plans schema 2 требуют endpoint_origin (без path/query/key); resume сверяет host даже
при совпадающем alias и запрещает закрытые кампании. Новые CLI явно требуют endpoint
origin/env, Helius не подставляется в public campaign. Raw, requests, receipt times,
failures, plans, source/hashes, engineering cutoff и conditional execution model — в
D37 evidence. Новый quality artifact work/coherence-quality-01 воспроизведён offline
и из work/coherence-wheel-01; D36 history проверена work/prospective-wheel-01.
