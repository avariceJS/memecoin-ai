# Current Project State

**As of:** 2026-10-12 21:12Z. Checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, HEAD `f936a75` (pushed); `main` is `3dab3a3`. Preserve pre-existing dirty
Agave/build-guard and Gecko v1 files. Production, champion, protected samples, real trading, and
Cortex remain untouched.

## Prospective acquisition

- Kingston `/Volumes/Kingston/BrainData` matches root ID `9428695af6d4f46c08e1a444cdc66de8` and
  UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`; latest observed free space is about 202 GiB.
  Writes use descriptor-bound storage only; maintain the 50-GiB floor and no internal fallback.
- Capture-only Gecko v4 LaunchAgent `ai.memecoin.brain.senses.gecko-capture-only-v4` is loaded
  from the pinned plist at 60-second ticks, no lease expiry. The first future 15-minute bucket
  produced 4 actual HTTP 200 requests, 4 durable captures/results/confirmations, no retry/unknown,
  and no eligible new-open bars (0/4). Exact independent replay, hashes, times, and limits are in
  `docs/evidence/gecko-capture-only-v4-first-live-bucket-2026-10-12T2100Z.json`. A controlled
  bootout/bootstrap recovered a healthy 4-intent/4-result prefix with no duplicates. Provider
  charge remains unknown/unverified; source/training/economic qualification remain false.
- Sol says wait for the next natural v4 bucket, then qualify a separate v5 six-pool rotating scout:
  cycle all 170 enrolled train pools; nominal revisit 28–29 buckets (~7.1h), or 42–43 buckets
  (~10.6h) with two deterministic four-bucket retention slots occupied. Keep stale/failed/empty
  pools in denominators; no response-based same-bucket replacement, inference of death, or
  backfill. New release requires synthetic cycle/retention/cap/recovery tests and a distinct
  future-only 6/6 pilot. Active v4 remains unchanged until that gate.
- Helius v5 structural collector and storage-health monitor remain loaded. The shared Kingston
  volume lock can serialize ticks while an RPC/frame is in flight; monitor latency before changing
  the writer contract. Alchemy account charge evidence remains separately receipted; this Gecko
  lane does not use Alchemy.

## Senses gates

- Agave Seatbelt confinement v2 passes 5 synthetic isolation tests but forced same-path stage
  replacement wrote to the underlay. Receipt:
  `docs/evidence/agave-confinement-v2-fixture-2026-10-12.json`. Sol ruled NO-GO for this unprivileged
  pathname-based route on the owner Mac. Do not run Cargo/Agave or touch the dirty source/cache;
  only reconsider with kernel-enforced mount identity or an unprivileged identity unable to access
  the underlay.
- Current independent horizon: implement a separate read-only native-settlement sufficiency
  adapter on synthetic evidence only. C3 receipt is a metadata baseline (485/485 ambiguous), not
  independent transaction replay. No proceeds, wallet net, `buyExact25`, or economic admission.
- User-owned dirty files are preserved; do not stage unrelated files. Continue after each horizon;
  the Senses stop condition is not met. Keep temporal/provenance distinctions explicit.
