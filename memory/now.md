# Current Project State

**As of:** 2026-10-12 05:18Z. Checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, at `7cde1e44b9988adb2a59f62d0315a7b642aabaf8`; `main` and both origin refs are
fast-forwarded to this commit. The current uncommitted scope adds synthetic raw-account replay and a
post-seal structural-input v1 sibling builder/publisher with disposable detach qualification v2.
Production, champion, protected evaluation, and real trading remain untouched; no signing or
submission; Cortex is out of scope.

## Live state

- Kingston `/Volumes/Kingston/BrainData` is bound to root ID `9428695af6d4f46c08e1a444cdc66de8`
  and UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`; latest verified free space is 220,877,201,408
  bytes (~205.7 GiB), above the 50-GiB floor. Health monitor is loaded and ready. Never fall back to
  internal storage or move frozen roots.
- c4 STOP remains terminal at frontier `455430160`, with no backfill. Sealed-v1 predecessor
  frontier is `455802008`. The 02:30Z and 04:30Z v2 siblings are terminal/disabled; no catch-up.
- Structural-input v1 accepts only independently verified sealed v2 captures. Five forced detach
  points passed with unchanged underlay, no fallback, no replay/provider calls, and zero open handles.
  Disposable capacity is not live capacity qualification; Kingston was untouched, and no real v2
  frame is compiled. Receipt: `brain-senses-structural-input-sibling-writer-qualification-v2.json`.
- Synthetic raw-account v1 covers Token-2022/WSOL byte layouts only. Hooks remain unsupported; runtime
  law, PIT, settlement, and admission are false. Combined focused suites (118 tests), targeted
  Ruff/format/mypy/py_compile, and `make smoke` pass.
- The fixed 06:00Z Helius plan is semantically accepted but not activation-ready. The plan is bounded
  at 409 requests, 4 RPS, 3.104 GB; actual charge remains unknown. Do not activate unless fresh
  predecessor, clock, storage, launch, and Sol cross-bound release evidence all pass by 05:45Z.
  Otherwise leave this fixed window unused; no shift or catch-up.
- Read-only provider meter remains limited: Helius key is configured but no authenticated JWT/account
  quota or charge meter; Alchemy last snapshot was 18,680 CU / $0.01 with no account limit or workload
  attribution. No signup/account mutation.

## Next

Finish or terminally leave the fixed 06:00Z release without replay. Then replan to the isolated
synthetic Token-2022/WSOL runtime horizon: first qualify fresh source/lock/patch/dependency closure,
then a bounded guarded fetch/build; do not inspect dirty Agave checkout/cache or sign. Stop that
horizon on closure/build failure or any need for signing. Continue Senses through economic,
candidate-position, lifecycle/modalities, recovery, unknown qualification and storage horizons; do
not declare engineering complete until the practical sensing/semantics/provenance/recovery/coverage
bottlenecks have moved to representation/model intelligence/learning/compute.
