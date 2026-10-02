# Текущий статус

Обновлено: **2026-10-02**, Europe/Moscow.

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

Локально на Python 3.13: 212 автоматических тестов, Ruff lint/format, strict mypy, полный CLI smoke с повторным ingest/export и двумя побитно одинаковыми replay results, сборка sdist/wheel. Synthetic fixture имеет отрицательный net-outcome, cash сохраняет капитал. Эти тесты проверяют бухгалтерию и временную семантику, не рыночную прибыльность.

## Чего нет и чего нельзя утверждать

Нет qualified historical/live market dataset, полной исторической account-state reconstruction, production collector, empirically calibrated execution, neural training, MPC/RL, paper/live bot и доказанного OOS edge. Ни один рыночный E01–E14 не проведён. Эмпирический forecast baseline тестировался только как reference implementation.

ReferenceReplay намеренно synthetic-only, one-quote, one-pool-per-base и serial-pending. Snapshot replay имеет контрфактуальное ограничение own impact; per-order/cumulative budgets не устраняют его. Данные пока обрабатываются in-memory; полного Solana throughput benchmark нет. RPC receipt сегодня не является временем доступности исторического события.

## Открытые решения

Backbone, размер, temporal format, scanner/K, cross-token/graph, targets/horizons/frequency, sizing, MPC/RL, источники, капитал, primary currency и реальные risk/promotion thresholds остаются открытыми. Числа `configs/synthetic.toml` — инженерный fixture, не утверждённые финансовые параметры. Актуальный реестр D01–D32: [decisions](decisions.md).

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

## Следующий крупный практический этап

Сначала выяснить HTTP 413 для getBlocks в настройках/логах QuickNode и согласовать
продолжение диагностики с пользователем. Endpoint уже доступен через `.env.local`,
но CLI сам файл не загружает. Затем синхронный bounded pilot: зафиксировать
диапазон finalized slots, сверить enumeration и порядок/hash блоков у двух операторов,
полные metadata релевантных tx и checkpoint reconstruction. Отдельно расширить замеры
доступности и накопить anchors для редких переходов. Нынешние 403/429 нельзя обойти
переименованием исходного RPC, а известные три tx не доказывают полноту диапазона.
ML/backtest profitability до прохождения data/execution gates не заявлять.
