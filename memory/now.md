# Current Project State

**As of:** 2026-10-13 10:45Z. Owner checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, HEAD and `origin/research/active` `e619d4e`; `main` and `origin/main` remain at
`770699d`. Unrelated Agave/build-guard work is dirty; keep it separate.

## Prospective acquisition

- Kingston `/Volumes/Kingston/BrainData` is bound to root ID
  `9428695af6d4f46c08e1a444cdc66de8`, UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`, device
  `16777242`/inode `400`; 202 GiB free, 50-GiB floor. Descriptor-bound writes only; no internal
  fallback. No collector LaunchAgent is loaded. The stale Gecko watchdog label is not a collector.
- Helius and Alchemy have no active acquisition. Last Gecko v4 read-only census: ten attempts, nine
  HTTP 200 / 7,445 bytes, one ambiguous possibly-spent transport, cursor 12, zero eligible bars;
  elapsed buckets are not backfilled. Charge is unknown/unverified.
- Source-pinned Gecko v6 callback and host pass 69 focused kernel/host/activation/replay tests plus
  `make smoke`. The storage contract now distinguishes BrainData and service-root descriptor
  identities, including service-path replacement rejection. A read-only Kingston census replayed
  41 roots / 62 attempts, with two unresolved responses; a sanitized committed receipt binds the
  conservative 1,015,808 raw-byte / 5,079,040 physical-byte carry-in. No v6 release is initialized;
  no collector LaunchAgent is loaded and no new Kingston/provider writes occurred.

## Senses gates

- Candidate-position witness, native settlement, and `buyExact25` remain independently
  unqualified. Market-source, training, and economic admission remain false; c4 is terminal; do not
  start Cortex.
- Agave owner-account build remains NO-GO due to detach-at-write isolation risk; preserve the dirty
  checkout/cache. This is independent of capture-only collection.
- Next: bind the committed census receipt and derived prior-usage array into the fresh epoch, then
  implement create-only v6 initialization/activation last and verify first-bucket recovery. Use
  future buckets only; never backfill. Continue the independent candidate-position witness
  environment gate in parallel.
