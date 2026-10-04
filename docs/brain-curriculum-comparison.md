# B12 — recent/full/balanced при одном экзамене

## Итог: история полезна на этом test, межрыночное разнообразие ещё недостаточно

**Созданы новые frozen модели и запущен отдельный future paper exam.** Основные B09
source/audit и B10 paper/train/UI сохранены; новые модели не добавлялись в training allowance
B10. Champion empirical не заменён. F01/D43/reserved holdout не использовались.

При одинаковой модели, validation/test и вычислительном бюджете full/balanced оказались
лучше recent по основному test loss. Это положительное exploratory evidence пользы более
длинной истории, **не доказанный edge и не устойчивый победитель**. Все arms обучались только
на5различных pools; balanced меняет их вес, не превращает5в24.

### Реальная учебная выборка и общий экзамен

Использован sealed B11 dataset, никаких новых provider queries. Общий train cutoff:
2026-10-01 22:15UTC; validation до2026-10-03 07:45UTC, test до2026-10-04 17:15UTC.
Train/validation/test whole-decision maturity purge; receipts и targets доступны≤B11as-of.
Test исторически позднее train, но был собран до нынешнего обучения: это **retrospective
future-in-event-time**, а не live future. Настоящий future exam начался отдельно после freeze.

| Выборка100% | Пригодных train decisions | Pools | UTC-days | Эффективное число pools по sampling weights |
|---|---:|---:|---:|---:|
| recent | 6 005 | 5 | 5 | 5.00 |
| full | 50 414 | 5 | 47 | 4.32 |
| balanced | 50 414 | 5 | 47 | 5.00 |

Full/balanced охватывают2026-08-16→2026-10-01; recent2026-09-27→2026-10-01. Один молодой
train pool имел5.16%массы full, в balanced —20%; pools с длинной историей также получают20%.
Затем внутри каждого pool поровну взвешиваются его UTC-days и decisions внутри дня.
Именно hierarchical pool/day weighting предотвращает доминирование длинного жизненного цикла.

Каждый fit получил64 000draws за1000updates. Фактически уникальных decisions:
recent6005; full36 250/36 281 по seed7/19; balanced33 585/33 579. Все fits действительно
увидели5pools; recent5days, остальные47days. 50 414 — доступная train support, не число
уникальных строк, обязательно просмотренных каждым SGD fit. Replacement используется во
всех arms одинаково; full не получил больше optimizer updates за счёт большего архива.

Общий validation2918decisions/10pools; test6027decisions/24pools/2UTC-days,
12054horizon variants. **19test pools отсутствуют в train.** Новые pools не переносили назад
через split и не выбирали более удобную границу после scores. Sampling/membership fractions
25/50/100 зафиксированы label-independent до fits, в каждом pool-day deterministic nested hash.

### Качество на одном и том же test

Основной показатель — средний pinball loss сначала внутри pool, затем между pools;
меньше лучше. 5/15min горизонты объединены; никакой подбор по test не выполнялся.

| Выборка100% | Empirical | Linear | GRU | GRU против recent GRU |
|---|---:|---:|---:|---:|
| recent | 0.0289014 | 0.0327059 | 0.0290466 | — |
| full | 0.0288195 | 0.0311739 | 0.0273579 | ошибка ниже5.81% |
| balanced | 0.0288719 | 0.0301046 | **0.0268664** | ошибка ниже7.51% |

Для linear улучшения4.68%/7.95%, но обе версии всё ещё хуже своих empirical baselines.
Empirical меняется лишь на0.28%/0.10%. Это не аргумент в пользу увеличения backbone:
эффект выборки заметнее у learned models, при прежних признаках/параметрах.

До test validation выбрал recent empirical, full GRU и balanced GRU. Их validation loss:
0.00902266 /0.00856263 /0.00851556. Прежняя frozen1%tie policy между curriculum оставляет
**full GRU**: он в пределах1%от balanced и не требует sampler weighting. Этот выбор записан
в ledger до test. Более низкий test balanced не использован для пересмотра selection.
Full GRU лучше recent-selected empirical на5.34%в этом test. Promotion champion не было.

Learning curves при одинаковых1000updates; таблица GRU (все27сочетаний сохранены в evidence):

| Fraction train support | recent | full | balanced |
|---|---:|---:|---:|
| 25% | 0.0274586 | 0.0263768 | 0.0263553 |
| 50% | 0.0269916 | 0.0274932 | 0.0269879 |
| 100% | 0.0290466 | 0.0273579 | 0.0268664 |

Рост не монотонный; лучший25%subset не становится новым выбранным кандидатом. Curves
показывают взаимодействие support/частоты повторений/optimization, не чистый независимый
эффект числа строк. Full vs recent одновременно меняют размер и возраст данных; этот опыт
не идентифицирует их причинные эффекты по отдельности. Balanced vs full использует одинаковые
строки/feature scaler, изолируя sampling/empirical weighting. Cardinality markets не выросла.

На5min GRU full/balanced относительно recent лучше11.16%/12.83%; на15min —3.30%/5.00%.
На seen и unseen pools направление выигрыша совпало, но regimes/волатильность различаются:
абсолютная ошибка unseen значительно выше и сама по себе не доказывает причинность diversity.
Tail calibration всё ещё плохая. Для GRU фактические coverages q10/q50/q90:
recent18.94/50.99/77.82%; full16.83/51.76/81.23%; balanced15.03/53.15/73.55% вместо10/50/90%.
Ниже средний loss balanced **не означает** лучше описанный верхний хвост. Данных для
устойчивой daily uncertainty мало:2testdays<5frozen minimum, CI намеренно `null`,
UTC-day bootstrap проверен fixtures, не выдан как значимый confidence band на двух днях.

### Что обучалось и что заморожено

36fits:3arms×3fractions×linear/GRU×seeds7/19. Width16/context8, прежние12features,
return quantiles5/15min, AdamW и gradient clipping прежние. Scaler train-only по уникальным
frames; full/balanced scaler одинаковый. Balanced меняет sampler и empirical quantiles.
Для всех arms empirical inverse-CDF estimator одинаковый; прежний B10 linear-interpolated
baseline не переписывался. Новых indicators/rules/auxiliary heads/targets нет.

Вместо разных по стоимости10epochs заранее выбраны1000updates×64decisions,10validation
checkpoints. Cached tensors проверены на совпадение с прежним encoder/inference. Один CPU
thread, deterministic PyTorch; вся bounded orchestration завершилась за183.66s, без retry,
36attempts completed. Это измерение данного run, не прогноз стоимости million-row trainer.
Все checkpoint weights/scalers/configs, membership/weights hashes и chronological boundaries
сохранены. Текущая материализация≤100kdecisions; streaming million-row loader не реализован,
он не требовался для этой гипотезы.

Frozen100%bundles (полныеSHA в evidence):

- recent `c9c40ce4…1803f`, selected empirical;
- full `7fcca7df…d4bf0`, selected GRU — основной development candidate;
- balanced `ccab396a…c5408`, selected GRU — заранее включённый comparator.

Все три публикуют empirical/linear/GRU, чтобы видеть поведение одной архитектуры при разных
данных; старый champion bundle также участвует в новом exam как контроль. Фактические
B10cash/positions не переносились и не сбрасывались. В B12 отдельные одинаково начатые
виртуальные books, поэтому cumulative PnL двух UI не смешивать.

### Future paper и сохранение продукта

B12 отдельный root `work/brain-b12/paper01`, frozen runtime
`72cfa4af21c81a4b20f010e8b35f8cc6ae08d1e698da273841db8220c316ae83`.
Новый [Control Center B12](http://127.0.0.1:8791); основной [B10](http://127.0.0.1:8790)
работает независимо. Labels `ai.memecoin.brain.b12.paper` и `.ui`; `.train` **не загружен**,
config дополнительно запрещает training(max_runs0). Никаких новых source requests.

Activation2026-10-04 18:29:01UTC(21:29МСК), expiry2026-10-05 18:28:50UTC(21:28МСК),≤24h.
Старые B09/B10expiry2026-10-07 15:23МСК, бюджеты и scheduled refresh не изменены.
Первый прежний B10daily fit всё ещё не раньше2026-10-05 13:41:54МСК+maturity/hourly scheduler.
Он не заменяет модели посреди B12exam.

Exam использует hot6 того же GT feed и прежние B07virtual costs/policy, 12books=4bundles×3families.
Model refs привязаны к SHA, finished≤activation; только event close и receipt послеactivation
допускаются к прогнозу. Никакой backdated exposure. Published forecasts precede target maturity;
новые outcomes сравниваются лишь после фактического поступления будущих bars. $100notional,
25bpsfee+50bpsslippage на сторону — допущения price-proxy paper, не измеренный execution.
Никакого реального execution/signing/paid или автоматического promotion.

Новый UI различает recent/full/balanced по именам, показывает forecasts, cash/rejection,
entries/exits и версии. Локальные API и JavaScript-render smoke проверены на реальном
snapshot с12books. Визуальный browser review **не проведён**: Browser runtime сообщил
`No browser is available`, discovery вернул `[]`; это ограничение среды, не сбой Brain.

### Фактический старт live exam

На cutoff **2026-10-04 18:36:40UTC**:72опубликованных forecasts,18общих observation groups
с точным input-frame hash для всех4bundles;16scored outcomes(4общие зрелые группы),56pending.
Первый прогноз18:30:33UTC.108виртуальных решений в12books — всеcash(`median_below_cost_buffer`),
входов/выходов0. Это фактическое поведение слабого первого кандидата, не обещание доходности.

Независимо воспроизведены7ticks,168raw market joins,36published15min forecast joins и все16
outcomes. Raw audit1544captures/73 459bars exact; новые bars source продолжил собирать во
время исследования. B10/B12/source healthy, B10по-прежнему1training reservation. Source
сохранил7transport failures; последний17:43UTC(доB12):`URLError / TimeoutError / status=null`.
Это не429/quota failure. B12worker11completed,max22.47s≤120s,не доказанный day-scale SLA.
Evidence root`work/brain-b12/exam-evidence01`, manifest
`9079c444969396d8ab9a2ecc48f6fb38c4c25826ef475109b317d58a216ecbab`.

### Evidence, проверки и следующий шаг

[Training/learning curves/scoring evidence](evidence/brain-curriculum-comparison-2026-10-04.json).
[Реальные future exposures и replay](evidence/brain-curriculum-exam-2026-10-04.json).
Локально: `work/brain-b12/design01`, `run01`, `exam-evidence01`; raw/weights внеGit.
Frozen plan commit68946a5; trainer057d19a; exam8999af1. Failed real fits0, новыхproviderqueries0.
36trial starts/ends и selection-before-test проверены; независимый rescoring совпал,
все12054variants у6neural ensembles100%точно воспроизведены из frozen weights без fit.
Полные check:650basePASS/7optional skips,677combinedMLPASS; затем4focusedPASS для
calendar-bootstrap/support gate и frozen exam guards. Smoke/build успешны. Новые guards
проверяют stale model pin, backdated exam, запрет retraining и доактивационные observations.

**Следующий B13:** сохранить B12future exam и проверить matured paired behavior, затем
увеличить именно train market/cohort diversity. Новый исторический учебник2025–2026 имеет
смысл, но ещё год тех же5pools не закрывает19unseen markets. Сначала квалифицированные
недостающие возрастные/рыночные cohort, representation gaps и tail errors; raw decoder/birth
cohorts — путь к более старым/умершим рынкам, не повод остановить paper. Не менять backbone
и не подключать новые targets одновременно с data curriculum. Перед следующим deployment
отдельно зафиксировать refit/future-exam protocol, чтобы recent young data могла входить в
последующее обучение без превращения уже прочитанного test в новое независимое evidence.
Главный bottleneck остаётся dataset diversity и режимы; single-provider freshness остаётся
главным operational риском. Новая модель уже существует и проходит настоящий будущий exam.

---

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
