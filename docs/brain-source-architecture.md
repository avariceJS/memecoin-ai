# Data and Acquisition

**Status:** raw Solana history and qualified OHLCV are separate, partially connected lanes.
This document owns source roles, data admission, coverage, and acquisition design.

**Scale target:** the Brain is intended to grow across very large market populations, long-lived
prospective history, multiple venues/modalities, and future worker/GPU scale-out. Today's OHLCV,
small slot samples, narrow venue paths, small models, and one-host limits are evidence fixtures,
not target boundaries; data contracts, provenance, recovery, and representations must extend without
a full redesign.

## Source roles

| Source | Current role | Limitation |
|---|---|---|
| Alchemy indexed Solana RPC | Historical transactions, birth discovery, address-indexed backfill; its paid Account Archive is a documented historical account-state path | Neither an indexed transaction nor the unqualified Account Archive proves decoded market semantics, transaction-position state, or coverage |
| GeckoTerminal | Current and historical OHLCV used by Brain's price forecasts | Provider bars are revisions with their own receipt/availability time; coverage can be sparse/stale |
| Public Solana RPC | Structural controls and bounded read-only verification | It is not a complete archive or independent proof of another provider's dataset |
| Helius | Independent indexed RPC and standard WSS cross-checks | A configured endpoint does not prove account plan, completeness, or independent agreement |

Alchemy Free and PAYG share one provider family. Read-only block verification can select the PAYG
endpoint through `MEMECOIN_ALCHEMY_PAYG_HTTP_URL`; the current research `.env.local` has no PAYG
endpoint, so no paid acquisition is currently configured. Helius remains a separate provider family.

The user-provided Helius RPC credential is configured in ignored, mode-0600 `.env.local` under
`MEMECOIN_HELIUS_RPC_URL`; the current Codex and Cursor MCP configs also hold it as private
mode-0600 configuration. The collector loads its endpoint inside the process, and LaunchAgent
arguments contain only paths. Do not claim that all historical logs are clean: an Oct 10 exact-value
scan found three old copies in two Cursor JSONL transcripts, redacted them, reset both files to
mode 0600, and validated all JSONL records. The earlier history scan missed these transcript
directories; the correction receipt supersedes its saved-history zero-match field. Post-cleanup
scans found no match in process command lines, tracked Git, c3 captures, or other saved Codex/Cursor
data beyond the current private configs. A two-hour Unified Log scan also found no match. One initial
credential-bearing process-list result remains in this agent-session tool transcript and cannot be
retroactively removed. See the
[`saved-transcript correction`](evidence/helius-mcp-saved-transcript-redaction-correction-2026-10-10.json)
and [`process remediation`](evidence/helius-mcp-credential-process-list-remediation-2026-10-10.json).
Read-only Helius MCP plan info and current pricing confirm Free at 1M credits/month and 10 standard
RPC requests/s; standard `getBlock`, `getBlocks`, `getSignaturesForAddress`, and `getTransaction` each cost one
credit. Account-level usage is not exposed. Standard WSS is available; enhanced
`transactionSubscribe` and mainnet LaserStream gRPC are not in Free. The old address-signature
watch stopped after 4,350 reserved/captured requests, with three transport failures and a growing
backlog; it is preserved as a stopped source run, not resumed as a per-signature completeness path.
The 20,000/day setting was only that run's bound, not a Brain quota or architecture ceiling. Use
Helius Free broadly where independent comparisons improve evidence, with per-wave credit, request,
raw-byte, and recovery accounting. See [Helius plan pricing](https://www.helius.dev/pricing),
[RPC rates](https://www.helius.dev/solana-rpc-nodes), and [Helius WebSocket capabilities](https://www.helius.dev/blog/laserstream-websockets).

A 2026-10-09 bounded PumpSwap program-address request returned zero signatures from Alchemy Free,
while public RPC returned five finalized references for the same address/limit/commitment shortly
afterward. This is a provider/index discrepancy, not evidence of zero activity. The six-call public
capture path preserved full `jsonParsed` transaction bodies and receipt times. It was a canary, not
a qualified market process: one transaction failed, the samples do not establish a pool census or
provider exhaustiveness, and the short canary source revision was not committed. See
[`brain-senses-program-watch-2026-10-09.json`](evidence/brain-senses-program-watch-2026-10-09.json).
The committed collector runs from a separate research LaunchAgent root. It journals each request
before I/O, seals responses, paces calls, records explicit provider/error/cursor state, and stops on
ambiguous reservations. Its observed population is transactions returned through the PumpSwap
program-address index, not a pool census. At the first decoder census boundary (279 transactions),
41 were failed on-chain transactions, 63 had no supported PumpSwap swap, and 175 remained
quarantined. Coverage and completeness are unknown. Do not admit these rows as exact market activity
or zero until independently reconciled.
An unrecognized PumpSwap instruction discriminator is explicitly surfaced as
`unsupported_instruction`, never summarized as `no_supported_swap`; recognized rows with unresolved
semantics remain quarantined.

The commit-bound read-only exception watcher records decoder/schema pins, transaction-shape classes,
representative signature hashes, raw-capture links, receipt/event times and new-class alerts. It
never changes raw captures or admits facts. In the first census, 53 apparent `event_transfer_mismatch`
cases exposed a v1 `buy_exact_quote_in` gross/net comparator defect. A bounded diagnostic now checks
separate user gross debit, pool quote credit, event net pricing amount, and fee-transfer equations for
that specific instruction. Successful arithmetic remains `economic_fee_semantics_diagnostic_only`;
remaining-account and Token-2022 blockers stay independent. V2 has a separate account and fee
retention layout and is not assigned the v1 equations. The official Pump [V2 trade contract](https://github.com/pump-fun/pump-public-docs/blob/main/docs/instructions/PUMP_SWAP_TRADE_V2.md)
describes that layout and its fee behavior. No economic process is qualified yet.

Alchemy endpoint qualification (2026-10-09) measured bounded read-only behavior on two
independent project accounts. The Free Solana account is published at 30M CU/month and 300 CU/s.
Earlier direct probes verified `getSlot` and a two-method JSON-RPC batch, but
`getSignaturesForAddress` returned HTTP 429 in 41/56 calls across two low-throughput phases. A later
isolated sequential raw capture returned HTTP 200 for both pages, with 571/412 ms end-to-end
latencies and no 429. This shows Free can support bounded raw development capture, but the earlier
429 pattern remains a measured bulk-ingestion risk. Its account-level usage meter was not available
to this workspace. PAYG returned 228/228 calls without provider errors (up to 33.68 calls/s in the
tested phase); the delayed usage meter moved by 5,520 CU: 180 `getSlot` calls at 20 CU each and 48
`getSignaturesForAddress` calls at 40 CU each. At the published $0.525/M CU rate, the measured
increment estimates $0.002898. PAYG 10,000 CU/s is a published base throughput, not a locally
saturated limit. PAYG's MCP transport did not expose JSON-RPC batch testing. A later two-call PAYG
MCP cursor probe was not reflected in the refreshed 5,760-CU account meter through 22:14 UTC; its
increment is unreconciled, so no zero-cost or request-count-based CU claim is made for that probe.
Use Free for bounded development when healthy; choose PAYG for larger indexed acquisition when its
observed reliability or throughput advantage is needed. Meter and budget each account independently.

Receipts: [`alchemy-endpoint-qualification-2026-10-09.json`](evidence/alchemy-endpoint-qualification-2026-10-09.json)
and [`alchemy-free-raw-cursor-qualification-2026-10-09.json`](evidence/alchemy-free-raw-cursor-qualification-2026-10-09.json).
Published plan/method references: [pricing plans](https://www.alchemy.com/docs/reference/pricing-plans),
[compute-unit costs](https://www.alchemy.com/docs/reference/compute-unit-costs),
[throughput](https://www.alchemy.com/docs/reference/throughput),
[Free tier](https://support.alchemy.com/articles/5827006872-what-is-included-in-the-free-tier), and
[`getSignaturesForAddress`](https://www.alchemy.com/docs/chains/solana/solana-api-endpoints/get-signatures-for-address).
Measurements are specific to this bounded test date and do not establish sustained throughput.

A separate 2026-10-09 PAYG probe returned 10 finalized signatures in descending slot order for one
known pool (slots 454,538,107 through 453,121,085). This qualifies the small response shape only.
Two 1,000-row MCP results could not be parsed as complete arrays; of two small-page attempts, one
was parsed and one summary was lost at the local tool boundary. No retry was issued. The later account
meter moved from 5,520 to 5,680 CU, exactly matching four requests at the published 40 CU method
cost (estimated $0.000084). The
[`probe receipt`](evidence/alchemy-signature-page-probe-2026-10-09.json) records the result and
limitations. Use a direct capture transport with atomic raw sealing and preflighted hashing for
future cursor qualification; the current MCP result surface is not a raw capture interface.

A follow-up bounded test stored the exact MCP result text for two sequential 10-row pages in ignored
`work/`, set mode 0600 and hashed both files. The result arrays contain 20 unique finalized
signatures with descending slots and no duplicate signature; the slot at the page boundary was
shared because distinct transactions can occupy one slot. The saved receipt omits the exact `before`
signature values, so request-to-page cursor lineage remains unverified. This qualifies local result
retention and observed response shape, not a verifiable cursor chain, HTTP wire capture or archive
completeness. The usage summary later reconciled the meter from 5,680 to 5,760 CU, exactly +80 CU
for two calls; estimated cost at the published method rate is $0.000042. Receipt:
[`alchemy-signature-cursor-qualification-2026-10-09.json`](evidence/alchemy-signature-cursor-qualification-2026-10-09.json).

The saved result was replayed through `signature_capture.publish` into the generic observation store.
It yielded 20 real provider-returned finalized signature events. The modality is transaction-reference
activity for one address, with slot and an RPC-reported transaction-success indicator; it is not
decoded swaps, volume, price, or a market-wide process. `blockTime` supplies chain event time; local
receipt time supplies point-in-time availability. The importer deliberately adds no enrollment or
support interval because neither an event-time membership declaration nor a queried event-time scope
was supplied. A first attempt to derive enrollment from the last receipt time produced 10 sparse rows;
that snapshot was rejected as semantically invalid. There are therefore no real experience examples,
objective targets, or real-source fit/evaluation from this capture. The machine receipt binds the
observation source and records that cursor lineage was not verified because exact `before` values
were not sealed. This proves the bounded positive-reference adapter only; it does not prove cursor
completeness, full retention, independent reconciliation, or that no other event occurred.

A bounded follow-up used the Free endpoint directly with a no-retry HTTP capture. Both five-row
pages returned HTTP 200 (571/412 ms); the raw response bodies were sealed at mode 0600 before JSON
decoding, and the second request cursor hash matched the first page's last signature. The direct
capture imported 10 finalized address references with raw-response provenance. It still emitted no
enrollment/support facts and cannot yield a valid historical snapshot by itself. Its 30M-CU account
meter is not available through the current account-summary tool. See
[`alchemy-free-raw-cursor-qualification-2026-10-09.json`](evidence/alchemy-free-raw-cursor-qualification-2026-10-09.json).

The same Free capture path subsequently accepted one maximum-limit request: 1,000 finalized refs,
HTTP 200 in 819 ms, with a 224 KB response body sealed and replayed into 1,000 observation facts.
This is one response-size check, not sustained rate qualification or an archive guarantee. The
importer still withheld enrollment and support because the result contains no independent membership
or event-scope evidence. See
[`alchemy-free-max-page-qualification-2026-10-09.json`](evidence/alchemy-free-max-page-qualification-2026-10-09.json).

A separate no-retry Free capture requested one finalized slot plus explicit adjacent boundary
witnesses. The in-range block response was complete (7.08 MB), while the left witness failed at the
transport layer and the right witness succeeded. Offline publication and replica replay verified
the single captured block and preserved the boundary failure. The range has unresolved lineage;
provider exhaustiveness, interval completeness and market-process coverage are not established.
The Free account usage delta was unavailable, so no CU cost was inferred. Receipt:
[`alchemy-free-block-range-2026-10-09.json`](evidence/alchemy-free-block-range-2026-10-09.json).

A selected PAYG app completed a bounded 300-request series: 270 `getSlot` requests at concurrency
4/8 and 30 sequential 10-row `getSignaturesForAddress` cursor pages. All returned successfully;
the 300 page rows were unique, slot-ordered, and cursor-linked in the live probe. MCP call latency
for the 30 pages was 290 ms median / 401 ms p95; the two `getSlot` phases measured 345-346 ms median
and 860-906 ms p95. These are MCP-end-to-end timings, not raw HTTP or provider-only latency. The
PAYG usage meter moved 5,840 -> 18,520 CU (+12,680) after its delayed update. The method
time-series attributed 11,400 CU to 570 `getSlot` calls and 1,280 CU to 32
`getSignaturesForAddress` calls, reconciling those method totals. The separate `getTransaction`
MCP requests did not appear in the `SUPERNODE_CU` method breakdown, so their CU cost remains
unreconciled; no zero-cost inference is made. At the published $0.525 / million CU rate, the
measured CU increment is approximately $0.006657; the account summary rounded MTD USD to $0.01.
No HTTP 429 or JSON-RPC error appeared in this bounded series. MCP has no raw JSON-RPC batch surface,
so PAYG batch behavior remains unmeasured. The Free direct HTTP page test processed 1,000 rows in
819 ms, while earlier Free phases had 41 HTTP 429s / 56 calls; PAYG is therefore the resource for
indexed work when Free throttling becomes an observed bottleneck, not a universal throughput claim.
See [`alchemy-payg-read-only-performance-2026-10-09.json`](evidence/alchemy-payg-read-only-performance-2026-10-09.json).

Free transaction retrieval was exercised on two complete bodies and then on a further 16
success-status finalized signatures evenly spread over one already sealed 1,000-row page. The first
probes showed that the endpoint requires `maxSupportedTransactionVersion=1`; one bounded request
timed out without retry. The later 16 JSONParsed responses all returned HTTP 200, complete, in
528 ms median / 1,046 ms p95. Four PAYG MCP transaction probes returned truncated text and are not
treated as captures. The decoder now accepts version 1 only when account keys resolve from the
transaction itself and no address lookup tables are present.

The expanded sample found a concrete IDL drift. All 33 candidate Buy/Sell events had exactly eight
bytes beyond the older pinned event layout. The official Pump IDL update at commit
[`8cda1fa`](https://github.com/pump-fun/pump-public-docs/commit/8cda1fa30ea658b20909d8aedf002047119388d2)
adds the trailing `creator_fee_unclaimed: u64` field. `memecoin_pumpswap.schema` now pins that file
hash and the decoder parses all 33 event payloads exactly. The 16 transactions remain quarantined:
all candidates have unreviewed remaining accounts and unqualified Token-2022 paths, with one
additional event/transfer mismatch. They have no training or execution eligibility. This is a
bounded schema-update qualification, not complete market-event support. Receipt:
[`alchemy-free-transaction-variant-qualification-2026-10-09.json`](evidence/alchemy-free-transaction-variant-qualification-2026-10-09.json)
and [`alchemy-free-transaction-qualification-2026-10-09.json`](evidence/alchemy-free-transaction-qualification-2026-10-09.json).

A narrow PumpSwap `sell` diagnostic now covers the pinned 24-byte instruction and 21-account prefix
with the evidenced fee-recipient/quote-ATA suffix, legacy SPL Token transfers, exact `SellEvent`,
and the strict CPI descendant subtree. It reconciles checked transfer amounts, exact pool signed
balance deltas, fee-recipient destinations, and empirical fee equations. Those fee equations remain
diagnostic hypotheses; the result has no training or execution eligibility. All touched token
accounts must have exact pre/post settlement for `reconciled_diagnostic_only`; otherwise exact
instruction/event transfer facts remain alongside an explicit quarantined, partial-settlement state.
It does not estimate wallet-net price or claim market/range completeness. The preselected 50-row
development replay matched 50/50: 12 had exact all-account settlement and 38 were quarantined for
unknown other account effects. After freezing the implementation at `c41f30b`, the one-time disjoint
50-row audit matched 50/50: all six event/transfer/fee-equation checks passed, 10 had exact
all-account settlement, and 40 were quarantined for unknown other account effects. This qualifies
the strict per-transaction gate and quarantine behavior on this sample only, not the 1,255-row class
or any market/range process. Fee equations remain empirical hypotheses. Receipts:
[`development replay`](evidence/helius-pumpswap-sell23-development-qualification-2026-10-09.json),
[`sealed audit`](evidence/helius-pumpswap-sell23-audit-qualification-2026-10-09.json).

A frozen `pumpswap_sell23_full_transaction_settlement_v2` adds a full ordered legacy SPL Token
account ledger: sibling transaction transfers, mint/burn, account initialization and closure are
included for target-touched accounts. A missing opening balance is exact zero only after an ordered
successful initializer; unsupported token effects remain unknown. `SyncNative` is never a balancing
residual: until ordered lamport funding, rent reserve and close effects are independently
reconciled, wrapped-SOL settlement after SyncNative is censored. A fresh 50-row development sample
matched 50/50 (18 exact full target-account settlements, 31 exact trade-transfer diagnostics with
censored native-close settlement, one unsupported token instruction); all six event/transfer/fee
equations passed on the 49 transactions with qualified target transfer facts. Its disjoint sealed
50-row audit matched 50/50: 20 exact full target-account settlements and 30 censored native closes;
all six equations passed 50/50. These are diagnostic-only samples, with no wallet-net price,
training or execution admission, and no qualification of the 1,255-row class or range completeness.
Fee formulas remain empirical hypotheses. See [v2 development](evidence/helius-pumpswap-sell23-ledger-v2-development-qualification-2026-10-09.json)
and [v2 audit](evidence/helius-pumpswap-sell23-ledger-v2-audit-qualification-2026-10-09.json).
Instruction semantics follow the [legacy Token interface](https://github.com/solana-program/token/blob/main/interface/src/instruction.rs),
[SyncNative documentation](https://solana.com/docs/tokens/basics/sync-native), and
[Close Account documentation](https://solana.com/docs/tokens/basics/close-account).

A subsequent offline census applied the frozen ledger to every one of the 1,255 eligible `sell23`
transactions in the 512 captured Helius full blocks. All 1,255 were found: 410 had exact full
target-account settlement, 843 had exact trade-transfer facts but censored native settlement, and
two remained quarantined for unsupported token instructions. All six event/transfer/fee equations
passed on the 1,253 qualified transfer records. This is a census only of that declared class in
those captured blocks; it does not establish global PumpSwap or market-process completeness.
Receipt: [v2 captured-class census](evidence/helius-pumpswap-sell23-ledger-v2-capture-census-2026-10-09.json).

The first prospective inventory-first Helius frame was also replayed offline through the frozen
ledger. All 67 eligible `sell23` candidates in its 16 selected full blocks matched: 13 had exact
full target-account settlement and 54 had censored native-close settlement. All six transfer/fee
equations passed on all 67. This extends the diagnostic to one fresh prospective selected-block
sample only; it is not an audit of the decoder, a complete process/population census, wallet-net
price, or training/execution admission. Receipt:
[inventory-frame census](evidence/helius-pumpswap-sell23-ledger-v2-inventory-frame-census-2026-10-10.json).

The first c3 prospective frame was replayed offline through the same frozen sell23 ledger. All 80
eligible candidates in its 16 selected full blocks matched: 10 had exact full target-account
settlement (diagnostic only), and 70 retained censored native-close settlement. Each of the six
ledger equations passed on all 80. The census used no additional Helius requests and does not extend
the decoder's audit, unselected-block/process coverage, wallet-net price, or training/execution
admission. Receipt:
[`c3 sell23 frame census`](evidence/helius-inventory-v3-c3-sell23-census-2026-10-10.json).

The pinned v1 `buy_exact_quote_in` path now has an offline structural attempt census over those same
512 Helius full-block captures: 642,312 transactions, 12,120 unique candidate transactions, and
12,143 instruction references across 507 slots, with no normalization failures. Of these refs,
8,422 came from successful transactions and 3,698 from failed transactions; failed rows remain
attempt-only. A source-bound attempt inspector recognizes the pinned 25-byte IDL shape only when
the trailing boolean is 0/1, and validates the documented two-account fee/quote-ATA suffix as a
candidate. It found 1,377 such 25-byte/two-account suffix candidates (1,299 successful-transaction
refs, 78 failed attempts). Another 25 refs had a structurally valid pair but failed the conservative
transaction-wide readonly check for the fee wallet. Three-account suffix predecessors and
four/seven-account suffixes remain unresolved; 24/26-byte payloads remain unverified. This is not
an economic fill decoder, transaction settlement, completeness, exact-zero, wallet-net price, or
training/execution admission. The planned 13:00Z independent audit was consumed by a failed census
attempt; future qualification must use a new disjoint source. Receipt:
[`buyExact v1 attempt census`](evidence/pumpswap-buy-exact-v1-attempt-census-2026-10-10.json).

The official [Pump AMM IDL pinned at commit `8cda1fa`](https://github.com/pump-fun/pump-public-docs/blob/8cda1fa30ea658b20909d8aedf002047119388d2/idl/pump_amm.json)
binds 23 fixed `buy_exact_quote_in` accounts and the `spendable_quote_in`,
`min_base_amount_out`, and `track_volume` arguments. Its docs identify the optional cashback
WSOL accumulator ATA at `remaining_accounts[0]`. A source survey of the exact official
`@pump-fun/pump-swap-sdk@1.14.0` NPM artifact (published 2026-02-17; integrity and local tarball
hash in the receipt) found the same instruction in generated IDL types but no explicit
high-level exact-quote builder implementation in `src/sdk`. This does not identify the builder
used by any capture or assign observed suffix positions. Cashback, pool-state, fee-recipient,
Token-2022, and other remaining-account variants still require source- and account-bound
qualification; no economic decoder change follows from this survey. Receipt:
[`buyExact SDK builder source survey`](evidence/pumpswap-buy-exact-sdk-builder-source-survey-2026-10-10.json).

The frozen development predicate selected all 62 successful refs in that captured census whose
payload is the pinned 25-byte shape, two-account suffix passes the source-bound checks, and both
base and quote use legacy SPL Token. The other 1,237 successful structural candidates include
Token-2022 on at least one side and remain quarantined. Offline replay of all 62 refs found one exact
`BuyEvent` and four checked legacy token transfers per ref; user quote-account gross debit matched
the event gross amount, pool quote credit matched its separate event field, and base credit matched
the event amount and minimum. The full ordered ledger reconciled all six target-touched token
accounts in all 62 transactions. Observed mint decimal pairs were 9/6 in 61 and 6/9 in one; amounts
remain integer atomic units and no UI conversion is made. Gross/net and fee-flow equations matched
all 62, but remain empirical diagnostic hypotheses. This is narrow development evidence only, with
no wallet-net price, training, execution, or market-completeness admission. Its deterministic plan
and replay receipt also bind a metadata-only manifest for 512 disjoint 13:00Z Helius full blocks.
That source was opened once by the frozen structural census on 2026-10-10. All 512 block payloads
were processed, but report construction failed because the source binding receipt uses `git_commit`
and omits optional `audit_code_commit` and `analysis_sha256` fields. No census output was published,
no per-reference outcomes were accepted, and no economic replay began. Preserve the failed attempt;
do not rerun or tune on this archive. Corrected code requires a new disjoint source and a new frozen
plan. Receipt: [`buyExact v1 failed audit attempt`](evidence/pumpswap-buy-exact-v1-audit-failure-2026-10-10.json).
Development receipt:
[`buyExact v1 development`](evidence/pumpswap-buy-exact-v1-development-qualification-2026-10-10.json).

A new Helius Free epoch was then captured for the frozen one-pass audit: 512/512 selected full
blocks parsed, 512/512 signature-only checks matched the full-response signature hashes, and the
slot set is disjoint from both the 15:00Z development source and the failed 13:00Z source. The
fresh class census covered 563,802 transactions and 15,215 v1 instruction references (11,012 in
successful transactions, 4,203 failed attempts), with zero normalization failures. Among 1,175
successful 25-byte/two-account documented-suffix candidates, 1,166 used legacy base plus Token-2022
quote, seven used legacy Token on both sides, and two involved Token-2022 on the base side. The
frozen predicate replayed all seven dual-legacy refs once: all seven had exact instruction/event/
transfer facts; three had exact full target-account settlement and four remained censored after
SyncNative/native close. The fee equations remain hypotheses. This qualifies only those seven
diagnostic rows; the 1,168 Token-2022-involving refs and all other layouts remain quarantined, with
no wallet-net price, training/execution admission, economic zero, market completeness, or edge claim.
The one-shot capture used 1,026 Helius reservations including its anchor, returned 2.244 GB raw and
495.8 MB zstd-stored block bodies; the Helius account meter remains unavailable. Receipt:
[`buyExact v1 fresh captured-class audit`](evidence/pumpswap-buy-exact-v1-audit-qualification-2026-10-10.json).

An offline development evidence census of the opened 15:00Z archive replayed all 1,234 successful
25-byte/two-account legacy-base/Token-2022-quote refs in that captured census across 298 slots. Each
subtree carried one legacy-token and three Token-2022 token instructions with 10-byte checked-form
wire payloads. In 1,233 refs, another transaction instruction touched a target token account outside
the target subtree; the single isolated path still lacks bank-time mint/account extension and fee
configuration evidence. Those states are explicitly `historical_extension_state_unavailable` for all
1,234 refs. This is a development census only: it does not qualify an amount decoder or settlement,
and the 15:00Z candidate count is a different population from the fresh 1,166-ref audit census.
Do not infer extension state from the 2026-10-01 fixtures or a later account fetch. Receipt:
[`Token-2022 development evidence census`](evidence/pumpswap-buy-exact-v1-token2022-dev-census-2026-10-10.json.zst).

A separate offline privilege replay bound the same 1,234 references and inspected every transaction
in each of their 298 captured full blocks (384,459 transaction records). Message-header and loaded
address privileges show that 1,213 references had no writable quote-mint occurrence anywhere in
their captured slot; 21 references had at least one writable occurrence, which is only potential
write evidence. Of the 1,213 mint-readonly references, 67 also had an instruction from an
unclassified program touching a target account; 1,146 had no such touch. The remaining 10
potential-write references also had an unclassified target-touching program. Therefore this replay
identifies a candidate subset for historical mint-byte acquisition only. It does not recover
mint/account bytes, prove an actual write or no-write beyond the captured provider block, resolve
the other target-account effects, qualify Token-2022 economics, or change the 1,234 quarantined
economic states. Receipt:
[`Token-2022 full-slot privilege census`](evidence/pumpswap-buy-exact-v1-token2022-privilege-census-2026-10-10.json).

The next offline trace replay covered all 1,146 mint-readonly references without an unclassified
program touch, across their 285 captured full blocks and 366,918 transaction records. It preserved
exact wire bytes and path/stack evidence for every instruction touching a target role, plus each
target account's writable-potential appearances across its captured slot. Of 3,556 Token-2022
target-touching instructions, 3,552 had first byte `12` and length 10; four had first byte `09` and
length 1. Those four rows are stopped at an unclassified Token-2022 wire shape; the other 1,142
remain structural traces only, with no amount or settlement decode. Same-slot writable privileges on
user, pool, or token accounts remain potential effects and do not establish a balance change. All
1,146 rows keep historical extension state unavailable. Receipt:
[`Token-2022 clean-stratum trace`](evidence/pumpswap-buy-exact-v1-token2022-clean-stratum-2026-10-10.json).

A bounded offline follow-up checked only the four `(program=Token-2022, wire=09)` rows against the
official Token-2022 source at commit `b4724e6e7fecc8fc36295f861a769401bf92b270`, with pinned
`instruction.rs` and `processor.rs` hashes in the derived receipt. All four source and raw-capture
checks passed: the one-byte instruction is `CloseAccount`; each successful transaction has path
`[5]`, stack height 1, and ordered accounts `[user quote token account, user destination, user
owner signer]`. The destination and owner resolve to the same user key, whose compiled message
privileges are writable and signer. The rows are labeled
`known_structural_unsupported_close`; no account lifecycle, Token-2022 extension state, amount,
rent/lamport settlement, PumpSwap economic effect, or admission is inferred. The original 1,146
economic states remain quarantined. Receipt:
[`four-row Token-2022 close structural classification`](evidence/pumpswap-buy-exact-v1-token2022-close-structural-2026-10-10.json).

A second offline follow-up replayed all 3,552 Token-2022 `(tag=12, length=10)` traces in the clean
stratum against their pinned full-block captures and the official Token-2022 source. Every row
passed the exact program, wire, path/stack, resolved-account, minimum-operand, signature, and
successful-transaction checks. The receipt records the encoded `u64` as `declared_amount_atomic`
and the trailing `u8` as `decimals_argument` (6 for all rows), without UI scaling or an effect claim.
Pump IDL target-role labels are emitted only when the trace parent is the clean-row candidate and
fixed IDL account keys match that row's role values; 114 sibling-parent traces retain unknown role
attribution. No amount is treated as realized transfer, balance delta, fee settlement, or PumpSwap
economics. Receipt:
[`Token-2022 TransferChecked structural replay`](evidence/pumpswap-buy-exact-v1-token2022-transfer-checked-2026-10-10.json).

A bounded offline endpoint-delta census replayed the preidentified no-sibling development
reference against its exact captured Helius full-block response; the raw transaction replay
matched its prior source-bound census row by canonical JSON comparison. All seven target token
accounts had matching provider-reported transaction-boundary amount rows, and the ordered target
trace contained only the pinned PumpSwap root plus legacy `TransferChecked` and Token-2022
`TransferChecked` instructions. This is one isolated subtree-delta feasibility candidate. Across
the frozen 1,234-reference development population, all seven target accounts had both compatible
endpoint rows in 52 refs; 1,182 refs were partial. The receipt records 7,452 signed per-account
transaction-endpoint deltas and 1,186 unknown account endpoints. A reported zero delta means equal
provider-reported boundary amounts, not no activity. The other 1,233 refs have an outside-subtree
target-account touch and remain censored for buy-subtree attribution, even where their whole-
transaction account deltas are present. Missing rows remain unknown. No CPI settlement, gross
transfer, withheld fee, wallet-net price, historical extension state, or economic admission is
qualified. Receipt:
[`Token-2022 transaction endpoint delta feasibility census`](evidence/pumpswap-buy-exact-v1-token2022-endpoint-delta-census-2026-10-10.json.zst).

Provider-capability survey update (2026-10-10): Alchemy's current [Solana Account Archive
documentation](https://www.alchemy.com/docs/solana/account-archive) describes paid-plan historical
`getAccountInfo` reads (`slot`, `lastUpdateBeforeSlot`, `firstUpdateAfterSlot`), with `slot: S`
returning the latest finalized account write at or before S. It claims account-write history since
July 2025 and excludes vote accounts and per-slot bookkeeping sysvars. These are provider claims,
not locally qualified results. The interface is slot-granular and does not identify a write's
transaction position within a slot. The current [`getAccountInfo` method reference](https://www.alchemy.com/docs/chains/solana/solana-api-endpoints/get-account-info)
lists 10 compute units, but the archive's paid-plan entitlement and actual account meter are not
verified here. Alchemy also documents [`simulateTransactionsAtSlot`](https://www.alchemy.com/docs/chains/solana/solana-api-endpoints/simulate-transactions-at-slot)
as a counterfactual runtime simulation over archived state, up to five ordered transactions per request;
this is not an actual execution trace or proof that a complete same-slot transaction prefix can be
replayed faithfully. In this research checkout, `MEMECOIN_ALCHEMY_PAYG_HTTP_URL` is absent, and the
available Alchemy MCP `getAccountInfo` interface exposes no historical-slot parameter. No archive
query or paid request was made. Therefore `historical_extension_state_unavailable` remains the
correct state for the 1,234 captured references: it means unavailable in their evidence and
qualified acquisition path, not that no provider offers historical account data. Any future
qualification first needs verified account entitlement, endpoint, method cost and coverage for the
sample, then immutable raw captures and ordered same-slot replay/reconciliation evidence. The first
potentially useful subset is mint fee-term state where a complete provider-returned slot transaction
sequence proves no writable same-slot mint occurrence before or after the target; the 1,233
sibling-touching references still need all ordered effects and withheld-balance reconciliation. If
same-slot ordering cannot be established, preserve
`same_slot_intermediate_state_unknown`. Do not change the quarantined economic decoder before that
gate. One no-RPC development control is frozen from an already captured Helius full-block response;
it tests mint bytes only and remains blocked on account entitlement, endpoint, price/limits and
archive coverage. Its three-call cap, source hashes, provider-response scope and acceptance checks
are in the [`Account Archive mint control plan`](evidence/alchemy-account-archive-mint-control-plan-2026-10-10.json).
The Alchemy MCP account surfaces returned one app (`memcoin-ai`, app ID recorded in the sanitized
access-gate receipt) with Solana mainnet enabled. The current usage summary reports 18,680 CU / USD
0.01 month-to-date through 03:44Z on 2026-10-10, with no account limit. Its app-filtered method
series attributes those same 18,680 CU to Oct 8 `getSlot` (15,000), `getSignaturesForAddress`
(3,520), and `getTransaction` (160); it reports no Account Archive `getAccountInfo` use. The
research `.env.local` has an Alchemy Free endpoint but no separately configured PAYG endpoint. The
account/app surfaces do not expose Account Archive entitlement, exact remaining budget, or an
account-specific archive meter. Generic published pricing and historical-slot semantics remain
provider documentation, not account-qualified access or coverage. No archive RPC was issued; the
three-read mint control remains frozen and `frozen_no_rpc_access_gate_pending`. Receipt:
[`Alchemy Account Archive access gate`](evidence/alchemy-account-archive-access-gate-mcp-2026-10-10.json).

One finalized slot was then fetched with full `getBlock` transactions from Alchemy Free and an
independent public Solana RPC. Both HTTP responses were complete and agreed on block identity,
parent linkage, 1,132 transaction entries, and the ordered flattened sequence of 1,171 signatures.
The full response hashes differ, as provider JSON representations may differ. This is single-slot
cross-provider consistency evidence, not proof that either RPC's list is exhaustive, not a bracketed
slot range, and not market-process support. The Free CU meter was unavailable. Receipt:
[`alchemy-public-block-cross-provider-2026-10-09.json`](evidence/alchemy-public-block-cross-provider-2026-10-09.json).

`signature_capture.publish_verified_values` now emits a versioned qualification receipt whose sole
capability is `observed_values`. The registered verifier replays sealed raw response pages and
reconciles every normalized signature reference, process/schema/decoder pins and conserved
resolved-vs-unresolved-time accounting. It checks role and protected entity identity before page
payload access. It never grants population coverage, support or completeness, and a values-only
source still cannot be indexed as market experience without independent enrollment. This allows
positive, available facts to be retained without turning omitted history or an empty capture into a
zero label. No real transaction objective target or
source-backed training example has resulted from the signature-reference captures.

Qualification has a read-only replay verifier that compares regenerated positive facts against the
existing immutable observation source using disk-backed scratch storage. It checks the source's
recorded chunk and refuses missing schema/rule dependencies instead of recreating them. The
`replicate_verified_values` path copies the source and verified-source shards/catalog pages, import
manifest, qualification receipt, schema/rules and only the raw capture files declared by the
receipt. The destination is fully revalidated; an integration test removes the original store and
capture directory before replaying from the replica. This establishes a portable single-capture
dependency closure, not distributed object storage or high-throughput worker capacity. Raw capture
files remain local inputs to verification, and implementation pins intentionally reject code drift.

## Data funnel

Every layer has a distinct denominator:

```text
requests -> received raw captures -> indexed transactions -> decoded events
         -> identified pools/mints -> qualified price intervals
         -> feature-complete origins -> mature labels -> admitted examples
```

HTTP 200, transaction count, registry market count, bars, eligible origins, and independent
market episodes are not interchangeable measures of training support. Record successes, empty
responses, unsupported layouts, failed requests, gaps, stale intervals, incomplete features,
pending targets, and mature examples separately.

The last historical raw vacuum indexed 5,400 monthly transactions / 865 birth pools and other
pool/fee streams. After dedup it represented 965 registry markets, 1,579 decoded events and
1,583 provenance observations. It added **zero** qualified USD training decisions at that stage.
Sampled date ranges ended at configured caps; this was not complete year coverage.

Later bar expansion produced 86 training pools (24 old + 62 new), 98,323 eligible decisions
before cap and 48,000 retained. Of 100 hydrated markets, 38 had zero eligible support. The new
cohort contributed 7,987 decisions, all retained, displacing old rows. Twenty new pools had fewer
than 16 rows. Nearly all new origins were adjacent 5-minute bars and do not represent independent
episodes.

The latest frozen 20-mint / 40-window historical envelope yielded 2,020 unique bars, 453 eligible
origins, 6 supported mints and 29 globally dependency-disjoint windows. One mint supplied 71.08%
of origins; age 7-10d had no support. This is not a broad or prospective benchmark.

Exact dated receipts and interpretation: [`research-history.md`](research-history.md). Full
machine-readable source/run records: [`evidence/README.md`](evidence/README.md).

## Canonical event and bar rules

- Store raw response bytes, request/receipt metadata, source alias, range, and hashes before decode.
- Decoder version, chain/venue, pool/mint identity, quote units, token program/extensions, and
  transaction status must be explicit. Unknown layouts remain quarantined, not approximated.
- Distinguish event time, received time, and actual availability time. Historical block time is
  not evidence that a model could observe the event then.
- Keep first-seen bar values immutable; later provider revisions are new observations. Do not
  replace an earlier feature with a revised candle that arrived after the historical decision.
- The legacy eight-frame OHLCV representation needs up to 15 transitive input bars. New contracts
  declare context and target definitions; each observed target is masked/purged independently.
- Missing/stale intervals are not zero returns, market death, or rug labels. Preserve failures in
  the enrollment denominator.
- Keep WSOL/USDC denominated prices separate from USD. A quote conversion is itself time-aware
  data and must be available at the relevant origin.
- Pool birth, graduation, provider support, liquidity rankings, and user selection create different
  populations. Track selection/survivorship and age-at-origin; do not infer a market universe from
  today’s survivors.

## Solana event ingestion

The implemented historical path is indexed RPC -> immutable capture -> reservation/cursor journal
-> versioned decoder -> canonical event shards -> single-writer SQLite catalog -> sealed snapshot.
Raw/provenance support is useful, but the current decoder set does not cover every venue. PumpSwap
historical IDL profiles reconciled a small set of old transactions; newer payload suffixes and
Token-2022 remaining-account semantics remain incomplete. CPMM decoding does not generalize to
CLMM/DLMM/Meteora.

The versioned compiled-transaction normalizer resolves static keys followed by loaded writable and
loaded readonly addresses, preserves instruction bytes/index paths and key provenance, and fails
closed when lookup addresses, account indexes, message versions, or CPI stack evidence are missing
or inconsistent. It decodes only the exact SPL `TransferChecked` wire form (tag 12, u64 amount,
decimals); Token-2022 transfer-fee/hook economic effects stay quarantined. This is a resolved
instruction layer, not yet an economically reconciled swap. The layout follows [Solana RPC JSON
structures](https://solana.com/docs/rpc/json-structures) and the [official SPL token instruction
implementation](https://github.com/solana-program/token/blob/main/interface/src/instruction.rs).

An early indexed query failed because the request omitted `maxSupportedTransactionVersion=0`.
That was a client request bug, not evidence that the provider lacked historical data. Later
corrected indexed RPC worked. `getSignaturesForAddress` gives references, not transaction
completeness; `minContextSlot` is not a historical-state selector. A raw capture can be retained
for future decoding without being admitted into features or trading.

## Capacity and next data-system boundary

The current B13 path materializes all admitted examples before applying the 48k cap and refuses
more than 500k examples. A lazy shard/mmap implementation exists, but is not wired into that
trainer. Historical SQLite audit scans once took 600s before a missing index; the indexed query
reduced the full scan to about 8s. These are implementation facts, not hardware limits.

The next scale design should add a durable coverage ledger by source/venue/pool/time/schema,
separate acquisition from semantic admission, support partitioned immutable raw/columnar data,
and make decoder/feature replay resumable. Keep SQLite for transactional metadata/control while
measured single-writer capacity suffices. No data-center load test has established where a store,
network, CPU, or GPU will fail first.

## Operational acquisition invariants

- Register exact ranges, source identity, decoder/schema, expiry, account-level budget, and
  failure policy before network requests.
- Record a reservation before IO; a timeout/ambiguous response is not a free retry.
- Retry only classified transient failures under a shared budget and deadline. Preserve STOP,
  partial receipts, and gap debt.
- Separate production and research namespaces/leases. A research worker must not become a second
  mutable writer or silently extend a production lease.
- Use per-partition quarantine for unknown data; do not let one unsupported venue erase healthy
  independent captures.
- Measure useful feature-ready and mature-origin throughput in addition to bytes and RPC counts.

## Data path interfaces

### Partitioned research experience (2026-10-07)

`memecoin_experience` introduces an opt-in research data plane. Its admitted-corpus adapter
streams existing sealed `birth-usd-admission-v1` training corpora, checks their namespace and
manifest, and requalifies bounded raw captures before accepting individual rows. The exact metadata
bytes used for the role gate must match the pinned manifest before market payloads are accessed. Reserved
evaluation roles and protected pools are rejected. It does not call providers or open owner
runtime. Multiple sorted corpora merge by earliest receipt, with explicit duplicate/revision
counts and pinned sources preserving the alternative versions. Equal receipt times use capture
hash as a deterministic tie-break. Legacy admission eligibility/disjoint counts retain their
context-8 meaning; new variable-context jobs derive their own membership and denominators.

The builder retains bounded history and emits every context-ready origin, including separately
masked incomplete horizons. Receipt maxima and capture dependencies cover every raw bar used
by every input frame. Historical availability remains explicitly unknown at event time.
Pool/day partitions contain binary feature/target columns plus provenance JSONL. Content-addressed
objects are shared by snapshot manifests; no 500k assembly ceiling applies. Publication uses
fsync and atomic namespace operations under a local writer lock. Immutable per-partition journal
entries allow retries to replay their input prefix and reuse published objects; this is not a
random-access source cursor. Corrupt objects fail closed. Unreferenced crash leftovers are safe
but currently require manual storage housekeeping.

Multiple admission units merge through a disposable, bounded-cache SQLite spool with a bounded
number of open files; a single unit streams directly. Failed merges restart from pinned sources.
Keep new admission units bounded (for example, per capture/pool); the old `compile_corpus`
implementation itself still materializes its supplied unit. Do not feed it the whole market at once.

Payload memory is bounded by context, raw-capture cache and partition size (maximum 4096 origins).
Legacy inline metadata is O(partitions); new large snapshots use the paged catalog below.
Source admission metadata remains O(pools/captures).
The initial format is a versioned stdlib binary layout, not Parquet or a distributed database.
The admitted-history adapter is retained; the continuous export described below connects a
second source to the same reader. Raw-vacuum semantic qualification remains separate work. These engineering changes establish no market edge.

### Continuous export and representation boundary (2026-10-08)

`continuous.export(index, output, plan)` reads the existing SQLite index using `mode=ro` and one
explicit WAL read transaction. It does not invoke the writer's schema initializer or change the
owner runtime. Its plan has exactly `role`, `cutoff_ns`, `start_s`, `end_s`, `selection` and `pools`;
each enrolled pool supplies `pool`, `base_mint`, `quote_mint`. Roles are `train` or the new
`research_evaluation`, never protected/reserved holdout. Validate roles/protected IDs before
opening index/payload. Missing enrolled pools remain in expected 300-second-slot denominators.

Export replays bounded captures, verifies their index/version/checksum agreement and explicit
HTTPS GeckoTerminal USD/base/5-minute namespace and mint identity. Unsupported namespace/mints
and malformed bar schemas are quarantined with pinned evidence. Integrity failures abort
publication. Selection is the earliest **qualified** receipt with capture-SHA tie-break, not the
legacy `firsts` table's insertion-order tie policy. Revisions/ties retain counters and all capture
versions. The export does not infer a request-attempt denominator: unindexed transport failures
are explicitly unknown. Selection history is the declared enrollment, not a reconstructed universe.

The portable bundle contains `meta/{source.json,captures.jsonl,rows.jsonl,manifest.json}` and
`captures/<capture-pin>/`. Export copies/seals into staging and atomically renames the result;
a failure cannot publish a partial final directory. The source reader verifies quarantine evidence
as well as raw dependencies of admitted rows, including after bundle relocation. It requires a
consumer cutoff at least as late as the export cutoff. No receipt is rewritten as event-time
availability. Re-export still scans/requalifies indexed captures; caching that pure pinned
qualification function and reducing WAL retention are future throughput optimizations.

`brain-experience-v2` declares a materialized layout (`dense-sequence-f64-v1`), context, named
feature units/modalities, named target units/kinds, representation name/version/code SHA and an
availability policy. The currently shipped OHLCV producer supports explicit horizon sets; generic
producers can emit a different feature/target count through `storage.build(..., schema=...)`.
Records carry nanosecond decisions, full input dependencies, availability maxima, separate target
maturity/availability, masks and capture pins. Structural validation cannot qualify a new venue's
semantics: that remains the producer's responsibility and needs independent evidence/review.
Dense views are not a replacement for immutable raw multi-modal evidence; new physical layouts
need a new layout reader while retaining snapshot/object and role contracts.

`historical_replay` means event-time splits on data known by the research cutoff, without a claim
that it existed at historical decisions. `point_in_time` additionally enforces input availability
by decision and label receipt within the role interval. The two validity domains are explicit.
Unknown/missing/censored targets have masks, never invented zero values.

`compose` combines snapshots with identical representation/role and disjoint entity/decision
intervals; identical objects deduplicate, overlapping distinct partitions fail closed. This is an
increment interface, not silent row-level revision replacement. Supply context/label overlap when
exporting an increment and use build-plan `origin_interval_s` with v2 to choose disjoint output
origins. Source accounting stays attached to parent snapshot pins and is not summed when parents
share inputs. Location hints live in build journals; relocating a pinned source does not change
the new dataset identity. `replicate` publishes a destination snapshot only after its objects are
copied, fsynced and verified.

Real-data CPU qualification on the existing 24-market development universe exported 95,801
qualified training-window bars and persisted 82,436 context-32 examples. Independent accounting
identified 12,486 losses after temporal gaps, 667 initial-context losses and 212 outside the chosen
origin interval. Only eight markets supplied usable cumulative training origins; four supplied
95.62% of that exposure. These are source/representation/population constraints, not retained-row
caps. Source coverage and model findings have their receipt and interpretation in
[`training-evaluation.md`](training-evaluation.md#real-data-system-qualification-2026-10-08).

New snapshots with more than 1,024 partitions use immutable catalog pages of at most 1,024 entries.
The root pins page identities/counts; each page pins feature objects. Readers cache at most two
catalog pages. Small and historical inline manifests remain supported. New object paths use a
SHA-prefix directory, with a fallback for old flat locations; build journals group ordinal records
into directories as well. Writers/composition flush page metadata incrementally. Root metadata
and the large-snapshot sampler are O(catalog pages), with O(page size) partition ordering, rather
than materializing all partition dictionaries or a full partition permutation. Metadata is still
not constant-size, and explicitly converting an iterator to a list is the caller's responsibility.

Relevant implementation: `src/memecoin_ai/{sources,storage,collection,observations,datasets}.py`,
`src/memecoin_brain/{source,data}.py`, `src/memecoin_brain_sources/`, `src/memecoin_continuous/`,
`src/memecoin_curriculum/{archive,admission,streaming}.py`, and `src/memecoin_pumpswap/`.

Common implementations and validity boundaries are in [`contracts.md`](contracts.md). Current
benchmark populations and ML conclusions are in [`training-evaluation.md`](training-evaluation.md).

## Population and coverage facts

`memecoin_population` is an opt-in research data plane independent of provider, cadence and model.
Population enrollment precedes download support: namespaced entities carry valid intervals,
knowledge timestamps, evidence pins and extensible categorical attributes. Immutable paged fact
snapshots append parents; compaction retains all historical facts. SQLite indexes are disposable
views. Overlapping metadata with equal knowledge timestamps is ambiguous and fails closed.
Enrollment is a union of declared intervals, not inferred survival; correction/retraction requires
an explicit new population definition. Protected entities and research/train roles remain gated.

Coverage facts distinguish capture from qualification, bind a full stream contract and use explicit
units, expected counts (nullable for event streams), observed counts and failure/unknown states.
Sparse plans join declared population intervals with arbitrary stream tiles. Hash selection is
stable as population grows and independent of downloaded support. Worker partitions are disjoint;
they are work descriptions, not a distributed executor or authority to retry charged requests.
Changing a stream/grid contract requires reprojecting existing captures into new cells. A missing
cell never proves that a provider request has not previously been charged.

Use `python -m memecoin_population ACTION --job arguments.json`: `publish` streams `rows_path`
JSONL; `compact` and `replicate` operate on fact pins; `plan` seals a sparse SQLite worklist;
`work` streams one `partition` (or all cells with null); `audit` joins a pinned experience snapshot.
The corresponding function signatures in `catalog.py`, `planning.py`, `diversity.py` define job keys.
The `universe` adapter imports a hash-pinned legacy universe with its real admission timestamp;
optional `age_edges_ns` produces time-varying lifecycle bands. It explicitly retains survivor
selection bias. `continuous-coverage` requalifies a sealed continuous export, then projects unique
bar support onto compatible cells; other modalities and out-of-export intervals stay unknown.
Only this adapter knows the existing 300-second OHLCV convention. New producers publish the same
population/coverage facts without changing their storage model.

Knowledge policy governs attribute lookup, not retrospective enrollment: the declared population
is the union as of its pinned cutoff, including periods whose attributes were then unknown.
This is essential for diagnosing selection bias and is not a claim that the population was known
historically. A population removal is a new selection contract; shorter metadata intervals never
silently withdraw prior enrollment. Before dispatch, reconcile work cells with the acquisition
request ledger and provider budget; planning alone cannot prove a request has never been charged.

Population attributes are small categorical stratification metadata, not market feature vectors.
Each fact supplies a complete attribute map for its valid interval; later knowledge replaces that
map for lookup, not field-wise merging across unrelated producers. Explicit `unknown` request
outcomes route to reconciliation, not another acquisition suggestion. Capture completeness and
qualified grid support remain distinct: qualified counts cannot exceed a known denominator.

### Independent birth population acquisition

`tools/population_campaign.py` freezes public keyless acquisition from previously captured birth
metadata, excludes prior/protected cohorts and their known base mints, and separates train/evaluation
mints before price requests. The population is an authority/calendar-page prefix, with graduation
and decoder-support bias; it is not a complete market census. Unsupported, empty and failed requests
remain enrolled. No replacement based on returned prices, survival or current liquidity.

Acquisition reserves each request durably before network IO, seals raw responses, enforces pacing,
expiry/storage/request limits, and requires a caller-owned live owner fence. Sealed requests recover
without another GET; ambiguous reservations and provider failures stop without automatic retry.
A transient failed owner check occurs before reservation: later readiness checks may resume only
unrequested work. Continuous export additionally supports optional per-entity disjoint `intervals`;
coverage denominators use these intervals, avoiding invented missing history before market birth.
HTTP failures, malformed payloads, namespace/mint failures and invalid bars stay quarantined in
source accounting. Integrity failures still abort publication.

`memecoin_population.corpus` bridges the acquisition ledger to portable qualified source bundles.
Every request in a role must be resolved before its source is published; the other role's payloads
are not opened. The bridge checks frozen-plan, request-window, ordinal, outcome, receipt and raw
capture bindings. Its sealed accounting lists every planned request and qualification reason.
Population publication retains the complete enrolled role with birth/lifecycle metadata known only
at plan creation, not retroactively at event time. OHLCV intervals denote grid-aligned opens in the
planned half-open request windows; full-bar receipt causality is enforced independently.
The population coverage adapter publishes a cell only when the source declares every eligible bar
open in it. Cells crossing undeclared per-entity gaps remain unknown; they are not relabeled as
observed zero/partial coverage merely because they lie within the export's global calendar bounds.

A sealed transport failure can be reconciled explicitly with
`python -m memecoin_population reconcile-transport-stop --job JSON` (`root`, exact `stop_pin`,
nonempty review `reason`). This is never invoked automatically. Under the capture lock it verifies
the entire attempted prefix against the frozen plan, ledger, outcomes, receipts and sealed captures;
reserved/unsealed requests and provider stops fail closed. It writes a recovery receipt and moves
the original STOP bytes into immutable history. Original budgets/expiry remain in force. The failed
request remains terminal and counted; a later guarded capture invocation sends only previously
unrequested jobs. Crash recovery of the reconciliation itself is idempotent.
Restart also reconstructs an unreviewed STOP from terminal ledger failures, covering a crash between
terminal commit and STOP publication. Earlier transport failures require their own matching review
receipt; interrupted archival with both directory entries is completed only after verifying the
original STOP hash. User-requested pause is a distinct STOP reason and is never auto-reconciled.

The 2026-10-08 independent-population campaign completed 307 birth-enrolled markets without
response-based replacement. Its source funnel is recorded in
[`brain-independent-population-learning-2026-10-08.json`](evidence/brain-independent-population-learning-2026-10-08.json):
38,705 unique train-role bars became 8,792 context-ready rows across 49 of 170 markets;
14,375 evaluation bars became 6,465 rows across 30 of 137. All 307 markets have some qualified bars.
The 32-consecutive-bar producer loses 29,913 train bars to initial/gap context warm-up. This is an
experience-conversion bottleneck; a larger population alone cannot resolve it.

The provider returned nonempty qualified payloads for 1,221 requests, yet 381 contained no bars
inside their own requested window (216 train, 165 evaluation). Older returned history and repeated
bars are not new interval coverage. Source exports deduplicate first qualified receipts across all
captures and apply the declared per-entity interval union; an overlapping response can recover
support absent from its own interval's request. The terminal timeout and six duplicate-bar
quarantines remain explicit. Requested intervals contain 587,520 train and 473,472 evaluation grid
slots; missing slots can mean inactivity, unsupported history or missing observations and are not
a provider-error rate. Wider lifetime work plans also include unrequested cells; cells partly
outside source intervals remain unknown, so their denominators differ intentionally.

Causal volume admission rejected only 20 train-role contexts and no evaluation contexts. It does
not fix the larger selection imposed by requiring uninterrupted active bars. Future sparse/event
producers should preserve event spacing, known inactivity versus unknown observation support,
lifecycle and censoring, and use objectives that can learn from short/failed market histories.
Do not infer inactivity from a missing provider bar or filter enrollment by future survival.

### Sparse observations and lifecycle facts

`memecoin_experience.observations` publishes `market-observations-v1`: a sorted immutable stream
of namespaced entity enrollments, typed events, observation-support intervals and evidenced
lifecycle transitions. Enrollment is an administrative interval, not a claim about market lifetime.
Empty, failed and unsupported entities remain enrolled. No terminal transition is inferred from
silence. Streams declare feature names/units/modalities and the exact observed process.

Every fact carries actual availability and evidence pins. Support is `complete`, `unknown`, `failed`,
`stopped` or `unsupported`; complete means coverage of the **named process**, not all economic
activity. `stopped` records an evidenced source stop; neither it nor `failed` implies market death.
Complete intervals cannot overlap other support assertions, end after their availability, or
precede the availability of included events. Missing HTTP/provider bars never certify absence.
Corrections/revised coverage require a new source version, not overlapping contradictory facts.

`memecoin_experience.qualification` adds `process-contract-v1` and immutable
`qualification-receipt-v1` records over the existing stores. A receipt binds its process descriptor,
input/source/decoder/qualification-code pins, population pin and role, actual availability, explicit
captured/decoded/rejected/qualified/unresolved partition counts, and the exact projection capabilities
it supports; counts use `accounting_unit=candidate_process_records`. `scope_pin` names a sealed paged manifest of explicit
entity/half-open-interval members with each member's own status, availability, counts, capabilities
and evidence. Accounting requires captured = decoded + rejected and decoded = qualified +
unresolved, both per member and for the partition. A `SupportAssertion` is accepted only for an
exact scope member; partition totals never prove that every member is complete. Projection
revalidates membership and capability immediately before writing the existing observation support
fact or population coverage row. Complete support additionally requires the member's
`complete_support` capability and zero rejected/unresolved records. Only then can that member's
qualified count of zero mean an exact observed zero. A self-declared status, opaque hash, or all-zero
receipt is insufficient: the statically registered synthetic fixture verifier currently supports
complete and partial fixtures after binding and re-executing against the explicit fixture, process
contract, exact member and verifier implementation hash. The receipt's qualification-code pin must
equal that registered verifier hash. Verifier reads repeat publisher checks for fixture intervals,
schema shape and finite values, so a content pin constructed outside the publisher does not bypass
validation. Synthetic source identity is derived from the process and scope pins, and synthetic receipts require the registered decoder identity. Fixture
publication checks finite values and payload shape; only registered rule definitions are accepted.
Other statuses remain declarations and project no counts. Full-use cache keys include file identity
for every fixture dependency, while full verification hashes each dependency. This grants no
real-source completeness path. `qualified_process_events` has no independent
expected-event denominator, so its projected coverage keeps `expected=null`, even for complete
support. The v1 observation schema has no `partial` support state: that projection is conservatively
`unknown`; population coverage preserves verifier-backed `partial` and its observed lower-bound
count. Observation projection checks the destination process, stream, schema pin, fields, units and
modality. Receipt/scope pages are fully checked on publication and first use/import, then cached by
immutable pins and dependency file signatures; member-page reads are bounded. Cache-hit invalidation
still stats each verifier fixture, so cost is linear in verified fixtures; large-scope throughput
is not established. The receipt itself does not infer a lifecycle transition.

The process descriptor binds `entity_namespace`, modality, a hash-pinned payload schema (value names,
units and denominations), versioned event-identity/time-basis/interval/revision rules, and nullable
timestamp precision. These rule/schema references must resolve to matching immutable artifacts.
Provider location is not process identity. Changing event, time, payload or revision semantics
changes the process pin. Distinct producers may share this semantic pin while retaining separate
source and qualification receipts.

These machine checks establish hash identity, accounting, role/scope and capability consistency;
they do not establish provider truth or replace human/domain qualification. Capture/reconciliation
records remain acquisition evidence; these receipts are not a replacement transport ledger or a
distributed qualification service. `stopped` is an explicit population
coverage status and routes to `review_stop`; it is never converted to empty, complete or observed=0.
Incomplete support remains explicit in the assertion and coverage fact, and cannot prove
inactivity.

Facts are CAS JSONL shards grouped by entity/kind/stream/day with paged catalogs. Publication and
replication use existing locks, seals and durable manifest publication. A crash before publication
leaves reusable objects; replay verifies and reuses them. Roles are checked before payload reads.
A disposable SQLite query index provides bounded-cache history, interval and rank-count lookup;
it is not the canonical event store. The current materializer is local and single-worker. Entity
partitioning and immutable source/snapshot identity provide the boundary for later distributed
materializers; there is no cluster scheduler or object-store publication implementation here.

`memecoin_experience.sensing` extends this local architecture without changing `sparse.materialize`.
It seals an arrival-independent schedule in content-addressed pages, then reconciles wrapper event
and support facts to the exact receipt-verified synthetic fixture pins on publication and use.
`materialize_episode` streams scheduled origins through `storage.build`; input history is as-of,
quiet origins remain, and the final context frame carries per-stream support state and masks. Count
targets use `(decision,maturity]` and preserve the full horizon; exact price returns require unique
qualified observations at both the scheduled origin and endpoint. The dry-run budget interface
returns evidence-pinned decisions only and cannot dispatch. One local schedule measurement processed
1,025 origins into three 512-row pages in 0.041 s with 278,257 bytes traced peak (Python 3.13.6,
arm64); this is not a throughput claim. The observation index and sparse materializer now preserve
multiple nonoverlapping enrollment intervals per entity; events, support and lifecycle facts must
belong to exactly one interval, and history/targets cannot cross the gap. This is property-tested,
not a large-scale throughput claim.

`sparse.materialize` projects one selected stream plus lifecycle facts into existing generic v2
snapshots. It accepts short histories, preserves event spacing, age, padding masks and known gap
coverage, and never forward-fills prices. Multiple source streams are representable today; joined
multistream producers remain future work. Count targets retain an exact value only under complete
coverage of the named process. Otherwise they retain a lower bound from qualified observed events,
the latest receipt time supporting that bound, and support reasons (`unknown_support`, `source_failed`,
`source_stopped`, `unsupported_process`, or `unasserted_gap`). An administrative enrollment boundary
is separately marked; lifecycle transitions still require explicit evidence. A zero lower bound on
an incomplete interval is not an inactivity label. Stable support-shard evidence avoids embedding a
changing whole-source hash in every old row; the snapshot binds the full source. Appending another
entity preserves unchanged source objects and materialized partitions.

CLI jobs: `python -m memecoin_experience {publish-observations,observations-from-continuous,
build-sparse,replicate-observations} --job JSON`. Publisher jobs supply sorted `rows_path`, `store`,
`schema`, `cutoff`, `sources`, optional `chunk`; materialization jobs supply `store`, `pin`, `output`,
`plan`, optional `role`/`chunk`. Plans declare stream, context, max_history_ns, lifecycle transitions,
lifecycle_origins, relative_features and named targets with kind/horizon_ns/feature. Replica jobs
supply source/target/pin/role. Adapter jobs supply a sealed continuous reference, store, role and
optional sorted lifecycle_path. The adapter preserves receipt availability and raw evidence,
always emits unknown observation support, and accepts birth facts only from explicit evidence. It
does not qualify a source's event-process completeness. Qualification needs a named process and
interval, complete request/cursor accounting, explicit failure/unsupported ranges, immutable receipt
provenance, and an independently reconciled coverage boundary. Provider disappearance is not a
lifecycle event. The existing real sparse source `81f7c74b…4b10c3e0` has 170/170 support facts
marked `unknown`; its 38,705 retained events prove neither complete event counts nor inactivity.
The 2026-10-09 Alchemy work qualifies bounded RPC behavior, positive signature references,
transaction response capture, one-slot block response consistency across two providers, and a
replicable block-inventory receipt. A later two-slot finalized interval matches provider-reported
block identities and transaction projections and produces one exact next-slot count for a narrowly
defined structural PumpSwap instruction-reference process. It is not a qualified economic trade or
market-activity process: references in failed transactions remain references, not executed swaps.
The first left-witness attempt failed and remains recorded; the successful bounded replay does not
erase it. The range inventory producer accepts only a validated, expiring request plan with explicit
witnesses and request/byte/time budgets; it records reservations before dispatch, never retries, and
exposes no automatic witness search or range expansion. The generic inventory verifier is offline
and can label a slot skipped only from successful enumeration plus a qualified parent/hash chain; an
absent provider record alone is unknown. These are finalized block inventory facts, not universal
provider guarantees or lifecycle evidence.

The 2026-10-09 Helius/public same-slot block pilot compared three separated finalized ranges (50
slots). Both providers returned identical slot sets in all three ranges; block identity and ordered
transaction signatures matched for all 50 slots, and each provider's full/signatures-only lists
matched for all 100 provider-slot pairs. All 56,615 paired transactions were structurally comparable:
56,495 exact JSON-tree matches and 120 differences, all confined to Helius `meta.logMessages` with
an explicit `Log truncated` marker. The 103 Helius calls are expected to cost 103 standard credits;
the account meter was unavailable. Both providers returned 7,953 PumpSwap structural instruction
references (7,761 in successful and 192 in failed transactions), not economic swaps. The 50 slots
were a feasibility selection, not a probability sample or broad-completeness result. Block capture
recovered about 159 structural references per full-block request and averaged 4.3 MB per full block
per provider, substantially improving request efficiency over one `getTransaction` per signature.
Receipt: [`helius-public-block-provider-pilot-2026-10-09.json`](evidence/helius-public-block-provider-pilot-2026-10-09.json).
The next wave is precommitted in
[`helius-public-block-sample-precommit-2026-10-09.json`](evidence/helius-public-block-sample-precommit-2026-10-09.json):
2,880 selected slots across three days, 24 equal slot-number strata/day, 144 random 20-slot windows,
and one seeded signatures-only cross-check per window. UTC-hour alignment is an approximation from
a short measured slot-rate interval and will be audited from captured blockTime. It expects 3,168
calls/credits per provider on current published pricing and about 24.8 GB paired from the pilot's
full-block mean alone. This wave stops at 30 GB paired raw responses, 75,000 expected Helius credit
reservations total, 25,000/day, or 4 Helius RPS; public calls share the 1 RPS gate. These are bounds
for this wave, not durable Brain caps. Preserve the prospective public signature watch independently
and report only sampled-block agreement; scale subsequent waves from measured discrepancy yield,
uncertainty, actual billing evidence, durable raw bytes, and available storage.

## Prospective provider epochs (2026-10-09)

**Current runtime status (2026-10-10):** the 64/hour and 512/hour finalized-block schedulers, the
legacy public/Helius block schedulers, decode watchers, and Helius watch-v3 are stopped or unloaded.
The pair64 campaign remains stopped; its raw captures are retained. Watch-v3's 12:00Z and 18:00Z
epochs have STOP records; no future bucket may bridge either stopped epoch. The inventory-first v2
pilot has begun in `work/brain-senses-live-helius-inventory-v2-16-hourly-20261010/`: bootstrap at
22:43Z on 2026-10-09 observed finalized slot 455028551 and committed frontier 455028552. No block
body had been requested at the initial check and no v2 LaunchAgent was loaded. The first 23:00Z
preflight failed with `schedule_binding_changed` before another reservation. The scheduler compared
the original source commit with moving repository HEAD although behavior code and locked
configuration were unchanged. The same real 23:00Z bucket later completed at 23:17Z without
backdating or repeating bootstrap. Two append-only, hash-bound migration records preserve the
original manifest, bootstrap evidence and exact scheduler diffs; one fixes the provenance binding,
the second corrects bootstrap verification after the frontier advances. Both preserve strict source,
configuration, request and recovery bindings. The original schedule manifest and bootstrap epoch
remain unchanged.

The first frame covered numeric slots `[455028552,455037817)`: 9,261 provider-returned produced-slot
members, 16 sampled blocks, and 16/16 full/signature self-checks. It used 34 captured requests,
71,939,096 raw bytes and 15,335,264 zstd bytes over an 18.2-second receipt span. Structural replay
counted 2,203 PumpSwap instruction references in selected blocks (1,943 in successful and 260 in
failed transactions); these are not economic swaps or exact activity. Four numeric positions were
absent from the provider's returned inventory. The selection does not establish global slot,
transaction, market or process completeness, and no exact economic zero is allowed. Helius charge
units and account usage remain unverified. Current free disk is about 70 GiB; no v2 LaunchAgent is
loaded. The Helius MCP account status check confirmed an API key is configured but returned no plan
or credit usage; no account upgrade or paid RPC was made. Receipt:
[`helius-account-status-mcp-2026-10-10.json`](evidence/helius-account-status-mcp-2026-10-10.json).
Frame receipt: [`helius-inventory-v2-first-frame-2026-10-09.json`](evidence/helius-inventory-v2-first-frame-2026-10-09.json).
The current public Helius credit reference lists 1 method credit each for `getSlot`, `getBlocks`,
and `getBlock`; that yields an estimated 34 credits for a complete frame or 816 for 24 frames if
those generic rates apply. Keep this separate from RPC request counts and actual account credits,
which remain unverified. Receipt:
[`Helius public credit-cost reference`](evidence/helius-public-credit-cost-reference-mcp-2026-10-10.json).

The `20261010T000000Z` scheduler invocation occurred at 00:43Z. Its single captured `getSlot`
anchor (43 raw / 56 zstd bytes) was 23,699 slots beyond the persisted frontier, above the frozen
20,000-slot width cap. The epoch sealed as `oversized_frame` after that one request; it made no
inventory or block-body requests and no retries. The numeric interval is an explicit unknown gap,
not a backfill, empty inventory or economic zero. Its terminal journal and request hashes are bound
by the [oversized-frame gap receipt](evidence/helius-inventory-v2-oversized-gap-2026-10-10.json).
At the 01:00Z handoff, only the actual 02:00Z bucket remained authorized; run it once and review
the frozen campaign. Do not replay this bucket.

The inventory-first CLI accepts no endpoint argument. The running collector obtains the RPC endpoint
from the protected local `.env.local` file or process environment; plans and request receipts retain
the provider alias and payload hashes, not the endpoint. Transport errors redact endpoint details.
Keep local credential files mode `0600` and out of Git. Do not place credentials in CLI arguments,
inline shell assignments or saved command text. The Helius MCP configuration passes its key in the
child environment; its command arguments do not contain the key. The local Helius MCP package is
pinned to `helius-mcp@2.2.0`; this matched both the resolved local package and current registry
version on 2026-10-10, and the pin preserved the existing child environment.

The next finalized-block process is a new versioned sensor. It must pin a finalized numeric slot
frame, seed, selection rule, provider and request/byte budgets before acquisition; capture and hash
the finalized `getBlocks` inventory for that exact frame; then select only from the inventory's
returned produced-block slots before requesting any block body. Retain the inventory denominator,
every selected member, all failures, and each member's full/signature-check outcome. Never replace
a selected member after a failed or incomplete body request. An unavailable, failed or ambiguous
inventory ends the epoch as `inventory_unknown` with an explicit gap; do not retry a charged attempt
or publish an empty result as zero activity. This proves only selected structural-reference
coverage inside the provider-reported produced-slot inventory, not independent chain inventory,
economic-process, pool, or market completeness. Solana's [`getBlocks` RPC contract](https://solana.com/docs/rpc/http/getblocks)
defines an inclusive range and returns confirmed block slots; pin `finalized` explicitly.

The prior seeded numeric-slot selector is a historical experiment with a different selection frame;
do not relabel its receipts as inventory-first coverage. Commit `d6a9562` added recovery to the old
numeric sampler, but its legacy scheduler still rejects attempted buckets. Commit `1380b99` adds a
separate versioned inventory-first epoch and scheduler: it journals bootstrap/finalized anchors,
commits a half-open numeric frame before inventory I/O, samples only the captured ordered
`getBlocks` members, and never replaces a failed selection. Restart reuses saved responses and
selection plans; ambiguous requests stop without replay. Committed-frame failures advance through
an explicit unknown/gap outcome, while failed anchors preserve the frontier. Structural counts are
qualified only when the full response validator, account/instruction resolution, normalization,
and ordered-signature cross-check all pass. These are no-RPC fixture results, not market
completeness. The scheduler pins immutable source/config identity, records commit as provenance,
serializes campaign writes, accounts for outstanding worst-case response reservations, and checks
disk headroom before each request. Interrupted epochs must match their saved plan and current
approved source hashes before recovery. Helius charge units remain unknown; request counts are
recorded separately. The focused suite has 15 passing tests. Sol selected a Helius-only hourly pilot with 16 sampled
produced slots, a 20,000-slot frame cap, zstd storage, and a 50 GiB pre-request free-space floor.
Each frame allows 34 reservations (one finalized-slot anchor, one inventory, and paired
full/signature responses for 16 blocks), 256 MB raw and 128 MB stored. The immutable campaign
manifest allows 25 epoch directories, 817 epoch requests, 4 GB raw and 1 GB stored; these request
limits are inconsistent. Twenty-four full frames use 816 requests, leaving one request, which can
begin a partial frame because the scheduler does not preflight all 34 request slots before sending
that frame's anchor. Do not install a LaunchAgent against this schedule. The first frame used
71,939,096 raw bytes and 15,335,264 zstd bytes; one frame does not establish sustained size or
latency tails. Generic Helius method credit prices are known, but the connected key's active plan
and remaining balance are not available through the MCP account tools. At the 01:00Z handoff, the
final actual 02:00Z bucket remained to be run once under the unchanged manifest; then the finite
campaign will be stopped and reviewed. Any successor needs a new schedule version, an all-in
quota/request/byte budget, and a full-frame request preflight before I/O. Invoke with the research
`src/` import path explicitly (or the research
environment that installs it editable); a shell Python without it imported an older installed
package from the separate production checkout and failed before I/O. Before each invocation,
recheck the actual UTC bucket, disk, and runtime state.
The frozen v2 pilot closed after the sole authorized 02:00Z bucket completed. Across five epoch
directories it reconciled 104 request reservations, 186,022,930 raw bytes and 41,818,169 zstd
bytes, with no outstanding reservations. The final inventory covered `[455066012,455082524)` and
reported 16,512 produced slots; all 16 selected blocks passed their full/signature self-checks.
The final observed anchor was slot 455082523. The earlier 00:00Z interval
`[455037817,455061516)` remains an explicit oversized-frame gap. Root STOP and final receipt bind the
manifest, frontier, epoch plans, summaries, journals and counters:
[`helius-inventory-v2-final-2026-10-10.json`](evidence/helius-inventory-v2-final-2026-10-10.json).
This is provider-bounded structural coverage, not global slot or economic-process completeness.
Alchemy pairing stays disabled until an account-level limit is verifiable.

An offline selector/yield census independently replayed the three complete v2 frames against
their provider-returned inventories and full/signature captures. All 48 selected block pairs match
the frozen selection and sealed structural checks. The scoped denominators are 30,269 returned
produced-slot members and 48 selected blocks; four numeric positions in the first frame were not
returned by `getBlocks`. The 00:00Z oversized interval remains a distinct 23,699-slot gap. The
selected blocks contain 5,743 PumpSwap structural instruction references across 2,903 transactions
(670 references in 553 failed transactions), plus 168 exact legacy sell23 structural candidates
from 28 candidate pools. Per-frame sell23 counts are 67, 31 and 70. In the first frame, 39/67
candidates came from two pools; aggregate top-two concentration across the three frames is 39/168.
Each frame selected blocks in all four numeric interval quarters, with selected event-time spans
from about 15 to 51 minutes and a 109-second median adjacent sampled-blockTime gap across the
combined sample. This supports use as a bounded structural sentinel with concentration monitoring;
it does not establish market representativeness or unselected-block activity. The three frame
receipts reconcile exactly to 102 requests, 186,022,844 raw bytes and 41,818,057 zstd bytes; the
other two campaign requests are the initial anchor and oversized-gap anchor (86 raw / 112 stored
bytes), matching the frozen 104-request campaign closure. Receipt:
[`Helius v2 selector and yield census`](evidence/helius-inventory-v2-selector-yield-census-2026-10-10.json).

The separately versioned Helius v3 successor contract is bounded to 24 future hourly attempts.
Each campaign has a unique ID, immutable manifest and terminal STOP, and its own LaunchAgent. A
separate finite generator has an initial 72-hour lease for at most three daily campaigns (including
the first), capped at 2,451 total requests, 12,000,000,000 raw bytes and 3,000,000,000 stored bytes,
plus the 50 GiB free-disk floor. The lease does not extend automatically. Its first campaign is
seeded only from the closed v2 pilot receipt after local reconciliation; its known 00:00Z gap stays
in lineage. Each later successor requires the immediate predecessor's verified normal 24-attempt
STOP and reconciled journals. Sealed historical gaps remain in lineage; unresolved epochs,
ambiguous state, abnormal STOPs, missed buckets, provider errors, or failed resource preflight halt
generation. Boundaries are future-only and requests are never replayed. Its no-RPC predecessor
lineage plus 24 attempts is at most 25 epoch directories; each campaign allows 817 all-in requests,
with 34 slots required before an anchor. Per-frame caps are 256,000,000 raw bytes and 128,000,000
compressed bytes; campaign caps are 4,000,000,000 raw and 1,000,000,000 compressed bytes. Preflight
reserves one complete frame envelope against committed bytes and other reservations, plus the 50 GiB
disk floor and a peak-write margin. Unused capacity is released only after request journals,
captures, summaries and file sizes reconcile. A missed hour records a gap, permits one anchor-only
observation, then seals terminal STOP; it never fetches missed prospective slots. Observed anchor and
inventoried interval remain separate. Ambiguous requests and integrity discrepancies stop the
campaign. STOP is atomically published after fsync and includes counters, frontiers, and hashes of
the bound manifest, active reservation and summary when present.

The campaign LaunchAgent checks STOP before scheduler or credential access, runs the scheduler in
process, and requests `launchctl bootout` only after durable STOP. If bootout fails, the next
calendar invocation retries teardown without RPC; `KeepAlive` is disabled. A separate locked
controller may create unique campaign roots only inside its 72-hour lease; durable lease and
aggregate state survive reboot, and each successor binds the predecessor STOP and frontiers. Crash
recovery reconciles local state only. Expiry or abnormal predecessor STOP seals controller STOP and
unloads it. Campaign 1 failed before anchor and RPC at its first scheduled event because launchd's
default PATH did not include the installed zstd executable. Its immutable STOP records zero requests,
zero bytes and no reservation; exactly one empty bucket directory was left. The one-use recovery
receipt preserves that STOP, binds source and zstd hashes, and authorizes campaign 2 under a fresh
root without changing the original deadline or aggregate ceilings. The missed `20261010T030000Z`
bucket is an explicit uncovered setup gap and is never replayed. Both plists configure only the non-secret
`BRAIN_ZSTD_EXECUTABLE` path. The RPC URL remains sourced from the private `.env.local` file and is
not included in process arguments. Recovery evidence is in
[`helius-inventory-v3-preanchor-recovery-2026-10-10.json`](evidence/helius-inventory-v3-preanchor-recovery-2026-10-10.json);
the initial installation record remains at
[`helius-inventory-controller-install-2026-10-10.json`](evidence/helius-inventory-controller-install-2026-10-10.json).

An invocation marker makes abnormal process exit detectable. The next invocation validates already
captured journals locally, never repeats an ambiguous request, seals STOP and unloads. Scheduler,
wrapper and controller crash-boundary tests are qualified locally, including 24 normal synthetic
attempts through STOP; this checks horizon and teardown state, not real provider throughput. The
real `20261010T040000Z` campaign-2 invocation took one parsed finalized-slot `getSlot` anchor at
slot 455116880, then sealed `missed_schedule_gap_requires_review`. Its only request reconciles to
43 raw / 56 zstd bytes; it made zero inventory or block-body requests. The gap
`[455082524,455116881)` is explicit, `missed_slot_backfill=false`, and the active reservation is
reconciled. The controller's unchanged normal-successor verifier rejected this non-24-attempt
campaign with `predecessor_campaign_stop_abnormal`, then sealed controller STOP without creating a
successor. The controller's aggregate counters remain zero because it did not admit the abnormal
campaign; campaign 2's actual 1 request / 43 raw / 56 stored bytes must still count against the
original lease if a separately authorized continuation is created. Receipt:
[`Helius campaign-2 missed-gap STOP`](evidence/helius-inventory-v3-campaign2-missed-gap-2026-10-10.json).
The immutable
campaign manifest binds its minute-05 local calendar trigger, UTC hour buckets, no-`RunAtLoad` and
no-`KeepAlive` settings, and teardown policy. The installed campaign plist hash is bound into
controller state and recorded in the runtime receipt.
The prior public and Helius program-address watches stopped on failed requests and remain immutable.
Neither cursor is resumed. Helius watch v2 made a fresh head query and attempted 1,000 signature
references plus 1,000 transaction bodies; 999 bodies were captured, one request failed, and a later
runtime restart wrote a terminal `ValueError` STOP. The account-bound source state is not resumed.
LaunchAgent `ai.memecoin.brain.senses.helius-watch-v3` starts a new current-head epoch each six-hour
UTC bucket in `work/brain-senses-live-helius-watch-v3/`. Each root links to the previous STOP and
last receipt, carries no cursor across roots, reserves each request before I/O, and stops after 1,100
request reservations, an error, or its bucket boundary. A failed epoch is not retried; the next
scheduled bucket starts a fresh query. At most 4,400 watch requests/day are planned, a campaign
bound rather than an architecture limit. This is a sparse provider address-index stream with
unknown completeness, not full program history or a block-range truth source. A fresh current-head
query is a separate observation, not a retry or a bridge across the old gap.

The current v3 generator is a finite qualification lease, not the sustainable long-lived collector.
Keep its 72-hour deadline, three-campaign limit and aggregate request/byte ceilings frozen. Campaign
2's first permitted event followed the missed `03:00Z` setup interval, so its frozen scheduler
captured one anchor-only observation and sealed a terminal missed-gap STOP; this is not a normal
24-attempt STOP and was correctly rejected by the unchanged successor verifier. Sol adjudicated the
final c2 receipt and selected its finalized anchor for a separately versioned, single-use c3
continuation. `tools/brain_senses_campaign3_handoff.py` binds that receipt and both STOPs, verifies
c1/c2 actual use and c2 journal/capture reconciliation, preserves both gaps, and seeds observed
frontier 455116881 without claiming inventory there. C3 was prepared offline and its LaunchAgent
loaded at 04:20Z for the next future trigger, 05:05Z. The first frame must take a new finalized upper
anchor and may inventory only forward from 455116881. No bootstrap request, missed-slot backfill or
duplicate campaign is allowed. C1's zero use and c2's 1 request / 43 raw / 56 stored bytes are
debited from the unchanged aggregate caps. The v3 scheduler permits at most 24 attempts of 34
requests each, so c3's effective maximum is 816 requests even though the legacy manifest ceiling is
817; the attempt cap prevents a 25th frame. Handoff and activation receipt:
[`helius-inventory-v3-c3-handoff-activation-2026-10-10.json`](evidence/helius-inventory-v3-c3-handoff-activation-2026-10-10.json).

Campaign 3's first real frame completed at `20261010T050000Z`: the new finalized anchor was slot
455133321, and the reconciled inventory interval was `[455116881,455133322)`. Helius returned 16,437
produced-slot inventory members across 16,441 numeric positions; four positions were not returned
and are not interpreted as economic zero. All 34 request results captured and reconciled to
61,403,814 raw / 13,580,271 zstd-stored bytes. All 16/16 selected full-block structural checks
passed. This qualifies the bounded frame and its capture path only; it is not market/process
completeness or economic swap qualification. Receipt:
[`helius-inventory-v3-c3-first-frame-2026-10-10.json`](evidence/helius-inventory-v3-c3-first-frame-2026-10-10.json).
The c3 LaunchAgent remains loaded for its next future hourly trigger. Its full 24-attempt run and
terminal STOP remain unqualified. Any mismatch, second gap, uncertain request, resource/provider
failure or interrupted active reservation halts without replay. Only after c3's terminal STOP may a separate finite sustainable-service policy be assessed;
the candidate is a seven-day lease of up to seven daily campaigns, sized from observed frame tails
and free disk while preserving the 50 GiB floor and a complete-frame write margin. It has no
automatic renewal. The Helius key remains in mode-0600 private provider configuration and the
collector reads its endpoint in-process; the key is absent from LaunchAgent arguments. MCP package
configuration in Codex and Cursor is pinned to `helius-mcp@2.2.0`. Legacy `@latest` npm process
listings exposed the inherited credential, so those five old processes were terminated; after
cleanup, no exact-key process-list matches remained. The exact key was absent from targeted saved
histories and a 262 MB two-hour Unified Log scan; one old Cursor config-history snapshot was
redacted. Helius MCP authenticated, but did not expose credit usage or plan limits without a JWT
session; the MCP transport closed after legacy-process cleanup. This does not affect the separate
collector LaunchAgent. The c3 activation receipt's initial process-argument field used an invalid
macOS `sysctl` check and is superseded by the credential process-list remediation receipt. The
initial process-list tool output is visible in this session transcript. A successful local activation
does not qualify real provider throughput, completeness or economic coverage.

The paired finalized-block sensor runs a seeded 64-slot epoch each UTC hour through
`ai.memecoin.brain.senses.block-epochs`, with raw reservations and receipts under
`work/brain-senses-live-helius-hourly-20261009/`. Every selected Helius block is checked against its
signatures-only response and the range's `getBlocks` inventory. Four times per day (UTC hours 0, 6,
12, 18), the same selected range also requests public RPC; all other hourly epochs use Helius alone.
This yields up to 1,536 Helius sampled slots/day (about 0.48% of the measured slot rate), roughly
3,120 standard credits/day, and up to 256 paired public slots/day. At the first scheduled capture's
4.8 MB per full block, Helius raw growth is provisionally about 7.4 GB/day / 22 GB per 72-hour
campaign; this is a campaign design estimate to reconcile against actual bytes and failures. The
1M monthly Free allowance and 10 RPC RPS are source-plan limits, not a Brain coverage goal. The
72-hour root has a finite campaign stop, 4,608 Helius slots, 768 public paired slot attempts,
20,000 Helius credit reservations, and 32 GiB aggregate raw-response cap; these are this wave's
bounds and do not constrain future sampling. Buckets are idempotent: an attempted bucket is never
replayed. Missed intervals remain unknown. Public RPC
requests use the shared 1 request/s gate. The first six-hour scheduled range
`[454865291,454865311)` captured all 20 Helius full blocks
and all 20 Helius full-block results parsed. Public returned 11/20 full blocks; eight full requests
received HTTP 429 and one timed out. Its sampled signatures-only request received HTTP 429 and its
`getBlocks` enumeration failed at transport. All 11 paired blocks had the same identity and ordered
signatures. Of 13,716 paired transactions, 13,651 matched exactly; 65 differences were only
`meta.logMessages`, each with Helius's explicit `Log truncated` marker. Helius returned 4,033
PumpSwap structural instruction references (3,510 in successful and 523 in failed transactions);
the public sample returned 2,304 references from its 11 full blocks. These counts are not economic
trades. Public failures remain unknown. This is source/range-bounded observation, not global market
coverage or a lifecycle claim. Receipt:
[`helius-public-prospective-block-epoch-2026-10-09.json`](evidence/helius-public-prospective-block-epoch-2026-10-09.json).

The 64-slot/hour wave and the 1,000-credit verifier are bounded experiments, not coverage ceilings.
The first public-RPC epoch received HTTP 429 on 120/128 full-block attempts, so public stays a sparse
availability sentinel. A separate 32-slot same-slot Helius Free/Alchemy Free calibration qualified
all 32 blocks on both sources after the empty-base58 decoder correction. Inventories and block
identity/signature order agreed. Of 39,318 paired transactions, 38 differed only in
`meta.logMessages`, each with Helius's explicit `Log truncated` marker. Receipt:
[`alchemy-free-helius-same-slot-calibration-2026-10-09.json`](evidence/alchemy-free-helius-same-slot-calibration-2026-10-09.json).

The first broad independent-verification epoch is complete. Its immutable sample contains 16 seeded
contiguous 32-slot windows, one in each equal slot stratum of the preceding-hour slot frame. Helius
captured `getBlocks` inventory for 13,399 produced slots and all 512 selected block bodies; all 512
full/signatures-only ordered signature lists matched, qualifying 512 exact **structural-reference**
counts. Those counts total 69,311, including one exact zero structural-reference slot; the economic
trade count remains unknown. This does not establish completeness beyond the Helius-reported bounded
inventory.

Alchemy Free captured 32 selected full blocks and signatures-only self-checks. All 32 same-slot block
identities and ordered signatures matched Helius. Its `getBlocks` inventory request failed once at
transport and was not retried. The 32 bodies still support bounded same-slot comparison, but Alchemy
inventory completeness and Alchemy exact structural zeros remain unknown. Across 36,852 paired
transactions, 36,773 trees matched and 79 differed only in `meta.logMessages`; offline replay of the
raw captures confirmed Helius's explicit `Log truncated` marker in all 79. Receipt:
[`helius-alchemy-stratified-512-first-epoch-2026-10-09.json`](evidence/helius-alchemy-stratified-512-first-epoch-2026-10-09.json).

This first epoch used 1,026 Helius attempts including the anchor (1,025 reservation rows in the
block capture plus the anchor's expected reservation) and 66 Alchemy Free attempts (65 captured,
one failed inventory). No request was retried or paid. It stored 576,376,420 bytes from
2,355,735,101 raw response bytes with zstd, taking 1,410.8 seconds from anchors through qualification
summary. Repeating this exact epoch 24 times projects 13.83 GB stored/day. The denser prior
calibration projected 14.9 GB/day, so retain 18 GiB/day storage capacity and 30 GiB free-space floor
until more epochs replace the estimate. Current disk had 75 GiB free after this epoch.

Sol's current wave keeps Helius at 512 selected slots/hour for a full diurnal cycle and pairs Alchemy
Free on four slots in each of the 16 Helius windows (64/hour). The first epoch took about 23.5
minutes including qualification. Its expected Helius use is 24,624 for the day, before other
sensors; the 26,000 campaign cap and 36,000 shared local reservation envelope are per-wave guards,
not recurring quota or Brain ceilings. At the daily evidence boundary, review completeness,
provider discrepancies, failures, CPU time, bytes, and disk headroom; if guards remain healthy,
renew the same 24-hour wave twice, reviewing between days. Do not autoextend without those reviews.
Helius coverage is intentionally wide because its independent comparison can expose gaps hidden by
Alchemy-family agreement. Every epoch records inclusion probabilities, request outcomes, immutable
raw/stored hashes, codec, and receipt times. Fresh inventories are new observations, not retries.
The 512/hour sample is itself a current collection rate, not a permanent coverage ceiling; raise it
when measured discrepancies or uncertainty show material benefit and storage/recovery remain sound.
Preserve the separate 64-slot prospective collector and Helius watch if a broad wave reaches its
campaign cap or disk floor.

The subsequent `20261009T150000Z` pair64 epoch captured 512/512 Helius selected full blocks with
matching signatures-only checks across 16 strata. Alchemy Free captured and self-checked all 64
preselected same-slot pairs; all 64 block identities, ordered signatures, and structural-reference
counts matched Helius. Across 80,467 paired transactions, 80,321 trees matched exactly. The other
146 differed only at `meta.logMessages`; the offline audit replayed all 146 and found Helius's
explicit `Log truncated` marker in each. Alchemy's single `getBlocks` inventory request failed and
was not retried, so Alchemy inventory and its unsampled slots remain unknown. Helius reported 13,390
produced slots in the bounded frame; this does not establish global discovery or economic trade
completeness. Four Helius slots had exact zero **structural references** within that inventory; no
economic zero was inferred. Receipt:
[`helius-alchemy-pair64-offline-audit-2026-10-09.json`](evidence/helius-alchemy-pair64-offline-audit-2026-10-09.json).

The pair64 epoch used 1,154 block requests plus two provider anchors: 1,025 Helius block requests
(1,025 reservations; one more expected credit for the Helius anchor) and 129 Alchemy requests, of
which one Alchemy inventory request failed. It made no retries or paid requests. Captured response
bodies totalled 3,012,173,250 raw bytes including anchors, with 695,246,836 bytes zstd-stored for
block responses and 86 raw anchor bytes; the epoch folder including its manifests, logs, and offline
analysis is about 701 MB. Local receipt times
are the first observed availability to this capture process; no decision was made and no historical
availability is claimed. Helius's account-level usage meter remains unavailable.

The 512-slot/hour campaign was stopped after this first pair64 epoch. Its `CAMPAIGN_STOP` records
the evidence-gated pause, and its calendar LaunchAgent is unloaded; the old 64-slot Helius/public
campaign is also stopped and unloaded. Repeating pair64 for a day would project about 72 GB raw and
16.7 GB stored from the observed epoch, before other sensors, against 70 GiB free at review. Do not
resume this rate automatically. A later finite wave must bind a fresh sample plan, explicit request
and byte ceilings, disk headroom, meter state, and a review point; target unresolved discrepancies or
strata instead of replaying the same sample.

Both schedulers failed before starting new epochs because v2 campaign preflight compared two
persisted field names that did not match their written keys (`max_helius_credit_reservations` and
`max_single_epoch_expected_stored_bytes`). The bindings are fixed and the same campaign was
re-exercised with a no-RPC scheduler fixture; no charged request was repeated. The old public and
Helius decode watchers also repeatedly exited `ValueError` and were unloaded after their latest logs
were preserved. They did not produce qualified anomaly coverage; a future decoder monitor must be
bound to immutable block-epoch captures and emit explicit failures before it is relied on.

Helius watch v3 remains the only scheduled prospective sensor in this set. It is a capped sparse
program-address reference stream, not block or economic completeness. Its `20261009T180000Z` epoch
reached 1,100 attempts (1,099 captured, one failed) and recorded STOP with pending signatures. Its
captured response bodies total 23,446,112 raw bytes; the complete epoch root uses 42,216 KiB. The
measured mean is about 21.3 KB/attempt; the 4,400-request/day schedule projects about 93.8 MB/day in
response bodies. Current Helius billing docs price standard RPC at one credit for most methods, so
the request-only projection is about 132,000 credits per 30 days before any data overage or other
account traffic. Account usage remains unmetered here, so account-level headroom is unknown. The next
six-hour epoch is a fresh observation and does not bridge the pending gap. The old Helius address
watcher stopped at its runtime-exception STOP, and Public RPC watcher epochs v2/v3 stopped after
query failures with no retry. Their LaunchAgents are unloaded and their raw captures, logs, and STOP
records are retained. Public RPC remains a historical sparse control because HTTP 429 and timeout
failures dominate its earlier sample; no Public RPC watcher is currently scheduled.

The 2026-10-09 `brain-experience-v3` contract carries typed native coordinates alongside independent
UTC availability. Origins, dependency extents, target windows, maturity and role intervals name a
pinned coordinate domain. Historical slot replay may leave `decision_ns` null; it cannot qualify as
point-in-time forecasting. There is no slot-to-UTC conversion or implicit alignment across domains.
Snapshots remain dense f64 partitions, with coordinate-aware publication, replication, composition,
batches, objective eligibility and registered evaluation. Legacy v1/v2 contracts are unchanged.
Provider errors/stops, unresolved support, explicit skipped-slot proofs, unsupported instruction
traces, pending windows and exact zeros retain separate states; only a qualified complete support
window or a verifiable skipped-slot absence can certify an exact zero for the declared reference
process.

The real v3 replay is bounded to two adjacent finalized slots: 2,066 transaction comparisons,
147 recognized instruction-reference facts (98 in successful transactions, 49 in failed ones), one
matured H=1 target with count 87, and one pending target. Shared training batches admit the one
matured exact row. The two-row episode cannot form independent train/validation/test sets; no fit or
learning-gain comparison was run. Cross-provider agreement and ancestry prove consistency within
these captured responses, not that either provider returned every transaction in the global ledger.
No current real source qualifies complete market trades, market inactivity, lifecycle death, or
historical availability. Expanding to another provider or modality requires its own semantic and
coverage evidence; the architecture does not infer cross-domain joins.
