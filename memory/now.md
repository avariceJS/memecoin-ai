# Current Project State

**As of:** 2026-10-13 11:56Z. Owner checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, HEAD and `origin/research/active` `4b90dde`.
`main` and `origin/main` remain `770699d`. Preserve unrelated dirty build-guard/Agave work.

## Prospective acquisition

- Kingston `/Volumes/Kingston/BrainData` remains bound to root ID
  `9428695af6d4f46c08e1a444cdc66de8`, UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`; 202 GiB
  free with a 50-GiB floor. No internal-storage fallback.
- The first v6 release `259c20d752fa383c3bb42637d10fb5da` is read-only: zero plan/intent/dispatch/
  response events. Its LaunchAgent was unloaded after 22 failed callbacks (exit 2). There is no
  active prospective collector and no new provider I/O.
- Sol approved a fixed release-qualified control pair for fresh successor
  `7a10e2d4c9f84c18a0b53d7e6f214c90`, no active pointer, bounded sanitized diagnostics outside
  Kingston, and a 45-minute future anchor. Additive inventory-v3 now verifies the exact historical
  41-root/62-attempt projection and independently replays all present v6 roots. Live read-only replay
  passed for 42 roots, including the old v6 zero-attempt root; successor is absent and prospective
  capacity is admitted. Focused tests (52), Ruff, and smoke pass. Source/docs/tests are being committed
  before create-only bootstrap. No backfill/retry; old root/control remain read-only.
- The v6 policy remains six selected pools per 15-minute bucket, one forward-only request per slot,
  12-second minimum spacing, bounded per-day/rolling request and byte caps, 16-GiB resident ceiling,
  and 50-GiB free-space floor. Capture does not qualify market source, price coverage, training, or
  economics. Provider charge remains unknown/unverified.

## Senses gates

- Source changes are committed/pushed through `4b90dde`; the current inventory-v3 scope is local and
  uncommitted. Focused v6 tests, Ruff, smoke, and live read-only census pass. Repository-wide `make
  check` currently fails in unrelated WSS files with 107 Ruff findings; do not mass-format them.
- Candidate-position witness, native settlement, and `buyExact25` remain unqualified. Broader
  lifecycle/population and modality coverage remain open. c4 is terminal; do not start Cortex.
- No Senses acquisition LaunchAgent is loaded now. After the successor collection/recovery check,
  stop expanding writer qualification and prioritize broad population/lifecycle and economic witness.
