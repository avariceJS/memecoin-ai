# B07 — первая живая paper/shadow вертикаль Brain

## Решение и план до activation, 2026-10-04

Новое прямое указание пользователя разрешает виртуальную торговлю и делает product loop
приоритетом. Прежний запрет paper заменён; реальные деньги/signing/transactions/paid services
запрещены. F01/D43/holdout неизменны. PumpSwap B06 quarantine не снимается: для раннего
paper используется отдельный exploratory GT price-bar path. Free quota не экономится
ради календарного ожидания; повторно скачивать уже имеющиеся bars ради paper бессмысленно.

B04 остаётся источником12pools/5min, с его quality/actual availability/STOP/expiry. Новый
paper root/runtime/job читает только этот Brain source. Начальный rollout до исходного
expiry2026-10-07T00:30:23.405597Z; остановка/истечение источника видна в Control Center,
новые входы запрещены, stale позиции не закрываются по вымышленной цене. Продление source
и широкий universe — отдельная operational версия; отсутствие недельного data feed не
скрывается семидневным timer. Сегодня цель — работающая вертикаль, не weekly SLA.

Три виртуальных portfolio на один immutable model bundle: empirical/linear/GRU, $1000 каждый,
long-only, $100 entry notional,≤3positions+pending,≤1наpool. Champion empirical прежний;
егоcash — допустимое решение, не принуждать к buy ради демо. Primary horizon15min,
enter если median predicted log return > log(1.016), q10≥−0.25. Фиксированные параметры —
engineering policy, не оптимизированы на paper PnL. Показывать также wait/rejection причины.

Fill model: первая новая закрытая5min candle с close_time>actualdecisiontime и
available_time>decisiontime, полученная с nominal lag≤300s. Нельзя исполниться по цене,
уже известной до решения. TTL entry10min. Market buy/sell reference close, slippage50bps
и fee25bps на сторону; это неподтверждённые assumptions, не venue quotes. $100 notional
плюс entryfee списываются изcash, qty рассчитывается по ухудшенной цене. Time exit заранее
задан при entry: first fresh available close с close_time≥entryclose+15min. Нет intrabar
stop/takeprofit и исполнения по исторически лучшему high/low. Отдельно показывать gross,
net и stress assumptions; mark-to-market не реализованный PnL и может быть stale.

До activation нужны tests causal ordering, restart/idempotency/accounting/limits, source
staleness/quarantine, data-only projection и offline replay. Затем frozen runtime и один
live job без прав реальных транзакций. Никаких исторических paper trades до activation.
Факт live fills/outcomes ждать и проверять; synthetic tests маркировать отдельно.

Candidate refresh: D44/D47/Brain stack, отдельный daily worker≤900s/run, max3runs этого
rollout, четыреfits(linear/GRU,seeds7/19),10epochs, chronological split/purge/train-only scaler.
Первыйrun сейчас по доступному локальномуcorpus; затем≥24h и≥100новыхmature decisions.
Train7day rolling corpus, только matured price targets; paper PnL не ground truth исполнения.
Исходный champion bundle не заменять; новыйbundle получает отдельные candidate books и
future paired forecasts. Автопromotion закрыт; daily forecast/policy summaries показываются.

UI: обнаруженная отдельная control-center ветка — recorded-only observatory. Core публикует
новый whitelist live-paper projection и отдельную рабочую read-only Control Center панель
на loopback; UI не получает raw/weights/credentials/holdout и не запускаетengine. Старую
параллельную ветку не merge/reset; контракт для её интеграции описать в итоговом документе.

После activation завершить actual live observation→prediction→decision→pending→fill→exit
и replay, насколько позволяет рынок (cash не подменять artificial buy). Опубликовать Git,
Memory и адрес открываемого UI; честно отделить работающие части от pending outcomes.

## Поправка перед повторной activation

Первая попытка paper01: paper worker успешно выполнил один tick; training не начался:
существующий D50 child ограничен600s и отклонил900s (`invalid_child_deadline`).
Обнаружен также контракт exclusive log: каждому запуску нужен уникальный log. Новые
B07 jobs остановлены, incident/root/runtime01 сохраняются. Новый paper02/runtime02
с timeout600s (ужесточение первоначальных≤900s), unique logs и отдельным TRAIN_STOP;
ошибка обучения не останавливает paper. Первый failure прекращает свой lane, без retry
loop. Runtime regression tests проверяют повторный запуск и budget. Правила торговли
не изменены, data source B04 не изменён.
