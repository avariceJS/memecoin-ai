# Roadmap с критериями перехода

## Приоритет после D51: независимый Brain track, 2026-10-04

По новому поручению F01 не является критическим путём развития мозга. B01 уже собрал
историю, обучил реальные candidates и запустил bounded continuous collection/training/
shadow. Это ранний exploratory проход R2/R4/R5, не прохождение economic gates R4–R8.
Frozen F01/D43 и reserved holdout продолжают отдельную ветку с прежними правилами.

**B02 — ближайшие дни:** накопить новые часы/дни в активном72h runtime; read-only status,
coverage/revisions/censoring и failure budget audit. Завершить недостающую history
отдельным bounded/versioned планом, расширять universe с admission times и контролем
survivorship. На новых версиях corpus — learning curves, calibration, paired future
shadow и обоснованные encoder/features ablations; все fits/exposures учитывать. Initial
empirical champion не означает отказ от нейросетей; пока они не выиграли validation.

Цель — содержательная Brain v1 и цикл непрерывного улучшения, без обещания качества к
дате. Послеexpiry/failure stop не перезапускать старый цикл и не сбрасывать budget; новая
deployment revision по evidence. Forecasting ещё не portfolio policy; execution-aware
labels/venue support, MPC и promotion требуют собственных исследований.
[План и текущий результат](../docs/brain-development.md).

R0 завершён и принят. Пользователь отдельно разрешил архитектурный проход поперёк будущих этапов: reference-компоненты реализованы, но рыночные gates R2–R8 этим не пройдены. Календарные сроки не установлены: они зависят от источника, качества истории и benchmark.

| Этап | Результат | Критерий перехода |
|---|---|---|
| R0 Memory Bank | Локальный Git, AGENTS, канонические документы | Завершено: пользователь принял базу и разрешил разработку |
| R1 Research specification | Сегмент-кандидаты, цели, капитал/units, risk budget, provenance и план оценки | Неизвестные явно разрешены или оформлены как исследовательские допущения |
| R2 Аудит и сбор данных | Исторический и live sample, manifest, timestamps, births/deaths, quality report | Доступность и ограничения понятны; данные пригодны выбранным горизонтам |
| R3 Ledger и минимальный replay | Один достоверный механизм исполнения, проверки балансов/fills/failures | Ошибки оценены; нет очевидной искусственной прибыли |
| R4 Baselines и первая supervised-модель | Cash, простые стратегии, linear/boosting, небольшой temporal encoder | Есть честная оценка наличия/отсутствия сигнала после издержек |
| R5 Контролируемые архитектурные опыты | Backbone, data format, size, targets, scanner и interactions | Выбор на development periods, без подбора на финальном тесте |
| R6 Портфель и последовательность | Sizing, action-space comparison, MPC | Улучшение сверх фиксированного sizing при одинаковом риске |
| R7 Зафиксированная система и проверка | Walk-forward, stress, uncertainty, закрытый тест | Выполнены заранее заданные критерии либо честный отказ/пересмотр |
| R8 Forward paper | Живой путь данных/решений, shadow comparison, execution residuals | Достаточное независимое наблюдение, объяснены расхождения с replay |
| R9 Необязательные V2 исследования | Graph, joint learning, long memory, RL, расширение сегмента | Каждый блок доказал пользу относительно сильной предыдущей версии |
| R10 Потенциальный live | Отдельный проектный этап | Только новое явное разрешение и конкретные ограничения; сейчас не планируется к запуску |

В R2 можно проводить описательный анализ сегментов. В R4/R5 ранний небольшой эксперимент не требует завершения всей сложной архитектуры. Для режима, где impact велик, R3 может выявить необходимость сузить область применимости до продолжения.

## Состав MVP

- Research specification и версии данных.
- Широкое наблюдение в рамках доступного качественного источника.
- Узкий подтверждённый execution domain, point-in-time features и ledger.
- Обязательные простые baselines.
- Минимальное сравнение небольших temporal моделей, без фиксации TCN заранее.
- Распределительные targets, проверяемый scanner, фиксированный затем ограниченный sizing.
- Независимый риск, временная оценка, журнал всех попыток.
- Сильный sequential baseline по мере появления необходимости и forward paper после offline gates.

MVP не обязан включать все 14 экспериментов сразу. Его задача — получить достоверную оценку edge и основных ограничений. При отсутствии сигнала остановиться на исследовании причин.

## Вторая версия

Point-in-time graph features, дополнительные execution mechanisms, pooling/attention, calibration/uncertainty, partial joint training, MPC с terminal value, champion/challenger retraining. RL только при условиях из [evaluation.md](evaluation.md).

## Отложено без автоматического обязательства

Большие foundation models, полный temporal GNN, full all-token attention, MoE, distributed training двух Mac, субсекундная торговля без подходящей инфраструктуры, социальные сигналы без качественной истории. Их можно рассмотреть при новых доказательствах.

## Недопустимые исследовательские практики

Будущие данные в pretraining/features, survivorship selection, прибыль по неисполнимой цене, исключение катастрофических исходов ради метрик, оптимизация на финальном тесте, обход risk limits, автоматическое promotion по максимальному PnL, обещание гарантированного улучшения от continual learning.

## Обновление R2 — bounded audit

Первый диагностический Raydium CPMM adapter проверен на реальных данных; CLI и raw
regression captures реализованы. R2 market gate не пройден: coverage, births/deaths,
полная реконструкция исторических состояний и независимая сверка остаются открытыми.
Следующий practically scoped pilot — forward collection с gap recovery и backfill
reconciliation. Цифры и отказы не дублируются здесь: см. docs/source-audit.md.

## Обновление R2 — forward pilot

Ограниченный fixed-cohort сбор и recovery проверены: интервальная реконструкция classic
SPL vault amounts совпала с endpoints. Это не полное protocol state и не passing всего R2.
Следующий gate — protocol transitions/fee counters и независимая полнота источника.
Измеренные результаты и ограничения: docs/forward-pilot.md.

## Обновление R2 — экономические переходы CPMM

Conditional quote math, fee counters и internal LP supply проверены на classic-SPL
swap интервалах. Это частичное продвижение R2/R3, не завершение source/execution gates.
Следующий этап: Token-2022 whitelist semantics и mainnet coverage creator/liquidity/
collection; config-history и независимая полнота источника остаются зависимостями.
Факты: docs/protocol-audit.md; текущий статус: state.md.

## Обновление R2 — ограниченный Token-2022

Allowlist, transfer fees, creator fees и harvest-to-mint проверены на новом bounded
mainnet pilot. Полные config/mint history, epoch boundaries, редкие liquidity/collection
переходы и независимая source completeness остаются открытыми. Следующий сбор следует
ориентировать на эти операции; масштабирование public RPC не доказано. См. docs/token2022.md.

## Обновление R2 — история параметров

D28–D30 реализованы; source completeness всё ещё условна. Новые bounded интервалы
проверяют зависимости, а LP witnesses — арифметику, не полноценные historical anchors.
Следующий практический gate: синхронный ограниченный сбор с независимой сверкой
провайдера, измерение задержек/объёма и targeted rare-operation coverage. До этого
не переходить к ML/backtest claims. Детали — docs/parameter-history.md.


## Текущий приоритет R2 → R3 после закрытия второго RPC (D33–D34)

Пользователь принял текущую сверку как достаточную для исходной задачи второго RPC;
полная historical trace-сверка всей сети не блокирует дальнейшую работу. Технические
ограничения в docs/helius-audit.md остаются, blanket market qualification не объявлена.

Следующий крупный этап: воспроизводимый economic state dataset → conditional quote/
roundtrip checks → causal observation/feature/target contract → bounded forward corpus.
Это использует уже собранные журналы, а не продолжает RPC comparison как самоцель.
R4/ML остаётся за gates доступности, покрытия, симуляции и temporal evaluation.
Первый результат реализован и воспроизведён на четырёх frozen journals:
16 intervals / 1 921 states. Контракт: docs/economic-dataset.md. Следующий результат —
causal observation/features и gap masks перед bounded forward sample.


## D35 выполнен как bounded инженерный этап

Причинный observation/feature contract и gap masks реализованы и проверены на реальном
сборе, остановленном на timeout: 31 snapshot, 33 features, corrected target endpoint
semantics. Сам sample слишком короткий и неполный для R4. Следующий практический этап —
возобновляемый collector с более длинными bounded prospective segments и явными gaps;
затем executable/size-conditioned label contract с execution uncertainty. Текущие
observed-price targets служат temporal QA, не обоснованию входа в ML.

## D36: реализация завершена, расширенный сбор ограничен качеством источника

Segment resume и execution-conditioned snapshot targets реализованы; 115 valid snapshots,
234.789 s суммарного coverage, два rejected context/Clock mismatch. После одного
consistency probe ошибка повторилась; дальнейшая сеть остановлена. Следующий шаг —
точечное устранение current-checkpoint bank inconsistency или квалификация другого
доступного coherent source; затем длительный prospective corpus с неизменёнными guards.
Это относится к CPMM data correctness, не к закрытой whole-chain trace задаче.
Execution labels пока conditional pre-network-cost outcomes с unknown inclusion/impact;
calibrated simulator и ML gates открыты. Evaluation period фактически не достигнут.

## D37: current bank coherence обойдён, economic layout gate остаётся

Public RPC даёт coherent Clock/context в восьми новых account responses; это bounded
кандидат, не production source qualification. Helius root cause неизвестен. После
шести preflight passes новый prospective сбор остановлен: неизвестные bytes AmmConfig
у двух configs не поддержаны pinned/current source. Длительный corpus не завершён,
R4/ML не начинается. Следующий этап — deployed config semantics/source compatibility
или подтверждение source defect, затем независимо проверенный versioned adapter.
Не игнорировать padding и не менять pools до случайного прохождения guards.


## D38: config gate решён для snapshots; следующий gate — доступность source

Семантика creator fee share подтверждена official docs/SDK и двумя provider observations
в разных slots. Snapshot/quote profile2 реализован без расширения legacy reconstruction.
Новый сбор возобновлён, но два сегмента подряд прерваны timeout public RPC: accepted2,
usable0, mature0. Сеть остановлена по плану; dataset/ML readiness не достигнуты.
Следующий практический этап: отдельная ограниченная диагностика доступности coherent
source при новых основаниях, затем immutable prospective segments на прежней когорте.
Не запускать одинаковые серии снова, не обходить Helius Clock equality. До достаточного
coverage и execution calibration training не начинается. Полный new creator collection
replay — отдельный evidence-driven adapter, не обязательное условие snapshot quotes.


## D39: bounded availability gate пройден, corpus coverage остаётся узким

Canonical public RPC + persistent verified TLS дал 482 accepted snapshots и реальный
restart между сегментами. Один долгоживущий connection оборван; предпочтителен новый
процесс/соединение на сегмент с прежними stop/cooldown/budget правилами. Phase root cause
всех прежних отказов не установлен; production reliability не объявлена.

Coverage QA теперь считает complete scenario decisions и chronological nonoverlap:
345 mature decisions, 141 engineering evaluation, только 15 неперекрывающихся
окон в двух кампаниях/partitions. Следующий крупный прирост — prospective observations
в разных календарных окнах при той же фиксированной когорте/guards, затем execution
uncertainty/selection coverage. Нельзя начинать ML только потому, что пройден один cutoff.

## D40-v2 завершён; D41 выявил cold-connection gate

Matched cadence/coalescing и symmetric stress проверены, WSS resource criterion
не выполнен; optional diagnostic/off. Следующий bounded corpus D41 перешёл на
Alchemy HTTP5 через прежний collector, но остановлен на первом connect timeout.
Новые snapshots0, offline targets/quality/support полностью censored. История sealed,
повторов нет. Документы: [D40-v2](../docs/alchemy-pilot-v2.md),
[D41](../docs/qualified-http-corpus.md).

Следующий этап — детализация DNS/TCP/TLS phase evidence, затем новый preregistered
ограниченный сбор при новых основаниях/в другом календарном окне. Закрытую кампанию
не возобновлять, не повышать timeout и не перебрать провайдеров до success. WSS не
возвращать без конкретной resource/change-discovery гипотезы. Календарное coverage,
market selection и execution uncertainty QA остаются gates перед ML/RL/paper/live.


## D42 завершён: дальнейший приоритет — данные и исполнение

Cold diagnosis не воспроизвела timeout. Выбран bounded Alchemy HTTP persistent TLS;
два разнесённых prospective окна собраны и проверены, WSS off. Transport tuning
закрыт без production SLA. [Итог](../docs/cold-connection-corpus.md).
Далее заранее заданные окна в разные часы/дни (те же guards и конечный budget),
затем read-only наблюдения network fees/receipt-to-chain timing и границ inclusion.
Нельзя считать чужие успешные сделки калибровкой admission собственной стратегии.
Не менять cohort по conditional profits, не начинать ML по нескольким минутам данных.


## D43 выполняется в календарном времени

Local finite schedule уже запущен до2026-10-05: первое окно выполнено,5 pending.
Сначала читать актуальный inventory; не перезапускать completed/reserved windows.
После новых окон — hours/days/gaps/full-grid audit, достаточность market universe и
новое preregistered timing наблюдение до finalization. Первые6 samples already finalized,
они не дают submission→inclusion latency. ML readiness пока false. [Runbook](../docs/calendar-execution.md).


## D44 завершён; независимый D45 не ждёт календаря

Пользователь разрешил engineering-only обучение и ML foundation параллельно D43.
Dataset/training/evaluation/checkpoint/inference и пять сменных encoders реализованы;
15 CPU+1 MPS smoke,40 benchmark cases, exact resume/reproduction,416 combined tests.
Это не продвижение в доказательство market edge; малый corpus не признан достаточным.
D43 не трогать/не переустанавливать. Следующий D45 — registry sealed окон, dedup и
provenance, audit coverage/target support, protocol/global folds/holdout contracts на
fixtures. Затем применить к реально накопленным окнам и принять отдельное решение о
research readiness/нужном sampling. Multi-window registry пока не реализован. Policy/MPC,
net evaluation и full execution calibration остаются следующими самостоятельными gates.
[Документ D44](../docs/ml-foundation.md).


## D45 завершён → D46 research specification/readiness dossier

Registry work/corpus-registry-01/revision000000 принимает новые sealed D43 окна через
ручной offline sync без переустановки runtime. Сейчас1development/5pending,1day/hour;
оба global folds пока train174/validation0/test0, readiness false. Holdout4/5 закреплён
в будущем и не раскрывается.17 registry property tests,419 base/433 combined, exact
wheel reproduction. Дальше независимый D46: scope/universe, target/execution assumptions,
support/uncertainty criteria и model-selection budget; затем формальное решение о
readiness по фактическому corpus, не календарному плану. D43 продолжает свой frozen job.
Использовать D45 status, старый полный D43 inventory может раскрыть holdout labels.


## D46 завершён → D47 runner conformance, не market training

F01 specification frozen до новых результатов: scope/target/selection/CI/coverage rules,
future84day design,9configs×3seeds и114fit budget. Current D45 calendar не может пройти
эти критерии; его holdout не меняется.15new tests/448combined, exact assessment wheel;
launch/release false. D47 — ledger/reservation/exposure, actual fold-aware runner и
paired scoring/bootstrap conformance на fixtures. Затем отдельно будущий sampling binding
и dossier по фактическим данным; никаких автоматически запущенных новых календарей,
research training или holdout releases. [D46](../docs/research-readiness.md).


## D49 завершён → D50 operational activation blockers

Bounded live source прошёл; proposal NOT READY для activation. Engine/binding/ingestion
conformance не заменяет account no-charge/cap proof и production service qualification.
D50: read-only billing identity/budget,supervisor210s/600s,disk/runtime guards,host sleep/
restart semantics; bounded full180RPC live окно после no-charge proof. Затем отдельное
итоговое решение о запуске frozen D48. Нет автоматического переноса дат/backfill или
изменения D43/F01. [D49](../docs/f01-collector-qualification.md).


## D50 engineering envelope завершён → D51 live/activation evidence

Supervisor210s/600s,crash/death/disk/clock/runtime и separate receipt boundary реализованы,
15new/517combined tests и exact fixture/wheel reproduction. New source/service не включён.
Account-specific Free/no-charge/credential mapping отсутствует,UI недоступен,full180RPC
live не проведён. D51 должен получить это evidence, отдельно квалифицировать одно180RPC
окно и host awake/login условия; затем отдельное итоговое решение оcalendaractivation.
Без доступа не выдумывать approval,не повторятьtransporttuning/не менятьF01. [D50](../docs/f01-operational-runtime.md).

## D51 открыт: не переходить к activation без недостающего evidence

2026-10-04. Bounded launcher и host read-only assessment подготовлены;506base/10new tests,
check/smoke successful. D50 runtime неизменён. Provider/owner account Free/no-paid-overage
и exact runtime mapping отсутствуют; MCP27940CU/USD0 недостаточно, browser[]. Запрос
несекретных facts задан, ответа нет. Live180RPC не проведён, source/service не включён.
Продолжение того жеD51: substantive billing review→single bounded receipt→live180RPC→
raw replay/verdict→отдельное calendar activation решение и host operating conditions.
Не делать новую инфраструктурную стадию вместо устранения blocker. [D51](../docs/f01-live-qualification.md).

## D51 завершён → D52 operational calendar activation decision

2026-10-04 owner attestation принят для одной3600CU allocation. Реальный frozen runtime
прошёл180/180RPC,299,34s,174usable/6warmup; raw и installed wheel reports exact.
Никакой второй попытки или transport tuning не нужно. MCPusage nonmonotonic, billing
delta не metric;3600CU nominal,paid0. Actual F01 source/service не активированы.
D52: отдельныйcalendarreceipt/allocation60480RPC/1209600CU,условия питания/awake/login/
monitoring и итоговое решение о deployment staged D50 service. HostAC snapshot не84daySLA.
T2026-10-07/F01/D43/holdout не менять; research остаётся за readiness будущего corpus.
[D51](../docs/f01-live-qualification.md), [evidence](../docs/evidence/f01-live-result-2026-10-04.json).
