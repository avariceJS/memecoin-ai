# Train-only LR continuation: preregistration

Разрешение: короткое research сравнение без champion/production/AUTO changes.
Цель: отделить finite-step optimization/checkpoint instability от необходимости менять
backbone. НЕТ model selection/generalization claims или нового просмотра eval scores.

Старт: четыре terminal4000 model+AdamW states sealed budget ablation
`work/brain-budget-ablation-20261007/run01`, manifest
`58545e8ef584e2987708169c330100c00d6ebc1693910d7ffded79abd738349a`.
Два sampler sensitivity arms72.09%/16.64%, seeds7/19, исходный GRU/sharedhead.
Каждый checkpoint fork в LR0.001(control)/0.0001(intervention). Другие settings,
scaler, train rows, loss scale, optimizer moments, clipping неизменны. AdamW decay
coefficient прежний: LR меняет также величину decoupled decay, это часть intervention.

Продолжение4001..5000:1000updates на fork, одинаковый deterministic draw suffix.
Первые256000draws в новом5000step stream должны точно совпасть с прежним budget receipt.
Итого8continuations/8000updates, CPU1thread, deterministic, hard deadline300s.
Не обучаем новый encoder/head, не меняем batch/weights, не выбираем лучший checkpoint.
Оцениваем full TRAIN под фактическими sampler weights в4000,4100,...,5000;
дополнительно old/new cohorts ×5m/15m. Сохраняем optimizer terminal states, diagnostics,
gradient norm/clipping и predictions на фиксированной small audit выборке train.
Исходный sealed dataset содержит eval rows, но inference/scoring validation/test НЕТ.
F01/D43/reserved holdout не открываются. Zero provider requests.

Primary: terminal train pinball и mean absolute coverage error к10/50/90;
stability: range и population SD coverage/loss на последних6fixed checkpoints4500..5000.
Lower temporal variance без улучшения terminal loss/calibration НЕ считается исправлением.
Публикуем каждый seed/sampler/cohort/horizon, не только среднее и не лучший результат.
Снижение train loss не доказывает future gain;2seeds и один snapshot ограничивают причинный
вывод. Этот опыт диагностирует LR intervention conditional on checkpoint4000, не оптимальный
LR schedule с начала обучения. При неустойчивом результате не запускать sweep автоматически.

Output новый `work/brain-lr-diagnostic-20261007/run01`, без retry/overwrite completed fits.
Broad development/calibration/prospective benchmark формируется отдельно; результаты
этого опыта не открывают readiness gate. На момент регистрации эксперимент не проведён.

## Результат

Восемь continuations завершены за31.218s. Train48k/86pools, тот же scaler/code/optimizer
checkpoint и совпадающий deterministic draw prefix/suffix. Каждая paired fork имеет exact
initial diagnostics. Validation/test predictions0, selection0, promotion0. Никаких retries.

Агрегаты ниже — арифметическое среднее per-seed metrics, НЕ quantile ensemble. Loss и coverage
взвешены фактическим sampler, усреднены между5m/15m. Calibration MAE — средний модуль
отклонения hit-rate от10/50/90 в процентных пунктах. Stability — population SD median
coverage по шести fixed checkpoints4500..5000, средняя по horizons. Это temporal variability
одного fit, НЕ uncertainty interval и НЕ population statistical SD.

Current mass72.09%, LR0.001 ->0.0001:
- Seed7: terminal loss0.036448799 ->0.037158456 (+1.95%);
  calibrationMAE2.927 ->2.342pp; medianSD8.310 ->3.769pp (-54.64%).
- Seed19: loss0.038437790 ->0.039444141 (+2.62%);
  calibrationMAE2.545 ->1.995pp; medianSD7.923 ->3.585pp (-54.76%).
- Mean seeds: loss+2.29%; calibrationMAE2.736 ->2.169pp;
  medianSD8.117 ->3.677pp (-54.70%).

Capped sensitivity mass16.64%, LR0.001 ->0.0001:
- Seed7: loss0.040092997 ->0.039441208 (-1.63%);
  calibrationMAE4.766 ->2.888pp; medianSD11.912 ->4.300pp (-63.90%).
- Seed19: loss0.040124999 ->0.039536237 (-1.47%);
  calibrationMAE11.525 ->2.857pp; medianSD16.224 ->7.461pp (-54.01%).
- Mean seeds: loss-1.55%; calibrationMAE8.145 ->2.872pp;
  medianSD14.068 ->5.880pp (-58.20%).

НЕЛЬЗЯ сравнивать absolute current/capped weighted train loss как качество двух моделей
на одинаковом target distribution: measures различны. Причинные контрасты — внутри sampler.
НизкийLR улучшилterminal pooled calibration во всех4pairs, а не только уменьшил скорость
движения. Однако loss current стал хуже: меньше oscillation не равно convergence/global optimum.

Пример исправленного instability: capped seed19 old-train coverage5m
6.37/29.25/74.83 ->5.80/47.49/94.36%;15m6.81/26.62/72.39 ->7.03/45.83/93.79%.
Но residual bias остаётся: current lowLR old15m median25.99%(seed7)/37.74%(seed19),
при new15m55.50%/56.34%. LR не устраняет objective/cohort mismatch и не гарантируетtails.

Gradient clipping активен на100%updates у всех8forks. Средняя pre-clip norm приcurrentLR
55.3–95.9, приlowLR33.9–37.4, threshold1; loss нормирован train IQR. Это измеренный
optimization regime, НЕ доказанный bug или аргумент выключить clip. LR intervention
не отделяет эффект clipping/momentum/decay; их одновременно не меняли.

## Диагноз и решение

Гипотеза optimization/checkpoint instability получила прямую conditional-on-checkpoint
поддержку: одинаковые starting state/draws, lowerLR уменьшает coverage oscillation у всех
seeds/samplers и улучшает terminal train calibration. Это сильнее основания менятьbackbone.
Но причинный вывод ограничен этим snapshot/поздним continuation; общий quality/future gain
не установлен. Нельзя объявить LR0.0001 новым default или выбирать расписание по старомуtest.

Следующий model-comparison experiment: фиксированный GRU/sharedhead, прежний LR против
зарегистрированного late-decay schedule, same objective/seeds/compute, на новом широком
development benchmark. Один заранее заданный terminal/stability criterion, безtest selector.
Сейчас такой benchmark отсутствует: сначала populated development/exposure contract,
затем отдельные calibration/future stages. [Основа benchmark](brain-benchmark-v1.md).
Дополнительный LR sweep/новый backbone/production change в этом этапе не запускались.

## Проверки и pins

48terminal groups независимо reconstructed через повторную inference sealed weights и
stdlib `math.fsum` weighted scoring: max error2.220446049250313e-16. Проверены stability
из истории, initial paired diagnostics/draw suffix equality и88audit prediction files
(finite/ordered). Полный per-checkpoint train prediction dump не сохранялся: исторические
coverage curves подтверждены receipts, не повторной inference всех88checkpoints.

[Полный JSON](evidence/brain-lr-diagnostic-2026-10-07.json) содержит каждыйseed/cohort/horizon,
историю и source/data/code pins. Sealed run на обоихMac:
`work/brain-lr-diagnostic-20261007/run01`, manifest
`3d81f4eb1239cf7dacfc4ade2650e6c5d752a0de4f97d19be76175258e07415a`.
Outer bundle Mac2 с runner/preregistration/verifier:
`b67e17c5383656c337b02c1cf4fe86785ac1e70caadbfed1b639b8e915b02bc3`.
КодbaseHEADc076e0b; CPU1thread/Torch2.14.1, forecast training only, fills0.
Owner до/после revision33 paused/paused(reason expired),queued/running0, officialchampion
`9ea4a6bd7977fb66fe4a18eba54ed7eaed0afd65810ec99c75aec3e1443e3920` unchanged.
