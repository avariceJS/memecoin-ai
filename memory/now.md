# Current Project State

**As of:** 2026-10-12 02:56Z. Checkout: `/Users/root1/Developer/memecoin-research`, branch
`research/active`; `main` remains `148ca165e1487a99c3ce6532dbc276c4922c31b6`. Published active is
`a7f082d`; local work extends the no-provider timing rehearsal. The first 03:15Z rehearsal binding
was explicitly disabled/unloaded before its callback when Sol identified a timing-evidence gap; it
made no status call and no provider request. Replacement activation waits for tightened code review.

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
- Helius account/charge evidence remains unavailable; that lease made no RPC requests. Alchemy
  read-only aggregate remains 18,680 CU / $0.01 without limit or workload attribution.
- Agave fetch closure remains terminal after the one separately reviewed v2 `cargo fetch --locked`
  failure (exit 101, exact cause unavailable). Partial cache is preserved and must not be reused or
  cleaned. No retry, metadata, or build. Receipt:
  `docs/evidence/agave-dependency-fetch-attempt-v2-2026-10-12.json`.

## Current horizon

A separately versioned status-only timing rehearsal is pinned to the failed-v2 plan, terminal receipt,
source and one calendar minute. Sol accepted the process-local old-runtime-gate shim but required
callback/preflight/status start <= +15s, status finish <= +30s, and exact terminal semantic state
(frontier 455802008, verified predecessor, zero IO). These fixes and fail-closed tests are in progress;
no provider lease is implied. If a real callback qualifies, refresh clock/storage/source/account facts
and seek a separate Sol review for a prospective Helius lease. The missed interval stays unknown and
never replays. c4 remains terminal.

Continue independent Senses horizons after each meaningful gate. Preserve event, receipt,
availability and decision times; immutable provenance; explicit unknown/censored states. Protected
samples stay unopened. Commit/push completed scopes to `origin/research/active`; sync `main` only
after a verified capability milestone. Keep documentation in English and update canonical owners.
