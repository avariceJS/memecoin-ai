# Source qualification status

Status: **no market source or research segment qualified yet**. No payment, wallet or transaction submission is involved.

## Verified local probe

On 2026-10-01 the bounded read-only probe reached the public Solana mainnet RPC and received successful `getVersion`, finalized `getSlot`, and `getFirstAvailableBlock` responses. Raw replies and local HTTP receipt timestamps are stored under ignored `work/public-rpc-probe/`. This proves connectivity only. A first-available-block response does not prove every intervening block, pool state, token birth/death or historical arrival timestamp is available.

`ReadOnlyRPC` allowlists methods and limits timeout and response bytes. Endpoints come from an environment variable; URLs and API keys are not written to receipts, configs or run manifests. Provider responses themselves still need review before sharing. Commands do not load `.env` automatically.

## Architectural consequence

RPC captures remain raw evidence. There is deliberately no generic transaction-to-swap decoder: token transfers alone are insufficient to infer venue execution semantics. A qualified adapter must declare program/version, pool mechanism, account layout, fee rounding, token extensions, ordering and confirmation policy. Normalized events require explicit market and availability clocks. Historical block time is not historical agent receipt time.

Official reference checked during implementation: [Solana getBlock](https://solana.com/docs/rpc/http/getblock). Its response includes block/transaction data, with nullable block time; it is not an archive of what our agent observed at the time. [Python sqlite3](https://docs.python.org/3/library/sqlite3.html) supports the transactional local reference store, not an assumed scalable full-chain database.

## Next qualification evidence

Compare a bounded set of source combinations on actual samples: birth/death coverage, swaps and liquidity changes, historical account-state reconstruction, live/history correspondence, retention gaps, timestamp semantics, permissions and costs. Use a prespecified observation window and inclusion rule, not today's winners. Where possible compare a captured live interval with later backfill and independent state checkpoints. Choose a first venue adapter only after the required reconstruction inputs are demonstrated.

Missing credentials or paid access should remain an explicit dependency. This foundation does not select Axiom, a commercial provider, a final pool family or a trading horizon.
