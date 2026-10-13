# Portability and Disaster Recovery

This document owns Brain host portability, recovery inventory, and erase-readiness across the
separate owner documents for acquisition data and operator behavior. The research checkout, owner
runtime, large research-data root, and protected evaluations have separate authority and recovery
boundaries. A successful code clone does not prove that Brain state can be restored.

## Current readiness

**Status as of 2026-10-13: NO-GO for factory reset.** A fresh clone and development environment
were rebuilt from `origin/research/active` at the inspected checkpoint
`3f5004b1be9bd09e529f4edce7a4f8306eadcc2a`; the synthetic smoke check passed. An isolated host
binding also re-verified the existing Kingston root by UUID and root marker. This rehearsal did not
restore the full corpus, ignored local research state, owner runtime, private credentials, or
protected material. Receipts:
[`first software/storage rehearsal`](evidence/portability-dr-rehearsal-2026-10-13.json) and
[`clean-cache rehearsal`](evidence/portability-dr-clean-cache-rehearsal-2026-10-13.json).

The checkout is clean and pushed at `research/active`. `main` remains at `770699d` because no new
live capability has been qualified. The supplied session handoff named `fa2e47d`; live Git and the
newer `memory/now.md` show that the branch has since advanced through `d077f26` to `3f5004b`.
On this host, a fresh network clone took 21.4 seconds, an uncached pinned development install took
19.6 seconds, and the synthetic smoke check took 0.7 seconds. This roughly 42-second software
bootstrap assumes macOS, Git, Python, and network access are already available; it excludes corpus
restore, owner runtime, credentials, and services.

Kingston is mounted at `/Volumes/Kingston`, UUID
`8f8f1081-9be4-4914-9111-75f1b90255bf`, with logical root ID
`9428695af6d4f46c08e1a444cdc66de8`. The live volume is case-sensitive APFS, unencrypted, and has
about 202.4 GiB free. It is the only external physical disk currently attached. No Time Machine
destination is configured. Kingston is the canonical external data tier, but its contents do not
currently have a verified independent physical replica.

The research checkout's ignored `work/` tree occupies about 16 GiB; its full dependency closure and
reset-surviving copies have not been classified. The ignored `.venv` is about 136 MiB and can be
rebuilt from `requirements-dev.lock`. `.env.local` is mode 0600 and contains Alchemy Free and Helius
URL settings; a reset-surviving credential source has not been verified. The private
`~/.config/brain-research` tree and installed LaunchAgent files also contain host bindings, service
records, and historical activation material. Do not restore an expired activation as authority.

The owner checkout is separate at `/Users/root1/Desktop/memecoin-ai`, commit
`a5944742b6b8ed0466c50bdeb72370ed3e3f2a31`, and 15 commits behind its `origin/main`. Its read-only
B01 status reported STOP and an expired runtime. Owner services, runtime bundles, champion, paper
ledgers, and jobs were not copied or changed in this rehearsal. Owner quiescence is not established:
`launchctl` reports a running owner operator process and several owner UI processes. D43 and owner
labels remain loaded, along with the read-only research storage-health service; no Senses
capture/watchdog label is loaded. Older Senses plist files remain installed but unloaded. This is
not a complete owner-runtime recovery inventory.

F01, D43, and reserved holdout contents were not read, hashed, copied, or included in the rehearsal.
Their backup custody is unresolved and must retain the existing protected-data boundary.

## State ownership and restore rules

| State | Source of truth | Restore rule |
|---|---|---|
| Code, history, canonical docs, committed receipts | Git remotes and immutable Git commits | Clone the exact accepted branch/commit; rebuild environments from checked-in pins. |
| Large research captures, datasets, journals, and immutable evidence | Kingston `BrainData` plus each artifact's own manifest, lineage, and recovery records | Verify volume UUID, root marker, artifact hashes, and semantic replay before admission. A volume copy alone is not a second backup. |
| Local ignored research state | Currently distributed across internal `work/`, model environments, and caches | Preserve unique evidence and dirty source before reset. Classify before disposal; rebuild only proven caches. |
| Private provider configuration | Secret manager or provider account recovery; current `.env.local` is host-local | Reissue or restore into a private mode-0600 file after code/data recovery. Never place secrets in Git, receipts, plists, or command arguments. |
| Host bindings | Per-host private configuration, including storage UUID/root binding | Recreate and verify explicitly. Paths are locators, not artifact identity. Never create old-path symlinks to satisfy a frozen reader. |
| Runtime and recovery authority | Versioned source, immutable journals/STOPs, activation records, and service definitions | Restore inert. Expired anchors, STOPs, spent reservations, ambiguity, and gaps remain terminal facts; never infer permission to resume. |
| Owner deployment and paper state | Separate owner checkout/runtime and its own durable state | Recover only through an owner-specific procedure. Research HEAD does not identify the deployed bundle or paper state. |
| Python environments and disposable caches | Checked-in lock files and reproducible setup | Rebuild after confirming the cache contains no unique captures, models, or manual changes. |
| Protected F01/D43/reserved material | Existing protected custody boundary | Excluded from ordinary inspection and rehearsal. Any backup procedure needs a separately reviewed custody method that does not expose or relabel contents. |

Preserve source bytes and original STOP/recovery histories. Keep event, availability, receipt, and
decision times distinct. Preserve `observed`, `unknown`, `censored`, and `failed` dispositions.
Sealed declarations may contain historical paths; retain their bytes and use explicit transport
locators for new restores. A recovery point is the last independently verifiable durable boundary,
not the newest directory timestamp.

## Restore procedure

1. Start on a clean compatible macOS host with Git and a supported Python version. Clone the exact
   accepted research commit from `origin/research/active`; confirm a clean worktree and commit ID.
2. Rebuild the base development environment with `make setup`. Build optional ML dependencies only
   when the restored task needs them. Do not copy an old virtualenv as a source of truth.
3. Attach Kingston, recreate the mode-0600 host-only binding with the actual mount path, then verify
   its volume UUID, case-sensitive APFS format, ownership, mount identity, 50-GiB free-space floor,
   and the `BrainData` root marker with
   `.venv/bin/python tools/research_storage.py check --config <private-binding> --min-free-bytes 53687091200 --require-root`.
   Do not run `create-root` against an existing corpus.
4. Restore or resolve each artifact through its declared manifest and transport locator. Verify
   exact hashes and replay receipts before admitting it. Restore to a new incoming path first.
   Never sync a live SQLite database independently of its WAL/SHM companions.
5. Restore private configuration from the authorized secret source. Validate its presence without
   printing values. Do not make provider requests as part of a restore rehearsal.
6. Keep all LaunchAgents unloaded while inventorying and verifying state. Recreate only the
   documented services for the assigned host role after source, identity, STOP, lease, and recovery
   checks pass. A worker receives no owner credentials, production runtime, or canonical writer.
7. Compare the restored evidence frontier and runtime identities with the last accepted handoff.
   Any missing prefix, unclassified state, failed replay, or unresolved attempt blocks readiness.

## Portability boundary

The verified path is currently macOS-specific: the storage resolver uses `diskutil` and APFS
ownership checks, and recurring host services use `launchd`. Kingston is currently unencrypted;
that is a custody risk for any future private backup stored there, even though no credential was
copied to it in this rehearsal. The environment lock can rebuild
Python tooling; it does not restore macOS service state, account access, GPU drivers, model caches,
or credentials. Frozen readers that reject symlink ancestors still require some source roots to
remain at their current paths until relocation-aware replay is qualified.

Linux or server recovery needs a separately qualified volume-identity and descriptor-bound writer
implementation, a service-manager backend, private secret delivery, and storage semantics for any
object-store or multi-worker path. Shared mutable SQLite remains single-writer. None of those
platform or cluster capabilities is implied by this Mac rehearsal.

## Blocks before `SAFE TO ERASE`

1. Inventory and preserve all authorized, non-protected internal state with a reset-surviving copy;
   identify exact data excluded as rebuildable and retain immutable evidence, failures, and STOPs.
2. Establish a verified independent backup destination for unique local state and Kingston data, or
   explicitly scope the claim to host loss while recording Kingston failure as an uncovered risk.
3. Resolve protected-data custody without reading or relabeling F01/D43/reserved holdout.
4. Establish the reset-surviving source for provider credentials and private host/service
   configuration.
5. Complete a full restore rehearsal from those sources into a fresh isolated checkout, including
   corpus dependency closure and replay, owner-state identity review, and inert-service verification.
6. Obtain Sol's independent final review of the complete receipt, exclusions, and remaining risks.

Until these conditions are met, the existing Mac remains part of the practical recovery path. The
software-and-storage rehearsal proves only that the published research commit, pinned development
dependencies, synthetic smoke path, and current Kingston binding can be reconstructed and checked.
