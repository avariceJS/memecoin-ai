# B06 — PumpSwap event semantics и экономичная raw ingestion

## Frozen план, 2026-10-04

B05 принят. Выбран PumpSwap:9/12 verified owners текущего universe, все4сохранённых raw
transactions. Приоритет — независимая от GT интерпретация событий и проверка экономичной
доставки, не новый backbone. B04/D43/F01/holdout/runtime/expiry неизменны; новыхfits0.

1. Зафиксировать официальный IDL/program provenance; читать только соответствующие
   byte-layouts, неизвестные extensions/инструкции fail closed. Раздельные статусы
   decoded/reconciled/unsupported/failed; никакого автоматического training admission.
2. Связать Anchor CPI events с конкретным PumpSwap parent instruction, pool/user/mints,
   account ordering, версиями legacy/v0 и actual inner transfers. Учитывать multihop,
   fees, multiple swaps, failed transactions; float balances не использовать как amounts.
   Token-2022/extensions вне первоначального supported profile, без имитации поддержки.
3. Fixtures с подменённым emitter/pool/amount, missing/truncated events, неверными depth,
   duplicate events, failed/null transactions и неизвестными schemas. Offline B05 replay
   с внешним pin088d61f3…; raw/actual timestamps не менять. Historical observation не
   превращается в прошлую online availability; execution model отсутствует.
4. Только после fixtures — один bounded indexed source probe Alchemy HTTP:
   getTransactionsForAddress, finalized/jsonParsed/full, два pages limit20 для первых
   двух B05 cohort pools (58hou…,8E1…), максимум4RPC/400nominalCU. При отсутствии cursor
   для pool — не искать замену. Один дополнительный standard getTransaction для первой
   returned signature первогоpool (40CU), всего≤5RPC/440CU,≤120s,timeout10s,max2MiB/response,
   ≥1s pacing. Первый provider/schema/clock failure→STOP, no retry/no alternative endpoint.
   Если метод недоступен — это результат, не повод купить upgrade или перезапустить probe.
   Exact bindings, durable reservation до сети; отдельный новый B06 budget USD0 при
   unchanged credential/no-PAYG owner attestation и fresh usage; не reuseB05/D51 allocation.
5. Сопоставить semantic payload со standard hydration и B05 overlapping signatures,
   считать полезные decoded events/CU, saturation/gaps, error dispositions. Probe не
   устанавливает full-history completeness, latency SLA или long-running activation.
6. Реализовать durable range/cursor progress contract: bounded restart, same-slot ordering,
   duplicate/revision/quarantine, незакрытые gaps и предотвращение silent cursor advancement.
   Применить к recorded pages. Wheel reproduction, check/smoke, Memory/Git.

Вердикт выбирается по evidence. Если payload/IDL расходятся, поддержка ограничивается
проверенным layout; не подгонять decoder молча. Если economical source недоступен, следующий
шаг должен закрыть этот bottleneck без недоказанного обещания сотен рынков. Недельный
сервис не активируется данным коротким опытом; B04 продолжает отдельный bounded сбор.

## Решение по B06

**Engineering foundation принят; production admission PumpSwap отклонён.** Получена
воспроизводимая интерпретация реальных transfer traces, но текущий public IDL не описывает
payload целиком. Token-2022 и remaining accounts требуют отдельной квалификации.
Indexed live qualification **не состоялась из-за нашей ошибки параметров запроса**;
метод не признан ни недоступным, ни экономически выгодным. Попытка завершена по stop budget.
B04 продолжает работать; новый collector/service не установлен, новых fits/promotion0.

Frozen plan `eafed8e`; acquisition/decoder code `7b4448b`; исправление и offline audit
`d6bb9f2`. После live ошибка исправлена только для будущего candidate: параметр
`maxSupportedTransactionVersion=0` добавлен и проверяется fixture transport. Failed
binding/raw/STOP не переписаны; в этой сессии повторного RPC не было.

## Что удалось проверить на реальных транзакциях

[Официальный IDL](https://github.com/pump-fun/pump-public-docs/blob/cb188ce08b5069196eef1f3e4a0c43b70099793b/idl/pump_amm.json)
зафиксирован commit `cb188ce08b5069196eef1f3e4a0c43b70099793b`, SHA256
`2091433899b07d003d98118ae6cd3c628960fd393b40710b6e15bce6d0e7f2d1`.
Полный документ и acquisition provenance находятся в sealed `work/brain-b06/official01`;
в коде сохранены только механические ABI facts. Pin документа не является доказательством
совпадения с текущей deployed program binary: эта проверка ещё впереди.

Использованы ровно4sealed B05transactions, без дополнительных hydration ради красивого
результата:3version0 и1legacy. Найдено5PumpSwap вызовов:3sell,1buy,1buy_exact_quote_in.
Одна транзакция проходит через два разных PumpSwap pools; второй pool не входит в
admitted universe, его movement сохранён как route evidence, не как расширение universe.
CPI parent определяется runtime stackHeight, не соседством строки Buy/Sell в логах.
[Anchor documentation](https://www.anchor-lang.com/docs/features/events) описывает
self-CPI events как отдельный механизм доставки; program/parent/account binding обязателен.

Во всех5событиях после разбора по pinned IDL остаются **8неинтерпретированных bytes**:
реальные полные payload lengths449(sell),497(buy),512(buy_exact_quote_in). Это size mismatch,
а не доказанное добавление поля строго в конец: изменение layout внутри структуры тоже
нельзя исключить. Поэтому разобранные fields остаются diagnostic interpretations.
Не присваивать неизвестным bytes имя по догадке и не принимать «совпало большинство полей»
за ABI qualification. Поля pool/user/token accounts сверены с конкретным parent instruction.

Все5вызовов используют Token-2022 для base и legacy SPL Token для WSOL quote; число
accounts превышает список pinned IDL. Ни transfer-fee/hook extensions, ни неизвестные
remaining roles не объявлены поддержанными. Даже успешное исполнение transaction не
доказывает корректность нашей экономической интерпретации всех fees.

Независимый **расчёт внутри тех же raw captures**, не independent-provider verification:
целочисленные transferChecked amounts, mints/decimals/program/authority и направления;
суммы по pool vaults совпали с pre/postTokenBalances во всех4transactions. Роутовые
переводы вне PumpSwap parent не включены в его movement. Несколько swaps одного vault
суммируются до проверки transaction-level delta. В fixture отдельный тест двух swaps.

| Pool / вызов | Base units | Quote user units | Quote vault units | External quote transfers |
|---|---:|---:|---:|---:|
| 58hou… sell | 6 072 309 | 104 | 107 | 3 |
| CnWY… buy_exact_quote_in, второй route leg | 2 873 567 | 104 | 102 | 2 |
| 8E1… sell | 49 041 121 642 | 975 628 | 987 781 | 12 153 |
| FXg… sell | 1 861 763 697 890 | 38 724 314 | 39 206 653 | 482 339 |
| Chhu… buy | 6 324 996 | 27 950 093 | 27 922 225 | 27 868 |

Base decimals6, quote9 в данном sample. External quote transfers — разница user/vault
потоков, **не полная execution cost**: LP fee остаётся в vault, network/rent/router fees
сюда не входят. Это не slippage simulator и не USD price series.

Для buy_exact_quote_in текущий IDL даёт `user_quote_amount_in=101`, тогда как три реальные
quote transfers составляют102+1+1=104. Instruction budget104 соблюдён; причина расхождения
event field не установлена. Оно может отражать ABI/semantics drift, не обязательно ошибку
провайдера. Простой перенос event field в training target исказил бы данные. Все4tx
получили `quarantined`, training_eligible=false, execution_supported=false. Ничего из этих
диагностик не добавлено в bar corpus и не использовано для обучения.

## Indexed source: точная причина остановки

[Alchemy method reference](https://www.alchemy.com/docs/chains/solana/solana-api-endpoints/get-transactions-for-address)
описывает full transaction pages; [таблица CU](https://www.alchemy.com/docs/reference/compute-unit-costs)
указывает100CU за метод. Наш frozen plan:2pools×2pages≤20transactions + одна standard
hydration; потолок5RPC/440CU,120s,USD0. Owner no-PAYG/no-card attestation и credential
mapping прежние; отдельная B06 allocation, не reuseD51/B05. Fresh MCP8520CU/USD0,
dataThrough09:36UTC — usage observation, не независимая проверка тарифного плана.

Фактически **1RPC,100nominalCU зарезервированы,0полученных transaction pages,0retries**.
Subprocess0,741s — время до ошибки, не latency benchmark. HTTP body сохранён до разбора:

```json
{"category":"rpc_error","code":-32015,"message":"Transaction version (0) is not supported by the requesting client. Please try the request again with the following configuration parameter: \"maxSupportedTransactionVersion\": 0","no_retry":true,"reserved_CU":100}
```

В первой реализации indexed config пропустил этот параметр; standard getTransaction его
имел. Это наша конфигурационная ошибка. Метод ответил JSON-RPC error, но этот ответ
**не доказывает Free-tier entitlement, полноту выдачи или полезные events/CU**.
Не классифицировать его как provider outage/платный blocker. Резервирование100CU не равно
проверенной provider billing charge за ошибку. Остальные340CU не разрешают обходить
заранее заданный first-error STOP. Новый corrected live probe требует собственного
плана/root/allocation, а не удаления STOP/замены исходного binding.

Источник тела ошибки sealed в `work/brain-b06/indexed01`; external manifest pin
`10afcea8fecc42e991706f0abd2069e8656798acdf6420b520060fc14e270e35`.
Исходный worker лог сообщает общий `source_or_integrity/SourceError`; отдельный safe audit
уточняет установленную причину -32015. Raw исходной истории не редактировался.

## Что приблизило unattended режим

Добавлен отдельный `memecoin_pumpswap`, без правок frozen collectors и execution adapter:

- Strict ABI inspection с явной quarantine новых размеров/неизвестных инструкций;
  integer transfer reconciliation, проверка emitter/parent/pool/mints/decimals/authority,
  failed/null/version/trace dispositions. Успешный fixture `reconciled_candidate` всё ещё
  не имеет training или execution permission.
- Durable range journal: raw seal до продвижения cursor, immutable hash-linked pages,
  сохранение max_pages после restart, проверка same-slot transactionIndex ordering,
  conflicting duplicate/revision/nonprogress cursor → отказ без advancement.
  Anchor reached отдельно от history completeness; невозможно автоматически закрыть gap
  только потому, что две страницы закончились. **Это fixture-qualified компонент,
  реальные indexed pages ещё не прошли его.**
- Изолированный indexed read-only client, без расширения allowlist frozen RPC-клиента;
  CU reservation до сети, raw payload/metadata fsync до availability, hard process deadline,
  no retry и no root reuse. Полная power-loss/недельная fault qualification не проводилась.
- Offline audit повторяет исходные interpretations и факт неудачной acquisition; installed
  wheel даёт тот же report. Actual raw receipt и время нового interpretation artifact
  различаются; blockTime не становится временем online-доступности.

Журнал намеренно малый: пересчитывает bounded ledger в памяти. Это не измеренная
инфраструктура миллиона событий/недель retention; adaptive scheduler, compaction,
backpressure, daily quota/disk accounting и fault supervisor не объявлены реализованными.

## B04 и shadow во время независимого B06

Frozen B04/D50 verified; D43/F01/holdout не открывались. B04 не переустанавливался,
expiry **2026-10-07T00:30:23.405597Z** и12000GET не продлевались. На handoff09:43:07UTC:
STOPнет, debit248,338forecasts,304published-scored/6censored/28pending. Последние три
cycles completed109,41/111,30/110,05s. Новых source incidents после B05 transport error
в наблюдённом срезе нет; это ещё не недели uptime.

Sealed corpus snapshot09:42:05.498129UTC:15364bars/12pools,185revision observations/126keys,
4receipt UTC-hour buckets одного дня. Shadow324forecasts; к cutoff опубликовано290scored
и6censored,28pending. Старый snapshot helper дополнительно вычислил12mature outcomes
offline: aggregate302scored, pending_after_offline_recovery16. **Не путать302 с числом
доступных online outcomes в cutoff.**

Для основного сравнения B06 взят `published_only`:

| empirical | GRU | linear | Часовых блоков |
|---:|---:|---:|---:|
| 0,006229 | 0,007902 | 0,014785 | 3 |

Это pool-macro pinball, меньше лучше. CIнет, champion unchanged; новыхfits0.
Aggregate с12offline recoveries:0,006132/0,007926/0,014733 — только вспомогательный срез.
Дополнительная temporal оговорка: старый helper ставит recovery `scored_ns` равным logical
snapshot cutoff, а не измеренному времени публикации отчёта. B06 не переписывает этот
immutable artifact/frozen source; `shadow-review.json` отдельно фиксирует фактическое
консервативное время доступности offline review. Эти12records не online evidence и не
основание model promotion. Future recovery tooling должно разделить cutoff и scoring time.

## Проверки, evidence и следующий шаг

23новых tests;588base/6optional skips,614combined; make check/smoke/build прошли.
Installed-wheel report exact,0network/0fits. Fixtures проверяют corruption, CPI attribution,
несколько swaps одного vault, unknown event tail, failed tx, failure charging, restart,
revision/order/cursor/no-progress/max-page boundaries. Полный реальный indexed happy path
не проверен. Routine lint исправления и diagnostic short-base58/доступ markdown403 не
являются market provider failures. HTML документация доступна; markdown не ретраился.

[Итоговое evidence](evidence/brain-pumpswap-events-2026-10-04.json) содержит pins raw/IDL/
audit/wheel/tests/B04 snapshots. Replay без сети:

```sh
.venv/bin/python -B -m memecoin_pumpswap.audit \
  work/brain-b05/raw01 work/brain-b06/indexed01 \
  10afcea8fecc42e991706f0abd2069e8656798acdf6420b520060fc14e270e35 \
  work/brain-b06/official01 work/brain-b06/new-offline-review
```

**Следующий оптимальный B07 — qualification реального protocol profile и один corrected
indexed-source probe.** Сначала установить deployed program/version и layouts/mint
extensions/token transfer fees/remaining accounts на ограниченном наборе. Официальный
IDL alone оказался недостаточным; без проверяемого объяснения неизвестных bytes поддержка
остаётся закрытой. Отдельный corrected bounded source plan проверяет v0, два pages,
standard overlap, cost per reconciled event и gaps. Не увеличивать universe до доказанной
семантики/стоимости. Если оба допуска пройдут — новый source-local supervisor и небольшой
многодневный paired collector; weekly unattended activation и ML refresh только после
его operational evidence. F01 не prerequisite; отсутствие semantic qualification —
реальный data gate независимого Brain track.
