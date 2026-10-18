# Real read-only regression fixture

Captured 2026-10-01 from Solana public mainnet RPC. Exact request/receipt times,
request parameters, response hashes, signatures and slots are in the JSON files.
`spec.json` declares selection before collection; `report.json` records every outcome.
No keys, endpoint credentials or wallet signing are involved. This is a small public
account/transaction regression sample, not a training dataset.

The discovery address is the SOL/USDC CPMM example in the official
[Raydium SDK demo](https://github.com/raydium-io/raydium-sdk-V2-demo/blob/master/src/cpmm/swap.ts):
`7JuwJuNU88gurFnyWeiyGKbFmExMWcmRZntn9imEzdny`.
It was chosen for structural control, not returns; it is not a memecoin.
One transaction also invokes a Token-2022 pool, retained with a rejection in the report.
Both version 0 and version 1 transactions are present. Source discovery can change;
this fixture's exact pool selection is pinned in its recorded request.

Offline: `mcai verify-qualification tests/fixtures/cpmm_control`.
Verification checks raw/receipt hashes and reproduces derived checkpoints and vault
flow comparisons. It does not prove the provider told the truth, complete coverage,
transaction ordering within a slot, historical receipt times or executable prices.
Expected: 1 decoded checkpoint and 2 transaction flow reconciliations; all research
qualification flags remain false. Public program source revision is a layout reference,
not proof that this exact revision was deployed.
