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
- Read-only RPC capture/probe без подписи и отправки транзакций; публичный mainnet endpoint успешно ответил на три диагностических запроса.
- Make tooling, тесты и CI definition для Python 3.11/3.13. Состояние удалённого CI не следует путать с локальной проверкой.

Команды и карта модулей: [README](../README.md). Точные ограничения: [contracts](../docs/contracts.md). Проверка источника: [source audit](../docs/source-audit.md).

## Что проверено

Локально на Python 3.13: 45 автоматических тестов, Ruff lint/format, strict mypy, полный CLI smoke с повторным ingest/export и двумя побитно одинаковыми replay results, сборка sdist/wheel. Synthetic fixture имеет отрицательный net-outcome, cash сохраняет капитал. Эти тесты проверяют бухгалтерию и временную семантику, не рыночную прибыльность.

## Чего нет и чего нельзя утверждать

Нет qualified historical/live market dataset, venue-specific pool decoder, account-state reconstruction, production collector, empirically calibrated execution, neural training, MPC/RL, paper/live bot и доказанного OOS edge. Ни один рыночный E01–E14 не проведён. Эмпирический forecast baseline тестировался только как reference implementation.

ReferenceReplay намеренно synthetic-only, one-quote, one-pool-per-base и serial-pending. Snapshot replay имеет контрфактуальное ограничение own impact; per-order/cumulative budgets не устраняют его. Данные пока обрабатываются in-memory; полного Solana throughput benchmark нет. RPC receipt сегодня не является временем доступности исторического события.

## Открытые решения

Backbone, размер, temporal format, scanner/K, cross-token/graph, targets/horizons/frequency, sizing, MPC/RL, источники, капитал, primary currency и реальные risk/promotion thresholds остаются открытыми. Числа `configs/synthetic.toml` — инженерный fixture, не утверждённые финансовые параметры. Актуальный реестр D01–D21: [decisions](decisions.md).

## Следующий крупный практический этап

Квалификация реального источника и первого сегмента: bounded historical/live sample, birth/death coverage, raw-to-pool decoder для конкретной версии протокола, временная семантика, независимые checkpoints, live/history reconciliation и оценка стоимости. После этого — venue-specific replay calibration. Общая инфраструктура для сохранения и аудита образцов уже есть; оплата или недоступные credentials требуют пользователя, остальные обычные инженерные действия разрешены в рамках будущего поручения.
