# CPMM economic dataset: мост от raw-аудита к симуляции

Новый этап после закрытия проверки второго RPC по поручению пользователя (D33).
Задача — воспроизводимая история экономических состояний и условных котировок из уже
собранных данных. Это отдельный формат для data QA и проверки симулятора; ни обучение,
ни replay стратегий на реальном рынке этим этапом не разрешаются автоматически.

## Контракт и команды

```sh
mcai export-economic-dataset --store work/token2022-stage/frozen/journal.sqlite \
  --size-ppm 10 --size-ppm 100 --size-ppm 1000 --out work/new-economic-dataset
mcai verify-economic-dataset work/new-economic-dataset
```

Команды offline, endpoint/credentials не нужны. Output создаётся только новым каталогом.
SQLite backup фиксирует согласованную копию journal; исходный файл открывается read-only.
В bundle входят `journal.sqlite`, `spec.json`, `source.json`, `states.jsonl`,
`quotes.jsonl`, `report.json`, `manifest.json`. План размеров и версия transformation
сохраняются до реконструкции. Незавершённый export без manifest не считается dataset.
Verifier проверяет hashes, совпадение исходников и полностью воспроизводит derived
rows/report из собственной копии journal. Изменение rows с пересчётом checksum не
обходит проверку. Для иной версии transformation требуется соответствующая версия кода.

| Таблица | Смысл |
|---|---|
| states | Start anchor, состояние **после** каждой упорядоченной транзакции, end anchor; pool/mints/decimals, vaults, effective reserves, protocol/fund/creator counters, LP supply, withheld, epoch, действующие fees |
| quotes | Две стороны × заданная сетка gross input; pool fees и Token-2022 deductions, net output, независимый гипотетический immediate round trip |
| report | Все интервалы, причины карантина, число rows/quotes, ограничения и запреты использования |

Segment задаётся парой checkpoints и pool. На общей границе остаются отдельные rows;
между сегментами нет неявного carry-forward. Все связанные транзакции, включая failed
и no-op, сохраняются. `slot`/`transaction_index` задают порядок; `blockTime` не становится
наносекундным временем исполнения. Первая/последняя строки — bank snapshots после
соответствующего slot, с `transaction_index=null`, а не выдуманные транзакции.

## Проверка состояния и карантин

Экспорт подключён к существующему `audit_protocol`: тот же ordered replay, CPI/events,
transfer math, history parameters, epoch rules и endpoint checks. Второй реализации
CPMM нет. Snapshot значений не мутирует контекст reconstruction. Параметры не берутся
из будущего endpoint для исправления прошлого. При любом blocker **весь интервал**
остаётся в report, а его states/quotes не экспортируются как проверенные.
CLI возвращает 1 при неполном protocol audit; пригодные соседние интервалы не означают
успех всей выборки. Неподдерживаемая операция или gap не превращается в отсутствие trades.
Сбой структурного аудита может прервать export без manifest.

## Время и provenance

Каждая строка содержит capture ID, SHA исходного ответа и реальный receipt; журнал
содержит остальное evidence, включая order/coverage/endpoints. `evidence_ready_ns` —
консервативный максимум receipts всего frozen journal, включая будущий контрольный
snapshot. Это время получения доказательств, **не** историческая доступность features.
`historical_available_ns=null` для всех rows; соответствующее ограничение не скрывается
даже для успешного audit. Поздний backfill не создаёт наблюдения в прошлом.

Состояния подходят для инженерной проверки экономических переходов. До исторического
ML/backtest нужны отдельно специфицированные causal observations/availability assumptions,
features/targets, coverage и temporal splits. Dataset не преобразуется в старый `Pool`
с одной fee_bps: так потерялись бы creator/transfer fees и исключённые counters.
`ReferenceReplay` остаётся synthetic-only. `training_ready`, `market_dataset_qualified`
и `execution_qualified` остаются false.

## Условная симуляция размера

Размер задаётся явно как ppm от effective input reserve, затем округляется вниз до
atomic units; это **gross wallet input**, перед transfer fee. 10/100/1000 ppm в первом
инженерном опыте — сетка диагностики, не выбранный торговый капитал или оптимальный
sizing. Допускается 1–8 уникальных размеров, каждый не выше 1% резервов.

Каждый probe начинается с неизменённого исторического состояния. Reverse swap идёт
по гипотетическому состоянию **после** forward swap с теми же тарифами. Положительный
немедленный round trip считается ошибкой и останавливает export. Нет переноса own impact
в следующую историческую строку. Неизвестный/pre-open timestamp, disabled swaps и
арифметически недопустимые размеры дают явную unavailable quote, не исчезают из сетки.

Это conditional math при допущении admission и неизменённых параметров. Нет модели
inclusion, latency, MEV, user-account permissions, network/priority fees и rent;
Clock/blockTime не доказывает admission. Наблюдавшаяся `meta.fee` хранится отдельно как
`observed_network_fee_lamports` и не выдаётся за цену будущей сделки. Quote samples и
roundtrip outputs — не fills, не PnL стратегии и не logged transitions для offline RL.
