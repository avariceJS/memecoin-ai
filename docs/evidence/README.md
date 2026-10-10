# Evidence Index

Evidence is immutable and task-scoped. Do not read this directory wholesale. Historical receipts
and figures retain their original bytes. Reports/receipts can
contain large exact payloads, so open only the named artifact needed for a specific claim.

## Brain and operator

- Alchemy Free/PAYG endpoint behavior, metered CU reconciliation and bounded errors: `alchemy-endpoint-qualification-2026-10-09.json` (no secrets; endpoint behavior only, no completeness claim).
- Alchemy MCP authenticated-account usage snapshot:
  `alchemy-account-usage-summary-mcp-2026-10-10.json` (18,680 CU / $0.01 month-to-date as of
  01:32Z; account-wide, not attributable to this checkout or a method; account limit not returned).
- Helius MCP account status/plan check: `helius-account-status-mcp-2026-10-10.json` (key configured,
  but plan and credit usage unavailable from the account tool; no plan mutation or paid RPC made).
- Finite Helius campaign-controller launchd installation and first campaign, still idle before its
  first scheduled RPC: `helius-inventory-controller-install-2026-10-10.json`.
- One-use pre-anchor setup recovery for the first v3 campaign's zero-I/O zstd preflight failure:
  `helius-inventory-v3-preanchor-recovery-2026-10-10.json` (preserves both predecessor STOPs,
  proves the sole empty attempt directory, binds the fixed zstd executable, and authorizes one
  fresh-root replacement without extending the original lease or budgets).
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
- `pumpswap-buy-exact-v1-development-qualification-2026-10-10.json`: complete replay of the frozen 62-ref dual-legacy development population, with exact token-account ledger facts; still diagnostic-only and not the sealed audit.
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
