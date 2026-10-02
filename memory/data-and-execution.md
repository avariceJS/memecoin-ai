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
как одинаковое состояние. HTTP failures и неполный план не дают agreement. Независимый
оператор пока недоступен; qualification не повышена. Отдельно измерены monotonic response
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
