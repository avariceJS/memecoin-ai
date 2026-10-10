# Current Project State

**As of:** 2026-10-10 07:08 UTC. Verify Git HEAD, runtime and disk before acting.

## Boundaries

Continue Senses; do not start Cortex. No production checkout changes, trading/signing, paid upgrades,
protected holdout access, remote pushes or automatic promotion. Keep event, receipt, availability and
decision times distinct. Never convert unknown, missing, censored, failed, stopped or unsupported to
zero. Follow `AGENTS.md` and `docs/INDEX.md`.

## Host and prospective runtime

- Research checkout: `/Users/root1/Developer/memecoin-research`, branch `research/active`; latest
  functional commit `2c1031d` adds an offline frame-2 role replication tool. Production remains
  untouched. About 70 GiB free; raw captures stay under ignored `work/`.
- Frozen campaign c3 is 3/24 attempts. Frames at 05:00Z, 06:00Z and 07:00Z are forward-adjacent:
  `[455116881,455133322)`, `[455133322,455149828)` and `[455149828,455166372)`, widths
  16,441/16,506/16,544. Each completed 34/34 requests and 16/16 structural checks. Frame 3's
  provider returned 16,540 produced slots in its 16,544-position interval; the four unreturned
  numeric positions are not treated as zero. Aggregate: 102 requests, 181,911,223 raw / 40,130,510
  stored bytes. Frame 3's journal, raw/compressed captures, summary, STOP and reservation-release
  hashes reconciled offline. No backfill; inherited gaps remain. The LaunchAgent remains loaded
  and idle; next expected bucket 08:00Z (08:05 trigger). Campaign cap remains 24 attempts / 816
  effective requests.
- C2 and controller STOPs are immutable. Old public/Helius watchers, c1/c2 and the controller are
  unloaded. Helius watch-v3 keeps its explicit STOP; do not resume it.
- Runtime recheck at 07:06Z found c3 is the only loaded job matching Senses/Helius collector or
  watcher names. Its third calendar launch completed with exit 0 and returned to idle. Older plist
  files remain on disk but are not loaded; no duplicate collector process was running. A fresh
  credential scan found zero exact-key matches in the worktree, all local Git objects, 334,396
  saved/runtime file scan passes, current process argv, and Unified Log over 12 hours. The scan's
  unreadable entries were stale pytest `current` symlinks and local Unix sockets. `.env.local`
  and Codex/Cursor MCP configs remain mode `0600`; both MCP configs pass the key through child env,
  not command arguments.
- Helius credential is authorized without rotation. `.env.local` and Codex/Cursor configs are mode
  `0600`; the collector resolves its endpoint in-process, while LaunchAgents pass only paths.
  Process arguments, tracked Git, c3 captures, saved Codex/Cursor files and a two-hour Unified Log
  scan had no exact-key matches outside private configs after remediation. Two historical Cursor
  JSONL transcripts were redacted and validated; one initial credential-bearing process-list result
  remains in this agent-session transcript. Codex/Cursor pin `helius-mcp@2.2.0`. MCP account tool
  returns `Transport closed`; local RPC LaunchAgent works independently.
- Alchemy Archive access/limits/coverage were not established. No archive query or paid request was
  made; no separate PAYG endpoint is configured. Pair64 offline audit supports only sampled block
  agreement (64/64 identity/signature pairs); Alchemy inventory failure remains explicit.

## Senses status

- Helius v2 prospective inventory pilot closed at 104 requests / 186,022,930 raw / 41,818,169
  stored bytes, with a preserved oversized-frame gap. It is provider-bounded structural coverage,
  not global discovery or economic completeness.
- PumpSwap legacy sell23 transfer-ledger facts are qualified only in their captured class; censored
  native settlement remains censored, and this is not wallet-net execution price/admission.
- PumpSwap buyExact Token-2022 economics remain quarantined. Captured layouts/traces and endpoint
  deltas do not establish historical extension state, CPI settlement, withheld effects or wallet
  price. Keep unsupported and unknown subgroups separate.
- Source architecture owns acquisition/completeness and Senses receipts. Training findings belong
  in `docs/training-evaluation.md`. Details and scoped denominators are in those canonical docs and
  their named receipts; this file is only live handoff.
- The first c3 frame's frozen sell23 replay found 80/80 eligible candidates: 10 exact diagnostic
  account ledgers and 70 censored native-close settlements; six equations passed on each. No RPCs
  were made. Receipt: `helius-inventory-v3-c3-sell23-census-2026-10-10.json`.
- The buyExact census tool now replays sealed c3 full-block captures while binding epoch plan,
  frame receipt, summary hash, and selected slots; its default v2 path remains unchanged. Focused
  tests passed (4/4), Ruff passed, and a 512-block Pair64 replay exactly matched the frozen
  counters/matrix. First c3 frame: 365 references in 15 of 16 selected blocks; 59 pinned
  25-byte/two-account suffix structural candidates (53 success, six failed). No settlement or
  economics is inferred. Receipt: `pumpswap-buy-exact-v1-c3-frame-census-2026-10-10.json`; its
  mistyped commit ID is corrected by the adjacent correction receipt.
- An independent raw full-block replay rechecked those 59 candidates' transaction paths, signatures,
  wire bytes, status, and five pinned IDL positions. All match across 27 pools; no mint alias or
  per-pool tuple conflict. All 59 ordered two-account suffixes also matched the pinned eight-wallet
  set and ATA derived from each observed quote mint/token program (53 success, 6 failed attempts).
  The 2 distinct base-position keys / 27 quote-position keys remain only an observed sample
  distribution, not economic orientation; actual fee movement/fill remain unproven. Receipt:
  `pumpswap-buy-exact-v1-c3-role-tuple-qualification-2026-10-10.json`. Qualifier commits:
  `09bfe6f5cd606293ac11de53caf18d83ee199e1a`, `02d7feb17f0b4810957b85c2e6f4919f06e0c070`,
  `2045af272134dbe44582435e4eb7f8891d3635b2`, `f89b3df5a02bc1e456ca6e527d55a34dd922a671`.
- Sol-directed offline replay of the other 306 first-frame buyExact refs found all 306 raw trailing
  wallet/ATA pairs match the pinned set and derivation rule (164 success, 142 failed). There are no
  pair mismatches; 301 refs retain an unresolved prefix account. Three rows remain
  `fee_pair_unqualified` because the recipient was transaction-writable. No prefix role, fee
  movement, or economic fill is inferred. Receipt: `pumpswap-buy-exact-v1-c3-extra-role-census-2026-10-10.json`;
  code commit: `8a4c197cc29d35aeda1e6800777dbd38741799d3`.
- Frame 2 buyExact replay: 404 unique candidate transactions, 479 instruction references, 0
  normalization errors. Frozen raw IDL +2/+3 role replication covered 418 25-byte invocations:
  376 successful and 42 failed references; all 160 +2 and 258 +3 wallet/ATA pairs matched pinned
  source rules. 59 pools, no alias, conflicting tuple or duplicate path. The +3 prefix stays
  unresolved; all other layouts and economics remain quarantined. Receipt:
  `pumpswap-buy-exact-v1-c3-frame2-role-replication-2026-10-10.json`. Implementation commit:
  `2c1031d49c100e9ddb9f5e16c49ad00fd8ca8714`.

## Next horizon

1. Read-only inspect each future c3 tick (next expected trigger 08:05Z): unique frame/request
   identities, new finalized anchor, forward interval, full inventory accounting, 16/16 body/signature
   checks, explicit gaps/failures, byte/disk floors, and no repeats. Do not run missed buckets again.
2. Accept normal campaign closure only after all 24 attempts, immutable reconciled journals/captures/
   summaries/STOP and budget totals, no ambiguity, agent bootout, and no later RPC. Otherwise stop and
   create a receipt-specific abnormal recovery decision; never backfill or reset the frontier.
3. After terminal STOP, send measured latency/byte tails, selected-block yield/concentration, gaps,
   account-level quota evidence, and free disk to Sol for a separate prospective-service lease. A
   finite seven-day lease (at most 5,719 all-in requests, measured byte ceilings, 50-GiB floor, no
   auto-renew) is a candidate only if normal closure and resource evidence support it. Continue Senses;
   no Cortex until sensing bottlenecks are actually closed.
4. Sol's current horizon: no separate engineering task before a new c3 material boundary. Monitor
   c3 read-only. The two-frame buyExact role replication passed; no economic experiment until pinned
   historical Token-2022 state and instruction-scoped settlement evidence exist.
5. Use `.venv/bin/python` from the research checkout for local tools. Interactive system `python`
   resolved a production-tree package during a `--help` probe; it did not affect the LaunchAgent or
   captures. Use `env -u PYTHONPATH .venv/bin/python ...` for reproducible research commands.
