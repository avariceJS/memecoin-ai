# Current Project State

**As of:** 2026-10-11 12:22 UTC. Research branch `research/active`, HEAD `151fc584d66a20d499d9521147950e5c33eb0991` before this memory update. Production checkout `/Users/root1/Desktop/memecoin-ai` is untouched.

## Runtime and acquisition

- Helius key use is authorized without rotation. `.env.local` is mode 0600; `~/.local/bin/helius-mcp-secure` is mode 0700 and passes the key in child environment, not argv. At 11:48Z two unexpected `@latest` processes were stopped. The exact-value scan found zero matches in 6,412 Git objects, 2,292 recent files / 533.6 MB, 65 launchd plists, current argv, or 295.5 MB of Unified Log. Receipt: `helius-key-post-resume-exposure-scan-2026-10-11T1148Z.json`. The prior agent transcript retains the already disclosed process-list output; remote storage and backups remain unscanned.
- Helius account-status MCP returned `Transport closed` at 11:17Z. No RPC was made. Charge controls are unknown. No Senses collector/LaunchAgent is loaded; APFS has 33 GiB free, below the reviewed 50-GiB floor. c4 is terminal: preserve gaps, do not backfill, and do not open frozen tail2. No successor lease/activation until charge, storage/runway, trust, and activation gates pass.

## Evidence boundary

- Pair64 offline receipt: 64/64 same-slot block identity/signature order matched; 80,321/80,467 transaction trees matched, with 146 explicit Helius log truncations. Alchemy inventory completeness remains unknown.
- `buyExact25` is not economically qualified; sell23 remains captured-class only. See `docs/brain-source-architecture.md`.
- Commit `46f6b46` records synthetic Token-2022/WSOL transition consistency (63 focused checks), not raw decoding/economic admission. `f7f26df` records source-pinned Agave hook-location feasibility, not a qualified transaction witness.
- Sol accepted Agave observer v5 for local synthetic ProgramTest feasibility only. Patch SHA `e8c977c70a13988213a30a57d3dca36e5f94fab26d1574844c2a378c55cfe5c3`; review receipt SHA `cdd875a8d123e452086dc9d62826ef448f96604fd8dba54e8c7ce6539cc2c4b9`; root decision receipt is `docs/evidence/agave-v3.0.0-candidate-position-observer-v5-review-2026-10-11.json`. No production, replay, persistence/finality or economic claim. Luna is starting the next offline raw Token-2022/WSOL account parser and fixture gate with pinned source/program/epoch/Rent inputs and independent invariants; failures remain attempted-only. No c4/tail2/provider/audit access.

## Next horizon

Continue the raw Token-2022/WSOL fixture gate and independently review it with Sol. Current free disk is 33 GiB; `work/` is about 33 GiB, including 16 GiB stratified captures and a 6.6-GiB Agave checkout/6.5-GiB target. No external volume is mounted. c4 ended abnormally; Helius account charge controls and live trust/review evidence are unknown. No prospective service is activatable. Do not backfill, reopen frozen tail2, retry failed MCP status requests, or issue duplicate requests. A future service requires a fresh forward-only lease, verified c4 discontinuity with gaps/open unknown preserved, exact caps/schedule, adequate measured storage and account controls, and separate Sol activation review. Cortex remains out of scope.
