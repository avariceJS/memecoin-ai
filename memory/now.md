# Current Project State

**As of:** 2026-10-11 08:18 UTC. Research remains on `research/active`; production
`/Users/root1/Desktop/memecoin-ai` is untouched. Recheck Git and runtime before new acquisition.

## Runtime and credentials

- The existing Helius key remains authorized without rotation. The Senses runner reads its endpoint
  in-process from owner-only mode-0600 `.env.local`; LaunchAgent arguments carry paths only. The
  07:51Z exact-value delta rescan found no matches in 6,267 local Git objects, current argv, 117,006
  recent accessible files / 1.16 GB, or 1.66 GB of Unified Log. Receipt:
  `docs/evidence/helius-key-exposure-delta-rescan-2026-10-11T0751Z.json`. Remote/session stores,
  inaccessible files, backups and Time Machine remain outside local visibility.
- At 08:03Z all Senses LaunchAgents were disabled; no Senses/Helius collector process was present.
  Internal APFS had 43,745,020 KiB (~41.72 GiB) free, below the existing 50 GiB floor. Kingston
  ExFAT remains unqualified. Helius account-specific usage/charge limits are unknown; its MCP
  account-status call returned `Transport closed`. No RPC, backfill, retry, or live request occurred.
- c4 remains terminal. Do not reopen the frozen 23:00Z tail2 body or backfill any missed slots.
  Preserve all gaps and the unknown-ended STOP interval.

## New offline qualification

- Sol accepted `pumpswap-c4-idl-history-qualification-v3-2026-10-11.json`, SHA-256
  `3003c80f0ad6291b45a2a8c6c477fe729d8339446eb591a67c3c5c47029bdf46`. It binds the final tool
  and 15-test suite hashes, 18 historical Pump AMM IDL snapshots, and both pinned Anchor source files.
  Fifteen instruction labels are IDL identities only. All 9,598 selected event-CPI references pass
  nested discriminator, one-account event-authority PDA and nearest direct Pump AMM parent checks;
  8,903 are successful-status and 695 failed-status transactions. This is structural only, with no
  payload decoding, settlement, economic admission, or global completeness. Tail2 remains unopened.
- Pair64 remains 64/64 selected block identity and ordered-signature matches, with 146 explicit
  Helius log-truncation differences; Alchemy inventory completeness is unknown. The sell23 census
  is captured-class only. `buyExact25` remains economically unqualified: 16 structural mismatches,
  Token-2022 cases quarantined and four legacy outcomes censored.

## Offline event-layout feasibility

- The new source-bound feasibility receipt `pumpswap-c4-event-layout-feasibility-validation-v1-2026-10-11.json` records full-byte Borsh parsing for the 9,130 BuyEvent/SellEvent references in the ten included frames against all 18 pinned IDL layouts. All are uniquely consistent with the 2026-10-07 schema; this does not establish deployed layout or event validity. Tests include deterministic Hypothesis value/offset properties and malformed wire mutations. Other 468 event refs and frozen tail2 remain excluded; no RPC or activation occurred.
- Receipt SHA-256: `443b38dbd1211ce321283a3cc858483ed5c9d54b4bc19c6f9c0fd6408bd40b9d`. Current external gates remain closed as above.

## Next decision horizon

Keep advancing offline Senses engineering. Before prospective activation, obtain account-specific
Helius usage/charge controls, a qualified durable storage root with fresh full-runway evidence, and
Sol review of the exact forward-only schedule, c4 discontinuity lineage, provider adapter and new
finite lease. Do not resume missed c4 intervals or repeat captured requests. No Cortex work.
