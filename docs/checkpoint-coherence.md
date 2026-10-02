# Coherent prospective checkpoints (D37)

Старые Helius responses с Clock.slot=context.slot+1 остаются rejected. В reference
[Agave getMultipleAccounts](https://github.com/anza-xyz/agave/blob/master/rpc/src/rpc.rs)
выбирается один bank, из него читаются все accounts и context.slot. В
[Bank::update_clock](https://github.com/anza-xyz/agave/blob/master/runtime/src/bank.rs)
Clock.slot равен bank slot. Это обосновывает существующий guard; deployed Helius
backend/version этим не идентифицирован, upstream root cause остаётся неизвестным.
Снятые исходники и hashes: work/checkpoint-coherence-research/sources.json.

Склейка разновременных счетов, подстановка Clock/context и расширение допуска на один
слот не используются. Simulation adapter не понадобился; торговые транзакции, подпись,
оплата и новые credentials не задействованы.

Bounded source probe: те же три pools, до двух rounds на endpoint, timeout 10 s,
spacing 2 s, no retries, stop each endpoint on first failure. Public RPC 6/6 strict
same-bank checkpoints; Helius Gatekeeper первый getMultipleAccounts timeout 10.061 s,
HTTP/RPC codes unknown, серия остановлена. Gatekeeper разрешает existing key по
[официальному описанию](https://www.helius.dev/blog/introducing-gatekeeper); его
успешная работа и независимость backend не подтверждены. Raw requests/responses,
errors/plan/manifest: work/coherence-source-probe-01. Это current-state qualification,
не возвращение к закрытой whole-chain historical trace задаче D33.

Для следующей bounded кампании выбран Solana public RPC с прежним strict decoder.
Шесть ответов не доказывают production readiness или отсутствие будущих ошибок.
Каждый последующий ответ проверяется, invalid не попадает в observations; first-error
stop и bounded resume сохраняются. Отдельные receipts не претендуют на общий bank
разных пулов: cross-pool inputs допускаются только по своей availability/freshness.

Новые campaign plans schema 2 закрепляют `endpoint_origin` (HTTPS scheme/host/port,
без path/query/key). Resume проверяет его до запросов даже при совпадении alias;
смена источника требует нового плана и warmup. Origin не является доказательством
backend identity или криптографической account-state proof. Явно закрытая кампания
не возобновляется. Старые bundles проверяются pinned wheel/source, не переписываются.
