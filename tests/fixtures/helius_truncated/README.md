# Truncated Helius `getTransaction` Trace

Unmodified response/receipt from `work/helius-trace-probe-01/capture`, mainnet slot 452408962,
collected 2026-10-02. Request was finalized, `jsonParsed`, max transaction version 1. Manifest
contains hashes. Endpoint and key are absent.

The logs contain the exact runtime marker `Log truncated`. This response shows why a completeness
guard is required even when responses match. It is not a complete trace and does not prove
independent provider agreement. The full primary `getBlock` is outside Git at
`work/helius-range-01/blocks/000-reference`; transaction index 433. Do not create a
`getTransaction` capture from that extracted transaction.
