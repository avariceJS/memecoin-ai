# Source qualification — 2026-10-01

**Bounded adapter audit completed; no market dataset qualified for ML or execution.**
No paid access, signing or transaction submission. Exact counts, slot ranges and report
hashes: [evidence](evidence/cpmm-2026-10-01.json). This is engineering evidence, not E01–E14.

## What was measured

| Sample | Transactions received | Pools discovered | Strict checkpoints | Vault flow comparisons |
|---|---:|---:|---:|---:|
| Final program-address cohort | 20/20 | 4 | 2 | 2 matched, from one transaction touching two pools |
| SOL/USDC control address | 5/5 | 2 | 1 | 2 matched, from two transactions |

All four final-cohort pools have structural inventories. Two were rejected by the strict
reserve decoder because they use Token-2022. One of the two supported pools pairs with WSOL;
the other is token/token. We have not verified token metadata/classification as memecoins.
The control pool comes from the official SDK example, is SOL/USDC and is not a memecoin.
Its transaction also discovers a second, unsupported pool; neither rejection is hidden.

Mainnet raw captures are in ignored `work/cpmm-*`. A ~178 KB control regression sample,
including requests, receipts, v0/v1 transactions, checkpoints and failures, is committed at
`tests/fixtures/cpmm_control`. Offline reproduction uses `mcai verify-qualification`.
Current inventories can also be reproduced from existing checkpoint captures with
`mcai inspect-cpmm <capture> --pool <id>`. Inventory is structural, not execution approval.

Earlier attempts are retained in the evidence list. `maxSupportedTransactionVersion=0`
caused RPC -32015 errors for actual v1 transactions. The updated reader requests version 1
and validates accepted legacy/0/1 envelopes. HTTP 403 was observed for Raydium pool-list
v1/v2 and a Dexscreener discovery request; no coverage or commercial-service conclusion
is inferred from those failures. Public RPC was sufficient for this bounded audit.

## Implemented boundary

- Latest finalized signatures of an explicitly recorded address; no selection by returns.
  Up to 30 signatures and 10 pool lookups/checkpoints, paced requests, timeout/byte bounds.
- Read-only account/transaction capture; endpoint/key excluded from receipts. Partial
  outcomes and unsupported pools remain in the report; no automatic retry conceals gaps.
- Pinned Raydium PoolState/AmmConfig decoding, owner/discriminator/length/padding guards.
- Pool + config + two vaults + two mints from ONE finalized getMultipleAccounts response.
  The preliminary lookup is for addresses only. No combination of reserves from different slots.
- Classic SPL Token reserve reconstruction subtracts protocol, fund and creator fee counters.
  Mint/freeze flags, pool status and open time are retained without declaring tradability.
- Token-2022 base structure and TLV extension type/length inventory; no extension payload
  economics, transfer fee/hook implementation or executable reserve approval.
- Parsed SPL transfer sums compared against pre/post token balance deltas. Both representations
  come from the same RPC provider. This is not independent-source corroboration or swap math.
- Offline raw/receipt checksum checks and reproduction of inventories, checkpoints and flows.
  CLI exit 1 flags partial/unsupported samples; exit 0 would still not certify market data.

## Temporal and coverage findings

A finalized context slot is a bank snapshot, not the last modification time of each account.
`minContextSlot` is a lower bound, NOT a historical account lookup. Raw HTTP receipt now is
not the time a historical agent could observe the transaction. No historical availability
is invented, and checkpoint inventory is not automatically converted into tradable Events.

The discovery sample is activity-selected and short. It cannot establish pool births/deaths,
absence of missed activity, historical retention, within-slot ordering, launch coverage or
market-wide proportions of Token-2022. Stable recent token balances alone cannot reconstruct
historical fee counters. No complete interval from an earlier checkpoint to a later independent
checkpoint was reconstructed. No provider SLA, historical latency, permission for bulk
redistribution or full-chain throughput was qualified. No payments were needed for these calls;
production data cost remains unknown. Public source revision was not verified against deployed
program bytecode. ReferenceReplay remains synthetic-only.

## Decision and next gate at the initial audit

Use Raydium CPMM as the first **data-adapter family**, not an approved trading universe.
Retain broad raw observations and explicit unsupported inventories. Do not remove Token-2022
assets from historical research silently, and do not treat all their extensions as ordinary SPL.
The present strict execution domain is only a candidate requiring further qualification.

Next: a bounded forward collection pilot with a declared discovery interval, durable cursor,
gap/reconnect recovery, explicit newly discovered/closed pools and account checkpoints.
Compare the captured interval with later backfill and reconstruct state between checkpoints;
obtain a second source if free access permits. Qualify Token-2022 extensions found in that
cohort separately. Acceptance requires accounted-for gaps and explained reconciliation
residuals; only then proceed to venue-specific execution math/calibration and research data.

## Primary references checked

- [Raydium pinned pool state](https://github.com/raydium-io/raydium-cp-swap/blob/59fb845a9e5bb569c8b2f3415f13b0c0ebcc6b92/programs/cp-swap/src/states/pool.rs),
  [config](https://github.com/raydium-io/raydium-cp-swap/blob/59fb845a9e5bb569c8b2f3415f13b0c0ebcc6b92/programs/cp-swap/src/states/config.rs),
  [swap account ordering](https://github.com/raydium-io/raydium-cp-swap/blob/59fb845a9e5bb569c8b2f3415f13b0c0ebcc6b92/programs/cp-swap/src/instructions/swap_base_input.rs).
- [Pinned Token-2022 framing](https://github.com/solana-program/token-2022/blob/5bc7ea3b9568b9fdac2d08c921d73d40260249c7/interface/src/extension/mod.rs).
- Solana [getMultipleAccounts](https://solana.com/docs/rpc/http/getmultipleaccounts),
  [getSignaturesForAddress](https://solana.com/docs/rpc/http/getsignaturesforaddress),
  [getTransaction](https://solana.com/docs/rpc/http/gettransaction),
  [transaction versions](https://solana.com/developers/cookbook/transactions/versions).

## Subsequent forward pilot

The bounded collection/recovery stage has now been run: see [forward pilot](forward-pilot.md).
It validates vault continuity for a fixed cohort and preserves the limitations above. The
qualification sample described here remains unchanged historical evidence.
