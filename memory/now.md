# Current Project State

**As of:** 2026-10-12 09:37Z. Checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`; base `c03534507ed26ce1ce0803e8dd04c4b2d758b043` is pushed to
`origin/research/active`. Structural-family triage code, tests, and canonical documentation are
currently uncommitted; the preceding verified baseline is `1ee7c8e54df105d6395cb647400e9dde65f71427`
on both long-lived branches.
Production, champion, protected evaluation, trading and Cortex remain untouched.

## Live boundaries

- Kingston `/Volumes/Kingston/BrainData`: root ID
  `9428695af6d4f46c08e1a444cdc66de8`, UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`;
  latest checked health is ready with 219,415,134,208 bytes free after the 10.3 MB projection.
  Use only the bound storage
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
  `ready`; `df` reports 214,272,616 KiB available. The research-storage health LaunchAgent is loaded
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
- Sample-only archive projection of the sealed `external-v2-20261012T0600Z` queue was published
  descriptor-bound/create-only, then replayed without changing either artifact or receipt. It
  preserves 12/3/9 scheduled/sealed/unobserved frames, 4,164 produced slots / 48 selected blocks,
  46,222 transactions, 4,865 references (4,176 successful / 689 failed), and 4,595 classes; the
  nine unobserved rows remain null. Failed references do not prove execution/effect/settlement; no
  extrapolation, absence, economic/lifecycle, PIT, training or admission claim. No provider I/O.
  Artifact SHA-256 `b348ef424d26a3baa3b296ade34c136d7cac8ed428c803d3b3c2a3edc5c01a72`; publication
  receipt SHA-256 `ca8eacdd774efa298b3c98edd3cb67722c3e3eaa11862c7113060ca4c25be670`.
- Offline structural-family triage reopened only the 48 selected blocks and reconciled all 4,865
  references into 68 raw-prefix families against 18 pinned IDL blobs; 14 families remain unresolved
  or ambiguous. Nine unobserved rows remain null. Kingston publication is create-only and replay
  verified: 20,259,829 bytes, artifact SHA-256
  `c5a83e4e65eb5227e7c2da16de4efb0a8365d21f6b008674f81ba04674b60ee0`, receipt SHA-256
  `ae6beb74e07035d00a289bd2b2eaa0c8eab5499da5527c7330ce5c9aebc51f02`. Diagnostic shape only;
  no semantic, execution, effect, economic, lifecycle, PIT, training or admission claim. No provider
  I/O. Nineteen focused tests, Ruff, formatting and scoped Mypy pass.
  Focused projection/storage/writer/capacity tests pass (82); changed-file Ruff/format, smoke, and
  Kingston root verification pass. Full-suite collection needs `PYTHONPATH=.:src`; the broad run
  had 2,926 passed, 24 failures and 25 optional skips. It preceded the final projection fixes (now
  covered by focused tests); remaining failures are outside this scope and need separate triage.
  `make check` stops first on existing Ruff findings outside this scope.
- The capacity qualification is synthetic only; the archive projection is a source-pinned view of
  the existing sealed selected-block observations. Neither qualifies runtime law, candidate-position
  admission, settlement, buyExact25 or PIT. No production/runtime change or build was made.

## Active horizons

The next horizon is a versioned external-v2-specific sample-local unknown-review queue built from
the immutable triage artifact and its 48 selected captures. Sol approved diagnostic categories,
source/time joins, mutation tests, and create-only Kingston publication; it does not qualify a live
anomaly monitor or a no-new-class rate. Economic candidate-position/native-settlement/buyExact25
admission remains design-only pending a separately qualified runtime witness. No provider lease,
Agave build, c4 replay, backfill, or Cortex work is authorized by these offline horizons.
