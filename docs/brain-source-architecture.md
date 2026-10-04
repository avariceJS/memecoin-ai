# B05 — источники и устойчивость Brain перед масштабированием

## План до новых runtime RPC — 2026-10-04

Пользователь изменил приоритет: сначала source architecture и unattended reliability,
а не очередной model experiment. B04 frozen collector продолжает работать в своём
исходном envelope; его runtime/STOP/retry правила не меняются. F01/D43/holdout закрыты.
Новых fits и model promotion в B05 не планируется. Linear-winsor остаётся кандидатом.

Сначала read-only audit B04 cycles/quarantine/revisions/coverage и действующих источников.
Затем изолированная diagnostic raw lane: finalized Alchemy HTTP, один getMultipleAccounts
для owner inventory всех12Brain pools, два signature pages(limit20) для четырёх заранее
выбранных адресов: stale58hou…, stale8E1…, ambiguousFXg… и первый healthyChhu….
Для каждого — максимум один getTransaction последней successful signature. Не искать
более «удобные» адреса/transactions при пустом/null ответе. До13RPC/500nominalCU,
hard180s,timeout10s,max1MiB/response,≥1s spacing; первый source/integrity error→STOP,
без retry или второго candidate. Raw/actual request/receipt/available times immutable.
У всех diagnostic данных historical availability unknown; ingestion не создаёт прошлый
online state. Account references не равны swaps; balances не заменяют venue decoder.

Это новая отдельная B05 allocation≤500CU/USD0 в рамках автономного read-only Brain
research, **не reuse D51 receipt/allocation**. Основание no-paid: owner attestation этого
же аккаунта/noPAYG/no card/30M, сверка credential fingerprint, свежий MCP usage8020CU/USD0
(dataThrough09:04UTC). Usage ранее был немонотонным: не использовать delta как measured
charge и не считать MCP доказательством Dashboard plan. При несовпадении credential,
исчезновении локального доступа или сомнении no-charge — runtime probe не проводить.
Никаких subscriptions, upgrades, trial enrollment, paid streaming или торговых RPC.

Результат probe не заменяет collector: показать activity/metadata coverage и реальные
границы semantics. Нужен provider-neutral append-only event evidence contract с actual
availability, duplicate/revision handling, account-reference versus decoded-trade gates,
bounded pagination/gaps, source-local health decisions и независимым fatal integrity.
Проверить fixtures, recorded/live offline reproduction и wheel. Не объявлять просто
новый vendor primary без verified venue decoding и prospective coverage evidence.

Рассчитать capacity/budget для12/100/300pools с явными предположениями; отличать HTTP
metadata/control от массового transaction data plane. Официальные Alchemy/Solana/Helius
документы проверить; Helius рассматривать как candidate, доступ/оплату не предполагать.
Итог: решение о роли GeckoTerminal и raw lane, конкретный следующий этап, Memory/Git.
