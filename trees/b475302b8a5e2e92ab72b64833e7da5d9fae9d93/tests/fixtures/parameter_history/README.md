# Mainnet regression captures

Unmodified finalized RPC responses and actual local HTTP receipt metadata, captured
2026-10-02 (Europe/Moscow). All file hashes are in manifest.json.

- `epoch-schedule`: mainnet schedule; normal epochs start at slot/epoch zero, no warmup.
- `lp-0-tx-1`, `lp-0-tx-3`: withdraw/deposit of pool 3pb9vkSkhFp9Y4fNp1rXanFhYUqCoKEztvy2eU9KXX3d.
- `lp-1-tx-0`, `lp-1-tx-1`: withdraw/deposit of SOL/USDC control 7JuwJuNU88gurFnyWeiyGKbFmExMWcmRZntn9imEzdny.
- `external-withheld`: withdrawWithheldTokensFromMint for the previously observed
  Token-2022 mint, to an external recipient (not a tracked CPMM vault).

LP witnesses use event-provided pre-reserves and LP supply. They do not establish
checkpoint-anchored history or individual fee counters. The external withdrawal
checks isolation of the tracked vault ledger, not full mint-wide withheld accounting.
Receipts are collection times, never original historical availability.
