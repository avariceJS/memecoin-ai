# Current Project State

**As of:** 2026-10-11 15:09 UTC. Research branch `research/active`; working scope based on `730dce9e61f9f36a70feb6f11eacd43f4902cd44` (offline WSS readiness patch pending commit). Production checkout `/Users/root1/Desktop/memecoin-ai` is untouched.

## Runtime and acquisition

- User authorizes the current Helius key without rotation. Codex/Cursor active configs point to the owner-only pinned `helius-mcp-secure` wrapper. A stale `helius-mcp@latest` process with credential-bearing argv was stopped. The 13:37Z exact-value rescan found zero matches in 6,449 reachable Git objects, 48 recent files / 19.2 MB, current argv, launchd state, and 1.98 GB Unified Log. Limits and receipt: `docs/evidence/helius-key-post-legacy-mcp-stop-exposure-rescan-2026-10-11T1337Z.json`.
- Fresh no-network owner census at about 15:05Z: all 15 expected Senses labels are disabled and unloaded, zero Senses writers, and four identifiable Helius MCP key-bearing worker environments (count only; no value read; activity not inferred). The 13:37Z exposure receipt found zero exact matches in argv/logs; it remains a historical exposure scan, not current process census. c4 is terminal with immutable abnormal STOPs and missed prospective buckets; do not reopen, backfill, or touch frozen tail2. Internal APFS has about 31 GiB free vs a 50-GiB floor. No current live cadence is approved.
- Owner wrapper is pinned to `helius-mcp@2.2.0`, mode 0700, and transfers the API key via child environment, not arguments; `.env.local` is mode 0600. Four identifiable MCP worker pairs still exist; clean argv was observed and no request activity is inferred. No RPC or LaunchAgent was started in this continuation.

## Evidence boundary

- Pair64 provider replay matched 64/64 block identity/signature order; 80,321/80,467 transaction trees matched, with 146 explicit Helius log truncations. Alchemy inventory completeness is unknown. Pair64/c3 program exposure is accepted only as an event-only archive projection, not native-slot, global-completeness, economic, or admission proof.
- PumpSwap sell23 remains captured-class only; buyExact25 remains economically unqualified. F01/D43 and reserved holdout remain protected.
- Sol accepted Agave Token-2022/legacy-WSOL parser scaffold v3 for pure parsing and unsigned synthetic mapping only. Patch `docs/evidence/agave-token-raw-parser-scaffold-corrected-v3-2026-10-11.patch`, SHA `3d1347ff…`; receipt alongside it. Exact clean pinned checkout passes 16 feature-on tests, feature-off check, and two feature-off tests. Proptest is omitted because `bit-set v0.8.0` is not cached offline. No signed fixture, RPC, real capture, witness, hook behavior, runtime fee law, source/economic/training/settlement/production qualification.

## Next horizon

The WSS first-entry v2 gate remains offline signed-assertion-only; its production claim API is disabled and activation is false. Public Helius docs state 2 credits per 0.1 MB uncompressed standard WSS and monthly→prepaid→autoscaling order, but account headroom, overage controls, rounding scope, and control/handshake charges remain unverified. The account MCP returned `Transport closed`; disk remains below the reviewed 50 GiB floor. Receipt: `docs/evidence/helius-public-wss-billing-source-capture-2026-10-11.json`. The frozen readiness v1 receipt is superseded and non-authoritative; its MCP environment-presence classifier is stale/incorrect. Focused WSS validation passed (75), focused shared-claim validation passed (11; one immutable c4-replay-dependent test excluded). Root differential review and commit are pending; next horizon is a Sol decision between offline buyExact25 qualification and forward-only autonomous collection groundwork. Do not begin Cortex.
