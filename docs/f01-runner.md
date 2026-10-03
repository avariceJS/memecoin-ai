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
