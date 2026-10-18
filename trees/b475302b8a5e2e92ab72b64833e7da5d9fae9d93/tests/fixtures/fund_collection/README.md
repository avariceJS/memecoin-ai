# Mainnet fund-fee collection witnesses

Three unchanged finalized `getTransaction` captures from Solana public RPC, collected
2026-10-02. Requests, source aliases, receipt times and response checksums are retained.
`manifest.json` registers raw files; no signed transaction or credentials are included.

Found by inspecting eight successful transactions from the fee authority
`FUNDduJTA7XcckKHKfAoEnnhuSud2JUCUZv6opWEjrBU`, using its current config identity only
as a retrospective search hint. This is not point-in-time token selection.

Pinned Raydium CPMM source: `59fb845a9e5bb569c8b2f3415f13b0c0ebcc6b92`,
`programs/cp-swap/src/instructions/admin/collect_fund_fee.rs`.
The witness checks instruction limits, direct CPI transfers and vault/recipient metadata.
Token-2022 deductions are observed; historical tariffs and prior fee counters are unknown.
No independent checkpoint reconstruction or provider agreement is implied.
