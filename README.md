# Memecoin AI

Рабочий локальный фундамент ML trading research для Solana memecoins. **Реальный edge не установлен.** Reference replay остаётся synthetic-only. Отдельный Raydium CPMM adapter проверяет экономические переходы и swap quotes на реальных транзакциях; полная модель исполнения ещё не квалифицирована.

## Быстрый запуск

Python 3.11+; runtime использует стандартную библиотеку. Команды выполняются из корня репозитория.

```sh
make setup
make check
make smoke
```

`make smoke` создаёт временный synthetic dataset, проверяет ingest/dedup/export, дважды выполняет replay и сравнивает результат. Пример намеренно содержит убыточную сделку; тест проверяет корректность, а не прибыль.

Для сохранения локальных артефактов:

```sh
.venv/bin/mcai fixture --out work/demo-data
.venv/bin/mcai audit work/demo-data
.venv/bin/mcai run work/demo-data --config configs/synthetic.toml --out runs/demo
.venv/bin/mcai verify-run runs/demo
```

Существующие datasets и runs не перезаписываются. Для повторного опыта указать новый output. CLI policies `cash` и `scripted_fixture` — контрольные реализации; альтернативная policy подключается через Python `Policy` interface. Формат TOML задаёт schedule, размеры, риск и execution assumptions явно.

Read-only проверка источника запускается отдельно:

```sh
export MEMECOIN_RPC_URL=https://api.mainnet-beta.solana.com
.venv/bin/mcai probe-rpc --source-alias public-mainnet --out work/rpc-probe
```

URL может содержать credential и не сохраняется в отчётах. `.env` не загружается автоматически. Probe не подписывает и не отправляет транзакции и не квалифицирует источник как торговый dataset.

## Высокоуровневое устройство

```text
Raw capture → immutable event journal → verified dataset bundle
                                        ├→ availability-gated observations → features/labels
                                        │                                  → purged folds/baselines
                                        └→ dual-clock synthetic replay
                                             policy → risk → reserved ledger → execution
                                                        ↓
                                      run provenance, metrics, checksums, limitations
```

| Код | Ответственность |
|---|---|
| `domain`, `storage`, `sources` | Временные контракты, raw evidence, SQLite, bundles и read-only RPC |
| `observations`, `sampling` | Известное на момент решения; reference feature/label pipeline |
| `execution`, `portfolio`, `replay` | Integer swaps, reservations, risk, target translation и два времени |
| `research` | Labels availability, purging, walk-forward, train-only normalization и reference forecasts |
| `config`, `experiments`, `cli` | Строгие настройки, воспроизводимые runs и проверки |

Подробные границы — [контракты реализации](docs/contracts.md), статус источников — [аудит](docs/source-audit.md). Новый Codex начинает с [AGENTS.md](AGENTS.md) и [Memory Bank](memory/README.md).

## Что пока отсутствует

Нет квалифицированного рыночного dataset, калиброванного venue execution adapter, непрерывного collector, откалиброванной latency/fill model, нейросетевого обучения, доказанного OOS edge, MPC/RL или live/paper trading сервиса. RPC receipt сейчас не заменяет историческое время получения. Синтетические reference tests не являются рыночными экспериментами E01–E14.

Реальные данные, веса, runs и credentials исключены из Git. Будущие конфигурации и краткие исследовательские отчёты должны оставаться версионированными. Не выбирать backbone, размер модели и горизонты до соответствующих экспериментов.

## Реальные данные: ограниченный аудит CPMM

```sh
export MEMECOIN_RPC_URL=https://api.mainnet-beta.solana.com
mcai qualify-cpmm --source-alias solana-public --limit 20 --out work/cpmm-audit
mcai verify-qualification work/cpmm-audit
mcai verify-qualification tests/fixtures/cpmm_control
```

`--address <pubkey>` меняет адрес discovery с программы на конкретный контрольный пул.
Максимум 30 signatures и 10 pools; время и размер каждого RPC ограничены. Запуск сохраняет
спецификацию до запросов, raw responses, receipts и все отказы. Exit 1 означает неполный/
неподдержанный образец, а не необходимость удалить неудачные строки. Exit 0 также **не**
означает готовность источника для ML: квалификационные flags остаются false.

`mcai inspect-cpmm <capture-directory> --pool <pool-id>` воспроизводит структурный inventory
сохранённого checkpoint, включая номера/длины Token-2022 extensions без интерпретации их
торговой семантики. Поддержанный legacy checkpoint отдельно проверяет pool/config/vault/mint
связи и исключает protocol/fund/creator fee counters из резервов. Это не quote adapter.

Результаты и следующая граница: [source audit](docs/source-audit.md).

## Forward pilot и восстановление

`collect-pilot` сохраняет finalized checkpoints фиксированной когорты и журнал запросов.
`recover-pilot` продолжает незавершённые страницы, transactions и block ordering после сбоя.
`audit-pilot` проверяет непрерывность vault balances между snapshots; `export-pilot` и
`verify-pilot` обеспечивают переносимый воспроизводимый архив вне Git.

Команды, ограничения и реальные результаты: [forward pilot](docs/forward-pilot.md).

## Протокольные переходы и котировки

`audit-protocol --store JOURNAL --out REPORT` восстанавливает classic-SPL CPMM vaults,
раздельные fee counters и LP supply; сверяет swap math с событиями, CPI transfers и
контрольными snapshots. Это read-only проверка условной котировки, не гарантия будущего fill.
Поддержка, фактические результаты и ограничения: [protocol audit](docs/protocol-audit.md).

## Ограниченный Token-2022 adapter

Новые pilots могут явно выбрать `collect-pilot --token-2022`. Семь accounts включают
Clock того же bank; принимается whitelist metadata/transfer-fee/immutable-owner extensions.
`audit-protocol` разделяет gross/net transfers и withheld vault balances. Старые journals
остаются classic-only. Scope и epoch/harvest ограничения: [Token-2022](docs/token2022.md).

## История параметров и bounded block recovery

Новый pilot: `collect-pilot --token-2022 --dependency-history` наблюдает также config
и mint-адреса. `recover-pilot --full-blocks` группирует recovery по блокам;
`recover-pilot --block-range` проверяет полный фиксированный диапазон до 256 слотов,
если пагинация общего config слишком дорога. Оба режима сохраняют настоящие raw
captures. `audit-liquidity-witness CAPTURE --out REPORT` — отдельная, более слабая
проверка исторических classic LP операций по event/transfers.
Контракт и mainnet результаты: [parameter history](docs/parameter-history.md).

## Независимая сверка и read latency

`crosscheck-captures` повторяет точные finalized-запросы у другого RPC, сохраняет весь
план, raw evidence и отказы. `measure-rpc-latency` измеряет response duration и Clock
observations; это не latency исполнения сделки. `verify-provider-audit` пересчитывает
sealed report offline. `audit-collection-witness` проверяет fee transfers без выдуманного
prestate counters. `compare-rpc-captures LEFT RIGHT --out REPORT` сравнивает сохранённые
raw captures без сети, показывает пути различий и marker обрезки логов. Одинаковые
обрезанные traces не дают положительного заключения о полноте. Новые отчёты используют
comparison schema 3; schema 1/2 сохранены для старых архивов.
Результаты, команды и ограничения второго источника:
[provider audit](docs/provider-audit.md).

## Экономический dataset для следующего этапа симуляции

`export-economic-dataset --store JOURNAL --size-ppm 10 --size-ppm 100 --size-ppm 1000
--out NEW_DIRECTORY` строит offline state tape и условную quote grid из frozen pilot.
`verify-economic-dataset NEW_DIRECTORY` полностью воспроизводит их из включённого raw
journal. Непроверенные интервалы карантинируются; historical availability остаётся
unknown, synthetic replay gate сохранён. Контракт: [economic dataset](docs/economic-dataset.md).
