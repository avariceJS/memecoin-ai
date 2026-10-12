# Current Project State

**As of:** 2026-10-12 08:59Z. Checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`; the current working changes are pending review and commit, based on
`c6f4264b80429c4332b89684b02f3c9c822fdbe2`. `main` remains at the prior verified baseline;
do not treat this horizon as pushed.
Production, champion, protected evaluation, trading and Cortex remain untouched.

## Live boundaries

- Kingston `/Volumes/Kingston/BrainData`: root ID
  `9428695af6d4f46c08e1a444cdc66de8`, UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`;
  latest checked health was ready with 219,425,021,952 bytes free. Use only the bound storage
  contract; no internal-SSD fallback.
- c4 and `external-v2-20261012T0600Z` are terminal. Three prospective frames plus bootstrap are
  sealed; nine windows remain expired-unobserved. No backfill, ambiguous replay, unresolved
  reservation, STOP, or successor eligibility. Expiry receipt:
  `docs/evidence/brain-senses-external-v2-20261012T0600Z-final-expiry-status.json`.
- Fresh Helius MCP account check at 08:51Z: API key configured, no JWT session, usage unavailable,
  plan unknown; no signup. Alchemy account summary remains 18,680 CU / $0.01 through 08:51Z,
  aggregate and not workload-attributed. Helius charge remains unknown.
- The one Sol-authorized Agave Cargo fetch stopped terminally after creating
  `target/.rustc_info.json`. Preserve the staged Cargo home/target; no retry, metadata, build or
  ProgramTest without another Sol review.
- Current read-only host check: Kingston identity still matches the bound root/UUID and health is
  `ready`, with 219,425,054,720 bytes available. The research-storage health LaunchAgent is loaded
  but idle. No Brain/Senses acquisition agent is loaded. Temporary launch-v3 rehearsal labels are
  unloaded; disabled overrides from earlier attempts were reset. The 06:00Z external-v2
  receipt retains 103 requests, four seals, frontier 455944206, zero unresolved buckets, STOP=false,
  no replay/successor eligibility, and disabled/unloaded state. The c4 terminal receipt remains the
  canonical prior qualification; do not run the default qualifier against checkout `work/` paths.

## Recent Senses evidence

- Pushed milestone `00e7eee`: fixed-service partial-prefix structural queue v2; three sealed frames
  scanned, nine unobserved rows retain null metrics. Deterministic create-only Kingston publication
  and replay passed. Full-seal v1 is unchanged.
- Pushed `622f1e9` adds a source-pinned synthetic multi-record attempt→commit→block→frozen
  bank→selected-root verification. Batch and absolute positions stay distinct; failed/FeesOnly
  effects are typed; duplicate/fork/time ambiguities reject. Nineteen focused tests, Ruff, Mypy and
  smoke pass. `make check` still stops at 107 pre-existing Ruff findings outside these new files.
  Receipt: `docs/evidence/agave-commit-to-root-synthetic-join-v1-2026-10-12.json`.
- Pushed `3239ad4` adds a descriptor-bound launch-v3 journal snapshot adapter that
  delegates journal semantics to v2, binds the plan/source/predecessor/window, and rejects torn,
  stale, or changed two-read snapshots. Nine focused tests, Ruff and focused Mypy pass. A two-image
  case-sensitive APFS run forced detach before failure-receipt `mkdir` and `create`; both failed
  closed with no receipt on remount and no underlay/fallback artifact. Disposable images used
  OS-noowners and zero-capacity thresholds, so this is detach evidence only. Receipt:
  `docs/evidence/brain-senses-launch-v3-failure-receipt-detach-qualification-v1-2026-10-12.json`.
- Local launch-v3 rehearsal completed one manual-only, synthetic, no-provider LaunchAgent kickstart
  and restart on disposable case-sensitive APFS. The receipt and journal verified, restart did not
  dispatch, teardown unloaded the label and reset its disabled override, and the image/plist were
  removed. This does not qualify Kingston capacity or real service activation. Receipt:
  `docs/evidence/brain-senses-launch-v3-disposable-launchd-rehearsal-v1-2026-10-12.json`.
- Kingston capacity/recovery qualification v1 completed with no provider, credential, launchd or
  lease activity: 3,104,000,000 logical payload bytes and 3,105,206,272 allocated high-water under
  the 3,394,000,000-byte cap; 531 prewrite guard samples, minimum free 216,318,795,776 bytes and
  end free 219,425,054,720 bytes. A child exited after a 1MB synthetic prefix; read-only recovery
  classified `possibly_spent_no_replay` with zero recovery writes/dispatches. Scratch cleanup and
  underlay checks passed. This is capacity and synthetic recovery evidence only, not live Senses
  activation/acquisition. Receipt SHA-256:
  `55d5e55657c5cb0ca2aaf6216c1c5c8a5c6c2cd8afc02811419bf67a8ad32ca3`.
- This is synthetic consistency only. Runtime law, source qualification, candidate-position
  admission, settlement, buyExact25 and PIT remain false. No real instrumentation or build is
  authorized by this receipt.

## Active horizons

Sol approved a 485-row c3 diagnostic-only replay. The final ordered-touch gate preserves raw
CreateAccount amounts but withholds unknown contributions; no complete residual is reported while
opaque effects remain. V3 replay retained 485 ambiguous rows, excluding 68 exact-ledger successes
and two failed references. Focused pytest, Ruff, and scoped Mypy pass; receipt:
`docs/evidence/pumpswap-sell23-c3-destination-lamport-diagnostic-v3-final-2026-10-12.json`.
V1/v2 artifacts remain immutable and are superseded. The v3 rehearsal above is complete. No provider,
Agave build, real lease, Kingston activation or economic admission is in scope.
