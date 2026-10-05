# Brain Development B01: независимый exploratory track

## Уточнение 2026-10-06 — результаты первого автономного периода

[Forensic review](brain-experience-review.md) и [historical vacuum](brain-vacuum-results.md)
имеют приоритет над более ранними описаниями активных B10/B12/D43 tracks.
AUTO B13 сохраняется; новый corpus отделён от USD train до qualification coverage/units.
965 registry/event markets не означают расширение 24-market train. Новых fits этой сессией
не выполнялось. Adaptive support trigger и streaming loader квалифицированы отдельно,
live cutover пока отсутствует. Следующий шаг — historical admission, затем data-only candidate.


## Решение и стартовый план — 2026-10-04, до сбора и обучения

Прямое указание пользователя открывает historical backfill, расширение universe,
continuous corpus, регулярное supervised training и shadow evaluation. Прежний запрет
market/model-development training заменён для этого track. F01 readiness больше не
является его gate. Frozen F01/D43, reserved holdout и их правила не меняются; результаты
Brain нельзя представлять confirmatory F01 или доказательством edge. F01 calendar
activation остаётся самостоятельным решением, не критическим путём Brain.

Первая вертикаль Brain v1 — распределение будущих returns наблюдаемой цены, baselines
и temporal candidates; не portfolio/trading policy. Reuse D44 PyTorch encoders/losses,
D47 immutable ledger/checkpoint principles. Другие targets/encoders экспериментальны.
Historical OHLCV не переделывать в fake CPMM execution labels. Протокол исполнения
CPMM не расширяется поддержкой market-data bars других пулов.

1. Проверить официальные public API и сделать bounded keyless discovery/history probe.
   Public pool ranking отражает текущую выборку/survivorship, не universe прошлого.
   До12новых Solana pools, F01 fixed3pools исключены из independent collection. Freeze
   admission time и pool/mint metadata; изменения состава — новая universe revision.
2. Historical lane: до3страниц×1000закрытых5min bars/pool, реальные retrieval timestamps
   и response bytes immutable. Bar event time отдельно; availability прошлого неизвестна.
   Chronological purged retrospective evaluation явно hindsight/model-development;
   результаты не online claims. Только доступные к training cutoff raw используются.
3. Prospective lane: опрос каждые5min, first-observed closed bar versions, без overwrite
   исправленных bars. Predictions пишутся до target availability, поздние данные не
   создают старые predictions. Gaps/censoring сохраняются. Shadow — прогнозы и proper
   losses, без simulated orders/paper PnL, подписи/торговли.
4. Начальный supervised protocol: empirical baseline, linear и GRU(width16,context8),
   seeds7/19, horizons5/15min, до10epochs/candidate, train/validation/test60/20/20 по
   глобальному event time с group maturity purge. Все pools делят global cutoffs;
   scaler train-only, выбор по validation, test diagnostic exploratory. Model registry
   immutable; champion автоматически не становится trade policy.
5. Отдельный Brain runtime до72hours с max12000public GET, interval≥3s/request,15s HTTP
   timeout, bounded responses2MiB, disk floor4GiB и Brain storage cap2GiB. Paid budget0,
   никаких credentials/upgrades/new paid sources. HTTP failure history immutable;
   no immediate retry,2consecutive failed acquisition cycles→stop. Не обходить auth/429.
   First source failure closes that source probe; fallback только documented public source.
6. Recurring supervised cycles не чаще6h, только при новых закрытых данных; fit budget
   ≤4neural fits/cycle,≤12cycles/72h, wall900s/cycle, threads1/CPU initial. Shared host
   limits, STOP/expiry/code pins, crash/no duplicate cycles и source/label leakage tests.
7. Реальный backfill→training→label-free inference→prospective shadow path, fixtures и
   failure tests до фонового запуска. Runtime из frozen snapshot, отдельный job/root,
   D43 не переустанавливать. Если public source недоступен, сохранить точный access
   blocker и продолжать независимую часть, не выдавать fixture за реальные данные.

Цель на ближайшие дни — первая содержательная exploratory Brain v1 с расширенным
corpus и будущими shadow observations. Срок не гарантирует качество или edge; число
необходимых режимов/токенов устанавливается evidence, не F01 календарём.

## Source и refinement до первого обучения

Выбран keyless GeckoTerminal API: наблюдаемая USD цена base token,5min OHLCV,
`include_empty_intervals=false`, закрытые bars. API beta/cached1min, rate approximately
10requests/minute: [официальная OpenAPI](https://api.geckoterminal.com/docs/v2/swagger.json),
[public API](https://www.geckoterminal.com/dex-api). Реальные receipt times сохраняются;
cache/confirmed feed не объявляется Solana finalized checkpoint. Поддержка market-data
PumpSwap/других DEX не означает execution adapter этих протоколов.

Discovery4pages дал12пулов (round-robin4/4/4 в age buckets<1d/1–7d/≥7d), известные SOL/
USDC/USDT base и fixed F01 pools исключены; liquidity при discovery≥$10000, один pool
на base mint. Это стартовый B01 scope, не universal/admissibility порог стратегии.
Nullable liquidity исключается с причиной, не считается нулём. Первый parser failure
сохранён, metadata повторно разобраны offline без дополнительных запросов.

Исторический probe остановлен наHTTP429:4пула,3484полученных закрытых rows,3482unique
first-observed bars после duplicate/revision audit. Данные остальных8pools pending,
не считаются отсутствующими на рынке. Новые provider requests отложены минимум900s
и на Retry-After, если он больше. Дальнейшая частота ограничена≥12s/request вместо7s
первого backfill; это снижение нагрузки в ответ на429, не обход лимита. Failed request
учитывается первым из2consecutive failed acquisition cycles. No immediate retry.

Стартовое retrospective60/20/20:1791train/595validation/1050test decisions,18purged;
числа не power claim. Линейный и GRU candidates — ensemble обоих seeds7/19, не выбор
удачного seed. Validation pool-macro pinball; simplicity tie≤1%, priority empirical,
linear,GRU. Unused execution-class heads D44 не обучаются и не выдаются за fill probabilities.
Data contract имеет собственные12bar features и25input channels; CPMM schema не подменён.
Historical event-lag channel не называется receipt age. Исторические actual availability
остаются временем загрузки, chronological diagnostic использует отдельную event-time ось.

Runtime collection worker имеет hard240s,training worker hard600s (строже планового900s),
per-fit cooperative195s/ledger reservation200s, threads1. Parent-death lease и process
group deadline переиспользуют D50 supervisor. Один due cycle, crash не повторяется;
service независим от D43. После72h network/training прекращаются, automatic renewal нет.


## Фактический результат B01 — 2026-10-04

Это работающий Brain-v1-dev forecasting path, ещё не торговая policy и не доказанное
преимущество. Initial4fits: linear/GRU×seeds7/19,10epochs,CPU1thread,4.922s весь worker.
D44 encoder/loss и D47 ledger/checkpoint reuse; fixed F01 runner не запускался.

| Candidate | Validation pool-macro pinball | Exploratory test pool-macro pinball |
|---|---:|---:|
| Empirical | 0.01009019 | 0.04061468 |
| Linear ensemble | 0.01690002 | 0.04025514 |
| GRU ensemble | 0.01126939 | 0.03299933 |

Выбран **empirical** по заранее заданному validation правилу. Лучшее test значение GRU
не меняет выбор задним числом. Test содержит4пула, train/validation сильно несбалансированы
по возрасту/пулу; initial corpus не позволяет заявлять generalization. Обе learned модели
сохранены и дают paired shadow forecasts вместе с baseline на одних input observations.
Train cutoff actual1791074515596023000ns; finished1791075073154602000ns. Execution model
`none; observed price log returns`, не CPMM fills/PnL.

Первые два frozen prospective cycles:134.464/139.863s,24successfulGET; корпус4267first-version
bars по12пулами,8revision observations.48predictions,12scored outcomes и36pending в срезе
`status-final-01.json`. Descriptive pooled pinball на этих12dependent outcomes:
empirical0.00890023,GRU0.00878604,linear0.02103840. Это одна короткая будущая выборка,
без CI/power/победителя. Историческая глубина остальных8pools ещё pending: recent tail
из live endpoint не завершает исходный deep backfill.

Третий цикл завершился через15.953s с `public_http_failure`, `URLError`, HTTP status null,
`no_retry=true`; subtype DNS/TLS/connect не установлен. До него history probe получил429.
Все request reservations считаются расходом, включая ошибки. В срезе37requests total
(включая initial external probe),34successful root captures,2failed; probe success отдельно.
Это1consecutive acquisition failure после двух успешных циклов, stop threshold2 сохранён.

«Закрытая свеча» означает лишь nominal interval end≤actual receipt. Независимое подтверждение
provider candle finality отсутствует; cache/revisions видны в raw. First-observed snapshot
сохраняется даже при более позднем исправлении provider. Event-time retrospective training
не утверждает, что такой snapshot был доступен исторически. Это наблюдаемая цена,
не атомарное состояние пула и не execution-quality label.

## Воспроизведение и эксплуатация

548tests с optional Torch,525base/3optional skips;ruff/format и существующий strict mypy
на59файлах,synthetic smoke,build/install wheel. Новые Brain modules не входят в strict
mypy target.21new tests покрывают causality/gaps/purge/scaler, budget/scope/cooldown,
fail-closed runtime, crash/no-overlap, tamper, checkpoint и future-label perturbation.
Из установленного wheel восстановлен initial dataset точно;4дополнительных reproduction
fits дали tensor-exact weights и exact metrics/selection.48saved predictions воспроизведены
с max abs error4.10e-8 (tolerance1e-5 из-за batched inference),12outcomes exact. Эти4fits
не новые selection trials; raw/reproduction outputs не перезаписаны.

Команды из `/Users/root1/Desktop/memecoin-ai`:

```sh
.venv/bin/python -B tools/brain_status.py
work/ml-venv/bin/python -I -B tools/brain_reproduce.py --installed-path work/brain-b01/installed-wheel-01 --output work/brain-b01/reproduction-NEW
# При необходимости остановить именно Brain:
touch work/brain-b01/dev01/STOP
```

Reproduction требует нового output path и не выполняет network requests. Status читает
только Brain metadata; фоновые публикации атомарны, но весь снимок не transactional.
Raw/секреты внеGit, публичные source URLs keyless. `work/brain-b01/dev01` содержит
requests,universe,cycles,training,models,shadow; первичный dataset отдельно
`work/brain-b01/dataset-initial-01`. Evidence хранит hashes immutable artifacts/срезов,
не притворяется seal постоянно растущего root.

Установлен `~/Library/LaunchAgents/ai.memecoin.brain.dev01.plist`, отдельный GUI-session
LaunchAgent. Runtime manifest pin:
`8d49becface75853f6a8d89ada3dd44c0be18015a934d34c76c3381d17a0a23c`;
initial model manifest:
`9ea4a6bd7977fb66fe4a18eba54ed7eaed0afd65810ec99c75aec3e1443e3920`.
Expiry2026-10-07T00:30:23.405597Z (03:30:23МСК), total≤12000GET; не более12training
cycles×4fits,≥6h и только новые данные. Job может остановиться раньше по STOP, quota,
disk, expiry, code/clock failure либо двум consecutive acquisition failures. Training
failure блокирует следующие automatic training attempts; data collection может продолжаться.
Нужны awake Mac,network и GUI login. Питание/сон/системные настройки не менялись;
послеexpiry network/training прекращаются, plist остаётся registered, auto-renewal нет.
Runtime source/config/universe/interpreter pins проверяются; это не container isolation
и не pin всех бинарных dependencies ОС/PyTorch. Их version сохранена в fit binding.

**Решение:** продолжать независимый Brain development и его bounded runtime; не ждать
F01 и не выпускать trading policy. Следующий **B02**: разные временные окна/пулы,
недостающий backfill и sampling bias, revision/censor/coverage audit, новые learning curves
и paired future shadow для model selection. Дополнительные backbones/features — после
этой диагностики, не догматический переход на GRU. Продление/изменение deployment —
новая versioned инженерная оценка в рамках разрешённого track, не правка frozen root.

[Сводное evidence](evidence/brain-development-2026-10-04.json) содержит проверки, source,
локальные artifact paths/hashes, первые результаты и limitations. D43/F01/holdout,
старые CPMM features/targets/configs не менялись;AlchemyRPC0,QuickNode0,paid0,trade orders0.


### Дополнение перед передачей

Следующий плановый цикл `1791076500` завершился успешно за139.359s без изменения frozen
runtime и без immediate retry. Срез `status-handoff-02.json`:4291bars/12pools,
19revision observations,72paired predictions,36scored/36pending; total49reservedGET
(46successful root captures +2failed +1external successful probe). Последовательность
failures сброшена успешным новым циклом; ошибки сохранены. Сервис продолжает работу
в прежних bounds. Основной reproduction выше относится к48predictions/12outcomes,
новые24predictions/24outcomes в тот replay не включены. Эти обновлённые counts не меняют
выбор модели или ограничение на статистические выводы. Evidence содержит оба среза.
