# Brain benchmark v1: development, calibration, prospective

Дата: 2026-10-07. Статус: контракт и metadata preflight реализованы; реальный широкий
benchmark НЕ наполнен. Production/champion/AUTO не менялись. Cold-transfer roster завершён:
40GET,6supportedmints/453origins/29globaldisjointwindows; age7–10d support0,71.08%origins
одногоmint. Futurepackets по-прежнему отсутствуют. Новый bounded research envelope работает,
но не квалифицирован для unattended acquisition:
[research result](brain-research-envelope-2026-10-07.md). Historicalcold-mint
transfer — отдельныйdescriptivedevelopmenttrack, незаменаstrictchronological/futurestages.

## Что уже есть, а чего нет

- Sealed expansion snapshot86pools: train48000rows, validation264/8oldpools,
  test246/8oldpools/1day. Train использован в нескольких fits; eval многократно раскрыт.
  Роль snapshot: train-only diagnostics и regression. Перенарезка его train не создаст
  чистую оценку уже обученных моделей: их scaler/weights уже видели эти строки.
- Latest AUTO snapshot `a1c2f6d...1099` уже audited; его test тоже не новый holdout.
- Published historical unseen20pool campaign:13eligiblepools/924decisions, ранее раскрытый
  descriptive report, не prospective. В текущей работе raw этого корпуса не открывались;
  `descriptive_unseen_test` не допускается в новый train/benchmark ingest.
- Existing future exams квалифицируют лишь свои заранее frozen bundles и узкий old-source
  поток. Replay новых LR models на их уже прошлых origins не станет prospective.
- Watch Market не прошёл owner-host live acceptance: пользовательского traffic measure
  и полноценного old+new Watch benchmark сейчас нет.
- F01/D43/reserved holdout вне этого проекта benchmark; не читать и не переразмечать.

Known exposed manifests закреплены в [config](../configs/brain-benchmark-v1.json).
Registry начальный, не исчерпывающий: до нового ingest обязательна lineage/exposure review
всех source refs, включая родительские datasets, fits, scaler и предыдущие reports.

## Единица и целевые распределения

Canonical decision: lane/pool/origin_closed_bar/horizon5m-or15m. Повторныйpoll/session UI
не добавляет второй outcome. Объединять pools одного mint при cold-market split и оценке
числа независимых рынков. Любое агрегирование должно сохранять denominator попыток:
eligible, stale, unsupported, input gap, missing label, pending, actually scored.

AUTO primary — фактические eligible origins frozen source policy, traffic weighting.
Secondary — mint/pool-day macro, horizons, age/seen strata. Не расширять hot universe
молча и не выдавать его liquidity-selected selection за весь рынок.

Watch primary breadth — per-mint macro, отдельно age<1d/1–7d/>=7d и seen/unseen относительно
общего frozen reference train-mint union. Per-model novelty отдельно. До накопления настоящих
session logs recruitment cohort называется Watch-proxy, не user-representative traffic.
User-selected Watch traffic оценивается своим потоком; proxy и user не смешивать в один score.
Глобальный AUTO/Watch mixture coefficient не фиксируется без product usage evidence.

Рекрутировать markets ДО outcomes по frozen birth/discovery/venue/time policy, включая
неудачные admissions в denominator. Нельзя выбирать pools по будущим returns, достаточности
будущих свечей или performance candidate. Age и observable volume/volatility вычисляются
на origin; liquidity только с историческим available receipt, иначе unknown. Missing gap
не означает zero return/death. Frozen criteria допускают новую pool enrollment по мере birth,
но запрет добавлять/заменять market после просмотра его outcomes сохраняется.

## Роли и lifecycle

1. **Train.** Только admitted corpus, whose labels and scaler inputs available at fit cutoff.
   Явный reference objective по strata; sampler proposal и loss weights различать.
   Support-aware ограничения tiny pools исследуются отдельно, не принимаются16.64% по умолчанию.
2. **Development.** Новый old+new corpus по frozen selection policy, временно позже train
   и с отдельным mint-disjoint transfer track. Можно открывать для diagnostics/tuning;
   каждое открытие/attempt записывается в exposure log. После первого просмотра больше не
   называть независимым test. Предлагаемый collection window7UTCdays, без score-based extension.
3. **Calibration.** Отдельное следующее3day окно после фиксации recipe; допускается только
   зарегистрированный residual calibration fit. Не выбирать backbone/LR/sampler по нему.
   Model/selection labels и calibration labels не перекрываются. Проверять calibration
   по horizons и supported strata, не обещать individual-token conditional guarantee.
4. **Prospective.** Следующее7day окно ПОСЛЕ freeze модели и calibrator. Предсказания до
   maturity/target availability; publication receipt и model hash обязательны. Outcome labels
   закрыты для model selection до окончания fixed window. Если support недостаточен,
   verdict insufficient; новый window только новым protocol, без дописывания после score.

Возможны ретроспективные development данные с корректным actual availability для текущего
fit, но нельзя задним числом заявлять online inference. Для bounded fresh campaign конкретные
UTCwindows, pool roster/selection seed, request budget/spacing/expiry, source namespace и
existing reservations должны быть записаны ДО requests. Для cold-transfer они созданы в
campaign01; для7/3/7day temporal lifecycle ещё НЕ созданы. Активный managed exam не отдаётся
под development; production не возобновлялся и его source lease не продлевался.

Для всех границ: whole-decision targets matured; не менее полного15bar feature dependency
не пересекает предыдущую роль. Использовать actual per-row input_start/maturity, не только
origin cutoff. New mint track disjoint от всей train/scaler membership; связанныеpoolaliases
не дают unseen status. Train cutoff раньше startdevelopment, model freeze раньше evaluation.

## Метрики, минимальный support и выбор

Каждая модель получает одинаковые canonical origins/input hashes/target receipts/costs.
Общий paired subset публикуется вместе с individual missing/abstention rates; нельзя
выиграть метрику молча отказавшись от сложных markets. Existing `curriculum.future` уже
проверяет prospective timing и exact paired forecast joins; это основа source adapter,
не повод написать второй несовместимый evaluator. Raw verification остаётся обязательной.

Отчёт по seeds и ensemble отдельно: pinball, q10/q50/q90 signed coverage residuals,
80%interval width, tails, per-horizon/per-stratum, traffic/mint/pool-day weighting.
Публиковать learning curves, terminal/fixed checkpoints и registered selector result;
не выбирать checkpoint по final test. Cheap empirical и текущий official champion входят
как frozen baselines. Trading policy/costs одинаковые B07 assumptions; forecasts и paper
PnL разные endpoints, zero-trade exam не evidence profit. Execution qualification прежняя.

Initial support floors в config (heuristics, НЕ power calculation/CI guarantee):
- AUTO каждыйhorizon:>=4mints,>=7UTCdays,>=40nonoverlap dependency windows.
- Watch seen:anyage:>=5mints,>=3days,>=30windows наhorizon.
- Watch unseen: отдельно каждаяagebucket>=5mints,>=3days,>=30windows наhorizon.
Seen-young не требует7days: при frozen train young status естественно исчезает с возрастом;
это reporting slice, а не невыполнимый cross-product gate. Unknown metadata явно отдельно.
Calibration имеет отдельный day-floor cap3 (AUTO>=3days); development и prospective
сохраняют AUTO>=7days. Это phase-specific policy явно записана в config, не ослабление
существующих promotion gates. Трёхдневный calibration fit не независимая qualification.

Минимумы — нижняя граница reporting, не разрешение promotion. Paired uncertainty оценивать
по совместным временным/mint blocks с сохранением зависимости, не iid по свечам. Даже при
выполнении floors широкие CI или poor strata дают inconclusive. Устанавливать economic
margin/equivalence bounds до финального сравнения, не выводить их из observed winner.
Не принимать модель только за низкий pooled loss при плохом q50/q10 в важном stratum.

## Реализованная проверка и пределы

`src/memecoin_curriculum/benchmark.py`, `tools/brain_benchmark_readiness.py` — offline,
безTorch/provider calls/runtime mutation. Вход: config с refs на sealed metadata packets
`benchmark-input.json`. Packet содержит role, source lineage hashes, exposure attestations,
role window/cutoff, enrollment/model/calibrator freeze times, reference train-mint hash,
и canonical row metadata (без target returns). Schema показана в synthetic tests.

Проверяются seals, запрет known exposed/protected source roles, duplicate origins,
temporal role overlaps, full dependency windows, target maturity/availability,
backdated prospective predictions, age/seen identity и минимальный per-horizon support.
Nonoverlap windows считаются по mint, не по связанным pools; statisticalN не выдумывается.

Это **metadata preflight**, НЕ raw provenance/admission validator: packet producer должен
отдельно связать source receipts, complete enrollment denominator и frozen model artifacts,
проверить trade-cost identity и exposure history. Даже при support_ready=true результат
`admission_ready=false`, `raw_receipt_verification_required=true`, promotion=false.
Ни один runtime/promotion path новый checker не вызывает. Существующие gates не менялись.

Запуск: `PYTHONPATH=src .venv/bin/python tools/brain_benchmark_readiness.py`.
Сейчас: support_ready=false; missing development/calibration/prospective packets.
[Сохранённый readiness](evidence/brain-benchmark-readiness-2026-10-07.json).

Проверки:22focused benchmark tests и3agent-context tests PASS; итоговый `make check`:
1007passed/12optional skips (Mac2безTorch/browser), ruff/format/mypy PASS; `make smoke` PASS.
Первый full run имел transient operator stop-test failure (isolated и повторный full PASS)
и context-budget failure: now.md сокращена, подробная история сохранена в reports/state.
ML experiment и независимая inference verification выполнены в existing Torch env наMac1.

## Следующий опыт

Самый дешёвый causal diagnostic проведён отдельно: same checkpoint/optimizer/draws,
LR0.001 vs0.0001, дваsamplers ×seeds7/19, train-only, без evaluation scoring.
[Протокол/результат](brain-lr-diagnostic-2026-10-07.md). Он определяет направление, не winner.

Уточнение после clipping audit: следующий trial после populated development — прежний
GRU/sharedhead, factorial2x2 LRconstant/late-decay x current/rare-safetyclip, seeds7/19.
Один objective, одинаковые initial Adam states/draws/budget, fixed terminal/stability curves.
Это separate main effects/interaction, не сравнение двух recipes с конфаундированными
LR и clipping. Batch/backbone фиксированы; threshold выбирается train-only до dev scores.
Значение0.0001 не объявлено оптимальным;
до broad development данных никаких новых sweeps по старому test. После dev-selection
один frozen candidate проходит отдельные calibration и prospective stages противchampion.
