# Current Project State

**As of:** 2026-10-11 05:22 UTC. Research work continues on `research/active` in
`/Users/root1/Developer/memecoin-research`; production `/Users/root1/Desktop/memecoin-ai` is
untouched. Read this state, then check Git/runtime before acquisition.

## Boundaries and runtime

- User authorizes the existing Helius key without rotation. It is loaded in-process from private
  config, not process arguments. Prior exposure scans found no exact key in checked Git objects,
  saved logs/files, current process args or 65 LaunchAgent plists. Their inaccessible-file and
  remote/backup/session-storage exclusions remain in the scan receipts. Do not print or expose it.
- The user authorizes the current Helius key without rotation. The 05:12Z scan found no exact
  credential/URL matches in 6,123 Git objects, current process args, 65 launchd plists, 7,384
  accessible recent files, or 934.8 MB of subsequent Unified Log. Its remote/provider, backup,
  inaccessible-file and Time Machine limits are recorded in the receipt. `.env.local` is mode 0600;
  the collector takes a config path and resolves the URL in process. No Senses LaunchAgent/collector
  is running. Free disk is now 24,655,904 KiB (~23.5 GiB), below the reviewed 50 GiB floor; Helius
  charge headroom remains unknown. No RPC or backfill was made. A new prospective lease still needs
  disk/account inputs and separate Sol runtime activation review; key rotation is not the blocker.
  Recheck runtime before any future request. Exposure receipt:
  `docs/evidence/helius-key-continuation-exposure-rescan-2026-10-11T0512Z.json`.
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
  compile and diff checks pass. No RPC/activation. Receipt:
  `docs/evidence/senses-prospective-controller-v1-recovery-qualification-2026-10-11.json`.
- buyExact25 economic feasibility remains negative: four legacy settlement rows are censored,
  Token-2022 economics quarantined, failed transactions attempt-only. Do not reopen frozen c4 tail2.

## Next decision horizon

Ask Sol to define the next bounded horizon for durable autonomous prospective collection and its
activation contract. Keep Senses runtime inactive until current disk/account headroom, real
out-of-band trust/price inputs, and a separate Sol lease activation review pass. The current 50 GiB
floor blocks RPC; do not backfill missed intervals or reuse the terminal c4 campaign.
