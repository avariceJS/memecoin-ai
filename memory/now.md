# Current Project State

**As of:** 2026-10-10 02:37 Europe/Moscow. Read `docs/INDEX.md`, then verify Git and runtime
before acting.

## Boundaries

Build scalable point-in-time Solana market experience; no demonstrated trading edge. Keep
`/Users/root1/Desktop/memecoin-ai`, real money/signing, protected F01/D43 and reserved holdouts,
promotions, and remote pushes untouched. Do not start Cortex before Senses is objectively ready.

## Host and prospective runtime

- Research checkout: `/Users/root1/Developer/memecoin-research`, branch `research/active`, HEAD
  `8b5e6db` at last pre-handoff check. Production remains untouched.
- About 70 GiB free at 23:32Z. No v2 LaunchAgent or collector process is active. Pair64, older block
  schedulers and watchers remain stopped/unloaded; Helius watch-v3 roots retain STOP records and
  must not be bridged. No Helius request has run since the first v2 frame.
- Alchemy MCP usage summary at 23:32Z: 18,680 CU / $0.01 month-to-date; response exposed no usage
  limit. Paid-tier Solana method filters were accepted; seven-day `solana-mainnet/getAccountInfo`
  time series was empty. The research checkout has no Alchemy Free/PAYG HTTP endpoint. No Account
  Archive query or paid request was made; MCP `getAccountInfo` has no historical-slot parameter.
- One no-RPC archive control is frozen in
  `docs/evidence/alchemy-account-archive-mint-control-plan-2026-10-10.json`. It pins three max
  historical mint reads for one development reference at slot 454901149 / tx index 1012. A captured
  1,023-tx Helius block has no writable quote-mint occurrence; full and signature-only response
  hashes/signature order were verified. Provider-response scope only; no query yet.
- Inventory-first Helius v2 bootstrap at 22:43Z observed finalized slot 455028551, frontier
  455028552, one `getSlot` reservation, 43 raw / 56 zstd bytes, no body. A preflight at 23:00Z
  failed before I/O because it compared moving Git HEAD. The same real UTC bucket completed at
  23:17Z after two append-only, hash-bound schedule migration records; the original manifest and
  bootstrap files are unchanged.
- First frame `[455028552,455037817)`: 9,261 provider-returned produced slots; 16 selected blocks
  passed full/signature self-check. 34/34 requests captured and parsed, no retries, 71,939,096 raw /
  15,335,264 zstd bytes. Structural replay: 2,203 instruction refs (1,943 in successful, 260 in
  failed transactions); not economic swaps or zero activity. Exact no-RPC recovery replay returned
  the stored summary with zero calls. Receipt:
  `docs/evidence/helius-inventory-v2-first-frame-2026-10-09.json`.
- Invoke the CLI with `PYTHONPATH=src` or a research environment that installs the checkout editable.
  Plain global Python imports an older installed package from the separate production checkout and
  fails before I/O. Never switch the command to production.

## Senses facts

- Pair64 offline audit: 512/512 Helius selected-block self-checks; 64/64 Alchemy same-slot block
  identity and ordered-signature matches; 146/80,467 transaction-tree differences were Helius log
  truncation. No independent inventory or global completeness.
- PumpSwap sell23 remains diagnostic: 1,255 candidates, 410 exact full account settlements, 843
  censored native settlements, two unsupported token instructions. No wallet-net price/admission.
- PumpSwap buyExact25 Token-2022 evidence census is offline only: 1,234 candidate refs from the
  15:00Z dev archive; historical mint/account extension state unavailable in captured evidence.
  No amount decoder or audit admission. All stay quarantined. Current Alchemy docs describe paid
  slot-state archive reads (10 CU method reference; not locally qualified); slot-level snapshots do
  not expose transaction order/intermediate writes. The exact account endpoint/entitlement and
  account usage limit remain unverified. See `docs/brain-source-architecture.md`.
- Senses remains incomplete: broad market/process discovery, lifecycle, provider-independent
  completeness and prospective continuity remain limited. Preserve unknown/gap/censored/failed/
  stopped/unsupported/immature separately; never convert them to zero.

## Next horizon

1. Run two more real hourly v2 frames one-shot, review all request/byte/self-check accounting, then
   decide whether to install the campaign's LaunchAgent. Check actual UTC, disk and runtime first;
   keep the 50 GiB floor and 20,000-slot cap.
2. If the owner exposes the Alchemy paid endpoint in research runtime, verify entitlement, account
   limit, billing and slot coverage before running the frozen 3-read control. Otherwise leave it
   unqueried; preserve `same_slot_intermediate_state_unknown` where ordering is unproved.
3. Only after provider/recovery gates, continue Token-2022 state qualification under Sol's frozen
   semantics. No Cortex.
