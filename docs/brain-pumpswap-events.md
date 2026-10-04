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
