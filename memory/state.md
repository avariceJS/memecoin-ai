# Текущий статус

Обновлено: **2026-10-01**, Europe/Moscow.

## Стадия и авторизация

Memory Bank принят как source of truth. Пользователь разрешил автономный архитектурный проход: реализацию, тесты, локальные инструменты, коммиты и push в существующий `origin/main` при доступной аутентификации. Реальные деньги, торговые транзакции и оплата внешних сервисов не разрешены.

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
- Make tooling, тесты и CI definition для Python 3.11/3.13. Состояние удалённого CI не следует путать с локальной проверкой.

Команды и карта модулей: [README](../README.md). Точные ограничения: [contracts](../docs/contracts.md). Проверка источника: [source audit](../docs/source-audit.md).

## Что проверено

Локально на Python 3.13: 67 автоматических тестов, Ruff lint/format, strict mypy, полный CLI smoke с повторным ingest/export и двумя побитно одинаковыми replay results, сборка sdist/wheel. Synthetic fixture имеет отрицательный net-outcome, cash сохраняет капитал. Эти тесты проверяют бухгалтерию и временную семантику, не рыночную прибыльность.

## Чего нет и чего нельзя утверждать

Нет qualified historical/live market dataset, полной исторической account-state reconstruction, production collector, empirically calibrated execution, neural training, MPC/RL, paper/live bot и доказанного OOS edge. Ни один рыночный E01–E14 не проведён. Эмпирический forecast baseline тестировался только как reference implementation.

ReferenceReplay намеренно synthetic-only, one-quote, one-pool-per-base и serial-pending. Snapshot replay имеет контрфактуальное ограничение own impact; per-order/cumulative budgets не устраняют его. Данные пока обрабатываются in-memory; полного Solana throughput benchmark нет. RPC receipt сегодня не является временем доступности исторического события.

## Открытые решения

Backbone, размер, temporal format, scanner/K, cross-token/graph, targets/horizons/frequency, sizing, MPC/RL, источники, капитал, primary currency и реальные risk/promotion thresholds остаются открытыми. Числа `configs/synthetic.toml` — инженерный fixture, не утверждённые финансовые параметры. Актуальный реестр D01–D23: [decisions](decisions.md).

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

## Следующий крупный практический этап

Bounded forward-capture pilot: заданное окно discovery, durable cursor/gap recovery,
реестр обнаруженных и закрывшихся пулов, account checkpoints и последующая сверка с
backfill. Проверить реконструкцию интервала между checkpoints и оценить необходимые
Token-2022 extensions. До объяснения пропусков и reconciliation residuals не переходить
к ML dataset или к заявлению калиброванного исполнения. Оплата/недоступные credentials
требуют пользователя; обычные технические решения разрешены.
