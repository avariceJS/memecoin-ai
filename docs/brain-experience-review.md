# Первые автономные сутки: forensic review и выбор следующего этапа

Дата: 2026-10-06 Europe/Moscow. Исходный Git `6b445b8`.
Срез сравнения: 2026-10-05 21:32:01.853405 UTC. Исторические outputs не переписаны.
Полные воспроизводимые артефакты: `work/brain-review-20261006/experience01.json`,
`paired01.json`; команды — `tools/brain_experience_audit.py`, `brain_refit_compare.py`.

## Что установлено до нового acquisition / изменения gate

Реальный AUTO выполнил snapshot `job_4d92ab8caea50925`, train
`job_ef9cb49cef6d7157` и future exam `job_3d405d04fd61c744`. Origin всех трёх — auto.
Первый цикл был manual/E2E. Второй не является fixture. Работает AUTO/waiting,
но непрерывная автономность потребовала ручного исправления 5 октября.

`6b445b886814449f5bc59d07f4d40b24791d9dd9` исправил `.DS_Store` и распространение exam
STOP на operator. Прямые worker logs показывают ту же ошибку также в B10-repaired02
и B12-repaired02 около 06:20:43–46 UTC 5 октября. Эти старые workers остались stopped;
B10 next daily train не состоялся. Новый operator runtime06 не переписывал их runtime.
Следовательно, source healthy не означает healthy paper во всех независимых контурах.
Первый B13 exam накопил примерно 9 часов работы, затем STOP; это не полный 24h exam.
Журнал имеет owner/UI Resume, затем повторный domain_stop и Codex Resume runtime06;
UI request сам по себе не удостоверяет личность нажавшего кнопку.

| Показатель | OLD B13 | NEW automatic B13 |
|---|---:|---:|
| Train pools / UTC days | 24 / 92 | 24 / 93 |
| Train до cap / после cap | 86619 / 48000 | 89504 / 48000 |
| Validation decisions / pools | 451 / 14 | 264 / 8 |
| Descriptive test decisions / pools | 352 / 14 | 252 / 8 |
| Выбранная семья | GRU16, seeds7/19 | GRU16, seeds7/19 |
| Validation pool macro loss | 0.0184998 | 0.0109419 |
| Собственный test pool macro loss | 0.0698125 | 0.0100774 |

Последние две строки НЕ paired comparison: периоды и рынки различаются.
На одном NEW test (252 decisions / 8 pools) GRU OLD→NEW:
5m pool macro 0.00732717→0.00703543 (−3.98% ошибки),
15m 0.01240363→0.01311930 (+5.77% ошибки).
5m q10/50/90 coverage: 4.76/20.63/93.65%→7.14/64.68/95.63%;
15m:19.05/34.52/93.25%→23.02/73.02/96.03%. Калибровка не установлена.
NEW future имеет слишком мало support на срезе. OLD replay на тех же сохранённых inputs
маркирован offline: его прогнозы не были опубликованы тогда. Promotion нет.

Snapshot maturity 87422→90020: +2598 новых eligible decisions, 15 уже известных pools,
effective pools9.60, largest share11.43%, 26 pool-days, только11 новых pool-days.
Все новые решения относятся к знакомым pools, 2273/2598 к знакомым coarse regime cells;
это не доказательство бесполезности. Exact feature duplicates0. Соседние 5m decisions
97.81%; только264 неперекрывающихся context+target windows (это тоже НЕ statistical ESS).
Train membership +2885 включает перенос прежних evaluation/purged rows при сдвиге cuts;
не считать весь прирост новым collected experience. Ни одного нового рынка в train.
Changed candle keys863→1861, changed close keys67→121; ambiguous captures9→16.
На первичном health-срезе 13/24 pools quarantined. Weighted effective pools24 у sampler
не равно реальной row concentration: archive effective pools4.48→4.62.

Операторский отчёт показывает WAIT и ENTER intents, но fills0, realized0, fees0 для B13
книг. Сумма equity нескольких независимых книг не один портфель. Legacy книги имеют
свою унаследованную историю. Не создавать trades для демонстрации активности.

## Окончательный статус D43 48h

План `6ec6bad`, collector `ed88a9f`, `work/calendar-execution-01/plan.json`:
6 окон на 0/6/12/24/36/48h. Реально outcome0 completed, outcome1 failed после полного
corpus (RPC error последней execution hydration), outcome2 failed на checkpoint validation.
`closed.json`: failed window budget. Последующие 3 окна не запускались из-за closure;
это не sleep-missed окна и не шесть completed. Reserved holdout4/5 не читался.

Accepted snapshots180+180+13=373; usable feature rows174+174+11=359;
mature decisions135+134+0=269; engineering evaluation67+68=135;
target variants1440+1440+120=3000, не независимые examples.
Три начатых окна стартовали примерно через16.1/23.8/17.5s после due; launchd выполнил
расписание до stop. Sleep/network outage как причина closure не доказаны. В третьем окне
HTTP200/14attempts, transport успешен, semantic checkpoint rejected. QA temporal/accounting
checks passed для всех трёх development окон; это воспроизведённый отчёт, не новая
полная raw causal requalification. Максимальные receipt ages2.41/2.88/3.63s.
Всего около602.5s суммарных segment spans за один UTC день и три часа суток.

Эксперимент доказал bounded scheduling/failure stop, получение coherent данных в двух
окнах и сохранение partial failures; не доказал 48h source uptime, broad corpus, full
execution costs/inclusion, ML readiness или edge. Его достаточно считать закрытым evidence;
не переустанавливать и не продлевать. F01 84-day design остаётся отдельным.
B09–B13 fault isolation, immutable repair и source/paper separation согласуются с этим
уроком; прямые причины их конкретных исправлений — собственные B09–B13 incidents,
а не выдуманная причинная связь с завершением D43.

## Выбор направления

Новая архитектура/больше GRU compute имеют низкий ожидаемый эффект: train всё ещё24pools,
за сутки появилось0новых pools. Ускорить прежний timer без расширения источников даст
больше fits на коррелированном опыте. Приоритет: program/indexed historical acquisition,
birth cohort discovery, versioned protocol admission, canonical store и streaming dataset.
Затем измеряемый diversity trigger; richer policy — отдельная ablation после data path.
Существующий AUTO продолжает жить. Cutover только после qualification новой дорожки.

Технический incident самого audit: SQLite mode=ro создал пустые WAL/SHM у двух sealed
snapshots. Все исходные manifest hashes совпали; удалены только эти новые ancillary files,
seals повторно verified. Audit использует immutable=1 только для sealed backups, никогда
для live WAL. Это не изменение данных и не удаление прежнего incident.

## Дополнительный forensic срез и точные оговорки

`experience02.json` уточняет genuinely-new train:2082решения,15pools,effective9.98,
largest11.19%,11new pool-days,25UTC-hour buckets,216disjoint windows.
Сравнение идёт против всех прежних eligible train/validation/test IDs, поэтому803из2885
новых train-membership rows не названы новым опытом. Raw UI proxy по telemetry:
87493→90103 (+2610), иной срез/definition, чем snapshot eligible87422→90020 (+2598).
Нельзя складывать их. Новые eligible age bins:0–1d149,1–7d988,7–30d291,30–90d585,
≥90d585; newborn<1d — лишь149. RMS return buckets<0.005/0.02/0.1/≥0.1:
1214/404/924/56. Volume bins<100/<1000/<10000/<100000/≥100000USD:
17/197/1415/939/30. Bucket boundary definitions — `diversity.py`.
На основных pools соседние15m returns имеют lag1 correlation около0.60–0.70;
редкие/малые cohorts не дают устойчивой оценки. Все raw per-pool counts есть в JSON.

Source journal с21:13:36UTC4октября до21:32:01UTC5октября:
2243reserved/2243outcome,2219valid,7duplicate_bar_in_response,17transport failures;
HTTP2002226,17безHTTPresponse. Цепочка journal и capture manifest references проверены,
полный raw replay всех GET заново не выполнялся. Один сохранённый failure:
requests/001774, URLError/ConnectionResetError. Между любыми outcome максимум114.90s,
но между запросами отдельных quarantined pools до12h. На NEW snapshot лишь4/24pools
имели last closed candle моложе10min; самый старый был44.62h. Низкая freshness рынка
и no-trade не различены по одному отсутствию новых баров. Healthy source ≠24fresh markets.

На исходном cutoff B13 forecasts1576 (дваmodel lanes, дваhorizons),scored1528,
censored16,pending32;788bookkeeping origin groups не statistical independent observations.
Агрегированная paper-проекция stale:2311WAIT,11ENTERintents,0HOLD/EXIT,0fills,
realized0,fees0,turnover0,cash100% в шести показанных независимых книгах.
Она НЕ полная свежая оценка всех книг; legacy PnL нельзя приписывать NEW Brain.
`operator.integrity_ok=false` в forensic renderer связан с `refused:runtime/outside_root`:
pinned runtime находится вне operator04, отчёт не последовал за ним по безопасному
path allowlist. Это ограничение проекции, не доказательство повреждения model bundles;
их seals проверены отдельно. Этот UI/report дефект не скрыт и не исправлен снятиемpathgate.

Поздний paired replay (`paired02.json`, cutoff22:03:46UTC5октября): всего18origin decisions,
2pools,18targets5m/14targets15m. OLD→NEW pool macro5m0.00306464→0.00246807,
15m0.00267620→0.00238181. NEW coverage5m0/88.89/100%,15m0/78.57/100%:
медиана сдвинулась вверх, но calibrated результат не доказан. OLD inference offline;
это не парный prospective publication и не новый экзамен, созданный нами.

### Временная линия UTC

- 2026-10-04 21:09:15: operator initialized; первоначальный UI цикл manual.
- 21:13:36: первый future exam activated.
- 21:46:36–21:49:59: реальные AUTO/Pause/Resume/Stop/restart/Resume при приёмке;
  вмешательство, не автономное испытание отказов.
- 2026-10-05 06:20:43: `.DS_Store` paper failure, ошибочный domain_stop.
- 08:30:47: Resume сразу снова paused;08:57:43: Codex Resume послеruntime06repair.
- 21:12:11–21:13:55: настоящий automatic snapshot.
- 21:13:55–21:14:48: automatic four-fit training/candidate publication.
- 21:14:49: новый future exam activated;21:14:50: paper job published.

AUTO Collect/config adoption, Snapshot, Train, publication, Future paper, Waiting прошли
на реальных данных;24h таймер сработал. Pause/Resume/Stop/restart проверены реальнымUI
под наблюдением человека/Codex. Crash reservation/fault tests остаются fixtures там, где
нет реального события. Пятидневного unattended AUTO не было; первый paper оборвался
через≈9h, source продолжил жить, B10/B12 stopped остались отдельным evidence.
