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
- Helius watch-v3 is now unloaded; its 12:00Z and 18:00Z roots have STOP records and must not be
  bridged. No v2 LaunchAgent is loaded. The v2 campaign bootstrap ran at 22:43Z on 2026-10-09:
  finalized slot 455028551, frontier 455028552, one `getSlot` reservation, 43 raw/56 zstd bytes.
  It did not fetch a block body. About 70 GiB were free at 22:45Z; recheck before each request.

## Senses facts

- `1380b99` adds a separate inventory-first finalized-block epoch and scheduler; `5348c45` adds
  its one-bucket CLI. It pins source,
  schedule and budgets; captures inventory before sampling; never replaces a selected slot; records
  failed/unknown states; resumes durable responses without replay; and enforces campaign locks,
  outstanding byte reservations and per-request disk floor. Helius charge units are unverified and
  are not equated to request counts. It has 13 passing no-RPC recovery/failure/CLI tests, Ruff and
  `git diff --check`. Bootstrap is complete; no frame body or v2 LaunchAgent has run yet. The next
  eligible real bucket is 2026-10-09 23:00Z.
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

1. Continue the finite Helius-only prospective pilot through the new CLI. Sol's locked settings: hourly,
   16 produced-slot samples, 20,000
   frame-width cap, 25 epochs including bootstrap, 817 reservations, 4 GB raw, 1 GB stored, 256/128
   MB per frame, zstd, and 50 GiB free-space floor. First run bootstrap plus three hourly frames as
   one-shot invocations; only enable the LaunchAgent on the same campaign after review. Unload
   watch-v3 first; recheck runtime/disk; do not claim global completeness.
2. After pilot evidence, ask Sol to settle a concrete Token-2022 fee/hook/account-settlement
   contract before decoder work. Continue Senses; no Cortex.
