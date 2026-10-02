# Реальный Helius getTransaction с обрезанным trace

Неизменённые response/receipt из `work/helius-trace-probe-01/capture`, mainnet
slot 452408962, получены 2026-10-02. Request: finalized, jsonParsed, max version 1.
Manifest содержит hashes. Endpoint/key отсутствуют в capture.

В logMessages есть точный runtime marker `Log truncated`. Этот ответ подтверждает
необходимость отдельного completeness guard даже при одинаковых responses.
Он не является полным trace и не доказывает независимое agreement. Полный первичный
getBlock вне Git: `work/helius-range-01/blocks/000-reference`; tx index 433.
Не создавать getTransaction capture из извлечения этой транзакции из блока.
