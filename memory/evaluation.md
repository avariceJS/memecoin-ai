# Оценка модели и управление версиями

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
