# Current Project State

**As of:** 2026-10-12 20:49Z. Checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, HEAD `959c0c3`, pushed. `main` remains `3dab3a3`. Preserve the unrelated dirty
Agave/build-guard files listed below. Production, champion, protected samples, real trading, and
Cortex remain untouched.

## Prospective acquisition

- Kingston `/Volumes/Kingston/BrainData` matches root ID `9428695af6d4f46c08e1a444cdc66de8` and
  UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`; descriptor check at 20:40Z measured about 202 GiB
  free. Descriptor-bound writes only; preserve the 50-GiB floor, no internal fallback.
- Helius `ai.memecoin.brain.senses.external-rolling-v5` remains loaded at 60 seconds and records
  structural `getSlot`, not market history. Storage-health monitor is loaded at 300 seconds.
- Capture-only v1/v2 roots are read-only. v3 ID `9d7d89fc934c46f3a8a9a5b6d7c4e201` passed its
  live LaunchAgent identity check and published a no-provider callback, then exited 2 in health
  publication (`durable_results` vs verifier's `terminal_count`). It has no request intent and is
  retired. v3 LaunchAgent is unloaded. The field contract is fixed with a regression test; v4 code
  and plist are still uncommitted. Create a new owner-only handoff receipt, fresh root and 58-GiB
  preflight before another LaunchAgent attempt. Do not reuse v3 or backfill its missed window.
- Alchemy read-only account meter remained 18,680 authenticated-team CU / $0.01 through 20:26Z;
  no account limit or workload attribution was returned. Helius confirms configured key but no JWT
  usage meter. Research lease charge remains unknown/unverified. Receipt:
  `docs/evidence/senses-provider-account-readonly-2026-10-12T2027Z.json`.

## Senses gates

Sol selected the next independent engineering horizon after collection: qualify a separately
versioned default-deny macOS confinement supervisor with a disposable fixture before any Agave
build. Test descendant read/write/network confinement, symlink escape, forced detach and no
surviving children. No Cargo or protected-data access is allowed by this decision. The existing
Agave runner v1 remains rejected. After this gate, source/cache closure and any actual build need a
separate review. Economic native settlement/`buyExact25`, broad causal population/lifecycle and
future observation coverage remain open; keep temporal provenance distinctions explicit.

User-owned dirty files, untouched:

```
 M tests/test_brain_senses_build_storage_guard.py
 M tools/brain_senses_build_storage_guard.py
?? tests/test_brain_senses_agave_build_runner_v1.py
?? tests/test_brain_senses_gecko_lane_v1.py
?? tools/brain_senses_agave_build_runner_v1.py
?? tools/brain_senses_gecko_lane_engine_v1.py
?? tools/brain_senses_gecko_lane_policy_v1.py
?? tools/brain_senses_gecko_lane_verify_v1.py
```
