# Horizon interaction и целевое распределение Brain

## Протокол до результатов

Дата: 2026-10-07. Изолированный research, без production/champion/AUTO mutations.
Проверяем causal effect снятия constant horizon-median offset при неизменном GRU.
Один sealed 86-pool expansion snapshot, прежние train/validation/test membership,
scaler, targets, AdamW, clipping, loss scale, seeds 7/19, CPU1thread, 4000 updates,
checkpoint cadence100. Два прежних sampler arms72.09%/16.64% служат sensitivity,
а не предлагаемыми production mixture. Ни один процент не объявляется целевым.

Control: исходная linear head. Intervention: к первым трём logits добавить
`s(h) * W_interaction @ latent`, где s(5m)=-1, s(15m)=+1, W имеет48параметров
и инициализируется нулём. Исходные predictions/encoder/head weights совпадают точно.
Median и softplus gap logits получают разные latent slopes по horizons; ordering
quantiles сохраняется. Других architecture variants/поиска hyperparameters нет.
Восемь fits:2samplers ×2heads ×2seeds. Controls должны точно воспроизвести прежние
4000-step terminal/selected weights. Это повторная verification, не новые seeds.

Primary: terminal4000 pinball и q10/q50/q90 coverage по horizon, seed, ensemble.
Secondary: validation-selected checkpoint, тот же pool-macro minimum/100step cadence.
Дополнительно train coverage/pinball под sampler weights отдельно old/new cohorts;
это in-sample diagnostic, НЕ generalization на новых рынках. Проверить initial
function equality, возможность непостоянного median horizon contrast, finite/ordered
quantiles и независимый пересчёт eval metrics по predictions/targets.

Test уже раскрыт:246overlapping decisions/8oldpools/1day. Нет нового test, чтения
F01/D43/reserved holdout, acquisition, выбора модели по test или promotion.
Все fits/selection завершаются до нового scoring test. Максимум300s, no automatic
retry; output `work/brain-horizon-ablation-20261007/run01`, только новый isolated root.
Результат ограничен вмешательством head при данном optimizer/budget: он не отделяет
representational benefit от изменения optimization geometry при добавлении parameters.
Нельзя объявлять new-cohort out-of-sample improvement по train calibration.

## Результат

Все8fits завершены за37.406s. Initial function equality и synthetic interaction-capacity
checks прошли для обоих seeds. Controls точно воспроизвели terminal и selected weights
предыдущего budget experiment. По saved predictions/targets независимо пересчитаны144
группы eval metrics: loss error0, coverage exact. Prediction quantiles finite/ordered.
После intervention median horizon gap действительно меняется по histories: например
capped seed7 terminal от -0.039441 до +0.061977 log-return против прежнего constant0.001741.
Таким образом, отрицательный результат не объясняется неработающим intervention.

Primary fixed-terminal4000 ensemble, control -> interaction:

- Mass72.09%: validation0.014260896 ->0.013885285 (-2.63%);
  test0.010528421 ->0.010114593 (-3.93%). Test coverage8.94/83.33/94.11%
  ->10.77/82.72/94.11%. Median calibration НЕ исправлена.
- Mass16.64%: validation0.012291340 ->0.012495636 (+1.66%);
  test0.008437630 ->0.008529096 (+1.08%). Coverage5.89/53.86/93.09%
  ->8.13/53.25/92.28%. Похожая pooled median скрывает5m38.62%/15m67.89%.

Primary по отдельным seeds, test pool-macro loss и pooled q10/q50/q90 coverage:

- Current/7:0.010689312 ->0.011693306;12.40/82.93/94.72 ->15.65/85.98/94.51%.
- Current/19:0.010573264 ->0.009796567;7.52/82.32/93.50 ->8.74/57.93/93.70%.
- Capped/7:0.008750951 ->0.008625478;8.13/39.43/90.45 ->12.60/48.37/92.07%.
- Capped/19:0.008515442 ->0.008814728;6.91/67.07/93.09 ->5.28/52.64/91.87%.

Secondary validation-selected ensemble:

- Current: validation+0.16%, test-4.90%; pooled test median38.01 ->51.02%,
  но5m42.28%/15m59.76%, tails5.08/93.09% вместо10/90%.
- Capped: validation+2.55%, test+1.13%; test coverage7.72/48.78/92.68
  ->9.76/47.56/92.48%; median5m41.06%/15m54.07%.

Head помогает некоторым cells/quantiles, но нет устойчивой победы по seeds/samplers/loss.
Нельзя cherry-pick current selected median51.02% и назвать это исправленной calibration.
Два seeds и один exposed test day не дают надёжного generalization uncertainty estimate;
приведённые проценты — paired descriptive effects, не статистически доказанная победа.
Вывод: structural restriction confirmed; hypothesis «главная оставшаяся причина и простое
исправление» NOT established. Отрицательный результат этого bounded fit не доказывает,
что более гибкая head никогда не полезна или constant-gap restriction безвреден.

## Что является целевой популяцией

Различать три оси: admission cohort(old24/new62), возраст market на origin и unseen pool
относительно model training. New admission не означает новый market сегодня. Birth corpus
покрывает первые<=3days исторических markets; live old cohort иная по age/regime/selection.
Из raw row share или количества pools нельзя вывести production mixture.

Фактические paths:

1. AUTO: `memecoin_brain/source.py:select_universe` берёт current public rankings,
   liquidity>=10000, round-robin age<1d/1–7d/>=7d; это survivor-biased discovery, не
   случайный рынок. B09 hot lane использует liquidity-selected subset6из24 и freshness
   gate300s. Current forecast traffic существенно уже universe и тем более86trainpools.
2. Watch Market: `memecoin_operator/watch_market.py` и [контракт](watch-market.md).
   Пользователь выбирает mint/pool, forecast требует контекст/closed bars/freshness.
   Нет основания переносить AUTO liquidity/ranking filters или его traffic weights
   на пользовательские Watch sessions. Реальная Watch traffic mixture пока не измерена:
   live acceptance/deployment ещё не выполнены. Любой выбранный mint не гарантирует
   qualified provider support; WAIT/unsupported сохраняется, не выдумывать прогноз.
3. Historical birth cohort тоже не весь рынок: migrated/provider-covered/contiguous
   survivors. Missing/sparse/failed hydration не являются нулевыми returns или dead labels.

Принимаем research target как ДВЕ lane-specific популяции eligible forecast decisions,
а не один искусственный old/new percentage. Unit: уникальный pool/origin/horizon,
не повторный polling tick и не каждое повторное открытие одной свечи в UI.
Для AUTO primary operational weighting — фактические eligible origins за заранее
закреплённое forward window. Для Watch primary breadth guardrail — macro по markets
в каждой age/seen stratum; empirical session/decision weighting публикуется отдельно,
когда появятся реальные session logs. Доля строк corpus ни один estimand не заменяет.
Без измеренного traffic/политики product-mix единственный «правильный» глобальный scalar
не идентифицируем. Пока выбор — vector scorecard/no-regression по supported strata,
не подбор coefficient, на котором выигрывает конкретная candidate.

Обязательные срезы: lane × horizon5m/15m × seen/unseen pool × age<1d/1–7d/>=7d.
Отдельно reporting по observable volatility/volume, venue и available liquidity;
thresholds фиксируются на train/reference до просмотра eval, неизвестные metadata
получают unknown bucket. Не переносить сегодняшнюю liquidity в исторический origin.
Не размножать sparse strata в независимые наблюдения: показывать pools, days, unique
origins, nonoverlap windows, abstention/missing/matured fractions. Если support мал,
verdict insufficient, а не условные «calibrated» из pooled average.

## Train и eval должны соответствовать задаче, но не совпадать искусственно

Training recipe должен задавать явный и версионированный reference objective по observable
strata. Иерархия weights: lane/reference stratum mass -> support-aware pool/day mass ->
rows; появление очередного tiny pool не должно автоматически пересчитывать массу всей
молодой когорты. Support измерять также по disjoint windows/дням, не только raw rows.
Sparse markets не исключать задним числом по outcomes: ограничить их individual weight,
сохранив отдельный robustness slice и admission-failure accounting.

Точные stratum proportions/support cap не выбирались этим опытом. Их следует закрепить
по task/reference sampling до нового model comparison, затем оценивать sensitivity,
а не устанавливать16.64%,72.09% или50/50 как естественную истину. Если для coverage
oversample редкий stratum, отличать sampling proposal p от target loss measure q:
коррекция q/p сохраняет objective лишь при общем support; clipping weights меняет
objective и должно быть отдельным preregistered intervention, не скрытой деталью.
Не обещать importance correction для unsupported regimes/selection bias без positivity.

Validation для tuning должна содержать отдельные old/new, young/mature strata, а не
только old8pools. Train/model-selection/calibration/final-test — разные temporal blocks;
whole-target maturity/receipt availability и purge полного feature dependency обязательны.
Отдельный pool/mint-disjoint track проверяет cold-market transfer; temporal-seen track
проверяет будущие regimes знакомых markets. Родственные pools одного mint не объявлять
независимыми unseen markets. Final prospective origins/labels появляются после freeze;
F01/D43/reserved holdout не использовать для этой разработки.

Primary metrics: pinball и signed coverage residuals q10/q50/q90 ПО каждому horizon/stratum;
дополнительно interval width и macro/traffic-weighted versions. Uncertainty оценивать
paired pool/day blocks при достаточном support, не iid-bootstrap overlapping variants.
Test8oldpools/1day остаётся только regression/descriptive benchmark. Historical already
exposed unseen report полезен как история, но в этом опыте его данные не читались.
Trading costs/policy не менялись, paper performance/edge этим forecast test не измерены.

## Что ещё мешает calibration и следующий шаг

Подтверждены: objective/exposure mismatch, finite-budget effect, seed/checkpoint sensitivity,
недостаточное соответствие validation deployment breadth, структурная horizon coupling.
Но contribution последней невелик/неустойчив в данном intervention. Даже train calibration
не стабильна: у capped interaction terminal seed19 old-train median5m41.75%/15m68.38%;
у seed7 old-train38.18%/64.77%. Поэтому одной сменой eval population всё не объясняется.
Current control seed19 old-train median15m меняется22.89% selected ->75.76% terminal;
это checkpoint sensitivity, а не доказательство необходимости другого backbone.

Pinball selection оптимизирует средний loss, не гарантирует conditional calibration.
Общая median участвует также в обоих ordered tails, поэтому gradient median-head нельзя
отождествить только с median hit-rate. Более высокая dispersion15m/new cohorts даёт
больше абсолютного loss; общий train IQR scale это между strata не устраняет. Простое
усреднение quantiles seeds также не гарантирует calibration ансамбля. Эти механизмы
нужно различать с доказанными причинными эффектами: LR/gradient noise/overfit/недостаток
capacity данным опытом отдельно не идентифицированы; новое post-hoc correction не fitted.

Следующий наиболее полезный recipe experiment после этой проверки: fixed encoder/head,
same sampler/draws, короткий low-LR continuation versus прежний LR с diagnostics на
weighted train и заранее закреплённом широком validation. Это отделит optimization
instability от необходимости менять representation. Но сначала нужен именно old+new
development-eval contract/достаточный validation support; не продолжать выбирать LR,
sampling fraction и steps по открытому old test. Отдельный chronological calibration
block может проверить horizon-specific residual correction; он не лечит unsupported
markets и не должен одновременно быть selection или final test.

Ни16.64%/4000, ни experimental head не приняты как production recipe. Предпочтение сейчас:
сохранить GRU/official champion, формализовать reference distribution и собрать/закрепить
широкий dev/prospective evaluation в отдельном bounded protocol; только затем выбирать
support-aware sampler/convergence rule. Новая acquisition здесь НЕ запускалась.

## Evidence и safety

[Полные metrics/seeds/train diagnostics](evidence/brain-horizon-ablation-2026-10-07.json).
Run на обоих Mac: `work/brain-horizon-ablation-20261007/run01`;
manifest SHA `497a90dfd84aa409eedc5e731ca70fd9b21a44475d1b2ce6954a3adf841de91d`.
Outer bundle на Mac2 содержит runner, preregistration, independent verifier и outputs;
manifest SHA `c35dec38ed3c73e2ec1bb5f7e6f804909f85896157bdcc85a28d03eb8bfd701e`.
Bundled verifier — provenance генерации evidence ДО outer seal; он пишет verification.json.
Для повторного запуска использовать отдельную незапечатанную копию, не изменять sealed root.
Source runtime08 code/data pins/scaler/membership/seeds указаны в protocol.json; codebase
HEAD `c076e0b3aae59199cb4ca789e86bbd1b5b41d88b`. Execution: CPU/Torch2.14.1/1thread,
forecast-only, без trading fills. Все исходные sealed artifacts неизменны.
Owner before/after revision33, paused/paused(reason expired), completed jobs only;
champion `9ea4a6bd7977fb66fe4a18eba54ed7eaed0afd65810ec99c75aec3e1443e3920` unchanged.
AUTO/release/gates/production source не менялись. Это не новый release-readiness audit.
