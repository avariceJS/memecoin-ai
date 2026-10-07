# Brain: полный технический handoff для независимого ревью

**Адресат:** Claude Code / Claude Opus 5.5, независимый технический и ML-ревьюер.
**Дата:** 2026-10-07, Europe/Moscow. **Автор:** текущий coding/research agent проекта.
**Проверенная база кода:** `2b834db345887223f9a1f09b86d869c8ec06293f`.
**Репозиторий:** `https://github.com/avariceJS/memecoin-ai.git`.

Это самостоятельный полный отчёт, а не краткое резюме последнего эксперимента.
Он описывает фактическое состояние, накопленные ошибки и предлагаемый пересмотр архитектуры.
Ревьюеру не предлагается одобрить текущую реализацию. Задача: независимо решить,
что сохранить, что заменить и как получить действительно масштабируемую систему.

## 1. Задача пользователя и границы этого handoff

Мы строим потенциально большой автономный Solana research/trading Brain, а не демонстрационный
forecasting script. Brain должен постоянно принимать большие объёмы market data, сохранять
историю и происхождение, расширять обучающий корпус на порядки, получать новые поколения
моделей и проверять их на независимом будущем рынке. Mac-компьютеры являются первым
вычислительным этапом. Архитектура должна допускать переход к GPU и data-center infrastructure.

V1 -> V2 -> V3 должны означать реальные поколения: существенный прирост полезных данных,
обоснованный training compute, воспроизводимый recipe, независимый benchmark и измеримое
изменение качества. Новый hash весов после секундного refit сам по себе не новое поколение.
Отрицательный результат допустим; искусственное увеличение модели ради номера версии нет.

Пользователь сообщает о двух Mac, мощном локальном железе, платном Alchemy с высоким RPC
throughput, доступе к сильным coding/research models и готовности масштабировать storage/compute.
Платный тариф и его фактические лимиты в этой сессии не аудировались. Старые free-tier budgets
в документах описывают исторические кампании, а не текущий ресурсный потолок проекта.
Наличие paid account не является разрешением покупать дополнительные услуги или обходить
учёт запросов. Неизвестные credentials и финансовые операции не нужны для этого ревью.

Сохранить safety, provenance, temporal correctness, immutable artifacts и честную evaluation
нужно при любом масштабе. Но фиксированные малые cohorts, короткие leases и ручная сборка
каждой кампании не должны становиться архитектурным пределом под видом safety.

В этой работе не менялись код системы, production, champion, AUTO или promotion gates.
Не запускались training, inference, acquisition или большие эксперименты. Выполнены чтение
кода/сохранённого evidence и минимальная read-only проверка owner-host. Protected F01/D43
и reserved holdout не открывались. Предложения ниже не являются установленным новым recipe
или разрешением немедленно перестраивать работающий runtime.

## 2. Как читать степень уверенности

- **[E] Evidence:** прямой код, sealed artifacts/receipts или опубликованный проверенный результат.
- **[I] Engineering inference:** мой вывод из этих фактов, включая предложения по замене слоёв.
- **[U] Unknown:** измерения или необходимого независимого эксперимента нет.

Здесь агрегированы проверки нескольких сессий. Числа старых экспериментов взяты из связанных
первичных отчётов и machine-readable evidence; все training runs заново не выполнялись.
Свежая проверка runtime явно датирована. Данные из разных snapshots не сложены в один corpus.
Слова «causal» ниже относятся к контролируемому вмешательству на конкретном dataset/seeds,
а не к доказанной универсальной причине поведения всех будущих моделей.

## 3. Краткий независимый диагноз автора

**[E] Построен настоящий, но небольшой исследовательский вертикальный прототип.** Есть real
provider data, on-chain ingestion/decoding, versioned storage, causal feature/label pipeline,
детерминированное обучение, immutable models, реальные future forecast/paper циклы, operator
и Control Center. Это не набор синтетических заглушек. Однако production Brain пока получает
узкий price-bar поток; официальный champion остаётся empirical, не GRU.

**[I] Самая сильная часть проекта сейчас: проверяемость. Самая слабая: способность превращать
доступный рынок в широкий постоянно обновляемый learning/evaluation corpus.** Мы лучше умеем
объяснить, почему нельзя засчитать наблюдение, чем непрерывно получать достаточно наблюдений,
которые можно засчитать. Первая способность нужна, но не заменяет вторую.

**[E] Не доказаны:** устойчивое превосходство learned Brain над champion, качественная
conditional calibration, экономический edge, real execution, недельная непрерывная автономность,
работа на 10x/100x/1000x данных или на distributed GPU. Широкий независимый benchmark отсутствует.

**[I] Систему не нужно выбрасывать целиком.** Contracts, raw provenance, immutable releases,
replay/accounting и значительную часть evaluator стоит сохранить. Но experiment-shaped
acquisition orchestration, materialize-then-cap training path и узкий model-selection loop
не должны стать основой масштабирования без существенной замены.

**[I] Сейчас главный bottleneck не доказанная недостаточность GRU и не отсутствие GPU.** Это
сочетание незавершённого data plane, слабой evaluation basis и плохо определённого optimization
objective. Если просто дать текущему recipe больше GPU и pools, эти проблемы масштабируются
вместе с ним. Обратная крайность, годами чинить маленькие cohorts, тоже неверна.

## 4. Что построено за время проекта

### 4.1. Исходный execution/replay фундамент

`src/memecoin_ai/` содержит domain contracts, causal histories, CPMM replay, portfolio/risk,
execution targets, provider checks, reconciliation, prospective collection и research tooling.
Были synthetic fixtures и затем реальные RPC snapshots/transactions. Эти уровни имеют разные
gates: тестирование арифметики и replay не квалифицирует собственное исполнение сделки.

Проверялись pool state, vault/token semantics, fees/transfer effects, availability и разрывы
истории. CPMM adapter не является универсальным adapter для CLMM, DLMM и любых Solana venues.
Наличие on-chain transaction не даёт автоматически корректной контрфактической цены нашей сделки.

Frozen F01 создавался как узкое conditional forecasting исследование с заранее определёнными
action/target semantics, folds, maturity purge и закрытым holdout. Он не был доказательством
торгового edge и не завершён как исследовательский результат. D43 был engineering pilot:
из шести запланированных окон не получилось шести успешных сборов; опубликованный разбор
указывает 0 completed, 1 execution-hydration failure, 2 semantic-checkpoint failures, остальные
не запускались. История не должна переписываться как успешные 48 часов наблюдения.

**Сохранить:** execution honesty и reusable invariants. **Не переносить:** ограниченный F01
cohort/календарь как обязательную форму всей будущей Brain data system.
Источники: [F01/readiness](research-readiness.md), [опыт эксплуатации](brain-experience-review.md),
[contracts](contracts.md), [memory/data-and-execution](../memory/data-and-execution.md).

### 4.2. ML foundation

`memecoin_ml` отделён от data/runtime. Есть empirical, linear, MLP, GRU, TCN и Transformer
варианты, общий feature/action interface, ordered quantiles, masked features, training receipts,
checkpoint/RNG resume, source/data/model pins и CPU/MPS checks.

D44 проверил 15 CPU smoke fits и один MPS GRU fit; microbenchmarks выполнены на реальном M4 Pro.
Маленькие модели нередко быстрее на CPU, некоторые более широкие cases выигрывали на MPS.
Это измерение соответствующих kernels/shapes, не доказательство оптимальной архитектуры Brain.
Первый real-data smoke имел лишь 174 usable decisions; action variants не независимые samples.
Такая база полезна для correctness, но недостаточна для выбора backbone.

Источник: [ML foundation](ml-foundation.md), `src/memecoin_ml/{data,models,training,benchmark}.py`.

### 4.3. Exploratory Brain Development

Отдельный Brain track снял зависимость exploratory learning от завершения F01. Его фактическая
задача: прогнозировать 5m/15m log returns по наблюдаемым USD OHLCV, сравнивать quantiles и вести
виртуальную long-only торговлю. Это другой target, не тот же execution-aware F01 outcome.

Последовательно появились bar-source/shadow, source qualification, hot/cold acquisition,
continuous store, incremental paper, recovery, historical curriculum, raw vacuum, AUTO operator,
двухмашинный workflow и отдельный Watch Market lane. Автоматический snapshot/train/future-exam
действительно запускался на реальных данных. Не все эти компоненты объединены в один масштабный
production data path, и не все новые repo features установлены на owner-host.

Основные packages:

- `memecoin_brain`: bar data, causal features/targets, models, shadow/runtime.
- `memecoin_brain_sources`: raw/indexed source probes, decoders и replay.
- `memecoin_continuous`: canonical store, source migration, hot lane, service qualification.
- `memecoin_collector`: acquisition scheduling/qualification.
- `memecoin_curriculum`: historical shards, admission, sampling, training, future evaluation,
  streaming/mmap utilities и benchmark metadata checks.
- `memecoin_registry`: model/dataset manifests, integrity и qualification contracts.
- `memecoin_paper`: causal virtual accounting, projection, supervision, audit/UI.
- `memecoin_operator`: commands/jobs, source adapters, training orchestration, release, health,
  readmodel, Control Center и Watch Market.

Это логическое разделение полезно. Наличие package с правильным именем не доказывает,
что его масштабируемый path подключён к текущему AUTO.

## 5. Реальная архитектура и deployment

```text
GeckoTerminal OHLCV/snapshots       Alchemy / Solana raw + indexed RPC
             |                                  |
     immutable HTTP captures          immutable RPC captures/journals
             |                                  |
 continuous canonical bar store       versioned decoder / market registry
             |                                  |
             |                         partial quote-event/history lane
             |                         (не полный qualified USD corpus)
             +---- admitted historical bar shards <--- birth-led recruitment
                                  |
                    sealed snapshot + features/targets
                                  |
             B13 materialization -> caps -> weighted CPU fits
                                  |
               candidate selection + immutable registry
                                  |
                 frozen future forecasts / B07 paper
                                  |
                   audit / operator / Control Center

Watch Market (repo, не deployed): verified mint/pool -> отдельный source/book
                                 -> pinned official champion -> forecasts/paper
```

Mac 1 является owner-host production/source/operator. Mac 2 содержит worker checkout,
инструменты и проверенные копии research artifacts; отсутствие live `work/` частей и production
LaunchAgents в worker checkout нормально. Shared mutable SQLite между Mac не применяется.
Исследовательские ablations выполнялись в существующем Torch environment Mac 1 и независимо
проверялись/копировались на Mac 2. Это ещё не общий distributed training scheduler.

### Свежая read-only проверка

2026-10-07 **17:16:36 UTC / 20:16:36 MSK**, owner API `operator-status-v1`:

- `mode=paused`, `phase=paused`, `revision=33`, `domain_stop=false`.
- Все 10 jobs, возвращённых status API: `completed`; queued/running среди них нет.
- Все три managed exams: `expired`. Последний expiry: 2026-10-07 12:23:51 UTC.
- Champion SHA: `9ea4a6bd7977fb66fe4a18eba54ed7eaed0afd65810ec99c75aec3e1443e3920`.
- SHA `operator04/deployment.json`:
  `db3f12d9e71fb58cfd966953877d6d2c9fcb7036e3e3d60b18bdd09992578fde`.
  Он совпал с ранее проверенным binding **runtime08**, runtime pin
  `fec78c60d5c93b4b1d9cf4a680843162169f214910b98cc135c194cee8e63c3a`.

Whitelist receipt сохранён в [evidence этого handoff](evidence/brain-handoff-status-2026-10-07.json).
Новая проверка подтверждает API и deployment hash, не является повторной полной аттестацией
running executable. Предыдущая конечная acquisition qualification сверяла runtime отдельно.
API сохраняет `active_exam_id` даже у expired exam; одно это поле не означает активный exam.
`expires_ns=0` на верхнем уровне API нельзя использовать вместо реальных source/exam receipts.

Последний source STOP после истечения lease признан ожидаемым deadline и сохранён. Его не
снимали. Это не работающий сейчас непрерывный collector и не повод написать «AUTO идёт».
Repo HEAD, selected candidate, official champion и installed runtime являются разными сущностями.
Источники: [operator](brain-autonomous-operator.md), [release/workflow](two-mac-workflow.md),
[последняя runtime qualification](brain-research-envelope-2026-10-07.md).

## 6. Какая модель является Brain сегодня

**[E] Официальный champion: empirical quantile model из прежнего admitted bundle**, SHA выше.
Последний завершённый AUTO training job `job_5edd5e5ecda88338` выбрал ensemble GRU seeds 7/19,
но **не promoted**. Его candidate SHA:
`69ebb2703f8765527911c32511255a27fa8c7d8a3315c2818bea3a329dc583c6`.
Promotion автоматически запрещён. Наличие выбранного GRU в registry не заменяет champion.

### Фактическая задача learned candidate

- 5-minute closed bars, horizons 1/3 bars, то есть 5m/15m.
- 12 features: returns, candle body/range/wicks, log USD volume, relative volume,
  short-history returns/RMS/positive fraction, zero-volume indicator.
- 8 feature frames; с историей самих features полная input dependency достигает 15 bars.
- Input содержит values, masks и age; фактический GRU width 16/context 8 очень мал.
  Для такой GRU в ML foundation измерены 2,174 parameters.
- Ordered q10/q50/q90 через median и softplus gaps; Brain price training не обучает
  execution-status logits как полезный торговый сигнал.
- Pinball loss, scaled train-label IQR; AdamW lr 0.001, weight_decay 0.0001; batch 64,
  global gradient clip 1; CPU, один thread, deterministic seeds 7/19.
- B13: 1,000 updates, linear/GRU x два seeds; empirical baseline refit на том же train.
  Validation-selected checkpoints и simplicity rule в пределах 1% лучшего pool-macro loss.
- Train cap 48,000; evaluation cap 6,000; rolling validation/test по три часа каждый.
  Сначала materialization до 500,000 decisions, потом retained subset.

**Критическая семантика selection:** pipeline выбирает среди моделей, обученных на новой
постановке, включая новый empirical. Старый official champion не является равноправным
участником этого внутреннего выбора. Новая GRU может победить ухудшившийся новый empirical
и всё равно проиграть старому champion. Promotion gates не позволили спутать эти события.

**[I] Это small bar-forecast learner, не ещё универсальный market Brain.** В нём нет доказанной
модели market microstructure, глубокой on-chain representation, обученной последовательной
торговой политики, qualified MPC или RL. LLM не является trading runtime. Эти возможности
не следует приписывать системе по её имени; возможность их добавить тоже не опровергнута.

Прямой код: `src/memecoin_brain/data.py`, `src/memecoin_ml/models.py`,
`src/memecoin_curriculum/{design,training}.py`, `src/memecoin_operator/learning.py`.

## 7. Сколько данных реально есть

Числа ниже относятся к разным слоям и датированным snapshots. Это не аддитивные объёмы.
Полный filesystem inventory и измерение общего размера raw archive в этой сессии не делались.

### 7.1. Raw/indexed on-chain discovery

Historical vacuum: 147 RPC, 14,340 nominal CU по локальному счётчику, не billing delta.
Monthly migration lane получил 5,400 transactions, около 90.79 MB, 865 birth pools;
pool-history lane: 4,919 transactions, около 88.70 MB, 1,236 swap candidates на восьми pools;
fee lane: 1,800 transactions, около 38.99 MB, 243 candidates на 97 pools.
После dedup: 1,579 decoded events, 167 swap pools, **965 registry markets**, 1,583 provenance
observations. Capture volumes разных lanes не означают столько уникального training content.

18 sampled date ranges покрывали 17 календарных месяцев; все упирались в caps.
Это не полная история этих месяцев. Coverage debt явно остался.
На этой стадии raw lane добавил **0 qualified USD training decisions**. Позже birth catalog
стал полезен для recruitment и bar hydration, но registry count не превратился автоматически
в тысячу обучающих рынков. [Historical vacuum](brain-vacuum-results.md).

### 7.2. Bar corpus и training expansion

Historical expansion control -> broad:

- 24 -> **86 train pools**: 24 прежних + 62 новых admitted.
- Eligible decisions 90,336 -> **98,323** до retained cap.
- Retained train 48,000 -> **48,000**, не рост retained dataset.
- 93 -> 104 UTC days; 370 -> 465 pool-days.
- Из 100 hydrated pools 38 дали zero eligible support. Получены 17,825 bars и
  **7,987 новых decisions**; все они вошли в retained set, вытеснив часть old rows.
- 20 из новых pools имеют <16 rows, 26 имеют >=64, 13 имеют >=256.
- Новые строки описывают первые <3 days markets; 5,427 younger than 1d, 2,560 age 1–3d.
- 97.17% новых origins разделены соседними 5-minute bars. 7,987 строк не равны 7,987
  независимым market episodes; для этой когорты counted 568 disjoint dependency windows.
- Effective pools по row shares выросли 6.19 -> 9.55, что гораздо меньше 86 pool IDs.

Latest AUTO snapshot, **другой** artifact: raw audit 100,656 bars / 5,624 captures;
train 48,000 / 86 pools, validation 264 / 8 pools, test 249 / 8 pools.
Expansion/ablation snapshot имеет test **246**, не 249. Подмена этих denominators неверна.

Источники: [expansion](brain-training-expansion-results.md),
[latest audit](brain-post-training-audit-2026-10-07.md).

### 7.3. Новая попытка наполнить широкий development benchmark

Последний frozen roster: 20 mints x два historical age windows, 40 GET, все HTTP 200.
Результат: **2,020 unique bars, 453 eligible origins, шесть supported mints,
29 global dependency-disjoint windows**. 34 из 40 windows не дали support.

Age <1d: 405 origins / шесть mints / 27 within-stratum windows / шесть UTC days.
Age 1–3d: 48 origins / один mint / три within-stratum windows / один day.
Age 7–10d: **0 support**. Один mint даёт 322/453 = **71.08%** origins.
27 + 3 нельзя объявить 30 global independent windows: dependency overlap на границе strata
учтён глобальным счётчиком, давшим 29. Disjoint вообще не означает statistical independence.

Это реальный завершённый data-only результат, но **не широкий benchmark** и не prospective
collection. Development/calibration/prospective packets для temporal lifecycle отсутствуют.
[Research envelope](brain-research-envelope-2026-10-07.md).

### 7.4. Чего эти данные не подтверждают

Нет complete Solana market coverage, полной lifecycle истории всех discovered markets,
репрезентативного потока пользовательских Watch sessions или статистически сильного old+new
future benchmark. Sparse/missing intervals не размечены как zero return, market death или
исполненная сделка. Живучие migrated/provider-covered markets создают selection bias.
Возраст pool в некоторых исследованиях является proxy, а не доказанным временем genesis mint.

## 8. Что выяснили про качество GRU

### 8.1. Completion и сравнение с champion

**[E]** Latest job завершил все четыре fits, immutable completion/receipts проверены;
partial/corrupt outputs и нарушения заявленных split/maturity/availability checks не найдены.
Это ограниченная проверка реализованных контрактов, не доказательство отсутствия любого
возможного leakage или dataset bias во всём проекте.

Matched latest candidate vs official empirical champion, одинаковые validation/test origins:

- Validation combined pool-macro pinball **0.010146 vs 0.007641**, candidate хуже ~32.8%.
- Test combined **0.008904 vs 0.006272**, хуже **41.95%** по неокруглённым значениям.
- Test 5m **0.008051 vs 0.004867**, хуже 65.43%; 15m **0.009756 vs 0.007678**, хуже 27.07%.
- Test GRU q10/q50/q90 coverage **1.606 / 77.912 / 98.996%** вместо nominal 10/50/90.
  Champion descriptive coverage приблизительно 15.9/53.4/93.4%, тоже не идеальная calibration.

**[E] На этом конкретном test GRU хуже. [U] Надёжность величины 41.95% на future population
не установлена:** 249 overlapping decisions, восемь старых pools, один UTC day, нет adequate CI.
Pinball является forecast loss без trading costs. Costed paper использует общую модель costs,
но в опубликованном раннем paired future срезе было восемь origins и ноль fills: сравнить
реальную экономическую полезность моделей по нему нельзя. Нового итогового PnL audit expired
exams этот handoff не проводил. [Post-training audit](brain-post-training-audit-2026-10-07.md).

### 8.2. «Поколения деградируют на 5%, 9%, 42%» не доказано

**[E]** Это разные datasets/baselines. B12 5.34% было **улучшением** относительно recent-selected empirical
на 6,027 decisions / 24 pools / два days. Expansion +8.92% было broad vs control GRU на
246 decisions. Latest +41.95% было другой candidate vs старый empirical на 249 decisions.
Они не образуют temporal degradation curve. Доказано несколько неудачных/неустойчивых
сравнений, а не универсальное ухудшение каждого нового поколения.

При подготовке handoff найдена неточность уже в нашем вторичном audit: там baseline для
5.34% ошибочно назван recent GRU. Первичный B12 report показывает selected full GRU
0.0273579 против recent-selected empirical 0.0289014; против recent GRU 0.0290466 эффект
составлял 5.81%. Старый evidence JSON не переписан; correction добавлена явно. Это хороший
пример, почему reviewer должен проверять и наши audits, а не только training outputs.
[Первичный B12](brain-curriculum-comparison.md), [вторичный audit с erratum](ml-review-audit-2026-10-07.md).

### 8.3. Sampling: причина части проблемы подтверждена

Исходный sampler даёт одинаковую mass каждому pool, затем одинаковую mass каждому pool-day,
затем делит её по rows внутри дня. Probability строки:
`1 / (number_of_pools * days_in_this_pool * rows_in_this_pool_day)`.

Новые 62/86 pools получили **72.09% sampling mass**, имея **16.64% retained rows**.
20 tiny new pools получили 23.26% всей mass. Draw-probability ESS упал **1872.95 -> 1171.60**.
Это концентрация sampling, не оценка количества независимых данных. Новая cohort имела другие
return/volatility distributions; 7,203/7,987 rows попали в два верхних volatility buckets.
Eval остался на старых markets. Training objective поменялся гораздо сильнее, чем выглядело
по доле новых строк.

Контролируемый опыт на одном sealed 86-pool snapshot: только new mass 72.09% -> 16.64%,
same rows/scaler/architecture/optimizer/budget/seeds, baseline tensors reproduced exactly.
1,000 updates, seeds 7/19:

- Validation pinball: seed7 **-1.15%**, seed19 **-9.83%**, ensemble **-6.42%**.
- Test pinball: seed7 **-5.22%**, seed19 **-16.13%**, ensemble **-11.91%**.
- Test ensemble loss **0.010601888 -> 0.009339007**.
- Test coverage pooled **2.44/65.45/94.72 -> 6.10/62.40/92.48%**.
- Но 15m median coverage **52.85 -> 71.95%**, то есть calibration там ухудшилась.

**Вердикт:** causal effect sampling в этом setup подтверждён; «sampling исправляет всю
calibration» неверно. 16.64% не оптимальная product mixture, а одна интервенция. Улучшение
на old-only eval не доказывает улучшения на новых молодых markets. Источник:
[sampling ablation и pins](brain-sampling-ablation-2026-10-07.md).

### 8.4. Training budget: недостаточность наблюдается, объяснение нужно уточнить

Бюджет 1,000 updates x 64 = 64,000 draws при 48,000 retained rows. При расширении **retained
rows не выросли**, поэтому буквальное «датасет стал больше, epochs уменьшились» для того
опыта неверно. Изменились objective и exposure: old cohort получила лишь около 17.9k draws
вместо прежних 64k, при многократном повторении sparse new pools.

Exact continuation 1,000 -> 4,000 updates, тот же draw prefix/optimizer, cadence 100:

- Current sampler: selected ensemble test **0.010601888 -> 0.009653287**, -8.95%.
- Capped sampler: **0.009339007 -> 0.008400464**, -10.05%.
- Оба изменения против current/1000: **-20.76%** test, -18.65% validation.
- Fixed terminal checkpoints тоже улучшили test loss: current -3.53%, capped -9.52%.
  Значит эффект не сводится только к большему числу checkpoint candidates.
- Но current terminal median coverage стала **83.33%**. Capped selected pooled median
  48.78% скрывает 5m/15m **41.06/56.50%** и seed medians **39.43/58.94%**.

**Вердикт:** finite budget ограничивал observed loss; увеличение compute не гарантирует
calibration и не исправляет другой objective. Сходимость к 4,000 и оптимальность этого числа
не доказаны. [Budget ablation](brain-budget-ablation-2026-10-07.md).

### 8.5. Horizon head: реальное ограничение, не найденная единственная причина

В текущем коде horizon входит только в общую linear head после encoder. Поэтому разность
median15m - median5m постоянна для любого history. Replay подтвердил constant gap с float32
отклонением около 5.96e-8. Для tails действует affine shift gap logits плюс softplus;
нельзя из этого утверждать постоянство всех quantile differences.

Изолированный head intervention добавил 48 zero-initialized horizon x latent parameters,
сохранил initial predictions, GRU, samplers, optimizer, seeds и 4,000 updates. Восемь fits,
controls exact, 144 metric groups independently rescored:

- Current sampler, terminal ensemble: test loss **-3.93%**, median всё ещё **82.72%**.
- Capped sampler: test loss **+1.08%**, median5m/15m **38.62/67.89%**.
- Эффекты seeds/checkpoint selection неоднородны; гибкость действительно добавлена.

**Вердикт:** head structural limitation confirmed; «именно это главный остаточный bottleneck
и его простое исправление решит calibration» не установлено. Отрицательный короткий опыт
не доказывает бесполезности гибких heads при другом optimization/data scale.
[Horizon ablation](brain-horizon-ablation-2026-10-07.md).

### 8.6. LR и clipping

Train-only диагностический fork: четыре terminal4000 checkpoints, каждый продолжен с
LR 0.001 или 0.0001 на identical next 1,000 draws-updates; восемь continuations.
Validation/test predictions **0**, model selection **0**.

- Current sampler mean-seed median-coverage temporal SD **8.117 -> 3.677 pp**, -54.70%.
  Train calibration MAE **2.736 -> 2.169 pp**, loss **+2.29%**.
- Capped: SD **14.068 -> 5.880 pp**, -58.20%; MAE **8.145 -> 2.872 pp**, loss **-1.55%**.
- Все четыре pairs улучшили terminal pooled train calibration, но cohort biases остались.

Это conditional-on-checkpoint причинная поддержка optimization instability, а не доказанная
победа low-LR модели на будущем рынке. LR также меняет effective AdamW decay; этот эффект
не отделён. Temporal SD одного fit не является confidence interval качества модели.

Clipping срабатывал на **100% updates всех восьми forks**. Средняя pre-clip norm:
55.3–95.9 при прежнем LR, 33.9–37.4 при low LR, threshold 1. Это не rare safety clip,
а постоянно действующая часть optimizer. **Вред clipping причинно не доказан.** При saturated
clip общий положительный loss scale алгебраически сокращается, но batch-dependent norm меняет
градиенты и Adam moments. Нельзя просто разделить LR на норму и объявить effective Adam step.

Следующий ранее предложенный ML diagnostic: LR schedule x clipping regime, 2x2, same
initial states/objective/draws/seeds, после широкого development benchmark. Он **не проведён**;
сейчас не рекомендую очередной sweep по старому test. [LR diagnostic](brain-lr-diagnostic-2026-10-07.md),
[clipping audit](evidence/brain-clipping-audit-2026-10-07.json).

### 8.7. Normalization и итоговый диагноз calibration

Исторический |z| около **1425.58** относился к B03 lower-wick/revision case, не к latest
test. Latest максимумы: train **135.764**, validation **6.115**, test **4.120**; scaler
пересчитан и совпал. Старое winsorization снизило extremes, но ухудшило validation GRU;
одной заменой scaler проблема не объяснена. Это не означает, что robustness features решена.

**Доказанные составляющие:** objective/cohort mismatch, finite-budget effects,
seed/checkpoint instability, узкая validation population, постоянная horizon-median связь.
**Поддержанная отдельным опытом причина части нестабильности:** optimization LR regime.
**Неидентифицированные вклады:** clipping, Adam moments, minibatch noise, capacity,
representation, conditional nonstationarity и отсутствие отдельной calibration процедуры.

Pinball minimization не гарантирует conditional calibration каждого market/horizon;
усреднение quantiles нескольких seeds также её не гарантирует. Красивый pooled score может
компенсировать разнонаправленные ошибки. Мы знаем несколько механизмов, но не имеем полного
causal decomposition latest +41.95%. Это честнее, чем назвать GRU сломанной или уже исправленной.

## 9. Evaluation: сильные контракты, слабая наполненность

**Что есть:** immutable folds/receipts, group maturity purge, train-only transforms, frozen
candidate manifests, future prediction timing, exact paired joins, refusal превращать старый
test в новый holdout, separate paper accounting. Latest integrity audit не нашёл нарушений
заявленных contracts. Это нужно сохранить и расширить, не переписать ради удобства.

**Чего нет:** широкого общего development benchmark, независимого calibration block,
репрезентативного prospective old+new benchmark, сильной uncertainty estimate для текущих
model comparisons. Старый test раскрывался многократно и теперь regression/descriptive only.
Даже если selector формально не читал test, последующее человеческое изменение recipe по его
отчётам тоже является exposure. Hash seals защищают immutable bytes, не от research overfitting.

Benchmark-v1 разделяет train, development, calibration, prospective; AUTO eligible traffic и
Watch breadth/traffic; seen/unseen mint и age-at-origin. Первоначальный дизайн окон 7/3/7 days
и support floors является предложением, не power calculation и не уже собранными данными.
Metadata checker реализован, но прямо возвращает admission_ready=false до raw provenance review.
Он не создаёт данные и не квалифицирует promotion. [Полный контракт](brain-benchmark-v1.md).

**[I] Целевое распределение нельзя получить из числа pools или raw row share.** Нужны два
заранее определённых deployment estimands:

1. AUTO: origins, реально eligible по frozen source/policy; primary traffic weighting,
   secondary mint/pool-day macro и age/volatility/venue strata.
2. Watch: отдельный user-selected market поток. До появления session logs recruitment
   называется Watch-proxy, не representative user traffic. Breadth оценивать per-mint,
   затем отдельно измерять реальные session/decision frequencies.

Training reference objective, sampling proposal и loss weights должны стать явными разными
объектами. Oversampling для coverage не обязан менять objective; correction требует общего
support и сама может быть нестабильной. Новые tiny pools не должны молча менять всю training
population. Точные proportions предстоит выбрать по задаче/данным, не по старому winner.

## 10. Paper и Watch Market: что пользователь действительно получает

### Paper engine

У каждого model book $1,000 virtual cash, entry notional $100, максимум три pending/positions,
одна позиция на pool. Policy: long-only, 15m median > log(1.016), q10 >= -0.25. Fill не по
уже известной цене: первая новая fresh closed bar после actual decision; TTL entry 10m.
Exit после 15m от entry bar на первой допустимой fresh observation. Costs: slippage 50 bps
и fee 25 bps на сторону. Это **uncalibrated price proxy**, не реальная модель исполнения.

Один ранний live срез действительно имел две сделки GRU и net **-$4.780619** при fees
$0.990525. Другие срезы имели много WAIT/ENTER intents и ноль fills. Ни нулевой PnL без сделок,
ни этот небольшой отрицательный результат не квалифицируют стратегию.
Learned exit, MPC, RL, реальные transaction signing/submission отсутствуют в этом Brain path.

**[E/I] Важный product mismatch:** forecast target сейчас origin-close -> future-close,
а entry происходит позже, exit считается от entry. Они не совпадают по интервалу. Код это
делает причинно, не leakage, но прогностический pinball не является автоматически loss для
доходности исполненной policy. Следует отдельно проверить policy-aligned targets, latency,
cost sensitivity и abstention, а не обещать торговое качество по bar forecast.
Источники: [paper loop](brain-paper-loop.md), `src/memecoin_paper/engine.py`.

### Watch Market

Feature опубликована commit `0e9a2c1`: mint/pool из Axiom используется как identifier,
resolve идёт через verified provider path; Axiom не execution API. На Start должен pin
**текущий official admitted champion**, а не latest selected candidate. Отдельный book/source
не вызывает AUTO training и не меняет его позиции. UI показывает market snapshot, closed-bar
forecast, q10/q50/q90, WAIT/ENTER/HOLD/EXIT, причины и virtual portfolio.

Это >=60s polling и 5-minute bar forecasts, не exchange tick stream. Quantile interval
не следует называть qualified uncertainty guarantee. Unsupported mint/gaps/stale data
должны давать явное отсутствие допустимого forecast, не выдуманную уверенность.

**Статус:** synthetic functional tests есть; owner-host live/provider/browser acceptance
не проведён; пользовательский traffic ещё не измерен. Активный runtime08 Watch не содержит.
Staged runtime09 был эквивалентен 08. Следующий release должен иметь новый manifest с Watch,
плановое имя runtime/release10; этот handoff его не создавал и не активировал.
Нельзя утверждать, что end-to-end пользовательский сценарий уже доказан на Mac 1.
[Watch contract/status](watch-market.md).

## 11. Почему acquisition всё ещё даёт мало ML support

### Реальные технические причины

1. **RPC transaction != training example.** Нужны versioned semantic decoding, mint/pool
   identity, token decimals/transfer semantics, quote conversion, gaps, actual availability,
   contiguous context, mature targets. Высокий throughput увеличивает доставку bytes,
   но не заменяет эти слои.
2. **Raw и bar lanes ещё не сошлись.** Alchemy даёт полезные birth/swaps/registry данные,
   но основной qualified USD training/live path зависит от GeckoTerminal OHLCV. WSOL/USDC
   quote units нельзя смешивать или выдавать за USD без правильного availability-aware join.
3. **Venue/ABI coverage ограничена.** CPMM не CLMM/DLMM; для новых PumpSwap payloads найдены
   дополнительные 8 bytes/Token2022 account variants. Diagnostic reconciliation ещё не
   означает admitted protocol semantics. Не все swaps становятся price bars безопасно.
4. **Sparse lifecycle.** Нужны 15 input bars плюс future targets, поэтому успешный HTTP200
   может дать ноль usable origins. Provider-supported young survivors не весь Solana рынок.
5. **Coverage debt.** Bounded historical pages часто заканчивались cap, не концом истории;
   pagination completeness и отложенные интервалы пока не обслуживаются полноценным планировщиком.
6. **Operational interruptions.** Сеть/sleep, source deadlines, false-positive-prone process
   guards, частичные campaigns и несколько legacy supervisors мешали сбору и интерпретации health.

### Ошибки реализации, которые нельзя выдавать за внешние ограничения

- Ранний indexed Alchemy запрос не передал `maxSupportedTransactionVersion=0` и получил
  -32015. Это была наша ошибка запроса, не доказательство недоступности indexed history.
  Позже historical vacuum на corrected path работал.
- Raw audit упирался в 600s deadline из-за missing query index: 512 repeated scans по
  264,529 versions. Index `(pin, open_s)` сократил audit batch до 2.235s, полный audit до 8.051s.
  Это исправление access pattern, не задача для большего GPU.
- `.DS_Store` вызвал paper failure; source оставался healthy. Recovery пришлось исправить,
  а метрики source-health и product-health разделить.
- Старый consumer guard по substring мог отвергать наблюдавшиеся expired legacy supervisors.
  Истинная identity прежнего `unexpected_project_consumer` не сохранена, поэтому задним числом
  объявлять именно его false positive нельзя. Новая exact-role/hash inventory дала нормальные
  diagnostics и успешно прошла finite suffix run, но не доказала unattended устойчивость.

Источники: [source architecture](brain-source-architecture.md),
[PumpSwap events](brain-pumpswap-events.md), [vacuum](brain-vacuum-results.md),
[operator/recovery](brain-autonomous-operator.md), [envelope](brain-research-envelope-2026-10-07.md).

### Исторические ограничения, которые пора перестать считать законами природы

Hot subset шесть pools, source universe 24, train cap 48k, короткие validation/test, CPU1thread,
1,000 updates, fixed expiry и вручную разрешаемые маленькие campaigns были осмысленными
страховочными bounds пилота. Они не выведены из предельных возможностей Mac, paid Alchemy,
storage или статистической оптимальности. Их изменение требует новой версии контрактов,
но нет оснований навсегда проектировать систему вокруг этих чисел.

**[I] Главная организационно-архитектурная ошибка:** acquisition развивался как последовательность
исключений и отдельных доказательных campaigns, а не как постоянный data product с coverage
SLO, durable backlog, общим бюджетом, ответственным владельцем и независимым research namespace.
Много труда ушло на аккуратную остановку/продолжение, мало на sustained полезный throughput.

Последний 31-GET suffix занял 927.4s при minimum spacing 30.316s, все ответы HTTP200,
unknown consumers 0. Это успешная квалификация конкретного finite envelope, не потолок
доступного Alchemy throughput. Большая часть этого campaign была GT, а не Alchemy ingestion.

## 12. Что сильно и стоит сохранить

- Immutable raw captures и content-addressed manifests, возможность independently recompute.
- Разделение event time, received/available time, fit cutoff, prediction publication и maturity.
- First-seen/revised data accounting: исправленная позже candle не подменяет исторический input.
- Явный denominator failures/gaps/unsupported, запрет превращать отсутствие данных в zero return.
- Deterministic replay/seeded draws/checkpoint-RNG/optimizer continuity; exact controls в ablations.
- Champion != candidate != selected family; immutable promotion/release boundary.
- Отдельные forecast metrics, paper accounting и execution qualification.
- Read-only worker workflow, sealed transfer вместо двух writers в одной mutable SQLite.
- Проверяемые command/job semantics, no silent STOP deletion, append-only evidence history.
- Tests на errors/properties: последний полный repo check 1,063 passed / 12 optional skips,
  smoke PASS. Это результат предыдущей code-сессии на Mac 2, не новый full run этого handoff.
  Optional Torch/browser checks там пропущены; count tests не доказательство ML качества.

**[I] Сохранить именно invariants и совместимость artifacts, а не каждую нынешнюю реализацию.**
Можно заменить JSON materialization на columnar store, не теряя receipts. Можно увеличить
continuous acquisition, не отменяя quotas. Можно изолировать bad partition, не скрывая её
failure и не выключая весь research навсегда.

## 13. Что я считаю ошибочным, слишком узким или требующим замены

1. **Малый пилот фактически стал default product architecture.** Caps и short leases помогали
   безопасно стартовать, но затем каждое расширение оформлялось как исключение из слишком
   узкого режима. Нужно сменить базовую единицу с «следующая кампания» на «непрерывный data product».
2. **Объём discovery путали с learning progress.** 965 markets, 86 train pools и 48k retained
   decisions говорят о разных вещах. Главные KPI должны быть supported market-days/episodes,
   completeness, independent evaluation support и actual training exposure.
3. **Sampling objective не был достаточно явным.** Equal-pool/day оказался политикой смены
   target population, а воспринимался как безобидное разнообразие. Sparse pools получили
   большую mass без доказанного соответствия реальной задаче.
4. **Benchmark появился слишком поздно.** Много локально корректных comparisons накопились
   вокруг узкого раскрытого test. Нужно было строить широкие development/future streams
   одновременно с первым continuous corpus, а не после отрицательных GRU результатов.
5. **Model training расширялся медленнее data semantics.** B13 cap48k/1000updates не превращает
   новый корпус в большую модель с большим exposure. CPU microfit доказал, что код работает,
   но не задаёт roadmap серьёзных поколений.
6. **Слишком много неполностью объединённых paths.** Raw events, GT bars, curriculum shards,
   streaming loader и AUTO существуют, но лучший loader не используется current trainer,
   а лучший raw source не поставляет основной qualified feature stream.
7. **Operator накопил знания о частных experiment layouts.** Process scanning, legacy role
   exceptions и filesystem manifests недостаточны как coordination protocol большой системы.
   Нужны declarative jobs/resources/leases и единая диагностика ownership.
8. **Forecast-to-policy задача слишком непрямая.** Returns на bar horizon и задержанная paper
   позиция имеют разные economics. Это допустимый baseline, но не финальный Brain objective.
9. **Документация стала большим историческим архивом.** Старые sections корректны как история,
   но без даты могут противоречить текущему статусу. Например, «Mac2 без checkout» уже устарело.
   Нужен короткий generated current inventory и evidence catalog, а не чтение всего архива.

Это моя ответственность как автора решений, а не вина пользователя за недостаточно узкий
запрос. Я слишком долго оптимизировал минимальный безопасный следующий шаг и недостаточно
рано сделал sustained data throughput и broad evaluation главными deliverables.

## 14. Способность вырасти на 10x / 100x / 1000x

**[E] Load tests этих масштабов не проводились.** Ниже инженерный capacity review, не измеренная
пропускная способность и не обещание конкретных costs. Для порядка величин берём около 100k
eligible decisions текущего expansion: 10x = 1m, 100x = 10m, 1000x = 100m. Raw events,
markets и feature tensors могут расти иначе; эти множители нельзя отождествлять.

### Уже сейчас, до 10x

Train cap48k уже ограничивает использование истории. `learning.prepare()` сначала собирает
dataset, потом ограничивает train; hard bound500k decisions встретится примерно на 5x этой
условной базы, ещё до 10x. Простое поднятие cap увеличит Python object/JSON/tensor memory,
не создавая streaming trainer. Raw byte объём сам по себе не измеряет RAM этого path.

### 10x

**[I] Достижимо без немедленного distributed training**, но не как blind cap increase.
Нужно подключить partitioned immutable datasets и lazy/mmap loader к реальному trainer,
инкрементально поддерживать feature/label state, убрать повторные whole-corpus scans,
иметь shared provider budgeting и независимый research acquisition service.
Один writer SQLite может оставаться catalog/control store, пока замеры подтверждают запас.
Mac CPU/MPS следует измерять на нужном batch/model, а не переносить результаты tiny fit.

### 100x

**[I]** Вероятны пределы single-host disk/IO, JSON-per-record overhead, catalog query patterns,
full snapshot copying и sequential hydration. Нужны разделение metadata/data plane,
partitioned columnar/event storage, параллельные acquisition/decoder workers с idempotency,
backpressure, checkpointed replay и dataset manifests без полной загрузки в RAM.
GPU training становится осмысленным при реально большем learner/throughput; его необходимость
и число accelerators должны следовать из profiles/learning curves, не из лозунга «big Brain».

### 1000x

**[I]** Текущий orchestration не подходит. Нужны object storage или эквивалентный shared
immutable data layer, distributed task ownership, recovery semantics, partition lifecycle,
cost/coverage accounting, scalable feature generation и GPU cluster job layer.
Distributed training требует отдельной reproducibility/numerical qualification; битовое
равенство CPU и multi-GPU не предполагать. Control Center должен читать projections,
не сканировать raw archive. Metadata retention/compaction должно сохранять provenance roots.

### Где сломается первым

**Первый функциональный предел:** недостаточная semantic coverage и benchmark population.
Можно быстро собрать много bytes и по-прежнему не получить нужный ML support.
**Первый очевидный current-code предел:** fixed caps и полная materialization в B13.
**Следующие вероятные пределы:** file/JSON overhead, indexed-store access patterns,
single-owner scheduling, provider contention и отсутствие end-to-end resource backpressure.
**Не установлено:** что первой обязательно сломается SQLite, сеть, MPS или именно GRU.
Это должен решить profiler/нагрузочный тест, а не популярность конкретной инфраструктуры.

В repo есть lazy LRU shards/mmap cache (`memecoin_curriculum/streaming.py` и related tooling).
Пилот показал быстрые warm cache reads, но не whole-pipeline throughput и не интеграцию в B13.
Это полезный задел, не готовое оправдание заявления «уже масштабируемо».

## 15. Предлагаемая целевая архитектура для независимого обсуждения

Всё в этом разделе **[I]**, не реализованный deployment и не принятый migration plan.

### Data plane

1. **Continuous raw ingestion.** Раздельные live и backfill priorities; global provider
   coordinator с actual attempts, rate/CU budgets, leases, deadlines и retry classification.
   Paid capacity учитывать по фактическому тарифу и измерению, не по старому free cap.
2. **Durable coverage ledger.** Для venue/pool/mint/time фиксировать wanted/fetched/decoded/
   qualified/feature-ready/label-ready, gaps и причины. Backfill frontier не теряется после
   process stop. Aggregate throughput выражается и в bytes, и в useful supported episodes.
3. **Immutable raw + versioned canonical events.** Partitioning по chain/venue/time/schema;
   idempotent ingestion, reorg/finality policy, decoder version and quarantine per partition.
   Исторические bytes не переписывать; corrected views являются новыми versions.
4. **Feature/label store.** Общая research/runtime semantics, explicit availability;
   multi-resolution bars/events, availability-aware USD conversion, dependency manifests.
   Отдельно raw collection admission и training/execution admission: непонятные события можно
   сохранять для будущего decoder, не пуская их в модель и не останавливая весь архив.
5. **Immutable training snapshots.** Ссылки на partitions и transforms вместо огромного
   вложенного JSON. Streaming batches, cached tensors, train-only transforms и recorded draw
   schedule/objective. Уже существующие artifact contracts должны пережить storage migration.

### Learning и evaluation plane

Training recipe version задаёт target population, exposure/compute budget, architecture,
optimizer, sampler, checkpoint selection, seeds и validation role. Хранить actual unique
draws, market/day/cohort exposure, gradient/update diagnostics и compute consumption.
Количество updates не обязано линейно следовать raw rows; нужны learning curves и convergence
criteria на отдельном development, без превращения stop rule в просмотр final test.

Benchmark нужен как постоянный независимый data product: frozen anchor для regression,
широкий открываемый development, отдельная calibration, периодически обновляемые sealed
prospective cohorts после freeze. Exposure ledger учитывает отчёты и ручные решения.
Сохранять traffic/mint/time weighting и failures denominator; одна scalar leaderboard
не должна прятать young/unseen failure. Cheap empirical/linear и official champion обязательны.

Новые generations сравнивать common-origin paired panels, calibration/sharpness, missing rates,
costed policy outputs и compute/data efficiency. Размер модели и FLOPs не заменяют outcome,
но должны быть явной осью исследования, а не навечно запрещённой сложностью.

### Control plane

Source, feature builder, training, evaluation и paper имеют отдельные workers/namespaces,
общие resource leases и immutable input/output references. Mac1 может оставаться operator/UI,
Mac2 стать research/data/training worker. Ресурсы RAM/IO/accelerator контролируются scheduler,
а не предположением, что все процессы безопасно помещаются одновременно.

Process inventory остаётся diagnostic, а не главным distributed lock. Unknown consumer
должен дать идентифицируемый incident с redacted hashes/role/ownership; автоматические retries
допустимы только для classified transient failures с bounds и debit, не integrity bypass.
Promotion и release отдельны от job completion. Research service не должен зависеть от
продления expired production exam; production не должен зависеть от очередного offline fit.

### LLM resources

Сильные внешние coding/research models могут review code, protocols и evidence. Пользователь
сообщал об авторизованном Cursor CLI с Grok; это не MCP-server и не повод извлекать tokens.
В этом handoff Grok не вызывался, независимый review ему не приписывается.

Local Qwen3.5 35B на Mac48GB является отдельным бесплатным preprocessing resource, не заменой
сильного reviewer. Измерение ~47 tok/s со значительным swap было сообщено пользователем;
здесь не перебенчмаркировалось. Допустимая роль: redacted log triage, clustering, summaries,
structured annotations с source refs и deterministic validators. Не ground truth, не promotion,
не financial decision authority. Market features от LLM требуют отдельной out-of-sample ablation.
Scheduler должен выгружать/ограничивать её при heavy training. Не использовать её любой ценой.

## 16. Что делать дальше: не очередной tiny fit

Предлагаю reviewer оценить четыре последовательных deliverables. Это направление, не новое
разрешение на действия и не обещанные сроки.

**A. Capacity/coverage specification и один end-to-end data service.** Выбрать первый реально
поддерживаемый venue/feature path, связать raw ingestion -> canonical -> bar/features -> snapshot.
Сформулировать target useful throughput и completeness; измерить paid provider, disk, CPU/RAM,
admission yield. Провести длительный qualification интервал с заранее заданными SLO, recovery
и failure accounting. Отдельные hard bounds нужны для cost containment, не для perpetual tiny size.

**B. Широкий development/prospective foundation параллельно накоплению train.** Не заменять
слабый mint после просмотра outcomes; расширять recruitment правилами до результатов, сохраняя
denominator. Собирать seen-temporal и mint-disjoint old/young cohorts, реальные Watch sessions
после самостоятельного release acceptance. Support/precision objectives должны быть существенно
выше восьми старых pools и одного дня; точные numbers вывести из desired precision/coverage,
а не назначить искусственно огромными ради отчёта.

**C. Интеграция scalable trainer, затем optimization experiment.** Подключить streaming datasets,
сохранить exact small-snapshot regression. На новом development сначала LR x clipping при
фиксированном backbone/objective, поскольку это наиболее информативный следующий короткий
ML опыт по имеющимся данным. Затем data/compute/model-size scaling curves. Не объявлять
16.64%/4000/0.0001 универсальным recipe и не подбирать очередные knobs по old test.

**D. Настоящая generation qualification.** Заранее frozen candidate/calibrator проходит
prospective old+new panel против champion с uncertainty, coverage и одинаковой policy/costs.
Улучшение должно быть meaningful для product, а не только statistical или pooled. Если данных
недостаточно, verdict insufficient; новый сбор отдельным protocol, не extension до победы.
Execution/real-money admission остаётся отдельной задачей и сейчас не разрешена.

В качестве capacity горизонтов разумно планировать миллионы, затем десятки/сотни миллионов
qualified decisions, а не навечно десятки тысяч. Это **design targets для проверки**, не факт
доступности независимых наблюдений и не автоматическое основание обучать всё подряд.
Какой compute нужен для полезного Brain на этих данных, сейчас неизвестно.

## 17. Что прошу независимого reviewer решить

1. Действительно ли главным разрывом является data/evaluation plane, или код скрывает более
   фундаментальные target/causal defects? Проверить, а не принять диагноз автора.
2. Какие existing contracts reusable, а какие перегружены историей F01/B-series и мешают масштабу?
3. Какой минимальный coherent data architecture на двух Mac естественно переносится в cluster?
   Где оставить SQLite/JSON, где заменить на partitioned columnar/object storage?
4. Какой source/decoder/quote-conversion путь первым даст широкий qualified Solana corpus
   при уже имеющемся Alchemy? Как измерить completeness, не только success/RPC count?
5. Правильно ли разделены AUTO/Watch populations, abstention и novelty? Какие benchmark
   roles/precision goals нужны для честных generations, не бесконечного self-tuning?
6. Есть ли основание менять backbone сейчас? Или сначала objective, optimization, targets,
   head/calibrator и exposure? Какие выводы causal experiments действительно переносимы?
7. Какие постоянные сервисы/legacy supervisors следует консолидировать или удалить после
   migration, сохранив воспроизводимость historical artifacts?
8. Как определить V1/V2/V3 в терминах data coverage, compute, quality и operational reliability?
9. Какой следующий один engineering milestone даст максимум sustained useful data, а какой
   следующий один научный experiment даст максимум информации после готовности benchmark?

Желаемый verdict: **оставить / заменить / удалить / пока неизвестно**, с evidence и приоритетами.
Не нужно защищать старую архитектуру или рекомендовать rewrite всего без конкретной причины.
Не нужно запускать review как повод читать protected holdout, менять champion или production.

## 18. Навигация, artifacts и воспроизводимость

### Начало независимой сессии

Прочитать `AGENTS.md`, затем `memory/now.md`, этот документ и выполнить read-only:

```sh
git status --short
git rev-parse HEAD
python3 tools/agent_context.py --task architecture
```

`memory/state.md`, `decisions.md`, `changes.md` сохраняют историю: искать релевантные разделы,
не трактовать все старые present-tense утверждения как текущий runtime. Repository source
не содержит весь private raw archive. Для воспроизведения нужны sealed `work/` bundles;
их отсутствие не следует исправлять новым acquisition или заменой synthetic fixtures.

### Основные документы

- [Project purpose](../memory/project.md), [architecture history](../memory/architecture.md),
  [evaluation history](../memory/evaluation.md), [data/execution](../memory/data-and-execution.md).
- [ML foundation](ml-foundation.md), [F01 research readiness](research-readiness.md).
- [Brain development](brain-development.md), [source architecture](brain-source-architecture.md),
  [PumpSwap decoder](brain-pumpswap-events.md), [historical vacuum](brain-vacuum-results.md).
- [Experience/forensic review](brain-experience-review.md), [paper](brain-paper-loop.md),
  [operator](brain-autonomous-operator.md), [two-Mac workflow](two-mac-workflow.md),
  [Watch Market](watch-market.md).
- [Expansion](brain-training-expansion-results.md), [post-training audit](brain-post-training-audit-2026-10-07.md),
  [independent claims audit](ml-review-audit-2026-10-07.md), [sampling](brain-sampling-ablation-2026-10-07.md),
  [budget](brain-budget-ablation-2026-10-07.md), [head](brain-horizon-ablation-2026-10-07.md),
  [LR](brain-lr-diagnostic-2026-10-07.md), [benchmark](brain-benchmark-v1.md).
- [Development acquisition](brain-development-acquisition-2026-10-07.md),
  [research envelope](brain-research-envelope-2026-10-07.md).

Каждый experiment report ссылается на JSON в `docs/evidence/`, protocol, code/data pins и
per-seed/per-horizon results. Они первичнее округлённых цифр этого handoff.

### Ключевые immutable identities

- Expansion/ablation dataset manifest:
  `ba9081b124dd066df8641a19724300c81785b885a15ce17a288a55cdc95be430`.
- Latest AUTO dataset manifest:
  `a1c2f6d101a28ea260c378e05e252bdc0859f8ed964d961bd776aa5cc8971099`.
- Sampling run manifest:
  `94c86c385c5d944ab34869874cb0922473af6c8fe9676744ad03ace518d15d11`.
- Budget run manifest:
  `58545e8ef584e2987708169c330100c00d6ebc1693910d7ffded79abd738349a`.
- LR run manifest:
  `3d81f4eb1239cf7dacfc4ade2650e6c5d752a0de4f97d19be76175258e07415a`.
- Latest research envelope seal:
  `9c8f79b0eb76f30981d3427a038b91b92b785ea8ed03ec4d0fe0465ec7d8a756`.
- Latest support seal:
  `7d4bf2b4cb2e39f0ca5e9b2f991136bc702b137951e9d400baecb9812d48168f`.

На обоих Mac research roots имеют относительные пути:
`work/brain-sampling-ablation-20261007/run01`,
`work/brain-budget-ablation-20261007/run01`,
`work/brain-horizon-ablation-20261007/run01`,
`work/brain-lr-diagnostic-20261007/run01`,
`work/brain-development-20261007/`.
Outer bundle seals и inner run seals разные; сверять по конкретному report, не сравнивать
их как один hash. Owner root: `/Users/root1/Desktop/memecoin-ai`; worker root:
`/Users/sl-aleksandr/Developer/memecoin-worker`. Live owner DB не открывать как immutable sealed
snapshot; read-only coherent transaction и immutable archived DB требуют разных режимов.

## 19. Что бы я сделал, если бы сегодня начинал этот проект заново, уже зная всё, что мы выяснили.

Я сохранил бы амбицию большого Brain с первого дня и разделил бы её на три равноправных
продукта: **market data platform, learning/evaluation system и paper/operator experience**.
Не строил бы всю систему вокруг короткого experiment runner, который потом приходится
превращать в production orchestration добавлением частных исключений.

Сначала сделал бы один действительно полный source-to-example path на поддерживаемом venue:
durable raw acquisition, versioned decoding, market identity, availability-aware prices,
feature/target dependencies, coverage ledger и streaming dataset. Один полный path полезнее
нескольких частично соединённых collectors. Но его schemas/partition boundaries сразу
спроектировал бы под миллионы и больше examples, а не под список Python dict в RAM.

С первого дня параллельно собирал бы development и future evaluation. Зафиксировал бы
AUTO/Watch estimands и denominators, сделал бы cohorts по времени и mint identity, оставил
отдельную calibration роль. Тогда не пришлось бы выяснять optimizer behavior на одном и том
же восьми-pool test и после каждого результата сомневаться, не подстроились ли мы под него.

Первым champion оставил бы прозрачный empirical/linear baseline. Маленькую GRU использовал
бы для проверки end-to-end обучения, а не называл бы её новым серьёзным поколением после
каждого секундного fit. Затем строил бы data/compute scaling curves на широком development,
увеличивал representation/model size там, где это даёт измеримый gain, и переносил training
на GPU тогда, когда pipeline и batch действительно позволяют использовать GPU.

Sampler objective, unique exposure, gradient norms, clipping fraction, update norms,
per-horizon calibration и checkpoint variability были бы обязательными diagnostics первого
recipe. Equal-pool weighting не принимал бы за нейтральное решение. Head позволяла бы
horizon-dependent response; даже при хорошей head требовал бы независимую calibration,
не обещая, что architectural flexibility сама решает optimization.

С самого начала acquisition был бы постоянным управляемым сервисом с global budget,
classified retries, backpressure и partition quarantine. Bounded qualification experiments
проверяли бы этот сервис перед расширением, но не заменяли бы его. Сеть и один expired exam
не должны были бы каждый раз превращать data collection в новую ручную исследовательскую
кампанию. При этом STOP, receipts и финансовые границы оставались бы честными и проверяемыми.

Watch Market вывел бы на минимальный настоящий live acceptance раньше: конкретный mint,
явная supported/unsupported semantics, pinned champion, видимые forecasts и отдельный book.
Это быстрее вскрыло бы разрыв между bar forecast, задержанным fill и ожиданием пользователя
«Brain видит именно мой рынок». Красивый operator без доказанного пользовательского пути
не является завершённым продуктом.

Из нынешнего проекта я перенёс бы contracts, provenance, immutable artifacts, causal replay,
accounting, полезные decoders и independent verification. Переписал бы места, где пилотные
caps, JSON materialization и ручные campaign-specific guards стали архитектурой. Не переписывал
бы всё только ради модного stack и не покупал бы compute как замену dataset/benchmark discipline.

Самое важное изменение моего подхода: **мерить прогресс не числом аккуратно закрытых маленьких
аудитов, а ростом устойчиво поступающего полезного опыта и независимой способности Brain
учиться на нём лучше.** Проверяемость должна делать этот рост надёжным, а не подменять его.
