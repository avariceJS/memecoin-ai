# Training support expansion — 2026-10-06

Цель: довести historical cohorts до train и frozen future comparison, не увеличивать модель.
B13 operator04/runtime06 остаётся работающим control. F01/D43 не читать.

## Probe до новых calls

Alchemy birth catalogue monthly-births01-catalogue определяет universe, без current ranking.
GT qualification: 2 earliest births в каждом месяце2025-04,2025-10,2026-04,2026-07,
2026-08,2026-09; 12GET, по1странице1000закрытых5mUSD candles доbirth+3days.
Точные addresses/base mints/code/config hashes сохраняются доcalls. Никакой фильтрации
по нынешней liquidity/успеху. Empty/404 остаются отрицательным coverage evidence.
Неизвестная schema/duplicate bars не допускаются; receipts фактические, прошлой online
availability не приписывать. Проверить metadata base/quote против on-chain birth.

Новый registered provider envelope заменяет прежний запрет параллельного GT probe:
старый pinned source имеет≥12s spacing (≤6GET/rolling60s), новый≥16s (≤4GET/60s),
совокупный доказуемый верхний bound≤10GET/60s против опубликованного30/minute.
Frozen source config SHA проверяется докаждогоcall; его budgets/limiter не сбрасываются.
Сторонние GT workers не добавляются. Cooldown старого source обязателен и длянового;
HTTP429/401/403 или transport/schema failure закрывает новыйcampaign безretry.
404 только одногоpool — admission failure, безповторного запроса. ≤24MiB responses,
deadline300s/expiry30min,USD0. Старый AUTO/source не останавливать.

Helius account read снова дал API-key configured/plan unknown; signup/upgrade не выполнять.
Alchemy остаётся verified archive/discovery provider; public RPC — candidate verification
lane, не обещанный wholesale archive. Выбор bulk после измерения training-eligible support.

Источник rate policy: https://apiguide.geckoterminal.com/faq (поисковый snapshot30/min;
текущий URL перенаправляет на CoinGecko docs). На практике новыйlane ограничен4/min.

## Admission probe01 outcome / amendment до второго probe

Первый GET (2025-04 birth) получил HTTP401 с явным ответом: Public API ограничен
последними180days; старше — платный Analyst. STOP сохранён, повторов нет, upgrades нет.
Следующий отдельный probe02 использует ТОЛЬКО законный public диапазон: по2earliest births
2026-05/06/07/08/09, 10GET, даты birth+3days внутри180days. Это новый cohort, не повтор
отказанного диапазона/endpoint с другими credentials. USD2025 остаётся raw admission задачей.

## Probe02 outcome / bounded bulk admission

Первые5GET дали1341eligible decisions/5pools. ШестойGET получил429; STOP/no-retry.
Опубликованный30/min НЕ оказался гарантией. Обязательный cooldown900s от receipt
1791260812341818000: следующий lane не раньше1791261712341818000.
Работающий source после этого healthy, его последнее обращение успешно; конфиг неизменён.

Новый конечный campaign:100 ранее не запрашивавшихся pools, по20изMay–Sep2026,
SHA-ranked внутри месяца и round-robin между месяцами. Все ранее attempted pools исключены.
Не retry429 pool и не смена endpoint/key для обхода лимита. Новый spacing≥30s (≤3start
включая обе границы60s); дополнительный yield≥12s после видимой reservation старогоsource.
Это lower-load qualification, а НЕ обещание доступногоrate. Любая429/401/403/transport
закрывает campaign. Старый source cooldown/STOP также закрывает его. Per-pool404/empty/
schema mismatch сохраняется как negative/quarantine; integrity gate не ослабляется.
≤100GET/200MiB response bytes,320MiBstorage,4GiBfreefloor,0USD; deadline каждогоchild45s,
leaseEOF kills child, фиксированная wall expiry и immutable reservations; automatic retry0.
Исторические gaps не zero-fill, текущая liquidity/success не участвуют в membership.

## Data-only experiment, preregistered before fitting

Один consistent source backup/cutoff. Control24market corpus и тот жеcorpus+admittedbirths.
Все новые historical origins должны быть раньше train boundary. Validation/test memberships
и значения обязаны совпасть byte-for-byte междуarms. B13-pool-day-v1 неизменён:12features,
context8,linear/GRU16,seeds7/19,1000updates,48ktrain cap,pool/dayweights,empiricalcontrol,
validation1% simplicity selection. Всего8новыхfits(4perarm),≤850sperarm; это отдельный
exploratory experiment, не debit/retry существующих B13 reservations. Результат любого
family сохранить; повторятьfits по неудобномуtest запрещено. FrozenB13 также reference.

Послеfreeze — отдельныйfuture exam на общих input receipts: frozenB13/control/broad,
всеempirical/linear/GRU predictions, прежняяfixed15mvirtualpolicy.24hилидоsourceexpiry.
Не объявлять improvement поdescriptivetest/несколькимfuturepoints. Никакихpromotions.
Послеoffline qualification возможно exactruntime revision, подключающее immutable
admitted corpus к следующим AUTO snapshots, с сохранениемconfig/quota/books/ledgers/gates.

## Transport recovery amendment (до нового worker)

Bulk01:10successfulGET/246eligible/7positivepools, затемURLError на11-мGET;
не429, source healthy. STOP/captures остаютсяsealed. Bulk02 продолжает только89ещё
не запрошенных cases из ТОГО ЖЕ preregistered100, с pin предыдущегоcampaign и debit11;
не повторяет неудачныйpool, не продлевает исходныйdeadline. Послеtransport gap cooldown120s.
ИзолированныйURLError/TimeoutError/ConnectionResetError теперь сохраняется как gap данного
case; прочиеcases можно продолжить.5transportfailures суммарно(включаяbulk01) либо3подряд
останавливаютcampaign.401/403/429/otherHTTP по-прежнему STOP; no automatic retry.
Это исправляет чрезмерно хрупкое завершение большогоbackfill после единственной сетевой
ошибки, не меняет data admission или работающийsource/operator.

## Provider role verification

Один public Solana `getAccountInfo` (finalized/base64) для уже выбранногоJune pool
6N1et9sdNoMvkTxHaMuPZ7ziZrQM6odSGGFbSsne6to:15stimeout,1MiB,1RPC,no retry,0USD.
Только проверка owner существующегоaccount; ответ не влияет на membership/выборпобедителей,
не доказывает historical completeness и не заменяет price feed. Endpoint
https://api.mainnet-beta.solana.com. Ошибка сохраняется, другие endpoints не перебирать.
