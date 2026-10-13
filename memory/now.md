# Current Project State

**As of:** 2026-10-13 04:23Z. Owner checkout `/Users/root1/Developer/memecoin-research`, branch
`research/active`, HEAD `561876a` (same as origin at last verification); `main` is `19c2aa3`.
Working tree contains complete-but-uncommitted Gecko v4 canary work plus separate Agave,
build-guard, account-feasibility, and Gecko-lane scopes. Keep unrelated scopes separate. Production,
champion, protected data, real trading, and Cortex remain untouched.

## Prospective acquisition

- Kingston `/Volumes/Kingston/BrainData` is bound to root ID
  `9428695af6d4f46c08e1a444cdc66de8`, UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`, device
  `16777242`/inode `400`; about 202 GiB free, above the 50-GiB floor. Descriptor-bound writes only;
  no internal-storage fallback.
- GeckoTerminal-only release `3569c27ff9916382c209ea71ef23e345` is running through LaunchAgents
  independent of this chat: collector callback every 12 seconds, watchdog every 15 seconds, no
  calendar expiry. Its first bucket passed at 04:20Z with six HTTP 200 responses / 4,688 bytes,
  six durable confirmations, no misses, no eligible bars, and passed timing proof. Bootout/bootstrap
  preserved all six request chains without replay. The next untouched bucket closes at 04:30Z.
  Global v2 replay after watchdog publication: 41 roots / 58 attempts, provider I/O disabled.
  Receipt: `docs/evidence/gecko-capture-only-v4-live-observation-2026-10-13T0421Z.json`.
- Daily/rolling caps remain 576 requests / 10 MiB and 17,280 / 300 MiB; 16-GiB resident ceiling
  and 50-GiB free floor. Provider charge is unknown/unverified; market-source qualification remains
  false. Helius v5r2 remains offline-only until its 19:00Z anchor; Alchemy is not collecting.

## Senses gates

- Candidate-position witness, native settlement, and `buyExact25` remain independently unqualified;
  do not convert synthetic traces into public-chain finality. c4 remains terminal.
- Agave build runner is NO-GO on the owner Mac until mount-detach isolation uses an unprivileged
  identity or isolated VM. This does not block continuous bounded market collection.
- Next: verify a second natural Gecko bucket and recovery, then advance the approved candidate-position
  witness source horizon. Market-source, training, and economic admission remain false; Senses is not
  at the Cortex boundary.
