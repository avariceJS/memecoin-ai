# Current Project State

**As of:** 2026-10-12 04:35Z. Checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, at `354fb418adb20da3e735ae61cdbbc26e2a1b3c85`; current work includes the new
descriptor-detach and provider-account receipts plus the raw-account fixture implementation in progress.
`main` remains unchanged.
Production, champion, protected evaluation, and real trading remain untouched; no signing or
submission; Cortex is out of scope.

## Live state

- Kingston root ID `9428695af6d4f46c08e1a444cdc66de8`, UUID
  `8f8f1081-9be4-4914-9111-75f1b90255bf`; do not move frozen roots or fall back to internal storage.
- c4 is terminal STOP at frontier `455430160`, with no backfill. The sealed v1 predecessor verifies
  at `455802008`.
- The 02:30Z v2 sibling and 04:30Z prospective initialization are terminal/disabled. The 04:30Z
  window was archived before activation; independent verification found zero requests, reservations,
  intents, seals, or STOP and unchanged frontier. Never replay it.
- Sparse-observation reference v1 is published and independently verified on Kingston: 307 entities,
  53,080 bars, 1,060,992 declared slots, 1,007,912 unobserved/unknown, and 15,257 consecutive-32
  baseline rows. It is historical-replay structure only; point-in-time eligibility is false.
- The v3 nested writer gate and fresh disposable descriptor-detach qualification passed. Sol accepted
  the exact nonauthorizing 06:00Z lease plan (409 requests, 4 RPS, 3.104 GB cap), conditional on the
  final clock, launch, storage, writer, and review chain; it has not been activated.
- At 04:24Z, Helius still had no JWT/account meter. Alchemy reported 18,680 CU / $0.01; an empty
  hourly series does not establish zero usage. See the 04:24Z provider receipt.

## Next

Finish the final release chain for the fixed 06:00Z plan: bind the outer lineage audit and 03:40Z
timing rehearsal into Sol's final receipt, refresh dual clock and Kingston identity/free-space,
verify the initialized prefix and activation gate, install the exact reviewed plist, and retain at
least 15 minutes of arm time. If any gate fails or the window is missed, leave this lease unused.
Then verify its real checkpoint/recovery without replaying ambiguous requests. In parallel, Luna is
implementing Sol-settled raw-account-fixture-v1 schema and independent byte replay with runtime-law
and economic admission hard-coded false. Do not inspect protected fixtures or the dirty Agave
checkout/cache.
