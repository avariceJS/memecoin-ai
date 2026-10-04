# B09 — paper восстановлен, hot lane отделён от background

## Решение и фактический результат, 2026-10-04

**Активирован bounded rollout на 72 часа**, до 2026-10-07T12:23:51.922091+00:00.
[Control Center](http://127.0.0.1:8790) снова показывает живые наблюдения, прогнозы,
решения и виртуальные позиции. Source, paper, trainer, UI и raw audit работают отдельными
frozen LaunchAgents. B04/B08 STOP сохранены; B07 writers остановлены только при успешном
cutover. Реальные деньги, транзакции и paid подключения не использовались.

[Итоговый evidence](evidence/brain-hot-lane-2026-10-04.json),
[wheel/UI verification](evidence/brain-hot-lane-verification-2026-10-04.json),
[Control Center screenshot](evidence/brain-hot-lane-control-center-2026-10-04.png).
Локальный sealed artifact `work/brain-b09/evidence-final`, manifest
`609b13613b81d4792f418be3ec16c9b52a301be1b82f82816fca7aeef29674f9`. Raw и SQLite snapshots остаются вне Git.

Это несколько реально наблюдавшихся циклов и запущенный многодневный режим —
**не доказательство суточного, недельного или месячного uptime**. Сон Mac, потеря сети и
provider outage остаются реальными ограничениями; telemetry не дорисовывает пропущенное время.

## Что изменилось

Hot cohort — шесть наиболее ликвидных active pools из ранее принятого universe 24, выбранные
до GET без использования returns/PnL. Hot cadence 120s, cold 1800s. History и cold чередуются
в background, не чаще 1/min, только с запасом≥35s до следующего hot deadline. Все запросы
под одним provider limiter 12s; это разделение обязанностей внутри acquisition, не обход
лимитов параллельными клиентами. Остальные 18 рынков остаются в corpus и UI denominator.

Старые captures повторно не скачиваются. Source journal сохраняет reservations/cursors/
error history; derived SQLite из B08 обслуживает causal paper/training views. В новом source
унаследованы обе предыдущие 429. Qualification lease ограничена 120GET/25min; расширение до
rollout требует approval, привязанного к точным config/runtime/qualification hashes.

Новые paper predictions разрешены только hot pools. Порог fresh300s, features, targets,
CPMM и paper engine/costs не менялись. Heartbeat >180s, cooldown или STOP блокируют новые
решения; guard повторяется после inference. Pending/open позиции обрабатываются прежними
правилами, пауза не создаёт фиктивный exit. Daily trainer независим от acquisition.

Checkpoint paper projection связан с immutable prefix head и результатом engine. Полная
проверка цепочки ledger и независимый replay сохранены. При cache corruption — fail-closed;
удалённый/отсутствующий cache можно воспроизвести из ledger. Это ускоряет повторные workers,
не меняя торговую историю. UI показывает hot freshness, quota, роли рынков, model behavior,
source heartbeat и наблюдавшиеся часы отдельно от календарного span.

## Qualification перед activation

Окно 2026-10-04T12:23:56.492218+00:00 — 2026-10-04T12:45:14.082497+00:00; последний bounded worker завершён
внутри25min lease. Получены103 среза после 180s warmup. Критерии зафиксированы заранее;
B08 negative gate не менялся и не заменялся удачным B09 snapshot.

| Критерий | Требование | Факт |
|---|---:|---:|
| Срезы с ≥1fresh hot | ≥80% | 93.20% |
| Срезы с ≥4fresh hot | ≥60% | 78.64% |
| Новые closed candles каждого hot | ≥3 | 4 у каждого |
| Максимальный hot request gap | ≤180s | 130.28s |
| HTTP success | ≥95% | 72/73 =98.63% |
| Background history | реальный progress | 5 successful GET |
| Raw/index | exact | 19 938 bars, exact |

В ряде срезов fresh было 0; это сохранено в полном sample series. Наличие хотя бы одного
fresh market 93,2% времени по samples не равно 100% надёжности. Доступность HTTP не заменяет
freshness gate. Нельзя extrapolate эти проценты на месяцы или на сотни рынков.

Первый live request timeout: `URLError` с `reason_type=TimeoutError`, HTTP status отсутствует.
Без повторного запуска qualification выполнен штатный 30s cooldown. После activation также
наблюдён transport timeout с автоматическим recovery; всего на report 2
таких failure receipts. Новых 429 на report нет, provider error history сохранена. Глубже
network/connect/TLS стадия этими receipt не установлена; credentials не обвиняются.

## Реальный paper после migration

Prefix 43 ticks /393 decisions /3 closed trades /6 books перенесён byte-for-byte, replay state exact.
Ни cash reset, ни искусственных exits нет. Новые события отделены от перенесённой истории:
на snapshot 19 новых ticks, 162 решений, 2 входов,
2 выходов. Snapshot ledger head `3d88416482054914e49dc324c46af194a470b09226efb8b7b9588f774f423f07`.

| Версия / кандидат | Новые решения | Входы | Выходы | Изменение realized USD |
|---|---:|---:|---:|---:|
| 9ea4a6bd / empirical | 27 | 0 | 0 | 0.000000 |
| 9ea4a6bd / gru | 27 | 0 | 0 | 0.000000 |
| 9ea4a6bd / linear | 27 | 1 | 1 | -1.707708 |
| dab00e17 / empirical | 27 | 0 | 0 | 0.000000 |
| dab00e17 / gru | 27 | 0 | 0 | 0.000000 |
| dab00e17 / linear | 27 | 1 | 1 | -1.707708 |

Обе linear-версии вошли в один и тот же pool/интервал: это один рыночный эпизод,
а не два независимых наблюдения.

Это независимые виртуальные portfolios с разной предысторией, не portfolio единой стратегии.
Champion empirical не заменён. Новые виртуальные PnL/MTM не являются executable edge:
50bps slippage +25bps fee на сторону — assumptions, не измеренная стоимость исполнения.
Старый GRU −$2.345041 остаётся отдельной перенесённой B07 историей.

Ниже только прогнозы, опубликованные после migration, и outcomes с actual maturity на cutoff.
Каждая строка сравнивает empirical/linear/GRU на одинаковых future observations внутри
одной версии bundle; между версиями не предполагается одинаковая полная exposure history.

| Bundle | Горизонт | n | Mean pinball: empirical / linear / GRU |
|---|---:|---:|---:|
| 9ea4a6bd | 5 мин | 21 | 0.001502 / 0.018661 / 0.004848 |
| 9ea4a6bd | 15 мин | 9 | 0.003249 / 0.005197 / 0.002173 |
| dab00e17 | 5 мин | 21 | 0.000860 / 0.009402 / 0.002581 |
| dab00e17 | 15 мин | 9 | 0.001950 / 0.003287 / 0.001753 |

Это малый descriptive slice; model selection/promotion по нему не выполнялись. Новыхfits
0. Следующий fit не раньше 24h и 100 новых mature samples, четыре fits/run≤600s,
максимум4 runs включая B07. Обучение использует market-price targets; paper PnL остаётся
отдельной policy evaluation. F01/D43/holdout не открывались и не менялись.

## Проверки и operating bounds

Новый source snapshot: 21069bars,24observed pools; actual coverage шире
hot cohort, но historical completeness/случайная выборка рынка не заявляются. Независимые
972 raw joins и185 forecast joins, model pins,
full engine replay exact. Для audit сделан consistent SQLite backup: live writer не
останавливался и не конкурировал с полной raw/index сверкой в mutable DB.

623 base PASS/6 optional skips;649 combined ML PASS;35 focused tests,check/smoke/build.
Synthetic 72h-shaped workload:864 ticks/31 104 decisions, fullreplay1.800s/cache0.065s exact;
это capacity test, не фактический uptime. Browser desktop/mobile проверен на живом B09.

Source pin `f4e722ed1a27f702a7a303f33e56548eb50c5ba442524028021cf53db98d59e5`;
paper pin `532c4c374c635c415c5e25a9d5d03c70323e60955d1853ef17024bfcea4d726c`. Root source `work/brain-b09/source01`, paper
`work/brain-b09/paper01`. Labels `ai.memecoin.brain.b09.{source,audit,paper,train,ui}`.
Лимиты:72h,≤25000 новыхGET включаяqualification,≤4000background, raw8GiB/free4GiB,paid0.
Третий 429/24h или6 consecutive transport failures закрывает source; STOP не стирать.
Pool-local quarantine сохраняется, recovery probes ограничены. Глобальный provider breaker
по-прежнему может потребовать инженерного вмешательства — multi-source fallback ещё нет.

Read-only диагностика: `/api/snapshot` на8790, `health.json`, `soak/*.json`, workerlogs и
failure receipts. Immutable samples позволяют проверить реальные gaps. `span_hours` не
подменяет `observed_hours`; interval>90s не заполняется как непрерывное наблюдение. UI
остаётся локальным, без доступа к credentials/weights/raw/holdout.

## Следующий шаг

Оставить этот frozen runtime собирать реальные сутки, проверить soak/coverage/censoring,
первый плановый refresh и paired future behavior. Не переустанавливать продукт ради
следующего исследования и не расширять hot cohort без проверки capacity.

Главное ограничение теперь — single-provider freshness/availability и короткий календарный
опыт. Следующий независимый engineering шаг — versioned PumpSwap/price cross-check и
пригодный резервный hot source. GT index имеет собственную source semantics: raw Alchemy/
Helius нельзя просто смешать с его first-observed candles. Backbone complexity пока не
приоритет; новые модели должны выигрывать на следующих данных, а не на этом срезе.

---

Ниже сохранены план и поправки, зафиксированные до соответствующих действий.

# Исходный B09 plan

## Frozen plan до новых запросов, 2026-10-04

B08 принят с отрицательной activation. Его STOP, negative qualification и два provider429
сохраняются. Цель B09 — вернуть честный paper loop и оставить ограниченный72h rollout,
если новое расписание выдержит несколько полных циклов. Недельный/месячный uptime не
объявлять без календарного evidence. F01/D43/holdout и реальные execution не затрагивать.

Universe прежние24, без discovery. Hot cohort — первые шесть active по убыванию liquidity
из immutable B08 admission (tie по адресу); не по returns/PnL. Cohort фиксируется до GET.
Остальные18 сохраняются в denominator и background collection, quarantine наследуется.
Это liquidity-biased operational subset, не репрезентативный широкий рынок.

Hot due90s, global spacing≥12s (≤5/min), одна сеть/один budget coordinator. Background
не чаще1/min и только при запасе≥35s до следующего hot due; чередовать cold prospective
и history. Cold due1800s, history прежние≤30pages/pool/90days/1000rows, cursor/pagecounts
наследовать изB08; raw reuse без redownload. Hot requests max100candles. Невозможность
собрать данные не превращать в synthetic candles или прошлую доступность.

Provider-wide cooldown/rate-events наследовать; третий429/24h→STOP,6 consecutive transport
failures→STOP, auth/integrity→STOP. Pool-local schema/quarantine не останавливает здоровых.
Один bounded worker≤8GET/240s, HTTP timeout20s. Qualification≤120GET/25min, без activation
дальнейший runtime не может тратить network. Опубликованная activation должна связывать
config/runtime/qualification hashes. Старые roots/jobs не переписывать.

Qualification:1200s наблюдения, первые180s warmup; sampling каждые≈10s по actual cutoff,
без заполнения пропусков. Gate: минимум90 recorded samples послеwarmup, span≥900s,
≥1fresh hot pool не менее80% samples, ≥4fresh не менее60%; каждыйhot pool дал≥3разных
новых closed candles посленачала, hot inter-request gaps≤180s, HTTPsuccess≥95%, безSTOP,
raw/indexexact. Fresh означаетactive и0≤now−close≤300s, как вpaper. Publish полный
sample series, denominator6/24, gaps, failures и оба расписания. Это новая operational
проверка cadence, не ретроспективная правка B08 gate и не model-selection trial.

Background обязан реально получить отдельные requests; history starvation видна отдельно,
не скрывается ростом live captures. При отрицательном gate не продолжать сетевую qualification.
При положительном gate exact prefix/cash/positions migration B07→B09, сохранить champion/
candidate versions и policy; source/paper/train/UI отдельные frozen jobs. Перед cutover
повторить current freshness и binding checks. На rollout72h максимум25000 новыхGET,
background≤4000, raw8GiB/freefloor4GiB, paid0. Qualification входит в общийbudget.

Daily training прежний D44/D47,≥24h+100new mature samples,4fits≤600s, максимум4runs включая
перенесённыйB07run; прежниеfeatures/targets/selection. Не переключать champion автоматически.
Новые outcomes толькопослеmaturity, model behavior/forecastloss раздельно. Live visibility:
hot/cold roles, provider cooldown, actualsource heartbeat и freshness, история paper trades,
дневныеmodelmetrics. Исследование/rawPumpSwap не блокирует source worker.

Проверки: fixtures hot priority/no starvation/cooldown/reservation/restart/bindings, полный
raw/index и paper replay, snapshot→raw/forecast joins, installedwheel и UI. Автоматический
soak status на72h должен измерять реальные часы, gaps/coverage/worker failures; первые
20минут не выдавать за сутки. Первые сутки требуют последующего review артефактов.

### Уточнение до первого GET: слот для background

Проверка worst-case расписания обнаружила, что6×12s при hot90s оставляют только18s,
меньше35s guard для background: history могла голодать бесконечно. До acquisition
hot interval изменён на120s (3hotGET/min). Это оставляет48s после earliest next start,
достаточно одного bounded background request. Global5/min, guard35s и maxhotgap180s
не меняются; все дальнейшие thresholds прежние. Источник не опрашивался до этой поправки.

## Engineering готовность во время qualification

Source bundle зафиксирован отдельно от последующей paper revision:7b2fe1d,
pin `f4e722ed1a27f702a7a303f33e56548eb50c5ba442524028021cf53db98d59e5`.
Runtime не переписывается во время измерения. Первоначальный lookup официального FAQ
GeckoTerminal теперь перенаправляет на https://docs.coingecko.com/ ; старое упоминание
30/min не используется как гарантия. В B09 действует собственный измеряемый bound5/min.

Paper checkpoint привязан к prefix head и result_sha immutable transition ledger.
При несовпадении state/checksum исполнение останавливается; без cache полный replay
восстанавливает точное состояние. Проверка полной цепочки ledger не отключена, независимый
полный replay остаётся audit oracle. Engine/policy/costs не изменены. Cash-only synthetic
workload72h-shaped:864ticks/31 104decisions/10 368unique predictions; полныйreplay1.800s,
checkpoint0.065s, exact. Это capacity fixture, не календарный uptime или performance модели.

Soak samples хранят actual times, hot freshness и sourceheartbeat. Сон/простой машины
виден какgap; covered time ограничивается≤90s между samples и не заполняет пропущенные
часы. В UI observed/span hours раздельно. При source heartbeat>180s новые predictions
paused; только hot pools допускаются к новым paper signals, остальные24 остаются в
market projection/corpus. Existing positions не закрываются искусственно, engine прежний.
