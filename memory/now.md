# Current Project State

**As of:** 2026-10-12 03:54Z. Checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, clean at `67512ff067e15e84fc67bc19a5e676a951747641` and pushed to origin.
Handoff `17415e81e93fdec4f1682e3769e546cbbcf2555d` is an ancestor. `main` remains
`148ca165e1487a99c3ce6532dbc276c4922c31b6`. Production/champion/protected evaluation/real trading
remain untouched; no signing/submission; Cortex is out of scope.

## Live state

- Kingston storage health is `ready`, root ID `9428695af6d4f46c08e1a444cdc66de8`, UUID
  `8f8f1081-9be4-4914-9111-75f1b90255bf`; about 206 GiB free, above floor plus one reviewed lease
  envelope. Do not format/move frozen roots; no internal-storage fallback.
- c4 is terminal STOP `minimum_free_disk_floor_plus_frame_envelope`, frontier `455430160`, final
  reservation reconciled; no backfill. Its STOP and terminal receipt hashes were rechecked.
- Sealed v1 predecessor verifies at frontier `455802008`. Failed v2 sibling `external-v2-20261012T0230Z`
  independently verifies stopped with zero reservations, intents, seals, and provider requests. It
  remains disabled/unloaded and separate from predecessor lineage.
- The 03:40Z status-only timing rehearsal passed and is disabled/unloaded. Fresh clock corroboration
  passed at 03:49Z. Helius account plan/usage/charges remain unknown; latest Alchemy aggregate is
  recorded in the 03:42Z provider receipt. No new provider lease is active.
- v2 status CLI now serializes set-valued verifier state deterministically; existing old activation
  source pins are stale by design after that source change. The new plan must pin current sources.
- Structural unknown queue v1 is committed and synthetic-ready only. c4 cannot supply the required
  transaction partitions/reference membership/post-fsync availability; no queue was published.
- Sol's next horizon is an offline sparse-observation reference for 307 enrolled markets, preserving
  the frozen 170/137 roles and every missing/failed/unsupported entity. Luna is implementing from
  role-separated qualified bundles. Output is historical-replay structure only: no point-in-time
  eligibility, lifecycle inference, labels, settlement, or training admission.

## Next horizons

1. Prepare one fresh finite v2 Helius lease within Sol's unchanged envelope. Recheck dual clock,
   storage, exact source hashes, launch inventory, c4 STOP, v1 predecessor, and terminal v2 sibling;
   compile a future-only candidate/plan and obtain Sol's final review of exact plan, qualification,
   activation and plist hashes before offline init/load/bootstrap. Never replay the missed window.
2. Complete and independently verify sparse-observation coverage/reference artifact; update canonical
   owners and commit/push each accepted engineering boundary.
3. Continue the highest-value non-waiting Senses horizon after these results. Stop only at overall
   Senses engineering-complete boundary or a true external blocker. Protected samples remain closed.

Capture-root caution: avoid wildcard reads; use exact pinned files and metadata-only summaries where
possible. The external writer remains source-pinned and unchanged.
