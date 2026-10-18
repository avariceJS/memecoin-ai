# Mainnet Parameter-History Captures

Unmodified finalized RPC responses and local HTTP receipt metadata, collected 2026-10-02
(Europe/Moscow). File hashes are in `manifest.json`.

- `epoch-schedule`: mainnet schedule; ordinary epochs start at slot/epoch zero, with no warmup.
- `lp-0-tx-1`, `lp-0-tx-3`: withdraw/deposit for pool
  `3pb9vkSkhFp9Y4fNp1rXanFhYUqCoKEztvy2eU9KXX3d`.
- `lp-1-tx-0`, `lp-1-tx-1`: withdraw/deposit for SOL/USDC control
  `7JuwJuNU88gurFnyWeiyGKbFmExMWcmRZntn9imEzdny`.
- `external-withheld`: `withdrawWithheldTokensFromMint` to an external recipient, not a tracked
  CPMM vault.

LP witnesses use event-provided pre-reserves and LP supply. They do not establish
checkpoint-anchored history or individual fee counters. The external withdrawal checks isolation
of the tracked vault ledger, not mint-wide withheld accounting. Receipt times are collection
times, not original historical availability.
