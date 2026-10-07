# Sampling mass ablation: B13 sealed snapshot, 2026-10-07

**Результат:** при неизменных данных и процедуре обучения ограничение mass новой когорты
72.09% -> 16.64% улучшило GRU pool-macro pinball у обоих seeds. У ансамбля improvement
6.42% validation / 11.91% test. Причинный эффект sampling на этом snapshot подтверждён,
но полное исправление calibration не подтверждено: median 15m стал заметно хуже.
Это не доказательство причины всего latest AUTO результата +41.95% и не promotion.

Основание: [предшествующий audit](ml-review-audit-2026-10-07.md) и прямое разрешение
пользователя на четыре fits. [Machine-readable evidence](evidence/brain-sampling-ablation-2026-10-07.json)
содержит исходные метрики, protocol, pins и дельты, включая row-micro pinball и per-pool loss.

## Замороженная постановка

- Один `experiment01/broad-snapshot/dataset`: 48,000 train decisions / 86 pools;
  62 новых pools, 7,987 train rows. Validation 264 decisions / 8 pools / 2 UTC dates;
  test 246 decisions / 8 pools / 1 UTC date. Это прежний expansion snapshot, не latest AUTO
  snapshot с 249 test decisions. Все eval pools из прежней когорты; test ранее раскрыт.
- Bounds event seconds: train end `1791244200`, validation end `1791255000`, test end
  `1791265800`; whole-target maturity purge сохранён. Использован точный B13 `partitions()`;
  train/validation/test membership проверен против sealed coverage receipt.
- Одинаковые rows/order, features, targets, train-only scaler bytes, GRU width16/context8,
  seeds7/19, CPU/PyTorch2.14.1, один thread, deterministic algorithms, 1,000 updates × 64
  decision draws, AdamW lr0.001/weight_decay0.0001, loss scale и gradient clipping.
- Checkpoints каждые100 steps, selection по минимальному validation pool-macro pinball.
  Правило одинаковое, выбранный checkpoint может отличаться. Test не используется в selection.
- Arm A: прежний equal-pool/day sampler. Arm B: суммарная mass новых pools равна
  `7987/48000`; исходные относительные pool/day weights внутри каждой когорты сохранены.
  Нет удаления строк, изменения scaler или фильтрации по labels.

Оба A-fit воспроизвели исходные B13 learned tensors **точно**. Source hashes, seeded draws,
scaler и 10 checkpoint receipts каждого fit проверены. Все четыре fits выполнены один раз
после восстановления сети; до этого output root отсутствовал. Fit/check timings суммарно
3.966s, весь runner 8.070s (`2026-10-07T07:43:02.567870Z` -> `07:43:10.637912Z`), без
provider calls. Предыдущая оценка около минуты оказалась консервативной; это один замер.

## Pinball по seeds и aggregate

Ниже primary metric: pool-macro quantile pinball, горизонты5m/15m имеют равный вес.
Меньше лучше; delta=(B/A-1). «Ансамбль» означает средние quantile predictions двух seeds,
а «среднее seeds» означает среднее двух отдельных метрик; эти операции не эквивалентны.

| Split | Модель | A: mass72.09% | B: mass16.64% | Delta B/A |
|---|---|---:|---:|---:|
| Validation | seed7 | 0.014856402 | 0.014686197 | -1.15% |
| Validation | seed19 | 0.015122154 | 0.013636278 | -9.83% |
| Validation | Ансамбль | 0.014873905 | 0.013919116 | -6.42% |
| Validation | Среднее seeds | 0.014989278 | 0.014161237 | -5.52% |
| Test | seed7 | 0.010611839 | 0.010057905 | -5.22% |
| Test | seed19 | 0.010699433 | 0.008974095 | -16.13% |
| Test | Ансамбль | 0.010601888 | 0.009339007 | -11.91% |
| Test | Среднее seeds | 0.010655636 | 0.009516000 | -10.70% |

Ансамбль absolute delta: validation -0.000954790, test -0.001262880.
Row-micro pinball ансамбля: validation 0.014873905 -> 0.013919116,
test 0.010546209 -> 0.009263585. Это forecast loss; costed paper PnL здесь не измеряется.

## Coverage q10/q50/q90

Все значения в процентах, nominal **10 / 50 / 90**. Coverage = доля `y <= predicted q`.

| Split | Модель | A: q10 / q50 / q90 | B: q10 / q50 / q90 |
|---|---|---|---|
| Validation | seed7 | 3.22 / 65.15 / 93.18 | 6.63 / 63.83 / 90.91 |
| Validation | seed19 | 3.22 / 66.10 / 93.56 | 8.14 / 46.97 / 90.72 |
| Validation | Ансамбль | 3.41 / 66.67 / 93.18 | 7.95 / 61.36 / 91.29 |
| Validation | Среднее seeds | 3.22 / 65.63 / 93.37 | 7.39 / 55.40 / 90.81 |
| Test | seed7 | 2.64 / 64.02 / 94.51 | 6.50 / 66.06 / 91.67 |
| Test | seed19 | 2.44 / 64.43 / 95.12 | 7.72 / 50.00 / 92.28 |
| Test | Ансамбль | 2.44 / 65.45 / 94.72 | 6.10 / 62.40 / 92.48 |
| Test | Среднее seeds | 2.54 / 64.23 / 94.82 | 7.11 / 58.03 / 91.97 |

У обоих seeds хвосты стали ближе к nominal. Median seed19 улучшился существенно, но
test median seed7 стал хуже (64.02% -> 66.06%). Описательная mean absolute coverage error
ансамбля по трём quantiles уменьшилась validation8.81pp -> 4.90pp, test9.24pp -> 6.26pp.
Это диагностическое среднее, не formal calibration test и не замена разбивке по horizon.

## Разбивка ансамбля по горизонту

| Split / horizon | Pinball A -> B | Delta | Coverage A | Coverage B |
|---|---|---:|---|---|
| Validation5m | 0.011407287 -> 0.010060313 | -11.81% | 3.41 / 75.38 / 94.70 | 5.30 / 53.41 / 92.80 |
| Validation15m | 0.018340523 -> 0.017777918 | -3.07% | 3.41 / 57.95 / 91.67 | 10.61 / 69.32 / 89.77 |
| Test5m | 0.008498750 -> 0.006754592 | -20.52% | 1.22 / 78.05 / 97.56 | 2.85 / 52.85 / 94.72 |
| Test15m | 0.012705025 -> 0.011923422 | -6.15% | 3.66 / 52.85 / 91.87 | 9.35 / 71.95 / 90.24 |

**Основное ограничение:** median15m ухудшился на обоих splits. На test его отклонение от
50% выросло с2.85pp до21.95pp, хотя tail quantiles стали ближе к nominal и pinball снизился.
У5m median почти исправлен, но q10 всё ещё имеет coverage2.85% вместо10%. Поэтому
aggregate loss/coverage недостаточно для заявления «calibration исправлена».

## Фактический sampling и checkpoints

| Arm / seed | Новых draws из64,000 | Фактическая доля | Unique train decisions | Selected step |
|---|---:|---:|---:|---:|
| A / 7 | 46,050 | 71.95% | 14,023 | 1000 |
| A / 19 | 46,232 | 72.24% | 14,091 | 700 |
| B / 7 | 10,444 | 16.32% | 18,532 | 900 |
| B / 19 | 10,687 | 16.70% | 18,608 | 1000 |

Draw-probability ESS выросла1171.60 -> 2413.12. Это мера концентрации sampler, не количество
независимых наблюдений. Вероятностные mass72.093% и16.6396% отличаются от realised draws
из-за фиксированного multinomial sampling; для каждого seed draw digest сохранён.

## Причинный вывод и следующий recipe

**Узкая причинная гипотеза подтверждена:** на фиксированном snapshot и при фиксированных
seeds изменение только cohort sampling probabilities улучшило выбранные GRU forecasts.
Различие в rows, scaler, architecture, compute или selection rule исключено проверками.
Эффект включает закономерное изменение выбранного checkpoint при прежнем validation rule.

**Сильная гипотеза не подтверждена:** sampling не объясняет автоматически всю calibration
проблему или latest AUTO +41.95%. Здесь другой snapshot; два seeds, один test day, восемь
старых eval pools и overlapping horizons. CI общей применимости не оценён. Уменьшение mass
новых pools может помогать именно старой eval universe; качество на новых молодых pools
этот эксперимент не измеряет. Значение16.64% не установлено как оптимальный budget.

Для следующего исследовательского recipe есть обоснованный **candidate**: явный budget
массы когорты с сохранением pool/day weighting внутри неё. Перед переносом в AUTO нужна
замороженная prospective проверка одновременно на старых и новых pools, с отдельной
calibration по5m/15m и несколькими UTC days. Больше подбирать mass/seeds по этому открытому
test нельзя. Такой следующий эксперимент здесь не запускался.

Production recipe/champion/gates/AUTO не менялись; при восстановленном подключении Mac1
уже был `paused/paused`, после fits остался в том же состоянии, queued/running jobs0.
Champion SHA `9ea4a6bd7977fb66fe4a18eba54ed7eaed0afd65810ec99c75aec3e1443e3920`.

## Pins и воспроизводимость

- Dataset manifest SHA `ba9081b124dd066df8641a19724300c81785b885a15ce17a288a55cdc95be430`;
  content SHA `a6b987591d6617465e430d74c677c994254d305915fb058671bb21b9e60c3ae0`.
- Run manifest SHA `94c86c385c5d944ab34869874cb0922473af6c8fe9676744ad03ace518d15d11`.
- Runner SHA `6b5637ad9079bc089a4908ad741272a350e79dc26592f1f75573468a56e155e8`.
- Mac1 immutable run: `work/brain-sampling-ablation-20261007/run01`.
  Mac2 verified copy: тот же относительный путь; рядом `runner.py` и `eval-inputs.json`.
  Полный local bundle, включая `verify-results.py`, sealed manifest SHA
  `f49ebb2a1ef59ecc968f1ec30d67b2e6ead31aab4528e15518e596ed5707109c`.
- Exact runtime source hashes и protocol сохранены в evidence; training modules совпадают
  с original B13 binding и worker Git `c076e0b`. Runtime08 не модифицировался.
- На Mac2 без Torch заново вычислены36metric groups из predictions и exported sealed
  targets (combined + оба horizons, два seeds + ensemble, оба splits, оба arms): loss error
  <1e-14, coverage exact. Проверены все member hashes run и соответствие eval membership.
