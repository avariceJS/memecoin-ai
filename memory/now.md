# Current Project State

**As of:** 2026-10-12 02:50Z. Active checkout: `/Users/root1/Developer/memecoin-research`, branch
`research/active`. Published active: `bcf6624`; `main`: `148ca165e1487a99c3ce6532dbc276c4922c31b6`.
The next gate is a separately versioned no-provider LaunchAgent timing rehearsal for the terminal
Helius external-v2 status path, then a fresh Sol-reviewed prospective lease if timing qualifies.

Production runtime, champion, protected evaluation and real trading remain untouched. No signing or
submission. Do not start Cortex.

## Live state

- Kingston `/Volumes/Kingston/BrainData` is bound to UUID
  `8f8f1081-9be4-4914-9111-75f1b90255bf`, root ID `9428695af6d4f46c08e1a444cdc66de8`; health
  is ready with ~206 GiB free, above the 50-GiB floor. No formatting or internal fallback.
- Helius external-v2 02:30–03:33Z is terminal `missed_bootstrap`, with verified v1 predecessor,
  zero provider requests/reservations/intents/sealed buckets, and disabled/unloaded LaunchAgent.
  No retry, catch-up, backfill, shift, or later-frame activation. Receipt:
  `docs/evidence/brain-senses-external-v2-missed-bootstrap-terminal-2026-10-12.json`.
- Helius account/charge evidence remains unavailable; the failed lease made no RPC requests.
  Alchemy read-only aggregate remains 18,680 CU / $0.01 without limit or workload attribution.

## Current horizon

- Agave fetch closure remains terminal after the one separately approved v2 `cargo fetch --locked`
  failure (exit 101, exact cause unavailable). Partial cache is preserved and must not be reused or
  cleaned. No source/lock changes, target artifacts, retry, metadata, or build. See
  `docs/evidence/agave-dependency-fetch-attempt-v2-2026-10-12.json`.
- Implementing a status-only timing rehearsal bound to the failed-v2 plan, terminal receipt, source,
  and exact one-minute LaunchAgent. It must prove the old v2 status path finishes within 60 seconds,
  makes no provider/dispatch calls, and leaves service manifest/events/STOP unchanged. No provider
  lease is approved by this rehearsal. Focused implementation tests pass; real calendar callback
  and evidence remain pending.
- If it passes, refresh clock/storage/source/account evidence and seek a separate finite prospective
  Helius lease review. The missed interval stays unknown and is never replayed. c4 remains terminal.

Continue independent Senses horizons after each meaningful gate. Preserve event, receipt,
availability and decision times; provenance; explicit unknown/censored states. Protected samples stay
unopened. Commit/push complete scopes to `origin/research/active`; sync `main` only after a verified
capability milestone. Keep documentation in English and update canonical owners.
