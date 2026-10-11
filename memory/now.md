# Current Project State

**As of:** 2026-10-11 13:23 UTC. Research branch `research/active`, HEAD `4070808a7e80677829197fa28c68219394367f39`. Production checkout `/Users/root1/Desktop/memecoin-ai` is untouched.

## Runtime and acquisition

- Helius key use is authorized without rotation. A 12:41Z exact-value rescan found zero matches in 6,416 reachable Git objects, 27 recent Codex logs / 500.4 MB, 82 recent repository files / 2.8 MB, 57 launchd plists, argv, or 846.7 MB of Unified Log. `.env.local` is 0600; the mode-0700 wrapper passes the key through child environment, not argv. The prior transcript and remote/backup storage remain outside erasure/scan scope. Receipt: `helius-key-post-resume-exposure-rescan-2026-10-11T1241Z.json`.
- Helius MCP is configured at pinned 2.2.0; no new RPC was issued. Account charge controls remain unknown. No Senses LaunchAgent is loaded; current APFS free space is 33 GiB, below the reviewed 50-GiB floor. c4 is terminal: preserve gaps, do not backfill, and do not open frozen tail2. No successor lease/activation until charge, storage/runway, trust, and activation gates pass.

## Evidence boundary

- Pair64 offline receipt: 64/64 same-slot block identity/signature order matched; 80,321/80,467 transaction trees matched, with 146 explicit Helius log truncations. Alchemy inventory completeness remains unknown.
- `buyExact25` is not economically qualified; sell23 remains captured-class only. See `docs/brain-source-architecture.md`.
- Commit `46f6b46` records synthetic Token-2022/WSOL transition consistency (63 focused checks), not raw decoding/economic admission. `f7f26df` records source-pinned Agave hook-location feasibility, not a qualified transaction witness.
- Sol accepted Agave observer v5 for local synthetic ProgramTest feasibility only. Patch SHA `e8c977c70a13988213a30a57d3dca36e5f94fab26d1574844c2a378c55cfe5c3`; review receipt SHA `cdd875a8d123e452086dc9d62826ef448f96604fd8dba54e8c7ce6539cc2c4b9`. Raw Token-2022/WSOL parser scaffolding passes 7 parser-only tests and feature checks, but Sol rejected witness freeze: base mint/authority/state/decimal identity, hook effects, epoch binding, and checker-schema mapping remain incomplete. No sealed raw trace exists. Standing no-signing boundary bars running ProgramTest transaction fixtures. Economic admission, source qualification, and completeness remain false.
- Commit `e22e3b7` adds an archival v2 projection of the frozen Pair64/c3 program-exposure ledger: 608 selected slot observations, 603 positive events, five census-only local zeros, 44,285 per-slot sum. Sol accepted only the event-only `Source.rows()` roundtrip; native slot indexing, sparse use, global completeness, economic qualification, and admission remain false. Receipt `senses-program-exposure-projection-v1-pair64-c3-2026-10-11.json`, source pin `5e11dcf5b56b38ed90d7104dcd736d5bb263d1f7da9af43da4850b46e0230608`.

## Next horizon

Continue source-pinned parser-only Token-2022/WSOL byte semantics using synthetic byte vectors and unsigned pure-function inputs; do not run signed ProgramTest fixtures. Raw account bytes cannot prove transfer-hook execution/order/effects. `work/` is about 33 GiB, including 16 GiB stratified captures and a 6.8-GiB Agave target; APFS has 33 GiB free and no suitable external volume is mounted. c4 ended abnormally; no forward-only successor lease exists. Helius key exposure is no longer a blocker, but no prospective service is activatable until account charge controls, storage runway, trust, continuity, and Sol activation review pass. Do not backfill or reopen frozen tail2. Cortex remains out of scope.
