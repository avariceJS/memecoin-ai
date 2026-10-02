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

## Новый blocker после source preflight

Первый запрос новой public кампании (work/prospective-public-coherent-01) имеет
context=Clock=452687445, однако AmmConfig CRRS… содержит u64=50000 по offset 116.
Последний public preflight того же config в slot 452686853 имел нулевые bytes 116+.
Это наблюдаемый переход между двумя ответами; точный write slot, инструкция и причина
изменения не установлены. Ни число, ни расположение не доказывают смысл новой комиссии.

Заранее известная classic когорта использует другой config D4FPE… . Первый из двух
planned контрольных запросов (work/coherence-classic-probe-01) получил то же неизвестное
reserved слово, при context=Clock=452688467. Второй запрос не отправлялся. Это не
выбор успешно прошедших пулов по будущей доходности; обе исходные когорты сохранены как
неподдерживаемые. Дальнейший перебор/collection остановлен.

[Текущий публичный AmmConfig source](https://github.com/raydium-io/raydium-cp-swap/blob/master/programs/cp-swap/src/states/config.rs)
объявляет эту область padding; [update_config](https://github.com/raydium-io/raydium-cp-swap/blob/master/programs/cp-swap/src/instructions/admin/update_config.rs)
не объясняет новое слово. Снятые копии/hashes лежат в work/checkpoint-coherence-research.
Структура deployed binary не считается доказанной совпадением публичного source.
Изначальный URL update_amm_config.rs был ошибочным (404); корректный update_config.rs
получен и проверен, это ошибка пути документации, не chain RPC.

Bounded metadata probe work/cpmm-program-version-01: getAccountInfo(PROGRAM) прошёл,
ProgramData=DMawCQzbgNTmbzaESc7o6pvL1KAeetY8zA7jNpzntHhU. Следующее finalized
getAccountInfo с dataSlice offset=0,length=45 завершилось timeout, без retry. Upgrade
slot/authority и соответствие бинарника source этим probe не установлены.

Новые raw captures добавлены как regression fixtures: bank equality должна проходить,
но economic decoder по-прежнему обязан отклонять reserved bytes. Нельзя заменять config
старым значением, угадывать 50000 как fee, переносить metadata из другого bank или
обходить guard для увеличения sample. В новой кампании 0 accepted observations,
3 missing/error feature rows и только censored scenarios. Шесть удачных preflight
captures не превращаются задним числом в live features.

## Следующий инженерный слой

`checkpoint_diagnostics.py` различает Clock mismatch и unsupported config bytes,
сохраняет source/raw hashes и uninterpreted reserved words. Его output имеет отдельный
kind и не допускается в CausalBook как observation. `corpus_quality.py` на verified
campaign/targets независимо проверяет maturity/cutoff, строгие endpoint availability,
segment boundaries и inventory; считает coverage, gaps, censoring и отдельные HTTP,
chain age и observation delays. Это не модель включения транзакций, train gate false.

Следующий реальный gate — выяснить deployed семантику AmmConfig reserved bytes и
версионировать поддержку только после независимой проверки состояния/операций, либо
подтвердить ошибку выдачи config источником. До этого длительный economic prospective
corpus остаётся незавершённым. Public источник — bounded working candidate, не production
qualification; прежняя Helius backend inconsistency не объявляется исправленной.
