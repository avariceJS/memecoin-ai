# B11 — архив, учебная выборка и фазовый цикл Brain

## План до новых RPC и dataset snapshot, 2026-10-04

Сохранить B09 source/audit и B10 paper/train/UI до первого scheduled refresh; не менять их
runtime, quotas, expiry, models и temporal gates. На старте B11 paper/source healthy, около
61k bars, новых B10 incidents нет. Следующий daily fit не раньше 2026-10-05 13:41:54 МСК.
F01/D43/holdout не читать. Реальные сделки, signing и paid подключения запрещены.

Выбор: archive-first с фазовым приоритетом тяжёлых задач, а не полным выключением остальных
сервисов. Сейчас training занимает десятки секунд/сутки, поэтому само распределение CPU
не основной bottleneck. Проверить потери archive→rolling7d/cap28k→context8→mature→split,
концентрацию по рынкам/датам, revisions, sampling bias и actual prospective coverage.

Реализовать offline immutable учебный snapshot из consistent SQLite backup существующего
GT store без GET/redownload. Полный archive bundle для существующего D44/B01 trainer,
отдельный воспроизводимый coverage/selection report и auxiliary bar-path targets:
close-to-close realized volatility, downside относительно origin по будущим closes,
future observed dollar volume. Это price-proxy labels, не исполнения, intrabar path,
историческая ликвидность или net opportunity. Модели/backbone/runtime пока не менять;
новые targets не подмешивать в старый model contract. Составить следующий data/feature
experiment до fits, с global chronological boundaries/purge, train-only transforms,
paired future evaluation и отдельными frozen model versions.

Новая bounded archival qualification Alchemy: два наиболее старых пула из admitted24,
созданных по metadata до2026 (tie address). Это targeted availability probe, не случайная
выборка и не admission dead-token universe. На каждый до2pages getTransactionsForAddress,
full/jsonParsed, finalized, maxSupportedTransactionVersion0, limit100, ascending,
blockTime [2025-01-01,2026-01-01), затем один getTransaction первой полученной transaction
для core cross-check. Максимум5RPC/440nominalCU/120s/8MiB per response/≥1s spacing,
первый error→STOP, без retry или замены cohort. JSON shape и реальные даты проверять;
raw сохранить отдельно, training_eligible=false до semantic qualification.

Это отдельная B11 allocation, не reuse D51/B06. Основание USD0: текущая owner attestation
noPAYG/no card и неизменный credential fingerprint. Свежий Alchemy MCP usage4480CU/$0,
dataThrough17:06UTC немонотонен относительно прошлых отчётов: не измеренная стоимость,
не verification plan/остатка. MCP/runtime/credentials различать. Helius setting существует,
но access/no-paid/account mapping не проверены; RPC Helius не выполнять. GT production
limiter не обходить параллельным backfill. Никаких новых GT acquisition в B11.

Крупный следующий collect запуск — после квалификации источника/семантики и отдельного
фиксированного envelope с полезными rows/covered pool-days на CU/GET, overlap cache, cursor
и resumable range journal. Готовность 2025–2026 не заявлять только по наличию archival RPC.
Исторический список ныне умерших pools требует birth cohorts из прошлых windows; текущий
trending universe не делает такого списка. Не заменять прошлую available_ns временем блока.

Проверки: fixtures availability/maturity/gaps/duplicate/immutable snapshot; полный raw audit,
reproduction snapshot и поддержанных labels, runtime hash equality до/после; make check/smoke.
Сохранить evidence, Memory Bank и Git. Два Mac, distributed orchestration и новые backbone
пока не нужны. Следующий этап определяется audit и фактическим archival result.

### Уточнение после реального capacity failure, до training/RPC

Первый read-only snapshot `textbook01` остановился на `B01_dataset_capacity` (>40000samples).
Raw SQLite backup сохранён и sealed со STOP; повторной загрузки сети нет. Archive revision2
использует тот же immutable backup, shards≤4096 decision origins с overlap14past/3future,
чтобы features всех8context frames и targets совпадали с прежним build. Все shards сохраняются;
материализация для существующего trainer ограничена новым явным envelope≤100000decisions.
Это не снятие gate B01 и не изменение frozen B10. Для миллионов examples далее нужен
streaming trainer; не выдавать текущий in-memory trainer за уже масштабированный.

### Исправление preflight до первого RPC

`archive-probe01` не сделал RPC: локальный alias с заглавной B отвергнут конструктором.
Обработчик parent failure имел duplicate `category` keyword; также исправлен. Первый root
sealed со STOP, debit0. Разрешена одна исправленная revision02: та же заранее выбранная
cohort и совокупные5RPC/440CU, новый120s только для ещё не начатой сетевой проверки.
Это исправление локального preflight, не повтор после provider отказа или смена выборки.
