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

## Real validation

The measured mainnet run and its limitations are recorded in the evidence section after the
bounded collection/recovery run completes. Existing qualification evidence is in source-audit.md.
