# Current Project State

**As of:** 2026-10-11 15:45 UTC. Research branch `research/active`; committed HEAD `a08405c5a83f6825ffe915ffd80f9ff0db015dd0`. Production checkout `/Users/root1/Desktop/memecoin-ai` is untouched. Luna is implementing the next offline census in untracked tool/test files; review before commit.

## Runtime, credentials, and storage

- User authorizes continued use of the current Helius key without rotation. `.env.local` is mode 0600; `~/.local/bin/helius-mcp-secure` is mode 0700 and pinned to `helius-mcp@2.2.0`, passing the key through child environment rather than argv.
- The 15:44Z exposure audit found and redacted 57 exact-key copies across three Cursor SQLite databases; SQLite integrity checks passed, database/WAL files are mode 0600, and a post-redaction scan found zero exact matches in 364,193 recent files / 49.78 GB, all 6,540 Git objects, current argv, and 500 MB of Unified Log. Receipt: `docs/evidence/helius-key-cursor-state-redaction-2026-10-11T1544Z.json`. Cursor remained open, so later in-memory reserialization is not ruled out. A separate ChatGPT-hosted `helius-mcp@latest` process remains present but current argv has no exact key/endpoint; authenticated Helius account MCP returned `Transport closed`.
- Senses collectors/LaunchAgents remain disabled; no Helius RPC or prospective cadence ran. c4 is terminal; missed slots stay gaps; do not reopen, backfill, or touch frozen tail2. APFS has about 27 GiB free after local database compaction; `work/` is about 34 GiB, no writable external data volume or local Time Machine snapshots found. Live WSS still fails the 50-GiB floor, account-specific billing, trust, and reviewed-lease gates.

## Evidence and next horizon

- WSS offline one-shot operator bridge committed as `a08405c`; 19 focused tests, Ruff, formatting, compilation, and diff checks passed. It supports only injected offline clients/status, terminal recovery without replay, one window, and false activation/provider-I/O claims. No LaunchAgent or provider adapter.
- Sol selected a read-only PumpSwap `create_pool` identity feasibility census over exactly 608 sealed selected Pair64/c3 blocks (512 + six 16-block frames). Stage 1 froze selection/source metadata and manifests; raw selected-body replay follows only after independent walker/mutation checks. Output is candidates/feasibility only, never birth/population/admission. No c4/tail2/F01/D43/holdout, provider call, or missed-slot work.
- Pair64/account MCP usage remains unavailable (`Transport closed`); buyExact25 economic audit waits for prospective position-state evidence, independent checks, and a separately frozen contract. Do not begin Cortex. Review Luna's census results and commit only the bounded verified scope.
