# Оценка модели и управление версиями

## B10 — crash recovery и реальные gaps вместо заявленного uptime, 2026-10-04

Исходный supervisor_guard −9 не имеет достаточной диагностики для установления root cause.
300+300 успешных read-only guard проверок не доказывают устранение причины. Full replay
85 исходных ticks exact; новый root продолжает ledger с теми же cash/positions/policy.
SIGKILL после commit проверен отдельным настоящим subprocess fixture; это engineering
failure injection, не намеренное завершение работающего Brain и не доказательство week uptime.

Отчёт разделяет carried и новые B10 decisions, а paired scores B09+B10 агрегирует только
по опубликованным predictions с реально созревшими outcomes. Простой не заполняется
online availability. Никакого selection/promotion по короткому slice. Training готовность
проверена fixtures, первый настоящий daily refresh ещё ожидается. [B10](../docs/brain-operational-recovery.md).

## B09 — temporal duty-cycle gate и observed soak, 2026-10-04

До GET:20minwindow+180swarmup,≥90samples/span≥900s,≥1fresh80%,≥4fresh60%,каждый hot≥3new
closedcandles,hotgap≤180s,success≥95%,historyprogress,raw/indexexact. ResultallPASS:
103samples,93.20%/78.64%,4candles каждый pool,maxgap130.28s,72/73success. Final0fresh на
отдельномсрезе не скрыт; новоеправилоизмеряет wholewindow,не заменяет B08staticgate.

Realpaperprefix/cash preserved; subsequentticks/decisions/outcomes и replay — B09evidence.
Soakspan и observedhours раздельно; gaps>90s не превращаютсяв uptime. Synthetic72h-shaped
checkpointbenchmark не measured72h. Qualifcount73 не включает последующий rolloutbudget.
649ML/623base tests;primarymetric источника — usable fresh coverage,неодин successfulHTTP.
[Отчёт](../docs/brain-hot-lane.md), [evidence](../docs/evidence/brain-hot-lane-2026-10-04.json).

## B08 — operational gate и воспроизводимость, 2026-10-04

Frozen gate:24 dispositions,≥6fresh наcutoff,≥4new pools withbars, exact raw/index,
migration fixtures. Первая оценка0fresh, вторая5fresh, обе negative;32GET budget exhausted.
Все12 новых pools observed; этот успех не компенсирует провал freshness. Activation не было.

Новыйindex точно воспроизвёл B07 as-of:516market/131forecast joins,43ticks,393decisions,
3exits,6books. Installedwheel exact; totaloldGRU net−2.345041 USD — B07 accumulated history,
не новый эксперимент/edge. Новый UI содержит permodel decision/entry/exit counters; ещё
не deployed. Сравнение по полным новым суткам и measured weeklyuptime не проведены.
616base/642combinedML tests PASS; benchmark single local raw1.488s/index0.191s, не SLA.
[Sealed evidence](../docs/evidence/brain-continuity-2026-10-04.json).

## B07 — проверять виртуальный цикл отдельно от edge, 2026-10-04

20paper tests: causal future fill, no intrabar hindsight, fee/cash invariants, bounded
exposure, stale/quarantine/source pause, TTL, duplicate/restart, immutable ledger/head,
read-only localhost API, повторный launch и supervisor failures. Реальные live решения
и закрытия фиксируются отдельно от fixtures; offline reproduction сверяет все transitions
по внешнему head pin. Synthetic reference execution gate не снимался.

Paired forecasting сравнивается на тех же matured future observations с разделением
horizon и model version. Policy возвращает cash/entry/pending/time exit; калибровки реального
исполнения нет. Daily realizedPnL, отдельноMTM/stale и fees/stress; разная датаактивации
моделей не смешивается в superiorityclaim. Day-over-day данные появляются только после
фактического времени, не дорисовываются из backfill. Автопromotion нет; F01 untouched.
[Live report](../docs/brain-paper-loop.md).

## B06 — raw semantics и published-only shadow, 2026-10-04

23newtests,588base/6skips,614combined; recorded/wheel exact, noRPC/no fits replay.
Real4tx/5calls movementreconciled, protocolqualified0. Это не подтверждение исполнения.
Liveindexedpages0:clientconfig-32015, no cost/coverage result, no retry. B04notmodified.
Snapshot09:42:05:15364bars,185revisions/126keys.324forecasts:290publishedscored+6censored,
28pending;12изpending дополнительноcomputedoffline,302aggregate/16stillpending.
Основноеmacroсравнениеpublished_only empirical0,006229/GRU0,007902/linear0,014785,
3hourblocks/CIнет; champion прежний. Handoff304scored относитсякпозднему времени.

Уточнение старогоhelper: recovery scored_ns равенlogicalsnapshotcutoff, не фактическому
моменту offline publication. Не выдавать это поле за actual online availability. B06
shadow-review.json фиксирует новое conservative actualreviewtime; frozenартефакты не
переписаны,12recoveryнеonlineevidence. Следующее recoverytooling обязано разделить
as-of cutoff иactualscoring/publication time. [B06](../docs/brain-pumpswap-events.md).

## B05 — исходные clocks и очередной descriptive shadow срез, 2026-10-04

Snapshot09:14UTC:15323bars,128revision observations/98keys,4receipt-hour buckets одного дня.
Paired oldmodel000000:220scored/6censored/28pending,3hourblocks, macro pinball empirical
0,006990/GRU0,008152/linear0,015251, CIнет. Raw→mature outcomes exact; новыхfits/promotion0.
Handoff234scored позднее snapshot, не подменяет frozen comparison. Unseen deaths/current
ranking/liquidity bias не сняты. B05 source probe — targeted diagnostic, не representative
sample или latency benchmark; Chhu событие появилось во время sequential probe.
Wheel replay inventory/pages/transactions/ledger view exact; source and wheel noRPC/no fits.
565base/6optional skips,591combined, check/smoke/build. D43/F01/holdout untouched.
[Результаты](../docs/brain-source-architecture.md),
[evidence](../docs/evidence/brain-source-architecture-2026-10-04.json).

## B04: preprocessing selection по validation, 2026-10-04

Два full-train folds B03, exact membership/targets иmatchedcontrolweights. Percentiles
зафиксированы до16fits; transform не видит futurefeatures/labels. Linear merits future
shadow поvalidation(+4,33%/+0,96%), GRUнепроходит(−8,67%/−2,83%). Ниpromotion, ни выбора
удачногоseed/horizon. Evaluationalreadyexposed:linear improvement9,56%/5,65%, paired1h
CI[4,28%;18,29%]/[1,41%;15,09%], exploratory/no multiplicity correction.

Shadow snapshotдоactivation198forecasts,171scored/6censored/21pending, recovery0;
все3старыхкандидата наоднихfutureobservations. Empirical/GRU/linear macro pinball
0,007418/0,008643/0,015252, дваhourclusters→CIнет. B04weights не участвовали.
Wheel transform/weights→scores/bootstrap и raw→shadow exact, replaytraining0.
Operational activationcollector-only неMLreadiness; training branchотсутствует.
[Evidence](../docs/evidence/brain-availability-normalization-2026-10-04.json).

## B03: воспроизводимые exploratory curves и mature shadow, 2026-10-04

24fits, fixed B02 procedure на новом sealed dataset. Global chronological cutoffs,
whole-horizon purge, train-only scaler, identical candidate membership, seeds7/19ensemble.
Двеfolds перекрываются; fixedepochs не фиксирует optimizer steps. Bootstrap200/seed20261004,
shared1hclusters, exploratory CI не multiplicity-corrected. Положительный второйfoldGRU
не переносится на первый и не подтверждает стабильный выигрыш с объёмом данных.

Shadow snapshot08:12:21.712272UTC:170forecasts,151published outcomes=145scored+6censored,
19pending, recovery_scoring0. Все outcomes после maturity, raw replay exact. Триcandidate
на одинаковых observations; pool-macro empirical/GRU/linear0,005534/0,006684/0,013963.
Двачасовыхblocks→CIнет. Это прежнийB01model000000, curvecheckpoints не shadow-promoted.

Installed wheel: dataset/folds/empirical quantiles/ensemble predictions/metrics/bootstrap/
shadow exact, retraining0/network0. 573 combined tests. НиF01, ни reservedholdout не раскрыты.
Источник не прошёл отдельный operational gate8/12→7/12fresh при≥9; assessment не заменяет
неудачу qualification. [Документ/evidence](../docs/brain-pool-quality.md).

## B02: maturation, paired curves и отсутствие устойчивого победителя

2026-10-04. Global folds50/65/80% и70/85/100% unique event times; nestedtrain25/50/100%,
whole-horizon purge, frozenvalidation/evaluation внутриfold,train-only scaler. Original
curve preflight KeyError trial_ids случился доsession/fit; исправлен, outputs сохранены.
24real fits и48fixturefits раздельно. Empirical/linear/GRU,seed7/19ensemble; seed selectionнет.
Pool-macro pinball,paired1hbootstrap200resamples/seed20261004,CIтолько≥8blocks; не
confirmatory/multiplicity-corrected. ИсторическоеfullGRU CIвключает0,seed7хужеbaseline.

Snapshot144shadowpredictions,128mature=96published+32recovery_scoring,16безsupport.
Recovery вычислен01:49:04UTC по реально ранее записанным raw; actual scoring time не
backdated, online records не заменялись. Только1UTC-hour block→CIне выдаётся. Все3модели
на однойmembership; horizon/model/pool/hour и published/recovery раздельно. GRUсовокупно
хужеbaseline,15min частичныйвыигрыш не причина выбиратьhorizon/model. Curveweights
не публиковались; shadow относится кB01model000000. [Отчёт](../docs/brain-data-scale.md).

## B01: exploratory versus confirmatory, 2026-10-04

Brain Development не gated by F01. Его исторические split/validation/test диагностические,
с неизвестной первоначальной online availability; текущий pool ranking даёт survivorship
bias. Initial1791/595/1050decisions не независимая выборка и не power justification.
Empirical выбран по validation; меньшее GRU test loss не меняет это решение задним числом.

Prospective shadow пишет predictions с model hash, actual inference/input timestamps и
квантилями всех3кандидатов до будущего target; outcomes отдельные immutable записи.
Сравнение paired на одних observations, horizons5/15min; missing future bars censor после1h.
Первый срез48predictions/12outcomes слишком мал для заявления победы. Ни token-price
pinball, ни shadow loss не являются net-edge, execution quality или paper PnL.
После recurrent training прежние development test периоды перестают быть закрытыми;
честный последующий контроль — новые ещё не наблюдавшиеся shadow outcomes.

Installed wheel воспроизвёл dataset exact,4model weights tensor-exact,48predictions
с max abs error4.10e-8 и12outcomes exact. Это проверка воспроизводимости, не доказательство
качества модели. F01 holdout/rules/admission остались закрытыми и неизменными.
[Отчёт](../docs/brain-development.md), [evidence](../docs/evidence/brain-development-2026-10-04.json).

## Что является успехом

Воспроизводимое улучшение net-результата на новых временных периодах при одинаковых ограничениях риска и правдоподобном исполнении. Нельзя определять успех одним accuracy, одним высоким Sharpe, несколькими лучшими сделками или положительным paper PnL.

Численные критерии экономической значимости, confidence, drawdown, tail loss и capacity пока не установлены. Они должны быть зафиксированы до выбора победителей, с учётом капитала и реальных издержек.

## Разбиение времени

Development train → validation для настройки → внешние walk-forward-периоды → закрытый финальный тест выбранной системы → forward paper.

В каждом fold воспроизводить фактическое обучение только по доступной истории, включая scaler, feature selection, pretraining, wallet labels, ranking и calibrators. Удалять train-примеры, target interval которых пересекает проверочный период. Дополнительные временные gaps/embargo задаются зависимостями окон и схемой оценки, а не магическим фиксированным числом.

Прошлые входные наблюдения могут законно присутствовать в контексте будущего решения. Это не то же самое, что будущие target/outcome или пересчитанные с их помощью признаки. Миллион перекрывающихся окон не является миллионом независимых наблюдений.

Если test многократно используется для выбора архитектуры, он становится development data. Нельзя продолжать называть его независимым. Статус финального holdout должен быть явным.

## Baselines

- Cash с явно выбранной валютой измерения.
- Простые допустимые торговые стратегии на той же вселенной и с тем же исполнением.
- Линейная модель и gradient boosting на доступных признаках.
- Небольшая supervised-сеть с фиксированным малым sizing.
- Сильный sequential baseline с MPC перед выводами о пользе RL.

Нельзя разрешать сложной модели лучший feed, меньшие комиссии или больше допустимого риска без отдельного учёта. Замерять доступность активов и упущенные scanner возможности.

## Метрики

| Область | Что измерять |
|---|---|
| Экономика | Net PnL, доходность всего капитала, разница с baselines, результат в SOL и USD |
| Риск | Просадка, время восстановления, хвостовые потери, концентрация, exposure и зависимые позиции |
| Исполнение | Quote/fill error, delay, failures/drops, costs, sensitivity к size и capacity |
| Устойчивость | Разброс по folds, режимам, токенам/deployers и seeds; зависимость от лучших сделок |
| Прогнозы | Proper scoring, калибровка вероятностей/квантилей, ranking и scanner recall |
| Эксплуатация | Data staleness, throughput, p95/p99 end-to-end latency, restart/recovery |

Sharpe и DSR — диагностические инструменты, не доказательство будущей прибыльности. Учитывать множественные попытки, тяжёлые хвосты и зависимость. Для uncertainty использовать обоснованные временные блоки/кластеризацию; независимый bootstrap сделок часто некорректен.

Удаление лучших сделок — sensitivity analysis, не автоматический критерий отказа от стратегии: некоторые стратегии естественно имеют асимметричное распределение. Важно понять воспроизводимость и концентрацию результата.

## Стрессы и диагностика

Повышенные издержки, задержки и их хвосты; изменение глубины; пропуски/устаревание данных; неудачные транзакции; отсутствие выхода; рыночные режимы; новые токены и deployers; возмущения графа; изменение размера капитала; выключение компонентов.

Проверять широкую область приемлемых параметров, а не острый оптимум. Риск при тестах остаётся сопоставимым; высокая прибыль от нарушения лимитов не считается улучшением.

## Условия перехода к RL

Нет универсального PnL-порога. Нужны поддержанное uncertainty-оценкой преимущество baseline на нескольких независимых временных периодах, устойчивость к правдоподобным издержкам, достаточно точное исполнение относительно ожидаемого улучшения и обнаруженная последовательная проблема. MPC уже должен быть соперником. RL проверяется на тех же данных, action support и лимитах.

## Retraining и promotion

1. Дождаться завершения меток и проверить качество новых данных.
2. Зафиксировать dataset/config/code/execution versions.
3. Обучить challenger; сравнить rolling window, recency weighting, fine-tuning и retraining from scratch по необходимости.
4. Проверить с champion и baselines на разрешённых временных периодах и стрессах.
5. Провести parallel shadow/paper с одинаковыми feeds и отчётностью.
6. Выпускать только при заранее заданном улучшении и отсутствии неприемлемого ухудшения риска/исполнения.
7. Сохранять предыдущую версию и путь rollback.

Drift — сигнал для анализа, ограничения активности или переобучения кандидата, не автоматическое разрешение promotion. Частота retraining не задана. Обновления после каждой сделки не являются планом.

## Ограничения вывода

Даже несколько качественных периодов не гарантируют будущий edge. Ошибка исполнения, смена рынка и неполнота данных остаются рисками. Если надёжного сигнала нет, пересмотреть данные, сегмент или гипотезу; не усложнять бесконечно модель. Реальные деньги не разрешены этим планом.


## Engineering evidence D40-v2/D41

Transport сравнение не является ML evaluation: matched10s arms разделяют одну feature
сетку, но sequential polls могут читать разные banks. Adaptive — отдельная sampling
policy; снижение requests оценивать вместе с quality и полным standalone payload.
В D40-v2 критерий отрицательный. В D41 connect timeout не дал ни одной зрелой метки;
3 masked rows и24 censored scenarios не увеличивают пригодный размер выборки.
Воспроизведение доказывает integrity pipeline, не market coverage/edge. Ссылки и
provenance: [D40-v2](../docs/alchemy-pilot-v2.md), [D41](../docs/qualified-http-corpus.md).


D42 conditional uncertainty использует integer break-even comparison для additional
roundtrip cost assumptions в input-asset bps. Delay0/5s paired только на том же
sample/pool/direction/size/partition, censored/unfilled не входят в численный return.
Не превращать conditional negative outcomes фиксированной когорты в отрицание edge
всего рынка; суммы scenario PnL не являются portfolio PnL. Два per-window engineering
cutoff не задают global chronological train/test split. [Результат](../docs/cold-connection-corpus.md).


D43 meta.fee является наблюдаемой transaction fee, но не полным экономическим cost
стратегии; own inclusion/admission denominator неизвестен. Шесть первых samples уже
finalized при первом poll и left-censored для transition timing. Не использовать
receipt−blockTime как submit→inclusion latency. Calendar schedule pending windows не
увеличивают sample size/coverage. [Evidence](../docs/evidence/calendar-execution-2026-10-03.json).


## D44: engineering-only training/evaluation contract

Global explicit train/validation/test cutoffs, all scenarios решения в одной partition,
purge по MAX target_available_ns всех siblings; validation-only epoch selection.
Normalization по unique train history frames; доступная прошлая history на validation
разрешена, future labels/IDs/absolute times не входят в model inputs. Censorship masked,
fully censored batches не делают optimizer step. Tests подтверждают неизменность
selected weights при изменении test labels и exact CPU epoch-boundary resume.
Pinball/coverage/class log-loss/Brier +3 temporal descriptive blocks сохраняются с
support counts. Это conditional-label диагностика, не net returns/edge/causal effect
действия. Scenarios/overlapping targets не независимы; significance не вычисляется.
Real smoke validation136 variants все roundtrip; test180/360 censored, failure support
слишком мал. Engineering test повторяемый и не считается protected market holdout.
Research mode закрыт; будущий D45 должен отдельно определить corpus qualification,
global folds/embargo/holdout и model-selection budget. [D44](../docs/ml-foundation.md).


## D45: frozen chronological membership и reserved holdout

Protocol configs/corpus-registry-v1.json фиксирует absolute cutoffs до новых окон:
fold0 train0/validation1/outer-test2,fold1 train0–1/validation2/outer-test3; окна4/5
reserved holdout. Embargo30s, purge целого решения по max(sibling maturity, nominal
horizon end); все scenarios остаются в одной partition. Late registry admission может
добавить IDs только в новой revision, не изменить прежнюю frozen dataset/membership.
Разделение не утверждает независимость folds или overlapping outcomes. Исторический
outer test может входить в validation более позднего заранее заданного fold.

Qualification считает actual temporal breadth, observed span union, unique decisions,
full-grid/censorship/nonoverlap и per-action/class support. Preregistered engineering
floors лишь diagnostics: даже synthetic с all numeric true сохраняет readiness false.
Real revision000000 имеет train174/val0/test0 в обоих folds. Ни один research gate не снят.
Holdout release/metrics/export API отсутствует; metadata/hash checks разрешены, чтение
raw через иные инструменты не может быть автоматически обнаружено. При external exposure
нельзя называть holdout globally unseen; следующий release требует отдельного review.
Для status применять D45 sync/audit, не старый full D43 target inventory. [Контракт](../docs/corpus-registry.md).


## D46: frozen admission/selection/confirmation contract

Forecast admission и economic promotion разведены. F01 conditional scope требует exact
snapshot-label semantics, source/temporal integrity и support; реальные own execution
costs/admission остаются обязательными для net-edge, но не выдаются за известные ради
разрешения conditional prediction. D44/D45 gates не сняты: D46 — assessment, не permission.

Validation-only all-seed selector отвергает missing/failed/duplicate/unknown trials,
NaN и любые test fields. Порог1% выбирает simpler priority; lucky seed не выбирается.
Final refit epochs заранее определены median validation epochs. Параметры/masks/scaler/
IQR train-only. Одинаковое paired support, equal actions→decisions→pools→UTCdays,
masked heads и explicit conditional estimand; censorship не объявлять MAR.

Новый final holdout требует отдельного release после frozen predictions/selection,
не заменяется D45 reserved окнами. Planned97.5% block CI для двух comparators,2% practical
loss improvement,2% max half-width,2/4day blocks и calibration guards. Это критерии
дизайна, не гарантия valid coverage/power. Короткие contiguous day runs/недостаток support
→inconclusive без удаления неудобных дней. Никакого optional stopping/retuning по holdout.
Runner/bootstrap/ledger ещё требуют реализации и conformance D47. [Точный protocol](../docs/research-readiness.md).


## D47: scoring, provenance и exposure реализованы

D46 future runner gap закрыт engineering implementation: train-only scaler/IQR,
all-seed validation selection, selected+comparators-only outer test, fixed refit.
Actions→decisions→pools→days, masks отдельных heads, CE floor+renormalization,
paired ratio-of-means bootstrap2/4days/5000/seed4601,97.5% CI. Missing support/short
runs→inconclusive. Exact offline reproduction из wheel;17conformance tests.
Immutable trial/artifact binding и separate admission/release receipts; durable
exposure BEFORE protected hashing. Crash расходует reservations; completed prediction
report можно пересчитать без raw reopening. Full ledger rewriting владельцем не
исключён: нужен external pinned head. Receipt byte checks не заменяют substantive
review, sampling/coverage dossier и corpus feasibility. F01 не проведён; ни один
research/economic gate не снят. [D47](../docs/f01-runner.md).


D47 final integrity review добавил worker boundary code/data/membership checks.
18conformance tests,466combined, повторные engineering scoring hashes равны прежним.
Финальное evidence: [addendum](../docs/evidence/f01-runner-integrity-2026-10-03.json);
старые outputs/evidence не перезаписывались.


## D48: sealed revision → exact F01 folds/readiness dossier

D47 runner plan материализуется с immutable dataset/revision/sampling/source binding;
его наличие не admission. Membership/role support сохраняют whole-decision purge и
embargo. Coverage denominator включает все заранее planned windows роли; календарные
роли должны закрыться по времени. Failed/missed/pending windows не удаляются; actual
starts unknown, поэтому lower bound usable fraction conservatively uses180 additional
starts/unknown window. Это достаточное условие прежнего90% floor, не estimated actual
fraction. Synthetic-only gate/отдельные reviews остаются закрыты. Holdout labels/stats
до release не оцениваются.20new/486combined tests, raw+dossier wheel reproduction.
Capacity fixture38 976decisions не sample size для статистики. [D48](../docs/f01-sampling.md).

## D51: qualification источника не снимает research readiness

Один реальный engineering window180snapshots прошёл preregistered source/runtime criteria
и exact raw/wheel replay после owner budget attestation.137mature decisions и1440variants
не достаточный независимый ML sample size; conditional snapshot targets не включают
калиброванные network costs/inclusion. Окно вне future F01 source, actualadmission0,
training/release0. Source PASS не research launch. [Evidence](../docs/evidence/f01-live-result-2026-10-04.json).
