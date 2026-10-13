# Current Project State

**As of:** 2026-10-13 05:54Z. Owner checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, HEAD and `origin/research/active` `71c919b`. `main` and `origin/main` remain at
`770699d`. Unrelated Agave/build-guard work is dirty; keep it separate.

## Prospective acquisition

- Kingston `/Volumes/Kingston/BrainData` is bound to root ID
  `9428695af6d4f46c08e1a444cdc66de8`, UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`, device
  `16777242`/inode `400`; 202 GiB free, 50-GiB floor. Descriptor-bound writes only; no internal
  fallback. No collector LaunchAgent is loaded. The stale Gecko watchdog label is not a collector.
- Helius and Alchemy have no active acquisition. Last Gecko v4 read-only census: ten attempts, nine
  HTTP 200 / 7,445 bytes, one ambiguous possibly-spent transport, cursor 12, zero eligible bars.
  Elapsed buckets are not backfilled; charge remains unknown/unverified.
- Commit `71c919b` adds source-bound cutover evidence, cursor-12 chain-v2, capture-prefix replay, and
  inventory-v4. A separate descriptor writer/host library passes 53 focused tests, including
  no-replay recovery and path-replacement with no underlay artifact. It remains synthetic-only:
  owner source loader, immutable release/preflight, plist/LaunchAgent, full detach matrix, and live
  qualification are open. No Kingston/provider/LaunchAgent activity occurred.

## Senses gates

- Candidate-position witness, native settlement, and `buyExact25` remain independently
  unqualified. Market-source, training, and economic admission remain false; c4 is terminal; do not
  start Cortex.
- Agave owner-account build remains NO-GO due to detach-at-write isolation risk; preserve the dirty
  checkout/cache. This is independent of capture-only collection.
- Next: finish a separately versioned source-bound owner-host preflight, then test recovery at every
  durable write boundary. Run only on a future untouched window after all release and storage gates
  pass; never backfill elapsed windows.
