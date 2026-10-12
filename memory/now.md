# Current Project State

**As of:** 2026-10-12 01:55Z. Next live horizon: final preflight for the finite v2 prospective lease.
Research checkout: `/Users/root1/Developer/memecoin-research`, branch `research/active`.
Production `/Users/root1/Desktop/memecoin-ai`, champion, paper ledgers, protected evaluation and
real trading remain untouched. No signing/submission. Do not start Cortex.

## Git and live runtime

- Last verified published state before this work was active `ca6e173`, main `17415e8`; two active-only
  commits. This turn has uncommitted guard/receipt/docs changes; inspect status before commit. The host
  `git-handoff-v1.json` still records `17415e8`.
- c4 remains terminal STOP `minimum_free_disk_floor_plus_frame_envelope` at frontier `455430160`,
  reservation reconciled; metadata-only terminal receipt and pins were rechecked. No c4 replay,
  backfill, or reactivation.
- v2 plan is private at `~/.config/brain-research/external-v2-20261012T0230Z/lease-plan.json`
  (SHA-256 `d878c52a…26dc`); the path-only LaunchAgent plist is `launchagent.plist`
  (SHA-256 `1bc7bbc7…d6ea`). Runtime gate passes; label is not loaded, plan/root is not initialized,
  and no v2 request has occurred. Planned finite window is 02:30–03:33Z, 409 requests, 4 RPS,
  3.104 GB payload and existing 50-GiB floor + peak/metadata margins. Final clock, launch census,
  capacity/predecessor revalidation and Sol review are due near 02:20Z. No schedule shift or catch-up.
- v1 finite lease is sealed and retired: 35 requests, 153 files unchanged after terminal recovery,
  frontier `455802008`; its four gaps remain explicit. Old agents remain retired; research storage
  health monitor is ready. Helius account usage and actual lease charge remain unknown/unverified;
  Alchemy aggregate meter is not workload attribution.

## Storage and build qualification

- Kingston `/Volumes/Kingston/BrainData` is verified case-sensitive APFS, owners enabled, 0700 root;
  UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`, root ID
  `9428695af6d4f46c08e1a444cdc66de8`, binding `~/.config/brain-research/storage-v1.json`.
  Latest free space is about 206 GiB; health is ready; 50 GiB is the floor. No format, fallback,
  unchecked alias, or frozen-root move/deletion.
- Sol approved the bounded build-storage/source-closure gate, not a large Agave build. The new
  supervisor and its focused suite are source-bound in
  `docs/evidence/brain-senses-build-storage-guard-apfs-qualification-2026-10-12.json` (9 tests pass).
  On disposable APFS, detach-time `mkdir`, create, tmp write, rename and held-cwd writes all failed
  with `ENOENT`; no underlay/delayed artifact or supervised child remained, and remount UUID matched.
  Real tiny Cargo build-script liveness also passed; its post-detach write boundary hung and was
  terminated. Capacity overrides and ownership-disabled volume were test-only; the temporary image
  and control files are removed. The guard is not a build-script sandbox: inspect pinned sources and
  forbid process-group escape before use. Existing dirty Agave checkout/shared target remain untouched.
- Before any Agave checkout/build, verify official source/lock pins and exact patch, resolve the
  actual offline dependency closure, place checkout/Cargo home/target/tmp under Kingston, inspect
  Cargo config and CLI target overrides, and revalidate identity/space under the guard. No large
  build until Sol reviews source closure and its exact command/process census.

## Evidence and next work

- v2 predecessor certificate is bound to the sealed v1 root, terminal time, frontier, all gaps and
  dispatch/availability chronology. c4 remains terminal. At 02:20Z, collect a fresh dual TLS Date
  pair, recheck exact plist/labels, health/identity/free space and predecessor seals, obtain final Sol
  activation review, initialize offline under locks, validate, then load only the finite label. After
  activation, prove the real bootstrap checkpoint and terminal recovery before further action.
- Continue Senses engineering after that lease horizon: independent candidate-position/native-
  settlement/buyExact25 evidence, then broad causal population, lifecycle and modalities; improve
  acquisition/recovery, monitoring, retention and unknown/anomaly qualification. Current witness,
  fee/hook/Rent and parser evidence is synthetic/local feasibility only. Preserve event/receipt/
  availability/decision time, provenance, and unknown/censored states. Do not wait weeks in chat for
  future observations or cross into Cortex.
- Documentation is English. Update canonical owners and this short state; keep raw receipts immutable.
  Push complete work to `origin/research/active`; after a verified capability milestone fast-forward
  and push `main` without rewriting history.
