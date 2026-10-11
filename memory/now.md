# Current Project State

**As of:** 2026-10-12 (Europe/Moscow), external-writer live qualification.
Research: `/Users/root1/Developer/memecoin-research`, `research/active`.
Production `/Users/root1/Desktop/memecoin-ai`, champion, paper ledgers and protected evaluation
remain untouched. No signing/submission. Do not start Cortex. Use Luna High → Sol High routing.

## Git and authority

- Only `main` and `research/active` are long-lived. Verify live Git before continuing; final pushed
  refs are recorded in `~/.config/brain-research/git-handoff-v1.json` after synchronization.
- Writer capability source commit: `27d7294`; finite lease activation: `bd1446b`. Completed work is
  committed/pushed in atomic scopes; do not rewrite published history or frozen campaign sources.
- Current Helius key remains approved without rotation. Keep credentials outside argv/plists/Git/logs.
  Scoped scan of 32 new commit files, process argv and prospective plist found zero exact matches.
- User authorizes active Free quota use and reasonable bounded Alchemy PAYG, with conservative caps
  and no retries after ambiguity/errors. A perfect account meter is not a global blocker. Helius MCP
  authenticates the configured key but lacks JWT/credit telemetry: actual charge remains unknown.
  Alchemy aggregate 18,680 CU/$0.01 is not workload attribution. Provider/readiness v2 receipts are
  dated 2026-10-11; consult them only for those specific facts.

## Storage

- `/Volumes/Kingston/BrainData`, 0700, case-sensitive APFS, owners enabled. Binding:
  `~/.config/brain-research/storage-v1.json` (0600), UUID
  `8f8f1081-9be4-4914-9111-75f1b90255bf`, root ID `9428695af6d4f46c08e1a444cdc66de8`.
  Floor: **50 GiB**. No formatting, unchecked aliases, absolute-path writer fallback or internal SSD
  fallback. New large artifacts require the explicit storage contract and descriptor-bound writes.
- Three sealed closed capture roots (18,285,310,608 logical bytes) remain under `BrainData/archive/work`;
  old paths are read-only aliases, never writer configuration. Final seals/migration receipts live
  under `BrainData/migration/20261011`. Existing frozen reader exceptions and dirty Agave/target
  remain internal. Do not move/delete them blindly; external Cargo writes are not yet qualified.
- Latest external free: **220,975,820,800 bytes (~205.8 GiB)**. This lease added **68,872,141 logical
  bytes**, including **64,223,372 raw/stored payload bytes**. No collector remains growing it.
  At the old conservative 18-GiB/day scenario, runway above floor is ~8.65 days; this is not a
  measured sustained rate or a new lease. Kingston is temporary capacity and is not a backup.

## Qualified runtime and terminal state

- The inactive `brain_senses_external_service_v1.py` prototype stays blocked with
  `EXTERNAL_WRITER_DETACH_QUALIFIED=False`. The separately versioned writer/orchestrator/verifier/
  clock/LaunchAgent stack uses held descriptors for mutations, locks and renames.
- Qualification: ten actual forced detach-at-write points on disposable APFS, unchanged internal
  underlay, and remount recovery with zero provider-loader calls. Actual Kingston scratch ownership,
  fsync, collision, rename and independent-process flock checks pass. Final receipts are detach and
  recovery **v5**, Kingston **v4**, dated 2026-10-12. Older revisions remain immutable historical pins.
- Fresh reviewed lease `external-v1-20261011T2133Z`: bootstrap 21:33Z, frame 21:38Z, expiry 21:41Z
  on Oct 11 UTC. Both buckets sealed, 35 RPC total (2 getSlot, 1 getBlocks, 32 getBlock), 16 paired
  selected slots from a 1,373-slot inventory; final frontier **455802008**. Four gaps preserved,
  including c4→new-bootstrap `[455430160,455800635)`. No backfill or ambiguous replay.
- Real launchd processes: bootstrap/frame exit 0; one approved terminal kickstart also exit 0.
  All **153 files** and request count **35→35** remain identical. The finite label is now disabled,
  unloaded and its hash-checked plist archived beside its private activation. No auto-renewal.
  Completion and checkpoint receipts: `docs/evidence/external-v1-20261011T2133Z-*.json`.
- c4 is still terminal STOP `minimum_free_disk_floor_plus_frame_envelope`, frontier **455430160**,
  bucket `20261010T230000Z`, reconciled reservation; all three metadata pins unchanged.
- All 15 older agents stay retired. Research storage-health agent stays enabled at 300s cadence;
  latest status `ready`. Production services were not changed. TLS Date corroboration is coarse;
  NTP remains unavailable, and no high-precision time or provider-side billing claim is made.
- Acquisition `decision_ns` precedes its own response availability. All 35 actual decisions were
  additionally checked after prior durable availability (frame after bootstrap seal); see the
  dispatch-causal-order receipt. This is not an economic decision timestamp.

## Validation and next horizon

- **91 focused external tests** and source qualification pass; synthetic smoke and 39 Control Center
  Python checks pass. Full repository checks remain non-green: 107 unchanged baseline Ruff errors,
  36 baseline WSS mypy errors; full `python -m pytest` gave 2,612 passed, 25 skipped, 13 failed.
  Ten failures concern old log-evidence replay, two symlink-rejecting exposure readers, one suite-wide
  low-FD condition. Focused external and migration runs retain FD count 4→4. Use `python -m pytest`
  or `PYTHONPATH=.`; the bare pytest entrypoint omits the repo root. Do not rewrite frozen evidence
  to make these tests pass. Full-suite receipt records its lack of a persisted full-run log.
- Offline successor-v2 compiler is accepted and qualified on the actual expired root under
  host/storage/service locks. It binds plan/source/event/bundle/seal hashes, terminal time, root,
  frontier, all gaps and denominators; stable metadata and dispatch/availability chronology are
  checked. Its candidate is a nonauthorizing qualification artifact: no v2 runtime is installed.
- **Next horizon:** have Sol define the smallest separately versioned v2 live plan/runtime/verifier
  consuming that typed predecessor certificate and supporting a useful bounded continuation.
  Regenerate candidate windows; the 21:50/21:55Z example is not a lease and will become stale.
  Fresh clock, volume, caps/latency envelope, quiescence and review remain required; exact account
  billing is still unknown, not a global blocker. Never revive c4, re-encode a v2 predecessor as c4,
  reuse old bucket/request identities, or rewrite v1 campaign sources. Preserve raw retention until
  any codec/compaction path receives descriptor-write and replay qualification.
- Senses remains incomplete. Prioritize sustainable acquisition/recovery/retention and successor
  lineage, then independent economic witness/settlement/buyExact25 and causal population/lifecycle/
  modalities. Observer v5 and parser scaffold v3 are local feasibility only; no signed ProgramTest
  transactions or qualified fee/hook/Rent witness. Unknown/censored states remain explicit. Do not
  wait weeks in chat for future history or cross into Cortex.
