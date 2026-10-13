# Portability and Disaster Recovery

This document owns Brain host portability, recovery inventory, and erase readiness. The project
checkout, owner runtime, large research-data root, and protected evaluations have separate authority
and recovery boundaries. A clean code clone is not a recovery of Brain state.

## Current readiness

**NO-GO for factory reset as of 2026-10-14.** The accepted recovery scope is loss or erasure of the
owner Mac while Kingston remains healthy. The user explicitly accepts Kingston as the only
reset-surviving physical copy for this iteration. Kingston failure, theft, or corruption therefore
remains an uncovered data-loss risk, but is not a blocker for the Mac-loss claim. A future 1-TB+
SSD, NAS, or other independent destination must add a separately verified replica and a
Kingston-loss rehearsal.

Live checks on the current Mac:

- `research/active` and `origin/research/active` are clean and both at
  `33ff52fbce1571caf1c7ae5bab7303f7f6026fca`. `main` and `origin/main` remain at
  `770699d3ef8558518ae750b6d5d6cab41c2e82c8`; do not advance `main` until the capability is
  qualified. The supplied handoff and prior DR text were stale; the live repository state is the
  authority.
- The owner checkout at `/Users/root1/Desktop/memecoin-ai` is clean at
  `a5944742b6b8ed0466c50bdeb72370ed3e3f2a31`, 15 commits behind `origin/main`. Read-only B01
  status is STOP with an expired runtime and zero orders within B01's scope; this is not an
  owner-wide trading-state audit. The owner operator and several owner paper/UI processes remain
  running.
- Internal `work/` trees occupy about 16 GiB in research and 11 GiB in owner. `~/.config/brain-research`
  and LaunchAgent definitions are host-local. Research and owner private `.env` files are mode
  0600. These are candidates for an encrypted, source-view backup; no live owner state or protected
  content has been copied.
- The internal Data volume has about 18 GiB free. No APFS snapshots exist on Data or Kingston;
  `tmutil` reports no configured Time Machine destination. The APFS snapshot system call requires
  superuser privilege and an entitlement. A privileged, read-only owner-wide snapshot has not been
  created or qualified. Copying live SQLite/WAL files one by one is not an acceptable substitute.
  Any eventual source snapshot must have a bounded retention window and monitored free-space
  headroom because continued service writes can retain old blocks; do not hold a snapshot after
  acceptance.
- Kingston is mounted at `/Volumes/Kingston`, UUID
  `8f8f1081-9be4-4914-9111-75f1b90255bf`, case-sensitive APFS, unencrypted, with root ID
  `9428695af6d4f46c08e1a444cdc66de8`. A current `research_storage.py check --require-root` passed
  with 217,582,518,272 bytes free. It is the only external physical drive. The canonical
  `BrainData` corpus remains there; its root identity is verified, but its contents have no second
  physical copy.
- A clean checkout from the current research remote took 44.59 seconds; pinned, uncached dev setup
  took 36.41 seconds; `make smoke` took 1.05 seconds. This proves a roughly 82-second software
  bootstrap on this macOS/Python 3.13 host, not owner/corpus recovery. A synthetic encrypted archive
  feasibility test passed archive restore, GPG integrity, file mode, hardlink, symlink, sparse
  logical size/allocation, xattr, resource fork, ACL, immutable-flag, wrong-passphrase rejection,
  and corrupt-ciphertext rejection. It did
  not exercise Kingston descriptor-bound publication, interruption, collision, detach, or
  commit-marker ordering. The test used only generated synthetic files. See
  [`2026-10-14 DR qualification`](evidence/portability-dr-qualification-2026-10-14.json).
- Repository-wide `make check` currently stops at Ruff with 107 pre-existing findings outside the
  DR scope.
  Focused Ruff and format checks for the new synthetic rehearsal helper pass; `make smoke` passes.

No providers, prospective collectors, owner services, champion, protected evaluation, or trading
state were changed by the DR work. No provider request or collector was initiated by the DR work.
No protected application or dataset reader was used. No owner/protected backup ciphertext has yet
been made. Recovery-secret custody is still not proven, and source consistency for the owner
runtime is still unresolved; both block `SAFE TO ERASE`.

## State ownership and restore rules

| State | Canonical source of truth | Recovery rule |
|---|---|---|
| Research code, history, canonical docs, committed receipts | Git remotes and immutable commits | Clone the exact accepted `research/active` commit; verify clean identity; rebuild dependencies from checked-in pins. |
| Owner code and deployed runtime identity | Owner Git remote plus exact runtime bundles, deployment pointers, and state under owner `work/` | Restore an inert copy; never infer the deployed bundle from repository `HEAD` or recreate active services automatically. |
| Large research captures, datasets, journals, immutable evidence, and recovery records | Kingston `BrainData` plus each object's manifest, lineage, STOP, and recovery records | Verify UUID, root marker, artifact hashes, and semantic replay before admission. This is canonical storage, not a second backup. |
| Unique local owner/research state | Local owner/research `work/`, private host config, and installed service definitions until a verified reset-surviving archive is published | Take a single read-only APFS source view, stream an opaque archive through encryption, then prove decrypt and restore. Do not copy a live SQLite database file-by-file. |
| Private provider configuration | Current private `.env` files until archived; provider account recovery is a fallback | Include only inside encrypted custody or reissue. Keep mode 0600. Never put values in Git, receipts, logs, plists, or process arguments. |
| Protected F01, D43, and reserved holdout | Existing protected source roots and their frozen declarations/manifests/lineage | Preserve as opaque bytes, retain classification and lineage, and encrypt separately from public receipts. No content parsing, training, evaluation, admission, or relabeling. |
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

The user authorizes read-only inventory and backup-only copying. The protected procedure is:

1. Define the custody set from path/type/size/metadata only: original protected bytes and their
   declarations, manifests, membership/split records, STOPs, and recovery dependencies. Keep the
   path inventory inside encrypted custody. Reject unresolved symlinks and out-of-scope filesystem
   dependencies. Do not inspect, parse, train on, evaluate, admit, or relabel protected content.
2. Use a qualified immutable source view. Prefer one read-only APFS snapshot covering the internal
   owner checkout, runtime/state roots, and protected roots. Record volume and snapshot identity
   plus cutoff. If dependencies live on other filesystems, give each its own explicit recovery
   point. A crash-consistent snapshot does not claim that an application operation completed.
3. Stream a metadata-preserving archive from that view directly into encryption. Write ciphertext
   only beneath the verified Kingston root through a held descriptor and create-only temporary
   name. Do not stage plaintext on unencrypted Kingston. Use isolated GPG configuration and a
   recovery secret held by the user outside the erased Mac. Never put the secret in arguments,
   environment, Git, receipts, or beside ciphertext.
4. Reopen the ciphertext with a fresh verifier; decrypt fully to a hashing sink; require GPG's
   terminal integrity success; validate archive completeness without a protected application
   reader; and compare the private archive digest. Fsync ciphertext and encrypted manifest, hash
   ciphertext, then publish a create-only group with a commit marker last. Failed or interrupted
   output is unpublished and cannot count as custody.
5. Qualify metadata and failure behavior using synthetic files before any protected stream. Retain
   the source snapshot until acceptance. Remove or quarantine only generated scratch, never original
   protected bytes or STOP/history.

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
4. Restore the accepted encrypted local-state group into a new isolated incoming location. Verify
   ciphertext and private manifest hashes, GPG integrity, archive completeness, and metadata for
   every member. Apply source identity, SQLite integrity, durable-prefix, lineage, STOP, gap, and
   unknown/censored/failed-state checks only to non-protected owner/research state and Kingston
   corpus dependencies. For F01/D43/reserved members, verify opaque byte/ciphertext integrity and
   custody metadata only; do not use protected application readers or perform replay, admission,
   training, or evaluation.
5. Restore owner runtime and paper state inert. Keep all LaunchAgents unloaded; do not initialize
   databases, checkpoint the original state, restart jobs, or issue provider requests during a
   rehearsal. Recreate only the documented service set after separate role authorization and
   non-protected source/identity/STOP checks. Protected material remains excluded from application
   readers and all semantic validation.
6. Compare the restored evidence frontier and runtime identities to the accepted recovery receipt.
   Any missing prefix, failed replay, unclassified state, or unavailable key blocks readiness.

## Service restore posture

The read-only `launchctl list` inventory captured 18 labels. Eight had a running PID: the owner
`operator` and seven owner UI processes. Ten were listed without a PID, including the D43 calendar,
paper/audit/train/source labels, and the research storage-health monitor. This is observed source
state, not a proposed startup set. No Senses capture/watchdog label was listed.

| Observed role and labels | Clean restore posture |
|---|---|
| Owner control: `ai.memecoin.brain.operator` (running) | Preserve its exact deployment definition and state. Keep unloaded; any later start requires separate owner release/recovery authorization and identity/STOP checks. |
| Storage monitor: `ai.memecoin.brain.research-storage-health-v1` (listed, no PID) | Preserve the definition. Keep unloaded during restore; later activation is a candidate only after the new host binding, source, and plist are verified. It records storage health and has no acquisition authority. |
| Protected evaluation: `ai.memecoin.d43.calendar01` (listed, no PID) | Preserve its definition and protected custody only. Do not install, load, or run evaluation as part of recovery. |
| Legacy jobs/source/audit/train/paper: `b04`, `b09r2.audit`, `b09r2.source`, `b10r2.train`, `b12.paper`, `expansion.paper`, `b10r2.paper`, `b12r2.paper` (all listed, no PID) | Preserve definitions and unique state in custody. Exclude from automatic install/start; each needs separate current justification and authorization. |
| Owner UI services: `b12.ui`, `b10.ui`, `expansion.ui`, `b12r2.ui`, `b10r2.ui`, `b12r1.ui`, `b10r1.ui` (all running) | Preserve definitions and exact runtime identity. Leave absent/unloaded on the restored host; a UI label does not prove read-only behavior or deployment authority. |

The initial restored-host baseline is zero loaded Brain/Senses labels. Keep historical plist files in
the encrypted custody set rather than copying the old LaunchAgents directory wholesale. Operator
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
recovery ciphertext. Its failure destroys the only declared restore source. This single-copy risk is
accepted only for recovery from owner-Mac loss. A future independent large SSD/NAS must be treated
as a new replication capability with hash verification and a full recovery rehearsal.

## Remaining gates before `SAFE TO ERASE`

1. Establish recovery-secret custody that is reachable after reset, then prove a fresh decrypt
   without exposing the secret.
2. Obtain a qualified, read-only source snapshot or another Sol-approved owner-wide consistent
   recovery point without changing production services. Bound snapshot lifetime and monitor free
   space; current live-copy alternatives cannot prove an atomic owner frontier.
3. Complete the protected opaque archive using the reviewed custody procedure; preserve F01/D43/
   reserved classifications and verify it without semantic access.
4. Capture the authorized owner runtime/private configuration and unique local research state from
   the accepted source view. Classify and exclude only proven disposable caches.
5. Restore the local archive and Kingston-backed corpus dependencies into an isolated clean
   compatible checkout; run identity, integrity, replay, and inert-service checks. Do not issue
   provider requests or launch a collector.
6. Record the exact durable cutoff, post-cutoff uncertainty, exclusions, artifact hashes, host
   identity, and single-Kingston-copy risk in an immutable receipt; commit and push the work.
7. Obtain Sol's independent final readiness review. Until then the owner Mac is not safe to erase.
