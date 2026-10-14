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

- The latest reviewed/pushed code baseline `research/active` and `origin/research/active` use is
  `7d8dc82f94e5c9e2631bc38ae79f1229cccdeea1`; the immutable inventory
  receipt records the prior source cutoff at `eaedeae61cd101882835095c2f54158ca07a2945`. `main`
  and `origin/main` remain at `770699d3ef8558518ae750b6d5d6cab41c2e82c8`; do not advance `main`
  until the capability is qualified.
- The owner checkout at `/Users/root1/Desktop/memecoin-ai` is clean at
  `a5944742b6b8ed0466c50bdeb72370ed3e3f2a31`. Read-only B01–B04 status reports all four scopes
  STOPped and expired. They retain 48, 48, 19, and 28 pending predictions respectively; these
  remain pending and are not to be resumed. This status does not audit owner operator/paper state.
  The owner operator and several owner paper/UI processes remain running.
- Internal `work/` trees occupy about 16 GiB in research and 11 GiB in owner. `~/.config/brain-research`
  occupies about 348 KiB. The owner `.env.local` is mode 0600 (470 bytes); its contents were not
  read. Credentials are reissued by the user after restore. `~/Library/LaunchAgents` contains 55
  Brain plist files while `launchctl list` reports 18 loaded labels, eight with PIDs. The loaded
  service list is not the full host-definition inventory; preserve definitions inert or prove them
  reproducible before erase.
- Kingston `BrainData` occupies about 19 GiB and remains on case-sensitive APFS with UUID
  `8f8f1081-9be4-4914-9111-75f1b90255bf`, root ID
  `9428695af6d4f46c08e1a444cdc66de8`, and about 203 GiB free. The 50-GiB storage floor passes.
  Kingston is the only reset-surviving physical copy.
- The internal Data volume has about 14 GiB free. `diskutil apfs listSnapshots` finds no Data
  snapshots and `tmutil` reports no configured destination. A noninteractive privilege check
  confirms the session cannot create or mount a privileged snapshot without user authentication.
  The available unprivileged `iostat` view combines reads and writes, so it did not establish a
  safe write-churn bound. A read-only owner-wide snapshot has not been created or qualified.
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
  No actual owner, private-config, LaunchAgent, protected, or Kingston corpus recovery archive has
  been captured or restored. No full clean-room restore has been rehearsed.
- `tools/brain_host_recovery.py` provides read-only host status and inert research-checkout bootstrap.
  `tools/brain_host_tree_custody_v3.py` and `tools/brain_host_recovery_set.py` verify declared tree
  members and a restored quarantine witness. Their `application_recovery_qualified` result remains
  false; the current set schema is an integrity index, not owner-root closure or erase approval.
- These live inventory and qualification observations are recorded in the immutable
  [`read-only DR inventory receipt`](evidence/portability-dr-readonly-inventory-2026-10-14T0101Z.json).

No providers, prospective collectors, owner services, champion, protected evaluation, or trading
state were changed by the DR work. No provider request or collector was initiated by the DR work.
No protected application or dataset reader was used. No owner/protected custody archive or complete
restore witness exists. Recovery-set code currently binds only caller-declared roles and one opaque
D43 root; it does not prove full owner-root closure, F01/reserved custody, service-definition
closure, or semantic replay. Recovery-secret custody is not required for the accepted plaintext
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

The read-only `launchctl list` inventory captured 18 loaded labels. Eight had a running PID: the owner
`operator` and seven owner UI processes. Ten were listed without a PID, including the D43 calendar,
paper/audit/train/source labels, and the research storage-health monitor. This is observed source
state, not a proposed startup set. No Senses capture/watchdog label was listed.

There are 55 Brain-named plist files in `~/Library/LaunchAgents`; 37 are not represented among the
18 currently loaded labels. Their definitions remain host-local. Preserve them as inert custody
bytes or prove each one is reproducible from canonical source before erase; never install the full
directory wholesale.

| Observed role and labels | Clean restore posture |
|---|---|
| Owner control: `ai.memecoin.brain.operator` (running) | Preserve its exact deployment definition and state. Keep unloaded; any later start requires separate owner release/recovery authorization and identity/STOP checks. |
| Storage monitor: `ai.memecoin.brain.research-storage-health-v1` (listed, no PID) | Preserve the definition. Keep unloaded during restore; later activation is a candidate only after the new host binding, source, and plist are verified. It records storage health and has no acquisition authority. |
| Protected evaluation: `ai.memecoin.d43.calendar01` (listed, no PID) | Preserve its definition and protected custody only. Do not install, load, or run evaluation as part of recovery. |
| Legacy jobs/source/audit/train/paper: `b04`, `b09r2.audit`, `b09r2.source`, `b10r2.train`, `b12.paper`, `expansion.paper`, `b10r2.paper`, `b12r2.paper` (all listed, no PID) | Preserve definitions and unique state in custody. Exclude from automatic install/start; each needs separate current justification and authorization. |
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
   explicit reauthentication, rebuild, disposable, or unresolved disposition. Preserve all 55
   Brain plist definitions inert or prove which are reproducible.
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
