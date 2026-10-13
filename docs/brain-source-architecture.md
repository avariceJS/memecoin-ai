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

At 2026-10-11 09:47Z, Alchemy's read-only MCP usage summary remained at 18,680 compute units and
$0.01 for month-to-date, last 7 days, and last 30 days. This authenticated-team aggregate is not
repository/workload attribution, an invoice, or a raw RPC capture; the account limit was not returned.
Do not infer remaining headroom or research cost from it. Receipt:
[`Alchemy MCP usage summary`](evidence/alchemy-usage-summary-mcp-2026-10-11T0947Z.json).

The user-authorized Helius RPC credential remains in ignored, mode-0600 `.env.local` under
`MEMECOIN_HELIUS_RPC_URL`. On Oct 11, the Codex and Cursor MCP configs were found to also contain the
literal value and had launched MCP processes with it in argv. Both configs now invoke the same
owner-only mode-0700 local wrapper; it reads the key from `.env.local` and execs pinned
`helius-mcp@2.2.0` with the key in child environment only. A stub launch probe passed, and 22 stale
Helius MCP processes were terminated. At that remediation snapshot the MCP transport returned
`Transport closed`; the later read-only account check below supersedes that transport status.
A fresh exact-value scan found zero matches in 6,303
local Git objects, current process arguments, 1,100 recently modified accessible files
(480,372,344 bytes), or 78,507,845 bytes of recent Unified Log. `.env.local` was excluded as the
expected private credential source. Do not claim that all historical logs are clean: an Oct 10 scan
found three old copies in two Cursor JSONL transcripts, which were redacted and validated; see the
[`saved-transcript correction`](evidence/helius-mcp-saved-transcript-redaction-correction-2026-10-10.json)
and [`process remediation`](evidence/helius-mcp-credential-process-list-remediation-2026-10-10.json).
This agent-session process-list output also included the already-known credential and cannot be
retroactively erased from its transcript. Provider-side logs, remote conversation storage, backups,
Time Machine, and inaccessible services remain outside the scan. See the
[`resume exposure rescan`](evidence/helius-key-resume-exposure-rescan-2026-10-10.json).
The follow-up config/process remediation and delta scan are recorded in
[`Helius MCP wrapper remediation`](evidence/helius-key-exposure-delta-rescan-2026-10-11T0824Z.json).
A later runtime check found one legacy `helius-mcp@latest` process with the credential in its
arguments; that process and child were stopped. The pinned process remained credential-free in
argv. The exact-value scan then found no match in 6,385 Git objects, 1,073 recently modified
accessible files (509,279,953 bytes), current process arguments, or 1,313,273,944 bytes of
Unified Log output. The active session transcript still contains that process-list output and
cannot be erased; remote storage, backups, Time Machine, and inaccessible files were not scanned.
Receipt: [`stale MCP process stop and rescan`](evidence/helius-stale-latest-process-stop-rescan-2026-10-11T1043Z.json).
At 11:48Z, two unexpected legacy `helius-mcp@latest` processes were found beside the pinned
2.2.0 pair and stopped. The follow-up exact-value scan found zero matches in 6,412 local Git
objects, 2,292 recent accessible files / 533.6 MB, 65 launchd plists, current process arguments,
or 295.5 MB of Unified Log output. `.env.local` remains mode 0600 and the owner-only wrapper
continues to pass the key through child environment, not argv. No RPC or collector activation
occurred. The prior agent transcript remains outside filesystem erasure; remote storage, backups,
Time Machine, inaccessible files, and other sessions remain outside scan visibility. Receipt:
[`11:48Z exposure rescan`](evidence/helius-key-post-resume-exposure-scan-2026-10-11T1148Z.json).
At 12:41Z, a follow-up exact-value scan found no matches in 6,416 reachable Git objects, 27 recent
Codex log files / 500.4 MB, 82 recent repository files / 2.8 MB, 57 launchd plists, current process
arguments, or 846.7 MB of Unified Log. `.env.local` remains mode 0600 and the mode-0700 wrapper
passes the endpoint through child environment, not argv. No Helius RPC or collector activation
occurred. The preceding 11:48Z scan covered all then-local Git objects; this scan covers refs and
the listed post-scan roots. The active transcript, remote/provider storage, backups, Time Machine,
and inaccessible files remain outside visibility. Receipt:
[`12:41Z exposure rescan`](evidence/helius-key-post-resume-exposure-rescan-2026-10-11T1241Z.json).
A 13:37Z follow-up caught and stopped a stale `helius-mcp@latest` npm process whose argv contained
a credential-bearing environment assignment; its Node child was stopped as well. The subsequent
exact current endpoint/key scan found zero matches across 6,449 reachable Git objects, 48 recent
accessible files / 19.2 MB, current process argv, launchd state, and 1.98 GB of the prior two hours
of Unified Log. Active Codex and Cursor configs point to the owner-only pinned wrapper; the matching
saved Cursor history config did not contain the current exact endpoint/key. No RPC or collector
activation occurred. Remote/provider storage, backups, Time Machine, inaccessible files, and prior
session storage remain outside the scan. Receipt:
[`13:37Z post-stop exposure rescan`](evidence/helius-key-post-legacy-mcp-stop-exposure-rescan-2026-10-11T1337Z.json).
A 15:12Z delta rescan found zero exact URL/key matches across all 6,522 local Git objects,
36,343 recent accessible files (10.26 GB) under Codex, Cursor, Logs, and the research checkout,
current process arguments, launchd text, and 621 MB of the last two hours of Unified Log. The
owner-only wrapper still passes the key through its child environment; clean arguments do not prove
the key is absent from intentionally credential-bearing process environments. This scan excludes
provider-side or remote conversation storage, backups, inaccessible files, oversized files, and
other sessions. Receipt: [`15:12Z exposure delta rescan`](evidence/helius-key-current-exposure-delta-rescan-2026-10-11T1512Z.json).
At 15:44Z, a broader delta scan found 57 exact-key copies in three local Cursor SQLite databases:
one workspace-state value, one conversation-search body mirrored in its FTS content, and 54 values
in global state. The values were replaced with `[REDACTED]`; secure deletion, FTS optimization,
checkpoint and vacuum completed, SQLite integrity checks passed, and the databases/WALs are mode
0600. The post-redaction scan found zero exact key or endpoint matches in 364,193 recent files /
49.78 GB, all 6,540 Git objects, current process arguments, and 500 MB of last-hour Unified Log.
No local Time Machine snapshots were listed. A separate ChatGPT-hosted `helius-mcp@latest` worker
was present with no key or endpoint in argv; the project Codex config remains on the pinned owner-only
wrapper. Cursor stayed open while its 1.7 GB global SQLite store was redacted, so later in-memory
reserialization is not ruled out. Remote/provider storage, cloud/external backups, inaccessible or
oversized files, and prior session transcripts remain outside the scan. No provider RPC or Senses
activation occurred. Receipt: [`Cursor state redaction and exposure rescan`](evidence/helius-key-cursor-state-redaction-2026-10-11T1544Z.json).
A follow-up scan at 19:04Z found no exact endpoint/path/hostname matches across 2,861 Git blobs, no exact endpoint/path matches in 3,452 recent saved files (2.05 GB), no process-argument matches, and no match in 213.6 MB of the last hour of Unified Log. Two Codex history files had 67 public-hostname-only mentions; no endpoint path or key component was present. The c4 LaunchAgent remained path-only and the scheduled 19:05Z frame was active at the 19:06Z status read. Receipt: [`19:04Z exposure rescan`](evidence/helius-key-followup-exposure-rescan-2026-10-10T1904Z.json).
At 23:18Z a credential-config enumeration accidentally emitted the current value in one tool result
in this interactive agent session. The active session transcript cannot be retracted from this
checkout. A secret-safe follow-up found no exact value in 5,818 Git objects, 177 recent accessible
saved files (1.05 GB across Codex, Cursor, and local work/log roots), current process or c4
LaunchAgent arguments, or 82.2 MB of Unified Log; the three expected private configs remain mode
0600. Remote conversation storage, provider logs, backups, and Time Machine remain outside the scan.
The user authorized continued use without rotation. The current c4 LaunchAgent carries only fixed
paths and reads the private endpoint in process. Receipt:
[`23:18Z post-output exposure rescan`](evidence/helius-key-post-tool-output-rescan-2026-10-10T2318Z.json).
The 04:35Z delta scan then found zero exact matches in 6,055 Git objects, the current process list,
124 accessible saved files changed since 02:19Z (403.9 MB), and 1.204 GB of subsequent Unified Log;
`.env.local` remained owner-only mode 0600. It does not inspect provider/remote storage, backups,
Time Machine, prior session transcript storage, or 1,194 inaccessible files. Receipt:
[`04:35Z exposure delta`](evidence/helius-key-exposure-delta-2026-10-11T0435Z.json).
A separate 04:39Z check found no exact credential in 65 user/system launchd property lists or
current process arguments, and no Senses/Helius/inventory label loaded:
[`launchd exposure check`](evidence/helius-key-launchd-exposure-check-2026-10-11T0439Z.json).
After the user's explicit no-rotation authorization, the 05:12Z continuation scan again found no
exact current credential or URL match in 6,123 local Git objects, current process arguments, 65
launchd property lists, 7,384 recently changed accessible files, or 934.8 MB of subsequent Unified
Log. `.env.local` remained owner-only mode 0600. Provider/remote tool/session storage, inaccessible
files, backups, and Time Machine remain uninspectable. The prospective adapter continues to receive
only the private config path and resolve the endpoint in process. Receipt:
[`05:12Z continuation exposure rescan`](evidence/helius-key-continuation-exposure-rescan-2026-10-11T0512Z.json).
After the account-status MCP call failed with `Transport closed`, a 05:26Z wider scan also found no
exact current credential/URL matches in 6,146 local Git objects, 65 launchd plists, current process
arguments, 14,449 recently changed files / 390.8 MB, 26,650 npm cache/installer files / 4.66 GB,
shell history/startup files, or 874.0 MB of subsequent Unified Log. Alchemy MCP health returned
`pong`; no blockchain RPC was made. Remote/provider storage, inaccessible files, backups and Time
Machine remain outside this inspection. Receipt:
[`05:26Z broad exposure rescan`](evidence/helius-key-broad-exposure-rescan-2026-10-11T0526Z.json).
Helius's public materials checked 2026-10-11 describe Free as 1M credits/month and 10 standard
RPC requests/s, but these public defaults do not establish the current key's plan or headroom. The
dedicated [credit table](https://www.helius.dev/docs/billing/credits) lists `getBlock` and
`getBlocks` at 1 credit each, while the [pricing FAQ](https://www.helius.dev/pricing) says archival
calls cost 10 credits. Treat this as an unresolved published-source conflict. For offline capacity
scenarios only, reserve 10 credits per `getBlock`/`getBlocks` and 1 per `getSlot`; a 34-request
frame (1 `getSlot`, 1 `getBlocks`, 32 `getBlock`) is therefore 322 assumed credits, or 7,728 for
24 frames. These are planning assumptions, not verified charges, a billing ceiling, or lease
authorization. Earlier read-only account-status calls at 06:51Z and 07:27Z returned
`Transport closed`; plan, usage, reset, prepaid balance, overage and autoscaling settings remain
unknown. Helius documents that extra-credit autoscaling can
create charges, with defaults varying by plan ([additional credits](https://www.helius.dev/docs/billing/additional-credits)).
The user's 2026-10-11 continuation instruction supersedes the former requirement for an exact
account meter before any schedule planning. Actively use available Helius/Alchemy Free quotas for
research value; bounded, reasonable Alchemy PAYG use is also authorized. Missing billing telemetry
is not a global acquisition blocker: use conservative published assumptions and local hard caps,
record actual charges as unknown/unverified, and stop on quota, 429, billing, payment or provider
errors without replaying ambiguous requests. Escalate only materially different substantial paid
risk. This does not authorize plan upgrades or waive source, storage, clock, lineage and independently
reviewed finite-lease release gates. Standard WSS is available; enhanced `transactionSubscribe` and mainnet LaserStream gRPC
are not in Free. The old address-signature watch stopped after 4,350 reserved/captured requests,
with three transport failures and a growing backlog; it is preserved as a stopped source run, not
resumed as a per-signature completeness path. The 20,000/day setting was only that run's bound, not
a Brain quota or architecture ceiling. See [RPC rates](https://www.helius.dev/solana-rpc-nodes) and
[Helius WebSocket capabilities](https://www.helius.dev/blog/laserstream-websockets).

The later read-only account check succeeds: Helius reports an API key configured, but no JWT
session, so plan and credit usage are unavailable. No signup or account mutation was performed.
Alchemy's authenticated-team summary through 20:54Z still reports 18,680 CU and $0.01, without an
account usage-limit field; it does not attribute charges to this checkout. The source-bound result
and limitations are in [`provider account evidence v2`](evidence/senses-provider-account-readonly-2026-10-11-v2.json).
The 2026-10-12 02:24Z read-only Helius MCP status again found an authenticated configured key but
no JWT session, so plan, quota, balance, and charge history remain unavailable. No signup or account
mutation was performed. Receipt: [`Helius account status at 02:24Z`](evidence/helius-account-status-mcp-2026-10-12T022418Z.json).
The 2026-10-12 02:23Z read-only MCP snapshot still reports 18,680 CU and $0.01 through that minute;
it returns no account limit or app/network/method attribution and does not meter the finite Helius
lease. Receipt: [`Alchemy usage summary at 02:23Z`](evidence/alchemy-usage-summary-mcp-2026-10-12T0223Z.json).
The no-provider LaunchAgent timing rehearsal scheduled for 03:20Z on 2026-10-12 received one
calendar callback but exited 1 with `ModuleNotFoundError` before completing the read-only status
path. No provider request or status result is claimed; the activation was disabled/unloaded and is
terminal. The source lacked the repository-root import binding used by the working v2 launcher.
That bounded fix is covered by an isolated subprocess import test. Any retry uses a distinct future
activation with refreshed hashes; it cannot authorize provider I/O. Receipt:
[`03:20Z timing rehearsal failure`](evidence/brain-senses-external-timing-rehearsal-2026-10-12T0320Z.json).

The distinct 03:40Z no-provider calendar rehearsal then passed on its single LaunchAgent callback:
callback/preflight began within 3.3 seconds of the scheduled minute, the read-only terminal status
completed within 11 seconds, and a +30-second gate confirmed exactly one run with exit 0. The
service manifest, event journal and terminal STOP hashes remained unchanged; provider I/O,
dispatches, requests, reservations and intents were all zero. This qualifies only the scheduled
status-only timing path. It does not authorize or establish a prospective provider lease. Receipt:
[`03:40Z timing rehearsal`](evidence/brain-senses-external-timing-rehearsal-2026-10-12T0340Z.json).

At 03:24Z, read-only Helius status and plan calls again found no JWT session, leaving plan and
usage unavailable. Alchemy's authenticated-team aggregate remained 18,680 CU / $0.01 through
03:24Z; it returned no account limit or network/app/method attribution. This does not meter the
Helius lease or attribute cost to this checkout. Receipt:
[`provider account snapshot at 03:24Z`](evidence/senses-provider-account-readonly-2026-10-12T0324Z.json).

At 03:42Z, the read-only Helius account status and plan still had no JWT session, so plan, quota,
usage, balance and charges remain unknown. Alchemy's team aggregate was still 18,680 CU / $0.01
through 03:42Z with no returned account limit or workload attribution. A fresh dual-HTTPS clock
pair passed at 03:43Z; this evidence does not meter or attribute any prospective Helius request.
Receipt: [`provider account snapshot at 03:42Z`](evidence/senses-provider-account-readonly-2026-10-12T0342Z.json).

The Helius cost references were rechecked on 2026-10-12. Its current credit reference lists
historical `getBlock` and `getBlocks` at 1 credit, while the pricing FAQ still says archival calls
cost 10 credits ([credit reference](https://www.helius.dev/docs/billing/credits), [pricing FAQ](https://www.helius.dev/pricing)). Keep 10 credits per archival request for conservative offline sizing until the published conflict is resolved. The public Free plan lists 1M credits/month and 10 standard RPC requests/s; these public values do not establish this key's plan or available headroom.

The separately reviewed prospective Helius v2 plan for 02:30–03:33Z was initialized offline on the
bound Kingston root under host, storage, and predecessor locks. Launchd ran it once, but startup
completed after the bootstrap dispatch margin; the runner sealed terminal `missed_bootstrap` with
zero provider requests, zero reservations/intents, and verified v1 lineage. The label is unloaded
and disabled. No scheduled frame was activated; the plan is terminal, and no retry, catch-up,
backfill, or schedule shift is allowed. Actual provider charge remains unknown. Receipt:
[`v2 missed-bootstrap terminal`](evidence/brain-senses-external-v2-missed-bootstrap-terminal-2026-10-12.json).

At 2026-10-11 07:18Z, the internal APFS capture volume had 40,637,412 KiB free (~38.76 GiB), below
the reviewed 50 GiB floor; there is no selected successor schedule, so this is not a runway estimate.
A separate read-only inventory found an external 239.8 GB ExFAT volume with 164.6 GB free. It is not
eligible under the durable collector contract: the read-only check does not establish enforceable
0700/0600 ownership, create-only hard-link seals, file/directory `fsync`, `flock`, stable
device/mount identity, or no-replay recovery after disconnect. macOS can be configured to ignore
external-volume ownership and present files as owned by the current user, so reported ownership
alone would not prove access enforcement ([Apple Support](https://support.apple.com/en-il/guide/mac-help/mchlp1204/mac)).
The volume was not written to or inspected for contents; owner intent for use/retention, encryption,
throughput, durability and unplug/remount behavior are unknown. Any future qualification needs
separately authorized disposable writes on this exact mount and complete lease-runway evidence; do
not relax the current checks. Receipts:
[`07:18Z host readiness`](evidence/senses-storage-readiness-2026-10-11T071800Z.json) and
[`external volume candidate`](evidence/senses-external-volume-candidate-2026-10-11T072000Z.json).

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

The frozen sell23 ledger v2 was then replayed once across c3's six completed 05:00–10:00Z frames
(96 selected full blocks), excluding the failed 11:00Z anchor epoch. All 553 successful exact-class
candidates matched: 68 had exact target-account ledger settlement and 485 were censored partial
settlements. Two failed exact-layout structural references were counted separately and not treated
as executions. All six ledger equations passed for every matched candidate (3,318 checks); no
unknown target-account rows or unsupported outcomes occurred inside this eligible class. The
top-two pool concentration was 48/553 (8.68%) across 97 pools represented by successful or failed
exact-class references. This is forward temporal reuse of the frozen captured-class contract, not
independent economic validation, global discovery completeness, wallet-net execution price, or
training/execution admission. No RPC was made. Receipt:
[`c3 six-frame sell23 replay`](evidence/pumpswap-sell23-c3-six-frame-ledger-replay-2026-10-10.json).

An offline native-close feasibility census then replayed all six c3 frames against that hash-bound
replay and the unchanged qualifier source. It retained all 485 censored successes, 68 exact rows,
and two failed references outside the successful denominator. Each censored row has a transaction-
boundary lamport endpoint for its native token account, with zero pre/post delta, plus one legacy
Token account creation/initialization, one explicit System funding transfer, one `SyncNative`, and
one `CloseAccount`. The created account is Token-owned with 165 data bytes and 1,488,440 lamports
of initial funding; the additional observed System transfer into the account ranges from 101,318
to 1,404,978,552 lamports. The row manifest records these as source-flow candidates, not native
trade proceeds. All 485 close destinations also have transaction-boundary lamport endpoints, but
the transaction traces include other operations on those destinations. No row carries a pre/post
token-balance endpoint for the closed account or the Rent sysvar in its message, so the historical
rent-exempt reserve and resulting wrapped-token amount remain unqualified. The [official SPL Token
documentation](https://www.solana-program.com/docs/token) describes `SyncNative` as deriving
wrapped amount from lamports minus rent reserve, and `CloseAccount` as returning remaining SOL;
this feasibility receipt does not infer the reserve or attribute destination deltas. No economic
equation, wallet-net price, admission, RPC, or paid request resulted. Receipt:
[`c3 native-close feasibility`](evidence/pumpswap-sell23-c3-native-close-feasibility-2026-10-10.json).

A separately versioned `sell23` native-settlement sufficiency adapter validates a typed,
upstream-declared verified envelope and conservatively classifies exact legacy WSOL source-account
flows while preserving event/receipt/availability times and candidate/frame denominators. Its
synthetic tests cover ordered effects, fork/capture joins, incomplete endpoints, unsupported Token-2022
paths, and ambiguous destinations. It does not open or replay c3 captures, independently verify raw
evidence, establish rent or close-destination settlement, infer native proceeds or wallet net, or
admit `buyExact25`, training, or economics. This is a diagnostic interface and synthetic consistency
check only; the 485 c3 destination-ambiguous cases remain unchanged.

A separate transaction-local gate follows that census. Its frozen predicate re-enumerates the same
96 receipt-hash-verified c3 blocks and all 485 censored native-account rows. It checks every
ordered instruction that touches the temporary account, including Associated Token setup, legacy
Token initialization (including `InitializeImmutableOwner`), exact System create/funding effects,
`SyncNative`, the frozen sell23 instruction/transfer, and `CloseAccount`. The gate reports the
1,488,440-lamport account creation separately from other System credits and keeps lamports distinct
from token atomic units. A row can be `source_account_flow_exact` while its close destination remains
`destination_ambiguous`; destination sibling effects do not become an instruction-scoped credit.
The gate passed all 485 source-account flows with zero unsupported touches or missing endpoints;
all 485 close destinations remain ambiguous. Failed transaction refs remain outside the success
denominator. This result supports account-level conservation and existing qualified sell transfer
facts only; it does not infer historical rent, wrapped-token amount from SyncNative, wallet proceeds,
or admission. Receipt:
[`c3 native-account flow gate`](evidence/pumpswap-sell23-c3-native-account-flow-2026-10-10.json).

A separate process-level c3 check froze its exact six-frame inputs before opening transaction bodies
for this replay. The immutable source manifest pins 96 selected full-block captures, frame/plan/
summary hashes, request/response/capture identities, receipt and availability times, the three prior
c3 receipts, and decoder/source hashes. Replay independently joined all 485 censored rows to their
successful transactions; 68 exact-ledger rows were excluded from this process check and two failed
structural references remain separately counted. All 485 rows have transaction fee and destination
boundary lamports in the transaction response, but destination owner is absent, closed-account token
endpoints are absent, historical rent is unknown, and sibling destination instructions prevent
attributing the boundary delta to `CloseAccount`. Outcomes are 0 exact, 485 ambiguous, 0 unsupported.
This is bounded to the six successful c3 frames and does not establish ownership, rent, wrapped
token amount, destination proceeds, wallet net, price, USD PnL, edge, or training eligibility. No
Pair64 replay or provider request was needed. The [process qualification receipt](../work/brain-senses-sell23-process-qualification-v1/process-receipt-final-v1.json)
binds the frozen source manifest and committed analyzer; the manifest is at
`work/brain-senses-sell23-process-qualification-v1/source-manifest-v1.json`.

The destination-side v3 feasibility diagnostic preserves ordered opaque-touch paths and withholds
a newly-created destination's CreateAccount contribution when an earlier or enclosing potentially
writable touch, uncertain order, or unproven writability prevents qualification. A withheld raw
amount remains an unknown contribution, and endpoint minus the known partial sum is recorded only
as `unexplained_delta_lamports`; no complete-equation residual is available while any contribution
is unknown. The v3 replay retains 485 censored successes, excluding 68 exact-ledger successes and
two failed references. It decodes 473 System Transfers and 631 CreateAccount effects, records 2,803
opaque writable-touch paths, and has zero missing destination endpoints. All 485 rows remain
ambiguous; 473 corrected source-side candidates have unresolved parent semantics and 12 are
unsupported. No CloseAccount credit, destination owner, rent, wrapped amount, wallet net, or
economic admission is inferred. The [v3 destination lamport diagnostic receipt](evidence/pumpswap-sell23-c3-destination-lamport-diagnostic-v3-final-2026-10-12.json)
supersedes the immutable v1, initial v2, and corrected v2-final diagnostic receipts. Focused
pytest, Ruff, and the recorded scoped Mypy check pass.

The implementation is frozen before a prospective audit: inspect all eligible sell23 native-close
references in the first four completed scheduled c4 hourly frames after the freeze, including pass,
fail, and unsupported rows. If a scheduled bucket fails or is incomplete, preserve it as a gap and
use the next completed scheduled frame only under this precommitted rule. Do not add RPC calls,
select frames based on their contents, or tune against the audit. For readable pass examples, select
at most two passing rows per pool by ascending SHA-256 of the ASCII signature (signature is the
tie-breaker); this example selection does not reduce the all-row denominator. Record every mismatch.
Campaign STOP before four completed frames or too few eligible cases makes the audit inconclusive.

The first four eligible completed c4 frames were 14:00Z–17:00Z. A versioned audit correction
compares each frozen plan to SHA-256 of the canonical schedule JSON and records both that digest
and the raw schedule-file digest; the original pre-case binding failure and selector remain
unchanged. Metadata preflight confirmed the same four frames before the v2 tool was frozen. The
single corrected audit reconciled 388 sell23 structural references across 64 full blocks: 387
successful references and one failed-transaction reference. Of 342 native-close source-account
flow rows, 341 passed the frozen lamport-accounting predicate and one had an unsupported outer
instruction touch at `[4]` alongside target CPI `[4,0]`; that row remains quarantined. The other 45
successful references were outside the native-close gate. All 342 close destinations remain
ambiguous. This supports no wallet-net price, rent attribution, destination attribution, settlement
completeness, or admission claim, and it does not justify generalizing the earlier 485/485 c3
development result. The correction made zero provider requests and did not modify c4. Receipt:
[`sell23 c4 native-flow audit v2`](evidence/pumpswap-sell23-c4-native-flow-audit-v2-2026-10-10.json).

An independent raw-capture replay then re-read only those four pinned c4 frames and cross-checked
the frozen native-sufficiency adapter. It reproduced 388 structural references (387 successful,
one failed), 45 successful references outside native-close, and 342 native-close rows. The raw
source-flow diagnostic again classified 341 exact rows and one unsupported touch; the adapter
reported 341 `evidence_insufficient` rows and one `unsupported_instruction`, with 341 ambiguous
destinations and one unqualified destination. The independent raw-flow denominator remains 342
ambiguous destinations. No economic or training admission followed. The replay made zero provider
requests and left c3 and c4 tail2 unopened. Its receipt records the internal c4 root's observed
device, owner and inode; the c4 receipts do not pin a volume UUID or Kingston identity.
[`c4 first-four raw-capture replay`](evidence/pumpswap-sell23-c4-first-four-raw-capture-replay-2026-10-13.json).

A separate read-only capability census of those same four hash-verified frames records every
structural reference. All 388 transaction metas contain pre/post lamport and token-balance arrays
and an inner-instruction array; for the 342 native-close rows, the source and close-destination
lamport endpoints are present, while both closed-account token endpoints and account-program-owner
endpoints are missing. All 342 destination lamport endpoints remain unattributed because other
transaction instructions also touch the destination. The destination is the message signer and fee
payer in all 342 rows, but token-owner metadata and historical Rent are unavailable. The 341 exact
source-flow rows each show the expected temporary-account lifecycle; the remaining row fingerprints
an unsupported Jupiter outer touch at `[4]` (35 raw data bytes; SHA-256
`9fa596ce300b827773ac667b055ba63f1a2d14902fc71de99c687fc63e316a47`) alongside target CPI `[4,0]`.
The selected blocks contain 1,151 observed transactions with raw discriminator
`c62e1552b4d9e870` and 25-byte instruction data; examples retain only raw discriminator, path, and
observed program-flow counts, with no argument decoding. These are transaction-completion facts
available after execution: event time, capture receipt time, local availability time, and decision
time remain separate, and no decision-time availability is inferred. Wallet net, Rent, destination
settlement, economics, and admissions remain unqualified. Receipt:
[`c4 raw capability census`](evidence/pumpswap-sell23-c4-raw-capability-census-2026-10-13.json).

A separate bounded-search deviation emitted a truncated 13:00Z buyExact state-locator result before
the audit. Sol adjudicated that this artifact was outside the frozen 14:00Z–17:00Z sell23 population
and that no selected sell23 case was opened or used by the search. Because the search recursively
included `docs`, the handoff does not claim that no other c4-related documentation text was
incidentally scanned. The deviation did not alter selection or predicate; preserve its bounded scope
record with the audit evidence:
[`sell23 audit scope deviation`](evidence/sell23-c4-bounded-search-scope-deviation-2026-10-10.json).

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

The frozen structural census now accepts the sealed v3 epoch layout directly. Its v3 branch binds
the campaign/bucket and epoch-plan hash to the frame receipt, checks the sealed summary hash and
selected-block outcomes, then requires the captured full-block slots to equal that summary. The
default v2 input path remains unchanged; the Pair64 captured census replay returned the same counters
and matrix as its frozen receipt. Replaying the first prospective c3 frame found 365 references in
16 selected full blocks (15 reference-bearing slots), including 59 pinned 25-byte/two-account
documented-suffix structural candidates: 53 in successful transactions and six in failed attempts.
No normalization errors occurred. This adds prospective structural-yield monitoring only; it does
not establish fills, settlement, economic correctness, market completeness, or admission. The other
account/layout subgroups remain unresolved or unverified. Receipt:
[`buyExact c3 frame census`](evidence/pumpswap-buy-exact-v1-c3-frame-census-2026-10-10.json).
Its commit-id metadata correction is recorded separately at
[`buyExact c3 census correction`](evidence/pumpswap-buy-exact-v1-c3-frame-census-correction-2026-10-10.json).

A source-bound offline structural novelty digest independently normalized every transaction in
Pair64's 512 selected Helius blocks and c3's six completed 16-block frames. Pair64 had 642,312
transactions, 37,427 unique PumpSwap-referencing transactions and 72,034 raw instruction refs
(60,385 successful-status refs, 11,649 failed-status refs); four selected blocks had no PumpSwap
refs. Across c3's 96 selected blocks, 98,993 transactions yielded 6,858 unique referencing
transactions and 13,422 instruction refs (11,373 successful-status, 2,049 failed-status); one block
had no refs. Pair64's buyExact raw counts and bytes matched its frozen census; the two c3 frames with
frozen buyExact censuses and all six c3 sell23 frame denominators matched. The digest pins block
hashes and event/receipt times, records exact wire bytes and instruction paths, and leaves unknown
accounts positional. Exact trailing bytes are structural fingerprints only; this is not global
discovery, an economic classification, or an admission result. Receipt:
[`PumpSwap structural novelty digest`](evidence/pumpswap-structural-novelty-v1-2026-10-10.json).

The follow-up c4 replay extends that novelty scan across the ten included frames (160 selected full
blocks), independently reproducing 173,899 transactions and 22,319 PumpSwap instruction references
from the frozen captures. Per-slot success/failure structural counts and each frame's buyExact
census reconcile to the frozen metadata/replay receipt; the 23:00Z tail2 remains excluded. The
replay reports 1,336 wire-family fingerprints and 19,751 exact structural fingerprints across these
frames. Those are detector keys built from wire arguments, instruction paths, privilege context and
transaction status, not counts of protocol layouts or new economic processes. Detailed
capture/block/class bindings are zstd-compressed under ignored `work/` and hash-bound by the compact
receipt. This adds no economic qualification, exact-zero permission, global discovery, or admission.
Receipt: [`c4 structural novelty replay`](evidence/pumpswap-c4-structural-novelty-v1-2026-10-11.json).

The separately versioned structural unknown-queue compiler and its tests currently have
synthetic-only readiness. The pinned c4 receipt/detail pair above lacks per-transaction
success/failed/unknown partitions, transaction membership for each instruction reference, and
post-fsync durable-availability timestamps. Without those fields the compiler cannot establish the
required disjoint denominators or causal source-availability order, so it does not publish a queue
from c4. Its metadata-only readiness check confirms the ten included buckets and explicitly
excluded 23:00Z tail2 from the receipt-bound summary; it does not open capture response bodies.
The compiler is deterministic and publishes only after revalidating exact input/queue bytes; a
separate create-only receipt records the bound root/volume, source-availability ceiling, and local
post-fsync confirmation time. Per-reference capture joins and typed instruction paths are required;
the input builder must independently verify the pinned full-block and capture-receipt artifacts
before supplying their paths and hashes.

A separate venue-neutral program-exposure ledger covers all 608 same captured blocks: 512 Pair64
selected slots and six c3 frames of 16 slots each. It reconciles 741,305 normalized transactions
and 2,574,909 raw instruction references across outer/inner paths, transaction status, message
version and resolved program-key source; raw and normalized reference totals agree, and all seven
datasets are complete for their sealed inputs. It preserves per-capture observations and reports
cross-dataset signature overlap instead of deduplicating. Only the exact program ID in the pinned
official Pump AMM IDL receives a venue label; every other ID remains `unknown_program`. This is
selected-block structural exposure, not program discovery completeness, pool identity, token/market
birth, an economic event, or admission. The ledger uses about 5 MB of output and its offline replay
read 699 MB in 98 seconds on this Mac. Receipt:
[`program exposure ledger`](evidence/senses-program-exposure-ledger-v1-pair64-c3-2026-10-10.json).

An archival v2 observation projection now publishes 603 positive Pump AMM program-reference counts
over those same 608 selected finalized slots. Five captured local zeros remain census-only. Its
slot-native coordinate domain is explicit; each event identity binds network, process, program,
slot, and blockhash, while the per-slot count is payload. All source `availability_ns` values were
unknown; `projection_known_utc` is newly recorded as 2026-10-11 13:14:54Z after the frozen ledger
decision and latest receipt. The projected `Source` is event-only: it does not fabricate enrollment,
UTC availability, sparse support, or a slot index. The verifier independently checks all 22 sealed
metadata manifests and their selected-slot bindings, pinned source code, complete ordered source
list, the ledger reconstruction, and `Source.rows()` equality. `Source.index()` rejects the native
slot coordinate before creating a database. This remains an archival structural view of selected
captures: the 44,285 per-slot sum is not global distinct transactions, and zeros do not establish
global absence. Completeness, economic qualification, training, and production admission remain
false. Receipt: [`Pair64/c3 program-exposure projection`](evidence/senses-program-exposure-projection-v1-pair64-c3-2026-10-11.json).

A separately versioned archive projection now preserves every Pump AMM structural-reference
occurrence from the three sealed `external-v2-20261012T0600Z` frames. It reconciles 12 scheduled
frames (3 sealed, 9 unobserved), 4,164 produced slots, 48 selected blocks, 46,222 scanned
transactions (43,636 successful, 2,586 failed, zero unknown), 4,865 references (4,176 in
successful and 689 in failed transactions), and 4,595 sample-local exact fingerprint classes.
Unobserved-window metrics remain null. Each positive row binds its capture, response, request,
result, signature, instruction path, structural fingerprint, and source event/receipt/capture
availability times; `decision_ns`, execution, effect, and settlement remain null. Classes retain
the classification `unresolved_unsupported_no_decoder_claim`. Failed-transaction rows establish
only that a structural reference appeared in the captured transaction/trace. This archive does not
extrapolate the selected blocks, claim absence or process support, deduplicate market events, or
establish economics, lifecycle, point-in-time support, training, or admission. Projection-known
time is recorded after the immutable source receipt; it is not a historical availability claim.
The source queue SHA-256 is
`fd8a2ffb750d53eb877e1326c02419358c24870aeb7338ea444a121712f83b81`; the source receipt SHA-256 is
`23ede32e11040280024afd7da06b657a353f445850a55aecf99b4d11fcc294f4`. The descriptor-bound
Kingston artifact is
`derived/pump-amm-structural-archive-projection-v1/external-v2-20261012T0600Z/archival-projection.json`
(10,292,022 bytes; SHA-256
`b348ef424d26a3baa3b296ade34c136d7cac8ed428c803d3b3c2a3edc5c01a72`); its create-only publication
receipt is `derived/pump-amm-structural-archive-projection-v1/external-v2-20261012T0600Z/publication-receipt.json`
(SHA-256 `ca8eacdd774efa298b3c98edd3cb67722c3e3eaa11862c7113060ca4c25be670`). No provider I/O was
performed.

An offline structural-family pass then reopened only the 48 selected captured blocks and
reconciled every projected reference by capture, signature, typed instruction path, status and raw
instruction bytes. It groups references by the first up to eight raw bytes, full data length,
account count, outer/inner context and transaction status; matching a pinned IDL discriminator is
reported only as documented shape. The result contains 68 sample-local families, 4,865/4,865
reconciled occurrences (4,176 successful, 689 failed, zero unknown), 4,595 underlying exact
fingerprint classes, and 14 families unresolved or ambiguous across 18 pinned IDL versions. Each
family has at most three deterministic representatives. All nine unobserved windows retain null
metrics, and all output rows keep execution, effect, settlement and decision time unset. The
descriptor-bound, create-only Kingston artifact is
`derived/external-structural-family-triage-v1/external-v2-20261012T0600Z/structural-family-triage.json`
(20,259,829 bytes; SHA-256
`c5a83e4e65eb5227e7c2da16de4efb0a8365d21f6b008674f81ba04674b60ee0`); its publication receipt
SHA-256 is `ae6beb74e07035d00a289bd2b2eaa0c8eab5499da5527c7330ce5c9aebc51f02`. A second invocation
verified the existing publication without changing either file. This is a diagnostic over the
selected sample: an IDL shape match does not verify semantics, and an unresolved prefix does not
prove a new instruction. It establishes no execution, effect, economics, lifecycle, absence,
point-in-time support, process support, training, or admission. No provider I/O occurred. The
separate versioned external-v2 unknown-review adapter was then qualified from the immutable queue,
archive projection, and family-triage artifacts after revalidating their receipts, selected captures,
and descriptor-bound source prefix. Its 10,636,879-byte Kingston artifact is
`derived/external-v2-sample-unknown-review-queue-v1/external-v2-20261012T0600Z/diagnostic-queue.json`
(SHA-256 `aea8f395f932fdf3e989f60439b3bf8e0e2d1c2bc63b5503eb4a5a8c36cf6bcf`); its create-only
publication receipt SHA-256 is `ce7828e50ba4ee6f29754e3b224b713e4c9c8f01cfe5d1e786035b293efd8885`.
The queue has 4,865 observed positive references: 2,603 shapes match a pinned IDL's documented
shape with semantics unverified, and 2,262 are unmatched or ambiguous; it records zero source/parse
failures for this already-clean sealed prefix. Nine unobserved windows retain null metrics. Replay
recomputed and byte-verified the existing artifact and receipt. This is a sample-local diagnostic
work queue, not decoder support, an anomaly monitor/rate, first-ever or no-new-class evidence,
execution, economics, lifecycle, PIT, training, or admission. No provider I/O occurred. A separate
selected-block positive-reference readiness adapter then revalidated the queue/archive/family/
unknown-review artifacts and receipts, replayed the terminal source queue, reopened all 48 selected
full-block responses, and rechecked the 18 pinned official IDL history blobs. Its descriptor-bound,
create-only Kingston artifact is
`derived/external-v2-selected-block-positive-reference-readiness-v1/external-v2-20261012T0600Z/readiness.json`
(76,309 bytes; SHA-256 `8db4ac0c8d0081f50cc9a471f9e795fee321dfd6d271008ea67c5fb123d1aba5`); its
publication receipt SHA-256 is `3563d443a4e40f6e9ffc8aa5f53b191a48d18433fa32cc82a4c758d4120ba7db`.
It records 12 frame dispositions (3 sealed, 9 unobserved-null), 48 selected native slot/blockhash
pairs, 4,164 produced slots, and 4,865 sample-local positive references (4,176 successful-status,
689 failed-status, zero unknown), cross-checking 4,595 structural classes. Acquisition UTC frame
buckets and native slot coordinates stay separate; expected event count, decision time and
complete-support are unset/false. Exact replay recomputed and byte-verified the existing artifact
and receipt. This is not a formal `SupportAssertion`, population coverage, zero/absence, UTC event
mapping, economic/lifecycle qualification, training, or admission. No provider I/O occurred.

A commit-bound `create_pool` feasibility census independently replayed those same 608 sealed
selected blocks. The verifier reconciled 741,305 transactions, 1,613,839 outer and 961,070 inner
instruction references, and 85,456 Pump AMM references to the venue-neutral ledger and structural
novelty digest. It found two successful inner references in Pair64 with 18 accounts and 70-byte
instruction data; at census time, neither matched a profile in the existing birth decoder, so event
CPI and pool/mint/vault tuple checks remained unassessed. All six c3 frames had zero fixed-discriminator
references within their qualified selected-block processes. This is a captured-class
identity/feasibility result only: it does not establish pool birth, market population, lifecycle,
economic activity, settlement, or admission. Receipt:
[`PumpSwap create_pool feasibility census`](evidence/pumpswap-create-pool-feasibility-v1-pair64-c3-2026-10-11.json).

A follow-up source-bound investigation examined only those two sealed instruction identities and
all 18 IDL revisions returned by the pinned Pump public-docs history capture. Both 70-byte
`create_pool` instructions and 352-byte nested `CreatePoolEvent` payloads exactly match the
Sep 12 and Oct 7 official IDL snapshots; a separate explicit-offset parser agrees with the IDL
type walker. The 18 ordered account roles, eight shared argument/event fields, and seven
event/account role bindings match, and the Anchor `emit_cpi` source-derived tag matches the observed
CPI prefix. This establishes an official interface match for these captured bytes only. The deployed
Pump AMM binary identity and pool/mint/vault account state are unknown; no pool birth, lifecycle,
economic, settlement, population, or admission claim follows. Receipt:
[`PumpSwap create_pool source feasibility`](evidence/pumpswap-create-pool-source-feasibility-v1-2026-10-11.json).

A private, hash-bound wire-layout replay of only those two transactions enumerated 19 and 17
System/ATA references (18 and 16 touching declared roles); 15 and 13 matched the exact bounded
profiles, while four seven-account ATA shapes in each transaction remain unsupported. The replay
does not attribute balance deltas to instructions or establish final account state, pool birth,
economic validity, or admission. Private artifact SHA-256 is
`4d2dd08e35da6db6d1fa900b8b872704890b666b35efeabf4b6c42e9ebf6fdd5`; builder SHA-256 is
`f1a9efe19a732ac2f18844d79932f432660a28829e14b0598c7a85c937efa4f3`; focused test SHA-256 is
`8cf5093068d665ac54e43edc4763b9941f8241daf83475949d22938cb8ea2b55`. All three artifacts remain
mode 0600 under ignored `work/pumpswap-create-pool-source-feasibility-v1/`; no reusable decoder is
promoted from this two-transaction sample.

Sol then commissioned a venue-neutral System/ATA wire-shape census over the same seven accepted,
sealed datasets (Pair64 512 plus six c3 frames × 16). The replay reconciled all 608 selected slots,
741,305 transactions, and 2,574,909 outer/inner instruction references, including 407,729 System/ATA
references. It found 103,357 six-account and 675 seven-account ATA instructions with one-byte tags
0/1; 636 of the latter have `SysvarRent` as the seventh observed key and 39 have other keys. The two
prior `create_pool` candidate transactions still contain 19/17 System/ATA references, 18/16 of them
touching declared roles, with four seven-account shapes each. This is only wire-shape prevalence in
these selected captures, including failed transactions; it does not qualify deployed ATA semantics
or effects. Keep the seventh-account forms partitioned as unknown; no decoder or economic meaning is
promoted. The private artifact, implementation and test hashes are bound by the
[`System/ATA selected-block census`](evidence/senses-system-ata-wire-census-v1-2026-10-11.json).

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

An independent offline replay mapped the 59 first-frame two-account suffix candidates back to
their captured compiled instructions and message account keys, using the pinned IDL account order.
All 59 wire bytes, signatures, transaction indices, and success/failure states matched the census;
the five selected IDL positions matched every reported role. Across 27 pools, each pool had one
non-aliased mint/program tuple and no conflicting tuple. The observed distribution was two distinct
`base_mint` keys and 27 `quote_mint` keys in this sample; these are IDL-position observations, not
proof of trade direction, settlement, or asset orientation. The 53 successful-transaction refs
and six failed attempts remain structural references only. A second raw check verified both
trailing accounts in order for all 59 refs: the first is in the pinned eight-wallet set, and the
second derives as that wallet's ATA for the observed quote mint and token program; both match the
census. This qualifies only the exact two-account suffix roles. It does not show that fees moved or
that a transaction settled an economic fill. No economics or pool-state query was added. Receipt:
[`buyExact c3 role tuple qualification`](evidence/pumpswap-buy-exact-v1-c3-role-tuple-qualification-2026-10-10.json).

The remaining 306 references in the same first frame were replayed once from the same raw blocks.
All 306 ordered trailing wallet/ATA pairs matched the pinned recipient set, quote-mint/program ATA
derivation, and prior census, with 164 successful-transaction references and 142 failed attempts.
There were no discordant pairs; 301 references retain an unresolved leading account, and five have
no such prefix. Three rows preserve the census state `fee_pair_unqualified` because the recipient
was transaction-writable, despite the wallet/ATA identity pair matching. This verifies that pair's
structural identity only; it does not promote those rows to the 59-reference candidate subgroup.
The 24/26-byte argument layouts, all leading accounts, pool_v2/cashback/hook claims, fills, fee
movement, and Token-2022 effects remain unresolved or quarantined. Receipt:
[`buyExact c3 remaining suffix census`](evidence/pumpswap-buy-exact-v1-c3-extra-role-census-2026-10-10.json).

The current official [`@pump-fun/pump-swap-sdk@2.1.0`](https://www.npmjs.com/package/@pump-fun/pump-swap-sdk)
was also pinned and compared with the local official AMM IDL. Its `buy_exact_quote_in` fixed
23-account order and arguments match; the builder appends an optional quote-mint cashback ATA,
then readonly `pool-v2` when the pool creator is non-default, then the readonly fee wallet and its
writable quote ATA. The `pool-v2` derivation is `findProgramAddress(["pool-v2", base_mint],
Pump AMM program)`. The original [`c3 prefix receipt`](evidence/pumpswap-buy-exact-v1-c3-pool-v2-prefix-qualification-2026-10-10.json)
qualified the 192 rows supplied by its census report and bound each row to raw captures, but did not
independently prove that the report contained the full matching population; treat that receipt as
row qualification only. The corrected v2 qualifier independently normalizes every transaction in
all 16 hash-bound blocks (16,668 transactions), selects matching valid-boolean 25-byte/23-fixed/3-tail
references, verifies census source hashes and exact identity/field equality by slot, signature and
instruction path, and fails closed on normalization errors, duplicate identities, or set mismatch.
The raw set and report each contain 192 references. All 192 prefixes match the official SDK pool-v2
derivation; none matches its cashback ATA derivation. Raw message-header privileges and the
previously qualified fee pair leave 190 references with the expected readonly `pool-v2` plus fee
pair (134 successful transaction references and 56 failed attempt-only references). Two failed
references have transaction-wide writability on either `pool-v2` or the fee recipient and remain
quarantined. Receipt: [`c3 raw-reconciled pool-v2 prefix qualification`](evidence/pumpswap-buy-exact-v1-c3-pool-v2-prefix-raw-reconciled-2026-10-10.json).
The population-binding qualifier is `tools/brain_senses_buy_exact_v1_pool_v2_prefix_qualifier_v2.py`
with the official SDK adapter `tools/brain_senses_buy_exact_v1_pool_v2_pda_derive.cjs`. It binds
SDK package integrity, source hashes, IDL, fee-recipient set, frame receipt, raw captures, census
tool/source pins, and the exact raw/report candidate set. Apply it once to the first complete c4
frame whose bodies have not been inspected for this predicate; a c3 replay or already-inspected
frame is development/ordinary replication only. This is structural role/order qualification for the
selected captured-block class; settlement and economic admission remain unqualified. Other account
counts, Token-2022 effects, cashback combinations and unknown layouts remain quarantined.

The separately scoped c3 dual-legacy settlement feasibility gate replayed both already-open frames
against raw blocks (32 blocks, 450 matching instruction references: 360 successful and 90 failed
attempt-only). The frozen structural predicate accepted 447 references and retained three
structural mismatches; none of the four successful dual-legacy references had exact settlement.
All four are censored: two lack a transaction-boundary token endpoint and two have explicit
candidate-subtree versus endpoint-delta residuals. Each has 10–20 ordered outside-subtree touches
of target accounts, so sibling and lifecycle effects cannot be allocated to the candidate. Fees
remain hypotheses; wallet-net amounts, exact execution prices, training/execution admission, and
global discovery are unqualified. Sol closed this development gate at 4/4 censored, 0 exact; no
lifecycle extension or economic audit is justified on these four rows. Receipt:
[`c3 two-frame dual-legacy gate`](evidence/pumpswap-buy-exact-v1-c3-two-frame-dual-legacy-gate-v2-2026-10-10.json).
The next independent check is structural only: the first successfully completed scheduled c4 frame
at or after 18:00Z, selected by schedule metadata before payload inspection. No fallback frame,
backfill, or economic qualification is authorized by that selection rule.

The frozen development predicate selected all 62 successful refs in that captured census whose
payload is the pinned 25-byte shape, two-account suffix passes the source-bound checks, and both
base and quote use legacy SPL Token. The other 1,237 successful structural candidates include
Token-2022 on at least one side and remain quarantined. Offline replay of all 62 refs found one exact
`BuyEvent` and four checked legacy token transfers per ref; user quote-account gross debit matched
the event gross amount, pool quote credit matched its separate event field, and base credit matched
the event amount and minimum. The earlier nested ledger reconciled all six target-role token
accounts in all 62 transactions. A follow-up full-transaction touch classifier found one sibling-
touch case where that nested `exact` status is insufficient. Observed mint decimal pairs were 9/6 in
61 and 6/9 in one; amounts remain integer atomic units and no UI conversion is made. Gross/net and
fee-flow equations matched all 62, but remain empirical diagnostic hypotheses. This is narrow
development evidence only, with no wallet-net price, training, execution, or market-completeness
admission. Its deterministic plan
and replay receipt also bind a metadata-only manifest for 512 disjoint 13:00Z Helius full blocks.
That source was opened once by the frozen structural census on 2026-10-10. All 512 block payloads
were processed, but report construction failed because the source binding receipt uses `git_commit`
and omits optional `audit_code_commit` and `analysis_sha256` fields. No census output was published,
no per-reference outcomes were accepted, and no economic replay began. Preserve the failed attempt;
do not rerun or tune on this archive. Corrected code requires a new disjoint source and a new frozen
plan. Receipt: [`buyExact v1 failed audit attempt`](evidence/pumpswap-buy-exact-v1-audit-failure-2026-10-10.json).
Development receipt:
[`buyExact v1 development`](evidence/pumpswap-buy-exact-v1-development-qualification-2026-10-10.json).

A frozen follow-up diagnostic separates the candidate legacy `TransferChecked` instruction flow
from transaction-wide target-token endpoint reconciliation. Across all 62 frozen dual-legacy tail2
development references, candidate flows and `BuyEvent` cross-checks match 62/62. Endpoint equations
are exact for 61/62 only where all provider-reported target-account touches are classified; the
remaining transaction stays `unknown_partial` because sibling programs also touch target accounts,
even though the nested legacy-token ledger reports `exact`. All five fee equations matched the event
cross-check in this sample, but remain empirical hypotheses rather than fee laws. ATA route checks
are pinned to an official interface source; this is not deployed-binary proof. No audit source or RPC
was opened, and no training, execution, wallet-net-price, or broader economic settlement admission
is granted. Receipt: [`buyExact tail2 development diagnostic`](evidence/pumpswap-buy-exact-tail2-v1-development-qualification-2026-10-10.json).

An offline join against Pair64's 64 captured Alchemy same-slot full blocks found two overlaps among
the 62 frozen references. For both, block identity and signature matched; transaction payloads
matched after excluding `meta.logMessages`, and replayed candidate flows, target endpoint account
facts and event cross-checks agreed across provider payloads. This is two-row captured payload
consistency using the same decoder, not a second independent decoder or completeness evidence.
Receipt: [`buyExact tail2 Pair64 provider check`](evidence/pumpswap-buy-exact-tail2-pair64-provider-check-2026-10-10.json).

Sol preregistered a disjoint prospective tail2 audit using c4 metadata only. The frozen development
commit is `f7ec8c0` at 22:11:37Z; bucket eligibility uses scheduled UTC bucket start, so 22:00Z is
excluded and 23:00Z–02:00Z are the conditional nominal four frames. Select the earliest four
naturally scheduled, successfully sealed eligible frames in order; if a bucket is absent, failed,
partial or ambiguous, retain it in the scheduled-bucket denominator and continue only with the next
natural c4 frame. No manual tick, retry, backfill, shrinkage or replacement population is allowed.
The metadata-only preregistration binds the development/census sources and denominators. After four
frames seal, a metadata-only manifest and final plan must additionally bind exact capture/source
hashes and audit runner/test hashes before any payload opens. Fewer than four eligible frames at c4
STOP makes the audit incomplete. A zero-row census earns no replication claim; mismatches remain
visible and block clean replication. Reporting is limited to candidate legacy transfer flow,
BuyEvent agreement and endpoint state in these selected captured blocks. Fee equalities remain
hypotheses; settlement, wallet-net price, broader completeness and training/execution admission are
not established. Receipt:
[`tail2 c4 audit preregistration`](evidence/pumpswap-buy-exact-tail2-c4-audit-prereg-2026-10-10.json).

The metadata selector is implemented in `tools/brain_senses_buy_exact_tail2_c4_manifest.py`. It
refuses a changed preregistration, source pin, lease/schedule pin, malformed frame, request identity,
capture/runtime code pin, capture receipt, byte accounting, or deterministic inventory selection. It
hashes compressed responses without decompression and creates its manifest outside the frozen
campaign root only after four eligible successful frames exist. During an earlier pre-fix CLI
invocation with only the 23:00Z frame available, the 34 compressed response files in that frame
were read only for SHA-256 and 34 request/result/capture metadata rows were read; no bytes were
decompressed, displayed, classified, or used to select a frame or infer an outcome. No output,
request, or campaign-state change occurred. Sol accepted this as a disclosed procedural deviation;
the original frame remains eligible and no replacement is allowed. Receipt:
[`23:00Z hash-only preplan deviation`](evidence/pumpswap-buy-exact-tail2-c4-manifest-preplan-hash-only-deviation-2026-10-10.json).
The c4 campaign naturally stopped after its 11th successful frame, before the 24-attempt lease
completed, with immutable reason `minimum_free_disk_floor_plus_frame_envelope`. The 11 captured
frame summaries contain 769,657,370 raw bytes and 177,576,450 stored bytes (69.97 MB raw and 16.14
MB stored per frame on average); campaign `du` was 202,719,232 allocated bytes. This is a bounded
sample, not a growth guarantee. At 00:08Z after STOP, free space was 54,975,664,128 bytes, only
614,572,928 bytes above the existing 50 GiB floor plus 674 MB frame peak. The prior ten-frame mean
projection is not revised into a service budget: disk headroom and host churn leave no defensible
multi-day runway. Account headroom and Helius charge units remain unknown. Sizing receipt:
[`c4 abnormal-stop sizing`](evidence/helius-inventory-v3-c4-abnormal-stop-sizing-2026-10-11.json).
These are frame-only byte totals. The separate bootstrap used one `getSlot` request and 43 raw / 56
stored bytes; campaign all-in is 375 requests, 769,657,413 raw bytes and 177,576,506 stored bytes.
The immutable denominator receipt is
[`c4 abnormal-stop accounting`](evidence/helius-inventory-v3-c4-abnormal-stop-accounting-2026-10-11.json).
At 00:43:31Z, free disk had fallen to 49,089,638,400 bytes, below the 50 GiB floor. All 15
Senses LaunchAgents found in the user LaunchAgents directory have persistent disabled overrides and
none is loaded. The c4 plist remains on disk but its exact label is persistently disabled; the
process-argument scan found no current Helius endpoint. Receipt:
[`c4 LaunchAgent quiescence`](evidence/helius-c4-launchd-quiescence-2026-10-11.json). No prospective
collector is accumulating observations at this time.
The sole eligible post-freeze frame is 23:00Z; the next natural invocation wrote STOP, so the preregistered audit is
closed incomplete at 1 of 4 required frames. Its semantic payload remains unopened, and it supports
no qualified estimate. The corrected builder stops before content hashing unless four metadata
candidates are complete. Sol ruled that the existing service-v1 normal-STOP predecessor gate must
reject this c4 STOP; c4 cannot be relabeled or reused as a normal terminal. Any successor requires
a separately versioned abnormal-stop lineage and fresh review, with no c4 retry/backfill, a future
anchor-only start, verified disk runway and defensible account headroom.

The metadata-only final-plan builder and offline one-pass audit runner are now implemented in
`tools/brain_senses_buy_exact_tail2_c4_plan.py` and
`tools/brain_senses_buy_exact_tail2_c4_audit.py`. The plan binds every scheduled bucket through the
fourth natural success, selected-frame capture identities, the deviation receipt, frozen source and
dependency hashes, and a synthetic-only test receipt. It publishes a separate create-only freeze
receipt; both must be durable before semantic decompression. The synthetic test receipt binds test
files, all tested source/dependency hashes, the Python implementation/version and the result artifact.
The runner independently rechecks the
schedule denominator, source and artifact hashes, reservation state, request/capture identities,
full/signature block agreement and transaction-signature order, then opens each response once. A
post-start provenance or parsing error seals an incomplete receipt and prohibits replay. Semantic
disagreement is a completed negative result. The test receipt binds exact test source hashes, an
exit code, and a JUnit result artifact whose hash and outcome counts are independently revalidated
at plan and audit preflight.

Sol's population gate requires an explicit success/failure status for every captured transaction
and resolvable instruction programs for every successful transaction. Unresolved instruction
indexes, unknown PumpSwap instruction bytes, parser-unknown successful candidates, malformed
candidate detection or unknown transaction status withhold the exact captured successful-class
denominator; observed tail2
candidate checks are reported only as a lower bound. Known failed attempts remain separate:
unresolved failed instructions can make that attempt census incomplete without reducing or
contaminating the successful denominator. Parseable but unsupported instruction variants stay
classified and quarantined. If the population gate passes, a positive result can claim only candidate
checked-transfer and BuyEvent agreement on the selected captured block class. Fee equalities remain
hypotheses; no settlement, wallet-net price, global completeness, exact economic zero or admission
follows. Focused tests are synthetic only. The stopped campaign has 11/24 successful frames; no
eligible payload has been semantically opened and the frozen audit is incomplete, with no qualified
result.

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
The offline sparse-observation-reference v1 compiler builds role-local histories from qualified row
indexes without opening capture response bodies or combining train/evaluation payloads. The
descriptor-bound Kingston artifact is independently verified and records 170 train entities /
38,705 bars and 137 evaluation entities / 14,375 bars, across 1,060,992 declared 5-minute support
slots; 1,007,912 slots remain unobserved/unknown. The existing 32-consecutive baseline remains
8,792 train and 6,465 evaluation rows (15,257 total, across 79 entities). The compiler retains
request failures separately (one failed evaluation window; the remaining 1,227 indexed windows are
qualified/unknown support), uses only earlier event-time bars in histories, and marks point-in-time
eligibility false. A row's receipt is full-bar availability evidence only when it is at or after the
half-open bar end; historical online availability remains unknown. Volume units are null with an
unverified status. Receipt: [`sparse-observation reference v1`](evidence/brain-senses-sparse-observation-reference-v1-2026-10-12.json).
These structural counts do not establish complete coverage, inactivity, lifecycle, settlement,
labels, edge, or training admission.
The scheduled 04:30Z external-v2 initialization was archived and disabled before activation because
its detach qualification was stale. The initialized root remained byte-identical and independent
verification found no request, reservation, intent, seal, or STOP; the window was not replayed. The
failure and reconciliation evidence is recorded in
[`04:30Z v2 pre-activation abort`](evidence/brain-senses-external-v2-aborted-before-activation-2026-10-12T0430Z.json).
The separately versioned descriptor-bound integration qualification then passed on disposable
case-sensitive APFS. Forced detach at the guarded mkdir and a later atomic rename produced safe
syscall failures, no fallback artifacts, unchanged underlay, and no provider calls; Kingston was not
touched. Receipt: [`external-v2 descriptor detach v2`](evidence/external-v2-descriptor-detach-integration-qualification-2026-10-12-v2.json).
The new post-seal structural-input v1 compiler and sibling publisher are separately qualified. They
accept only independently verified sealed v2 captures, preserve transaction status and structural
scan unknowns in denominators, bind instruction occurrence paths, capture/result/receipt hashes and
availability, and publish outside the frozen source root. Existing publications are accepted only
after exact immutable receipt/manifest validation; gaps or unknown scans withhold publication. A
fresh disposable APFS run forced detach at mkdir, lock open, create, atomic rename and receipt create;
all five points left the underlay unchanged with no fallback artifact, provider call, replay, or open
handle. This qualifies the isolation mechanism only: the disposable volume did not meet the live
capacity envelope, Kingston was untouched, and no real v2 frame has been compiled or published.
Receipt: [`structural-input sibling-writer qualification v2`](evidence/brain-senses-structural-input-sibling-writer-qualification-v2.json).
The c4 structural queue remains unqualified and incompatible with this sibling schema; c4 stays
terminal and no historical gap is backfilled.
Sol accepted the exact 06:00Z prospective plan after checking its 21 source pins, the sealed-v1
predecessor at frontier 455802008, c4's terminal STOP at 455430160, and both inactive v2 siblings.
The candidate remains nonauthorizing until the complete final release chain passes. The fixed lease
allows at most 409 requests at 4 requests/second and 3.104 GB raw/stored bytes; no provider request
has yet occurred. If release checks miss the arm window, this schedule remains unused.
At 04:24Z, read-only Helius status reported a configured key but no JWT session, so plan and usage
remain unknown. Alchemy reported 18,680 CU / $0.01 across month-to-date, 7-day, and 30-day windows,
with no account limit or workload attribution; its hourly query returned no rows and does not prove
zero usage. No signup, payment, or provider RPC occurred. Receipt: [`04:24Z provider account snapshot`](evidence/senses-provider-account-readonly-2026-10-12T0424Z.json).
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
The historical MCP reference receipt below captured a 1-credit-per-method interpretation and its
34/816-credit estimates. The current public sources conflict as described above, so retain that
receipt as historical evidence only; do not use it as an account billing model or activation input.
Request count, public cost scenario, and actual account credits are distinct quantities. Receipt:
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

The next scheduled frame completed at `20261010T060000Z` on the adjacent forward interval
`[455133322,455149828)`, width 16,506. Helius returned 16,506 inventory members; all 34 request
results and capture hashes reconciled to 63,742,226 raw / 13,808,125 stored bytes, and all 16/16
selected block/signature checks passed. The inherited gaps remain untouched. Across these two frames,
68 bucket-and-sequence request identities are accounted; a repeated `getSlot` payload is the new
hourly anchor observation, not a retry. No method/parameter identity repeats within either frame.
Receipt: [`helius-inventory-v3-c3-frame2-2026-10-10.json`](evidence/helius-inventory-v3-c3-frame2-2026-10-10.json).

An offline replay of frame 2 found 404 unique candidate transactions and 479 instruction references
in 16 selected full blocks, with zero normalization errors. Of these, 418 invocations have the
pinned 25-byte layout and two or three remaining accounts: 160 in the +2 stratum and 258 in +3.
Every trailing wallet/ATA pair matched the pinned recipient set, raw quote-mint/token-program ATA
derivation, and census pair; all five pinned IDL roles matched raw compiled account indices. The
eligible references split into 376 successful and 42 failed transaction attempts. All 59 observed
pools have nonaliased base/quote mints and internally consistent role tuples; no duplicate paths or
tuple conflicts occurred. A +3 prefix remains unresolved even when its trailing pair matches.
The other 61 instruction references, including 24-/26-byte payloads and unsupported account
shapes, remain quarantined. This replicates structural role mapping across two prospective frames;
it does not establish fee movement, fill, settlement, economic orientation, or completeness. Receipts:
[`buyExact frame-2 census`](evidence/pumpswap-buy-exact-v1-c3-frame2-census-2026-10-10.json) and
[`buyExact frame-2 role replication`](evidence/pumpswap-buy-exact-v1-c3-frame2-role-replication-2026-10-10.json).

A later raw/report reconciliation of the exact 25-byte/23-fixed/three-tail subgroup independently
normalized all 16 frame-2 full blocks (16,426 transactions): raw and census sets matched 258/258,
with no normalization errors, duplicates, or set differences. The unchanged pool-v2 predicate
accepted 257 references (225 successful transactions and 32 failed attempt-only references) and
quarantined one successful reference because its fee wallet was globally writable. Raw compiled
instructions show that message key at account position 24 in both the outer sibling instruction and
the PumpSwap CPI; the sibling program's semantics remain unknown. This explains the observed message
privilege but does not waive the frozen transaction-wide readonly requirement. No economic settlement
was qualified. This is ordinary replication of an already examined development frame, scoped only to
its selected blocks. Receipt:
[`frame-2 raw-reconciled pool-v2 prefix replication`](evidence/pumpswap-buy-exact-v1-c3-frame2-pool-v2-prefix-raw-reconciled-2026-10-10.json).

Campaign 3 completed six forward-adjacent frames through `20261010T100000Z`, then terminated
abnormally on its reserved 11:00Z finalized `getSlot`: the request failed at transport, produced no
response bytes, and its provider charge remains unknown. Count it as the 205th attempted request.
The frontier remains 455216025; the failed interval is unknown. c3 is permanently terminal: do not
retry the bucket, adopt its uncertain request as free, reset its frontier, or backfill. Its root STOP,
frontier, failed request/result, reservation release and epoch summary are hash-bound in
[`c3 abnormal STOP`](evidence/helius-inventory-v3-c3-abnormal-stop-2026-10-10.json). The six prior
frames remain valid only within their captured provider-reported inventory and selected-block scope.

Sol authorized one separately versioned c4 recovery lease, independent of the closed 72-hour
controller: one `getSlot(finalized)` bootstrap in a fresh future UTC bucket, then at most 24 future
hourly frame attempts beginning from that new anchor. The c3 frontier is only the lower marker for
an explicit uncovered interval; the bootstrap makes no `getBlocks`/`getBlock` request against it.
The c4 lease caps its own all-in use at 817 request reservations, 4,000,000,000 raw bytes and
1,000,000,000 stored bytes. Its lineage separately records c1=0, c2=1 and c3=205 attempted
requests. Frames retain the 20,000-slot and 34-request bounds, and the existing 50-GiB disk floor
plus complete-frame peak margin. It has no successor or
auto-renewal. A missed/ambiguous bootstrap or any frame failure is terminal and is not replayed;
missed schedule buckets remain gaps. First full-frame acquisition is allowed only in the next
scheduled bucket after a verified bootstrap receipt. These bounds are this one recovery lease, not
future architecture ceilings. The Helius key remains in mode-0600 private provider configuration and
the collector reads its endpoint in-process; the key is absent from LaunchAgent arguments. MCP package
configuration in Codex and Cursor is pinned to `helius-mcp@2.2.0`. Legacy `@latest` npm process
listings exposed the inherited credential, so those five old processes were terminated; after
cleanup, no exact-key process-list matches remained. An initial saved-history scan was incomplete;
the later correction found three exact copies in two Cursor JSONL transcripts, redacted them in
place, retained valid JSONL, and set both files to mode 0600. Helius MCP authenticated, but did not
expose credit usage or plan limits without a JWT
session; the MCP transport closed after legacy-process cleanup. This does not affect the separate
collector LaunchAgent. The c3 activation receipt's initial process-argument field used an invalid
macOS `sysctl` check and is superseded by the credential process-list remediation receipt. The
initial process-list tool output is visible in this session transcript. A successful local activation
does not qualify real provider throughput, completeness or economic coverage.

At 11:38Z a fresh exact-value scan found the expected sole checkout occurrence in ignored, mode-0600
`.env.local`; it found no match in 5,130 Git objects, 8,786 current saved log/config files across
the local Codex, Cursor, Claude and Library Logs roots, or current process command lines. The loaded
c4 LaunchAgent carries no credential environment entry and its arguments contain only paths. This
scan did not inspect service-side conversation storage, backups, Time Machine or remote systems; the
previous credential-bearing process-list result remains visible in the prior agent session transcript.
Receipt: [`local Helius-key exposure scan`](evidence/helius-key-local-exposure-scan-2026-10-10.json).

At 12:30Z, the authorized no-rotation rescan again found the single expected `.env.local`
occurrence (mode 0600), with no exact match in 5,165 Git objects, 10,308 selected local saved
text/config/log files, or current process command lines. It did not inspect provider-side systems,
backups or remote services; the prior process-list disclosure remains in the agent-session
transcript. Receipt: [`local Helius-key exposure rescan`](evidence/helius-key-local-exposure-rescan-2026-10-10.json).

At 13:28Z, a post-redaction exact-value rescan found only the three expected mode-0600 private
configuration occurrences (`.env.local`, Codex MCP config, and Cursor MCP config). It found zero
matches in all 5,238 Git objects, 249,759 accessible saved text/runtime/evidence files, current
process command lines, or 1,285,040,709 bytes emitted by an eight-hour Unified Log scan. The three
historical Cursor transcript copies remain redacted. Remote provider logs and inaccessible service
storage were not inspected. Receipt:
[`post-redaction local Helius-key rescan`](evidence/helius-key-local-exposure-rescan-post-redaction-2026-10-10.json).

Before c4's next calendar collection, a 13:55Z exact-value rescan covered all 5,253 Git objects,
253,570 accessible saved text/runtime/evidence files (23,476,338,195 bytes), 1,313,859,327 bytes of
the last eight hours of macOS Unified Log output, and current process command lines; all had zero
matches. The only three matches are in the expected mode-0600 `.env.local` and Codex/Cursor MCP
configs. The c4 LaunchAgent remains loaded with path-only arguments, and the MCP server remains
pinned with the credential in environment configuration. Provider-side and inaccessible remote
storage are outside this scan. Receipt:
[`pre-resume Helius-key rescan`](evidence/helius-key-pre-resume-exposure-rescan-2026-10-10.json).

At 14:30Z, after prospective collection resumed, a follow-up scan found zero exact credential or
endpoint matches in all 5,318 current Git objects, 681 active process command lines, 4,995
accessible files modified since the previous full scan, and 90,043,541 bytes of new Unified Log
output. The only expected exact matches remain in the three mode-0600 private configurations.
Helius MCP is pinned to `helius-mcp@2.2.0` and receives its credential through environment
configuration; c4 LaunchAgent arguments contain only fixed paths and commands, while the collector
loads `.env.local` in-process. The c4 LaunchAgent is loaded idle between calendar triggers with two
complete frames and zero open reservations. Provider-side, remote, backup, and inaccessible service
storage remain outside the scan. Receipt:
[`post-resume Helius-key rescan`](evidence/helius-key-post-resume-exposure-rescan-2026-10-10.json).

The 18:43Z follow-up scanned all 2,861 Git blobs, 324,977 checkout/recent saved files, current
process arguments, and 703,165,178 bytes of recent Unified Log output. No endpoint or credential
component appeared; 68 matches were only the public Helius hostname in two mode-0600 Codex history
files. `.env.local` and the two private MCP configurations remain mode 0600. The c4 LaunchAgent
remains loaded idle with path-only arguments. Provider-side, remote, backup and inaccessible service
storage remain outside scope. Receipt:
[`follow-up Helius-key rescan`](evidence/helius-key-followup-exposure-rescan-2026-10-10.json).

Campaign 4 was prepared and its calendar LaunchAgent loaded idle at 11:28Z, with the first permitted
bootstrap at 12:05Z. The activation-time disk check found 73,256,988,672 free bytes against
54,361,091,200 required for the floor and full-frame peak margin. It had zero runs, reservations,
attempt directories or captured bytes at the receipt boundary. The c3/controller predecessors were
persistently disabled; by 11:33Z the failed scheduler, decode watchers, capped address-watch streams
and completed fixed-sample LaunchAgents were also persistently disabled, with their files and
STOP/raw evidence retained. c4 is not an address-index replacement. No exact Helius-key match was
found in process arguments. Receipt:
[`c4 activation`](evidence/helius-inventory-v3-c4-activation-2026-10-10.json).

The 12:05Z c4 trigger completed exactly one fresh finalized `getSlot` request at 455,249,112 and
recorded frontier 455,249,113. It made no `getBlocks` or `getBlock` requests, retained the uncovered
interval `[455216025,455249113)` without backfill, reconciled 43 raw / 56 stored bytes, and left zero
reservations. The c3 terminal STOP hash is unchanged. The LaunchAgent had one run with exit code 0,
no frame attempts yet, and about 73.10 GB free after bootstrap. Its next full frame is only the
13:05Z scheduled bucket; no manual invocation or request replay is permitted. Receipt:
[`c4 bootstrap`](evidence/helius-inventory-v3-c4-bootstrap-2026-10-10.json).

The scheduled 13:05Z c4 frame completed from the new frontier over `[455249113,455265709)`. Its
34/34 request/result journals reconciled (one `getSlot`, one `getBlocks`, 16 signature-only and 16
full `getBlock` responses), and every response was captured. The provider returned all 16,596
produced-slot inventory members; all 16 selected full-block/signature identity and ordered-signature
checks passed. The frame stored 58,434,290 raw and 13,182,819 compressed bytes, with no open
reservation or campaign STOP. No c3 gap was fetched. This proves only selected-block source
consistency inside the provider-reported range, not economic or global market completeness. Receipt:
[`c4 first frame`](evidence/helius-inventory-v3-c4-first-frame-2026-10-10.json).
From first request start through final receipt (91.38 seconds), measured transfer/capture throughput
was 0.639 MB/s raw and 0.144 MB/s compressed, with a 4.43:1 raw-to-stored ratio. At one such frame
per hour this is a provisional 816 reservations, 1.40 GB raw and 0.316 GB compressed per day;
these are first-frame projections, not billing credits or architecture limits. No provider credit
counter was available in this runtime.

The frozen buyExact pool-v2 three-tail qualifier was applied once to this first eligible untouched
c4 frame. Its independent raw-block normalization covered 17,607 transactions in 16 full blocks;
the exact valid-boolean 25-byte/23-fixed/three-tail candidate set matched the census 171/171 with no
normalization failures, duplicates, or set differences. All 171 references passed the pinned SDK
pool-v2/fee-wallet/ATA structural predicate: 141 successful-transaction references and 30 failed
attempt-only references. No economic settlement, fee movement, historical pool initialization,
wallet-net result, training eligibility, or market completeness was qualified. Later c4 frames are
replication only, not independent audit or classifier tuning. Receipts:
[`c4 buyExact census`](evidence/pumpswap-buy-exact-v1-c4-first-frame-census-2026-10-10.json) and
[`c4 raw-reconciled pool-v2 qualification`](evidence/pumpswap-buy-exact-v1-c4-first-frame-pool-v2-prefix-raw-reconciled-2026-10-10.json).

Following the metadata-only pre-registration, the unchanged qualifier was applied once to the
still-unopened c4 `18:00Z` frame. The scheduled capture reconciled 34/34 requests and 16/16 sampled
block/signature checks; the offline census normalized 17,784 transactions and 427 PumpSwap
instruction references with zero normalization errors. Its full matching 25-byte/23-fixed/three-tail
class contained 271 candidates; raw/report identities matched. The frozen SDK predicate accepted
270/271: all 132 successful references passed, while 138/139 failed attempt-only references passed
and one failed reference was quarantined because `pool-v2` was transaction-writable. No settlement,
fee movement, exact zero, wallet-net result, training admission, or global completeness was assessed.
The selector, limits, and no-fallback rule are bound by the
[`c4 future-frame pre-registration`](evidence/pumpswap-buy-exact-v1-c4-future-frame-structural-audit-prereg-2026-10-10.json);
frame and replay receipts are
[`c4 18:00Z frame`](evidence/helius-inventory-v3-c4-frame-20261010T180000Z.json),
[`c4 18:00Z census`](evidence/pumpswap-buy-exact-v1-c4-1800-structural-census-2026-10-10.json),
and [`c4 18:00Z pool-v2 structural qualification`](evidence/pumpswap-buy-exact-v1-c4-1800-pool-v2-structural-qualification-2026-10-10.json).

Sol authorized one frozen offline development trace over those 141 successful c4 references. It
revalidated all 16 raw full-block captures against their receipt hashes, then recorded each
candidate's exact instruction bytes/arguments, every transaction instruction touching its 26
supplied account roles, ordered token/system account operands, Pump event bytes/path, and transaction
endpoint availability. The 140 Token-2022-base/legacy-quote rows remain without bank-time extension
or withheld state; no fee, conservation, or event-to-transfer equations ran. The single dual-legacy
row has an exact role-matching descendant BuyEvent but remains censored by outside-subtree touches,
unsupported target-touching programs, and incomplete token endpoints, so its conditional ledger gate
also stayed closed. Across 3,665 unique target-account endpoints, all had pre/post lamport values;
841 had compatible pre/post token rows and transaction-boundary deltas, while 2,824 were missing
token rows and remain unknown. These are whole-transaction endpoint facts, never candidate-buy
settlement. The artifact preserves 2,334 ordered target-touching instructions and 138 exact
role-matching descendant BuyEvents; three references have no such event. It admits no class,
wallet-net price, fee schedule, training, or execution claim. Receipt:
[`c4 successful-reference trace`](evidence/pumpswap-buy-exact-v1-c4-success-trace-2026-10-10.json.zst).

Sol then authorized an offline state-bracketing feasibility census over those 140 Token-2022-base
references. It revalidated all 16 receipt-hash-bound full blocks (17,607 transactions total) and
recorded each reference's two mint roles, seven token-account roles, static/ALT provenance,
transaction index, and every earlier/later same-slot transaction with writable potential. These
privileges are possible-write indicators only. Of 140 rows, 137 have one exact role-matching
descendant BuyEvent and three do not; 139 have at least one missing or partial token endpoint, and
all 140 have an outside-subtree target-account touch. Ninety-one rows have no other-transaction
writable potential on either mint, but zero pass the mint-only bank-state bracket after same-
transaction mint touches and event association are checked. Zero pass the full-account bracket;
no single-row acquisition control was emitted and no state RPC was made. The receipt lists required
bank-time raw bytes, owners, lamports, TLVs, and extension dependencies without inferring their
values. It establishes no fee, effect, settlement, decoder admission, or market completeness:
[`c4 state-bracketing feasibility`](evidence/pumpswap-buy-exact-v1-c4-state-bracketing-feasibility-2026-10-10.json).

A separate offline mint-state locator then rechecked only the 140 frozen Token-2022-base
identities against those same 16 receipt-hash-bound blocks (17,607 transactions). All 140 target
transactions passed the successful-status, base-mint-readonly, and distinct-resolved-fee-payer
gate. No other same-slot transaction had writable potential for those target mints, so all 140
remain candidates for both the S-1 and S whole-account snapshot boundaries. Writable privilege is
only potential; failed transactions were treated as nonblocking only when failure and a distinct
resolved fee payer were known, because transaction fees persist on failure. No account snapshot was
requested or acquired, and this establishes no state value, mutation/effect, lifecycle, settlement,
economic qualification, or provider completeness. Receipt:
[`c4 mint-state snapshot locator`](evidence/pumpswap-buy-exact-v1-c4-state-snapshot-locator-2026-10-10.json).

An offline Account Archive manifest independently joined those 140 locator identities to the
feasibility rows by slot, transaction identity, blockhash and Token-2022 base-mint role, and matched
the 16 capture response/compressed hashes and receipt times. Deduplication yields 75 unique
`(mint,S-1)` and 75 unique `(mint,S)` point reads. The manifest requires full `base64` account bytes,
owner, lamports, space and explicit TLV parse records; it distinguishes `value: null` nonexistence,
out-of-coverage, provider/transport failures, malformed responses and cross-source conflicts. Its
proposed ceiling is 150 requests, 9,830,400 raw response bytes and 1,500 published standard CUs;
these are planning caps only, not authorization or account-meter evidence. Alchemy documents
historical `getAccountInfo(slot)` as paid-plan only (Free is unavailable), with slot S representing
the latest finalized write at or before S and response context at the requested slot. The research
endpoint, entitlement, archive coverage and account-specific billing remain unverified. The prior
three-read control stays frozen at `frozen_no_rpc_access_gate_pending`; this manifest made zero RPCs.
Equal S-1/S bytes would not reconstruct state at the target transaction position or prove Token-2022
effects. With 139/140 partial token endpoints and outside-subtree touches in all 140 rows, buy
settlement remains prohibited. Sources: [Alchemy Account Archive](https://www.alchemy.com/docs/solana/account-archive),
[Alchemy `getAccountInfo`](https://www.alchemy.com/docs/chains/solana/solana-api-endpoints/get-account-info),
[Solana `getAccountInfo` RPC reference](https://solana.com/docs/rpc/http/getaccountinfo). Receipt:
[`c4 Alchemy Account Archive proposed mint manifest`](evidence/alchemy-account-archive-c4-mint-snapshot-manifest-2026-10-10.json).

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

At the 2026-10-09 stop boundary, Helius watch v3 was the only scheduled prospective sensor in this
older service set. It was a capped sparse program-address reference stream, not block or economic
completeness. Its `20261009T180000Z` epoch
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

## Seven-day prospective Helius service policy (offline implementation, 2026-10-10)

The next service is separately versioned from the old three-campaign/72-hour controller. Its
offline policy ledger lives in `tools/brain_senses_service_v1.py`; it does not create a LaunchAgent,
load credentials, invoke an RPC client, or modify campaign 4. It requires a terminal c4 proof bound
to both the STOP hash and terminal receipt hash, with zero post-STOP requests, before initialization.
The offline controller now has `seal_from_epoch`, which derives a service seal from the unique
reservation's daily v3 epoch path. It verifies pinned schedule/source and epoch-plan hashes, request
journal identities, capture receipts and response hashes, produced-slot selection, anchor and
frontier values, request/raw/stored accounting, and physical campaign bytes before releasing the
single-writer marker. Missing or inconsistent evidence leaves that marker in place and writes a
service STOP. Synthetic tests use a fake client and make no RPC.

One exclusive controller owns one outstanding reservation at a time and an append-only,
hash-chained ledger across daily campaign roots. Campaign IDs are unique per UTC date and their
manifests are create-only. Each bucket reservation pins its campaign ID, UTC bucket, deterministic
seed, uniform-without-replacement selection from finalized `getBlocks` members, width cap, codec,
and request/byte/disk budgets before anchor I/O. Authorization expires within seven days, and
allows at most seven daily campaigns, 24 hourly buckets per day, and 5,719 requests all-in. A
regular frame reserves 34 requests; an external bootstrap reserves one finalized `getSlot` request.
Bootstrap, recovery, and sleep-gap anchors run in a separate v2 epoch tree; only a validated, sealed
anchor lineage may initialize a fresh v3 campaign. Its first frame must be at a strictly later hourly
bucket. The v3 scheduler and v2 epoch source remain unchanged. A missed bucket is retained as a gap;
the next future tick gets an anchor-only epoch, and repeated sleep gaps may each use a distinct future
anchor. A first started ambiguous frame attempt is charged as spent, its unstarted request capacity
is released, and one fresh campaign generation may recover at a later future bucket with a new
external anchor. That recovery does not advance the prior committed frontier; the uncovered interval
is explicit. A second ambiguity, an ambiguous bootstrap, or inconsistent evidence writes STOP.
Reservations survive restart and cannot be sent again.

Per-frame ceilings remain 256 MB raw / 128 MB stored, frame width at most 20,000 slots, and daily
ceilings 4 GB raw / 1 GB stored. The service's provisional aggregate ceilings are 12 GB logical raw
and 3 GB stored, based on the observed two-frame c4 total of 123,935,342 raw and 27,413,524 stored
bytes at 14:31Z; these are provisional and subject to a 24-frame tail review and preflight. At that
check, `du` reported 31,292 KiB allocated and `df` reported 75,550,884 KiB free. Before each new
reservation, the controller requires at least 50 GiB free plus the full-frame peak margin and checks
physical allocated bytes under its campaign root against the stored cap. It never deletes captures
to make room. Request counts are not charge units; provider credit usage remains unverified, and no
Alchemy pairing or paid archive path is included. The read-only `--status-only` command performs no
provider call and reports ledger totals, pending reservations, physical bytes, free disk, reset
count, and STOP. Its sealed timestamp projection keeps native slot and nullable block time separate
from request start, response receipt, availability, and decision nanoseconds. For ambiguous
transport failures, receipt and availability remain unknown; request-start time is reported only
when the request journal captured it, never inferred from reservation time.

The separate runner entry point and LaunchAgent template are
`tools/brain_senses_service_v1_runner.py` and
`ops/launchd/ai.memecoin.brain.senses.helius-service-v1.plist`. They are offline implementation
artifacts only; the template is uninstalled and the service is not activated. The template passes
fixed absolute paths only, contains no credential in argv or environment, and sets `RunAtLoad` and
`KeepAlive` to false. Its environment sets a fixed system-only `PATH` and the versioned absolute
Homebrew zstd executable. Before initialization, the runner pins its own source hash, the shared
private-file reader hash, and the template hash in the service root. Its read-only status path does
not load credentials or contact a provider. A scheduled tick takes an exclusive runner lock,
reconciles outstanding service state before credential loading, and accepts only the current UTC
bucket during minutes 05–15; late ticks, duplicate bucket IDs, and missing credentials create no
reservation or provider call. After activation and source checks but before loading credentials or
reserving bootstrap/frame work, each tick verifies that zstd is usable. Missing or unusable zstd
returns sanitized `no_rpc` without a reservation. The credential loader reads the private
mode-0600 configuration through a no-follow descriptor, validates stable file identity, and keeps
the HTTPS RPC endpoint in-process. Its URL checks independently reject duplicate config/query keys,
userinfo, fragments, non-default ports, and non-Helius hosts. Tick-time activation requires a
matching normal c4 STOP/zero-post-STOP receipt, a reviewed gate receipt for measured tails, account
limits and authorization, and an independent check that c4's exact LaunchAgent label is unloaded.
After a durable service STOP, the runner only attempts to boot out its own exact label. The focused
runner and service suites pass 80 synthetic tests; Sol independently accepted the zstd path and
pre-reservation check as an offline operational fix. These checks do not eliminate removal of the
external binary after preflight and are not live launchd or activation evidence.

The activation check binds c4's exact versioned campaign ID, LaunchAgent label, root, STOP, and
terminal receipt. Under a host-wide and service-root exclusive lock, each scheduled tick probes the
exact c4 label and inventories the Senses LaunchAgent namespace plus running `brain_senses_*.py`
processes. Unknown inventory, another writer, or an indeterminate launchd response blocks provider
work. The census repeats after a frame reservation and immediately before the scheduler handoff. If
that frame reservation is still `reserved_not_started`, a failed recheck records a zero-request
unstarted seal and preserves a future anchor/gap transition; it never retries that bucket. Crash
recovery keeps the writer marker until the controller gap state and seal are durable, and validates
the unstarted evidence on restart. This behavior is covered by injected controller-write/seal-append
crashes, append-before-writer recovery, and started/mismatched/activity rejection tests. Offline
`initialize` can still create the new root and binding metadata without this tick-time gate; it
performs no provider I/O. The host lock coordinates service v1 runners; launchd/process snapshots
exclude other observed writers but are not an atomic lock against a newly launched noncooperating
process. Activation therefore remains prohibited until the full c4 terminal, storage-tail,
account-limit, and independent-review gates pass.

A source-bound host readiness snapshot at 2026-10-11 06:58Z binds the intended `work/` capture
root to APFS `disk3s5` in container `disk3`, with a 494,384,795,648-byte container. `df -Pk`
reported 19,990,468 KiB available (~19.06 GiB); APFS reported 20,470,231,040 bytes unallocated,
consistent within 8 KiB. This is 30.94 GiB below the 50 GiB floor. `tmutil` reported no local
snapshots. `du -sk` measured the research `work/` tree at 27,779,080 KiB, Docker at 69,823,064
KiB, and Steam at 106,697,988 KiB; the latter two are inventory-only and were not modified. The
`diskutil info` `FreeSpace` field returned zero for this APFS volume and conflicts with `df` and
container unallocated space; it is excluded from free-space decisions. The historical c4 mean
stored rate is about 388 MB/day over ten frames, not a long-term tail. No successor finite schedule
is selected, so no lease runway is claimed. Helius account status again returned `Transport closed`;
available units, reset and overage terms remain unknown. No RPC, lease, deletion or LaunchAgent
change occurred. Receipt:
[`current storage/account readiness snapshot`](evidence/senses-storage-readiness-2026-10-11T065300Z.json).

The exclusive writer marker spans v2 reservation, epoch
execution/reconciliation, immutable capture/result/summary lineage, and v3 campaign initialization.
Restart reconciles both roots, validates their hashes and accounting, and does not replay a started
request. A successful external bootstrap binds the prior c4 committed frontier, anchor slot,
receipt/availability times, explicit uncovered interval, and bootstrap bucket before initializing
the new campaign. At an exactly adjacent UTC day boundary, it reconciles the previous campaign's
last sealed bootstrap/frame, acquires the old v3 schedule lock, and records the canonical v3
administrative no-RPC STOP. A closure receipt hashes that STOP and prior root evidence; new-generation
lineage carries forward the last anchor, receipt time, frontier, inventory interval, and gaps before
initializing the next g01 root and reserving its first frame. The old root remains non-writable
through the canonical scheduler. A missed midnight uses the future anchor-only path and records no
invented frame or gap. Initialization repairs only its proven unreserved manifest/controller/ledger
prefix; an append-before-writer crash is sealed as provably unstarted. The offline suite uses
synthetic clients only; no provider RPC, LaunchAgent load, or service activation is claimed. Account
credit and the c4 terminal/tail gates still prevent activation.

Activation remains gated on c4's actual STOP and terminal/no-post-STOP receipt, measured tails for
24 frames or an abnormal-stop dossier, account read-only credit/overage status or a conservative
account cap, and operational review of the separate service runner. Synthetic evidence covers
restart, bootstrap, sleep gaps, ambiguity, generation handoff, lease/campaign/day/request limits,
and writer exclusion. After any future activation, the first qualification is one observed seven-day
run before considering a longer policy. This policy proves no provider or chain completeness and
authorizes no economics or decoder changes.

Sol conditionally approved the existing service's post-c4 activation procedure on 2026-10-10; this
has no effect on the running c4 lease. Activation requires, in order: a normal durable STOP at the
24-attempt cap; a terminal receipt binding STOP, epoch plans/summaries, request journals, and
reservation release; no ambiguous/open reservation or post-STOP request; and the exact c4
LaunchAgent unloaded. Early/abnormal STOP cannot satisfy this normal service-v1 gate; a distinct
successor requires its own Sol review even if an abnormal terminal receipt is completed. Reconcile
all 24 real frame request/raw/stored/latency/width tails, `du`, and free-disk headroom before fixing
the new service's finite budget. The first five c4 frames initially projected about 12.4 GB raw
across 167 service frames, above the provisional 12 GB raw cap. An interim ten-frame sizing replay
accounts for all 341 requests: 11 `getSlot` anchors, 10 `getBlocks` inventories, 160 full `getBlock`
captures and 160 signature checks. All 341 provider charge units remain `unknown_unverified`. The
replay measures 706.3 MB raw and 161.8 MB stored across completed frames; per-frame mean is 70.63 MB
raw / 16.18 MB stored, with 19.9–165.2 seconds anchor-to-seal (median 36.8 seconds). At that mean,
167 future frames project 11.79 GB raw and 2.70 GB stored. Across 160 full-block calls, request-start
to-receipt latency is 1.27 seconds median, 9.02 seconds p95, and 27.13 seconds maximum. The 160
signature checks have 0.506 seconds p95; 10 inventories have 0.927 seconds p95; 11 anchors have
5.67 seconds p95. This interim projection is close to the raw cap and has no defensible tail margin;
it does not revise the cap. Reconcile all 24 tails before
choosing an earlier hard STOP or reviewing a cap revision; never relax a live manifest. Confirm
account credit/overage status if exposed; otherwise pin a conservative
no-paid-overage request envelope (at most 5,719 requests), retain unknown provider charge units, and
stop on quota/429 conditions. Independently review source and plist pins, one-writer and no-retry
crash evidence, sanitized `PATH`/zstd, fixed path-only arguments, mode-0600 credential loading,
secret-free logs, and absence of another loaded Senses writer.

Only after those gates may a fresh service ID be initialized with authorization time after c4 STOP
and expiry within seven days, binding exact c4 terminal hashes. Load the single scheduled agent and
let a future calendar tick make the one-request bootstrap; do not manually kickstart, catch up, or
replay. The next distinct scheduled bucket is the first 34-request frame. Accept this deployment only
after both requests/frames reconcile to immutable captures, ledger/frontier/gap lineage, availability
times, and disk accounting. Then monitor read-only through the bounded lease and review the observed
run before any longer renewal. This conditional procedure does not authorize Cortex or new economic
claims.

The offline implementation is `tools/brain_senses_c4_terminal_qualifier.py`. It independently
recomputes c4 inventory selection and structural summaries from captured payloads, rechecks every
request/result/reservation and byte/time boundary, and indexes acquisition files by size, mtime and
SHA-256. After normal STOP it may create exactly one derived `terminal-receipt.json`; it never
changes c4 acquisition artifacts, calls RPC, retries, backfills, or loads credentials. Receipt
creation requires external operational snapshots spanning at least 70 minutes, with the first within
seven minutes of STOP, exact c4 LaunchAgent/process absence, stable artifact inventory, and a
complete filtered Unified Log query. The post-STOP zero-request claim is scoped to c4-attributable
activity through pinned local collector paths; it is not provider-side or host-wide network silence.
The service predecessor gate reruns the verifier and pins its source hash. Focused offline tests
cover accounting helpers, late request/reservation rejection, and service-gate delegation. This
normal verifier requires the 24-attempt finite STOP and cannot qualify c4's 11-frame disk STOP.

For this specific abnormal STOP, Sol approved a separately versioned metadata-only terminal
qualifier in `tools/brain_senses_c4_abnormal_terminal_qualifier.py`. It reconciles frame and
bootstrap request/byte denominators from immutable summaries and request-result metadata, binds
plans, reservation releases, response digests, journal hashes, frontier and STOP, and does not open
or decompress response bodies. Its stated result is limited to terminal control, accounting and
provenance; it does not independently replay response bodies or the structural census, does not
requalify market semantics, and grants no successor activation. The 23:00Z payload remains closed
under the frozen four-frame tail2 preregistration (1/4, incomplete).

The first operational snapshot remains evidence of process, LaunchAgent and artifact state at
00:08:11Z. Its saved `log show` window was formatted as UTC wall time and interpreted by macOS as
Europe/Moscow local time, so it queried the wrong three-hour interval; that original receipt is
preserved and explicitly excluded from post-STOP log coverage. The abnormal path performs a separate
read-only historical Unified Log query with explicit Europe/Moscow conversion and records its actual
later capture time, UTC event interval, local arguments, timezone offset, output hash and event
summary. A second process/LaunchAgent observation must occur after the 70-minute STOP window. The
query is captured after a 71-minute safety margin and covers at least the full 70-minute interval;
it establishes only local retained-log coverage, not provider-side or host-wide network silence. If
the corrected query or metadata reconciliation fails, terminal proof stays incomplete.

The corrected metadata-only proof completed at 01:16Z on 2026-10-11. Its log receipt covers
00:05:00Z–01:16:26Z (71 minutes 26 seconds) and records nine allowlisted OS events; the paired
observations show no c4 process, Senses writer, or loaded Senses LaunchAgent. The immutable
[`abnormal c4 terminal receipt`](evidence/helius-inventory-v3-c4-abnormal-terminal-2026-10-11.json)
has SHA-256 `8b97dd4936ddcd6e8494d35e06cb2a86d01929589e4c4c4c5ff7ba91825cbe65` and passed verifier
replay after publication. It proves local abnormal-stop control, accounting and provenance only.
It does not qualify c4 response bodies, prove provider-side silence, close the unknown-ended gap,
or authorize any successor. Free disk after verification was 47,633,055,744 bytes, 6,054,035,456
bytes below the 50 GiB floor; refresh disk and account evidence and obtain a fresh Sol decision
before any prospective acquisition.

Sol approved `initial-entry/discontinuity-v1` as an offline certificate and inert proposal gate only.
`tools/brain_senses_initial_entry_discontinuity_v1.py` rebuilds the certificate from the pinned c4
abnormal-terminal receipt after the existing metadata-only qualifier replays it;
`tools/brain_senses_initial_entry_discontinuity_verifier_v1.py` independently reconstructs and
checks the certificate and any proposal. The certificate preserves all three c4 preexisting gap
objects, adds a deterministic c4 STOP discontinuity gap, and carries the full unknown continuation
separately with `end_exclusive=null`. Its SHA is a distinct predecessor digest, never a normal v3
terminal handoff. A proposed lease must use a new ID, be created after the verified terminal
observation, start with a future anchor bucket, and carry the exact frontier, gap IDs, and unknown
interval. Later caller-scheduled anchor buckets may reset after gaps; schedule monotonicity and
lease expiry are checked without inventing a cadence. Time fields are UTC nanoseconds; frontier,
anchor, and gap endpoints are slot heights.
The verifier reports both `certificate_file_sha256` (exact artifact bytes) and
`predecessor_digest_sha256` (canonical sorted compact JSON); proposals bind the latter under that
same `predecessor_digest_sha256` name.
This protocol cannot validate a future anchor, close the unknown interval, initialize or activate a
lease, or authorize RPC. Its metadata-only evidence boundary leaves all c4 response bodies and the
frozen 23:00Z tail2 body unopened; no provider-side silence or account-headroom claim is added.
Certificate: [`senses initial-entry/discontinuity v1 c4`](evidence/senses-initial-entry-discontinuity-v1-c4-2026-10-11.json).
Its source-bound offline validation receipt records 21 focused tests:
[`initial-entry/discontinuity v1 validation`](evidence/senses-initial-entry-discontinuity-v1-offline-validation-2026-10-11.json).

Sol approved a separately versioned offline compiler/verifier for an inert candidate mapping from
that certificate to finite-calendar-v3 / operation-envelope-v2. The candidate records
`predecessor_digest_kind=initial_entry_discontinuity_certificate_v1` separately from
`predecessor.terminal_sha256` (the certificate's canonical digest), and also binds the exact
certificate-file SHA, frontier, all four ordered gap IDs, and the open unknown interval. Caller
inputs supply the new lease ID, UTC-nanosecond clock/lease/bucket times, finite schedule, caps, and
source hashes. The first bucket must be a future anchor; schedule order, duplicate IDs, expiry,
cap shape, source hash shape, clock watermark, and the 50 GiB physical floor are checked. Candidate
metadata includes hashes of the compiler, independent verifier, calendar driver, operation bridge,
child-v3 runner, and their verifiers; the compiler/verifier hashes are computed from the source
files at run time, with no embedded self-hash. The independent verifier reconstructs these pins
and the plan from separate expected inputs. Slot heights and UTC nanoseconds remain distinct units.
Clock values and source hashes are caller-supplied pins, not authenticated fresh evidence. The
stateless compiler cannot establish lease-ID uniqueness across separate candidate packets; a future
activation gate must verify evidence and claim one successor atomically.

The compiler only replays the metadata-only c4 certificate verifier and emits a hash-bound JSON
candidate. It does not initialize a lease or authorize activation. Synthetic qualification proves
that four inherited gaps clear only after a captured finalized anchor, and the next frame begins at
that anchor slot plus one; the handoff preserves the exact gaps and unknown interval. The 36 focused
tests include crash-prefix checks that prove no child replay. A durable observation source for the
c4 terminal watermark may expire from local Unified Log; requalification and an initial-entry
activation-preflight branch remain future gates. No candidate schedule was selected, no lease was
initialized, and no provider request or production mutation occurred. Receipt:
[`initial-entry calendar compiler v1 validation`](evidence/senses-initial-entry-calendar-compiler-v1-offline-validation-2026-10-11.json).

Sol approved a separately versioned, two-bucket c5 forward-only HTTP pilot policy. Its offline
state model is `tools/brain_senses_c5_forward_pilot_v1.py`: one explicitly pre-pinned future UTC
bucket may reserve one finalized `getSlot` anchor; only the immediately following pre-pinned bucket
may reserve one 34-request frame. Starts are accepted only during the scheduled UTC minute 05–15.
The plan binds the c4 abnormal terminal receipt, STOP, 23:00Z
frontier/anchor/request times, predecessor gaps, and the unknown-ended disk-STOP continuation.
The new anchor can close only the interval `[455430160, anchor + 1)` and seed forward capture.
The hard envelope is 35 total requests, 1 MB bootstrap raw/stored, 256 MB frame raw, 128 MB frame
stored, and 20,000 slots. A missed bucket, interruption, failed or ambiguous request, journal
uncertainty, or intervening gap is terminal for review; there is no retry, replacement bucket,
third bucket, manual tick, or backfill. The separate runner shell is
`tools/brain_senses_c5_http_runner_v1.py`; imports and its `status` command are read-only. A future
scheduled run requires a create-only mode-0600 activation receipt binding the reviewed plan,
source/plist hashes, exact c4 terminal/STOP/root/label pins, reviewer decision and expiry, the two
future buckets, 35-request cap, and hashed account/disk/review/churn evidence. The production
activation verifier currently fails closed: official raw-source parsers and the independently
pinned reviewer-decision digest are not yet present. Local summaries or synthetic fixtures cannot
establish provider account headroom, no-overage enforcement, disk churn, or reviewer provenance.
The future account parser must bind hashed official source bytes, request/receipt times, account/key
fingerprint, plan/reset/validity, used and available units, the pinned 35-method charge schedule or
provider-enforced no-overage rule, and shared-client use through frame expiry. The disk parser must
bind target-volume identity/free-space evidence plus source-hashed comparable historical peak/churn
samples, then recompute the composite against the 61,834,628,800-byte minimum. The uninstalled
path-only template is
`ops/launchd/ai.memecoin.brain.senses.c5-forward-pilot-v1.plist.template` (`RunAtLoad=false`,
`KeepAlive=false`). Before credential access it checks the exact plan/source/predecessor pins, the
scheduled UTC minute and bucket, exact `launchctl print gui/<uid>` service table and disabled-label
inventory (including c4 disabled and unloaded), launchd PID-to-runner PID linkage, exact tokenized
path-only runner argv, root reconciliation, fresh disk headroom and real-clock window, and the same
shared host-writer lock used by service-v1.
It validates the existing mode-0600 `MEMECOIN_HELIUS_RPC_URL` using the service-v1 Helius host and
query rules in-process only. The v2 bootstrap adapter
reserves one `getSlot`; the unchanged v3 scheduler reserves one 34-request frame. Both adapter
journals prohibit replay: an outstanding reservation or uncertain outcome is terminal and keeps
an explicit uncovered interval. The bootstrap lineage binds the plan hash and a finite controller
lease through minute 15:59 of the frame bucket’s UTC hour. The service ledger is hash-chained and
reserves before dispatch; wall clock and disk headroom are checked again before reservation and immediately
before adapter dispatch. STOP creation is atomic and create-only, preserving the first terminal
reason across outer error handling.
No production runner/default adapter, real private config, provider call, production-root
initialization, or LaunchAgent operation was invoked in this implementation task; the template
remains uninstalled and no operational bucket pair has been instantiated. Focused fake-only runner
and policy validation passed 66 tests; these checks cover synthetic transitions, evidence predicates,
endpoint rules, lease/clock/STOP behavior, and lock/preflight gates, not live provider account/disk
evidence. The sanitized parser was also exercised against read-only target-host command output. The
observed short host-disk samples do not substantiate an adverse-churn margin; activation-gate
validation therefore fails closed until reviewed raw-source parsers, a pinned reviewer decision,
and matching account/disk evidence exist. Synthetic churn fixtures test predicates only and are not
operational evidence. A target-host inventory probe and fresh account/disk evidence are required
before any activation decision.
Current disk remains below the 50 GiB floor and Helius account/credit capacity remains unknown, so
both external gates remain closed. Independent review and a fresh activation decision remain
required.

The frozen c4 captured-class replay is recorded at
[`pumpswap-c4-captured-class-replay-2026-10-11.json`](evidence/pumpswap-c4-captured-class-replay-2026-10-11.json).
Its metadata manifest was independently pinned by internal freeze digest, manifest-byte digest,
repository HEAD, and campaign root before replay; the 23:00Z body remained unopened. The 10 included
frames contain 160 selected full blocks, 173,899 transactions, 591,869 resolved instruction
references, 22,319 PumpSwap instruction references, and 3,555 buyExact candidate transactions / 3,556
instruction references. The 13:00Z and 18:00Z census counts and matrices reproduce their previously
published receipts exactly. This broadens captured-class structural exposure only; it does not prove
transaction discovery or economic-process completeness, exact economic zero, settlement semantics,
or model admission. The source-bound replay tool is
`tools/brain_senses_c4_captured_class_replay_v1.py`.

An offline source-first qualification compared the 16 c4 IDL-unrecognized discriminator values
against 18 captured Pump AMM IDL revisions available by the c4 capture. Its immutable input
inventory is [`c4 unrecognized discriminator census`](evidence/pumpswap-c4-idl-unrecognized-discriminators-v1-2026-10-11.json).
Fifteen values resolve to
one documented instruction name each, pinned to the historical IDL commit, time, blob digest, and
Pump AMM program ID. These are source identities only; they do not establish deployed behavior,
valid account roles, or economic semantics. The remaining value matches Anchor's generic
`EVENT_IX_TAG_LE`. In all 9,598 included c4 references, the captured inner instruction has the tag
and a unique historical IDL event discriminator, exactly one account resolving to Pump AMM's
`__event_authority` PDA, and direct Pump AMM parent ancestry in the transaction instruction trace.
The ten nested event-name counts reconcile across the references; 8,903 are in successful-status
transactions and 695 in failed-status transactions. This freezes only structural event-CPI shape:
event payloads are not decoded or validated, failed transactions do not become successful events,
and no settlement/economic admission, global completeness, or exact economic zero follows.
The replay implementation is `tools/brain_senses_pumpswap_idl_history_qualification_v1.py`; its
focused primitive/stack tests include a sibling-program ancestry counterexample. The 23:00Z c4 tail2
body remains unopened. Receipt:
[`c4 historical IDL and event-CPI structural qualification`](evidence/pumpswap-c4-idl-history-qualification-v3-2026-10-11.json).

A separate offline development replay then parsed only the 4,384 BuyEvent and 4,746 SellEvent
payloads in those same ten included frames against all 18 captured official IDL layouts. Each of the
9,130 references consumed its full bytes under exactly one historical schema: the 2026-10-07 IDL
(commit `8cda1fa30ea658b20909d8aedf002047119388d2`, 40 BuyEvent fields and 33 SellEvent fields).
This is an IDL-consistent development observation, not a deployed-layout or event-validity claim;
no layout was selected from the c4 date. Failed-transaction references remain failed. Observed wire
lengths, including Anchor tag and event discriminator, are BuyEvent 497 bytes (1,169), 500 (178),
508 (35), 512 (2,576), 515 (426), and SellEvent 449 bytes (4,746). The parser is independently
checked against Anchor 0.27.0 / lock-resolved Borsh 0.9.3 serialization sources, with bounded string
length, strict UTF-8, canonical bool, fixed pubkey, signed integer and exact-consumption checks.
Development-only Hypothesis coverage exercises generated values and field offsets across distinct
historical schemas. The 468 other event references and unopened 23:00Z tail2 body were excluded; no
settlement, economic admission, completeness, or zero claim follows. Receipt:
[`c4 BuyEvent/SellEvent IDL-layout feasibility`](evidence/pumpswap-c4-event-layout-feasibility-validation-v1-2026-10-11.json).

An offline development replay then applied the already-pinned Pump SDK 2.1.0 pool-v2 prefix
classifier and existing pool-v2 candidate-subtree diagnostic to the exact 25-byte/valid-bool,
23-fixed-account/three-tail class in those same ten c4 frames. All ten reports were byte-hash-bound
to the c4 replay; each selected candidate set was re-normalized against all 160 captured full
blocks with exact identity-set equality and zero normalization errors. Of 3,556 buyExact references,
2,333 met the structural predicate; 2,317 matched the SDK-derived pool-v2/fee-wallet/ATA and
transaction-privilege checks, while 16 remain structural mismatches (13 failed attempts with a
transaction-wide writability conflict and three successful references with ATA, fee-wallet, PDA, or
privilege mismatches). The 2,317 matches comprise 767 failed attempt-only references and 1,550
successful transaction references. The existing economic diagnostic classified 1,546 successful
references as quarantined for Token-2022 effects and four legacy-token references as censored (two
missing token-account endpoints, two candidate-subtree/transaction-endpoint residuals); zero rows
passed that narrow diagnostic. This zero is a decoder-acceptance count, not an economic zero. The
1,223 other buyExact references remain outside this three-tail stratum. The 23:00Z frozen tail2 body
was excluded and not opened. Receipt:
[`buyExact c4 pool-v2 development replay`](evidence/pumpswap-buy-exact-v1-c4-pool-v2-development-replay-2026-10-11.json).

The offline evidence-feasibility taxonomy is recorded at
[`buyExact c4 evidence-feasibility taxonomy`](evidence/pumpswap-buy-exact-v1-c4-evidence-feasibility-2026-10-11.json).
It replays all 2,333 candidates against the same 160 full blocks, preserving the 780 failed attempts,
the 13 failed structural writability mismatches, and the three successful structural counterexamples.
Transaction-tree ancestry identifies 1,621 candidate-descendant exact BuyEvent wire records (1,553
successful transactions and 68 failed attempts); all decoded roles and four instruction-argument
fields agree, but those event claims still do not prove committed effects, especially for failed
attempts. The replay records ordered Token and Token-2022
transfer operands, account roles, mint/program matches, target-account touches outside the candidate
subtree, and provider transaction-boundary endpoints. It preserves the four legacy diagnostic
censorings as two missing user-quote endpoints and two residuals: candidate-subtree user-base deltas
of 4,392,114 and 160,993,911 atomic units versus transaction-boundary deltas of zero, alongside
user-quote subtree deltas of -529,450 and -2,089,402 versus transaction-boundary balance deltas of
9,235 and 18,226. No unexplained residual is assigned to SyncNative, rent, close, fees, or another process.
All 160 source captures are full-block `getBlock` responses; historical bank-time mint/account and
Token-2022 extension state are absent. The detailed row-level output is compressed under ignored
`work/` and bound by the receipt hashes. This remains development evidence only: no decoder
promotion, exact economic zero, global completeness, or RPC/audit authority.

The follow-up [`buyExact c4 economic-identifiability inventory`](evidence/pumpswap-buy-exact-v1-c4-economic-identifiability-validation-v1-2026-10-11.json)
joins those exact 2,333 taxonomy identities to the ten pinned full blocks. It inherits the
taxonomy's identity-set equality receipt, revalidates each row against its transaction signature,
instruction path, program and 23 fixed account roles, and records candidate-descendant BuyEvent
fields, ordered transfer operands, outside-subtree target touches, transaction fee/lamport metadata,
WSOL roles, and token endpoints/residuals. It does not repeat the full census scan. The partition is
1,550 successful structural passes, 3 successful mismatches, 767 failed structural-pass attempts,
and 13 failed mismatches: 16 mismatches overall, overlapping the 780 failed attempts. Four
dual-legacy success-pass rows remain censored (two missing endpoints and two subtree/boundary
residuals); the existing diagnostic separately quarantines successful Token-2022 cases.

The pinned official `buy_exact_quote_in` IDL lists 23 fixed accounts and documents that fees are
deducted from `spendable_quote_in`, that the payer may need rent for three account creations, and
that a cashback WSOL accumulator ATA may be supplied. The captured three-tail candidate class is
selected by the pinned SDK/census prefix, not the 23-account IDL list. Event and instruction u64
values remain raw atomic/wire observations; this schema does not declare decimal scale or prove
deployed behavior. Transaction fee, lamport, and token balances are whole-transaction boundaries,
not amounts settled at the candidate instruction position. Historical bank-time account/mint bytes,
Token-2022 extension/withheld-fee/hook state, and position-specific state are unavailable. Thus the
inventory sets no economic predicate, admission threshold, audit result, decoder promotion, or zero
claim. It excludes all 1,223 other buyExact references and the unopened 23:00Z tail2 body; no
backfill, RPC, or provider request occurred. Its row artifact is source-bound under ignored
`work/pumpswap-buy-exact-v1-c4-economic-identifiability-v1-final/`.

Any later audit decision requires a separately frozen contract, a fresh disjoint prospective
sample, source-bound completeness and independent transaction-boundary evidence. Missed intervals
remain gaps; the current evidence grants no RPC authority.

The follow-up [`buyExact position-state source-feasibility proof`](evidence/pumpswap-buy-exact-v1-position-state-source-feasibility-v1-2026-10-11.json)
uses only the accepted 2,333-row ledger plus byte-pinned official Solana RPC, Token-2022, and Agave
v3.0.0 Geyser sources; no c4 response body was reopened. This scoped proof does not assess the
separate Alchemy Account Archive path, which has now received its own offline feasibility screen;
no Alchemy chain request was made. The row ledger separates 780 exact failed-attempt transaction
dispositions from candidate-position state (not economic zeros), 1,550 successful structural
passes for which the captured documented sources do not provide candidate-position state, and
three successful structural mismatches that remain unknown. Of 2,333 candidates, 2,328 contain a
Token-2022 program role and five are dual
legacy. Standard RPC transaction responses expose ordered instruction facts and transaction-level
pre/post balances, while `getAccountInfo` and `simulateTransaction` do not document an original
instruction-position selector. Geyser v3 account updates carry account data, `write_version`, and
an optional transaction reference; the pinned interface does not provide an instruction/CPI index
or per-instruction checkpoint. Token-2022 fee and transfer-hook semantics require mint,
token-account, hook-program, and ordered invocation state that these boundaries do not supply. This
is a conservative insufficiency finding for the captured documented source set, not a claim that no
custom or future source can provide the witness. A custom prospective runtime checkpoint adapter
remains a candidate to investigate, not a qualified source. Historical replay would require a
specific pre-slot snapshot, complete ordered ledger/fork, era-correct runtime,
feature/program state, and independent boundary checks; current docs do not establish c4 replay
feasibility. A property-based integer-unit counterexample shows identical
`atomic_units_of_mint` transaction endpoints can hide different candidate-position balances. This
is source feasibility only: no
economic predicate, audit, decoder, admission, or collector change.

The separate [`Alchemy Account Archive feasibility screen`](evidence/pumpswap-buy-exact-v1-alchemy-account-archive-feasibility-v1-2026-10-11.json)
pins Alchemy's public account-archive, historical simulation, token-account, and CU docs and
classifies the same 2,333 accepted candidate identities without reopening c4 response bodies. Alchemy
documents finalized `getAccountInfo(slot=S)` as the latest write at or before S; its history cursors
are slot-scoped. `simulateTransactionsAtSlot` is a hypothetical shared-overlay simulation with a
five-transaction request limit, 128 referenced-account limit, 100 state overrides per entry, and
25 MiB rendered-result cap, using one pinned SVM release. Of the 2,333 candidates, 2,330 exceed a
single request containing the entire same-slot transaction prefix and candidate (transaction
index ≥5); the three within that capacity are failed attempts. This count does not establish that
every predecessor is causally necessary, nor does passing the screen prove historical replay. No
transaction-index or instruction-index selector is documented, and the archive slot snapshot is
finalized slot state. Alchemy's `firstUpdateAfterSlot` table says strictly after S while its prose
says at or after S; this feasibility screen does not rely on that cursor boundary. The taxonomy
gives a lower-bound 17,584 candidate
state-account×slot pairs across 7,617 addresses (18,544 pairs including program roles), including
2,328 Token-2022 and 2,246 WSOL rows. One `getAccountInfo` per state pair at its published 10 CU
reference is a nominal 175,840 CU sizing scenario only; it is not measured traffic, a paid request
plan, or a dollar estimate. Read-only Alchemy MCP usage showed 18,680 aggregate CU / $0.01 month to
date through 09:02Z, with no usage-limit field returned. Archive entitlement and account-specific
spend/overage limits remain unverified. A read-only MCP refresh at 2026-10-13 01:37Z still reported
18,680 CU / $0.01 month-to-date and returned no account limit. Helius reports a configured key, but
account usage and plan remain unavailable without a JWT session. No Alchemy chain RPC or archive read occurred; all rows keep
`economic_admission=false` and the frozen audit remains sealed.

Sol's current prospective-collection decision supersedes the earlier c5 activation horizon: c5's
offline policy and runner may remain available for review, but c4's abnormal STOP does not satisfy
the service-v1 normal-stop gate and neither c5 nor another successor may make live calls now. The
separately versioned durable controller is approved for offline implementation only. Any future
activation needs a new finite reviewed lease, source-bound account and disk/churn evidence, exact
future UTC schedule and caps, c4 terminal/quiescence proof, and independent reviewer binding. Missed
buckets are gaps; restart is anchor-only; ambiguous started requests are never replayed; renewal is
a new reviewed lease. No default live cadence or aggregate envelope is approved.

The offline state-machine contract is `tools/brain_senses_prospective_controller_v1.py`. It binds
one finite lease to declared source hashes, predecessor terminal/STOP hashes, a frontier, explicit inherited
gaps, future UTC buckets and lease/day request, raw-byte, stored-byte, physical-disk and slot-width
caps. Its durable event chain records one reservation per bucket before the request boundary, the
method and request digest, start-time/disk rechecks, immutable raw artifact hash, event/receipt/
availability times, result, seal, gap and first-reason STOP. Ambiguous or unsealed attempts remain
fully charged and STOP without replay. A missed bucket is explicit; only a later anchor can reset
the frontier before another frame. Normal `close_lease` requires every scheduled bucket to have
exactly one captured seal or an elapsed gap, no outstanding reservation, and no failed/unknown seal.
It can close before lease expiry once the finite schedule is fully accounted. A premature close
fails without writing STOP or TERMINAL. Explicit `cancel_lease` writes the abnormal
`owner_cancelled_before_schedule_exhaustion` STOP only when no request is outstanding; untouched
bucket IDs remain `cancelled_unstarted` in derived status, never gaps, and cannot seed a successor.
An outstanding reservation is neither cancelled nor reclassified. A failed/unknown result crash
between result, seal and STOP recovers as abnormal STOP and cannot reserve another request. STOP
times cannot predate the latest durable reservation, start, gap, seal, receipt, or availability
time; captured seals and terminal receipts cannot predate capture availability.
`initialize_successor` transfers its verified frontier/gaps and a new review-decision digest to a
distinct lease under a local shared writer lock; there is no auto-renewal. This remains an offline
protocol implementation: declared source and review hashes are not independently parsed or
authenticated here. It has no provider adapter, credential loader, LaunchAgent, activation evidence
parser, autonomous calendar scheduler, or live generation root. It does not prove provider charge
units, disk churn, capture completeness, or remote/multi-host locking. Those remain activation and
scale-out gates; future multi-worker coordination must preserve the immutable lease and evidence
contracts while replacing the local coordination backend.

The separate read-only verifier/CLI is `tools/brain_senses_prospective_verifier_v1.py`. It
independently parses canonical plan and JSONL bytes, validates the event hash chain and lease/day
accounting, checks each reservation/start/result/seal transition, reads allowlisted immutable capture
files through no-follow descriptors, binds receipt/availability/decision times, and verifies STOP,
terminal, frontier/gaps, and a supplied predecessor. It returns a recovery recommendation for every
valid durable prefix; it never repairs files, retries a request, creates STOP, or authorizes
activation. A supplied predecessor's own plan, event chain and terminal-to-ledger head are checked;
its own predecessor is not recursively checked. Declared source
hashes are shape-checked; they match source content only when compared with independently computed
digests supplied by the caller. A review digest is similarly an external binding, not reviewer
authentication. The local SHA-256 chain detects accidental or partial changes but is not a signature
against an actor able to rewrite the whole lease. Capture files must be owner-held, single-link,
read-only regular files under the lease root or an explicit allowlist. A normal terminal result only
establishes the controller-v1 close/STOP contract; `activation_authorized` is always false. It cannot
establish that a recorded disk measurement, provider charge, request outcome, or external approval is
truthful. Focused offline validation covered 18 verifier cases and the controller's 20
transition/boundary cases; receipt:
[`prospective controller independent verifier v1`](evidence/senses-prospective-verifier-v1-2026-10-11.json).
The v1 contract represents one request/capture per reserved bucket. It is not an integration gate
for the existing v3 inventory scheduler, whose frame is 34 child RPC requests (one anchor, one
inventory, 16 full-block bodies, and 16 signature checks). Do not present one frame manifest as the
v1 response artifact or accept a parent seal without child-journal verification. A separately
versioned operation-envelope/child-bundle contract and verifier are required before any prospective
runner can use that scheduler.

The 2026-10-11 recovery requalification checks a supplied predecessor's plan, event chain and
terminal-to-ledger head before reporting a normal terminal. Terminal closure requires the exact
scheduled bucket set to be covered by captured seals or elapsed gaps. Early owner cancellation
records unstarted buckets as censored, not gaps, rejects outstanding requests, and cannot produce a
normal terminal or successor. Crash prefixes after failed/unknown results recover to STOP without
dispatch or replay. The focused controller/verifier suite passes 52 cases, including rejection of
backdated STOP/cancel records and seals before capture availability. Receipt:
[`prospective controller recovery requalification`](evidence/senses-prospective-controller-v1-recovery-qualification-2026-10-11.json).

## Prospective Pump bonding-curve references (offline gate, 2026-10-10)

Sol selected standard Helius WSS `logsSubscribe` filtered by the single Pump bonding-curve
program ID at `finalized` as the next population-discovery horizon. The program ID is pinned to
Pump's official source at commit `91db6800e55bf341696564bd30a08ed4e3fc7491`; the exact
`docs/PUMP_PROGRAM_README.md` SHA-256 is
`3532f985fcab38480392759a6c2f01015b7178b9f2c7dc0db8c9f85ce9f72571`. Solana's official
`logsSubscribe` contract permits one address in `mentions`; Helius's public billing page currently
states standard Solana WSS at 2 credits per 0.1 MB uncompressed across plans, with monthly credits
consumed before prepaid credits and autoscaling. This public rate and ordering do not establish
account-specific balance, overage/autoscaling controls, rounding scope, or control/handshake charges.
References: [Pump source](https://github.com/pumpr-fun/pump-fun/blob/91db6800e55bf341696564bd30a08ed4e3fc7491/docs/PUMP_PROGRAM_README.md),
[Solana `logsSubscribe`](https://solana.com/docs/rpc/websocket/logssubscribe), and
[Helius WSS pricing and connection notes](https://www.helius.dev/blog/laserstream-websockets).

The offline contract is `src/memecoin_collector/wss_reference.py`. It preserves each complete raw
JSON-RPC message under an immutable sequence path, chains journal rows, verifies raw hashes and
re-derived notification fields on recovery, keeps repeated `(slot, signature)` deliveries rather
than deduplicating evidence, checks the acknowledged subscription ID, and seals interrupted
sessions as terminal gaps with no resume. Slot is the only event coordinate. Receipt is measured
when the full message arrives; availability is measured after the exact raw message is fsynced;
decision time remains null. Malformed messages and failed transactions stay in their denominators.
Before each receive, the future transport must reserve one maximum 4 MiB message, the remaining
aggregate caps, and the disk floor; an oversized frame must be rejected from its WebSocket length
header before payload read; a refused frame records its header and never reads its payload. No
network socket is opened, provider request made, or live evidence captured in this offline milestone.
The private endpoint loader in `src/memecoin_collector/wss_tls.py` reads only the fixed repository
`.env.local` file through the shared `src/memecoin_collector/private_config.py` reader, which uses a
no-follow, nonblocking descriptor, owner/mode/link/size checks, and a before/after file identity
check; its secret-bearing endpoint representation is redacted. No endpoint or API key is passed in
process arguments or included in request evidence. `src/memecoin_collector/wss_transport.py` parses bounded
RFC 6455 server frames and assembles uncompressed text messages; it validates canonical length
encodings and rejects masked server frames, reserved bits, binary messages, and unsupported control
shapes. Oversize declarations fail after header reads and before payload reads. Client frame encoding
uses fresh random masks by default, as required by [RFC 6455](https://www.rfc-editor.org/rfc/rfc6455.html);
tests inject a fixed mask only for deterministic offline verification. The injectable
`src/memecoin_collector/wss_client.py` now specifies strict HTTP/1.1 Upgrade validation, exact
`Sec-WebSocket-Accept` binding, one standard `logsSubscribe` request, raw ack/error capture, and
subscription-ID binding. It rejects redirects, extension/subprotocol negotiation, oversized headers,
and unverified TLS-adapter assertions. Fresh authorization, wall-time session, disk-floor, and
session-owner gates run before Upgrade I/O and again before `logsSubscribe` dispatch. The monotonic
session origin is recorded at session creation and cannot be supplied or refreshed by the client.
A monotonic four-hour deadline is passed to every injected stream read/write, including partial
header and frame reads. The byte-stream adapter must itself interrupt blocked I/O at that deadline.
Endpoint and request target are used only to construct the
in-memory Upgrade request; they are not written to the journal. Failed Upgrade evidence retains
only the status code, header names, byte count and digest, never response header values that might
reflect a credential-bearing request target. The client cannot connect to a provider. `wss_tls.py`
adds an offline, deadline-bound TLS adapter over a caller-supplied connected TCP socket. It uses
Python's verified default trust store and hostname checking, disables TLS key logging, handles
nonblocking readiness in both directions, returns sanitized phase errors and completed-byte counts,
and closes without TLS unwrap on every terminal path. `wss_socket.py` adds a bounded offline-capable
DNS/TCP adapter: it passes only the exact `mainnet.helius-rpc.com` hostname to a fixed-code resolver
child, scrubs the child environment, caps IPC output, kills and reaps on timeout, selects the first
usable OS-ordered public address, and makes one nonblocking TCP attempt without retry, address
family fallback, or same-window resume. DNS and connect receipt times, durations, selected family/
address, and sanitized outcomes are journaled; pre-ack failures seal an explicit unobserved session
gap. Free disk, authorization, and independent wall-clock/monotonic four-hour deadlines are checked
before and after each connection phase.
Campaign authorization binds hashes for the endpoint reader, socket adapter, and resolver script.
No provider request, live endpoint connection, service activation, or WSS LaunchAgent has occurred.

The frozen *future* pilot envelope is at most four hours per session, one subscription, two
sequential sessions at most, 128 MiB inbound message bytes, 64 MiB physically stored bytes, and a
50 GiB free-disk floor. Reconnection creates a new window and an explicit gap: no catch-up or HTTP
transaction retrieval. The arithmetic `2 * ceil(128 * 2^20 / 100,000) = 2,686` is only an
aggregate-rounding scenario for the documented rate; the source does not establish that rounding
scope or billable control/handshake overhead. The separate seven-day HTTP service's 5,719-call
envelope plus that scenario is not a charge ceiling. A credential-free source capture is recorded
in [`Helius public WSS billing source capture`](evidence/helius-public-wss-billing-source-capture-2026-10-11.json);
the Helius account knowledge tool returned `Transport closed`, so account balance, plan, overage,
autoscaling, and concurrent-use evidence remain unavailable. The first-entry v2 artifact is an
offline signed-assertion contract only: its rate/rounding/control model is not parsed from and
qualified against provider account artifacts, its live-gate flag is false, and its production claim
entry point fails closed without touching the owner registry. See
[Helius credit schedule](https://www.helius.dev/docs/billing/credits). `src/memecoin_collector/wss_campaign.py` contains the offline single-writer campaign implementation; it is not activated.
Its immutable credential-free authorization binds a c4 STOP hash, account-budget evidence hash,
source/filter, exact hashes for the campaign/reference/transport code, caps, and an expiry of at most
seven days. It enforces two sequential windows maximum,
aggregate raw/stored byte caps, disk floor, explicit reconnect gaps, and no-resume recovery. The
wrapper checks the shapes and hashes in its authorization object but does not validate external c4
or account receipts; a future activation gate must independently reconcile those artifacts. Its
authorization binds the injected client, TLS adapter, shared private-file reader, DNS/TCP adapter,
and resolver script source hashes as well as the campaign, reference, and WebSocket transport
modules. The offline client shares
raw/stored budgets with notifications, persists the
bounded pre-ack response before classifying it, and records protocol failures without endpoint
material. Unknown response header names and all header values are excluded from failure evidence.
Transport-failure `partial_input_bytes` is the number of application bytes known to have reached the
parser for that failed unit; it excludes outbound bytes, does not count as a notification, and is not
a complete wire-byte or billing denominator. Bytes buffered internally by TLS at cancellation may
not be observable to the parser.
Control-frame payloads remain independently limited by RFC 6455's 125-byte bound and do not consume
application-message capacity. Activation still requires c4's normal terminal gates, account-level entitlement and
headroom, a separately bounded overhead allowance or billing evidence, immutable session
authorization, an independently reviewed TLS adapter, and a one-writer LaunchAgent review. Stop
on cap, low disk, malformed transport state, or journal ambiguity.

Notification projections bound copied signatures to 128 Unicode characters and slots and
subscription IDs to unsigned 64-bit integers. Out-of-range facts stay in the immutable raw message
and contribute to the parse-failure denominator; they are not copied into journal fields. The ack
subscription ID uses the same unsigned 64-bit bound. This keeps projected journal growth within the
existing per-message reservation without tightening the raw capture cap or implying economic
validity.

An offline payload benchmark verified and replayed the captured Pair64 512 full blocks and c3's
six 16-block frames (96 blocks total): 608 blocks and 741,305 transactions produced 51,502
Pump-program mentions, 34,112 failed transactions, 335 explicit Helius `Log truncated` notifications,
and 69,592,769 bytes of compact `logsNotification`-shaped UTF-8 JSON. No mentioned row was
unsupported and none had null logs. The payload estimate uses existing block `meta.err` and
`meta.logMessages`; it excludes WebSocket framing, TLS, and provider billing overhead. It is a
selected-block capacity warning (about 52% of the 128 MiB raw cap), not a live bytes/time rate,
continuous-window projection, or completeness result. Sol kept the frozen pilot envelope unchanged;
only live delivered bytes and connected duration can calibrate a successor. Reproduction:
[`Pump WSS selected-block payload benchmark`](evidence/senses-pump-wss-reference-payload-benchmark-2026-10-10.json),
using `tools/brain_senses_wss_reference_benchmark.py`.

This source yields only provider notifications delivered inside recorded connection windows. It
does not establish complete Pump births, a pool/market census, economic execution, inactivity,
absence, lifecycle or death. Comparison with selected captured blocks can measure only overlap in
that selected scope. Focused offline validation: 126 tests, including 300 deterministic generated
deliveries with duplicates, crash-prefix recovery, raw mutation, subscription mismatch, transport
header preflight, fragmented text messages, ping/pong masking, strict Upgrade and raw ack/error
binding over fake deadline-aware streams, pre-I/O authorization/disk gates, fresh monotonic origin,
authorization expiry during a control read, wall-clock regression cleanup, malformed pre-ack
fail-closed recovery, JSON recursion and integer-digit limits with raw preservation and stable
failure accounting, signature/slot/subscription projection bounds, cumulative projection and
near-cap stop-before-read behavior, independent ping caps, aggregate limits, writer exclusion,
source-bound authorization, reconnect gaps and no-resume recovery, trusted/untrusted local TLS
certificates, hostname/key-log rejection, TLS deadlines, partial read/write and readiness failure
accounting, SSLWantRead/SSLWantWrite in both directions, private-config symlink/hardlink/FIFO/size
checks, cancellation cleanup, stale-session ownership, and raw-ack preservation across authorization
expiry, rejection of forged TLS verification flags without the internal handshake capability, and
sanitized private-config descriptor I/O errors and file-change detection. These establish only the
tested local adapter behavior. They do not prove public DNS/TCP,
provider authentication or acceptance, live provider deadlines, account billing, LaunchAgent
operation, or continuous throughput. The 2026-10-11 rerun passed all 126 focused tests; a
source-hash-bound [offline operational review receipt](evidence/senses-wss-offline-operational-review-2026-10-11.json)
records that no WSS process or LaunchAgent was present, free disk was below the floor, and live
activation remains unauthorized.

`tools/brain_senses_wss_operator_bridge_v1.py` is an inert process/operator harness for the
existing campaign journal. Its CLI exposes only an explicitly read-only status projection; run
execution exists only as a Python API with a caller-injected client factory, and no default live
client, credential loader, network adapter, LaunchAgent operation, or source-qualified claim writer
is present. Before factory construction it revalidates the immutable plan/source bindings,
authorization hash and nanosecond lease, wall clock, and current free-disk floor. A pre-existing
campaign is passed only to the existing recovery routine, which seals it terminally without
reconnect or replay, even when the external dispatch lease has expired or disk is below the
dispatch floor. Recovery receipts bind the root's actual authorization and separately record
whether the supplied external authorization matches. The bridge receipt records
`activation_authorized=false` and `provider_io_performed_by_bridge=false`; I/O by a caller-injected
client is `unknown_not_observed`, and status does not infer provider activity from local files.
These synthetic tests and this validation made no provider requests. A repeat invocation returns a
separate no-dispatch result referencing the immutable original receipt by hash; it does not reuse
the original receipt's historical factory flag as the current invocation result. One campaign
window is enforced for each bridge invocation even though the underlying campaign supports two.
Plan and authorization files are read through no-follow, owner-only, mode-0600 descriptors and
checked for identity stability around the read. A first-preflight rejection before campaign-root
creation has no campaign receipt; a post-root pre-factory denial terminalizes with
`pre_factory_preflight_denied`. Focused synthetic tests exercise fresh-process reopen, crash
prefixes, ambiguous partial-input accounting, source and lease changes, cap/disk/clock denials,
root-authorization mismatch, one-window enforcement, and owner-lock exclusion. This remains
offline harness validation only; no credential was accessed, no campaign restarted, and no
activation occurred. Live activation still requires a separate Sol review.

A host-only readiness snapshot at 2026-10-11 16:10Z found all 15 installed Senses LaunchAgents
disabled and unloaded, with no matching running collector process. The only eligible persistent
volume, `/System/Volumes/Data`, had 29,293,846,528 bytes free: 24,393,244,672 bytes below the
50-GiB floor and 32,540,782,272 bytes below the documented 61,834,628,800-byte c5 minimum. The
full-lease peak/churn composite is still unsubstantiated. Account plan, credits, overage, and WSS
charge class remain unknown (`Transport closed`); no provider/account call was made. Pair64/c3
inventory identified named derived-review files but authorized none as reclaimable; raw captures,
journals and manifests remain preserved. Any future collector needs a new source-bound label, root,
manifest and finite lease. Receipt:
[`Senses operational readiness inventory`](evidence/senses-operational-readiness-inventory-v1-2026-10-11.json).

The 06:30Z read-only refresh again found no Helius plan, quota, usage, or charge without a JWT
session. Alchemy reported team-wide 18,680 CU / $0.01 across MTD, 7-day, and 30-day totals through
06:30Z; the Oct 12 hourly series remained empty, with no account limit or workload attribution.
Research lease charge remains `unknown_unverified`. Signup was not invoked. Receipt:
[`provider account snapshot 06:30Z`](evidence/senses-provider-account-readonly-2026-10-12T0630Z.json).

A read-only refresh at 20:27Z again found Helius usage unavailable without a JWT. Alchemy remained at
18,680 team-wide CU / $0.01 across MTD, 7-day, and 30-day totals through 20:26Z; no account limit
or workload attribution was returned. This aggregate does not prove collector traffic or research
lease charges. Signup was not invoked and no chain RPC was made. Receipt:
[`provider account snapshot 20:27Z`](evidence/senses-provider-account-readonly-2026-10-12T2027Z.json).

The 2026-10-13 12:28Z read-only refresh still reported 18,680 team-wide CU / $0.01 across the
month-to-date, 7-day and 30-day windows. The hourly series for 05:20–12:26Z was empty; attribution,
account limit and project charges remain unavailable. Helius MCP confirmed a configured key but
could not return usage without a JWT session. Neither account surface was used for chain RPC.
Receipt: [`provider usage refresh 12:28Z`](evidence/alchemy-usage-refresh-mcp-2026-10-13T1228Z.json).

A fresh read-only storage feasibility check at 2026-10-11 16:54Z measured 26,090,602,496 bytes
available on the internal APFS container, 27,596,488,704 bytes below the 50-GiB floor. The
maximum identified rebuildable cache plus `dist/` allocation was 110,530,560 bytes and would still
leave the volume below both that floor and the documented c5 minimum; no cache removal or evidence
movement is authorized. The attached external Kingston volume is 239.8 GB ExFAT with 166,798,032,896
bytes free. A disposable write/fsync/rename/lock probe succeeded, but a file requested and chmoded
to 0600 was reported as 0700 under the `noowners` mount; ExFAT also provides no journaling guarantee
for metadata recovery after abrupt disconnect/power loss. It is therefore ineligible for the
current capture/journal contract. The scratch probe was removed. No data was migrated, no mount
settings changed, and restart/reconnect durability remains unverified. Prospective collection stays
disabled until an owner-enabled journaling filesystem with sufficient free capacity is provided and
qualified. Receipt: [`Senses storage recovery feasibility`](evidence/senses-storage-recovery-feasibility-v1-2026-10-11.json).

The durable research-data interface is a logical root identity, not a disk number, volume label,
or absolute host path. A host binding records a random root ID and the expected filesystem volume
UUID; each campaign binds that root ID and volume UUID plus a relative capture path, while receipts
retain content hashes and separately record the observed mount locator. Before reservation or write,
a versioned resolver must prove the expected owner-enabled APFS volume is mounted, writable, has
enforced owner/mode semantics, and has fresh free-space headroom. It must fail closed for an absent,
renamed, read-only, wrong-UUID, `noowners`, or mountpoint-directory fallback; recheck identity under
the writer lock and before dispatch. No implicit internal-SSD fallback is allowed. Capture-coupled
journals and raw payloads stay on one filesystem so the design does not claim cross-volume atomicity.

Migration is group-based and preserves the internal original until verification completes: inventory
closed roots and receipt pins, copy to a temporary external group, fsync, compare exact path/type/size
and hashes, publish a create-only group manifest and immutable old-to-new locator receipt, run
independent read-only verifiers, and only then release the old copy. Active roots are never moved.
The worktree `work/` remains a legacy location during migration; no writer may treat an unchecked
symlink as an external-storage binding. `memecoin_research_storage` implements the versioned
resolver, root marker, descriptor-anchored private configuration, writer lock and identity checks.
`tools/research_storage.py` separates volume qualification, create-only host configuration,
root creation and read-only checks. Campaign journals stay beneath the verified root.
`tools/research_storage_migrate.py` accepts only explicitly attested closed research `work/` groups;
it copies with bounded-memory hashing, preserves supported metadata, verifies source and destination
again, and publishes create-only manifests/locators. It never deletes an original. Protected names,
unsupported metadata or changed source evidence fail closed. Independent application replay is a
separate release gate; a migration hash match alone does not qualify legacy absolute-path readers.
Published v2 manifests retain strict xattr equality. The v3 contract permits only a destination-added
`com.apple.provenance` attribute absent from the corresponding source object, recorded separately
with its actual size and SHA-256 and verified unchanged. Every source xattr remains exact; other
extras, altered values or unstable attributes fail closed. This records an OS-added destination
property and does not alter blockchain provenance. Offline migration checks pinned identity on every
entry/chunk, performs full volume checks at least every five seconds during callbacks and at all
publication boundaries, and never weakens the live service's full dispatch check.

After exact verification and independent replay, source release uses a reversible internal rename
and old-path alias trial. An incompatible reader causes restoration of the original. Accepted
archives receive an owner-reversible APFS `uchg` flag plus a separate flag-aware hash seal before
original removal; this prevents accidental writes, not privileged or owner removal. The generic
migration verifier intentionally rejects flagged trees: use the recorded pre-seal verification and
final archive seal together. Aliases support only qualified read-only legacy readers; they are not
writer configuration. Experience/Snapshot readers reject symlink ancestors, so their frozen roots
remain internal until a separately qualified relocation-aware reader can preserve their pins.

The new `tools/brain_senses_external_service_v1.py` is an **inactive adapter prototype** for the
existing service-v1 runner. Its storage/config/source/plist bindings, per-call guard, and RPC
transport guard are tested, but frozen service internals still perform absolute-path recursive
writes. A physical detach between a guard and those calls could redirect a write to an underlying
mountpoint. Therefore `EXTERNAL_WRITER_DETACH_QUALIFIED = False` blocks initialization and scheduled
ticks before mutation or client loading; no CLI override exists. Synthetic tests may patch the gate
only to exercise downstream contracts. Before live activation, a separately versioned writer must
anchor every write/lock/rename to the verified directory descriptor and pass detach-at-write fault
injection (or an independently proved equivalent OS-enforced scheme). Do not just flip the flag.
Predecessor, terminal, billing, clock and finite-lease gates also remain required. Existing frozen
campaign sources and receipts are unchanged. This limitation does not affect the descriptor-anchored
migration tool or read-only storage monitor.

The separate `brain_senses_external_writer_v1` / `external_orchestrator_v1` /
`external_verify_v1` stack now implements descriptor-bound create, append, lock, atomic rename,
unlink and capture persistence. It does not enable the prototype or rewrite frozen campaign sources.
The held root descriptor, private no-follow descendants and same-volume identity prevent a detach
from redirecting mutations to the internal mountpoint underlay. Repeated guard checks additionally
stop dispatch and writes when identity, ownership or the free-space envelope is unavailable.

Final qualification uses ten forced `diskutil unmount force` injection points on a disposable APFS
image after successful guards and before mutation; every internal underlay trap remains byte/mode
unchanged. Reservation-before-detach recovery remounts the image and proves zero provider-loader
calls and no replay. The scratch-image harness normalizes only its OS-disabled ownership flag, so
this is detach-mechanism evidence, not ownership qualification. A separate real Kingston scratch
run enforces enabled ownership, pinned UUID/root, fsync, create-only collision, atomic rename,
readback and independent-process flock exclusion, then removes its scratch subtree. Receipts:
[`forced detach v5`](evidence/senses-storage-detach-qualification-2026-10-12-v5.json),
[`Kingston writer v4`](evidence/senses-kingston-writer-qualification-2026-10-12-v4.json), and
[`remount no-replay v5`](evidence/senses-external-recovery-qualification-2026-10-12-v5.json).
Earlier qualification revisions remain immutable source snapshots, not current activation pins.

Before the isolated candidate-position witness checkout/build, `tools/brain_senses_build_storage_guard.py`
adds a separate bounded storage supervisor. It pins the configured Kingston UUID/root, keeps source,
Cargo home, target and temp paths beneath that root, forces Cargo offline and Rust 1.86.0, requires
90 GiB free for the initial 32-GiB allocation envelope (then reserves the remaining allocation cap
plus the 8-GiB peak margin and 50-GiB floor), and terminates its process group when a bounded identity,
space or tree probe fails. The probe runs outside the supervisor's blocking filesystem checks; a
two-second deadline bounds how long a stalled check can leave the child running. Successful parent
exit also closes residual in-group children before returning. The APFS qualification forced detach
during both a real Cargo build script and five post-detach `mkdir`/create/write/rename/held-cwd
attempts; all explicit path writes failed with `ENOENT`, the underlay was absent, the remounted UUID
matched, and no delayed artifact or in-group process remained. The small fixture used an OS-disabled
ownership disposable image and test-only capacity thresholds; the actual Kingston binding, identity,
owners-enabled state and free space are separately checked before any source closure. Source symlinks
are allowed only when their normalized target remains inside the source checkout; if the target exists,
it must also stay on the pinned volume. This accepts an intentional dangling upstream docs link while
rejecting escapes. Cargo's Git checkout symlink allowance is opt-in and accepts only relative links
below one ordinary `<repository>/<revision>` tree whose normalized targets stay within that exact
revision. Cross-revision, cross-repository, absolute, and symlinked structural paths are rejected;
Cargo config, registry, target, and temp symlinks remain rejected. The corrected guard passed 16
focused tests and a fresh forced-detach APFS qualification; all five post-detach writes failed with
`ENOENT`, the manifest matched, and no process survived. The current source and policy are pinned in
[`build storage guard APFS qualification v4`](evidence/brain-senses-build-storage-guard-apfs-qualification-2026-10-12-v4.json); earlier receipts remain bound to earlier guard revisions. This supervisor
is not an arbitrary build-script sandbox: it does not constrain a descendant that deliberately leaves
the process group, and sampled allocation checks are not a kernel hard limit. Review the source/build
scripts and process census; do not use `setsid` descendants. Receipt:
[`build storage guard APFS qualification`](evidence/brain-senses-build-storage-guard-apfs-qualification-2026-10-12.json).

A fresh Kingston checkout composes the receipt-bound candidate-position observer v5 and corrected
Token-2022/WSOL parser v3 patches on Agave `v3.0.0`, with a 20-path hash map, pinned Crossbeam
revision, and 1,075 locked packages. Check-only passed with empty Cargo/target/tmp roots and no network;
the exact source closure is recorded in
[`Agave Token-2022 runtime source closure`](evidence/agave-token2022-runtime-source-closure-2026-10-12.json).
This is not dependency-fetch, runtime-test, Bank finality, candidate completeness, settlement, or
economic evidence. Sol approved one guarded locked fetch only after the fixed 06:00–07:03Z lease is
terminal, independently verified, and unloaded; any failure is terminal with no retry. Metadata may
follow only offline after a successful fetch, while a build needs a separate review.

The scheduled 06:20Z callback for that fixed lease exited 1 before any durable reservation or intent;
its exception could not be recovered because launchd stdout/stderr target `/dev/null` and the queried
Unified Log had no Python detail. Sol directed immediate fail-closed disable/unload. Before and after
snapshots preserve the same 103 requests, four reservations/intents/seals, event tail, and frontier;
the callback changed no journal. At 06:28Z, the four sealed windows accounted for 144,392,483 measured
raw/stored bytes against the 3,104,000,000-byte lease cap; provider charge remains unverified. Status
classified 06:20Z as `expired_unobserved` after its window ended; by 06:28Z the cancelled-unstarted
06:25Z window had expired the same way; at 06:33Z the 06:30Z window was also expired/unobserved.
The journal remains a verified prefix, not a completed lease:
no 06:20 replay,
no later scheduled callbacks, no fabricated STOP/provider error/zero, and no successor eligibility.
At 06:33Z Kingston still matched its pinned UUID/root, health was `ready`, and 220,613,636,096 bytes
were free; the service subtree accounted for 158,588,928 allocated bytes across 435 files. This is
about 176 MB of volume-free-space churn since the handoff snapshot, comfortably above the 50-GiB
floor, and does not alter the early-unload decision.
Receipt: [`06:00Z lease callback failure and unload`](evidence/brain-senses-external-v2-20261012T0600Z-callback-failure-unload.json).
After the 07:03Z expiry, a fresh status verification retained the same four seals and 103 requests;
the remaining nine scheduled windows are `expired_unobserved`, with no unresolved reservations,
STOP, replay authority, or successor eligibility. The LaunchAgent remains disabled and unloaded.
The final status snapshot is in
[`06:00Z final expiry status`](evidence/brain-senses-external-v2-20261012T0600Z-final-expiry-status.json).

Sol approved a separate fixed-service partial-prefix compiler and publisher; the complete v1
structural-input and queue compilers remain unchanged. It verifies the entire 13-window service
prefix and closed source file set before reopening only the 48 selected full-block captures in the
three sealed frames. The Kingston sibling artifact retains bootstrap plus all 12 planned frame rows;
the failed 06:20Z callback is explicit and counts/probabilities for the other eight unobserved
windows are null. It records 46,222 scanned transactions, 4,865 structural references and 4,595
unresolved fingerprint classes with separate denominators. This is a sampled structural prefix,
  not full-schedule coverage, a no-new-class result, point-in-time admission, market completeness,
  economic validity, or successor eligibility. Output and its post-fsync publication receipt are
under `derived/partial-structural-queue-v2/external-v2-20261012T0600Z/`; artifact SHA-256 is
`fd8a2ffb750d53eb877e1326c02419358c24870aeb7338ea444a121712f83b81`. Publication is create-only,
descriptor-bound and replayed deterministically; duplicate publication returned the same hashes.
  Tool, focused tests, output location and artifact digest are indexed in the
  [`evidence locator`](evidence/README.md); terminal state is recorded in
  [`final expiry status`](evidence/brain-senses-external-v2-20261012T0600Z-final-expiry-status.json).

The unrecoverable callback motivated an inactive, separately versioned launch-v3 callback prototype
and launch-failure receipt writer. Its fixed-schema payload allowlists identifiers, times, phase,
error class/reason, intent certainty and a verified journal-tail digest; it never serializes
exception text or performs provider I/O. The synthetic callback dispatches once, then unwinds its
host→storage→predecessor→service lock stack before independently rereading twice under that same
lock order and reacquiring host→storage for a best-effort descriptor-bound, create-only sibling
receipt. `verified_none` requires matching plan/source/predecessor pins, event tail, and unchanged
empty reservation and intent sets; the plan bucket must match its UTC window and callback-start time.
Uncertain reads remain `unknown`, while spent IDs remain `possibly_spent`. The receipt sink has
focused tests for secret exclusion, idempotency/conflict, unchanged service manifest, forced
descriptor detachment, and no fallback; the v3 orchestration tests cover exactly-once injected
dispatch, proof ordering, lock contention, and preserving the original exception when the sink
fails. A separate journal snapshot adapter now delegates event, reservation, request-intent,
file-closure, and predecessor checks to the independent v2 verifier, then binds the verified plan,
source map, predecessor certificate and exact scheduled window to each fresh descriptor-bound
snapshot. Synthetic crash-prefix, torn-journal, source/window mismatch, symlink and between-read
mutation tests pass; one fixture exercises the full v2 plan/event/file verifier with predecessor
verification isolated. A disposable case-sensitive APFS qualification then forced detach immediately
before both failure-receipt `mkdir` and `create`: both writes failed closed, the receipt was absent
after remount, and the seeded internal underlay remained byte- and mode-identical with no fallback
artifacts. The fixture used two OS-noowners images and test-only capacity thresholds; it does not
qualify Kingston ownership or capacity. Receipt:
[`launch-v3 failure-receipt detach qualification`](evidence/brain-senses-launch-v3-failure-receipt-detach-qualification-v1-2026-10-12.json).
An inert manual-only plist and synthetic LaunchAgent rehearsal then completed one no-provider
kickstart on a disposable case-sensitive APFS volume. The runner independently verified its
descriptor-bound journal and `verified_none` receipt; a process restart did not dispatch again.
Teardown disabled and booted out the nonce, cleared its disabled override, and verified that the
label and process were absent; the plist and image were removed and the underlay stayed unchanged.
This qualifies only the launchd invocation and failure/restart path: capacity was not qualified,
no Kingston or v2 service was activated, and the frozen v2 launcher remains unchanged. Receipt:
[`launch-v3 disposable LaunchAgent rehearsal`](evidence/brain-senses-launch-v3-disposable-launchd-rehearsal-v1-2026-10-12.json).

The read-only source map then follows the v5 observer identity through Agave's adjacent commit,
block-index, freeze, and root sites. It finds that execution IDs and commit-result vector order do not
yet persist a unique Bank/fork, absolute block-index, frozen-hash, and rooted-block join; a slot-only
join admits a same-slot fork counterexample. Failed execution traces remain attempts, while
FeesOnly carries rollback-account handling without the observer trace. The hash-pinned map verifier
checks 12 files and 38 unique anchors in the fresh checkout; it performs no Cargo operation. This is
source feasibility only, not a persistence/finality or economic witness. Receipt:
[`Agave persistence/finality source map`](evidence/agave-v3-transaction-persistence-finality-source-map-2026-10-12.json).
The separately versioned synthetic commit-to-root verifier now checks unique attempt/commit,
batch-local and absolute block positions, frozen-bank lineage, selected-root ancestry, per-subject
bounded availability receipts, and typed failed/FeesOnly rollback dispositions. Permuting input
rows leaves its verdict unchanged; duplicate or fork-mismatched joins fail closed. The fixture is
synthetic consistency only: source/runtime, candidate-position, settlement, buyExact25 and PIT
qualification remain false. Its reproducible inputs, verdict and source pins are recorded in the
[`synthetic commit-to-root join receipt`](evidence/agave-commit-to-root-synthetic-join-v1-2026-10-12.json).
Real persistence/finality instrumentation and any build remain a separate Sol-reviewed horizon.

The Agave source-closure review then allowed exactly one locked fetch in a fresh Kingston stage after
the fixed Senses lease expired. The wrapper reported `cargo_fetch_created_build_output` after Cargo
created `target/.rustc_info.json`; the source diff and lockfile still match their pinned hashes. A
read-only follow-up verified the exact source/lock hashes, pinned Crossbeam checkout, 822 registry
archives, the preserved 3,022-byte Rust compiler-probe marker, empty tmp, and no Cargo process. The
guard's control flow shows Cargo exited zero before its final target-directory rejection. This points
to a guard false positive on normal compiler probing, rather than a failed dependency fetch; that is
an inference, because the wrapper discarded Cargo stdout/stderr. No Cargo retry, offline metadata,
build, or ProgramTest followed. The captured package inventory is not a Cargo resolver result. A
hash-bound read-only receipt records the findings and links the forced-detach and synthetic join
receipts: [`Agave read-only cache preflight`](evidence/agave-read-only-cache-preflight-v1-2026-10-12.json).
Any new Cargo operation still requires fresh Sol review and a separately versioned guard with
diagnostic capture and explicit compiler-probe handling. The original immutable receipts remain:
[`source closure`](evidence/agave-token2022-runtime-source-closure-2026-10-12.json) and
[`terminal fetch wrapper result`](evidence/agave-token2022-runtime-fetch-attempt-terminal-2026-10-12.json).
Earlier attempts remain separately recorded:
[`fetch attempt`](evidence/agave-dependency-fetch-attempt-2026-10-12.json),
[`source-state correction`](evidence/agave-dependency-fetch-attempt-source-correction-2026-10-12.json),
[`dependency fetch preflight v2`](evidence/agave-dependency-fetch-preflight-2026-10-12-v2.json).
The earlier v2 terminal outcome is recorded at
[`dependency fetch attempt v2`](evidence/agave-dependency-fetch-attempt-v2-2026-10-12.json).

Sol subsequently authorized exactly one separately versioned offline metadata operation in a fresh
descriptor-bound Kingston stage. On 2026-10-12 Cargo exited zero after one locked, offline,
default-feature resolution filtered to `aarch64-apple-darwin`; no fetch, build, tests, or
ProgramTest ran. The local graph normalizer then rejected distinct workspace package IDs because
path redaction collapsed them to one value. The process kept Cargo stdout only in memory, so no
stdout digest or resolved-graph receipt survived. Read-only stage inspection verified the source
cache was unchanged, the copied cache remained on Kingston, `target/` contained only the 3,022-byte
successful Rust 1.86.0 target-probe marker, and `tmp/` was empty. The marker validates with the
corrected observed schema; the ID normalizer fix is tested, but Cargo must not be rerun under this
one-operation authorization. This establishes only that offline metadata returned successfully,
not a persisted resolver graph or runtime/economic qualification. The immutable failure receipt is
on Kingston at
`qualifications/agave-v3.0.0-token2022-metadata-v1-20261012/metadata-preflight-failure-v1.json`
(SHA-256 `060a604d86edc2a229e7ca24b83967ada72ba44e345c59a74235fe61ad70e0d8`).

The first release is deliberately a finite operational qualification: one prospective finalized
anchor and one inventory-first frame five to ten minutes later. It reserves at most 35 requests,
288,000,000 raw/stored payload bytes, 32,000,000 bytes per response and 20,000 slots per frame;
16 deterministic inventory members receive signature/full-block pairing. The 50-GiB floor plus
all 288 MB of lease bytes and a 290-MB peak margin is checked before mutation and dispatch. These
are local envelopes, not provider charge or market-completeness estimates. Transport is limited to
four requests/second, rejects redirects, and stops on quota/HTTP/provider/transport errors. Ambiguous
or spent requests never replay; missed buckets never backfill. Bootstrap explicitly records the
uncovered interval from c4's terminal frontier to the fresh anchor.

The independent reader recomputes request wire hashes, response hashes, receipts, selected slots,
block structure/signature agreement, event ordering, availability and byte/request denominators.
It accepts valid incomplete prefixes without granting activation or replay authority. Response event
time remains separate from receipt and post-fsync availability. Per-request `decision_ns` records
the acquisition dispatch decision before its own response; bundle/seal publication follows
availability. It is not a downstream economic decision timestamp. Recovery may publish missing local terminal evidence, never repeat provider I/O.
The launch entry point binds reviewed plan/source/qualification/plist/config hashes, all c4 terminal
pins, disabled/unloaded legacy agents, its launchd PID and exact argv. Credentials load in-process
only after gates; plists contain paths only and logs point to `/dev/null`.

Each bucket obtains fresh verified-TLS HEAD Date corroboration from Cloudflare and Google, with
bounded RTT, age, date agreement and wall/monotonic drift checks. This supports coarse prospective
windows with a five-second dispatch margin; it does not establish nanosecond clock accuracy or NTP
synchronization. Source changes invalidate the lease. Activation requires a concrete Sol review and
future calendar schedule; passing a synthetic test or hash verifier alone does not authorize RPC.

The reviewed finite lease `external-v1-20261011T2133Z` then ran through launchd on Oct 11 UTC:
bootstrap 21:33, frame 21:38, expiry 21:41. Both buckets sealed; all 35 RPC intents have captured
results (2 getSlot, 1 getBlocks, 32 getBlock), with 16 signature/full pairs from 1,373 inventoried
slots. Raw/stored payload totals are 64,223,372 bytes; all service files total 68,872,141 logical
bytes. The independent frontier is 455802008. The three inherited gaps plus the fresh uncovered
`[455430160,455800635)` interval remain explicit; this does not establish market-wide completeness.
Each of the 35 dispatch decisions was also checked against prior durable availability, including
the bootstrap seal before the frame's first decision. This strengthens the actual chronology check
beyond v1's request-start ordering predicate, without claiming precise external clock truth.

Bootstrap/frame exited 0. One separately approved terminal launchd restart exited 0 and left all
153 files, hashes and request count unchanged (35 before/after). After expiry the finite label was
disabled/unloaded and its exact plist archived. c4's terminal metadata stayed unchanged and the
300-second research storage-health monitor remained ready. This is a clean finite operational
qualification, not an automatically renewed acquisition campaign. Actual provider charges remain
unknown. Receipt: [`finite external lease completion`](evidence/external-v1-20261011T2133Z-completion.json),
with its hash-bound checkpoints and [`dispatch chronology`](evidence/external-v1-20261011T2133Z-dispatch-causal-order.json).

Repository-wide validation is not green: the full module-run reports 2,612 passed, 25 skipped and
13 failed; frozen log-evidence replay, symlink-rejecting readers and a suite-wide low-FD condition
remain explicit. Focused external/migration runs have no observed FD leak. The bare pytest launcher
omits the repository root; use `python -m pytest` or `PYTHONPATH=.`. Do not alter frozen campaign
sources or receipts to conceal these failures. See the
[`full-suite diagnosis`](evidence/senses-external-full-suite-validation-2026-10-12.json).
Its `recorded_date_utc` label uses the Oct 12 Moscow date; actual observation was Oct 11 UTC. It is
not an execution timestamp, and no full-run log was persisted.

The additive `tools/brain_senses_external_successor_v2.py` is an **offline, nonauthorizing**
compiler, not a v2 runtime. It accepts a held reader and caller-pinned plan/root identity, derives
predecessor facts from independently verified v1 bytes, and requires both sealed buckets, no STOP
or unresolved prefix, stable metadata around verification and a final frame-seal event. It binds
both bundle/seal digests, the chain tip separately from the entire events-file digest, source pins,
all gaps, frontier, exact request/byte totals and actual terminal seal time. Every dispatch decision
must follow prior durable availability; the frame must additionally follow the bootstrap seal.
The new future minute-aligned windows must follow the maximum of expiry, seal and final event time
with a one-minute margin and use a distinct service/bucket identity. Missing/ambiguous evidence
requires separate failure review; it never becomes clean continuity.

Real-root callers must establish quiescence and hold host/storage/service locks. The compiler
compares metadata snapshots before/after independent verification and never writes, loads a
credential, calls a provider or emits authority. Caller time/root pins are not live attestations.
The real expired lease produced a candidate under those locks with the full 153-file manifest
unchanged; all activation/dispatch/replay flags remain false. The candidate's sample future windows
are a qualification artifact, must be regenerated for any later lease, and have no installed runtime.
A separate v2 plan/runtime/verifier and fresh Sol-reviewed operational evidence remain required.
Receipt: [`actual-root successor-v2 qualification`](evidence/external-v1-20261011T2133Z-successor-v2-validation.json).
The compiler/verifier focused suite passes 34 tests; all seven external suites pass 91 with FD count
4→4. New-file Ruff/format and targeted mypy pass. No v1 source hash was changed.

All 15 previously installed Senses plists were disabled/unloaded, hash-verified and archived under
`~/.config/brain-research/retired-launchagents-20261011/`; their installed files were removed so they
cannot restart as parallel login collectors. Historical direct scripts are unsupported forensic
entry points. The only new enabled research LaunchAgent is
`ai.memecoin.brain.research-storage-health-v1`, running every 300 seconds and at load. It writes only
bounded private internal `~/.config/brain-research/storage-health/status.json`; no RPC or acquisition
occurs. Launchd stdout/stderr use `/dev/null` because external log paths would be opened before any
storage guard. Unmount/remount and launchd reload qualification proved `ready → storage_unavailable
→ ready` with no fallback corpus creation. Physical cable removal and login remain untested.

On 2026-10-11 the dedicated 240.1 GB external USB disk was reformatted under explicit user
authorization as GUID + case-sensitive APFS, volume `Kingston`, UUID
`8F8F1081-9BE4-4914-9111-75F1B90255BF`. The user then enabled filesystem ownership and assigned
the mount root to `root1:staff`. The configured root is `/Volumes/Kingston/BrainData` (0700), logical
ID `9428695af6d4f46c08e1a444cdc66de8`; private host binding is
`~/.config/brain-research/storage-v1.json` (0600), with a 50 GiB minimum free-space floor.
Code, Git, environments, documentation and small operational state remain internal. Future large
roots must use this explicit binding and relative paths; the volume label is only a host locator.
Physical disconnect/reconnect and login persistence are distinct from safe unmount/remount tests.
The 2026-10-12 no-provider capacity/recovery qualification wrote and read back exactly
3,104,000,000 deterministic payload bytes as 97 fsynced 32,000,000-byte files under a unique
nonce subtree. The descriptor-bound writer recorded 3,105,206,272 allocated bytes at high-water,
including files and directories, below the 3,394,000,000-byte payload-plus-peak cap. Across 531
prewrite guard samples, minimum free space was 216,318,795,776 bytes; after cleanup it was
219,425,054,720 bytes. The 50-GiB floor and 290,000,000-byte peak margin were enforced. The
synthetic child exited after writing a 1,000,000-byte prefix against a 4,000,000-byte intent;
read-only recovery classified it `possibly_spent_no_replay` with zero recovery writes or dispatches.
All 97 files and 15 directories were removed through the held root descriptor, nonce absence was
rechecked through a fresh guard, and the service-root underlay listing was unchanged. Provider,
credential, launchd and lease interaction were absent. This qualifies configured Kingston capacity,
descriptor writes and synthetic no-replay recovery only; it does not qualify a live Senses journal,
activation, schedule or provider acquisition. The immutable source-bound receipt is
[`Kingston capacity and recovery qualification`](evidence/kingston-capacity-recovery-qualification-v1-2026-10-12.json)
(SHA-256 `55d5e55657c5cb0ca2aaf6216c1c5c8a5c6c2cd8afc02811419bf67a8ad32ca3`).

See `memory/now.md` for current migration, runtime and capacity state.
The completed host handoff is recorded in
[`external storage qualification and migration`](evidence/senses-external-storage-handoff-2026-10-11.json):
three closed capture groups totaling 18,285,310,608 logical bytes were released from internal storage
only after replay and final seals. At that snapshot internal/external free space was 44.61/205.86 GiB.
With no market collector active, current acquisition growth is zero; the prior conservative 18-GiB/day
scenario leaves 8.66 days above the configured reserve. This is temporary capacity, not a new budget
approval or a replicated corpus. Qualification datasets with symlink-rejecting frozen readers and
dirty Agave source/build state remain explicit internal exceptions.

## Offline v3 block child envelope (2026-10-11)

The separately versioned operation-envelope v2 binds a scheduled parent operation to the existing
finalized inventory-first child journal v3. Bootstrap captures one finalized anchor; a frame binds
its start to the next unobserved slot and its end to the later anchor. Gaps require a later
anchor-only reset. Each frame reserves exactly 34 child attempts: anchor, inventory, 16 full blocks,
and 16 signature checks. The verifier binds plan, provider identity (`helius-free`), request/result
records, capture `source_alias`, receipt and stored/raw bytes, manifest paths, parent events and
recomputed totals. A configured zstd executable is pinned by resolved path, version and SHA-256.

Receipt time is taken from the response capture; availability is sampled only after response,
receipt and containing directories have been fsynced. Request, persistence and clock failures retain
unknown/null payload availability rather than inventing a time. A started ambiguous child is never
replayed. The independent v2 verifier recomputes selected-slot block identity and ordered signatures,
compiled transaction/instruction structure and Pump-program reference counts from captured response
bytes; it compares every structural-check and per-slot outcome row and frame status, then binds child
STOP, summary and byte totals. Partial recovery validates exact request/result/capture prefixes,
selection derivation, source alias, receipt/availability ordering and orphan artifacts before
returning no-replay status. Capture availability must precede child STOP/summary sealing and parent
seal. A later parent STOP must follow every verified completed child STOP and latest capture
availability, including an unsealed bundle or a completed child without a parent bundle; when a
parent seal exists, STOP must also follow its time. A durable child STOP without its summary is
accepted only as incomplete no-replay evidence; summary-only is invalid. Parent event sequence proves
bundle-before-seal journal order, but v2 has no wall-clock bundle persistence timestamp, so no
bundle-time attestation is claimed. These are observed clock coordinates and local journal order,
not attested time or measured bundle durability. A future decision-time proof needs a separate
post-fsync availability contract. Every captured response marked parsed is independently checked
for JSON-RPC 2.0, exact integer ID 1, a present result and no error member, including started-prefix
recovery. Captured anchor/inventory values are rebound to their payloads. Prefix result files must
be contiguous, and written frame/selection artifacts are recomputed immediately, including sparse
inventory selections and empty/oversized frames.

This verifier qualifies local structure only. It does not prove provider inventory truth, market
completeness, or economic zero. Local hashes and host timestamps do not attest provider, account,
reviewer, or historical disk truth. Updating this transitive source invalidates prior source-bound
calendar, initial-entry, activation-preflight and supervisor eligibility receipts until requalified.

This is an offline fixture gate. The child requires an explicitly supplied client and no longer
constructs a provider RPC client or loads credentials. Code that explicitly supplies a real client
could still invoke the injected interface; no such adapter, scheduler, service or authorization is
included. Any live adapter and RPC requires a separate Sol activation review plus fresh account,
charge, disk, predecessor and schedule evidence. The focused offline suite passes 84
operation-envelope cases (110 combined with child-v2 compatibility tests), including independent
structural recomputation over a 34-attempt fake frame, rebound JSON-RPC envelope and semantic
mutations, capture-to-result-journal bindings for slot/inventory/block metadata and event slots,
STOP/summary and parent/child time checks, unbundled/unsealed/STOP-only crash prefixes, immediate
frame/selection verification, sparse selections, orphan/corrupt artifacts, zstd capture, and
provider/source mismatches. The bundle must retain `provider_charge_units=null` and
`provider_charge_status=unknown_unverified`; no cost is inferred. Sol accepted this bounded offline
qualification at the verifier source hash recorded in the independent receipt. Receipts:
[`offline operation-envelope/child-v3 validation`](evidence/senses-operation-envelope-v2-child-v3-offline-validation-2026-10-11.json)
and [`independent child-v3 verifier qualification`](evidence/senses-operation-envelope-v2-child-v3-independent-qualification-2026-10-11.json).

### Dependent gate requalification after verifier update (2026-10-11)

After the operation-envelope verifier changed, its finite-calendar, initial-entry candidate,
supervisor and activation-preflight dependents were re-run in dependency order against disposable
synthetic fixtures. The current source pins were recomputed by the suites; the candidate anchor then
completed a synthetic 34-request frame with independent bundle verification, and the calendar
covered gap-to-future-anchor recovery and transfer of gaps/unknown intervals. Structural mutations,
crash/STOP no-replay behavior, stale-pin rejection and synthetic signed activation-preflight inputs
were exercised. The aggregate result is 239 passing tests: 127 finite-calendar/operation/child,
57 initial-entry certificate/candidate, 19 supervisor, and 36 activation-preflight. The existing
initial-entry crash fixture was corrected to use the scheduled parent bucket identity required by
the independent verifier. Historical gate receipts remain immutable; this receipt binds their
digests and the current source/test hashes. This is offline requalification only: no provider or
runtime use, and `activation_authorized=false`. It does not establish current account/disk
headroom, reviewer authentication, or durable c4 observation provenance. Receipt:
[`dependent-gates offline requalification`](evidence/senses-dependent-gates-requalification-2026-10-11.json).

## Finite-calendar prospective envelope v3 (2026-10-11)

An offline lifecycle driver wraps the parent operation envelope with a caller-supplied finite
schedule. It independently verifies the parent prefix before each decision, writes elapsed buckets
as explicit gaps, and emits an independently verified terminal handoff on expiry, STOP, or complete
schedule exhaustion. The handoff transfers the next-planned-start frontier, all gaps and unknown
intervals, and conservative reserved-versus-actual request/byte accounting. The frontier is not
proof that all earlier slots were observed. Started child operations are never replayed; unknown
interval lineage is preserved through successor handoffs. A regressed anchor cannot seal or advance
the frontier and instead stops with the prior frontier intact.

The driver accepts no cadence, provider factory, credential path, scheduler, LaunchAgent, or live
activation. Successor creation checks a distinct lease, terminal transfer, not-before time and a
fresh review digest; hashes/digests do not authenticate the reviewer, independently prove the
predecessor chain from a successor directory, or prevent multiple successor claims. Those are
activation-gate obligations. The focused operation-envelope, child-v3 and calendar suite passes 82
cases, including a 34-attempt fake frame over UTC day rollover, gap-to-anchor recovery, no-replay
crash prefixes, stale time, disk floor, writer exclusion, transitive unknown lineage and anchor
frontier regression. No provider request or live activation occurred. Receipt:
[`finite-calendar-v3 offline validation`](evidence/senses-finite-calendar-v3-offline-validation-2026-10-11.json).

A follow-up closes the missed-anchor transition: while reset is required, an active frame waits
until its bucket expires, then that bucket is recorded as a gap; a later anchor can proceed without
an unnecessary STOP or request. The focused calendar/bridge/child suite now passes 83 cases.
Receipt: [`finite-calendar gap/anchor follow-up`](evidence/senses-finite-calendar-v3-gap-anchor-followup-2026-10-11.json).

The offline one-shot supervisor v1 wraps an already initialized finite-calendar lease. Its immutable
owner-only binding pins the lease root identity, exact plan digest, and source hashes for the
supervisor, calendar driver/verifiers, operation envelope/verifier, and child-v3 runner. Before
each decision it independently verifies the parent prefix and any terminal handoff, then uses the
calendar's exclusive owner lock to serialize the single decision. The existing finite calendar
accounts elapsed buckets as gaps and may dispatch at most one due operation; ambiguous prefixes,
STOP, regressed UTC, disk evidence faults, and a failed floor cannot dispatch or replay. UTC wall
time is sampled after lock acquisition and again after the decision; stale injected times are
rejected and any post-decision regression is reported as `clock_fault`, even if it remains later
than the last ledger event. A no-event transient regression is not durably journaled; a later
invocation resamples under the lock, while started-operation failures use the existing STOP and
no-replay rules. Read-only status reports the frontier, gaps, unknown intervals, caps, next bucket and terminal reason; its
snapshot verifier detects changes to the bound plan, code, or event prefix. The command line only
emits status. Programmatic operation execution has no default client and accepts only explicitly
injected runner/client objects; qualification used synthetic clients. No client construction,
credential lookup, scheduler, LaunchAgent or activation path is included. `activation_authorized`
remains false.

This supervisor cannot establish an initial lease or a discontinuous successor. The first live
entry remains a separately versioned and reviewed gate: c4's abnormal STOP is not a normal
predecessor, and its verified terminal/gaps/unknowns must be bound before a fresh future anchor can
be considered. Do not claim continuity, backfill missed intervals, reopen c4, or inspect its frozen
tail2. Local hashes do not authenticate plan/reviewer identity, and an external caller that injects
a live client is outside this offline qualification. The 19 supervisor tests and 76 combined
supervisor/calendar/operation-envelope tests pass using synthetic captures only; no RPC or live
activation occurred. Receipt:
[`offline one-shot supervisor v1 validation`](evidence/senses-calendar-supervisor-v1-offline-validation-2026-10-11.json).

A subprocess follow-up adds a synthetic process-boundary check without changing supervisor
runtime code. One process captures an anchor, a fresh process independently verifies the durable
seal, a third writes the `after_start_before_dispatch` prefix and exits abruptly, and a fourth
reopens the lease, verifies the `abnormal_unsealed_prefix` terminal handoff and preserved unknown
interval, and confirms that the injected client receives no replay call. The full supervisor suite
passes 20 tests. This does not simulate interruption during fsync or a real provider dispatch, and
does not qualify a LaunchAgent or unattended live scheduler. Receipt:
[`supervisor process-restart recovery validation`](evidence/senses-calendar-supervisor-v1-process-restart-validation-2026-10-11.json).

The offline activation-preflight v1 verifier now binds each predecessor handoff's terminal time,
gaps and unknown intervals through the complete lineage; checks the finite operation plan, all
intersected UTC-day request/byte upper bounds, full-lease storage runway and reviewed churn reserve;
and verifies signed host disk evidence against the caller-pinned capture path, device, filesystem,
clock source and current free space. A separately pinned per-method Helius price model includes a
canonical billing unit and recomputed method multiplicities. Claim records are owner-local,
write-once by predecessor terminal hash, and historical claim verification never reports current
eligibility. Trust roles require distinct Ed25519 public keys. Sol accepted this only as an offline
eligibility gate: `activation_authorized` remains false; no calendar/bridge runtime consumes it,
cross-host exclusion is not established, and the caller must supply real out-of-band trust, price,
clock and capture-root pins before a future adapter can use it. The byte bound charges the full
lease cap to every intersected bucket/day and may conservatively reject larger schedules. The 36
preflight tests and 131 focused preflight/calendar/bridge/controller/verifier tests pass; no provider
request, activation, live attestation, or real trust root was used. Receipt:
[`offline activation-preflight v1 validation`](evidence/senses-activation-preflight-v1-offline-validation-2026-10-11.json).

An initial-entry branch now accepts the separately typed c4 discontinuity certificate as a
predecessor without converting it into a normal v3 handoff. It independently verifies the fixed
certificate bytes and canonical digest, the candidate file and compiler inputs, and exact candidate
plan equality; preserves all four certified gaps and the still-open unknown interval; and uses a
domain-separated, owner-local one-use claim key. The signed host clock must match the candidate's
clock pin and its first anchor must remain strictly in the future. Sol accepted the branch only for
offline eligibility/preflight: `activation_authorized` remains false, no RPC or collector was
started, and external signature truth and cross-host exclusion remain unproven. The focused suite
passes 44 tests. Receipt: [`initial-entry activation-preflight v1 validation`](evidence/senses-initial-entry-activation-preflight-v1-offline-validation-2026-10-11.json).

## Candidate-position witness source feasibility (2026-10-11)

Sol reviewed the source-pinned Agave v3.0.0 feasibility contract and approved only an inert
synthetic consistency checker. The pinned `InvokeContext::process_instruction` boundary appears
capable in principle of exposing current frame account bytes for top-level instructions and nested
CPI; precompiles require a separate path. Standard Geyser does not supply candidate-instruction
position snapshots. This is a custom-runtime instrumentation hypothesis, not an implemented or
qualified source.

The required evidence chain separates frame attempts, successful/failed SVM execution, the later
`Bank::commit_transactions` result, post-freeze bank state hashes, ledger block hashes and the final
root binding. Commit batch order is distinct from block transaction order. A source-bound unique
bank-instance → freeze → block transaction → root join is required; same-slot or receipt ambiguity
stays unknown. Failure snapshots are attempted state, while fee/nonce rollback-account changes are
tracked separately. Availability uses conservative UTC upper bounds only when every required
receipt has finite evidenced clock uncertainty; an unknown bound leaves availability unknown.

The narrow core checker validates typed synthetic receipt/path/account consistency, bank-to-root
joins, shared execution runtime identity, and dimensional field labels; its initial 54-test
qualification is recorded in the core receipt. Sol accepted a follow-on synthetic transition
consistency gate, bringing the focused suite to 63 passing tests. It checks supplied Token-2022
gross/fee balance transitions and hook ordering, plus supplied WSOL SyncNative/CloseAccount account,
lamport, token-unit and path/order continuity. These are consistency checks over synthetic claims:
they do not decode raw account bytes, derive fee terms or Rent, validate hook effects, or establish
runtime/source/economic correctness. Neither protocol fixture is a qualified economic decoder.
Receipt: [`candidate-position transition consistency`](evidence/solana-candidate-position-witness-transition-consistency-v1-2026-10-11.json).
The checker's outputs permanently set economic admission, source qualification and real
provider/runtime completeness to false. The remaining work requires source-bound runtime witness
instrumentation and independent validation. Receipt:
[`candidate-position synthetic checker validation`](evidence/solana-candidate-position-witness-synthetic-checker-validation-v1-2026-10-11.json).
A disposable, source-pinned Agave v3.0.0 patch then verified the narrow hook-location hypothesis:
isolated ProgramTest CPI success and child-error paths each emitted ordered after-push/pre-execution
and post-execution/pre-pop account snapshots. Seven InvokeContext regression cases passed with the
feature enabled and again with it disabled, preserving the default path. Sol accepted this only as
local raw boundary-capture feasibility. The collector uses a global buffer,
has no transaction identity or guarded panic cleanup, omits failed pushes, and observes executable-
chain return rather than final instruction/transaction or Bank commit. It is not a transaction-scoped
witness, real-source qualification, or economic decoder. Receipt and exact patch:
[`Agave v3 boundary-capture feasibility`](evidence/agave-v3.0.0-candidate-position-boundary-capture-feasibility-v1-2026-10-11.json),
[`source patch`](evidence/agave-v3.0.0-candidate-position-boundary-capture-feasibility-v1.patch).
No c4 body or historical replay was opened, and no provider RPC, decoder promotion, audit or economic
admission occurred. That v1 capture was not transaction-scoped. The v5 gate below adds local
execution identity and sequencing, guarded frame cleanup, explicit push/pop/transaction terminal
outcomes, complete selected-role account-set checks, and independent replay tests. Prospective
activation remains closed: c4 is terminal with gaps preserved, no successor lease exists, provider
account charge controls are unknown, and internal disk is below the reviewed floor.

### Transaction-scoped local observer v5 (2026-10-11)

Sol accepted the source-pinned Agave v3.0.0 v5 patch for local synthetic ProgramTest observer
feasibility only. Each boundary binds execution ID and attempt ordinal; account-bearing phases check
the selected raw snapshots, terminal events carry no account snapshot, and each instruction frame
retains the same program/data identity through its phases. Mutation cases cover cross-attempt splicing,
missing/unavailable selected fields outside pre/post, and program/data drift. Feature-on CPI tests
passed 11/11; feature-off packages compile; patch whitespace check is clean. The source patch and
review receipt are hash-bound in the immutable [v5 review record](evidence/agave-v3.0.0-candidate-position-observer-v5-review-2026-10-11.json).

This does not qualify production integration, historical replay, Bank persistence/finality,
settlement, real-source completeness, or economic admission. Precompiles remain unsupported. The
additive `SVMTransaction::signed_message_hash` default method is unconditional, although feature-off
execution does not invoke it. The next bounded offline gate is raw Token-2022/WSOL account-byte
decoding from disposable ProgramTest traces with source-pinned fee, hook, native-token, Rent, epoch,
and runtime inputs; failed executions remain attempted-only. No c4/tail2, provider, archive, or audit
data is in scope.

### Raw Token-2022/WSOL parser scaffold v3 (2026-10-11)

Sol accepted a source-pinned, feature-gated pure parser and unsigned synthetic mapping scaffold only.
The corrected patch applies to Agave `v3.0.0` commit `b6c96e84b10396b92912d4574dae7d03f606da26`,
adds the mapping module export once, and leaves `Cargo.lock` unchanged. Legacy Token, Token-2022,
and legacy WSOL identities are constants cross-checked against pinned upstream source; supplied
program/mint IDs and account owners reject on mismatch. Feature-on tests pass 16/16; feature-off
check passes and the feature-off library has two tests. The patch is independently re-applied and
validated in the exact clean checkout; its SHA-256 is
`3d1347ffbca5c5023d2d235835ee0ff7eca2c95bfbf5ff6bf10ec831af0ebbee`.

This is not a transaction witness or economic decoder. Hook execution, ordering, and effects remain
unknown/unsupported; Rent and epoch are caller-supplied; fee arithmetic is local consistency only,
not Token-2022 runtime-law evidence. Raw bytes and mapping inputs are separate from observer traces.
No signed fixture, runtime capture, RPC, source completeness, economic qualification, training,
settlement, or production admission occurred. A proptest run was omitted because the offline cache
lacked `bit-set v0.8.0`; no dependency was fetched. Existing edge tests and a deterministic fee
sweep remain. The exact patch and unique receipt are
[`parser scaffold v3 patch`](evidence/agave-token-raw-parser-scaffold-corrected-v3-2026-10-11.patch)
and [`parser scaffold v3 receipt`](evidence/agave-token-raw-parser-scaffold-corrected-v3-2026-10-11.json).

The repository now has a separate `brain-senses-raw-account-fixture-v1` synthetic byte-replay
contract for Token-2022 fee configuration, transfer-hook presence, attempted instruction wires,
Clock/Rent layouts, and WSOL account fields. It binds canonical fixture bytes and verifier source
hashes, rejects malformed encodings and broken account joins, and records instruction effects only
as attempted. Transfer hooks remain unsupported without runtime evidence; fee fields remain raw
configuration rather than a settlement calculation. Upstream source references are inherited pin
metadata and are not revalidated by this fixture tool. The 26 focused tests qualify only synthetic
byte parsing: runtime law, point-in-time eligibility, economic settlement, and admission remain
false. No real capture or Agave trace was consumed.

An independent local-trace envelope v1 now binds the pinned Agave/patch/lock/runtime/feature
identities, one attempt and execution, ordered instruction/CPI paths, candidate account roles,
pre/post or attempted-only raw account bytes, Clock/Rent bytes, fee/hook configuration bytes, and
receipt versus durable-availability evidence. Its verifier enforces exact shapes, same-attempt
joins, raw-byte hashes, Clock epoch/slot joins, and permanently false qualification claims. Focused
Hypothesis and mutation tests pass; the envelope validates structure only. No ProgramTest trace has
yet been consumed, and it supplies no runtime-law, hook-effect, rooted-chain, settlement,
buyExact25, point-in-time, or economic qualification.

Sol's prospective economic-witness decision keeps `raw_candidate_witness_acquired` separate from
`economic_settlement_qualified`, with qualification false by default. Existing transaction-meta
endpoints, ordinary `getBlock`/`getTransaction`, slot-S account reads, and message signer/fee-payer
identity do not establish ordered instruction-position state or ownership. A future raw witness must
bind the exact transaction/instruction/CPI path and execution attempt to ordered raw pre/post account
bytes, lamports and program owners for all possibly writable roles; Clock/Rent/config bytes;
success/failure, commit/freeze and finalized-root joins; source/runtime/decoder identity; raw hashes;
and distinct event, receipt, durable-availability and decision times. Missing clock bounds keep
availability unknown. Failed, partial, unsupported, missing and fork/root-ambiguous captures remain
explicit status classes, never evidence of zero or settlement. Qualification additionally requires
independent replay of every writable effect, era-correct Token/Token-2022 fee and hook semantics,
WSOL and historical Rent conservation, isolated close-destination credit, and owner/fee attribution
using integer lamports and mint atoms separately. `sell23` and `buyExact25` need separate class gates;
the observed 25-byte `buyExact25` wire does not qualify amount decoding. A bounded future-only RPC
feasibility pilot may provide slot-bound diagnostics only. Class-level economic or training admission
remains a separately registered prospective gate. Acceptance must include a disjoint denominator
with failed/partial/unsupported cases, raw replay, and adversarial fork, CPI order, opaque touch,
missing Rent/owner/hook and delayed-availability cases.

The inert `brain-senses-prospective-account-state-feasibility-v1` checker validates only supplied
record shape and internal consistency for a preselected persistent token-account example, with at
most two finalized account reads, raw account-byte hash checks, distinct request/receipt/availability
times, and explicit failed/unobserved denominators. Its `selected_before_acquisition` flag, horizon,
and evidence hashes are assertions, not source-authenticated proof. `none_within_horizon` is a
syntactically accepted status, not verified absence; the one-position denominator describes only
the supplied record. Intervening-write completeness, source coverage, finality/fork identity, clock
uncertainty, candidate-position admission, settlement, PnL, buyExact25, PIT and economics remain
unknown or false. The checker performs no provider I/O or activation. Its focused Hypothesis suite
also verifies arbitrary account bytes round-trip through canonical lowercase hex and rejects
free-form failure payloads. Any real-data use requires a separately reviewed/versioned contract
before acquisition, with a durably timestamped complete preselection denominator, fixed finite UTC
horizon, immutable raw source receipts and qualified coverage/finality/clock joins. Even then, two
endpoint reads remain diagnostics and cannot establish instruction-position state.

Sol's follow-on source-capability review found no documented read-only RPC path that independently
provides raw account bytes at an exact instruction/CPI position. Transaction/block data supplies
ordering and transaction-level balance metadata; finalized account reads supply endpoint state;
neither resolves intervening same-slot writes or fee/nonce effects. Alchemy Archive `getAccountInfo`
at explicit slots may be useful as a bounded historical diagnostic if entitlement is verified, but
two endpoint states still cannot qualify a candidate-position witness. The smallest next experiment
is a separately versioned, pre-registered one-position diagnostic: choose one public, unprotected,
finalized fixture by a deterministic signature/slot rule before new responses; cap at four calls
(block, transaction, and at most two slot-bound account reads), one finite attempt window, raw bytes,
and Kingston allocation. Verify Archive entitlement before those reads; never substitute Helius or
current-state RPC for unavailable historical coverage. Preserve ambiguous attempts as spent without
retry and classify same-slot writers, source gaps, fork conflicts, failures, and unobservable state
explicitly. A passing result can report only `slot_boundary_diagnostic`; it cannot establish the
candidate-position witness, settlement, `buyExact25`, PIT, or economics. The raw observer still
requires an isolated pinned Agave worker/VM with ordered transaction-to-Bank-to-root evidence.

### Independent rolling prospective acquisition policy (2026-10-12)

Sol selected a new Helius-only forward collector with independent epoch roots, starting at a fresh
finalized `getSlot` anchor at the next UTC hour. Each 24-hour epoch has 24 hourly inventory-first
frames (one `getSlot`, one `getBlocks`, up to 16 selected `getBlock` reads and 16 signature checks),
for at most 817 attempted requests, 4,000,000,000 response bytes, and 4,000,000,000 physically
allocated bytes. Polling is frequent and idempotent; an expired window becomes an explicit
unobserved gap and is never backfilled. A rolling 30-day accounting window caps dispatch at 25,000
requests, 250,000 conservatively modeled Helius credits, 120,000,000,000 response bytes, and
120,000,000,000 physically allocated bytes. Physical allocation includes payloads, journals, plans,
receipts, and directory metadata; measure it under the bound service root and reserve a bounded
maximum response plus metadata before each intent. These credit values are a local upper-bound
model, not an account meter or charge claim. The per-request pace is at most four requests per
second; free-space floor and peak-write margins remain enforced. The 30-day window is not a service expiry: a fresh
independent epoch may start after exact terminal verification, and the loaded supervisor remains
eligible to continue while all safety and rolling-budget gates pass. A cap pause resumes only with
headroom and a new future anchor.

The service also pins a lifetime allocation cap on first activation:
`min(150,000,000,000 bytes, activation free space − 50 GiB floor − 8 GiB operating reserve −
4 GB next-epoch allowance − 322,000,000-byte peak/metadata margin)`. With the current Kingston
headroom this is 150 GB. The cap never resets when the 30-day accounting window rolls. A
descriptor-relative inventory counts all service-root files and directories using
`max(size, allocated blocks) + 16 KiB` per object, rejects unsafe or unexplained entries, and is
recomputed before each request. Every closed epoch terminal receipt binds an independently replayed
allocation manifest. Each fresh epoch must fit its full 4-GB allowance in both the lifetime budget
and current free-space envelope. The live volume guard preserves the 50-GiB floor, 8-GiB operating
reserve, request reservation, and peak/metadata margin. Reaching the lifetime cap writes a durable
pause that does not auto-resume; capacity expansion or evidence-preserving offload requires a new
qualified bound. No raw capture deletion or automatic calendar expiry is permitted.

This policy does not authorize c4/external-v2 replay or successor linkage. The separate v4 runtime,
independent capture/frame/epoch verifier, immutable allocation-cap receipt, LaunchAgent, and forced
detach/recovery tests are implemented. Activation still requires a pinned clean commit, exact
source/plist/host/credential/clock preflight, and live forward-only validation on Kingston. Code is
now pinned; the remaining live gate is the first anchor/frame and independent replay. Once loaded,
the hourly service has no calendar lease expiry; only real request/rolling/lifetime/storage
caps, provider errors, clock/provenance ambiguity, volume identity, recovery integrity, and the
available storage floor pause dispatch. Missing billing telemetry, unknown decoders, market
completeness, and pending economic qualification do not block bounded prospective collection.
If a code update invalidates a plan before its first durable request intent, that future-only plan is
terminally abandoned under the old source pin and the ordinary recovery cooldown; the request is
never replayed. A source change after any request intent remains fail-closed.

The v5 LaunchAgent is loaded under `gui/501` with a 60-second interval, `RunAtLoad`, and no calendar
expiry. It uses verified v4 cutover usage and the Kingston-bound shared coordinator v2. Its fresh
17:00Z anchor captured one finalized `getSlot` response; independent replay verified current source
bindings, the event prefix, raw response receipt, availability time, and coordinator intent
linkage, with no unresolved reservation. This proves structural anchor acquisition only, not a
complete frame or prospective market-price history. V5 remains governed by rolling, epoch,
physical-allocation, and Kingston free-space caps; there is no lease expiry or gap backfill.

This structural Helius collector is not a prospective market-price history lane. The first bounded
GeckoTerminal OHLCV lane is a separate forward-only service. Shared descriptor-bound capacity
coordinator v2 passes crash-prefix, lane-lock independence, descriptor-loss, corrupt-ledger,
allocation-boundary, and multiprocess shared-cap tests. It pins aggregate v4/v5 Helius allocation,
requires a held lane lock for each intent, and supports service-scoped recovery. Gecko v3 retains
durable 12-second pacing, one-intent/no-retry dispatch, 429 cooldown, and restart recovery. The
17:15Z pilot is terminal with zero requests; the 17:45Z attempt was sealed before provider I/O
because its plist lacked the required static pilot ID. The later sealed pilot at 18:15Z made three
Gecko requests and left one selected pool unrequested/unknown. Its receipt is immutable and
diagnostic-only; independent forensic replay of the clock-bound v3 records verifies three
intents/dispatches/results and does not authorize release.

The existing runtime added three clock fields to v3 intents, while the prior independent verifier
required the exact seven-field v3 contract. This mismatch is now closed for future collection by
the separately versioned independent intent-v5 verifier. It requires the exact clock-bound schema,
hashes every field except the digest, reads canonical clock bytes from the same descriptor-bound
pilot root, and verifies path, hash, dual-HTTPS clock evidence, wall/monotonic
progress, bucket window, terminal hash coupling, and complete clock-file closure. It preserves
post-fsync availability separately from HTTP receipt and replays each successful bar against the
raw response. A partial bucket is a verified prefix with missing pools unknown and cannot authorize
release. The v4 schedule allows dispatch only in `[close+60s, close+240s)`, request start strictly
before `close+215s`, and durable availability strictly before `close+240s`; late responses remain
spent and unknown without retry. Its two-stage preflight requires a full four-pool receipt, at least
one eligible new-open bar, and exact test/source/plist/coordinator/storage pins.

The v4 runtime replaces per-minute hash-chain growth with monitor-v2: one descriptor-bound atomic
latest-health snapshot plus a bounded, immutable transition ledger for alarms and recovery. The
snapshot binds Gecko-local intent/dispatch and result/capture/confirmation tails, source and plan
hashes, volume identity, and a coordinator sequence/hash/policy marker. Since Helius and Alchemy
share that coordinator ledger, an independently replayed later provider event is accepted only as a
valid ancestor extension; Gecko-local tail changes require verified service-scoped no-replay
recovery and an immutable `journal_reconciled` receipt. Corrupt, missing-after-initialization, stale,
or mismatched monitor state never authorizes request replay. Healthy 60-second ticks replace the
same bounded snapshot; the alarm-receipt cap pauses for review. Focused tests cover 30-day
constant-size heartbeats, crash points around rename, recovery, cap behavior, stale state, and detach without
writes to the mountpoint underlay.

Capture-only v1/v2 roots and the no-request v3 root remain read-only; their LaunchAgents are
unloaded. The separately versioned v4 service uses the pinned 15-minute/four-pool technical policy,
60-second LaunchAgent ticks, no calendar expiry, one intent per pool/bucket, no retry/backfill,
12-second minimum dispatch spacing, and finite provider, raw-byte, memory, and Kingston caps. Its
runtime, monitor, and exact interpreter handoff are committed with the v4 plist. Sol approved
capture-only operation with `market_source_qualified=false`, `training_admission=false`, and
`economic_admission=false` until independent prospective semantic qualification. At the first
future bucket (21:00Z on 2026-10-12), independent replay verified four actual GeckoTerminal HTTP 200
dispatches, four durable raw captures/results/confirmations, no missing/unknown/spent requests, and
four timely availability records on the bound Kingston volume. All returned bars were outside the
new-open window, so eligible new-open pools were 0/4; this is live provider acquisition evidence,
not useful-price coverage or source qualification. The immutable observation receipt is
[`v4 first live bucket`](evidence/gecko-capture-only-v4-first-live-bucket-2026-10-12T2100Z.json).
A controlled LaunchAgent bootout/bootstrap between windows then recovered to the same verified
4-intent/4-result prefix without replay or internal-storage fallback. The service remains loaded;
the next bucket is forward-only. Provider charge remains unknown/unverified. Alchemy same-frame
corroboration remains offline.

The next two natural v4 buckets each independently replayed as four real HTTP 200 dispatches,
four durable results, and four confirmations with a healthy prefix and no retry. The second bucket
contained 12 returned bar records, all excluded as out-of-window; eligible new-open pools remained
0/4. This qualifies continued prospective transport and recovery only, not usable new-open coverage,
population, or market-source completeness. Its immutable observation is
[`v4 second live bucket`](evidence/gecko-capture-only-v4-live-bucket-2026-10-12T2115Z.json).
The receipt's `qualification.claim_limit` prose reverses that classification; its immutable
structured replay fields are clarified by the
[`v4 second-bucket erratum`](evidence/gecko-capture-only-v4-live-bucket-2026-10-12T2115Z-erratum.json).
A separate pure selector
`gecko-train-six-15m-rotating-scout-v1` encodes Sol's next sampling horizon for the 170-pool train
population: six distinct pools per close, deterministic hash-ranked cycling, and at most two
four-bucket retention slots from the immediately previous bucket's timely, received, durable,
eligible new-open evidence. Remaining scout slots cycle without response-based replacement. With no
retention the nominal cycle revisits in 28–29 buckets; with two retained slots, four scouts revisit
in 42–43 buckets. Property tests cover permutation invariance, full-population cycling, retention
ordering/expiry, and fail-closed time joins. The selector has no provider or storage side effects and
is not active. Its compiler revalidated the exact four frozen enrollment/manifest/plan/selection
byte pins and emitted all 170 train-only metadata rows, with population digest
`938ee425361c27e03f18a123bcdf3d35edbbfafe719c12324c517b408c1cbc8d`; the v3/v4 four-row selected
subset remains unchanged. Training eligibility remains false. The separate pure plan-chain model
hash-binds each six-pool plan, prior-only evidence, cursor transition, and population/policy pins;
independent replay requires an explicit no-dispatch gap record, and a missed bucket leaves the
cursor unchanged. Recovery only returns an already persisted, replay-verified plan. Its focused
synthetic tests pass. A create-only descriptor-bound journal binds root ID, UUID, device/inode,
policy/population and release digest; forced atomic-rename failure leaves no partial plan or
path-based fallback artifact, and restart recovers the identical persisted plan. These tests use a
disposable local descriptor and do not initialize the Kingston v5 root. No provider runtime or v5
activation exists yet. v4 remains the only Gecko collector until offline release/source binding,
recovery qualification, cross-version cap accounting, source replay, and a distinct future-only
six-request pilot pass their gates. Proposed
v5 ceilings are 576 intents/UTC day and 17,280/rolling 30 days, 10 MiB/day and 300 MiB/30 days of raw
responses, with 2 GiB reserved physical allocation; all existing same-provider intents/bytes count
toward limits. The transport contract keeps at least 12 seconds between dispatches (maximum five
per rolling minute), a 20-second timeout, 16 KiB response ceiling, no retry/backfill, and the existing
16 GiB resident/50 GiB free-space limits. These are bounded capture-only caps, not source, training,
or economic qualification.

The separate shared-usage assessor requires a complete explicit root inventory and independently
replayed snapshots, aggregates same-provider attempts across versions, charges unresolved intents
at their pinned maximum response size, and includes a prospective request before admission. Its
descriptor-bound Kingston adapter now discovers all known direct and collection roots, verifies
absence, holds each root's shared lock while independently replaying it, hashes every root's files,
and rechecks the root census after replay. Unknown service entries, malformed pilot identities,
unrecognized legacy plan failures, or a changing census fail closed. A present direct v5 root remains
unsupported. The separate `capture-only-v2/<release-id>` scout-chain root now has an independent
read-only usage replay: it binds release, policy and plan chain; enforces exact artifact/file closure;
replays v2 intents with embedded decision clocks, dispatches, atomic result envelopes with embedded
bounded raw bodies, coordinator reservations/settlement, and per-request post-settlement confirmations
with embedded fresh clock evidence; and projects unresolved intents at their pinned maximum. The
shared coordinator's Gecko path supports create-only atomic intent publication. An initialized plan-only root is
`verified_none` only when it has no attempt artifacts. The adapter verifies the release storage
identity against the descriptor and coordinator policy, holds a nonblocking shared root lock, and
fails closed on malformed or changing evidence. This replay grants no dispatch authority and does
not qualify source semantics. Three old capture-only roots with stale source bindings are accepted
only through a narrow inert-plan check: no activation or attempt artifacts, false authority, and
valid pinned policy/plan hashes. Clock-bound v3 forensic replays count only their verified requests
toward usage; diagnostic-only status does not qualify source semantics. The capture-only v4
LaunchAgent that produced the old root remains unloaded after integrity replay found an unreferenced
clock artifact for a forward bucket where all dispatch windows had already been missed. The root is
preserved and not repaired in place; the v2 writer stores clocks only inside the request artifact
they support, so a bucket that produces only skips leaves no clock artifact.

The immutable [`22:00Z shared-usage observation`](evidence/gecko-shared-usage-observation-2026-10-12T2200Z.json)
records the earlier 22:01:03Z read-only snapshot. The subsequent [`v4 usage reconciliation`](evidence/gecko-v4-usage-reconciliation-2026-10-12.json)
is limited to one exact orphan clock: it binds the immutable legacy root prefix, storage identity,
source closure at commit `8e54478727c21572ceb654b689a31e97e11463c3`, clock bytes, and four durable
missed-dispatch skips, then independently replays all 18 intents, dispatches, terminal results and
confirmations (13,675 raw bytes; zero unresolved). The old root was not changed. The current
[`23:09Z shared-usage observation`](evidence/gecko-shared-usage-reconciliation-observation-2026-10-12T2309Z.json)
verified 11 known roots and 28 same-provider attempts (21,400 raw-response bytes). A synthetic
16-KiB prospective reserve remains inside daily and rolling caps. This is diagnostic only: no lane
dispatch lock was held, no provider I/O occurred, and admission was not authorized. Provider charge
remains unknown/unverified. The v2 usage verifier, descriptor-bound inventory adapter, and
separately versioned descriptor writer are implemented and synthetic-tested. The writer acquires
the shared Gecko lane lease before its nonblocking v2 root lock, rechecks the exact root prefix, and
publishes intent, terminal outcome, and confirmation through create-only descriptor-bound atomic
writes; both clocks live in the record they support. Forced detach during intent publication leaves
the replacement mount untouched. This is not yet a provider runtime or LaunchAgent qualification.
The prior v4 LaunchAgent was loaded but stopped with exit 2 after the exact capture root failed replay
on one unreferenced clock file for a bucket with four durable missed-dispatch skips and no intents or
dispatches; it has been booted out. The root remains immutable. The global same-provider census now
succeeds through two exact, release-specific usage reconciliations; no generic clock-closure bypass
was added. The a078 v2 health sidecar is accepted as `verified_none` only after replaying its
terminal receipt against the unchanged request root and stable 19-root census. That receipt binds
the old source map/commit, storage identity, plan and six slots, STOP snapshot/event, four unique
intent/dispatch/HTTP-200/raw-result/settlement/confirmation chains (3,041 bytes), two unknown
no-intent/no-skip slots, and absence of any post-STOP artifact. The independent live inventory
replayed that request root as `verified_prefix` (four attempts) and the health root as `verified_none`
with provider I/O disabled. Receipt:
[`a078 terminal usage reconciliation`](evidence/gecko-capture-only-v2-a078-terminal-usage-2026-10-13.json).
The old release remains terminal; the reconciliation grants no source, training, economic, retry, or
dispatch authority. A corrected v2 host scheduler/runtime, crash/race/recovery qualification,
release/source/plist binding, and bounded forward-only provider pilot remain open. Before v2
provider I/O, v4 must remain unloaded because its pacing check does not account for v2 dispatches.
Existing v4 budget logic still omits capture-only roots, so it is not shared cross-version admission
authority.
Physical allocation remains the shared coordinator's responsibility.

The Agave build runner v1 remains rejected. A separately versioned default-deny Seatbelt fixture
v2 passed synthetic parent/child read, write, network, symlink, and detached-child cleanup tests,
but the forced stage-path replacement test showed that the child can write to a same-name underlay.
Its profile is therefore **NO-GO** for Kingston/Cargo: pathname grants and sampled UUID checks do
not bind ordinary Cargo writes to a mounted volume across detach. This blocks the current
unprivileged Seatbelt route on this owner Mac; it does not prove all possible kernel or separate-user
confinement approaches impossible. Preserve the dirty Agave checkout/cache and do not run Cargo or
ProgramTest. A future route requires a demonstrated kernel-enforced mount-identity boundary or an
unprivileged identity unable to access the underlay, plus source/cache review.

Sol selected a fresh unprivileged build identity as the next inert feasibility gate, layered with
the path-restricting profile. Its OS-level DAC/ACL must deny search/write access to the mountpoint
underlay and owner checkout/credentials even after Kingston detaches or the staged path is replaced;
only a dedicated subtree on the verified mounted volume may be writable. Before any offline Cargo
build, a disposable-volume test must force detach/path replacement across absolute paths, held
directories, symlinks, renames, build-script children and detached children, with an unchanged
underlay, no internal fallback, no surviving child, pinned offline source/cache closure, verified
mount identity and bounded allocation. Any escape is NO-GO and moves the build to an isolated VM or
worker without a writable host share. The current owner Mac has no dedicated service identity; the
only non-owner local account is a system PostgreSQL account, and switching to it requires admin
authentication. No account or ACL has been changed. Passing this gate would permit only a separately
reviewed offline build; runtime/source-law and rooted economic qualification remain later gates.

The shared capacity coordinator v2 is initialized on the configured Kingston data root. Its
descriptor-bound policy binds root ID, UUID, device, and inode; live Helius v5 admission and Gecko
pilot use the same canonical ledger while holding their lane locks. It reuses the existing v4
Helius lock without rewriting it and rejects partial or mismatched coordinator state. Independent
v5 replay verifies the captured request intent against the shared reservation ledger.

The offline Gecko v2 contract is not eligible for activation: its two-second spacing does not meet
the public API's current approximately 10-calls/minute guidance, which may vary with traffic.
[GeckoTerminal's official API documentation](https://api.geckoterminal.com/docs/index.html) also
identifies the API as beta and says responses are cached for one minute. Sol selected a separately
versioned v3 contract with at least 12 seconds between actual dispatches across restarts, persisted
under the lane lock, and a pre-HTTP deadline check. It preserves the forward-only 60–180-second
post-close window, one intent/no retry, and stops the bucket on 429 with a durable cooldown of at
least one hour or a valid longer `Retry-After` (over 24 hours requires review). v2 remains frozen
offline. The v4 capture-only service exercises this pinned forward-only transport policy under a
separate technical release identity; it does not qualify the price modality or promote its pool
population.

Helius v5's separately versioned adapter, independent verifier, rolling-usage cutover builder,
LaunchAgent, and forced detach-at-durable-intent tests were implemented, but its LaunchAgent is not
currently loaded. Two recent epochs were abandoned after `response_invalid`, each retaining one
possibly-spent request, and the following open 23:00Z epoch missed its dispatch windows. It remains
immutable and cannot be resumed or repaired in place. Sol approved a distinct v5r2 release with a
narrow cutover exception for that exact no-dispatch open root; it must replay all 35 prior attempts,
including both ambiguous requests, preserve shared coordinator semantics, and anchor only at a
fresh future epoch after the two-abandonment window clears. The separate v5r2 implementation now
passes focused replay tests and a fresh read-only Kingston preflight: all 35 attempts reconcile
(33 captured, two possibly spent), inherited and shared-capacity accounting closes at 651 lifetime
requests / 6,510 modeled credits, and the next anchor is 2026-10-13 19:00Z. The exact preflight
receipt is [`Helius v5r2 cutover preflight`](evidence/helius-v5r2-cutover-preflight-2026-10-13T0245Z.json).
This closes offline cutover/recovery accounting only. The old LaunchAgent stays unloaded; the new
one is not activated before the pinned future anchor. Provider charge remains unknown/unverified.
Market bars, complete structural inventory, and economic qualification remain unproven.

The new Gecko capture-only-v2 release `00263d9787393fbc4d0fa3db8a276d7c` was prepared as a
plan-only Kingston root and briefly loaded with a 12-second callback. It failed closed before any
intent because the exact a078 terminal receipt binds the prior 19-root global census; adding the
new sibling changes that census. The a078 request and health roots remain unchanged, and the
00263d root has no intent, dispatch, or provider result. The expired prospective slot is unknown and
will not be backfilled. Its callback is unloaded. Sol approved a separately versioned bridge that
reconstructs the exact historical 19-root manifest while independently replaying every current
root and requiring stable full-census and same-provider usage checks. The bridge now also accepts a
new health sidecar only with its independently replayed same-ID capture root, matching release,
binding and storage identities, empty health usage, and a bounded health intent count. The current
inventory performs a final census recheck after bridge receipt reconciliation. Focused tests pass;
a read-only descriptor-bound live replay verifies 21 roots, 32 attempts, and 24,441 response bytes
with provider I/O disabled. A fresh release/root is required before any dispatch; this failure and
bridge grant no source, training, or economic authority.

A distinct post-bridge Gecko root `6672937e8ee81c6074e99d7f5f8083c3` then recorded three actual
HTTP-200 responses (2,531 bytes); every returned bar was outside the admissible forward window, so
there were zero eligible bars. Three later slots were durably classified `missed_dispatch`; no
backfill or ambiguous request occurred. A bootout/bootstrap recovery check preserved the exact
3/3/3 intent/result/confirmation chain and made no provider request. That source-bound LaunchAgent
is now unloaded: review found its idle health callback could publish zero usage counters without
replaying the global census. The host now recomputes the verified census while holding the Gecko
lane lock and leaves the prior snapshot unchanged if the census fails. Its entrypoint also adds the
repository `src/` path before importing the descriptor-bound storage package. The old source-bound
root and release remain untouched; a new release must pin this correction and target only a future
bucket. The observed responses and recovery qualify transport/replay behavior only, not price
coverage, source semantics, or market admission.

The corrected source-pinned release `060bed015daf4918b061d4eea72e2d65` is loaded as a LaunchAgent
with a 12-second callback and no calendar lease expiry. It is bound to the verified Kingston root.
Its policy permits at most six selected pools per 15-minute bucket, one request per callback, at
least 12 seconds between dispatches, 576 requests/10 MiB raw per UTC day, 17,280 requests/300 MiB
raw and 2 GiB physical reserve per rolling 30 days, a 16 GiB resident ceiling, and a 50 GiB
free-space floor. These are finite safety limits, not a time-limited lease. Recovery was checked by
bootout/bootstrap before provider collection. Across the first three natural buckets it produced
seven actual GeckoTerminal HTTP 200 responses / 5,819 raw bytes; independent inventory replay
verified the exact prefix, 27-root/42-attempt global census, and the empty plan-only candidate root
([`three-bucket live observation`](evidence/gecko-capture-only-v2-live-observation-2026-10-13T0307Z.json)).
All three windows had zero eligible new-open bars: 3/6, 2/6 and 2/6 requests, with the 11 remaining
selected slots durably marked `missed_dispatch`. Health is paused outside the window with
`stop_latched=false`; a bootout/bootstrap after the first two buckets preserved all five then-current
request chains without replay. Measured callbacks exceed the 12-second cadence, constraining
coverage. A two-second process sample observed 26 subprocess forks in the active source-pinned
callback. The fresh v3 successor `742db927422a3f772c6f46f6d9084c60` is source-bound and independently
accepted as `verified_none` by the global inventory. Its descriptor-write fast path passed forced
detach/replacement tests; a full-validation, no-provider callback took 22.2 seconds. This does not
yet demonstrate six timely requests in one window, so the candidate is not activated. No calendar
gap is backfilled. Provider charge remains unknown/unverified; market-source, training, and
economic admission remain false.

The source-pinned Gecko capture-only v4 release `3569c27ff9916382c209ea71ef23e345` completed its
first forward bucket under independent watchdog v2. It made six GeckoTerminal HTTP 200 requests
(4,688 raw bytes), with six durable intents/results/confirmations, no misses or ambiguous requests,
and dispatch spacing of at least 23.7 seconds. The watchdog passed the timing gate and independently
recorded zero eligible new-open bars. Full v2 inventory replay after watchdog publication verified
41 roots / 58 attempts with provider I/O disabled; its watchdog proof is a `verified_none`, zero-
attempt diagnostic and does not expand dispatch authority. A post-bucket LaunchAgent
bootout/bootstrap preserved all six request chains with no replay. The LaunchAgent remains loaded
with a 12-second callback, no calendar lease expiry, and its next untouched bucket at 04:30Z;
descriptor-bound writes remain bound to Kingston. This demonstrates one bounded live bucket and
restart recovery, not sustained coverage or market-source qualification. Provider charge remains
unknown/unverified; training and economic admission remain false. Receipt:
[`v4 forward-canary observation`](evidence/gecko-capture-only-v4-live-observation-2026-10-13T0421Z.json).

The next untouched bucket closed at 04:30Z with four of six selected requests dispatched: three
HTTP 200 responses and one durable `URLError` terminal classified
`transport_uncertain_possibly_spent`; two opportunities remained unattempted. Across both buckets,
the Kingston root holds ten intents and confirmations, nine HTTP 200s / 7,445 raw bytes, one
ambiguous possibly-spent attempt, and zero eligible new-open bars. The ambiguous request is never
retried; its cooldown ends at 04:47:52Z. Read-only replay then exposed a temporal-verifier defect:
the old verifier treats urllib's 20-second per-blocking-operation timeout as a total wall bound and
rejects this receipt at 20.216 seconds (`v2_terminal_times`). The frozen release remains loaded but
its subsequent callbacks fail closed before provider I/O. The launchd label was absent by 04:49Z;
there is currently no autonomous collector loaded, and the 04:45 bucket passed without a durable
dispatch record. Do not claim continuous collection or re-run the old source-bound release. Reconcile
this exact terminal with a separately versioned verifier adapter, preserve its spent usage and
cooldown, account the two unattempted 04:30 slots and all six 04:45 slots as explicit gaps in a
reviewed successor handoff, and resume only from a fresh untouched bucket after full read-only
census. No request replay or backfill is allowed.

Sol retained six deterministic pools per 15-minute bucket and the existing rotating cursor; these
two buckets do not justify increased cadence or response-driven selection. After exact replay repair,
require one clean six-slot recovery bucket, then measure one frozen 170-pool sweep: about 29 completed
buckets with no eligible-retention fills, or up to 43 if both retention slots fill. This is an
observation horizon, not source qualification. Keep separate cumulative and per-bucket denominators
for selected distinct pools / 170, confirmed/ambiguous/failed/missed transport outcomes per selected
opportunity, and timely raw-replayed new-open bars per opportunity and per confirmed response.
Record bar-age/window-rejection histograms and failure reasons. A full sweep with no eligible bars
triggers source/window/availability semantics review before changing the sampler; sparse positive
coverage still requires independent source qualification and never implies training admission.

The 04:30 cutover was subsequently replayed into a source-bound handoff certificate, and a separate
scout-chain v2 now verifies the predecessor at cursor 12, requires explicit elapsed-bucket gaps, and
opens plan creation only from close+60 seconds through before close+180 seconds. Its synthetic
chain/handoff/inventory tests pass; it has no provider or dispatch authority. Inventory-v3 currently
accepts only zero-attempt v2 successor roots. A distinct capture-prefix verifier and inventory-v4
must replay v2 intents, dispatches, terminals, raw responses, confirmations, skips, and coordinator
events into full shared usage before a v2 host may dispatch. The frozen v1/v2 runtime is unchanged.
As of 05:23Z, no collector LaunchAgent is loaded and no prospective provider I/O is active; Kingston
has 202 GiB free. Continue only with synthetic tests until the versioned host, release/plist binding,
crash/ambiguity recovery, deadline semantics, and forced detach qualification are complete.

The chain-v2 capture-prefix verifier and inventory-v4 now pass synthetic replay for plan-only,
expired-unaccounted, and unresolved-intent prefixes; unresolved attempts consume their maximum
response reserve in shared usage. A separate descriptor writer/host transaction library passes
synthetic reservation, dispatch, terminal/confirmation, no-replay recovery, and path-replacement
tests. These modules have no owner-host loader or activation path and were not run against Kingston
or a provider. Source-bound release loading, plist/watchdog identity, every-write detach coverage,
and runtime installation remain open; no autonomous collector is loaded.

The v6 source-bound callback distinguishes the BrainData descriptor identity from its nested service
root. Epoch and activation bind both device/inode pairs; the host reopens the exact service path
through the held BrainData descriptor before dispatch and verifies the writer FD. The create-only
bootstrap initialized Kingston release `259c20d752fa383c3bb42637d10fb5da` with activation written
last and an 11:15Z anchor. Its independent replay found zero plans and no provider I/O. The first
LaunchAgent failed closed on every callback with exit status 2 and wrote no journal event; it has
been booted out, and its plist and root are preserved read-only. At that point there was no active
prospective collector or v6 provider traffic. Sol approved additive inventory-v3: retain exact replay of the
historical 41-root / 62-attempt receipt, independently replay every current v6 root (including old
zero-attempt roots), and prove a candidate successor ID absent before bootstrap. It passes 52 focused
tests and descriptor-bound live replays (42 roots before bootstrap, then all current roots). The first
successor `7a10e2d4c9f84c18a0b53d7e6f214c90` was create-only initialized but its launchd callback
exposed a direct-script import defect; sanitized diagnostic `v6_owner_runtime_import_failed` was
written, with no plan, intent, dispatch, response, or provider I/O. The repository-root import path
was fixed, and release `31cf35634151805f006a2cdde943c74c` was initialized create-only. Three idle
callbacks exited 0, but a later docs-only descendant commit invalidated its exact-HEAD source pin;
the agent is now unloaded and no provider I/O occurred. A fresh full-current read-only replay of 44
roots still verifies the three v6 roots as `verified_none`, with zero v6 attempts and 62 historical
attempts. The root/control records remain immutable.

Sol selected ancestor-plus-exact-bytes runtime binding for future releases: pin creation still
requires clean current HEAD; each callback requires that source commit to be an ancestor of HEAD,
the pinned paths clean, and exact byte equality among release-commit blobs, current-HEAD blobs, and
no-follow checkout reads, in addition to exact installed plist and launchd identity. A docs-only
descendant is accepted; source, manifest, plist, or process drift fails before intent. The old
`31cf...` release cannot resume under changed pinned host code. A fresh release/root and untouched
future anchor are required; only its first forward bucket plus controlled restart/no-replay replay
can qualify autonomous capture. Capture policy remains six selected pools per 15-minute bucket,
one forward-only request per slot, 12-second spacing, bounded request/byte/storage caps, a 16-GiB
resident ceiling, and a 50-GiB free-space floor. Market-source, training, and economic admission
remain false.

Before the first provider bucket, Sol selected a read-only population/coverage audit as the next
sensing horizon. The preregistration binds release `94da5c80bdd82308ebbad0c8f2e158b3`, its source,
policy, selector and 170-pool population pins, and starts at the immutable 13:15Z anchor. Its
scheduled-bucket denominator is durable plans plus durable gaps; the pool-opportunity denominator
is only the slots selected in durable plans, regardless of whether an intent, successful response,
or usable new-open bar follows. Durable gap buckets are unknown intervals with no pool identities
and no cursor advance; the following plan reuses the pre-gap cursor and pre-decision evidence. The
first rotation ends only after each pinned pool has appeared in at least one durable plan. Existing raw
bar classifications, lifecycle-age uncertainty and event/receipt/availability times remain
separate; misses, empty responses and disappearance never imply zero or death. Receipt:
[`v6 population-coverage preregistration`](evidence/gecko-v6-population-coverage-preregistration-2026-10-13.json).

A descriptor-bound read-only census independently replayed 41 known roots and 62 attempts, including
two unresolved response sizes. The conservative prior offset is 1,015,808 raw bytes and 5,079,040
physical bytes. The immutable sanitized census receipt is
[`v6 prior-usage census`](evidence/gecko-v6-prior-usage-census-2026-10-13T1045Z.json).

Sol's next independent source horizon is positive Gecko bar values from that pinned rotation. The
offline v1 extractor/projector consumes only events accepted by the v6 journal verifier and reads raw
bodies through a caller-supplied descriptor-bound reader. It retains plan-slot and gap denominators,
emits only timely, durably confirmed `eligible_new_open` values, and binds pool/base/
quote identity, raw-response hash, source/policy/selector/population pins, and separate event,
decision, request, receipt and availability times. Its process-contract hash also binds the exact
v3 raw-response parser implementation. Empty, missed, failed, ambiguous, late, malformed,
unclosed, out-of-window and overlap-revision cases remain denominators or parser dispositions, never
zero values or lifecycle evidence. Birth age is explicitly unknown absent independent birth evidence.
The projector is read-only and grants no coverage, completeness, source, training or economic
qualification. Its focused synthetic mutation tests pass. The first natural bucket produced four
confirmed response bodies (3,042 bytes), one permanently spent ambiguous request, and one missed
slot. Its 12 parsed bars were all outside the eligible interval, so it emitted zero eligible new
opens. A controlled bootout/bootstrap preserved the attempt-list and source-prefix hashes without
replay. The v6 launch agent was then unloaded because its fail-closed behavior stops after a
non-success terminal; a distinct forward-only successor with explicit cooldown/gap lineage is
required for sustainable collection. The immutable sanitized observation is
[`v6 first natural bucket`](evidence/gecko-v6-first-natural-bucket-2026-10-13T1315Z.json). The
preregistered 170-pool rotation remains incomplete and no source, support, lifecycle, training or
economic qualification follows from this one bucket.

## Forward-only GeckoTerminal pool discovery (2026-10-13)

Sol approved a separate provider-indexed Solana pool-discovery lane. It scans fixed pages 1–3 every
five minutes, in order at the scan anchor and +12/+24 seconds, with a 60-second scan expiry. Each
scheduled page has one attempt; unavailable capacity becomes a durable missed slot, and ambiguous
transport is never retried. The three-page discovery schedule uses at most 864 calls per UTC day.
The owner LaunchAgent wakes every 10 seconds, treats wakeups as hints rather than schedule state,
and starts at most one page request per callback. Early or shared-rate-blocked wakeups do not reserve
capacity; a priority/rate race after intent is terminalized as a no-dispatch miss. A read-only idle
path avoids full source and history replay when the next page or maintenance trigger is not due.
The discovery lane locally enforces at least 12 seconds between starts and no more than five starts
in any rolling 60-second window, and refuses to start while a v7 owner label is loaded. It is not
yet a shared cross-release rate arbiter, so concurrent v7/discovery collection is not qualified.
The public API currently
describes an approximately ten-call/minute limit and one-minute response caching; these are provider
guidance, not guarantees ([official API guide](https://api.geckoterminal.com/docs/index.html)).

Collection uses immutable 72-hour epochs, each with at most 2,592 requests, 1,358,954,496 raw
bytes, and 6,794,772,480 bytes of physical reservation. Every page reserves 512 KiB raw and
2.5 MiB physical. The owner may automatically renew unchanged policy indefinitely; day 45 is a
review alert, not an expiry. Lifetime discovery storage remains capped at 101,921,587,200 bytes
across all epochs, with alerts at 70%, 85%, and 95%; actual allocation and unresolved debt never
reset. Before each epoch end, the owner prepares a successor at T−1h. It cannot dispatch until the
old epoch is terminal, quiescent, independently replayed, and gaps, ambiguous debt, rate counters,
and storage accounting have transferred exactly once. The next epoch reserves only its uncommitted
runway above the 50-GiB free floor and all carried liabilities. No artifact is deleted or offloaded
to create capacity. Capacity exhaustion, a floor breach, provider STOP, source/ledger drift, or
failed handoff stops dispatch with explicit missed slots; poor yield and pending semantic qualification
do not. Changed source, endpoint, cadence, page set, caps, root, or provider entitlement requires a
new reviewed release. This diagnostic lane does not claim a complete pool census, verified on-chain
birth, point-in-time availability, inactivity, or lifecycle death.

Successor standby schema v3 records a typed predecessor frontier: the latest contiguous terminal
plan-summary or explicit-gap row at preparation, its artifact hash, and a canonical prefix hash.
At rollover, replay verifies that the recorded frontier is unchanged within the completed epoch
seal. A final gap or an all-gap epoch is valid and remains unknown; an incomplete plan is not a
frontier, and evidence after a missing interior scan fails closed. This permits no-traffic epochs
to renew without manufacturing a successful plan or treating missing observations as zero.

Within an epoch hold, the capacity ledger binds each page request ID to its one-based planned
ordinal and accepts only strictly increasing ordinals. Gaps are allowed so a durably recorded missed
slot does not block later scheduled pages, but a skipped ordinal can never be reserved later as a
backfill. The discovery schedule remains responsible for recording each terminal or explicit missed
slot and independently replaying the page prefix before dispatch; the global ledger does not impose
an order across unrelated provider lanes.

Enrollment is based on a syntactically valid Solana pool address before content or activity filters.
The provider's `pool_created_at` remains an assertion; first durable availability, request start,
receipt, scheduled time, and model decision time stay separate. Malformed page bodies, malformed
relationships, duplicate rows, page overlap/order changes, oversize responses, HTTP failures, and
missing slots retain explicit denominators and unknown exposure. Disappearance is not death. A
deterministic hash-sampling contract is retained for a later, separately leased follow-up stage, but
the current pilot's follow-up fraction is zero.

The discovery source-manifest v3 pins the descriptor-bound `live_writer.py` implementation and
external-writer schema alongside the collector, coordinator, storage root resolver, and LaunchAgent
template. These files are part of the write-authority closure and must be committed and clean before
release preparation can produce an activatable source identity. At callback time, the recorded
release commit must be an ancestor of current HEAD; each pinned path must be clean and byte-identical
across the release-commit blob, current-HEAD blob, and no-follow checkout read. This allows safe
descendant work outside the pinned closure while source drift or divergent history fails closed.

The discovery policy/replay kernel is offline-qualified. Coordinator-v3 migration is now published
on Kingston under descriptor-bound writes: v2 policy/ledger bytes and unresolved liabilities are
bridged, and the v3 pointer was durably created last under all v2 lane locks. The v2 source guard
rejects future mutations after the pointer while preserving read-only replay. The owner and unattended
epoch renewal remain unqualified; this versioned frontier contract has focused synthetic rollover
coverage. The stale, inert `runtime-v2` candidate stays immutable. A fresh release uses
`runtime-v3`, a v3 activation path, and a v3 LaunchAgent label/template; shared coordinator-v3 ledger,
capacity roots, locks, and liability accounting remain common and do not reset. The source-binding
verifier now has descendant, unpinned-dirty, pinned-drift, and divergent-history tests. Five forced-
recovery cases exercise the real v3 ledger with the descriptor writer:
reservation without intent, intent without dispatch, ambiguous dispatch, durable result settlement
exactly once, and root detach during a page write without underlay artifacts. These are synthetic
recovery tests, not a live Kingston recovery or provider request. A fresh source-bound release,
activation, first natural scan, and controlled LaunchAgent restart/replay remain pending. No provider
I/O has occurred under this discovery policy.

The separate read-only discovery-to-enrollment projector replays explicit contiguous plan/gap
prefixes against the pinned epoch history and exact raw page hashes. It retains page and scan
denominators, first durable pool availability, source row/page provenance, and conflicting identity
claims; duplicate appearances do not replace first-seen facts. Current output is diagnostic
`unassigned_observation` with protected exclusion unchecked, zero price follow-up, and no market,
lifecycle, economic, training, or evaluation claim. It does not read protected cohorts. Focused
synthetic mutation/reorder/raw-mismatch tests pass; one real prospective scan and authenticated
protected-exclusion membership are still required before provider-discovered enrollment can be
published as an eligible population.
