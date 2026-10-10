# Current Project State

**As of:** 2026-10-10 21:17 UTC. Verify live runtime before any campaign or service decision.

## Boundaries and runtime

- Continue Senses only in `/Users/root1/Developer/memecoin-research`, branch `research/active`.
  Production `/Users/root1/Desktop/memecoin-ai` is untouched. No trades, signing, paid upgrade,
  holdout access, push, retry or backfill.
- c3 is terminal after its ambiguous 11:00Z anchor failure; preserve its uncovered interval.
  c4 remains the unchanged finite calendar lease (one bootstrap plus at most 24 hourly frames).
  Read-only status at 21:17Z after the natural 21:05Z tick: 9/24 frames complete, 306 frame
  requests plus one bootstrap (307 all-in), 649,607,110 raw / 146,677,436 stored bytes, zero failed
  or gapped epochs, zero open reservations, 15 attempts remaining, frontier 455397121, and
  63,392,165,888 free bytes. LaunchAgent remains loaded and idle between calendar ticks, with fixed
  path-only arguments; next scheduled trigger 22:05Z. No manual tick. Credit units remain unknown.
  A relative-path status invocation failed the root binding; the absolute-path read-only status
  succeeded without changing campaign state. Preserve all gaps.
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
  shared no-follow private-config reader. Sol's DNS/TCP gate is implemented offline: a fixed-code
  child receives only the public Helius hostname, with scrubbed environment, bounded IPC and
  kill/reap timeout; the connector selects one public OS-ordered address and attempts one TCP
  connection. DNS/connect results are journaled and pre-ack failures seal explicit unobserved gaps.
  No live socket, provider request or service has run. WSS authorization binds client/TLS/config
  reader/socket/resolver source hashes but does not verify c4/account receipt contents. The 128 MiB
  WSS envelope estimates 2,686 published
  credits; together with HTTP's 5,719 calls the planning baseline is 8,405 before overhead. This is
  not a billing ceiling. No activation before c4 terminal proof, measured tails, account headroom /
  overhead review, independent source/one-writer review and authorization. The credential/TLS
  patch's final PPV was complete with no findings/gaps; the offline DNS/TCP connector, journal and
  TLS/client focused suite passes 129 tests and independent review found no remaining blockers.
  Connector remains unactivated.
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
