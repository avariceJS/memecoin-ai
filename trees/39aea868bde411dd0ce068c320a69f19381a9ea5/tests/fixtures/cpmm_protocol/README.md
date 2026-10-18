# Real CPMM Regression Evidence

Unmodified public mainnet RPC captures from the frozen forward pilot, collected 2026-10-01.
`manifest.json` binds raw responses/receipts and records deliberate selection. No private wallet
material is included.

- Prefix from checkpoint 452384086: the first 11 successful transactions in canonical block
  order. Atomic failures are omitted from this compact prefix; it makes no terminal-endpoint
  claim. Includes an exact-output swap.
- Complete interval `(452384747, 452384827]`: three successful swaps, two atomic failures, and
  both endpoint snapshots. Tests reconstruct vault balances, protocol/fund/creator counters, and
  internal LP supply from the left endpoint.

The full 189-transaction journal and source-index/block evidence remain outside Git. This fixture
does not establish index completeness, independent source truth, creator-fee behavior (disabled
in this pool), liquidity-change coverage, deployment-binary equivalence, or future executable
fills. Mutation tests corrupt copies; originals remain intact. See the historical
[`protocol-audit` source](../../../docs/INDEX.md) and Git history for the original detailed report.
