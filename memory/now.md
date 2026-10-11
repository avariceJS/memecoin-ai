# Current Project State

**As of:** 2026-10-11 12:17 UTC. Research branch `research/active`, HEAD `f52bd0bd5c077097e7d65635c2edfe0c69782305`, worktree clean before this handoff update. Production checkout `/Users/root1/Desktop/memecoin-ai` is untouched.

## Runtime and acquisition

- Helius key use is authorized without rotation. `.env.local` is mode 0600; `~/.local/bin/helius-mcp-secure` is mode 0700 and passes the key in child environment, not argv. At 11:48Z two unexpected `@latest` processes were stopped. The exact-value scan found zero matches in 6,412 Git objects, 2,292 recent files / 533.6 MB, 65 launchd plists, current argv, or 295.5 MB of Unified Log. Receipt: `helius-key-post-resume-exposure-scan-2026-10-11T1148Z.json`. The prior agent transcript retains the already disclosed process-list output; remote storage and backups remain unscanned.
- Helius account-status MCP returned `Transport closed` at 11:17Z. No RPC was made. Charge controls are unknown. No Senses collector/LaunchAgent is loaded; APFS has 33 GiB free, below the reviewed 50-GiB floor. c4 is terminal: preserve gaps, do not backfill, and do not open frozen tail2. No successor lease/activation until charge, storage/runway, trust, and activation gates pass.

## Evidence boundary

- Pair64 offline receipt: 64/64 same-slot block identity/signature order matched; 80,321/80,467 transaction trees matched, with 146 explicit Helius log truncations. Alchemy inventory completeness remains unknown.
- `buyExact25` is not economically qualified; sell23 remains captured-class only. See `docs/brain-source-architecture.md`.
- Commit `46f6b46` records synthetic Token-2022/WSOL transition consistency (63 focused checks), not raw decoding/economic admission. `f7f26df` records source-pinned Agave hook-location feasibility, not a qualified transaction witness.
- Sol rejected Agave observer v2 and v3; v4 is also rejected for freeze until boundary-level execution ID/attempt identity, selected-account validation at every account-carrying phase, and frame program/data agreement are independently checked with mutations. Luna is implementing only these bounded v5 changes in ignored `work/agave-v3.0.0-full-source`; offline only, no c4/tail2/provider access or default/production hooks. Re-review with Sol before acceptance.

## Next horizon

Finish and review v5, then run the Sol-approved bounded synthetic ProgramTest raw Token-2022/WSOL witness/parser gate. This does not qualify a real source or economic admission. In parallel, prepare official account-evidence interpretation and a storage/runway dossier without RPC. Current free disk is 33 GiB versus the 50-GiB floor; c4 ended abnormally; Helius account charge controls and live trust/review evidence are unknown. No prospective service is activatable. Do not backfill, reopen frozen tail2, retry failed MCP status requests, or issue duplicate requests. A future service requires a fresh forward-only lease, verified c4 discontinuity with gaps/open unknown preserved, exact caps/schedule, adequate measured storage and account controls, and separate Sol activation review. Cortex remains out of scope.
