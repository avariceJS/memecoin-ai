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
