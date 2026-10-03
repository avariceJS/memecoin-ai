# Данные и достоверное исполнение

## Временная корректность

Система в момент t видит только информацию, которая реально могла быть ей доступна. Хранить event time, slot и доступный порядок событий, received time, feature available time, источник, уровень подтверждения, исправления и provenance. Не выдавать blockchain timestamp за время получения. Ретроспективное восстановление receipt time часто невозможно; такие ограничения явно отмечаются.

Пример ошибки: событие в 12:00:00 пришло в 12:00:03, но бэктест использует его в 12:00:01. Та же проблема возникает с исторически пересчитанными holders, metadata, wallet labels и графом.

Raw events неизменяемы; нормализованные таблицы и features воспроизводимы из версии raw/transformations. Для live и replay одинаковая семантика. Коррекции и поздние события не должны молча переписывать знания прошлой policy. Нужны дедупликация, gap detection, reconnect и контроль часов.

## Вселенная и приоритеты данных

Хранить реестр известных на каждый момент токенов и пулов, включая умершие, исчезнувшие и недоступные. Не строить историю из текущего списка популярных токенов. Область охвата провайдера и пробелы — часть датасета.

Приоритеты:

1. Состояния пулов и котировки для разных размеров; реальные сделки, liquidity adds/removes, комиссии.
2. Время доступности и качество источника, failed/dropped execution evidence.
3. Возраст/стадия жизни токена, поддерживаемые token properties, mint/freeze/transfer restrictions.
4. Потоки, returns, volatility, trade intensity, inter-arrival, buy/sell imbalance.
5. Концентрация и изменения holders, deployer history, доступные связи адресов.
6. SOL и агрегаты/взаимодействия мемкоин-сектора.
7. Социальные и текстовые сигналы только при качественной point-in-time истории и ablation.

Volume acceleration и trade velocity производятся из raw events. Market cap/FDV не заменяет выходную ликвидность; holders count не заменяет распределение предложения. Абсолютные token price/volume требуют нормализации. Подозрительная активность может быть признаком, но нельзя автоматически считать все наблюдаемые объёмы независимым спросом.

## Формат входа

Сохранять raw event sequence, даже если модель использует bars. Стартовая гипотеза — multi-scale bars + короткая event-ветка. Для пустых интервалов различать отсутствие торговли, неизвестное состояние и сбой feed. Carry-forward значения сопровождаются возрастом наблюдения.

Бар доступен только в закрытом виде к его закрытию или как корректный partial snapshot. Не подмешивать будущие high/low/volume. Quote-volume bars имеют переменную длительность; учитывать её. Trade-count bars чувствительны к искусственному дроблению сделок. См. E09/E10.

## Wallet и deployer

Репутация кошелька рассчитывается в момент t только по прошлым наблюдаемым результатам с учётом незакрытых исходов, малого sample size и доступной себестоимости. Нельзя выбрать нынешних лучших и использовать их исторические сделки как заранее известные smart wallets.

Временной граф: funding, transfer, co-holding, deployer relations; каждому edge назначается известное время и уверенность. Общий funding может быть биржей/сервисом. Синхронность не доказывает общий контроль. Мягкие группы и локальные признаки предпочтительнее необратимых утверждений об ownership.

Графовые embeddings нельзя обучить на всей будущей сети и затем вставить в исторический train/test. Для новых токенов/адресов необходим cold-start путь. Быстрый успешный кошелёк может иметь недоступную нам цену входа; наблюдение его сделки не равно возможности повторить её PnL.

## Аудит источника перед выбором

Проверить историческую полноту, births/deaths, поддерживаемые пулы, разрешение времени, receipt timestamps, семантику buy/sell, динамику holders, corrections, rate limits, условия хранения/использования, воспроизводимость, live/history consistency и стоимость. Axiom пока лишь рассмотренный вариант интерфейса; предположений о его API или полноте не принято.

## Минимальный event simulator

Разделить истинное воспроизводимое состояние, наблюдение агента, момент решения, отправку и возможное исполнение. Исполнять по состоянию в допустимый момент после задержки, не по цене решения.

Поддерживаемый механизм пула должен быть реализован точно в заявленной области. Constant-product, concentrated liquidity и другие механизмы не сводятся к одной формуле slippage. Неподдерживаемые token extensions/ограничения передачи исключаются до явной поддержки. Численные тарифы брать из версии механизма, не хардкодить по старой документации.

Моделировать:

- Pool fees, network/priority fees и дополнительные расходы маршрута при наличии.
- Состояние ликвидности, size-dependent quote и price impact; не считать spread/slippage/impact независимыми штрафами, если уже входят в цену.
- Data, compute, delivery и inclusion latency с зависимостью от условий.
- Slippage tolerance, failed execution, dropped/expired transactions, retries и pending state.
- Конкурирующие сделки, неблагоприятный сдвиг цены и исчезновение ликвидности.
- Ограничения маршрута, доступный баланс, создание token accounts и связанные расходы/заблокированные средства, если релевантно пути исполнения.
- Завершение эпизода, остаточные позиции и невозможность выхода.

Обычный атомарный swap не получает искусственное частичное исполнение как order-book limit order. В серии транзакций отдельные этапы могут иметь разные исходы. Included transaction с ошибкой и dropped transaction различаются, в том числе по расходам.

## Бухгалтерия и стоимость портфеля

Ledger cash, token units, комиссии, pending commitments, фактические fills и failures. Комиссии учитывать ровно один раз. Отображать net-результат в SOL и USD до выбора primary currency. Не смешивать рыночную доходность SOL с alpha модели.

Открытые позиции оценивать консервативной ликвидационной стоимостью с учётом размера, маршрута и совместных ограничений. Last trade price не является гарантированной ценой выхода. Невозможность ликвидации не исчезает в конце эпизода. Различать цензурирование неизвестного исхода, реальную потерю ликвидности и ошибку данных.

Stop-loss — намерение, не гарантия цены. Hard risk limits ограничивают действия, но не гарантируют максимальную фактическую потерю при скачке или недоступном выходе.

## Контрфактуальные ограничения

Исторический рынок не реагирует на наши виртуальные сделки. Если позиция существенно меняет пул, дальнейшая историческая траектория становится некорректным контрфактуалом. Одноразовый impact penalty это не исправляет. Первые исследования ограничить областью малого воздействия; capacity проверять отдельно.

Исторические события и один экзогенный replay не дают наблюдавшихся переходов произвольных действий агента. Сгенерированные действия остаются зависимыми от симулятора. Не называть их реальными logged execution data для offline RL.

## Проверка и борьба с exploitation

Сверка pool math на исторических состояниях/транзакциях, независимые проверки ledger, отсутствие невозможной прибыли от мгновенного round trip при неизменном рынке, корректные fees и failures. Замораживать оценочную версию исполнения независимо от прибыльности стратегии.

Оценивать диапазон правдоподобных задержек, расходов и ликвидности; стрессовать рыночные разрывы и отказ feed. Проверять прибыль около пропусков, границ эпизода и сбросов состояния. Отделять calibration periods от проверки.

Paper trading проверяет живой pipeline, котировки и решение. Он не доказывает inclusion, конкурентное исполнение, собственный impact и реальную ликвидацию. Эти ограничения остаются явными до любых будущих отдельно разрешённых live-проверок.

## Реализованный data adapter

Диагностический CPMM reader, структурный Token-2022 inventory и их границы описаны
в [source audit](../docs/source-audit.md) и [contracts](../docs/contracts.md). Raw receipt
и context slot не заменяют historical event/feature availability. Реальные checkpoint
артефакты не подаются в synthetic reference replay. Первоначальная проверка vault flows
не восстанавливала fee counters; новый protocol audit описан ниже.

## Forward pilot

Реализованы ограниченный polling фиксированной когорты и восстановление между finalized
checkpoints. Сканируются pool и оба vault; order внутри слота берётся из блока. Проверяется
полная последовательность pre/post token balances, а не только сумма дельт. Recovery не
меняет прежние receipts. Граница доказательности, команды и результат — в
[forward pilot](../docs/forward-pilot.md). Полное pool state, universe coverage и независимый
провайдер остаются открытыми; snapshot consistency не является execution calibration.

## Проверка протокольных котировок

Поверх vault-аудита реализована реконструкция classic-SPL экономического состояния из
инструкций, с независимой проверкой вычисленных сумм по events/transfers. Начисления
protocol/fund fees проверены на реальных swap; creator fee, LP changes и collections
имеют source-derived реализации, но недостаточно mainnet coverage. Не использовать
event reserve/amounts как поправки к состоянию ради совпадения. Успешная транзакция
может не выполнить ни одного swap. Failed transactions атомарны в экономической модели,
но их network fees не исчезают из будущего execution ledger.
Граница conditional quote vs full execution и факты: [protocol audit](../docs/protocol-audit.md).

## Token-2022

Добавлен отдельный allowlisted codec/schema 2 с Clock в том же checkpoint. Token transfer
fee и CPMM creator/trade fees считаются на разных этапах; withheld ведётся отдельно от
spendable vault amount. Административные изменения не интерполируются, текущая эпоха
collector не используется как историческая. Свойства пользовательских счетов и hooks
не считаются проверенными по одному mint. Границы и mainnet evidence: [Token-2022](../docs/token2022.md).

## История параметров и альтернативное покрытие интервала

Текущее расширение D28–D30: config/mints добавляются только явным opt-in;
экономические параметры применяются по времени исполнения, включая activation в
эпохах. Старая предпосылка unchanged terms не стала доказательством задним числом.
Публичная mainnet schedule не имеет warmup; значения не захардкожены в runtime.
Full-block и bounded-range recovery сохраняют исходное доказательство и отдельно
от незакрытых address cursors проверяют покрытие. Captures и decode cache нельзя
использовать для ретроактивной доступности features. LP witnesses имеют более слабый
уровень, чем interval reconstruction. Точные границы: [history](../docs/parameter-history.md).

## Проверка провайдера и измерение задержек

D31–D32: read-side audit хранит точный план и raw ответы; разный account bank не сравнивается
как одинаковое состояние. HTTP failures и неполный план не дают agreement. На первом этапе независимый
оператор был недоступен; последующие результаты и закрытие этапа — D33 ниже. Отдельно измерены monotonic response
и receipt-minus-Clock, без переноса этих значений в trade inclusion/fill model.

D30 расширено тремя реальными fund-collection witnesses: gross vault debits, net recipient
credits и requested limits проверены, прежние counters и tariffs не восстановлены.
Подробности, числа и reproducible commands — [provider audit](../docs/provider-audit.md).


Range enumeration допускает provider-specific размер частей, не меняя исследуемые
границы. Original capture и все chunk responses сохраняются отдельно. При partial
failure нельзя объявить неувиденные slots пропущенными у провайдера. Для положительного
bounded-range agreement нужны все full block comparisons и ancestry; совпадение только
списка слотов недостаточно. Текущий транспортный отказ не дал такого agreement.

Диагностика 2026-10-02 получила полное enumeration 12/12 у QuickNode, но только
три full blocks до timeout четвёртого. Full-range agreement всё ещё false. Sandbox
DNS failure отделён от timeout разрешённой сети и MCP 401 тарифного Console API.
Явный CLI `--timeout` не является execution latency или общим сетевым deadline.
Captures/hashes и ограничения: [provider audit](../docs/provider-audit.md).

## Переход проверки второго источника на Helius

2026-10-02: Helius — текущий кандидат вместо QuickNode по прямому поручению. Новые
QuickNode запросы прекращены, архивы сохраняются. Документационный MCP отвечает,
chain preflight получил timeout внутреннего getSupply; mainnet, finalized height и
история через Helius не подтверждены. На этом первом этапе runtime endpoint/key отсутствовал в доступной
настройке проекта. MCP tools, parsed data и direct JSON-RPC имеют разные границы;
MCP tool results сохранены отдельно и не превращены в RPC captures.

Для будущего direct audit — точные reference requests, один getBlocks на 12 слотов
согласно документированным параметрам Helius, без универсального лимита пять. Новые
comparison schema 2 сравнивают весь block/transaction result; одинаковая неполнота
обязательных full-block metadata даёт unavailable. Schema 1 воспроизводит старые
отчёты. Не подменять MCP wall time HTTP duration или execution latency. Результат,
ограничения и отложенный план: [Helius](../docs/helius-audit.md).

## Полнота traces: реальное расхождение Helius

После настройки endpoint выполнен direct audit: список 12 slots совпал, первый
block identity/order совпал, но две logMessages arrays укорочены с Log truncated.
1 308/1 310 tx совпали целиком; остальные 11 blocks не загружались. Дополнительный
raw getTransaction сохранил обрезку. Наличие поля logMessages не доказывает полного
trace. Одинаковая обрезка у двух источников также не даёт positive completeness.

Schema 3 выделяет marker и различающиеся JSON paths, не меняет raw payload и не
игнорирует поля. Legacy schema 1/2 воспроизводят старые отчёты. Full-block mismatch
остаётся false для agreement даже при совпадении balances/fees/instructions.
Cross-method getTransaction/getBlock differences (например rewards null/[]) не
нормализованы ради совпадения. Следующий gate — доступ к полным историческим logs.
Evidence: docs/evidence/helius-direct-2026-10-02.json; текущий отчёт: docs/helius-audit.md.


## D32: границы Helius и независимая проверка cohort

2026-10-02 trace gate проверен четырьмя targeted calls: getTransaction(json) и
getTransactionsForAddress(full/jsonParsed), по две signatures. Все HTTP 200, та же
обрезка; documented untruncated LaserStream не поддерживает historical replay.
Этот доступ не годится для **полной архивной trace-сверки**, но остаётся кандидатом
для точных state/transaction checks. Processed/enhanced data и current simulation
не являются восстановлением historical logs. Полный report — docs/helius-audit.md.

По прямому поручению выполнена альтернативная ограниченная проверка, заменяющая
прежнюю остановку любой работы до полного trace agreement. Она не меняет критерий
самого full agreement. Шесть счетов заранее заданы frozen checkpoint (pool, 2 vaults,
config, 2 mints). Scope diagnostic учитывает весь block и разделяет writable/readonly/
no reference; несовпадающие keys, неоднозначные flags и порядок дают отказ. Failed tx
не исключаются из potential writers. Ноль writers не доказывает переходы состояния.
Полнота этого списка экономических зависимостей отдельно не квалифицирована.

Первый активный block 452408963 совпал целиком: 1 154 tx, включая один potential writer,
и parent link к 452408962. Всего 1/2 blocks / 2 462 из 2 464 tx matched; 10 blocks остаются,
full-range false. Readonly mismatch первого блока сохраняется. Replay invocations/log
guards не обходятся. Следующий gate — полная история cohort с fail-closed traces.

Current finalized snapshot pair: Helius получил семь счетов slot 452651827; public RPC
timeout, поэтому same-bank values не сравнивались. minContextSlot — нижняя граница,
не архивная фиксация bank. Сеть остановлена на первой паре. Latencies отдельных reads
не являются исполнением/throughput. 252 tests; raw+plans+hashes и результаты:
docs/evidence/helius-traces-2026-10-02.json. Инфраструктурная независимость не доказана.


## D33–D34: source audit закрыт, воспроизводимый economic dataset

По поручению пользователя 2026-10-02 текущая независимая сверка достаточна для исходной
цели второго RPC. Дозагрузка 10 blocks и идеальная trace-сверка всей сети отменены.
Две усечённые Helius traces, incomplete full-range agreement и отсутствие same-bank
current-state comparison остаются фактами; gates не превращаются в pass. Пересмотр
нужен только при влиянии конкретной неполноты на новую задачу CPMM.

Следующий реализованный этап — economic state tape и conditional quote grid из
существующих journals. Export/verify offline, coherent SQLite backup, source snapshots,
настоящие receipts и raw hashes. Все tx, в том числе failed/no-op, сохраняются в
прошедших audit интервалах; failed intervals отдельно карантинируются целиком.
Нет восстановления прошлого из будущего endpoint. Mint/epoch/fee semantics используют
existing ParameterHistory и CPMM math, не отдельную упрощённую копию.

`historical_available_ns=null`; evidence_ready_ns — максимум receipts всего journal,
включая endpoint evidence, не время доступности историческому агенту. Никаких ложных
feature timestamps или автоматического допуска к training. Размеры 10/100/1000 ppm
— явно заданный инженерный probe; quotes не observed fills. Reverse leg использует
hypothetical after-state forward leg; own impact не переносится на исторический рынок.
Старый synthetic-only replay gate остаётся. Dataset не использует generic fee_bps Pool.

Первый corpus: 4 bundles, 16 consistent intervals, 5 pools, 1 889 tx, 1 921 states,
11 430 quotes и 96 unavailable (classic checkpoint timestamp unknown). Reproduction
и checksums прошли, новых RPC нет; 264 tests. Дальше causal observations/features,
gap masks и forward receipts для подготовки пригодного temporal research sample.
Точная схема/ограничения: docs/economic-dataset.md; measured build evidence:
docs/evidence/economic-corpus-2026-10-02.json.


## D35: наблюдённая доступность и prospective corpus

Snapshot observations имеют фактические received/decode-available timestamps;
feature rows — as_of и feature_available, earliest decision не раньше publication.
Требуется Clock того же finalized bank. Старый state tape с unknown availability не
допускается через этот интерфейс. 12 простых features computed одинаковой CausalBook
в runtime/offline; нет fitted scaler, будущих corrections или blanket carry через gaps.
Duplicate bank не обновляет age; missing, stale, warmup, clock-ahead, timeout и
slot conflicts остаются masks, а не числовыми нулями. Между polls trade flow unknown.

Targets schema 2: observed log reserve-price change, не executable return. Endpoint
snapshot available >= decision+horizon, slot новее start, с bounded tolerance;
per-pool coverage, censoring и actual label maturity обязательны. Calibration labels
с maturity на/после frozen cutoff purged. Feature carry-forward в пределах freshness
может быть допустим, но не является fresh endpoint target. Original schema 1 сохранена
как история; новый artifact не меняет raw/frames и ссылается на parent manifest.

Первый сбор: 31 успешный snapshot, 1 transport timeout, 4 planned calls не отправлены;
no retries. 33 feature rows / 29 usable, v2 partitions 8/4/7/14 (calibration/evaluation/
purged/censored). ~109 s и три pools — инженерный sample, не ML-ready corpus.
Actual endpoint horizons 20.129–32.554 s, не точные 20 s. Оба artifact offline/wheel
reproduced, 280 tests. Контракт, flags и safe error log:
docs/causal-forward.md; docs/evidence/causal-forward-2026-10-02.json.

Далее — более длинный prospective сбор возобновляемыми bounded сегментами, не заполнение
пропусков задним числом. Execution-conditioned targets требуют следующей отдельной
проверки costs/admission/delay; synthetic-only replay и запрет ML/RL/paper/live сохранены.

## D36: сегменты prospective corpus и execution-conditioned targets

Реализованы `prospective.py` (неизменяемые bounded segments, persisted reservations,
crash evidence, hash-linked seals, общий бюджет/cutoff, эксклюзивный OS lock) и
`execution_targets.py` (условные size/delay/slippage/holding probes). История CausalBook
не переносится через границы; tails censored. Interrupted сегмент не участвует в labels,
raw bytes сохраняются. Completed/failed сегменты offline воспроизводятся с pinned source.

115 новых valid snapshots / 117 corpus requests плюс один isolated probe; 120 features,
112 usable. Два HTTP 200 ответа имеют Clock.slot на единицу больше context.slot;
строгий decoder остановил обе кампании. Probe между ними был согласован, однако
повторный отказ исключает продолжение серии без расследования. Ни поля, ни guards
не исправлены ради приёма данных. Два original captures — regression fixtures.
Источник проблемы не установлен; это наблюдаемое нарушение текущего checkpoint
контракта, не доказательство universal Helius failure. Historical audit D33 закрыт.

Условная execution model: фиксированный по decision reserve gross input, future
entry quote с minOut, fixed holding от entry publication, отдельные exit decision и
future fill. Integer CPMM/creator/Token-2022 fees; target_available по наблюдённой
publication последнего нужного snapshot. Неудачный выход оставляет позицию и unknown
PnL, censored не заменяется нулём. Network costs, inclusion, native FX, rent/ATA и
persistent own impact неизвестны, поэтому net_pnl всегда null. Модель independent
future snapshot reset — явное контрфактуальное допущение, не калиброванный симулятор.

Получено 960 вариантов: 432 conditional roundtrip, 4 entry unfilled, 8 exit unfilled,
516 censored. 67 distinct decisions имеют хотя бы один mature scenario, evaluation=0:
ни одна frozen campaign boundary не достигнута. Cutoffs не перенесены задним числом.
234.789 s coverage двух сегментов с gaps недостаточно для ML. Следующий gate —
согласованный текущий snapshot источник, затем новые длительные сегменты и отдельная
калибровка исполнения. Контракт/результат: docs/prospective-campaign.md и
docs/evidence/prospective-execution-2026-10-02.json.

## D37: согласованность bank не равна поддержке layout

Strict Clock.slot=context.slot сохранён и подтверждён reference Agave. Bounded public
RPC preflight — шесть fully supported snapshots прежней когорты; новый первый economic
collection response coherent, но содержит неизвестные config bytes. Classic control
с другим config повторил protocol-domain отказ при coherent Clock. Нельзя расширить
padding whitelist или молча перенести старые fees в новое состояние.

Диагноз: два разных AmmConfig имеют u64=50000 по offset 116, который pinned/current
public source называет reserved padding. Это наблюдаемый raw факт, не подтверждённое
значение комиссии/upgrade. ProgramData read timeout не позволил установить deployed
upgrade metadata. До выяснения семантики обе когорты не допускаются к economic features.
Новые согласованные preflight captures не имеют online feature publication и не
превращаются задним числом в observations. New accepted prospective=0; old corpora
не удалены. D33 whole-chain historical trace milestone остаётся закрытым.

Origin pinning в campaign schema 2 запрещает смену endpoint под прежним alias; origin
не содержит секретов и не доказывает backend independence. Explicit closure запрещает
resume. Диагностический kind не принимается CausalBook. Corpus quality report независимо
проверяет label maturity, frozen cutoff, actual endpoint availability, no cross-segment
legs, failed-exit inventory и unknown net PnL. HTTP response delay, chain Clock age,
feature computation и conditional observation delay — отдельные distributions.

11 RPC calls: 9 HTTP200 / 2 timeout, 8 coherent snapshots / 6 full preflight passes,
2 unsupported configs; 0 accepted forward snapshots, 3 masked features, 24 censored
scenarios. 313 tests, smoke/build, offline/wheel QA прошли. Требуется установление
поддержанной deployed семантики либо ошибки source responses до следующего economic
сбора. Contract/evidence: docs/checkpoint-coherence.md и
docs/evidence/checkpoint-coherence-2026-10-02.json.


## D38: подтверждённая семантика config и результат нового сбора

Прежний D37 вывод «неизвестные bytes» уточнён новым evidence: official SDK
cc33ec28a8921a35609e83293e9e07ad830b0779 и docs
3699439bcad7ebdb7d65ba62cb896e93ecd3a204 называют word@116 creator fee share.
50000 означает 5% накопленного creator counter для protocol при collection;
per-creator PDA может менять ставку. Shared остаётся в vault и переходит в excluded
protocol counter; creator payout уменьшает vault и creator counter. Effective reserves
и swap quotes от этого перераспределения не меняются. Swap fee не повышена на 5%.

Поддержан explicit config_layout_version=2 для семи linked accounts с Clock.
Default legacy version1 остаётся прежним. V2 требует share<=1e6 и нули bytes124+,
не меняет account owners/linkage/Token-2022/Clock/availability guards. Новые plans
фиксируют profile2; observation хранит default rate и unknown effective override
отдельно от swap Fees. V2 checkpoint запрещён в legacy replay/transition. Это не
поддержка новой collection execution: нужны PDA, history и independent CPI witness.

Read-only probe: public timeout20.331s; Helius coherent configs context452694796
(оба word50000), ProgramData upgrade_slot452376460. Это не same-bank cross-provider
comparison и не доказательство бинарной эквивалентности public Rust source.

Новая immutable campaign work/prospective-config-v2-01: прежние три pools, public
origin, max3 segments ×20 rounds ×10s, 180 max requests. Фактически два сегмента:
по одному valid snapshot, затем timeout15.008/15.007s следующего getMultipleAccounts;
HTTP/RPC code null, transport cause unknown. Один resume после cooldown; третьего
сегмента нет, closure.json запрещает продолжение. Ни успешные raw, ни features не
скачивались повторно для воспроизведения; второй snapshot — новое prospective время.
Warmup после каждого сегмента сохраняется: 6 masked rows, 0 usable, 48 censored
execution scenarios, 0 mature decisions. Gap между decision frames 70.385s не заполнен.
HTTP successful response times 0.795/0.692s, total response bytes7048; usable chain age
и execution-delay distributions пусты. Это не trade latency/backfill throughput.

7 RPC суммарно: 4 HTTP200 / 3 timeout. 327 tests, smoke/build и offline dev/wheel
reproduction прошли; old D36/D37 artifacts проверены оригинальными pinned wheels.
Evidence: docs/evidence/creator-fee-share-2026-10-02.json. Следующий gate — восстановить
доступность coherent snapshot source после отдельной диагностики/изменения условий,
затем новый bounded plan и длинный corpus. ML/RL/paper/live не запускались.


## D39: доступный coherent transport, prospective corpus и support QA

2026-10-02 выбран canonical public mainnet https://api.mainnet.solana.com и explicit
stdlib_persistent_v1. Mainnet genesis и 12/12 strict preflight snapshots подтверждены.
Старый и canonical public host разрешились в один IP; это не независимые providers.
Диагностика 24 RPC: 20 HTTP200  / 4 timeout. Как минимум два отказа наблюдались на
connect/TLS до HTTP; exact upstream cause остальных неизвестна. User-Agent отдельно
не устранил проблему. Helius Clock mismatch остаётся rejected, QuickNode не запрашивался.

Кампания 01: 4×30×5 s, 303 requests / 302 valid, три полных сегмента; четвёртый прерван
connection error на headers, 3.856 s, HTTP/RPC code null. Raw/error сохранены;
скрытого reconnect нет, исчерпанный budget и closure запрещают продолжение. Кампания02:
2×30×5 s, 180/180 valid, отдельные процессы и новое TLS-соединение на каждый сегмент.
Реальный restart/resume прошёл. Предпочтительный дальнейший режим — connection reuse
внутри сегмента, закрытие на границе, одна invocation на сегмент. Это bounded result,
не production SLA. Все decoder/Clock/linkage/freshness/censoring guards сохранены.

Всего 482 новых accepted snapshots  / 483 corpus requests, 483 feature rows,
464 usable, 781.187 s суммарного внутрисегментного coverage. Gaps не заполнены.
Targets: 3864 scenarios, {'censored': 1250, 'conditional_roundtrip': 2472, 'entry_unfilled': 76, 'exit_unfilled': 66}.
345 distinct mature decisions, в том числе 141 engineering evaluation.
Frozen cutoffs не переносились; final holdout отсутствует. Complete all-eight-scenario
decisions=307; chronological all-pool nonoverlap windows=15 суммарно
по двум отдельным кампаниям/partitions. Это не оценка independent effective sample size.

HTTP response distributions, возраст chain Clock и conditional observation delays
измерены отдельно. Network costs/admission/impact неизвестны; net_pnl=null.
Новый coverage.py проверяет grid/maturity/overlap и variation, не обучает модель и не
фильтрует online decisions по будущему. Обе пары campaign/targets/quality/support
воспроизведены offline и installed wheel work/persistent-wheel-01; D38 сохранён и
проверен прежним pinned wheel. 346 tests, lint/format/mypy, smoke/build прошли.

Artifacts: work/prospective-persistent-01/02, work/persistent-targets-01/02,
work/persistent-quality-01/02, work/persistent-support-01/02. Provenance/plans/cutoffs/
errors/source/seed(null)/hashes — docs/evidence/persistent-source-2026-10-02.json;
контракт docs/persistent-source.md. Следующий этап — coverage разных календарных окон
на том же bounded режиме и оценка execution uncertainty перед ML. Минут/трёх pools
недостаточно; market universe, simulator calibration, training/edge не квалифицированы.

## D40: условия bounded пилота Alchemy HTTP+WSS (2026-10-03)

Пользователь согласовал проверку гибрида; основной pipeline пока остаётся D39.
На момент handoff пилот не был проведён; сведения о доступе были сообщением пользователя.
Теперь v1 выполнен: MCP и отдельные MEMECOIN_ALCHEMY_HTTP_URL/WSS_URL в ignored
`.env.local` проверены. Итог ниже. Секреты не выводить/запрашивать в чате;
платные действия/upgrade не разрешены.

Начальный контракт: finalized WSS notifications — дополнительный raw канал и trigger,
HTTP — coherent snapshot authority. Сохранить общий observation/feature/target pipeline;
не склеивать последние values разных accounts в якобы same-bank snapshot. Сохранить
pool/config/mint/vault/Clock linkage и versioned decoder. Account updates сами по себе
не дают доказательства каждого swap или transaction order. Если потребуется иной
уровень stream semantics, его квалифицировать отдельным adapter, сохранив исходный ответ.

До сети зафиксировать cohort, два сопоставимых режима Alchemy HTTP / HTTP+WSS, длительность,
request/byte/subscription budgets, commitment, source/code versions, retry/stop/recovery
policy, критерии сравнения и отчёт. Получить строгий HTTP baseline прежде admission WSS
observations. Начать с finalized; confirmed/processed и rollback — отдельная проверка.

Пилот должен проверить:

- Snapshot/stream handoff без скрытого окна потерь; границы доказанной полноты и порядка
  сформулированы для конкретных методов. Сверка конечного snapshot не доказывает всех
  промежуточных изменений, тишина WSS не доказывает отсутствие активности.
- Forced disconnect, restart, late/duplicate/out-of-order сообщения и backpressure:
  gaps обнаружены, история сохранена, восстановление bounded и проверяемое. Если API
  не позволяет восстановить нужную историю или protocol replay не поддержан, интервал
  остаётся непригодным; HTTP доступность не разрешает выдумывать отсутствующие states.
- Raw сообщения, subscription/connection provenance, фактические receipt/decode/feature
  availability timestamps, immutable plan и ошибки. Recovery не переписывает прошлую
  online доступность; gap masks, warmup, censoring, maturity и split guards сохраняются.
- Побитно/семантически воспроизводимые observations/features/targets согласно существующему
  контракту. New creator fee share поддержан только для snapshot/quote profile2 (D38),
  не для полноценной collection reconstruction. Synthetic replay gate остаётся.
- Реальное улучшение пригодного calendar/change coverage либо нагрузки относительно
  HTTP-only при неизменном качестве. Отдельно измерять HTTP response time, WSS delivery/
  receipt age, chain-state age, recovery throughput, CPU/memory/traffic; ни одна из этих
  величин не объявляется измеренной trade execution latency. Учитывать subscription/
  bandwidth limits существующего доступа; платные действия не разрешены.

Основной data pipeline менять только после отчёта и отдельного зафиксированного решения.
Итоговый документ должен описывать доказанную область, несоответствия, unresolved gaps и
условия перехода/отказа. ML readiness, execution qualification и edge пилот не доказывает.
Официальные ориентиры, прочитанные при архитектурной оценке (актуальность перепроверить):
https://www.alchemy.com/docs/reference/account-subscribe и
https://www.alchemy.com/docs/reference/solana-subscription-api-endpoints.


### Фактический результат D40-v1

Два 120s segments на прежних3 pools:144 HTTP-only /72 hybrid /144 public strict
snapshots, плюс 6 Alchemy preflight;366 coherent account responses всего. Сохранены raw,
receipt/decode/feature availability, config/profile2, source/plan manifests. Runtime WSS
— отдельный stdlib read-only RFC6455 transport,16 dependency subscriptions +root,
queue128, message64KiB, bounded cumulative bytes/count и ack deadline. Никаких
account notifications как атомарного pool state. Subscription ack barrier перед anchors;
slot>anchor остаётся dirty. Root — liveness, не доказательство sequence completeness.

Два forced disconnect оставили masks, затем resubscribe и новые anchors за1,219/1,468s.
Restart новым процессом обнулил историю; tails/gaps не склеивались. HTTP recovery здесь
только current-state resync, не backfill historical writes. Ошибка/переполнение закрывает
stream; незапечатанный interrupted segment запрещает следующий, сохраняя original bytes.
Silent omitted hint с изменившимся HTTP state вызывает uncertainty gap; unchanged endpoints
не доказывают полноту. Live crash/overload не инжектировались; adversarial cases synthetic.

WSS1158 messages/258666bytes; все raw receipts и derived output проверены offline/dev/wheel.
HTTP-only138/144 usable, hybrid120/150 (120/144 scheduled); payload520818 против524304bytes.
Calls−50% не компенсируют ухудшение quality: criterion failed. Наблюдается config noise
и эффект coarse scheduler (~10s actual hybrid); нет чистого causal transport win.
Итог 364 tests (346 прежних +18 WSS/integrity), smoke/build прошли.
Основной D39 collector остаётся; v2 требует отдельного frozen matched-cadence плана.
[Контракт/ограничения](../docs/alchemy-pilot.md),
[evidence](../docs/evidence/alchemy-pilot-2026-10-03.json). Training/edge false.


### D40-v2 и D41: окончательная роль транспорта

D40-v2 исправил scheduler/content coalescing и подтвердил matched10s/common feature
cadence, сохранив separate steady/stress. 421 Alchemy strict snapshots,144 public,
1274 WSS messages; initial EOF и единственный serial-ack follow-up сохранены отдельно.
Adaptive экономит31,94% HTTP, usable хуже4,17п.п., payload выше28,49% HTTP5: criterion
false. WSS diagnostic/off; никаких склеенных WSS pool states или lossless history.
[Контракт/результат](../docs/alchemy-pilot-v2.md),
[evidence](../docs/evidence/alchemy-pilot-v2-2026-10-03.json).

Для D41 явно выбран Alchemy HTTP5 persistent TLS с прежним prospective pipeline,
finalized/profile2 и fixed cohort. План180 max остановлен на первой connect ошибке
15,0718s; HTTP ответа/новых snapshots нет. DNS/TCP/TLS подфаза неизвестна. Closed bundle
не resume, retries0. Existing targets/quality/support дали3 masked rows/24 censored
scenarios/0 mature. Raw references не становятся новыми online data. Строгая snapshot
корректность Alchemy доказана только на D40; availability нового окна не квалифицирована.
[Итог D41](../docs/qualified-http-corpus.md),
[evidence](../docs/evidence/qualified-http-corpus-2026-10-03.json). Guards/CPMM/features/
targets неизменны; далее cold-connection observability, потом новый bounded plan.


### D42: bounded operational mode и execution uncertainty

Диагностика отдельно наблюдает прежние DNS/TCP/TLS функции, не меняет их аргументы,
TLS trust или RPC guards. План ограничил4 connections/8 requests, раунды разнесены60s.
Все probes прошли; причина прежнего D41 failure неизвестна. Выбор Alchemy по frozen
правилу при равном результате с public не доказывает превосходства provider или SLA.
Mode: persistent verified TLS в сегменте, новый процесс/соединение между сегментами,
no retries внутри failed окна; WSS off, fallback нет. Новые prospective окна имеют
отдельные планы/cutoffs и реальные receipt times; промежутки не backfill.

Offline tools/execution_uncertainty.py проверяет original targets до подсчёта сценариев.
Сетка extra roundtrip cost0/5/10/25/50/100bps — assumptions в input-asset units, не
измеренный native tariff. Censored/unfilled отдельно, никаких zero-fills, сумм atomic
units разных tokens или overlapping hypothetical PnL. Delay0/5s paired только внутри
одинаковых decision/pool/direction/size/partition. Это QA неопределённости, не новая
feature, policy или profitability result; net_pnl/inclusion/network costs unknown.
[Контракт и результат](../docs/cold-connection-corpus.md).

Результат двух окон D42: 360 accepted/348 usable, 268 distinct mature decisions,
135 engineering evaluation, 12 chronological nonoverlap windows. Фактические
calendar bounds/разрывы/источники/config/code/seed/QA — в
[evidence](../docs/evidence/cold-connection-corpus-2026-10-03.json). Все network calls
прошли; прежние failed artifacts неизменны. Per-window cutoffs — engineering QA,
не единый global train/test split и не final holdout. Следующий приоритет — окна в
разные часы/дни и отдельное наблюдаемое execution-cost evidence; ML gates прежние.

Strict calendar created_ns criterion текущего D42 false:599,994084s (недобор5,916ms),
при actual segment starts600,037526s. Offline audit сохранил нарушение, future anchor
исправлен; raw/times/план не менялись, дополнительных network requests не было.


### D43: active finite schedule и наблюдённые transaction costs

Шесть frozen окон по часам/дням,192 maximum RPC на окно (180 corpus+12 execution),
1152 total. First error stops window,2 failed windows stop whole plan, missed/crash
не retry/backfill. Pinned package/tools/reference hashes, global OS lock, guard deadlines,
`.env.local` только runtime. LaunchAgent `ai.memecoin.d43.calendar01` действительно
работает независимо от чата; root work/calendar-execution-01. Текущий inventory
проверять до нового запуска. Первое окно completed:180 accepted/174 usable,135 mature,
5 nonoverlap; другие5 pending. Guards/features/targets/CPMM не менялись.

getSignatureStatuses добавлен только в read-only allowlist. Address listings confirmed,
3 polls0/5/15s, до6 finalized tx: сохраняются meta.fee/err/CU и настоящие receipt times.
Не обещается доказанный swap type/full protocol replay. Первые6 tx successful, fees
15 001–154 999lamports; timing6/6 left-censored (already finalized), inclusion неизвестен.
Meta.fee не включает все расходы стратегии; приписывать fee одной ноге swap/переводить
в input-asset bps без маршрута/FX нельзя. Net targets остаются null. Документ:
[calendar execution](../docs/calendar-execution.md),
[evidence первого окна](../docs/evidence/calendar-execution-2026-10-03.json).


### D44: read-only sealed window → ML engineering bundle

Optional memecoin_ml проверяет window seal и exact execution_targets reproduction,
не меняя source corpus/CPMM/features/targets. Bundle хранит исходные decision/availability
и target maturity, group histories по campaign segment/pool/causal segment; unusable
и gaps>15s сбрасывают контекст. Missing flags не теряются; unknown costs остаются unknown.
Восемь scenarios не размножают independent decisions. MAX maturity siblings используется
для новых global smoke partitions; прежние per-window calibration/evaluation cuts не
переименованы в ML folds. Export174 usable из180 source starts;1392 variants. Сценарии
censored на usable starts остаются в bundle и counts, без fabricated labels.
Artifact dirs append-only/new; dataset/run/checkpoint checksums и source binding,
raw/checkpoints вне Git.70 frozen D43 protected files совпали до/после; RPC не было.
Рабочая реализация пока eager/bounded, research qualification false, multi-window merge
и dedup следующий D45. [Контракт](../docs/ml-foundation.md), [evidence](../docs/evidence/ml-foundation-2026-10-03.json).


### D45: immutable multi-window registry

Stdlib memecoin_registry связывает одну frozen calendar plan, cohort/target semantics,
reference seal и converter sources. Development admission: exact seal inventory/outcome,
no unsafe paths/symlinks, semantic D44 export, scenario grid/availability verification,
source seal recheck; duplicate(pool,decision_ns) между окнами — отказ, не новые samples.
Partial/failed/missed не становятся training corpus. Pending не добавляет coverage.
Writer lock, fsync/staging/atomic revision publish, immutable objects и parent hashes;
failed attempts сохраняются, idempotent restart не переписывает старые artifacts.
Registry_available/as-of не подменяет source decision/online availability: folds —
offline historical protocol, не утверждение прошлой доступности обученной модели.

Первый реальный registry1window:180 starts,174 usable,6 unusable;1392 variants,362censored;
122 complete-grid decisions,5 greedy nonoverlap,279.775962s union observed spans.
Holdout окна4/5 только hashes/operational metadata; raw/targets остаются в D43, semantic
verification deferred until release. Whole crossing window quarantine. Routine sync/audit
не выводит holdout outcomes/returns/status class counts; читать старый full D43 target
inventory после появления holdout нельзя без фиксации exposure. Это process guard,
не OS protection от владельца raw. [D45](../docs/corpus-registry.md).
