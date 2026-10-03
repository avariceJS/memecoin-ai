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
Rare status может отсутствовать структурно для некоторой action; тогда зарегистрированный
joint F01 не проходит gate даже при большом числе обычных rows. Это повод признать
этот дизайн infeasible и отдельно preregister иной estimand на новых данных, а не
бесконечно ждать класс, балансировать synthetic labels или снижать floor задним числом.
Все cutoffs/labels mature, exact hashes и temporal guards обязательны; counts scenarios
не заменяют decisions. Nonoverlap не означает independence. Минимумы — ограничения
дизайна до новых данных, не доказанная power и не разрешение обучаться автоматически.

Проверяем scope/universe, target semantics, dependence/precision plan, conformance runner,
exposure ledger и formal admission. Пока этих evidence нет, launch=false. Полноценный
runner ещё не реализован: D44 ограничен5epoch smoke и другими loss/selection semantics.
D46 строит executable planning/assessment/selection contracts; не обходит старый gate.

## Experiment/model-selection protocol

Empirical baseline обязателен: action-conditioned train quantiles и Laplace status
counts, fallback только pooled train с явным support report. Trainable grid: linear16;
MLP/GRU/TCN/Transformer widths16/64, context8, те же12 features/masks/age и action grid.
Это набор для F01, не выбранный backbone или размер проекта навсегда. Scaling frequency,
horizon, context, новые признаки, reweighting классов и pretrained models не входят в
этот budget; добавление потребует нового опыта, а не незарегистрированного extra trial.

Каждый candidate обучается на seeds7/19/41. Train-only transforms и pooled observed
return IQR с floor1e-4; ни IDs, абсолютное время, будущий status/mask, ни future scaler
не становятся features. Loss=masked pinball/IQR +masked class cross-entropy; каждый head
усредняется только по наблюдаемым labels. Fully censored batch не обновляет optimizer.
AdamW lr0.001,weight_decay0.0001,batch64,clip1; максимум100epoch,patience10. Best epoch —
самая ранняя эпоха с минимальным validation score. CPU/threads1 для первого опыта;
смена device/compute recipe не скрытая причина дополнительных trials.

Для каждого из3 folds:27 fits=9 candidates×3seeds. Candidate score — средний validation
joint proper loss всех3seeds. Из кандидатов в пределах1% лучшего выбирается минимальный
заранее записанный priority:linear,затем четыре width16,затем четыре width64 в порядке
MLP/GRU/TCN/Transformer. При best=0 допуск0. Это simplicity policy, не оценка рыночного
качества по числу параметров. Missing/failed seed не исключается из среднего: selection
незавершён. Никакой выбор lucky seed. Предсказание ансамбля — среднее ordered quantiles
и среднее probabilities, не среднее logits.

Outer test оценивает **процедуру validation selection**, а не служит новым tuning set.
На нём фиксированная выбранная процедура и empirical/linear comparators; нельзя
перебирать все кандидаты по outer score. Между folds grid/hyperparameters/criteria
не меняются. Final selection повторяет27fits на train0–49/val49–56; final refit выбранного
и linear ансамблей3+3 fits на0–56. Число epochs каждого refit — медиана best validation
epochs его трёх seeds, затем фиксировано; scaler и IQR refit только на0–56. Если выбран
linear, redundant refit slots не используются для новых моделей. Всего≤114trainable fits
и4deterministic empirical fits. Resume той же trial identity не сбрасывает budget/time.

Каждый fit≤600s, весь опыт≤86400s compute/wall accounting. Учитывать failed/interrupted
attempts. Stop при integrity/NaN/неподдержанной операции; сохранять ошибку и checkpoint,
не подбирать новый seed. Допустимый resume — тот же code/data/config/RNG/optimizer;
исправление численной реализации после результатов требует incident/version review,
не continuation под прежним ID. Превышение бюджета → incomplete, не победа быстрого
оставшегося кандидата. Неиспользованный бюджет не становится правом на extra tuning.

## Scoring, uncertainty и критерий результата

Одна paired единица — UTC-day, внутри которого сохраняются все pools/actions/decisions.
Для каждого head сначала masked mean по actions решения, затем по eligible decisions
pool/day, затем равновесно по pools и дням. Отсутствующий return label не zero return;
return head conditional on observed roundtrip. Paired support одинаков для каждого
comparator/candidate. День без поддержки необходимого head/cohort не молча исчезает:
фиксируется missing; post-release support gate может сделать результат inconclusive.
Class CE использует probability floor1e-6 с перенормировкой probabilities перед loss.
Normalize return loss IQR соответствующего train/refit, не empirical test performance.
Joint score — сумма двух head scores; Brier/coverage и каждый component сохраняются отдельно.

Primary относительный выигрыш=1−mean(loss_selected)/mean(loss_baseline), одинаковая
агрегация для empirical и linear. Если знаменатель≤1e-12, improvement не определён,
результат inconclusive; никаких eps-derived «побед». Bootstrap paired day losses,
5000replicates,seed4601; основной block length2 календарных дня, sensitivity4. Все pools,
scenarios и comparators одного дня ресемплируются вместе, seeds не statistical replicates.
Не объединять блоками разные folds/пропущенные календарные даты. Внутри каждого непрерывного
run выбирать contiguous blocks с возвращением до исходной длины run, последнюю выборку
обрезать; одинаковые индексы у всех comparators. Если run короче4 дней, зависимость таким
дизайном не проверена: **inconclusive**, не выкидывание коротких runs ради красивого CI.

Outer folds дают отдельные descriptive reports; final confirmatory inference — только
новый holdout56–84. Minimum24 observed days плюс те же predeclared action/censoring
criteria; выяснять label support holdout до formal release нельзя. Два97.5% percentile
CI предназначены для family alpha0.05 против2comparators при валидности block intervals;
это не гарантия finite-sample coverage на нестационарном рынке. Отдельный dependence review
обязателен до admission, а block4 sensitivity должна подтвердить направление/precision.

Forecast improvement разрешено назвать лишь если для **обоих** comparators при обоих
block lengths lower bound>2%, CI half-width≤2%, class Brier deterioration≤0.01,
class log-loss deterioration≤0.02 и absolute quantile-coverage errors≤0.05. Noninferiority
guards применяются по худшему comparator; coverage — по всем3levels. Нет cherry-pick
horizon/action/head. Если условий недостаточно — inconclusive/no improvement с полным
отчётом, без adaptive extension и ретюнинга по тому же holdout. Even success — только
conditional forecast skill на этой когорте, не net-edge, production promotion или RL gate.

Числа2%/2%/support floors — заранее принятая практическая строгость первого исследования;
это не оценённая power и не пользовательский финансовый risk budget. Dependence, rare
class support и out-of-scope regimes могут оставить вывод неопределённым и после84 дней.

## Admission, release и amendments

1. До нового сбора materialize future T и отдельный sampling plan с exact source/cohort/
   schedule/budget/failure policy. Boundaries immutable; D43 ничего не переустанавливает.
2. D47 runner/registry adapter должен пройти conformance на fixtures: masks/weighting,
   global purge, all-seed selection, byte-bound data/config/code, deterministic resume,
   consumed compute budget, immutable trial reservations/statuses и exposure ledger.
3. По sealed development revision сформировать dossier всех numeric checks и linked
   reviews: universe/scope, conditional semantics, dependence, runner, exposure. Boolean
   «reviewed» сам по себе не свидетельство; reviewer decision обязан перечислить hashes,
   область допуска и нерешённые ограничения. D46 выдаёт assessment, **не permission token**.
4. Отдельное зафиксированное forecast admission разрешает только frozen F01 training,
   не economics/paper/live. Текущий D44 gate не изменён. Holdout release — другое решение:
   после регистрации selected/refit artifacts, loss/CI code hashes, evidence completeness
   и exposure review. Release однократный для одной окончательной процедуры/comparators.
5. Сбой после чтения holdout означает consumption/exposure, даже если report не записан.
   Повтор запуска допустим лишь для exact reproduction тех же predictions/metrics;
   выбирать новую модель после failure/reveal нельзя. Изменение hypotheses/thresholds,
   calendar/model grid/weighting/aggregation — новая версия с причиной, полным log уже
   увиденного и новой future evaluation; старые bytes и отрицательные результаты сохранить.

D45 reserved holdout4/5 остаётся нераскрытым и не заменяет новый F01 holdout. Никакой API
D46 его не освобождает. Инструмент не может обнаружить внешнее чтение файлов владельцем;
поэтому не заявляем криптографическую или globally-unseen гарантию.

## Реализованные команды и границы

Новый stdlib пакет `memecoin_research`, исходники D43/D44/D45 не меняются.
`work/research-d46/protocol` — immutable spec/source binding; другие outputs только новые.

```sh
# Уже выполнено; не перезаписывать existing protocol:
# .venv/bin/python tools/research_readiness.py freeze \
#   configs/research-f01-v1.json work/research-d46/protocol
.venv/bin/python tools/research_readiness.py plan work/research-d46/protocol work/F01-plan-NEW.json
.venv/bin/python tools/research_readiness.py assess work/research-d46/protocol \
  work/F01-assessment-NEW --registry work/corpus-registry-01 --revision 0
# materialize --anchor-ns FUTURE_UTC_MIDNIGHT_NS создаёт только design binding,
# не активирует collector/новый sampling budget.
```

`select --phase fold0 --results VALIDATION.json` принимает полный набор27 rows со строгими
полями trial_id/partition/status/score/best_epoch; неизвестные поля, test role, NaN,
duplicate/missing/failed trials отклоняются. Trial ID=phase:candidate:seed. Это проверка
функции выбора, не runner/ledger или доказательство provenance входного JSON. Эти
связи обязан реализовать D47 до admission. `result_verdict` проверяет уже верифицированные
summary intervals; сам bootstrap/prediction scorer ещё не реализован. Никаких вызовов
training/holdout release в CLI нет. Spec content fingerprint запрещает менять пороги
под прежним F01-v1; новой версии потребуется явное обновление контракта и review.

## Фактический результат D46

[Evidence](evidence/research-readiness-2026-10-03.json). Preregistration58c66da,
code2fbd09d. Frozen protocol и114slot plan созданы; реальные F01 fits0, holdout releases0.
Assessment только по уже опубликованной D45 revision000000: design_checks_passed=false,
82 детализированных blockers (включая отсутствующие будущие folds/reviews), launch=false.
Число blockers не мера расстояния до readiness: некоторые относятся к одной общей
причине — текущий finite engineering corpus не является будущим F01 sampling plan.

15 новых tests: spec tamper/overwrite, future anchor/holdout exclusion, exact budget,
all-seed mean/tie policy, missing/duplicate/failed/unknown trials, test-field rejection,
NaN guard, scenario-vs-decision/day counts, positive-design closed gate, CI/precision/
calibration verdict.434 base и448 combined tests,ruff/strict mypy/smoke/build прошли.
Exact installed-wheel assessment; D44/D45 source identity сохранена. Runtime audit-hook
запрещал открывать D43 paths и подтвердил0 таких обращений при assessment; содержимое
reserved holdout не читалось. D43 sync/install/collector/transport действия не выполнялись.

Внешний web lookup справочных страниц завис и был остановлен без retries, причина
unknown; выводов по этим страницам не сделано. Критерии — явно собственный frozen
research design, не внешняя гарантия power. Исправлена одна E501 formatting ошибка,
первый failed check log сохранён. Remote CI здесь не проверен.

Решение: specification/readiness planning приняты, research gate остаётся закрыт.
Следующий D47 — conformance будущего admitted fold-aware runner: immutable trial/exposure
ledger, exact scoring/masks/weighting, paired block inference, budgets/resume, отдельные
admission/release receipts. Разрабатывать и проверять на fixtures, не снимая gate и не
раскрывая D45 holdout. Новый prospective sampling binding потребуется отдельно; он не
создаётся автоматически в D46 и не меняет действующий D43.


Обновление D47: описанный выше gap runner/scoring/ledger реализован и проверен на
engineering fixtures/data; historical D46 results остаются неизменны. Содержание F01-v1
не изменялось. Sampling/source adapter и actual admission пока отсутствуют.
[Текущее состояние D47](f01-runner.md).
