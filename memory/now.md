# Current Project State

**As of:** 2026-10-11 06:09 UTC. Research work continues on `research/active` in
`/Users/root1/Developer/memecoin-research`; production `/Users/root1/Desktop/memecoin-ai` is
untouched. Read this state, then check Git/runtime before acquisition.

## Boundaries and runtime

- User authorizes the existing Helius key without rotation. It is loaded in-process from private
  config, not process arguments. Prior exposure scans found no exact key in checked Git objects,
  saved logs/files, current process args or 65 LaunchAgent plists. Their inaccessible-file and
  remote/backup/session-storage exclusions remain in the scan receipts. Do not print or expose it.
- The user authorizes the current Helius key without rotation. The 05:26Z broad scan found no exact
  credential/URL matches in 6,146 Git objects, current process args, 65 launchd plists, 14,449
  accessible recent files / 390.8 MB, the full 26,650-file `~/.npm` tree / 4.66 GB, shell
  history/startup files, or 874.0 MB of subsequent Unified Log. Remote/provider, inaccessible-file,
  backup, session-storage and Time Machine limits remain. `.env.local` is mode 0600; the collector
  takes only a config path and resolves the URL in process. Alchemy MCP ping returned `pong`; Helius
  account-status MCP returned `Transport closed`, so current account headroom is unverified. No
  Senses LaunchAgent/collector is running. Free disk at 05:22Z was 24,655,904 KiB (~23.5 GiB),
  below the reviewed 50 GiB floor. No RPC or backfill was made; key rotation is not the blocker.
  Recheck runtime before any future request. Receipt:
  `docs/evidence/helius-key-broad-exposure-rescan-2026-10-11T0526Z.json`.
- c4 `work/brain-senses-live-helius-inventory-v3-20261010-4` is terminal. The 23:00Z tail2 body
  remains frozen/unopened; missed prospective slots remain gaps. No trading, signing, paid upgrade,
  protected holdout access, backfill, remote push or Cortex work.

## Completed this continuation

- Offline finite-calendar-v3 plus operation-envelope-v2/child-v3 now has a safe missed-anchor
  transition: an expired frame becomes a gap, then a later anchor may proceed without request or
  unnecessary STOP. Sol accepted the offline gate. See the finite-calendar receipts in
  `docs/evidence/README.md`.
- Sol conditionally accepted the new activation-preflight verifier strictly as offline eligibility
  after distinct Ed25519 keys across host, Helius checker and Sol roles were enforced. It preserves
  transitive gaps/unknown/time lineage, conservative every-day bounds, full-lease storage runway,
  bound disk/clock evidence, signed plan-specific Helius charge units and one owner-local claim per
  predecessor. It never authorizes activation; no calendar/runtime consumes it. Cross-host locking,
  real trust/charge evidence and a reviewed adapter remain future gates. Receipt:
  `docs/evidence/senses-activation-preflight-v1-offline-validation-2026-10-11.json`.
- Focused verification passed: 36 preflight cases and 131 combined preflight/calendar/bridge/
  controller/verifier cases; Ruff, format, compile and diff checks passed. No RPC or live activation.
- Prospective controller v1 recovery was requalified offline: normal close requires every bucket to
  have a captured seal or elapsed gap; early cancellation is an abnormal STOP with separate
  `cancelled_unstarted` status; failed-result crash prefixes stop without replay; a supplied
  predecessor's plan, event chain and terminal head are independently checked. Backdated STOPs and
  seals before capture availability are rejected. Sol accepted this offline contract. The 52 focused
  tests and 145 combined activation/calendar/operation/controller/verifier tests pass; Ruff, format,
  compile and diff checks pass. 58 WSS socket/TLS credential-transport tests also pass. No RPC/
  activation. Commit: `8044baa`. Receipt:
  `docs/evidence/senses-prospective-controller-v1-recovery-qualification-2026-10-11.json`.
- buyExact25 economic feasibility remains negative: four legacy settlement rows are censored,
  Token-2022 economics quarantined, failed transactions attempt-only. Do not reopen frozen c4 tail2.

## Next decision horizon

Keep Senses runtime inactive until current disk/account headroom, real out-of-band trust/price
inputs, and a separate Sol lease activation review pass. Current free disk remains below the 50 GiB
floor; do not backfill missed intervals or reuse the terminal c4 campaign. Sol approved the offline
one-shot supervisor over calendar-v3 → operation-envelope-v2 → child-v3. Its status-only CLI and
explicit-injection API are source-bound and qualified by 19 focused / 76 combined tests; it samples
UTC after the shared lock and after each decision; stale inputs and even ledger-newer post-clock
regressions are reported. A transient regression with no new event is not durably journaled; each
later call resamples under lock. No RPC or live activation occurred. First live entry still needs a
separately versioned discontinuity gate:
c4's abnormal stop is not a normal predecessor; bind verified terminal/gaps/unknowns and start at a
fresh future anchor without claiming continuity or reopening tail2. Receipt:
`docs/evidence/senses-calendar-supervisor-v1-offline-validation-2026-10-11.json`.

The offline initial-entry/discontinuity-v1 certificate is now bound to the metadata-only c4
abnormal-terminal verifier. It carries all predecessor gaps plus the c4 STOP gap, keeps the
continuation end unknown, and treats any successor schedule as an inert proposal with a future
anchor first. No initialization, activation, RPC, response-body access, or tail2 access occurred.
Validation: 21 focused tests; independent verifier replay passed. Receipt:
`docs/evidence/senses-initial-entry-discontinuity-v1-offline-validation-2026-10-11.json`.

Sol approved the offline initial-entry calendar compiler/verifier v1. The inert candidate pins the
certificate file and canonical predecessor digest separately, all four gaps/open unknown interval,
caller-supplied future schedule/caps/source hashes, and compiler/verifier/calendar/bridge/child code.
Independent verification and a synthetic finalized-anchor + 34-request frame handoff passed, along
with mutation and crash/no-replay cases (36 focused tests). No schedule was selected, owner lease
initialized, activation, RPC, or provider-body/tail2 access occurred. Local Unified Log may no
longer preserve durable c4 observation provenance; activation still requires requalification and a
separate initial-entry preflight branch. Caller clock/source values are pins, not authenticated
evidence; stateless candidate generation does not ensure global lease-ID uniqueness. Receipt:
`docs/evidence/senses-initial-entry-calendar-compiler-v1-offline-validation-2026-10-11.json`.
