# Current Project State

**As of:** 2026-10-11 11:48 UTC. Research branch `research/active`, HEAD `f7f26dfbcddeb0792dbb52f88d6c03431ca14d6a`. Production checkout `/Users/root1/Desktop/memecoin-ai` is untouched.

## Runtime and acquisition

- Helius key use is authorized without rotation. `.env.local` is mode 0600; `~/.local/bin/helius-mcp-secure` is mode 0700 and passes the key in child environment, not argv. At 11:48Z two unexpected `@latest` processes were stopped. The exact-value scan found zero matches in 6,412 Git objects, 2,292 recent files / 533.6 MB, 65 launchd plists, current argv, or 295.5 MB of Unified Log. Receipt: `helius-key-post-resume-exposure-scan-2026-10-11T1148Z.json`. The prior agent transcript retains the already disclosed process-list output; remote storage and backups remain unscanned.
- Helius account-status MCP returned `Transport closed` at 11:17Z. No RPC was made. Charge controls are unknown. No Senses collector/LaunchAgent is loaded; APFS has 33 GiB free, below the reviewed 50-GiB floor. c4 is terminal: preserve gaps, do not backfill, and do not open frozen tail2. No successor lease/activation until charge, storage/runway, trust, and activation gates pass.

## Evidence boundary

- Pair64 offline receipt: 64/64 same-slot block identity/signature order matched; 80,321/80,467 transaction trees matched, with 146 explicit Helius log truncations. Alchemy inventory completeness remains unknown.
- `buyExact25` is not economically qualified; sell23 remains captured-class only. See `docs/brain-source-architecture.md`.
- Commit `46f6b46` records synthetic Token-2022/WSOL transition consistency (63 focused checks), not raw decoding/economic admission. `f7f26df` records source-pinned Agave hook-location feasibility, not a qualified transaction witness.
- Sol rejected Luna's Agave v2 observer patch for freeze. Bounded v3 fixes are underway in ignored `work/agave-v3.0.0-full-source`; offline only, no c4/tail2/provider access or default/production hooks. Re-review with Sol before acceptance.

## Next horizon

Continue v3 observer qualification and offline evidence. Separately resolve account-specific charge controls and durable storage/runway before any fresh prospective lease. Do not retry failed MCP status requests or activate a collector without the required policy. Cortex remains out of scope.
