# Real CPMM regression evidence

Unmodified mainnet RPC captures, 2026-10-01, from the frozen forward pilot.
`manifest.json` binds raw responses/receipts and records their deliberate selection.
These are public addresses/transactions, not private wallet material.

- Prefix from checkpoint 452384086: first 11 successful transactions in canonical
  block order. Atomic failed transactions are omitted from this compact prefix;
  no terminal endpoint claim is made for the prefix. Includes exact-output swap.
- Complete five-transaction interval (452384747, 452384827]: three successful swaps,
  two atomic failures, both endpoint snapshots. Tests reconstruct vault balances,
  protocol/fund/creator counters and internal LP supply from the left endpoint.

The full 189-transaction journal and source-index/block evidence remain outside Git.
This fixture alone does not establish index completeness, independent source truth,
creator-fee behaviour (disabled in this pool), liquidity-change coverage, deployment
binary equivalence, or future executable fills. Mutation tests are synthetic corruption
of copies; original captures stay intact. See docs/protocol-audit.md.
