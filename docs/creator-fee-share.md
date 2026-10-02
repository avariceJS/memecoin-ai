# AmmConfig creator fee share: подтверждённая ограниченная поддержка (D38)

Новое u64 по offset 116 — `creatorFeeShareRate`, не произвольный padding.
Официальный SDK commit `cc33ec28a8921a35609e83293e9e07ad830b0779`,
[src/raydium/cpmm/layout.ts](https://github.com/raydium-io/raydium-sdk-V2/blob/cc33ec28a8921a35609e83293e9e07ad830b0779/src/raydium/cpmm/layout.ts)
подтверждает расположение: после creatorFeeRate, затем 14 reserved u64.
Официальные docs commit `3699439bcad7ebdb7d65ba62cb896e93ecd3a204`,
[CPMM fees](https://github.com/raydium-io/raydium-docs-v1/blob/3699439bcad7ebdb7d65ba62cb896e93ecd3a204/products/cpmm/fees.mdx)
описывают смысл и момент применения. Проверенный public Rust program HEAD всё ещё
`59fb845…` и отстаёт от docs/SDK в этом поле. Его padding-разметка не полная текущая схема.
Копии official Git repos/source hashes сохранены вне Git в work/*-semantics.

Значение 50000 означает 5% накопленной creator fee, передаваемых протоколу **при collection**.
При settlement shared=floor(creator_counter*effective_rate/1e6), creator payout — остаток;
shared добавляется к protocol counter и остаётся в vault, creator counter обнуляется.
Оба counter исключены из effective reserves, поэтому перестановка долей не меняет
swap curve. Per-creator CreatorFeeShare PDA может переопределить config default;
эффективная ставка collection без этого PDA неизвестна. Ни swap fee, ни trade fee share
не увеличиваются на 5%.

Read-only независимое наблюдение: Helius вернул coherent three-account read обоих
configs с тем же word=50000, ранее наблюдавшимся через public RPC; slots различны, это
не same-bank cross-provider comparison. ProgramData metadata: upgrade_slot=452376460,
authority present. Это раньше наблюдённого изменения config; не доказательство точного
writer/update slot, версии ELF или reproducible build equivalence.
Первый public query текущего bounded probe завершился timeout20.331s; повтора не было.
Plan/requests/responses: work/config-semantics-probe-01, raw/hashes сохранены.

## Область поддержки

`decode_config(..., layout_version=2)` и seven-account checkpoint profile 2 признают
только это поле: целое 0..1_000_000; все bytes 124+ по-прежнему обязаны быть нулевыми.
Profile 1 остаётся прежним строгим decoder. Clock equality, account linkage, program
owners, mint/vault extensions и все temporal guards не меняются.

Новые forward plans явно выбирают config_layout_version=2. Live/offline используют
одинаковый профиль. Collection metadata сохраняется в economic observation отдельно
от swap Fees: default известен, override/effective rate неизвестны. Никакой raw response
не правится и не подменяется синтетическим capture.

V2 Checkpoint применяется только для snapshots и conditional swap quotes; его нельзя
передать в legacy transition/replay. Исторические audits сохраняют default profile 1.
Полная creator collection reconstruction требует отдельного PDA/history/CPI witness и
не объявлена поддержанной. Binary equivalence, inclusion/network costs и ML gates
остаются открытыми. Synthetic-only ReferenceReplay не меняется.

Проверки: реальные rejected раньше captures теперь разрешены только явным snapshot
profile 2; v1 по-прежнему отвергает их. Проверены boundaries, unknown remaining bytes,
Clock mismatch и запрет legacy replay. Независимая integer accounting проверка
подтверждает invariance reserves/quotes при документированном перераспределении fees.


## Prospective continuation и воспроизведение

Новый immutable plan `work/prospective-config-v2-01`: public RPC, прежние три пула,
3×20 rounds×10 s, максимум180 requests. Фактически 4 requests в двух сегментах:
2 valid seven-account captures, 2 timeout15.008/15.007s у следующего пула4GWh…QYfm.
Метод getMultipleAccounts, finalized/base64, minContextSlot452398741; HTTP/RPC code
отсутствуют, причина transport timeout не установлена. После первого отказа выполнен
один resume с cooldown; после второго серия закрыта, третьего сегмента нет.
Один слишком ранний локальный resume был отклонён cooldown guard до сети.

`work/config-v2-execution-targets-01`: 6 masked feature rows, 48 censored scenarios,
0 usable/mature decisions. `work/config-v2-quality-01` подтверждает temporal/accounting
инварианты; warmup/reset/gap rules сохранены. Между frames70.385s, coverage внутри
каждого одноточечного сегмента0s. Успешные HTTP responses0.795/0.692s, всего7048bytes;
chain age для usable features и conditional execution latency не измерены (нет eligible
rows). Два быстрых ответа не характеризуют доступность сервиса или trade latency.

7 RPC с probe вместе: 4 HTTP200 /3 timeout. Source/plan/capture/receipt/target/quality
hashes и безопасные ошибки: [evidence](evidence/creator-fee-share-2026-10-02.json).
327 tests, lint/format/mypy, smoke/build прошли. Campaign, targets, quality повторены
без сети из development package и `work/config-v2-wheel-01`; D36/D37 сохранены и
повторены оригинальными wheels. `diagnose-checkpoint ... --config-layout-version 2`
проверяет новую схему явно; default1 сохраняет прежний смысл результатов диагностики.

Следующий этап — доступность coherent source, затем новый длинный prospective corpus.
Нужна отдельная диагностика/изменение условий RPC перед новой серией; прежний Helius
Clock mismatch не исправлен. Переключение обратно само по себе не решает integrity.
ML/market qualification не достигнуты; полный collection replay не нужен для snapshot
quote domain, но не может считаться поддержанным без отдельного evidence.
