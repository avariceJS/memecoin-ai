# Token-2022 Account Evidence

Public finalized mainnet snapshots collected 2026-10-01. `manifest.json` binds raw RPC responses
and receipts. Three seven-account snapshots include Clock from the same bank: one metadata-only
Token-2022 mint and two 300-bps transfer-fee mints. A separate six-account `unsupported` sample
contains hooks, confidential/default-state, and delegate extensions; it must stay outside the
allowlist. These snapshots alone do not prove transaction math, source completeness, future fills,
or memecoin classification.

Two immutable transaction sequences are selected from the verified pilot:

- All eight transactions in the first metadata-only interval, with both endpoints.
- The first four transactions in the fee-bearing interval: incoming fees, harvest, then outgoing
  fees. This prefix makes no terminal-snapshot claim; the full interval is checked using the frozen
  journal outside Git.

Raw data date: 2026-10-01 MSK; audit completed 2026-10-02 MSK. Mutation tests use copies and are
synthetic. Exact semantics and limitations are in Git history for the retired `token2022.md`
report; the current validity boundary is [`docs/contracts.md`](../../../docs/contracts.md).
