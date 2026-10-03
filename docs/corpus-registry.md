# D45: immutable registry, qualification и global folds

2026-10-03. Независимый offline поток рядом с активным frozen D43. D43 не менять,
не переустанавливать и не вызывать collector/RPC. D44 training gate не открывать.

## План до реализации/опытов

Stdlib пакет memecoin_registry отдельно от D43 и memecoin_ml (сохраняем source identity
старых checkpoints). Registry связывается с одной immutable calendar plan/cohort/target
semantics. Только завершённые sealed windows с matching outcome, полным file inventory,
без symlinks/path escape и с повторной semantic verification могут попасть в development.
Failed/missed/pending/unsealed сохраняются в inventory и не увеличивают corpus.
Повтор окна — idempotent; изменённый seal/outcome и пересечение decision identity — отказ.
До и после копирования/верификации сверять seal; исходные availability не менять.

Один writer lock, staging → atomic publish; immutable objects и цепочка revisions с
parent hash. Сбой оставляет evidence и не публикует частичную revision; no retry loop.
Следующий sync может безопасно продолжить; прежние versions/outputs не перезаписываются.
Каждая revision содержит полный inventory, development bundle, fold membership и report.
Registry import time отдельно от source online availability; это offline historical
разбиение, не заявление о том, что модель была обучена/доступна в прошлом.

Frozen absolute cutoffs до новых окон. D43 plan: fold0 train window0, validation1,
outer test2; fold1 expanding train0/1, validation2, outer test3. Границы — due timestamps,
embargo30s после train/validation cutoff; maturity purge MAX всех scenario siblings плюс
номинальный конец горизонта. История остаётся внутри source window/segment/pool/gap.
Окна4/5 зарезервированы как будущий holdout до чтения их outcomes; start должен быть
позже создания protocol. Любое окно, пересекающее holdout start по reservation/finish,
целиком quarantine; после конца holdout тоже не добавлять в development автоматически.
D44/window0 явно exposed development. Смещать cutoffs по support/returns нельзя.

Holdout admission проверяет hash inventory/outcome/identity; semantic label verification
отложена до отдельного release stage. Никаких labels/returns/class counts holdout в
обычных reports/exports; release API отсутствует. Это процессный барьер в инструментах,
не криптографическая изоляция от владельца raw-файлов. Holdout не объявляется globally
unseen, если кто-либо читал его через другой инструмент.

Qualification раздельно: integrity, actual UTC hours/days/window/segment coverage,
unique decisions/pools, masks/censorship, complete scenario grid, greedy temporal
nonoverlap (не independent sample count), поддержка каждого action/status в folds,
планируемые и фактические missing windows. Counts не являются доказательством
статистической достаточности. Engineering floors заранее: ≥3 development windows,
≥2 UTC days,≥3 UTC hours; для каждой train/validation/test partition ≥20 decisions,
≥3 nonoverlap groups и ≥3 labels каждого status, в том числе по каждой action.
Эти диагностические floors не включают readiness. Universe/regimes, execution costs/
admission, research specification и formal readiness decision остаются unresolved;
research_training_ready всегда false, даже на богатом synthetic fixture.

Тесты: staged multi-window append/restart/idempotency, corruption/conflicting overlap,
late/seal race, malformed paths, future availability, whole-decision purge/embargo,
fixed memberships после новых окон, holdout non-disclosure/exclusion, false readiness
при всех numeric floors. Реальный sync только существующих sealed окон; offline repeat
и wheel reproduction; никаких training runs этого этапа. Seed null. Результат пока не получен.
