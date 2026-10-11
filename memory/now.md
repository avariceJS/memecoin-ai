# Current Project State

**As of:** 2026-10-11 12:41 UTC. Research branch `research/active`, HEAD `bddefa79e0300b31073032c39e11b35a1fdbe167` before this update. Production checkout `/Users/root1/Desktop/memecoin-ai` is untouched.

## Runtime and acquisition

- Helius key use is authorized without rotation. A 12:41Z exact-value rescan found zero matches in 6,416 reachable Git objects, 27 recent Codex logs / 500.4 MB, 82 recent repository files / 2.8 MB, 57 launchd plists, argv, or 846.7 MB of Unified Log. `.env.local` is 0600; the mode-0700 wrapper passes the key through child environment, not argv. The prior transcript and remote/backup storage remain outside erasure/scan scope. Receipt: `helius-key-post-resume-exposure-rescan-2026-10-11T1241Z.json`.
- Helius MCP is configured at pinned 2.2.0; no new RPC was issued. Account charge controls remain unknown. No Senses LaunchAgent is loaded; APFS has 32 GiB free, below the reviewed 50-GiB floor. c4 is terminal: preserve gaps, do not backfill, and do not open frozen tail2. No successor lease/activation until charge, storage/runway, trust, and activation gates pass.

## Evidence boundary

- Pair64 offline receipt: 64/64 same-slot block identity/signature order matched; 80,321/80,467 transaction trees matched, with 146 explicit Helius log truncations. Alchemy inventory completeness remains unknown.
- `buyExact25` is not economically qualified; sell23 remains captured-class only. See `docs/brain-source-architecture.md`.
- Commit `46f6b46` records synthetic Token-2022/WSOL transition consistency (63 focused checks), not raw decoding/economic admission. `f7f26df` records source-pinned Agave hook-location feasibility, not a qualified transaction witness.
- Sol accepted Agave observer v5 for local synthetic ProgramTest feasibility only. Patch SHA `e8c977c70a13988213a30a57d3dca36e5f94fab26d1574844c2a378c55cfe5c3`; review receipt SHA `cdd875a8d123e452086dc9d62826ef448f96604fd8dba54e8c7ce6539cc2c4b9`. Raw Token-2022/WSOL parser scaffolding passes 7 parser-only tests and feature checks, but Sol rejected witness freeze: base mint/authority/state/decimal identity, hook effects, epoch binding, and checker-schema mapping remain incomplete. No sealed raw trace exists. Standing no-signing boundary bars running ProgramTest transaction fixtures. Economic admission, source qualification, and completeness remain false.

## Next horizon

Continue offline parser hardening under Sol's bounded review; do not run signed fixtures. Find the next useful Senses engineering gate that does not require signing or provider acquisition. `work/` is 33 GiB, including 16 GiB stratified captures and a 6.8-GiB Agave target; APFS has 32 GiB free and no suitable external volume is mounted. c4 ended abnormally; no forward-only successor lease exists. Helius key exposure is no longer a blocker, but no prospective service is activatable until account charge controls, storage runway, trust, continuity, and Sol activation review pass. Do not backfill or reopen frozen tail2. Cortex remains out of scope.
