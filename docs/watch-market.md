# Watch Market

Дата реализации: 2026-10-06. Watch Market — отдельный single-pool virtual lane в
Control Center. Он не переключает и не вызывает AUTO jobs, не пишет в AUTO paper ledger и
не умеет отправлять транзакции. Axiom служит только местом, откуда пользователь копирует
mint/pool; данные поступают напрямую от GeckoTerminal Public API без ключа.

## Поток

1. В Control Center открыть вкладку Watch Market, вставить Solana mint или pool address и
   выполнить поиск. Provider resolution показывает проверенные пулы; при вводе mint выбрать
   нужный pool, затем выбрать сторону именно этого mint в паре.
2. Start закрепляет текущую champion model manifest SHA и создаёт отдельную сессию с $1,000
   virtual cash, отдельным source journal, shadow predictions и immutable paper ledger.
3. Фоновый worker не чаще одного раза в 60 секунд получает pool price snapshot и OHLCV
   aggregate=5; из OHLCV Brain использует только first-observed closed bars. История
   provider revisions учитывается, но прежняя версия бара не переписывается.
4. Текущий Brain выдаёт 5/15-minute quantile forecasts. UI показывает q10/median/q90 и
   интервал по доступным Brain candidates; отдельный B07 empirical book создаёт решения
   WAIT/ENTER/HOLD/EXIT и virtual portfolio. EXIT использует существующий fixed-time
   accounting, а не совет пользователя или real execution.
5. Stop останавливает только Watch Market. Сессия и журнал остаются для проверки.

## Чтение результата

Provider pool snapshot и цена последнего закрытого 5m бара показываются раздельно с
временем получения. Snapshot может запаздывать; частота API не является биржевым streaming
feed. Для inference и paper fills нужны как минимум восемь баров, свежесть ограничена
существующими Brain/paper gates, а новый сигнал исполняется только на следующем
наблюдаемом закрытии. Конфигурация paper accounting: $100 notional на позицию, 50 bps
slippage и 25 bps fee на сторону; эти допущения не калиброваны на выбранном рынке.

Это single-pool exploratory shadow. Coin/pool может быть вне train support; прогнозы,
uncertainty, сигналы и virtual PnL не являются ground truth, доказательством edge или
оценкой исполнимости. Любая идея использовать derived data как feature требует отдельного
заранее заданного prospective/ablation эксперимента на будущих данных, с provenance,
temporal split и report; Watch outputs сами в train corpus не попадают.

## Ограничения запуска и проверки

- Watch source имеет собственный store `watch-market/`, request budget 8640 и срок не более
  72 часов. При существующем проектном convention operational state находится рядом с
  operator root, а не в Git.
- API paths ограничены provider allowlist, бесплатный/paid budget равен нулю; upstream
  throttling, missing bars, stale data и invalid pools блокируют корректный inference/paper.
- Текущая реализация прошла synthetic tests; публичный provider end-to-end, конкретный
  Axiom mint/pool, browser visual acceptance и Mac 1 rollout ещё не проверены.
- Для перехода к live acceptance нужны конкретный mint/pool от пользователя и owner-host
  release в отдельное разрешённое окно. Перед release прочитать `docs/two-mac-operations.md`;
  не восстанавливать runtime/credentials из worker checkout и не нарушать AUTO, активные
  exams, Pause/no-jobs и rollback gates.

Код: `src/memecoin_operator/watch_market.py`; HTTP/UI: `src/memecoin_operator/web.py`;
tests: `tests/operator/test_watch_market.py`, `tests/operator/test_web.py`.
