# Read-Only CPMM Control Fixture

Captured from public Solana mainnet RPC on 2026-10-01. JSON files contain exact request/receipt
times, parameters, response hashes, signatures, and slots. `spec.json` records selection before
collection; `report.json` records all outcomes. No key, credential, or wallet signing is included.
This is a small public-account/transaction regression sample, not a training dataset.

The discovery address is the SOL/USDC CPMM example in the official
[Raydium SDK demo](https://github.com/raydium-io/raydium-sdk-V2-demo/blob/master/src/cpmm/swap.ts):
`7JuwJuNU88gurFnyWeiyGKbFmExMWcmRZntn9imEzdny`. It was selected as a structural control, not by
returns; it is not a memecoin. One transaction also invokes a Token-2022 pool, retained with a
rejection in the report. Both version 0 and version 1 transactions are present. Discovery can
change; the fixture's exact pool selection is pinned in its request.

Offline verification:

```sh
mcai verify-qualification tests/fixtures/cpmm_control
```

Verification checks raw/receipt hashes and reproduces derived checkpoints and vault-flow
comparisons. It does not prove provider truth, complete coverage, transaction order within a slot,
historical receipt times, or executable prices. Expected: one decoded checkpoint and two
transaction-flow reconciliations; all research qualification flags remain false. The public
program source revision is a layout reference, not proof of the exact deployed revision.
