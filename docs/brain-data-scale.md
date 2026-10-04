# B02 — качество и масштаб данных Brain Development

## План до дополнительного сбора и обучения — 2026-10-04

B01 принят; Brain не ожидает F01. Цель B02 — coverage существующих 12 пулов,
revision/sampling audit, learning curves и paired matured shadow, без новых backbone,
торговли или изменения D43/F01/holdout. B01 runtime/config/universe/model остаются frozen.

Historical acquisition — отдельный append-only root, тот же keyless GeckoTerminal
Solana USD base-token OHLCV/5min/include_empty_intervals=false. Целевая глубина до
10 суток перед anchor начала B02, но не ранее nominal creation bar. Для каждого пула
запрашивать назад от первого имеющегося бара; максимум 24 новых GET/30 минут, до 1000
баров/ответ, paid0. Это дополнение к B01, не сброс его quota: совокупный ceiling12000
requests сохраняется через зарезервированный debit24. B01 не редактировать; B02 прекращает
дополнительный сбор, если total attempts B01+B02≥12000. Для оставшегося B01 budget
операционный резерв24 проверяется заранее против оставшегося календарного ceiling.
В случае невозможности доказать совместимость — новые запросы не начинать.

Shared B01 acquisition lock, только свободная часть текущего уже успешно завершённого
B01 cycle; не менее12s между запросами любого lane, последующее удержание lock12s,
не начинать менее35s до следующей5min границы. Первый HTTP/schema failure или crash
закрывает B02 acquisition, без retry/нового root для обхода бюджета. Уважать B01 STOP,
expiry,401/403/429 cooldown и source bounds, free floor4GiB, B02 root cap256MiB. Пробелы
и недобранные pools сохранять; отсутствие bars не доказывает отсутствие торговли.
Официальная [OpenAPI](https://api.geckoterminal.com/docs/v2/swagger.json): beta, cache1min,
приблизительно10requests/min, before_timestamp, максимум1000rows. Public лимит не SLA.

Merge по earliest actual available_ns, immutable raw/capture SHA, first-observed versions
из B01+B02. Новая история не меняет прошлую online availability или B01 predictions.
Проверять unique pool/time, duplicates/revisions и later receipt; не переписывать data.
Audit: coverage относительно target interval/creation, gaps, event/receipt UTC bins,
число пулов/age/DEX, концентрация rows, revision magnitudes/unique keys, feature/target
support и missingness. Current-ranked universe имеет survivorship bias, deaths/unseen
не известны. Не объявлять bars finalized coherent execution или calibrated costs.

Learning curves: immutable merged dataset, фиксированные global chronological folds
по unlabelled unique decision times: train/validation/evaluation boundaries50/65/80%
и70/85/100%. На каждом fold train — последние25/50/100% его train unique times, nested;
validation/evaluation membership одинаково для трёх размеров. Whole decision maturity
purge на каждой границе; только полные labels; train-only scaler на subset. Historical
retrieval causal cutoff отдельно от retrospective event-time folds; online claims нет.

Те же empirical/linear/GRU(width16,context8), seeds7/19,10epochs, AdamW и losses B01.
24neural fits max, CPU1thread, per-fit195s, общий hard1800s. Все trials/checkpoints,
seed scores, train/validation/evaluation loss и counts сохранять через D47 ledger.
Результаты не подменяют действующий champion и не поступают в B01 model registry.
Основная descriptive метрика pool-macro pinball; также pooled/per-horizon/per-pool loss,
coverage, seed variability, compute. Парный bootstrap по1h temporal blocks, all pools
вместе,200resamples,seed20261004,95% percentile; при<8blocks CI не выдавать. Это
exploratory uncertainty diagnostic, не confirmatory test/multiplicity correction.

Shadow: сохранить snapshot predictions/outcomes до анализа; только outcomes с actual
availability/scored≤as_of и target maturity после prediction. Три candidates на одной
membership, разрезы horizon/model/pool/UTC-hour; pending/censored отдельно, без их
подмены нулями. Проверить source/prediction hashes и пересчитать losses из raw, а не
доверять summary. Новые outcomes не становятся основанием выбора lucky snapshot.

Основание следующего эксперимента: согласованное улучшение при росте train по обоим folds,
seeds и новым будущим временным блокам; при коротком shadow scope стабильность не
установлена независимо от loss. Иначе исследовать sampling/feature scaling/target
heterogeneity, не увеличивать сеть. Не обещать накопление суток за одну сессию;
существующий72h job продолжает календарное покрытие в прежних bounds.
