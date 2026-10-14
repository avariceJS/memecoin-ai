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

- The latest exact-code restore rehearsal used `7d8dc82f94e5c9e2631bc38ae79f1229cccdeea1`, an
  ancestor of the published DR changes on `research/active`. The immutable inventory receipt
  records the prior source cutoff at `eaedeae61cd101882835095c2f54158ca07a2945`. `main` and
  `origin/main` remain at `770699d3ef8558518ae750b6d5d6cab41c2e82c8`; do not advance `main` until
  the capability is qualified.
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
- Internal `work/` trees occupy about 16 GiB in research and 11 GiB in owner. Their top levels have
  234 and 275 mixed entries respectively, so they still need a complete recovery classification.
  `~/.config/brain-research` occupies about 348 KiB and currently has 14 entries. The owner
  `.env.local` is mode 0600 (470 bytes); its contents were not read. Credentials are reissued by the
  user after restore. `~/Library/LaunchAgents` contains 55 Brain plist files plus one D43 plist.
  At 01:12Z, 18 labels were loaded and nine had PIDs, including `ai.memecoin.brain.b09r2.source`; by
  01:17Z, 18 remained
  loaded and eight had PIDs, with that source label loaded but without a PID. At the later probe,
  72 processes had cwd metadata under owner/research roots. No process arguments or environments
  were read. These time-bounded observations do not establish whether the source label performed
  earlier work or whether other services wrote state. DR work did not change or activate services.
  The loaded service list is not the full host-definition inventory. Metadata-only inspection found
  all 56 definitions have `RunAtLoad`; four contain five environment entries whose values have not
  been classified. Do not copy those entries blindly: treat them as potentially private, preserve
  only safe inert definitions, and restore credentials by user reauthentication. See the
  [`LaunchAgent metadata receipt`](evidence/portability-dr-launchagent-metadata-2026-10-14T0120Z.json).
- Kingston `BrainData` occupies about 19 GiB and remains on case-sensitive APFS with UUID
  `8f8f1081-9be4-4914-9111-75f1b90255bf`, root ID
  `9428695af6d4f46c08e1a444cdc66de8`, and about 203 GiB free. The 50-GiB storage floor passes.
  Kingston is the only reset-surviving physical copy.
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
- A fresh clone of `7d8dc82` bootstrapped Python 3.13 and passed `make smoke`; both new recovery CLIs
  also passed direct `--help` invocation from that clone. The bootstrap reported no storage or
  service changes. This still ran on the existing Mac, did not isolate host caches, and restored no
  owner/private/protected/Kingston state. See the
  [`bootstrap rehearsal receipt`](evidence/portability-dr-bootstrap-rehearsal-2026-10-14T0107Z.json).
  Since that rehearsal, the private config root has been captured to Kingston as provisional opaque
  quarantine, but it has not been restored or activated. No owner runtime, LaunchAgent, classified
  protected, or Kingston corpus recovery set has been captured and restored. No full clean-room
  restore has been rehearsed.
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
  pins the bundle, tar, private manifest, and completion marker hashes. This is opaque, provisional custody: the three log files may
  have changed outside the per-file read windows, xattrs/ACLs are not preserved, and no application
  state was interpreted. The 10,693 ignored-status records reconcile as 10,631 under `node_modules`,
  ten under `dist`, 11 under `.ruff_cache`, nine other ignored records, and 32 `.local` files. The
  first 10,661 records were excluded from this archive; their rebuildability/disposability is not
  yet proven. This does not establish a
  consistent source point or close the owner-wide recovery boundary.

No providers, prospective collectors, owner services, champion, protected evaluation, or trading
state were changed by the DR work. No provider request or collector was initiated by the DR work.
No protected application or dataset reader was used. No qualified owner-wide recovery set, classified
F01/D43/reserved custody set, or complete restore witness exists. The provisional Codex bundle may
contain protected-like objects whose role remains unclassified. Recovery-set code currently binds
only caller-declared roles and one opaque D43 root; it does not prove full owner-root closure,
F01/reserved custody, service-definition closure, or semantic replay. Recovery-secret custody is not required for the accepted plaintext
custody scope; provider credentials require user reauthentication after restore. Source consistency
and completeness remain unresolved and block `SAFE TO ERASE`.

## State ownership and restore rules

| State | Canonical source of truth | Recovery rule |
|---|---|---|
| Research code, history, canonical docs, committed receipts | Git remotes and immutable commits | Clone the exact accepted `research/active` commit; verify clean identity; rebuild dependencies from checked-in pins. |
| Owner code and deployed runtime identity | Owner Git remote plus exact runtime bundles, deployment pointers, and state under owner `work/` | Restore an inert copy; never infer the deployed bundle from repository `HEAD` or recreate active services automatically. |
| Large research captures, datasets, journals, immutable evidence, and recovery records | Kingston `BrainData` plus each object's manifest, lineage, STOP, and recovery records | Verify UUID, root marker, artifact hashes, and semantic replay before admission. This is canonical storage, not a second backup. |
| Unique local owner/research state | Local owner/research `work/`, `~/.config/brain-research`, and Brain LaunchAgent definitions until a verified Kingston custody set is published | Capture from one read-only APFS source view where available. Use SQLite online backups for declared non-protected databases. Preserve unknown and unfinished state; never copy a live database/WAL family file-by-file. |
| Private provider configuration | User reauthentication/reissuance; current `.env.local` is not needed for inert restore | Do not capture secret values. Keep local values mode 0600 and never place them in Git, public receipts, logs, plists, or process arguments. |
| Protected F01, D43, and reserved holdout | Existing protected source roots and their frozen declarations/manifests/lineage | Preserve exact bytes, classification, and lineage as opaque custody. Verify byte/custody metadata only; no protected application readers, replay, training, evaluation, admission, or relabeling. |
| Host bindings | Per-host private config, including Kingston UUID/root binding | Recreate from the attached volume and verify explicitly. Paths are locators, not artifact identity. |
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

1. On a clean compatible macOS host, install Git and a supported Python. Clone the exact accepted
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

## Service restore posture

The 01:12Z read-only `launchctl list` inventory captured 18 loaded labels. Nine had a running PID:
the owner `operator`, seven owner UI processes, and `ai.memecoin.brain.b09r2.source`. Nine were listed
without a PID, including the D43 calendar, paper/audit/train labels, and the research storage-health
monitor. By 01:17Z, eight of 18 had PIDs and `b09r2.source` was loaded without one. This is observed
source state, not a proposed startup set. No Senses capture/watchdog label was listed. Neither probe
proves the source workload or write frontier.

There are 55 Brain-named and one D43-named plist files in `~/Library/LaunchAgents`; at least 38
definitions were not represented among the 18 labels loaded at the 01:12Z probe. Their definitions
remain host-local.
Four definitions contain environment entries with unclassified values. Preserve safe declarations
as inert custody or prove them reproducible from canonical source; classify or omit potentially
private values and reauthenticate credentials. Never install the full directory wholesale.
Their declared arguments refer to owner, research, and private-config roots; 56 argument references
pass through only three distinct symlink mappings. Output/error paths point either inside owner work
or to `/dev`; six owner log path references per stream currently point to missing files. These are
path-field references, not a count of unique dependencies. On a replacement host, rebuild any authorized
service definitions against verified roots instead of recreating old symlink accidents. See the
[`LaunchAgent path receipt`](evidence/portability-dr-launchagent-paths-2026-10-14T0124Z.json).

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

1. Inventory every owner/research/local-config/service root and bind each to a source point or an
   explicit reauthentication, rebuild, disposable, or unresolved disposition. Classify potentially
   private environment entries and all excluded Codex worktree ignored records. Preserve all
   56 Brain/D43 definitions inert or prove which are reproducible.
2. Obtain a qualified, read-only source snapshot or another Sol-approved owner-wide consistent
   recovery point without changing production services. Bound snapshot lifetime and monitor free
   space; current live-copy alternatives cannot prove an atomic owner frontier.
3. Complete opaque custody of every identified F01/D43/reserved root and its lineage; verify hashes
   without semantic access. Current metadata-only path inventory does not prove F01/reserved are
   absent or covered.
4. Capture the authorized owner runtime/private configuration and unique local research state from
   the accepted source view. Classify and exclude only proven disposable caches.
5. Restore the local archive and Kingston-backed corpus dependencies into an isolated clean
   compatible checkout; run identity, integrity, replay, and inert-service checks. Do not issue
   provider requests or launch a collector.
6. Record the Data snapshot cutoff and separate Kingston frontier, post-cutoff uncertainty,
   exclusions, artifact hashes, host identity, inert service count, and single-Kingston/plaintext
   custody risks in an immutable receipt; commit and push the work.
7. Obtain Sol's independent final readiness review. Until then the owner Mac is not safe to erase.
