# Portability / Disaster Recovery v2 Roadmap

This document preserves hardened DR as a future capability. The active migration is the narrower,
practical Migration v1 described in [`portability-disaster-recovery.md`](portability-disaster-recovery.md).
V1 acceptance does not imply v2 qualification.

## Objective and claim

Hardened v2 should recover Brain after complete loss or erasure of the owner Mac, assuming Kingston
survives. A clean compatible Mac must reconstruct code, Brain-owned unique state, configuration, and
data into a known, integrity-checked, **inert** state. Restore and Start remain distinct operations.
The final claim requires a real clean-host rehearsal and independent Sol `SAFE TO ERASE` GO. Accepted
residual risks are Kingston as the only reset-surviving physical copy and unencrypted local custody;
a second SSD/NAS and enterprise secret custody are outside this scope.

## State at the v1 scope change

The scope-change baseline was `90b8faf0bfb8604a890d74a4573869ec6c81611d`. The current published
`research/active` continuation point after the practical Migration v1 implementation and Kingston
capture is `6658654b294565975a5c632e8374139a457c0e73`, also at `origin/research/active`. Resume v2
from the current branch after the second-Mac Migration v1 gate; do not move `main` automatically.
The exact uncommitted v3 prototype is
archived as
[`v3-capture-adapter-draft-2026-10-16.tar.gz`](archives/portability-disaster-recovery-v2/v3-capture-adapter-draft-2026-10-16.tar.gz),
SHA-256 `d4118bf333ded8c7633e22aca3bc103182577c6d8506050244548c1ce0126190`. The archive contains
the v3 policy chain, capture adapter, and focused tests. Their extracted SHA-256 values are:

- `brain_host_absence_gap_chain_v3.py`: `d600b957dbd0252f4187914254329bf8cf7d86ac62e89ebfa1d97c8ee70d9ec1`
- `brain_host_capture_absence_gap_v3.py`: `e11cfe02eb5976f35e433f414baa6cb3d7670895a5caf611374ddd9b078468d5`
- `test_brain_host_capture_absence_gap_v3.py`: `caa0e91fbf495d87876369adf582e746c0ff8471b1af33091949493db221cfe5`

The three original files remain in the worktree too; do not delete them while v1 is in progress.

Implemented evidence with deliberately narrow scope:

- Published source-only Recovery kits and v7 staging/bootstrap tooling; read-only HeldDataT identity
  and root locator; metadata-only root census; inert restore-flow prototypes.
- v2 nine-role absence-gap validators bind intent, witness, policy, capacity, and candidate schemas;
  Sol accepted these only as inert document-consistency checks.
- v5 source/capture and capacity code has focused tests and limited synthetic/disposable-host
  rehearsals. It does not establish owner source cutoff, full owner capture, or Kingston frontier.
- The v3 draft covers streamed member hashing, bounded disk-backed indexes, targeted protected
  traversal, reissue precedence, external operation pins, INDEX-last publication, and a temporary
  fixture that runs the stored-INDEX verifier. Its relevant checks passed 200 tests plus Ruff,
  formatting, `py_compile`, and `git diff --check`.

Sol's latest v3 ruling is **NO-GO for production acceptance**. SQLite scratch is stat-checked by
pathname and then reopened with `sqlite3.connect(path)`, leaving a TOCTOU window; there is no trusted
production `ScratchSessionV3` adapter. The draft has no real APFS/HFS source/target/scratch capture,
physical read-only target reattach, or owner-scale resource qualification. The public v3 capture
entrypoint remains fail-closed. This is a bounded v3 implementation/qualification blocker, not a
reason to start v4/v5.

Recovery transcript evidence observed the pinned Data UUID locked and unmounted at the recorded
checks; a pre-Terminal automatic mount attempt failed. Early boot/authentication/mount history and
snapshot-history result remain unknown. Do not repeat Recovery probes only to collect the same
facts.

## Architecture and trust boundaries

- Git is canonical for code and published history; a clean clone does not recover local runtime or
  ignored state. Preserve unreachable/local Git state until explicitly inventoried.
- Kingston `/Volumes/Kingston/BrainData` is the reset-surviving data tier. Protect existing content;
  do not rewrite published history or lower capacity reserves to force a fit.
- Source Data is mutable and does not prove a cutoff while writers remain active. V2 needs a
  qualified held source view, explicit cutoff, and fresh inventory/capture boundary.
- Capture and restore must be identity/descriptor bound, capacity bounded, provenance checked, and
  fail closed on missing roots, drift, aliases, or partial writes.
- Provider credentials and session stores are excluded for re-authentication/reissue. Never print or
  archive secrets blindly. Rebind host paths deliberately; do not depend on old absolute paths.
- F01, D43, and reserved holdout are protected. Any future custody is opaque bytes and lineage only;
  never parse, train, evaluate, relabel, resplit, or otherwise use their semantics.
- Restore creates a known inert host. No collectors, provider requests, services, protected
  evaluations, or trading start automatically. Start is a separate, explicit operation.
- PersonalBackup is a user-managed transition, not permanent Brain capacity. Do not inspect, move,
  or delete it. Any Brain capacity gate uses a fresh post-transition Kingston measurement.

## Remaining v2 path

1. Reopen the archived v3 slice only after practical Migration v1 succeeds. First implement
   descriptor-bound scratch creation/SQLite identity and cleanup; retain fail-closed publication.
2. Qualify actual capture using distinct disposable APFS/HFS source, target, and scratch identities;
   exercise failure/partial cases, physical target read-only reattach, and owner-scale memory, FD,
   spool, and free-floor bounds. Do not create another protocol generation unless Sol finds a new
   fundamental contract problem.
3. Qualify the installed RecoveryOS held read-only source view and deterministic boot-history rule.
   Establish exact Data aliases and source cutoff **T** without relying on live-copy timing.
4. Close all nine source roles and opaque protected F01/D43/reserved custody. Preserve role lineage,
   SQLite/WAL companions, Git/local state, and explicit exclusions/reissue requirements.
5. Compute fresh capacity and capture the authorized full set to Kingston. Independently read it
   back, qualify STOP/pending/durable Kingston frontier **K**, and reconcile `T↔K` with post-cutoff
   uncertainty explicit.
6. Restore the complete capture to an isolated clean compatible Mac. Verify hashes, provenance,
   DB/state presence, bootstrap/smoke, and inert service reconstruction. Prove no automatic
   activation, provider calls, collection, protected evaluation, or trading.
7. Record T, K, exclusions, host identity, integrity, inert state, and single-Kingston residual in
   immutable evidence. Obtain Sol's independent final `SAFE TO ERASE` verdict.

## First action when v2 resumes

After Migration v1 acceptance, recheck live Git/runtime/storage facts. Review this archived v3
draft and resolve the descriptor-bound scratch blocker, then get Sol's bounded review before
dependent physical qualification. Keep the next Recovery boot gated on a complete Sol-approved
operator package. Migration v1 success is not v2 qualification.
