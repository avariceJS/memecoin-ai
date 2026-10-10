# Current Project State

**As of:** 2026-10-10 20:40 UTC. Verify live runtime before any campaign or service decision.

## Boundaries and runtime

- Continue Senses only in `/Users/root1/Developer/memecoin-research`, branch `research/active`.
  Production `/Users/root1/Desktop/memecoin-ai` is untouched. No trades, signing, paid upgrade,
  holdout access, push, retry or backfill.
- c3 is terminal after its ambiguous 11:00Z anchor failure; preserve its uncovered interval.
  c4 remains the unchanged finite calendar lease (one bootstrap plus at most 24 hourly frames).
  Read-only status at 20:33Z after the natural 20:05Z tick: 8/24 frames complete, 272 frame
  requests plus one bootstrap (273 all-in), 583,582,961 raw / 130,344,829 stored bytes, zero failed
  epochs, zero open reservations, 16 attempts remaining, frontier 455380633, and 68,484,501,504
  free bytes. LaunchAgent remains loaded and idle between calendar ticks, with fixed path-only
  arguments; next trigger 21:05Z. No manual tick. Credit units remain unknown. Preserve all gaps.
- Helius key is still in ignored `.env.local` and private MCP configs, all mode 0600. User accepts
  no rotation. Latest follow-up scan found zero exact endpoint/path matches in Git, recent saved
  files, current process args or 213.6 MB of Unified Log. Two Codex history files had only 67
  public-hostname mentions. c4 args contain fixed paths; loader reads endpoint in-process. Prior
  process-list disclosure remains in accepted agent-session history. Provider-side logs, remote
  storage, backups and Time Machine are outside scan scope. Receipt:
  `docs/evidence/helius-key-followup-exposure-rescan-2026-10-10T1904Z.json`.
- No Cortex. Do not alter c4's frozen lease or the previously frozen PumpSwap audits.

## Senses evidence and current decision horizon

- Pair64 offline comparison: 512 Helius selected blocks and 64 same-slot Alchemy pairs; all block
  identities and ordered signatures match. Across 80,467 transactions, 80,321 trees match exactly;
  146 differences are explicit Helius log truncations. Alchemy inventory failure was not retried.
  This is selected-scope structural agreement, not global completeness.
- PumpSwap buyExact c3 settlement feasibility: 450 structural candidates across two frames; four
  dual-legacy successes are censored, zero exact. No lifecycle extension/economic admission.
- PumpSwap sell23 c4 frozen audit: four frames (14:00–17:00Z), 388 structural refs; 341/342
  native-close source-account flows exact, one unsupported touch quarantined. Destinations remain
  ambiguous; no wallet-net/rent/economic admission.
- Program-exposure ledger covers 608 captured selected blocks: 2,574,909 normalized outer/inner
  references and 1,073 distinct program IDs; selected structural exposure only.
- Sol kept the future standard Helius WSS Pump bonding-curve `logsSubscribe` envelope unchanged:
  one subscription, at most two sequential four-hour sessions, 128 MiB inbound message bytes,
  64 MiB stored bytes, 4 MiB message limit and 50 GiB free-disk floor. Reconnect/interruption is
  an explicit gap; no catch-up/HTTP retrieval. Offline implementation and benchmark are committed
  in `814502d`; focused suite: 28 passed. Payload replay: 608 selected blocks, 741,305 transactions,
  51,502 mentions, 69,592,769 application-JSON bytes (~52% of cap). This is not a live rate or
  completeness estimate; benchmark was reproduced against its pinned source revision.
- Offline WSS implementation now includes verified deadline-bound TLS over a caller-connected
  socket, strict Upgrade/subscription handling, cleanup ownership, partial input accounting, and a
  shared no-follow private-config reader. It still has no DNS/TCP connector, provider request,
  LaunchAgent, or live evidence. WSS authorization binds client/TLS/config-reader source hashes but
  does not verify c4/account receipt contents. The 128 MiB WSS envelope estimates 2,686 published
  credits; together with HTTP's 5,719 calls the planning baseline is 8,405 before overhead. This is
  not a billing ceiling. No activation before c4 terminal proof, measured tails, account headroom /
  overhead review, independent source/one-writer review and authorization. Final PPV: complete,
  no findings/gaps; 156 focused tests pass.
- The separate seven-day Helius service v1 also remains offline. Its conditional Sol gate is in
  `docs/brain-source-architecture.md`; after c4 STOP, reconcile terminal/no-post-STOP proof, all
  frame tails, disk and account limits, then review its cap (early stop vs evidence-backed revision)
  before any fresh initialization. Its credential loading now shares the fd-based reader and uses
  stricter RPC URL checks, with that helper source-bound into its runner pin. No manual bootstrap or
  catch-up.

## Next horizon

1. Let the unchanged c4 LaunchAgent collect only scheduled future frames; inspect read-only status
   after natural ticks and retain missed intervals as gaps. Do not retry, backfill or manually tick.
2. After normal c4 STOP, verify terminal receipt, request accounting, no open/ambiguous reservation,
   no post-STOP I/O and unloaded label; calculate full storage/latency/width tails and account
   headroom before selecting the authorized successor path.
3. Review the seven-day autonomous service runner against that evidence; only then activate on its
   future calendar tick if every Sol gate is satisfied. Continue offline engineering and economic
   qualification gates meanwhile; do not begin Cortex.
