# Текущий статус

## B05 — текущий статус, 2026-10-04

**Source architecture переоценена; новый primary collector не активирован.** Пользователь
поставил надёжный самостоятельный data/training loop выше очередного model experiment.
GeckoTerminal — discovery/history/cross-check; finalized raw transaction lane — кандидат
основного будущего потока после venue decoding/coverage qualification. B04 collector-only
работает без изменения runtime, budget или expiry; linear-winsor future challenge отложен.

Planfba806a, raw0c9af4b, launcher3f3dbdb, replay08fbced. Один Alchemy probe:13RPC/500nominalCU,
12,34s, failures0/retries0, USD0 по owner attestation+unchanged mapping, не provider proof.
Новая отдельная B05 allocation, не reuseD51.9/12owners PumpSwap;4×40references и4hydratedtx.
Все pages насыщены; history_complete=false, decoded_trades0/training_eligible=false.
Два stale pools имеют последние references внутри последних GT bars: lag GT не доказан,
редкая активность совместима с evidence. Для100/300pools naive hydration/polling не годится:
условные1tx/min дают207/622M CU за30days; это сценарий, не measured billing.

Sealed09:14UTC snapshot15323bars/12pools,128revisions/98keys,4receipt-hour buckets одного дня.
Shadow220scored/6censored/28pending,3hour blocks, macro pinball empirical0,006990/GRU0,008152/
linear0,015251, CIнет. Новыхfits0, champion прежний. Никакого edge/quality claim.
Handoff09:18:53UTC B04:debit203/12000,268forecasts/234scored/6censored/28pending,STOPнет.
ОдинURLError cycle восстановился на следующем due, subtypeunknown. Первые3hourlyprobes:
HUQduplicate_bar_in_response;5gep/FXgvalid-stale;3quarantines сохраняются, probes1/3, release0.
Original expiry2026-10-07T00:30:23.405597Z; job не продлён. Это bounded, не вечный сервис.

Новый evidence ledger: causal admission, duplicate/revision/as-of/null→hydration; bounded
raw acquisition и offline replay. Source-local health policy пока не deployed supervisor.
8newtests,565base/6optional skips,591combined; check/smoke/build, installed-wheel exact replay.
Frozen B04/D50 verified; D43/F01/holdout не открывались. Raw work/brain-b05 вне Git.

**Следующий B06:** verified PumpSwap event decoder на recorded+fixtures, затем economical
bounded indexed/transaction ingestion с gaps/budget/local failure accounting; лишь после
qualification масштабировать universe и подключать новый corpus к D44/D47 training.
Helius mainnet access/no-paid не установлен, subscriptions нет; WSS tuning не переоткрывать.
[Итог](../docs/brain-source-architecture.md),
[evidence](../docs/evidence/brain-source-architecture-2026-10-04.json).

## B04 — предыдущий срез, collector остаётся активным

**Collector-only активирован; automatic training отключён.** Новый контракт отделяет
запись observations/missingness от достаточности fresh universe для модели. B03 остаётся
REJECTED/STOP, старые jobs не включались. Новый `ai.memecoin.brain.b04` loaded, root
`/Users/root1/Desktop/memecoin-ai/work/brain-b04/prospective01`, frozen pin
`f248c0305d81f684db7954d1c914c335c01c2ab0f9312d10ecc593ab6d2d183e`.

Два qualification cycles по9GET/99,54s и99,14s, каждый:7fresh/2valid-stale/3quarantine
skips, denominator12, event time продвинулся у7пулов. Ошибок provider/schema0, probes0.
Probe budgets/next_due унаследованы без reset. До второй фазы early-due assertion
остановилась доGET/резервирования; история сохранена, фактически market cycles2.
На activation18новых GET, общий153/12000/paid0. Original expiry2026-10-07T00:30:23.405597Z
не продлён. 5min due-only, ≥12s/GET,240s/cycle, global q≥4/auth/rate/2transport/integrity
stops; дополнительно6cycles без event progress→STOP. Awake/login/network необходимы,
power settings не менялись. Дальнейшие GET/status проверять read-only, не считать153
текущим live counter навсегда. Handoff08:53:37UTC: первый автономныйcycle107,34s completed,
STOPнет, debit162,212forecasts/178scored/6censored/28pending; `status-handoff-02.json`. Training branch отсутствует; B04weights не deployed.

Audit: duplicate captures различаются толькоopen. Extreme lower_wick с|z|1425,58 есть
вraw (low≈0,00004928 приopen/close≈0,17), независимого подтверждениянет. Не удалялся.
16development fits59,09s: train-only q01/q99winsorization перед прежнимz, unchanged
B03dataset/fullfolds/seeds7/19/10epochs.8controlweights tensor-exact B03. Linear validation
+4,33%/+0,96%→кандидат отдельногоfuture shadow; GRU−8,67%/−2,83%→отклонить этот clipping.
Empirical всё ещё лучше linear, champion не меняется. Exposed evaluation не confirmatory.

Sealed snapshot08:47:30.797927UTC доactivation:15287bars/12pools,98revisions/79keys,
calendarfull11/12. Shadow198forecasts:171scored/6censored/21pending,2hourblocks, CIнет;
empirical/GRU/linear macro pinball0,007418/0,008643/0,015252. Это старыйmodel000000.
После activation новые observations сохраняются отдельно от этого cutoff.
10newtests,557base/6optional skips,583combined,check/smoke/build; installed-wheel transform/
predictions/scoring/bootstrap и raw→mature shadow exact, replayfits/network0.
D43/F01/holdout не читались/не менялись; frozen D50/B03 verified.

**Следующий B05:** наблюдать frozen collector, coverage/revisions/stops в новых часах/днях;
заранее ограничить paired future shadow linear-winsor challenger с cutoff до новых
observations. Не менять champion/GRUbackbone по exposed тесту. Исходный budget/expiry
не обновлять молча. Read-only:
`.venv/bin/python -B tools/brain_status.py --root work/brain-b04/prospective01`.
Остановка — создать `work/brain-b04/prospective01/STOP`; не удалять дляretry.
[Отчёт и график](../docs/brain-availability-normalization.md),
[evidence](../docs/evidence/brain-availability-normalization-2026-10-04.json).

## B03 — предыдущий исторический срез, заменён B04

**Data/engineering этап завершён; continuous activation отклонена.** Per-pool quarantine
квалифицирован на fixtures, recorded replay и двух live cycles. Fresh support 8/12 и 7/12
при заранее заданном gate ≥9. Третьей попытки нет. B03 root закрыт STOP, job не установлен,
qualified receipt не выпущен, automatic refresh не запускался. **Фоновый Brain сбор сейчас
не работает.** D43/F01 остаются отдельными и неизменными; это не ожидание F01 readiness.

B03 добавил 32 public GET: 20 prospective + 12 historical; общий debit 135/12000, paid0.
Expiry прежний: 2026-10-07T00:30:23.405597Z. Все шесть недостающих prefixes достигли floor.
Corpus 4853→15237 bars/12 pools, common-window dataset 14326 decisions. Calendar coverage
полное у 11/12, у одного отсутствуют два интервала. Largest-pool row share 62%→21%,
train diversity 1→4 старых пула. Revision observations 84/69 keys. Receipt support —
три UTC-часа одного дня, не непрерывный многодневный corpus; actual timestamps сохранены.

Seed quarantine уточнён до сети: два conflicting pools HUQ…/5gep…. Второй live cycle
добавил FXg… (`duplicate_bar_in_response`, capture000129); whole response исключён,
остальные запросы продолжились. Сейчас quarantined 3/12; два других пула stale,
причина отсутствия новых bars неизвестна. Provider failures0. Hourly recovery probes
вживую не проводились, release пока проверен только на fixtures.

24 development fits /64,01s: прежние empirical/linear/GRU, seeds7/19, две learning curves.
Full-train GRU improvement −1,33%/+6,84% по folds; второй fold положительный, но рост
не монотонен, первый не подтверждает, seed7 хуже baseline в обоих. Shadow170forecasts:
145scored/6censored/19pending, два часовых блока; empirical/GRU/linear pool-macro pinball
0,005534/0,006684/0,013963. Champion empirical и B01 model000000 не менялись. Max feature
|z|1425,58 (`lower_wick`) требует data/feature audit, не увеличения backbone.

16 новых тестов, 548 base/5 optional skips, 573 combined; check/smoke/build прошли.
Installed-wheel dataset/folds/checkpoint predictions/metrics/bootstrap/shadow exact,
reproduction fits/network0. Imported raw/старый STOP/D50 bundle verified; D43/holdout
не читались. Artifacts: `work/brain-b03/{prospective01,snapshot01,curves01,reproduction01}`;
runtime pin `0119e651fec62f642a82042a5d078cbb69ec9ca5c3dddab57c2f282ac9b6a9b4`.
Read-only: `.venv/bin/python -B tools/brain_status.py --root work/brain-b03/prospective01`.

**Следующий B04:** source availability/freshness и extreme-bar/feature audit; отдельная
train-only robust-normalization ablation с прежними моделями/targets. Для continuous
сбора — новый явно зафиксированный collector-only контракт, различающий source health,
unknown inactivity и forecast eligibility при denominator12. Не объявлять B03 pass,
не уменьшать gate задним числом и не продлевать expiry/budget молча. До отдельного
конкретного решения новый job не запускать. [Итог и графики](../docs/brain-pool-quality.md),
[evidence](../docs/evidence/brain-pool-quality-2026-10-04.json).

## B02 — предыдущий исторический срез, заменён B03

**Аналитика завершена; data expansion частично, Brain collectors ОСТАНОВЛЕНЫ.**
F01 не блокирует Brain. История +505 unique bars, merged4 853/12pools, полный target
coverage6/12; ещё6пулов недобраны. Current-ranked bias сохраняется, largest pool62%rows,
train обоих chronological folds пока только1старый pool. 68revision observations/55keys.

24real neural fits:2folds×3nested train sizes×linear/GRU×seeds7/19. Fixed validation/
evaluation и maturity purge, train-only scaler; hard600s, completed18,94s. На полном
train GRU ensemble лучше empirical исторически0,92%/4,53%, но CI включает0 и seed7 хуже
в обоих folds. Shadow128mature outcomes (96published+32offline recovery),16pending:
empirical/GRU/linear macro pinball0.005228/0.006428/0.014142. Это1UTC-hour block, не
устойчивое преимущество. Champion/backbone не менялись. Max evaluation feature z-score784
относительно train1pool — аргумент за data diversity/normalization audit, не рост модели.

**Почему остановлен сбор:** public OHLCV capture000087 содержит конфликтующие bars
одногоtimestamp (разныйopen). Frozen B01 corpus ломал последующие циклы из-за повторной
валидации этого raw. Подготовлен versioned B02 runtime с whole-capture quarantine:
старые schema failures исключаются с provenance, новые ответы всё ещё strict; hashes
fatal. Старый job остановлен при handoff. Первый live GET нового candidate снова вернул
duplicate_bar_in_response; failed cycle+inherited debit1→STOP2, без retry. B02 всего2GET,
общий сB01/probe103,paid/Alchemy/QuickNode/trades0. History continuation preflight не
создал root и не сделалGET. Stop budgets и raw сохранены, третий collector не создан.

Ни `ai.memecoin.brain.dev01`, ни `ai.memecoin.brain.b02` сейчас не loaded. Старый plist
перенесён в `work/brain-b02/retired-b01.plist`, новый не установлен. **Фоновый Brain сбор
не продолжается.** Оба STOP остаются. B02 root `work/brain-b02/prospective01`, failed
frozen runtime pin e95f7d5e…; original expiry2026-10-07T00:30:23.405597Z/ceiling12000
сохранены, не продлены. D43/F01/holdout не менялись/не читались, D50 bundle verified.

Артефакты: `work/brain-b02/snapshot01` (cutoff2026-10-04T01:49:04Z), `curves02`,
`reproduction01`; прошлые failures/history01/curves01 сохранены. 533base/4optional skips,
557combined tests,9newtests,ruff/format/mypy/smoke/build. Installed wheel: dataset/folds/
weights→predictions/metrics/bootstrap/shadow exact, reproduction fits/network0.
Read-only status: `.venv/bin/python -B tools/brain_status.py --root work/brain-b02/prospective01`.

**Следующий B03:** fixture/recorded qualification per-pool quarantine с неизменным universe
denominator; global provider failure-stop сохранить. Затем конкретное versioned activation
решение в оставшемся original envelope, недостающий history/новые часы/дни. После diversity
— отдельная train-only robust normalization ablation при прежних моделях/targets.
Не удалять STOP и не включать failed candidate. F01 не prerequisite.
[Итог и графики](../docs/brain-data-scale.md),
[evidence](../docs/evidence/brain-data-scale-2026-10-04.json).

## Предыдущий B01 — исторический срез, сейчас заменён B02

## Текущий приоритет B01 — Brain Development, 2026-10-04

По прямому указанию пользователя F01 больше не блокирует развитие мозга. Открыт
независимый exploratory track: настоящий historical backfill → supervised training →
prospective shadow. Прежние smoke-only ограничения ниже относятся к истории/F01,
а не к B01. Frozen D43/F01, reserved holdout и старые src/configs не менялись.

**Brain-v1-dev работает как прогнозная вертикаль, не торговая policy.** Keyless public
GeckoTerminal: universe12новых Solana pools, age4/4/4. Initial3482unique5min bars/4pools,
3454decisions; train1791/val595/test1050, purge18. Четыре neural fits (linear/GRU×seeds7/19)
реально проведены; empirical baseline выиграл validation. Полного исторического покрытия
ещё нет; остальные8пулов включены в prospective corpus. Срез второго успешного цикла:
4267bars/12pools,48paired shadow predictions,12scored outcomes. Это маленький зависимый
development sample, не edge/качество/generalization claim.

**Отдельный LaunchAgent установлен:** `ai.memecoin.brain.dev01`, root
`/Users/root1/Desktop/memecoin-ai/work/brain-b01/dev01`, frozen runtime
`work/brain-b01/runtime-dev01`, pin
`8d49becface75853f6a8d89ada3dd44c0be18015a934d34c76c3381d17a0a23c`.
Сбор каждые5min, sequential≥12s/GET, retraining≥6h при новых данных; CPU1thread для fit.
До2026-10-07T00:30:23.405597Z (03:30:23МСК),≤12000GET включая probe,≤12training cycles×4fits,
paid0. Нужны бодрствующий Mac/GUI session/network; power settings не менялись. Это
72h bounded continuous run, не обещание круглосуточного сервиса/бесконечного renewal.

Первый history429 закрыл backfill probe; выдержан≥900s cooldown. После двух успешных
prospective cycles следующий завершился `public_http_failure/URLError`, HTTP status null,
15,95s, no immediate retry. Точный DNS/TLS/connection subtype не установлен. История
сохранена;2consecutive acquisition failures→STOP, правило не ослабляется. Последнее
состояние проверять read-only `.venv/bin/python -B tools/brain_status.py` из core root:
фоновый job продолжает жить независимо от данного снимка памяти.
Остановка: `touch /Users/root1/Desktop/memecoin-ai/work/brain-b01/dev01/STOP`.

Проверки:525base/3optional skips,548combined с Torch,ruff/format/mypy прежних strict
модулей,synthetic smoke/build. Из installed wheel raw→dataset exact, повторные4fits дали
tensor-exact weights и exact selection/metrics;48predictions replay max error4.10e-8,
12outcomes exact. Model manifest9ea4a6bd…; ledger/checkpoints/raw внеGit immutable.

Дополнение к передаче: следующий плановый cycle1791076500 completed139,359s;
сервис продолжает сбор после URLError без правки runtime. Последний сохранённый срез
`status-handoff-02.json`:4291bars/12pools,72predictions/36scored/36pending,49reservedGET,
19revision observations. Успешный цикл сбросил consecutive failures; прежняя история
не удалена. Reproduction выше относится к более раннему48/12срезу.

**Следующий B02:** покрытие разных часов/дней и пулов, завершение bounded history для
недопредставленных pools отдельной versioned acquisition, coverage/revision/censor audit,
paired future shadow и learning curves/следующие кандидаты. F01 calendarD52 — отдельная
ветка, не prerequisite Brain. Не менять текущий frozen Brain runtime на месте, не
превращать exploratory test в reserved holdout. [Итог](../docs/brain-development.md),
[evidence](../docs/evidence/brain-development-2026-10-04.json).

## Предыдущее состояние D51 (сохранённая история)

Обновлено: **2026-10-04**, Europe/Moscow.

**D51 завершён: BOUNDED LIVE PASS; calendar не активирован.** После owner attestation
по Dashboard выполнен substantive review и один scoped receipt на180RPC/3600CU.
Это owner evidence, не independent provider verification; текущий endpoint совпал с
frozen credential fingerprint. Attestation/review commit7e9ae2b, analysis18e6c6b.

Реальный Alchemy finalized/profile2:180/180advanced coherent snapshots,3pools×60;
174usable features/6warmup. Окно2026-10-04T00:02:04–00:07:03UTC,299,34s; segments148,77/
149,00s, никаких ошибок/retries. RPC latency p50/p9587/136ms, Clock age at feature max14,36s
при frozen45s. Raw4,75MB, immutable source seal3a4fd46aaa1cc4cb7daf8c91d08a838aa7295cff277305660b9582aeab48d5d9.
Offline raw/features/targets + installed D50 wheel дали byte-identical reports.
506tests/2optional skips,Ruff/format/mypy/smoke прошли. Src/configs/bundle pin unchanged.

Номинально3600CU по180локальным attempts; реальный provider charge не выведен из MCP:
usage27660→19320CU при более свежемdataThrough (`account_usage_nonmonotonic`). Paid0,
PAYG/upgrades0. Owner confirmed no paid overage/no card и30M allowance. Этот review
не заменяет отдельную calendar allocation. Host после окна AC Power29% charging;
это не84day uptime proof. Power/D43/holdout не менялись, reserved raw не открывались.
Actual F01 source root отсутствует,service не установлен,windows/admission/training0.

**Следующий D52:** operational calendar activation decision для unchanged D48. Нужны
отдельный calendar receipt/allocation60480RPC/1209600CU, host awake/power/login/monitoring
условия и итоговое разрешение запуска; затем existing staged service. Не повторять
transport tuning/live qualification, не сдвигатьT2026-10-07/backfill, не снимать ML gate.
[Итог D51](../docs/f01-live-qualification.md),
[final evidence](../docs/evidence/f01-live-result-2026-10-04.json),
[owner attestation](../docs/evidence/f01-d51-owner-attestation-2026-10-04.json).
Предыдущий blocked snapshot сохранён в [prior evidence](../docs/evidence/f01-live-qualification-2026-10-04.json).

### Принятый D50: предыдущее состояние


**D50: engineering runtime envelope завершён; activation остаётся BLOCKED.**
Core main; отдельная control-center ветка не менялась в рамкахD50. Новый memecoin_operational
сохраняет source hashes D43–D49 и F01/D48 rules. Реальные subprocess210s/segment,
600s от durable reservation, process-group kill, orphan lease EOF, per-RPC code/budget/
credential/STOP/disk/clock guards; due-only/no retry/8failed semantics прежние.

Итоговый staged bundle `work/collector-d50/runtime-candidate-final`, SHA
`a06f1d69ff8fb1c0f8647fc5622c3e2d8c1811e697d40eb8be15deb1663da880`.
Plist проверен, service не установлен. Receipt не выпущен; закрытый service invocation
не создал source root. Runtime-пакет пригоден для следующей bounded квалификации,
не evidence awake uptime84days. Disk guard2GiB+512MiB — stop floor, не FS reservation.

15new/496base/517combined tests,ruff/mypy/smoke/build; final180fakeRPC через4процесса→D48
admission→raw/dossier reproduction; installed wheel exact. Всего540fake calls
заD50 с preliminary и final, marketRPC0, researchtraining0. Исправлены post-child
и reservation-origin deadline границы; prior artifacts сохранены, final pin новый.
MCP usage27 660CU/USD0 по-прежнему не даёт plan/hard cap/mapping; browser недоступен.
**Full180RPC live не проведён**, account budget review отсутствует. Actual F01 windows0,
D43/holdout не открывались/не менялись, source root не создан; D49 candidate валиден.

**Следующий D51:** substantive account no-charge/mapping evidence, отдельный bounded
receipt и один180RPC live qualification; условия host awake/login/disk monitoring;
после evidence — отдельное итоговое calendar activation decision. Не возвращаться к
transport tuning, не сдвигатьT/backfill и не начинать настоящее F01 обучение.
[Итог D50](../docs/f01-operational-runtime.md),
[evidence](../docs/evidence/f01-operational-runtime-2026-10-03.json).


**D49 завершён как qualification; verdict NOT READY FOR ACTIVATION.**
Реальный Alchemy runtime HTTP:6/6 strict finalized/profile2 checkpoints3pools, Clock age
9,95–10,77s, nominal120CU. MCP usage27 540→27 660CU/$0,00; plan tier/hard monetary cap и
exact credential→billing account mapping не подтверждены. Public budget1 209 600CU,
max432 000CU/month не разрешает расходы. Paid budget$0, full180RPC live window не проведено.
Billing UI недоступен: browser runtime no browser, список пуст; account plan/limit нельзя
подтвердить имеющимися admin tools. Нужен read-only billing evidence, не ключ в чате.

Новый isolated memecoin_collector: due-only durable scheduler, crash full180reservation,
no retry/catchup,8failure stop, window adapter, staged exact activation candidate.
16new/481base/502combined tests, strict checks/smoke/build;180fake full raw integration;
installed wheel reproduced live probe/raw/dossiers. Actual F01 windows0,source root не
создан, service не установлен. D43/F01/holdout не читались/не менялись. Protocol sources
D43–D48 совпадают с577954c; D43 operational inventory по-прежнему last published.

Space135,67GB; empirical source+registry raw projection2,65GB vs100,8GB max response
ceiling без metadata. Storage reservation, host uptime и совместный production supervisor
210s/600s ещё не квалифицированы. Candidate work/collector-d49/activation-candidate
имеет все permissions false; exact SHA/pins и remaining blockers в отчёте.
**Следующий D50:** account-specific no-charge/budget proof, production service envelope/
disk/runtime checks и bounded180RPC live qualification после no-charge proof; затем
отдельное activation decision. Frozen calendar не сдвигать и не backfill.
[Итог D49](../docs/f01-collector-qualification.md),
[evidence](../docs/evidence/f01-collector-qualification-2026-10-03.json).


**D48 завершён: future sampling/source contract, sealed adapter и readiness dossier.**
Новый stdlib `memecoin_sampling` сохраняет D43–D47 sources/F01-v1. Immutable proposal
`work/sampling-d48/proposal`:T2026-10-07 00:00UTC, development до2026-12-02, reserved
holdout до2026-12-30.336 окон по4×5min/день,224development/112holdout, ceiling60480RPC.
Это **не activation/разрешённый расход**: новый source root/service не создан, network0.

Пустой actual registry/dossier:0admitted windows,111blockers из113numeric checks,
collection/research/release false. Existing D45 development174decisions не переименован
в новый corpus. Adapter сохраняет raw+converted objects, whole-window temporal/semantic
checks, append-only revisions, fixed planned denominator и unknown-start lower bounds.
Bound source paths/holdout индексы отклоняются до raw reads; synthetic data_kind закрывает
prospective gate. Source timestamps/targets/CPMM не меняются.

20new/465base+2optional skips/486combined tests,ruff/mypy/smoke/build, exact installed-wheel
raw/dossier reproduction. Full raw synthetic fixture180fake calls,real RPC0,training0;
предыдущий успешный180fake и failed artifacts сохранены. Capacity shape fixture224copies:
38976decisions,188MB JSON,peak RSS744MB/load+validate+partition1.424s; не fit budget/power.
D45/D46 bindings verified; D43 paths не открывались аудитами, runtime не менялся,
operational inventory не обновлялся. [Итог D48](../docs/f01-sampling.md),
[evidence](../docs/evidence/f01-sampling-2026-10-03.json).

**Следующий D49:** bounded collector/activation qualification для concrete proposal:
source freshness/coherence, provider quota/monetary/storage/runtime feasibility,
immutable activation binding и due-only/failure-stop/restart conformance. Затем отдельное
явное решение о запуске конкретного бюджета, без изменения D43/inherited holdout.
Не auto launch из наличия plan; не F01 training. При опоздании activation новый binding,
не backfill и не изменение старых dates. Rare-class support/actual compute остаются gates.

**D47 завершён: runner/ledger/scoring foundation frozen F01.**
Отдельный `memecoin_runner` сохраняет D43–D46 sources/bindings. Полный fold/selection/
refit graph, CPU trainer, immutable trial/exposure chain, provenance-bound validation,
paired UTC-day scoring/bootstrap, отдельные pinned admission/release receipts.
Реальные research fits0,admissions0,holdout releases0,RPC0. Research gate закрыт.

Final engineering runs: synthetic27fits и D45 development27fits,2epochs,seeds7/19/41,
9configs; это smoke, не качество модели. Partitions real78/17/30,purged49.18new tests,
445base+2optional skips,466combined;ruff/mypy/smoke/build и exact installed-wheel
score/bootstrap reproduction прошли. Evidence хранит ledger heads и artifact hashes.
Дополнительный54fit повтор после integrity-fix bbcd0e8 дал те же score hashes;
всего135 bounded smoke fits. Прежние artifacts/static logs сохранены. Stale wheel
при параллельной сборке обнаружен SHA guard; fresh wheel reproduction успешен. D43 paths не
открывались audit-hook проверками; D43 runtime не менялся, operational status не обновлён.
[Итог D47](../docs/f01-runner.md), [финальное evidence](../docs/evidence/f01-runner-integrity-2026-10-03.json).

**На момент D47 следующим был D48 (теперь выполнен выше):** future sampling/source binding и qualified sealed
prospective development adapter/readiness dossier для F01. Не менять D43/D45 и не
читать inherited holdout. Для actual admission всё ещё нужны достаточный corpus,
linked substantive reviews и RAM/compute feasibility. Receipt hashes проверяют bytes,
не заменяют review. Crash conservatively consumes reservations; raw holdout retry
после consumption закрыт до incident review. Новый collection budget не следует
автоматически из frozen84day design. D43/D45 counts ниже — last published inventory.

**D46 завершён: frozen F01 research specification и readiness framework.**
F01-v1 — conditional forecast skill только fixed3pool cohort, не net-edge/реальные fills.
Frozen9candidates×3seeds,validation-only selection,≤114fits; отдельный будущий84day
календарный дизайн. Это не новый collector и не автоматическое продление D43/D45.
Существующий finite D45 corpus не может удовлетворить F01. Reserved holdout4/5 сохранён,
не прочитан и не переименован. D46 assessment открывал только D45 development artifacts.

Readiness разделён: conditional semantics/scope/support для forecast admission; полные
costs/admission/risk/MPC/net evaluation для economic claims остаются отдельными gates.
Текущее assessment revision000000:design=false,launch=false,release=false. Даже positive
synthetic design/reviews не выдаёт executable permission. D44 trainer smoke-only без правок.
15 новых/434base/448combined tests,ruff/mypy/smoke/build; exact wheel assessment.
D44/D45 source identities неизменны. Реальных research fits0,holdout releases0.
[Итог D46](../docs/research-readiness.md), [evidence](../docs/evidence/research-readiness-2026-10-03.json).

**На момент завершения D46 следующим был D47 (теперь выполнен выше):** admitted
fold-aware runner conformance, immutable
trial/exposure ledger, frozen scoring/bootstrap, consumed budgets и resume, отдельные
admission/release receipts. Только fixtures/smoke до readiness; no new collection или
holdout reveal. Новому F01 corpus потребуется отдельный future sampling binding,
который не изменяет действующий D43. Числа D43/D45 ниже — last published inventory;
операционный D43 inventory в D46 не обновлялся, содержимое reserved окон не читалось.


**D45 завершён: immutable registry и frozen global folds готовы к новым D43 окнам.**
Root `work/corpus-registry-01`, revision000000:1 development/5 pending,180 starts,
174 usable decisions/1392 scenarios,122 all-scenarios-observed,5 greedy nonoverlap
(не independent samples),1 UTC day/hour. Оба folds пока train174/validation0/test0.
Readiness false; counts сами его не снимают. Holdout окна4/5 закреплены заранее,
labels не экспортируются; пересекающие holdout окна quarantine целиком.

17 registry tests,419 base/433 combined tests,ruff/mypy/smoke/build прошли. Idempotent
resync=no_change, exact offline audit/fold export из wheel.70 protected D43 hashes и
D44 source identity не изменены. Research training этого этапа не запускался.
[Итог D45](../docs/corpus-registry.md), [evidence](../docs/evidence/corpus-registry-2026-10-03.json).

Routine D45 status: `tools/corpus_registry.py sync/audit`, без пересоздания root.
Full D43 inventory может раскрыть holdout labels; не использовать его для model selection.

**D44 завершён: независимый ML/training foundation принят для engineering smoke.**
Optional `memecoin_ml` и `work/ml-venv` (Python3.13/PyTorch2.14.1), immutable causal
sequences, grouped maturity purge, train-only scaler, empirical+linear baselines,
MLP/GRU/TCN/Transformer, conditional quantile/status heads, trainer/checkpoint/resume/
label-free inference. D43 plan/runtime/LaunchAgent не изменены:70 protected hashes равны.
15 CPU smoke runs +1 MPS training прошли;40 CPU/MPS benchmark cases и numerical
prediction/gradient parity пяти кандидатов прошли. Exact CPU epoch resume и installed-
wheel real-data reproduction подтверждены.402 base /17 ML /416 combined tests,
ruff/mypy/smoke/build прошли; remote CI пока не проверен.

Это не real ML result: real smoke train87/validation17/test45 decisions,purged25;
validation содержит только roundtrip, test180/360 variants censored. Engineering
CPU default, GRU2174 params — первый temporal candidate; рыночный backbone не выбран.
[Итог D44](../docs/ml-foundation.md), [evidence](../docs/evidence/ml-foundation-2026-10-03.json).

**D43 активно: конечное 48-часовое расписание установлено, первое окно выполнено.**
Local LaunchAgent `ai.memecoin.d43.calendar01`, immutable plan/runtime в
`work/calendar-execution-01`. Окна 0/6/12/24/36/48 h; максимум1 152 RPC,
первый failure закрывает окно, два failed windows — весь план. Sleep/missed не catch-up.
Не пересоздавать и не запускать второй collector поверх существующего расписания.

Первое окно 2026-10-03 10:54–10:59 Europe/Moscow:180/180 strict snapshots,174 usable,
135 mature decisions (67 evaluation),5 chronological nonoverlap windows. Execution:
12 RPC,6 observed transactions, fees15 001–154 999 lamports; все6 already finalized
при первом poll, timing left-censored. Submission/inclusion latency, own admission,
полные strategy costs/FX/rent/impact и net PnL остаются unknown. Это empirical evidence,
не калибровка собственных сделок. Ошибок сети0;399 tests/check/smoke/build и exact
reproduction dev/frozen wheel прошли.
[Документ D43](../docs/calendar-execution.md),
[evidence на первое окно](../docs/evidence/calendar-execution-2026-10-03.json).

**Следующий шаг потока D43:** read-only D45 sync/audit активного расписания; следующие окна
03 октября16:53/22:53,04 октября10:53/22:53,05 октября10:53 (Москва),5 pending.
Затем audit реальных часов/дней, gaps/full-grid support и отдельно execution evidence.
Будущие окна не считать состоявшимися. На Mac нужен активный user session/доступ;
пропуски остаются missing. STOP и команды управления — в документе D43. Job не делает
автоматических Git/Memory updates. Transport tuning закрыт, WSS off; market ML не начинается, engineering smoke разрешён D44.

**D42 завершён с сохранённым отклонением календарного guard.** Все 4 cold connections
и 8 strict checkpoints прошли. DNS/TCP/TLS наблюдались без изменения транспорта;
причина прежнего D41 timeout остаётся неизвестной. По frozen правилу выбран Alchemy
HTTP persistent TLS внутри сегмента, новый процесс на его границе; WSS выключен.
Два prospective окна: 360 accepted snapshots, 348 usable rows, 268 mature decisions
(135 engineering evaluation), 12 chronological nonoverlap windows. Это минуты одной
сессии, не разные рыночные режимы.

Создания планов разнесены на 599,994084 s вместо минимум 600 s: недобор 5,916 ms из-за
ошибки anchor runner/first campaign. Фактические старты сегментов разнесены на
600,037526 s, но strict creation-boundary criterion оставлен false. Исправлен только
будущий scheduler; исходные times не изменены, повторного сбора нет.
Offline/dev/installed-wheel reproduction и 386 tests/check/smoke/build прошли.
[Итог D42](../docs/cold-connection-corpus.md),
[evidence](../docs/evidence/cold-connection-corpus-2026-10-03.json).

**Текущий приоритет:** transport tuning закрыт; накапливать новые заранее заданные окна
в разные часы/дни с прежними guards, затем read-only execution-cost/availability evidence.
Offline sensitivity0/5/10/25/50/100bps и paired delay0/5s реализованы; это assumptions,
не calibrated costs/inclusion. Conditional failures/censoring не zero-fill; net_pnl=null.
Нет market ML/RL/paper/live, подписи, оплаты или расширения protocol scope.
Engineering smoke разрешён D44; SLA не объявлен.

**D40-v2 завершён; WSS оставлен опциональной диагностикой, выключен по умолчанию.**
Исправлены absolute scheduler/content coalescing. Matched10s ticks и общая feature grid
проверены; 421 strict Alchemy snapshots/144 public controls, два120s steady/stress окна,
1274 WSS messages. Coalescing подавил198 unchanged hints без удаления raw. Adaptive
HTTP−31,94%, usable−4,17п.п., payload+28,49% кHTTP5: criterion false. Initial v2 startup
EOF после4 ack сохранён; один preregistered serial-ack follow-up прошёл. Причина EOF
не установлена. [Отчёт](../docs/alchemy-pilot-v2.md),
[evidence](../docs/evidence/alchemy-pilot-v2-2026-10-03.json).

**D41 начат и закрыт на первом отказе:** Alchemy HTTP5 persistent TLS через прежний
collector, WSS off, план2×30×5s/max180 requests. Фактически1 попытка, connect timeout
15,0718s, HTTP=null, snapshots0. Повторов/второго сегмента нет. Offline/dev/wheel QA:
3 masked rows,24 censored scenarios,0 mature/nonoverlap. Это unresolved cold-connection
availability, не выявленная ошибка credentials/account coherence. Missing access не
установлен. [Итог D41](../docs/qualified-http-corpus.md),
[evidence](../docs/evidence/qualified-http-corpus-2026-10-03.json).

После D41 был назначен cold-connection audit; он выполнен в D42 выше. Закрытый
D41 не возобновлялся. Public D39 остаётся reference/control, hidden fallback нет.

377 tests, lint/format/mypy, smoke/build; D40v2 и D41 точно воспроизведены dev/wheel.
CPMM/features/targets, profile2/legacy boundaries и synthetic-only gate не менялись.
История D40-v1 (366 strict snapshots, отрицательный результат и scheduler confound)
сохранена в [первом отчёте](../docs/alchemy-pilot.md); D40-v2 заменяет его следующий шаг.

**Текущий результат D39:** найден и проверен bounded вариант согласованного источника:
canonical public mainnet `https://api.mainnet.solana.com`, explicit verified TLS keepalive
внутри сегмента, новый процесс/соединение на его границе. Account/Clock/temporal checks
не ослаблены. Некоторые прежние timeout локализованы до HTTP на connect/TLS; полная
причина всех отказов неизвестна. User-Agent сам по себе проблему не устранил.

**482 новых accepted snapshots  / 483 corpus requests**, 483 feature rows, 464 usable,
781.187 s суммарного segment coverage. Первая кампания 302 valid остановилась на
connection error четвёртого сегмента; вторая 180/180 завершена с реальным process restart.
Все failed/censored artifacts сохранены. Helius Clock mismatch unresolved; QuickNode
не использован, D33 whole-chain historical audit остаётся закрытым.

Execution-conditioned targets: 3864 scenarios, 345 distinct mature decisions,
141 engineering evaluation. Все восемь scenarios созрели в одной partition у 307
решений; chronological nonoverlap оставляет 15 окон по двум кампаниям. Это не
независимые samples и не final holdout. Net PnL неизвестен, ML/RL/paper/live не запускались.

346 tests, lint/format/mypy, smoke/build и dev/installed-wheel verification прошли.
Memory и previous raw/source snapshots сохранены. Контракт: [persistent source](../docs/persistent-source.md);
[evidence](../docs/evidence/persistent-source-2026-10-02.json). Последующий transport выбор уточнён D40-v2/D41 выше; execution QA
и dataset sufficiency остаются необходимыми. Public RPC не объявлен production source.

## Стадия и авторизация

Memory Bank принят как source of truth. Пользователь разрешил автономный архитектурный проход: реализацию, тесты, локальные инструменты, коммиты и push в существующий `origin/main` при доступной аутентификации. Выполнены утверждённые bounded forward collection/recovery pilot и этапы протокольной реконструкции CPMM и ограниченной поддержки Token-2022. Реальные деньги, торговые транзакции и оплата внешних сервисов не разрешены.

Локальный инженерный фундамент реализован. Это не прохождение рыночных gates R2–R8: реальный источник/сегмент ещё не квалифицирован, edge не установлен. Git и факт отправки проверять непосредственно через историю и remote, а не по вручную дублируемым hash.

## Что реально существует

- Python 3.11+ package и CLI; runtime на stdlib. Dev tools закреплены в lock-файле.
- Неизменяемые нормализованные события, observed/assumed/unknown availability, raw provenance.
- SQLite append/dedup с rollback конфликтов, content-verified bundles и аудит ограничений.
- Availability-gated observations, causal reference features и отдельные targets/censoring.
- Synthetic dual-clock replay, integer CPMM reference, задержки, slippage/failure/drop, reservations, fees, risk и target-to-intent conversion.
- Liquidation marks с сохранением непроданных позиций и явными unpriced assets.
- Purged walk-forward, mature-label guard, train-only standardizer, empirical distribution baseline, pinball и block bootstrap.
- Run config/provenance/source snapshot, completed/failed lifecycle, checksums и evidence blockers для promotion.
- Read-only RPC capture/probe и bounded CPMM qualification: реальные v0/v1 транзакции, pinned account decoder, structural Token-2022 inventory, offline reproduction и сверка vault flows.
- Bounded forward collector фиксированной когорты: SQLite/WAL journal, persisted cursors, recovery, block ordering, vault continuity audit и переносимые snapshots.
- Allowlisted Token-2022 checkpoints с Clock, transfer-fee gross/net math и отдельный withheld ledger с harvest-to-mint.
- Make tooling, тесты и CI definition для Python 3.11/3.13. Состояние удалённого CI не следует путать с локальной проверкой.

Команды и карта модулей: [README](../README.md). Точные ограничения: [contracts](../docs/contracts.md). Проверка источника: [source audit](../docs/source-audit.md).

## Что проверено

Локально на Python 3.13: 399 автоматических теста, Ruff lint/format, strict mypy, полный CLI smoke с повторным ingest/export и двумя побитно одинаковыми replay results, сборка sdist/wheel. Synthetic fixture имеет отрицательный net-outcome, cash сохраняет капитал. Эти тесты проверяют бухгалтерию и временную семантику, не рыночную прибыльность.

## Чего нет и чего нельзя утверждать

Нет qualified historical/live market dataset, полной исторической account-state reconstruction, production collector, empirically calibrated execution, neural training, MPC/RL, paper/live bot и доказанного OOS edge. Ни один рыночный E01–E14 не проведён. Эмпирический forecast baseline тестировался только как reference implementation.

ReferenceReplay намеренно synthetic-only, one-quote, one-pool-per-base и serial-pending. Snapshot replay имеет контрфактуальное ограничение own impact; per-order/cumulative budgets не устраняют его. Данные пока обрабатываются in-memory; полного Solana throughput benchmark нет. RPC receipt сегодня не является временем доступности исторического события.

## Открытые решения

Backbone, размер, temporal format, scanner/K, cross-token/graph, targets/horizons/frequency, sizing, MPC/RL, источники, капитал, primary currency и реальные risk/promotion thresholds остаются открытыми. Числа `configs/synthetic.toml` — инженерный fixture, не утверждённые финансовые параметры. Актуальный реестр D01–D43: [decisions](decisions.md).

## Результат bounded data-аудита

На mainnet полностью получен финальный образец 20/20 транзакций: 4 pool inventories,
2 strict reserve checkpoints, 2 совпавшие vault-flow сверки одной транзакции по двум пулам.
Ещё 2 пула отклонены строгим decoder из-за Token-2022. Контроль SOL/USDC: 5/5 транзакций,
1 strict checkpoint, 2 совпавшие сверки двух транзакций. Контрольный pool не мемкоин;
классификация токенов основной выборки не подтверждена. Это не полная историческая
реконструкция, не независимая проверка провайдера и не swap-price calibration.

Публичный Raydium каталог ответил 403; read-only mainnet RPC пригоден для bounded audit.
Выявленная ошибка RPC версии транзакций исправлена после проверки актуальных docs.
Решение: CPMM — первая семья data adapter; legacy-only ограничение не распространять
на всю наблюдаемую вселенную. История отказов сохранена. Подробности и точные evidence
в [source audit](../docs/source-audit.md); компактный реальный regression fixture в Git.

## Результат forward pilot

Сбор на фиксированной когорте двух ранее известных пулов: 10 finalized checkpoints за
211.299 секунды receipt span, 8 интервалов (2 непустых), 189 транзакций в 15 блоках.
Из них 46 успешных, 143 failed; v0/v1 сохранены. После restart/backfill все 24 address
scans завершены, pending jobs нет, промежуточные balances и конечные residuals совпали
во всех интервалах. Перед дозагрузкой gap-open audit не проходил; endpoints не менялись.

56 записей об ошибках/остановках сохранены, включая один измеренный HTTP 429. Начальные
маленькие страницы и исправленная ошибка CLI привели к лишним запросам: это не throughput
benchmark. Журнал и ~8.7 MB архив вне Git; summary/hashes в docs/evidence/forward-2026-10-01.json.
Архив повторно проверен без сети из development package и установленного wheel. Повторное
recovery завершённого журнала не отправило запросов. Подробности — [forward pilot](../docs/forward-pilot.md).

На момент завершения forward pilot реконструировались только classic SPL vault amounts.
Следующий протокольный этап расширил эту проверку — см. ниже. Полнота индекса
условна относительно одного провайдера; birth/death universe и классификация memecoins
не доказаны. Synthetic execution gate и market_dataset_qualified=false сохранены.

## Результат протокольного этапа

Реализованы `cpmm.py` (integer transitions) и `protocol.py`/`audit-protocol` (CPI/event/
transfer/endpoint verification). На прежнем immutable журнале: 44 swap — 42 exact-input,
2 exact-output; 143 failed без изменения состояния; 2 успешных no-op маршрута. Все суммы
swap совпали до atomic unit; экономическое состояние совпало во всех 8 интервалах.
Новая версия не меняет старый checkpoint schema и не подгоняет состояния по endpoints.

126 тестов, lint/format/strict typing, synthetic smoke и package build прошли. Повторный
аудит, включая установленный wheel, побитово идентичен; прежний frozen pilot также
проверен повторно. Компактные реальные regression captures сохранены в Git; полный
журнал, source captures и отчёты вне Git, hashes/summary — docs/evidence.

На момент завершения classic этапа deposit/withdraw/collections и creator fee были
подтверждены только инженерными тестами; creator fee проверена в следующем этапе ниже. Дополнительная диагностическая выборка 24 mainnet tx
нашла creator-fee swaps; все четыре проверенных кандидата используют Token-2022 и не
допущены strict classic adapter. Полная source completeness, config-history, binary
equivalence, oracle/Clock, latency/inclusion/network costs остаются неизвестными.
Полная execution qualification, market dataset и edge не объявлены.
Детали, команды и provenance: [protocol audit](../docs/protocol-audit.md).

## Результат Token-2022 этапа

Whitelist mint/vault extensions реализован отдельно от inventory. Schema 2 явно
выбирается при создании pilot и включает Clock того же bank. В новом mainnet pilot:
9 snapshots трёх пулов за 52.948 секунды, 382 транзакции в 88 блоках, 6 интервалов.
132 exact-input swap с creator fee совпали до atomic unit; 58 входящих и 65 исходящих
transfer fees проверены. 8 harvest-to-mint восстановлены отдельно от spendable reserves.
Все конечные economic/withheld состояния совпали, pending jobs нет.

Реальные harvest показали неполноту первоначальной модели «только начислять withheld»;
переход добавлен по pinned source и реальным traces. Публичный RPC дал 19 HTTP 429 и
один transport SourceError: история сохранена, recovery завершён после замедления.
Новый collector добавляет cooldown и увеличивает spacing после 429 без скрытого retry.

160 тестов, lint/format, strict typing, smoke, build, old archive regression и одинаковые
dev/wheel protocol reports прошли. ~22.6 MB frozen journal вне Git; компактные реальные
fixtures и summary/hashes сохранены. Подробности: [Token-2022](../docs/token2022.md).

Mainnet-подтверждения exact-output с Token-2022, LP changes, pool fee collection и
прямого withdraw withheld ещё нет. Epoch-boundary и mint/config изменения блокируют
интервал; полная mint-wide сумма withheld не реконструируется. У одного из трёх пулов
не было успешных swaps: его snapshots не расширяют доказанное покрытие quote math.
Qualified dataset, полное execution и edge не заявлены; synthetic gate сохранён.

## Результат истории параметров

Реализованы opt-in config/mint dependency history, epoch schedule с Clock anchors,
ordered config/transfer fee/supply mutations и отдельные event-conditioned LP witnesses.
Источник показал дорогую пагинацию общего config: добавлены parsed full blocks и
альтернативное bounded-range coverage, без подмены raw captures или старых cursors.

На mainnet два новых интервала по 12 слотов: 455 + 863 связанных tx, economic/withheld/
config/mint endpoints совпали. У самих выбранных pools успешных операций не было;
это не новые quote calibration observations. 13 отдельно найденных LP witnesses
совпали по arithmetic/transfers, но их prestate взят из events. Один реальный внешний
withdraw-withheld сохранён как regression без притязания на полный mint ledger.

Смена config/tariff и граница эпох проверены инженерно, но не наблюдались в новых
mainnet интервалах. LP между anchors, pool fee collections, прямые withdrawals в/из
tracked vault, независимая полнота и deployed binary equivalence остаются gates.
192 tests, lint/typing, smoke/build, offline frozen verification и побитово равные
dev/wheel range reports прошли; старые classic/Token-2022 audits также прошли.
E01–E14 не проведены.
Документ и evidence: [parameter history](../docs/parameter-history.md).

## Результат проверки второго провайдера и времени получения

Реализованы exact-request cross-provider comparison, bounded paired Clock sampler,
sealed raw/measurement bundles с offline reproduction и collection-transfer witnesses.
Независимая сверка **не состоялась**: PublicNode вернул 403, OnFinality — 429;
formal plans сохранены полностью, остановлены после первого отказа. dRPC также дал
403 в preflight. Смена alias не считается доказательством независимой инфраструктуры.

Изолированный реальный run: 12 пар confirmed/finalized, 24 запроса, 0 ошибок.
Response p50 253.726 / 249.244 ms; receipt minus chain Clock p50 1.217 / 9.718 s.
Это read-side measurements с ограничением часов/выборки, не inclusion/fill latency.
Нельзя присвоить historical finalized данным доступность через 250 ms.

Целевой поиск fee authorities дал 3 collect_fund_fee: CPI/metadata/limits совпали,
наблюдены Token-2022 deductions. Старые fee counters и historical tariffs неизвестны;
полная checkpoint-anchored collection реконструкция не объявлена. Protocol/creator
collections, config/tariff updates и epoch boundary по-прежнему не подтверждены.
209 tests, lint/format/typing, smoke/build и dev/wheel offline reproduction прошли.
Подробности и ограничения: [provider audit](../docs/provider-audit.md).

## Повторный запуск с настроенным пользователем RPC

2026-10-02 пользователь сообщил о настройке `MEMECOIN_SECONDARY_RPC_URL`.
В текущем процессе Codex переменная отсутствует; `.env`/`.env.local` в репозитории
не найдены, `.env.example` содержит пустое значение. CLI preflight в 09:33:53 UTC
завершился с exit 2: `RPC endpoint environment variable is not set`.
Сетевых запросов к новому RPC не было; его доступность не проверена. Запрошено место
настройки без передачи секрета в чат. CLI не загружает `.env` автоматически.
Локальный журнал: `work/secondary-env-preflight-20261002T093353Z.json`.

## QuickNode: доступ восстановлен, range-запрос отклонён

После уточнения пользователя `.env.local` прочитан явно без выполнения shell-кода;
секрет не выведен и не сохранён в Git. `getSlot(finalized)` у QuickNode прошёл.
Первый exact-request `getBlocks(452408962,452408973,finalized)` получил HTTP 413.
План: 13 запросов; отправлен 1, остальные 12 остановлены. JSON request — 103 bytes,
поэтому причина 413 не объявляется установленной по одному HTTP status. Тело HTTP
ошибки текущий transport не сохраняет. Пользователь уведомлён сразу; дальнейшие RPC
запросы остановлены по его условию. Независимого agreement пока нет.

Crosscheck теперь останавливает серию при любой SourceError/ValidationError, а не
только HTTP 401/403/429. 212 tests и lint/format/typing прошли. Raw references и failed
plan сохраняются, offline verification проходит. Evidence:
[secondary access](../docs/evidence/secondary-access-2026-10-02.json).

## Разбиение по лимиту QuickNode Discover

Пользователь уточнил причину прежнего HTTP 413: предел getBlocks — 5 слотов;
разрешил разбить прежний диапазон без upgrade. Реализованы inclusive partition,
проверка объединения raw ответов против исходного capture и отдельная проверка
покрытия full blocks с ancestry. Никаких синтетических RPC captures при объединении.

Новый фиксированный план: 452408962–966 / 967–971 / 972–973. Первый запрос завершился
SourceError без HTTP status и без полученного response; оставшиеся части и getBlock
не запрашивались. Причина транспорта неизвестна (старый клиент её не сохранял).
Пользователь уведомлён сразу; новый failed bundle не заменяет прежний HTTP 413.
Добавлены безопасные категории transport errors для будущих запусков; задним числом
этому сбою timeout/DNS/TLS не приписаны. Mainnet agreement по-прежнему не получено.

227 tests, lint/format/typing, smoke/build и offline verification установленным wheel
прошли. Evidence: [chunked range](../docs/evidence/chunked-range-2026-10-02.json).

## Диагностика QuickNode, 2026-10-02

Sandbox дал DNS failure, разрешённая сеть вернула первую часть. MCP инструменты
доступны, но Console API/Tooling Access получили 401 из-за тарифа. Прямой RPC работает
перемежающе: новый 30s plan остановился на timeout; 60s plan получил все 12 slots
и три совпавших full blocks, четвёртый getBlock получил timeout. Остальные запросы
остановлены. Причина старого SourceError неизвестна; увеличение timeout не доказано
как исправление нестабильности. CLI получил явный `--timeout` (0,60], default 10s.
227 tests, lint/typing и smoke прошли; три новых bundles проверены offline.
[Evidence](../docs/evidence/quicknode-diagnostic-2026-10-02.json).

## Первый preflight Helius (до настройки endpoint)

По новому поручению пользователь выбрал **Helius вместо QuickNode** для следующей
сверки. Новых QuickNode запросов не делать; его evidence и общие RPC-интерфейсы сохраняются.

Helius MCP документация ответила, но `getNetworkStatus(finalized)` вернул timeout
внутреннего getSupply (30000 ms); HTTP/RPC code неизвестны. Wall time MCP 288.369 s
не является HTTP latency прямого RPC. Серия остановлена. В schema есть getBlock,
но нет getBlocks dispatcher; успешные chain reads не подтверждены. Helius runtime
endpoint/key в проекте/окружении не найден. Сверено через Helius **0/12 блоков**.

Следующий шаг — локальная настройка `MEMECOIN_HELIUS_RPC_URL` через авторизованный
доступ, затем mainnet genesis/finalized preflight и сохранённый bounded план максимум
16 запросов, getBlocks одним диапазоном из 12 slots. CLI dotenv не загружает.
Secrets в чат/Git не передавать; upgrade не разрешён.

Offline проверены 12 reference blocks / 14 809 tx, checksums/metadata/ancestry;
SQLite frozen journal цел. Новые сравнения schema 2 сохраняют все поля block/tx
result и отклоняют неполные metadata; legacy bundles воспроизводятся schema 1.
Три прежних QuickNode blocks совпали также по schema 2 без новых запросов.
239 tests, lint/format/typing, smoke и build прошли. Исторические ML E01–E14 не
проведены, data/execution qualification не повышена. Детали, безопасный лог и план:
[Helius audit](../docs/helius-audit.md), [evidence](../docs/evidence/helius-preflight-2026-10-02.json).

## Direct Helius: доступ подтверждён, обнаружена неполнота traces

2026-10-02 после настройки пользователем MEMECOIN_HELIUS_RPC_URL выполнен
новый immutable read-only plan. Sandbox getGenesisHash дал dns failure; вне sandbox
mainnet genesis/version/finalized slot подтверждены. Enumeration 452408962–452408973
совпал 12/12. Первый full block получен за 5.373 s, 10 011 629 bytes, 1 310 tx.
Identity/order и все поля кроме двух logMessages arrays совпали; 1 308 tx совпали
целиком. У Helius tx indices 433/743 имеют Log truncated. Полных block matches 0/1,
11 остальных блоков не запрошены, range agreement false.

Один отдельный getTransaction для tx 433 вернул тот же обрезанный trace; его
meta.rewards=[] отличается от block null и также сохранён. HTTP 200 у всех шести
запросов вне sandbox; всего 10 056 361 payload bytes. Это ограниченные response-time
наблюдения, не sustained throughput/chain age/execution latency. После выявленного
metadata blocker сеть остановлена; причинный механизм/настройка Helius неизвестны.

Comparison schema 3 обнаруживает marker, сохраняет mismatch и выдаёт bounded
JSON-pointer diagnostics; одинаковая обрезка даёт unavailable. Старые schema 1/2
воспроизводятся. Добавлены offline compare-rpc-captures и неизменённый реальный
getTransaction regression. 244 tests, lint/format/strict typing, smoke и sdist/wheel build прошли.
Отчёт и raw hashes: [Helius audit](../docs/helius-audit.md),
[evidence](../docs/evidence/helius-direct-2026-10-02.json).

## Следующий ограниченный шаг на предыдущем этапе (заменён D32 ниже)

Установить доступный способ получать у Helius полные исторические logs на уже
известной signature; до этого не повторять успешно полученные данные и не начинать
синхронный сбор. Если такого пути нет, этот доступ не подходит для полной trace
qualification. Не удалять logs из сравнения и не подставлять primary tail, чтобы
получить agreement. Mainnet доступ подтверждён, но market dataset, execution,
инфраструктурная независимость и edge не квалифицированы. QuickNode для новых
запросов не используется; прежние archives остаются. ML/RL/trading не начинались.

## Актуальный результат: historical trace gate и D32

2026-10-02: дополнительные getTransaction(json) и getTransactionsForAddress(full)
для **обеих** проблемных signatures получили HTTP 200, но те же обрезанные logs.
Полных traces восстановлено 0/2. Helius untruncated LaserStream документирован,
но historical replay там не поддержан. Проверенный доступ **непригоден для полной
исторической trace-сверки**; это не запрет использовать его для иных проверок данных.

По новому поручению выполнен следующий ограниченный шаг: scope diagnostic на заранее
заданном cohort и один новый full block 452408963 — первый с potential write в шесть
счетов frozen cohort. Все 1 154 tx/metadata совпали; parent ancestry подтверждена.
Общий результат: **12/12 slots**, **1/2 full blocks match**, **2 462/2 464 tx exact**;
10 blocks ещё не получены, full-range agreement false. В первом блоке cohort writers
вообще нет; readonly config mismatch не удалён и не назван pass.

Синхронная finalized пара семи account reads остановлена: Helius slot 452651827
ответил (0.318 s), public RPC timeout 20.069 s, HTTP/RPC code неизвестны. Сравнения
счетов одного bank не было. Retries/upgrade/QuickNode requests не выполнялись.

Код: общая offline diagnose_block_account_scope, 8 новых tests; strict comparison
и replay trace gates сохранены. **252 tests**, lint/format/typing, smoke/build прошли;
raw manifests проверены, новые comparisons воспроизведены offline. Отчёт:
[Helius audit](../docs/helius-audit.md), [evidence](../docs/evidence/helius-traces-2026-10-02.json).

**Далее:** bounded независимая сверка полной истории записей frozen cohort на оставшихся
10 блоках (включая failed/unsupported), с отдельным строгим full-block результатом.
Не отбирать только удобные traces. Перед новой синхронной парой проверить доступность
public RPC; текущий snapshot не восстанавливает исторический bank. Для полного tail
двух исключений нужен другой подтверждённый архив. Dataset/execution/infrastructure
independence и edge всё ещё не квалифицированы; ML/RL/trading не начинались.


## Результат D34: economic dataset построен

Вместо дальнейшей RPC-сверки реализованы offline `export-economic-dataset` и
`verify-economic-dataset`: SQLite snapshot + source/config/hashes + states/quotes/report.
Состояния экспортируются непосредственно existing protocol replay; fees, counters,
LP supply, withheld, epoch и порядок tx сохраняются. Failed/no-op не удаляются.
Любой blocker карантинирует интервал целиком, без endpoint repair. Quote probes
с Token-2022 используют isolated hypothetical state и проверяют immediate round trip;
ReferenceReplay по-прежнему synthetic-only, ML/RL/paper/live не запускались.

Четыре immutable исходных journals (classic, token2022, dependency-index/range)
обработаны **без сети**. 16/16 интервалов прошли, 5 pools, 1 889 уникальных tx,
1 921 state rows, 11 430 conditional quotes; 96 unavailable на classic anchors без
chain timestamp. 176 наблюдавшихся swap checks и 8 harvest сохраняются из прежних
аудитов, это не новые market observations. В corpus 127 tx slots, sparse envelope
452384039–452408973 не означает непрерывное покрытие. Нулевой positive roundtrip
на данной сетке — проверка модели, не свидетельство PnL или edge.

Все четыре bundles пересчитаны из собственной journal-копии, Token-2022 — также из
установленного wheel. Оригиналы остались побитно прежними. **264 tests**, lint/format/
strict typing, smoke и build прошли. Raw+bundles: work/economic-corpus-01; контракт:
[dataset](../docs/economic-dataset.md); [evidence](../docs/evidence/economic-corpus-2026-10-02.json).

**Ограничения:** historical_available_ns=null для всех строк; receipt и поздняя
endpoint verification не становятся исторической доступностью features. Training,
market dataset и execution qualification false. Короткий fixed cohort не покрывает
births/deaths, разнообразие режимов или calibrated inclusion/costs. Quotes одной
истории/размеров не являются независимыми сделками или логами поведения агента.

**Следующий крупный шаг:** causal observation/feature pipeline с отдельным контрактом
доступности и gap masks, затем bounded forward corpus с настоящими receipts и
разделением calibration/evaluation. Не интерполировать будущие snapshots/параметры.
Provider stage закрыт D33; оставшиеся 10 blocks не являются текущим заданием.


## Текущий результат D35: causal features и forward corpus

CausalBook обрабатывает только реальные validated finalized snapshots семи счетов
одного bank с Clock. Receipt/decode/as-of/feature-publication timestamps разделены;
future append/mutation не меняет прошлые features. 12 признаков, nullable values и
явные masks; duplicate bank не обновляет freshness, gap/regression/conflict/invalid
history требует нового прогрева. Старый economic tape не объявляется causal dataset.

Реальный frozen plan: все три прежних Token-2022 pool, 12 rounds × 10 s, max 36 calls,
10 s timeout / 1 MB, spacing 1 s, max ages 25/45 s, max gap 25 s. Выполнены 32 вызова,
31 HTTP success / 112 971 bytes; index 31 к pool 4GWh…QYfm получил transport timeout
10.006 s, HTTP/RPC code неизвестны. Серия остановлена, retries нет, оставшиеся четыре
calls не выполнялись. Успешные response median 318.753 ms — не execution latency.

11 frames / 33 rows, 29 usable, 3 warmup + 1 timeout mask; ~109.038 s span.
В ходе QA усилен endpoint contract: fresh targets schema 2 требуют snapshot available
не раньше horizon end и slot новее start. Это исключает превращение carry-forward
feature frame в новое будущее наблюдение. Результат: **8 calibration / 4 engineering
 evaluation / 7 purged / 14 censored**. Horizon 20 s, tolerance 15 s; actual endpoints
20.129–32.554 s, без интерполяции. Schema 1 artifact сохранён неизменным как история.

Пути: work/causal-forward-session-01/corpus и work/causal-forward-targets-02.
Оба artifacts воспроизведены offline и из wheel; все raw/source/manifest checks прошли.
**280 tests**, lint/format/typing, smoke/build. Подробный контракт:
[causal forward](../docs/causal-forward.md), [evidence](../docs/evidence/causal-forward-2026-10-02.json).

Original collector code a666c5b; target-v2 code 7cc4f91. Для оригинала на новой версии
`verify-forward-corpus --compatible-version` разрешает отличие source только при
точном воспроизведении всех values; ожидаемый exit 1 означает incomplete collection.
`verify-forward-targets PARENT DERIVED` пересчитывает оба слоя. Secrets не сохранялись.

**Граница:** temporal contract проверен, но training_ready/market_dataset/execution/edge
false. Fixed cohort, короткая история, unknown intervening trades, неисполняемые price
labels и незавершённый сбор не заменяют квалифицированную выборку. Evaluation partition
использовался для инженерной проверки контракта, не final holdout. Следующий этап —
возобновляемый segmented prospective collector и более длинное покрытие с честными
пропусками; затем execution-conditioned labels до ML. RL/paper/live не начинались.


## Результат D36: bounded segments и условное исполнение

Кампания фиксирует cohort/budget/expiry/source и единый calibration cutoff до сети.
Resume создаёт новый immutable segment с warmup, hash-linked seal и OS lock; crash bytes
сохраняются, весь planned segment budget резервируется, пропуски не backfill. HTTP/RPC/
validation ошибки останавливают кампанию; timeout/connection могут продолжиться новой
invocation после cooldown, максимум два failed сегмента подряд. Resume/budget/crash/
concurrency проверены synthetic tests; реальное продолжение той же кампании в этом
запуске не выполнялось из-за non-transient validation guards.

Реальные artifacts: `work/prospective-campaign-01` (48 valid / 49 requests),
`work/prospective-campaign-02` (67 / 68), isolated `work/prospective-clock-check-01`
(1 valid, не входит в features). Всего 118 requests, все HTTP 200; два несовместимых
checkpoint в quarantine. Context/Clock пары: 452681508/452681509 и 452682339/452682340.
Root cause upstream не установлен. Raw fixtures добавлены, исходные responses неизменны.
Plan-01 360 maximum, остановлен; после probe plan-02 ограничен 270, также остановлен.
Новые запросы прекращены; 360 не было фактически израсходовано.

`work/conditional-execution-targets-01/02`: два направления, sizing 100/1000 ppm
по decision reserve, delay scenarios 0/5 s, holding 20 s, tolerance 15 s, minOut с
100 bps slippage. Quotes используют existing integer CPMM/transfer fee math. Entry,
exit-decision и exit-fill требуют новых snapshots, causal availability, no gaps.
Exit failure сохраняет acquired position; unknown network costs/admission/persistent
impact не подменяются нулями. `net_pnl=null` для всех labels; результат roundtrip только
после pool/transfer fees до network expenses, не executable/realized PnL.

444 mature scenario outcomes относятся к 67 различным decisions, все calibration;
engineering evaluation 0. Восемь вариантов и перекрывающиеся окна не независимы.
Полный долгий сбор и execution qualification остаются открытыми, training_ready=false.
Исходный D35 corpus и все прошлые journals не изменялись. 297 tests, lint/format/mypy,
smoke/build, обе пары campaign/target воспроизведены offline и из installed wheel
`work/prospective-wheel-01`. Seeds отсутствуют (детерминированная data QA), code/plan/
source hashes, точные times/cutoffs/errors и measurement metrics — в evidence D36.


## Результат D37: coherence отдельно от поддержки protocol version

Agave getMultipleAccounts выбирает один bank для accounts/context; Clock.slot в Bank
равен slot. Это поддерживает прежний strict equality guard, но не устанавливает
upstream причину двух Helius ответов. Helius MCP troubleshooting не дал объяснения;
документированный Gatekeeper с existing key получил timeout первого запроса 10.061 s.

`work/coherence-source-probe-01`: Solana public 6/6 full strict snapshots прежних трёх
пулов, Gatekeeper 0/1 timeout. `work/prospective-public-coherent-01`: новый fixed plan
3×20 rounds×10 s, 180 max requests, прежняя cohort, public origin закреплён. Первый
getMultipleAccounts дал context=Clock=452687445, но config CRRS… word@116=50000;
collection остановлен, raw сохранён. Последний preflight в slot 452686853 имел нули
в этой области. Точная write transition и смысл изменения не доказаны.

`work/coherence-classic-probe-01`: заранее выбраны оба classic pools прежнего pilot
(включая SOL/USDC control), другой общий config D4FPE…. Первый ответ в slot 452688467
тоже coherent, но word@116=50000; второй запрос не отправлялся. Пул по доходности не
выбирался, успешно прошедшая замещающая cohort не найдена и не объявлена. Разумный
следующий этап — source/deployed layout qualification, а не дальнейший перебор.

Текущий публичный Raydium source всё ещё описывает bytes 116+ как padding, update_config
не объясняет это значение. Копии/хеши в work/checkpoint-coherence-research. Program read
`work/cpmm-program-version-01` установил ProgramData адрес DMaw…tHhU, но чтение его
первых 45 bytes завершилось timeout. Upgrade slot/authority и binary equivalence
неизвестны. Нельзя делать вывод, что подтверждён upgrade или новая комиссия.

`checkpoint_diagnostics.py` даёт отдельный diagnostic-only kind, не observation;
разделяет Clock mismatch и unsupported config words. `corpus_quality.py` проверяет
coverage/maturity/boundaries/inventory и отдельно измеряет HTTP, chain age и условные
observation delays. Новые raw fixtures сохраняют оба distinct-config отказа. Guard
AmmConfig padding и все cpmm/tokens decoders не менялись.

Plans schema 2 требуют endpoint_origin (без path/query/key); resume сверяет host даже
при совпадающем alias и запрещает закрытые кампании. Новые CLI явно требуют endpoint
origin/env, Helius не подставляется в public campaign. Raw, requests, receipt times,
failures, plans, source/hashes, engineering cutoff и conditional execution model — в
D37 evidence. Новый quality artifact work/coherence-quality-01 воспроизведён offline
и из work/coherence-wheel-01; D36 history проверена work/prospective-wheel-01.
