# Текущий статус

## 2026-10-07 — post-training audit и Watch Market release readiness

Read-only Mac1 аудит последнего законченного AUTO training `job_5edd5e5ecda88338`:
completion корректен, 4/4 fits закончены, ledger chain и sealed candidate/model/snapshot/dataset
manifests проверены, partial/corrupt outputs и temporal/split/availability violations не найдены.
Pipeline выбрал 2-seed GRU, candidate не promoted. Матched pool-macro pinball на test:
combined 0.008904 против champion empirical 0.006272 (+41.95% хуже); 5m +65.43%, 15m
+27.07%. На validation combined +32.78% хуже. CI нет; retrospective folds малы и serially
correlated. Managed prospective exam использует одинаковый B07 15m book/costs (50 bps slippage
+25 bps fee на сторону); на момент receipt обе книги имели 8 decisions, 0 trades/fills, $0
realized PnL/fees. Cost-adjusted superiority не установлена. Champion остался empirical SHA
`9ea4a6bd7977fb66fe4a18eba54ed7eaed0afd65810ec99c75aec3e1443e3920`.

Mac1: `auto/waiting`, domain STOP=false, queued/running jobs=0, активная managed exam
`job_2069a67d64cf1bc8` истекает `2026-10-07T12:23:51.922091Z`; operator expiry
`2026-10-11T20:31:13.058527Z`. PID 64937 обслуживает `operator-runtime08`. Это не Pause-only
окно: не ставить на паузу ради release, пока exam активен. После expiry заново подтвердить
status/no jobs/no exams, затем routine Control Center Pause, guarded activation и health check;
resume только после успешного health. `runtime09` имеет тот же manifest pin, что runtime08,
и не содержит Watch Market. Требуемый новый bundle: `operator-runtime10` из Watch Market commit
`0e9a2c1`. Производственный runtime не переключался.

Watch Market implementation/review/tests опубликованы в `0e9a2c1`; start закрепляет официальный
champion path+manifest SHA из operator config и проверяет hash, не смотрит на latest candidate.
Regression test добавляет более новую unpromoted GRU в registry и доказывает, что Watch стартует
с pinned official champion. Полный отчёт, manifests, hashes и live-readiness receipt:
[audit](../docs/brain-post-training-audit-2026-10-07.md) и
[JSON](../docs/evidence/brain-post-training-audit-2026-10-07.json).

Проверки Mac2: `make check` — 985 passed / 12 skipped; Ruff, format, mypy и `make smoke` passed.
Skipped cases требуют optional Torch/browser dependencies; browser visual acceptance и live
provider acceptance не проводились. Mac1 production activation/pause не выполнялись.

## 2026-10-06 — Watch Market implementation (не выложено)

В Control Center добавлен отдельный Watch Market UI/API/worker: пользователь вводит mint/pool,
выбирает разрешённый GeckoTerminal pool и mint side; отдельная 60s public-provider lane
строит closed-5m Brain forecasts, quantile uncertainty и independent B07 virtual book/ledger.
Зафиксирован model manifest SHA, $1,000 paper cash; real execution выключен. AUTO routes,
config, jobs и ledger не используются этой lane. Реальная provider acceptance не проводилась;
mint/pool ещё не дан, браузерная визуальная проверка недоступна в этой среде, Mac1 rollout не
делался. Это implementation proof плюс synthetic tests, не подтверждение live market path,
edge или торговли. Подробности/границы: [docs/watch-market.md](../docs/watch-market.md).

Синтетические проверки: focused tests покрывают mint/pool validation, model pin, per-market
forecast/paper accounting, WAIT/ENTER/HOLD/EXIT, CSRF/UI/API и отсутствие AUTO mutation.
`make check`: 985 passed, 12 skipped; ruff, format, mypy и `make smoke` прошли. Skips —
optional ML dependencies (Torch отсутствует) и browser acceptance (Playwright Node module
отсутствует). Локальный fixture запускался и был остановлен; подключённого browser surface
в среде нет. Mac1 в исходной read-only
проверке оставался AUTO/waiting с активным managed exam; production release не разрешён
этой задачей и не выполнялся.

## 2026-10-06 — Mac 2 принят как development host; transport/release tooling

Проверен `/Users/sl-aleksandr/Developer/memecoin-worker`: M4Pro/48GiB, bootstrap receipt
и SHA256SUMS, clean base eee7ce1, fetch/fast-forward доa594474. Python3.13/dev env и
Cursor CLI2026.10.01-e373342 установлены. Cursor auth отсутствует, model catalog не проверен;
Cursor MCP servers не настроены. На Mac1 `root1@192.168.31.66` включён Remote Login,
авторизация ключом Mac2 и host fingerprint подтверждены. Synthetic artifact принят Mac1 и
возвращён на Mac2. Read-only `/api/status` ответил `operator-status-v1`, `auto/waiting`,
`domain_stop=false`; LaunchAgent, listener и runtime process совпали на PID 64937
(`ai.memecoin.brain.operator`, pinned `runtime08`). Commit `669d4d5` подготовлен на owner Python
3.13.6: 980 passed/12 skipped; `runtime09` staged, но модули runtime идентичны `runtime08`.
Activation не выполняли: одна managed exam действовала до `2026-10-06 21:14:49 UTC`, режим AUTO;
auto-review отклонил запрос. Post-check подтвердил прежний active pointer/PID/status; staged bundle
остался в production work directory, неактивным.

Реализованы tools/worker_artifacts.py (explicit sealed roots, staged hash/membership
verification, idempotence/conflict отказ, SSH stdin transport), tools/worker_release.py
(exact Git prepare/checks, preserve refs bind, paused/no-active-exams update, code rollback
без database restore). На synthetic roots проверены round trip/retry/tamper и сохранение
новых ledger events при rollback. Двуххостовая synthetic передача/возврат квалифицированы;
production release на Mac1 не проверяли.

Уточнение пользователя: сильный внешний MCP-Qwen сейчас отложен; local35B — бесплатный
preprocessing worker, не strong code reviewer. Одна задача Q8 context8192 дала≈47.1tok/s,
но swap5.3→14.2GiB/free88→7%; output900tokens обрезан и содержит неверные findings, review
не принят. Новая очередь local_evidence_queue.py сохраняет curated source/STOP и deterministic
summary, optional annotation без authority. RAM gate откладывает текущуюQ8; нет daemon,
training/market features/admission изменений. Synthetic101events:11303→788bytes; это не
billed-token savings. Codex worker перенёс UI tests с отсутствующего Mac1 work/ на synthetic
status и tracked optional browser runner: operator suite220passed/4skipped.

Команды, ограничения, missing access и следующий шаг: [two-mac-operations](../docs/two-mac-operations.md).
Следующий технический шаг: после expiry и штатной UI Pause — owner-host activate/health/rollback
rehearsal. Cursor login всё ещё нужен для hosted
model catalog; новый historical campaign не начат.

Итог: commits `3854eb2` (portable UI tests), `ab8c1a7` (worker tools/границы).
`make check`:975passed/12optional skipped; exact Git release `ab8c1a7` после последних
regressions:978passed/12skipped,ruff/format/mypy/smoke exit0. Synthetic bind→switch→rollback
сохранил событие после переключения, launchctl calls0. Независимый review выявил и привёл
к исправлению wrong-service health, expiry/recovery, pending-temp и failure-journal ошибок.
Полные hashes/receipts/делегирование: [evidence](../docs/evidence/two-mac-worker-2026-10-06.json).
GitHub auth avariceJS подтверждена; Git author локально настроен по существующей истории.

## 2026-10-06 — подготовка реального worker checkout для Mac 2

Выбрана асимметричная схема: Mac1 сохраняет source/AUTO/paper/registry; Mac2 — engineering/
LLM worker, далее bounded non-overlapping historical campaigns и при необходимости heavy
replay/training. Current8fits28.64s против≈85min acquisition: distributed training не нужен.
Доступ ко второмуMac отсутствует, только user-reported48GB/Ollama/Qwen35B≈39GB; SSH адрес
запрошен. Дополнительные accounts не активированы, budgets не увеличены. AlchemyTerms2.2(x)
запрещает spread across accounts; допускаются лишь реально разрешённые provider envelopes.

Добавлен standalone `tools/worker_preflight.py` (host/Git/CLI/loopback Ollama metadata,
0inference/0providerrequests). Context helper исправлен: полныеAGENTS/now безprefix truncation,
остальные excerpt bounded сhashes. Прежние72.9k — размер вывода доtruncation, не измеренный
billed context; bytes также не tokens. Подготовка portableGitbootstrap описана в
[two-mac-workflow](../docs/two-mac-workflow.md). Live read-only check: AUTO/waiting,
domain_stop=false, sourcehealthy; deployment/runtime/expiries не менялись.
Проверки tooling:9passed,ruff/format,bashsyntax; `make smoke` reproducible syntheticPASS.
Полный runtime suite повторно не запускался: изменены только tool/docs/worker bootstrap.
Bootstrap ZIP5.54MB закреплён на code`eee7ce1`, лежит в`work/two-mac-bootstrap-20261006-01.zip`.
Same-host rehearsal: exactcommit/cleanclone, existingtargetrefused, tamperrejected доclone.
Mac2acceptance и реальные modeltasks ещё не выполнены. Final readonly: AUTO/waiting,
source4898attempts/0failures/healthy, domain_stop=false; [receipt](../docs/evidence/two-mac-bootstrap-2026-10-06.json).

## 2026-10-06 — сокращение повторного контекста Codex

Подтверждена структура памяти: `state.md` 1 477 строк /134KB, `decisions.md` 1 176 /115KB,
`changes.md` 819 /75KB; полный стартовый перечень создавал избыточное повторное чтение истории.
Добавлены компактная точка входа `memory/now.md`, task-scoped `tools/agent_context.py` и
профили operator/data/ml/architecture/review/docs с cap 24 000 символов (до ~6 000 tokens по
грубой chars/4 эвристике, не по реальному биллингу). История/evidence не удалялись. Пакет
выводит только профильные документы, git status и последние commits; query ограничен этими
источниками. Большие logs/work и protected evidence автоматически не включаются.

Этот checkout на основном M4 Pro/24GB; фактический hardware проверен. Здесь доступен Codex;
все остальные модели пользователя находятся на втором M4 Pro/48GB, где checkout проекта нет.
Поэтому реальные Grok/Qwen/Kimi/GLM/DeepSeek repo-задачи пока не запускались: нет подтверждённой
передачи файлов/ветки или локального Qwen endpoint. Второй узел выбран как будущий изолированный
implementation/LLM/offline training worker; live source/AUTO/canonical store остаются на первом.
Передача только bounded context/sanitized evidence либо sealed shards и hash-verified bundles;
никакого общего mutable SQLite/второго writer.

Роли и текущая схема описаны в [now.md](now.md); полного расхода/остатка Astra quota эта среда
не показывает. Проверки: полный `make check` — 922 passed, 11 skipped (optional ML env);
ruff, format и mypy прошли. Context helper: 3 tests passed.

## 2026-10-06 — восстановление после закрытия крышки / clock jump

Установленная причина паузы: 2026-10-06 10:47:26 МСК source child завершился `-9`,
`clock_jump_or_suspend` спустя 5,91 с supervisor time; старый source runtime ошибочно
превратил эту ожидаемую реакцию на сон в `STOP(source_worker_failure)`. STOP и incident
сохранены в `source-repaired01`, хеши зафиксированы в evidence. Это был fail-closed каскад,
а не проблема «Продолжить» или отсутствующего интернет-соединения.

Добавлена обработка точного sleep/SIGKILL случая: source пишет отдельный
`sleep-interruption`, не создавая STOP; managed B13 не распространяет этот сигнал на весь
operator; paper возобновляется только после полного replay, без лимита на повторные
подтверждённые suspend-прерывания. Другие неизвестные ошибки остаются STOP. Исправленный
source восстановлен в отдельный root с теми же 4449 attempts, expiry, бюджетом и audited
index; старый root и STOP неизменны. Уже исправленный source пережил ещё одно такое
прерывание без STOP и вернулся к healthy heartbeat.

Operator runtime08 pinned и переключён на recovered source; paper exam перепривязан
отдельной revision с точным paper ledger. Сейчас operator `mode=auto`, `phase=waiting`,
`domain_stop=false`, source healthy; последнее чтение: 4481 attempts, failures=0. Quota
2/6, сутки/100 matured gate, champion, expiry и AUTO book сохранены. Полный текущий paper
replay: 1650 ticks, 1512 decision, 3 cancel, 0 open/pending positions, realized 0, edge
не установлен. Сон всё ещё означает пробел в наблюдениях; восполнение истории не заявляется.
[Recovery evidence](../docs/evidence/brain-suspend-recovery-2026-10-06.json),
[инструкция оператору](../docs/brain-operator-manual.md).

## 2026-10-06 — actual training support 24 → 86

На session cutoff2026-10-06 06:20:01UTC: common future12horizon cases/8origins/2pools,
5m8 и15m4. BroadGRU loss0,006225/0,006437 хужеcontrol0,003319/0,002723; малыйN,
не доказательство общей деградации или улучшения. Все12virtualbooks CASH,0fills/fees/PnL.
24h exam ещё идёт. [Exact evidence](../docs/evidence/brain-training-expansion-2026-10-06.json).

Текущий результат: [training expansion](../docs/brain-training-expansion-results.md).
Alchemy birth selection → GT USD admission → sealed corpus → прежний B13 recipe выполнен.
62 новых train pools /7987 eligible decisions /95 новых pool-days; May–Sep2026, возраст≤3days.
Train до cap90336→98323, после cap48000 в обоих arms, UTCdays93→104. Каждый learned fit
broad реально использовал86pools. Всего8fits,28,64s вместе с snapshots/audit, без retries.
Оба arms выбрали GRU; общий descriptive test broad хуже control на8,92%. Улучшения нет.

AUTO operator04 теперь runtime07, pin90059c8a474da9e534bbba41a23ab195b20a2aa4c99d152026985fc4616f5c6c.
В binding закреплён work/brain-expansion-20261006/corpus01,
SHA727df2a88d022966f6ca614e5b3fba0edca963008ebfce15c70c8ed2d40443e4.
Следующие AUTO snapshots добавляют этот corpus; source index не переписывался. Source-only
matured growth отделён от total training support. AUTO/waiting,2/6reservations;24h+100gate
сохранён. Config/source/expiry/books/STOP сохранены,2372старых event/STOP hashes совпали.
8externalfits НЕ скрыты как0fits и НЕ списаны повторно с operator reservations.

Новый common frozen paper: work/brain-expansion-20261006/paper01, UI8797,
activation2026-10-06 05:51:06UTC, expiry2026-10-07 05:51:06UTC. B13latest/control/broad,
все3families, прежняяfixed15policy,trainingdisabled,0providerGET. Это отдельные LaunchAgents,
не managed кнопкой B13 «Стоп всё»; команды остановки в operator manual. Champion unchanged.
Broad SHA6acae357bd985087bc3c85820c001be6264f471c8661659bfb0ebde73ec5e41a;
control SHA1839f0ca035711ebad0748d5207ad24ee93446c15c0059140fba5fe495d24ab2.
В registry опубликованы expansion01-{control,broad}-{dataset,candidate}.

Main acquisition sealed:107GET/100success/401×1/429×1/URLError×5,2,19MB payloads,0USD.
Unseen20case plan закреплён доfits:20successGET,13eligiblepools/924decisions. Этот past-event
test запрещён в train/selection. Broad empirical лучше поpoolmacro,но хужепоpooledloss;
broadGRU не улучшился (5m+17,08%,15m+0,027% кcontrol).
Future exam ещё идёт; не выдавать короткий срез за доказательство улучшения. GT остаётся
единственным qualifiedUSDfeed;965registrymarkets не стали965trainmarkets.
Главный следующий вопрос — support-aware sampling и широкая future evaluation, вместе с
полнотой historical/live coverage. Новыеpools дают16,64%строк,но72,09%samplingmass;
20pools<16decisions. Модель/feature/policy sweep по раскрытому test запрещён.
Сначала проверить actual future reports, source health, readonly seals; не повторять fits.

## 2026-10-06 — начальная запись этапа (завершён; актуальный итог выше)

Пользователь принял forensic/vacuum и поручил расширить именно training support, затем
честно сравнить learned models на будущем рынке. Модельную сложность не увеличивать.
[Протокол](../docs/brain-training-expansion.md): Alchemy birth cohorts May–Sep2026 →
GT USD bars с exact mint/quote/receipt проверкой → immutable admitted corpus → прежнийB13
recipe.100outcome-independent new pools, capped acquisition; data-only control/broad8fits
зарегистрированы, пока не проведены. СтарыйAUTO/runtime06/source/24hgate ещё не менялись.

GT probe2025 получил401(public180day limit); probe02 после5validGET получил429.
STOP/no retries сохранены,900scooldown соблюдён. Bulk01:10validGET, затем15sURLError;
bulk02 продолжает лишь89unrequestedcases исходного100сdebit11/старымdeadline.
Transport gaps изолированы,120scooldown,≤5failures включаяbulk01;401/403/429stillSTOP.
Raw artifacts: work/brain-expansion-20261006/. Многиеpools имеют очень малоcontiguouswindows;
финальная ширина ещё не измерена. Не перезапускать завершённыеcampaigns.

Реализованы offline admission/merge, exact shared-evaluation experiment, prospective join
с повторным расчётомloss, pinned historical refs для будущихAUTO snapshots. Live matured
сохраняется в source namespace отдельно отtotaltraining, чтобы не сломатьgrowth subtraction.
Ниruntimecutover, ниnewcandidate/futureexam пока не выполнены. При продолжении сначала
проверить текущийbulk02 и егоSTOP/results.

## 2026-10-06 — forensic review и historical vacuum qualification завершены

Актуальный итог: [forensic review](../docs/brain-experience-review.md),
[vacuum results](../docs/brain-vacuum-results.md). Предыдущая секция «в работе» ниже — история.
AUTO operator04/runtime06 продолжает работать: waiting, source healthy, domain_stop=false,
2/6 reservations. В этой сессии runtime, LaunchAgents, expiry, книги и gate не менялись.
Source expiry 2026-10-07 12:23:51 UTC; operator bound 2026-10-11 20:31:13 UTC.

Реальный automatic refit подтвердился, но первая paper-сессия упала через ~9h; `.DS_Store`
и domain STOP исправлены прежним commit6b445b8. Legacy B10/B12 остались stopped.
D43: 1 completed + 2 failed окна, затем closure; последние 3 не запускались. Holdout не читался.
OLD/NEW: 5m paired descriptive loss −3.98%, 15m +5.77%; общего улучшения не доказано.
Новое eligible +2598, действительно новое внутри train +2082, новых train pools 0.

Новый raw lane: 147 RPC /14340 nominal CU. Sealed corpus01: 965 разных markets,
865 birth pools, 167 swap pools, 1579 уникальных reconciled swap candidates из 2025–2026
campaigns. Births из 17 месяцев; пригодные swap layouts преимущественно 2025.
Это НЕ 965 train markets: raw → USD training admission ещё не квалифицирован,
новых training-ready decisions 0. Действующий Brain: 24 train pools /93days /48000 retained.
Свежий candidate на enlarged corpus не обучался; активен уже automatic B13 candidate.

Реализованы indexed multi-pool discovery/fee streams, immutable cursor/reservation journal,
gap debt, strict historical IDLs, global dedup/revision rejection, canonical SQLite snapshot.
Campaigns завершены, новый 24/7 daemon не установлен. Exact decoder не игнорирует suffix.
Streaming shards + memory-map minibatch cache проверены на реальном 91472-sample snapshot.
Information-support-v1 реализован только shadow; live AUTO остаётся 24h+100matured.
Operator deployment guard и все OLD/NEW dataset/model seals повторно проверены.

Следующий сильный шаг: qualified contiguous historical ranges + quote/USD normalization
и actual 2026 layouts; затем расширенный dataset, fixed-recipe candidate/future paper,
после этого exact cutover loader/diversity gate. Не сжигать quota на partial bars.
Не открывать F01/D43 reserved holdout и не оживлять закрытый calendar track.


## 2026-10-06 — forensic review, новый data этап в работе

Первый настоящий B13 AUTO refit выполнен: candidate `job_ef9cb49cef6d7157`, exam
`job_3d405d04fd61c744`; AUTO/waiting, 2/6 reservations. OLD/NEW по-прежнему24train pools,
48k retained,92→93days. На одинаковом NEW test GRU5m loss−3.98%,15m+5.77%; общего
improvement нет. Eligible growth2598/15known pools/newpools0/effective9.60; сильное
overlap. Legacy B10/B12 также STOP `.DS_Store`, их daily/future continuation не состоялась.
D43 закрылся после completed1+failed2, последние3окна не запускались. Holdout4/5 не читался.
[Forensic review](../docs/brain-experience-review.md), [следующий протокол](../docs/brain-market-vacuum.md).
Работающий operator/source пока не менялись, adaptive gate ещё не включён.


## B13 — финальная приёмка, 2026-10-05

Текущий оператор: `work/brain-b13/operator04`, http://127.0.0.1:8792.
Реальный UI путь Collect → Snapshot →4fits→immutable candidate→future paper выполнен.
Dataset job_4ed49dcc84dfb59d:24train markets/92days/48000decisions; val451/test352,
unseen_test_pools=[]; dataset SHA356a202be87a81c3753fe9141948be3a3f5d982c6190c821e7d7efbe86fd2191.
Candidate job_fe34d70ab6cea0bc, SHA c895590ac4726af6fcc8e46b870b6f08c8fdd7caf1fe50dbca018b16a8e77a76.
GRU выбран validation до test; descriptive test хуже empirical. Promotion НЕ выполняется.
Future exam job_b7edd93ea86173f9: activation2026-10-04 21:13:36UTC, expiry24h.
Реальные5/15min outcomes созрели; пока WAIT/CASH, не искусственные trades.

Активные repaired services: source-repaired01 b09r1.source/audit (original25000/4000,
expiry2026-10-07 12:23:51UTC); B10 b10-repaired02 b10r2.paper/train/UI8796;
B12 b12-repaired02 b12r2.paper/UI8795 (expiry2026-10-05 18:28:50UTC unchanged).
Original B09 STOP20:32UTC audit600s, B12 STOP19:41UTC WAL read race и неудачный
B10-repaired01 STOP missing sealed model/ledger/.lock сохранены. Fullraw exact95204bars,
repair сохраняет budgets/cursors/quarantine; copied paper ledger prefixes exact.
Старые8790/8791/8793/8794 — архивные консоли, не активные paper workers.

AUTO bound:6reservations включаяfailures, ≥24h+100newmatured, срок продукта
2026-10-11 20:31:13UTC. После original source expiry — отдельный qualified successor
≤30000additional GET/background6000. SingleproviderGT и фиксированный24market cohort
остаются ограничениями. Policy fixed15min, не learned exit/MPC; virtualbooks независимы.
Не читать/менять protectedF01/D43/holdout. D43 LaunchAgent сохранён.

**Приёмка завершена, AUTO включён.** Pinned runtime `work/brain-b13/operator-runtime04`,
LaunchAgent ai.memecoin.brain.operator, pin16f1500f49f9cd48f097fdd2ca9936a76da38089c7694b43beece6a6db7f548a.
Real UI AUTO/Pause/Resume/Stop → restart → Resume прошёл; модель, dataset, exam и1/6
reservation сохранены. Source/config/activation/старый B12 STOP pins неизменны.
make setup/check/smoke/build passed;853base/10optionalskips,40ML; finaloperator208/3skips,
history18passed. Итог docs/evidence/brain-operator-acceptance-2026-10-05.json.
History work/brain-b13/operator-report-final01.json;116predictions/84scored/32pending
наcutoff, реальные5/15min outcomes, no trades в новом exam. Не считать58bookkeepinggroups
статистически независимыми. Пятидневный uptime ещё не измерен.
Commit5aa933c + финальный evidence/handoff commit. Grok receipts сохранены; новые задачи
не запускать из-за исчерпания квоты. Следующий Codex: source/runtime health, gaps/incidents,
reservations/candidates, paired matured forecast losses и policy ledger; не переобучать
по test/PnL и не сбрасывать quotas/STOP. Старые статусы ниже — исторические срезы.
[Протокол](../docs/brain-autonomous-operator.md), [приёмка](../docs/brain-b13-acceptance.md),
[инструкция](../docs/brain-operator-manual.md). Следующий Codex: сначала эту секцию,
final evidence и operator report/ledger; прежние статусы ниже — история до B13 repair.

### B13 runtime repair, 2026-10-05 11:58 МСК

Причина предыдущей паузы: `candidate_tick` перечислял файлы внутри
`candidate-shadows`; `.DS_Store` вызвал `NotADirectoryError`, paper worker exit1 и STOP
у exam `job_b7edd93ea86173f9` в09:20:43МСК. Это был exam STOP, не source STOP и не вчерашний
тест кнопки. STOP, exam ledger и инцидент сохранены. Код теперь фильтрует настоящие
каталоги-lanes и не следует по symlink; STOP отдельного exam больше не блокирует весь
operator domain, source STOP по-прежнему fail-closed.

Запущен LaunchAgent на pinned `work/brain-b13/operator-runtime06`, pin
`2620370937bf0547b222fdd591025a1e981372299e72eaf2034763c8d0d5779b`. Штатный Resume
вернул `mode=auto`, `phase=waiting`, `domain_stop=false`; source healthy, STOP старого exam
виден как `stopped`. Сейчас scheduler ждёт прежний 24h interval; reservations1/6 и сроки
не менялись. `make check`:870 passed/10 optional skips; `make smoke` reproducible synthetic
PASS. Повторные24h uptime всё ещё не измерены. Evidence переключения runtime:
`work/brain-b13/operator04/revisions/operator-runtime06/`.

## B12 — curriculum сравнен, frozen candidate уже получает future paper, 2026-10-04

**B09 source/audit и B10 paper/train/UI сохранены.** Дополнительно работает независимый
B12 frozen exam: `work/brain-b12/paper01`, bundle `work/brain-b12/paper-runtime01`,
labels `ai.memecoin.brain.b12.{paper,ui}`, [UI8791](http://127.0.0.1:8791).
Основной [B10 UI8790](http://127.0.0.1:8790) не заменён. В B12 training job НЕ загружен,
max_runs0; веса recent/full/balanced и старого champion неизменны до конца exam.
B12 activation2026-10-04 21:29МСК, expiry **2026-10-05 21:28МСК** (24h).
B09/B10 expiry по-прежнему2026-10-07 15:23МСК; первый B10daily refresh не раньше
2026-10-05 13:41:54МСК плюс100new mature/hourly scheduler. Его allowance не изменён.

36matched fits за183.66s на sealed B11 данных, provider queries0. Общий train end
2026-10-01 22:15UTC, validation end2026-10-03 07:45UTC, test end2026-10-04 17:15UTC.
Recent train6005decisions/5pools/5UTCdays; full иbalanced50414/5pools/47days. Balanced
иерархически уравнивает pools/days; это НЕ новые рынки. Test6027decisions/24pools/2days,
19pools отсутствуют в train. Один и тот же test, features и1000SGDupdates для всех arms.

GRU test pool-macro pinball recent0.0290466/full0.0273579/balanced0.0268664: снижение
ошибки5.81%/7.51%. Curves немонотонны, CI null при2days, tail calibration плохая.
**До test выбран full GRU** по validation1%tie; champion empirical НЕ promoted.
Full GRU против recent-selected empirical: test loss ниже5.34%, exploratory only.
Модели100%: recent `c9c40ce4…1803f`, full `7fcca7df…d4bf0`, balanced `ccab396a…c5408`.
B12 runtime pin `72cfa4af21c81a4b20f010e8b35f8cc6ae08d1e698da273841db8220c316ae83`.

Live cutoff18:36:40UTC:72forecasts,18paired observation groups,16scored outcomes/4paired
mature groups,56pending. 12новых experimental books,108decisions—allcash(median below cost
buffer),trades0. First prediction18:30:33UTC, после activation/eventclose/receipt gates.
Exact replay7ticks/168raw joins/36published forecast joins и16outcomes; raw audit1544captures,
73459bars exact. Source/B10/B12 healthy, B10training reservations1, source всего7transport
failures (последний17:43UTC, до B12); их история сохранена. Новых source queries B12 нет.

650basePASS/7skips,677combinedMLPASS +4focusedPASS последующих scoring/exam guards;
check/smoke/build и повторные6ensemble predictions exact. Browser unavailable, UI API/JS
render smoke выполнен, visual review не заявлять. F01/D43/holdout untouched; no real money.
[Итог B12](../docs/brain-curriculum-comparison.md),
[training evidence](../docs/evidence/brain-curriculum-comparison-2026-10-04.json),
[future exam/replay](../docs/evidence/brain-curriculum-exam-2026-10-04.json).

**Следующий B13:** сохранить оба paper loops; оценить matured paired behavior и первый
B10scheduled refresh; расширить именно train market/cohort diversity. Новое frozen refit/
future-exam правило нужно до включения young/recent outcomes: уже прочитанный test не
становится новым independent evidence. Raw2025decoder/birth cohorts полезны для широкого
учебника, ещё год тех же5pools недостаточен. Backbone/auxiliary heads пока не менять.
Ни week uptime, ни edge, ни calibrated execution не установлены. Ниже исторические срезы.

## B11 — архив и фазовый учебный цикл, 2026-10-04

**Активны прежние B09 source/audit + B10 paper/train/UI, hashes/config не менялись.**
Выбран archive-first с фазовым приоритетом heavy collect/train/paper; phase scheduler пока
не установлен. F01/D43/reserved holdout untouched. Реальные средства/signing/paid запрещены.
Control Center: http://127.0.0.1:8790. Expiry прежний:2026-10-07 15:23МСК.

Создан sealed `work/brain-b11/textbook02`:61 233bars/24pools/50historical event days, один
receipt day,35shards;59 837context decisions/59 404complete labels. Raw audit1318captures exact;
legacy per-pool build всех decisions/features и119 088mature auxiliary labels воспроизведён.
Действующий rolling7d содержит15 287bars:28kcap сейчас НЕ binding. Наивный full-history split
даёт28 191train examples лишь4pools против6005/5pools rolling: bottleneck — выборка/diversity,
а не размер backbone.611revised candle keys/40close changes сохранены first-observed.
Новый archive adapter≤100kdecisions, streaming trainer ещё не реализован; live код не менялся.

Alchemy qualification действительно вернула400full transactions за2025:5RPC/440nominalCU,
standard core cross-check exact. Это неподдержанные Raydium CLMM/Orca transaction records,
**не admitted swaps/candles/годовой corpus**. PumpSwap quarantine остаётся. Новых GT/Helius
requests0; no-paid основание — прежняя owner attestation+credential match, не provider bill.
Failed `textbook01`(capacity) и `archive-probe01`(preflight0RPC) sealed/STOP сохранены.

Cutoff17:27:37UTC: оба live service healthy,906source attempts,61 249bars в dashboard,
6сохранённых transport timeout/cooldown без нового fatalSTOP;400soak samples,span4.684h,
observed4.378h,≥1fresh89.75%,≥4fresh70.50%,старый maxgap1189.70s не скрыт. Суток ещё нет.
Новыхfits0/promotion0. Первый daily candidate не раньше **2026-10-05 13:41:54МСК** плюс
100new matured/hourscheduler; это прежний recipe, НЕ новый fullarchive/aux-head experiment.

Проверки644basePASS/6skips,670combinedMLPASS,check/smoke/build; offline reproduction exact.
[Итог/стратегия B11](../docs/brain-archive-curriculum.md),
[corpus](../docs/evidence/brain-archive-curriculum-2026-10-04.json),
[reproduction](../docs/evidence/brain-archive-reproduction-2026-10-04.json),
[health/failures/checks](../docs/evidence/brain-archive-qualification-2026-10-04.json).

**Следующий B12:** curriculum/streaming loader и полезная широкая historical campaign;
rolling vs full vs pool-day balanced train при общих absolute validation/test и прежних
features/models. До fits зафиксировать конкретный snapshot/steps/budgets; auxiliary heads
позже отдельной ablation. Разработка не ждёт суток, live runtime пока сохраняем для первого
daily review. Broad2025/dead-token claims требуют historical birth cohorts+decoder semantics.
Недельная activation не принята. Разделы B10 и ниже — исторические срезы.

## B10 — paper восстановлен после реального отказа supervisor, 2026-10-04

Активны **неизменный source/audit B09** и новая **paper/train/UI revision B10**.
[Control Center](http://127.0.0.1:8790). Roots: `work/brain-b09/source01`,
`work/brain-b10/paper01`; bundles `work/brain-b09/source-runtime01` и
`work/brain-b10/paper-runtime01`. Labels `ai.memecoin.brain.b09.{source,audit}`,
`ai.memecoin.brain.b10.{paper,train,ui}`. B09 paper/train/UI fenced, исходные файлы сохранены.
Expiry прежний **2026-10-07T12:23:51.922091Z** (15:23 МСК), GET budget 25000,
background 4000, USD0. Source activation/config/runtime/seed hashes не менялись.

13:35:45 UTC B09 paper получил `supervisor_guard`, exit −9 после 7,61s. Worker log пуст;
конкретная причина guard неизвестна. Не называть это установленным provider failure,
OOM или устранённой corruption. Две серии по 300 read-only guard проверок прошли;
полный ledger replay exact. Старый STOP/incident сохранены, `work/brain-b10/incident-evidence`
sealed. Отдельный source transport timeout `URLError/TimeoutError` прошёл cooldown и recovery.

B10 перенёс 85 ticks / 765 decisions / 5 exits / 6 books, без cash reset и изменения позиций.
На переносе open/pending=0. Prefix head `c72cc8d9b8929fff58f39f9173aaa5f3fabfef55e9e3a854ed8f8147c86d96f2`.
Новые ticks идут с фактическим временем, простой около 20 минут остаётся gap в soak.
Исходный source pin `f4e722ed1a27f702a7a303f33e56548eb50c5ba442524028021cf53db98d59e5`;
B10 paper pin `a15df723ce8dc2413bdda20eee13a9e62544f873125a4fccb9130c03d27bc67c` (code 9b3a9c4).

Safe guard diagnostics теперь сохраняют stage/type/errno/allowlisted reason, включая startup.
Paper recovery: только внешний SIGKILL/SIGTERM при `worker_failed`, максимум две reservation
за цепочку migration, cooldown60s, полный ledger/projection audit перед продолжением.
Unknown guard/integrity/deadline → STOP; training failures → TRAIN_STOP без повторов.
Исходный неизвестный сбой B09 **не** подпадает под automatic recovery. Его восстановление —
явная проверенная revision B10. Последующее усиление migration переносит TRAIN_STOP;
исходный B09 TRAIN_STOP отсутствовал, поэтому новая runtime installation для этого не нужна.

Модели/policy/features/targets не менялись. Новый training candidate допускается только после
completed result, полного sealed run и атомарной публикации отдельного ready marker.
Первый следующий fit не раньше **2026-10-05 13:41:54 МСК** и ≥100 новых mature samples;
hourly scheduler, максимум4 runs с учётом перенесённого B07, 4 fits/run≤600s. Новых fits0,
champion empirical прежний. UI показывает актуальные paper/source health и training gate
независимо от старого dashboard. Внешних provider probes B10 не добавлял.

Проверки: 640 base PASS/6 optional skips, 666 combined ML PASS, 52 focused; check/smoke/build,
реальный SIGKILL fixture после ledger commit, exact recovery, safe logging, retry exhaustion,
partial candidate exclusion, repeated migration, desktop/mobile без JS errors/overflow.
Cutoff14:05:31UTC: 10 новых ticks/108 decisions (все cash), новых trades0; 29 808 bars/24 pools,
62 captures во время paper STOP. Raw/index exact; 1764 raw joins/291 forecast joins,
272 matured outcomes независимо воспроизведены. Evidence manifest
`f9acebb864d7f2218a737a9eb855c461772479f1e07fd0aab92ecf6e5db19b89`;
дополнительные 30/30 healthy paper/source samples за 290,26s. Сутки не измерены.
Полный отчёт с зафиксированным cutoff и offline reproduction:
[B10](../docs/brain-operational-recovery.md), [evidence](../docs/evidence/brain-operational-recovery-2026-10-04.json).

**На сдаче B10 предлагался следующий B11** (новый выбор см. выше): сохранить действующий runtime; наблюдать реальные сутки и первый scheduled
refresh с будущими paired decisions. Параллельно квалифицировать независимую source lane,
не смешивая unsupported raw PumpSwap с GT candles. Weekly activation пока нет: неизвестный
operational failure не объяснён, резервного source нет, суток/планового retraining ещё не было.
F01/D43/holdout untouched; реальные средства/signing/платные подключения запрещены.
Ниже исторические срезы, а не текущие указания перезапустить старые jobs.

## B09 — paper восстановлен, активен ограниченный rollout на 72 часа, 2026-10-04

**Исторический срез сдачи B09: source, paper, trainer и UI работали.**
В B10 source сохранён, остановившийся paper заменён отдельной revision (см. выше). [Control Center](http://127.0.0.1:8790).
Roots: `work/brain-b09/source01` и `work/brain-b09/paper01`.
Expiry: **2026-10-07T12:23:51.922091Z** (15:23 МСК). Реальные деньги, signing и paid
не использовались; F01/D43/reserved holdout не открывались и не менялись. Старые B04/B08
STOP сохранены; B07 writers/UI остановлены при успешном переносе, артефакты сохранены.

Шесть hot pools выбраны до запросов по liquidity среди active B08. Всего в universe24.
Hot cadence120s, cold1800s; history/cold чередуются в background не чаще1/min и только
при запасе35s до hot deadline. Общий provider spacing12s (≤5/min). Source пока только GT;
автоматического fallback на Alchemy/Helius нет, raw PumpSwap ещё не admitted.

Qualification PASS:73GET,72success/1URLError→TimeoutError; новых429 нет. После warmup
103среза:≥1fresh в93,20%,≥4fresh в78,64%; каждый hot дал4новые closed candles,
max gap130,28s≤180s. Пять history GET успешны, raw/indexexact19 938bars. Были интервалы
с0fresh; это не100%fresh uptime. Fresh300s и отрицательный B08 gate не менялись.
После activation ещё один transport timeout вызвал автоматический cooldown30s и recovery
без ручного вмешательства. На итоговом cutoff source/paper/TRAIN_STOP отсутствуют.

Migration exact: прежние43ticks/393decisions/3closedtrades/6books, cash и model namespaces
сохранены. После переноса —19новыхticks,162решения,2входа и2выхода. Обе linear-версии
торговали один pool/интервал: один рыночный эпизод, не два независимых observations;
по−$1.707708 net при assumed costs. Champion empirical не заменён; новыхfits0.
Независимые972raw joins/185forecast joins и installed-wheel replay точные. Corpus snapshot:
21 069bars/24observed pools. Последующие live counters меняются, смотреть UI/health.

Source pin: `f4e722ed1a27f702a7a303f33e56548eb50c5ba442524028021cf53db98d59e5`.
Paper pin: `532c4c374c635c415c5e25a9d5d03c70323e60955d1853ef17024bfcea4d726c`.
Approval связан с config/runtime/qualification hashes; до него только25min/120GET lease.
Rollout≤25000GET, background≤4000,72h,raw8GiB/free4GiB,paid0. Унаследованы оба прежних
rate events; третий429/24h либо6consecutive transport failures→STOP. Бюджеты не обнулять,
STOP не снимать. Source heartbeat>180s/cooldown/STOP блокирует новые predictions.

Новые predictions только hot; остальные рынки остаются в corpus и market UI. Daily trainer:
≥24h+100новых mature samples,4fits/run≤600s, максимум4runs включая старыйB07run.
Checkpoint projection привязан к ledger prefix/resultSHA; полный audit/replay сохранён.
Synthetic864ticks/31 104decisions: full1,800s/checkpoint0,065s exact — не measured72h uptime.
Soak хранит actual timestamps/gaps; сон Mac не превращается в online availability.
649combinedML/623base PASS,6optional skips,35focused;check/smoke/build и desktop/mobile UI.

**План следующего B10 на момент сдачи B09 (заменён фактическим B10 выше):**
не переустанавливать runtime ради research. Собрать сутки soak,
проверить первый плановый fit, coverage/censoring и paired future behavior. Параллельно
готовить независимый hot-price cross-check/versioned PumpSwap admission. Главные ограничения:
single-provider freshness, небольшой календарный опыт и ещё не измеренный рост source journal
за несколько дней. Недельный/месячный unattended режим и edge не доказаны.
[Итог](../docs/brain-hot-lane.md), [evidence](../docs/evidence/brain-hot-lane-2026-10-04.json).
Ниже исторические срезы; B08 paused-status заменён B09 activation.

## B08 — источник не прошёл weekly activation, 2026-10-04

**B08 activation отклонена.** Новый source не запущен как LaunchAgent; cutover не выполнен.
B04 получил HTTP429 во время B08 discovery и штатно поставил STOP. Старые B07 paper/train/UI
jobs сохранены, Control Center http://127.0.0.1:8790 доступен, но source остановлен и новые
входы paused. Это не работающий непрерывный рыночный цикл. Старые STOP не стирать.

Новый `work/brain-b08/source01`: 24 admitted/observed pools, 21 active quality + 3 inherited
quarantine, 18 600 first-observed bars (+3 085 к seed), 464 indexed captures/58 843 versions.
Версии включают повторные значения: реально изменённых pool×candle 232, включая B04.
Максимальный span одного pool 14.68 days; цель до90 days не достигнута. История текущего
discovered universe не снимает survivorship/sampling bias и не получает прошлую online availability.

Три discovery success, четвёртый GET429; B04 отдельный429 через6s. Новый source наследовал
оба rate events и полный15min cooldown, снизил pacing до5/min. Затем32GET:29 live success,
2 history success,1 history URLError безHTTP status (низкоуровневая причина неизвестна),
новых429 нет. Первый freshness gate 0/6, финальный5/6 при24 dispositions/12new observed.
Оба результата сохранены, требования не снижены. Бюджет qualification исчерпан, source STOP
`qualification_freshness_gate_failed`; новых запросов после verdict0. ВсегоB08 36publicGET,
paid/RPC/newfits0. Это отказ текущего сочетания feed/cadence, не доказательство невозможности GT.

Инкрементальный SQLite first-observed index воспроизводится из immutable raw; full audit exact,
локальный single-run fullscan1.488s / index0.191s. As-of старого paper snapshot точный:516rawjoins,
131forecastjoins,43ticks,393decisions,3closedtrades,0open/pending,6books. СтарыйGRU total net
−$2.345041 при assumed costs; это накопленная B07 история, не результат B08 и не edge.
Installed-wheel replay exact. Реальный migration НЕ выполнен; cash/openposition/restart
migration проверен fixtures. Champion прежний, одна перенесённая в плане training версия,
новыхfits0. D43/F01/holdout не открывались/не менялись.

Проверки:616 base PASS/6optional skips,642 combinedML PASS,28 focused PASS,check/smoke/build.
Новый UI отдельно проверен на8791 с реальным B07 dashboard: noJS errors/overflow1440 и390px;
в продуктовый frozenB07 UI этот код не установлен. Source runtime pin
`8dad09e5985d71c8348d517ca1440c2061ad14639805c486f12f5c466e1ca1f5`, staged expiry
2026-10-11T11:32:39.985204Z не означает разрешённый rollout. Бюджет75000GET/7days/8GiB —
предел подготовленной конфигурации, не потраченная quota и не измеренный недельный uptime.

**Следующий B09:** восстановить полезный live paper feed: раздельные live-priority и history
бюджеты/cadence, небольшой active set с проверкой свежести на нескольких полных циклах,
старые рынки сохранять в denominator. Нужен новый явный bounded plan, не повтор B08 или
обнуление rate events. Raw PumpSwap/versioned semantics и альтернативный source cross-check
развивать параллельно; новые модели не приоритет. До week/month нужны устойчивый feed,
операционный soak и масштабирование полного journal/ledger replay.
[Итог B08](../docs/brain-continuity.md), [evidence](../docs/evidence/brain-continuity-2026-10-04.json).
Разделы ниже — предыдущие срезы; утверждение B07 о работающем B04 заменено этим статусом.

## B07 — живой virtual paper/shadow Brain, 2026-10-04

Пользователь явно разрешил paper и сменил приоритет на быстрый наблюдаемый product loop.
Реальные деньги/signing/paid запрещены; F01/D43/holdout неизменны. B06 raw quarantine
не снят, но не блокирует отдельный GT price-proxy paper. AGENTS/Memory отражают новое
разрешение. Free quota использовать по полезности данных, не ради месяцев ожидания.

**Paper, daily-training и read-only Control Center активны:** http://127.0.0.1:8790.
Root `work/brain-b07/paper02`, frozen runtime02 pin:
`ed32c47382e1cc9d0d4fe087ad448e27366fdfce86c0ea8587483484778b0b7c`.
Source — прежний B04: job/config/budget/expiry не менялись. Rollout до
2026-10-07T00:30:23.405597Z. D50 bundle и B04 проверены, protected data не открывались.
Это bounded virtual demo, не weekly unattended или калиброванное исполнение.

Итоговый slice 10:41:44–11:01:23 UTC: 17 ledger ticks, 183 решения, два входа/выхода,
шесть портфелей (три кандидата × две версии), открытых/pending на cutoff нет. Старый GRU:
две виртуальные сделки, net −$4.780619 после assumed costs; остальные выбрали cash.
Это evidence работающего pipeline, не edge. Snapshot `work/brain-b07/replay-final`,
manifest c9dd2266…; независимые 204 raw joins / 61 forecast joins / два model pins и
installed-wheel replay всех закрытий exact. 66 matured forecasts по четырём группам
version × horizon; winner/promotion по этой малой выборке не выбирался.

Первый training: четыре fits, 59.87 s, 10 605 matured samples. Новый bundle dab00e17…
выбрал GRU внутри development; champion 9ea4a6bd… empirical НЕ заменён. Следующий run
не раньше 24 h и ≥100 новых mature samples, максимум три runs. Paper PnL не execution
truth. Второй календарный fit пока не наступил. Исходные paper01/runtime01 сохранены
с supervisor incident и STOP; исправлены deadline ≤600 s и unique logs. Paper02:
27 workers без ошибок, mean 14.00 s / max 19.48 s; actual start intervals 41.09–50.17 s
при Launchd StartInterval 30 s. STOP/TRAIN_STOP отсутствуют.

20 paper tests; 608 base / 6 optional skips; 634 combined ML PASS, check/smoke/build.
Desktop UI без JS errors/overflow; на mobile 390 px есть horizontal overflow. Отдельная
UI worktree ce8505a чистая, не merge/reset. B04 продолжает сбор: 15 476 bars на UI-срезе,
дополнительных provider GET для B07 нет.
[Итоговый evidence](../docs/evidence/brain-paper-loop-2026-10-04.json),
[wheel/UI verification](../docs/evidence/brain-paper-loop-verification-2026-10-04.json).

**Следующий B08:** до expiry 7 октября подготовить многодневную source/runtime revision,
incremental canonical local store, полезное расширение history/universe и полные будущие
сутки для сравнения моделей/политики. PumpSwap ABI/indexed lane улучшать параллельно.
[План, рабочий контур и управление](../docs/brain-paper-loop.md).

## B06 — предыдущий срез, 2026-10-04

**PumpSwap production admission отклонён; foundation проверен, indexed live inconclusive.**
Plan eafed8e, acquisition7b4448b, fix/replayd6bb9f2. В4sealed B05tx найдено5PumpSwap calls;
integer transfers сходятся с vault pre/post balances во всех4tx. Но pinned official IDL
cb188ce… не исчерпывает payload:8unconsumed bytes у каждогоevent; всеbaseToken-2022,
remaining accounts не qualified. Buy_exact_quote_in event field101 против raw user104.
Причина schema/semantics drift неизвестна; все4tx quarantined, training_eligible=false.
Существующий CPMM/features/targets/runtime неизменны. Новыхfits0, champion прежний.

Новый indexed Alchemy probe:1RPC/100nominalCU reservation из440, ответ-32015 из-за нашей
ошибки: пропущен maxSupportedTransactionVersion=0. STOP/no retry соблюдён, страниц0;
не объявлять provider outage/noFree/cost saving. Параметр исправлен и fixture-tested,
live не повторён. Новый запуск требует отдельного plan/root/budget, исходный STOPнеудалять.
Owner no-PAYG/card attestation+samecredential; freshMCP8520CU/USD0 не planverification.

Новыйmemecoin_pumpswap: ABI/trace/transfer audit, durable bounded range ledger (raw seal
доcursor, restart budget, ordering/revision/nonprogress rejection, anchor≠complete).
Журнал проверен толькоfixtures; real indexed happy path/weekly supervision не qualified.
23newtests;588base/6skips,614combined; check/smoke/build/wheel exact. B04/D50verified;
D43/F01/holdout не открывались. Raw/evidence work/brain-b06 внеGit.

B04 на09:43:07UTC:STOPнет,debit248/12000,338forecasts/304scored/6censored/28pending.
Job/budget/expiry2026-10-07T00:30:23.405597Z неизменны. Sealed09:42:05 snapshot15364bars,
185revisions/126keys,4receipt-hour buckets одного дня. Shadow primary published_only290
scored,macro empirical0,006229/GRU0,007902/linear0,014785,3blocks/CIнет. Ещё12mature outcomes
вычисленыoffline(aggregate302), не online. Старыйhelper ставит recovery scored_ns=cutoff;
actual conservative review availability отдельно вshadow-review.json, не backdated.

**Следующий B07:** deployed PumpSwap ABI/mint-extension/remaining-fee profile qualification
и отдельный corrected bounded indexed probe. Только после семантики+cost/coverage —
source-local supervisor/небольшой многодневныйcollector, затем admitted corpus/training.
Не увеличивать модель/universe и не объявлять weekly unattended готовым.
[Отчёт](../docs/brain-pumpswap-events.md),
[evidence](../docs/evidence/brain-pumpswap-events-2026-10-04.json).

## B05 — предыдущий срез, 2026-10-04

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

### B13 final audit note

AUTO2cycles/recover synthetic integration3passed (learningstub, реальный domain snapshot/
exam), actual4fits/futureexam отдельно. Growth100 сравнивает livecontext rows с dataset
послеfoldpurge; это operationalproxy, не точный счётчик новыхtrainingdecisions (fixture740/734).
24h/6reservations/immutable dataset gates остаются; denominator унифицировать следующим
Codex до расширенияrollout. ManualPaper повторно допустим отдельнымexam, AUTOне дублирует.

### 2026-10-06 — проверки и evidence текущего этапа

`make setup`, `make check` (890 passed /11 optional skips), `make smoke`, `make build` — passed.
В отдельной ML-среде: 135 passed (brain/curriculum/vacuum/ml, включая tensor-cache parity).
Код: `520731f` forensic, `6431b50` historical vacuum/corpus, `e2e9262` loading/shadow trigger.
Final evidence: [brain-vacuum-2026-10-06.json](../docs/evidence/brain-vacuum-2026-10-06.json).
Полные raw/artifacts остаются вне Git в `work/brain-review-20261006/`; production gate не менялся.
