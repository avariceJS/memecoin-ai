# Evidence Index

Evidence is immutable and task-scoped. Do not read this directory wholesale. Historical receipts
and figures retain their original bytes. Reports/receipts can
contain large exact payloads, so open only the named artifact needed for a specific claim.

## Brain and operator

- Helius key current exposure delta rescan at 15:12Z:
  `helius-key-current-exposure-delta-rescan-2026-10-11T1512Z.json` (zero exact matches in all
  6,522 Git objects, 36,343 recent accessible files / 10.26 GB, current process arguments,
  launchd, or 621 MB of Unified Log; exact values were compared in memory only).
- Helius Cursor-state redaction and exposure rescan at 15:44Z:
  `helius-key-cursor-state-redaction-2026-10-11T1544Z.json` (57 exact-key copies redacted across
  three local SQLite databases; integrity passed, files restricted to mode 0600, and the subsequent
  49.78 GB recent-file scan found no exact key or endpoint match).
- Helius public WSS billing source capture:
  `helius-public-wss-billing-source-capture-2026-10-11.json` (public rate and credit consumption
  order only; account balance/overage, rounding scope and control/handshake billing remain unknown).
- Senses first-entry readiness v2:
  `senses-first-entry-readiness-v2-2026-10-11.json` (supersedes the v1 readiness view; inert
  signed-assertion contract, production claim disabled, activation false).
- Senses first-entry readiness v1:
  `senses-first-entry-readiness-v1-2026-10-11.json` (frozen, superseded and non-authoritative;
  its Helius MCP environment-presence classifier is stale/incorrect and must not be used as current
  process evidence. Use the v2 receipt and fresh runtime census instead.)
- Prospective Helius external-v2 terminal result:
  `brain-senses-external-v2-missed-bootstrap-terminal-2026-10-12.json` (plan/clock/launch/review
  hashes, verified predecessor, terminal `missed_bootstrap`, zero provider requests/reservations,
  disabled/unloaded label, and no-retry/no-backfill disposition).
- No-provider calendar timing rehearsal at 03:20Z:
  `brain-senses-external-timing-rehearsal-2026-10-12T0320Z.json` (one launchd callback, exit 1 on
  `ModuleNotFoundError`, no status result or provider I/O, activation disabled/unloaded).
- Helius/Alchemy read-only account snapshot at 03:24Z:
  `senses-provider-account-readonly-2026-10-12T0324Z.json` (Helius plan/usage unavailable without
  JWT; Alchemy team aggregate 18,680 CU / $0.01, with no limit or workload attribution).
- Agave bounded dependency fetch attempt:
  `agave-dependency-fetch-attempt-2026-10-12.json` (stopped by the local symlink guard after Cargo
  created four internal links in the pinned Crossbeam checkout; the partial cache is preserved and
  no retry/build occurred). Its `source_checkout_modified` field is corrected by
  `agave-dependency-fetch-attempt-source-correction-2026-10-12.json`: the pre-existing composite
  diff exactly matches the preflight snapshot; the fetch did not modify Agave source or lockfile.

- Alchemy Free/PAYG endpoint behavior, metered CU reconciliation and bounded errors: `alchemy-endpoint-qualification-2026-10-09.json` (no secrets; endpoint behavior only, no completeness claim).
- Alchemy MCP authenticated-account usage snapshot:
  `alchemy-account-usage-summary-mcp-2026-10-10.json` (18,680 CU / $0.01 month-to-date as of
  01:32Z; account-wide, not attributable to this checkout or a method; account limit not returned).
- Alchemy Account Archive account-access gate:
  `alchemy-account-archive-access-gate-mcp-2026-10-10.json` (the authenticated app exposes Solana
  mainnet and its Oct 8 method usage, but not research endpoint identity, archive entitlement,
  remaining limit, or account-specific archive coverage; zero archive RPCs).
- Helius MCP account status/plan check: `helius-account-status-mcp-2026-10-10.json` (key configured,
  but plan and credit usage unavailable from the account tool; no plan mutation or paid RPC made).
- Helius MCP read-only account status at 2026-10-12 02:24Z:
  `helius-account-status-mcp-2026-10-12T022418Z.json` (authenticated key configured, but no JWT;
  plan, quota, credit balance, and charge history remain unavailable; no signup or account mutation).
- Helius MCP read-only account plan at 2026-10-12 02:24Z:
  `helius-account-plan-mcp-2026-10-12T022457Z.json` (plan and eligibility remain unknown; no signup
  or account mutation).
- Helius credential process-list remediation:
  `helius-mcp-credential-process-list-remediation-2026-10-10.json` (corrects the c3 activation
  receipt's invalid process-argument check; five legacy key-displaying npm processes were stopped).
- Helius saved-transcript redaction correction:
  `helius-mcp-saved-transcript-redaction-correction-2026-10-10.json` (corrects the earlier
  incomplete saved-history scan; three credential occurrences in two old Cursor JSONL transcripts
  were redacted, files set to 0600, and JSONL validity retained).
- Helius local exact-key exposure scan:
  `helius-key-local-exposure-scan-2026-10-10.json` (zero matches in 5,130 Git objects, 8,786
  scanned local saved log/config files and current process arguments; expected private `.env.local`
  occurrence only; scope limitations and prior session-transcript exposure disclosed).
- Helius local exact-key exposure rescan after authorized continuation:
  `helius-key-local-exposure-rescan-2026-10-10.json` (zero matches in 5,165 Git objects, 10,308
  selected saved local text/config/log files, and process command lines; `.env.local` remains the
  sole mode-0600 occurrence; prior session transcript disclosure and remote-scope limits retained).
- Helius exact-key pre-resume exposure rescan:
  `helius-key-pre-resume-exposure-rescan-2026-10-10.json` (zero matches in 5,253 Git objects,
  253,570 saved text/runtime/evidence files, current process command lines, and 1.31 GB of recent
  Unified Log output; only the three expected mode-0600 private configurations match).
- Helius exact-key post-redaction rescan:
  `helius-key-local-exposure-rescan-post-redaction-2026-10-10.json` (zero matches in 5,238 Git
  objects, 249,759 accessible saved text/runtime/evidence files, current process command lines, and
  1.285 GB of recent Unified Log output; the three expected private mode-0600 configs remain the
  only local exact-value matches).
- Helius exposure rescan before resumed campaign work:
  `helius-key-resume-exposure-rescan-2026-10-10.json` (zero matches in 5,334 Git objects, active
  process arguments, and 199 MB of recent Unified Log output; a complete checkout plus full
  accessible saved-root scan found credential components only in the three expected mode-0600
  private configs).
- Helius follow-up exposure scan at 19:04Z: `helius-key-followup-exposure-rescan-2026-10-10T1904Z.json` (zero exact endpoint/path/hostname matches in Git blobs, process arguments and recent Unified Log; two Codex history files contain only the public hostname).
- Helius service v1 operational review and post-resume credential scan:
  `helius-service-v1-operational-review-2026-10-10.json` (source/plist hash-bound offline review,
  zstd launchd/pre-reservation fix and Sol acceptance; zero current-key matches in Git, process
  arguments, recent log/text files and 672.6 MB of Unified Log; no activation or provider request).
- Helius credential rescan after the resumed c4 frame:
  `helius-key-post-resume-c4-log-rescan-2026-10-10T2218Z.json` (mode-0600 private config; zero
  current endpoint/key-path matches in 1,459 active campaign files, process/LaunchAgent arguments,
  and 55.5 MB of recent Unified Log; no credential value stored in the receipt).
- Helius post-tool-output local exposure rescan:
  `helius-key-post-tool-output-rescan-2026-10-10T2318Z.json` (zero exact matches in every Git
  object, 177 recent local saved files / 1.05 GB, process and LaunchAgent arguments, and 82.2 MB
  of Unified Log; three expected private configs remain mode 0600. The receipt records the separate
  active-session tool-output disclosure and local/remote scan limits without storing the value).
- Helius post-resume delta exposure scan:
  `helius-key-post-resume-exposure-scan-2026-10-11T0047Z.json` (zero exact matches in all 5,905
  local Git objects, 83 recently changed accessible saved files / 322 MB, process arguments, and
  284 MB of recent Unified Log; current private `.env.local` remains owner-only mode 0600. The
  pinned MCP configuration supplies credentials through environment configuration, while Senses
  launch entries remain path-only; remote services and backups are outside local visibility).
- Helius pre-commit post-resume scan:
  `helius-key-post-resume-exposure-scan-2026-10-11T0212Z.json` (zero exact matches in 5,961 local
  Git objects, 21,090 accessible recent files / 1.22 GB, current process arguments, and 1.25 GB of
  recent Unified Log; followed by the broader 02:18Z scan below).
- Helius key post-resume exact exposure scan:
  `helius-key-post-resume-exposure-scan-2026-10-11T0218Z.json` (zero exact matches in 5,961
  local Git objects, 75,127 accessible recent files / 1.26 GB, current process arguments, and
  1.26 GB of recent Unified Log; `.env.local` remains owner-only mode 0600. Provider/remote
  storage, inaccessible backups and one unreadable file are outside the complete scan scope).
- Helius key exposure delta check at 04:35Z:
  `helius-key-exposure-delta-2026-10-11T0435Z.json` (zero exact matches in 6,055 local Git
  objects, current process arguments, 124 accessible files changed since 02:19Z / 403.9 MB, and
  the subsequent Unified Log window; `.env.local` remains mode 0600. Unreadable files, remote
  storage and backups remain outside the local scan).
- Helius launchd exposure check at 04:39Z:
  `helius-key-launchd-exposure-check-2026-10-11T0439Z.json` (zero exact matches in 65 user/system
  launchd property lists and current process arguments; no Senses/Helius/inventory labels loaded).
- Helius key continuation exposure rescan at 05:12Z:
  `helius-key-continuation-exposure-rescan-2026-10-11T0512Z.json` (zero exact matches in 6,123 local
  Git objects, current process arguments, 65 launchd property lists, 7,384 recently changed
  accessible files, and 934.8 MB of subsequent Unified Log; provider/remote storage, inaccessible
  files and backups remain outside the scan).
- Helius key broad exposure rescan at 05:26Z:
  `helius-key-broad-exposure-rescan-2026-10-11T0526Z.json` (zero exact matches in 6,146 local Git
  objects, current process arguments, 65 launchd property lists, 14,449 recently changed files /
  390.8 MB, all 26,650 files / 4.66 GB under `~/.npm`, shell history/startup files, and 874.0 MB of
  subsequent Unified Log; remote/provider storage, inaccessible files and backups remain outside).
- Helius credential delta scan at 07:51Z:
  `helius-key-exposure-delta-rescan-2026-10-11T0751Z.json` (zero exact matches in 6,267 local Git
  objects, current process arguments, 117,006 files modified within the three-hour delta / 1.16 GB,
  and 1.66 GB of subsequent Unified Log. Current `.env.local` remains mode 0600; inaccessible,
  remote/provider, backup, and Time Machine storage remain outside local visibility).
- Helius MCP credential transport remediation and follow-up scan:
  `helius-key-exposure-delta-rescan-2026-10-11T0824Z.json` (removed literal key values from Codex
  and Cursor MCP configs; path-only wrapper reads `.env.local`, passes the key through child
  environment, pins `helius-mcp@2.2.0`, and passed an argv-isolation probe; 22 stale processes
  stopped; zero exact matches in 6,303 Git objects, current process args, 1,100 recent files / 480
  MB, or 78.5 MB of recent Unified Log. MCP transport remains closed; no RPC occurred.)
- Helius stale legacy MCP process stop and exact-value rescan:
  `helius-stale-latest-process-stop-rescan-2026-10-11T1043Z.json` (stopped the old `@latest`
  process that exposed the key in argv; zero matches in 6,385 Git objects, 1,073 recent accessible
  files / 509 MB, current argv, and 1.31 GB of Unified Log. The current session transcript still
  contains the process-list result; remote and backup storage remain outside scope.)
- Helius post-resume exact-value exposure rescan at 11:48Z:
  `helius-key-post-resume-exposure-scan-2026-10-11T1148Z.json` (stopped two unexpected legacy
  `@latest` processes; zero matches in 6,412 Git objects, 2,292 recent files / 533.6 MB, 65
  launchd plists, current argv, and 295.5 MB of Unified Log. Credential values were not persisted;
  remote, inaccessible, backup, and Time Machine storage remain outside scan scope.)
- Helius post-resume exact-value exposure rescan at 12:41Z:
  `helius-key-post-resume-exposure-rescan-2026-10-11T1241Z.json` (zero matches in 6,416 reachable
  Git objects, 27 recent Codex logs / 500.4 MB, 82 recent repository files / 2.8 MB, 57 launchd
  plists, current argv, or 846.7 MB of Unified Log. Remote, inaccessible, backup, Time Machine, and
  prior session-transcript storage remain outside scan visibility.)
- Helius post-stop exact-value exposure rescan at 13:37Z:
  `helius-key-post-legacy-mcp-stop-exposure-rescan-2026-10-11T1337Z.json` (stopped a stale legacy
  `@latest` process that exposed a credential-bearing argv assignment; zero exact matches in 6,449
  reachable Git objects, 48 recent accessible files / 19.2 MB, current argv, launchd state, and
  1.98 GB of Unified Log. Remote, inaccessible, backup, Time Machine, and prior session-transcript
  storage remain outside scan visibility.)
- Agave v3.0.0 Token-2022/WSOL parser-only scaffold v3:
  [`agave-token-raw-parser-scaffold-corrected-v3-2026-10-11.json`](agave-token-raw-parser-scaffold-corrected-v3-2026-10-11.json)
  and exact source patch
  [`agave-token-raw-parser-scaffold-corrected-v3-2026-10-11.patch`](agave-token-raw-parser-scaffold-corrected-v3-2026-10-11.patch).
  Accepted for unsigned synthetic byte parsing/mapping only; hook effects/order, runtime fee law,
  Rent/epoch binding, trace/witness, and economic admission remain unqualified.
- Helius account-status retry at 06:51Z returned `Transport closed`. Public billing materials checked
  2026-10-11 conflict on archival `getBlock`/`getBlocks` units; see `docs/brain-source-architecture.md`
  for official source links, the 10-credit offline scenario, unknown account controls, and conditions
  for reopening schedule planning. No RPC or lease activation followed.
- `alchemy-usage-summary-mcp-2026-10-11T0727Z.json`: read-only authenticated-team MCP aggregate
  reported 18,680 compute units / $0.01 for month-to-date, last 7 days, and last 30 days through
  07:27Z. It has no repository/app attribution, no returned account limit, and is not an invoice or
  raw RPC capture; it does not establish available headroom or authorize requests.
- `alchemy-usage-summary-mcp-2026-10-11T0947Z.json`: read-only follow-up still reports 18,680 CU /
  $0.01 through 09:47Z. The account-specific usage limit remains unreturned; no Alchemy RPC was
  issued by this snapshot.
- `alchemy-usage-summary-mcp-2026-10-12T0223Z.json`: read-only authenticated-team follow-up still
  reports 18,680 CU / $0.01 through 02:23Z. No app/network/method attribution or account usage
  limit was returned; this does not meter or attribute the finite Helius lease.
- Frozen c4 selected-block structural replay:
  `pumpswap-c4-captured-class-replay-2026-10-11.json` (independently pinned metadata freeze and
  source-bound offline replay of 10 frames / 160 selected blocks; excludes unopened 23:00 body and
  makes no global completeness, economic exact-zero, training or execution claim).
- `pumpswap-c4-structural-novelty-v1-2026-10-11.json`: reproducible offline novelty scan of the
  same ten included c4 frames. Reconciles 173,899 transactions, 22,319 PumpSwap instruction
  references, per-slot structural outcomes and buyExact census. The 23:00Z tail2 body remains
  excluded. Exact class/block bindings are zstd-compressed under ignored `work/` and hash-bound by
  the compact receipt; fingerprints are not protocol-layout counts or economic facts.
- `pumpswap-c4-idl-unrecognized-discriminators-v1-2026-10-11.json`: immutable 16-value / 11,623-reference inventory used as the input denominator for the historical IDL and event-CPI source qualification.
- `pumpswap-c4-idl-history-qualification-v3-2026-10-11.json`: source-first comparison against 18
  historical official Pump AMM IDL blobs and pinned Anchor event-CPI source. Fifteen names are
  discriminator/name identities only. All 9,598 generic event-tag references in the included c4
  blocks pass exact nested-event discriminator, one-account PDA identity, and active-stack direct
  Pump AMM parent checks, with transaction success/failure and checked/failed/unresolved denominators
  preserved. No payload decode or economic admission; c4 tail2 remains unopened. This is the final
  Sol-accepted receipt hash-bound to the corrected stack implementation and sibling counterexample.
- `pumpswap-c4-event-layout-feasibility-validation-v1-2026-10-11.json`: offline full-byte Borsh feasibility replay for the 9,130 BuyEvent/SellEvent payloads in the ten included frames against all 18 pinned IDL layouts; source-bound to Anchor/Borsh serialization sources and generated parser tests. Reports IDL-consistent development observations only; no deployed layout, event validity, economic admission, completeness or activation claim. Other 468 event references and tail2 remain excluded.
- `pumpswap-buy-exact-v1-c4-pool-v2-development-replay-2026-10-11.json`: source-bound development
  replay of all ten included c4 frames through the existing SDK 2.1.0 structural qualifier and
  pool-v2 diagnostic. It binds all full-block capture hashes and report/epoch receipts, preserves
  16 structural mismatches, classifies 767 failed attempts, 1,546 Token-2022 quarantines and four
  censored legacy cases; it does not open 23:00Z, establish an exact economic zero or grant audit/RPC
  authority.
- `pumpswap-buy-exact-v1-c4-evidence-feasibility-2026-10-11.json`: transaction-tree descendant
  event/transfer wire facts, whole-transaction target-account touches and endpoints, and the four
  legacy censored traces over the same 2,333 development candidates / 160 blocks. The complete
  row-level taxonomy is zstd-compressed under ignored `work/` and bound by source/tool/output hashes;
  no bank-time Token-2022 extension state or economic admission is claimed.
- `pumpswap-buy-exact-v1-c4-economic-identifiability-validation-v1-2026-10-11.json`: joins the
  hash-pinned 2,333 taxonomy identities to their ten-frame full-block evidence, adding transaction-
  wide fee/lamport metadata, ordered target-account wire facts and explicit settlement missingness.
  Six focused tests and offline replay pass; this defines no economic admission or zero claim.
- `pumpswap-buy-exact-v1-position-state-source-feasibility-v1-2026-10-11.json`: classifies all
  2,333 accepted row-ledger identities against byte-pinned official RPC, Token-2022, and Agave
  Geyser source snapshots. It separates exact failed-attempt transaction dispositions from
  candidate-position state, which is not applicable to those failures; captured documented sources
  lack position state for 1,550 successful structural passes, while three successful mismatches
  remain unknown. A property-based integer-unit counterexample demonstrates that transaction
  endpoints do not identify an intermediate instruction state. No c4 response body, RPC, admission,
  or audit was used.
- `solana-candidate-position-witness-synthetic-checker-validation-v1-2026-10-11.json`: binds the
  Sol-reviewed Agave v3.0.0 source-feasibility contract, narrow core inert synthetic checker,
  54-test suite and source hashes. Sol accepted core consistency only; economic admission,
  runtime/source qualification, and provider completeness are always false. Token-2022 fee/epoch/
  withheld/hook binding and WSOL account/rent/frame-path binding remain open fixture gates. No
  validator, RPC, replay, c4 body, audit, or economic admission was used.
- `solana-candidate-position-witness-transition-consistency-v1-2026-10-11.json`: binds the
  Sol-accepted synthetic Token-2022 and WSOL transition-consistency checks and 63 focused tests.
  This validates supplied transition claims only; raw decoding, fee/Rent derivation, runtime
  instrumentation and economic/source admission remain false.
- `agave-v3.0.0-candidate-position-boundary-capture-feasibility-v1-2026-10-11.json`: pins a
  disposable Agave v3.0.0 patch, toolchain, lockfile, isolated ProgramTest success/error captures,
  and 16 focused passes, including the same InvokeContext regression cases with the feature disabled.
  Sol accepted only local hook-location/raw-boundary feasibility;
  the exact patch is retained alongside the receipt. It does not qualify a transaction-scoped
  witness, finality, real-source replay, completeness, or economics.
- [`Agave v3 observer v5 review`](agave-v3.0.0-candidate-position-observer-v5-review-2026-10-11.json):
  Sol accepted a hash-bound, transaction-scoped V5 observer for local synthetic ProgramTest
  feasibility only (11 feature-on CPI tests; feature-off package check). This closes the recorded
  boundary identity, phase snapshot, and frame instruction-binding defects. It does not qualify
  production integration, historical replay, Bank persistence/finality, real-source completeness,
  or economic admission. Exact patch/captures and logs remain in ignored `work/`.
- `brain-senses-build-storage-guard-apfs-qualification-2026-10-12.json`: source-hash-bound review
  and disposable case-sensitive APFS detach tests for the Cargo/Git storage supervisor. Five
  post-detach path attempts fail closed with `ENOENT`, the underlay stays absent, remount UUID
  matches, and no supervised process remains. Capacity thresholds are test-only; ownership is
  disabled on the disposable image; this does not authorize a large Agave build or sandbox arbitrary
  build scripts.
- [`brain-senses-build-storage-guard-apfs-qualification-2026-10-12-v2.json`](brain-senses-build-storage-guard-apfs-qualification-2026-10-12-v2.json): reruns the five forced-detach write cases against the revised source-symlink policy; all attempts return `ENOENT`, with an unchanged manifest, matching remount UUID, and no process-group survivor. Existing and intentionally dangling internal source links are covered by the focused unit suite.
- [`brain-senses-build-storage-guard-apfs-qualification-2026-10-12-v3.json`](brain-senses-build-storage-guard-apfs-qualification-2026-10-12-v3.json): repeats forced-detach cases after adding an opt-in allowance for internal symlinks under Cargo's pinned Git checkout tree; all five writes fail with `ENOENT`, the underlay manifest and remount UUID match, and no process-group survivor or scratch artifact remains. Tests reject escapes and preserve default rejection outside the opt-in scope.
- [`brain-senses-build-storage-guard-apfs-qualification-2026-10-12-v4.json`](brain-senses-build-storage-guard-apfs-qualification-2026-10-12-v4.json): reruns the same five forced-detach cases after narrowing Cargo's opt-in to relative links whose targets stay inside one exact repo/revision tree. Cross-revision, cross-repository, absolute and symlinked structural paths are rejected; all post-detach writes fail with `ENOENT`, the manifest matches, and the image is removed.
- [`agave-dependency-fetch-preflight-2026-10-12-v2.json`](agave-dependency-fetch-preflight-2026-10-12-v2.json): current wrapper/guard hashes passed `--check-only` against separate v2 Cargo/target/tmp roots on Kingston. No network or provider requests occurred. This is preflight evidence only, not approval or execution of another fetch.
- `agave-dependency-fetch-attempt-2026-10-12.json` (with its source-state correction receipt): the single approved locked Cargo fetch stopped on four upstream internal Crossbeam symlinks. Its partial cache remains preserved; no retry, offline metadata, or build has run.
- [`agave-dependency-fetch-attempt-v2-2026-10-12.json`](agave-dependency-fetch-attempt-v2-2026-10-12.json): the separately approved single fetch through the narrowed guard exited 101 after ~16 seconds. The partial v2 cache is preserved; its cause is unknown because Cargo stdout/stderr were discarded. Source and lock pins are unchanged, target/tmp are empty, and no process remains. No retry or offline metadata followed.
- `pumpswap-buy-exact-v1-alchemy-account-archive-feasibility-v1-2026-10-11.json`: separately
  screens the same 2,333 identities against byte-pinned Alchemy Archive and simulation docs. It
  distinguishes slot-boundary bytes from transaction/instruction-position state; 2,330 exceed one
  request containing the entire same-slot prefix plus candidate, without implying every predecessor
  is causally necessary or that other replay paths are impossible. It records lower-bound account×slot
  sizing and read-only usage evidence. No Alchemy chain RPC, c4 body, economic admission, or audit was
  used.
- `helius-inventory-v3-c4-abnormal-stop-sizing-2026-10-11.json`: offline sizing over the 11 sealed
  c4 frame summaries, STOP accounting, allocated campaign bytes, and post-STOP `df` snapshot. It
  records finite-sample raw/stored throughput and limited headroom above the prior floor-plus-frame
  gate; it does not establish a long-term disk trend or authorize a successor.
- `helius-inventory-v3-c4-abnormal-stop-accounting-2026-10-11.json`: separates frame-only, bootstrap-
  only and campaign all-in request/raw/stored byte denominators, bound to the 11 immutable frame
  summaries, bootstrap receipts and c4 STOP.
- `helius-inventory-v3-c4-abnormal-terminal-2026-10-11.json`: metadata-only c4 terminal proof,
  verifier-replayed after publication; binds the 11-frame/bootstrap denominators, two process/
  LaunchAgent snapshots, and corrected 71-minute local Unified Log window. It grants no successor
  authorization and does not qualify response bodies or provider-side silence.
- `senses-initial-entry-discontinuity-v1-c4-2026-10-11.json`: offline certificate derived from
  the pinned abnormal-terminal receipt and metadata-only verifier. It preserves c4's complete
  preexisting gaps and unknown-ended STOP discontinuity as a distinct predecessor digest, not a
  normal v3 handoff; a matching lease proposal remains inert and activation is false.
- `senses-initial-entry-discontinuity-v1-offline-validation-2026-10-11.json`: source-bound
  validation of the independent certificate/proposal verifier (21 focused tests, Ruff, format,
  compile, diff and verifier CLI); opaque c4 bodies and the frozen tail2 body were not accessed.
- `senses-initial-entry-calendar-compiler-v1-offline-validation-2026-10-11.json`: source-bound
  offline compilation and independent verification of an inert finite-calendar-v3 candidate from
  the initial-entry certificate (36 focused cases, including synthetic anchor + 34-request frame,
  exact gap/unknown transfer, mutation and crash/no-replay checks). No candidate schedule, lease,
  provider request, or activation was created.
- `senses-initial-entry-activation-preflight-v1-offline-validation-2026-10-11.json`: source-bound
  offline eligibility check for the typed c4 discontinuity predecessor, exact candidate/compiler
  binding, future-anchor rule, signed storage/charge/review evidence and domain-separated one-use
  claim. Sol accepted this eligibility-only scope; 44 focused tests passed. No provider request,
  activation, or cross-host exclusion is claimed.
- `senses-calendar-supervisor-v1-process-restart-validation-2026-10-11.json`: Sol-accepted
  synthetic subprocess recovery check for durable capture verification, abrupt exit after a
  started-but-undispatched child, terminal no-replay recovery and unknown-interval preservation.
  Twenty supervisor tests pass; no provider RPC, LaunchAgent or live scheduler is qualified.
- `helius-c4-launchd-quiescence-2026-10-11.json`: records all 15 installed Senses LaunchAgents
  persistently disabled and unloaded, the exact c4 override, a credential-free process-argument
  scan, and contemporaneous free disk below the 50 GiB floor.
- `senses-wss-offline-operational-review-2026-10-11.json`: source-hash-bound synthetic validation
  (126 tests) and local WSS service inventory; no live provider connection, account billing evidence,
  or activation is claimed.
- `senses-operational-readiness-inventory-v1-2026-10-11.json`: fresh host-only APFS/LaunchAgent and
  campaign-root metadata inventory. All 15 installed Senses labels were disabled and unloaded; the
  eligible internal volume had 29,293,846,528 free bytes, below both the 50-GiB floor and the
  61,834,628,800-byte c5 minimum. Account plan/credits/overage/WSS charge class remain unknown;
  no API calls, cleanup, moves, runtime changes, or activation occurred.
- [`Senses storage recovery feasibility`](senses-storage-recovery-feasibility-v1-2026-10-11.json):
  fresh read-only internal APFS capacity/candidate audit and a disposable Kingston ExFAT probe.
  The current internal volume remains below the WSS floor, and Kingston cannot satisfy the current
  capture contract (`noowners`; requested 0600 file observed as 0700). The temporary probe was
  removed; no existing data was moved or changed, and no service was activated.
- [`Senses storage recovery feasibility`](senses-storage-recovery-feasibility-v1-2026-10-11.json):
  read-only APFS recovery plan with current df/container cross-check, large-path classifications,
  rebuildable-cache upper bound, and a no-action dry run. It finds insufficient eligible capacity;
  it authorizes no cleanup or movement.
- `senses-prospective-verifier-v1-2026-10-11.json`: source-bound offline validation of the
  independent read-only controller lease verifier, including durable crash prefixes, no-replay
  recommendations, STOP marker/ledger crash ordering, cap/floor checks, terminal transfer, and
  rejection of abnormal predecessors; no service activation or RPC is authorized.
- `senses-prospective-controller-v1-recovery-qualification-2026-10-11.json`: follow-up validation
  requires a captured seal or elapsed gap for every bucket before normal terminal close, preserves
  early-cancelled buckets separately from gaps, repairs failed-result STOP crash prefixes without
  replay, independently verifies a supplied predecessor's ledger/head, and rejects backdated STOPs
  or seals before capture availability; 52 focused tests, no activation or provider requests.
- `senses-operation-envelope-v2-child-v3-offline-validation-2026-10-11.json`: Sol-accepted offline
  parent envelope and v3 child-bundle validation, including provider/source identity, bounded zstd,
  receipt versus fsynced availability, frontier continuity, STOP precedence and no-replay recovery;
  fake captures only, no live RPC or activation authority.
- `senses-operation-envelope-v2-child-v3-independent-qualification-2026-10-11.json`: independent
  verifier qualification of child structural summaries, selected block payloads, JSON-RPC success
  envelopes and capture-to-result-journal metadata, STOP/summary causal times (including unbundled
  and unsealed children), malformed/orphan prefix rejection, immediate artifact validation, sparse
  selections and STOP-only recovery. The bundle charge fields remain unknown/unverified; no cost is
  inferred. 84 focused verifier cases / 110 combined tests. Synthetic fake-client evidence only;
  Sol accepted the bounded qualification. Prior transitive eligibility receipts are invalidated until
  requalified.
- `senses-dependent-gates-requalification-2026-10-11.json`: aggregate offline requalification of
  finite-calendar/operation/child, initial-entry certificate/candidate, supervisor and synthetic
  signed activation-preflight gates after the v2 verifier hash changed. 239 tests passed in order;
  current code pins, no-replay/mutation cases and `activation_authorized=false` were checked using
  disposable fixtures. Existing receipts remain immutable and are linked by digest; no RPC or live
  activation is claimed.
- `senses-storage-readiness-2026-10-11T065300Z.json`: timestamped, hashed read-only `df`, APFS,
  volume identity, work/Docker/Steam allocation and local-snapshot inventory for the intended
  capture root at 06:58Z. Free space then was ~19.06 GiB, 30.94 GiB below the reviewed 50 GiB floor;
  `diskutil`'s zero `FreeSpace` field is recorded as inconsistent, not used for gating. No lease
  schedule is selected, no Helius headroom is established, and no cleanup or RPC occurred. Raw
  command captures are under the ignored mode-0700 `work/brain-senses-host-readiness-20261011T065300Z/`
  tree, with each artifact mode 0600.
- `senses-storage-readiness-2026-10-11T071800Z.json`: fresh read-only internal APFS/df/volume,
  local-snapshot, disk inventory, and launchd snapshot after the c4 novelty replay. Internal free
  space was 40,637,412 KiB (~38.76 GiB), still below the 50 GiB floor; no cleanup or activation.
  Hashed raw outputs are under ignored mode-0700 `work/brain-senses-host-readiness-20261011T071800Z/`.
- `senses-external-volume-candidate-2026-10-11T072000Z.json`: read-only metadata/free-space check of
  the mounted external Kingston ExFAT volume (239.8 GB total, 164.6 GB free). No contents were
  enumerated and no writes occurred. Sol ruled it ineligible under the current durable collector
  contract until separately authorized disposable qualification proves permissions, create-only
  sealing, fsync, locking, mount identity, and unplug/remount recovery; see the canonical source
  document and [Apple's external-volume ownership note](https://support.apple.com/en-il/guide/mac-help/mchlp1204/mac).
  Hashed command captures are under ignored mode-0700 `work/brain-senses-volume-candidate-20261011T072000Z/`.
- `senses-finite-calendar-v3-offline-validation-2026-10-11.json`: Sol-accepted caller-scheduled
  lifecycle and handoff around the operation envelope; binds gaps, next-start frontier, unknown
  interval lineage, conservative usage and no-replay terminal states. The 82 focused tests use fake
  clients only; no RPC, live activation, reviewer authentication, or predecessor-chain activation
  proof is claimed.
- `senses-finite-calendar-v3-gap-anchor-followup-2026-10-11.json`: source-bound missed-anchor
  recovery check; an active frame waits until its end, becomes a gap, and leaves the later anchor
  eligible without dispatch. The calendar/bridge/child suite passes 83 focused cases; no RPC.
- `senses-calendar-supervisor-v1-offline-validation-2026-10-11.json`: source-bound one-shot wrapper
  over an already initialized finite-calendar-v3 lease, operation-envelope-v2 and child-v3. It
  verifies prefixes, serializes decisions with the calendar owner lock, exposes stale-checkable
  read-only status; offline qualification used synthetic clients (19 focused tests / 76 combined
  cases), with no RPC or activation. The Python API accepts caller-injected clients; live injection,
  initial entry and discontinuity remain separate review gates.
- Finite Helius campaign-controller launchd installation and first campaign, still idle before its
  first scheduled RPC: `helius-inventory-controller-install-2026-10-10.json`.
- One-use pre-anchor setup recovery for the first v3 campaign's zero-I/O zstd preflight failure:
  `helius-inventory-v3-preanchor-recovery-2026-10-10.json` (preserves both predecessor STOPs,
  proves the sole empty attempt directory, binds the fixed zstd executable, and authorizes one
  fresh-root replacement without extending the original lease or budgets).
- Campaign-2 missed-gap anchor-only STOP:
  `helius-inventory-v3-campaign2-missed-gap-2026-10-10.json` (one parsed finalized `getSlot`, no
  inventory/block requests or backfill, reconciled request/byte counters, and the unchanged
  controller's expected strict rejection; no successor was created).
- One-use campaign-3 anchor-adoption preparation and LaunchAgent activation:
  `helius-inventory-v3-c3-handoff-activation-2026-10-10.json` (receipt-bound c2 reconciliation,
  forward-only seed, inherited gaps, finite caps, disk/lease preflight, and scheduled-but-not-yet-run
  state at seal time).
- Campaign-3 abnormal STOP and c4 predecessor binding:
  `helius-inventory-v3-c3-abnormal-stop-2026-10-10.json` (the failed 11:00Z anchor remains one
  potentially charged request; c3 is terminal, with no retry or backfill).
- Campaign-4 one-use recovery lease activation:
  `helius-inventory-v3-c4-activation-2026-10-10.json` (fresh 12:00Z bootstrap bucket, disabled
  predecessor jobs, key-free LaunchAgent arguments, and verified zero I/O at activation).
- Campaign-4 interim sizing after ten natural frames:
  `helius-inventory-v3-c4-midcampaign-sizing-2026-10-10T2222Z.json` (source-hashed frame tails,
  341 request methods, raw/stored bytes, per-method latency, current free disk and a clearly limited
  167-frame mean projection; credits remain unknown and this does not change the 24-frame lease or
  authorize service v1).
- Campaign-4 scheduled bootstrap:
  `helius-inventory-v3-c4-bootstrap-2026-10-10.json` (one finalized anchor request, explicit
  uncovered interval with no backfill, byte/request reconciliation, unchanged c3 STOP, and the
  next forward-only scheduled frame boundary).
- Campaign-3 first prospective frame:
  `helius-inventory-v3-c3-first-frame-2026-10-10.json` (34/34 captured requests, exact raw/stored
  reconciliation, a new finalized anchor, 16/16 selected-block structural checks, and retained gaps;
  no global discovery or economic-zero claim).
- Campaign-3 second prospective frame:
  `helius-inventory-v3-c3-frame2-2026-10-10.json` (forward-adjacent interval, 34/34 reconciled
  captures, exact raw/stored accounting, 16/16 checks, no missing inventory positions in this
  provider response, and no backfill/retry).
- Campaign-3 first-frame sell23 replay:
  `helius-inventory-v3-c3-sell23-census-2026-10-10.json` (80/80 eligible structural candidates
  matched offline; 10 diagnostic exact ledgers, 70 censored native-close outcomes, zero RPCs).
- Campaign-3 six-frame sell23 ledger replay:
  `pumpswap-sell23-c3-six-frame-ledger-replay-2026-10-10.json` (frozen ledger v2 replay over 96
  completed selected blocks, per-frame and per-pool exact/censored/failed accounting, input/code
  hashes, and zero RPCs).
- Campaign-3 sell23 native-close feasibility:
  `pumpswap-sell23-c3-native-close-feasibility-2026-10-10.json` (485 censored rows replayed against
  the six-frame source hashes; row-level lamport/token endpoint, create/fund/sync/close and sibling
  instruction traces; no rent reserve inference, economic admission, or RPC).
- Campaign-3 sell23 native-account conservation gate:
  `pumpswap-sell23-c3-native-account-flow-2026-10-10.json` (independent replay of all 96 frozen
  c3 blocks and 485 censored rows; ordered account-touch checks and lamport equations pass 485/485;
  destinations remain ambiguous; no rent, wallet proceeds, admission, or RPC).
- Campaign-3 sell23 process-level field qualification: immutable source freeze and final receipt at
  `work/brain-senses-sell23-process-qualification-v1/` (96 c3 full blocks; 485 censored rows joined;
  0 exact, 485 ambiguous, 0 unsupported; destination ownership and rent remain unresolved).
- Closed frozen Helius inventory-v2 pilot:
  `helius-inventory-v2-final-2026-10-10.json` (104 requests, reconciled raw/stored bytes, final
  anchor and inventory interval, and the inherited 00:00Z oversized-frame gap).
- Helius public method-credit reference:
  `helius-public-credit-cost-reference-mcp-2026-10-10.json` (generic published rates estimate 34
  credits per complete 34-request frame; it is not account-meter evidence).
- One bounded PAYG `getSignaturesForAddress` page and MCP pagination/accounting limits:
  `alchemy-signature-page-probe-2026-10-09.json` (one parsed 10-row page, no raw response, no
  coverage or zero-activity claim; subsequent ambiguous attempts are disclosed in the receipt).
- Two sequential PAYG signature pages with local raw-result files, cursor reconciliation and hashes:
  `alchemy-signature-cursor-qualification-2026-10-09.json`; raw MCP text is in ignored `work/`, not
  Git. The delayed meter reconciled +80 CU. Their 20 returned refs replay into generic observations;
  no enrollment/support interval is inferred, and cursor lineage remains unverified. A snapshot
  produced from an earlier receipt-time-derived membership attempt is explicitly rejected.
- Two sequential direct Free-account HTTP signature pages with response bodies sealed before JSON
  parsing, exact cursor-hash lineage and the offline generic observation import:
  `alchemy-free-raw-cursor-qualification-2026-10-09.json`; raw bodies remain in ignored `work/` and
  account-level Free CU usage is not exposed by the current workspace meter.
- One Free-account direct HTTP request at the Solana RPC maximum page limit (1,000 rows), then
  raw-imported to the observation store with unknown support:
  `alchemy-free-max-page-qualification-2026-10-09.json`; this is response-size/capture qualification,
  not archive completeness or training evidence.
- Bounded PAYG behavior comparison: `alchemy-payg-read-only-performance-2026-10-09.json` records a
  selected-app 300-request series, cursor-paged signature behavior, latency/concurrency and the
  reconciled +12,680 CU meter delta for 570 `getSlot` plus 32 `getSignaturesForAddress` calls.
  PAYG MCP does not expose raw batch transport or a complete `getTransaction` body.
- Free `getTransaction` version/encoding and direct raw-body probes:
  `alchemy-free-transaction-qualification-2026-10-09.json`. Two complete version-1 transaction
  bodies are retained under ignored `work/`; a candidate was recognized but quarantined while its
  event schema and account/token paths remained unresolved.
- Follow-up Free `getTransaction` sample:
  `alchemy-free-transaction-variant-qualification-2026-10-09.json` records 16 complete JSONParsed
  bodies, 33 event payloads reconciled exactly after pinning the official Pump IDL update, and
  continued quarantine for remaining-account and Token-2022 semantics. The raw bodies stay in
  ignored `work/`; this does not establish event-process coverage or training eligibility.
- One finalized `getBlock` response was independently re-read from Alchemy Free and a public Solana
  RPC. Both complete responses had the same block identity, 1,132 transactions, and ordered
  flattened signer sequence (1,171 signatures). The sanitized receipt
  `alchemy-public-block-cross-provider-2026-10-09.json` retains response hashes and the ordered
  sequence hash. This checks one slot's response consistency only; it does not prove exhaustive RPC
  transaction lists, slot-range coverage, market-process coverage, or zero activity. Free CU delta
  was unavailable.
- Two adjacent finalized slots were reconciled across provider-reported block identities and
  transaction projections, then replayed through a v3 coordinate-native experience snapshot and
  shared training batch: `brain-native-slot-experience-2026-10-09.json`. One H=1 count (87 structural
  instruction references) matured; the next stayed pending. The two rows cannot form independent
  splits or support a fit. Provider agreement is bounded response consistency, not global ledger or
  market-process completeness; raw payloads and identities remain outside this receipt.
- Initial PumpSwap program-address watcher canary and provider discrepancy:
  `brain-senses-program-watch-2026-10-09.json`. Alchemy Free returned an empty address-index page;
  public RPC returned five nearby finalized references and a six-call capture sealed five full
  transactions. This uses an intermediate uncommitted collector revision, remains unknown-coverage
  evidence, and is not an economic-process qualification. The durable collector implementation is
  in commit `d68871a`; its ongoing run will produce separate commit-bound evidence.
- One no-retry Free bounded range capture requested one finalized block with explicit neighboring
  witnesses. The requested block was complete; the left witness failed at transport and lineage
  remains unresolved. Offline inventory publication and replica replay succeeded, but interval and
  provider completeness, market-process support, zero activity and CU cost were not established:
  `alchemy-free-block-range-2026-10-09.json`.
- Helius/public finalized block comparison across three separated ranges and 50 slots:
  `helius-public-block-provider-pilot-2026-10-09.json`. The 103 Helius calls are expected to cost
  103 standard credits (account meter unavailable); all slot sets, block identities, ordered
  signatures, and full/signatures-only lists agreed. Of 56,615 paired transactions, 120 differed
  only because Helius explicitly truncated `meta.logMessages`. Raw provider bodies and the corrected
  full comparison remain under ignored `work/`; this is a non-random qualification pilot, not broad
  completeness evidence. The follow-up sample is deliberately larger than this first pilot.
- Same-slot Helius Free/Alchemy Free calibration across 32 recent finalized slots:
  `alchemy-free-helius-same-slot-calibration-2026-10-09.json`. All 32 paired blocks qualified
  after fixing the valid empty-base58 instruction case; 38 transaction-tree differences were
  limited to Helius logs with an explicit `Log truncated` marker. At this denser sample, lossless
  zstd stored about 1.14 MB/provider/slot, so it supersedes the three-block compression estimate
  for sizing the next wave. Captures remain under ignored `work/`.
- First prospective 512-slot/hour Helius epoch with Alchemy same-slot pairs:
  `helius-alchemy-stratified-512-first-epoch-2026-10-09.json`. Helius qualified all 512 selected
  structural counts; Alchemy's range inventory failed once and remains incomplete. All 32 captured
  pairs matched block identity/signature order. Of 36,852 transaction pairs, 79 differences were
  log-only, and offline replay confirmed Helius's explicit truncation marker in each. Stored zstd
  was 576,376,420 bytes; anchor-to-summary qualification took about 23.5 minutes.
- Offline replay of the later 512-Helius / 64-Alchemy pair64 epoch:
  `helius-alchemy-pair64-offline-audit-2026-10-09.json`. All 64 selected pairs agree on block identity
  and ordered signatures. Of 80,467 paired transactions, 146 differ only in Helius log messages,
  and all 146 contain its explicit truncation marker. Alchemy inventory failed once; its unsampled
  slot coverage remains unknown. The receipt binds the ignored raw root and complete offline audit
  hash; it does not claim transaction discovery completeness or economic agreement.
- `pumpswap-structural-novelty-v1-2026-10-10.json`: commit-bound offline structural digest over
  Pair64's 512 selected Helius blocks and c3's six completed 16-block frames. It independently
  enumerates PumpSwap instructions, matches available Pair64/c3 census denominators, and records
  exact wire/path/privilege classes, status splits, block hashes and times. It makes no global
  discovery, settlement, economic, or admission claim.
- `senses-program-exposure-ledger-v1-pair64-c3-2026-10-10.json`: bounded offline census of all
  program IDs and outer/inner instruction references in those same 608 captured blocks, with
  transaction-status, message-version, resolved account-key source, request/response provenance,
  capture times, selected-slot denominators, and explicit cross-dataset overlaps. Completeness is
  limited to the sealed selected-block inputs; program exposure is not a market, pool, birth, trade,
  or economic fact.
- `pumpswap-create-pool-feasibility-v1-pair64-c3-2026-10-11.json`: commit-bound offline replay and
  independent raw-block re-enumeration of the fixed Pump AMM `create_pool` discriminator across
  those 608 selected blocks. Two successful Pair64 inner references remain unsupported by pinned
  historical profiles; the receipt makes no pool-birth, population, economic, settlement, or
  admission claim. The full stage and candidate result remain under ignored `work/` and are bound by
  hashes in the receipt.
- `pumpswap-create-pool-source-feasibility-v1-2026-10-11.json`: source-bound follow-up of only the
  two sealed Pair64 identities. Both instruction and nested event bytes exactly match two pinned
  official IDL snapshots, with independent parser agreement and Anchor event-CPI tag provenance.
  Deployed program identity and pool/mint/vault state remain unknown; this is not a birth or economic
  qualification.
- [`System/ATA selected-block census`](senses-system-ata-wire-census-v1-2026-10-11.json): binds the
  private replay of all System/ATA instruction wire references in those same seven sealed datasets,
  including denominator reconciliation, the 675 seven-account ATA tag-0/1 observations, and the
  bounded recovery of the two prior `create_pool` transactions. This is selected-sample wire-shape
  evidence only; it does not qualify account effects or deployed ATA behavior.
- `senses-program-exposure-projection-v1-pair64-c3-2026-10-11.json`: hash-bound archival v2
  event-only projection with 603 positive selected-slot counts and five census-only local zeros.
  It preserves native slot coordinates and separately records when this retrospective projection
  became locally available; it is not sparse/learning-ready, economically qualified, or complete.
- `senses-pump-wss-reference-payload-benchmark-2026-10-10.json`: reproducible selected-block estimate of standard Pump `logsSubscribe` application payload bytes; it excludes framing/TLS/provider overhead and makes no live-rate or completeness claim.
- Sell23 c4 audit v2 metadata-only preflight:
  `pumpswap-sell23-c4-audit-v2-metadata-preflight-2026-10-10.json` (confirms unchanged first four
  frozen frames and canonical schedule binding; records the prior pre-case hash-format failure;
  opened no transaction cases and made no provider requests).
- One-shot corrected sell23 c4 native-flow audit:
  `pumpswap-sell23-c4-native-flow-audit-v2-2026-10-10.json` (first four frozen frames, all raw
  denominators reconciled, 341/342 native-close source-account flows exact, one unsupported touch
  quarantined; no wallet-net, rent, destination attribution, or admission claim).
- Sell23 audit bounded-search scope deviation:
  `sell23-c4-bounded-search-scope-deviation-2026-10-10.json` (records a truncated accidental
  emission of the 13:00Z buyExact locator, its exact artifact hash and command digest, and Sol's
  scope-limited determination that it did not expose or affect the 14:00–17:00Z sell23 audit cases).
- First real Helius-only inventory-first v2 frame after the scheduler binding migration:
  `helius-inventory-v2-first-frame-2026-10-09.json`. It records 34 successful requests, a 9,261-slot
  provider-returned inventory denominator, 16/16 selected structural self-checks and explicit raw /
  zstd accounting. The captured-class instruction references are not economic swaps; account-level
  charge units and global completeness remain unknown. Raw captures stay in ignored `work/`.
- The next real scheduler bucket sealed as an oversized gap:
  `helius-inventory-v2-oversized-gap-2026-10-10.json`. One `getSlot` anchor recorded a width above
  the pinned cap; no inventory or block body was requested, and the bucket must not be replayed.
- The 2026-10-10 01:00Z real v2 frame:
  `helius-inventory-v2-frame-2026-10-10T010000Z.json`. Its 34-request journal validates offline;
  all 16 selected full/signature pairs pass structural self-checks. The preceding oversized gap
  remains uncovered, and the captured structural references are not economic swaps.
- `helius-inventory-v2-selector-yield-census-2026-10-10.json`: source-bound offline replay of
  selector decisions and all 48 full/signature pairs from the three completed frames (30,269
  provider-returned members). Structural yield, selected sell23 pool concentration, receipt times,
  bytes, the 00:00Z gap and campaign-closure accounting are retained. It qualifies only a bounded
  structural sentinel, not market representativeness or economic activity.
- Frozen no-RPC Alchemy Account Archive mint-state control plan:
  `alchemy-account-archive-mint-control-plan-2026-10-10.json`. It pins one already captured
  Token-2022 development reference and at most three historical reads, but has not been run because
  research-runtime entitlement, endpoint, account limit and actual billing remain unverified. It
  does not qualify fee math or account settlement.
- Frozen PumpSwap sell23 full-transaction legacy-token ledger v2 qualification:
  `helius-pumpswap-sell23-ledger-v2-development-qualification-2026-10-09.json` and
  `helius-pumpswap-sell23-ledger-v2-audit-qualification-2026-10-09.json`. Fresh disjoint samples
  contain exact full settlements and explicitly censored native-close settlements; they are
  diagnostic only and do not qualify the full sell population or establish market completeness.
- Frozen v2 replay of all 1,255 eligible sell23 candidates in the selected 512 full blocks:
  `helius-pumpswap-sell23-ledger-v2-capture-census-2026-10-09.json`. All candidates matched;
  410 full settlements, 843 censored native settlements, and two unsupported token instructions.
- Frozen v2 offline replay of all 67 eligible sell23 candidates in the first 16-block prospective
  inventory-first frame: `helius-pumpswap-sell23-ledger-v2-inventory-frame-census-2026-10-10.json`.
  Thirteen had exact full target-account settlement and 54 censored native-close settlements; all
  six equations passed. This remains a selected-frame captured-class result.
- Offline replay of the historical Helius full-block captures after the empty-base58 fix:
  `helius-historical-instruction-normalization-audit-2026-10-09.json`. All 2,880 stored full
  responses now normalize and parse; all are present in their captured Helius inventories. The
  precommitted root has only 144 full/signatures-only self-checks, so only those 144 qualify as exact
  selected-block counts under the current contract; no historical availability claim is made.
- Precommitted follow-up sample: `helius-public-block-sample-precommit-2026-10-09.json` pins the
  seed, integer-slot windows, signature-only subset, estimated time strata, and wave-specific
  request/credit/byte bounds before any sample request. The plan contains no raw chain payloads.
- First scheduled Helius/public finalized-block window:
  `helius-public-prospective-block-epoch-2026-10-09.json`. It records a 20-slot prospective
  sample, 20/20 Helius full responses, 11/20 public full responses, and the exact error accounting;
  public failures remain unknown, and Helius exact counts are response-bounded structural
  instruction references. A broader seeded 64-slot/hour prospective verifier is a separate
  campaign under `work/brain-senses-live-helius-hourly-20261009/`; per-slot Helius signatures-only
  self-checks are enabled, with independent public comparisons at six-hour UTC intervals. Its
  campaign-specific size and request estimates are in `docs/brain-source-architecture.md`.
- Sensing schedule, multistream point-in-time episode, enrollment re-entry and account-budget
  dry-run tests are in `tests/test_experience_sensing.py` and `tests/test_experience_sparse.py`;
  schedule resource measurements are synthetic and small.
- Git consolidation: `brain-git-consolidation-2026-10-08.json`
  (research/UI ancestry, compatibility fixes, full Mac 1 regression and unchanged production fence).

- Single-host operating-mode handoff: `brain-single-host-handoff-2026-10-08.json`
  (Mac 1 isolated active research checkout, verified research payload transfer, unchanged owner;
  Mac 2 retained as inactive standby).

- Sparse/event-time foundation, real train-role conversion and synthetic load:
  `brain-sparse-experience-2026-10-08.json` (typed observation support/lifecycle, 2,000 synthetic
  entities, 170 real train-role markets, CPU/exposure/replica qualification; no learning-gain claim).
- Censor-aware count objective and real-source coverage audit:
  `brain-censoring-objective-2026-10-08.json` (opt-in synthetic fit/resume/evaluation; existing real
  source has 170/170 support intervals unknown; no provider request or learning-gain claim).

- Partitioned experience engineering capacity (synthetic only):
  `brain-experience-capacity-2026-10-07.json` (initial 520k-bar development measurement),
  `brain-experience-million-capacity-2026-10-07.json` (final million-bar path at `8683305`).
- Post-review experience batching: `brain-experience-batch-capacity-2026-10-08.json`
  (million synthetic bars, cross-partition batches of 1,024, code `035406a`).

- Development, source architecture, historical vacuum, and acquisition: `brain-development-2026-10-04.json`,
  `brain-source-architecture-2026-10-04.json`, `brain-vacuum-2026-10-06.json`,
  `brain-development-acquisition-2026-10-07.json`, `brain-research-envelope-2026-10-07.json`.
- B02/B03/B04 source and quality: `brain-data-scale-2026-10-04.json`, `brain-pool-quality-2026-10-04.json`,
  `brain-availability-normalization-2026-10-04.json`; plots: `brain-b02-coverage.svg`,
  `brain-b02-learning-curves.svg`, `brain-b03-coverage.svg`, `brain-b03-learning-curves.svg`,
  `brain-b04-normalization.svg`.
- B06/B07/B08/B09/B10/B11 and host acceptance: `brain-pumpswap-events-2026-10-04.json`,
  `brain-paper-loop-2026-10-04.json`, `brain-paper-loop-verification-2026-10-04.json`,
  `brain-b07-first-activation-incident.json`, `brain-continuity-2026-10-04.json`,
  `brain-hot-lane-2026-10-04.json`, `brain-hot-lane-verification-2026-10-04.json`,
  `brain-operational-recovery-2026-10-04.json`, `brain-operational-recovery-verification-2026-10-04.json`,
  `brain-archive-curriculum-2026-10-04.json`, `brain-archive-qualification-2026-10-04.json`,
  `brain-archive-reproduction-2026-10-04.json`, `brain-operator-preflight-2026-10-04.json`,
  `brain-operator-acceptance-2026-10-05.json`, `brain-operator-delegation-2026-10-05.json`,
  `brain-operator-delegation-final-2026-10-05.json`, `brain-operator-final-checks-2026-10-05.json`,
  `brain-operator-history-2026-10-05.json`, `brain-suspend-recovery-2026-10-06.json`.
- Latest expansion/training/evaluation: `brain-training-expansion-2026-10-06.json`,
  `brain-curriculum-comparison-2026-10-04.json`, `brain-curriculum-exam-2026-10-04.json`,
  `brain-experience-review-2026-10-06.json`, `brain-post-training-audit-2026-10-07.json`,
  `ml-review-audit-2026-10-07.json`, `brain-benchmark-readiness-2026-10-07.json`.
- Causal diagnostics: `brain-sampling-ablation-2026-10-07.json`,
  `brain-budget-ablation-2026-10-07.json`, `brain-horizon-ablation-2026-10-07.json`,
  `brain-lr-diagnostic-2026-10-07.json`, `brain-clipping-audit-2026-10-07.json`.
- Owner/worker handoff status: `brain-handoff-status-2026-10-07.json`,
  `two-mac-bootstrap-2026-10-06.json`, `two-mac-worker-2026-10-06.json`.
- UI images: `brain-continuity-paused-ui-2026-10-04.png`, `brain-hot-lane-control-center-2026-10-04.png`,
  `brain-operational-recovery-control-center-2026-10-04.png`, `brain-paper-control-center-2026-10-04.png`.

## Earlier source, CPMM, and F01 evidence

- Alchemy transport: `alchemy-pilot-2026-10-03.json`, `alchemy-pilot-v2-2026-10-03.json`,
  `qualified-http-corpus-2026-10-03.json`, `cold-connection-corpus-2026-10-03.json`.
- CPMM/protocol/fees/token history: `cpmm-2026-10-01.json`, `protocol-2026-10-01.json`,
  `creator-fee-share-2026-10-02.json`, `parameter-history` is included in `history-2026-10-02.json`,
  `token2022-2026-10-02.json`, `economic-corpus-2026-10-02.json`, `checkpoint-coherence-2026-10-02.json`.
- Forward/prospective: `forward-2026-10-01.json`, `causal-forward-2026-10-02.json`,
  `prospective-execution-2026-10-02.json`, `calendar-execution-2026-10-03.json`.
- Provider access: `provider-audit-2026-10-02.json`, `helius-preflight-2026-10-02.json`,
  `helius-direct-2026-10-02.json`, `helius-traces-2026-10-02.json`, `secondary-access-2026-10-02.json`,
  `quicknode-diagnostic-2026-10-02.json`, `chunked-range-2026-10-02.json`,
  `persistent-source-2026-10-02.json`.
- Research registry and F01: `corpus-registry-2026-10-03.json`,
  `research-readiness-2026-10-03.json`, `f01-runner-2026-10-03.json`,
  `f01-runner-integrity-2026-10-03.json`, `f01-sampling-2026-10-03.json`,
  `f01-collector-qualification-2026-10-03.json`, `f01-operational-runtime-2026-10-03.json`,
  `f01-live-qualification-2026-10-04.json`, `f01-live-result-2026-10-04.json`,
  `f01-d51-owner-attestation-2026-10-04.json`.
- ML foundation: `ml-foundation-2026-10-03.json`.

For an exact filename lookup, use `rg --files docs/evidence`. Receipt names are deliberately kept
stable because reports and experiment artifacts refer to them. `work/` datasets and model bundles
are not in Git; consult the receipt's pinned local root and manifest when authorized.

- `brain-integrated-pipeline-capacity-2026-10-08.json`: 100k synthetic bars through continuous export, v2 experience, two CPU training epochs and registered evaluation at `f5aca3d`.
- `brain-integrated-million-capacity-2026-10-08.json`: million-bar writer/reader check at `f5aca3d`, before paged-catalog implementation.
- `brain-paged-million-pipeline-capacity-2026-10-08.json`: million-bar, two-epoch full pipeline at `2f1eff2`; eight pools use inline catalogs despite the paging-capable revision and filename.
- `brain-wide-paged-pipeline-capacity-2026-10-08.json`: 1.024M synthetic bars across 2,048 pools at `6ae0ea9`; actual paged train/evaluation catalogs, two CPU epochs and separate evaluation.
- `brain-real-system-qualification-2026-10-08.json`: fixed 24-market development population, cumulative/replica snapshots, three CPU fits, exact SIGKILL recovery and retrospective evaluation; harness `339cb40`, reporter `d0f6732`.

- `brain-population-million-capacity-2026-10-08.json`: 10k synthetic markets, 520k coverage cells, 1M examples and identical audited/plain CPU training at `47c72ae`; payloads in `work/brain-population-scale-20261008/`.
- `brain-population-real-diversity-2026-10-08.json`: population/coverage/usable-exposure audit of the prior 24-market sealed development corpus at `47c72ae`; no new market fit, provider request or holdout access.
- `brain-independent-population-learning-2026-10-08.json`: 307 independently birth-enrolled markets,
  1,228 requests with one unretried timeout, original split infeasibility, explicitly revised calendar
  split and three CPU fits at `24ae9ae`. Broader data beats neural controls but loses to empirical
  train quantiles; source/context/admission/exposure denominators, paired mint intervals and access
  disclosures are retained. Payloads: `work/brain-diverse-campaign-20261008/`.
- `pumpswap-buy-exact-v1-attempt-census-2026-10-10.json`: offline structural attempt census for the pinned v1 instruction in the captured 512-block Helius class; no settlement or completeness claim.
- `pumpswap-buy-exact-v1-c3-frame-census-2026-10-10.json`: fresh offline c3 frame replay; 365
  instruction references in 16 selected full blocks, including 59 pinned 25-byte/two-account
  suffix candidates (53 successful transaction references, six failed attempts). No settlement claim.
- `pumpswap-buy-exact-v1-c3-frame-census-correction-2026-10-10.json`: corrects only the c3 census
  receipt's mistyped tool commit ID; preserves the original receipt and binds the verified source hash.
- `pumpswap-buy-exact-v1-c3-role-tuple-qualification-2026-10-10.json`: independently maps the 59
  c3 two-account suffix candidates from raw full-block account indices to pinned IDL roles; all 27
  pool tuples are internally consistent, and all 59 ordered trailing pairs match the pinned wallet
  set and quote-mint/token-program ATA rule. This is structural only.
- `pumpswap-buy-exact-v1-c3-extra-role-census-2026-10-10.json`: one source-bound replay of the
  other 306 c3 references; all trailing pairs match the pinned wallet/ATA rule, 301 leading
  accounts remain unresolved, three census rows retain writable-recipient failure state, and 142
  references are failed attempts.
- `pumpswap-buy-exact-v1-c3-frame2-census-2026-10-10.json`: offline source-bound census of the
  second prospective c3 frame; 404 candidate transactions, 479 instruction references, and zero
  normalization failures across 16 selected full blocks.
- `pumpswap-buy-exact-v1-c3-frame2-role-replication-2026-10-10.json`: applies frozen raw IDL and
  suffix rules to all 418 eligible 25-byte +2/+3 invocations in frame 2; 160 +2 and 258 +3 trailing
  pairs source-supported, with +3 prefixes retained as unknown. Structural only; no economics.
- `pumpswap-buy-exact-v1-c3-frame2-pool-v2-prefix-raw-reconciled-2026-10-10.json`: exact raw/report
  reconciliation for 258 three-tail candidates from 16 frame-2 blocks; the frozen pool-v2 predicate
  accepts 257 and quarantines one successful transaction with a transaction-wide writable fee wallet.
  The raw sibling reference is recorded, while its semantics and all economic settlement remain
  unresolved. Development replication only.
- `helius-inventory-v3-c4-first-frame-2026-10-10.json`: sealed receipt for the scheduled c4 13:05Z
  epoch; all 34 requests reconcile and all 16 selected full-block/signature checks pass across a
  16,596-slot provider-reported inventory interval, with no backfill or economic completeness claim.
- `helius-inventory-v3-c4-frame-20261010T180000Z.json`: sealed receipt for the preregistered
  18:00Z c4 frame; 34 request/result pairs and 16 sampled full-block/signature checks reconcile,
  with exact capture, plan, summary, reservation, selection, and predecessor-lineage hashes.
- `pumpswap-buy-exact-v1-c4-1800-structural-census-2026-10-10.json`: offline census of 17,784
  transactions and 427 PumpSwap instruction references in 16 hash-bound full blocks, with zero
  normalization errors; the matching 25-byte/three-tail class has 271 references.
- `pumpswap-buy-exact-v1-c4-1800-pool-v2-structural-qualification-2026-10-10.json`: one-pass
  application of the frozen SDK predicate to all 271 raw-reconciled candidates; 270 structural
  passes and one failed attempt quarantined for transaction-wide pool-v2 writability. No economics.
- `pumpswap-buy-exact-v1-c4-first-frame-census-2026-10-10.json`: offline census of 17,607
  transactions in the first c4 frame; 260 pinned v1 instruction references, including 171 exact
  25-byte/23-fixed/three-tail candidates. No settlement qualification.
- `pumpswap-buy-exact-v1-c4-first-frame-pool-v2-prefix-raw-reconciled-2026-10-10.json`: first
  untouched c4 application of the frozen pool-v2 prefix predicate; raw/report sets match 171/171,
  all 171 pass structurally (141 successful refs, 30 failed attempts). This is structural only and
  is not an independent economic audit.
- `pumpswap-buy-exact-v1-c4-success-trace-2026-10-10.json.zst`: hash-bound offline trace of the
  141 frozen c4 successful references; includes raw Pump event bytes, ordered instructions touching
  target roles, declared token operands, and transaction endpoint availability/deltas. The 140
  Token-2022-base rows receive no economic equations; the single dual-legacy row's conditional
  ledger gate remains closed. Endpoint deltas are transaction-scoped and unattributed.
- `pumpswap-buy-exact-v1-c4-state-bracketing-feasibility-2026-10-10.json`: exhaustive offline
  same-slot writable-potential and archive-bracket census for the 140 Token-2022-base references;
  records transaction order, static/ALT provenance, endpoint/event/sibling gates and bank-time state
  requirements. No full-account or mint-only row passes, no acquisition control was emitted, and no
  state RPC was made.
- `pumpswap-buy-exact-v1-c4-state-snapshot-locator-2026-10-10.json`: separate offline locator over
  those same 140 identities and 16 hash-bound blocks. All 140 pass target-message gates and have no
  earlier/later same-slot mint writable-potential blockers; this only identifies S-1/S whole-account
  snapshot candidates. No state was acquired and no effect or economic claim is made.
- `alchemy-account-archive-c4-mint-snapshot-manifest-2026-10-10.json`: no-RPC proposed read
  manifest for the exact 140 locator identities, independently joined to the frozen feasibility
  receipt and 16 block hashes. It deduplicates 75 `(mint,S-1)` plus 75 `(mint,S)` reads, defines
  full-base64/context/TLV validation and explicit failure/conflict states, and proposes 150 requests,
  9,830,400 raw bytes and 1,500 standard CUs as caps only. No access or request is authorized; the
  pre-existing three-call control remains frozen pending entitlement and account-budget checks.
- `pumpswap-buy-exact-v1-development-qualification-2026-10-10.json`: complete replay of the frozen 62-ref dual-legacy development population, with exact token-account ledger facts; still diagnostic-only and not the sealed audit.
- `pumpswap-buy-exact-tail2-v1-development-qualification-2026-10-10.json`: commit-bound follow-up
  over the same 62 dual-legacy development references; candidate transfer facts are exact 62/62,
  target-account endpoint reconciliation is exact 61/62 under classified provider-reported touches,
  and one sibling-touch row remains `unknown_partial`. Fee equations are empirical hypotheses; no
  audit/RPC/admission.
- `pumpswap-buy-exact-tail2-pair64-provider-check-2026-10-10.json`: offline Helius/Alchemy payload
  join for the two development candidates overlapping Alchemy's 64 same-slot full blocks; both
  candidate flows, endpoint account facts and event checks agree after excluding log messages.
  Same decoder on both sides; no independent decoder or completeness claim.
- `pumpswap-buy-exact-tail2-c4-audit-prereg-2026-10-10.json`: metadata-only preregistration of
  the first four naturally completed c4 frames whose scheduled bucket starts after the frozen
  development commit; records conditional nominal buckets, failed/missing-frame handling,
  denominators, source pins and claim limits. At preregistration time no eligible frame payload
  was opened. A later hash-only preplan access to the 23:00Z frame was adjudicated as a disclosed
  procedural deviation; its receipt records the limits and retained eligibility:
  `pumpswap-buy-exact-tail2-c4-manifest-preplan-hash-only-deviation-2026-10-10.json`.
- `pumpswap-buy-exact-tail2-c4-incomplete-outcome-2026-10-11.json`: binds the frozen preregistration
  to c4's immutable abnormal disk STOP. Only the 23:00Z frame was eligible (1/4 required); its
  semantic payload remains unopened and no qualified estimate is available. No substitute or
  backfill is permitted.
- The c4 tail2 metadata manifest builder and its focused tests are in
  `tools/brain_senses_buy_exact_tail2_c4_manifest.py` and
  `tests/test_brain_senses_buy_exact_tail2_c4_manifest.py`. They enforce the preregistered freeze,
  source/runtime pins, natural-frame selection, request/capture accounting, and compressed-byte
  hashes without decompressing eligible payloads. It emits no manifest until four eligible frames
  are successfully source-bound; c4 stopped at 1/4 eligible frames, so no manifest or semantic
  audit was created.
- The metadata-only final-plan builder and one-pass audit runner are
  `tools/brain_senses_buy_exact_tail2_c4_plan.py` and
  `tools/brain_senses_buy_exact_tail2_c4_audit.py`; focused synthetic tests are in the matching
  `tests/test_brain_senses_buy_exact_tail2_c4_{plan,audit}.py`. The plan requires the complete
  scheduled-bucket denominator and hashes of the preregistration, source modules, dependencies,
  deviation receipt, test source files, all tested source/dependency hashes, the Python runtime and
  a JUnit result artifact with checked outcome counts. The
  runner distinguishes successful, failed and
  unknown transaction status; an unknown status or unresolved successful instruction withholds an
  exact successful-class denominator. It retains observed candidates as a lower bound while the
  denominator is incomplete, and reports failed-attempt uncertainty separately. No real frame has
  passed through this runner; the frozen audit has not opened semantic payloads.
- `pumpswap-buy-exact-v1-audit-failure-2026-10-10.json`: preserved one-time 13:00Z census failure after all 512 payloads were processed but before report publication; no outcomes accepted and no reuse permitted.
- `pumpswap-buy-exact-v1-audit-qualification-2026-10-10.json`: fresh disjoint 512-slot Helius Free captured-class census and one-pass replay of all seven successful dual-legacy refs; diagnostic-only, with four censored native settlements and Token-2022 still quarantined.
- `pumpswap-buy-exact-v1-token2022-dev-census-2026-10-10.json.zst`: source-bound offline replay of the 15:00Z development captures; all 1,234 quote-side Token-2022 refs retain unknown historical extension state, with no settlement claim.
- `pumpswap-buy-exact-v1-token2022-privilege-census-2026-10-10.json`: full-message and same-slot writable-privilege replay for those 1,234 refs across 298 captured full blocks; identifies mint-readonly slots as candidates for historical mint-byte acquisition, while 77 refs retain unclassified target-touching programs. No economic or extension-state qualification.
- `pumpswap-buy-exact-v1-token2022-clean-stratum-2026-10-10.json`: exact target-touching instruction bytes/paths and per-target-account same-slot privileges for the 1,146 structurally clean refs. Four rows contain an unclassified Token-2022 `(first byte 9, length 1)` variant and stop; no economics or extension-state qualification.
- `pumpswap-buy-exact-v1-token2022-close-structural-2026-10-10.json`: source-bound offline structural classification of only those four Token-2022 `CloseAccount` rows. All four pass pinned source hashes, raw full-block identity/status, wire, path/stack, ordered-account and privilege checks. No lifecycle, amount, rent/lamport settlement, economics, or admission claim.
- `pumpswap-buy-exact-v1-token2022-transfer-checked-2026-10-10.json`: source-bound offline replay of all 3,552 exact Token-2022 `(tag=12, length=10)` traces from the clean stratum. Every trace passes structural gates; the encoded u64 amount remains atomic units, decimals is recorded separately, and 114 sibling-parent traces have unknown Pump IDL role attribution. No realized-effect or economic claim.
- `pumpswap-buy-exact-v1-token2022-endpoint-delta-census-2026-10-10.json.zst`: offline endpoint-delta census of the frozen 1,234-ref development population. Raw replay validates the one preidentified no-sibling ref; 52 refs have all seven compatible endpoint rows, 1,182 are partial, and 1,233 remain censored for subtree attribution due to sibling touches. No fee, CPI-settlement, or economic qualification.
- `pumpswap-buy-exact-sdk-builder-source-survey-2026-10-10.json`: pins the official 23-account
  v1 IDL and NPM SDK artifact integrity; the examined SDK release has no explicit exact-quote
  builder implementation, leaving captured remaining-account variants unresolved. Raw NPM
  tarball is retained outside Git under `work/official-sources/`.
- `pumpswap-buy-exact-v1-c3-pool-v2-prefix-qualification-2026-10-10.json`: pins the official
  PumpSwap SDK 2.1.0 and qualifies 192 rows supplied by the census report against raw captures;
  this first receipt did not independently prove report population completeness. Treat it as row
  qualification only. It is superseded for the captured-class denominator by the raw-reconciled v2
  receipt below.
- `pumpswap-buy-exact-v1-c3-pool-v2-prefix-raw-reconciled-2026-10-10.json`: independently
  normalizes all 16 hash-bound c3 first-frame blocks (16,668 transactions), verifies census source
  pins and exact raw/report identity and field equality for the full matching valid-boolean
  25-byte/23-fixed/3-tail class (192/192, with zero normalization errors or set differences), then
  applies the unchanged SDK pool-v2 predicate. 190 pass structural roles and two failed attempts
  remain quarantined. This does not establish global discovery completeness or economic settlement.
  Reproduction uses `tools/brain_senses_buy_exact_v1_pool_v2_prefix_qualifier_v2.py` and
  `tools/brain_senses_buy_exact_v1_pool_v2_pda_derive.cjs` with the package artifact under ignored
  `work/source-surveys/`.
- `pumpswap-buy-exact-v1-c3-two-frame-dual-legacy-gate-v2-2026-10-10.json`: source-bound
  development replay over both c3 frames and 450 structural candidates; four successful
  dual-legacy cases are censored, zero exact. Two lack an endpoint and two have subtree/endpoint
  residuals; all have unresolved outside-subtree target-account touches. No audit, fee, wallet-net,
  admission, or completeness claim.
- `pumpswap-buy-exact-v1-c4-future-frame-structural-audit-prereg-2026-10-10.json`: metadata-only
  pre-registration for a single structural replay on the first complete scheduled c4 frame at or
  after 18:00Z. It binds the existing qualifier and source pins, excludes already-opened frames,
  and provides no economic audit or new-request authority.
- `senses-activation-preflight-v1-offline-validation-2026-10-11.json`: source-hash-bound validation
  and Sol review of the offline-only successor eligibility verifier, including transitive gap/time
  lineage, exact filesystem and clock-source evidence, conservative daily byte/request caps,
  signed plan-specific Helius charge units, historical-only claim checks and owner-local
  predecessor claim uniqueness. Fake cryptographic fixtures only; no real trust root, provider
  request, scheduler or live activation.
