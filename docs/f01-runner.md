# D47: воспроизводимый runner frozen F01

## План до реализации и экспериментов — 2026-10-03

Источник правил: [D46](research-readiness.md) и неизменный
[F01-v1](../configs/research-f01-v1.json). Новых гипотез/thresholds/моделей нет.
D43, D45 registry и reserved holdout не изменять и не читать D43 raw.
Новый пакет `memecoin_runner` сохраняет source identities предыдущих этапов.

1. Immutable hash-chain trial/exposure ledger с single-writer lock, durable publication,
   строгими transitions, привязкой code/data/spec/environment, бюджетом failed/interrupted
   attempts. Checkpoints append-only; resume восстанавливает optimizer/RNG/early stopping.
2. Chronological adapter: whole-decision purge maturity/nominal horizon, embargo,
   partition digests; admission и holdout release — разные byte-bound решения.
   Numeric assessment/boolean не разрешает training. Engineering mode явно отделён.
3. Train-only empirical baseline/scaler/IQR; frozen AdamW/grid/seed policy,
   validation-only epoch/candidate selection, ensembles probabilities, fixed-epoch refit.
4. Paired scoring actions→decisions→pools→UTC days, отдельные masks, explicit missing
   support; block bootstrap2/4days,5000replicates,seed4601,97.5% CI, никаких skipped runs.
5. Fixtures: tamper/duplicate/crash/resume/budget/causal boundaries/selection/support/
   bootstrap/exposure-before-read. Engineering data — только уже sealed D45 development.
   Никакого настоящего F01, holdout release, RPC или service действий.
6. Offline reproduction, base/optional tests, build; evidence, Memory Bank, commits/push.

Стоп: integrity/NaN/unsupported operation — сохранить категорию и остановить опыт,
без replacement seeds. Engineering fixture failures исправляются с сохранением failed
logs; это conformance разработки, не скрытые research retries. Сбор F01 требует отдельного
будущего sampling binding и review, которых сейчас нет. План фиксируется commit до опытов.

## Реализация

`memecoin_runner` — отдельный пакет; D44 models/causal transforms переиспользуются,
D44 smoke trainer и исходники D43–D46 не меняются. Base scoring/contracts/ledger — stdlib;
trainer/worker/runner требуют существующий optional `work/ml-venv`. Dataset JSON пока
читается целиком, tensors создаются batch-wise; это не обещание out-of-core JSON loading.
Перед будущим admission нужны actual corpus RAM/compute feasibility checks.

Plan связывает frozen spec, dataset bytes, все Python sources четырёх предыдущих/нового
пакетов, Python/platform, absolute cutoffs и список trial IDs. Ledger дополнительно
связывает Torch и admission digest. Engineering recipe ограничена одним fold и≤5epochs;
её outputs всегда engineering_only. Research recipe восстанавливается из immutable
F01-v1, изменение grid/epochs/boundaries под прежним plan отвергается.

Full orchestration:108 validation fits, all-seed selection,3 selected outer evaluations,
final selection, median-epoch refit выбранного и linear ансамблей. При selected=linear
всего111 fits; duplicate slots не используются. Empirical fit3outer+1final refit. Outer
test не участвует в выборе, final holdout отделён от development JSON и открытия runner.
Whole-decision purge использует MAX maturity/nominal horizon; scaler только unique train
frames, IQR только observed train returns. Точное CPU epoch resume восстанавливает RNG,
AdamW, weights, best epoch/weights и early stopping. Loss/masks/ordering — frozen F01.

Каждый fit выполняется отдельным процессом. Parent hard timeout убивает worker по
оставшемуся fit/total budget; cold import тоже оплачивается. Время scoring/orchestration
учитывается как session overhead; общий wall budget проверяется на выходе из session,
не является hard OS kill для stdlib scoring. Late completion не считается успешной.
Interrupted не закрытая session консервативно расходует весь оставшийся global budget;
незакрытый fit — весь свой reservation. Это может сделать crash необратимым incomplete,
зато не создаёт free retries. Controlled suspend/resume epoch tested отдельно; автоматического
retry failed worker нет. После incident продолжение прежнего исследования закрыто.

Ledger — exclusive flock, immutable numbered events с previous/SHA chain; publication
через fsync+no-overwrite hardlink. Artifacts/checkpoints append-only, completion inventories
сверяются перед повторным использованием. Partial checkpoint после crash не «чинится»
молчаливым удалением. Owner filesystem может переписать всю историю: для обнаружения
suffix rollback нужен внешний pinned head. Это проверяемая provenance, не WORM storage.

## Admission и exposure

`run` требует отдельный receipt с exact externally pinned SHA для research mode.
Receipt перечисляет reviewer, decision/scope/time, binding/spec hashes, limitations,
все7 linked review evidence (file+hash). Обычный `reviewed=true` не является receipt.
Numeric per-fold support проверяется повторно. Sampling schedule/source/cohort/coverage,
usable/planned-window fractions, independence и corpus provenance должны быть независимо
проверены в linked dossier; runner не выдумывает эти evidence из counts готового JSON.
Receipt validator проверяет bytes/scope, **не подменяет содержательный review** и не
доказывает личность reviewer. Trusted SHA — процессная граница оператора, не подпись.
Ни настоящего receipt, ни readiness admission D47 не создаёт. Новый prospective
collector→development adapter требует отдельной квалификации на будущих sealed manifests.

Holdout release — другой pinned receipt: registered procedure/refit inventory,
loss/CI sources, exposure review/completeness, expected holdout bytes и procedure digest.
`evaluate_holdout` сначала durable exposure, потом даже hashing protected file. Сбой
после записи consumption не позволяет новой процедуре/повторному raw opening. Training
того же experiment после exposure запрещён. Унаследованный D45 holdout не входит в
новый F01 design; его bytes здесь вообще не открываются.

После успешного release `reproduce_released` пересчитывает сохранённые scoring inputs/
predictions и bootstrap по externally pinned ledger head, без reopening raw holdout.
Crash до сохранения полного report требует отдельного incident/exact-reproduction review;
автоматического escape hatch нет. Fixture receipts в unit tests не являются решениями
проекта и существуют только внутри temporary directories.

Scoring сохраняет pinball/CE/Brier/coverage, support и explicit missing heads. Missing
pool/head не удаляет неудобный день. CE floor1e-6 с renormalization; Brier — исходные
probabilities. Bootstrap jointly resamples selected/empirical/linear day losses, ratio
of means;2/4day blocks не пересекают gaps/folds, run<4days →inconclusive.5000replicates,
seed4601,97.5% percentile intervals. Outer outputs явно descriptive; final verdict требует
post-release support. Никаких claims edge/реальной fill probability.

## Воспроизведение и команды

```sh
make check
make runner-check
# Только NEW directories; не повторять поверх существующих outputs:
PYTHONPATH=src work/ml-venv/bin/python tools/f01_runner_smoke.py work/D47-fixture-NEW
PYTHONPATH=src work/ml-venv/bin/python tools/f01_runner_smoke.py work/D47-engineering-NEW \
  --engineering-dataset work/corpus-registry-01/revisions/000000/development/dataset.json
# Будущий research run требует independently reviewed plan/dataset и pinned admission:
# PYTHONPATH=src work/ml-venv/bin/python tools/f01_runner.py PROTOCOL PLAN DATASET NEW_RUN \
#   --admission REVIEWED_RECEIPT --admission-sha256 REVIEWED_SHA
```

Low-level fit/worker — внутренние компоненты; не альтернативный публичный способ обхода
admission. Library не является OS sandbox против владельца данных/произвольного Python.
При будущей интеграции запускать только через admission-aware runner. Torch/source/platform
updates требуют нового conformance/code binding до research, не continuation после scores.
