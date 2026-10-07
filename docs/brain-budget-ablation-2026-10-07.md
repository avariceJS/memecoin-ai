# Sampling × training budget: диагноз и результаты

**Диагноз:** подтверждены два отдельных конечных эффекта: изменение optimizer distribution
из-за cohort sampling и недостаточный для лучшего observed loss budget 1000 steps. Их сочетание
объясняет значительную часть ухудшения на данном fixed expansion snapshot. При capped mass
и 4000 steps ensemble test pinball на 20.76% ниже current sampler/1000 steps. Увеличение budget
само по себе не исправляет calibration; признаки ограничений общей horizon head и заметная
seed/checkpoint sensitivity сохраняются. Доказательств необходимости заменить GRU backbone нет.

[Полное machine-readable evidence](evidence/brain-budget-ablation-2026-10-07.json) включает
все seeds, horizons, weighted train diagnostics, terminal checkpoints, protocol и source pins.
Ниже сначала сохранена постановка, зарегистрированная до продолжения, затем результаты.

Дата: 2026-10-07. Пользователь разрешил короткую самостоятельную причинную диагностику
sampling/distribution mismatch и fixed training budget. Production/champion/AUTO не менять.

## Зафиксированная проверка

Два прежних sampler arms (new mass72.09% /16.64%) × budgets1000/4000 updates × seeds7/19.
Используются sealed weights/optimizer/RNG на step1000 из предыдущего
[ablation](brain-sampling-ablation-2026-10-07.md), без повторного обучения первого1000steps.
Те же48000trainrows, scaler, targets, architecture, optimizer/loss/gradient clipping.
Единственное дополнительное вмешательство: ещё3000updates на каждый из четырёх fits.

Выбор4000steps сделан до новых результатов: старый control расходовал64000draws на старые
pools, broad при1000steps только17860 expected olddraws; ×4 даёт71442old draws и перекрывает
потерю exposure. Retained dataset у обоих прежних corpus arms был48000rows, поэтому гипотеза
«больше retained rows при прежнем числе updates» буквально не объясняет тот expansion.

До продолжения: проверить полное совпадение первых64000draws в длинном seeded stream с
исходным; replay steps901–1000 из checkpoint900 должен точно воспроизвести model+optimizer
checkpoint1000 у всех4fits. Это verification replay, а не новые независимые trials.
Checkpoint cadence остаётся100updates (не `steps//10`, который при4000 изменился бы на400).
Выбор по прежнему минимуму validation pool-macro pinball среди checkpoints100–4000; test
не участвует. Дополнительно сравниваются фиксированные terminal checkpoints1000/4000,
чтобы отличить longer optimization от расширения числа кандидатов checkpoint selection.

Primary outcomes: validation/test pinball и q10/q50/q90 coverage по seed/ensemble и horizon;
sampling × budget interaction. Diagnostics: coverage на полном train под uniform-row и
фактическими sampler weights, отдельно old/new/sparse-new cohorts; фактические draw counts;
constant horizon-median offset из текущей linear head. Weighted empirical quantiles служат
контролем train calibration/discrete target mass. Эти диагностики не меняют selection.

Compute: CPU1thread, PyTorch2.14.1, deterministic algorithms, total continuation12000updates
+400verification updates, максимум300s. Новый output `work/brain-budget-ablation-20261007/run01`,
никаких writes в прежние sealed artifacts или operator registry. Ноль provider requests,
новых architecture variants, automatic retry, promotions, release/AUTO mutations.

Test уже раскрыт:246decisions/8oldpools/1UTCday. GeneralizationCI и вывод о новых pools по
этомуtest недоступны. Capped mass16.64% и4000updates не объявляются оптимальными настройками.
ПоследнийAUTO snapshot и его+41.95% не являются предметом прямого matched comparison здесь.

На момент фиксации protocol эксперимент ещё не был выполнен. Его неизменная копия сохранена
в `work/brain-budget-ablation-20261007/registered-protocol.md`; SHA входит в run protocol.

## Результаты factorial comparison

Primary metric: pool-macro pinball, combined5m/15m; меньше лучше. Ensemble = средние
quantile predictions seeds7/19. Coverage в процентах, nominal q10/q50/q90=10/50/90.
Все selected models выбраны только по validation, по одинаковому правилу и cadence100steps.

| New-cohort mass | Updates | Validation loss | Test loss | Test q10/q50/q90 coverage |
|---|---:|---:|---:|---|
| 72.09% current | 1000 | 0.014873905 | 0.010601888 | 2.44 / 65.45 / 94.72 |
| 72.09% current | 4000 | 0.013350575 | 0.009653287 | 4.47 / 38.01 / 93.29 |
| 16.64% capped | 1000 | 0.013919116 | 0.009339007 | 6.10 / 62.40 / 92.48 |
| 16.64% capped | 4000 | 0.012099615 | 0.008400464 | 7.72 / 48.78 / 92.68 |

Контролируемые контрасты:

- Только budget1000->4000: current sampler -10.24% validation / -8.95% test;
  capped sampler -13.07% validation / -10.05% test.
- Только sampling72.09%->16.64% при4000steps: -9.37% validation / -12.98% test.
  Преимущество sampler не исчезло после компенсации количества old-pool draws.
- Оба изменения: -18.65% validation / -20.76% test относительно current1000.
- Additive difference-in-differences по test loss = +0.000010058 (selected models).
  Это описательная величина; двух seeds недостаточно для inference о статистическом interaction.
  Нельзя складывать относительные проценты или объявлять доказанную synergy.

## Seeds и проверка checkpoint selection

| Arm4000 / seed | Selected step | Validation loss | Test loss | Test coverage |
|---|---:|---:|---:|---|
| Current /7 | 3700 | 0.013411189 | 0.009638147 | 6.10 / 52.64 / 94.31 |
| Current /19 | 3100 | 0.013924311 | 0.010041195 | 4.07 / 31.10 / 92.89 |
| Capped /7 | 4000 | 0.012289378 | 0.008750951 | 8.13 / 39.43 / 90.45 |
| Capped /19 | 3200 | 0.012229911 | 0.008405482 | 7.93 / 58.94 / 93.50 |

В4000-step run появилось больше eligible checkpoints. Чтобы не приписать всему improvement
только optimization, отдельно проверены фиксированные terminal1000/4000 checkpoints:

| Arm | Terminal test loss1000 ->4000 | Delta | Terminal test median coverage1000 ->4000 |
|---|---|---:|---|
| Current | 0.010913531 ->0.010528421 | -3.53% | 70.53% ->83.33% |
| Capped | 0.009325468 ->0.008437630 | -9.52% | 71.95% ->53.86% |

Таким образом, budget помогает loss и без выбора лучшего checkpoint, но current sampler
при longer training может резко ухудшать median coverage. Lowest pinball selection не
равнозначен calibration qualification. Один capped seed выбрал boundary4000: достижение
сходимости к4000steps и оптимальность такого budget не установлены.

## Почему это distribution mismatch

При1000steps current sampler даёт почти номинальный median coverage5m на своей weighted
training population, но сильный bias на old cohort **внутри тех же train данных**:

| Seed | All weighted train median5m | Old train median5m | New train median5m |
|---|---:|---:|---:|
| 7 | 54.21% | 78.84% | 44.67% |
| 19 | 48.12% | 69.84% | 39.71% |

Test содержит8oldpools; ensemble5m coverage78.05% согласуется с old-train симптомом.
Это сильное механистическое evidence: модель оптимизируется под одну mixture population,
а проверяется под другой. Новый sampler меняет именно эту смесь; scaler/rows остаются теми же.
Нельзя считать weighted global calibration доказательством calibration каждого cohort.

Различаются и target distributions. Ниже train quantiles log-return под условными pool/day
weights внутри когорты; это не проценты price PnL и не будущие test labels:

| Cohort / horizon | q10 | q50 | q90 |
|---|---:|---:|---:|
| Old5m | -0.034970 | +0.002029 | +0.047450 |
| New5m | -0.170544 | -0.007365 | +0.090495 |
| Old15m | -0.060467 | +0.006154 | +0.088092 |
| New15m | -0.345796 | -0.024773 | +0.136290 |

Новая cohort существенно более отрицательно асимметрична. Её72.09% optimizer mass нельзя
интерпретировать просто как «больше разнообразия при прежней цели». При этом установлено,
что weighted empirical quantiles на том же train дают примерно10/50/90% coverage
(отклонение максимум0.065pp): наблюдаемые большие GRU biases не объясняются одним соглашением
`y<=q` или дискретными ties в labels.

## Что действительно делает фиксированный budget

И control, и broad уже имели cap48000retained rows. 64000draws/48000rows=1.333 осталось
тем же. Поэтому буквальное объяснение «train rows выросли, epochs упали» для этого
эксперимента **неверно**. Изменились effective exposure и распределение повторов.

| Arm / seed | Old draws1000 | Old draws4000 | Unique decisions4000 |
|---|---:|---:|---:|
| Current /7 | 17,950 | 71,503 | 25,982 |
| Current /19 | 17,768 | 71,258 | 25,876 |
| Capped /7 | 53,556 | 213,818 | 36,413 |
| Capped /19 | 53,313 | 213,361 | 36,464 |

В исходном24-pool control все64000draws приходились старой когорте. Current4000 уже
получает больше old draws, чем control1000 или capped1000, но test loss остаётся хуже
capped1000 (0.009653287 против0.009339007). Дефицит exposure и changed objective нельзя
свести к одному счётчику updates. Сам budget действительно ограничивает observed loss,
однако чистое увеличение steps не устраняет смещение optimizer population.

## Calibration: исправлена не полностью

Capped4000 ensemble выглядит хорошо в aggregate: test7.72/48.78/92.68%. Однако:

- По horizons test median coverage5m=41.06%,15m=56.50%.
- По seeds combined median coverage39.43% и58.94%.
- На validation capped4000 median5m=38.64%,15m=62.88%.
- На weighted train отдельных selected capped models median также нестабилен: seed7
  34.00%/34.85% для5m/15m, seed19 53.40%/45.71%.

Иными словами, часть красивого aggregate результата получается при усреднении разных
ошибок. Полная calibration по horizon/seed/cohort не квалифицирована.

Код `memecoin_ml/models.py` добавляет normalized horizon только в общую linear head после
encoder. Отсюда `median15m - median5m = 10 * head.weight[0,-1]` для любого history.
Prediction replay подтвердил эту константу: максимальный observed range5.96e-8 (float32).
Это реальное ограничение head, а не особенность именно GRU encoder. Оно делает horizon
responses связанными; его причинный вклад в residual calibration failure **не изолирован**,
поскольку architecture ablation здесь не выполнялась. Менять backbone на основании этих
результатов преждевременно.

## Диагноз и следующий шаг

1. Главный подтверждённый механизм исходного симптома: optimizer objective стал преимущественно
   new-cohort objective, а eval остался на old markets. Sampling меняет качество причинно
   при обоих проверенных budgets; «GRU сама по себе плохая» из evidence не следует.
2. Budget1000 также ограничивает качество. Эффект сохраняется при terminal comparison,
   но увеличение steps без изменения sampler не гарантирует calibration и может усилить bias.
3. Нестабильность optimization/checkpoint/seed и общая horizon head остаются ограничениями.
   Head restriction подтверждён кодом, но его contribution нельзя объявлять установленной
   причиной без отдельной архитектурной ablation. Ни4000steps, ни16.64% не доказаны оптимальными.
4. Следующее исследовательское действие: зафиксировать target deployment mixture и candidate
   recipe с явным cohort budget и достаточным validation-обоснованным compute; оценить frozen
   candidates на общей prospective выборке old+new pools. Обязательны отдельные horizon/cohort
   coverage и seed spread, а не только pooled loss/coverage. Support/realised exposure следует
   измерять вместе с compute; raw corpus size сам по себе недостаточен для назначения budget.
5. Если после такой фиксации данных/calibration остаётся плохой, следующий узкий diagnostic
   contrast должен проверять horizon head при прежнем GRU. Новый backbone или увеличение размера
   пока не обоснованы. Дополнительные fits или prospective exams в этой сессии не запускались.

Ограничения: previously exposed historical test,246decisions/8oldpools/1day,2seeds, CI нет.
Новые pools не имеют независимого eval в этом эксперименте; train diagnostics не заменяют
out-of-sample result. Это не доказательство всей причины latestAUTO+41.95% на другомsnapshot,
не сравнение с champion и не свидетельство trading edge.

## Проверки и artifacts

Четыре exact continuations,12000новыхupdates плюс400verification replayupdates;
model+optimizer replay900->1000 совпал точно у всех4fits. Longdrawstream воспроизвёл исходный
prefix. Original1000metrics exact. Selected/terminal weights независимо сверены с checkpoints;
все144evalmetricgroups (seed/ensemble,selected/terminal,1000/4000,split,horizon) независимо
пересчитаны наMac2: losserror<1e-14,coverageexact. Все artifact member hashes совпали.

Run2026-10-07T08:18:16.535578Z ->08:18:33.123755Z,16.588s целиком; четыре continuation/replay
блока11.122s. CPU1thread,0providercalls. Protocol и source hashes находятся в JSON evidence.

- Mac1/Mac2 run: `work/brain-budget-ablation-20261007/run01`.
- Local bundle с runner, protocol, verifier и дополнительной проверкой: manifest SHA
  `bc2222f6bcbc5dd6c63cc3a75fb82d922cfe9d17a33a797a803a9d55bc1b17bf`.
- Manifest SHA `58545e8ef584e2987708169c330100c00d6ebc1693910d7ffded79abd738349a`.
- Prior run SHA `94c86c385c5d944ab34869874cb0922473af6c8fe9676744ad03ace518d15d11`.
- Snapshot manifest SHA `ba9081b124dd066df8641a19724300c81785b885a15ce17a288a55cdc95be430`.
- Mac1 до/после: paused/paused,domainSTOP=false,activejobs0, champion SHA
  `9ea4a6bd7977fb66fe4a18eba54ed7eaed0afd65810ec99c75aec3e1443e3920`.
  Production/runtime/AUTO/champion/gates не менялись; original artifacts не переписывались.
