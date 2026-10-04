# Цель и границы проекта

## B10 — сохранение живого продукта включает восстановление отказов, 2026-10-04

Реальная эксплуатация остановила B09 paper при работающем collector. Приоритет B10 —
восстановить продукт с точной бухгалтерией и диагностикой, без переустановки source.
Активны B09 source + B10 paper; недельная автономность остаётся целью, а не достигнутым
свойством. Unknown integrity/guard failure не лечить бесконечными restart. Новый источник
должен пройти отдельный semantic admission, новые модели — честные будущие observations.
[Актуальный статус](state.md), [B10](../docs/brain-operational-recovery.md).

## B09 — работающий product loop снова приоритетен, 2026-10-04

Paper восстановлен через отдельный hot acquisition priority:6liquid markets/120s,24 total
universe, backgroundhistory/cold независимо по cadence пододним provider budget. Существующий
На сдаче B09 runtime был активен на 72h; B10 сохраняет source, заменив отказавший paper. Цель week/month остаётся,
но фактически проверены несколько циклов, не сутки. GT пока единственный live source;
canonicalmulti-source требует отдельного semantic admission/cross-check.
[Текущий статус](state.md), [решение/ограничения](../docs/brain-hot-lane.md).

## B08 — устойчивость продукта, 2026-10-04

Цель пользователя — week/month unattended paper с широким рынком, corpus и честными future
comparisons. B08 подготовил incremental store и отдельный seven-day source, но live gate
не прошёл:5fresh вместо≥6. B04 остановился по429; B07 UI/accounting сохранены, новые входы
paused. Не описывать продукт как непрерывно торгующий. Ближайший приоритет — восстановление
live feed отдельным B09 plan, а не backbone/новый ML winner. Исследование не должно требовать
остановки product loop, но outage источника нельзя скрыть или заполнить историей.
[Актуальный статус](state.md), [итог](../docs/brain-continuity.md). Предыдущие статусы ниже — история.

## Новый приоритет B07 — живой виртуальный product loop, 2026-10-04

Прямое указание пользователя заменяет прежний запрет paper: разрешены непрерывные
виртуальные решения/позиции/результаты, видимый Control Center и параллельное supervised
переобучение. Research perfection и F01 не блокируют раннюю честную exploratory вертикаль.
Реальные деньги, подпись/отправка транзакций, кошельки и paid upgrades по-прежнему запрещены.
Агрегированные price-bar labels и виртуальная бухгалтерия не являются execution truth.

Приоритет скорости: разумно использовать доступные free limits для полезной history/live
информации, без мультиаккаунтов, повторной загрузки уже сохранённой истории и обещаний
«сильной нейросети» от одного расхода quota. Market-data и execution layers независимы;
Axiom не обязательная зависимость. Canonical local history/raw architecture сохраняется,
но B06 raw PumpSwap admission не prerequisite для отдельного GT price-proxy paper lane.

B07 запускает отдельные виртуальные portfolios empirical/linear/GRU и daily candidates
на существующем B04 data feed. Champion empirical не заменён. Это bounded rollout до
2026-10-07T00:30:23.405597Z, не обещание weekly unattended. Длительность следующего source
rollout, расширение universe и полезная history — следующий B08. [B07](../docs/brain-paper-loop.md).
Разделы B01–B06 ниже — история до нового разрешения; их прежний paper-запрет заменён.

## Авторизация B01–B06 — предыдущий контекст, 2026-10-04

Пользователь отдельно открыл historical backfill, расширение universe, continuous corpus,
регулярное supervised training и shadow evaluation. Для этого независимого exploratory
track F01 readiness и прежнее smoke-only ограничение больше не блокеры. Использовать
D44/D47 stack с собственным честным bar-data contract; confirmatory F01/его reserved
holdout остаются закрытыми по прежним правилам. Brain-v1-dev обучен; B03 расширил corpus
до 15237 bars и train diversity до 4 pools. B04 активировал отдельный collector-only job
после нового availability review; automatic training выключен, B03 остаётся REJECTED/STOP.
Champion empirical сохранён; linear-winsor — кандидат отдельного будущего shadow опыта.
Прежний expiry/12000publicGET/paid0 не продлён; [статус](../docs/brain-availability-normalization.md).
B05 уточнил приоритет: двигаться к длительному самостоятельному сбору широкого рынка,
разнообразному corpus, bounded retraining и честному future shadow с изоляцией плохих
источников/пулов. До масштабирования выбрана разделённая source architecture; GT сохраняется
как discovery/history/cross-check, raw transactions — candidate primary после venuequal.
B04 job без изменений; B06 реализовал raw interpreter, но production admission закрыт
из-за ABI/Token-2022 drift. СледующийB07 — deployed protocol profile и corrected bounded
indexed probe; linear-winsor challenge отложен. [B06](../docs/brain-pumpswap-events.md). [B05](../docs/brain-source-architecture.md).
Цель ближайших дней — содержательная модель и цикл её улучшения, не обещание edge.
На момент B06 paper bot не был разрешён; это ограничение заменено B07. Реальные деньги,
подпись/отправка сделок, paid upgrades и RL по-прежнему не разрешены.

## Цель

Создать собственную обучаемую ML-систему для торговли мемкоинами, способную находить возможности, выбирать токены и управлять позицией. Главный результат исследования — достоверное свидетельство устойчивого преимущества на будущих данных после исполнения и всех существенных издержек. Создание сложной нейросети само по себе не является успехом.

Система может не найти edge. Отрицательный достоверный результат полезнее ложноположительного бэктеста. Архитектура не гарантирует прибыльности.

## Контекст пользователя

- Начальный рынок — Solana memecoins; точная торговая вселенная ещё не выбрана.
- Первый этап — long-only, без плеча и реальных денег.
- Два MacBook Apple M4 Pro, примерно по 24 GB unified memory. Допускается длительная работа.
- Собственная модель на PyTorch, работающая независимо от LLM. Codex выполняет разработку, не является торговым мозгом.
- Axiom рассматривался как платформа, но не выбран источником данных или обязательной зависимостью.
- Требуются критическое сравнение альтернатив, эксперименты, out-of-sample, walk-forward и paper trading.
- Новая версия модели не становится основной автоматически из-за высокого PnL.
- Последняя концепция и Memory Bank приняты как база. Пользователь разрешил автономную реализацию фундамента и push в origin/main; реальные деньги не разрешены.

## Что должна уметь будущая система

Наблюдать переменное множество токенов, оценивать возможности и риск, выбирать активы или cash, открывать и изменять позиции, учитывать занятый капитал, pending-транзакции и альтернативные возможности, выходить при допустимом исполнении. Отсутствие сделки является полноценным действием.

Преимущество должно быть выученным из данных; механические правила допустимости, учёт баланса, ограничения риска и execution logic могут и должны быть детерминированными. Полный отказ от if/else не является требованием.

## Что не входит в текущий этап

- Production real-money bot и неограниченный платный collector. Bounded virtual paper разрешён B07. Bounded continuous Brain collection и регулярное supervised training разрешены отдельным решением B01. Reference data/replay/research компоненты разрешены и реализуются; их фактический статус — в `state.md`.
- Реальная торговля, отправка транзакций, создание или подключение кошелька.
- Оплата провайдера/подписок/облачного GPU и развёртывание. Публикация завершённых коммитов в указанном GitHub origin/main разрешена.
- Обучение LLM или торговля через LLM.
- Обязательные RL, Transformer, GNN, MoE или GraphRAG.

## Что известно о ресурсах

Память двух Mac не является общим пулом 48 GB для одной модели. Unified memory делится с ОС и остальными процессами. D44 измерил малые CPU/MPS candidates на M4 Pro; производительность больших моделей и полной training/inference цепочки ещё не установлена. Размер модели определяется не только памятью весов, но и контекстом, активациями, batch size, optimizer states и стоимостью подготовки данных.

Предпочтительное разделение после начала реализации: первый Mac — сбор, качество данных и живой paper-контур; второй — обучение, replay и исследования. Независимые эксперименты на обоих возможны при наличии ресурсов. Distributed training одной модели не является стартовым требованием.

## Открытые параметры перед разработкой

| Параметр | Статус и зачем нужен |
|---|---|
| Источник истории и live-потока | Brain B01: public GeckoTerminal OHLCV, initial audited limitations; execution lane Alchemy отдельно |
| Вселенная и типы пулов | Не выбраны; наблюдение шире первой области достоверного исполнения |
| Возрастные группы токенов | Не выбраны; не исключать ранние запуски из исследования без проверки |
| Исследовательский капитал и размеры сделок | Не заданы; нужны для capacity, fixed fees и price impact |
| Валюта основной цели | Не выбрана; показывать SOL и USD, затем зафиксировать primary objective |
| Риск-бюджет, drawdown и concentration limits | Не заданы; нельзя придумывать как принятые пользователем |
| Реальные задержки | Не измерены; определяют доступные горизонты |
| Частоты и holding horizons | Определить экспериментом |
| Период истории и разнообразие режимов | Есть ограниченные B01 history/prospective данные; необходимое разнообразие ещё не установлено |
| Budget на данные и дополнительный compute | Не задан |
| Критерии значимого экономического улучшения | Формализовать до выбора победителей |

Обратимые исследовательские допущения можно оформлять явно, но пользовательские финансовые ограничения нельзя объявлять установленными без их определения. Фактический технический этап и достигнутые gates — в state.md; финансовые параметры и economic research specification остаются открытыми; conditional forecast F01 specification закреплена в D46.


## Уточнение авторизации 2026-10-03 — независимый ML foundation

D44 реализует optional ML/training и первую сменную temporal architecture отдельно
от работающего frozen D43. Пользователь разрешил engineering smoke/integration runs на
synthetic/имеющихся данных. Это не признание corpus достаточным и не market-result claim.
Обучаемые heads пока conditional return-before-network-costs/statuses, не trading policy.
Техническая готовность trainer/checkpoint/inference не снимает corpus/execution/evaluation
gates. D43 не изменять/не переустанавливать. [Итог D44](../docs/ml-foundation.md).


D47 подготовил admission-aware F01 runner/ledger/scoring на fixtures и engineering
data. Это техническая готовность вычислительной процедуры, не достаточность corpus
или разрешение исследования. Separate future sampling/ingestion qualification и
reviewed admission остаются обязательными; [D47](../docs/f01-runner.md).


D48 материализует будущий F01 sampling contract и sealed development ingestion/readiness
без collector activation и без признания corpus достаточным. Пороговые checks не
заменяют source/coverage/dependence reviews; [D48](../docs/f01-sampling.md).

D49 qualification (2026-10-03) не активировала новый collector: source6/6 strict HTTP
checkpoints подтверждён, но account monetary cap/plan и production service envelope
не подтверждены. Paid budget0, D48 calendar/F01 gates неизменны. Следующий D50 — закрыть
эти operational blockers до отдельного решения. [Итог](../docs/f01-collector-qualification.md).

D50 (2026-10-03) закрыл engineering supervisor/runtime gaps через отдельный пакет,
сохранив научные и source bindings. Это не разрешение расходов/collector или доказанный
84day uptime. Actual no-charge/account mapping и full live qualification остаются
D51, затем отдельное activation решение. [D50](../docs/f01-operational-runtime.md).

D51 (2026-10-04) завершил bounded live qualification после owner attestation:180/180
strict checkpoints,299,34s,exact offline/wheel reproduction. Это engineering source
evidence,не ML результат и не calendar activation. СледующийD52 — отдельное operational
решение по unchanged D48/host/allocation; source/service пока отсутствуют.
[D51](../docs/f01-live-qualification.md).
