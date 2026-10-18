# Token-2022 real account evidence

Public finalized mainnet snapshots captured 2026-10-01. `manifest.json` binds raw
RPC responses and receipts. Three seven-account snapshots include Clock from the
same bank: metadata-only Token-2022 and two 300-bps transfer-fee mints. The separate
six-account `unsupported` sample contains hooks/confidential/default-state/delegate
extensions and must stay outside the allowlist. These snapshots alone do not prove
transaction math, source completeness, future fills or memecoin classification.

Two additional immutable sequences are selected from the verified pilot:
- all eight transactions of the first metadata-only interval with both endpoints;
- the first four transactions of the fee-bearing interval: incoming fees, harvest,
  then outgoing fees. This prefix has no terminal snapshot claim; the full interval
  is checked using the frozen journal outside Git.

Raw data date: 2026-10-01 MSK; audit completed 2026-10-02 MSK. Mutation tests work on
copies and remain synthetic. Full results and limitations: docs/token2022.md.
