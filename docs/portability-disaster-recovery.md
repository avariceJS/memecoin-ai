# Portability and Disaster Recovery

This document owns Brain host portability, recovery inventory, and erase readiness. The project
checkout, owner runtime, large research-data root, and protected evaluations have separate authority
and recovery boundaries. A clean code clone is not a recovery of Brain state.

## Current readiness

**NO-GO for factory reset as of 2026-10-15.** The accepted recovery scope is loss or erasure of the
owner Mac while Kingston remains healthy. The user explicitly accepts Kingston as the only
reset-surviving physical copy and accepts unencrypted local custody for this iteration. Kingston
failure, theft, or corruption remains an accepted uncovered risk, not a blocker for the Mac-loss
claim. A future 1-TB+ SSD, NAS, or other independent destination must add a separately verified
replica and a Kingston-loss rehearsal.

Live checks on the current Mac:

- On 2026-10-14 at 14:51Z, a descriptor-bound, metadata-only census published and reread-verified
  its private JSONL manifest and completion marker on the pinned Kingston root (299,700,736 bytes).
  It covered nine declared roots and four Git bindings; all roots remained on the pinned Data UUID
  and no separate Git metadata roots were found. The census is **incomplete**: one directory in
  `owner_checkout` changed metadata during traversal, so `scan_complete=false`. The exact locator
  is retained only in the private Kingston manifest. The other eight roots passed their included
  traversal checks; three research `wallets` roots and owner Codex `.local` remain explicitly
  opaque, backup-required exclusions. Two `.env.local` files were excluded for credential reissue.
  No application, credential, symlink-target, xattr, or protected payload bytes were read; no
  provider request or service change occurred. This sequential census is neither a common source
  point nor root-closure, custody, or erase evidence. Receipt:
  [`declared-root boundary census`](evidence/portability-dr-root-boundary-census-2026-10-14T1451Z.json).
- Read-only owner runtime refresh (2026-10-15 21:32:34 UTC) found 35 Brain/D43 labels loaded, 12
  with PIDs, and 12 cwd-matched workspace processes (nine owner, three research, including the
  status probe). Both Gecko canary watchdogs had PIDs; storage was not inspected. No arguments or
  environments were read and no services changed. A preceding same-session point check returned 15
  labels with PIDs and showed `gecko-capture-only-v6` running; this label was absent from the saved
  21:32Z check. These short-window changes do not establish a write frontier or source quiescence,
  so the owner census/source cutoff remains blocked. Receipt:
  [`live runtime refresh`](evidence/portability-dr-live-runtime-refresh-2026-10-15T2132Z.json).
- A bounded allowlisted projection at 22:27Z revalidated Kingston UUID/root and pinned the v6
  Gecko capture activation, its exact repository plist, source manifest, epoch, and opaque journal
  counters. The existing activation still says `capture_authorized=true`; its 60-second scheduled
  label was loaded. The journal had 21 events: five intents, four dispatches, five outcomes, five
  confirmations, one missed slot, and zero unresolved intents. Both 15-second canary watchdogs had
  terminal receipts (`passed` and `failed_closed`). A pinned source-manifest self-hash was valid,
  but one of its 15 source files no longer matched the activation source commit:
  `src/memecoin_research_storage/__init__.py`. The current tick implementation verifies this set
  before work and therefore fails closed; no tick or request was invoked by the DR probe. The
  projection is recorded in the
  [`Senses pre-quiescence receipt`](evidence/portability-dr-senses-prequiescence-2026-10-15T2227Z.json).
- After confirming terminal journal/watchdog state and no active callback PIDs, the exact v6 label
  and its two terminal canary watchdog labels were disabled and unloaded. No callback was killed;
  the Kingston journal hash stayed unchanged. All three remained absent and disabled across 25
  checks over 75 seconds, longer than the v6 60-second schedule; legacy v3/v4 capture labels
  remained unloaded. Their prior enable/load state and exact plist hashes are in the pre-quiescence
  receipt; re-enabling is a separate owner-authorized action. No other label was changed. This
  closes only this Senses timer/write loop, not the owner-wide source frontier: paper/UI processes
  and the loaded D43 scheduler remain outside the quiescence claim. See the
  [`Senses quiescence receipt`](evidence/portability-dr-senses-quiescence-2026-10-15T2230Z.json).
- At 22:35Z, the legacy `ai.memecoin.brain.b09r2.source` launcher was bound to its exact deployed
  runtime: the 154-file deployment bundle and manifest verified, and the imported runtime tree
  matched that bundle. The deployed checkout was `a5944742b6b8ed0466c50bdeb72370ed3e3f2a31`, an
  ancestor of current `research/active`. The source activation was expired and its STOP marker
  recorded `source_worker_failure`; no callback PID was present. Its hash-valid journal had 3,605
  events, including ten reservations without outcomes. Following Sol review, only this exact
  five-second launchd label was disabled and unloaded; no process was killed, no reservation was
  replayed, and the source root was retained. It remained unloaded for 15 seconds with no journal
  change. This prevents automatic re-entry into that fail-closed source worker while preserving
  unresolved attempts as possibly spent history. The root remains host-local and its complete
  Kingston custody is not established. This closes one launch surface only; operator/UI and D43
  remain untouched, and there is no owner-wide cutoff or erase qualification. See the
  [`B09 source quiescence receipt`](evidence/portability-dr-b09-source-quiescence-2026-10-15T2235Z.json).
- A subsequent real v2 root-boundary census completed endpoint-stable metadata traversal for all
  eight declared-present roots (819,063 entries) and published a 317,360,545-byte private manifest
  plus completion marker to the pinned Kingston root; descriptor-bound readback matched both
  receipt hashes. The ninth required role, the previously declared temporary verifier worktree,
  was absent and explicitly retained as unresolved; it was not recreated. Three research `wallets`
  subtrees and owner Codex `.local` remain opaque and backup-required, and two credential-named
  entries remain excluded for reissue. This v2 run supersedes the prior incomplete v1 only as the
  latest traversal evidence for its corresponding present-root scope. It is sequential live
  metadata, not an atomic source point, root closure, complete custody, or erase evidence. Receipt:
  [`v2 root-boundary census`](evidence/portability-dr-root-boundary-census-v2-2026-10-15T2239Z.json).
- The census CLI supports relocated hosts through an explicit mode-0600
  `brain-host-root-roster-v2` JSON input. Required logical roles must be declared as present or
  absent with an explicit disposition and reason; present roots carry absolute local paths, kinds,
  optional metadata-only exclusions, and optional checkout-role keys for bounded Git admin/common
  path derivation. It does not discover Desktop/tmp paths or Codex worktrees. Source volume UUID
  and mount point are roster pins; Kingston UUID and root ID are independently roster-pinned and
  checked against storage configuration. The v2 input uses `schema`, `version`,
  `source_volume_uuid`, `source_mount_point`, `storage_volume_uuid`, `storage_root_id`,
  `required_roles`, and `roles`; each role has `key` and `state`. Present roles use `path`,
  `expected_kind`, optional `metadata_only_exclusions`, `opaque_expectations`, and
  `git_checkout_key`, while absent roles require `disposition` and `reason`. The historical v1
  profile remains available only through
  `--legacy-profile`. The exact mode-0600 roster bytes and SHA-256 are held in the private manifest
  and the completion marker binds the digest. Required-role validation checks only the roster's
  internal consistency; it does not prove that an independently approved owner-host inventory is
  complete. A complete scan requires at least one present root and means traversal completed for
  declared present roots on the pinned source volume; it never proves root closure, a source cutoff,
  restore readiness, or erase safety. Git metadata locators inside an explicit exclusion fail
  closed instead of scanning around it. This interface change has only synthetic focused coverage
  so far; no owner census was rerun and no live recovery qualification changed.
- The exact-commit bootstrap helper was hardened and rehearsed at
  `8544c4d82ebc7dbf277cab5e68715e279f2b267b` from a disposable local bare Git fixture. It created a
  clean final checkout, verified matching HEAD and an unchanged dependency-lock SHA, ran `pip
  check` and smoke, and isolated Python venv/install/check/smoke stages under a temporary HOME with
  pip config/cache and Python user-site disabled plus explicit `https://pypi.org/simple`.
  Deliberately invalid host pip index/config/cache/Python-path values did not contaminate those
  stages; unknown commit and preexisting destination cases failed closed. The helper deliberately
  leaves Git clone/fetch and the initial Python-version probe in the caller environment so normal
  user-controlled Git authentication can work. This was a same-Mac rehearsal with Python 3.13.6,
  not a replacement-host test; private GitHub auth, artifact-hash reproducibility, and offline
  dependency availability remain unproven. Package downloads from PyPI occurred; no Brain provider
  requests, storage changes, or service changes occurred. This verifies code bootstrap only, not
  Brain state recovery or erase readiness. Receipt: [`isolated bootstrap rehearsal`](evidence/portability-dr-isolated-bootstrap-rehearsal-2026-10-14T1836Z.json).
- The earlier exact-code clean-clone/bootstrap rehearsal used
  `7d8dc82f94e5c9e2631bc38ae79f1229cccdeea1`, an ancestor of the published DR changes on
  `research/active`. The later Kingston operator-flow rehearsal used current commit
  `89ff00f1f32d5aa2043f6729d78190a03ff4c5b6` with only synthetic artifacts; its scope and limits
  are recorded below. The immutable inventory receipt
  records the prior source cutoff at `eaedeae61cd101882835095c2f54158ca07a2945`. `main` and
  `origin/main` remain at `770699d3ef8558518ae750b6d5d6cab41c2e82c8`; do not advance `main` until
  the capability is qualified.
- A clean-`HOME`, empty-environment bootstrap probe at the current published DR commit stopped at
  `git clone`: the private remote requires user-controlled GitHub authentication. The isolated
  attempt created no checkout or environment and changed no persistent Kingston or production state;
  its temporary home was removed. Replacement-host login is therefore an explicit restore prerequisite; no auth database or
  credential was copied. A full authenticated isolated bootstrap remains unproven. See the
  [`clean-home Git authentication receipt`](evidence/portability-dr-clean-home-git-auth-boundary-2026-10-14T0208Z.json).
- The owner checkout at `/Users/root1/Desktop/memecoin-ai` was clean at
  `a5944742b6b8ed0466c50bdeb72370ed3e3f2a31` on branch `main`. Its cached `origin/main` is
  `6feba83525c0d21392432c53becd79e8901650ea`, 15 commits ahead; a read-only live query now returns
  `770699d3ef8558518ae750b6d5d6cab41c2e82c8`, matching research `origin/main`. That object is not
  present in the owner clone. Existing objects in the research checkout establish owner `HEAD` as
  507 commits behind live `main`, and cached `origin/main` as 492 commits behind it. This resolves
  stale tracking-ref ancestry but not which bundle, activation pointer, or state owner services use.
  See the [`owner Git remote check`](evidence/portability-dr-owner-git-remote-check-2026-10-14T0122Z.json)
  and [`ancestry receipt`](evidence/portability-dr-owner-git-ancestry-2026-10-14T0123Z.json).
  Read-only B01–B04 status reports all four scopes STOPped and expired. They retain 48, 48, 19, and
  28 pending predictions respectively; these
  remain pending and are not to be resumed. This status does not audit owner operator/paper state.
  The owner operator and several owner paper/UI processes remain running.
  A 02:43Z read-only probe found 18 combined Brain/D43 labels loaded, nine with PIDs, and 72
  cwd-matched owner/research processes, including three owner Python paper/operator processes. It
  read no arguments or environments and made no changes; this point-in-time observation does not
  establish a write frontier or consistent source view.
- An earlier metadata-only census counted 330,273 regular files and 108 symlinks in research `work/`, and
  285,577 regular files and 18 symlinks in owner `work/` (about 28.6 GB combined). It read no file
  bytes and found no unreadable entries. These are large mixed state trees, not classified recovery
  sets. Across consecutive full scans, owner `work/` kept the same entry counts but its apparent
  regular-file bytes fell by 594,048 and its membership/metadata digest changed. A short repeat
  observed metadata changes to three owner entries, including one growing log (+56,423 bytes) and
  one SQLite file; no entries were added or removed. Research `work/` metadata remained stable over
  that short repeat. This confirms active owner-state churn and does not bound byte writes or qualify
  a snapshot. A point-in-time `lsof` metadata pass over 12 owner-cwd processes found 26 open-file
  records within owner `work/`; it found no named SQLite-family descriptors within either `work/`
  root. This does not establish inactivity or a write frontier. Receipt:
  [`host metadata census`](evidence/portability-dr-host-metadata-census-2026-10-14T0153Z.json).
  Both trees still need complete recovery classification.
  `~/.config/brain-research` occupies about 348 KiB and currently has 14 entries. The owner
  `.env.local` is mode 0600 (470 bytes); its contents were not read or hashed. The
  owner and research checkout `.env.local` were excluded from the three-member MCP custody capture;
  every future recovery archive must exclude `.env.local` credential files for user reissue.
  `~/Library/LaunchAgents` has 60 plist files: 55 Brain, one D43, and four host-software definitions
  for Steam cleanup, Zoom, and Homebrew PostgreSQL. The four host-software labels were already
  loaded without PIDs; DR did not load or change them. The PostgreSQL label has no PID and its last
  status is nonzero; a read-only search of the owner checkout found no PostgreSQL references in its
  recorded scope. A negative search does not prove absence of dependency; keep this service outside
  the inert Brain baseline pending explicit dependency proof.
  At 01:12Z, 18 labels were loaded and nine had PIDs, including `ai.memecoin.brain.b09r2.source`; by
  01:17Z, 18 remained
  loaded and eight had PIDs, with that source label loaded but without a PID. At the later probe,
  72 processes had cwd metadata under owner/research roots. No process arguments or environments
  were read. These time-bounded observations do not establish whether the source label performed
  earlier work or whether other services wrote state. DR work did not change or activate services.
  A value-filtered plist projection confirms all 56 Brain/D43 definitions contain `RunAtLoad` (54
  true, two false) and the four host-software definitions contain three true and one false. The plist
  byte streams were opened to project selected fields, but environment values were not inspected or
  interpreted, retained, or emitted. Five environment-key names occur across four Brain/D43 plists;
  one additional environment key occurs in one host-software plist. Keep credentials private and
  reissue them. The existing
  [`LaunchAgent metadata receipt`](evidence/portability-dr-launchagent-metadata-2026-10-14T0120Z.json)
  remains the original metadata-only record; the later
  [`root/dependency map`](evidence/portability-dr-root-dependency-map-2026-10-14T0221Z.json)
  owns this bounded classification.
- Kingston `BrainData` occupies about 19 GiB and remains on case-sensitive APFS with UUID
  `8f8f1081-9be4-4914-9111-75f1b90255bf`, root ID
  `9428695af6d4f46c08e1a444cdc66de8`. The 06:10Z read-only check found about 203 GiB (217 GB)
  free; the
  50-GiB storage floor passes.
  Kingston is the only reset-surviving physical copy.
- A fresh read-only preflight at 04:56Z confirmed the same Kingston UUID/root with
  217,174,470,656 bytes free. Internal Data had 14,453,396 KiB free (13.78 GiB) at 97% used; no
  APFS snapshots or Time Machine destination were present. The session has no noninteractive admin
  path and no safe snapshot retention/churn budget is qualified, so no snapshot was attempted.
- The 01:18Z storage refresh reports 13,234,572 KiB free on internal Data and 212,382,340 KiB on
  Kingston. It found no Data APFS snapshots or configured Time Machine destinations. These are
  point-in-time capacity/facility checks, not a qualified recovery boundary; see the
  [`storage/owner refresh receipt`](evidence/portability-dr-storage-owner-refresh-2026-10-14T0118Z.json).
- The read-only refresh receipt measured 12,960,665,600 bytes free before removal of two agent-created
  rehearsal clones. Removing those exact, verified disposable checkouts reclaimed 596,779,008 bytes;
  current `df` reports 13,239,692 KiB free (about 12.6 GiB). `diskutil apfs listSnapshots` finds no
  Data snapshots and `tmutil` reports no configured destination. A fresh
  noninteractive privilege check confirms the session cannot create or mount a privileged snapshot
  without user authentication. The available unprivileged `iostat` view combines reads and writes,
  so it did not establish a safe write-churn bound. A read-only owner-wide snapshot has not been
  created or qualified.
  Copying live SQLite/WAL files individually is not an acceptable substitute. Any snapshot needs a
  short, monitored retention window and a conservative free-space abort floor.
- Prior exact-current clone and uncached dependency bootstrap took about 82 seconds on this
  macOS/Python 3.13 host. On the current commit, `make smoke` passes; the recovery-set,
  restore-witness, tree-custody-v3, bootstrap, SQLite-backup, and static-custody focused suite passes
  90 tests; scoped Ruff/format and `git diff --check` pass. Repository-wide `make check` stops at
  Ruff with 107 findings outside the DR scope. These are synthetic engineering checks.
- A synthetic frontier scaffold wraps the v1 set/witness with exact storage member pins, a
  caller-supplied post-capture source fingerprint assertion, and a fixture corpus object plus
  STOP/pending marker. Its archive and identity callback use pytest temporary storage. Sol accepts
  this bounded scaffold only: it does not prove an immutable T cutoff, reconcile capture members
  to that cutoff, establish a real K journal prefix, or exercise Kingston. Ten frontier tests and
  46 combined recovery-set/tree-v3/SQLite/frontier tests pass. See the
  [`frontier scaffold receipt`](evidence/portability-dr-frontier-scaffold-2026-10-14T0314Z.json).
  The factory-reset verdict remains NO-GO.
- A K object/STOP fixture of the same format has now been written under the configured Kingston
  root with the held descriptor-bound create-only writer, reread through a fresh storage writer,
  and removed through the held descriptors after an exact-membership check. This tests the storage
  binding for a synthetic 57-byte object only; no recovery set, real journal prefix, source cutoff, or restore
  witness was exercised on Kingston. See the
  [`Kingston synthetic K receipt`](evidence/portability-dr-kingston-synthetic-k-rehearsal-2026-10-14T0317Z.json).
- An opt-in synthetic-only Kingston rehearsal then copied six fixture artifacts, built a v1
  recovery set, restored all six into isolated same-Mac temporary targets, bound and verified the
  restore witness, and published/read back the synthetic object/STOP/end-marker frontier envelope.
  Exact member type/path/size/hash inventories were compared before nonce cleanup; only the normal
  shared zero-byte `.index.lock` and `.publish.lock` coordination files remain (whether they
  pre-existed was not established). Sol independently accepted this narrow pass. It proves neither
  an immutable owner T cutoff, a real K journal prefix, xattr/ACL recovery, nor clean-host restore;
  factory reset remains NO-GO. See the
  [`synthetic Kingston restore receipt`](evidence/portability-dr-kingston-synthetic-restore-2026-10-14T0333Z.json).
- A synthetic held-directory reader now re-verifies all six recovery-set artifact pins and compares
  four v3 fixture roots through open directory/file descriptors against verified pages: exact
  membership, file bytes, topology, and recorded stat/xattr-name fields. It skips excluded D43
  descendants. A separate metadata-only pass compares the synthetic SQLite DB/WAL/SHM/journal
  family through held descriptors to its backup receipt; fixture setup separately runs online backup
  against its disposable source DB. Artifact inventory commitments/counts are derived, and member
  capture intervals must fit the declared capture window; the fixture's unresolved descendant
  remains explicit. The focused suite passes 53 tests, including source-drift and parent-replacement
  rejection plus a Hypothesis event-order property. Synthetic root device/inode bindings are
  checked, while snapshot identity, policy/tool hashes, and event times remain fixture declarations.
  This is not APFS
  snapshot attestation; protected-root binding, full owner closure, and a real snapshot-family
  capture remain open. Sol accepts only this synthetic scope. The v1 recovery-set verifier hash is
  unchanged. See the
  [`held-view synthetic receipt`](evidence/portability-dr-source-view-held-view-2026-10-14T035534Z.json).
- A negative-only restore assessor now combines the existing set/witness integrity checks with
  independent pins, the exact clean research commit, fresh filtered launchd/cwd observations, and
  a held Kingston mount/root binding. Missing evidence is `unknown`; mismatches fail. It always
  returns `blocked`, with complete inertness, owner/protected custody, T, K, and application restore
  still unknown. The read-only storage opener does not take a writer lock; verification may create
  and remove short-lived system temporary SQLite scratch. Synthetic tests cover missing and
  mismatched pins, absent restore roots, active labels, unavailable process observation, witness
  drift, low capacity, and simulated final root-identity drift. Sol accepts this narrow assessor;
  the simulated root drift is not a real rename-race rehearsal. It did not assess live Kingston
  artifacts or restore targets and does not change the NO-GO verdict. See the
  [`restore assessor receipt`](evidence/portability-dr-restore-assessment-v1-2026-10-14T0411Z.json).
- A consume-existing-view adapter contract is now exercised on synthetic fixtures. It opens the
  declared view and roots through held no-follow descriptors, checks same-device topology and
  path/root identity before and after reconciliation, and binds only the four v3 tree roots to
  prior capture pages. Protected-v2 and SQLite locators remain source-unbound; the SQLite family
  has a separate metadata-only check through the owner root. Exact declared credential files must
  be excluded with `reauth_reissue`; this does not establish exhaustive credential closure. The
  existing capture reader reopens paths, so the result says post-capture reconciliation and
  descriptor-sourced capture is false. No APFS snapshot is created or mounted; snapshot identity,
  read-only status, and cutoff remain unqualified. T, host closure, and erase readiness stay false.
  Sol accepts only this synthetic contract. See the
  [`source-view adapter receipt`](evidence/portability-dr-source-view-adapter-v1-2026-10-14T0421Z.json).
- A single inert restore conductor now preflights the six pinned recovery roles, audits every v3
  exclusion page, rejects protected-scope/unresolved custody, caller-forbidden target roots,
  absolute v3 symlinks, and any protected-v2 symlink, then extracts into a fresh 0700 quarantine,
  verifies the restored members, binds a witness, and verifies it again. Failures retain partial
  quarantine without resume semantics. The output claims only byte-verified inert artifacts and
  keeps T/K, source closure, host closure, activation, and erase readiness false. Sol accepted this
  implementation scope after an independent code review. Its exclusions for owner checkout,
  private config, LaunchAgents, and any additional source roots depend on correct caller-supplied
  paths; this does not prove owner-wide closure. The conductor result can now be passed directly
  to the negative-only restore assessor with `--handoff` after the assessor-side integrity and
  field checks.
  Synthetic tests exercise actual captured absolute-v3 and protected-v2 symlinks and reject them
  before quarantine creation. See the
  [`inert restore conductor receipt`](evidence/portability-dr-inert-restore-v1-2026-10-14T0438Z.json).
  This remains a fixture-only implementation result, not a live Kingston or clean-host rehearsal.
- The committed conductor-to-assessor flow was then rehearsed against the configured Kingston
  volume using six synthetic role captures, an isolated temporary `HOME`, and a separate-device
  temporary restore quarantine. The set and witness pins passed, restored bytes were verified, the
  assessor returned `blocked` with T, K, and complete owner/protected custody `unknown`. Set and
  witness pin passes establish internal matching/readback only: this run generated the expected
  hashes itself and had no external trusted recovery receipt. The known run nonce paths were
  removed and no run set/witness ID remained in the filtered 32-hex ID lists;
  shared parent-directory effects were not classified. No provider request or service change
  occurred; no persistent set or witness ID remained. A failed fixture attempt had created six random synthetic artifact
  roots before refusing a stale UUID/root marker; those exact roots were byte-compared with their
  local synthetic source and descriptor-unlinked before the successful attempt. The pre-existing
  shared index lock remains and its ownership/mutation is not classified. This proves the bounded
  operator path on the current host, not a clean compatible-host restore or erase readiness. The
  local pytest-managed HOME and restore target were not checked for post-run removal. See the
  [`Kingston inert restore operator-flow receipt`](evidence/portability-dr-kingston-inert-restore-flow-2026-10-14T0450Z.json).
- A fresh clone of `7d8dc82` bootstrapped Python 3.13 and passed `make smoke`; both new recovery CLIs
  also passed direct `--help` invocation from that clone. The bootstrap reported no storage or
  service changes. This still ran on the existing Mac, did not isolate host caches, and restored no
  owner/private/protected/Kingston state. See the
  [`bootstrap rehearsal receipt`](evidence/portability-dr-bootstrap-rehearsal-2026-10-14T0107Z.json).
  Since that rehearsal, the private config root and all 60 current LaunchAgent definitions were
  captured to Kingston and byte-verified after isolated quarantine restore. Neither group was
  activated. Owner runtime/state, protected custody, and Kingston corpus dependencies still lack a
  qualified complete recovery set and full restore witness. No full clean-room restore has been
  rehearsed.
- `tools/brain_host_recovery.py` provides read-only host status and inert research-checkout bootstrap.
  `tools/brain_host_tree_custody_v3.py` and `tools/brain_host_recovery_set.py` verify declared tree
  members and a restored quarantine witness. Their `application_recovery_qualified` result remains
  false; the current set schema is an integrity index, not owner-root closure or erase approval.
- These live inventory and qualification observations are recorded in the immutable
  [`read-only DR inventory receipt`](evidence/portability-dr-readonly-inventory-2026-10-14T0101Z.json).
- The fresh [`read-only DR refresh receipt`](evidence/portability-dr-readonly-refresh-2026-10-14T0112Z.json)
  records 18 loaded labels / nine running, 12.1 GiB free on Data, and a recursive metadata-only name
  scan across owner/research work, private config, and Kingston. The scan located
  `owner/work/calendar-execution-01`; no exact F01, reserved, or holdout component name matched.
  This name-based scan does not prove those roots are absent or that protected custody is complete.

- A 01:17Z metadata-only follow-up found basename-prefix candidates under owner work (37 D43 and
  30 F01), research work (15 D43, 81 F01, and three reserved), and Kingston (four D43 and eight
  reserved). Private config had no matching prefix. Paths and contents were not recorded or opened;
  names alone do not establish protected role. These candidates expand the unclassified custody
  inventory and must be resolved as opaque custody members or explicitly ruled out by an authorized
  classification before erase. The fresh service/process status and limits are in the
  [`metadata follow-up receipt`](evidence/portability-dr-metadata-followup-2026-10-14T0117Z.json).
  Type-only counts show 59 regular files/eight directories in owner work, 68 regular files/31
  directories in research work, and eight regular files/four directories on Kingston. Their
  suffixes include JSON, logs, Markdown, Python, and Rust source. Nested entries can be related or
  duplicated; extensions do not establish role. See the
  [`candidate entry-type receipt`](evidence/portability-dr-candidate-entry-types-2026-10-14T0119Z.json).
  A follow-up kept a private path inventory of all 178 matching entries in those three roots on
  Kingston at `host-recovery/private-path-inventory-20261014-a1/`. The 62,395-byte manifest records
  relative paths, lstat metadata, and symlink link text; no payload was opened and no symlink was followed.
  The manifest and completion marker were reread through a fresh descriptor-bound verifier and
  hash-checked. All candidate entry metadata was stable at the end of the scan, which does not make
  this a common source point or establish role/byte custody. See the
  [`private candidate-path inventory receipt`](evidence/portability-dr-protected-candidate-path-inventory-2026-10-14T0155Z.json).

- Recursive metadata for `~/.config/brain-research` finds 13 directories and 75 files (about
  184 KiB), all files mode 0600. Its top-level entries include five versioned external configuration
  sets, three timing-rehearsal sets, one retired-LaunchAgent set, one storage-health set, four
  handoff/binding files, and no symlinks. These host-local records may include old activations or
  private configuration; names and modes do not prove contents safe or disposable. Preserve or
  explicitly reissue them after private classification. Metadata-only follow-up counted 15 mode-0600
  plist copies in the retired set; they remain inert custody candidates and have not been compared
  to live definitions. The metadata inventory opened no contents; a later opaque backup copied and
  hashed bytes without parsing them. See the
  [`private-config metadata receipt`](evidence/portability-dr-private-config-metadata-2026-10-14T0127Z.json).
  The focused [`retired plist set receipt`](evidence/portability-dr-retired-launchagent-set-metadata-2026-10-14T0136Z.json)
  records the follow-up count without file names or contents. The entire private root is now in a
  provisional Kingston quarantine; all member hashes were verified after reread. Its metadata was
  stable during this live pass, but no atomic cross-file boundary is proved. The archive is not
  restored or activated automatically; see the
  [`private-config custody receipt`](evidence/portability-dr-private-config-custody-2026-10-14T0140Z.json).
  The opaque restore witness counts 14 archive directory entries because it includes the archived
  `brain-research/` source root plus its 13 descendant directories.

- A later metadata-only filesystem count found 60 regular plist files in `~/Library/LaunchAgents`;
  the earlier Brain/D43 metadata receipt covers 56. All 60 files were privately captured and
  restored into temporary inert quarantine; member hashes matched. A later value-filtered projection
  classified the four extra files as Steam cleanup, two Zoom services, and Homebrew PostgreSQL 16.
  Their selected path fields were reduced to root categories; environment values were not inspected
  or interpreted, retained, or emitted. All
  four labels were already loaded without a PID, with three nonzero last statuses. No definition was
  installed or loaded by DR. These are host application services, not part of the replacement Brain
  baseline. All 56 Brain/D43 definitions remain historical inert custody and need per-role
  authorization before any later install. The copy is still a live provisional capture without an
  atomic owner-wide boundary; see the
  [`LaunchAgent custody receipt`](evidence/portability-dr-launchagent-custody-2026-10-14T0143Z.json)
  and [`root/dependency map`](evidence/portability-dr-root-dependency-map-2026-10-14T0221Z.json).

- The private root/dependency map stores metadata for the immediate owner/research `work/` children,
  all 88 entries in private Brain config, and all 60 LaunchAgents; raw paths stay on Kingston. The
  contemporaneous census counted 285,577 owner and 330,273 research files. Among 657 recorded entries,
  604 have extended attributes (mostly `com.apple.provenance`); ACL markers were absent. Values were
  not read, and current custody archives do not preserve xattrs/ACLs. This root-only map is not a
  recursive owner-state closure; the subsequent metadata inventory below adds recursive paths but
  not semantic ownership. Broad activation/deployment basenames also match generated files, so exact
  nonprotected pointer closure remains open.

- A later recursive metadata-only inventory records 320,989 owner and 411,217 research entries
  encountered under the two `work/` roots, including root directories, under a sensitive-subtree
  pruning rule. It records lstat fields, xattr names,
  symlink target text without following links, and ACL presence where determinable. It did not read or
  hash regular-file payload bytes. Seven owner and nine research sensitive-name entries are lstat-only
  exclusions; their directories were not descended. All 67 owner and 99 research protected-name
  candidates from the separate private inventory are present with matching recorded lstat fields;
  the 12 Kingston candidates remain in that separate inventory. The earlier research census counted
  21 more regular files and three more directories. This scan pruned three sensitive-name directories
  and does not count their descendants; the scopes differ and the deltas do not prove deletion. The
  root directory signatures
  stayed unchanged, but recursive metadata was not checked again after scanning. The private map is
  therefore useful for path topology and candidate reconciliation, not a source snapshot or custody
  set. See the [`recursive work metadata receipt`](evidence/portability-dr-recursive-work-metadata-2026-10-14T0230Z.json).

- The three research directories pruned by the sensitive-name rule are named `wallets`. A separate
  one-second lstat/scandir-only pass counted 21 files and three subdirectories beneath them (69,375
  apparent file bytes); child names were transiently enumerated to traverse entries but not retained
  or emitted, and no payloads were read or copied. Those counts numerically account for the earlier
  census deltas of 21 files and three directories. The scans were at different times, so this does
  not prove historical membership continuity. Their contents and role remain unknown. The user
  authorizes opaque custody only; v4 plans their metadata classification as private, unresolved,
  backup-required state without opening payloads. No payload has been read or copied. Capture still
  requires a qualified T and Kingston publication path. See the
  [`pruned-subtree metadata receipt`](evidence/portability-dr-pruned-subtree-metadata-2026-10-14T0241Z.json).

Scope caveat after that receipt: a later broad `git grep` covered tracked `docs/`, `src/`, `tools/`,
and `tests/`, which includes `docs/evidence/`. Its output was truncated before matching files could
be classified. No result was used for protected-data analysis or copied into project artifacts, and
no files were changed by the search. Whether a protected receipt line was printed is unresolved;
the refresh receipt's `protected_payloads_read=false` applies only to its 01:12Z capture. Do not
rely on that later search output or treat it as protected custody evidence.

- A separate Codex control-center worktree was found under host-local worktree storage. Its branch
  tip was absent from the queried remote branch heads and from the research checkout's object DB;
  the exact queried remote result does not prove no other copy exists. A 19,421,684-byte Git bundle
  containing its 131-commit reachable history and a 3,010,560-byte tar of its ignored `.local`
  tree were written create-only to Kingston through the descriptor-bound storage writer at
  `host-recovery/provisional-opaque/codex-worktree-20261014-a1/`. The tar
  contains 32 files (2,941,740 source bytes) and six directories; all 32 per-file hashes are in
  the private backup manifest. A fresh empty bare repository recovered the branch tip and all nine
  commits absent from cached origin refs; all tar member hashes matched after rereading the backup
  from Kingston. The immutable [`provisional Codex worktree custody receipt`](evidence/portability-dr-codex-worktree-custody-2026-10-14T0133Z.json)
  pins the bundle, tar, private manifest, and completion marker hashes. This is opaque, provisional
  custody: the three log files may have changed outside the per-file read windows, xattrs/ACLs are
  not preserved, and no application
  state was interpreted. All 10,661 other ignored-status records (10,650 regular files and 11
  symlinks) were also copied into a separate opaque tar and verified after Kingston reread. Their
  metadata remained stable during capture, and a post-capture status comparison matched all archive
  member paths. The 10,693 status records reconcile as 10,631 under `node_modules`, ten under
  `dist`, 11 under `.ruff_cache`, nine other ignored records, and 32 separately captured `.local`
  files. Their role and rebuildability/disposability remain unclassified; empty directories,
  xattrs, and ACLs are not preserved. A follow-up metadata check found all 11 symlinks resolve to
  regular files inside the captured tree; those targets were present and hash-matched in the
  isolated restore. This closes these path-level dependencies only, not package/runtime readiness
  or external dependencies. See the
  [`ignored-state custody receipt`](evidence/portability-dr-codex-ignored-custody-2026-10-14T0140Z.json).
  See also the [`symlink closure receipt`](evidence/portability-dr-codex-symlink-closure-2026-10-14T0145Z.json).
  These archives are provisional live copies and do not establish a consistent source point or close
  the owner-wide recovery boundary. All three archives (`.local`, private config, ignored state)
  have since been restored into temporary quarantine roots, with every file hash and symlink link
  text checked; the temporary roots were removed after verification. No contents were parsed or
  executed. This is only a local-group restore witness, not a Brain-wide clean-room restore; see the
  [`opaque restore witness receipt`](evidence/portability-dr-opaque-restore-witness-2026-10-14T0141Z.json).

- Host-tool follow-up found an unversioned owner-only Helius MCP wrapper and small Codex/Cursor MCP
  configuration files. The wrapper reads a private `.env.local`, pins `helius-mcp@2.2.0`, and embeds
  an absolute path to the research checkout's `.env.local`. A targeted value-free credential scan of
  the wrapper and parsed config fields found no recognized credential patterns. Codex high-entropy
  values were heuristically path/URL-like; embedded secrets are not ruled out. All three files were copied into a private Kingston static-custody capture
  and hash-verified by a fresh verifier, then restored into an isolated quarantine and byte-checked.
  They remain inert: none was installed or executed. The capture manifest contains no `.env`-named
  member; owner and research `.env.local`, Codex/Cursor auth/session stores, and MCP-specific app
  caches were excluded from this capture. Future recovery archives must exclude `.env.local`
  credential files and auth/session stores; classify other cache/build roots separately before
  treating them as disposable.
  Configuration values remain private and potentially sensitive pending full review. On a replacement host, reauthenticate and rebind the wrapper/config paths
  to verified roots before any separate authorization to use MCP. This aid does not qualify Brain
  restore. See the [`private MCP tool custody receipt`](evidence/portability-dr-private-mcp-tool-custody-2026-10-14T0200Z.json).

No providers, prospective collectors, owner services, champion, protected evaluation, or trading
state were changed by the DR work. No provider request or collector was initiated by the DR work.
No protected application or dataset reader was used. No qualified owner-wide recovery set, classified
F01/D43/reserved custody set, or complete Brain restore witness exists. The provisional Codex bundle may
contain protected-like objects whose role remains unclassified. Recovery-set code currently binds
only caller-declared roles and one opaque D43 root; it does not prove full owner-root closure,
F01/reserved custody, service-definition closure, or semantic replay. Recovery-secret custody is not required for the accepted plaintext
custody scope; provider credentials require user reauthentication after restore. Source consistency
and completeness remain unresolved and block `SAFE TO ERASE`.

Independent Sol review of the published `beac49989afb416695c46258c2e0c747e93e2df9` handoff
confirms **NO-GO** under the accepted healthy-Kingston/plaintext scope. The review found no material
claim correction: current receipts prove provisional local-group custody and narrow isolated
restores, not an owner-wide consistent cutoff, complete owner/protected custody, or a full inert
Brain restore. The next material horizon requires interactive admin authentication for a qualified
source view, a monitored snapshot headroom budget, then complete custody and isolated restore.
Research checkout authentication remains a clean-host user step. Sol review deferred an optional
exact-commit local source bundle: remote user reauthentication is accepted, while the bundle adds
little recovery value and would duplicate reachable history into plaintext Kingston custody.

Independent Sol review of the current operator-flow increment confirms **NO-GO** under the same
accepted scope. The receipt supports only the same-Mac synthetic six-role conductor/witness/assessor
path; expected set and witness pins were generated by that run and are not external recovery
authority. No further non-invasive inventory or synthetic rehearsal materially closes the remaining
gates. Next progress requires an admin-assisted owner-wide source view, complete root/dependency and
protected-custody classification, capture from that view, a real Kingston K frontier, and user Git
authentication plus an isolated clean-compatible-host restore.

Sol separately reviewed the 02:50Z operator deployment-pointer pass and the 02:55Z active service-root
pass. Both scopes and limitations are accepted: the deployment pin matches its manifest, and five
declared nonprotected working-directory entries match the private inventory while three sensitive
or protected-candidate entries remain excluded. Neither pass proves loaded process/code identity or
changes the owner-wide custody and source-consistency NO-GO.

## State ownership and restore rules

| State | Canonical source of truth | Recovery rule |
|---|---|---|
| Research code, history, canonical docs, committed receipts | Git remotes and immutable commits | Clone the exact accepted `research/active` commit; verify clean identity; rebuild dependencies from checked-in pins. |
| Owner code and deployed runtime identity | Owner Git remote plus exact runtime bundles, deployment pointers, and state under owner `work/` | Restore an inert copy; never infer the deployed bundle from repository `HEAD` or recreate active services automatically. |
| Large research captures, datasets, journals, immutable evidence, and recovery records | Kingston `BrainData` plus each object's manifest, lineage, STOP, and recovery records | Verify UUID, root marker, artifact hashes, and semantic replay before admission. This is canonical storage, not a second backup. |
| Unique local owner/research state | Local owner/research `work/`, `~/.config/brain-research`, and Brain LaunchAgent definitions until a verified Kingston custody set is published | Capture from one read-only APFS source view where available. Use SQLite online backups for declared non-protected databases. Preserve unknown and unfinished state; never copy a live database/WAL family file-by-file. |
| Private provider configuration | User reauthentication/reissuance; current `.env.local` files are not needed for inert restore | Exclude every `.env.local` credential file from recovery archives. Record metadata only where needed; do not open or hash secret bytes. Keep local values mode 0600 and never place them in Git, public receipts, logs, plists, or process arguments. |
| Protected F01, D43, and reserved holdout | Existing protected source roots and their frozen declarations/manifests/lineage | Preserve exact bytes, classification, and lineage as opaque custody. Verify byte/custody metadata only; no protected application readers, replay, training, evaluation, admission, or relabeling. |
| Host bindings | Per-host private config, including Kingston UUID/root binding | Recreate from the attached volume and verify explicitly. Paths are locators, not artifact identity. |
| Owner developer-tool integration | Private Kingston quarantine of the Helius wrapper and Codex/Cursor MCP config | Restore only to quarantine first; treat configuration values as potentially sensitive, rebind host paths, and reauthenticate before separate MCP use authorization. This capture excludes `.env.local`, Codex/Cursor auth/session stores, and MCP-specific app caches; classify other cache/build roots separately. This state is not required for inert Brain restore. |
| Runtime and recovery authority | Versioned source, immutable journals/STOPs, activation records, and service definitions | Restore inert. Expired anchors, STOPs, spent reservations, ambiguity, gaps, and failures remain facts; none grants restart permission. |
| Environments and caches | Checked-in pins for dependencies; caches are disposable only after unique evidence is ruled out | Rebuild environments; do not treat old virtualenvs or Homebrew state as the source of truth. |
| Credentials for developer tools and host login | Account/password-manager recovery and user-controlled reauthentication | Reauthorize after reset. Do not copy Keychain wholesale as a substitute for documented secret custody. |

Preserve source bytes and original STOP/recovery histories. Keep event, availability, receipt, and
decision times distinct. Preserve `observed`, `unknown`, `censored`, and `failed` dispositions.
Sealed declarations can contain historical paths; retain original bytes and use explicit transport
locators for new restores. A recovery point is the last independently verifiable durable boundary,
not the newest timestamp. For unfinished work, preserve the uncertainty around it; never turn a
missing prefix into a clean history.

## Backup-only custody procedure

The user authorizes read-only inventory and backup-only copying. Plaintext custody on the bound,
owner-controlled Kingston volume is an explicit accepted scope for this iteration; do not describe
these artifacts as encrypted or as a second physical copy. The protected procedure is:

1. Define the custody set from path/type/size/metadata only: original protected bytes and their
   declarations, manifests, membership/split records, STOPs, and recovery dependencies. Keep the
   private path inventory under the verified Kingston root. Reject unresolved symlinks and
   out-of-scope filesystem dependencies. Do not inspect, parse, train on, evaluate, admit, or
   relabel protected content.
2. Use a qualified immutable source view. Prefer one read-only APFS snapshot covering the internal
   owner checkout, runtime/state roots, and protected roots. Record volume and snapshot identity
   plus cutoff. If dependencies live on other filesystems, give each its own explicit recovery
   point. A crash-consistent snapshot does not claim that an application operation completed.
3. Stream metadata-preserving custody objects beneath the verified Kingston root through a held
   descriptor and create-only names. Do not capture provider secret values; the user reissues them
   after restore. Nonsecret private host configuration remains private on Kingston and must not be
   copied into public receipts.
4. Reopen every object with a fresh verifier; validate byte hashes, member completeness, and
   metadata without a protected application reader. Fsync content and manifests, then publish a
   create-only group with a commit marker last. Failed or interrupted output is unpublished and
   cannot count as custody.
5. Qualify metadata and failure behavior using synthetic files before any protected stream. Retain
   the source snapshot through custody artifact verification and publication acceptance, then
   release it promptly before a longer restore rehearsal. If the measured churn/headroom budget
   cannot safely cover that short capture window, abort before capture. Remove or quarantine only
   generated scratch, never original protected bytes or STOP/history.

The reproducible 2026-10-14 synthetic rehearsal is
[`tools/brain_dr_synthetic_archive_rehearsal.py`](../tools/brain_dr_synthetic_archive_rehearsal.py),
bound by its source hash in the receipt. It used `bsdtar 3.5.3` and GnuPG 2.4.8 in an isolated GPG
home and verified metadata and encryption failure handling on synthetic files only. It does not
qualify a Kingston writer or publication protocol. Protected data was not read or copied. State the
eventual custody receipt as “opaque backup bytes processed;
protected semantic access/evaluation/training false,” rather than claiming that raw bytes were never
read.

## First-host code entry

`recovery/ENTRY.txt` is a plain-text code-bootstrap card stored at
`BrainData/host-recovery/ENTRY.txt` for use before a repository checkout exists. Its approved
commit and SHA-256 are supplied in a trusted handoff outside Kingston; do not treat the card or a
hash stored beside it as its own trust anchor. The card checks the mounted Kingston UUID and
existing root marker read-only, then uses interactive Git authentication to fetch the exact pinned
commit into a new temporary entry checkout. The checkout's card bytes and hash must match Kingston
before the existing `brain_host_recovery.py bootstrap` helper is run. The independent handoff must
instruct the operator to calculate and compare the Kingston card hash before executing any command
from the card; its trusted first command is
`shasum -a 256 /Volumes/Kingston/BrainData/host-recovery/ENTRY.txt`.

The current pinned card is at commit `edf2898ba4fb877ed8bee159fc9673754f7e12e4`, SHA-256
`ac8859ee4fc499ffe55dc1824be11a55fd4475fee2dbe0cae749a7f6cbbb5b76`. Supply these values
through an independent trusted handoff when recovering; a Kingston-local copy of these values is
not an independent trust anchor. This session records the pins in Git and the chat handoff; it has
not distributed a separate persistent handoff artifact outside Kingston.

The helper makes a second exact-commit clone into a separate new destination, installs the locked
development requirements, and runs smoke. Both checkouts must report the pinned `HEAD`, and the
final helper result must say `complete` with `smoke=passed`. The temporary entry checkout remains
until this result is recorded; only that exact run-owned directory may later be removed. This
two-clone path avoids a pre-checkout executable and preserves the final checkout's normal Git
remote. It costs an extra source clone but no additional dependency installation.

This path produces **code bootstrap verified** only. It does not restore private or durable Brain
state, qualify Kingston custody, activate services, send provider requests, prove a clean-host
inert restore, or change erase readiness. Wrong/missing Kingston identity, auth failure, a changed
card, a wrong commit, a preexisting destination, or an incomplete smoke result blocks the flow.
The previously recorded clean-`HOME` auth failure remains a real prerequisite; this card does not
copy old-host credentials. The Mac-loss recovery procedure below still governs data and runtime.
The local-fixture rehearsal passed both exact-HEAD checks, locked environment creation, smoke, and
the wrong-commit, wrong-volume-pin, missing-authentication, and preexisting-target failure cases.
Its success path used an ephemeral local bare remote on the current Mac, so authenticated GitHub
bootstrap and physical clean-host deployment remain unproven. The final rehearsal also recreated
the host-only Kingston binding in temporary `HOME` and checked the existing root. It supersedes
the earlier 06:06Z rehearsal iteration, before the trust-order correction. See the
[`pin-first first-host entry rehearsal receipt`](evidence/portability-dr-first-host-entry-rehearsal-2026-10-14T0610Z.json).

## Restore procedure

1. On a clean compatible macOS host, install Git and a supported Python. Sign in to the Git account
   that can read the private remote through its normal user-controlled authentication flow; never
   copy the old host's Keychain, tokens, or session stores. Clone the exact accepted
   research commit from `origin/research/active`; confirm the commit and a clean worktree.
2. Rebuild the base development environment from `requirements-dev.lock` and run `make smoke`.
   Optional ML dependencies remain separate and task-specific.
3. Attach Kingston; recreate a mode-0600 host-only binding using the actual mount path. Verify
   volume UUID, case-sensitive APFS, ownership, mount identity, 50-GiB free-space floor, and
   `BrainData` root marker. On a fresh host with no private binding, create the mode-0600 binding
   with the existing root identity pins:

   ```sh
   .venv/bin/python tools/research_storage.py bind-existing \
     --config "$HOME/.config/brain-research/storage-v1.json" \
     --mount /Volumes/Kingston --data-root BrainData \
     --expected-volume-uuid 8f8f1081-9be4-4914-9111-75f1b90255bf \
     --expected-root-id 9428695af6d4f46c08e1a444cdc66de8 \
     --min-free-bytes 53687091200
   .venv/bin/python tools/research_storage.py check \
     --config "$HOME/.config/brain-research/storage-v1.json" \
     --min-free-bytes 53687091200 --require-root
   ```

   Do not run `create-root` against an existing corpus.
4. Restore the accepted local-state group into a new isolated incoming location. Verify artifact
   and private manifest hashes, archive completeness, and metadata for every member. Apply source
   identity, SQLite integrity, durable-prefix, lineage, STOP, gap, and unknown/censored/failed-state
   checks only to non-protected owner/research state and Kingston corpus dependencies. For
   F01/D43/reserved members, verify opaque byte and custody metadata only; do not use protected
   application readers or perform replay, admission, training, or evaluation.
5. Restore owner runtime and paper state inert. Keep all LaunchAgents unloaded; do not initialize
   databases, checkpoint the original state, restart jobs, or issue provider requests during a
   rehearsal. Recreate only the documented service set after separate role authorization and
   non-protected source/identity/STOP checks. Protected material remains excluded from application
   readers and all semantic validation.
6. Compare the restored evidence frontier and runtime identities to the accepted recovery receipt.
   Any missing prefix, failed replay, unclassified state, unresolved dependency, or identity mismatch
   blocks readiness. Provider reauthentication is a later user action and is not part of inert
   recovery.

### Inert artifact restore conductor

For a recovery set that already has an independently pinned set ID and `INDEX.json` SHA-256, use
`tools/brain_host_inert_restore_v1.py` with the verified host-only storage binding, an empty target
parent on a device separate from Kingston, the exact research repository root, and the owner
checkout plus any other source roots that must be excluded, each supplied as a repeatable
`--forbidden-root`. Include private config and LaunchAgents roots explicitly when they are source
roots on the host. The command checks all six roles before creating `brain-restore-<nonce>`, writes
only inside that new quarantine, and prints one JSON result.
Save that output as the local restore record; the command does not publish a public receipt or
persist the result elsewhere. Pass the saved full result directly to the read-only assessor:
`.venv/bin/python tools/brain_host_restore_assessment.py --storage-config <private-binding> --handoff <restore-result.json> --format json`.
The assessor checks the conductor result digest, exact handoff fields, false activation/erase
authority, and absolute target paths before mapping pins and targets into the existing assessment.
That digest detects accidental modification; it does not authenticate the result or independently
accept its pins. Treat every handoff-supplied pin as untrusted until it is compared with a separate
trusted recovery receipt. The assessor remains negative-only and reports blocked while source
closure and durable T/K boundaries are unresolved. The conductor's successful result is not
`Brain restored` and is never erase approval. The assessor also accepts the standalone
`assessor_handoff` object when one is provided separately; that form has strict schema and authority
checks but no enclosing conductor digest.

## Service restore posture

The 01:12Z read-only `launchctl list` inventory captured 18 loaded labels. Nine had a running PID:
the owner `operator`, seven owner UI processes, and `ai.memecoin.brain.b09r2.source`. Nine were listed
without a PID, including the D43 calendar, paper/audit/train labels, and the research storage-health
monitor. By 01:17Z, eight of 18 had PIDs and `b09r2.source` was loaded without one. This is observed
source state, not a proposed startup set. No Senses capture/watchdog label was listed. Neither probe
proves the source workload or write frontier.

There are 55 Brain-named and one D43-named plist files in `~/Library/LaunchAgents`; the other four
are host-software services. At least 38 Brain/D43 definitions were not represented among the 18
labels loaded at the 01:12Z probe. All 60 definitions remain host-specific custody; the 56 Brain/D43
definitions need per-role authorization before any later install. Their plist byte streams were
parsed to select declared fields; environment values were not enumerated into the projection or
emitted. Reissue credentials through user authentication. Never install the full directory wholesale.
Their declared arguments refer to owner, research, and private-config roots; 56 argument references
pass through only three distinct symlink mappings. Output/error paths point either inside owner work
or to `/dev`; six owner log path references per stream currently point to missing files. These are
path-field references, not a count of unique dependencies. On a replacement host, rebuild any authorized
service definitions against verified roots instead of recreating old symlink accidents. See the
[`LaunchAgent path receipt`](evidence/portability-dr-launchagent-paths-2026-10-14T0124Z.json).

A later metadata-only reconciliation of the 01:43Z quarantined 60-definition plist capture found 321
absolute path references in the selected launchd fields. Four private-config references all match the
private-config inventory. Of 215 owner/work references, 203 match the recursive inventory (105
directory, 65 file, 33 symlink entries); the remaining 12 are the six missing stdout and six missing
stderr log destinations already recorded in the earlier path receipt. None falls beneath the pruned
sensitive directories. Six owner-checkout references and 51 research-checkout references are outside
their respective `work/` roots; no lexical declared path references enter `research/work/`. This closes
declared path topology for the inventoried work/config portions only; it does not identify the
deployed code revision or prove runtime behavior.
See the [`LaunchAgent work-path reconciliation receipt`](evidence/portability-dr-launchagent-work-path-reconciliation-2026-10-14T0238Z.json).

A 02:50Z allowlisted pointer pass found the current `ai.memecoin.brain.operator` plist declares the
owner `brain-b13/operator04` root. The owner code at its clean but 507-commits-behind `a594474`
checkout defines `deployment.json` as exactly `{bundle, pin}`. The 153-byte pointer is a regular
file whose lstat metadata matches the private recursive inventory; its `pin` matches the SHA-256 of
the selected bundle's `manifest.json` (16,876 bytes), also matching that inventory. Neither path
intersects the 178-entry protected-candidate inventory, and no path component was a symlink. Only
the pointer and manifest were read; bundle payloads were not. This pins one declared operator
bundle manifest, but does not bind it to an owner Git revision or prove the running process loaded
it. At the same point, 17 Brain labels were loaded and eight had PIDs; the operator was one, leaving
seven other running Brain code identities unresolved. One D43 label was loaded without a PID. This
does not establish an owner-wide source frontier. Private locators and hashes are in the
[`owner deployment-pointer receipt`](evidence/portability-dr-owner-deployment-pointer-2026-10-14T0250Z.json)
and its Kingston-only manifest.

A 02:55Z metadata-only pass covered the eight Brain LaunchAgent labels then running. It read only
their current plists' `WorkingDirectory` field and lstat metadata. Five declared nonprotected
owner/work working-directory entries matched the recursive inventory; three entries intersected
the protected-candidate inventory or sensitive-name rule and were excluded without retaining their
paths. No environment values, program arguments, process arguments, or protected payloads were
inspected. This reconciles five declared path entries but does not prove that the loaded definitions
match those files, that running processes use those directories, or that their code identities are
known. See the [`active service-root receipt`](evidence/portability-dr-active-service-roots-2026-10-14T0255Z.json)
and its private Kingston manifest.

The four additional host-software definitions are `com.valvesoftware.steamclean`, two Zoom services,
and `homebrew.mxcl.postgresql@16`. They were already loaded without PIDs at the latest probe; three
had nonzero last status. A scoped owner-checkout search found no PostgreSQL source references, which
does not prove that no dependency exists. Keep all four outside the restored Brain service set;
PostgreSQL remains outside pending explicit dependency proof, and other applications may need their
own reinstall/state review.

| Observed role and labels | Clean restore posture |
|---|---|
| Owner control: `ai.memecoin.brain.operator` (running) | Preserve its exact deployment definition and state. Keep unloaded; any later start requires separate owner release/recovery authorization and identity/STOP checks. |
| Storage monitor: `ai.memecoin.brain.research-storage-health-v1` (listed, no PID) | Preserve the definition. Keep unloaded during restore; later activation is a candidate only after the new host binding, source, and plist are verified. It records storage health and has no acquisition authority. |
| Protected evaluation: `ai.memecoin.d43.calendar01` (listed, no PID) | Preserve its definition and protected custody only. Do not install, load, or run evaluation as part of recovery. |
| Legacy jobs/source/audit/train/paper: `b04`, `b09r2.audit`, `b09r2.source`, `b10r2.train`, `b12.paper`, `expansion.paper`, `b10r2.paper`, `b12r2.paper` (`b09r2.source` had a PID at 01:12Z and none at 01:17Z; others had none at the earlier probe) | Preserve definitions and unique state in custody. Exclude from automatic install/start; each needs separate current justification and authorization. |
| Owner UI services: `b12.ui`, `b10.ui`, `expansion.ui`, `b12r2.ui`, `b10r2.ui`, `b12r1.ui`, `b10r1.ui` (all running) | Preserve definitions and exact runtime identity. Leave absent/unloaded on the restored host; a UI label does not prove read-only behavior or deployment authority. |

The initial restored-host baseline is zero loaded Brain/Senses labels. Keep historical plist files in
the private custody set rather than installing the old LaunchAgents directory wholesale. Operator
and storage-health are future owner-role candidates only; this classification does not authorize
enabling either. Watch Market and the integrated Observatory have no accepted owner deployment.
Do not recreate retired Senses services, stale activations, or STOP-bound releases. An inactive
worker receives no owner services, credentials, or protected evaluation scheduler. These restore
rules do not instruct stopping or changing services on the source Mac.

## APFS snapshot T attestor contract

A new `tools/brain_host_apfs_snapshot_attestor_v1.py` consumes an already-mounted snapshot; it never
creates, mounts, or unmounts one. Its synthetic contract suite exercises a versioned host plan bound
by a separately supplied SHA-256 pin, a separately selected snapshot UUID/XID, the pinned Data
volume UUID/group/device, exact APFS snapshot mount source (`SnapshotName@/dev/<Data device>`),
read-only `fstatfs`, `diskutil` plist identity, and three disjoint held no-follow directory roots.
The Darwin adapter invokes `/usr/sbin/diskutil` with a controlled environment. It holds the mount
parent, requires a root-owned parent without group/other write, reopens the mount leaf around OS path
queries, and revalidates the snapshot and roots before borrowed descriptor use and after each borrow.
An injected OS backend always reports synthetic; 17 focused tests pass. The actual Darwin API shape
was checked read-only against the current System snapshot and live Data volume: System snapshot UUID/XID
matched and was read-only; Data had zero snapshots. The System snapshot was used only as API-shape
evidence and is not owner T. The 18 focused attestor tests pass.

A digest comparison proves that the supplied plan matches the separately supplied pin; it does not
prove who approved that pin. Human approval and trusted pin delivery remain external. Snapshot UUID
and XID must be selected and reviewed after snapshot creation before the digest is pinned. Path-based
`diskutil` observations cannot be atomic with held descriptors; the rechecks mitigate replacement
races but do not protect against a local root adversary, which remains outside the accepted home-host
threat model. The result proves mounted snapshot identity and three root handles only. Root
completeness, application consistency, owner/protected closure, capture, Kingston K, restore, and
erase readiness remain false or unproven. No owner Data snapshot exists, and there is no real T.
Sol independently reviewed the implementation contract and required the pin, exact UUID/XID
selection, exact source-name binding, absolute `diskutil` path, and post-query descriptor checks.
The second independent review found no remaining blocker for this bounded identity contract; it did
not qualify plan approval or an actual Data T. Optional plist creation time is normalized as UTC.

## Offline read-only Data view feasibility

A bounded metadata-only feasibility pass found a possible source-view route that does not need APFS
snapshot headroom: shut the owner Mac down cleanly, start it in macOS Recovery (or another qualified
offline environment), unlock Data without mounting it if the volume is encrypted/locked, then explicitly mount the exact Data volume
read-only. The current host's `diskutil(8)` documents `apfs unlockVolume ... -nomount` and
`diskutil mount readOnly`; the read-only mount option denies writes even to superuser. Apple documents
Recovery Terminal and Share Disk on Apple silicon, but Share Disk is a transfer facility and does not
establish a read-only source export ([Apple Recovery guide](https://support.apple.com/guide/mac-help/macos-recovery-a-mac-apple-silicon-mchl82829c17/mac)).
No Recovery boot, unlock, mount, or capture was attempted. The current
`brain_host_tree_capture_v4.py` and `brain_host_source_view_adapter_v1.py` are explicitly
synthetic-only, and the former refuses a non-synthetic held snapshot handle. A pinned execution-kit
candidate now exists on Kingston and passed a bounded synthetic launch probe from its read-only image
on the current online host. It is not a qualified Recovery runtime or real Data-view adapter. Treat
the Recovery route as a conditional candidate, not a currently executable recovery procedure,
qualified T, or approval to interrupt the running host.

A fresh read-only `diskutil info` observation confirms that the current Data volume has FileVault
enabled and is mounted/unlocked under the live OS. A first Recovery Terminal check of the pinned Data
UUID, FileVault state, lock state, and mountpoint is a necessary gate: require `FileVault: Yes`,
`Locked: Yes`, and no mountpoint; stop if any check fails. Record the clean shutdown-to-Recovery boot
chain and every earlier authentication choice. Apple documents Recovery Assistant and notes that a
personal recovery key can unlock the volume while entering recoveryOS, so abort if any pre-Terminal
step unlocked Data ([Apple FileVault deployment guide](https://support.apple.com/guide/deployment/manage-filevault-with-device-management-dep0a2cb7686/web)).
Even `Locked: Yes` is only a point-in-time check; by itself it cannot exclude an earlier
unlock/mount/relock or another Data alias. A qualified history boundary also needs the earliest
available boot-session disk arbitration/mount logs, explicit treatment of missing log coverage, and
enumeration of all Data aliases and snapshots. If those logs cannot establish prior events, retain
the residual assumption and do not claim independently proven no-prior-writable mount or qualified
T. Then, if the history boundary is accepted, consider interactive unlock with `-nomount` followed by
an explicit read-only mount. The current online `diskutil(8)` manual says read-only mount denies writes
even to superuser, but Disk Arbitration may run `fsck_apfs -q`; the local `fsck_apfs(8)` manual says
quick check attempts no repairs. Recovery's bundled versions and actual mount behavior remain
unobserved. See the
[`Recovery locked-preflight feasibility receipt`](evidence/portability-dr-recovery-locked-preflight-2026-10-14T0653Z.json).
Sol independently reviewed this gate and rejected the Locked/no-mount observation as sufficient
history proof. Acceptance also requires shutdown-to-first-probe and authentication records, earliest
available mount events with gaps accounted for, all Data UUID/group/device/mount aliases checked
before unlock, and a second unmounted check after `unlockVolume -nomount`. If the log history cannot
establish prior events, state the remaining assumption; do not claim independent proof or qualify T.

### Recovery boot evidence reviewed 2026-10-15

The final Recovery boot was completed, and the user has returned to normal macOS. Its command set is
hash-bound in the
[`Recovery boot analysis receipt`](evidence/portability-dr-recovery-boot-analysis-2026-10-15T1942Z.json).
The first and last `diskutil info` checks identify the pinned Data UUID as `disk3s5`, with FileVault
enabled, locked, and not mounted. The saved `/usr/bin/log show` output starts 3.658 seconds after
the recorded kernel boot time. It shows a successful APFS probe for `disk3s5`, one automatic mount
attempt, and an explicit failure 19 milliseconds later (`0x0000004D`). Treat that value as an
uninterpreted failure: it does not state why the attempt failed or whether a hypothetical earlier
mount was read-only or writable. No successful Data-volume mount appears in the saved log or mount
listings. The separate `/System/Volumes/Data?owner=0` discovery appears alongside the Recovery
mount-table's `tmpfs` at `/System/Volumes/Data`; it is not evidence that `/dev/disk3s5` mounted.

The final text transcript contains no `listSnapshots` command or output, so the Recovery snapshot
inventory remains unknown. Photographs in the snapshot folder are from the preceding failed attempt
and are excluded from this boot's evidence. The final text shows `/usr/bin/log show` working.

This boot establishes the point-in-time locked/unmounted state and the logged failed automatic mount
attempt. It does not establish that no earlier mount occurred: pre-Terminal authentication and Data
access choices were not recorded, and the first saved log event follows kernel boot by 3.658 seconds.
The current live macOS read-only follow-up at 20:31Z found the same Data UUID mounted as expected,
one Data role member, and zero Data snapshots; this does not repair the Recovery history gap or
qualify a historical source view. Sol's independent decision is to avoid another Recovery boot
solely to repeat this preflight. Keep prior writable-mount history as an explicit human-attested
residual assumption; no Data T is qualified and erase remains NO-GO. A new Recovery boot is useful
only as part of a separately planned source-view rehearsal; repeating point checks cannot retroactively
close this boot's log gap.

`tools/brain_host_recovery_preflight_v1.py` now implements the bounded first-action observation.
Invocation requires independently pinned Data UUID, volume-group UUID, container UUID, and device
identifier; all four must match before the point-in-time check can pass. It checks APFS
container/group membership, FileVault/lock/mount state, Data snapshots, and matching kernel mount
entries. It resolves APFS sources by container role or `diskutil info` identity; incomplete mount
lines, unresolved aliases in the pinned container, and group-only ambiguity return `unknown`. It
never unlocks or mounts, omits mountpoint and snapshot-name strings from output, and always leaves
prior-mount history and erase readiness false. CLI exit status is nonzero for every blocked/unknown
state. The commands are sequential rather than atomic; recheck immediately before any dependent
Recovery action, and treat the reported host timestamp as untrusted. Seventeen focused synthetic
tests, scoped Ruff/format, and mypy pass. A live read-only run at 09:15Z
matched the pinned Data/container/group/device, found FileVault enabled, Data unlocked and mounted
writable, no Data snapshots, and returned `blocked_unlocked` with `safe_to_erase=false`. This
demonstrates the observer's current-host blocked path only; it is not Recovery behavior, mount-history
evidence, a qualified source view, or an erase gate. The first Recovery Terminal invocation still
needs the user-assisted sequence and history review described above. On Recovery, stop if any pinned
identity differs or any alias cannot be mapped; do not change the expected pins merely to continue.

Read-only `lstat` at 06:15Z found that the named owner checkout/work, research checkout/work, private
Brain config, and `~/Library/LaunchAgents` root entries reported the same local `st_dev` as internal
Data; Kingston `BrainData` reported a different `st_dev` and has its own pinned volume UUID/root.
This groups the root entries only. It does not prove their types, exclude symlinks/firmlinks or other
aliases, place descendants on the same volume, or close root coverage. `st_dev` is a local boot
identifier, not an APFS identity pin. A fresh read-only check found about 12.8 GiB free on Data at
97%, no Data snapshots, and 18 Brain/D43 launchd labels loaded, eight with PIDs. Prior metadata
scans observed active owner-tree churn. There is no credible live non-snapshot cutoff while those
source services remain active.

For a future offline rehearsal, acceptance requires a clean shutdown before the capture environment
starts, no earlier automatic writable Data mount in Recovery before the selected T, exact Data APFS
UUID/group/container/device checks, all source roots bound to the same held Data view (or individually
marked unresolved), an interactive unlock with no credential in argv or logs, explicit read-only
mount and filesystem-flag checks before and after capture, and held descriptor identity checks
throughout. T, if qualified, is scoped to Data; Kingston K remains a separate frontier. A pinned
capture tool/runtime must be runnable offline without installing onto the source Data volume. Stop if
the view is writable or ambiguous, any source writer remains, APFS requires repair/replay, a root
escapes the view, or protected/SQLite/metadata restore semantics are unresolved. The current
machine-readable feasibility observation is the
[`offline-view feasibility receipt`](evidence/portability-dr-offline-view-feasibility-2026-10-14T0616Z.json).

This route does not close protected-root classification, owner-root closure, xattr/ACL restore
semantics, capture-tool availability in Recovery, SQLite recovery behavior, Kingston K lineage, or
clean-host restore. All remain open, and `SAFE TO ERASE` remains false.

Sol independently reviewed this feasibility boundary. The offline route is a credible conditional
Data-volume source view, but no real T is qualified and the NO-GO verdict is unchanged. A later
read-only `fstatfs` result cannot prove that Recovery did not previously auto-mount Data writable.
That boot/mount history needs independent evidence from a user-assisted rehearsal or another trusted
observable mechanism. The next implementation horizon is to close the candidate runtime dependency
boundary before building a real `HeldDataT` adapter; caller-supplied JSON alone must not assert
boot-history facts. Kingston K remains a separate frontier.

### Pinned execution-kit candidate

The candidate at `BrainData/host-recovery/offline-execution-kit-v1/candidate-20261014T0624Z-a1.dmg`
is pinned by an immutable image manifest and was published create-only to Kingston with exact fresh
descriptor readback. A same-host probe mounted the image read-only, used an isolated HOME/PATH and
Kingston scratch, launched the bundled recovery CLI, imported SQLite/hash support, and exercised
only the synthetic capture path and interruption cleanup. It made no provider requests, activated
no service, and read no Data or protected payload. The candidate image remained hash-stable. See the
[`execution-kit rehearsal receipt`](evidence/portability-dr-offline-execution-kit-2026-10-14T0630Z.json).

This is an online current-host probe, not an offline Recovery or clean-host rehearsal. A metadata-only
`otool` survey covered 82 Mach-O members and 12 symlinks. It found Homebrew absolute install names
and RPATHs. A sandbox probe denying reads beneath `/opt/homebrew` stopped dyld at the absolute
Python.framework dependency before Python startup. The earlier invalid-shadow-library probe remains
inconclusive and does not prove fallback behavior. The candidate is therefore not independent of
this host's Homebrew prefix; its dependency closure and Recovery compatibility are unqualified. Do
not alter the owner's Homebrew installation to simulate absence.

A second immutable candidate, `candidate-20261014T0635Z-a2.dmg`, bundles five required dylibs,
rewrites the Homebrew references found in 85 surveyed Mach-O members to bundled-relative load names,
removes Homebrew RPATHs, and regenerates local ad-hoc signatures; explicit macOS system references
remain. Its manifest pins 3,689 runtime members and 373 source files. It was
published create-only to Kingston with exact fresh descriptor readback, then the Kingston copy was
mounted read-only and tested while sandbox rules denied all reads beneath `/opt/homebrew`. Selected
SQLite, hashing, TLS, decimal, and lzma imports and the recovery CLI help path passed with dynamic
loader override variables unset; no absolute Homebrew install names or RPATHs remained in the Mach-O
survey. See the [`dependency-closure rehearsal receipt`](evidence/portability-dr-offline-execution-kit-closure-2026-10-14T0637Z.json).

This closes only the dependency path used by those current-host probes. It is not a clean compatible
Mac or Recovery test; system library/version compatibility, unexercised Python modules, real source
capture, and restore remain open. Ad-hoc signing supplies no publisher identity or notarization. Sol
independently accepted this bounded current-host closure claim; it did not qualify Recovery,
clean-host compatibility, exhaustive dynamic closure, or Data T.

A third immutable candidate, `candidate-20261014T0640Z-a3.dmg`, removes one dangling Homebrew
`site-packages` symlink. Its static audit verified all 3,688 runtime members against the manifest,
resolved all 11 remaining symlinks and 15 `@loader_path` dependencies within the image, checked the
10 explicit macOS system dependencies against the allowlist, and found no unresolved entries. The
exact Kingston-published image then passed synthetic descriptor capture, inert topology restore, and
interruption cleanup under the same Homebrew-denial sandbox; its image hash matched before and after.
The test used only disposable fixtures and did not access Data, protected payloads, services, or
provider APIs. See the
[`capture/restore rehearsal receipt`](evidence/portability-dr-offline-execution-kit-capture-restore-2026-10-14T0640Z.json).
Sol independently accepted this narrow current-host result from the receipt and recorded procedure;
Sol did not open or re-hash the Kingston image.

This still proves only bounded tool and dependency behavior on the current online host. Real Recovery
availability, system-library compatibility, clean compatible-host restore, no-prior-writable-Data
mount evidence, real Data T, owner/protected custody, Kingston K, and full restore remain open. The
Recovery compatibility and mount-history rehearsal is user-dependent. Metadata-only root closure can
proceed separately without interrupting the live owner host. Do not erase or reboot the Mac as part
of this work.

### Ignored roots and unpushed owner-worktree history

A read-only Git/filesystem metadata census covered the current research checkout, owner checkout, the
research verifier worktree, and the linked owner Codex worktree. It found path-class candidates with
names associated with Python caches, build outputs, virtual environments, and dependency directories,
plus the already excluded `.env.local` credential files. Names and ignore rules do not prove that
every ignored artifact is disposable, reproducible, or free of unique evidence; no deletion or
recovery-exclusion decision follows from this census. Git reports each `work/` root as one ignored
entry; previous metadata-only counts are 330,273 regular files plus 108 symlinks under research
`work/`, and 285,577
regular files plus 18 symlinks under owner `work/`. Those roots are still opaque and backup-required.

The owner Codex worktree also has a clean `control-center/observatory-handoff` branch with nine local
commits beyond its cached local upstream ref. A verified Git bundle containing opaque reachable Git
objects/history was written create-only to Kingston at
`BrainData/host-recovery/git-bundles/control-center-observatory-handoff-20261014.bundle`; its exact
head, upstream, and bundle hash are in the
[`ignored-root custody receipt`](evidence/portability-dr-ignored-root-and-branch-custody-2026-10-14T0645Z.json).
The commit payloads were not semantically inspected and could contain protected-like material; the
bundle is not merged into `research/active`. An earlier read-only remote query found the tip absent
from the queried remote ref set only; it does not prove there is no other remote copy.

The same owner worktree contains ignored `apps/control-center/.local` state: six directories, 32
regular files (three `.log`, 21 `.png`, eight `.json`), and 2,941,740 bytes. No contents were read.
Classify the current live state as private, opaque, unresolved, and backup-required until it is
covered by an accepted source view or separately screened. A provisional Kingston tar with
per-file hashes was previously verified and restored to quarantine, but it is an uncoordinated live
copy: three logs may have changed outside individual read windows, and xattrs/ACLs were omitted. A
06:50Z current-path hash check confirms the same archive remains present. `lsof` reported no open
files under the source path at that instant, which does not prove source inactivity or a cutoff. The
fresh Git bundle does not contain `.local`. See the
[`opaque archive recheck receipt`](evidence/portability-dr-codex-local-archive-recheck-2026-10-14T0650Z.json)
and [`claim correction receipt`](evidence/portability-dr-ignored-root-claim-correction-2026-10-14T0651Z.json).
This review also does not close owner/protected roots or establish a durable source boundary.

A follow-up `lstat`/device lookup verified that the declared owner/research checkouts and `work/`
roots, private Brain config, LaunchAgents, owner Codex worktree, and its `.local` state are directory
entries on the current Data APFS volume (UUID
`430740ca-42e7-4e6c-a07a-e4e7370152cc`). Kingston `BrainData` is a separate directory entry on its
pinned volume UUID/root. The named entries had no symlink path components and resolve to their
declared paths. This is an entry-level identity check only; descendants, internal firmlinks, full
root closure, and a held offline Data view remain unproven. See the
[`root entry identity receipt`](evidence/portability-dr-root-entry-identity-2026-10-14T0647Z.json).

The v4 private custody planner and synthetic-only artifact exerciser classify one exact three-root
group as private, opaque, unresolved, and backup-required. The separate
`tools/brain_host_tree_capture_v4.py` now exercises the source capture path against a synthetic held
snapshot handle: it traverses all three held roots without following links, builds the complete
cross-root hardlink roster using stat/type observations before excluding exact `.env.local` files
and every alias, then probes xattr/ACL/flag metadata only for included objects. It streams only
included regular-file groups, records symlink text without materializing targets, and fails closed
when metadata cannot be observed. Darwin symlink metadata uses `O_SYMLINK` descriptors; the Linux
fixture adapter uses `/proc/self/fd` with no-follow xattr calls at the leaf. Darwin ACL values use
the versioned `acl_to_text` representation
(whose returned text allocation is released with `acl_free`, as documented by [Apple's acl_to_text
manual](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man3/acl_to_text.3.html));
the ACL and allocated text buffers are both freed. A disposable Darwin fd probe observed ENOENT
for no extended ACL; other ACL errors remain unresolved. It captures only disposable synthetic
filesystem fixtures and does not access project state.

Metadata reads check planned object identity before and after access and validate exact xattr, ACL,
and flag obligations. After source streaming, the implementation re-walks the held roots and
metadata, compares the canonical roster, and rereads the exact output member set (objects, manifest,
and receipt) through fresh no-follow descriptors. Interruption, source drift, or post-borrow
revalidation failure removes only its nonce-scoped temporary tree. Tests assert that excluded
credential inode bytes and xattr values are never read, a path swapped to a credential inode fails
before metadata reads, unresolved outside hardlink aliases remain explicit, excluded `.env.local`
symlink text is not queried, symlink targets are not followed, unsupported xattr inventory fails
closed, xattrs are captured, drift and interruption do not leave artifacts, and readback hashes
match. The 71 focused attestor/v4 tests,
scoped Ruff/format, strict mypy for the capture module, and `git diff --check` pass.

This path requires a synthetic `HeldSnapshotT`; its receipt sets `complete_marker_published`, real
T, real capture, Kingston custody, restore verification, and erase readiness to false. The
synthetic flag is an in-process label, and the output parent is caller-supplied: invoke this API only
with disposable fixture roots and private scratch. It is not wired to owner-data capture or
persistent publication and cannot mint a production `COMPLETE` marker. Darwin execution is tested
on this Mac's temporary fixture filesystem, not a real read-only Data snapshot. Populated ACL
handling, symlink xattr restoration, and owner-scale in-memory roster capacity are not qualified.
This is an implementation contract test only; source completeness, Kingston durability, a full
restore, and factory-reset readiness remain unproven.

The next synthetic boundary, `tools/brain_host_tree_restore_v4_synthetic.py`, consumes only that
capture bundle and recreates included directories, bytes, and within-capture hardlink topology under
a fresh 0700 disposable quarantine. It validates the canonical plan, receipt-bound manifest hash,
exact source bundle membership, every captured object hash, metadata evidence roster, and a second
source-bundle read against the original pins before returning. The witness remains under a pending
name until fresh quarantine and capture readback pass, then is renamed descriptor-relatively and
read back again. It never materializes symlinks, including absolute or dangling
links; exact `.env.local` entries and aliases remain absent. Source xattr/ACL/flags/owner metadata is
preserved as a mode-0600 evidence member but is not applied. External hardlink aliases remain
explicitly unresolved by count, without a guessed location. The witness also fixes `real_T`,
Kingston custody, full restore, and erase readiness to false.

The focused capture/restore suite passes 21 tests (85 combined APFS attestor/v4 planner/synthetic
planner/capture/restore tests). It includes cross-root hardlinks, unresolved
outside aliases, excluded credentials and aliases, absolute/dangling symlinks, corrupt objects,
missing/unexpected members, wrong object/group mapping, preexisting-target refusal, FIFO rejection,
simulated cross-device link failure, interrupted open/write cleanup, bundle replacement, and
post-write quarantine mutation. Readback checks exact tree membership, object bytes, hardlink inode
identity, private metadata evidence, witness bytes, and
source capture integrity against the initially verified manifest, receipt, and object map. Object
preflight streams hashes under a 128-MiB per-object and 512-MiB aggregate synthetic fixture limit.
Restore also checks distinct hardlink groups have distinct inodes and every file's link count equals
its in-quarantine alias count. After acquiring the quarantine descriptor, recursive failure cleanup
is descriptor-relative; if it cannot acquire that descriptor, it removes only the still-empty
nonce directory and reports incomplete cleanup if that fails. Ruff and strict mypy pass for the
restore implementation. This is still
same-host disposable synthetic evidence: it does not exercise owner state, a real T, Kingston,
compatible-host restore, metadata application, launchd, or user authentication. Caller-selected
scratch and the process-local synthetic label are not authority controls. It does not change the
NO-GO factory-reset verdict.

Sol independently accepted this bounded implementation after reviewing its input/output identity,
cleanup, FIFO rejection, link-topology, and publication-failure boundaries. Sol did not rerun the
85 tests and did not access owner or protected payloads; the pass count above is local execution
evidence, not an independent reproduction.

## Portability boundary and remaining risk

The currently tested host bootstrap is macOS-specific. Kingston qualification uses `diskutil`, APFS,
and macOS volume-ownership checks; recurring host services use `launchd`; owner bundles and some
filesystem metadata are macOS-bound. A clean Mac can rebuild the research development environment
quickly, but owner runtime recovery, private secrets, services, protected custody, and full corpus
replay are not yet rehearsed.

Linux/server recovery requires a separately qualified volume-identity and descriptor-bound writer
backend, a service-manager adapter, private secret delivery, and storage semantics for object-store
or multi-worker paths. Shared mutable SQLite remains single-writer. No cluster, object-store,
replication, or Kingston-loss capability is implied by this work.

Kingston is presently the only reset-surviving physical copy of `BrainData` and any future owner
recovery artifacts. Its failure destroys the only declared restore source. This single-copy risk is
accepted only for recovery from owner-Mac loss. A future independent large SSD/NAS must be treated
as a new replication capability with hash verification and a full recovery rehearsal.

## Remaining gates before `SAFE TO ERASE`

The Recovery compatibility/point-check probe has been completed once; do not request another boot
merely to repeat it. A **user-dependent** Recovery boot remains necessary only when a concrete
source-view rehearsal is ready. For that combined gate, record clean shutdown and every
authentication/recovery-key/UI choice, then make the first Terminal action check the pinned Data UUID,
FileVault, lock, and mount state before Disk Utility or source access. Capture boot time, earliest
available disk arbitration/APFS logs, the complete APFS volume/group listing, kernel mounts, and one
`listSnapshots` attempt; repeat the identity and mount checks at the end. An inaccessible snapshot
listing or incomplete log is `unknown`. Stop on changed identity, unlocked or mounted Data, any
unmapped alias, or any earlier successful or uncertain Data mount. If the earliest history cannot be
established, retain the explicit human-attested assumption; do not claim proven no-prior-writable
mount. This gate alone still does not authorize unlock/mount or qualify T. Metadata-only
owner/local-root closure can proceed independently.
Only after these boundaries are designed should a real offline Data-view adapter be implemented. If
the offline route is infeasible, qualify a conservative Data free-space floor,
bounded snapshot retention, and interactive admin
procedure before creating a source snapshot. The 06:15Z read-only preflight found about 12.8 GiB
free at 97% Data use and no Data snapshot; the prior 06:10Z check found about 12 GiB at 98%. An
offline route must still prove shutdown boundary, exact volume/root identity, read-only mount flags,
held-view stability, and tool availability. The APFS T attestor contract remains synthetic-tested; a
real T still requires an independently pinned source-view identity and Sol-reviewed host plan. A
fresh read-only status check sees 18 Brain/D43 launchd labels, eight with PIDs; the earlier process
census found 72 processes in the configured owner/research roots. This work changed none of them. The three
`work/wallets` subtrees are authorized for opaque, private, unresolved custody only; preserve them
without semantic reading and do not infer a role from their names. No payload has yet been read or
copied. The existing recovery procedure keeps services inert; this checkpoint does not authorize
starting them.

1. Inventory every owner/research/local-config/service root, including owner Codex worktree local
   state, and bind each to a source point or an
   explicit reauthentication, rebuild, disposable, or unresolved disposition. Classify potentially
   private environment entries and all excluded Codex worktree ignored records. Keep the three
   research `work/wallets` roots opaque, private, unresolved, and backup-required. Preserve all 56
   Brain/D43 definitions inert or prove which are reproducible.
2. Rehearse the conditional offline read-only Data view, including first-action Locked-state gate,
   pre-Terminal authentication log, boot-session mount history/gaps, all Data aliases, and explicit
   read-only held-view checks. If prior mount history cannot be established, retain that assumption
   explicitly. If the offline route is infeasible, qualify a conservative free-space floor, bounded
   snapshot retention, and interactive admin procedure.
   Bind the exact approved Data volume and all declared roots; select and independently pin the
   approved source view. No live-copy pass can prove an owner frontier while state changes.
3. Complete opaque custody of every identified F01/D43/reserved root and its lineage; verify hashes
   without semantic access. Current metadata-only path inventory does not prove F01/reserved are
   absent or covered.
4. Capture the authorized owner runtime/private configuration and unique local research state from
   the accepted source view. Classify and exclude only proven disposable caches.
5. Establish the real Kingston K STOP/pending/durable prefix and reconcile it with the source cutoff.
6. Restore the local archive and Kingston-backed corpus dependencies into an isolated clean
   compatible checkout; run identity, integrity, replay, and inert-service checks. Do not issue
   provider requests or launch a collector.
7. Record the Data snapshot cutoff and separate Kingston frontier, post-cutoff uncertainty,
   exclusions, artifact hashes, host identity, inert service count, and single-Kingston/plaintext
   custody risks in an immutable receipt; commit and push the work.
8. Obtain Sol's independent final readiness review. Until then the owner Mac is not safe to erase.
