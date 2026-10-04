# Реестр архитектурных решений

## B08 — index принят, расширенный rollout отклонён, 2026-10-04

Принят engineering foundation: immutable raw + SQLite derived first-observed/version index,
durable reservations, per-pool quarantine, global cooldown/STOP, frozen source supervisor,
точная миграция paper prefix и отдельные поведенческие показатели кандидатов. Fixtures и
offline reproduction прошли. Менять старые paper trades/cash/policy/models не разрешено.

**Activation отклонена по frozen gate:**32 qualificationGET использованы, итог5fresh/24 вместо≥6;
вторая проверка в оставшихся8GET не заменила первую0fresh. Это operational criterion, не ML
trial. Первые429 сохранились в provider budget; новый STOP и оба qualification artifacts
не удалять. Staged plists не загружены, migration/cutover не выполнялся. B04 STOP сохраняется.

B09 направить на feed continuity с отдельной cadence для live и history; не требовать
полного raw PumpSwap qualification как prerequisite восстановления paper. Не увеличивать
universe ещё раз до проверки нескольких полных freshness циклов и устойчивости scheduling.
Недельная/месячная автономность не доказана; full journal/ledger replay требует load/soak.
[План, amendments и отрицательный результат](../docs/brain-continuity.md).

## B07 — product loop сейчас, strict research и execution отдельно, 2026-10-04

Статус: принято по новому прямому поручению. Следующий шаг B06 protocol qualification
отложен как gate раннего paper. Честно маркированный GT bar-price proxy разрешён для
виртуальной торговли, не для калиброванного execution или F01. Frozen plan9f7bbeb,
engine41eee13, supervisor correction5b77fc3. Не изменять CPMM/features/targets/D43/holdout.

Отдельный memecoin_paper: фактическое время decision, только последующая закрытая свеча
для fill, Decimal accounting, fixed costs/time exit/limits, immutable ledger и replay.
Empirical champion может оставаться в cash. Новая модель получает отдельные futurebooks;
promotion не автоматический. Paper PnL — evaluation политики, не supervised execution label.
Daily refresh использует matured market returns D44/D47, семидневное rolling окно,
четыре fits/run, не более трёх runs в первом rollout. F01 остаётся отдельным закрытым треком.

Первая activation выявила local supervisor deadline mismatch: training не начался,
B07 остановлен; исходные root/runtime/logs сохранены. Исправленная версия ограничена600s
на training вместо≤900s и120s на paper; unique invocation logs, failure stops раздельные.
B04 runtime/budget/expiry не менялись. Новый rollout paper02/runtime02 живой, UI loopback8790.
Источник пока12pools/5min; дальнейшая скорость определяется полезным data diversity,
операционным сроком и честным сравнением, не экономией бесплатной квоты ради ожидания.
[План/результат](../docs/brain-paper-loop.md).

## B06 — fail-closed protocol qualification вместо prefix-only admission, 2026-10-04

B05 принят; автономное движение к unattended Brain подтверждено. PumpSwap выбран по9/12
owners, без model complexity. Engineering23tests/recorded/wheel прошёл. Real4tx/5calls:
transfers/vault deltas consistent, но all events8unconsumedbytes, Token-2022/remainingroles
неqualified, одинeventquote101vsactual104. Решение: raw movement diagnostics допустимы,
production/training admission REJECTED. Не игнорировать неизвестныйsuffix и не считать
pinnedpublicIDL доказательством deployedABI. GT остаётся bootstrap/cross-check; B04 прежний.

Indexed probe остановлен после1RPC/100CU(-32015,нашmissingversionparam), no retry поplan.
Исправление offline не превращает failed qualification вPASS. Availability/costfullpages
UNKNOWN; no source upgrade/paid service. СледующийB07 — реальныйprotocol profile и один
corrected bounded indexedprobe подnewbinding. F01/D43/holdout неизменны.
[Отчёт](../docs/brain-pumpswap-events.md),
[evidence](../docs/evidence/brain-pumpswap-events-2026-10-04.json).

## B05 — разделить роли источников до масштабирования, 2026-10-04

B04 принят; пользователь приоритизировал unattended data/candidate/shadow loop. Это меняет
следующий шаг с linear-winsor challenge на source architecture. Принято: GT сохраняет
discovery/bootstrap/bar cross-check; кандидат primary — independently decoded finalized raw
transactions. Не объявлять Alchemy primary лишь по доступности API и не заменять B04 сейчас.

Один заранее ограниченный probe13RPC/500CU/USD0 прошёл без retries; owner inventory9/12PumpSwap,
160references/4hydrated. Старые bars двух stale pools согласуются с последнимиreferences,
поэтому GT lag не установлен. Schema/revisions и bandwidth/quota остаются самостоятельными
причинами не строить единственный market-wide source на GT. Наивный Alchemy per-event HTTP
тоже не масштабируется в30M при предположении1tx/min/pool. Изучить indexed/filtered ingestion,
не подписываться на paid service и не возвращаться к D40 transport tuning.

Новый evidence-only contract не допускает account references в training, revisionsне
перезаписываются, recovery availability не backdated. Health function не равно production
multilane supervisor. B04 frozen stops/expiry не ослаблены; первыеlivehourlyprobes не выпустили
три pools, локальный schema conflict не остановил остальные. Champion empirical unchanged;
learning curves B03/B04 не переинтерпретировать по новой одной shadow точке.

B06 — PumpSwap decoder и bounded economical ingestion qualification; затем qualified corpus,
chronological training и future paired comparisons. User goal — длительная автономность,
но B05 не объявляет её достигнутой. [Документ](../docs/brain-source-architecture.md),
[evidence](../docs/evidence/brain-source-architecture-2026-10-04.json).

## B04 — новый collector-only контракт и bounded preprocessing experiment, 2026-10-04

B03 принят пользователем. Plan2d057b7 заранее разделил source availability logging и
fresh-universe/model qualification. B03 gate≥9 не снижен и решениеREJECTED не изменено.
Новый scope допускает запись всех12dispositions при valid support и прежних failure/
quota/quality guards; stale activity остаётсяunknown. Training запрещён в новомruntime,
старый model000000 shadow сохраняет actual availability/≤300s gate.6cycles без event
progress→STOP. Original12000/expiry сохранены, probe counters/due унаследованы.

Два cycles прошли:9valid/7fresh/2stale/3skipped каждый, failures0. Отдельный substantive
review exact root/runtime/plist/cycle seals разрешил `ai.memecoin.brain.b04`. Наactivation
153GET общегоdebit, paid0; qualified collector не являетсяresearch/promotion допуском.

16fits,8controlweights exactB03. Train q01/q99clip+z помог linear validation4,33%/0,96%,
но ухудшилGRU8,67%/2,83%. Заранее заданный validation criterion допускает толькоlinear
к отдельному будущему shadow challenge; no promotion. Empirical champion сохранён,
raw extreme bars не удаляются как неудобные. B05 — prospective coverage и этот challenger,
не более сложныйbackbone. [Документ](../docs/brain-availability-normalization.md),
[evidence](../docs/evidence/brain-availability-normalization-2026-10-04.json).

## B03 — pool isolation квалифицирован, continuous activation отклонена, 2026-10-04

B02 принят пользователем, продолжен B03. Plan12583b0 и runtime5c0cf67 ввели только known
schema quarantine целого ответа/пула, durable hourly probes ≤3 с двумя последовательными
fresh successes для release, прежний denominator12 и global provider/integrity stops.
Raw/first availability и старые STOP неизменны. Full inventory до GET выявил два seed
conflicts вместо одного; этот refinement записан до сети. New live conflict FXg… стал
третьим quarantined pool; isolation позволил закончить остальные запросы.

**Решение по frozen qualification: REJECT continuous activation.** Fresh8/12 и7/12 при
gate≥9, не устанавливать job, не выдавать qualified receipt, не запускать refresh.
Третьего retry нет; B03 root STOP, original expiry/12000 ceiling сохранены,135 debited.
Не пересматривать этот outcome задним числом. Новый collector-only availability contract
может рассматриваться в B04 отдельно, с полной missingness и новым точным binding.

24 development fits на 14326 decisions расширили train diversity до4пулов. Одинfold
GRU положительный, другой нет, shadow хуже empirical; не менять champion/architecture.
Следующий model experiment — заранее зарегистрированная robust-normalization ablation
после extreme-bar/feature audit, не увеличение backbone. F01 не prerequisite Brain;
D43/F01/holdout нетронуты. [Результат](../docs/brain-pool-quality.md),
[evidence](../docs/evidence/brain-pool-quality-2026-10-04.json).

## B02 — data scaling и остановленный Brain source, 2026-10-04

**Решение:** learned superiority не установлено; сохранять empirical champion и
существующие backbones. 24fixed-protocol fits показали улучшение learned loss при росте
train, но оба train состоят из1pool; CI включает0, seeds расходятся, shadow хужеbaseline.
Причина следующего data/features experiment — imbalance и extreme evaluation z-scores,
а не недостаточный размер модели. B02 exploratory, не F01/net-E01 результат.

History probe HTTP200 включал boundary bar; offline diagnosis позволила принять его
без повторногоGET. Continuation plan сdebit1 не выполнился: preflight обнаружил
conflicting duplicate вB01 raw. Явное operational amendment разрешило новый Brain
candidate сwhole-capture quarantine и прежними quota/expiry, не правку frozen B01.
Его первый новый ответ тоже conflict; inherited failure debit1+failedcycle=STOP2.
Принять остановку обоих jobs, failed candidate не активировать. Raw/история сохранены.

Следующий B03: per-pool quarantine квалифицировать отдельно, учитывать больной pool
вcoverage/denominator, не подменять universe здоровой выборкой. Global provider/auth/rate
errors по-прежнему стопают source. Никаких automatic retries/reset старогоbudget.
После новой reviewed versioned activation — coverage и normalization ablation; без
нового backbone/promotion. [Отчёт](../docs/brain-data-scale.md),
[evidence](../docs/evidence/brain-data-scale-2026-10-04.json).

## B01 Brain Development — отдельное решение пользователя, 2026-10-04

**Статус: реализован и bounded runtime активирован.** F01 больше не блокирует развитие
мозга; historical backfill/universe expansion/continuous corpus/supervised training/shadow
разрешены в независимом model-development track. Прежние ограничения smoke-only заменены
только здесь. F01/D43/holdout, trading/paid restrictions не изменены.

Первый source keyless GeckoTerminal, forecast5/15min log price returns,12пулов/три возраста.
Причина: доступная история и широкая market-data вертикаль без выдачи bar observations за
coherent CPMM execution. Слабости: current-ranking survivorship, cached/revisable candles,
неполный history и неизвестная historical online availability. Модель experimental:
empirical,linear,GRU×seeds7/19, validation-only selection; initial победил empirical.
Ни learned edge, ни superiority backbone не установлены; portfolio/MPC остаются будущими.

Самостоятельное activation решение принято в рамках нового прямого разрешения:
отдельный job72h/12000GET/paid0,≥12s/request,5min collection,≥6h retraining≤12cycles×4fits,
hard240/600s workers,STOP/disk/clock/code guards,2consecutive failures stop. Это не
повторное использование scoped Alchemy D51 receipt и не активация D48 calendar. Новые
AlchemyRPC0. Frozen runtime не редактировать на месте; renew/расширение — отдельная
versioned инженерная оценка в разрешённом track. Ветка D52 остаётся независимой.

[План/результат B01](../docs/brain-development.md),
[evidence](../docs/evidence/brain-development-2026-10-04.json). Следующий B02: расширять
coverage, недостающий backfill и paired shadow evidence, сравнивать candidates/learning
curves; не ждать F01 календаря и не ослаблять causal/execution constraints.

Зафиксирован 2026-10-01. Текущий статус отражает последний критический review, принятый пользователем как база. Рыночные сравнения E01–E14 не проведены; инженерные проверки и реальные data-аудиты отмечены отдельно. Связанные сравнения описаны в [research.md](research.md); эволюция — в [changes.md](changes.md).

## D01 Цель и независимость от LLM

**Статус:** принято, высокая уверенность в постановке. Оптимизировать вероятность достоверного будущего net-edge, а не сложность AI-проекта. Собственная PyTorch-модель, без зависимости от LLM в runtime. Codex — инструмент разработки.

Причина: языковая модель не является необходимой для численных рыночных решений. Ограничение: исключается часть потенциального текстового сигнала в первой версии. Пересмотр: отдельный доказанный вклад социальных/текстовых данных, не замена всей системы LLM.

## D02 Память проекта

**Статус:** принято для текущего масштаба. Markdown, ссылки, ID и Git; без GraphRAG. Причина: маленький изменяемый корпус, прозрачность и доступность в новой сессии. Слабость: ручное поддержание связей. Пересмотр: измеримые пропуски retrieval при большом корпусе отчётов. Граф кошельков — отдельная тема.

## D03 Ресурсы и этап

**Статус:** принято. Два M4 Pro по ~24 GB, long-only без плеча, без реальных денег. Архитектурная реализация разрешена новым поручением. Для будущего живого контура предпочтительно разделить сбор/paper и обучение/replay по двум Mac. Слабость: ноутбуки не гарантируют непрерывность сбора. Пересмотр ресурсов — после benchmark и выявления bottleneck, не заранее.

## D04 Проверяемая модульность

**Статус:** принятое направление; превосходство экспериментально. Shared encoder + heads + portfolio control + независимые risk/execution. Причина: локализация ошибок и явные ограничения. Слабость: промежуточные targets могут ограничивать оптимальность. Альтернативы: end-to-end, независимые модели. Долгосрочно допускается partial joint training. Проверка: E04.

## D05 Temporal backbone

**Статус:** определить экспериментом. TCN больше не основной выбор по умолчанию. Сравнить TCN, GRU, небольшой Transformer; LSTM и SSM по обоснованной необходимости. TCN удобен для локальности, но теряет удалённые связи; GRU сжимает память; attention стоит дороже; SSM зависит от kernels. Проверка: E01, E10.

## D06 Размер модели

**Статус:** определить экспериментом. Прежние 1–5M не требование. Scaling от ~0.5M до 30M поэтапно; 100M только при продолжающейся пользе. Причина: число сырых событий не равно независимым наблюдениям. Слабость малого размера — underfit, большого — variance и стоимость. Проверка: E02.

## D07 Последовательность и RL

**Статус:** принято, RL отложен до оснований. Forecast + однопериодный baseline недостаточен как окончательное решение; проверить MPC. RL необязателен, возможен recurrent PPO в хорошем симуляторе после imitation. IQL — только при подходящих logged trajectories. Слабость MPC — ошибки сценариев и terminal value; RL — sample cost и exploitation. Проверка: E03.

## D08 Scanner

**Статус:** стартовая гипотеза и эксперимент. Лёгкий encoder всей доступной вселенной — равноправный, возможно предпочтительный вариант против простого scanner. Не фиксировать top-K 100–300. Причина: избежать пропуска слабых ранних сигналов. Слабость: цена сбора признаков и обновлений. Проверка: E05.

## D09 Cross token контекст

**Статус:** определить экспериментом рано. Агрегаты — контроль, а не финальное ограничение. Learned pooling, sparse/set attention, граф — кандидаты. Причина: возможны миграция капитала и групповые движения. Слабость: нестабильные корреляции и ошибки связей. Проверка: E06.

## D10 Action space и sizing

**Статус:** target position — инженерное предпочтение; детализация экспериментальна. Небольшая сетка сначала, continuous/adaptive optimizer позднее. Сохранять hold-current-units и pending state. Альтернатива action+conditional sizing допустима, независимые противоречивые heads нежелательны. Слабость сетки — quantization; continuous — дрожание. Проверка: E07.

## D11 Prediction objectives

**Статус:** принято направление, состав heads экспериментален. Рыночные прогнозы + action-conditioned net outcomes с фиксированной continuation policy; калибровка рисков. Слабость net-target — ошибки симулятора. Альтернативы raw return, MFE/MAE, barrier targets, value. Горизонты не зафиксированы. Проверка: E08.

## D12 Частота и формат времени

**Статус:** определить экспериментом. 5–15 s не принятое значение. Разделять получение данных, обновление модели, пересмотр решения и исполнение. Гипотеза: периодический + событийный пересмотр, multi-scale bars + короткая event-ветка. Слабость hybrid — сложность. Проверка: E09, E10.

## D13 Wallet и deployer

**Статус:** стартовая гипотеза. Point-in-time мягкие связи → fixed graph features → локальный embedding, прежде большого temporal GNN. Причина: контролировать ошибки entity inference. Слабость: fixed features теряют сложные связи. Нельзя считать общий funding доказательством одного владельца. Проверка: E11.

## D14 Рыночный охват

**Статус:** принятое направление. Собирать шире, чем первая область достоверного исполнения. Начать глубокую симуляцию с ограниченного сегмента, конкретный сегмент выбрать по данным. Исключение первых мгновений не универсальное правило. Слабость: edge может находиться вне первого контура. Проверка: E12.

## D15 Приоритет исполнения

**Статус:** принято, высокая уверенность при материальной ошибке. Улучшение симулятора важнее перехода TCN→Transformer, если неопределённость исполнения меняет вывод об edge. Слабость: бесконечное усложнение симулятора. Пересмотр при малой остаточной ошибке и доказанном model bottleneck. Проверка: E13.

## D16 Оценка и выпуск модели

**Статус:** принято, высокая уверенность. Временные splits, purging зависимых меток, walk-forward, закрытый тест, paper; одинаковые ограничения, baselines, стрессы, учёт всех попыток. Promotion не только по PnL; champion/challenger и rollback. Недостаток: больше времени до решения. Это плата за достоверность, не причина отказаться от контроля. См. [evaluation.md](evaluation.md).

## D17 Эволюция без обязательного усложнения

**Статус:** архитектурное направление, конкретные блоки гипотезы. Гибрид временных представлений, interactions и sequential control; partial joint training. Transformer/SSM/GNN/RL/MoE не обязательная лестница развития. Пересмотр и добавление каждого блока — E14 и соответствующие предметные ablations.

## Что не зафиксировано

Нет выбранных API/провайдера, пула, размера капитала, риск-порогов, base currency, K, latency, horizons, backbone, parameter count, reward coefficients, deployment и расписания retraining. Ни один описанный эксперимент ещё не проведён.


## D18 Реализованный локальный фундамент

**Статус:** принято как инженерное решение текущего прохода. Python 3.11+, stdlib runtime, pytest/ruff/mypy, SQLite reference journal, immutable JSONL bundles с manifests и raw provenance. Причина: переносимость и проверяемость без infrastructure overhead. Слабость: bundle/replay in-memory, не полный поток Solana. Streaming/Parquet и специальные базы добавлять после measured bottleneck. Dev tools закреплены в requirements-dev.lock; PyTorch пока не требуется для data/replay contract tests.

## D19 Доказательность исполнения

**Статус:** реализовано и покрыто инженерными тестами. ReferenceReplay принимает только synthetic origin; observed/assumed/unknown availability различаются, неизвестная отклоняется, assumed требует явного opt-in. CPMM reference, rounding, single quote asset, serial pending и cumulative participation — явные ограничения. Нельзя снять synthetic gate и объявить появившийся PnL достоверным без venue adapter и calibration. Исторические snapshots сбрасывают own impact; бюджет лишь ограничивает, но не решает контрфактуальную проблему.

## D20 Контроль исследовательских артефактов

**Статус:** реализовано. Dataset hash, resolved config, source snapshot/hash, Git/environment, completed/failed status и checksums сохраняются для каждого запуска. Label availability, purging, train-only normalization и censoring имеют проверяемые guards. Эти проверки не заменяют независимый OOS протокол. Promotion helper перечисляет blockers по предоставленным evidence flags, не является автоматической сертификацией или deployment.

## D21 Граница квалификации источника

**Статус:** принято. Read-only RPC capture отделён от нормализации и квалификации рынка. Успешный getSlot/getVersion/getFirstAvailableBlock подтверждает доступность endpoint, а не полноту данных и pool reconstruction. Пока нет venue decoder, production feed и исторических receipt timestamps. Следующий практический этап — квалификация конкретного источника/сегмента на реальных образцах.

## D22 Первый протокольный adapter — диагностический CPMM

**Статус:** реализовано и проверено на mainnet account/transaction samples. Выбран Raydium
CPMM из-за доступных исходников и проверяемой структуры счетов. Layout закреплён по revision;
соответствие deployed binary не доказано. Pool/config/vaults/mints читаются одним finalized
checkpoint. Суммы protocol/fund/creator fees исключаются из reserve balances. Это data decoder,
не калиброванная модель исполнения. Источник и мемкоин-сегмент для обучения ещё не утверждены.

## D23 Наблюдение шире поддержанного исполнения

**Статус:** уточнено по реальным данным, не ablation модели. Начальный legacy SPL decoder
оказался слишком узким для обнаруженных CPMM pools: в образцах встречается Token-2022.
Поэтому raw capture и structural extension inventory сохраняют эти пулы; reserve/execution
допуск не расширяется без семантики extensions. Отказ виден в отчёте. Поддержка RPC transaction
v1 добавлена после ошибки -32015 и сверки текущей документации; старые неудачные captures
сохранены. Пропуски нельзя выдавать за отсутствие активности. Доказательства: source-audit.

## D24 Проверяемый forward pilot на фиксированной когорте

**Статус:** реализовано; результаты реального прогона — в docs/forward-pilot.md.
Выбрана ограниченная когорта заранее известных CPMM pools. Это проверка сбора, не universe
мемкоинов и не система полного discovery births/deaths. Finalized checkpoints задают интервал
(start_slot, end_slot]; сканируются адрес пула и оба vault, чтобы не терять прямые переводы.
Курсор и задания сохраняются атомарно; пропуск/ошибка не закрывается по совпадению endpoint
балансов. SQLite journal допускает одного writer и согласованный read-only аудит.

## D25 Уровни доказательности реконструкции

**Статус:** принято на этапе forward pilot; расширено D26. В vault-аудите восстанавливаются classic SPL vault amounts
из pre/post transaction metadata. Порядок внутри слота берётся из getBlock signatures.
Проверяются непрерывность pre-balance и остаточная ошибка на независимом account checkpoint
того же RPC. Это независимый путь состояния, но НЕ независимый провайдер. Pool fee counters,
семантика Token-2022, полнота индекса провайдера и исполнимая цена не считаются доказанными.
Raw receipts дозагрузки сохраняют фактическое позднее время; историческая availability
не восстанавливается задним числом. Архив аудита содержит journal, checksums и исходники
проверяющего кода; не приписывает этот код предыдущим collector sessions.

## D26 Экономическая реконструкция отделена от квалификации исполнения

**Статус:** реализовано; real-evidence coverage ограничен, см. docs/protocol-audit.md.
Точный integer adapter отделён от generic synthetic reference. Стартовое экономическое
состояние берётся только из левого checkpoint; инструкции применяются в runtime/CPI порядке,
сверяются с событиями и token transfers, затем с правым checkpoint. Старый decoder schema
сохранён для воспроизводимости архивов. Quote-match не означает готовность transaction
admission, inclusion, latency или counterfactual replay.

Причина: сумма vault flows скрывает ошибки fee splitting и промежуточного execution state.
Проверка на 189 mainnet tx дала 44 точных swap и 8 совпавших endpoint состояний; две успешных
транзакции оказались no-op для пула. Неизвестные переходы блокируют интервал.
Слабость: config считается неизменным между snapshots; модель не воспроизводит oracle и
epoch, Token-2022 исключён. Creator-fee кандидаты из дополнительной выборки все использовали
Token-2022, поэтому граница не расширена на основании красивого quote match.
Пересмотр: реальные переходы недостающих типов, extension semantics, config history и
независимая source validation. Исследовательские архитектурные гипотезы не менялись.

## D27 Token-2022 через whitelist и версию checkpoint

**Статус:** реализовано; фактическое покрытие mainnet — в docs/token2022.md.
Выбраны mint extensions TransferFeeConfig/MetadataPointer/TokenMetadata и vault extensions
TransferFeeAmount/ImmutableOwner. Неизвестные hooks/confidential/delegate/memo ограничения
отклоняются. Classic decoder не расширен молча: новая immutable pilot spec явно выбирает
семь accounts с Clock и schema 2. Старые six-account archives воспроизводятся прежним путём.

Причина: token inventory недостаточен для исполнения, transfer fees меняют net reserve
credit и net user output; withheld нельзя повторно вычитать из amount. Отдельно сохраняются
тарифы обеих эпох; применяется эпоха снимка, интервал через её границу пока блокируется.
Минус — узкая область, консервативный отказ при изменении mint base и отсутствие полной mint-wide реконструкции.
Обнаруженный на mainnet harvest-to-mint добавлен как отдельный переход withheld без
изменения spendable reserve; это корректировка первоначально неполной модели.
Пересмотр — проверяемая история config/mint updates, epoch-boundary сегментация, реальные
редкие liquidity/collection/harvest операции. Никаких изменений ML-гипотез и live-допуска.

## D28 История экономических параметров

**Статус:** реализовано; реальное покрытие смен параметров пока отсутствует.
Новый explicit pilot mode наблюдает config и mint вместе с pool/vault. Параметры
применяются по порядку instructions; fee activation определяется captured epoch
schedule, проверяемым против Clock обеих границ. Правый endpoint не исправляет
прошлое. Реализованы pinned config updates, setTransferFee и supply changes.
Причина: одинаковые endpoints могут скрыть временную смену тарифа. Старый режим
сохраняет допущение о неизменности. Слабость: стоимость dependency coverage и
неподдержанные admin/extensions; неизвестные переходы блокируются. Расширять только
по source-derived semantics и реальным witnesses, не интерполяцией.

## D29 Альтернативные доказательства покрытия интервала

**Статус:** реализовано, проверено на mainnet и offline. Помимо address pagination,
есть parsed full blocks и bounded getBlocks range (≤256 слотов). В реальном втором
pilot 100 address запросов не закрыли общий config, а 13 range запросов закрыли
весь короткий интервал. Причина изменения: поздний backfill общего адреса вынужден
просматривать множество более новых посторонних tx. Старые cursors сохраняются;
аудит указывает фактически использованное доказательство, pending не равен proof.
Слабость full blocks: объём всей сети; это не выбор production feed. Полнота любого
способа пока условна относительно одного RPC. Следующий gate — независимая сверка
источника и measured sustained collection, не нейросеть.

## D30 Уровни свидетельств редких операций

**Статус:** принято и реализовано. Event-conditioned LP witness отдельно от
checkpoint-anchored replay. 13 реальных LP операций подтвердили арифметику/переводы,
но event prestate не объявляется независимо восстановленным. Внешний withheld
withdraw подтверждает отсутствие изменения наших vaults, не mint-wide bookkeeping.
Причина: исторических pre-checkpoints для этих tx нет. Слабость — частичное покрытие;
пересмотр при накоплении независимых anchors для редких операций.

## D31 Сверка провайдеров — отдельное доказательство

**Статус:** инструменты реализованы; mainnet agreement не получено из-за доступа.
Сохранять полный план exact finalized requests, исходные captures и неудачные попытки;
401/403/429 останавливают серию без fallback. Различать mismatch, unavailable и
incomparable banks. Причина: null results и разные context slots не доказывают ни
полноту, ни расхождение. Независимость оператора проверяется отдельно от alias.
На mainnet PublicNode/OnFinality не вернули данных для comparison; gate остаётся false.
Ограничение: даже совпадение выбранных tx слабее сравнения полного bounded диапазона.
Следующий опыт — доступный второй оператор и block-range audit. Evidence: docs/provider-audit.md.

## D32 Время ответа отдельно от возраста данных и исполнения

**Статус:** измерены bounded read-side характеристики, execution latency неизвестна.
Concurrent confirmed/finalized Clock reads: monotonic HTTP duration, wall receipt-minus-Clock
и polling-dependent slot visibility считаются отдельно. Маленькая выборка и неопределённость
часов сохраняются в интерпретации. Причина: mainnet дал ~250 ms response при ~9.7 s
возрасте finalized Clock; подстановка response duration в execution simulation неверна.
Слабость: нет синхронизации часов, blockhash finality tracking, длительного load sample
и submission/inclusion measurements. Выбор feed/frequency требует следующих измерений;
никакого разрешения торговых транзакций это решение не создаёт.

### D31: уточнение после HTTP 413

Первый настроенный пользователем QuickNode endpoint прошёл getSlot, но отклонил
getBlocks. Теперь любая SourceError/ValidationError останавливает оставшийся план;
это сохраняет budget и позволяет сначала разобраться с причиной, как запросил
пользователь. Старые failed plans не переписываются. Тело HTTP error отсутствует,
поэтому фактическая причина 413 остаётся unknown; следующий сетевой запуск отложен.

### D31: chunked enumeration без поддельных captures

По уточнению пользователя Discover ограничивает getBlocks пятью слотами. Число
слотов inclusive: [s, min(s+4,end)], следующий запрос начинается с previous_end+1.
Сохраняются исходный широкий capture и каждый настоящий узкий ответ; объединённый
список — derived result, не переименованный ответ первого RPC. Полный план фиксируется
до запросов. Partial failure не превращается в missing-slots conclusion; сравнивать
пропуски можно лишь после получения всех частей. Empty chunk допустим как наблюдение
skipped slots, но сам по себе ничего не доказывает. Full-range agreement требует
совпадения enumeration и всех full blocks плюс ancestry внутри диапазона.
Реализация проверена инженерно; новая mainnet попытка остановилась на transport failure.

### D31: диагностика сети и отдельный RPC timeout

2026-10-02 подтверждён документированный free-trial предел 5 slots. Прямые запросы
в sandbox получили dns failure, вне sandbox успешно перечислили весь прежний
диапазон по 5 + 5 + 2. MCP Console API/Tooling Access недоступны на текущем тарифе;
это не отказ самого пользовательского RPC endpoint. Добавлен CLI `--timeout`
(0,60], default 10 s, без автоматических retries. Причина: управлять ожиданием
через штатную команду. Слабость: timeout не гарантирует общий deadline и не исправляет
сеть. 30s enumeration и четвёртый full block при 60s получили timeout; полного
agreement нет. Старому SourceError причину не приписывать. Evidence:
docs/evidence/quicknode-diagnostic-2026-10-02.json. Пересмотр — после диагностики
сетевого пути и успешного полного нового плана.

### D31: Helius как следующий кандидат, явная граница MCP/runtime

**Статус:** принято по прямому поручению 2026-10-02; data agreement ещё не получено.
QuickNode исключён из новых сетевых запросов; его evidence и общая архитектура остаются.
Helius Knowledge MCP отвечает, chain getNetworkStatus получил getSupply timeout.
HTTP/RPC status и причина неизвестны. Direct endpoint отсутствует; MCP-подключение не
считается передачей credentials приложению. Новый endpoint указывается отдельной
MEMECOIN_HELIUS_RPC_URL, чтобы не использовать прежний URL случайно.

Причина: нужен доступный независимый источник и воспроизводимые raw responses.
Выбор: bounded direct plan на тот же диапазон, 12 slots одним getBlocks согласно
официальным docs; реальный допуск пока не проверен. До настройки доступного endpoint
серия остановлена. Слабость: документация не доказывает доступ или completeness.
Пересмотр — после успешного preflight и полной сверки, затем синхронный малый pilot.
Evidence: docs/evidence/helius-preflight-2026-10-02.json; отчёт: docs/helius-audit.md.

Новая comparison schema 2 сравнивает полный block/transaction result, включая
дополнительные поля; одинаково отсутствующие обязательные full-block metadata не
дают agreement. Причина: schema 1 выбирала только часть top-level fields блока.
Старые bundles воспроизводятся прежней schema; их результат не повышается молча.
Новые планы фиксируют transport bounds и останавливаются при первом non-match/error.

### D31: прямой Helius работает, усечённые логи блокируют full agreement

**Статус:** подтверждён реальными ответами 2026-10-02 после настройки endpoint.
Enumeration 12/12 совпал; первый block из 1 310 tx отличается двумя logMessages
arrays с marker Log truncated. Один целевой getTransaction не восстановил trace.
11 оставшихся блоков не запрошены, серия остановлена; full-range agreement false.
Нет основания объяснять обрезку тарифом, клиентским транспортом или конкретной
настройкой Helius. Сжатие не решает отсутствие tail в успешно распарсенном JSON.

Выбор: schema 3 сохраняет полный result comparison, отдельно диагностирует marker
и bounded JSON paths. При одинаковой обрезке статус unavailable, при различии —
mismatch. Старые schema 1/2 не переписываются. Причина: structural presence guard
не различал полные logs и список с marker. Альтернатива отбросить logs отклонена:
это ослабит согласованную проверку metadata и не докажет trace completeness.
Слабость: отсутствие marker само по себе не исключает иной вид неполноты.
Пересмотр — при проверяемом получении полных historical traces через Helius; до
этого синхронный pilot не начинать. Evidence: docs/evidence/helius-direct-2026-10-02.json.


### D32: Helius не проходит полную historical trace-сверку; cohort audit отдельно

**Статус:** принято/проверено 2026-10-02 по новому прямому поручению продолжить и
выбрать альтернативу. Заменяет запрет D31 на любые следующие проверки до получения
полных logs, сохраняя критерии full-range agreement и replay без ослабления.

**Основание:** обе signatures одинаково truncated в getBlock, getTransaction(json),
indexed full history; для первой также jsonParsed getTransaction. Документированный
untruncated LaserStream не поддерживает historical replay. Доступ признан непригодным
для полной исторической trace-сверки этих данных; не делаем вывода обо всех продуктах
Helius или о возможности исправить это тарифом.

**Выбор:** продолжать независимые точные сравнения данных frozen cohort, отдельно от
полной trace qualification. Полные raw и strict comparisons остаются. Scope diagnostic
учитывает readonly references/failed tx и проверяет одинаковые resolved keys. Первые
1 310 tx содержат 0 cohort writers; следующий выбранный по primary earliest active
block 452408963 совпал целиком (1 154 tx, один writer), ancestry совпала. Current
state pair остановлена из-за public RPC timeout; совпадение account state не доказано.

**Слабость:** совпадение одного активного блока не квалифицирует полную историю,
scope dependencies, исполнение или независимость инфраструктуры. Readonly classification
не заменяет full logs для других целей. Альтернатива — другой доступный архив с полными
traces; пока не подтверждён. Upgrade/QuickNode/склейка primary logs не используются.
Пересмотр — новое проверяемое получение недостающих tails или evidence расхождения
полной истории cohort. Следующий этап: bounded оставшиеся 10 blocks и полная cohort
history, без выборки только удобных транзакций. Evidence:
docs/evidence/helius-traces-2026-10-02.json; docs/helius-audit.md.


### D33: этап второго RPC закрыт в согласованной области

**Статус:** принято по прямому поручению пользователя 2026-10-02. Накопленная
независимая сверка достаточна для исходной цели проверки второго RPC. Отменяет
предложение D32 дозагрузить оставшиеся 10 блоков как обязательный следующий этап.
Новых запросов ради идеальной historical trace-сверки всей Solana не выполнять.

Факты не повышаются: enumeration 12/12, full blocks 1 match / 1 mismatch,
2 462/2 464 tx exact, две Helius traces truncated, current-state pair без сравнения
из-за public timeout. Full-range agreement остаётся false, инфраструктурная
независимость не доказана. Это принятые ограничения, а не открытый бесконечный проект.
Пересмотр только если конкретная неполнота влияет на новый CPMM segment или задачу.
История и evidence сохраняются; QuickNode для новых запросов не используется.

### D34: следующий крупный этап — экономический dataset и условная симуляция

**Статус:** реализован и проверен на четырёх frozen journals, 2026-10-02.
Причина: после достаточного source audit наибольший разрыв — отсутствие воспроизводимого
формата реальных экономических состояний между raw journals и будущими features/labels/
execution research. Выбор: offline state tape на existing protocol reconstruction,
целые интервалы в карантин при blocker, atomic units/fees/withheld/epoch/provenance,
явно неизвестная historical availability; conditional size grid и immediate round trip.

Не преобразовывать реальные состояния в synthetic Pool с одной fee_bps, не снимать
ReferenceReplay gate. ML/RL/paper/live не начинать. Сетка ppm — инженерное допущение,
не торговый sizing. Альтернатива масштабировать RPC/продолжать редкие trace hunts
отложена: имеющиеся данные ещё не доведены до исследовательского формата.
Слабость: короткая fixed cohort история и неизвестная доступность не годятся для
OOS edge. Следующий критерий — воспроизводимый corpus/quality report, затем causal
observation/feature contract и подготовка bounded forward dataset. Контракт:
docs/economic-dataset.md. Текущий build/result — memory/state.md.


Результат D34: 16/16 intervals, 1 889 tx, 1 921 states, 11 430 quoted / 96 unavailable;
264 tests, offline reproduction всех bundles и wheel verification Token-2022 прошли.
Evidence: docs/evidence/economic-corpus-2026-10-02.json. Source gates не повышены:
следующий приоритет causal availability/feature contract и bounded forward corpus.

### D35: наблюдённые causal snapshots и bounded forward corpus

**Статус:** реализован и проверен реальным bounded сбором 2026-10-02.
Пользователь утвердил следующий этап 2026-10-02. Выбор: семь счетов одного finalized
bank с Clock, фактические receipt/decode/feature-publication timestamps и общая
live/offline CausalBook. Historical state tape не превращается в past observations.

Причина: будущему ML нужны причинные входы, проверяемая maturity labels и gap masks.
12 простых features не используют будущую реконструкцию или train/test normalization.
Duplicate banks не обновляют age; gap/regression/conflict сбрасывают history. Невалидные
rows сохраняются с причинами. Target — future observed log-price change, не executable
return; censoring/actual label maturity и purging на временной границе обязательны.

Первый инженерный план: прежняя fixed cohort из трёх Token-2022 pools, 12 rounds ×
10 s, максимум 36 read-only Helius requests; 10 s timeout / 1 MB cap, no retries,
stop on first error. Max ages 25/45 s, gap 25 s, horizon 20 s, target tolerance 15 s;
первая половина запланированного времени calibration, вторая engineering evaluation.
Это допущения data QA, не утверждённые параметры торговли или финальный holdout.

Альтернатива backfill receipts как историческая доступность отклонена. Слабость:
короткий cohort sample и polling не дают trade flow, regime coverage или execution
calibration. ML/RL/paper/live и synthetic replay gate не меняются. Пересмотр — реальные
проблемы доступности/покрытия в corpus, без ослабления temporal guards. Контракт:
docs/causal-forward.md; результат — memory/state.md и отдельный evidence report.

Уточнение D35 после первого bounded сбора: feature frame может содержать допустимый
carry-forward ещё свежего старого snapshot; schema 2 target требует **новый** snapshot
с input_available_ns >= horizon end и slot > start slot. Per-pool coverage и censoring
не расширяются общим временем frame. Первые schema 1 targets остаются историческими;
исправленные labels — отдельный производный artifact с parent hash, без новых RPC.
Compatibility verification требует точного воспроизведения всех старых values из raw,
несмотря на отличие source version. Это инженерная корректировка, не настройка модели
по holdout. Все новые collection plans фиксируют target_schema=2.


Результат D35: 31 successful snapshot / 32 requests, остановка на timeout без retry;
33 features / 29 usable, schema 2 targets 8 calibration / 4 engineering evaluation /
7 purged / 14 censored. 280 tests, offline/wheel reproduction. Data/execution/ML gates
не повышены. Evidence: docs/evidence/causal-forward-2026-10-02.json. Следующий приоритет —
длиннее prospective coverage через возобновляемые сегменты, затем execution targets.

### D36: возобновляемый prospective corpus и execution-conditioned targets

**Статус:** этап утверждён пользователем 2026-10-02; реализация завершена, длительный
сбор остановлен из-за повторного checkpoint inconsistency (результат ниже).
Новые immutable bounded сегменты продолжают frozen campaign, не историю causal book.
Crash evidence сохраняется, segment boundary сбрасывает warmup и цензурирует labels;
общий бюджет и temporal cutoff фиксируются до сети. Причина: увеличить prospective
coverage без превращения recovery receipts в прошлые observations. Альтернатива
заполнять gaps историей отклонена. Timeout допускает ограниченный новый сегмент после
cooldown; повторный последовательный отказ останавливает серию. Контракт:
docs/prospective-campaign.md. Объём, execution uncertainty и temporal guards остаются
основаниями допуска к ML; запуск обучения сейчас не разрешён качеством данных.

Результат D36 (2026-10-02): код segment resume и conditional execution targets завершён,
длинный сбор заблокирован двумя observed context/Clock mismatches (разные pools/slots).
После первого отказа разрешён один отдельный consistency probe; он прошёл, но новая
bounded кампания снова остановлена. Это не повод разрешить mismatch или продолжать
создавать новые кампании. Дальнейшая сеть остановлена; нужен targeted разбор текущей
bank consistency или другой уже разрешённый источник. D33 historical milestone остаётся
закрытым. 115 valid snapshots, 234.789 s segment coverage, 120 features, 960 scenarios /
432 conditional roundtrips / 516 censored; 297 tests и offline/wheel reproduction.

Execution targets ограничены моделью независимых будущих snapshots, explicit size/
entry-and-exit delay/slippage/holding. Unknown inclusion/network costs/persistent own
impact оставлены unknown; net_pnl=null. Реальные trading tx не отправлялись. Обучение
не начинается: fixed three-pool/minutes sample, две ошибки consistency, zero engineering
evaluation decisions на frozen cutoff, исполнение не квалифицировано. Полный отчёт:
docs/evidence/prospective-execution-2026-10-02.json. Пересмотр — устранённый current-state
blocker и новые prospective периоды, не ослабление temporal/data gates.

### D37: сохранить strict coherence и сменить текущий snapshot source

**Статус:** реализовано 2026-10-02; сбор возобновлён, затем остановлен новым
protocol-support blocker (подробный результат ниже).
Agave reference подтверждает единый bank для getMultipleAccounts/Clock. Старые два
Helius mismatch не нормализуются; upstream cause не установлен. MCP troubleshooting
не дал объяснения. Bounded preflight: public RPC 6/6 valid, Gatekeeper первый timeout.
Выбор: public RPC для следующей bounded prospective кампании с тем же decoder, без
нового протокольного adapter. Endpoint origin закреплён новым plan schema 2; aliases
недостаточно для защиты от случайной смены endpoint при resume. Старые raw/targets
и source-pinned verifiers сохраняются. Альтернативы: one-slot tolerance/сшивка счетов
отклонены; simulation/streaming не нужны до проверки простого согласованного источника.
Слабость: public RPC имеет ограничения доступности, короткий preflight не гарантия.
Следующий критерий — успешные bounded segments, offline verification и readiness report.
Контракт: docs/checkpoint-coherence.md. D33 historical audit остаётся закрытым.


Результат D37: public RPC 8/8 новых Clock/context agreement, шесть strict preflight
passes, два unsupported AmmConfig responses у разных configs. Unknown u64=50000 по
offset 116 не интерпретируется как fee и не игнорируется. Public source обходит
наблюдённый Clock mismatch в этой bounded выборке, но Helius upstream root cause не
установлен и новый full economic corpus не квалифицирован. Actual prospective accepted=0.
Gatekeeper и ProgramData reads — два timeout, no retries. Выбор следующего этапа:
подтвердить deployed layout/семантику config и source compatibility либо account
response defect, затем новый independently verified adapter. Не продолжать pool search,
не возвращать historical trace milestone D33. 313 tests, offline/wheel reproduction;
docs/evidence/checkpoint-coherence-2026-10-02.json. Training gate остаётся false.

### D38: versioned snapshot support для creator fee share

**Статус:** семантика подтверждена official SDK+docs и new read-only provider evidence,
2026-10-02; snapshot adapter реализован, bounded continuation выполнен. Offset116 — default creator fee share
(denominator 1e6), применяемая при creator fee collection; per-creator PDA override
неизвестен без отдельного наблюдения. Swap math/reserves не меняются от перераспределения
между excluded counters. Поддержать только snapshot/quote profile2, сохранить metadata,
range0..1e6 и нули bytes124+; legacy decoder/replay default1 не расширять. Version2
Checkpoint отклоняется legacy replay. Причина: достаточно evidence для snapshot quotes,
недостаточно для полного creator collection replay или deployed binary equivalence.
Проверенный Rust HEAD отстаёт от docs/SDK; lookup только одного репозитория был неполным.
Контракт/provenance: docs/creator-fee-share.md; source revisions закреплены. Пересмотр:
новая область nonzero bytes/неизвестная операция либо независимый collection witness.

Результат: Helius подтвердил оба config в coherent read (slot452694796), ProgramData
upgrade_slot452376460; это не same-bank comparison с прежним public capture и не
binary equivalence. Public probe timeout, затем новая campaign public profile2:
2 valid snapshots / 4 requests, два timeout подряд после одного bounded resume.
Кампания закрыта; не переключаться автоматически на Helius с unresolved Clock mismatch.
6 masked features / 48 censored scenarios / 0 mature decisions. Corpus/targets/quality
воспроизведены dev и installed wheel, 327 tests, smoke/build прошли. Дальнейший gate —
доступный coherent source; требуются новые основания для ограниченного source preflight,
не бесконечный retry. docs/evidence/creator-fee-share-2026-10-02.json.

### D39: bounded public source через persistent verified TLS

**Статус:** transport и independent target-support QA реализованы 2026-10-02;
bounded qualification и real process restart выполнены. Выбран официальный
https://api.mainnet.solana.com со stdlib_persistent_v1, после mainnet genesis и 12/12
strict coherent snapshots. Старый и canonical host сейчас имеют один IP: не независимые
providers. Helius Clock mismatch unresolved; его guards не ослаблять и не использовать
слепой fallback. QuickNode остаётся retired для новых запросов.

Диагностика показала как минимум TLS/connect timeout до RPC. Смена User-Agent не
оказалась достаточным fix. Exact upstream cause всех отказов неизвестен. Keepalive
уменьшает необходимость повторять cold TLS setup; TLS/hostname verification сохраняется.
Transport привязан к endpoint, max bytes/deadline, не следует redirects и не повторяет
запросы. Phase/reuse metadata и transport profile закреплены в immutable plans/attempts;
source-pinned offline reproduction сохраняется. Public RPC не получает SLA от этого выбора.

Следующий QA этап: учитывать полную scenario grid и chronological nonoverlap target
windows; не объявлять variants/overlapping labels независимыми samples. Все planned
scenarios решения должны созреть в одной partition. Это offline coverage diagnostic,
не future-conditioned online filter. ML всё ещё за coverage/execution gates.
Пересмотр: failed strict snapshot, recurrent availability failures, server closing behavior
или insufficient coverage; не расширять tolerances ради продолжения. Контракт:
docs/persistent-source.md. Результат: 482 valid / 483 corpus requests; campaign01 оборван на headers, campaign02 180/180
при новом процессе/соединении на сегмент. 345 mature decisions, 141 evaluation,
15 chronological nonoverlap windows. 346 tests, smoke/build, offline/wheel проверены.
Evidence: docs/evidence/persistent-source-2026-10-02.json. Полный длительный/рыночный
corpus и ML readiness не достигнуты.

### D40: проверить Alchemy HTTP+WSS до изменения основного pipeline

**Статус:** bounded v1 реализован/проверен 2026-10-03; hybrid не допущен к замене.
Первоначальное согласование пользователя было направлением пилота, не миграцией.
В новой сессии MCP app/mainnet и отдельные `.env.local` HTTP/WSS фактически проверены;
секреты не выводились/коммитились. План зафиксирован `b3e7d89` до runtime запросов,
код `6368caf`. Результат/причина отказа — ниже.

Причина: D39 доказал bounded correctness/restart, но календарное/рыночное покрытие
остаётся узким. Гибрид потенциально уменьшает polling и даёт лучшее покрытие изменений.
Начальный вариант: WSS сохраняет уведомления и инициирует обновления, HTTP остаётся
источником coherent snapshots и контроля. Не считать отдельные account notifications
атомарным состоянием pool+dependencies или полной упорядоченной историей транзакций.
HTTP recovery/backfill допустим только в реально поддержанной области; endpoint access
не доказывает возможность восстановить любое historical account state.

Пилот сравнивает Alchemy HTTP и Alchemy HTTP+WSS на одной заранее фиксированной когорте,
начиная с finalized; смену провайдера отделить от эффекта transport. Доказать строгую
snapshot корректность, безопасную границу snapshot→stream, обнаружение gaps и восстановление
после forced disconnect/process restart, обработку duplicate/late/out-of-order delivery
и перегрузки, воспроизводимость online/offline. Совпадение endpoints не доказывает
полноту intermediate transitions. Невосстановимые интервалы маскировать/censor; recovered
receipts не становятся прошлой доступностью. Нужен измеримый выигрыш в пригодном coverage
или нагрузке при сохранении качества, с учётом трафика и разрешённого бюджета.

Не менять основной pipeline до evidence отчёта и отдельного явного решения по этим
критериям. Не снимать Clock/linkage/decoder/availability guards, не расширять scope
creator collection replay и не начинать ML/RL/paper/live. D33 historical trace audit
не переоткрывать; QuickNode не использовать для новых запросов. Детальный контракт:
раздел D40 в memory/data-and-execution.md. Пользователь просит ссылки на итоговый документ
этапа и evidence в финальных отчётах.


Результат D40-v1:6 preflight strict snapshots, 216 Alchemy paired snapshots,
144 public controls; всё accepted. Два 120 s сегмента, два разных процесса, два forced
reconnect;1158 WSS messages. Raw→observations/features/observed-price targets побитово
воспроизводимы dev/wheel. Подписки подтверждены до anchors; WSS values не склеиваются.
Исторические gaps не восстановлены. Duplicate/late/overflow/silent missing hint проверены
synthetic adversarial tests; полный silent write loss недоказуем этим subscription API.

Выбор: сохранить D39 primary, Alchemy HTTP считать пригодным только для проверенной
bounded strict-checkpoint области. HTTP calls hybrid уменьшились50%, но usable fraction
упала95,83→80% (83,33% на scheduled frames), payload уменьшился0,66%, coverage не выросло.
Primary criterion провален. Config дал 170 notifications одного value; scheduler фактически
проредил hybrid до~10s. Нет основания приписать экономию только transport или менять
основной pipeline. Слабость опыта: короткая fixed cohort, stress только в hybrid,
нет matched10s HTTP control. Альтернатива: простой более редкий HTTP; следующий D40-v2
сначала scheduler/coalescing и новый frozen matched-cadence steady/stress plan.
364 tests; [отчёт](../docs/alchemy-pilot.md),
[evidence](../docs/evidence/alchemy-pilot-2026-10-03.json). На момент v1 V2 не был проведён; актуальный результат ниже. ML gates прежние.


### D40-v2: разделить transport, cadence и fault stress

2026-10-03 пользователь разрешил самостоятельно выбрать роль WSS по evidence и перейти
дальше. План `f58a3d1`: точная matched10s пара http10/wss10, http5 как быстрый контроль,
adaptive eligibility5s/heartbeat10s как отдельная policy; steady и symmetric stress
раздельно. Абсолютные ticks не зависят от HTTP completion; identical content нового
slot не dirty, late messages не заменяют HTTP anchor. Полная write history всё ещё
не доказана. Итоговое решение — после неизменяемых двух segments; ML запреты прежние.


Результат D40-v2: initial WSS EOF после4 ack, без close code, unknown cause; stopped
bundle сохранён. Новый frozen v2b меняет только serial subscription admission; две
120s campaigns steady/stress прошли. Matched10s ticks/common as-of проверены,421 Alchemy
snapshots,144 public controls,1274 WSS messages. Adaptive49 vsHTTP5 72 calls, quality
66/72 vs69/72; total payload336855 vs262152 bytes. Criterion false, v1 cadence artifact
устранён. Выбор: WSS optional diagnostic, выключен в дальнейшем corpus. Не обязательная
часть ML/runtime. Слабость: короткая fixed cohort, разные banks; не доказана causal
эффективность serial subscribe. Пересмотр только при новой конкретной change-discovery
гипотезе и заранее заданном resource/quality win, не endless tuning.

### D41: qualified HTTP corpus и проверка достаточности данных

**Статус:** новый bounded план принят автономно по прямому поручению пользователя
2026-10-03, до сбора. Следующие2×30×5s segments/max180 requests используют Alchemy HTTP
persistent TLS, source origin/config profile2 pinned. HTTP5 выбран ради observation
coverage; не является утверждением оптимальной decision frequency. WSS off. Прежний
public collector остаётся reference, fallback нет. Первый failure закрывает campaign.
Затем неизменённые execution targets/quality/support, всё offline+wheel. Не расширять
протокол или ML gates. Причина: source/scheduler уже проверены, следующий bottleneck —
календарное coverage и execution uncertainty. План configs/qualified-http-corpus-v1.json.


Результат D41: первый cold connect timeout15,0718s до HTTP; snapshots0, retries0,
кампания закрыта. Dev/wheel QA:3 masked rows/24 censored scenarios, mature0. Выбор
Alchemy HTTP не отменяет availability gate: пока квалифицированы только полученные
strict checkpoints. WSS остаётся off, запусков сверх stop budget не было. Приоритет
следующего этапа — DNS/TCP/TLS phase observability и новый ограниченный календарный
сбор при новых основаниях; не повторять закрытый D41. Ослаблять coherence/protocol
checks, увеличивать timeout ради success или начинать ML нельзя. Слабость: одна
connect ошибка не определяет upstream cause и не оценивает долгосрочную reliability.
[Итог](../docs/qualified-http-corpus.md), [evidence](../docs/evidence/qualified-http-corpus-2026-10-03.json).

### D42: завершить cold диагностику и вернуться к данным

2026-10-03 пользователь разрешил bounded диагностику, выбор режима и новые календарные
окна с execution uncertainty. План07fc1ae, diagnostic code add2ef1:4 новых соединения
Alchemy/public/public/Alchemy,8/8 strict snapshots, warm reuse4/4. Ошибок нет; прошлый
D41 timeout не воспроизведён, upstream причина неизвестна. По заранее заданному tie
правилу выбран Alchemy HTTP persistent TLS внутри сегмента, новые процессы на границах,
без WSS/fallback. Это bounded operational choice, не SLA. Дальнейший transport tuning
не приоритет; запланированы два prospective окна с разнесением≥600s и uncertainty audit.
Оба окна выполнены: 360 accepted/348 usable, 268 mature/135 evaluation,
12 nonoverlap windows. Offline/wheel exact, network failures0. Calendar created_ns≥600s criterion false:599,994084s, shortfall5,916ms;
actual segment starts600,037526s.
исправлен будущий anchor, старые timestamps неизменны. Дополнительное
conditional cost/delay evidence не калибрует admission/costs и не доказывает edge.
Transport gate закрыт в bounded области; дальше разные часы/дни и execution evidence,
не новый transport tuning. Контракт docs/cold-connection-corpus.md;
evidence docs/evidence/cold-connection-corpus-2026-10-03.json.

### D43: finite calendar schedule и реальные address-related execution observations

2026-10-03 пользователь разрешил автономный сбор по часам/дням и read-only исследование
execution costs/timing; transport tuning закрыт. Frozen план6ec6bad:6 окон0/6/12/24/36/48h,
180 corpus+12 execution RPC на окно,1152 максимум, первая ошибка закрывает окно, две
failed windows закрывают расписание. Sleep/missed не catch-up. Local launchd в рамках
прямого поручения; pinned tools/wheel/reference hashes, no secrets in plist, STOP marker.
Чужие meta.fee/compute units и observed commitment transitions — empirical inputs,
не own admission/submission latency, не full strategy costs или net PnL. Отдельный
read-only method getSignatureStatuses включён в allowlist; торговые методы не добавлены.
Результат первого окна:180 accepted/174 usable,135 mature (67 evaluation),5 nonoverlap.
Execution12 RPC/6 tx, observed fees15 001–154 999 lamports; все6 timing left-censored.
Первое окно completed, остальные5 pending; background job реально установлен/запущен.
399 tests и exact dev/frozen-package reproduction. Не объявлять календарный этап
полностью завершённым. Следующий шаг — inventory после новых due окон и решение о
недостающей universe/timing выборке; никакой текущей ML readiness.
Контракт docs/calendar-execution.md, конфигурация configs/calendar-execution-v1.json.

### D44: независимый ML foundation без market-result claims

2026-10-03 пользователь явно разрешил dataset/training/model/evaluation/scaling основу
и smoke/integration обучение на имеющихся данных. Это уточняет прежний запрет ML:
инженерные training runs разрешены; настоящий ML результат/edge на малом corpus нет.
D43 не менять/переустанавливать. Отдельный optional пакет/окружение, frozen experiments,
causal sequences, grouped maturity purge, train-only scaler, сменные backbone и
conditional heads, checkpoint/resume/inference, CPU/MPS benchmarks. Нет dogmatic TCN,
размера или частоты; нет RL/trading/paper/live. Контракт docs/ml-foundation.md.


D44 завершён:15 CPU smoke+1 MPS train,40 benchmark cases, exact CPU resume и real
installed-wheel reproduction,17 ML/416 combined tests. CPU engineering default; GRU16
первый temporal candidate, но backbone/size/frequency не фиксируются. MPS ops/parity
подтверждены на M4 Pro; speed зависит от shapes. Corpus readiness false: validation
не покрывает failure classes, test50% censored variants. Research mode закрыт до
отдельной corpus/protocol qualification. D43 protected hashes70/70 равны. Следующий
независимый D45 — multi-window registry/qualification/global folds, пока не проведён.
Evidence: docs/evidence/ml-foundation-2026-10-03.json.

### D45: registry и qualification независимо от frozen D43

2026-10-03 пользователь разрешил multi-window registry, corpus qualification и global
chronological folds/holdout, сохраняя запрет research training до readiness decision.
До опытов закреплён план docs/corpus-registry.md: immutable append revisions/objects,
semantic admission development, future holdout quarantine, fixed absolute folds с
group maturity purge. Count floors только diagnostics; research readiness не включается
автоматически. D43/CPMM/features/targets/D44 model sources не изменять. Результат не получен.


D45 завершён: code1038265, registry work/corpus-registry-01/revision000000.1 реальное
окно/5pending,174 decisions; fold0/fold1 train174,val0,test0. Absolute protocol создан
до будущих окон; holdout4/5 quarantined до отдельного release, API release отсутствует.
Window0 exposed в D44 остаётся development.17 synthetic registry tests,419 base/433
combined, exact wheel audit/export, repeat no_change. Diagnostic count floors сами
не открывают research gate. D43 protected70hashes/D44 model source identity неизменны.
Следующий D46: research specification/readiness dossier; пока не проведён. Routine
status через D45: старый D43 inventory раскрывает label reports и не подходит для
защиты holdout. Контракт/evidence: docs/corpus-registry.md и docs/evidence/corpus-registry-2026-10-03.json.

### D46: frozen F01 specification и раздельные readiness gates

2026-10-03 пользователь разрешил определить research criteria/model-selection protocol
до накопления достаточного corpus. F01 ограничен conditional forecast skill fixed cohort,
не net-edge: forecast semantic qualification отдельно от full economic calibration.
D43/D45/holdout4–5 не менять и не раскрывать. Их finite corpus недостаточен для F01;
новый будущий84day binding только проектируется, сеть/collector не запускаются.
Frozen rules: configs/research-f01-v1.json; мотивировка docs/research-readiness.md.
Никаких post-hoc cutoff/seed/metric changes. Research launch/release остаются закрыты.


D46 завершён: prereg58c66da,code2fbd09d; frozen F01-v1 и executable planner/selector/
assessment в отдельном memecoin_research.9candidates,seeds7/19/41,114fit ceiling,
paired proper-loss CI lower bound>2% against empirical AND linear с predeclared precision/
calibration guards. Новый84day design требует будущего binding после inherited holdout;
D43/D45 не расширяются задним числом. Count/coverage floors необходимы, не power guarantee.
15new/434base/448combined tests, exact installed-wheel assessment; текущий launch/release
false, research fits0. D43 не открывался assessment; D44/D45 source identities прежние.
Forecast semantic readiness отделён от economic qualification: conditional prediction
не требует притворяться калиброванной собственной торговлей, но не доказывает net-edge.
Следующий D47 — runner/ledger/scoring conformance на fixtures, пока не выполнен.
Документ docs/research-readiness.md, evidence docs/evidence/research-readiness-2026-10-03.json.


### D47: frozen F01 execution foundation, admission остаётся отдельным

2026-10-03. По поручению пользователя реализованы isolated runner, immutable trial/
exposure ledger, CPU worker deadlines, frozen scoring/bootstrap и selection/refit.
Принято для engineering use, не research admission. Не модифицировать D44 smoke trainer
ради разрешения F01; новый пакет сохраняет прежние fingerprints. Unclosed attempts/
sessions консервативно расходуют reservations, без free retry. Holdout consumption
пишется до read/hash; crash требует incident review, не новой модели на раскрытом test.

Планf4aebb7,code225ab0f;54final+27preliminary engineering fits, research fits0.17new/
445base/465combined tests, exact wheel reproduction. Архитектура не выбрана по smoke.
Следующий D48: отдельный future sampling/source/ingestion contract/readiness dossier;
D43 и inherited holdout сохраняются. Документ/evidence: docs/f01-runner.md и
docs/evidence/f01-runner-2026-10-03.json.


D47 integrity addendum bbcd0e8: repeated source/data verification на worker boundary
обязательна, чтобы initial admission не принимала изменённые между fits bytes.
Повтор54engineering fits не меняет научный protocol; score hashes прежние.18new/466combined
tests и final wheel reproduction; original evidence сохранено, addendum
docs/evidence/f01-runner-integrity-2026-10-03.json. Admission по-прежнему отсутствует.


### D48: offline future sampling/ingestion foundation

2026-10-03. По поручению пользователя реализован отдельный memecoin_sampling, не
изменяющий D43–D47. Планb970996,code84482bd. Frozen proposal T2026-10-07UTC,84days,
4×5min/day,336окон/60480RPC ceiling. Дизайн ограничивает observed UTC-window scope,
не объявляет continuous market coverage. Monetary quota/activation не разрешаются
появлением proposal; collector не установлен. Даты F01-v1/метрики не ослаблены.

Принято: bound source root и holdout index guard до чтения; raw+converted immutable
objects; full source replay; per-role denominator всех planned windows. Unknown failed/
missing starts учитываются верхним180 ceiling и conservative usable lower bound, не
выдуманной точной fraction. Records сохраняют нарушения failure-stop, readiness false.
Synthetic data_kind не становится prospective.20new/486combined tests, exact wheel
reproduction; actual registry0windows,training/RPC/release0. Capacity fixture лишь data
path feasibility. Следующий D49 — source/quota/runtime/activation qualification перед
отдельным решением о новом collector. Документ docs/f01-sampling.md; evidence
docs/evidence/f01-sampling-2026-10-03.json.


### D49: источник квалифицирован bounded probe; proposal не активировать

2026-10-03. Принято: current finalized persistent Alchemy/profile2 остаётся source candidate,
6/6 coherent checkpoints/120CU прошли. MCP account usage delta120CU/USD0 согласуется с
HTTP, но не доказывает plan tier/zero-dollar hard cap/exact credential binding к account.
Public1 209 600CU ceiling не денежная авторизация; paid budget0. NOT READY FOR ACTIVATION.

Engineering engine/adapter/immutable staged binding приняты: due-only,full reservation
при crash,no retry/catchup,8failed stop,16new/502combined tests,180fake raw integration и
exact wheel reproduction. Production supervisor210s/600s,storage/runtime checks,host wake/
login и full180RPC live qualification ещё gates. Никакого source/service activation,
D43/F01/holdout изменений или настоящего training. Candidate сохранять; дальнейшие
изменения runtime требуют нового candidate, не silent rewrite этого evidence.
Следующий D50 закрывает operational blockers; только затем отдельное activation decision.
[Документ](../docs/f01-collector-qualification.md),
[evidence](../docs/evidence/f01-collector-qualification-2026-10-03.json).


### D50: operational envelope отдельно от реальной activation

2026-10-03 пользователь принял D49 и поручил продолжить core roadmap. Engineering
runtime реализован новым memecoin_operational без изменения frozen D43–D49 sources.
Приняты210s/segment и600s от durable reservation, process-group kill/orphan lease,
per-RPC runtime/credential/budget/disk/clock fences; исходные due-only/no retry/8failed
и F01/D48 остаются. Disk floor2GiB+512MiB — stop guard, не обещание reserved capacity.

Решение по evidence: engineering qualified, calendar activation BLOCKED.15new/517combined
тестов, полный180fake raw subprocess pipeline и wheel reproduction прошли. Receipt
requires explicit decision+pins+substantive billing review; USD0 не Free proof. Scope
bounded привязан к одному root/180RPC,calendar — к exact D48 root/60480RPC. Новых approvals0.

Actual account no-charge/mapping неизвестен; browser отсутствует. Full live qualification
не проведена (marketRPC0). Source/service не создан/не установлен. Следующий D51 закрывает
эти внешние/live/host условия, затем отдельное activation решение. Current bundle final
не переписывать; при code changes новый pin. Doc/evidence: [D50](../docs/f01-operational-runtime.md),
[JSON](../docs/evidence/f01-operational-runtime-2026-10-03.json).


### D51: один bounded launcher готов; live и activation остаются закрыты

2026-10-04. D50 принят пользователем. План97c2d26,launcher4ed893b сохраняют unchanged
D50 runtime и frozen D43/F01/D48. Exact one-attempt receipt/claim, durable180reservation
до worker, no retry после crash/concurrent claim, protected paths и review validity600s.
10new tests/506base/2optional skips,make check/smoke; никакой новой scientific архитектуры.

Решение: D51 OPEN/BLOCKED, NOT READY FOR ACTIVATION. MCP27940CU/USD0 и app metadata не
подтверждают Free/no-paid-overage/runtime→billing account; app ID не account ID. Browser[];
один запрос owner nonsecret attestation без ответа. Разрешённый путь — substantive review
этого подтверждения либо provider evidence, не autogenerated approval. LiveRPC0,
reservation0,receipt0. Отсутствие доступа не основание объявлятьD51 выполненным/создаватьD52.

Host battery40%,free134,77GB,GUI501 — наблюдение, не uptime guarantee. Будущая activation
отдельна от bounded180RPC: нужны awake/power/login условия, exact runtime/receipt и итоговое
решение. D43/holdout не менять; calendarT и no-backfill остаются. [Документ](../docs/f01-live-qualification.md),
[evidence](../docs/evidence/f01-live-qualification-2026-10-04.json).

### D51 завершён после owner attestation; calendar decision остаётся отдельным

2026-10-04. Прямое подтверждение владельца Alchemy Dashboard сняло blocker bounded
no-charge/account mapping согласно preregistered97c2d26. Evidence7e9ae2b честно маркирует
owner attestation, local account alias не provider ID; no PAYG/card/paid overage,
30M allowance и3600CU allocation. Принят только один reviewed180RPC receipt.

Результат:180/180advanced strict finalized/profile2,3pools×60,174usable/6warmup,299,34s;
raw/checkpoint/features/targets exact replay, independent installed wheel reports
byte-identical.506tests/2skips,check/smoke. Принять bounded runtime/source qualification;
D51 завершён. Предыдущий blocked snapshot9f616a7 не переписан, финальное evidence отдельно.

MCPusage не монотонен27660→19320CU; не использовать его дельту как measured billing.
Локально180attempts,nominal3600CU; paid0. Calendar receipt/activation не выдавались:
owner allocation относилась к одной попытке. D52 — отдельное operational решение
для frozen D48 (60480RPC/1209600CU,host awake/power/login иmonitoring). Не требуется
повтор bounded окна/transport tuning; no-backfill/T/F01/D43/holdout прежние.
[Итог](../docs/f01-live-qualification.md), [evidence](../docs/evidence/f01-live-result-2026-10-04.json).
