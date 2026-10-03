# D43: календарный corpus и наблюдаемое исполнение

2026-10-03. [План](../configs/calendar-execution-v1.json) фиксируется до сети.
Transport tuning завершён. Alchemy HTTP persistent TLS, finalized/profile2 для corpus,
WSS off, прежняя fixed cohort. Никаких QuickNode, D33, подписей/торговли/оплаты или ML.

Шесть окон:0/6/12/24/36/48 часов от immutable UTC anchor при создании расписания.
Lateness≤15min; пропущенное окно записывается missed, не переносится в удобное время.
Каждое окно:2×30×5s, максимум180 snapshots requests; отдельно до12 read-only execution
requests. Общий верхний бюджет1152 RPC, максимум2 failed windows, no hidden retries или
смены источника. Первый failure останавливает окно. Interrupted reservation не rerun.
Новый процесс на сегмент; deadline210s/сегмент и210s/execution audit. Сеть разрешена
только в due окне, общий OS lock исключает concurrent runners. Finite local launchd
job проверяет расписание раз в60s; после последнего окна/stop marker запросов нет.
Если Mac спит/выключен/недоступен, missed остаётся missing data. Не удерживать Mac бодрствующим.

Runtime tools и wheel копируются в локальный pinned каталог с проверкой hashes перед
каждым запуском; последующие правки repo не меняют активный эксперимент. Credentials
читаются в runtime из ignored `.env.local`, не входят в plist, args, reports или snapshot.
Raw вне Git, каждое окно и его QA immutable; общий registry — append-only outcomes.
Остановка: создать `STOP` в корне расписания; это не стирает имеющиеся данные.

Execution sample: три getSignaturesForAddress(confirmed, limit8) без pagination;
объединить signatures и выбрать до6 по slot descending/signature, без фильтра по err.
Три getSignatureStatuses в offsets0/5/15s (без history search), затем до6
getTransaction(finalized,json,maxSupportedTransactionVersion0). Всего≤12 RPC.
Null tx/status сохраняются как missing/censored; не повторять до success. Failed
транзакции не удалять. Signature/slot linkage, fee integer, chronology и raw hashes
обязательны. Meta.fee — observed total transaction fee в lamports, не полная стоимость
strategy roundtrip: tips, rent, pool/transfer fees и FX не включать неявно. Compute units
не являются задержкой исполнения; decomposition base/priority здесь не оценивается.

Первое observed confirmed и первое observed finalized — границы наших наблюдений,
не точные chain transition times. Если с первого poll finalized, transition left-censored;
без finalized — right-censored; null не означает failure/drop. BlockTime — оценка chain
времени в секундах; receipt−blockTime не submission→inclusion latency. Чужие on-chain
транзакции представляют включённую выборку, не знаменатель dropped/unsubmitted действий.
Никаких calibrated inclusion/admission, собственных signatures, net PnL labels или
замены existing targets этим audit. Наличие адреса пула не доказывает swap: transactions
классифицируются только как address-related, без расширения protocol adapter.

Official references: [getTransaction](https://solana.com/docs/rpc/http/gettransaction),
[getSignatureStatuses](https://solana.com/docs/rpc/http/getsignaturestatuses),
[getSignaturesForAddress](https://solana.com/docs/rpc/http/getsignaturesforaddress),
[fees](https://solana.com/docs/core/fees). Seed null; source/config/plans/cutoffs/actual
receipt bounds и raw manifests входят в evidence. Настройка стратегии/ML не проводится.

Достаточность corpus: отдельно считать completed/missed/failed windows, реальные UTC
hours/days, usable coverage, full-grid/nonoverlap, отсутствующие regimes/universe и
execution unknowns. Шесть пятиминутных окон — пополнение engineering corpus, не заранее
объявленный достаточный ML dataset. План обучения остаётся закрыт до отдельного решения.
