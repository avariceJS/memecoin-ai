# Current Project State

**As of:** 2026-10-13 05:23Z. Owner checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, HEAD and `origin/research/active` `bc1082f`. `main` remains at `770699d`.
Working tree contains unrelated Agave/storage changes and an in-progress Gecko successor lineage;
keep scopes separate and do not publish incomplete work.

## Prospective acquisition

- Kingston `/Volumes/Kingston/BrainData` is bound to root ID
  `9428695af6d4f46c08e1a444cdc66de8`, UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`, device
  `16777242`/inode `400`; 202 GiB free, 50-GiB floor. Use descriptor-bound writes only; no internal
  fallback. No collector LaunchAgent is loaded. The stale Gecko watchdog label is not a collector.
- Helius and Alchemy have no active acquisition in this checkout. Latest Gecko v4 census remains
  read-only: ten attempts, nine HTTP 200 / 7,445 bytes, one ambiguous possibly-spent transport,
  cursor 12, zero eligible bars; 04:30/04:45 and later missed buckets are never backfilled. Charge
  remains unknown/unverified.
- Frozen v1/v2 chain cannot safely continue at cursor 12. A separate chain-v2, cutover certificate,
  and inventory-v3 plan-only replay now pass focused tests. Capture-prefix verification, full-census
  inventory-v4, runtime/host, crash/detach qualification, and source/plist release binding remain
  open. No Kingston/provider/LaunchAgent action is authorized until those gates pass.

## Senses gates

- Candidate-position witness, native settlement, and `buyExact25` remain independently
  unqualified. Market-source, training, and economic admission remain false; c4 is terminal; do not
  start Cortex.
- Agave owner-account build remains NO-GO due to detach-at-write isolation risk; preserve the dirty
  checkout/cache. This does not independently block capture-only prospective collection.
- Next: qualify the v2 capture-prefix verifier and inventory-v4, then a separately versioned host
  and forced recovery/detach tests. Recheck the first future untouched bucket only after release
  qualification; never backfill elapsed windows.
