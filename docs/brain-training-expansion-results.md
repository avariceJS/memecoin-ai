# Расширение training support — 2026-10-06

Acquisition, admission и восемь fits завершены. Training support **24 → 86 рынков**.
Расширение подключено к AUTO; независимый future exam работает. Улучшение модели
пока не установлено. [Протокол](brain-training-expansion.md).
Предыдущие [forensic](brain-experience-review.md) и [vacuum](brain-vacuum-results.md) приняты;
этот этап проверяет превращение historical registry в actual training support.

## Что изменено в пути данных

`Alchemy historical birth catalogue → outcome-independent month/SHA selection →
GT USD/5m hydration → exact birth/mint/quote/receipt admission → immutable corpus →
snapshot + прежний B13 recipe → frozen candidate → prospective paper`.

Исторические cohorts выбраны из May–Sep2026, без текущего ranking/liquidity/success filter.
Это выборки из ранее сохранённых monthly migration windows, не полный census месяцев.
Graduation bias сохраняется: bonding-curve токены, не дошедшие до migration, не представлены.
«No contiguous targets» не означает умерший токен или rug. Простое попадание pool в train
отделяется от числа его окон и ширины фактических SGD draws.

Namespace свечей совпадает с работающим Brain: USD/base/5min/first-observed. Для каждого
capture проверяются raw seal/hash, request currency/aggregation, base/quote mints против
on-chain birth, UTC bucket, OHLCV, birth range и реальные receipts. Gaps не заполняются.
Targets требуют все future bars; будущая доступность не переносится назад в прошлое.
UNKNOWN2026swap layouts и WSOL-цены не подставляются вместо этого contract.

Новый corpus — отдельный immutable artifact. AUTO snapshot умеет объединять его со своим
consistent live backup. Reference закрепляется в runtime binding. Первое наблюдение
сохраняется, conflicting revisions учитываются отдельно. Sealed historical-test corpus
имеет отдельный role и не допускается в training merge.

Счётчик growth для действующего24h+100gate остаётся source-only. Total training support
хранится отдельно: иначе после добавления archive следующий refit получил бы отрицательный
live growth. Information trigger пока не включён; модель, features и policy не усложнялись.

## Отрицательные operational результаты

- `admission-probe01`: HTTP401 для2025; ответ public API прямо ограничивает history180days.
  Upgrade не выполнялся; старый диапазон не запрашивался повторно через другие keys/hosts.
- `admission-probe02`:5validGET/5pools/1341eligible, затем429. STOP сохранён,900scooldown.
- `admission-bulk01`:10validGET/7positivepools/246eligible, затем15sURLError.
- `admission-bulk02`:14cases, включая2transportgaps; остановка при routine live cooldown.
- `admission-bulk03`: завершены все 75 оставшихся cases; 73 успешных GET, два URLError.
  Вместе с первыми campaigns выполнены исходные 100 случаев без повторных запросов.
  Source cooldown означает WAIT; auth/rate/sourceSTOP остаются fatal. Timeout30s,
  transport failure cap10/3consecutive. Исходный budget и wall expiry не сбрасывались.

Новые operational fixes относятся к historical worker. Работающий live collector не
переписывался. Его краткий transport cooldown и последующее самостоятельное recovery
сохранены в журнале. Нельзя приписывать все ошибки провайдера новому lane или считать
опубликованный30/min гарантированно доступной пропускной способностью.

Ранние protocol hashes воспроизведены точными SHA-matching prefixes в
`work/brain-expansion-20261006/protocol-prefixes01`; старые campaigns не редактировались.
Начиная сbulk03 копия protocol хранится внутри pinned code bundle.

## Роли providers после проверки

| Provider | Практическая роль | Что пока ограничивает |
|---|---|---|
| Alchemy | Historical birth universe; raw archival transactions2025–2026 | Coverage/decoders/quote normalization доUSD bars |
| GeckoTerminal | Готовые USD historical/live bars в существующий feature contract | Public180days,429/timeouts,sparse intervals; единственный qualifiedpricefeed |
| Helius | Возможный второй archival/event adapter | Configuredkey,ноaccountplan/no-paidbulk не подтверждены; installer/signup не запускался |
| Public Solana | Независимая проверка текущего account/venue owner | Не готовый historical price feed; wholesale archive не измерен |

Public verification01 ошибочно запросил адрес с пропущенным символом; егоnull не используется.
Verification02 взял точныйaddress изsealedbirthbinding: account существует, owner
`pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`. Обаcaptures сохранены;2RPC,0USD budget.
Это проверка owner, не доказательство completeness/price correctness за2026June.

Alchemy теперь участвует в отборе actual training universe. Ценовой runtime всё ещё зависит
отGecko; это ограничение не скрыто за формулировкой «multi-source». Helius raw transactions
сами по себе также не устраняют decoder/coverage/USD bottleneck. Нельзя на основании
одного advertised throughput обещать1000qualifiedtrainmarkets.

Первичные источники:
[GT OHLCV semantics](https://docs.coingecko.com/reference/pool-ohlcv-contract-address),
[Alchemy indexed history](https://www.alchemy.com/docs/chains/solana/solana-api-endpoints/get-transactions-for-address),
[Alchemy CU weights](https://www.alchemy.com/docs/reference/compute-unit-costs),
[Helius archival API](https://www.helius.dev/historical-data),
[public Solana endpoints](https://solana.com/docs/references/clusters).

## Сравнение без подгонки модели

### Что действительно дошло до обучения

| Показатель | Control | Broad |
|---|---:|---:|
| Train markets, включая реальные SGD draws | 24 | **86** |
| Eligible train до cap | 90336 | 98323 |
| Retained train | 48000 | 48000 |
| UTC days с train observations | 93 | 104 |
| Pool-days | 370 | 465 |
| Effective pools по числу retained rows | 6,19 | 9,55 |
| Largest-pool share | 19,62% | 15,46% |
| Неперекрывающиеся полные feature/target windows | 4910 | 5343 |
| Unique decisions в 64000 SGD draws, seed7/19 | 17278 / 17293 | 14023 / 14091 |

Каждый из четырёх learned fits broad использовал все 86 pools; это проверено по `fit.json`.
Все 7987 новых решений вошли в retained train и вытеснили столько же старых из cap.
Новая история — первые ≤3 суток выбранных births May–Sep2026, не пять полных месяцев.
Новых markets из2025 в USD train нет. Валидный corpus содержит17825bars из100 успешных
ответов;62pools дали targets,38 — ни одного полного contiguous decision; schema rejects0.

Дополнение age audit использует уже сохранённые birth timestamps исходного source
(отдельный metadata SHA в report). В начальном `support.json` старые pools были `unknown`,
поскольку туда передавались только новые birth records; это не отсутствие metadata в проекте.

| Возраст retained train | Control | Broad |
|---|---:|---:|
| <1day | 4521 | 9916 |
| 1–7days | 6463 | 8574 |
| 7–30days | 9895 | 8146 |
| 30–90days | 8288 | 6524 |
| ≥90days | 18833 | 14840 |

Новый опыт:62pools,95pool-days,14UTCdays,254UTC-hours;5427decisions в возрасте<1day,
2560 —1–3days. Effective pool count19,66; largestshare10,70%;97,17% решений соседствуют
с предыдущим5m origin. Полных disjoint windows568; это **не** статистически независимый N.
Exact feature duplicates со старым retained train0; near-duplicate fraction не оценена.

У20новыхpools меньше16решений, только26 имеют≥64,13 имеют≥256. При прежних pool/day
weights новыеpools получают72,09% вероятностной массы, хотя дают16,64% строк. Pools
с<16решениями получают23,26% массы. Effective decisions по sampling weights снижается
1872,95→1171,60; это мера концентрации weights, не независимых наблюдений.
Новый опыт разнообразен по рынкам, но сильно смещён к молодым рынкам:7203из7987решений
в двух верхних volatility buckets;2440 имеют rollingvol≥0,1. Эта смена распределения —
измеренный факт; считать её единственной доказанной причиной изменения loss нельзя.

Acquisition:107GET,100success,401×1,429×1,URLError×5;2 193 348bytes успешных payloads,
0USD budget. Это74,64eligible decisions и5,31disjoint windows на chargedGET с учётом ошибок.
От первого probe до последнего bulk — около85минут, включая900s rate cooldown.
Скорость одной historical выборки нельзя экстраполировать на устойчивый daily поток.
Восемь fits вместе со snapshots/audit заняли28,64s; peakRSS1 806 925 824bytes на этом Mac.
Compute/training и второй Mac на данном масштабе не были главным ограничением.

### Общий descriptive test

Одинаковые246decisions/8старыхpools/1UTCday; validation264decisions/8pools.
Это exploratory historical test. Значения — pool-macro quantile pinball; меньше лучше.

| Bundle / family | 5m | 15m |
|---|---:|---:|
| Frozen B13 empirical | 0,00699045 | 0,01308205 |
| Frozen B13 GRU | 0,00679913 | 0,01258571 |
| Control empirical | 0,00699282 | 0,01309119 |
| Control linear | 0,01566156 | 0,01403807 |
| Control GRU | 0,00725524 | 0,01221196 |
| Broad empirical | 0,01002557 | 0,01950810 |
| Broad linear | 0,02871347 | 0,01624103 |
| Broad GRU | 0,00849875 | 0,01270502 |

Оба новых bundles выбрали GRU по validation до test. Broad GRU хуже control на17,14%
(5m),4,04%(15m),8,92% по средней двух горизонтов. Выигрыша расширенного Brain здесь нет.
Broad GRU лучше собственного ухудшившегося empirical; это не победа над прежним baseline.
Coverage(q10/q50/q90): control GRU5m[0,0325;0,2886;0,9512],15m[0,1423;0,5488;0,8943];
broad5m[0,0122;0,7805;0,9756],15m[0,0366;0,5285;0,9187]. Ошибки калибровки сохраняются.
Все family/per-pool metrics, включая отрицательные, сохранены в reproducible JSON.

### Future и отложенный historical test

Future activation **2026-10-06 05:51:06 UTC**, expiry **2026-10-07 05:51:06 UTC**.
Срез этого отчёта: **06:20:01 UTC**, cutoff_ns `1791267601231386000`.
Каждый bundle опубликовал 20 horizon forecasts: 12 scored, 8 pending. Это общие
**8 market/origin decisions, 12 horizon cases**, а не 36 независимых observations:
5m — 8 cases, 15m — 4 cases, два старых рынка, один UTC day. Все input/target identities
и causal receipts совпали; offline inference не выдаётся за опубликованный прогноз.

| Bundle / family | Future 5m loss | Future 15m loss |
|---|---:|---:|
| Frozen B13 empirical | 0.00360831 | 0.00688762 |
| Frozen B13 linear | 0.01336337 | 0.00332097 |
| Frozen B13 gru | 0.00329437 | 0.00411153 |
| Control empirical | 0.00361516 | 0.00693163 |
| Control linear | 0.01332573 | 0.00330904 |
| Control gru | 0.00331950 | 0.00272287 |
| Broad empirical | 0.00755257 | 0.01439207 |
| Broad linear | 0.02691280 | 0.01060330 |
| Broad gru | 0.00622468 | 0.00643741 |

Broad GRU хуже control и frozen B13 GRU на обоих горизонтах этого раннего среза.
Coverage broad GRU: 5m [0;0,875;1], 15m [0;0,75;1]; control GRU: [0;0,375;1] /
[0;0,75;1]. При таком N это не надёжная оценка calibration. CI и independent N не заявлены;
устойчивого улучшения нет, весь 24h exam ещё **не завершён**. Нельзя переносить результат
двух знакомых pools на широкий новый universe. Exam продолжает работать автономно.

Все 12 отдельных virtual books остаются в CASH: по1000USD price-proxy capital, positions0,
pending entries0, fills/trades0, fees0, realized PnL0. Это отдельные книги, не единый
портфель на12000USD. Policy fixed15min/cost assumptions не менялась; trading improvement
не измерено. Ранние отчёты с0/2matured cases сохранены как отдельные artifacts.

Отложенные20pools не входят в train, selection или AUTO corpus.


Отложенный historical test завершён:20успешныхGET,13eligiblepools/924decisions/64disjoint
windows. Одинpool даёт66,34%строк; effectivepools2,19. Он НЕ добавлен к86trainmarkets.
Acquisition заняла618,73s,293945bytes. Итого stage:127GTGET и2publicSolanaRPC;
новыхAlchemy/HeliusRPC здесь0 — Alchemy birth catalogue переиспользован изvacuum stage.
Pool-macro loss:

| Bundle / family | 5m | 15m |
|---|---:|---:|
| Frozen B13 empirical | 0,04758885 | 0,09459989 |
| Frozen B13 GRU | 0,04484205 | 0,08512382 |
| Control empirical | 0,04757927 | 0,09455843 |
| Control linear | 0,05242092 | 0,10359891 |
| Control GRU | 0,04488315 | 0,09222546 |
| Broad empirical | **0,04386242** | **0,08400778** |
| Broad linear | 0,04795485 | 0,09359359 |
| Broad GRU | 0,05255104 | 0,09225014 |

Широкий empirical лучше control empirical по macro примерно7,81%/11,16%; broad GRU
хуже controlGRU5m17,08%,15m практически равен(+0,027%). Победу learned models результат
не подтверждает. На этих unseenmarkets simplebroadbaseline оказался лучшеGRU поmacro.
Но pooledloss broad empirical0,02219/0,04678 противcontrol0,02154/0,04592 — хуже.
Вывод зависит от weighting; маленькие pools и одиночные targets делают macro шумным.
Selection не пересматривалась по этомуtest. BroadGRU остаётся recordedcandidate,
старыйempiricalchampion не заменён. Не выдавать historicalunseenresult заfuturegain.

Control: тот же consistent source prefix и cutoff, старый24market corpus.
Broad: control + admitted historical markets. Validation/test samples и input frames совпали
byte-for-byte; новые historical origins находятся доtrain boundary. Train cap48k и прежние
pool/day weights,12features,context8,GRU16/linear,seeds7/19,1000updates сохранены.
Все 8 fits выполнены отдельно от B13 AUTO; повторных fits по test не было.
Validation1% simplicity selection сохраняет empirical полноправнымcandidate.

После freeze независимый paper сравнивает frozen B13/control/broad на одних будущих receipts.
Forecast loss/calibration проверяется пофактически опубликованнымpredictionfiles; loss
пересчитывается изq/Y, input/target identity сверяется. Нельзя выдавать offline inference
заprospective prediction. Policy прежняяfixed15min, книги независимы, real executionнет.

20 дополнительных unseen pools закреплены до fits; acquisition началась после freeze; их past-event
test не входит ни в train, ни в selection. Он дополняетfuture exam, но не заменяет его.
Future borrowedsource узок: hotforecast support преимущественно изстарых рынков. Даже
улучшение на нескольких таких outcomes не доказывает общего улучшения широкогоBrain.

## Проверки и evidence

`make check`: 909 passed /11 optional skips. ML: 140 passed. Smoke/build прошли.
Финальные ruff/check/format для src/tests/tools и git diff --check прошли.
На повторном запуске с тем же `--cutoff-ns` совпали scientific metrics, membership,
source journal prefix, admission и costs; latest operational dashboard намеренно отдельный.
[Git evidence](evidence/brain-training-expansion-2026-10-06.json).
Проверены namespace/mint mismatch, gaps, receipt cutoff, corruption, duplicate admission,
source-onlygrowth послеmerge, stagedruntimeбезпереключения, prospective pairing/metric
reproduction, transport isolation/auth/rateSTOP. Реальный cutover сохранил 2372 прежних paper event/STOP files по hashes.
AUTO/waiting, source healthy, reservations2/6; source config и operator config неизменны.

## Что теперь работает автономно

Runtime07 принимает immutable historical corpus в каждом следующем AUTO snapshot.
Обычный цикл collect → snapshot → train → candidate → future paper сохранён. В registry
основной панели8792 видны control и broad; общий отдельный exam доступен на8797.
Приёмка cutover сохранила2372старыхpaper event/STOP files. Rollback отдельно проверен
наfixture; реального rollback здесь не потребовалось. Source LaunchAgent не менялся.

Gate остаётся24h+100новыхsource-matured, quota2/6. Следующая reservation не раньше
2026-10-06 21:13:55UTC. Historical training count не участвует в livegrowth subtraction.
Information trigger остаётсяshadow: ускорятьfits при выявленном distribution mismatch
сейчас менее полезно, чем исправить использование и проверку данных. Сами8fits эксперимента
выполнены сразу, без ожидания24h, по отдельному заранее зарегистрированному бюджету.

Историческая acquisition этого этапа — **завершённый bounded batch**, не новый24/7daemon.
Live source universe остаётся24markets;86 — actualtraining support с добавленной историей.
AUTO после cutover ещё не дошёл до следующего суточного refit; это не выдано за новый
реальный AUTO cycle. Новый merge/recipe прошёл реальный эксперимент, интеграцияAUTO —
тесты и exactruntime revision. Новыеchampion/feature/policy/realexecution не внедрялись.

## Главные оставшиеся ограничения и следующий шаг

1. **Использование широкого опыта.** Равный вес pools переоценивает короткие истории;
   validation и будущий поток недостаточно представляют новые cohorts. Следующий model
   эксперимент — preregistered support-aware sampling и широкая future cohort, с прежней
   архитектурой и сильным empirical baseline. Повторятьfits по этим раскрытымtests нельзя.
2. **Полнота qualified price history.**38из100успешных historical ответов не дали contiguous
   targets; gaps нельзя заменять выдуманными нулевыми returns. Для многих sparse рынков
   нужны доказанные empty intervals либо отдельный masked/event contract и новая ablation.
3. **Независимость от GT.** Для2025 и event-oriented500/1000markets нужны exact2026PumpSwap
   decoders, coverage всех существенных fee routes и qualified quote→USD normalization.
   Alchemy indexed history уже пригоден дляuniverse; raw transactions ещё не readyUSDfeed.
4. **Размер training path.** Текущая compatibility materialization ограничена510kbars /
   500kdecisions, retainedtrain48k. Ранее квалифицированные shards/mmap ещё не включены в
   живойB13trainer. На текущих98keligible fit занял секунды, поэтому второйMac/LLM не нужны
   для снятия этого bottleneck. Для большего corpus нужен отдельный measured loader cutover.

Достигнуто86trainmarkets, не100/500/1000. Все965registrymarkets не гидратированы;
сегодняшние победители не использованы как фильтр, но graduation/month-window/activity bias
сохраняются. Genericwatch-marketUI/AxiomAPI, новыйMPC/RL и multi-Macinstallation не выполнялись.

## Artifacts и воспроизведение

Корень: `work/brain-expansion-20261006/`, raw/model/dataset outputs внеGit.

| Artifact | SHA manifest |
|---|---|
| corpus01 | `727df2a88d022966f6ca614e5b3fba0edca963008ebfce15c70c8ed2d40443e4` |
| control-model | `1839f0ca035711ebad0748d5207ad24ee93446c15c0059140fba5fe495d24ab2` |
| broad-model | `6acae357bd985087bc3c85820c001be6264f471c8661659bfb0ebde73ec5e41a` |
| operator-runtime07 | `90059c8a474da9e534bbba41a23ab195b20a2aa4c99d152026985fc4616f5c6c` |
| paper-runtime01 | `424cf3ce1d3d275775db6df80e9af57ddca2c844bc3003fb06faf05552c8dcd0` |

Cutover evidence: `work/brain-b13/operator04/revisions/operator-runtime07/`.
Ранняяопорная версия кода:948b068, qualification/protocol beforefits:c5d88bb.
Runtime integration/results commit:660bcfd; финальный commit добавляет future evidence и MemoryBank. Без новыхfits/GET можно
пересчитать будущий срез, выбрав **новый** output filename:

```sh
work/ml-venv/bin/python -B tools/brain_expansion_report.py \
  work/brain-expansion-20261006/experiment01 \
  work/brain-expansion-20261006/paper01 \
  work/brain-expansion-20261006/corpus01 \
  work/brain-expansion-20261006/comparison-next.json \
  --unseen work/brain-expansion-20261006/unseen-corpus01
```

Для точного повторения scientific prefix добавить `--cutoff-ns 1791267601231386000`;
operational dashboard показывает своё последнее состояние и собственный timestamp.
Полный session report SHA: `938079d62cd58a49a16050f5f0c629345482404851363a9cbe3cac9f7abfe571`.

Completed campaigns/fits не перезапускать. Reservedunseen corpus не добавлять кtrainingrefs.
Expiry source2026-10-07 12:23:51UTC иoperator2026-10-11 20:31:13UTC сохранены.
НовыеexamLaunchAgents независимы откнопкиB13«Стоп всё»; [команды](brain-operator-manual.md).
