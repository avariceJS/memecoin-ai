# Current Project State

**As of:** 2026-10-11 00:10 UTC. Recheck runtime before any campaign or service decision.

**Research HEAD:** `53b1d9a623139e162100d9dec9a4fc676b236159` on `research/active`.

## Boundaries and runtime

- Continue Senses only in `/Users/root1/Developer/memecoin-research`, branch `research/active`.
  Production `/Users/root1/Desktop/memecoin-ai` is untouched. No trades, signing, paid upgrade,
  holdout access, push, retry or backfill.
- c3 is terminal after its ambiguous 11:00Z anchor failure; preserve its uncovered interval.
  c4 is immutable and terminal after a natural 00:05Z `minimum_free_disk_floor_plus_frame_envelope`
  STOP following 11/24 successful frames. It records 374 frame requests plus one bootstrap (375
  all-in), 769,657,370 raw / 177,576,450 stored bytes, zero failed/gapped epochs, zero open
  reservations, and frontier 455430160. Its exact LaunchAgent is unloaded; no retry, backfill, or
  manual tick. The last successful 23:00Z frame is the only eligible c4 tail2 observation; the
  frozen cohort closed incomplete at 1/4 with no semantic payload opened or qualified estimate.
  Receipt: `docs/evidence/pumpswap-buy-exact-tail2-c4-incomplete-outcome-2026-10-11.json`.
  Offline 11-frame sizing: 69.97 MB raw / 16.14 MB stored per frame mean; c4 allocated 202,719,232
  bytes. At 00:08Z free space was 54,975,664,128 bytes, 614,572,928 above the prior floor-plus-
  frame gate. No multi-day runway; account headroom/charge units unknown. Receipt:
  `docs/evidence/helius-inventory-v3-c4-abnormal-stop-sizing-2026-10-11.json`.
  The eleven-frame method accounting is 12 `getSlot`
  (including bootstrap), 11 `getBlocks`, 176 full `getBlock`, and 176 signature checks; provider
  charge units remain unknown.
  Preserve all gaps.
- Helius key is still in ignored `.env.local` and private MCP configs, all mode 0600. User accepts
  no rotation. Latest follow-up scan found zero exact endpoint/path matches in Git, recent saved
  files, current process args or 213.6 MB of Unified Log. Two Codex history files had only 67
  public-hostname mentions. c4 args contain fixed paths; loader reads endpoint in-process. Prior
  process-list disclosure remains in accepted agent-session history. Provider-side logs, remote
  storage, backups and Time Machine are outside scan scope. Receipt:
  `docs/evidence/helius-key-followup-exposure-rescan-2026-10-10T1904Z.json`. Post-resume scan also
  found zero matches across 2,918 Git blobs, current process arguments, 270 recent log/text files
  (50.4 MB), and 672.6 MB of Unified Log output. `.env.local` remains mode 0600; key material was
  not emitted. See the service review receipt for exact scope.
- After the 22:00Z frame and this continuation, a scoped scan found zero endpoint/key-path matches
  in 1,459 c4 campaign files (180.4 MB), current process/LaunchAgent arguments, and 55.5 MB of
  recent Unified Log. `.env.local` remains mode 0600; value was not emitted. Receipt:
  `docs/evidence/helius-key-post-resume-c4-log-rescan-2026-10-10T2218Z.json`.
- At 23:18Z a private-config enumeration accidentally emitted the key in one tool result in the
  interactive agent session; this transcript cannot be retracted from the checkout. A secret-safe
  rescan found zero exact matches across all 5,818 Git objects, 177 recent local saved files (1.05
  GB), current process/LaunchAgent arguments, and 82.2 MB of Unified Log. All three expected private
  config files remain mode 0600. Provider and remote conversation storage remain inaccessible.
  Receipt: `docs/evidence/helius-key-post-tool-output-rescan-2026-10-10T2318Z.json`.
- User authorized continued use without key rotation; the current endpoint is loaded in-process from
  private mode-0600 config and is absent from process arguments. c4's exact LaunchAgent is unloaded.
  After STOP, a regenerable 1.366 GB pip HTTP cache was purged; immutable captures and campaign
  state were untouched. At 00:08Z free disk was 53,687,172 KiB (54,975,664,128 bytes), only
  614,572,928 bytes above c4's floor-plus-frame gate. No post-STOP RPC or manual tick occurred.
- c4 terminal proof implementation has a committed fix (`53b1d9a`) for macOS `log show` timestamp
  syntax. The first successful external snapshot was captured within seven minutes of STOP at
  `work/evidence/c4-terminal-observations-20261011/`; wait 70 minutes from STOP before capturing
  the second and qualifying. No terminal receipt exists yet. Sol rejected service-v1 predecessor
  reuse from abnormal STOP; any abnormal successor requires a fresh versioned lineage and review.
  The qualifier independently
  revalidates capture journals, selected-slot derivation, full/signature block agreement, structural
  summaries, request timing, reservation release, accounting and artifact hashes. It creates only
  the derived `terminal-receipt.json` after natural STOP and a 70-minute external observation window;
  no campaign bytes, lease, runtime or request state changed. Service predecessor validation now
  reruns this verifier and pins its source. Focused suite, including final hardening: 87 passed.
  Snapshot evidence is local-scope only. Review notes: ignored `work/reviews/` artifact.
- No Cortex. Do not alter c4's frozen lease or the previously frozen PumpSwap audits.
- Helius MCP reproducibility check: persistent `~/.codex/config.toml` is pinned to
  `helius-mcp@2.2.0`. Two Helius child processes inherited `@latest` from the already-running
  Codex app-server; both local npm lockfiles resolve to 2.2.0 with identical tarball integrity.
  A fresh read-only `getHeliusPlanInfo` call at 22:16Z failed with MCP `Transport closed`; current
  account plan and limits remain unknown. Do not restart the app-server during the campaign; new
  app-server starts should consume the pinned config. A further read-only `getAccountStatus` call at
  23:59Z also returned `Transport closed`; no result or request accounting was available, and it
  was not retried.

## Senses evidence and current decision horizon

- Interim c4 sizing at 10/24 natural frames: 706.3 MB raw / 161.8 MB stored; mean per frame
  70.63/16.18 MB; anchor-to-seal median 36.8 s, p95 165.2 s. Mean projection for 167 future frames
  is 11.79 GB raw / 2.70 GB stored, leaving no defensible margin under the provisional 12 GB raw
  cap. Full-block request-start-to-receipt median/p95/max: 1.27/9.02/27.13 s (n=160). Do not revise
  budget or activate service v1 before all 24 frame tails and terminal gates.
  Receipt: `docs/evidence/helius-inventory-v3-c4-midcampaign-sizing-2026-10-10T2222Z.json`.

- Frozen buyExact tail2 dual-legacy development diagnostic (`f7ec8c0`): candidate `TransferChecked`
  flow exact 62/62; `BuyEvent` cross-check match 62/62; endpoint reconciliation exact 61/62 with
  one sibling-touch `unknown_partial`. Five fee equations matched but remain hypotheses; no
  settlement, wallet-net price or admission. The two references overlapping Alchemy's 64 same-slot
  pairs agree on the candidate facts. Sol accepted the dev-only freeze. Receipts:
  `docs/evidence/pumpswap-buy-exact-tail2-v1-development-qualification-2026-10-10.json`.
  Provider join: `docs/evidence/pumpswap-buy-exact-tail2-pair64-provider-check-2026-10-10.json`.
- Sol's metadata-only prospective audit plan is frozen in
  `docs/evidence/pumpswap-buy-exact-tail2-c4-audit-prereg-2026-10-10.json`. The 22:00Z bucket
  starts before `f7ec8c0` and is excluded; 23:00–02:00Z are nominal if all seal successfully.
  If any eligible bucket is missing/failed/partial/ambiguous, count it and continue only on the
  next naturally completed c4 frame; report all scheduled buckets and the four captures. Do not
  open eligible payloads until four frames seal, metadata-only source binding is frozen, and the
  exact runner/test hashes are recorded in a final plan. A pre-fix CLI invocation hash-read the 34
  compressed responses and 34 metadata rows from the 23:00Z frame, without decompression, display,
  classification, output, RPC or campaign mutation. Sol retained the frame and preregistration with
  this procedural deviation disclosed; see
  `docs/evidence/pumpswap-buy-exact-tail2-c4-manifest-preplan-hash-only-deviation-2026-10-10.json`.
  No semantic payload was opened. The corrected builder does not hash content until four candidates
  exist.
- The final-plan builder and one-pass audit runner are implemented offline. They bind all scheduled
  buckets through the fourth natural success, source/dependency/test hashes, frame identities and a
  separate create-only freeze receipt. Sol decided that any unknown transaction status or unresolved
  instruction in a successful transaction withholds an exact captured successful-class denominator;
  observed candidate checks remain a lower bound. Failed-attempt uncertainty is separate. The
  runner audits every transaction/reference once and seals an incomplete receipt after any
  post-start provenance or parsing failure. Synthetic test evidence now binds the JUnit result hash
  and test/failure/error/skip counts, all 22 tested source/dependency hashes, and Python runtime
  identity. No eligible frame payload was semantically opened; the frozen cohort is incomplete.
  Synthetic focused verification: 28 tests pass; Ruff, format and py_compile pass; `make smoke` passes with
  synthetic-only evidence. Differential review is complete with no open findings; details are in
  ignored `work/reviews/tail2-c4-plan-audit-differential-review-20261011.md`.
  Sealed reusable synthetic receipt at
  `work/evidence/tail2-c4-synthetic-20261011-v6/test-receipt.json` (28 passed, 0 failed/errors/skipped;
  receipt SHA-256 `9e8949d6f46e3e609e4ab4b4cd015de1d4d4b13719114db1868ee10921ae88f9`).
- Verification: focused PumpSwap suite 44 passed, targeted Ruff/format passed, and `make smoke`
  passed with synthetic-only evidence. Repository-wide `make check` stops on 43 E501 findings in
  two unchanged pool-v2 qualifier tools. `make test` cannot collect three unrelated legacy tests
  importing absent tools (`brain_senses_block_epoch_v2`, `brain_senses_buy_exact_v1_census`,
  `brain_senses_buy_exact_v1_extra_role_census`); 13 optional ML tests skip. These are outside the
  current diff; the differential-review artifact records details under ignored `work/reviews/`.

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
  A scoped offline gate patch binds exact c4 campaign/label/root, inventories competing Senses
  launchd labels and processes, and holds host/root writer locks. A failed second frame check seals
  only a provably unstarted frame and retains the sleep-gap anchor across tested crash points. Sol
  review found no remaining blocker. The inactive plist now sets a sanitized PATH and fixed versioned
  zstd path; each scheduled runner verifies zstd before credential loading or durable reservation.
  Sol accepted this offline operational fix; focused service/runner suite: 80 passed. Initialization
  can create local metadata without the tick gate but performs no RPC. Service v1 remains
  uninitialized. Source-bound review and exposure scan:
  `docs/evidence/helius-service-v1-operational-review-2026-10-10.json`.

## Next horizon

1. Let the unchanged c4 LaunchAgent collect only scheduled future frames; inspect read-only status
   after natural ticks and retain missed intervals as gaps. Do not retry, backfill or manually tick.
   Tail2's implementation froze at `f7ec8c0`. Once the first four successful scheduled frames
   strictly later than that commit complete, select them by metadata only, freeze the audit plan,
   then open their payloads once. Do not inspect those future full-block payloads before plan freeze.
2. After normal c4 STOP, collect two external operational snapshots spanning at least 70 minutes
   (first within seven minutes), then create and reverify the terminal receipt. Confirm no open or
   ambiguous reservation, c4-attributable post-STOP request, or loaded label; calculate full
   storage/latency/width tails and account
   headroom before selecting the authorized successor path.
3. Review the seven-day autonomous service runner against that evidence; only then activate on its
   future calendar tick if every Sol gate is satisfied. Continue offline engineering and economic
   qualification gates meanwhile; do not begin Cortex.
