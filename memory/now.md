# Current Project State

**As of:** 2026-10-12, descriptor-writer qualification in progress. Research checkout:
`/Users/root1/Developer/memecoin-research`; production `/Users/root1/Desktop/memecoin-ai` untouched.
Do not start Cortex. Continue Senses under Luna → Sol → Astra routing. Current source qualification
is complete; a concrete reviewed finite LaunchAgent lease is the next operational gate.

## Git and authority

- Only `main` and `research/active` remain locally and on `origin` (plus `origin/HEAD`). Obsolete
  branches were removed after ancestry/unique-commit review. No history rewrite. Continue work on
  `research/active`; regular batch pushes and verified milestone fast-forwards are policy in AGENTS.
- Verified code baseline: `35da772` (storage/migration, inactive external service adapter). The final
  documentation handoff commit is pushed to both branches, with a clean worktree. Its exact full
  `main`/`research/active`/remote hashes are recorded after push in the small host snapshot
  `~/.config/brain-research/git-handoff-v1.json`; always verify live Git before continuing. This avoids
  a self-referential commit hash inside its own tracked file.
- Current Helius key remains authorized without rotation. Credentials stay in private files/env,
  never argv/plists/logs. Latest scoped scan: zero exact secret/endpoint matches in 3,338 Git blobs,
  process argv, 45 installed LaunchAgent plists, handoff/config files. Previous Cursor redaction and
  its limits remain in the source architecture; do not reopen the user's accepted rotation decision.

## Storage: actual configuration and migration

- Primary new research-data root: `/Volumes/Kingston/BrainData` (0700), case-sensitive APFS,
  ownership enabled; mount root is writable by `root1:staff`. UUID
  `8f8f1081-9be4-4914-9111-75f1b90255bf`, logical root ID
  `9428695af6d4f46c08e1a444cdc66de8`. Private host binding:
  `~/.config/brain-research/storage-v1.json` (0600), minimum available reserve **50 GiB**.
- Code/Git/venvs/docs/config and small operational state stay internal. Future large roots use the
  explicit binding and relative paths, not unchecked `work/` paths. No internal fallback.
- Three closed roots moved to `BrainData/archive/work/`, **18,285,310,608 logical bytes** total:
  `brain-senses-live-helius-public-stratified-20261006-09`,
  `brain-senses-live-helius-alchemy-512-pair64-20261009`,
  `brain-senses-live-helius-alchemy-512-20261009`. Originals were released only after exact manifests,
  independent verification, application replay on real external paths and reversible old-path aliases,
  unchanged-original rehash, then final flag-aware archive seals. Old paths are read-only compatibility
  symlinks. Archives have owner-reversible `uchg`; hashes/receipts remain the integrity authority.
- Manifests are beside archive roots; verification/locator/seal/release receipts and reproduction
  scripts are under `BrainData/migration/20261011/` (operator copy manifest under `operator/`). v2
  remains strict; v3 separately records and verifies only OS-added destination `com.apple.provenance`
  absent from source. Generic migration verification precedes `uchg`; use the final seal afterward.
- Deliberately retained internal: System/Sparse/Population qualification roots (~4.4 GiB combined;
  frozen Experience/Snapshot readers reject symlink ancestors), dirty Agave source plus mutable
  `target/` (~7 GiB; qualify guarded external `CARGO_TARGET_DIR` before future rebuilds), environments,
  c4 and other frozen absolute-path roots. No protected payload read/move and no duplicate copy of
  these exceptions. Do not blanket-move `work/` or delete dirty Agave work.
- At receipt time: internal free **44.61 GiB**, external free **205.86 GiB**. No market collection is
  currently growing the corpus. Prior bounded calibration was 14.9 GB/day; at a conservative
  **18 GiB/day**, external runway above the 50-GiB floor is **8.66 days**. This is a scenario, not a
  new lease or measured current rate. The 240-GB tier is temporary; a larger tier/replication will be
  needed for sustained history. One external copy is not a backup or recovery-from-hardware-loss claim.

## Runtime and recovery

- All **15** legacy Senses agents are disabled/unloaded; installed plists were hash-verified and
  archived under `~/.config/brain-research/retired-launchagents-20261011/`. No market collector is
  active. Unrelated production owner services were not changed. No provider request was made here.
- Enabled research agent: `ai.memecoin.brain.research-storage-health-v1` (RunAtLoad + every 300 s).
  It only reads storage and writes bounded private internal
  `~/.config/brain-research/storage-health/status.json`; stdout/stderr are `/dev/null`. Latest status
  `ready`, last tested exit 0. Actual unmount/remount produced `ready → storage_unavailable → ready`
  with no fallback corpus; reload also passed. Physical cable removal/login persistence remain untested.
- Owned APFS scratch checks passed file/directory fsync, enforced readonly mode, create-only
  collision, same-volume rename/hash replay, and independent-process flock exclusion.
- **Live writer remains gated:** `tools/brain_senses_external_service_v1.py` is an inactive prototype.
  `EXTERNAL_WRITER_DETACH_QUALIFIED = False` blocks initialization/ticks before writes/client loading.
  Guard checks cannot make frozen absolute-path recursive writes safe against detach between check
  and lookup. A separately versioned dirfd-bound writer plus forced detach-at-write testing is required;
  do not simply flip the flag. Storage migration itself is descriptor-anchored and qualified.
- c4 remains terminal: STOP `minimum_free_disk_floor_plus_frame_envelope`, observed frontier
  **455430160**, last attempted bucket `20261010T230000Z`, active-frame reservation **reconciled**.
  Historical gaps remain explicit. No c4 resume, missed-slot backfill, or ambiguous-request replay.

## Senses boundary and next Luna horizon

- Readiness remains approximately **60%**, a management estimate, not a metric. Senses is incomplete.
  Closed foundations include temporal/provenance/missing-state contracts, OHLCV baseline, sparse
  experience plumbing, selected-class sell23 ledger v2 (1,255: 410 exact target-account settlements,
  843 censored native settlements, 2 unsupported), selected-block provider comparisons and bounded
  create_pool source-interface matching. These are scoped qualifications, not market-wide economic
  completeness or training/trading admission. External corpus storage is now usable with explicit
  legacy exceptions; safe autonomous market writing is still a separate release gate.
- Large remaining gates: dependable prospective operations and resource/retention monitoring;
  independent economic-process qualification (including buyExact25/native settlement candidate-position
  evidence); broader causally enrolled population/lifecycle/modalities and systematic unknown-case
  qualification. Those have work that can be done now. Weeks/months of future history cannot be
  manufactured and must be separated from engineering work. Cortex bottleneck has not yet been reached.
- **Current decision horizon:** the separate descriptor-bound writer/orchestrator/independent verifier
  and path-only LaunchAgent CLI are implemented. Ten forced detach-at-write points, reservation/remount
  no-replay and real Kingston scratch ownership/lock/fsync checks pass. Final pins are detach/recovery
  v5 and Kingston v4 receipts dated 2026-10-12. The inactive adapter remains blocked. Sol reviews the
  concrete future bootstrap plus one-frame lease before activation; c4 stays terminal.
- Helius account MCP now authenticates the existing key, but no JWT/credit meter is available. Charge
  remains unknown. User resource authorization permits bounded Free acquisition without an exact
  account meter; this is no global blocker. Alchemy aggregate usage remains 18,680 CU/$0.01, not
  workload attribution. See the 2026-10-11 v2 provider-account and external-readiness receipts.
- New focused suite: 78 passed; new-file Ruff/format and targeted mypy passed; synthetic smoke passed.
  Full make check is not green: 107 pre-existing lint errors in eight unchanged frozen files, plus
  36 baseline mypy errors in five unchanged WSS files. Full pytest runner diagnosis is in progress.
  Do not rewrite frozen campaign sources to hide these pre-existing failures.
- After that bounded horizon, replan within Senses: the 608-slot System/ATA census leaves 675
  seven-account ATA shapes (636 Rent-key, 39 other-key) as unknown extra-account partitions; buyExact25
  is deferred without a candidate-position witness. Dirty Agave work is preserved, not qualified.
  Use evidence-dependent Sol decisions; do not start a venue/model campaign merely to create activity.
- Latest receipt: `docs/evidence/senses-external-storage-handoff-2026-10-11.json` (runtime, capacity,
  migration hashes, credential scope and validation). **188 focused tests**, Ruff, targeted mypy
  (4 new source files) and synthetic `make smoke` passed. Canonical storage/release contracts are in
  `docs/brain-source-architecture.md`; do not reread the entire evidence archive.
