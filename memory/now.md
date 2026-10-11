# Current Project State

**As of:** 2026-10-11 10:30 UTC. Research remains on `research/active`; production
`/Users/root1/Desktop/memecoin-ai` is untouched. Recheck Git and runtime before new acquisition.

## Runtime and credentials

- The existing Helius key remains authorized without rotation. The Senses runner reads its endpoint
  in-process from owner-only mode-0600 `.env.local`; LaunchAgent arguments carry paths only. The
  Codex/Cursor MCP configs were found to also contain the literal key and had launched 22 MCP
  processes with it in argv. Both configs now invoke `~/.local/bin/helius-mcp-secure` (mode 0700),
  which reads `.env.local` and starts pinned `helius-mcp@2.2.0` with the key in child environment
  only; a stub launch probe passed and stale processes were terminated. Follow-up exact-value scan:
  zero in 6,303 Git objects, current argv, 1,100 recent files / 480 MB, or 78.5 MB recent Unified
  Log. Receipt: `docs/evidence/helius-key-exposure-delta-rescan-2026-10-11T0824Z.json`. MCP still
  returns `Transport closed`; no RPC or account-status request succeeded. Remote/session stores,
  inaccessible files, backups, Time Machine remain outside scan. The agent-session tool transcript
  retains one process-list output with the known key.
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
- Sol accepted the exact source-bound development replay; commit `6c6aa08` records it. No RPC or activation occurred.
- New offline buyExact identifiability receipt `pumpswap-buy-exact-v1-c4-economic-identifiability-validation-v1-2026-10-11.json`, SHA-256 `29e9d00c326f10973dde8f953b16bac136e7774d3fe94e67190a43c6832228c2`: revalidates the 2,333 hash-pinned taxonomy identities against ten included full blocks and records transaction-wide fee/lamport evidence, ordered target touches, event fields and explicit unknown settlement state. Population remains 1,550 successful structural passes, 16 mismatches (overlapping the 780 failed attempts), and four legacy pass censorings. Six focused tests and replay pass. No economic admission predicate, settlement claim or economic zero; tail2 remains unopened.
- Sol-accepted offline `buyExact` position-state proof: `pumpswap-buy-exact-v1-position-state-source-feasibility-v1-2026-10-11.json` binds the accepted row ledger and official RPC/Token-2022/Agave v3.0.0 sources. Population: 780 exact failed-attempt transaction dispositions, 1,550 successful structural passes without candidate-position state from the captured documented sources, and 3 successful mismatches unknown. The row schema separates disposition from candidate position state; failed attempts are not economic zeros. Eight focused Hypothesis tests pass. No c4 bodies or RPC.
- Separate Alchemy Archive feasibility receipt `pumpswap-buy-exact-v1-alchemy-account-archive-feasibility-v1-2026-10-11.json`: all 2,333 identities mapped. 2,330 exceed one request containing the entire same-slot prefix plus candidate; this screen does not prove predecessor causal necessity or replay feasibility. The only three within the request count are failed attempts. Candidate-role lower bound is 17,584 non-program account×slot pairs (7,617 addresses); one-read-per-pair would be a nominal 175,840 CU scenario at published 10 CU/method, not measured usage or dollars. Historical slot bytes do not establish instruction-position state. No archive RPC or audit.
- Read-only Alchemy usage snapshot: 18,680 aggregate CU / $0.01 MTD through 09:02Z; method series reports Oct 8 `getSlot` 15,000 CU, `getSignaturesForAddress` 3,520, `getTransaction` 160. Usage limit field absent; Archive entitlement and spend caps remain unverified. Current internal free space is 42,393,532 KiB (~40.45 GiB), below the 50 GiB floor.

## Next decision horizon

The key-based pause is removed. The 08:24Z exact-value scan found no key in 6,303 Git objects,
current argv, recent accessible files or recent Unified Log; the exact-key check also found no value
in the dirty diff or six untracked source files. The secure wrapper reads the owner-only
`.env.local`; remote/session stores and backups remain outside local scan scope, and the agent-session
transcript retains the known prior process-list output. Do not rotate absent a new exposure or user
direction.

At 09:46Z, no Senses/Helius collector process or matching LaunchAgent was loaded. Internal APFS had
42,251,576 KiB (~40.29 GiB) free, below the reviewed 50 GiB floor. Helius MCP's last read-only
account-status attempt returned `Transport closed`; do not repeat it without a specific reason. No
RPC, c4 backfill, or retry occurred in this resumed work. c4 remains terminal with its gaps and
unknown-ended STOP interval preserved; no successor lease exists.
Alchemy MCP's read-only usage summary succeeded at 09:47Z and remains 18,680 CU / $0.01 MTD, but
does not return the account limit or repository attribution. Receipt:
`alchemy-usage-summary-mcp-2026-10-11T0947Z.json`.

Sol accepted the source-pinned Agave v3.0.0 candidate-position hook feasibility contract and the
narrow core synthetic consistency checker. Receipt
`solana-candidate-position-witness-synthetic-checker-validation-v1-2026-10-11.json` binds 11
official-source hashes and 54 passing focused tests. Economic admission, real source qualification,
and provider completeness remain false. Token-2022 fee/epoch/withheld/hook binding and WSOL
account/rent/frame-path binding remain unimplemented synthetic fixture gates. No Agave patch/build,
real witness, economic decoder, or audit exists.

At 09:59Z, no Senses/Helius collector process or matching LaunchAgent was loaded. APFS had
43,307,476 KiB (~41.3 GiB) free, still below the 50 GiB floor; existing production-named LaunchAgents
are unrelated and were not changed. Helius RPC remained idle and c4 immutable. Before any live
successor, obtain account-specific provider usage/charge controls, durable storage above the
reviewed floor with full schedule runway, and a fresh Sol-reviewed forward-only lease. Never
backfill missed slots or reopen frozen tail2. Cortex remains out of scope.

Sol accepted the initial-entry activation-preflight branch for offline eligibility only. Its 44
focused tests pass; the branch binds the fixed c4 certificate and candidate/compiler inputs, keeps
the four known gaps and open unknown interval, and uses a domain-separated owner-local one-use
claim. Activation remains false. Receipt:
`senses-initial-entry-activation-preflight-v1-offline-validation-2026-10-11.json`. At 10:19Z,
no Senses collector was present; the Helius MCP worker's visible process arguments contained no
key, and APFS showed 43,386,480 KiB free (~41.38 GiB), below the 50 GiB floor. Helius charge
headroom remains unknown; no RPC or retry was made. Next: preserve the frozen c4 boundary and
advance an independent offline sensing gate while resolving account-level charge controls and
durable storage runway; do not treat key authorization as a waiver of those gates.

Sol confirmed the one-shot supervisor already provides locking, single-operation decisions,
read-only status, clock/disk checks, and in-process crash-prefix recovery. Added a subprocess
integration test only: synthetic anchor capture, independent process reopen, forced exit after a
durable started-but-undispatched prefix, then terminal `abnormal_unsealed_prefix` verification
with the unknown interval retained and zero replay calls. Sol accepted this narrow evidence;
20 supervisor tests pass. No live scheduler or LaunchAgent is qualified. At 10:29Z, APFS had
43,369,980 KiB (~41.36 GiB) free, still below the 50 GiB floor; no Senses collector/LaunchAgent
was loaded. A fresh Helius account-status MCP call again returned `Transport closed`; no RPC was
made. Next independent offline gate: Token-2022/WSOL witness-fixture semantics. Before a live
successor, still require provider account/charge controls, storage runway, authenticated trust,
and a separately reviewed activation boundary. Do not backfill or reopen c4/tail2.
