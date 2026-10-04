# B10 — эксплуатационный отказ и восстановление paper

## Результат и решение

**Paper работает в новой revision B10; source/audit B09 продолжает исходный rollout.**
Срок не продлён: до 2026-10-07 15:23 МСК. Модели, hot cohort, policy, costs и quota прежние.
[Control Center](http://127.0.0.1:8790),
[основной evidence](evidence/brain-operational-recovery-2026-10-04.json),
[независимые проверки](evidence/brain-operational-recovery-verification-2026-10-04.json),
[снимок интерфейса](evidence/brain-operational-recovery-control-center-2026-10-04.png).

B10 не является доказательством недельной автономности. Мы нашли реальный отказ самого
paper-supervisor при продолжающемся сборе данных. Разделение source и paper сохранило
acquisition, но ещё не обеспечило непрерывную работу всего продукта. Поэтому следующая
source/model разработка уступила приоритет восстановлению, диагностике и безопасному restart.

### Что произошло и что удалось установить

В 13:35:45 UTC B09 supervisor завершил worker; исходный безопасный лог:

```json
{"category":"supervisor_guard","elapsed_seconds":7.608944125007838,"exit_code":-9}
```

Worker log пуст. Категория означает исключение guard, не установленную причину внутри него.
Нет оснований объявлять это provider outage, OOM, clock jump или доказанной corruption.
Оба frozen inventories сейчас точны; две серии по 300 read-only проверок прошли. Это
не доказывает исчезновение первопричины. Исходные STOP/incident и runtime сохранены.
Sealed `work/brain-b10/incident-evidence` содержит исходный snapshot и диагностические hashes.

Полный replay остановленного paper точный: 85 ticks, 765 decisions, 5 exits, 6 books,
open/pending=0. B10 перенёс этот prefix без сброса cash/модельных namespaces. Старые
B09 paper/train/UI jobs fenced; source/audit не перезапускались. Новый paper впервые
обновил dashboard около 13:55 UTC. Между соседними успешными soak samples gap **1189,70s**.
Во время paper STOP source принял ещё **62 captures**. Эти данные доступны для последующего
scoring/training по реальным временам, но не создают прошлые decisions или сделки.

Отдельно у source зарегистрирован третий B09 timeout, request000294:
`public_http_failure / URLError / TimeoutError / http_status=null` в 13:51:02 UTC.
Он прошёл штатный 30s cooldown и автоматически восстановился. Всего в B09 — три таких
receipt, включая qualification; новых HTTP429 в них нет. Это отдельное транспортное
событие, которое не объясняет paper guard failure.

### Что изменилось в продукте

* Safe guard diagnostics сохраняют stage, exception type, errno и только allowlisted reason.
  Startup failure тоже получает durable incident. Произвольные exception messages не пишутся.
* Paper может автоматически восстановиться после внешнего SIGKILL/SIGTERM при
  `category=worker_failed`: cooldown60s, максимум две reservation за migration lineage,
  полный ledger/projection audit перед продолжением. Потраченный allowance не обнуляется
  успешным запуском или migration. Unknown guard, integrity, clock/deadline и training errors
  не перезапускаются этим механизмом. **Исходный B09 сбой автоматически не лечится**:
  B10 — отдельное проверенное решение о восстановлении после audit.
* Candidate publication отделена от появления весов на диске. Нужны completed result,
  sealed training run и отдельный атомарный ready marker с manifest hash. Partial fit не
  получает новые paper exposures. Daily/maturity gates и fit budget сохранены.
* UI читает текущие paper/source health независимо от старого успешного dashboard, показывает
  STOP/паузы и срок следующего допустимого обучения. История soak перенесена вместе с gap.
* Повторная migration сохраняет shadow namespaces, prior incident lineage, recovery budget
  и TRAIN_STOP. Замена paper не даёт права повторить failed training или продлить source.

Новый paper frozen на code `9b3a9c4`, pin
`a15df723ce8dc2413bdda20eee13a9e62544f873125a4fccb9130c03d27bc67c`.
Позднейшее усиление helper для переноса TRAIN_STOP проверено fixtures; активный bundle не
переустанавливался, B09 TRAIN_STOP отсутствовал. Source pin прежний
`f4e722ed1a27f702a7a303f33e56548eb50c5ba442524028021cf53db98d59e5`.
Source config/activation/deployment/seed/universe/provider-seed hashes совпали до и после.

### Измеренный срез после восстановления

Cutoff **2026-10-04 14:05:31 UTC**, sealed root `work/brain-b10/evidence-final`, manifest
`f9acebb864d7f2218a737a9eb855c461772479f1e07fd0aab92ecf6e5db19b89`.
Это 17 успешных workers за 10,19 минут между samples, не сутки:

| Показатель | Факт |
|---|---:|
| Новые paper ticks / decisions | 10 / 108 |
| Решения каждого candidate × bundle | 18 |
| Новые входы / выходы | 0 / 0 — все остались в cash |
| Новые paper incidents | 0 |
| Worker p50 / max | 7,89s / 10,26s |
| Source healthy / ≥1fresh hot по samples | 94,12% / 88,24% |
| Локальный corpus | 29 808 bars / 24 observed pools |
| Raw captures / versions | 805 / 101 787 |
| Полный raw/index audit | exact, 800 valid captures |

Нулевой новый PnL означает отсутствие новых сделок, а не качество стратегии. Перенесённые
пять закрытых сделок и их результаты сохранены. На полном B09+B10 sample series календарный
span1,312h, observed1,006h; gap не засчитан полностью как uptime. Проценты freshness по
срезам не заменяют календарную доступность и пока нестабильны на таком малом окне.

1 764 raw market joins, 291 published forecast joins и full engine replay точны; отдельно
воспроизведены **272 matured forecast outcomes** из first-observed raw. Installed wheel
получил тот же результат. Replay использует cutoff последнего paper tick (28 807 bars),
source audit — более поздний report cutoff (29 808 bars); это разные явно сохранённые времена.

Paired future scores с начала B09 до cutoff, включая восстановленный B10. Каждая строка
сравнивает кандидатов на одинаковых observations внутри bundle. Ниже mean pinball:

| Bundle | Горизонт | n | Empirical | Linear | GRU |
|---|---:|---:|---:|---:|---:|
| 9ea4a6bd | 5 мин | 74 | 0,001596 | 0,018481 | 0,004865 |
| 9ea4a6bd | 15 мин | 62 | 0,003507 | 0,005186 | 0,002314 |
| dab00e17 | 5 мин | 74 | 0,000996 | 0,009267 | 0,002600 |
| dab00e17 | 15 мин | 62 | 0,002316 | 0,002337 | 0,002282 |

Всего 272 scored и 48 pending; это не 272 независимых рыночных эпизода. Горизонты и версии
зависимы. Mixed ordering не основание для promotion; champion empirical прежний.
Новых fits0. Первый следующий daily fit не раньше **5 октября 13:41:54 МСК**,
затем hourly scheduler и ≥100 новых mature samples; максимум4 runs вместе с исходным B07.
Обучение использует price targets, paper PnL остаётся отдельной policy evaluation.

### Проверки и граница готовности

640 base PASS/6 optional skips, 666 combined ML PASS, 52 focused; make check/smoke/build.
Настоящий SIGKILL в отдельном subprocess fixture после ledger commit, restart с exact replay,
corruption fail-closed, bounded retry exhaustion, safe logs, повторная migration и candidate
publication проверены. Это failure injection на fixtures, работающий paper намеренно не убивали.
Desktop1440/mobile390: без JS errors и горизонтального overflow. Дополнительная ограниченная
read-only health series: 30/30 healthy paper и source samples за 290,26s, STOP0.
Это отдельный короткий срез текущей работоспособности, не восстановленный uptime.
Manifest observer `f413780417c4cfab298fe33d9df9a57eb52daa36e2eb7498869dc782610e39ca`;
проверки wheel и series binding сохранены в verification evidence.

**Решение: продолжать B09 source + B10 paper в текущих bounds. Weekly activation пока закрыта.**
Неизвестная причина guard failure, single-provider freshness, отсутствие реальных суток и
первого scheduled refresh сильнее мешают недельному режиму, чем текущая модель. Mac должен
оставаться включённым/доступным; провалы observation не дорисовываются. Автономность выросла
за счёт fault isolation, сохранения состояния, ограниченного restart и видимой диагностики,
но произвольные неизвестные ошибки система всё ещё намеренно не обходит.

Оптимальный B11: оставить контур на реальные сутки, проверить scheduled refresh и последующие
paired decisions, параллельно квалифицировать независимую source lane. GT остаётся текущим
временным USD price-proxy; PumpSwap raw и account balances нельзя вставить в его candles
без versioned semantics, покрытия и отдельной provenance. Новый backbone не приоритет.
Prospective operating review и условия недельного расширения — [roadmap](../memory/roadmap.md).
Реальные средства/подписи/транзакции/paid подключения не использовались; F01/D43/holdout untouched.

---

Ниже сохранён план, зафиксированный до новой runtime revision (commit `f74c8ed`).

## План до изменения runtime, 2026-10-04

B09 принят. Реальная эксплуатация обнаружила более срочное ограничение, чем новый source:
2026-10-04 13:35:45 UTC paper supervisor завершил worker после 7,61s, записав только
`supervisor_guard`, exit −9. Worker log пуст. Source продолжает acquisition. Причина внутри
guard неизвестна; provider outage, OOM или corruption не установлены. STOP не удалять.
Текущие frozen inventories точны, 300 read-only guard проверок прошли за 2,714s.
Полный replay: 85 ticks, 765 decisions, 5 exits, 0 open/pending; state SHA
`b8623678acdf0b237a69f590952a72950a48b81d670ec81fe6469bcf92420a11`.

B10 — отдельная paper revision поверх неизменного B09 source. Скопировать ledger,
shadow namespaces, обучение и исходную историю soak; проверить exact prefix/cash/positions.
Сохранить старые STOP/incident и lineage. Не менять source runtime, allocations, expiry,
hot cohort, models, policy/costs, F01/D43/holdout. Никаких новых provider probes или fits
раньше прежнего daily gate. Новый paper prefix b10; исходный срок 2026-10-07 12:23:51 UTC.

Добавить безопасную диагностику guard: stage/type/errno и только allowlisted reason,
без произвольного exception text, endpoints или credentials. Ошибки startup тоже имеют
incident. UI независимо от последнего dashboard показывает STOP/возраст paper и source,
ожидаемое время следующего training, а не выдает последний успешный snapshot за health.

Автоматическое восстановление разрешить только paper worker, внешне завершённому SIGKILL
или SIGTERM при category worker_failed (не supervisor kill), максимум два раза за deployment
с переносом уже потраченного бюджета через migration, cooldown60s. Перед продолжением —
полный ledger replay и проверка projection. Никаких retries training, integrity errors,
unknown guard errors или deadlines. Каждая попытка имеет immutable incident/recovery receipt;
неоконченная попытка расходует allowance. На третьем/другом отказе STOP. Это не общий
авторестарт после любой ошибки и не доказательство устранения исходного неизвестного сбоя.

Проверки: fixtures crash до/после ledger commit, exact recovery, corruption fail-closed,
retry exhaustion/restart, training partial artifacts не становятся кандидатами, safe logs,
migration уже migrated root, отсутствие backdated решений. Независимый replay реальных
артефактов и raw/prediction joins; make check/smoke/build и optional ML tests. После cutover
наблюдать несколько реальных циклов без ручного вмешательства; сутки не симулировать.

Следующий source сохраняет отдельную provenance lane: GT first-observed candles нельзя
подменять on-chain balances или неqualified PumpSwap events. Multi-source остаётся следующим
архитектурным участком, а не объявленным B10 fallback. Недельную activation не выдавать:
нужны реальные сутки, scheduled refresh и устранение неизвестных operational failures.
