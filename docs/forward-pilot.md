# Forward collection pilot

The pilot collects an explicit fixed cohort of 1–4 already-known classic SPL CPMM pools.
It tests data recovery and vault accounting, not trading, memecoin classification or full
pool discovery. No signing, paid access or neural training is involved.

## Workflow

```sh
export MEMECOIN_RPC_URL=https://api.mainnet-beta.solana.com
mcai collect-pilot --store work/pilot/journal.sqlite --source-alias solana-public \
  --pool 3pb9vkSkhFp9Y4fNp1rXanFhYUqCoKEztvy2eU9KXX3d \
  --rounds 3 --interval 20 --budget 150
mcai recover-pilot --store work/pilot/journal.sqlite --source-alias solana-public \
  --budget 300 --rpc-interval 1.2 --recovery-page-size 100
mcai audit-pilot --store work/pilot/journal.sqlite --out work/pilot/audit-01.json
mcai export-pilot --store work/pilot/journal.sqlite --out work/pilot/frozen-01
mcai verify-pilot work/pilot/frozen-01
```

Re-run collect without `--pool` to continue the same journal; configuration is immutable.
Recovery page size/pacing are operational overrides, recorded in actual requests. Commands
are finite: bounded rounds, request attempts and response bytes. A round consists of pool
checkpoints followed by recovery; `--interval` is a pause between rounds, NOT a promised
sampling period. A slow backlog can dominate this period. `--snapshots-only` explicitly
separates account polling from transaction recovery for controlled gap tests.

CLI reports pending scans, transactions, block orders and pools lacking an interval. Exit 0
from collection means these jobs are presently clear, not that consistency or coverage passed.
Use audit for reconciliation. Audit/export paths are exclusive and cannot overwrite results.
Collectors are single-writer (Unix file lock); readers use SQLite read snapshots. Export uses
SQLite backup and includes a checksummed journal, deterministic report and audit source copy.
Raw data and exports remain outside Git. Old receipts never gain earlier availability on retry.

## Correctness boundary

- All six accounts for each checkpoint are read from one finalized bank. Lookup responses
  determine addresses only. Null/unsupported accounts create issues; they are not zero reserves.
- Each adjacent interval is `(S, E]`: all transactions in start bank S are already included.
- Pool and BOTH vault addresses are paginated. Empty pages before the lower boundary leave a
  gap, rather than proving there was no activity. Cursor and discovered signatures commit together.
- Identical finalized pages can serve adjacent intervals when their requested minContextSlot
  is at least each interval's E. Each scan is separately filtered and audited against those receipts.
- Transactions deduplicate by signature. Block signature order, not timestamp or signature sort,
  defines intra-slot order. Missing tx/block data keeps the interval unverified.
- Starting vault balances are advanced by transaction pre/post deltas. Each pre-balance must
  equal the reconstructed balance. A mismatch is retained as drift; no reset hides it. Terminal
  balances must equal the later account checkpoint. Failed transactions cannot change tokens.
- The report separately exposes the observed change in excluded fee counters. Those counters
  and full historical pool account state are NOT reconstructed. Neither are executable quotes.
- Account checkpoints and transaction metadata come from the SAME provider. Passing means
  internal consistency conditional on its address index; missed net-zero activity can escape it.
  Birth/death coverage, independent-provider verification and market qualification remain false.
- A closed or missing vault requires a separate lifecycle decoder; it is never silently valued
  at zero. Token-2022 remains structural inventory in the earlier audit tool, outside this pilot.

## Real validation — 2026-10-01

[Machine-readable evidence](evidence/forward-2026-10-01.json) pins the counts, interval
bounds, residuals and artifact hashes. The cohort was fixed before capture: one SOL/token
pool from the previous adapter audit and the official SOL/USDC control pool. Neither this
choice nor the token metadata qualifies a representative memecoin research universe.

- 10 finalized checkpoints over **211.299 seconds** of checkpoint receipt time; 8 adjacent
  pool intervals. Two intervals were nonempty; six were empty according to the address index.
- 189 distinct transactions: **46 successful and 143 failed**, 131 v0 and 58 v1; 15 blocks.
  Failed attempts were retained and their token balances checked, not counted as swaps.
- 24 address scans closed at their lower boundaries. All 189 transactions and block orders
  were eventually available. No pending jobs remained.
- All 8 intervals passed sequential pre-balance continuity and endpoint equality. Both vault
  residuals were zero in every interval, including the interval with 184 transactions.
- The saved gap-open report was inconsistent/incomplete; after recovery the same checkpoint
  endpoints passed. The run included intentional deferred transaction ingestion, budget stops,
  process restart and real source errors. These are not all described as network outages.
- There were 56 issue records: 53 earlier transport errors without an HTTP classification,
  two budget stops, and one explicitly recorded HTTP 429 during block recovery. The final block
  succeeded on a later request. Old failures remain visible; there is no retroactive diagnosis.
- Small initial pages and a discovered CLI tuning-wiring bug caused excessive requests before
  correction. Total 538 stored replies and 335 page applications are NOT a production efficiency
  benchmark. Defaults/overrides and shared-page reuse are tested; continuous backlog and
  source-limit adaptation still need a longer operational pilot.

The raw journal and ~8.7 MB frozen archive are ignored under `work/forward-pilot/`.
`frozen-final` includes checksums, the journal, deterministic audit report and auditing source.
Both the development package and installed wheel reproduced its report offline. Repeated
recovery after completion used zero RPC requests. The summary in Git references the raw
artifacts by hash; the complete real dataset is deliberately not committed.

**This proves limited internal accounting consistency, not complete market capture.**
Pool fee counters changed in the interval but were only observed at endpoints. There is no
full protocol-state replay, independent provider, Token-2022 execution support, launch/closure
universe coverage, calibrated executable price, OOS model or trading edge.

## Next practical gate

Decode protocol transitions for the same fixed CPMM domain: distinguish successful swaps,
liquidity operations, fee collection and failed attempts; reconstruct fee counters as well
as vault balances. Validate those transitions and integer quote math on held-out real
transactions/checkpoints. Independent-source/index-completeness checks remain a parallel
data-quality dependency; successful vault reconciliation must not be used to waive them.

Primary RPC semantics checked: Solana
[getSignaturesForAddress](https://solana.com/docs/rpc/http/getsignaturesforaddress) and
[getBlock](https://solana.com/docs/rpc/http/getblock). Finalized bank/account semantics and
pinned protocol references are recorded in [source audit](source-audit.md).
