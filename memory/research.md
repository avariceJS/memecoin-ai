# Реестр исследовательских экспериментов

## 2026-10-06 — matched data expansion, 8 fits

Один consistent source prefix/cutoff; control24pools и broad86pools. Validation264/test246
решений на одинаковых8старыхpools, exact sample/input equality проверена доfits. Прежний
B13-pool-day-v1,48k cap,12features/context8,linear/GRU16,seeds7/19,1000updates. Все8fits
completed,28,64s including snapshots; no retries. Оба выбралиGRU поvalidation; broad
descriptive loss хужеcontrol5m17,14%,15m4,04%,aggregate8,92%. Сложность не увеличена.

62новыхрынка/7987решений/95pool-days admitted, каждыйfit действительно sampled86pools.
20новыхpools<16decisions;72,09%samplingmass выделено новым16,64%строк. Это measuredshift,
не установленная единственная причина regression. Nextablation должна иметь новый protocol
и futureevidence; не делать повторныеfits ради победыGRU нараскрытомtest.
FrozenB13/control/broadprospectiveexam05:51UTC→24h, immutableweights; отдельные20unseen
historicalpoolsзарегистрированыдоfits,получаютсяпослеfreeze,не training/selection/future.
Unseen13pools/924decisions: broadempirical выигрываетpoolmacro; broadGRU неулучшился.
Pooledloss даёт другойвывод; dominantpool66,34%строк. Selection не менялась.
[Протокол](../docs/brain-training-expansion.md), [результат](../docs/brain-training-expansion-results.md).

## 2026-10-06 — forensic и acquisition qualification, без новых fits

OLD/NEW B13 paired descriptive test выполнен отдельно от собственных rolling test scores.
5m улучшился на 3.98%,15m ухудшился на 5.77%; confidence/edge не заявлены.
Исторические acquisition campaigns зарегистрированы до RPC; финальные strict swap reports
имеют суффикс02 после проверки exact instruction length. Приобретение данных не означает
новый ML эксперимент: additional fits0, promotion0, future paper прежнего frozen candidate.
[Forensic](../docs/brain-experience-review.md), [acquisition](../docs/brain-market-vacuum.md).


## 2026-10-05 — B13 preregistered autonomous refit

Рецепт зарегистрирован commit86929cb до новых fits: прежние 12 features/context8,
linear + GRU16, seeds7/19,1000steps,64batch; pool/day balance выбран для diversity.
На snapshot cut, округлённом до5min: train end=cut−6h, val end=cut−3h, test end=cut.
Whole-decision maturity строго до границы fold, actual receipts не позже cut.
Caps48k/6k/6k — deterministic pool/day selection; archive сохраняется целиком.
Validation выбирает backbone до раскрытия descriptive test. Последовательные rolling
tests могут повторно использовать историю и не становятся независимым confirmatory
экспериментом. Future paper не меняет frozen weights, PnL не является training label.
Максимум6 reservations, включая failures, минимум24h и100newmatured между циклами.
Новый реальный snapshot:24train markets/92UTCdays,48k retained; val451,test352,
в test нет unseen рынков. Это изменение состава/разбиений, не matched ablation B12.
GRU выбран validation, но descriptive test не лучше empirical. Promotion отсутствует.
[Протокол](../docs/brain-autonomous-operator.md), [результат](../docs/brain-b13-acceptance.md).

## B12 — выполнено matched curriculum сравнение, 2026-10-04

Sealed B11 dataset, plan68946a5, trainer057d19a;36fits=3arms×3fractions×2families×2seeds.
Seeds7/19,width16/context8,1000updates×64decisions,10validation checkpoints;CPU1thread.
Recent6005/5pools/5days;full/balanced50414/5pools/47days. Полная support≠уникальные draw:
full~36250,balanced~33580, всеfitsувидели5pools/47days. Общий test6027/24pools/2days.
GRU macro pinball0.0290466→0.0273579(full)/0.0268664(balanced), relative5.81%/7.51%.
Full/balanced лучшеrecent и наseen, иunseen, но size/age effects отдельно не идентифицированы.

Full GRU выбран по validation до test(1%tie cbalanced); test не применялся дляперевыбора.
25%GRU subsets местами лучше100%, поэтому доказательства монотонного scaling нет.
В B12future exam все3arms100%и старый champion frozen; новыеoutcomes только послеmaturity.
Не refit на раскрытомtest без нового protocol и отдельно future evidence. [B12](../docs/brain-curriculum-comparison.md).

## B11 — data curriculum до новых model/feature fits, 2026-10-04

Новыхfits0. Полный archive не равен полезной выборке: naive train4pools, rollingtrain5pools.
Прежние rolling7d/cap28k отделены от archive;28k сейчас не достигнут. Sharded corpus и
119 088mature auxiliary horizon labels готовы offline, heads пока не подключены.

Следующий exploratory опыт: recent/full/pool-day-balanced train, общие absolute eval dates,
whole-decision maturity purge, nested deterministic25/50/100% learning curves, train-only
transforms, прежние12features/quantiles и empirical/linear/GRU16,seeds7/19. Конкретный snapshot,
step/walltime budget связывать до fits; данный B11 specification не выполненный trial.
Auxiliary heads/longer features — последующая отдельная ablation, не общий пакет улучшений.
Future candidate сравнивать также по действиям/cash/rejections/cost sensitivity, без promotion
по luckyPnL. F01 остаётся независим. [B11 specification](../docs/brain-archive-curriculum.md).

## B10 — эксплуатационная устойчивость прежде нового model experiment, 2026-10-04

Новых fits/параметров/selection нет. После отказа B09 восстановлен B10 paper с прежними
weights и доступными matured outcomes; F01/D43/holdout не затронуты. Защищена публикация
нового кандидата: completed run и отдельный atomic ready marker после sealing; незавершённый
fit не получает future exposures. Сутки+100new mature samples и прежний fit budget не меняются.
Следующий реальный refresh не раньше 2026-10-05 13:41:54 МСК; будущие paired decisions,
forecast loss и paper behavior оценивать раздельно, без автоматического promotion.
[B10](../docs/brain-operational-recovery.md).

## B09 — живой feed без нового model-selection, 2026-10-04

Новых fits0 в stage; champion empirical прежний. Daily4fits/run≥24h+100newmature, максимум
4runs включая перенесённый B07. Operational горячая cohort6 выбрана по liquidity/quality до GET,
не по loss/PnL. Trainingrolling7days/capacitysuffix сохраняются; historical/fresh observations
не считаются uniformrandommarket. Старые F01gates не смешивать сэтим exploratorytrack.

Новые outcomes созревают в работающем B09 shadow, paperactions оцениваютсяотдельноот forecast
loss. Новые versions получают pairedfutureobservations безавтоп romotion. Дальнейшиесутки
иданныхрежимы важнее backbonecomplexity. [Activation evidence](../docs/brain-hot-lane.md).

## B08 — новые данные без новых fit/promotion, 2026-10-04

Corpus expanded12→24 admitted pools и15 515→18 600 first bars, discovery bias сохраняется.
Это engineering/source qualification; новыхfits0, F01/holdout не использованы. Prepared
trainer остаётся D44/D47, ≥24h+100new matured samples,4fits≤600s, target7days и capacity
suffix≈28k bars с включением всехpools на boundary. Max8runs относится только к НЕ
активированному B08; работающий frozen B07 по-прежнему max3. Champion не меняется.
Сначала восстановление live feed/B09, потом честные следующие сутки/outcomes и paired
model behavior; не выбирать новый backbone по трём старым paper trades.
[Итог](../docs/brain-continuity.md).

## B07 — первый живой paper baseline и регулярные candidates, 2026-10-04

Новый strategic priority — наблюдаемый decision/outcome/training loop, без снятия temporal
integrity. Frozen policy не оптимизирована на paper PnL. Три кандидата каждого immutable
bundle принимают решения на одинаковых observations, с одинаковыми cash/cost/position
ограничениями. Дневные portfolio metrics дополняют paired forecast losses 5/15min;
это exploratory engineering/model development, не confirmatory F01 и не доказанный edge.

Первый automatic refresh завершён:10605 mature decisions,4fits(linear/GRU×seed7/19),
59.87s, source-as-of cutoff доfit; seven-day rolling corpus, chronological60/20/20+
purge/train-onlyscaler. Development selection GRU; champion empirical НЕ заменён.
Новый modeldab00e17… оценивается только на последующих прогнозах/сделках. Далее≥24h
и≥100новыхmature samples, max3runs доsourceexpiry; исходная baseline9ea4a6bd… неизменна.
Paper PnL не target тренировочного loss: новые market outcomes послеmaturity попадают
в следующий dataset; ledger outcomes используются для отдельнойpolicy evaluation.

СледующийB08: многодневный source/data plan, полезная history/universe coverage и
сравнения по полным дням/режимам. Не наращивать backbone и не выбиратьwinner по одному
положительномуportfolio. [B07](../docs/brain-paper-loop.md).

## B06 — actual ABI прежде raw-to-training, 2026-10-04

Newfits0,modelcomplexity0. Independent computation по4B05captures выявила5PumpSwapcalls,
согласованные integer transfers/vault deltas, но publicIDL payload size mismatch иToken-2022
support gap. Всеquarantined. Именаполей event не являются истиной без согласования с
instruction/transfer trace и deployedprofile. Indexed benchmark не проведён:1RPCfailedиз-за
нашейversionconfig, исправлен толькоoffline. СледующийB07 — semantic profile+corrected
bounded indexed probe; до этого не подключатьrawкtraining. [B06](../docs/brain-pumpswap-events.md).

## B05 — data-source qualification перед новым model challenge, 2026-10-04

Приоритет пользователя: приближать unattended широкий corpus/training/shadow loop. Поэтому
linear-winsor challenger отложен, не научно отклонён; новыхfits0, model selection0. Два stale
GT pools согласуются с bounded on-chain last references, lag не доказан.9/12owners PumpSwap
делают verified event decoder лучшим следующим B06, не более сложный backbone. Raw tx пока
training_eligible=false. Исследовать indexed/filtered ingestion cost+coverage после decoder;
vendor и universe size не догма. Старые B03/B04 learning curves неизменны.
Brain track independent of F01, но causal/chronological/matured shadow gates сохраняются.
[Документ](../docs/brain-source-architecture.md).

## B04: train-only winsorization ablation, 2026-10-04

Plan2d057b7 доfits. Sealed B03dataset/folds100%, empirical/linear/GRU, seeds7/19,
width16/context8/10epochs/CPU1. Дваarms:identity иq01/q99clipping непрерывныхfeatures
до прежнегоz; boundedfraction/zero_volume неclipped, missing/targets неизменны.
16developmentfits59,09s/hard600s,8контрольныхweights tensor-exactB03. Transformfit
толькоunique trainframes; отдельныйSHA вcheckpoints; learned thresholds безretuning.

Validationlinear improvement4,33%/0,96%, GRU−8,67%/−2,83%. Frozen criterion:mean≥1%,
ни одинfold хуже>1%. Толькоlinear заслуживает отдельного будущегоshadow; empirical
всё ещё лучшеlinear. Evaluationalreadyexposed иbootstrapexploratory, неF01. Model000000
иchampionнеизменны. NextB05 — новыйpairedfuture challenger experiment приcontinuous
сборе; GRUclipping и усложнениеbackbone не оправданы. [Отчёт](../docs/brain-availability-normalization.md).

## B03: learning curves на расширенной истории, 2026-10-04

Preregistered12583b0, runtime5c0cf67, offline tools32b36e5. Sealed dataset14326decisions,
четыре старых train pools, прежние две chronological folds и nested25/50/100%train.
24 real neural fits64,01s/CPU1, seeds7/19,10epochs; никаких новых backbone/features/targets.
Каждая кривая имеет неизменные validation/evaluation IDs; B02/B03cutoffs различаются,
абсолютные losses между этапами не matched comparison. Fit ledger/weights сохранены.

GRU full-train improvement −1,33%/+6,84%; paired exploratory CI [−24,77%;14,09%] /
[1,02%;13,50%]. Второйfold положителен, но train-size slope не монотонен, первыйfold
не подтверждает, seed7хуже empirical в обоих. ShadowGRUхуже20,8% на145scored variants/
2UTC-hour blocks. Никакого promotion/edge claim; старый champion empirical сохранён.
Max evaluation |z|1425,58 lower_wick — сначала extreme candle/source/feature audit,
затем отдельная train-only robust-normalization ablation. [Кривые](../docs/brain-pool-quality.md).

## B02 выполненный exploratory data experiment, 2026-10-04

Frozen plan8038099, curve implementation8601019:2chronological folds, nested25/50/100%
train times, одинаковые validation/evaluation IDs на каждой кривой,4neural fits/точку,
seeds7/19,10epochs,CPU1. 24real fits18,94s. History enlarged+505bars, но train по-прежнему
1старыйpool, evaluation2/12pools. Две folds перекрываются; epochsfixed, optimizer steps
растут сdata. Нельзя приписывать весь slope исключительно sampling diversity.

GRU ensemble full-train empirical-relative improvement0,92%/4,53%; block-bootstrap CI
[-33,0%;26,9%]/[-9,0%;11,0%],seed7хуже в обоих folds. Shadow aggregateGRUхуже~23% на128
matured variants/1hourblock. Никакого architecture selection/promote. Следующая model
ablation только после data quality/diversity: train-only robust feature normalization,
при прежнемtarget/backbone. Volatility-conditioned targets — отдельный будущий опыт.
[Learning curves и отчёт](../docs/brain-data-scale.md).

## B01: независимое model-development обучение разрешено, 2026-10-04

Новое прямое указание пользователя отделяет Brain Development от frozen F01. Historical
retrospective и регулярное supervised training разрешены без F01 readiness; запрещены
смешение результатов, открытие protected holdout и заявление edge по малому корпусу.
B01 protocol зафиксирован до fit commitbf6bd4a: empirical/linear/GRU, seeds7/19,
5/15min log-price returns, global60/20/20 и horizon purge; train-only scaler. Реальные
4fits проведены, empirical выбран validation pool-macro pinball0.01009019 против
linear0.01690002/GRU0.01126939. Test используется exploratory diagnostic; повторяемые
пересмотры будут создавать selection bias, поэтому не называть его confirmatory.

Новая версия модели каждые≥6h только при новых данных,≤12cycles/72h; immutable ledger,
model cards и checkpoints, selection до чтения test. D44 encoder/loss и D47 ledger
переиспользованы без изменения F01 runner. 4дополнительных installed-wheel fits —
reproduction, не новая попытка выбора модели. B01 не выполняет net-E01–E14.
Следующий эксперимент определяется coverage/shadow/learning curves, backbone не закреплён.
[Протокол и evidence](../docs/brain-development.md).

Все эксперименты ниже имеют статус **не проведён** на 2026-10-01. Это план объективного выбора, а не результаты. Полный декартов перебор запрещать не нужно, но он не является планом: сначала небольшие контролируемые сравнения, затем подтверждение сильных кандидатов.

## Общий протокол

1. Зафиксировать гипотезу, primary criterion, practically meaningful improvement и риск-ограничения до запуска сравнения.
2. Одинаковые point-in-time данные, доступная вселенная и execution assumptions для сравниваемых вариантов.
3. Настройка внутри train/validation; внешние временные folds не используются для выбора параметров. Финальный закрытый тест используется после выбора системы.
4. Сопоставимый бюджет tuning и несколько seeds. Отдельно сравнивать fixed-compute и достаточно обученные модели: одинаковое число параметров не означает одинаковую стоимость.
5. Парные оценки на одинаковых периодах и сценариях исполнения; учитывать временную зависимость, не считать сделки независимыми автоматически.
6. Регистрировать все варианты, включая неудачные. Не выбирать seed по результату внешнего теста.
7. Основная оценка — net-полезность и риск, дополнительно proper scoring rules, калибровка, latency, память, training cost.
8. При неопределённом результате предпочесть простоту или собрать дополнительную независимую информацию, а не объявлять победителя.

Числа ниже — диапазоны исследования. Экономические пороги, статистический уровень и риск-бюджет должны быть заданы в research specification; универсального минимального Sharpe или процента прибыли здесь нет.

## E01 Temporal backbone

**Связь:** D05. **Гипотеза:** локальный backbone может дать сопоставимый net-edge дешевле attention; противоположная гипотеза — удалённые связи существенно полезны.

Сравнить TCN, GRU, небольшой causal Temporal Transformer с одинаковыми features, horizons, heads и физической длительностью доступной истории. LSTM — дополнительный контроль памяти; SSM включить после аппаратной проверки.

Измерить proper forecast loss, calibration, net-результат с общим downstream, p50/p95/p99 всей inference-цепочки при burst load, peak memory и время обучения. Сначала общий формат входа, затем архитектурно подходящие варианты в отдельном эксперименте. Отключить дальнюю историю и проверить её добавочную пользу.

Выбор: модель на устойчивой границе качества и стоимости. Нельзя приписать backbone улучшение от более богатого входа.

## E02 Scaling модели и данных

**Связь:** D06. Сравнить примерно 0.5M, 2M, 8M, 30M поэтапно. 100M — если тенденция улучшения сохраняется. Сначала изменять ширину, сохраняя контекст; длину истории исследовать отдельно.

Для нескольких размеров обучающей истории оценить train loss, временную validation, калибровку, net-пользу, variance по seeds, память и compute. По возможности отделять увеличение разнообразия от увеличения числа перекрывающихся окон.

Underfit предполагается только после проверки оптимизации, информативности признаков и меток. Высокая ошибка train/validation может означать отсутствие сигнала. Падение train loss без будущей пользы не оправдывает рост сети.

Выбор: минимальная модель в устойчивой хорошей области, не максимум одной кривой.

## E03 Последовательное управление и RL

**Связь:** D07. Сравнить один и тот же encoder со следующими контроллерами: однопериодный optimizer, MPC, imitation-initialized recurrent PPO. Одинаковые scanner, risk limits, capital и исполнение. Сперва заморозить encoder; joint tuning — отдельный фактор.

Проверить, улучшает ли MPC использование капитала и выходы, затем даёт ли PPO дополнительный устойчивый net-результат сверх MPC. Рандомизация исполнения в train должна быть правдоподобной; evaluation включает отдельные режимы и стрессы. Не использовать будущие события в planner.

До RL нужны: поддержанный uncertainty-оценкой edge baseline на нескольких внешних периодах, устойчивость к правдоподобным издержкам, достаточно малые execution errors и конкретная последовательная проблема. IQL — альтернативный опыт только при подходящем fixed dataset действий, не из одних цен.

## E04 Способ совместного обучения

**Связь:** D04. Сравнить frozen forecast + optimizer, partial joint decision training, direct policy, независимые модели. Сопоставить доступную информацию, backbone/compute и внешний risk layer.

Измерять итоговый net-результат, калибровку heads, stability, зависимость от execution assumptions, negative transfer. Если полезна joint training, ablate вспомогательные losses. End-to-end и RL — разные оси эксперимента.

## E05 Scanner recall и стоимость

**Связь:** D08. Сравнить простой scanner, табличный learned scanner, shared lightweight encoder всей доступной вселенной. Построить recall–latency–cost кривые для нескольких K.

Возможность определить заранее: доступный токен, заданный размер/latency, замороженная exit policy, минимальный net-outcome и допустимый downside. Не использовать максимум будущей цены как исполнимую прибыль.

Recall@K = вовремя отобранные прибыльные эпизоды / все такие эпизоды в доступной вселенной. Соседние окна одного движения объединять; позднее попадание не считать успехом. Дополнительно profit-weighted recall с ограничением веса экстремальных эпизодов и фактический портфельный результат.

На контрольных блоках оценивать всю вселенную, иначе знаменатель неизвестен. Сравнение относится только к наблюдаемому охвату данных, не ко всем когда-либо созданным токенам.

## E06 Межтокенная информация

**Связь:** D09. Контроли: только token features; +SOL/aggregates; +learned pooling; +sparse/set attention; +graph interactions.

Сначала frozen encoder, затем joint learning. Одинаковое время доступности peers. Проверить инвариантность/эквивариантность к перестановке, stale peers и меняющееся множество. Для графа — перемешанные связи с сохранением базовых свойств как контроль. Сравнить новые токены и периоды миграции активности.

Выбор: добавочный net-edge и риск-контроль после стоимости взаимодействий, не только улучшение fit.

## E07 Action space и sizing

**Связь:** D10. Сравнить сетки 4/8/16 уровней, adaptive grid, continuous target policy, action+conditional sizing, optimizer по одним прогнозам. Представлять одинаковую область допустимых решений, насколько возможно.

Контролировать hold-current-units, pending state, slippage и нелинейные costs. Измерить net-пользу, turnover, sensitivity к малым возмущениям входа, частоту risk override и ошибки исполнения. Шумная высокая точность sizing не считается улучшением.

## E08 Prediction targets и pretraining

**Связь:** D11. С общим encoder сравнить raw return, net executable outcomes, MFE/MAE auxiliary, barrier targets, несколько риск-heads и ranking. Менять одну основную задачу за раз, затем проверять сочетания.

Для action-outcome меток зафиксировать size, execution model, horizon и continuation policy. Сравнить отдельные heads горизонтов и head с входным h; проверить края диапазона и согласованность распределений. Для sequential planner исследовать joint scenarios отдельно.

Сравнить direct supervised from scratch, self-supervised на разрешённом train и внешний pretrained baseline при известной допустимой provenance. Forecast future return — supervised задача даже при автоматической разметке. Внешний pretraining с неизвестной временной границей ограничивает честность исторической оценки.

Главный контроль: forecast improvement превращается в net-пользу на отдельной оценочной execution model. При изменении комиссий проверить переносимость, а не только исходный fit.

## E09 Частота и горизонт

**Связь:** D12. Сначала измерить реальное распределение feed/inference/submission/execution delays. Затем заморозить сигналы и добавить задержки, чтобы получить decay curve net-edge.

Отдельно переобучить на разных decision frequencies и holding horizons. Кандидаты от субсекундных до минутных исследовать только при достаточно точных arrival timestamps. Сравнить fixed cadence и event-triggered пересмотр; частота пересмотра не обязана равняться частоте сделок.

Измерять stale-decision rate, turnover, execution error, net-result и risk. Нельзя честно симулировать 100 ms по грубой исторической временной метке. Интервал 5–15 s не контрольная истина.

## E10 Формат входа

**Связь:** D05, D12. Сравнить 1s/5s bars, quote-volume bars, trade-count bars, raw events и гибрид, ограничив начальный бюджет. Token-volume между активами требует нормализации.

В одном сравнении держать одинаковую физическую историю, в другом — compute. Учитывать закрытие бара, незавершённый snapshot, inter-arrival, no-trades vs feed-loss. Измерить пользу порядка событий внутри бара и дополнительную задержку.

## E11 Wallet и graph information

**Связь:** D13. Последовательность: wallet statistics → soft clustering + fixed features → локальный learned embedding → temporal GNN. Все связи и репутации доступны только из прошлого.

Проверить cold-start токенов/адресов/deployers, связь с размером выборки репутации, удаление/добавление ошибочных edges, ablation identity memorization. Измерить как доходность, так и предотвращённые tail losses. Хорошая link prediction сама по себе не доказывает торговую пользу.

## E12 Выбор сегмента

**Связь:** D14. На development history сравнить возрастные группы и типы пулов по доступности данных, net-возможностям после задержек, liquidity/downside, стоимости simulator support.

Выбрать ограниченный первый торговый сегмент, сохранив широкое наблюдение. Подтвердить результат на следующих периодах. Не использовать финальный тест для выбора сегмента и не объявлять отрицательный результат одного сегмента отрицанием edge всего рынка.

## E13 Симулятор против backbone

**Связь:** D15. Факторное сравнение TCN/Transformer × базовая/улучшенная execution model после появления валидного минимального симулятора. Оценочная модель фиксируется независимо от прибыльности стратегий.

Калибровать на отдельных наблюдениях транзакций, сверять бухгалтерию и размерные котировки. Измерить изменение результатов и ranking стратегий, ошибки fills/fees/delays, устойчивость вывода об edge к диапазону исполнения.

Выбор приоритета дальнейшей работы: устранять execution uncertainty, пока она способна изменить важное исследовательское решение.

## E14 Эволюция V2 и V3

**Связь:** D17. Добавлять по одному длинную память, cross-token, локальный temporal graph, joint training, learned terminal value, policy/RL. После совместного обучения удалять каждый блок и проверять взаимодействия.

MoE только после гипотезы о стабильных режимах и достаточности данных каждого эксперта. Сравнить с обычным ансамблем и простой сетью. Сложность принимается только за воспроизводимую пользу на новых данных.

## Минимальная запись будущего запуска

ID и дата; связанная гипотеза; версия кода; data manifest и coverage; точные временные границы; target horizon и purging; конфигурация; seeds; аппаратная среда; версия execution model; ограничения; все primary/secondary metrics; uncertainty; стрессы; ограничения результата; решение продолжить/отклонить/собрать данные. Тяжёлые артефакты вне Git, конфигурации и отчёты в Git.


## Инженерный результат D40-v2/D41 (2026-10-03)

Frozen matched-cadence controls и symmetric stress отделили WSS overhead от cadence:
adaptive resource criterion не выполнен. WSS optional/off; детали и ограничения —
[отчёт D40-v2](../docs/alchemy-pilot-v2.md). Следующий prospective D41 закрыт на первом
connect timeout, все derived labels censored. Никакое новое обучение/исследование edge
не проведено. Следующая работа — availability observability и календарные окна,
затем execution uncertainty, согласно [D41](../docs/qualified-http-corpus.md).


D42 выполнил bounded cold diagnosis, два заранее разнесённых prospective окна и
conditional execution uncertainty audit; E01–E14 по-прежнему не проведены. Assumed
cost grid не является выбором реалистичного тарифа по profitable результату. Результат
не меняет ML gates; дальнейшая работа — calendar/market coverage и execution evidence.
[Отчёт](../docs/cold-connection-corpus.md).


D43 запустил bounded календарное пополнение и read-only fee/status наблюдения. Это
engineering data qualification, не выполнение E01–E14. Первое окно завершено;5 ещё
pending до2026-10-05. Следующий sampling plan для timing должен давать pre-finalization
наблюдения, сохраняя неизвестный момент отправки и selection bias. Текущую когорту
не менять по observed profits. [Контракт](../docs/calendar-execution.md).


## D44: инженерные ML-опыты разрешены отдельно от market research

2026-10-03 пользователь разрешил ML foundation параллельно frozen D43, не ожидая
календарных окон. Реализованы empirical/linear comparators и сменные MLP/GRU/causalTCN/
Transformer с единым conditional head contract.15 runs (synthetic seeds7/19, real seed7),
3epochs,width16/context8 проверяют только plumbing. Дополнительный1epoch MPS run и
40 actual-device benchmark cases не являются E01/E02 ablation по рыночному качеству.
GRU default и CPU default — инженерные стартовые конфигурации, не selected backbone.
Гипотезы/market experiments выше остаются непроведёнными; текущие labels before costs
и conditional reset не заменяют downstream execution-aware/MPC evaluation.
Следующий независимый D45: multi-window registry, qualification и global protocol/folds;
реальное обучение требует отдельного принятия corpus/protocol, не count threshold.
[Контракт/результат](../docs/ml-foundation.md), [evidence](../docs/evidence/ml-foundation-2026-10-03.json).


## D45 и следующий D46

D45 реализован без самостоятельных training runs: immutable registry, diagnostic
qualification и fixed global folds/holdout. Реально импортировано1sealed D43 окно;
несколько окон/restart/holdout/false-readiness проверены на явно synthetic fixtures,
не на выдуманном многодневном corpus. Source/temporal/execution semantics не ослаблены.
Следующий независимый D46 — research specification/readiness dossier: целевой scope
и universe, target limitations, execution assumptions, поддержка/uncertainty criteria,
model-selection budget и release protocol. Никакое число окон само по себе не включает
research mode. [D45 evidence](../docs/evidence/corpus-registry-2026-10-03.json).


## F01-v1 / D46: первый frozen conditional forecast design

F01 — самостоятельный подготовительный predictive study, не выполнение net-E01–E14.
Scope fixed3pool/CPMM profile2 и existing conditional reset labels; не whole-market
универсальность, не фактическая fill probability. Empirical+linear обязательны;
MLP/GRU/TCN/Transformer width16/64,context8;9configs×3seeds,3outer folds+final selection,
≤114trainable fits. Mean validation loss all seeds,1% simplicity tie rule; no test tuning.
Смена grid/targets/weighting/cutoffs после результата — новая версия и fresh evaluation.

Новый future T/calendar84days:3walk-forward folds и reserved28day final holdout, без
переиспользования D45 holdout4/5. Source sampling binding пока не установлен. Критерии
support/censoring/coverage плюс linked semantic/scope/dependence/runner/exposure review;
числа не гарантируют power. Primary paired daily joint proper-loss improvement относительно
обоих comparators, block2/4 sensitivity, predefined CI width/calibration guards.
F01 не проведён. Текущий assessment false, research launch и holdout release отсутствуют.
[Спецификация](../docs/research-readiness.md), [machine config](../configs/research-f01-v1.json).


## D47: execution foundation F01, без начала исследования

Frozen F01-v1 не изменён. Реализованы runner всей selection/refit процедуры, provenance
результатов, budgets/ledger, exact masks/scoring/bootstrap.54final engineering fits
и27предварительных smoke не являются F01/E01 результатом. Архитектура остаётся
экспериментальной; validation winner на малом corpus не принят как backbone.
Research/admission/holdout release0. Следующий D48 — future source/sampling binding,
sealed development adapter и readiness dossier; не auto collection/training.
[Итог/evidence](../docs/f01-runner.md).


## D48: sampling design материализован, F01 не начат

Неизменный F01-v1 получил future proposal и qualified sealed development adapter.
4×5min UTC-stratified windows/day задают ограниченный estimand fixed cohort;84days
и60480RPC ceiling не гарантируют support/power. Activation/source/quota feasibility
отдельно до сети. Никакого настоящего corpus или training в D48 нет;20new tests и
180final fake raw calls — conformance. Actual empty dossier111blockers; scope/semantic/
dependence/formal reviews по-прежнему отсутствуют. [Итог/evidence](../docs/f01-sampling.md).
