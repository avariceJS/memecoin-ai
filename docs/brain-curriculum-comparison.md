# B12 — recent/full/balanced при одном экзамене

## Frozen plan до fits и просмотра test scores, 2026-10-04

Использовать неизменный B11 `work/brain-b11/textbook02/dataset-full`, manifest и membership
связать в отдельном sealed design. Новых provider queries нет. B09/B10 runtime, source,
training allowance, champion, ledger и расписание не менять. F01/D43/reserved holdout не читать.
Архивный test — retrospective event-time development, не новый live market и не confirmatory.

Общие границы из B11 rolling view, UTC: recent train start2026-09-27T17:55:00;
train end2026-10-01T22:15:00; validation end2026-10-03T07:45:00;
test end2026-10-04T17:15:00. Membership требует decision<target maturity<partition end,
полные5/15min labels, фактическую input/target availability≤B11as-of. Никаких random folds.
Validation/test и их веса одинаковы для всех arms, training_end не сдвигать ради новых pools.

Три arms: recent — все train decisions после recent start; full — весь past до общего
train end; balanced — те же full decisions, hierarchical sampling: равный вес pools,
затем UTC-days внутри pool, затем decisions внутри pool-day. Это уточнение B11 равного
pool×day: плоский вес групп всё ещё давал бы больший вес pool с длинной историей.
Балансировка не увеличивает фактическое число различных рынков; показать counts и weights.
Fractions25/50/100 nested по deterministic hash(seed7,pool,day,id) внутри pool-day,
ceil и минимум1 на группу. Никакой фильтрации по будущему return или test outcome.

Empirical quantiles и linear/GRU width16/context8,seeds7/19; прежние12features и два горизонта.
Оптимизатор AdamW lr0.001/wd0.0001, gradient clip1; CPU,1thread, deterministic. Scaler прежний,
train-only на уникальных feature frames соответствующего arm/fraction; у full/balanced
одинаковый scaler. Target IQR unweighted train-only, min1e-4. Balanced влияет на sampling и
empirical quantiles, не добавляет feature/target. Все arms используют empirical inverse-CDF
quantiles (uniform weights для recent/full), без различия estimator между arms.

**Matched compute вместо прежних10epochs:**1000updates×64decisions с replacement для каждого
из36neural fits (3arms×3fractions×2families×2seeds).10validation checkpoints через100updates,
выбор epoch только по validation pool-macro pinball. В B11 max10epochs был предложением;
фиксированное число updates устраняет скрытое преимущество full от большего числа батчей.
Fractions измеряют пользу поддержки при одинаковом draw budget; не естественный cost роста.
Max180s/fit, весь stage7200s, no retry на failed real fit, partial runs не кандидаты. Новых
hyperparameter trials нет. Tensor minibatches переиспользуют прежний encoder; cached CPU
training inputs ограничены текущим≤100kdecision adapter, не заявлять million-row scaling.

Для каждого arm100% выбрать family по validation: empirical→linear→GRU preference within1%.
Общий exploratory selection также только100%, minimum validation с simplicity tie1%, затем
recent→full→balanced. Fractions — diagnostics, не выбор лучшего luckysubset. Selection записать
в immutable ledger **до** любых test scores. Все arms/families/fractions показать на одном
test, включая проигравших. Основной показатель pool-macro pinball; micro, per-horizon,
calibration и tail/часовые срезы дополнительно. Paired UTC-day cluster bootstrap500draws,
seed7; <5testdays → CI не публиковать как надёжный, вывести support и descriptive deltas.
Не выбирать champion и не менять policy по результатам test.

После successful full run: sealed совместимые model bundles для всех3arms100%, fixed weights,
scalers, dataset/source/code/recipe/membership hashes. Сохранить прежний empirical champion.
Отдельный B12 read-only-source paper exam может получать те же hot6observations для трёхarms
и старого champion, на независимых virtual books с теми же B07costs/limits. Не добавлять
training runs в B10 и не менять его кандидатов/allowance. Новый root и localhost UI, без
provider requests; train job не запускать. Exam после ready/activation, только новые закрытия
свечей; модели неизменны весь exam, outcomes только после maturity. Expiry≤24h и≤B09expiry,
CPUworker≤120s, existing fail-closed/recovery bounds; никакого real execution/signing/paid.
Activation после fixture/live isolation checks; если ресурсы/health не проходят — staged,
не ухудшать B09/B10 ради нового UI. Новая сторона расходует CPU/disk, не provider quota.

Успех этапа — честный ответ о data value, воспроизводимый frozen candidate и его future
exposure (или конкретный безопасный blocker), а не обязательное преимущество balanced/GRU.
Raw2025semantic work и auxiliary heads отложены до результата этого сравнения.
