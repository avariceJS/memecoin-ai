# Current Project State

**As of:** 2026-10-10 Europe/Moscow. Read `docs/INDEX.md`, then verify Git and runtime before
acting.

## Boundaries

Build scalable, point-in-time Solana market experience; no demonstrated trading edge. Keep
`/Users/root1/Desktop/memecoin-ai`, real money/signing, protected F01/D43 and reserved holdouts,
promotions, and remote pushes untouched. Do not start Cortex before Senses is objectively ready.

## Host and prospective runtime

- Research checkout: `/Users/root1/Developer/memecoin-research`, branch `research/active`.
  Production checkout is separate and unchanged.
- About 70 GiB free at last check. Helius account usage meter is unavailable. Alchemy MCP responds,
  but its aggregate team CU meter is not an account/app limit. No paid request was made.
- Pair64 and older finalized-block campaigns are stopped/unloaded. The 2026-10-09 15:00Z captures
  and tracked offline audit receipt remain in place. The audit confirms bounded 64-slot provider
  response agreement; Alchemy `getBlocks` failed, so its inventory and unsampled slots remain
  unknown. See `docs/brain-source-architecture.md` and
  `docs/evidence/helius-alchemy-pair64-offline-audit-2026-10-09.json`.
- Helius address watch-v3 is the only loaded prospective sensor in this group; it runs on a
  five-minute schedule and is idle between invocations. The 12:00Z and 18:00Z roots have STOP
  records; do not bridge those gaps. Unload it before enabling the new block sensor.
- Disk and LaunchAgent state were checked 2026-10-10 01:17 Moscow: ~70 GiB free; no v2 block
  LaunchAgent. Recheck before runtime changes.

## Senses facts

- `1380b99` adds a separate inventory-first finalized-block epoch and scheduler. It pins source,
  schedule and budgets; captures inventory before sampling; never replaces a selected slot; records
  failed/unknown states; resumes durable responses without replay; and enforces campaign locks,
  outstanding byte reservations and per-request disk floor. Helius charge units are unverified and
  are not equated to request counts. It has 12 passing no-RPC recovery/failure tests, Ruff and
  `git diff --check`. No live run or LaunchAgent yet.
- Previous 512-slot/hour Helius/Alchemy epoch: 512/512 Helius selected bodies self-check; 64/64
  Alchemy same-slot pairs match block identity and ordered signatures. Of 80,467 paired transactions,
  146 differences were exclusively Helius `meta.logMessages` truncation with explicit markers.
  This is sampled payload agreement, not independent discovery or economic agreement.
- PumpSwap sell23 remains diagnostic: 1,255 candidates, 410 exact full account settlements, 843
  censored native settlements, two unsupported token instructions. No wallet-net price or admission.
- PumpSwap buyExact25 v1 has a new one-shot 512-block audit receipt; seven dual-legacy refs had
  exact event/transfer facts, while Token-2022 settlement/layouts remain quarantined. No training,
  execution or edge claim. Details and limitations are in `docs/training-evaluation.md`.
- Senses is not complete: broad market/process completeness, lifecycle, provider-independent
  discovery, and prospective continuity remain limited. Preserve unknown, gap, censored, failed,
  stopped, unsupported and immature states; never convert these to zero.

## Next horizon

1. Finish review of `1380b99`, add the bounded scheduler CLI/service entry point, then run a finite
   Helius-only prospective pilot. Before enabling it, unload watch-v3; verify actual LaunchAgent
   execution, continuity, failures, bytes and recovery. Keep caps under measured disk/provider
   constraints; do not claim global completeness.
2. After pilot evidence, ask Sol to settle a concrete Token-2022 fee/hook/account-settlement
   contract before decoder work. Continue Senses; no Cortex.
