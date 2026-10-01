# Memecoin AI

Рабочий локальный фундамент ML trading research для Solana memecoins. **Реальный edge не установлен.** Исполнение сейчас проверяется только на явно синтетических данных; поддерживаемый математический CPMM reference не является реализацией Raydium или другой площадки.

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

Нет квалифицированного рыночного dataset, venue decoder, непрерывного collector, откалиброванной latency/fill model, нейросетевого обучения, доказанного OOS edge, MPC/RL или live/paper trading сервиса. RPC receipt сейчас не заменяет историческое время получения. Синтетические reference tests не являются рыночными экспериментами E01–E14.

Реальные данные, веса, runs и credentials исключены из Git. Будущие конфигурации и краткие исследовательские отчёты должны оставаться версионированными. Не выбирать backbone, размер модели и горизонты до соответствующих экспериментов.
