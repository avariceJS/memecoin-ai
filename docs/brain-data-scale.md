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

Refinement до первого GET: D50 supervisor отверг1800s как invalid_child_deadline;
acquisition root/requests ещё не созданы. Используется более строгий hard600s и
cooperative565s, одна попытка; исходный ceiling24GET сохраняется. Это не retry сети.

Уточнение pagination после offline evidence, до новых GET: первый ответ HTTP200
содержит506валидных bars, ровно1на cursor,0после, earliest точно creation floor.
Ошибка `pagination_not_strictly_before` была ошибочным предположением клиента, не
невалидным OHLCV/429. History01 остаётся sealed/failed, повтор этой страницы запрещён.
Разрешена ровно одна continuation revision с parent SHA: принять уже полученную
страницу offline, исключить законченный pool, следующий before=cursor−1. Все12pools,
anchor и aggregate24GET прежние, debit1; новый child≤600s и общий wall≤исходных30min.
Это явное уточнение local stop protocol на установленных evidence, не сброс quota и
не обход provider отказа. Любой новый failure закрывает continuation окончательно.

Operational amendment до нового prospective deployment: B01 capture000087 содержит
конфликтующие OHLCV на одном timestamp (разный open); весь ответ quarantined, исходные
bytes/receipt сохраняются. Frozen B01 corpus повторно валидирует этот poison capture и
ломает последующие циклы. B02 safe_corpus исключает только целиком проверенные market
schema failures; manifest/hash/clock corruption остаётся fatal. Новый ответ в live worker
по-прежнему обязан пройти исходный strict bars parser, иначе cycle failed. Это не
разрешение выбирать произвольный duplicate и не ослабление source validator.

Разрешён versioned handoff только Brain: остановить старый job под lock, сохранить его
STOP/error history, перенести immutable captures/models/shadow с исходными временами и
hashes в work/brain-b02/prospective01. Учесть все прежние attempts и historical debit1,
сохранить исходные expiry2026-10-07T00:30:23.405597Z, ceiling12000,training tickets/max12.
Новый pin/job ai.memecoin.brain.b02, prior failure debit1; первый новый failed cycle
достигает stop budget2. Старый runtime не править. Это исправление доказанного persistent
corpus failure, не автоматический restart/failure-budget reset. Дополнительные расходы0.

Live qualification нового B02 runtime не прошла: первый GET HTTP200 снова содержит
duplicate_bar_in_response (тот же первый pool); request сохранён, source validator не
ослаблен. Offline проверка runtime binding/host/corpus прошла. Новый cycle failed при
prior debit1 достигает stop budget2: prospective01 остановлен, LaunchAgent B02 не
установлен. Старый Brain job остановлен при handoff. Дополнительных GET больше нет;
history02 preflight завершился до root/GET, continuation не выполнена. Общее B02
приращение2GET (одна historical page + один prospective response). Это ограничивает
результат B02, не повод создавать третий collector или сбрасывать quota.

Допустимо offline досчитать mature outcomes существующих predictions по уже полученным
валидным raw. Такие derived outcomes имеют фактическое время данного offline расчёта и
маркер recovery_scoring, не заменяют исходные online records. Reports разделяют
published и recovered membership; новые network observations не создаются.
