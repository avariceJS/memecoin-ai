# D40: bounded Alchemy HTTP / HTTP+WSS

План v1 заморожен до runtime запросов: [конфигурация](../configs/alchemy-pilot-v1.json).
Три прежних pool, finalized, profile2. Два параллельных режима Alchemy и прежний
public HTTP collector как контроль; максимум два 120-секундных сегмента, отдельные
процессы. Проба Alchemy HTTP: genesis + два обхода когорты (7 запросов). Первый
неожиданный отказ останавливает серию, без автоматического retry или fallback.

HTTP-only: 5 s. Hybrid: coalesced dirty-account trigger не чаще 5 s, контрольный
snapshot каждые 15 s. Подписки на pool/config/vaults/mints, root для liveness.
Новый Clock читается только вместе со всеми dependencies через HTTP. При reconnect
новая connection generation, acknowledgements всех subscriptions, затем HTTP anchors.
До anchor уведомления сохраняются; slot выше anchor остаётся dirty. Это handoff
для snapshot-trigger режима, НЕ доказательство lossless transaction stream.
Нет write sequence/cursor: silent loss нельзя полностью обнаружить, heartbeat только
ограничивает stale state. Нельзя объявлять перескок slot пропущенным account update.
Disconnect/overflow/restart оставляют исторический gap; recovery даёт лишь новые
observations с фактической поздней availability и новым warmup.

Бюджеты и критерий выигрыша зафиксированы в JSON. Никакого расширения выборки по
результату. Поддержка snapshot/quote profile2 не расширяет creator collection replay.
Общий CausalBook, decoder и targets используются без смены формул. Нет обучения,
торговли, подписи, оплаты, upgrade, QuickNode или возврата к D33.

Официальные документы, проверены 2026-10-03:

- [accountSubscribe](https://www.alchemy.com/docs/reference/account-subscribe): один
  account, base64, explicit finalized; numeric subscription id, без replay cursor.
- [Solana PubSub](https://www.alchemy.com/docs/reference/solana-subscription-api-endpoints):
  streaming endpoint, account/program/log/signature/root/slot/block методы; bandwidth billing.
- [Общие лимиты](https://www.alchemy.com/docs/reference/subscription-api): заявлены
  100 connections FREE / 2000 прочие, 1000 subscriptions/connection, 200 concurrent
  requests, batch1000. Это документационные ceilings, не проверенная квота данного app.
- [getMultipleAccounts](https://www.alchemy.com/docs/chains/solana/solana-api-endpoints/get-multiple-accounts)
  и [Solana](https://solana.com/docs/rpc/http/getmultipleaccounts): один context,
  до100 accounts; minContextSlot — lower bound, не запрос historical bank.

MCP list_apps/select_app/getGenesisHash подтвердили доступ к единственному существующему
app и mainnet. Runtime credentials отдельно найдены в ignored `.env.local`; они не
выводятся/копируются в evidence. Старое Alchemy raw evidence в work не найдено.
