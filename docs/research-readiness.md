# D46: F01 research specification и readiness framework

2026-10-03. Пользователь разрешил самостоятельно определить критерии и protocol,
не меняя D43 и не раскрывая reserved holdout. [Frozen specification](../configs/research-f01-v1.json).
Результат исследования F01: **не проведён**. Нового research training/holdout release нет.

## Решение до реализации проверок

F01 — первое ограниченное исследование conditional forecast skill на фиксированной
наблюдаемой когорте CPMM. Проверяем добавочную прогнозную информацию causal history
относительно empirical/linear comparators. Это ещё не E01 net-edge и не trading policy.
Target — существующий independent_future_snapshot_reset_v1,20s holding, delays0/5s,
направления0/1,size100/1000ppm, fees только pool/transfer. Unknown network costs и own
inclusion остаются unknown. Status head прогнозирует conditional snapshot outcome,
не вероятность реального исполнения заявки. Censorship selection не предполагается MAR.
Scope — только заранее закреплённые3 pools и наблюдаемые окна; перенос на memecoin
universe/new tokens не заявляется. Pool dropout/unsupported states не заменять новыми
успешными pools. Нельзя удалять failed/unavailable periods или выбирать когорту по returns.

Разделяем готовность conditional forecasting и economic/strategy qualification.
Для F01 необходима проверенная семантика условных labels, не выдуманная калибровка
собственных сделок. Для net-edge по-прежнему нужны полные costs/admission/risk/capital,
policy/MPC и независимый execution-aware OOS. Forecast readiness не снимает этих gates.
Это уточнение прежнего общего blocker D45, а не признание текущего corpus достаточным.

## Календарь и статус существующих данных

D43/D45 остаются engineering pilot со своими неизменными folds и holdout4/5. Их
максимальные4 development окна не могут удовлетворить F01. Не расширять их задним
числом и не превращать reserved holdout в train. D46 не читает D43 raw/holdout: assessment
использует только уже опубликованную D45 development revision и её pinned objects.

F01 требует **нового** prospective binding: T — будущая UTC-полночь после конца
унаследованного holdout. До любого нового сбора фиксируются exact T/cutoffs, cohort,
sampling schedule/RPC budget/source/code hashes, failure/missing policy. Это дизайн,
не установка нового collector, расходование нового RPC budget или обещание84 дней данных.
Три folds (days from T): train0–28/val28–35/test35–42; train0–35/val35–42/test42–49;
train0–42/val42–49/test49–56. Final selection train0–49/val49–56, refit0–56, новый
holdout56–84. Embargo30s; purge all siblings по MAX maturity и nominal horizon end,
не пересекать segment/gap histories. T не выбирается по видимым future outcomes.

Это одна заранее выбранная консервативная стартовая длина, не универсальная потребность
нейросетей в84 днях. Недостаток coverage/support после planned end → inconclusive;
нельзя автоматически продлевать до положительного результата. Новый план — новая версия
с объяснением и fresh evaluation. D45 cutoffs не сдвигаются при материализации F01.

## Readiness до training: необходимые, но не достаточные условия

Для каждого fold и final selection train:≥24 UTC days,3000 decisions,300 greedy
nonoverlap; validation/outer test:≥6 days,500 decisions,50 nonoverlap. Для каждой action
и каждого из3 observed statuses: train≥30,validation/test≥10. Censored≤35% по action;
usable≥90% всех starts, completed≥80% заранее запланированных окон. Четыре UTC6h strata
должны присутствовать каждый минимум в75% observed days; доля каждого pool≥15%.
Все cutoffs/labels mature, exact hashes и temporal guards обязательны; counts scenarios
не заменяют decisions. Nonoverlap не означает independence. Минимумы — ограничения
дизайна до новых данных, не доказанная power и не разрешение обучаться автоматически.

Проверяем scope/universe, target semantics, dependence/precision plan, conformance runner,
exposure ledger и formal admission. Пока этих evidence нет, launch=false. Полноценный
runner ещё не реализован: D44 ограничен5epoch smoke и другими loss/selection semantics.
D46 строит executable planning/assessment/selection contracts; не обходит старый gate.
