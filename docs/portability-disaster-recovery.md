# Portability and Disaster Recovery

This document owns Brain host portability, recovery inventory, and erase readiness. The project
checkout, owner runtime, large research-data root, and protected evaluations have separate authority
and recovery boundaries. A clean code clone is not a recovery of Brain state.

## Current readiness

**NO-GO for factory reset as of 2026-10-14.** The accepted recovery scope is loss or erasure of the
owner Mac while Kingston remains healthy. The user explicitly accepts Kingston as the only
reset-surviving physical copy and accepts unencrypted local custody for this iteration. Kingston
failure, theft, or corruption remains an accepted uncovered risk, not a blocker for the Mac-loss
claim. A future 1-TB+ SSD, NAS, or other independent destination must add a separately verified
replica and a Kingston-loss rehearsal.

Live checks on the current Mac:

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
  `9428695af6d4f46c08e1a444cdc66de8`, and about 203 GiB free. The 50-GiB storage floor passes.
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

## Restore procedure

1. On a clean compatible macOS host, install Git and a supported Python. Sign in to the Git account
   that can read the private remote through its normal user-controlled authentication flow; never
   copy the old host's Keychain, tokens, or session stores. Clone the exact accepted
   research commit from `origin/research/active`; confirm the commit and a clean worktree.
2. Rebuild the base development environment from `requirements-dev.lock` and run `make smoke`.
   Optional ML dependencies remain separate and task-specific.
3. Attach Kingston; recreate a mode-0600 host-only binding using the actual mount path. Verify
   volume UUID, case-sensitive APFS, ownership, mount identity, 50-GiB free-space floor, and
   `BrainData` root marker using
   `.venv/bin/python tools/research_storage.py check --config <private-binding> --min-free-bytes 53687091200 --require-root`.
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
checkout plus any other source roots that must be excluded. The command checks all six roles before
creating `brain-restore-<nonce>`, writes only inside that new quarantine, and prints one JSON result.
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

The next stateful gate is still to qualify a conservative Data free-space floor, bounded snapshot
retention, and interactive admin procedure before any snapshot is created. The latest read-only
preflight found about 13.8 GiB free at 97% Data use and no Data snapshot, so creating one is not
currently qualified. The APFS T attestor contract is synthetic-tested, but a real Data T still
requires a separately reviewed host plan and post-creation UUID/XID pin delivered outside the
snapshot. A fresh read-only status check still sees 18 Brain/D43 launchd labels, eight with PIDs,
and 72 processes in the configured owner/research roots; this work changed none of them. The three
`work/wallets` subtrees are authorized for opaque, private, unresolved custody only; preserve them
without semantic reading and do not infer a role from their names. No payload has yet been read or
copied. The existing recovery procedure keeps services inert; this checkpoint does not authorize
starting them.

1. Inventory every owner/research/local-config/service root and bind each to a source point or an
   explicit reauthentication, rebuild, disposable, or unresolved disposition. Classify potentially
   private environment entries and all excluded Codex worktree ignored records. Keep the three
   research `work/wallets` roots opaque, private, unresolved, and backup-required. Preserve all 56
   Brain/D43 definitions inert or prove which are reproducible.
2. Qualify a conservative free-space floor, bounded retention, and interactive admin procedure.
   Obtain a Sol-reviewed Data source snapshot or another owner-wide consistent read-only recovery
   point without changing production services. Bind the exact approved Data volume and three roots;
   select and independently pin the resulting snapshot UUID/XID. Current live-copy alternatives
   cannot prove an atomic owner frontier.
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
