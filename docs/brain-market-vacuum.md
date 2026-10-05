# Market vacuum: поэтапная квалификация после forensic review

2026-10-06. [Исходные измерения](brain-experience-review.md).
Это новый exploratory Brain track, не изменение frozen B13 или F01.

## Протокол до новых provider RPC

Сначала Alchemy indexed full pages по PumpSwap program, по2 страницы limit100 из каждого
заранее выбранного historical интервала, ascending: 2025-04-01, 2025-07-01,
2026-01-01, 2026-09-01 (каждый день UTC). Failure status any; никаких сегодняшних winners.
Начальный bound:8pages+до4standard hydration cross-check, ≤960 nominal CU/12RPC,
10MiB/response,1s pacing,300s deadline,USD0. Первое provider/schema/integrity failure
закрывает campaign, сохраняет reservation/STOP. Повторы в том же root запрещены.
Raw availability = actual receipt, historical blockTime никогда не online availability.
Транзакции с program reference ещё не swaps/births; неподдержанные layouts quarantine.

Основание no-paid — сохранённое owner attestation и exact credential fingerprint из B11,
не MCP usage delta (снова немонотонна, теперь0). Helius MCP подтверждает configured key,
но plan unknown: не выполнять signup/upgrade или считать доступ verified.
GT live клиент сохраняется; параллельный обход его frozen limiter не допускается.

После первых pages: сравнить decoded useful events/CU/bytes, venue/profile coverage и
creation incidence. Если приемлемо, отдельный versioned масштабный envelope до100000CU,
≤1000pages,≤10GiB raw с остановкой при low usefulness и без обхода source errors.
Это потолок разрешённого исследования, не обязательство выкачать quota.
Каждый расширенный campaign должен связать exact addresses/date-ranges/decoder pins
до RPC. Не обещать year coverage по четырём малым samples.

## Архитектурные границы

Provider adapter → immutable capture → journal cursor/gap debt → versioned decoder →
canonical events/market birth registry → sealed shards → dataset → fixed-recipe candidate.
Один program page маршрутизируется во все встретившиеся pools; signature+instruction path
задаёт identity. Неизвестный decoder и conflicting revision не продвигают admission.
Failed transactions сохраняются отдельно и не становятся executed swaps.
Дедупликация global; restart не обнуляет budget; pending reservation не бесплатный retry.
Read-only tracking конкретного pool/mint — адресный range этого же пути, не Axiom API.

Canonical unit/quote namespace обязателен: WSOL prices не смешивать с GT USD bars.
Сначала поддержанный economic profile и проверка gaps; затем новые train cohorts.
Данные birth registry сохраняют и недавние, и исчезнувшие markets; отсутствие нынешнего
account не считается rug. Migration-only выборка имеет graduation bias, который надо
показать рядом с survivorship bias. Protocol support не означает execution calibration.

Multi-Mac позже: immutable shard/model transfer и versioned API; один SQLite writer
на store. LLM node может читать incident/corpus summaries, но не менять модели, policy
или admission. Его установка сейчас не приоритет.

## Qualification amendment после program-probe01, до дополнительных RPC

Четыре даты вернули0rows/400CU; result/manifest сохранены. Это не absence-of-trades.
Следующий независимый control probe: global_config `ADyA8…`, event_authority `GS4CU…`
из всех пяти B05 raw calls и прежний B11 control pool `2AXX…` на2025-04-01;
PumpSwap pool `Chhu…` на2026-10-04. По2pages+1corecheck/case, те же12RPC/960CU/300s.
Данные/адреса выбраны до запроса; новый root, предыдущий результат не заменяется.
Цель — квалифицировать address index и historical protocol layouts, не повторить пустую
страницу до успеха. Helius account read подтвердил лишь API-key auth, plan/usage unknown;
signup/upgrade не выполнялись.

Address controls: config/event_authority пусты, B11 pool2025 дал200tx, PumpSwap pool2026
дал200tx, core equality прошла. Итого680CU, qualified swaps0 по прежнему strict decoder.
Следующий targeted birth-cohort probe использует documented Pump withdraw_authority
`39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg` из official
[Pump README](https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_PROGRAM_README.md).
Он входит в migrate и может дать births эффективнее program index; это гипотеза,
не объявленная полнота. Те же4dates/2pages/corecheck/960CU/300s, отдельный root.
Graduation и authority-version bias остаются; текущие winners не используются.

## Cohort campaign v1 — зарегистрировано до bulk RPC

Exact birth decoder profiles подтвердили199/800tx,4dates,0layout errors у create_pool.
Следующий root `work/brain-review-20261006/monthly-births01`: Pump withdraw_authority,
18UTC dates first-of-month2025-04-01..2026-09-01,ascending,3pages×100fulltx/date,
status any, finalized/jsonParsed/version0. ≤54RPC/5400nominalCU/540MiB,
≤600s deadline,1s between requests, first errorSTOP. Стандартные corechecks4/4
предыдущегоprobe сохранились; это не доказательство полноты indexedprovider.
Все даты/лимиты/code hashes записываются до calls. No paid/PAYG; exactownercredential.
Admit только birth registry metadata после exactABI+CPI+eventroles+newpoollamports+
vaultmint/decimals. Не доказана liquidity accounting, не swaps, не trainingready.
Cursor и gap debt сохраняются на каждом page; raw receipt и durable availability различны.
При crash неподтверждённый request остаётся charged и не повторяется автоматически.

## Historical swaps qualification v1 — до hydration новых pools

monthly-births01:865metadata pools,5400tx/54RPC/5400CU/90,791,326bytes/58.30s.
Это18date samples, не18 полных месяцев. Все18 cursors имеют оставшийся gap debt.
Exact historical swap profiles уже reconciled14swaps в сохранённых probes, без новых RPC.
Следующий root `monthly-pool-history01`: earliest(event_s,pool) birth в каждом из18месяцев
каталога, первые3pages×100tx в диапазоне [birth,birth+86400), ascending.
54RPC/5400CU/540MiB/600s; первое failureSTOP. Выбор не использует будущие outcomes.
Canonical swap candidates обязаны пройти exactprofile+token transfers+vault net balances.
Неизвестные suffix/Token2022/extensions/remainingaccounts остаются quarantine.
Quote units сохраняются WSOL/USDC; USD обучение не смешивать с quote-denominated ценой.
По результатам оценить shared fee-account stream: одна страница → несколько pools.

Preflight correction:865births представлены17месяцами, за2025-11 в трёх страницах
0admitted births. Это не отсутствие рынков. `monthly-pool-history01/STOP` сохраняет
preflight failure,0RPC. Новый root `monthly-pool-history02`,17earliest pools,
51RPC/5100CU; остальные ограничения прежние. Не выдумывать missing cohort.

## Information trigger v1 — shadow preregistration

До изменения AUTO: измеряемый trigger в shadow, recipe/scaler/policy прежние.
Обычная ветка: ≥1000 genuinely-new eligible train decisions, effective pools≥8,
≥8new pool-days,≥128disjoint context+target windows,≥1h since last reservation.
Быстрая ветка:≥20000new decisions,effective pools≥50,new pool-days≥50,
≥1000disjoint windows,≥10min. Это engineering support proxies, не estimator mutual information.
Отсчёт new IDs — против ВСЕХ прежних train/validation/test eligible, с текущимtrainpurge;
перенос прежнего validation вtrain не считается новым опытом. Freshness≤10min,
exact snapshot/model pins, quota available, no source/domain STOP обязательны;
missing/stale metrics fail closed. Ошибки модели пока descriptive, не train-on-test trigger.
Не включать gate в старый AUTO до qualification его нового snapshot admission path.
Raw historical swaps/registry births не повышают eligible counters.

## Shared fee-index qualification — до RPC

Historical pool probe:4919tx/5100CU,1353strictly reconciled swaps/8pools,
19incomplete quote-bar candidates. Большая часть2026layouts остаётся quarantined.
Ни один бар пока неUSD training-ready. Следующий `fee-stream01`: fixedfeeATAs
`94qWNrtmfn42h3ZjUZwWvK1MEo9uVmmrBPd2hpNjYDjb` и
`7GFUN3bWzJMKMRZ34JLsvcqdssDbXnp589SiE33KVwcC`, обнаруженные в reconciled historical
transfers, на2025-05-01/2025-07-01/2025-10-01UTC;3pages×100/address/date,
18RPC/1800CU/180MiB/600s. Это индекс fee flow, не полный program stream; coverage
другихfee recipients/quotes/версийunknown. Цель измерить many-pool routing/CU,
без per-pool запросов. Outcomes по существующим markets не участвуют ввыборе.
